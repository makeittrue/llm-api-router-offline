from __future__ import annotations

"""Responses API 入站桥接：OpenAI Responses API ↔ OpenAI Chat Completions。

对称于 app/anthropic_bridge.py 的五段式结构：
  1. responses_to_chat_request      — Responses 请求 → ChatCompletionRequest
  2. openai_to_responses_response   — Chat 响应 → Responses response 对象
  3. openai_error_to_responses      — OpenAI error → Responses error
  4. ResponsesStreamConverter       — 流式 chunk → Responses SSE 事件
  5. responses_sse_from_openai_sse  — 异步生成器，解析上游 SSE 喂给转换器

DeepSeek Responses API 无状态（store / previous_response_id / conversation 不支持），
本桥接亦无状态。不支持的能力（reasoning.effort 透传、内置工具、有状态会话）静默忽略。
"""

import json
import time
import uuid
from typing import Any, AsyncIterator

from app.models import ChatCompletionRequest, ChatMessage


def _resp_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _normalize_json_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


# ============ 请求转换：Responses API → OpenAI Chat Completions ============


def _extract_message_text(content: Any) -> str:
    """从 Responses message item 的 content（str 或 content part 列表）提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype in ("input_text", "output_text", "text"):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif ptype == "input_image":
                # DeepSeek：图片不支持，替换为占位文本（与上游行为一致）
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def _input_items_to_messages(items: list[Any]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    pending_reasoning: str | None = None

    def _attach_reasoning(msg: ChatMessage | None) -> None:
        nonlocal pending_reasoning
        if pending_reasoning and msg is not None and msg.reasoning_content is None:
            msg.reasoning_content = pending_reasoning
        pending_reasoning = None

    for item in items:
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        # 兼容简化写法：无 type 但含 role → 视为 message
        if itype is None and isinstance(item.get("role"), str):
            itype = "message"

        if itype == "message":
            role = item.get("role") or "user"
            if role == "developer":
                role = "system"
            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"
            text = _extract_message_text(item.get("content"))
            msg = ChatMessage(role=role, content=text or "")
            _attach_reasoning(msg)
            messages.append(msg)

        elif itype == "function_call":
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            call_id = item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:12]}"
            tool_calls = [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": _normalize_json_string(item.get("arguments"))},
                }
            ]
            msg = ChatMessage(role="assistant", content=None, tool_calls=tool_calls)
            _attach_reasoning(msg)
            messages.append(msg)

        elif itype == "function_call_output":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                continue
            output = item.get("output")
            if isinstance(output, (dict, list)):
                output = json.dumps(output, ensure_ascii=False)
            elif output is None:
                output = ""
            messages.append(ChatMessage(role="tool", tool_call_id=call_id, content=str(output)))

        elif itype == "reasoning":
            # DeepSeek：明文 content 归并到相邻 assistant 消息的 reasoning_content
            content = item.get("content")
            if isinstance(content, str) and content:
                pending_reasoning = content
            elif isinstance(content, list):
                texts = [
                    p.get("text")
                    for p in content
                    if isinstance(p, dict) and isinstance(p.get("text"), str)
                ]
                if texts:
                    pending_reasoning = "\n".join(texts)
        # web_search_call / custom / 其他类型忽略

    # 末尾兜底：reasoning item 后若无相邻 assistant 消息，pending_reasoning 仍未消费
    if pending_reasoning:
        last = messages[-1] if messages else None
        if last is not None and last.role == "assistant" and last.reasoning_content is None:
            last.reasoning_content = pending_reasoning
        else:
            messages.append(ChatMessage(role="assistant", content="", reasoning_content=pending_reasoning))

    return messages


def _map_tools(tools: Any) -> list[dict[str, Any]] | None:
    if not isinstance(tools, list):
        return None
    out: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            continue  # web_search / file_search / code_interpreter 等忽略
        fn = tool.get("function")
        if not isinstance(fn, dict):
            fn = tool  # 允许扁平写法 {type:function, name, parameters}
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        func: dict[str, Any] = {"name": name}
        if isinstance(fn.get("description"), str):
            func["description"] = fn["description"]
        if isinstance(fn.get("parameters"), dict):
            func["parameters"] = fn["parameters"]
        if "strict" in fn:
            func["strict"] = fn["strict"]
        out.append({"type": "function", "function": func})
    return out or None


def _map_tool_choice(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice  # none / auto / required
    if not isinstance(tool_choice, dict):
        return None
    tctype = tool_choice.get("type")
    if tctype in ("auto", "none", "required"):
        return tctype
    if tctype == "function":
        name = tool_choice.get("name")
        if isinstance(name, str) and name:
            return {"type": "function", "function": {"name": name}}
    return None


def _map_text_format(text: Any) -> Any:
    if not isinstance(text, dict):
        return None
    fmt = text.get("format")
    if not isinstance(fmt, dict):
        return None
    ftype = fmt.get("type")
    if ftype == "json_schema":
        js: dict[str, Any] = {}
        for k in ("name", "description", "strict"):
            if k in fmt:
                js[k] = fmt[k]
        if isinstance(fmt.get("schema"), dict):
            js["schema"] = fmt["schema"]
        return {"type": "json_schema", "json_schema": js}
    if ftype in ("text", "json_object"):
        return {"type": ftype}
    return None


def responses_to_chat_request(body: dict[str, Any]) -> ChatCompletionRequest:
    """将 Responses API 请求体转为 ChatCompletionRequest。"""
    instructions = body.get("instructions")
    messages: list[ChatMessage] = []
    if isinstance(instructions, str) and instructions:
        messages.append(ChatMessage(role="system", content=instructions))

    input_val = body.get("input")
    if isinstance(input_val, str):
        messages.append(ChatMessage(role="user", content=input_val))
    elif isinstance(input_val, list):
        messages.extend(_input_items_to_messages(input_val))

    if not messages:
        raise ValueError("Responses API 请求必须提供 'input' 或 'instructions' 之一")

    max_output_tokens = body.get("max_output_tokens")
    payload: dict[str, Any] = {
        "model": body.get("model") or "",
        "messages": messages,
        "stream": bool(body.get("stream")),
        "temperature": body.get("temperature"),
        "top_p": body.get("top_p"),
        "max_tokens": max_output_tokens if isinstance(max_output_tokens, int) else None,
        "user": body.get("user"),
        "tools": _map_tools(body.get("tools")),
        "tool_choice": _map_tool_choice(body.get("tool_choice")),
        "response_format": _map_text_format(body.get("text")),
    }
    return ChatCompletionRequest.model_validate(payload)


# ============ 响应转换：OpenAI Chat Completions → Responses API ============


def _empty_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 0,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 0,
    }


def _map_usage_to_responses(usage: Any) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return None
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

    cached = 0
    ptd = usage.get("prompt_tokens_details")
    if isinstance(ptd, dict) and isinstance(ptd.get("cached_tokens"), int):
        cached = ptd["cached_tokens"]
    else:
        for key in ("cached_tokens", "prompt_cache_hit_tokens", "cache_hit_tokens"):
            v = usage.get(key)
            if isinstance(v, int) and v:
                cached = v
                break

    reasoning_tokens = 0
    ctd = usage.get("completion_tokens_details")
    if isinstance(ctd, dict) and isinstance(ctd.get("reasoning_tokens"), int):
        reasoning_tokens = ctd["reasoning_tokens"]

    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": cached},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
        "total_tokens": int(usage.get("total_tokens") or (input_tokens + output_tokens)),
    }


def _extract_chunk_usage(chunk: dict[str, Any]) -> dict[str, Any] | None:
    """提取流式 chunk 的 usage：先顶层，缺失时回退 choices[0].usage（兼容 Kimi 风格）。"""
    usage = chunk.get("usage")
    if isinstance(usage, dict):
        return usage
    for choice in chunk.get("choices") or []:
        if isinstance(choice, dict) and isinstance(choice.get("usage"), dict):
            return choice["usage"]
    return None


def _finish_to_status(finish_reason: str | None) -> tuple[str, dict[str, Any] | None]:
    if finish_reason == "length":
        return "incomplete", {"reason": "max_output_tokens"}
    return "completed", None


def extract_echo_fields(body: dict[str, Any]) -> dict[str, Any]:
    """从 Responses 请求体提取需在响应中回显的字段（temperature/top_p/tool_choice 等）。"""
    tc = body.get("tool_choice")
    ptc = body.get("parallel_tool_calls")
    reasoning = body.get("reasoning")
    return {
        "instructions": body.get("instructions") if isinstance(body.get("instructions"), str) else None,
        "max_output_tokens": body.get("max_output_tokens") if isinstance(body.get("max_output_tokens"), int) else None,
        "temperature": body.get("temperature"),
        "top_p": body.get("top_p"),
        "tool_choice": tc if tc is not None else "auto",
        "parallel_tool_calls": ptc if ptc is not None else True,
        "tools": body.get("tools") if isinstance(body.get("tools"), list) else [],
        "reasoning": reasoning if isinstance(reasoning, dict) else {"effort": None, "summary": None},
        "text": body.get("text") if isinstance(body.get("text"), dict) else None,
        "user": body.get("user"),
        "metadata": body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
        "truncation": body.get("truncation"),
    }


def _build_response_fields(
    *,
    response_id: str,
    created_at: int,
    status: str,
    error: Any,
    incomplete_details: dict[str, Any] | None,
    model: str,
    output: list[dict[str, Any]],
    usage: dict[str, Any],
    echo: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 Responses API response 对象的完整字段集（流式与非流式共用，避免重复定义）。"""
    echo = echo or {}
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "error": error,
        "incomplete_details": incomplete_details,
        "instructions": echo.get("instructions"),
        "max_output_tokens": echo.get("max_output_tokens"),
        "model": model,
        "output": output,
        "parallel_tool_calls": echo.get("parallel_tool_calls", True),
        "previous_response_id": None,
        "reasoning": echo.get("reasoning", {"effort": None, "summary": None}),
        "store": False,
        "temperature": echo.get("temperature"),
        "text": echo.get("text"),
        "tool_choice": echo.get("tool_choice", "auto"),
        "tools": echo.get("tools", []),
        "top_p": echo.get("top_p"),
        "truncation": echo.get("truncation"),
        "usage": usage,
        "user": echo.get("user"),
        "metadata": echo.get("metadata", {}),
    }


