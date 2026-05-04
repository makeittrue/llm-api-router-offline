from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ProviderConfig(BaseModel):
    name: str
    base_url: str
    api_key: str
    api_type: str = "openai"


class RouteConfig(BaseModel):
    model: str
    provider: str
    provider_model: str | None = None


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class LogConfig(BaseModel):
    db_path: str = "logs.db"


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    log: LogConfig = LogConfig()
    providers: list[ProviderConfig] = []
    routes: list[RouteConfig] = []


def load_config(config_path: str | None = None) -> AppConfig:
    if config_path is None:
        config_path = os.getenv("LLM_ROUTER_CONFIG", "config.yaml")

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    for provider in raw.get("providers", []):
        if "api_key" in provider and provider["api_key"]:
            env_val = os.getenv(provider["api_key"], "")
            if env_val:
                provider["api_key"] = env_val

    return AppConfig(**raw)


def build_route_map(config: AppConfig) -> dict[str, tuple[ProviderConfig, str]]:
    provider_map: dict[str, ProviderConfig] = {p.name: p for p in config.providers}
    route_map: dict[str, tuple[ProviderConfig, str]] = {}

    for route in config.routes:
        if route.provider not in provider_map:
            raise ValueError(
                f"Route references unknown provider '{route.provider}', "
                f"available: {list(provider_map.keys())}"
            )
        provider = provider_map[route.provider]
        actual_model = route.provider_model or route.model
        route_map[route.model] = (provider, actual_model)

    return route_map
