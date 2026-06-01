from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.billing import calculate_request_cost
from app.config import BillingConfig
from app.models import ChatCompletionRequest, ChatCompletionResponse

# SQLite / 内存：超大 messages 或不可序列化对象会导致 log_call 抛错，进而让流式响应末尾变成 HTTP 500
_MAX_LOG_JSON_CHARS = 512_000


def _usage_tokens_triple(u: Any) -> tuple[int, int, int]:
    if u is None:
        return 0, 0, 0

    if isinstance(u, dict):
        return (
            int(u.get("prompt_tokens") or 0),
            int(u.get("completion_tokens") or 0),
            int(u.get("total_tokens") or 0),
        )

    pt = getattr(u, "prompt_tokens", None)
    ct = getattr(u, "completion_tokens", None)
    tt = getattr(u, "total_tokens", None)
    try:
        return int(pt or 0), int(ct or 0), int(tt or 0)
    except (TypeError, ValueError):
        return 0, 0, 0


def _usage_dict(u: Any) -> dict[str, Any] | None:
    if u is None:
        return None
    if isinstance(u, dict):
        return u
    if hasattr(u, "model_dump"):
        try:
            dumped = u.model_dump(mode="python", exclude_none=True)
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            return None
    return None


def build_request_log_meta(request: ChatCompletionRequest) -> dict[str, Any]:
    roles: list[str] = []
    message_chars: list[dict[str, Any]] = []
    last_user_preview: str | None = None
    for m in request.messages:
        roles.append(m.role)
        c = m.content
        if isinstance(c, str):
            ln = len(c)
            if m.role == "user":
                tail = c[:400] + ("…" if len(c) > 400 else "")
                last_user_preview = tail
        elif isinstance(c, list):
            ln = sum(len(str(p)) for p in c)
        else:
            ln = 0
        message_chars.append({"role": m.role, "chars": ln})
    meta: dict[str, Any] = {
        "message_count": len(request.messages),
        "roles": roles,
        "has_user": "user" in roles,
        "last_user_preview": last_user_preview,
        "message_chars": message_chars,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "stream": bool(request.stream),
    }
    # 记录 Kimi thinking 参数（对象，如 {"type": "enabled"}）
    thinking = getattr(request, "thinking", None)
    if thinking is not None:
        meta["thinking"] = thinking
    return meta


