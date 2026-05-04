from __future__ import annotations

from typing import AsyncIterator

from app.config import AppConfig, ProviderConfig, build_route_map
from app.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelInfo,
)
from app.providers.base import BaseProvider, create_provider


class Router:
    def __init__(self, config: AppConfig):
        self.config = config
        self.route_map = build_route_map(config)
        self._provider_cache: dict[str, BaseProvider] = {}

    def _get_provider(self, provider_config: ProviderConfig) -> BaseProvider:
        if provider_config.name not in self._provider_cache:
            self._provider_cache[provider_config.name] = create_provider(provider_config)
        return self._provider_cache[provider_config.name]

    def resolve(self, model: str) -> tuple[BaseProvider, str]:
        if model not in self.route_map:
            available = list(self.route_map.keys())
            raise ValueError(
                f"Model '{model}' not found in route configuration. "
                f"Available models: {available}"
            )
        provider_config, provider_model = self.route_map[model]
        provider = self._get_provider(provider_config)
        return provider, provider_model

    def list_models(self) -> list[ModelInfo]:
        models = []
        for model_name in self.route_map:
            models.append(ModelInfo(id=model_name))
        return models

    async def dispatch(
        self, request: ChatCompletionRequest
    ) -> ChatCompletionResponse:
        provider, provider_model = self.resolve(request.model)
        response = await provider.chat_completion(request, provider_model)
        response.model = request.model
        return response

    async def dispatch_stream(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[bytes]:
        provider, provider_model = self.resolve(request.model)
        async for chunk in provider.chat_completion_stream(request, provider_model):
            yield chunk
