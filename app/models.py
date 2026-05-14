from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """与 OpenAI Chat Completions 单条消息对齐；未知字段与扩展结构原样进出。"""

    model_config = ConfigDict(extra="allow")

    role: str
    content: Any | None = None
    name: str | None = None
    tool_calls: Any | None = None
    tool_call_id: str | None = None
    # MiMo / DeepSeek 等思考链：多轮对话必须把上一轮 assistant 的推理原文回传，否则上游 400
    reasoning_content: str | None = None


class ChatCompletionRequest(BaseModel):
    """OpenAI `/v1/chat/completions` 请求体：`extra` 放行官方新增或厂商扩展字段。"""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    n: int | None = None
    stream: bool | None = False
    stream_options: dict[str, Any] | None = None
    stop: Any | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict[str, float] | None = None
    user: str | None = None
    seed: int | None = None
    tools: Any | None = None
    tool_choice: Any | None = None
    parallel_tool_calls: bool | None = None
    response_format: Any | None = None
    modalities: Any | None = None
    default_models: list[str] | None = None


class CompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChoiceDelta(BaseModel):
    role: str | None = None
    content: str | None = None


class StreamChoice(BaseModel):
    index: int = 0
    delta: ChoiceDelta
    finish_reason: str | None = None


class ChatChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionResponse(BaseModel):
    """网关内部或测试用快照；对外开放接口改为透传上游 `dict`/JSON。"""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[ChatChoice] = []
    usage: CompletionUsage | dict[str, Any] | None = None


class ChatCompletionChunk(BaseModel):
    id: str = ""
    object: str = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[StreamChoice] = []


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "llm-router"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo] = []
