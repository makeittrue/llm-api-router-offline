from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


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


class BillingTimeWindowConfig(BaseModel):
    name: str | None = None
    timezone: str = "UTC"
    weekdays: list[int] = Field(default_factory=list)
    start_time: str | None = None
    end_time: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    input_price: float | None = Field(default=None, ge=0)
    output_price: float | None = Field(default=None, ge=0)
    cache_read_price: float | None = Field(default=None, ge=0)
    cache_write_price: float | None = Field(default=None, ge=0)


class BillingTokenTierConfig(BaseModel):
    name: str | None = None
    min_prompt_tokens: int | None = Field(default=None, ge=0)
    max_prompt_tokens: int | None = Field(default=None, ge=0)
    input_price: float | None = Field(default=None, ge=0)
    output_price: float | None = Field(default=None, ge=0)
    cache_read_price: float | None = Field(default=None, ge=0)
    cache_write_price: float | None = Field(default=None, ge=0)


class BillingRuleConfig(BaseModel):
    provider: str
    provider_aliases: list[str] = Field(default_factory=list)
    provider_model_patterns: list[str] = Field(default_factory=list)
    match_mode: str = "exact"
    input_price: float = Field(default=0, ge=0)
    output_price: float = Field(default=0, ge=0)
    cache_read_price: float | None = Field(default=None, ge=0)
    cache_write_price: float | None = Field(default=None, ge=0)
    unit: int = Field(default=1_000_000, ge=1)
    currency: str = "CNY"
    source_url: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    note: str | None = None
    token_tiers: list[BillingTokenTierConfig] = Field(default_factory=list)
    time_windows: list[BillingTimeWindowConfig] = Field(default_factory=list)


class BillingConfig(BaseModel):
    enabled: bool = True
    default_currency: str = "CNY"
    round_digits: int = Field(default=8, ge=0, le=12)
    rules: list[BillingRuleConfig] = Field(default_factory=list)


class PathPrefixRewrite(BaseModel):
    """将历史/模型输出里的旧绝对路径前缀换成当前机器的工作区（Trae 工具在本地执行）。"""

    from_path: str
    to_path: str

    @model_validator(mode="before")
    @classmethod
    def _accept_from_to_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            d = dict(data)
            if "from_path" not in d and "from" in d:
                d["from_path"] = d.pop("from")
            if "to_path" not in d and "to" in d:
                d["to_path"] = d.pop("to")
            return d
        return data


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
        "请基于上方 IDE 对话上下文继续。遵循系统说明；若任务不清晰可简要追问。"
    )
    trae_bridge_between_consecutive_assistant: str = "请接续本会话中上一条助手回复继续。"

    # 对 assistant.tool_calls 里 function.arguments 的 JSON 做字符串值前缀替换（请求入站与非流式响应出站）
    path_prefix_rewrites: list[PathPrefixRewrite] = Field(default_factory=list)


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
    billing: BillingConfig = BillingConfig()
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
