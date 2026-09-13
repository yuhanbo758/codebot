"""完全访问的 OpenCode HTTP 链路与配置边界回归；不调用真实模型。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_DATA = tempfile.TemporaryDirectory(prefix="codebot-access-tests-")
os.environ.setdefault("CODEBOT_DATA_DIR", _DATA.name)
from config import app_config, OpenCodeConfig, RakazoConfig
from core.opencode_ws import OpenCodeClient


class FullAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_config_defaults_and_opencode_roundtrip(self):
        from api.routes.config import OpenCodeAccessUpdateRequest, update_opencode_access
        self.assertFalse(OpenCodeConfig().full_access)
        self.assertFalse(RakazoConfig().full_access)
        with patch.object(app_config.opencode, "full_access", False), patch("api.routes.config.save_config"):
            result = await update_opencode_access(OpenCodeAccessUpdateRequest(full_access=True))
            self.assertTrue(result["data"]["full_access"])
            result = await update_opencode_access(OpenCodeAccessUpdateRequest(full_access=False))
            self.assertFalse(result["data"]["full_access"])

    async def test_stream_approves_without_persisting_rules_and_keeps_question(self):
        await self._check_stream("build", True)

    async def test_plan_does_not_override_or_auto_approve(self):
        await self._check_stream("plan", False)

    async def _check_stream(self, mode, should_allow):
        original = [{"permission": "bash", "pattern": "*", "action": "ask"}]
        patches, replies = [], []

        def handle(request):
            path = request.url.path
            if request.method == "PATCH":
                patches.append(json.loads(request.content)["permission"])
                return httpx.Response(200, json={})
            if path.endswith("/reply"):
                replies.append(json.loads(request.content))
                return httpx.Response(200, json=True)
            if path == "/global/event":
                events = [
                    {"type": "permission.asked", "properties": {"id": "perm", "sessionID": "session"}},
                    {"type": "question.asked", "properties": {"id": "question", "sessionID": "session"}},
                    {"type": "session.idle", "properties": {"sessionID": "session"}},
                ]
                return httpx.Response(200, text="".join("data: " + json.dumps({"payload": event}) + "\n\n" for event in events))
            if path.endswith("/message"):
                return httpx.Response(200, json=[])
            if path.endswith("/prompt_async"):
                return httpx.Response(204)
            return httpx.Response(200, json={"permission": original})

        runtime = OpenCodeClient("http://test")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with (
                patch.object(app_config.opencode, "full_access", True),
                patch.object(runtime, "ensure_connected", new=AsyncMock()),
                patch.object(runtime, "_get_client", new=AsyncMock(return_value=client)),
                patch.object(runtime, "_resolve_session_id", new=AsyncMock(return_value="session")),
                patch.object(runtime, "_validate_model_available_on_server", new=AsyncMock(return_value=None)),
            ):
                events = [event async for event in runtime.execute_task_stream("hello", mode=mode)]
        self.assertTrue(any(event.get("event_type") == "question.asked" for event in events))
        if should_allow:
            self.assertEqual(patches, [])
            self.assertEqual(replies, [{"reply": "once"}])
            self.assertFalse(any(event.get("event_type") == "permission.asked" for event in events))
        else:
            self.assertEqual(patches, [])
            self.assertEqual(replies, [])
