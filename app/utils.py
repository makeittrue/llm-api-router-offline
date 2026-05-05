from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

# JWT配置
SECRET_KEY = "llm-router-super-secret-key-2024"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码是否正确"""
    # bcrypt算法最多支持72字节，自动截断
    plain_password_bytes = plain_password.encode('utf-8')[:72]
    hashed_password_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(plain_password_bytes, hashed_password_bytes)


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    # bcrypt算法最多支持72字节，自动截断
    password_bytes = password.encode('utf-8')[:72]
    # 生成哈希并转成字符串存储
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode('utf-8')


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
