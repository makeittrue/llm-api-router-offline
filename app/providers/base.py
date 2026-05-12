from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from app.config import ProviderConfig
from app.models import ChatCompletionRequest


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

    def _build_payload(self, request: ChatCompletionRequest, provider_model: str) -> dict[str, Any]:
        payload = request.model_dump(mode="python", exclude_none=True)
        payload["model"] = provider_model
        return payload

    async def chat_completion(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> dict[str, Any]:
        url = self._build_url("/chat/completions")
        headers = self._build_headers()
        payload = self._build_payload(request, provider_model)
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
        payload = self._build_payload(request, provider_model)
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

                    async for chunk in resp.aiter_raw():
                        yield chunk
        except UpstreamError:
            raise


def create_provider(config: ProviderConfig) -> BaseProvider:
    provider_map = {
        "openai": OpenAICompatibleProvider,
    }
    cls = provider_map.get(config.api_type, OpenAICompatibleProvider)
    return cls(config)
