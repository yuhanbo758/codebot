"""启动关键路径回归：本地 API 不依赖 AI 服务或向量库提前就绪。"""
import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_DATA = tempfile.TemporaryDirectory(prefix="codebot-startup-tests-")
os.environ.setdefault("CODEBOT_DATA_DIR", _DATA.name)

from core.memory_manager import MemoryManager
from utils import installer


class LazyMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_operations_and_close_do_not_load_vector_store(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "core.memory_manager._create_chroma_client"
        ) as create:
            manager = MemoryManager(data_dir=directory)
            try:
                self.assertEqual(await manager.get_conversations(), [])
            finally:
                manager.close()
            create.assert_not_called()

    async def test_collections_share_one_client_and_failed_initialization_can_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "core.memory_manager._create_chroma_client"
        ) as create:
            client = create.return_value
            memories, facts = object(), object()
            client.get_or_create_collection.side_effect = [
                memories, RuntimeError("temporary failure"), memories, facts
            ]
            manager = MemoryManager(data_dir=directory)
            try:
                with self.assertRaisesRegex(RuntimeError, "temporary failure"):
                    _ = manager.facts_collection
                self.assertIsNone(manager._chroma_client)
                self.assertIs(manager.memory_collection, memories)
                self.assertIs(manager.facts_collection, facts)
                self.assertIs(manager.chroma_client, client)
                self.assertEqual(create.call_count, 2)
                self.assertEqual(client.get_or_create_collection.call_count, 4)
            finally:
                manager.close()


class BackgroundStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_ready_before_opencode_and_sync_uses_resolved_port(self):
        import main

        started, release, connected = asyncio.Event(), asyncio.Event(), asyncio.Event()
        client = SimpleNamespace(base_url="http://127.0.0.1:11200", connected=False)

        async def start(_port):
            started.set()
            await release.wait()
            return 11201

        async def connect():
            client.connected = True
            connected.set()

        client.connect = connect
        client.disconnect = AsyncMock()
        scheduler = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        with ExitStack() as stack:
            for name in ("conversations_db", "tasks_db", "MemoryManager", "NotificationService"):
                stack.enter_context(patch.object(main, name))
            stack.enter_context(patch.object(main, "SandboxManager", return_value=SimpleNamespace(shutdown=AsyncMock())))
            stack.enter_context(patch.object(main, "OpenCodeClient", return_value=client))
            stack.enter_context(patch.object(main, "TaskScheduler", return_value=scheduler))
            stack.enter_context(patch.object(main, "start_opencode_server", side_effect=start))
            stack.enter_context(patch.object(main, "stop_opencode_server"))
            stack.enter_context(patch.object(main, "_seed_builtin_skills"))
            stack.enter_context(patch.object(main, "_migrate_auto_json_skills"))
            stack.enter_context(patch.object(main, "get_local_ip", return_value="127.0.0.1"))
            stack.enter_context(patch.object(main, "run_organize_loop", new=AsyncMock()))
            sync = stack.enter_context(patch.object(main.mcp_router, "full_sync_mcp_to_opencode"))
            for config, key in ((main.app_config.codex, "auto_start"), (main.app_config.rakazo, "enabled"),
                                (main.app_config.lark_bot, "enabled"), (main.app_config.sandbox, "enabled")):
                stack.enter_context(patch.object(config, key, False))
            context = main.lifespan(main.app)
            await asyncio.wait_for(context.__aenter__(), timeout=1)
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                self.assertEqual((await main.health_check())["status"], "healthy")
                self.assertFalse(client.connected)
                sync.assert_not_called()
                release.set()
                await asyncio.wait_for(connected.wait(), timeout=1)
                await asyncio.sleep(0)
                self.assertEqual(client.base_url, "http://127.0.0.1:11201")
                sync.assert_called_once()
            finally:
                await context.__aexit__(None, None, None)

    async def test_cancelling_startup_terminates_unadopted_process_and_closes_logs(self):
        ready_check = asyncio.Event()
        process = MagicMock()
        process.poll.return_value = None

        async def not_ready(_url):
            ready_check.set()
            await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            from config import settings

            stack.enter_context(patch.object(settings, "DATA_DIR", Path(directory)))
            stack.enter_context(patch.object(settings, "LOGS_DIR", Path(directory)))
            stack.enter_context(patch.object(installer, "_is_port_open", return_value=False))
            stack.enter_context(patch.object(installer, "_collect_opencode_commands", return_value=[["opencode.exe"]]))
            stack.enter_context(patch.object(installer, "_sync_external_provider_config"))
            stack.enter_context(patch.object(installer, "_is_opencode_http_ready", side_effect=not_ready))
            popen = stack.enter_context(patch.object(installer.subprocess, "Popen", return_value=process))
            task = asyncio.create_task(installer.start_opencode_server())
            await asyncio.wait_for(ready_check.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            process.terminate.assert_called_once()
            self.assertTrue(popen.call_args.kwargs["stdout"].closed)
            self.assertTrue(popen.call_args.kwargs["stderr"].closed)


if __name__ == "__main__":
    unittest.main()
