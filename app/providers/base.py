from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from app.config import ProviderConfig
from app.models import ChatCompletionRequest

# Trae / Cursor 等会通过 OpenAI SDK 附带大量非标准字段；Pydantic extra 会原样转发，
# 自建 MiMo / vLLM 等严格校验时常见 400「Param Incorrect」。仅转发 OpenAI Chat 标准键。
_OPENAI_CHAT_TOP_LEVEL_KEYS = frozenset(
    {
        "messages",
        "temperature",
        "top_p",
        "n",
        "stream",
        "stop",
        "max_tokens",
        "max_completion_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "seed",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "response_format",
        "modalities",
    }
)

_OPENAI_MESSAGE_KEYS = frozenset(
    {
        "role",
        "content",
        "name",
        "tool_calls",
        "tool_call_id",
        "function_call",
        # 小米 MiMo thinking 模式：见上游错误 param「reasoning_content ... must be passed back」
        "reasoning_content",
        "reasoning",
    }
)


def _sanitize_messages(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg, dict):
            out.append({k: v for k, v in msg.items() if k in _OPENAI_MESSAGE_KEYS})
        else:
            # ChatMessage 等模型实例
            raw = msg.model_dump(mode="python", exclude_none=True)  # type: ignore[union-attr]
            out.append({k: v for k, v in raw.items() if k in _OPENAI_MESSAGE_KEYS})
    return out


def _needs_mimo_reasoning_echo_pad(provider_model: str, base_url: str) -> bool:
    """MiMo thinking 模式多轮对话要求回传 assistant 的 reasoning_content；部分客户端（如 Trae）在 tool 轮会漏该字段。"""
    pm = (provider_model or "").lower()
    bu = (base_url or "").lower()
    if "xiaomimimo" in bu:
        return True
    if "mimo" in pm:
        return True
    return False


def _pad_mimo_reasoning_in_messages(messages: list[dict[str, Any]]) -> None:
    """对齐 Hermes / 官方说明：缺省或空字符串时用单个空格占位，避免上游 400。"""
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        rc = msg.get("reasoning_content")
        rs = msg.get("reasoning")
        if (rc is None or rc == "") and isinstance(rs, str) and rs != "":
            msg["reasoning_content"] = rs
            rc = msg["reasoning_content"]
        if rc is None or rc == "":
            msg["reasoning_content"] = " "


def _debug_message_summary(messages: list[dict[str, Any]], *, tail: int = 8) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    start = max(0, len(messages) - tail)
    for idx, msg in enumerate(messages[start:], start=start):
        content = msg.get("content")
        if isinstance(content, str):
            content_kind = "text"
            content_chars = len(content)
            preview = content[:120] + ("..." if len(content) > 120 else "")
        elif isinstance(content, list):
            content_kind = "parts"
            content_chars = len(json.dumps(content, ensure_ascii=False))
            preview = json.dumps(content[:2], ensure_ascii=False)[:120]
        elif content is None:
            content_kind = "none"
            content_chars = 0
            preview = None
        else:
            content_kind = type(content).__name__
            rendered = str(content)
            content_chars = len(rendered)
            preview = rendered[:120] + ("..." if len(rendered) > 120 else "")

        tool_calls = msg.get("tool_calls")
        summary.append(
            {
                "idx": idx,
                "role": msg.get("role"),
                "content_kind": content_kind,
                "content_chars": content_chars,
                "has_reasoning_content": isinstance(msg.get("reasoning_content"), str),
                "reasoning_chars": len(msg.get("reasoning_content") or ""),
                "has_reasoning": isinstance(msg.get("reasoning"), str),
                "reasoning_alias_chars": len(msg.get("reasoning") or ""),
                "tool_call_count": len(tool_calls) if isinstance(tool_calls, list) else 0,
                "tool_call_id": msg.get("tool_call_id"),
                "preview": preview,
            }
        )
    return summary


def _debug_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    return {
        "model": payload.get("model"),
        "stream": payload.get("stream"),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "tools_count": len(payload.get("tools")) if isinstance(payload.get("tools"), list) else 0,
        "has_tool_choice": payload.get("tool_choice") is not None,
        "tail_messages": _debug_message_summary(messages or []),
    }


def _needs_reasoning_echo_retry(body: dict[str, Any]) -> bool:
    err = body.get("error")
    if not isinstance(err, dict):
        return False
    merged = " ".join(
        str(v).lower()
        for v in (err.get("message"), err.get("param"), err.get("code"), err.get("type"))
        if v is not None
    )
    return "reasoning_content" in merged and "passed back" in merged


def _strip_empty_tools(payload: dict[str, Any]) -> None:
    tools = payload.get("tools")
    if tools is None or tools == []:
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        payload.pop("parallel_tool_calls", None)


