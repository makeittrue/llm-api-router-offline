"""上游运营商账户实时余额查询。

各运营商余额接口均为 GET + Bearer 认证：
- DeepSeek:     https://api.deepseek.com/user/balance
- Moonshot/Kimi: https://api.moonshot.cn/v1/users/me/balance（国际站 api.moonshot.ai 同路径）
- 智谱:          https://open.bigmodel.cn/api/biz/account/query-customer-account-report
- OpenAI:       未提供公开余额查询 API → 返回 unsupported

按 base_url 的 host 识别运营商（scheme+host+固定路径构造端点，
因此 base_url 是否带 /v1 等前缀均可正确工作）；未知 host 同样返回 unsupported。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import ProviderConfig

_TIMEOUT = httpx.Timeout(10.0)

# host → (运营商标识, 余额端点绝对路径)
_VENDOR_SPECS: dict[str, tuple[str, str]] = {
    "api.deepseek.com": ("deepseek", "/user/balance"),
    "api.moonshot.cn": ("moonshot", "/v1/users/me/balance"),
    "api.moonshot.ai": ("moonshot", "/v1/users/me/balance"),
    "open.bigmodel.cn": ("zhipu", "/api/biz/account/query-customer-account-report"),
}

# 明确无余额接口的运营商，提示语比通用"不支持"更友好
_KNOWN_UNSUPPORTED: dict[str, str] = {
    "api.openai.com": "OpenAI 未提供公开的余额查询 API",
}


def _safe_host(base_url: str) -> str:
    """解析 base_url 的 host；畸形 URL（如 IPv6 括号不配对）返回空串而非抛错。"""
    try:
        return (urlsplit(base_url).hostname or "").lower()
    except ValueError:
        return ""


def detect_vendor(base_url: str) -> tuple[str, str] | None:
    """按 base_url 的 host 识别运营商，返回 (vendor, 余额端点绝对路径)。"""
    return _VENDOR_SPECS.get(_safe_host(base_url))


def _to_float(value: Any) -> float | None:
    """DeepSeek 余额为字符串、其余为数字；统一转 float，失败返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_deepseek(data: dict[str, Any]) -> dict[str, Any]:
    balances: list[dict[str, Any]] = []
    infos = data.get("balance_infos")
    if isinstance(infos, list):
        for info in infos:
            if not isinstance(info, dict):
                continue
            balances.append(
                {
                    "currency": info.get("currency") or "",
                    "available_balance": _to_float(info.get("total_balance")),
                    "components": {
                        "granted_balance": _to_float(info.get("granted_balance")),
                        "topped_up_balance": _to_float(info.get("topped_up_balance")),
                    },
                }
            )
    return {"is_available": data.get("is_available"), "balances": balances}


def _parse_moonshot(data: dict[str, Any]) -> dict[str, Any]:
    code = data.get("code")
    if code not in (None, 0):
        raise ValueError(f"Moonshot 接口返回错误码: {code}")
    detail = data.get("data")
    if not isinstance(detail, dict):
        raise ValueError("Moonshot 响应缺少有效的 data 字段")
    balances = [
        {
            "currency": "CNY",
            "available_balance": _to_float(detail.get("available_balance")),
            "components": {
                "voucher_balance": _to_float(detail.get("voucher_balance")),
                "cash_balance": _to_float(detail.get("cash_balance")),
            },
        }
    ]
    # 官方文档：available_balance <= 0 时无法调用推理 API
    available = _to_float(detail.get("available_balance"))
    return {"is_available": None if available is None else available > 0, "balances": balances}