class CallLogger:
    def __init__(self, db_path: str = "logs.db", billing_config: BillingConfig | None = None):
        self.db_path = db_path
        self.billing_config = billing_config
        self._init_db()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS call_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                model TEXT NOT NULL,
                provider TEXT,
                provider_model TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                is_stream INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success',
                error_message TEXT,
                user_id TEXT,
                request_messages TEXT,
                created_at TEXT NOT NULL,
                duration_ms INTEGER DEFAULT 0,
                log_meta TEXT,
                cached_input_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                estimated_cost REAL,
                billing_currency TEXT,
                billing_rule TEXT,
                billing_meta TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_call_logs_model ON call_logs(model)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_call_logs_created_at ON call_logs(created_at)
        """)
        # 日志查询最常用的是按user_id+时间倒序，添加联合索引大幅提升查询速度
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_call_logs_user_id_created_at ON call_logs(user_id, created_at DESC)
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(call_logs)").fetchall()}
        if "log_meta" not in existing:
            try:
                conn.execute("ALTER TABLE call_logs ADD COLUMN log_meta TEXT")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        for column_name, column_type in (
            ("cached_input_tokens", "INTEGER DEFAULT 0"),
            ("cache_write_tokens", "INTEGER DEFAULT 0"),
            ("estimated_cost", "REAL"),
            ("billing_currency", "TEXT"),
            ("billing_rule", "TEXT"),
            ("billing_meta", "TEXT"),
        ):
            if column_name in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE call_logs ADD COLUMN {column_name} {column_type}")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        
        # 用户表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                token_version INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "token_version" not in existing:
            try:
                conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        
        # 用户路由表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_routes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                provider_name TEXT NOT NULL,
                provider_base_url TEXT NOT NULL,
                provider_api_key TEXT NOT NULL,
                provider_api_type TEXT DEFAULT 'openai',
                provider_model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, model)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_routes_user_id ON user_routes(user_id)
        """)

        # 用户 default 自动降级链配置
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_default_routes (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                models_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_notification_settings (
                user_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                daily_summary_enabled INTEGER NOT NULL DEFAULT 0,
                alerts_enabled INTEGER NOT NULL DEFAULT 0,
                feishu_app_id TEXT,
                feishu_app_secret TEXT,
                feishu_receive_id_type TEXT NOT NULL DEFAULT 'open_id',
                feishu_receive_id TEXT,
                daily_summary_time TEXT NOT NULL DEFAULT '09:00',
                updated_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS notification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                user_id TEXT,
                username TEXT,
                event_date TEXT,
                metric TEXT,
                threshold_value REAL,
                observed_value REAL,
                payload TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_notification_events_user_date
            ON notification_events(user_id, event_date DESC)
        """)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(notification_events)").fetchall()}
        if "status" not in existing:
            try:
                conn.execute("ALTER TABLE notification_events ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        
        conn.commit()
        conn.close()
        
    # ========== 用户相关方法 ==========
    def create_user(self, username: str, password_hash: str) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, created_at)
                VALUES (?, ?, ?)
                """,
                (username, password_hash, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"用户名 {username} 已存在")
        finally:
            conn.close()
            
    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT id, username, password_hash, token_version FROM users WHERE username = ?
                """,
                (username,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
            
    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT id, username, token_version FROM users WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def increment_user_token_version(self, user_id: int) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE users
                SET token_version = token_version + 1
                WHERE id = ?
                """,
                (user_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("用户不存在")
            row = cursor.execute(
                "SELECT token_version FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
            if row is None:
                raise ValueError("用户不存在")
            conn.commit()
            return int(row[0])
        finally:
            conn.close()

    def list_users(self) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT id, username, created_at
                FROM users
                ORDER BY username ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
            
    # ========== 用户路由相关方法 ==========
    def create_user_route(
        self,
        user_id: int,
        model: str,
        provider_name: str,
        provider_base_url: str,
        provider_api_key: str,
        provider_model: str,
        provider_api_type: str = "openai",
    ) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_routes (
                    user_id, model, provider_name, provider_base_url,
                    provider_api_key, provider_api_type, provider_model, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, model, provider_name, provider_base_url,
                    provider_api_key, provider_api_type, provider_model,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(f"模型 {model} 已存在")
        finally:
            conn.close()
            
    def get_user_routes(self, user_id: int) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT id, model, provider_name, provider_base_url,
                       provider_api_type, provider_model, created_at
                FROM user_routes
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
            
    def get_user_route_by_model(self, user_id: int, model: str) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT id, user_id, model, provider_name, provider_base_url,
                       provider_api_key, provider_api_type, provider_model
                FROM user_routes
                WHERE user_id = ? AND model = ?
                """,
                (user_id, model),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
            
    def update_user_route(
        self,
        route_id: int,
        user_id: int,
        **kwargs,
    ) -> bool:
        allowed_fields = [
            "model", "provider_name", "provider_base_url",
            "provider_api_key", "provider_api_type", "provider_model"
        ]
        update_fields = [f"{k} = ?" for k in kwargs.keys() if k in allowed_fields]
        if not update_fields:
            return False
            
        values = list(kwargs[k] for k in kwargs.keys() if k in allowed_fields)
        values.append(route_id)
        values.append(user_id)
        
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                UPDATE user_routes
                SET {', '.join(update_fields)}
                WHERE id = ? AND user_id = ?
                """,
                values,
            )
            conn.commit()
            return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            raise ValueError(f"模型 {kwargs.get('model')} 已存在")
        finally:
            conn.close()
            
    def delete_user_route(self, route_id: int, user_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM user_routes
                WHERE id = ? AND user_id = ?
                """,
                (route_id, user_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    def get_user_default_route(self, user_id: int) -> dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT user_id, enabled, models_json, updated_at
                FROM user_default_routes
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if not row:
                return {"user_id": user_id, "enabled": False, "models": [], "updated_at": None}

            try:
                models = json.loads(row["models_json"] or "[]")
            except json.JSONDecodeError:
                models = []
            if not isinstance(models, list):
                models = []
            normalized_models = [m.strip() for m in models if isinstance(m, str) and m.strip()]
            return {
                "user_id": row["user_id"],
                "enabled": bool(row["enabled"]),
                "models": normalized_models,
                "updated_at": row["updated_at"],
            }
        finally:
            conn.close()

    def upsert_user_default_route(
        self,
        user_id: int,
        *,
        enabled: bool,
        models: list[str],
    ) -> dict[str, Any]:
        normalized_models = [m.strip() for m in models if isinstance(m, str) and m.strip()]
        updated_at = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO user_default_routes (user_id, enabled, models_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    models_json = excluded.models_json,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    1 if enabled else 0,
                    json.dumps(normalized_models, ensure_ascii=False),
                    updated_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_user_default_route(user_id)

    def get_user_notification_settings(self, user_id: int) -> dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT
                    user_id,
                    enabled,
                    daily_summary_enabled,
                    alerts_enabled,
                    feishu_app_id,
                    feishu_app_secret,
                    feishu_receive_id_type,
                    feishu_receive_id,
                    daily_summary_time,
                    updated_at
                FROM user_notification_settings
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if not row:
                return {
                    "user_id": user_id,
                    "enabled": False,
                    "daily_summary_enabled": False,
                    "alerts_enabled": False,
                    "feishu_app_id": "",
                    "feishu_app_secret": None,
                    "feishu_app_secret_configured": False,
                    "feishu_receive_id_type": "open_id",
                    "feishu_receive_id": "",
                    "daily_summary_time": "09:00",
                    "updated_at": None,
                }
            data = dict(row)
            data["enabled"] = bool(data.get("enabled"))
            data["daily_summary_enabled"] = bool(data.get("daily_summary_enabled"))
            data["alerts_enabled"] = bool(data.get("alerts_enabled"))
            data["feishu_app_secret_configured"] = bool(data.get("feishu_app_secret"))
            data["feishu_app_secret"] = None
            return data
        finally:
            conn.close()

    def get_user_notification_settings_with_secret(self, user_id: int) -> dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT
                    user_id,
                    enabled,
                    daily_summary_enabled,
                    alerts_enabled,
                    feishu_app_id,
                    feishu_app_secret,
                    feishu_receive_id_type,
                    feishu_receive_id,
                    daily_summary_time,
                    updated_at
                FROM user_notification_settings
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
            if not row:
                return {
                    "user_id": user_id,
                    "enabled": False,
                    "daily_summary_enabled": False,
                    "alerts_enabled": False,
                    "feishu_app_id": "",
                    "feishu_app_secret": "",
                    "feishu_receive_id_type": "open_id",
                    "feishu_receive_id": "",
                    "daily_summary_time": "09:00",
                    "updated_at": None,
                }
            data = dict(row)
            data["enabled"] = bool(data.get("enabled"))
            data["daily_summary_enabled"] = bool(data.get("daily_summary_enabled"))
            data["alerts_enabled"] = bool(data.get("alerts_enabled"))
            return data
        finally:
            conn.close()

    def upsert_user_notification_settings(
        self,
        user_id: int,
        *,
        enabled: bool,
        daily_summary_enabled: bool,
        alerts_enabled: bool,
        feishu_app_id: str,
        feishu_app_secret: str | None,
        feishu_receive_id_type: str,
        feishu_receive_id: str,
        daily_summary_time: str,
    ) -> dict[str, Any]:
        current = self.get_user_notification_settings_with_secret(user_id)
        app_secret = (
            feishu_app_secret.strip()
            if isinstance(feishu_app_secret, str) and feishu_app_secret.strip()
            else str(current.get("feishu_app_secret") or "")
        )
        updated_at = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO user_notification_settings (
                    user_id,
                    enabled,
                    daily_summary_enabled,
                    alerts_enabled,
                    feishu_app_id,
                    feishu_app_secret,
                    feishu_receive_id_type,
                    feishu_receive_id,
                    daily_summary_time,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    daily_summary_enabled = excluded.daily_summary_enabled,
                    alerts_enabled = excluded.alerts_enabled,
                    feishu_app_id = excluded.feishu_app_id,
                    feishu_app_secret = excluded.feishu_app_secret,
                    feishu_receive_id_type = excluded.feishu_receive_id_type,
                    feishu_receive_id = excluded.feishu_receive_id,
                    daily_summary_time = excluded.daily_summary_time,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    1 if enabled else 0,
                    1 if daily_summary_enabled else 0,
                    1 if alerts_enabled else 0,
                    feishu_app_id.strip(),
                    app_secret,
                    feishu_receive_id_type.strip() or "open_id",
                    feishu_receive_id.strip(),
                    daily_summary_time.strip() or "09:00",
                    updated_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return self.get_user_notification_settings(user_id)

    def list_active_notification_settings(self) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT
                    s.user_id,
                    u.username,
                    s.enabled,
                    s.daily_summary_enabled,
                    s.alerts_enabled,
                    s.feishu_app_id,
                    s.feishu_app_secret,
                    s.feishu_receive_id_type,
                    s.feishu_receive_id,
                    s.daily_summary_time,
                    s.updated_at
                FROM user_notification_settings s
                JOIN users u ON u.id = s.user_id
                WHERE s.enabled = 1
                  AND (s.daily_summary_enabled = 1 OR s.alerts_enabled = 1)
                ORDER BY s.user_id ASC
                """
            ).fetchall()
            results = [dict(row) for row in rows]
            for item in results:
                item["enabled"] = bool(item.get("enabled"))
                item["daily_summary_enabled"] = bool(item.get("daily_summary_enabled"))
                item["alerts_enabled"] = bool(item.get("alerts_enabled"))
            return results
        finally:
            conn.close()

    def log_call(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse | None = None,
        response_body: dict[str, Any] | None = None,
        provider_name: str | None = None,
        provider_model: str | None = None,
        duration_ms: int = 0,
        status: str = "success",
        error_message: str | None = None,
        user_id: int | None = None,
        log_meta: dict[str, Any] | None = None,
        stream_usage: dict[str, int] | None = None,
    ):
        conn = sqlite3.connect(self.db_path)
        try:
            is_stream = 1 if request.stream else 0
            created_at = datetime.now(timezone.utc)

            if stream_usage:
                pt = int(stream_usage.get("prompt_tokens") or 0)
                ct = int(stream_usage.get("completion_tokens") or 0)
                tt = int(stream_usage.get("total_tokens") or 0)
            elif response_body is not None:
                pt, ct, tt = _usage_tokens_triple(response_body.get("usage"))
            elif response is not None:
                pt, ct, tt = _usage_tokens_triple(response.usage)
            else:
                pt = ct = tt = 0

            request_id = None
            if response_body is not None:
                rid = response_body.get("id")
                request_id = str(rid) if rid is not None else None
            elif response is not None:
                request_id = response.id

            usage_raw: dict[str, Any] | None = None
            if response_body is not None:
                usage_raw = _usage_dict(response_body.get("usage"))
            elif response is not None:
                usage_raw = _usage_dict(response.usage)
            if usage_raw is None and log_meta:
                usage_raw = (
                    _usage_dict(log_meta.get("response", {}).get("usage_raw"))
                    or _usage_dict(log_meta.get("stream", {}).get("usage_raw"))
                )
            if usage_raw is None and stream_usage:
                usage_raw = dict(stream_usage)

            try:
                messages_json = json.dumps(
                    [m.model_dump(mode="python", exclude_none=True) for m in request.messages],
                    ensure_ascii=False,
                )
            except (TypeError, ValueError) as e:
                messages_json = json.dumps(
                    [{"role": "system", "content": f"[request_messages 无法序列化: {e}]"}],
                    ensure_ascii=False,
                )
            if len(messages_json) > _MAX_LOG_JSON_CHARS:
                messages_json = json.dumps(
                    [
                        {
                            "role": "system",
                            "content": (
                                f"[日志已截断] 原始 messages JSON 长度 {len(messages_json)}，"
                                f"超过上限 {_MAX_LOG_JSON_CHARS}，未写入完整内容。"
                            ),
                        }
                    ],
                    ensure_ascii=False,
                )

            log_meta_json: str | None = None
            if log_meta is not None:
                try:
                    log_meta_json = json.dumps(log_meta, ensure_ascii=False)
                except (TypeError, ValueError) as e:
                    log_meta_json = json.dumps({"_error": f"log_meta 序列化失败: {e}"}, ensure_ascii=False)
                if len(log_meta_json) > _MAX_LOG_JSON_CHARS:
                    log_meta_json = json.dumps(
                        {"_truncated": True, "_original_chars": len(log_meta_json)},
                        ensure_ascii=False,
                    )

            billing_result = None
            if status == "success":
                billing_result = calculate_request_cost(
                    billing_config=self.billing_config,
                    provider_name=provider_name,
                    provider_model=provider_model,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    usage_raw=usage_raw,
                    created_at=created_at,
                )

            billing_meta_json: str | None = None
            billing_currency: str | None = None
            billing_rule: str | None = None
            cached_input_tokens = 0
            cache_write_tokens = 0
            estimated_cost: float | None = None
            if billing_result is not None:
                billing_currency = billing_result.get("currency")
                patterns = [str(p) for p in (billing_result.get("rule_model_patterns") or [])]
                billing_rule = f"{billing_result.get('rule_provider')}:{'|'.join(patterns)}"
                cached_input_tokens = int(billing_result.get("cached_input_tokens") or 0)
                cache_write_tokens = int(billing_result.get("cache_write_tokens") or 0)
                estimated_cost = float((billing_result.get("costs") or {}).get("total_cost") or 0)
                try:
                    billing_meta_json = json.dumps(billing_result, ensure_ascii=False)
                except (TypeError, ValueError) as e:
                    billing_meta_json = json.dumps({"_error": f"billing_meta 序列化失败: {e}"}, ensure_ascii=False)

            conn.execute(
                """
                INSERT INTO call_logs (
                    request_id, model, provider, provider_model,
                    prompt_tokens, completion_tokens, total_tokens,
                    is_stream, status, error_message, user_id,
                    request_messages, created_at, duration_ms, log_meta,
                    cached_input_tokens, cache_write_tokens, estimated_cost,
                    billing_currency, billing_rule, billing_meta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    request.model,
                    provider_name,
                    provider_model,
                    pt,
                    ct,
                    tt,
                    is_stream,
                    status,
                    error_message,
                    user_id,
                    messages_json,
                    created_at.isoformat(),
                    duration_ms,
                    log_meta_json,
                    cached_input_tokens,
                    cache_write_tokens,
                    estimated_cost,
                    billing_currency,
                    billing_rule,
                    billing_meta_json,
                ),
            )
            conn.commit()
        except Exception as e:
            print(f"[WARN] log_call 写入失败（不影响 API 响应体）: {e!r}")
        finally:
            conn.close()

    def query_logs(
        self,
        user_id: int | None = None,
        model: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conditions = []
            params = []
            if user_id:
                conditions.append("user_id = ?")
                params.append(str(user_id))
            if model:
                conditions.append("model = ?")
                params.append(model)
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            sql = f"""
                SELECT * FROM call_logs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def count_logs(
        self,
        user_id: int | None = None,
        model: str | None = None,
    ) -> int:
        conn = sqlite3.connect(self.db_path)
        try:
            conditions = []
            params: list[Any] = []
            if user_id:
                conditions.append("user_id = ?")
                params.append(str(user_id))
            if model:
                conditions.append("model = ?")
                params.append(model)
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            sql = f"SELECT COUNT(*) FROM call_logs {where_clause}"
            row = conn.execute(sql, params).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
        finally:
            conn.close()

    def get_usage_summary(
        self, user_id: int | None = None, model: str | None = None, month: str | None = None
    ) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conditions = ["status = 'success'"]
            params = []
            if user_id:
                conditions.append("user_id = ?")
                params.append(str(user_id))
            if model:
                conditions.append("model = ?")
                params.append(model)
            if month:
                conditions.append("strftime('%Y-%m', created_at) = ?")
                params.append(month)
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            sql = f"""
                SELECT
                    model,
                    COUNT(*) as call_count,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(cached_input_tokens) as cached_input_tokens,
                    SUM(cache_write_tokens) as cache_write_tokens,
                    SUM(estimated_cost) as estimated_cost,
                    AVG(duration_ms) as avg_duration_ms,
                    'CNY' as billing_currency
                FROM call_logs
                {where_clause}
                GROUP BY model
            """
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_billing_summary(
        self, user_id: int | None = None, model: str | None = None, month: str | None = None
    ) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conditions = ["status = 'success'"]
            params = []
            if user_id:
                conditions.append("user_id = ?")
                params.append(str(user_id))
            if model:
                conditions.append("model = ?")
                params.append(model)
            if month:
                conditions.append("strftime('%Y-%m', created_at) = ?")
                params.append(month)

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            sql = f"""
                SELECT
                    model,
                    provider,
                    provider_model,
                    billing_currency,
                    COUNT(*) as call_count,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    SUM(total_tokens) as total_tokens,
                    SUM(cached_input_tokens) as cached_input_tokens,
                    SUM(cache_write_tokens) as cache_write_tokens,
                    SUM(estimated_cost) as estimated_cost_total,
                    AVG(COALESCE(estimated_cost, 0)) as avg_cost_per_call,
                    AVG(duration_ms) as avg_duration_ms
                FROM call_logs
                {where_clause}
                GROUP BY model, provider, provider_model, billing_currency
                ORDER BY estimated_cost_total DESC, total_tokens DESC, model ASC
            """
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_user_usage_rollups(
        self,
        start_at: str,
        end_at: str,
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conditions = [
                "cl.status = 'success'",
                "cl.user_id IS NOT NULL",
                "cl.created_at >= ?",
                "cl.created_at < ?",
            ]
            params: list[Any] = [start_at, end_at]
            if user_id is not None:
                conditions.append("cl.user_id = ?")
                params.append(str(user_id))

            where_clause = "WHERE " + " AND ".join(conditions)
            sql = f"""
                SELECT
                    CAST(cl.user_id AS INTEGER) as user_id,
                    COALESCE(u.username, '') as username,
                    COUNT(*) as call_count,
                    COALESCE(SUM(cl.prompt_tokens), 0) as total_prompt_tokens,
                    COALESCE(SUM(cl.completion_tokens), 0) as total_completion_tokens,
                    COALESCE(SUM(cl.total_tokens), 0) as total_tokens,
                    COALESCE(SUM(cl.cached_input_tokens), 0) as cached_input_tokens,
                    COALESCE(SUM(cl.cache_write_tokens), 0) as cache_write_tokens,
                    COALESCE(SUM(cl.estimated_cost), 0) as estimated_cost,
                    COALESCE(MAX(cl.billing_currency), 'CNY') as billing_currency,
                    COALESCE(AVG(cl.duration_ms), 0) as avg_duration_ms
                FROM call_logs cl
                LEFT JOIN users u ON u.id = CAST(cl.user_id AS INTEGER)
                {where_clause}
                GROUP BY cl.user_id, u.username
                ORDER BY estimated_cost DESC, total_tokens DESC, username ASC
            """
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_model_usage_rollups(
        self,
        start_at: str,
        end_at: str,
        user_id: int,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT
                    model,
                    provider,
                    provider_model,
                    COUNT(*) as call_count,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(estimated_cost), 0) as estimated_cost
                FROM call_logs
                WHERE status = 'success'
                  AND user_id = ?
                  AND created_at >= ?
                  AND created_at < ?
                GROUP BY model, provider, provider_model
                ORDER BY estimated_cost DESC, total_tokens DESC, model ASC
                LIMIT ?
                """,
                (str(user_id), start_at, end_at, int(limit)),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def has_notification_event(self, event_key: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM notification_events WHERE event_key = ? LIMIT 1",
                (event_key,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def claim_notification_event(
        self,
        *,
        event_key: str,
        event_type: str,
        user_id: int | None,
        username: str | None,
        event_date: str | None,
        metric: str | None = None,
        threshold_value: float | None = None,
        observed_value: float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        conn = sqlite3.connect(self.db_path)
        try:
            payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
            conn.execute(
                """
                INSERT INTO notification_events (
                    event_key, event_type, status, user_id, username, event_date, metric,
                    threshold_value, observed_value, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_key,
                    event_type,
                    "pending",
                    str(user_id) if user_id is not None else None,
                    username,
                    event_date,
                    metric,
                    threshold_value,
                    observed_value,
                    payload_json,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def update_notification_event(
        self,
        event_key: str,
        *,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
            conn.execute(
                """
                UPDATE notification_events
                SET status = ?, payload = COALESCE(?, payload)
                WHERE event_key = ?
                """,
                (status, payload_json, event_key),
            )
            conn.commit()
        finally:
            conn.close()

    def delete_notification_event(self, event_key: str) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DELETE FROM notification_events WHERE event_key = ?", (event_key,))
            conn.commit()
        finally:
            conn.close()