def openai_to_responses_response(
    data: dict[str, Any], *, requested_model: str, echo: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Chat Completions 响应 → Responses API response 对象。"""
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}

    output: list[dict[str, Any]] = []

    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        output.append(
            {
                "type": "reasoning",
                "id": _resp_id("rs"),
                "summary": [{"type": "summary_text", "text": reasoning_content}],
            }
        )

    text = message.get("content")
    if isinstance(text, str) and text:
        output.append(
            {
                "type": "message",
                "id": _resp_id("msg"),
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        )

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            if not isinstance(fn, dict):
                continue
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                continue
            output.append(
                {
                    "type": "function_call",
                    "id": _resp_id("fc"),
                    "status": "completed",
                    "call_id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    "name": name,
                    "arguments": _normalize_json_string(fn.get("arguments")),
                }
            )

    # deprecated 旧式 function_call（与 tool_calls 等价，兼容遗留上游）
    function_call = message.get("function_call")
    if isinstance(function_call, dict) and function_call.get("name"):
        output.append(
            {
                "type": "function_call",
                "id": _resp_id("fc"),
                "status": "completed",
                "call_id": function_call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "name": function_call["name"],
                "arguments": _normalize_json_string(function_call.get("arguments")),
            }
        )

    status, incomplete = _finish_to_status(choice.get("finish_reason"))
    return _build_response_fields(
        response_id=data.get("id") or _resp_id("resp"),
        created_at=int(data.get("created") or time.time()),
        status=status,
        error=None,
        incomplete_details=incomplete,
        model=requested_model,
        output=output,
        usage=_map_usage_to_responses(data.get("usage")) or _empty_usage(),
        echo=echo,
    )


def openai_error_to_responses(body: dict[str, Any]) -> dict[str, Any]:
    """OpenAI error body → Responses API error（结构与 chat error 一致）。"""
    err = body.get("error")
    if not isinstance(err, dict):
        return {
            "error": {
                "message": json.dumps(body, ensure_ascii=False),
                "type": "api_error",
                "param": None,
                "code": None,
            }
        }
    return {
        "error": {
            "message": err.get("message") or "Unknown error",
            "type": err.get("type") or "api_error",
            "param": err.get("param"),
            "code": err.get("code"),
        }
    }


# ============ 流式转换：OpenAI Chat SSE → Responses SSE ============


class ResponsesStreamConverter:
    """把 OpenAI Chat Completions 流式 chunk 序列转为 Responses API SSE 事件。

    事件序列对齐 DeepSeek / OpenAI Responses API：
      response.created → response.in_progress
      → (response.output_item.added → response.content_part.added
         → response.output_text.delta | response.reasoning_text.delta
         | response.function_call_arguments.delta → ...done → response.output_item.done)*
      → response.completed | response.failed
    每个事件携带递增的 sequence_number。
    """

    def __init__(self, *, requested_model: str, echo: dict[str, Any] | None = None):
        self.requested_model = requested_model
        self.echo = echo
        self.response_id = _resp_id("resp")
        self.created_at = int(time.time())
        self.seq = 0
        self.started = False
        self.finished = False
        self.usage: dict[str, Any] | None = None
        self.status = "completed"
        self.incomplete_details: dict[str, Any] | None = None

        # output items 状态，按 output_index 索引
        self.items: dict[int, dict[str, Any]] = {}
        self.tool_index_to_output: dict[int, int] = {}
        self.next_output_index = 0
        self.reasoning_output_index: int | None = None
        self.message_output_index: int | None = None

    def _next_seq(self) -> int:
        s = self.seq
        self.seq += 1
        return s

    def _emit(self, event_type: str, payload: dict[str, Any]) -> bytes:
        data = {"type": event_type, "sequence_number": self._next_seq(), **payload}
        return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")

    def _response_skeleton(self) -> dict[str, Any]:
        return _build_response_fields(
            response_id=self.response_id,
            created_at=self.created_at,
            status=self.status,
            error=None,
            incomplete_details=self.incomplete_details,
            model=self.requested_model,
            output=[],
            usage=self.usage or _empty_usage(),
            echo=self.echo,
        )

    def _ensure_started(self) -> list[bytes]:
        if self.started:
            return []
        self.started = True
        return [
            self._emit("response.created", {"response": self._response_skeleton()}),
            self._emit("response.in_progress", {"response": self._response_skeleton()}),
        ]

    def _ensure_reasoning_item(self) -> int:
        if self.reasoning_output_index is not None:
            return self.reasoning_output_index
        idx = self.next_output_index
        self.next_output_index += 1
        self.reasoning_output_index = idx
        self.items[idx] = {
            "type": "reasoning",
            "id": _resp_id("rs"),
            "text": "",
            "item_added": False,
        }
        return idx

    def _ensure_message_item(self) -> int:
        if self.message_output_index is not None:
            return self.message_output_index
        idx = self.next_output_index
        self.next_output_index += 1
        self.message_output_index = idx
        self.items[idx] = {
            "type": "message",
            "id": _resp_id("msg"),
            "text": "",
            "item_added": False,
            "part_added": False,
        }
        return idx

    def _ensure_function_call_item(self, tool_index: int) -> int:
        if tool_index in self.tool_index_to_output:
            return self.tool_index_to_output[tool_index]
        idx = self.next_output_index
        self.next_output_index += 1
        self.tool_index_to_output[tool_index] = idx
        self.items[idx] = {
            "type": "function_call",
            "id": _resp_id("fc"),
            "call_id": f"call_{uuid.uuid4().hex[:12]}",
            "name": "",
            "arguments": "",
            "item_added": False,
        }
        return idx

    def process_openai_chunk(self, chunk: dict[str, Any]) -> list[bytes]:
        out = self._ensure_started()

        usage = _extract_chunk_usage(chunk)
        if usage is not None:
            mapped = _map_usage_to_responses(usage)
            if mapped:
                self.usage = mapped

        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                self.status, self.incomplete_details = _finish_to_status(finish_reason)

            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue

            # 思考链（DeepSeek / MiMo 在 delta.reasoning_content 流式输出）
            reasoning = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                idx = self._ensure_reasoning_item()
                item = self.items[idx]
                if not item["item_added"]:
                    item["item_added"] = True
                    out.append(
                        self._emit(
                            "response.output_item.added",
                            {
                                "output_index": idx,
                                "item": {"type": "reasoning", "id": item["id"], "summary": []},
                            },
                        )
                    )
                out.append(
                    self._emit(
                        "response.reasoning_text.delta",
                        {"output_index": idx, "delta": reasoning},
                    )
                )
                item["text"] += reasoning

            # 文本输出
            content = delta.get("content")
            if isinstance(content, str) and content:
                idx = self._ensure_message_item()
                item = self.items[idx]
                if not item["item_added"]:
                    item["item_added"] = True
                    out.append(
                        self._emit(
                            "response.output_item.added",
                            {
                                "output_index": idx,
                                "item": {
                                    "type": "message",
                                    "id": item["id"],
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [],
                                },
                            },
                        )
                    )
                if not item["part_added"]:
                    item["part_added"] = True
                    out.append(
                        self._emit(
                            "response.content_part.added",
                            {
                                "output_index": idx,
                                "content_index": 0,
                                "part": {"type": "output_text", "text": "", "annotations": []},
                            },
                        )
                    )
                out.append(
                    self._emit(
                        "response.output_text.delta",
                        {"output_index": idx, "content_index": 0, "delta": content},
                    )
                )
                item["text"] += content

            # 工具调用（新式 tool_calls）
            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    tidx = int(call.get("index") or 0)
                    fn = call.get("function") or {}
                    out.extend(
                        self._function_call_delta(
                            tidx, call.get("id"), fn.get("name"), fn.get("arguments")
                        )
                    )

            # deprecated 旧式 function_call（与 tool_calls 等价，归到 index 0）
            fc = delta.get("function_call")
            if isinstance(fc, dict):
                out.extend(
                    self._function_call_delta(0, fc.get("id"), fc.get("name"), fc.get("arguments"))
                )

        return out

    def _function_call_delta(
        self, tool_index: int, call_id: Any, name: Any, arguments: Any
    ) -> list[bytes]:
        """发 function_call item 的 added / arguments.delta 事件（新式与旧式共用）。"""
        out: list[bytes] = []
        idx = self._ensure_function_call_item(tool_index)
        item = self.items[idx]
        if isinstance(call_id, str) and call_id:
            item["call_id"] = call_id
        if isinstance(name, str) and name:
            item["name"] = name
        if not item["item_added"]:
            item["item_added"] = True
            out.append(
                self._emit(
                    "response.output_item.added",
                    {
                        "output_index": idx,
                        "item": {
                            "type": "function_call",
                            "id": item["id"],
                            "status": "in_progress",
                            "call_id": item["call_id"],
                            "name": item["name"],
                            "arguments": "",
                        },
                    },
                )
            )
        if isinstance(arguments, str) and arguments:
            out.append(
                self._emit(
                    "response.function_call_arguments.delta",
                    {"output_index": idx, "delta": arguments},
                )
            )
            item["arguments"] += arguments
        return out

    def _build_final_output(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for idx in sorted(self.items.keys()):
            item = self.items[idx]
            if item["type"] == "reasoning":
                result.append(
                    {
                        "type": "reasoning",
                        "id": item["id"],
                        "summary": [{"type": "summary_text", "text": item["text"]}],
                    }
                )
            elif item["type"] == "message":
                result.append(
                    {
                        "type": "message",
                        "id": item["id"],
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": item["text"], "annotations": []}
                        ],
                    }
                )
            elif item["type"] == "function_call":
                result.append(
                    {
                        "type": "function_call",
                        "id": item["id"],
                        "status": "completed",
                        "call_id": item["call_id"],
                        "name": item["name"],
                        "arguments": item["arguments"],
                    }
                )
        return result

    def finish(self) -> list[bytes]:
        if self.finished:
            return []
        self.finished = True
        out = self._ensure_started()

        for idx in sorted(self.items.keys()):
            item = self.items[idx]
            if item["type"] == "reasoning":
                if item["item_added"]:
                    out.append(
                        self._emit(
                            "response.reasoning_text.done",
                            {"output_index": idx, "text": item["text"]},
                        )
                    )
                    out.append(
                        self._emit(
                            "response.output_item.done",
                            {
                                "output_index": idx,
                                "item": {
                                    "type": "reasoning",
                                    "id": item["id"],
                                    "summary": [{"type": "summary_text", "text": item["text"]}],
                                },
                            },
                        )
                    )
            elif item["type"] == "message":
                if item["part_added"]:
                    out.append(
                        self._emit(
                            "response.output_text.done",
                            {"output_index": idx, "content_index": 0, "text": item["text"]},
                        )
                    )
                    out.append(
                        self._emit(
                            "response.content_part.done",
                            {
                                "output_index": idx,
                                "content_index": 0,
                                "part": {
                                    "type": "output_text",
                                    "text": item["text"],
                                    "annotations": [],
                                },
                            },
                        )
                    )
                if item["item_added"]:
                    out.append(
                        self._emit(
                            "response.output_item.done",
                            {
                                "output_index": idx,
                                "item": {
                                    "type": "message",
                                    "id": item["id"],
                                    "status": "completed",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": item["text"],
                                            "annotations": [],
                                        }
                                    ],
                                },
                            },
                        )
                    )
            elif item["type"] == "function_call":
                if item["item_added"]:
                    out.append(
                        self._emit(
                            "response.function_call_arguments.done",
                            {"output_index": idx, "arguments": item["arguments"]},
                        )
                    )
                    out.append(
                        self._emit(
                            "response.output_item.done",
                            {
                                "output_index": idx,
                                "item": {
                                    "type": "function_call",
                                    "id": item["id"],
                                    "status": "completed",
                                    "call_id": item["call_id"],
                                    "name": item["name"],
                                    "arguments": item["arguments"],
                                },
                            },
                        )
                    )

        resp = self._response_skeleton()
        resp["output"] = self._build_final_output()
        out.append(self._emit("response.completed", {"response": resp}))
        return out

    def fail(self, error_body: dict[str, Any]) -> list[bytes]:
        """流式上游错误：发 response.failed（必要时补发 created/in_progress）。"""
        out = self._ensure_started()
        resp = self._response_skeleton()
        resp["status"] = "failed"
        resp["error"] = error_body.get("error") or error_body
        resp["output"] = self._build_final_output()
        out.append(self._emit("response.failed", {"response": resp}))
        return out


async def responses_sse_from_openai_sse(
    openai_stream: AsyncIterator[bytes],
    *,
    requested_model: str,
    echo: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    converter = ResponsesStreamConverter(requested_model=requested_model, echo=echo)
    parser_buf = ""
    async for chunk in openai_stream:
        parser_buf += chunk.decode("utf-8", errors="replace")
        lines = parser_buf.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            parser_buf = lines.pop()
        else:
            parser_buf = ""

        for line in lines:
            line = line.rstrip("\r\n")
            if not line.startswith("data: "):
                continue
            body = line[6:].strip()
            if body == "[DONE]":
                for event in converter.finish():
                    yield event
                return
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                continue
            if isinstance(data.get("error"), dict):
                for event in converter.fail(data):
                    yield event
                return
            for event in converter.process_openai_chunk(data):
                yield event

    # 处理末尾不完整的缓冲
    if parser_buf:
        line = parser_buf.rstrip("\r\n")
        if line.startswith("data: "):
            body = line[6:].strip()
            if body != "[DONE]":
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        if isinstance(data.get("error"), dict):
                            for event in converter.fail(data):
                                yield event
                            return
                        for event in converter.process_openai_chunk(data):
                            yield event
                except json.JSONDecodeError:
                    pass
    for event in converter.finish():
        yield event
