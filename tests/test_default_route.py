from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.config import AppConfig, BillingConfig, BillingRuleConfig, LogConfig, ProviderConfig, RouteConfig
from app.logger import CallLogger
from app.providers.base import BaseProvider, UpstreamError
from app.router import Router


class DummyProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        super().__init__(config)

    async def chat_completion(self, request, provider_model: str) -> dict:
        if provider_model in {"broken-upstream"}:
            raise UpstreamError(
                502,
                {"error": {"message": f"{provider_model} failed", "type": "upstream_error"}},
            )
        return {
            "id": f"chatcmpl-{provider_model}",
            "object": "chat.completion",
            "created": 0,
            "model": provider_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": f"reply from {provider_model}"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def chat_completion_stream(self, request, provider_model: str):
        if provider_model == "reasoning-upstream":
            payload = {
                "id": "stream-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": provider_model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "reasoning_content": "thinking"},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
            return

        if provider_model == "broken-upstream":
            raise UpstreamError(
                502,
                {"error": {"message": f"{provider_model} failed", "type": "upstream_error"}},
            )

        payload = {
            "id": "stream-2",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": provider_model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": f"reply from {provider_model}"},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


class DefaultRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.original_create_provider_main = main.create_provider
        self.original_create_provider_router = __import__("app.router", fromlist=["create_provider"]).create_provider
        self.original_config = main.app_config
        self.original_router = main.router
        self.original_logger = main.call_logger
        self.original_overrides = dict(main.app.dependency_overrides)

        self.client_cm = TestClient(main.app)
        self.client = self.client_cm.__enter__()

        config = AppConfig(
            log=LogConfig(db_path=self.db_path),
            billing=BillingConfig(
                rules=[
                    BillingRuleConfig(provider="primary"),
                    BillingRuleConfig(provider="zhipu"),
                    BillingRuleConfig(provider="primary"),
                ]
            ),
            providers=[
                ProviderConfig(name="primary", base_url="https://primary.example", api_key="x"),
                ProviderConfig(name="backup", base_url="https://backup.example", api_key="x"),
                ProviderConfig(name="user-provider", base_url="https://user.example", api_key="x"),
            ],
            routes=[
                RouteConfig(model="broken-model", provider="primary", provider_model="broken-upstream"),
                RouteConfig(model="backup-model", provider="backup", provider_model="backup-upstream"),
                RouteConfig(model="custom-model", provider="primary", provider_model="global-upstream"),
                RouteConfig(model="reasoning-model", provider="backup", provider_model="reasoning-upstream"),
            ],
        )
        main.app_config = config
        main.router = Router(config)
        main.call_logger = CallLogger(self.db_path)
        main.create_provider = self._create_provider
        router_module = __import__("app.router", fromlist=["create_provider"])
        router_module.create_provider = self._create_provider
        main.app.dependency_overrides[main.get_current_user] = lambda: {"id": 1, "username": "tester"}

    def tearDown(self):
        main.create_provider = self.original_create_provider_main
        router_module = __import__("app.router", fromlist=["create_provider"])
        router_module.create_provider = self.original_create_provider_router
        main.app_config = self.original_config
        main.router = self.original_router
        main.call_logger = self.original_logger
        main.app.dependency_overrides = self.original_overrides
        self.client_cm.__exit__(None, None, None)
        self.temp_dir.cleanup()

    @staticmethod
    def _create_provider(config: ProviderConfig) -> DummyProvider:
        return DummyProvider(config)

    def test_default_model_requires_enabled_configuration(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={"model": "default", "messages": [{"role": "user", "content": "hello"}]},
        )

        self.assertEqual(response.status_code, 404)
        self.assertIn("disabled", response.json()["error"]["message"])

    def test_default_model_falls_back_to_next_candidate(self):
        main.call_logger.upsert_user_default_route(
            1,
            enabled=True,
            models=["broken-model", "backup-model"],
        )

        response = self.client.post(
            "/v1/chat/completions",
            json={"model": "default", "messages": [{"role": "user", "content": "hello"}]},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["model"], "default")
        self.assertEqual(body["choices"][0]["message"]["content"], "reply from backup-upstream")
        last_log = main.call_logger.query_logs(user_id=1, limit=1)[0]
        self.assertEqual(last_log["provider_model"], "backup-upstream")

    def test_default_model_prefers_user_route_before_global_route(self):
        main.call_logger.create_user_route(
            user_id=1,
            model="custom-model",
            provider_name="user-provider",
            provider_base_url="https://user.example",
            provider_api_key="secret",
            provider_model="user-upstream",
        )
        main.call_logger.upsert_user_default_route(1, enabled=True, models=["custom-model"])

        response = self.client.post(
            "/v1/chat/completions",
            json={"model": "default", "messages": [{"role": "user", "content": "hello"}]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["choices"][0]["message"]["content"],
            "reply from user-upstream",
        )
        last_log = main.call_logger.query_logs(user_id=1, limit=1)[0]
        self.assertEqual(last_log["provider_model"], "user-upstream")

    def test_non_stream_success_call_is_logged(self):
        response = self.client.post(
            "/v1/chat/completions",
            json={"model": "custom-model", "messages": [{"role": "user", "content": "hello"}]},
        )

        self.assertEqual(response.status_code, 200)
        logs = main.call_logger.query_logs(user_id=1, limit=10)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["provider_model"], "global-upstream")
        self.assertEqual(logs[0]["prompt_tokens"], 1)
        self.assertEqual(logs[0]["completion_tokens"], 1)
        self.assertEqual(logs[0]["total_tokens"], 2)
        self.assertEqual(logs[0]["status"], "success")

    def test_billing_provider_options_are_derived_from_billing_rules(self):
        response = self.client.get("/v1/admin/billing/providers")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "providers": [
                    {
                        "name": "primary",
                        "base_url": "https://primary.example",
                        "api_type": "openai",
                    },
                    {
                        "name": "zhipu",
                        "base_url": "",
                        "api_type": "openai",
                    },
                ]
            },
        )

    def test_stream_default_model_keeps_resolved_model_in_synthetic_chunks(self):
        main.call_logger.upsert_user_default_route(1, enabled=True, models=["reasoning-model"])

        response = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "default",
                "stream": True,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        payloads = []
        for line in response.text.splitlines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            payloads.append(json.loads(line[6:]))

        self.assertGreaterEqual(len(payloads), 3)
        self.assertTrue(all(item["model"] == "reasoning-upstream" for item in payloads))


if __name__ == "__main__":
    unittest.main()
