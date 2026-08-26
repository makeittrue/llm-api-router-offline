from __future__ import annotations

import unittest
from unittest import mock

import httpx

from app.balance import detect_vendor, query_all_balances, query_provider_balance
from app.config import ProviderConfig


def _provider(name: str, base_url: str, api_key: str = "sk-test") -> ProviderConfig:
    return ProviderConfig(name=name, base_url=base_url, api_key=api_key)


def _fake_response(status_code: int = 200, payload: dict | None = None, text: str = ""):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    response.text = text
    return response


def _patch_http(payload: dict, status_code: int = 200):
    """让 httpx.AsyncClient(...) 的 get() 返回指定响应，并记录请求 URL。"""
    client = mock.AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = _fake_response(status_code, payload)
    return mock.patch("app.balance.httpx.AsyncClient", return_value=client), client


class DetectVendorTests(unittest.TestCase):
    def test_supported_hosts(self):
        self.assertEqual(
            detect_vendor("https://api.deepseek.com"),
            ("deepseek", "/user/balance"),
        )
        self.assertEqual(
            detect_vendor("https://api.moonshot.cn"),
            ("moonshot", "/v1/users/me/balance"),
        )
        # base_url 带 /v1 前缀也能识别
        self.assertEqual(
            detect_vendor("https://api.moonshot.cn/v1"),
            ("moonshot", "/v1/users/me/balance"),
        )
        self.assertEqual(
            detect_vendor("https://open.bigmodel.cn/api/paas"),
            ("zhipu", "/api/biz/account/query-customer-account-report"),
        )

    def test_unsupported_hosts(self):
        self.assertIsNone(detect_vendor("https://api.openai.com/v1"))
        self.assertIsNone(detect_vendor("https://example.com"))


class QueryProviderBalanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_balance(self):
        patcher, client = _patch_http(
            {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "110.00",
                        "granted_balance": "10.00",
                        "topped_up_balance": "100.00",
                    }
                ],
            }
        )
        with patcher:
            result = await query_provider_balance(
                _provider("deepseek", "https://api.deepseek.com")
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["vendor"], "deepseek")
        self.assertTrue(result["supported"])
        self.assertTrue(result["is_available"])
        self.assertEqual(result["balances"][0]["currency"], "CNY")
        self.assertEqual(result["balances"][0]["available_balance"], 110.0)
        self.assertEqual(
            result["balances"][0]["components"],
            {"granted_balance": 10.0, "topped_up_balance": 100.0},
        )
        # 端点应为 https://api.deepseek.com/user/balance
        url = client.get.call_args.args[0]
        self.assertEqual(url, "https://api.deepseek.com/user/balance")
        headers = client.get.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sk-test")

    async def test_moonshot_balance(self):
        patcher, client = _patch_http(
            {
                "code": 0,
                "data": {
                    "available_balance": 49.58894,
                    "voucher_balance": 46.58893,
                    "cash_balance": 3.00001,
                },
                "scode": "0x0",
                "status": True,
            }
        )
        with patcher:
            result = await query_provider_balance(
                _provider("moonshot", "https://api.moonshot.cn")
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["vendor"], "moonshot")
        self.assertTrue(result["is_available"])
        self.assertEqual(result["balances"][0]["available_balance"], 49.58894)
        self.assertEqual(
            result["balances"][0]["components"],
            {"voucher_balance": 46.58893, "cash_balance": 3.00001},
        )
        url = client.get.call_args.args[0]
        self.assertEqual(url, "https://api.moonshot.cn/v1/users/me/balance")

    async def test_moonshot_zero_balance_is_unavailable(self):
        patcher, _ = _patch_http(
            {
                "code": 0,
                "data": {
                    "available_balance": 0,
                    "voucher_balance": 0,
                    "cash_balance": 0,
                },
            }
        )
        with patcher:
            result = await query_provider_balance(
                _provider("moonshot", "https://api.moonshot.cn")
            )
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["is_available"])

    async def test_zhipu_balance(self):
        patcher, client = _patch_http(
            {
                "code": 200,
                "msg": "操作成功",
                "data": {
                    "availableBalance": 2.726199,
                    "rechargeAmount": 10.0,
                    "giveAmount": 5.0,
                    "totalSpendAmount": 12.27,
                },
                "success": True,
            }
        )
        with patcher:
            result = await query_provider_balance(
                _provider("zhipu", "https://open.bigmodel.cn/api/paas")
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["vendor"], "zhipu")
        self.assertEqual(result["balances"][0]["available_balance"], 2.726199)
        url = client.get.call_args.args[0]
        self.assertEqual(
            url,
            "https://open.bigmodel.cn/api/biz/account/query-customer-account-report",
        )

    async def test_zhipu_success_false_is_error(self):
        patcher, _ = _patch_http(
            {"code": 401, "msg": "未授权", "success": False}
        )
        with patcher:
            result = await query_provider_balance(
                _provider("zhipu", "https://open.bigmodel.cn/api/paas")
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("未授权", result["error"])

    async def test_openai_unsupported(self):
        result = await query_provider_balance(
            _provider("openai", "https://api.openai.com/v1")
        )
        self.assertEqual(result["status"], "unsupported")
        self.assertFalse(result["supported"])
        self.assertIn("OpenAI", result["error"])

    async def test_unknown_host_unsupported(self):
        result = await query_provider_balance(
            _provider("custom", "https://llm.example.com/v1")
        )
        self.assertEqual(result["status"], "unsupported")
        self.assertIn("暂不支持", result["error"])

    async def test_http_error(self):
        patcher, _ = _patch_http(
            {"error": {"message": "Invalid API key"}},
            status_code=401,
        )
        with patcher:
            result = await query_provider_balance(
                _provider("deepseek", "https://api.deepseek.com")
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 401", result["error"])

    async def test_missing_api_key(self):
        result = await query_provider_balance(
            _provider("deepseek", "https://api.deepseek.com", api_key="")
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("API Key", result["error"])

    async def test_transport_error(self):
        client = mock.AsyncMock()
        client.__aenter__.return_value = client
        client.get.side_effect = httpx.ConnectError("connection refused")
        with mock.patch("app.balance.httpx.AsyncClient", return_value=client):
            result = await query_provider_balance(
                _provider("deepseek", "https://api.deepseek.com")
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("请求失败", result["error"])

    async def test_query_all_preserves_order(self):
        providers = [
            _provider("deepseek", "https://api.deepseek.com"),
            _provider("moonshot", "https://api.moonshot.cn"),
            _provider("openai", "https://api.openai.com/v1"),
        ]
        patcher, _ = _patch_http(
            {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "5.00",
                        "granted_balance": "0.00",
                        "topped_up_balance": "5.00",
                    }
                ],
            }
        )
        with patcher:
            results = await query_all_balances(providers)
        self.assertEqual([r["name"] for r in results], ["deepseek", "moonshot", "openai"])
        # mock 对所有 host 返回同一份 deepseek 格式响应：deepseek 正常解析，
        # moonshot 因缺 data 字段正确报错（不影响顺序与其余结果），openai 不支持
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[1]["status"], "error")
        self.assertEqual(results[2]["status"], "unsupported")

    async def test_query_all_isolates_unexpected_exception(self):
        """单个运营商抛出未预期异常时，其余结果不受影响（不放大为整端点失败）。"""
        providers = [
            _provider("deepseek", "https://api.deepseek.com"),
            _provider("openai", "https://api.openai.com/v1"),
        ]
        async def _boom(_provider):
            raise RuntimeError("unexpected")

        with mock.patch("app.balance.query_provider_balance", side_effect=_boom):
            results = await query_all_balances(providers)
        self.assertEqual(results[0]["status"], "error")
        self.assertIn("unexpected", results[0]["error"])
        self.assertEqual(results[1]["status"], "error")
        self.assertIn("unexpected", results[1]["error"])

    async def test_query_all_empty(self):
        self.assertEqual(await query_all_balances([]), [])

    async def test_malformed_moonshot_data_field_does_not_crash(self):
        """HTTP 200 但 data 为数字：应落入 error 字段而非抛 AttributeError。"""
        patcher, _ = _patch_http({"code": 0, "data": 5})
        with patcher:
            result = await query_provider_balance(
                _provider("moonshot", "https://api.moonshot.cn")
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("data", result["error"])

    async def test_malformed_deepseek_balance_infos_type(self):
        """HTTP 200 但 balance_infos 为数字：类型防护后按空余额处理，不抛 TypeError。"""
        patcher, _ = _patch_http({"is_available": True, "balance_infos": 42})
        with patcher:
            result = await query_provider_balance(
                _provider("deepseek", "https://api.deepseek.com")
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["balances"], [])

    async def test_malformed_base_url_does_not_crash(self):
        """畸形 base_url（IPv6 括号不配对）应返回 unsupported，而非抛 ValueError。"""
        result = await query_provider_balance(
            _provider("broken", "https://[invalid")
        )
        self.assertEqual(result["status"], "unsupported")
        self.assertIn("无法解析", result["error"])

    async def test_moonshot_nonzero_code_is_error(self):
        patcher, _ = _patch_http({"code": 1234, "data": {}})
        with patcher:
            result = await query_provider_balance(
                _provider("moonshot", "https://api.moonshot.cn")
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("1234", result["error"])

    async def test_zhipu_string_false_is_error(self):
        """success 为字符串 "false" 时也应识别为失败（宽松判定）。"""
        patcher, _ = _patch_http(
            {"code": 200, "msg": "未授权", "success": "false"}
        )
        with patcher:
            result = await query_provider_balance(
                _provider("zhipu", "https://open.bigmodel.cn/api/paas")
            )
        self.assertEqual(result["status"], "error")
        self.assertIn("未授权", result["error"])

    async def test_queried_at_semantics(self):
        """未发起查询的分支 queried_at 为 None；拿到响应后写入时间戳。"""
        unsupported = await query_provider_balance(
            _provider("openai", "https://api.openai.com/v1")
        )
        self.assertIsNone(unsupported["queried_at"])

        patcher, _ = _patch_http(
            {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "1.00",
                        "granted_balance": "0.00",
                        "topped_up_balance": "1.00",
                    }
                ],
            }
        )
        with patcher:
            ok = await query_provider_balance(
                _provider("deepseek", "https://api.deepseek.com")
            )
        self.assertIsNotNone(ok["queried_at"])


if __name__ == "__main__":
    unittest.main()
