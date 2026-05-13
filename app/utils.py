from __future__ import annotations

import os
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

# 仅当未设置任何 JWT 密钥环境变量时使用；生产环境必须通过环境变量覆盖
_DEFAULT_DEV_JWT_SECRET = "llm-router-super-secret-key-2024"
# 通过环境变量显式设置时的最小长度（过短易被撞库或误配）
_MIN_JWT_SECRET_LEN = 16
ALGORITHM = "HS256"


def _read_jwt_secret() -> str:
    """优先 LLM_ROUTER_JWT_SECRET，其次 SECRET_KEY；均未设置则使用开发默认并告警。"""
    for env_name in ("LLM_ROUTER_JWT_SECRET", "SECRET_KEY"):
        raw = os.getenv(env_name)
        if raw is not None and raw.strip():
            secret = raw.strip()
            if len(secret) < _MIN_JWT_SECRET_LEN:
                raise ValueError(
                    f"环境变量 {env_name} 长度须 >= {_MIN_JWT_SECRET_LEN}（当前 {len(secret)}）；"
                    "请使用足够长的随机串，例如：openssl rand -hex 32"
                )
            return secret
    warnings.warn(
        "未设置环境变量 LLM_ROUTER_JWT_SECRET（或 SECRET_KEY），正在使用内置开发用 JWT 密钥；"
        "生产环境必须设置随机密钥，否则存在严重安全风险。详见 README「环境变量（JWT）」。",
        UserWarning,
        stacklevel=2,
    )
    return _DEFAULT_DEV_JWT_SECRET


def _read_access_token_expire_days() -> int:
    raw = (os.getenv("LLM_ROUTER_ACCESS_TOKEN_EXPIRE_DAYS") or "7").strip()
    try:
        n = int(raw)
    except ValueError as e:
        raise ValueError(
            f"环境变量 LLM_ROUTER_ACCESS_TOKEN_EXPIRE_DAYS 必须为整数，当前值: {raw!r}"
        ) from e
    if n < 1:
        raise ValueError("环境变量 LLM_ROUTER_ACCESS_TOKEN_EXPIRE_DAYS 必须 >= 1")
    return n


SECRET_KEY = _read_jwt_secret()
ACCESS_TOKEN_EXPIRE_DAYS = _read_access_token_expire_days()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码是否正确"""
    # bcrypt算法最多支持72字节，自动截断
    plain_password_bytes = plain_password.encode("utf-8")[:72]
    hashed_password_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(plain_password_bytes, hashed_password_bytes)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    # bcrypt算法最多支持72字节，自动截断
    password_bytes = password.encode("utf-8")[:72]
    # 生成哈希并转成字符串存储
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict[str, Any]) -> str:
    """创建JWT访问令牌"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> dict[str, Any] | None:
    """验证JWT令牌，返回解码后的数据"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
