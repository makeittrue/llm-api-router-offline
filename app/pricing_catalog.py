"""models.dev 远程价格目录。

服务启动后在后台定时从 models.dev 拉取 api.json（带本地缓存、失败兜底），
供计费逻辑在「yaml 未写价」或「yaml 完全没有规则」时补充价格。

价格统一换算为 CNY / 1M tokens（汇率见 RemotePricingConfig.usd_to_cny），
索引采用「整体引用替换」实现读多写少下的无锁访问。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from app.config import RemotePricingConfig

# 我们的 provider 名/别名 → models.dev provider id（按优先序）。
# 若 RemotePricingConfig.provider_aliases 里出现同名键，则以配置为准（可覆盖为 [] 以禁用映射）。
DEFAULT_PROVIDER_ALIASES: dict[str, list[str]] = {
    "openai": ["openai"],
    "deepseek": ["deepseek"],
    "moonshot": ["moonshotai", "moonshotai-cn"],
    "moonshotai": ["moonshotai", "moonshotai-cn"],
    "kimi": ["moonshotai", "moonshotai-cn"],
    "zhipu": ["zhipuai"],
    "zhipuai": ["zhipuai"],
    "glm": ["zhipuai"],
    "bigmodel": ["zhipuai"],
    "qwen": ["alibaba", "alibaba-cn"],
    "dashscope": ["alibaba", "alibaba-cn"],
    "aliyun": ["alibaba", "alibaba-cn"],
    "bailian": ["alibaba", "alibaba-cn"],
    "xiaomi": ["xiaomi"],
    "mimo": ["xiaomi"],
    "doubao": [],
    "volcengine": [],
    "ark": [],
}


def normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result < 0:
        return None
    return result


def extract_usd_prices(cost: Any) -> dict[str, float | None] | None:
    """从 models.dev 条目的 cost 中取 base 价（USD / 1M tokens）。"""
    if not isinstance(cost, dict):
        return None
    prices = {
        "input": _to_float(cost.get("input")),
        "output": _to_float(cost.get("output")),
        "cache_read": _to_float(cost.get("cache_read")),
        "cache_write": _to_float(cost.get("cache_write")),
    }
    if prices["input"] is None and prices["output"] is None:
        return None
    return prices


def build_index(api: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """把 api.json 转成 {provider_id: {model_lower: {model, full_id, cost_usd}}} 索引。"""
    index: dict[str, dict[str, dict[str, Any]]] = {}
    if not isinstance(api, dict):
        return index
    for provider_id, provider_data in api.items():
        if not isinstance(provider_data, dict):
            continue
        models = provider_data.get("models")
        if not isinstance(models, dict):
            continue
        provider_key = normalize(provider_id)
        if not provider_key:
            continue
        bucket = index.setdefault(provider_key, {})
        for full_id, entry in models.items():
            if not isinstance(entry, dict):
                continue
            prices = extract_usd_prices(entry.get("cost"))
            if prices is None:
                continue
            model_raw = full_id.split("/", 1)[1] if "/" in full_id else full_id
            model_key = normalize(model_raw)
            if not model_key:
                continue
            bucket[model_key] = {"model": model_raw, "full_id": full_id, "cost_usd": prices}
    return {k: v for k, v in index.items() if v}


@dataclass(frozen=True)
class RemotePrice:
    """一条 models.dev 价格（input/output/cache_* 均为 CNY / 1M tokens）。"""

    provider_id: str
    model_id: str
    full_id: str
    input: float | None
    output: float | None
    cache_read: float | None
    cache_write: float | None
    source_url: str
    usd_to_cny: float
    match_kind: str  # "exact" | "prefix"


class PricingCatalog:
    def __init__(self, config: RemotePricingConfig):
        self.config = config
        self._index: dict[str, dict[str, dict[str, Any]]] = {}
        self._loaded = False
        self._source: str | None = None
        self._last_refresh_at: str | None = None
        self._last_error: str | None = None
        self._last_cache_write_error: str | None = None
        aliases = dict(DEFAULT_PROVIDER_ALIASES)
        aliases.update(config.provider_aliases or {})
        self._aliases = aliases

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def loaded(self) -> bool:
        return self._loaded and bool(self._index)

    # ---------- provider 映射 ----------

    def provider_candidates(self, names: Iterable[str | None]) -> list[str]:
        """把我们的 provider 名/别名映射为 models.dev provider id 候选列表（按优先序去重）。"""
        out: list[str] = []
        seen: set[str] = set()
        for name in names:
            key = normalize(name)
            if not key:
                continue
            raw = self._aliases[key] if key in self._aliases else [key]
            for provider_id in raw:
                provider_key = normalize(provider_id)
                if provider_key and provider_key not in seen:
                    seen.add(provider_key)
                    out.append(provider_id)
        return out

    # ---------- 加载与刷新 ----------

    def load_cache_sync(self, cache_path: str | Path | None = None) -> bool:
        """同步读取本地缓存（纯本地、不联网），供启动/回补/测试复用。"""
        path = Path(cache_path or self.config.cache_path)
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            self._last_error = f"cache load failed: {e}"
            return False
        index = build_index(data)
        if not index:
            self._last_error = "cache parsed but empty index"
            return False
        self._index = index
        self._loaded = True
        self._source = "cache"
        return True

    async def refresh(self, force: bool = False) -> bool:
        """后台刷新：缓存未过期时跳过联网，失败时保留上一份可用索引。"""
        path = Path(self.config.cache_path)
        if not force and self.loaded and self._cache_is_fresh(path):
            return True
        try:
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                headers={"User-Agent": self.config.user_agent},
            ) as client:
                resp = await client.get(self.config.source_url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:  # 网络/解析失败都不能影响调用方
            if not self.loaded:
                # 先尝试回落到本地缓存（会写入 cache load 失败原因），再用更有价值的网络错误覆盖它
                await asyncio.to_thread(self.load_cache_sync)
            self._last_error = f"fetch failed: {e}"
            return False

        try:
            index = await asyncio.to_thread(build_index, data)
        except Exception as e:
            self._last_error = f"index build failed: {e}"
            return False
        if not index:
            self._last_error = "remote data parsed but empty index"
            return False

        self._index = index
        self._loaded = True
        self._source = "network"
        self._last_error = None
        self._last_refresh_at = datetime.now(timezone.utc).isoformat()
        # 写缓存失败不影响本次刷新的内存索引，单独记录，不与 last_error / 刷新成功状态冲突。
        self._last_cache_write_error = await asyncio.to_thread(self._write_cache, path, data)
        return True

    async def run_scheduler(self, stop_event: asyncio.Event) -> None:
        """周期性后台刷新；首次加随机抖动，降低多 worker 同步拉取。"""
        jitter = random.uniform(0.0, min(60.0, max(self.config.refresh_interval_seconds * 0.1, 1.0)))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=jitter)
            return
        except asyncio.TimeoutError:
            pass
        while not stop_event.is_set():
            try:
                await self.refresh()
            except Exception as e:
                self._last_error = f"scheduler refresh failed: {e}"
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.config.refresh_interval_seconds)
            except asyncio.TimeoutError:
                continue

    def _cache_is_fresh(self, path: Path) -> bool:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return False
        return age < self.config.cache_ttl_seconds

    def _write_cache(self, path: Path, data: Any) -> str | None:
        """原子写缓存；返回错误信息（None 表示成功）。不影响内存索引。"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except Exception as e:
            return f"cache write failed: {e}"
        return None

    # ---------- 查询 ----------

    def lookup(
        self,
        provider_candidates: Iterable[str],
        model: str | None,
        *,
        allow_prefix: bool = True,
    ) -> RemotePrice | None:
        """按候选 provider 找价：先 exact，再长度受限的前缀（双向、取最长命中）。

        allow_prefix=False 时只做精确匹配（供 yaml 规则补价使用，避免 match_mode=exact 的规则
        被相近模型价格误补）。
        """
        model_key = normalize(model)
        if not model_key:
            return None
        index = self._index
        candidates: list[str] = []
        for candidate in provider_candidates:
            key = normalize(candidate)
            if key:
                candidates.append(key)
        for provider_key in candidates:
            entry = index.get(provider_key, {}).get(model_key)
            if entry is not None:
                return self._to_price(provider_key, entry, "exact")
        if not allow_prefix:
            return None
        min_len = self.config.min_prefix_match_length
        if len(model_key) < min_len:
            return None
        for provider_key in candidates:
            bucket = index.get(provider_key)
            if not bucket:
                continue
            best: tuple[str, dict[str, Any]] | None = None
            for key, entry in bucket.items():
                if len(key) < min_len:
                    continue
                if key.startswith(model_key) or model_key.startswith(key):
                    if best is None or len(key) > len(best[0]):
                        best = (key, entry)
            if best is not None:
                return self._to_price(provider_key, best[1], "prefix")
        return None

    def _to_price(self, provider_key: str, entry: dict[str, Any], match_kind: str) -> RemotePrice:
        rate = self.config.usd_to_cny
        usd = entry.get("cost_usd") or {}

        def convert(value: float | None) -> float | None:
            if value is None:
                return None
            return round(value * rate, 6)

        return RemotePrice(
            provider_id=provider_key,
            model_id=str(entry.get("model") or ""),
            full_id=str(entry.get("full_id") or ""),
            input=convert(usd.get("input")),
            output=convert(usd.get("output")),
            cache_read=convert(usd.get("cache_read")),
            cache_write=convert(usd.get("cache_write")),
            source_url=f"https://models.dev/{entry.get('full_id')}",
            usd_to_cny=rate,
            match_kind=match_kind,
        )

    def provider_ids(self) -> list[str]:
        """当前索引中所有 models.dev provider id（已归一化、排序）。"""
        return sorted(self._index.keys())

    def list_entries(
        self,
        *,
        provider_id: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        """分页/过滤列出目录条目（供价格页面使用）。价格同时给出 USD 原值与 CNY 换算值。"""
        provider_key = normalize(provider_id)
        query_key = normalize(query)
        rate = self.config.usd_to_cny
        items: list[dict[str, Any]] = []
        total = 0
        for pid in self.provider_ids():
            if provider_key and pid != provider_key:
                continue
            for model_key, entry in self._index[pid].items():
                if query_key and query_key not in model_key and query_key not in pid:
                    continue
                total += 1
                if total <= offset or len(items) >= limit:
                    continue
                usd = entry.get("cost_usd") or {}

                def convert(value: float | None) -> float | None:
                    return None if value is None else round(value * rate, 6)

                items.append({
                    "provider_id": pid,
                    "model": entry.get("model"),
                    "full_id": entry.get("full_id"),
                    "usd": {k: usd.get(k) for k in ("input", "output", "cache_read", "cache_write")},
                    "cny": {k: convert(usd.get(k)) for k in ("input", "output", "cache_read", "cache_write")},
                    "source_url": f"https://models.dev/{entry.get('full_id')}",
                })
        return {
            "total": total,
            "items": items,
            "providers": self.provider_ids(),
            "usd_to_cny": rate,
        }

    def status(self) -> dict[str, Any]:
        try:
            cache_age = round(time.time() - Path(self.config.cache_path).stat().st_mtime, 1)
        except OSError:
            cache_age = None
        return {
            "enabled": self.config.enabled,
            "loaded": self.loaded,
            "source": self._source,
            "provider_count": len(self._index),
            "entry_count": sum(len(bucket) for bucket in self._index.values()),
            "last_refresh_at": self._last_refresh_at,
            "last_error": self._last_error,
            "last_cache_write_error": self._last_cache_write_error,
            "cache_path": self.config.cache_path,
            "cache_age_seconds": cache_age,
            "usd_to_cny": self.config.usd_to_cny,
        }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="models.dev 价格目录：状态查看 / 手动刷新")
    parser.add_argument("--config", default=None, help="配置文件路径，默认读取 LLM_ROUTER_CONFIG 或 config.yaml")
    parser.add_argument("--cache", default=None, help="覆盖缓存文件路径")
    parser.add_argument("--refresh", action="store_true", help="强制联网刷新（默认仅读取本地缓存）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    from app.config import load_config

    try:
        app_config = load_config(args.config)
    except FileNotFoundError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    remote = app_config.billing.remote_pricing
    if args.cache:
        remote = remote.model_copy(update={"cache_path": args.cache})
    catalog = PricingCatalog(remote)
    if not catalog.load_cache_sync():
        print(f"[warn] 本地缓存不可用：{catalog.status().get('last_error')}", file=sys.stderr)
    if args.refresh:
        ok = asyncio.run(catalog.refresh(force=True))
        print(f"[refresh] {'ok' if ok else 'failed'}", file=sys.stderr)
        if not ok:
            print(f"[error] {catalog.status().get('last_error')}", file=sys.stderr)
    print(json.dumps(catalog.status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())