from __future__ import annotations

import json
import unittest

from app.config import ProviderConfig
from app.models import ChatCompletionRequest
from app.providers.base import OpenAICompatibleProvider


class ProviderPayloadSanitizationTests(unittest.TestCase):
    def test_build_payload_strips_nested_trae_specific_fields(self):
        provider = OpenAICompatibleProvider(
            ProviderConfig(
                name="test-provider",
                base_url="https://example.com/v1",
                api_key="secret",
            )
        )
        request = ChatCompletionRequest.model_validate(
            {
                "model": "trae-facing-model",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "hello",
                                "cache_control": {"type": "ephemeral"},
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": "https://example.com/demo.png"},
                                "extra": True,
                            },
                        ],
                    },
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "index": 0,
                                "function": {
                                    "name": "read_file",
                                    "arguments": {"path": "/tmp/demo.txt"},
                                    "extra": "drop-me",
                                },
                            }
                        ],
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "server": "trae",
                        "function": {
                            "name": "read_file",
                            "description": "Read a local file",
                            "parameters": {"type": "object"},
                            "strict": True,
                            "x-trae-meta": {"origin": "ide"},
                        },
                    }
                ],
                "tool_choice": {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": {"should_not": "pass-through"},
                    },
                    "x-trae": "drop-me",
                },
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "result",
                        "schema": {"type": "object"},
                        "strict": True,
                        "x-trae-meta": 1,
                    },
                    "extra": True,
                },
                "stream_options": {"include_usage": True},
                "default_models": ["fallback-a"],
            }
        )

        payload = provider._build_payload(
            request,
            provider_model="upstream-model",
            for_stream=False,
        )

        self.assertEqual(payload["model"], "upstream-model")
        self.assertNotIn("stream_options", payload)
        self.assertNotIn("default_models", payload)
        self.assertEqual(
            payload["messages"][0]["content"],
            [
                {"type": "text", "text": "hello"},
                {"type": "image_url", "image_url": {"url": "https://example.com/demo.png"}},
            ],
        )
        self.assertEqual(
            payload["messages"][1]["tool_calls"],
            [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": "/tmp/demo.txt"}, ensure_ascii=False),
                    },
                }
            ],
        )
        self.assertEqual(
            payload["tools"],
            [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a local file",
                        "parameters": {"type": "object"},
                        "strict": True,
                    },
                }
            ],
        )
        self.assertEqual(
            payload["tool_choice"],
            {"type": "function", "function": {"name": "read_file"}},
        )
        self.assertEqual(
            payload["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "schema": {"type": "object"},
                    "strict": True,
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
