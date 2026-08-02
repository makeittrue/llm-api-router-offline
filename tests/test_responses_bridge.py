from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.responses_bridge import (
    ResponsesStreamConverter,
    openai_to_responses_response,
    responses_to_chat_request,
)
from app.config import AppConfig, BillingConfig, BillingRuleConfig, LogConfig, ProviderConfig, RouteConfig
from app.logger import CallLogger
from app.providers.base import BaseProvider
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
        if provider_model == "tool-stream-upstream":
            chunks = [
                {
                    "id": "stream-tool",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": provider_model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_abc",
                                        "function": {"name": "read_file", "arguments": ""},
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "stream-tool",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": provider_model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": "{\"path\":\"/tmp\"}"}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                {
                    "id": "stream-tool",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": provider_model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
                },
            ]
            for chunk in chunks:
                yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
            return

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
                    {"index": 0, "delta": {"content": "hello"}, "finish_reason": None}
                ],
            },
            {
                "id": "stream-1",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": provider_model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ]
        for chunk in chunks:
            yield f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


class ResponsesBridgeTests(unittest.TestCase):
    def test_responses_to_chat_request_input_string_and_instructions(self):
        body = {
            "model": "my-model",
            "instructions": "You are helpful.",
            "input": "你好",
            "temperature": 0.5,
            "max_output_tokens": 256,
        }
        req = responses_to_chat_request(body)
        self.assertEqual(req.model, "my-model")
        self.assertEqual(req.temperature, 0.5)
        self.assertEqual(req.max_tokens, 256)
        self.assertEqual(req.messages[0].role, "system")
        self.assertEqual(req.messages[0].content, "You are helpful.")
        self.assertEqual(req.messages[1].role, "user")
        self.assertEqual(req.messages[1].content, "你好")

    def test_responses_to_chat_request_items_function_call_and_output(self):
        body = {
            "model": "my-model",
            "input": [
                {"role": "user", "content": "read file"},
                {
                    "type": "function_call",
                    "call_id": "call_abc",
                    "name": "read_file",
                    "arguments": "{\"path\": \"/tmp/demo.txt\"}",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_abc",
                    "output": "file body",
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ],
            "tool_choice": {"type": "function", "name": "read_file"},
        }
        req = responses_to_chat_request(body)
        # system 无，user / assistant(tool_calls) / tool
        self.assertEqual(req.messages[0].role, "user")
        self.assertEqual(req.messages[1].role, "assistant")
        self.assertEqual(req.messages[1].tool_calls[0]["id"], "call_abc")
        self.assertEqual(req.messages[1].tool_calls[0]["function"]["name"], "read_file")
        self.assertEqual(req.messages[2].role, "tool")
        self.assertEqual(req.messages[2].tool_call_id, "call_abc")
        self.assertEqual(req.messages[2].content, "file body")
        self.assertEqual(req.tools[0]["function"]["name"], "read_file")
        self.assertEqual(req.tool_choice, {"type": "function", "function": {"name": "read_file"}})

    def test_responses_to_chat_request_developer_role_maps_to_system(self):
        body = {
            "model": "m",
            "input": [
                {"role": "developer", "content": [{"type": "input_text", "text": "dev instr"}]},
                {"role": "user", "content": "hi"},
            ],
        }
        req = responses_to_chat_request(body)
        self.assertEqual(req.messages[0].role, "system")
        self.assertEqual(req.messages[0].content, "dev instr")

    def test_responses_to_chat_request_requires_input_or_instructions(self):
        with self.assertRaises(ValueError):
            responses_to_chat_request({"model": "m"})

    def test_openai_to_responses_response_text_and_usage(self):
        resp = openai_to_responses_response(
            {
                "id": "chatcmpl-1",
                "created": 1700000000,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "hello world"},
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            },
            requested_model="my-model",
        )
        self.assertEqual(resp["object"], "response")
        self.assertEqual(resp["status"], "completed")
        self.assertEqual(resp["model"], "my-model")
        self.assertEqual(resp["store"], False)
        self.assertEqual(resp["previous_response_id"], None)
        self.assertEqual(resp["output"][0]["type"], "message")
        self.assertEqual(resp["output"][0]["content"][0]["type"], "output_text")
        self.assertEqual(resp["output"][0]["content"][0]["text"], "hello world")
        self.assertEqual(resp["usage"]["input_tokens"], 3)
        self.assertEqual(resp["usage"]["output_tokens"], 5)
        self.assertEqual(resp["usage"]["total_tokens"], 8)

    def test_openai_to_responses_response_tool_calls(self):
        resp = openai_to_responses_response(
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
                                        "arguments": "{\"path\": \"/tmp\"}",
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            },
            requested_model="my-model",
        )
        self.assertEqual(resp["output"][0]["type"], "function_call")
        self.assertEqual(resp["output"][0]["call_id"], "call_abc")
        self.assertEqual(resp["output"][0]["name"], "read_file")
        self.assertEqual(resp["output"][0]["arguments"], "{\"path\": \"/tmp\"}")

    def test_openai_to_responses_response_length_is_incomplete(self):
        resp = openai_to_responses_response(
            {
                "id": "chatcmpl-1",
                "choices": [
                    {"finish_reason": "length", "message": {"role": "assistant", "content": "..."}}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
            requested_model="m",
        )
        self.assertEqual(resp["status"], "incomplete")
        self.assertEqual(resp["incomplete_details"], {"reason": "max_output_tokens"})

    def test_stream_converter_emits_text_events(self):
        converter = ResponsesStreamConverter(requested_model="my-model")
        events: list[bytes] = []
        events.extend(converter.process_openai_chunk({"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]}))
        events.extend(converter.process_openai_chunk({"choices": [{"delta": {"content": "hello"}, "finish_reason": None}]}))
        events.extend(
            converter.process_openai_chunk(
                {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
            )
        )
        events.extend(converter.finish())
        text = b"".join(events).decode("utf-8")
        self.assertIn("event: response.created", text)
        self.assertIn("event: response.in_progress", text)
        self.assertIn("event: response.output_item.added", text)
        self.assertIn("event: response.content_part.added", text)
        self.assertIn("response.output_text.delta", text)
        self.assertIn('"delta": "hello"', text)
        self.assertIn("event: response.output_text.done", text)
        self.assertIn("event: response.content_part.done", text)
        self.assertIn("event: response.output_item.done", text)
        self.assertIn("event: response.completed", text)
        # sequence_number 递增出现
        self.assertIn("sequence_number", text)

    def test_stream_converter_emits_function_call_events(self):
        converter = ResponsesStreamConverter(requested_model="my-model")
        events: list[bytes] = []
        events.extend(
            converter.process_openai_chunk(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "id": "call_abc", "function": {"name": "read_file", "arguments": ""}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            )
        )
        events.extend(
            converter.process_openai_chunk(
                {
                    "choices": [
                        {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"path\":\"/tmp\"}"}}]}, "finish_reason": None}
                    ]
                }
            )
        )
        events.extend(
            converter.process_openai_chunk({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        )
        events.extend(converter.finish())
        text = b"".join(events).decode("utf-8")
        self.assertIn("response.function_call_arguments.delta", text)
        self.assertIn("response.function_call_arguments.done", text)
        self.assertIn("\"name\": \"read_file\"", text)
        self.assertIn("event: response.completed", text)
        # completed 事件里的 response.output 应含 function_call item
        self.assertIn("\"type\": \"function_call\"", text)

    def test_openai_to_responses_response_reasoning_content(self):
        resp = openai_to_responses_response(
            {
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "answer",
                            "reasoning_content": "let me think",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
            requested_model="m",
        )
        self.assertEqual(resp["output"][0]["type"], "reasoning")
        self.assertEqual(resp["output"][0]["summary"][0]["type"], "summary_text")
        self.assertEqual(resp["output"][0]["summary"][0]["text"], "let me think")
        self.assertEqual(resp["output"][1]["type"], "message")
        self.assertEqual(resp["output"][1]["content"][0]["text"], "answer")

    def test_stream_converter_emits_reasoning_events(self):
        converter = ResponsesStreamConverter(requested_model="m")
        events: list[bytes] = []
        events.extend(converter.process_openai_chunk(
            {"choices": [{"delta": {"reasoning_content": "thinking"}, "finish_reason": None}]}
        ))
        events.extend(converter.process_openai_chunk(
            {"choices": [{"delta": {"content": "answer"}, "finish_reason": None}]}
        ))
        events.extend(converter.process_openai_chunk(
            {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        ))
        events.extend(converter.finish())
        text = b"".join(events).decode("utf-8")
        self.assertIn("response.reasoning_text.delta", text)
        self.assertIn("response.reasoning_text.done", text)
        self.assertIn("\"delta\": \"thinking\"", text)
        self.assertIn("response.output_text.delta", text)
        # 最终 reasoning item 的 summary 应承载思考链文本
        self.assertIn("\"summary_text\"", text)
        self.assertIn("\"text\": \"thinking\"", text)

    def test_stream_converter_usage_in_choices(self):
        """Issue 1: 流式 usage 在 choices[0].usage（Kimi 风格）应被捕获。"""
        converter = ResponsesStreamConverter(requested_model="m")
        events: list[bytes] = []
        events.extend(converter.process_openai_chunk(
            {"choices": [{"delta": {"content": "hi"}, "finish_reason": None,
                          "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}}]}
        ))
        events.extend(converter.finish())
        text = b"".join(events).decode("utf-8")
        self.assertIn("\"input_tokens\": 5", text)
        self.assertIn("\"output_tokens\": 7", text)

    def test_responses_to_chat_request_reasoning_merge(self):
        """reasoning item 归并到相邻 assistant message 的 reasoning_content。"""
        body = {
            "model": "m",
            "input": [
                {"type": "reasoning", "content": "thought"},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]},
            ],
        }
        req = responses_to_chat_request(body)
        self.assertEqual(req.messages[0].role, "assistant")
        self.assertEqual(req.messages[0].reasoning_content, "thought")

    def test_responses_to_chat_request_reasoning_tail_fallback(self):
        """reasoning 在末尾无后续 assistant：兜底生成带 reasoning_content 的 assistant 消息。"""
        body = {
            "model": "m",
            "input": [
                {"type": "message", "role": "user", "content": "hi"},
                {"type": "reasoning", "content": "orphan thought"},
            ],
        }
        req = responses_to_chat_request(body)
        self.assertEqual(req.messages[0].role, "user")
        last = req.messages[-1]
        self.assertEqual(last.role, "assistant")
        self.assertEqual(last.reasoning_content, "orphan thought")

    def test_responses_to_chat_request_text_format_json_schema(self):
        body = {
            "model": "m",
            "input": "hi",
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "result",
                    "schema": {"type": "object"},
                    "strict": True,
                }
            },
        }
        req = responses_to_chat_request(body)
        self.assertEqual(req.response_format["type"], "json_schema")
        self.assertEqual(req.response_format["json_schema"]["name"], "result")
        self.assertTrue(req.response_format["json_schema"]["strict"])

    def test_responses_to_chat_request_tool_choice_strings(self):
        req = responses_to_chat_request({"model": "m", "input": "hi", "tool_choice": "required"})
        self.assertEqual(req.tool_choice, "required")
        req2 = responses_to_chat_request({"model": "m", "input": "hi", "tool_choice": "none"})
        self.assertEqual(req2.tool_choice, "none")

    def test_openai_to_responses_response_echo_fields(self):
        """Issue 2: response 应回显请求的 temperature/top_p/tool_choice/parallel_tool_calls。"""
        echo = {
            "temperature": 0.7,
            "top_p": 0.9,
            "tool_choice": "none",
            "parallel_tool_calls": False,
            "max_output_tokens": 128,
            "instructions": "be nice",
        }
        resp = openai_to_responses_response(
            {
                "id": "chatcmpl-1",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            requested_model="m",
            echo=echo,
        )
        self.assertEqual(resp["temperature"], 0.7)
        self.assertEqual(resp["top_p"], 0.9)
        self.assertEqual(resp["tool_choice"], "none")
        self.assertEqual(resp["parallel_tool_calls"], False)
        self.assertEqual(resp["max_output_tokens"], 128)
        self.assertEqual(resp["instructions"], "be nice")

    def test_openai_to_responses_response_defaults_without_echo(self):
        """无 echo 时回退默认值（tool_choice=auto, parallel_tool_calls=True）。"""
        resp = openai_to_responses_response(
            {
                "id": "chatcmpl-1",
                "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            requested_model="m",
        )
        self.assertEqual(resp["tool_choice"], "auto")
        self.assertEqual(resp["parallel_tool_calls"], True)
        self.assertIsNone(resp["temperature"])
        self.assertIsNone(resp["top_p"])


class ResponsesEndpointTests(unittest.TestCase):
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
                RouteConfig(model="resp-model", provider="primary", provider_model="global-upstream"),
                RouteConfig(model="tool-model", provider="primary", provider_model="tool-upstream"),
                RouteConfig(model="tool-stream-model", provider="primary", provider_model="tool-stream-upstream"),
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

    def test_responses_endpoint_non_stream(self):
        response = self.client.post(
            "/v1/responses",
            headers={"x-api-key": "ignored-by-override"},
            json={"model": "resp-model", "input": "你好"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["object"], "response")
        self.assertEqual(body["model"], "resp-model")
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["output"][0]["type"], "message")
        self.assertEqual(body["output"][0]["content"][0]["text"], "reply from global-upstream")

    def test_responses_endpoint_accepts_bearer_auth(self):
        response = self.client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer ignored-by-override"},
            json={"model": "resp-model", "input": "hi", "instructions": "be nice"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["output"][0]["content"][0]["text"], "reply from global-upstream")

    def test_responses_endpoint_tool_calls(self):
        response = self.client.post(
            "/v1/responses",
            headers={"x-api-key": "ignored-by-override"},
            json={"model": "tool-model", "input": "read file"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["output"][0]["type"], "function_call")
        self.assertEqual(body["output"][0]["name"], "read_file")
        self.assertEqual(body["output"][0]["call_id"], "call_abc")

    def test_responses_endpoint_stream(self):
        response = self.client.post(
            "/v1/responses",
            headers={"x-api-key": "ignored-by-override"},
            json={"model": "resp-model", "input": "hello", "stream": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: response.created", response.text)
        self.assertIn("response.output_text.delta", response.text)
        self.assertIn("event: response.completed", response.text)

    def test_responses_endpoint_stream_tool_calls(self):
        response = self.client.post(
            "/v1/responses",
            headers={"x-api-key": "ignored-by-override"},
            json={"model": "tool-stream-model", "input": "read file", "stream": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("response.function_call_arguments.delta", response.text)
        self.assertIn("event: response.completed", response.text)
        self.assertIn("\"type\": \"function_call\"", response.text)

    def test_responses_endpoint_echo_fields(self):
        response = self.client.post(
            "/v1/responses",
            headers={"x-api-key": "ignored-by-override"},
            json={
                "model": "resp-model",
                "input": "hi",
                "temperature": 0.3,
                "top_p": 0.8,
                "tool_choice": "none",
                "parallel_tool_calls": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["temperature"], 0.3)
        self.assertEqual(body["top_p"], 0.8)
        self.assertEqual(body["tool_choice"], "none")
        self.assertEqual(body["parallel_tool_calls"], False)

    def test_responses_endpoint_missing_input_returns_400(self):
        response = self.client.post(
            "/v1/responses",
            headers={"x-api-key": "ignored-by-override"},
            json={"model": "resp-model"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())


if __name__ == "__main__":
    unittest.main()
