from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app import main
from app.logger import CallLogger
from app.utils import create_access_token, get_password_hash


class TokenVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test.db")
        self.original_logger = main.call_logger
        self.original_overrides = dict(main.app.dependency_overrides)

        self.client_cm = TestClient(main.app)
        self.client = self.client_cm.__enter__()

        main.call_logger = CallLogger(self.db_path)
        self.user_id = main.call_logger.create_user("tester", get_password_hash("secret"))

    def tearDown(self):
        main.call_logger = self.original_logger
        main.app.dependency_overrides = self.original_overrides
        self.client_cm.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def _token(self, token_version: int = 0) -> str:
        return create_access_token(
            {
                "sub": str(self.user_id),
                "username": "tester",
                "token_version": token_version,
            }
        )

    def test_regenerate_invalidates_previous_token(self):
        old_token = self._token()

        response = self.client.post(
            "/v1/user/token/regenerate",
            headers={"Authorization": f"Bearer {old_token}"},
        )

        self.assertEqual(response.status_code, 200)
        new_token = response.json()["access_token"]
        self.assertNotEqual(new_token, old_token)

        old_response = self.client.get(
            "/v1/user/token",
            headers={"Authorization": f"Bearer {old_token}"},
        )
        self.assertEqual(old_response.status_code, 401)

        new_response = self.client.get(
            "/v1/user/token",
            headers={"Authorization": f"Bearer {new_token}"},
        )
        self.assertEqual(new_response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
