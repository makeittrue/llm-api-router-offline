from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from app.utils import verify_password, get_password_hash, create_access_token, verify_token

from app.config import AppConfig, load_config, ProviderConfig
from app.logger import CallLogger, build_request_log_meta
from app.models import ChatCompletionRequest, ModelListResponse
from app.providers.base import BaseProvider, UpstreamError, create_provider
from app.router import Router

app_config: AppConfig | None = None
router: Router | None = None
call_logger: CallLogger | None = None
DEFAULT_ROUTE_MODEL = "default"


@asynccontextmanager
async def lifespan(application: FastAPI):
    global app_config, router, call_logger

    app_config = load_config()
    router = Router(app_config)
    call_logger = CallLogger(app_config.log.db_path, app_config.billing)

    yield


app = FastAPI(title="LLM API Router", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 欢迎页
@app.get("/", include_in_schema=False)
async def welcome_page():
    html_content = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LLM API Router</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1000px;
            margin: 0 auto;
            padding: 40px 20px;
            line-height: 1.6;
            color: #333;
        }
        h1 {
            color: #2563eb;
            margin-bottom: 20px;
            font-size: 2.5rem;
        }
        .subtitle {
            font-size: 1.2rem;
            color: #64748b;
            margin-bottom: 40px;
        }
        .card {
            background: #f8fafc;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            border: 1px solid #e2e8f0;
        }
        .card h2 {
            color: #1e293b;
            margin-bottom: 16px;
            font-size: 1.4rem;
        }
        pre {
            background: #1e293b;
            color: #e2e8f0;
            padding: 16px;
            border-radius: 8px;
            overflow-x: auto;
            font-family: 'Monaco', 'Menlo', monospace;
            font-size: 0.9rem;
        }
        .btn {
            display: inline-block;
            padding: 12px 24px;
            background: #2563eb;
            color: white;
            text-decoration: none;
            border-radius: 8px;
            margin-right: 16px;
            margin-top: 8px;
            transition: background 0.2s;
        }
        .btn:hover {
            background: #1d4ed8;
        }
        .btn.secondary {
            background: #64748b;
        }
        .btn.secondary:hover {
            background: #475569;
        }
        ul {
            margin-left: 24px;
            margin-bottom: 16px;
        }
        li {
            margin-bottom: 8px;
        }
    </style>
</head>
<body>
    <h1>🚀 LLM API Router</h1>
    <p class="subtitle">多厂商大模型统一接入网关，支持OpenAI兼容接口协议</p>

    <div class="card">
        <h2>📋 功能特性</h2>
        <ul>
            <li>支持OpenAI、Anthropic、百度文心、阿里通义、腾讯混元等所有主流厂商</li>
            <li>用户私有路由管理，每个用户可以独立配置自己的模型路由</li>
            <li>完整的调用日志统计和费用计算</li>
            <li>100%兼容OpenAI接口协议，现有代码无需修改即可切换</li>
        </ul>
    </div>

    <div class="card">
        <h2>🔗 快速入口</h2>
        <a href="/admin" class="btn">管理后台</a>
        <a href="/docs" class="btn secondary">接口文档</a>
        <a href="/health" class="btn secondary">健康检查</a>
    </div>

    <div class="card">
        <h2>💡 接口调用示例 (OpenAI 兼容)</h2>
        <p>和OpenAI SDK完全兼容，只需要把base_url改成本服务地址即可：</p>
        <pre>
import openai

client = openai.OpenAI(
    api_key="你的API_KEY",
    base_url="http://localhost:8000/v1"
)

# 调用聊天补全
response = client.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[{"role": "user", "content": "你好"}]
)
print(response.choices[0].message.content)
</pre>
    </div>

    <div class="card">
        <h2>📡 cURL 调用示例</h2>
        <pre>
curl http://localhost:8000/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer 你的API_KEY" \\
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "你好"}]
  }'
</pre>
    </div>

    <div class="card">
        <h2>✅ 健康检查</h2>
        <pre>curl http://localhost:8000/health</pre>
        <p>返回：<code>{"status":"ok"}</code></p>
    </div>

    <div class="card">
        <h2>📚 获取模型列表</h2>
        <pre>
