from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from cryptography.fernet import Fernet

from app import main
from app.config import AppConfig, LogConfig, ProviderConfig
from app.logger import CallLogger
from app import utils


class SensitiveDataEncryptionTests(unittest.TestCase):
    """P4：用户路由 API Key 加密存储、脱敏展示与存量明文迁移。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.data_key = Fernet.generate_key().decode()
        utils.reset_data_key_cache()

    def tearDown(self):
        utils.reset_data_key_cache()
        self.temp_dir.cleanup()

    def _make_logger(self, *, with_key: bool) -> CallLogger:
        env = {"ROUTER_DATA_KEY": self.data_key if with_key else ""}
        with patch.dict(os.environ, env, clear=False):
            utils.reset_data_key_cache()
            return CallLogger(self.db_path)

    def _db_raw_key(self, route_id: int) -> str | None:
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT provider_api_key FROM user_routes WHERE id = ?", (route_id,)
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _create_route(self, logger: CallLogger, user_id: int, api_key: str = "sk-secret-abcdef123456") -> int:
        return logger.create_user_route(
            user_id=user_id,
            model="my-model",
            provider_name="custom",
            provider_base_url="https://api.example.com",
            provider_api_key=api_key,
            provider_model="upstream-model",
        )

    def test_encrypt_on_write_and_decrypt_on_read(self):
        logger = self._make_logger(with_key=True)
        user_id = logger.create_user("alice", "hash")
        route_id = self._create_route(logger, user_id)

        # 数据库中是密文（Fernet 密文以 gAAAAA 开头），不是明文
        raw = self._db_raw_key(route_id)
        self.assertTrue(raw.startswith("gAAAAA"))
        self.assertNotIn("sk-secret", raw)

        # 转发时解密出原文
        route = logger.get_user_route_by_model(user_id, "my-model")
        self.assertEqual(route["provider_api_key"], "sk-secret-abcdef123456")

        # 列表仅返回脱敏值，不返回完整明文
        routes = logger.get_user_routes(user_id)
        self.assertEqual(len(routes), 1)
        self.assertNotIn("provider_api_key", routes[0])
        self.assertEqual(routes[0]["provider_api_key_masked"], "sk-***3456")

    def test_plaintext_mode_without_data_key(self):
        logger = self._make_logger(with_key=False)
        user_id = logger.create_user("bob", "hash")
        route_id = self._create_route(logger, user_id)

        # 未配置密钥时以明文存储（仅限开发）
        self.assertEqual(self._db_raw_key(route_id), "sk-secret-abcdef123456")

    def test_legacy_plaintext_migrated_on_startup(self):
        # 先以明文模式写入存量数据
        logger = self._make_logger(with_key=False)
        user_id = logger.create_user("carol", "hash")
        route_id = self._create_route(logger, user_id, api_key="sk-legacy-key-0000")
        self.assertEqual(self._db_raw_key(route_id), "sk-legacy-key-0000")

        # 配置 ROUTER_DATA_KEY 后重新初始化：存量明文被自动加密迁移
        logger2 = self._make_logger(with_key=True)
        raw = self._db_raw_key(route_id)
        self.assertTrue(raw.startswith("gAAAAA"))
        route = logger2.get_user_route_by_model(user_id, "my-model")
        self.assertEqual(route["provider_api_key"], "sk-legacy-key-0000")

    def test_encrypt_decrypt_roundtrip_helpers(self):
        plain = "sk-helper-roundtrip"
        with patch.dict(os.environ, {"ROUTER_DATA_KEY": self.data_key}, clear=False):
            utils.reset_data_key_cache()
            encrypted = utils.encrypt_secret(plain)
            self.assertTrue(encrypted.startswith("gAAAAA"))
            self.assertEqual(utils.decrypt_secret(encrypted), plain)
            # 存量明文原样返回
            self.assertEqual(utils.decrypt_secret(plain), plain)


class AdminRoleTests(unittest.TestCase):
    """P5：首个注册用户为管理员，/v1/admin/* 仅管理员可访问。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.original_logger = main.call_logger
        self.original_config = main.app_config
        self.original_overrides = dict(main.app.dependency_overrides)

        self.client_cm = TestClient(main.app)
        self.client = self.client_cm.__enter__()

        config = AppConfig(
            log=LogConfig(db_path=self.db_path),
            providers=[ProviderConfig(name="demo", base_url="https://demo.example", api_key="x")],
        )
        main.app_config = config
        main.call_logger = CallLogger(self.db_path)
        utils.reset_data_key_cache()

    def tearDown(self):
        main.call_logger = self.original_logger
        main.app_config = self.original_config
        main.app.dependency_overrides = self.original_overrides
        self.client_cm.__exit__(None, None, None)
        self.temp_dir.cleanup()
        utils.reset_data_key_cache()

    def test_first_user_is_admin_second_is_user(self):
        with patch.dict(os.environ, {"LLM_ROUTER_ADMIN_USERNAME": ""}, clear=False):
            first = self.client.post("/register", json={"username": "boss", "password": "pw"})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["role"], "admin")

            second = self.client.post("/register", json={"username": "staff", "password": "pw"})
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["role"], "user")

    def test_admin_endpoints_403_for_regular_user(self):
        # 模拟普通用户（含 role=user），admin 接口应返回 403
        main.app.dependency_overrides[main.get_current_user] = lambda: {
            "id": 2, "username": "staff", "role": "user",
        }
        self.assertEqual(self.client.get("/v1/admin/providers").status_code, 403)
        self.assertEqual(self.client.get("/v1/admin/routes").status_code, 403)

    def test_admin_endpoints_200_for_admin(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {
            "id": 1, "username": "boss", "role": "admin",
        }
        response = self.client.get("/v1/admin/providers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"providers": [{"name": "demo", "base_url": "https://demo.example", "api_type": "openai"}]},
        )
        self.assertEqual(self.client.get("/v1/admin/routes").status_code, 200)

    def test_me_returns_role(self):
        main.app.dependency_overrides[main.get_current_user] = lambda: {
            "id": 1, "username": "boss", "role": "admin",
        }
        response = self.client.get("/v1/me")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "admin")

    def test_env_admin_username_promotes_user(self):
        with patch.dict(os.environ, {"LLM_ROUTER_ADMIN_USERNAME": "boss"}, clear=False):
            # 首个注册用户不再因「第一个用户」逻辑而成为 admin
            first = self.client.post("/register", json={"username": "alpha", "password": "pw"})
            self.assertEqual(first.json()["role"], "user")

            # 命中环境变量指定用户名的用户获得 admin
            second = self.client.post("/register", json={"username": "boss", "password": "pw"})
            self.assertEqual(second.json()["role"], "admin")

            # 引导逻辑在初始化时也会把已存在的 boss 提升为 admin
            main.call_logger = CallLogger(self.db_path)
            user = main.call_logger.get_user_by_username("boss")
            self.assertEqual(user["role"], "admin")


if __name__ == "__main__":
    unittest.main()
