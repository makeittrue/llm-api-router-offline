from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


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


class ContextConfig(BaseModel):
    """对 Trae 等客户端消息的裁剪与 max_tokens 兜底；命中长上下文名单时用更宽松上限。"""

    # 未命中 long_context 名单时（兼容小上下文模型）
    message_char_cap: int = Field(default=2000, ge=0)
    history_message_keep: int = Field(default=10, ge=1)
    max_tokens_default: int = Field(default=4096, ge=1)
    max_tokens_cap: int = Field(default=8192, ge=1)

    # 请求 model 名（小写）包含任一子串时，改用下列上限（适配约 1M 上下文类模型，如 DeepSeek V4、MiMo 2.5）
    long_context_model_substrings: list[str] = Field(
        default_factory=lambda: [
            "deepseek-v4",
            "deepseek_v4",
            "deepseek v4",
            "mimo-2.5",
            "mimo_2.5",
            "mimo2.5",
            "mimo-v2.5",
        ]
    )
    long_context_message_char_cap: int = Field(default=800_000, ge=0)
    long_context_history_message_keep: int = Field(default=256, ge=1)
    long_context_max_tokens_default: int = Field(default=32768, ge=1)
    long_context_max_tokens_cap: int = Field(default=393_216, ge=1)

    trae_merge_consecutive_assistant: bool = True
    trae_synthetic_user_when_missing: bool = True
    trae_synthetic_user_content: str = (
        "Continue from the IDE conversation context above. "
        "Follow the system instructions; reply helpfully or ask a brief clarifying question if the task is unclear."
    )


def model_uses_long_context(model: str, ctx: ContextConfig) -> bool:
    m = model.lower()
    return any(p.lower() in m for p in ctx.long_context_model_substrings if p)


def route_targets_long_context(
    request_model: str, provider_model: str | None, ctx: ContextConfig
) -> bool:
    """对外 model 或上游 provider_model 任一命中长上下文名单即视为长窗口路由。"""
    if model_uses_long_context(request_model, ctx):
        return True
    if provider_model and model_uses_long_context(provider_model, ctx):
        return True
    return False


class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    log: LogConfig = LogConfig()
    context: ContextConfig = ContextConfig()
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
