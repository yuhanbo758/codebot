"""模型手动刷新要更新 CLI 缓存，并让 Codex 桥读取可运行的 Server 目录。"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_DATA = tempfile.TemporaryDirectory(prefix="codebot-model-refresh-tests-")
os.environ.setdefault("CODEBOT_DATA_DIR", _DATA.name)

from api.routes import chat  # noqa: E402
from config import app_config  # noqa: E402
from core.model_route_registry import model_route_registry  # noqa: E402
from core.opencode_ws import OpenCodeClient  # noqa: E402
from utils import installer  # noqa: E402


GPT6 = {"id": "openai/gpt-6-sol", "name": "GPT-6 Sol", "provider": "openai", "model": "gpt-6-sol"}


class ModelRefreshTests(unittest.IsolatedAsyncioTestCase):
    def test_existing_local_cli_precedes_bundled_and_models_follow_running_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = "opencode.exe" if os.name == "nt" else "opencode"
            npm_dir = root / "AppData" / "Roaming" / "npm"
            local = (npm_dir / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
                     if os.name == "nt" else root / "local" / executable)
            bundled = root / "resources" / "opencode" / executable
            for candidate in (local, bundled):
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(b"MZtest" if os.name == "nt" else b"test")
            environment = {
                "APPDATA": str(root / "AppData" / "Roaming"),
                "USERPROFILE": str(root),
                "LOCALAPPDATA": str(root / "AppData" / "Local"),
                "CODEBOT_RESOURCES_DIR": str(root / "resources"),
                "CODEBOT_OPENCODE_PATH": "",
            }
            with (
                patch.dict(os.environ, environment),
                patch.object(Path, "home", return_value=root),
                patch.object(app_config.opencode, "cli_path", ""),
                patch.object(installer.shutil, "which", side_effect=lambda name: str(local) if name == executable else None),
                patch.object(installer, "_opencode_server_process", None),
                patch.object(installer, "_opencode_server_command", None),
            ):
                commands = installer.collect_opencode_commands()
                self.assertEqual(commands[0], [str(local)])
                self.assertIn([str(bundled)], commands)

                # Server 实际使用回退程序时，模型 CLI 应读取同一程序的目录。
                with (
                    patch.object(installer, "_opencode_server_process", SimpleNamespace(poll=lambda: None)),
                    patch.object(installer, "_opencode_server_command", [str(bundled)]),
                ):
                    self.assertEqual(installer.collect_opencode_commands()[0], [str(bundled)])

    def test_codex_bridge_accepts_refreshed_gpt6_catalog(self):
        payload = {"connected": ["openai"], "all": [{
            "id": "openai", "name": "OpenAI", "models": {"gpt-6-sol": {
                "name": "GPT-6 Sol", "api": {"id": "gpt-6-sol", "npm": "@ai-sdk/openai"},
                "capabilities": {"reasoning": True, "toolcall": True},
            }},
        }]}
        routes, incompatible = model_route_registry.parse_runnable_catalog(
            payload, {"openai": {"type": "oauth", "access": "fake-access"}}, {},
        )
        self.assertFalse(incompatible)
        self.assertTrue(routes["openai/gpt-6-sol"].public_model()["runnable"])

    async def test_explicit_refresh_calls_cli_with_refresh_flag(self):
        runtime = OpenCodeClient("http://test")
        process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b"openai/gpt-6-sol\n", b"")))
        with (
            patch("core.opencode_ws.collect_opencode_commands", return_value=[["opencode"]]),
            patch("core.opencode_ws.asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn,
        ):
            models = await runtime.get_models_from_cli(refresh=True)
        self.assertEqual([item["id"] for item in models], ["openai/gpt-6-sol"])
        self.assertEqual(spawn.await_args.args[:3], ("opencode", "models", "--refresh"))

    async def test_failed_online_refresh_uses_existing_cli_cache(self):
        runtime = OpenCodeClient("http://test")
        with patch.object(runtime, "_run_opencode_models_cli", new=AsyncMock(side_effect=[[], [GPT6]])) as run:
            self.assertEqual(await runtime.get_models_from_cli(refresh=True), [GPT6])
        self.assertEqual(run.await_args_list[0].kwargs, {"refresh": True})
        self.assertEqual(run.await_args_list[1].kwargs, {})

    async def test_route_only_forces_refresh_when_requested(self):
        runtime = SimpleNamespace(
            base_url="http://127.0.0.1:11200",
            connected=True,
            get_models_from_cli=AsyncMock(return_value=[GPT6]),
        )
        old_client = chat.opencode_ws
        chat.opencode_ws = runtime
        try:
            with (
                patch.object(chat, "_load_current_server_models_for_refresh", new=AsyncMock(return_value=[GPT6])),
                patch.object(chat, "is_managed_opencode_server_running", return_value=False),
            ):
                result = await chat.get_models(refresh=True)
                self.assertTrue(result["success"])
                self.assertTrue(result["data"]["models"][0]["runnable"])
                runtime.get_models_from_cli.assert_awaited_with(refresh=True)
                await chat.get_models()
                runtime.get_models_from_cli.assert_awaited_with(refresh=False)
        finally:
            chat.opencode_ws = old_client


if __name__ == "__main__":
    unittest.main()