class UpstreamError(Exception):
    """上游 HTTP 失败时透出状态码与本 body（常为 OpenAI 风格的 `{\"error\":...}`）。"""

    def __init__(self, status_code: int, body: dict[str, Any]):
        self.status_code = status_code
        self.body = body
        super().__init__(json.dumps(body, ensure_ascii=False))

    @classmethod
    def from_httpx_response(cls, resp: httpx.Response) -> UpstreamError:
        raw = resp.text
        status = resp.status_code
        try:
            parsed = resp.json()
        except json.JSONDecodeError:
            return cls.from_text(status, raw)
        if isinstance(parsed, dict):
            return cls(status, parsed)
        return cls.from_text(status, raw)

    @classmethod
    def from_text(cls, status_code: int, text: str) -> UpstreamError:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return cls(status_code, parsed)
        return cls(
            status_code,
            {
                "error": {
                    "message": text or f"HTTP {status_code}",
                    "type": "upstream_error",
                    "code": f"http_{status_code}",
                }
            },
        )


class BaseProvider(ABC):
    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def chat_completion(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def chat_completion_stream(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> AsyncIterator[bytes]: ...


class OpenAICompatibleProvider(BaseProvider):
    def _build_url(self, path: str) -> str:
        base = self.config.base_url.rstrip("/")
        return f"{base}{path}"

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        request: ChatCompletionRequest,
        provider_model: str,
        *,
        for_stream: bool,
        force_reasoning_echo_pad: bool = False,
    ) -> dict[str, Any]:
        raw = request.model_dump(mode="python", exclude_none=True)
        payload: dict[str, Any] = {
            k: v for k, v in raw.items() if k in _OPENAI_CHAT_TOP_LEVEL_KEYS
        }
        payload["messages"] = _sanitize_messages(raw.get("messages", []))
        if force_reasoning_echo_pad or _needs_mimo_reasoning_echo_pad(provider_model, self.config.base_url):
            _pad_mimo_reasoning_in_messages(payload["messages"])
        payload["model"] = provider_model
        # Trae / 新版 OpenAI SDK 会带 stream_options；多数自建兼容服务不认该字段 → 400
        payload.pop("stream_options", None)
        # 同时传两个上限时，部分上游只接受其一
        if payload.get("max_tokens") is not None and payload.get("max_completion_tokens") is not None:
            payload.pop("max_completion_tokens", None)
        _strip_empty_tools(payload)
        # 流式仅支持 n=1；部分上游对显式 n 也不兼容，直接省略
        if for_stream:
            payload.pop("n", None)
        return payload

    async def chat_completion(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> dict[str, Any]:
        url = self._build_url("/chat/completions")
        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                force_reasoning_echo_pad = False
                for attempt in range(2):
                    payload = self._build_payload(
                        request,
                        provider_model,
                        for_stream=False,
                        force_reasoning_echo_pad=force_reasoning_echo_pad,
                    )
                    payload["stream"] = False

                    print(f"[DEBUG] Sending to {url} payload:")
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                    print("[DEBUG] Payload summary:")
                    print(json.dumps(_debug_payload_summary(payload), indent=2, ensure_ascii=False))

                    resp = await client.post(url, headers=headers, json=payload)
                    print(f"[DEBUG] Response status: {resp.status_code}")
                    if resp.status_code < 400:
                        return resp.json()

                    print(f"[DEBUG] Error response content: {resp.text}")
                    err = UpstreamError.from_httpx_response(resp)
                    retryable = attempt == 0 and _needs_reasoning_echo_retry(err.body)
                    if retryable:
                        print("[DEBUG] Retrying with assistant reasoning_content padding")
                        force_reasoning_echo_pad = True
                        continue
                    raise err
        except UpstreamError:
            raise
        except Exception as e:
            print(f"[ERROR] Chat completion transport error: {e}")
            raise

    async def chat_completion_stream(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> AsyncIterator[bytes]:
        url = self._build_url("/chat/completions")
        headers = self._build_headers()
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                force_reasoning_echo_pad = False
                for attempt in range(2):
                    payload = self._build_payload(
                        request,
                        provider_model,
                        for_stream=True,
                        force_reasoning_echo_pad=force_reasoning_echo_pad,
                    )
                    payload["stream"] = True

                    print(f"[DEBUG] Sending stream to {url} payload:")
                    print(json.dumps(payload, indent=2, ensure_ascii=False))
                    print("[DEBUG] Stream payload summary:")
                    print(json.dumps(_debug_payload_summary(payload), indent=2, ensure_ascii=False))

                    async with client.stream("POST", url, headers=headers, json=payload) as resp:
                        print(f"[DEBUG] Stream response status: {resp.status_code}")
                        if resp.status_code >= 400:
                            buf = await resp.aread()
                            text = buf.decode("utf-8", errors="replace")
                            print(f"[DEBUG] Stream error response content: {text}")
                            err = UpstreamError.from_text(resp.status_code, text)
                            retryable = attempt == 0 and _needs_reasoning_echo_retry(err.body)
                            if retryable:
                                print("[DEBUG] Retrying stream with assistant reasoning_content padding")
                                force_reasoning_echo_pad = True
                                continue
                            raise err

                        # 必须用 aiter_bytes：aiter_raw 为未解压的 gzip/deflate，直接转发会导致客户端 SSE 解码失败
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                        return
        except UpstreamError:
            raise


def create_provider(config: ProviderConfig) -> BaseProvider:
    provider_map = {
        "openai": OpenAICompatibleProvider,
    }
    cls = provider_map.get(config.api_type, OpenAICompatibleProvider)
    return cls(config)
