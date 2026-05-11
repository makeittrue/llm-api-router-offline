from __future__ import annotations

import json
import time
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.utils import verify_password, get_password_hash, create_access_token, verify_token

from app.config import AppConfig, load_config, ProviderConfig
from app.logger import CallLogger
from app.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelListResponse,
)
from app.router import Router
from app.providers.base import create_provider

app_config: AppConfig | None = None
router: Router | None = None
call_logger: CallLogger | None = None


@asynccontextmanager
async def lifespan(application: FastAPI):
    global app_config, router, call_logger

    app_config = load_config()
    router = Router(app_config)
    call_logger = CallLogger(app_config.log.db_path)

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


@app.get("/v1/models")
async def list_models(current_user: dict = Depends(get_current_user)):
    # 合并全局模型和用户私有模型
    global_models = router.list_models()
    user_routes = call_logger.get_user_routes(current_user["id"])

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

    seen = set()
    unique_models = []
    for model in all_models:
        if model["id"] not in seen:
            seen.add(model["id"])
            unique_models.append(model)
    return ModelListResponse(data=unique_models)


@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    current_user: dict = Depends(get_current_user)
):
    start_time = time.time()
    provider_name = None
    provider_model = None
    provider = None
    
    # 打印Trae发送的请求参数，排查参数错误问题
    print(f"[DEBUG] Trae request: {json.dumps(request.model_dump(exclude_unset=True), indent=2, ensure_ascii=False)}")

    # 处理Trae特殊格式：清理content中的系统提醒和历史上下文垃圾内容
    cleaned_messages = []
    for msg in request.messages:
        if msg.role == "user":
            # 处理两种content格式：字符串和多模态数组
            if isinstance(msg.content, str):
                content = msg.content
            elif isinstance(msg.content, list):
                # 合并数组中所有text类型的内容
                content = ""
                for item in msg.content:
                    if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
                        content += item["text"] + "\n"
            else:
                # 其他格式直接保留
                cleaned_messages.append(msg)
                continue
            
            # 执行清理逻辑
            # 移除<system-reminder>包裹的内容
            content = re.sub(r'<system-reminder>.*?</system-reminder>', '', content, flags=re.DOTALL)
            # 移除Trae特有的工具调用标记
            content = re.sub(r'<\|｜DSML\|｜.*?>', '', content, flags=re.DOTALL)
            # 移除<user_input>标签和内容
            content = re.sub(r'<user_input>.*?</user_input>', '', content, flags=re.DOTALL)
            # 移除工具调用和返回相关的标签
            content = re.sub(r'<tool_call_id>.*?</tool_call_id>', '', content, flags=re.DOTALL)
            content = re.sub(r'<toolcall_status>.*?</toolcall_status>', '', content, flags=re.DOTALL)
            content = re.sub(r'<toolcall_result>.*?</toolcall_result>', '', content, flags=re.DOTALL)
            # 移除markdown代码块标记
            content = re.sub(r'```.*?```', '', content, flags=re.DOTALL)
            # 移除所有其他的<>包裹的标签内容
            content = re.sub(r'<[^>]+>', '', content)
            # 移除多余的空行和空白
            content = re.sub(r'\n\s*\n', '\n', content).strip()
            
            # 如果清理后内容为空，跳过这条消息
            if content:
                msg.content = content
                cleaned_messages.append(msg)
        else:
            cleaned_messages.append(msg)
    
    # 替换清理后的消息
    request.messages = cleaned_messages

    # 优先匹配用户自己的路由
    user_route = call_logger.get_user_route_by_model(current_user["id"], request.model)
    if user_route:
        # 动态创建provider
        provider_config = ProviderConfig(
            name=user_route["provider_name"],
            base_url=user_route["provider_base_url"],
            api_key=user_route["provider_api_key"],
            api_type=user_route["provider_api_type"],
        )
        provider = create_provider(provider_config)
        provider_model = user_route["provider_model"]
        provider_name = user_route["provider_name"]
    else:
        # 匹配全局路由
        try:
            provider, provider_model = router.resolve(request.model)
            provider_name = provider.config.name
        except ValueError as e:
            return JSONResponse(status_code=404, content={"error": {"message": str(e)}})

    if request.stream:
        return StreamingResponse(
            _stream_response(request, provider, provider_model, provider_name, start_time, current_user["id"]),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        response = await provider.chat_completion(request, provider_model)
        response.model = request.model
        duration_ms = int((time.time() - start_time) * 1000)
        # 测试：确认user_id存在
        print(f'[LOG] Calling log_call with user_id={current_user["id"]}, type={type(current_user["id"])}')
        call_logger.log_call(
            request=request,
            response=response,
            provider_name=provider_name,
            provider_model=provider_model,
            duration_ms=duration_ms,
            status="success",
            user_id=current_user["id"],
        )
        return response
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        call_logger.log_call(
            request=request,
            provider_name=provider_name,
            provider_model=provider_model,
            duration_ms=duration_ms,
            status="error",
            error_message=str(e),
            user_id=current_user["id"],
        )
        return JSONResponse(
            status_code=502,
            content={"error": {"message": f"Upstream error: {str(e)}"}},
        )


async def _stream_response(
    request: ChatCompletionRequest,
    provider,
    provider_model: str,
    provider_name: str,
    start_time: float,
    user_id: int,
):
    total_content = ""
    try:
        async for chunk in provider.chat_completion_stream(request, provider_model):
            try:
                chunk_str = chunk.decode("utf-8")
                for line in chunk_str.split("\n"):
                    if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                        data = json.loads(line[6:])
                        for choice in data.get("choices", []):
                            delta = choice.get("delta", {})
                            total_content += delta.get("content", "")
            except Exception:
                pass
            yield chunk

        duration_ms = int((time.time() - start_time) * 1000)
        call_logger.log_call(
            request=request,
            provider_name=provider_name,
            provider_model=provider_model,
            duration_ms=duration_ms,
            status="success",
            user_id=user_id,
        )
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        call_logger.log_call(
            request=request,
            provider_name=provider_name,
            provider_model=provider_model,
            duration_ms=duration_ms,
            status="error",
            error_message=str(e),
            user_id=user_id,
        )
        error_data = json.dumps({"error": {"message": str(e)}})
        yield f"data: {error_data}\n\n".encode("utf-8")
        yield b"data: [DONE]\n\n"


@app.get("/v1/logs")
async def get_logs(
    model: str | None = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user)
):
    logs = call_logger.query_logs(user_id=current_user["id"], model=model, limit=limit, offset=offset)
    return {"data": logs}


@app.get("/v1/logs/summary")
async def get_logs_summary(
    model: str | None = None,
    current_user: dict = Depends(get_current_user)
):
    summary = call_logger.get_usage_summary(user_id=current_user["id"], model=model)
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


@app.post("/v1/user/routes")
async def create_user_route(
    route: RouteCreate,
    current_user: dict = Depends(get_current_user)
):
    try:
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
        update_data = route.dict(exclude_unset=True)
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