curl http://localhost:8000/v1/models \\
  -H "Authorization: Bearer 你的API_KEY"
</pre>
    </div>

</body>
</html>
    """
    return HTMLResponse(content=html_content)

# 健康检查接口
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# OAuth2 方案
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# 鉴权依赖
async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = verify_token(token)
    if payload is None:
        raise credentials_exception
    user_id: str = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    user = call_logger.get_user_by_id(int(user_id))
    if user is None:
        raise credentials_exception
    return user


# 数据模型
class UserCreate(BaseModel):
    username: str
    password: str


class RouteCreate(BaseModel):
    model: str
    provider_name: str
    provider_base_url: str
    provider_api_key: str
    provider_model: str
    provider_api_type: str = "openai"


class RouteUpdate(BaseModel):
    model: str | None = None
    provider_name: str | None = None
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    provider_model: str | None = None
    provider_api_type: str | None = None


class DefaultRouteUpdate(BaseModel):
    enabled: bool
    models: list[str] = Field(default_factory=list)


def _normalize_model_names(models: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for model in models or []:
        if not isinstance(model, str):
            continue
        candidate = model.strip()
        if not candidate or candidate in seen:
            continue
        normalized.append(candidate)
        seen.add(candidate)
    return normalized


def _is_default_route_model(model: str | None) -> bool:
    return isinstance(model, str) and model.strip().lower() == DEFAULT_ROUTE_MODEL


def _assert_user_route_model_allowed(model: str) -> None:
    if _is_default_route_model(model):
        raise ValueError(
            f"模型名 '{DEFAULT_ROUTE_MODEL}' 为保留名称，请在页面的默认降级配置区中管理。"
        )


def _can_resolve_model_for_user(user_id: int, model: str) -> bool:
    if _is_default_route_model(model):
        return False
    if call_logger.get_user_route_by_model(user_id, model):
        return True
    try:
        router.resolve(model)
        return True
    except ValueError:
        return False


def _validate_default_route_models(user_id: int, models: list[str]) -> list[str]:
    normalized = _normalize_model_names(models)
    invalid_models = [model for model in normalized if not _can_resolve_model_for_user(user_id, model)]
    if invalid_models:
        raise ValueError(f"以下模型不可用，无法加入 default 降级链：{', '.join(invalid_models)}")
    return normalized


def _get_user_default_route_config(user_id: int) -> dict[str, Any]:
    config = call_logger.get_user_default_route(user_id)
    return {
        "enabled": bool(config.get("enabled")),
        "models": _normalize_model_names(config.get("models") or []),
        "updated_at": config.get("updated_at"),
    }


def _default_route_model_entry() -> dict[str, Any]:
    return {
        "id": DEFAULT_ROUTE_MODEL,
        "object": "model",
        "created": int(time.time()),
        "owned_by": "my-llm",
    }


@app.get("/v1/models")
async def list_models(current_user: dict = Depends(get_current_user)):
    # 合并全局模型和用户私有模型
    global_models = router.list_models()
    user_routes = call_logger.get_user_routes(current_user["id"])
    default_route = _get_user_default_route_config(current_user["id"])

    all_models = [
        {"id": m.id, "object": m.object, "created": m.created, "owned_by": m.owned_by}
        for m in global_models
    ] + [
        {
            "id": route["model"],
            "object": "model",
            "created": int(time.time()),
            "owned_by": "my-llm",
        } for route in user_routes
    ]
    if default_route["enabled"] and default_route["models"]:
        all_models.append(_default_route_model_entry())

    seen = set()
    unique_models = []
    for model in all_models:
        if model["id"] not in seen:
            seen.add(model["id"])
            unique_models.append(model)
    return ModelListResponse(data=unique_models)


def _preview_from_completion_body(data: dict[str, Any]) -> tuple[str | None, str | None]:
    choices = data.get("choices") or []
    if not choices:
        return None, None
    ch0 = choices[0]
    fr = ch0.get("finish_reason")
    msg = ch0.get("message") or {}
    mc = msg.get("content")
    if isinstance(mc, str):
        return fr, mc
    if mc is not None:
        return fr, json.dumps(mc, ensure_ascii=False)
    return fr, None


def _sse_error_stream(error_body: dict[str, Any]) -> AsyncIterator[bytes]:
    async def gen():
        yield f"data: {json.dumps(error_body, ensure_ascii=False)}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"

    return gen()


ResolvedChatTarget = tuple[str, BaseProvider, str, str]


def _resolve_model_target_for_user(user_id: int, model: str) -> ResolvedChatTarget:
    user_route = call_logger.get_user_route_by_model(user_id, model)
    if user_route:
        provider_config = ProviderConfig(
            name=user_route["provider_name"],
            base_url=user_route["provider_base_url"],
            api_key=user_route["provider_api_key"],
            api_type=user_route["provider_api_type"],
        )
        provider = create_provider(provider_config)
        return model, provider, user_route["provider_model"], user_route["provider_name"]

    provider, provider_model = router.resolve(model)
    return model, provider, provider_model, provider.config.name


def _candidate_models_for_request(
    user_id: int,
    request: ChatCompletionRequest,
) -> tuple[list[str], str | None]:
    request_fallback_models = _normalize_model_names(request.default_models)
    if not _is_default_route_model(request.model):
        return [request.model, *request_fallback_models], None

    default_route = _get_user_default_route_config(user_id)
    if not default_route["enabled"]:
        return [], (
            "Model 'default' is disabled for this user. "
            "Please enable and configure it in the admin page first."
        )
    if not default_route["models"]:
        return [], (
            "Model 'default' is not configured for this user. "
            "Please set at least one fallback model in the admin page."
        )
    return [*default_route["models"], *request_fallback_models], None


def _resolve_models_to_try(
    user_id: int,
    request: ChatCompletionRequest,
) -> tuple[list[ResolvedChatTarget], str | None]:
    models_to_try: list[ResolvedChatTarget] = []
    seen_models: set[str] = set()
    candidate_models, primary_resolve_error = _candidate_models_for_request(user_id, request)
    if not candidate_models:
        return [], primary_resolve_error

    for idx, candidate_model in enumerate(candidate_models):
        if candidate_model in seen_models:
            continue
        seen_models.add(candidate_model)
        try:
            models_to_try.append(_resolve_model_target_for_user(user_id, candidate_model))
        except ValueError as e:
            if idx == 0:
                primary_resolve_error = str(e)

    if not models_to_try and _is_default_route_model(request.model) and primary_resolve_error is None:
        primary_resolve_error = (
            "Model 'default' has no available fallback models in current route configuration."
        )
    return models_to_try, primary_resolve_error


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    current_user: dict = Depends(get_current_user)
):
    start_time = time.time()

    print(
        "[DEBUG] chat/completions request:",
        json.dumps(request.model_dump(mode="python", exclude_none=True), indent=2, ensure_ascii=False),
    )

    models_to_try, primary_resolve_error = _resolve_models_to_try(current_user["id"], request)
    if not models_to_try:
        if _is_default_route_model(request.model) and primary_resolve_error:
            message = primary_resolve_error
        elif not request.default_models and primary_resolve_error:
            message = primary_resolve_error
        else:
            message = f"Model '{request.model}' and all fallback models not found in route configuration."
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "param": "model",
                    "code": "model_not_found",
                }
            },
        )

    if request.stream:
        last_error = None
        for response_model, try_provider, try_provider_model, try_provider_name in models_to_try:
            try:
                raw = try_provider.chat_completion_stream(request, try_provider_model)
                ait = raw.__aiter__()
                try:
                    first = await ait.__anext__()
                except UpstreamError as e:
                    duration_ms = int((time.time() - start_time) * 1000)
                    call_logger.log_call(
                        request=request,
                        provider_name=try_provider_name,
                        provider_model=try_provider_model,
                        duration_ms=duration_ms,
                        status="error",
                        error_message=str(e),
                        user_id=current_user["id"],
                        log_meta={
                            "request": build_request_log_meta(request),
                            "error_stage": "stream_open",
                            "upstream_status_code": e.status_code,
                            "upstream_error_body": e.body,
                        },
                    )
                    last_error = e
                    continue
                except StopAsyncIteration:
                    err = {
                        "error": {
                            "message": (
                                "上游在建立流后未返回任何数据（空响应体）。"
                                "若为 MiMo thinking 模式，请确认客户端能解析带 reasoning_content 的 SSE；"
                                "或暂时关闭思考链 / 换非 thinking 模型。"
                            ),
                            "type": "api_error",
                            "code": "empty_upstream_stream",
                        }
                    }
                    last_error = Exception(err["error"]["message"])
                    continue
                except Exception as e:
                    duration_ms = int((time.time() - start_time) * 1000)
                    call_logger.log_call(
                        request=request,
                        provider_name=try_provider_name,
                        provider_model=try_provider_model,
                        duration_ms=duration_ms,
                        status="error",
                        error_message=str(e),
                        user_id=current_user["id"],
                        log_meta={"request": build_request_log_meta(request), "error_stage": "stream_open"},
                    )
                    last_error = e
                    continue

                async def merged_bytes():
                    yield first
                    async for chunk in ait:
                        yield chunk

                return StreamingResponse(
                    _stream_response(
                        request,
                        sse_chunks=merged_bytes(),
                        response_model=response_model,
                        provider_model=try_provider_model,
                        provider_name=try_provider_name,
                        start_time=start_time,
                        user_id=current_user["id"],
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            except Exception as e:
                last_error = e
                continue

        if last_error:
            if isinstance(last_error, UpstreamError):
                return StreamingResponse(
                    _sse_error_stream(last_error.body),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            else:
                return StreamingResponse(
                    _sse_error_stream(
                        {
                            "error": {
                                "message": str(last_error),
                                "type": "api_error",
                                "code": "bad_gateway",
                            }
                        }
                    ),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )

    last_error = None
    for _, try_provider, try_provider_model, try_provider_name in models_to_try:
        try:
            data = await try_provider.chat_completion(request, try_provider_model)
            data["model"] = request.model
            duration_ms = int((time.time() - start_time) * 1000)
            finish_reason, assistant_text = _preview_from_completion_body(data)
            log_meta = {
                "request": build_request_log_meta(request),
                "response": {
                    "finish_reason": finish_reason,
                    "assistant_chars": len(assistant_text or ""),
                    "preview": _log_preview_text(assistant_text or ""),
                    "usage_raw": data.get("usage"),
                },
            }
            call_logger.log_call(
                request=request,
                response_body=data,
                provider_name=try_provider_name,
                provider_model=try_provider_model,
                duration_ms=duration_ms,
                status="success",
                user_id=current_user["id"],
                log_meta=log_meta,
            )
            return JSONResponse(content=data, media_type="application/json")
        except UpstreamError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.log_call(
                request=request,
                provider_name=try_provider_name,
                provider_model=try_provider_model,
                duration_ms=duration_ms,
                status="error",
                error_message=str(e),
                user_id=current_user["id"],
                log_meta={"request": build_request_log_meta(request), "error_stage": "chat_completion"},
            )
            last_error = e
            continue
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            call_logger.log_call(
                request=request,
                provider_name=try_provider_name,
                provider_model=try_provider_model,
                duration_ms=duration_ms,
                status="error",
                error_message=str(e),
                user_id=current_user["id"],
                log_meta={"request": build_request_log_meta(request), "error_stage": "chat_completion"},
            )
            last_error = e
            continue

    if last_error:
        if isinstance(last_error, UpstreamError):
            return JSONResponse(status_code=last_error.status_code, content=last_error.body)
        else:
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "message": str(last_error),
                        "type": "api_error",
                        "code": "bad_gateway",
                    }
                },
            )

    return JSONResponse(
        status_code=502,
        content={
            "error": {
                "message": "All models failed",
                "type": "api_error",
                "code": "bad_gateway",
            }
        },
    )


def _log_preview_text(text: str, max_total: int = 4000) -> str:
    if len(text) <= max_total:
        return text
    h = max_total // 2
    return text[:h] + "\n…\n" + text[-h:]


def _accumulate_stream_sse_line(line: str, acc: dict) -> None:
    if not line.startswith("data: "):
        return
    body = line[6:].strip()
    if body == "[DONE]":
        acc["saw_done"] = True
        return
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        acc["sse_json_errors"] = acc.get("sse_json_errors", 0) + 1
        return
    acc["sse_data_events"] = acc.get("sse_data_events", 0) + 1
    if isinstance(data.get("model"), str) and data["model"]:
        acc.setdefault("response_model", data["model"])
    if data.get("id"):
        acc.setdefault("upstream_response_id", data["id"])
    u = data.get("usage")
    # 兼容 Kimi：流式响应的 usage 可能在 choices[0] 中而不是顶层
    if not isinstance(u, dict):
        for choice in data.get("choices") or []:
            cu = choice.get("usage")
            if isinstance(cu, dict):
                u = cu
                break
    if isinstance(u, dict) and (
        u.get("prompt_tokens") is not None
        or u.get("completion_tokens") is not None
        or u.get("total_tokens") is not None
    ):
        acc["usage"] = u
    for choice in data.get("choices") or []:
        if choice.get("finish_reason"):
            acc["finish_reason"] = choice["finish_reason"]
            acc["saw_finish_reason"] = True
        delta = choice.get("delta") or {}
        if isinstance(delta.get("role"), str):
            acc["saw_role"] = True
        if "content" in delta:
            acc["saw_content_field"] = True
        c = delta.get("content")
        if isinstance(c, str) and c:
            acc.setdefault("_text_parts", []).append(c)
        r = delta.get("reasoning_content")
        if isinstance(r, str) and r:
            acc.setdefault("_reason_parts", []).append(r)
            acc["saw_reasoning_content"] = True


def _build_sse_chunk_bytes(
    *,
    model: str,
    response_id: str | None,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> bytes:
    payload = {
        "id": response_id or f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _stream_log_snapshot(acc: dict) -> tuple[str, int, str]:
    text = "".join(acc.get("_text_parts", []))
    reason = "".join(acc.get("_reason_parts", []))
    preview_body = text
    if reason:
        preview_body = text + "\n--- reasoning ---\n" + reason
    return text, len(reason), _log_preview_text(preview_body)


async def _stream_response(
    request: ChatCompletionRequest,
    *,
    sse_chunks: AsyncIterator[bytes],
    response_model: str,
    provider_model: str,
    provider_name: str,
    start_time: float,
    user_id: int,
):
    acc: dict = {"sse_data_events": 0, "sse_json_errors": 0}
    parser_buf = ""
    try:
        async for chunk in sse_chunks:
            # 分块边界可能截断多字节 UTF-8；日志解析用替换策略，避免整路流因解码异常中断
            chunk_str = chunk.decode("utf-8", errors="replace")
            parser_buf += chunk_str
            lines = parser_buf.splitlines(keepends=True)
            if lines and not lines[-1].endswith(("\n", "\r")):
                parser_buf = lines.pop()
            else:
                parser_buf = ""
            for line in lines:
                try:
                    _accumulate_stream_sse_line(line.rstrip("\r\n"), acc)
                except Exception:
                    acc["sse_parse_exceptions"] = acc.get("sse_parse_exceptions", 0) + 1
            yield chunk

        if parser_buf:
            try:
                _accumulate_stream_sse_line(parser_buf.rstrip("\r\n"), acc)
            except Exception:
                acc["sse_parse_exceptions"] = acc.get("sse_parse_exceptions", 0) + 1

        # 某些客户端在只有 reasoning_content、缺少 content 或缺少 [DONE] 时会一直停留在“思考中”。
        # 只有在上游尚未给出 [DONE] 时，才能安全补发兼容收尾块。
        if (
            not acc.get("saw_done")
            and acc.get("saw_reasoning_content")
            and not acc.get("saw_content_field")
        ):
            acc["synthetic_empty_content_chunk"] = True
            yield _build_sse_chunk_bytes(
                model=acc.get("response_model") or response_model,
                response_id=acc.get("upstream_response_id"),
                delta={"role": "assistant", "content": ""},
            )
        if not acc.get("saw_done") and not acc.get("saw_finish_reason"):
            acc["synthetic_finish_chunk"] = True
            yield _build_sse_chunk_bytes(
                model=acc.get("response_model") or response_model,
                response_id=acc.get("upstream_response_id"),
                delta={},
                finish_reason=acc.get("finish_reason") or "stop",
            )
        if not acc.get("saw_done"):
            acc["synthetic_done"] = True
            yield b"data: [DONE]\n\n"

        duration_ms = int((time.time() - start_time) * 1000)
        text, reason_len, preview = _stream_log_snapshot(acc)
        usage = acc.get("usage")
        stream_usage = None
        if isinstance(usage, dict):
            stream_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            }
        log_meta = {
            "request": build_request_log_meta(request),
            "stream": {
                "sse_data_events": acc.get("sse_data_events", 0),
                "sse_json_errors": acc.get("sse_json_errors", 0),
                "sse_parse_exceptions": acc.get("sse_parse_exceptions", 0),
                "upstream_response_id": acc.get("upstream_response_id"),
                "finish_reason": acc.get("finish_reason"),
                "usage_raw": usage,
                "assistant_text_chars": len(text),
                "assistant_reasoning_chars": reason_len,
                **(
                    {"stream_only_reasoning_no_content": True}
                    if (not text) and reason_len
                    else {}
                ),
                **(
                    {"synthetic_empty_content_chunk": True}
                    if acc.get("synthetic_empty_content_chunk")
                    else {}
                ),
                **(
                    {"synthetic_finish_chunk": True}
                    if acc.get("synthetic_finish_chunk")
                    else {}
                ),
                **({"synthetic_done": True} if acc.get("synthetic_done") else {}),
            },
            "response_preview": preview,
        }
        call_logger.log_call(
            request=request,
            provider_name=provider_name,
            provider_model=provider_model,
            duration_ms=duration_ms,
            status="success",
            user_id=user_id,
            log_meta=log_meta,
            stream_usage=stream_usage,
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        text, reason_len, preview = _stream_log_snapshot(acc)
        usage = acc.get("usage")
        stream_usage = None
        if isinstance(usage, dict):
            stream_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            }
        log_meta = {
            "request": build_request_log_meta(request),
            "stream": {
                "sse_data_events": acc.get("sse_data_events", 0),
                "sse_json_errors": acc.get("sse_json_errors", 0),
                "sse_parse_exceptions": acc.get("sse_parse_exceptions", 0),
                "upstream_response_id": acc.get("upstream_response_id"),
                "finish_reason": acc.get("finish_reason"),
                "usage_raw": usage,
                "assistant_text_chars": len(text),
                "assistant_reasoning_chars": reason_len,
            },
            "response_preview": preview or None,
            "error_stage": "stream",
        }
        call_logger.log_call(
            request=request,
            provider_name=provider_name,
            provider_model=provider_model,
            duration_ms=duration_ms,
            status="error",
            error_message=str(e),
            user_id=user_id,
            log_meta=log_meta,
            stream_usage=stream_usage,
        )
        error_payload = {
            "error": {
                "message": str(e),
                "type": "api_error",
                "code": "bad_gateway",
            }
        }
        error_data = json.dumps(error_payload, ensure_ascii=False)
        yield f"data: {error_data}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


@app.get("/v1/logs")
async def get_logs(
    model: str | None = None,
    limit: int = 20,
    offset: int = 0,
    current_user: dict = Depends(get_current_user)
):
    uid = current_user["id"]
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    total = call_logger.count_logs(user_id=uid, model=model)
    logs = call_logger.query_logs(user_id=uid, model=model, limit=limit, offset=offset)
    return {"data": logs, "total": total, "limit": limit, "offset": offset}


@app.get("/v1/logs/summary")
async def get_logs_summary(
    model: str | None = None,
    month: str | None = None,
    current_user: dict = Depends(get_current_user)
):
    summary = call_logger.get_usage_summary(user_id=current_user["id"], model=model, month=month)
    return {"data": summary}


@app.get("/v1/billing/summary")
async def get_billing_summary(
    model: str | None = None,
    month: str | None = None,
    current_user: dict = Depends(get_current_user)
):
    summary = call_logger.get_billing_summary(user_id=current_user["id"], model=model, month=month)
    return {"data": summary}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ========== 用户认证接口 ==========
@app.post("/register")
async def register(user: UserCreate):
    try:
        # bcrypt算法最多支持72字节，自动截断
        hashed_password = get_password_hash(user.password)
        user_id = call_logger.create_user(user.username, hashed_password)
        return {
            "user_id": user_id,
            "username": user.username,
            "access_token": create_access_token({"sub": str(user_id), "username": user.username}),
            "token_type": "bearer"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = call_logger.get_user_by_username(form_data.username)
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        {"sub": str(user["id"]), "username": user["username"]}
    )
    return {"access_token": access_token, "token_type": "bearer"}


# 管理后台页面
@app.get("/admin")
async def admin_page():
    return FileResponse("static/admin.html")


# 管理API：获取全局路由列表（兼容旧版本）
@app.get("/v1/admin/routes")
async def get_global_routes(current_user: dict = Depends(get_current_user)):
    routes = []
    for route in app_config.routes:
        routes.append({
            "model": route.model,
            "provider": route.provider,
            "provider_model": route.provider_model or route.model
        })
    return {"routes": routes}


# 管理API：获取全局服务商列表（兼容旧版本）
@app.get("/v1/admin/providers")
async def get_global_providers(current_user: dict = Depends(get_current_user)):
    providers = []
    for provider in app_config.providers:
        providers.append({
            "name": provider.name,
            "base_url": provider.base_url,
            "api_type": provider.api_type
        })
    return {"providers": providers}


# ========== 用户路由管理接口 ==========
@app.get("/v1/user/routes")
async def get_user_routes(current_user: dict = Depends(get_current_user)):
    routes = call_logger.get_user_routes(current_user["id"])
    return {"routes": routes}


@app.get("/v1/user/default-route")
async def get_user_default_route(current_user: dict = Depends(get_current_user)):
    return _get_user_default_route_config(current_user["id"])


@app.put("/v1/user/default-route")
async def update_user_default_route(
    route: DefaultRouteUpdate,
    current_user: dict = Depends(get_current_user)
):
    try:
        models = _normalize_model_names(route.models)
        if route.enabled and not models:
            raise ValueError("启用 default 自动降级前，至少需要配置一个候选模型")
        validated_models = _validate_default_route_models(current_user["id"], models) if models else []
        saved = call_logger.upsert_user_default_route(
            current_user["id"],
            enabled=route.enabled,
            models=validated_models,
        )
        return saved
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/v1/user/routes")
async def create_user_route(
    route: RouteCreate,
    current_user: dict = Depends(get_current_user)
):
    try:
        _assert_user_route_model_allowed(route.model)
        route_id = call_logger.create_user_route(
            user_id=current_user["id"],
            model=route.model,
            provider_name=route.provider_name,
            provider_base_url=route.provider_base_url,
            provider_api_key=route.provider_api_key,
            provider_model=route.provider_model,
            provider_api_type=route.provider_api_type,
        )
        return {
            "id": route_id,
            "model": route.model,
            "message": "路由创建成功"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/v1/user/routes/{route_id}")
async def update_user_route(
    route_id: int,
    route: RouteUpdate,
    current_user: dict = Depends(get_current_user)
):
    try:
        update_data = route.model_dump(exclude_unset=True)
        if "model" in update_data:
            _assert_user_route_model_allowed(update_data["model"])
        success = call_logger.update_user_route(
            route_id=route_id,
            user_id=current_user["id"],
            **update_data
        )
        if not success:
            raise HTTPException(status_code=404, detail="路由不存在或无权限修改")
        return {"message": "路由更新成功"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/v1/user/routes/{route_id}")
async def delete_user_route(
    route_id: int,
    current_user: dict = Depends(get_current_user)
):
    success = call_logger.delete_user_route(route_id, current_user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="路由不存在或无权限删除")
    return {"message": "路由删除成功"}


@app.get("/v1/user/token")
async def get_user_token(current_user: dict = Depends(get_current_user)):
    new_token = create_access_token({"sub": str(current_user["id"]), "username": current_user["username"]})
    return {
        "access_token": new_token,
        "token_type": "bearer",
        "username": current_user["username"],
    }


@app.post("/v1/user/token/regenerate")
async def regenerate_user_token(current_user: dict = Depends(get_current_user)):
    new_token = create_access_token({"sub": str(current_user["id"]), "username": current_user["username"]})
    return {
        "access_token": new_token,
        "token_type": "bearer",
        "message": "Token已重新生成"
    }
