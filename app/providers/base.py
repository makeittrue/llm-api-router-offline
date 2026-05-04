from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from app.config import ProviderConfig
from app.models import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)


class BaseProvider(ABC):
    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def chat_completion(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> ChatCompletionResponse: ...

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
        self, request: ChatCompletionRequest, provider_model: str
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": provider_model,
            "messages": [m.model_dump(exclude_none=True) for m in request.messages],
        }
        for field in (
            "temperature",
            "top_p",
            "n",
            "stream",
            "stop",
            "max_tokens",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "user",
        ):
            val = getattr(request, field, None)
            if val is not None:
                payload[field] = val
        return payload

    async def chat_completion(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> ChatCompletionResponse:
        url = self._build_url("/chat/completions")
        headers = self._build_headers()
        payload = self._build_payload(request, provider_model)
        payload["stream"] = False

        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        return ChatCompletionResponse(**data)

    async def chat_completion_stream(
        self, request: ChatCompletionRequest, provider_model: str
    ) -> AsyncIterator[bytes]:
        url = self._build_url("/chat/completions")
        headers = self._build_headers()
        payload = self._build_payload(request, provider_model)
        payload["stream"] = True

        async with httpx.AsyncClient(timeout=300.0) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            yield b"data: [DONE]\n\n"
                            break
                        yield f"data: {data_str}\n\n".encode("utf-8")


def create_provider(config: ProviderConfig) -> BaseProvider:
    provider_map = {
        "openai": OpenAICompatibleProvider,
    }
    cls = provider_map.get(config.api_type, OpenAICompatibleProvider)
    return cls(config)
