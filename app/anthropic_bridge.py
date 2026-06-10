from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from app.models import ChatCompletionRequest, ChatMessage

_FINISH_TO_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}


def _normalize_json_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _parse_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _system_text(system: Any) -> str | None:
    if system is None:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts) if parts else None
    return None


def _anthropic_content_to_openai(content: Any) -> Any:
    if content is None or isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content

    text_parts: list[str] = []
    image_parts: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                text_parts.append(text)
        elif block_type == "image":
            source = block.get("source")
            if isinstance(source, dict) and source.get("type") == "base64":
                media_type = source.get("media_type") or "image/jpeg"
                data = source.get("data")
                if isinstance(data, str):
                    image_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{data}"},
                        }
                    )
        elif block_type == "tool_use":
            name = block.get("name")
            if isinstance(name, str) and name:
                tool_calls.append(
                    {
                        "id": block.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": _normalize_json_string(block.get("input") or {}),
                        },
                    }
                )
        elif block_type == "tool_result":
            tool_use_id = block.get("tool_use_id")
            if isinstance(tool_use_id, str) and tool_use_id:
                tool_results.append(
                    {
                        "tool_call_id": tool_use_id,
                        "content": _normalize_json_string(block.get("content") or ""),
                    }
                )

    if tool_results:
        return tool_results
    if tool_calls:
        msg: dict[str, Any] = {"tool_calls": tool_calls}
        if text_parts:
            msg["content"] = "\n".join(text_parts)
        else:
            msg["content"] = None
        return msg
    if image_parts:
        parts: list[dict[str, Any]] = []
        if text_parts:
            parts.append({"type": "text", "text": "\n".join(text_parts)})
        parts.extend(image_parts)
        return parts
    if text_parts:
        return "\n".join(text_parts)
    return ""


def _map_tool_choice(tool_choice: Any) -> Any:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return None
    choice_type = tool_choice.get("type")
    if choice_type == "auto":
        return "auto"
    if choice_type == "any":
        return "required"
    if choice_type == "none":
        return "none"
    if choice_type == "tool":
        name = tool_choice.get("name")
        if isinstance(name, str) and name:
            return {"type": "function", "function": {"name": name}}
    return "auto"


def _map_tools(tools: Any) -> list[dict[str, Any]] | None:
    if not isinstance(tools, list):
        return None
    out: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            continue
        fn: dict[str, Any] = {"name": name}
        if isinstance(tool.get("description"), str):
            fn["description"] = tool["description"]
        schema = tool.get("input_schema")
        if isinstance(schema, dict):
            fn["parameters"] = schema
        out.append({"type": "function", "function": fn})
    return out or None


def anthropic_to_chat_request(body: dict[str, Any]) -> ChatCompletionRequest:
    messages: list[ChatMessage] = []
    system = _system_text(body.get("system"))
    if system:
        messages.append(ChatMessage(role="system", content=system))

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in {"user", "assistant"}:
            continue
        converted = _anthropic_content_to_openai(msg.get("content"))
        if isinstance(converted, list) and converted and "tool_call_id" in converted[0]:
            for tool_result in converted:
                messages.append(
                    ChatMessage(
                        role="tool",
                        tool_call_id=tool_result["tool_call_id"],
                        content=tool_result["content"],
                    )
                )
            continue
        if isinstance(converted, dict) and "tool_calls" in converted:
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=converted.get("content"),
                    tool_calls=converted["tool_calls"],
                )
            )
            continue
        messages.append(ChatMessage(role=role, content=converted))

    metadata = body.get("metadata")
    user_id = None
    if isinstance(metadata, dict) and isinstance(metadata.get("user_id"), str):
        user_id = metadata["user_id"]

    payload: dict[str, Any] = {
        "model": body.get("model") or "",
        "messages": messages,
        "stream": bool(body.get("stream")),
        "temperature": body.get("temperature"),
        "top_p": body.get("top_p"),
        "max_tokens": body.get("max_tokens"),
        "stop": body.get("stop_sequences"),
        "user": user_id,
        "tools": _map_tools(body.get("tools")),
        "tool_choice": _map_tool_choice(body.get("tool_choice")),
    }
    return ChatCompletionRequest.model_validate(payload)


