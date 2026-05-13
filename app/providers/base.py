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
        self, request: ChatCompletionRequest, provider_model: str, *, for_stream: bool
    ) -> dict[str, Any]:
        raw = request.model_dump(mode="python", exclude_none=True)
        payload: dict[str, Any] = {
            k: v for k, v in raw.items() if k in _OPENAI_CHAT_TOP_LEVEL_KEYS
        }
        payload["messages"] = _sanitize_messages(raw.get("messages", []))
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
        payload = self._build_payload(request, provider_model, for_stream=False)
        payload["stream"] = False

        print(f"[DEBUG] Sending to {url} payload:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                print(f"[DEBUG] Response status: {resp.status_code}")
                if resp.status_code >= 400:
                    print(f"[DEBUG] Error response content: {resp.text}")
                    raise UpstreamError.from_httpx_response(resp)
                return resp.json()
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
        payload = self._build_payload(request, provider_model, for_stream=True)
        payload["stream"] = True

        print(f"[DEBUG] Sending stream to {url} payload:")
        print(json.dumps(payload, indent=2, ensure_ascii=False))

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    print(f"[DEBUG] Stream response status: {resp.status_code}")
                    if resp.status_code >= 400:
                        buf = await resp.aread()
                        text = buf.decode("utf-8", errors="replace")
                        print(f"[DEBUG] Stream error response content: {text}")
                        raise UpstreamError.from_text(resp.status_code, text)

                    # 必须用 aiter_bytes：aiter_raw 为未解压的 gzip/deflate，直接转发会导致客户端 SSE 解码失败
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        except UpstreamError:
            raise


def create_provider(config: ProviderConfig) -> BaseProvider:
    provider_map = {
        "openai": OpenAICompatibleProvider,
    }
    cls = provider_map.get(config.api_type, OpenAICompatibleProvider)
    return cls(config)
