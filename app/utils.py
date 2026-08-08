from __future__ import annotations

import base64
import os
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from cryptography.fernet import Fernet
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


# ========== 敏感数据加密（用户路由 API Key 等） ==========
#
# 使用环境变量 ROUTER_DATA_KEY 配置 Fernet 密钥（应用级加密）。未配置时处于
# 「仅限本地开发」模式：数据以明文存储并发出 UserWarning；生产环境必须配置，
# 否则丢失密钥后将无法解密已有数据（不可恢复）。
_DATA_KEY_LOADED = False
_fernet: Fernet | None = None


def _coerce_fernet_key(raw: str) -> bytes:
    """将用户提供的密钥字符串转为 Fernet 密钥字节。

    优先按 Fernet 标准（urlsafe base64 编码的 32 字节）解析；同时兼容
    `openssl rand -hex 32` 生成的 64 位十六进制串，方便直接沿用现有习惯。
    """
    try:
        Fernet(raw.encode())
        return raw.encode()
    except Exception:
        pass
    try:
        key_bytes = bytes.fromhex(raw)
    except ValueError as e:
        raise ValueError(
            "环境变量 ROUTER_DATA_KEY 不是有效的 Fernet 密钥："
            "请使用 `openssl rand -base64 32`（或 `openssl rand -hex 32`）生成。"
        ) from e
    if len(key_bytes) != 32:
        raise ValueError(
            f"环境变量 ROUTER_DATA_KEY 必须是 32 字节密钥（当前 {len(key_bytes)} 字节）；"
            "请使用 `openssl rand -base64 32` 生成。"
        )
    return base64.urlsafe_b64encode(key_bytes)


def _get_fernet() -> Fernet | None:
    """懒加载 Fernet 实例；未配置 ROUTER_DATA_KEY 时返回 None（明文模式）。"""
    global _DATA_KEY_LOADED, _fernet
    if not _DATA_KEY_LOADED:
        _DATA_KEY_LOADED = True
        raw = (os.getenv("ROUTER_DATA_KEY") or "").strip()
        if not raw:
            _fernet = None
            warnings.warn(
                "未设置环境变量 ROUTER_DATA_KEY，用户路由 API Key 将以明文存储；"
                "仅限本地开发，生产环境必须设置（详见 README「敏感数据加密」）。",
                UserWarning,
                stacklevel=2,
            )
        else:
            _fernet = Fernet(_coerce_fernet_key(raw))
    return _fernet


def reset_data_key_cache() -> None:
    """重置数据密钥缓存（供测试切换环境变量用）。"""
    global _DATA_KEY_LOADED, _fernet
    _DATA_KEY_LOADED = False
    _fernet = None


def data_key_configured() -> bool:
    """ROUTER_DATA_KEY 是否已配置（决定是否执行存量明文加密迁移）。"""
    return _get_fernet() is not None


def encrypt_secret(plaintext: str) -> str:
    """加密敏感字段；未配置数据密钥时原样返回（明文模式）。"""
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(stored: str | None) -> str | None:
    """解密敏感字段；兼容存量明文（非 Fernet 密文或解密失败时原样返回）。"""
    if not stored:
        return stored
    fernet = _get_fernet()
    if fernet is None or not stored.startswith("gAAAAA"):
        return stored
    try:
        return fernet.decrypt(stored.encode("utf-8")).decode("utf-8")
    except Exception:
        # 存量明文 / 密钥不匹配：原样返回，避免把明文丢弃
        return stored


def mask_api_key(key: str) -> str:
    """脱敏展示 API Key：保留前 3 位与后 4 位，中间打码（如 sk-***abcd）。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}***{key[-4:]}"