def _parse_zhipu(data: dict[str, Any]) -> dict[str, Any]:
    # 宽松判定：兼容布尔 false 与字符串 "false"（非空字符串为 truthy，需显式归一化）
    success = data.get("success", True)
    if isinstance(success, str):
        success = success.strip().lower() not in ("false", "0", "")
    if not success:
        raise ValueError(str(data.get("msg") or "智谱余额接口返回 success=false"))
    detail = data.get("data")
    if not isinstance(detail, dict):
        raise ValueError("智谱响应缺少有效的 data 字段")
    balances = [
        {
            "currency": "CNY",
            "available_balance": _to_float(detail.get("availableBalance")),
            "components": {
                "recharge_amount": _to_float(detail.get("rechargeAmount")),
                "give_amount": _to_float(detail.get("giveAmount")),
                "total_spend_amount": _to_float(detail.get("totalSpendAmount")),
            },
        }
    ]
    return {"is_available": None, "balances": balances}


_PARSERS = {
    "deepseek": _parse_deepseek,
    "moonshot": _parse_moonshot,
    "zhipu": _parse_zhipu,
}


def _base_result(provider: ProviderConfig) -> dict[str, Any]:
    return {
        "name": provider.name,
        "base_url": provider.base_url,
        "vendor": None,
        "supported": False,
        "status": "unsupported",
        "is_available": None,
        "balances": [],
        "error": None,
        # 拿到上游响应后才会写入；未发起查询的分支保持 None
        "queried_at": None,
    }


async def query_provider_balance(provider: ProviderConfig) -> dict[str, Any]:
    """查询单个运营商的实时余额，返回归一化结果（不抛异常，错误信息在 error 字段）。"""
    result = _base_result(provider)

    spec = detect_vendor(provider.base_url)
    if spec is None:
        host = _safe_host(provider.base_url)
        if host:
            result["error"] = _KNOWN_UNSUPPORTED.get(
                host, f"暂不支持该服务商（{host}）的余额查询"
            )
        else:
            result["error"] = "base_url 无法解析，不支持余额查询"
        return result

    vendor, path = spec
    result["vendor"] = vendor
    result["supported"] = True

    if not provider.api_key:
        result["status"] = "error"
        result["error"] = "API key 未配置（环境变量未设置或为空）"
        return result

    parts = urlsplit(provider.base_url)
    url = f"{parts.scheme}://{parts.netloc}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {provider.api_key}",
                    "Accept": "application/json",
                },
            )
        result["queried_at"] = datetime.now(timezone.utc).isoformat()
        if resp.status_code >= 400:
            result["status"] = "error"
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
            return result
        data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"响应不是 JSON 对象: {str(data)[:120]}")
        parsed = _PARSERS[vendor](data)
    except ValueError as e:
        # 含各 parser 对错误信封的显式报错与 JSON 解析失败
        result["status"] = "error"
        result["error"] = str(e) or "余额响应解析失败"
        return result
    except httpx.HTTPError as e:
        result["status"] = "error"
        result["error"] = f"请求失败: {e}"
        return result
    except Exception as e:
        # 兜底：畸形响应结构等未预期异常。契约是"单运营商失败不影响其他运营商"，
        # 绝不让异常穿透到 gather / 端点层。
        result["status"] = "error"
        result["error"] = f"解析余额响应失败: {e.__class__.__name__}: {e}"
        return result

    result.update(parsed)
    result["status"] = "ok"
    return result


async def query_all_balances(providers: list[ProviderConfig]) -> list[dict[str, Any]]:
    """并发查询所有运营商余额，顺序与 providers 一致；单个运营商意外异常不影响整体。"""
    if not providers:
        return []
    results = await asyncio.gather(
        *(query_provider_balance(p) for p in providers), return_exceptions=True
    )
    final: list[dict[str, Any]] = []
    for provider, item in zip(providers, results):
        if isinstance(item, BaseException):
            # query_provider_balance 自身已保证不抛异常，此处为第二层防线
            fallback = _base_result(provider)
            fallback["status"] = "error"
            fallback["error"] = f"查询异常: {item.__class__.__name__}: {item}"
            final.append(fallback)
        else:
            final.append(item)
    return final
