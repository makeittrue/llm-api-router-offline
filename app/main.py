from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
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
        hashed_password = get_password_hash(user.password[:72])
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
    # bcrypt算法最多支持72字节，自动截断，和注册保持一致
    if not user or not verify_password(form_data.password[:72], user["password_hash"]):
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
