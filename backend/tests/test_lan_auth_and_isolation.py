"""局域网认证和隔离执行失败关闭策略的回归测试。"""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from starlette.requests import Request


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import app_config  # noqa: E402
from core.lan_auth import (  # noqa: E402
    SESSION_COOKIE,
    consume_pairing_code,
    create_session_token,
    issue_pairing_code,
    request_is_authenticated,
    validate_session_token,
)
from core.sandbox.manager import SandboxManager  # noqa: E402
from main import app  # noqa: E402


def make_request(host: str, *, token: str = "", cookie: str = "") -> Request:
    headers = [(b"host", b"192.168.1.8:15682")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode("utf-8")))
    if cookie:
        headers.append((b"cookie", f"{SESSION_COOKIE}={cookie}".encode("utf-8")))
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/api/chat/models",
        "query_string": b"",
        "headers": headers,
        "server": ("192.168.1.8", 15682),
        "client": (host, 50000),
    })


class LanAuthTests(unittest.TestCase):
    def setUp(self):
        self.original_enabled = app_config.security.lan_auth_enabled
        self.original_token = app_config.security.lan_api_token
        app_config.security.lan_auth_enabled = True
        app_config.security.lan_api_token = "test-master-token"

    def tearDown(self):
        app_config.security.lan_auth_enabled = self.original_enabled
        app_config.security.lan_api_token = self.original_token

    def test_loopback_remains_compatible_but_remote_requires_auth(self):
        self.assertTrue(request_is_authenticated(make_request("127.0.0.1")))
        self.assertFalse(request_is_authenticated(make_request("192.168.1.20")))
        self.assertTrue(request_is_authenticated(make_request("192.168.1.20", token="test-master-token")))

    def test_pairing_code_creates_signed_remote_session(self):
        local = make_request("127.0.0.1")
        remote = make_request("192.168.1.20")
        with patch("core.lan_auth.secrets.randbelow", return_value=123456):
            code, _ = issue_pairing_code(local)
        self.assertEqual(code, "123456")
        self.assertTrue(consume_pairing_code(remote, code))
        session, _ = create_session_token()
        self.assertTrue(validate_session_token(session))
        self.assertTrue(request_is_authenticated(make_request("192.168.1.20", cookie=session)))
        self.assertFalse(validate_session_token(session + "tampered"))


class LanAuthMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_api_requires_pairing_then_accepts_signed_cookie(self):
        original_enabled = app_config.security.lan_auth_enabled
        original_token = app_config.security.lan_api_token
        app_config.security.lan_auth_enabled = True
        app_config.security.lan_api_token = "middleware-test-token"
        try:
            with patch("core.lan_auth.secrets.randbelow", return_value=654321):
                code, _ = issue_pairing_code(make_request("127.0.0.1"))
            transport = httpx.ASGITransport(app=app, client=("192.168.1.20", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://192.168.1.8:15682") as client:
                blocked = await client.post("/api/security/unpair")
                self.assertEqual(blocked.status_code, 401)
                self.assertEqual(blocked.headers.get("x-codebot-pairing-required"), "1")

                paired = await client.post("/api/security/pair", json={"code": code})
                self.assertEqual(paired.status_code, 200)
                self.assertIn(SESSION_COOKIE, client.cookies)

                allowed = await client.post("/api/security/unpair")
                self.assertEqual(allowed.status_code, 200)
        finally:
            app_config.security.lan_auth_enabled = original_enabled
            app_config.security.lan_api_token = original_token


class IsolationFailClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_runtime_path_cannot_select_arbitrary_executable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_runtime = temp_path / "not-a-sandbox.exe"
            fake_runtime.write_bytes(b"not executable")
            config = SimpleNamespace(
                workspace_dir="",
                isolation_backend="windows_sandbox",
                execution_mode="sandbox",
                exec_timeout=10,
                network_enabled=False,
                runtime_binary=str(fake_runtime),
            )
            manager = SandboxManager(temp_path, config)
            with patch("core.sandbox.manager.sys.platform", "win32"):
                with patch("core.sandbox.manager.shutil.which", return_value=None):
                    with patch.dict("core.sandbox.manager.os.environ", {"WINDIR": str(temp_path)}, clear=False):
                        self.assertIsNone(manager._resolve_windows_sandbox())

    async def test_unselected_backend_is_not_probed_or_run_on_host(self):
        config = SimpleNamespace(
            workspace_dir="",
            isolation_backend="none",
            execution_mode="sandbox",
            exec_timeout=10,
            network_enabled=False,
            runtime_binary="",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = SandboxManager(Path(temp_dir), config)
            with patch.object(manager, "_resolve_windows_sandbox") as resolver:
                with patch.object(manager, "_run_local_command") as local_run:
                    status = manager.get_status()
                    result = await manager.execute("Write-Output should-not-run")
        self.assertFalse(status["backend_selected"])
        self.assertEqual(status["mode"], "not_configured")
        self.assertFalse(result.success)
        self.assertEqual(result.execution_mode, "isolation_not_configured")
        resolver.assert_not_called()
        local_run.assert_not_called()

    async def test_sandbox_mode_never_falls_back_to_host(self):
        config = SimpleNamespace(
            workspace_dir="",
            isolation_backend="windows_sandbox",
            execution_mode="sandbox",
            exec_timeout=10,
            network_enabled=False,
            runtime_binary="",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = SandboxManager(Path(temp_dir), config)
            with patch.object(manager, "_resolve_windows_sandbox", return_value=None):
                with patch.object(manager, "_run_local_command") as local_run:
                    result = await manager.execute("Write-Output should-not-run")
        self.assertFalse(result.success)
        self.assertEqual(result.execution_mode, "isolation_unavailable")
        local_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