def _map_stop_reason(finish_reason: str | None) -> str:
    if not finish_reason:
        return "end_turn"
    return _FINISH_TO_STOP.get(finish_reason, "end_turn")


def _map_usage(usage: Any) -> dict[str, int] | None:
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens": int(usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("completion_tokens") or 0),
    }


def openai_to_anthropic_response(data: dict[str, Any], *, requested_model: str) -> dict[str, Any]:
    choices = data.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}
    content_blocks: list[dict[str, Any]] = []

    text = message.get("content")
    if isinstance(text, str) and text:
        content_blocks.append({"type": "text", "text": text})

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
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                    "name": name,
                    "input": _parse_json_value(fn.get("arguments") or "{}"),
                }
            )

    function_call = message.get("function_call")
    if isinstance(function_call, dict) and function_call.get("name"):
        content_blocks.append(
            {
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:12]}",
                "name": function_call["name"],
                "input": _parse_json_value(function_call.get("arguments") or "{}"),
            }
        )

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    response: dict[str, Any] = {
        "id": data.get("id") or f"msg_{uuid.uuid4().hex[:12]}",
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content_blocks,
        "stop_reason": _map_stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
    }
    usage = _map_usage(data.get("usage"))
    if usage:
        response["usage"] = usage
    return response


def openai_error_to_anthropic(body: dict[str, Any]) -> dict[str, Any]:
    err = body.get("error")
    if not isinstance(err, dict):
        return {
            "type": "error",
            "error": {"type": "api_error", "message": json.dumps(body, ensure_ascii=False)},
        }
    err_type = err.get("type") or "api_error"
    if err_type == "invalid_request_error":
        mapped_type = "invalid_request_error"
    elif err_type == "authentication_error":
        mapped_type = "authentication_error"
    elif err_type == "permission_denied_error":
        mapped_type = "permission_error"
    elif err_type == "not_found_error":
        mapped_type = "not_found_error"
    elif err_type == "rate_limit_error":
        mapped_type = "rate_limit_error"
    else:
        mapped_type = "api_error"
    return {
        "type": "error",
        "error": {
            "type": mapped_type,
            "message": err.get("message") or "Unknown error",
        },
    }


