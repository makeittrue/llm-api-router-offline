from __future__ import annotations

import json
import time

import httpx

from app.config import FeishuConfig


class FeishuNotifier:
    def __init__(self, config: FeishuConfig):
        self.config = config
        self._tenant_access_token: str | None = None
        self._tenant_access_token_expire_at = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled and self.config.app_id and self.config.app_secret)

    def resolve_receive_id(self, username: str) -> str | None:
        return self.config.user_targets.get(username)

    async def _get_tenant_access_token(self) -> str:
        if not self.enabled:
            raise RuntimeError("飞书通知未启用或缺少 app_id/app_secret")

        now = time.time()
        if self._tenant_access_token and now < self._tenant_access_token_expire_at:
            return self._tenant_access_token

        url = f"{self.config.base_url.rstrip('/')}/open-apis/auth/v3/tenant_access_token/internal"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json={
                    "app_id": self.config.app_id,
                    "app_secret": self.config.app_secret,
                },
            )
            response.raise_for_status()
            data = response.json()

        if data.get("code") != 0:
            raise RuntimeError(f"飞书 tenant_access_token 获取失败: {data.get('msg') or data}")

        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise RuntimeError("飞书 tenant_access_token 响应缺少 token")

        expire = int(data.get("expire") or 7200)
        self._tenant_access_token = token
        self._tenant_access_token_expire_at = now + max(60, expire - 60)
        return token

    async def send_text(self, receive_id: str, text: str) -> dict:
        token = await self._get_tenant_access_token()
        url = f"{self.config.base_url.rstrip('/')}/open-apis/im/v1/messages"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                params={"receive_id_type": self.config.receive_id_type},
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        if data.get("code") != 0:
            raise RuntimeError(f"飞书消息发送失败: {data.get('msg') or data}")
        return data
