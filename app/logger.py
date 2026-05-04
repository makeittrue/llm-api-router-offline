from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import ChatCompletionRequest, ChatCompletionResponse


class CallLogger:
    def __init__(self, db_path: str = "logs.db"):
        self.db_path = db_path
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
                duration_ms INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_call_logs_model ON call_logs(model)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_call_logs_created_at ON call_logs(created_at)
        """)
        
        # 用户表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        
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
                SELECT id, username, password_hash FROM users WHERE username = ?
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
                SELECT id, username FROM users WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            return dict(row) if row else None
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

    def log_call(
        self,
        request: ChatCompletionRequest,
        response: ChatCompletionResponse | None = None,
        provider_name: str | None = None,
        provider_model: str | None = None,
        duration_ms: int = 0,
        status: str = "success",
        error_message: str | None = None,
        user_id: int | None = None,
    ):
        conn = sqlite3.connect(self.db_path)
        try:
            usage = response.usage if response else None
            request_id = response.id if response else None
            is_stream = 1 if request.stream else 0

            messages_json = json.dumps(
                [m.model_dump(exclude_none=True) for m in request.messages],
                ensure_ascii=False,
            )

            conn.execute(
                """
                INSERT INTO call_logs (
                    request_id, model, provider, provider_model,
                    prompt_tokens, completion_tokens, total_tokens,
                    is_stream, status, error_message, user_id,
                    request_messages, created_at, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    request.model,
                    provider_name,
                    provider_model,
                    usage.prompt_tokens if usage else 0,
                    usage.completion_tokens if usage else 0,
                    usage.total_tokens if usage else 0,
                    is_stream,
                    status,
                    error_message,
                    user_id,
                    messages_json,
                    datetime.now(timezone.utc).isoformat(),
                    duration_ms,
                ),
            )
            conn.commit()
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

    def get_usage_summary(
        self, user_id: int | None = None, model: str | None = None
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
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            sql = f"""
                SELECT
                    model,
                    COUNT(*) as call_count,
                    SUM(prompt_tokens) as total_prompt_tokens,
                    SUM(completion_tokens) as total_completion_tokens,
                    SUM(total_tokens) as total_tokens,
                    AVG(duration_ms) as avg_duration_ms
                FROM call_logs
                {where_clause}
                GROUP BY model
            """
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