def count_anthropic_tokens(body: dict[str, Any]) -> dict[str, Any]:
    total_chars = 0
    system = _system_text(body.get("system"))
    if system:
        total_chars += len(system)
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            total_chars += len(json.dumps(content, ensure_ascii=False))
    tools = body.get("tools")
    if isinstance(tools, list):
        total_chars += len(json.dumps(tools, ensure_ascii=False))

    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        if system:
            total += len(enc.encode(system))
        for msg in body.get("messages") or []:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                total += len(enc.encode(json.dumps(content, ensure_ascii=False)))
        if isinstance(tools, list):
            total += len(enc.encode(json.dumps(tools, ensure_ascii=False)))
        return {"input_tokens": max(total, 1 if total_chars else 0)}
    except Exception:
        return {"input_tokens": max(total_chars // 4, 1 if total_chars else 0)}


class AnthropicStreamConverter:
    def __init__(self, *, requested_model: str):
        self.requested_model = requested_model
        self.message_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.started = False
        self.finished = False
        self.text_block_started = False
        self.text_block_index = 0
        self.tool_blocks: dict[int, dict[str, Any]] = {}
        self.next_block_index = 0
        self.stop_reason: str | None = None
        self.usage: dict[str, int] | None = None

    def _emit(self, event_type: str, payload: dict[str, Any]) -> bytes:
        return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

    def _ensure_started(self) -> list[bytes]:
        if self.started:
            return []
        self.started = True
        return [
            self._emit(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": self.message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": self.requested_model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
        ]

    def _ensure_text_block(self) -> list[bytes]:
        if self.text_block_started:
            return []
        self.text_block_started = True
        self.next_block_index = max(self.next_block_index, 1)
        return [
            self._emit(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": self.text_block_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
        ]

    def _tool_block_index(self, tool_index: int) -> int:
        if tool_index not in self.tool_blocks:
            block_index = self.next_block_index
            self.next_block_index += 1
            self.tool_blocks[tool_index] = {
                "block_index": block_index,
                "started": False,
                "tool_use_id": f"toolu_{uuid.uuid4().hex[:12]}",
                "name": "",
            }
        return self.tool_blocks[tool_index]["block_index"]

    def process_openai_chunk(self, chunk: dict[str, Any]) -> list[bytes]:
        out = self._ensure_started()
        if isinstance(chunk.get("usage"), dict):
            mapped = _map_usage(chunk["usage"])
            if mapped:
                self.usage = mapped

        for choice in chunk.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            finish_reason = choice.get("finish_reason")
            if finish_reason:
                self.stop_reason = _map_stop_reason(finish_reason)

            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                continue

            content = delta.get("content")
            if isinstance(content, str) and content:
                out.extend(self._ensure_text_block())
                out.append(
                    self._emit(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": self.text_block_index,
                            "delta": {"type": "text_delta", "text": content},
                        },
                    )
                )

            function_call = delta.get("function_call")
            if isinstance(function_call, dict):
                out.extend(self._handle_tool_delta(0, function_call))

            tool_calls = delta.get("tool_calls")
            if isinstance(tool_calls, list):
                for call in tool_calls:
                    if not isinstance(call, dict):
                        continue
                    idx = int(call.get("index") or 0)
                    fn = call.get("function")
                    if not isinstance(fn, dict):
                        fn = {}
                    merged: dict[str, Any] = {}
                    if isinstance(call.get("id"), str):
                        merged["id"] = call["id"]
                    if isinstance(fn.get("name"), str):
                        merged["name"] = fn["name"]
                    if "arguments" in fn:
                        merged["arguments"] = fn.get("arguments")
                    out.extend(self._handle_tool_delta(idx, merged))

        return out

    def _handle_tool_delta(self, tool_index: int, data: dict[str, Any]) -> list[bytes]:
        out: list[bytes] = []
        block_index = self._tool_block_index(tool_index)
        state = self.tool_blocks[tool_index]

        if isinstance(data.get("id"), str):
            state["tool_use_id"] = data["id"]
        if isinstance(data.get("name"), str) and data["name"]:
            state["name"] = data["name"]

        if not state["started"]:
            state["started"] = True
            out.append(
                self._emit(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": state["tool_use_id"],
                            "name": state["name"] or "tool",
                            "input": {},
                        },
                    },
                )
            )

        arguments = data.get("arguments")
        if isinstance(arguments, str) and arguments:
            out.append(
                self._emit(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "input_json_delta", "partial_json": arguments},
                    },
                )
            )
        return out

    def finish(self) -> list[bytes]:
        if self.finished:
            return []
        self.finished = True
        out = self._ensure_started()

        if self.text_block_started:
            out.append(
                self._emit(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": self.text_block_index},
                )
            )
        for state in self.tool_blocks.values():
            if state["started"]:
                out.append(
                    self._emit(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": state["block_index"]},
                    )
                )

        delta: dict[str, Any] = {"stop_reason": self.stop_reason or "end_turn"}
        message_delta: dict[str, Any] = {"type": "message_delta", "delta": delta}
        if self.usage:
            message_delta["usage"] = self.usage
        out.append(self._emit("message_delta", message_delta))
        out.append(self._emit("message_stop", {"type": "message_stop"}))
        return out


async def anthropic_sse_from_openai_sse(
    openai_stream: AsyncIterator[bytes],
    *,
    requested_model: str,
) -> AsyncIterator[bytes]:
    converter = AnthropicStreamConverter(requested_model=requested_model)
    parser_buf = ""
    async for chunk in openai_stream:
        chunk_str = chunk.decode("utf-8", errors="replace")
        parser_buf += chunk_str
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
                yield converter._emit(
                    "error",
                    openai_error_to_anthropic(data),
                )
                return
            for event in converter.process_openai_chunk(data):
                yield event

    if parser_buf:
        line = parser_buf.rstrip("\r\n")
        if line.startswith("data: "):
            body = line[6:].strip()
            if body != "[DONE]":
                try:
                    data = json.loads(body)
                    if isinstance(data, dict):
                        for event in converter.process_openai_chunk(data):
                            yield event
                except json.JSONDecodeError:
                    pass
    for event in converter.finish():
        yield event
