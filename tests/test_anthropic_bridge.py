from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.anthropic_bridge import (
    AnthropicStreamConverter,
    anthropic_to_chat_request,
    openai_to_anthropic_response,
)
from app.config import AppConfig, BillingConfig, BillingRuleConfig, LogConfig, ProviderConfig, RouteConfig
from app.logger import CallLogger
from app.providers.base import BaseProvider, UpstreamError
from app.router import Router


class DummyProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        super().__init__(config)

    async def chat_completion(self, request, provider_model: str) -> dict:
        if provider_model == "tool-upstream":
            return {
                "id": "chatcmpl-tool",
                "object": "chat.completion",
                "created": 0,
                "model": provider_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{\"path\": \"/tmp/demo.txt\"}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            }
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
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }

    async def chat_completion_stream(self, request, provider_model: str):
        chunks = [
            {
                "id": "stream-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": provider_model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            },
            {
                "id": "stream-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": provider_model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "hello"},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": "stream-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": provider_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
        ]
        for chunk in chunks:
            yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


class AnthropicBridgeTests(unittest.TestCase):
    def test_anthropic_to_chat_request_maps_system_tools_and_tool_result(self):
        body = {
            "model": "my-claude-model",
            "system": "You are helpful.",
            "max_tokens": 1024,
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
            "tool_choice": {"type": "tool", "name": "read_file"},
            "messages": [
                {"role": "user", "content": "hello"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_123",
                            "name": "read_file",
                            "input": {"path": "/tmp/demo.txt"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_123",
                            "content": "file body",
                        }
                    ],
                },
            ],
        }

        chat_request = anthropic_to_chat_request(body)
        self.assertEqual(chat_request.model, "my-claude-model")
        self.assertEqual(chat_request.max_tokens, 1024)
        self.assertEqual(chat_request.messages[0].role, "system")
        self.assertEqual(chat_request.messages[0].content, "You are helpful.")
        self.assertEqual(chat_request.messages[-1].role, "tool")
        self.assertEqual(chat_request.messages[-1].tool_call_id, "toolu_123")
        self.assertEqual(chat_request.tool_choice, {"type": "function", "function": {"name": "read_file"}})

    def test_openai_to_anthropic_response_maps_tool_use(self):
        response = openai_to_anthropic_response(
            {
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": "{\"path\": \"/tmp/demo.txt\"}",
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            },
            requested_model="claude-sonnet",
        )

        self.assertEqual(response["model"], "claude-sonnet")
        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(response["content"][0]["type"], "tool_use")
        self.assertEqual(response["content"][0]["name"], "read_file")
        self.assertEqual(response["usage"]["input_tokens"], 3)

    def test_stream_converter_emits_anthropic_events(self):
        converter = AnthropicStreamConverter(requested_model="claude-sonnet")
        events: list[bytes] = []
        events.extend(
            converter.process_openai_chunk(
                {
                    "choices": [{"delta": {"role": "assistant"}, "finish_reason": None}],
                }
            )
        )
        events.extend(
            converter.process_openai_chunk(
                {
                    "choices": [{"delta": {"content": "hello"}, "finish_reason": None}],
                }
            )
        )
        events.extend(
            converter.process_openai_chunk(
                {
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
        )
        events.extend(converter.finish())
        text = b"".join(events).decode("utf-8")
        self.assertIn("event: message_start", text)
        self.assertIn("event: content_block_delta", text)
        self.assertIn('"text": "hello"', text)
        self.assertIn("event: message_stop", text)


class AnthropicMessagesEndpointTests(unittest.TestCase):
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
            billing=BillingConfig(rules=[BillingRuleConfig(provider="primary")]),
            providers=[
                ProviderConfig(name="primary", base_url="https://primary.example", api_key="x"),
            ],
            routes=[
                RouteConfig(model="claude-model", provider="primary", provider_model="global-upstream"),
                RouteConfig(model="tool-model", provider="primary", provider_model="tool-upstream"),
            ],
        )
        main.app_config = config
        main.router = Router(config)
        main.call_logger = CallLogger(self.db_path)
        main.create_provider = self._create_provider
        router_module = __import__("app.router", fromlist=["create_provider"])
        router_module.create_provider = self._create_provider
        main.app.dependency_overrides[main.get_current_user_flexible] = lambda: {
            "id": 1,
            "username": "tester",
        }

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

    def test_messages_endpoint_accepts_x_api_key_auth(self):
        response = self.client.post(
            "/v1/messages",
            headers={"x-api-key": "ignored-by-override"},
            json={
                "model": "claude-model",
                "max_tokens": 128,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "message")
        self.assertEqual(body["model"], "claude-model")
        self.assertEqual(body["content"][0]["text"], "reply from global-upstream")

    def test_messages_endpoint_maps_tool_calls(self):
        response = self.client.post(
            "/v1/messages",
            headers={"x-api-key": "ignored-by-override"},
            json={
                "model": "tool-model",
                "max_tokens": 128,
                "messages": [{"role": "user", "content": "read file"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stop_reason"], "tool_use")
        self.assertEqual(body["content"][0]["type"], "tool_use")
        self.assertEqual(body["content"][0]["name"], "read_file")

    def test_messages_stream_returns_anthropic_sse(self):
        response = self.client.post(
            "/v1/messages",
            headers={"x-api-key": "ignored-by-override"},
            json={
                "model": "claude-model",
                "max_tokens": 128,
                "stream": True,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: message_start", response.text)
        self.assertIn("event: message_stop", response.text)

    def test_count_tokens_endpoint(self):
        response = self.client.post(
            "/v1/messages/count_tokens",
            headers={"x-api-key": "ignored-by-override"},
            json={
                "model": "claude-model",
                "messages": [{"role": "user", "content": "hello world"}],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["input_tokens"], 1)


if __name__ == "__main__":
    unittest.main()
