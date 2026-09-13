"""项目目录必须贯穿 OpenCode 会话生命周期；HTTP 模拟不调用真实模型。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_DATA = tempfile.TemporaryDirectory(prefix="codebot-workspace-tests-")
os.environ.setdefault("CODEBOT_DATA_DIR", _DATA.name)
from core.opencode_ws import OpenCodeClient


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def check_execution(self, workspace, streaming, retry=False, fallback=False):
        """模拟按 directory 隔离项目的上游，防止只有创建请求携带目录。"""
        requests = []
        sessions = []
        prompts = []

        def handle(request):
            requests.append(request)
            path = request.url.path
            if path == "/global/event":
                # 全局事件本身不绑定目录，依靠 sessionID 路由。
                sid = "session2" if retry else "session1"
                events = [
                    {"type": "message.updated", "properties": {"info": {
                        "id": "user", "sessionID": sid, "role": "user"}}},
                    {"type": "message.part.updated", "properties": {"part": {
                        "id": "user-part", "messageID": "user", "sessionID": sid,
                        "type": "text", "text": "不应出现在助手回复中的用户原文"}}},
                    {"type": "message.part.delta", "properties": {
                        "messageID": "user", "sessionID": sid, "partID": "user-part",
                        "field": "text", "delta": "也不应回显"}},
                ]
                events += [] if fallback else [
                    {"type": "message.updated", "properties": {"info": {
                        "id": "answer", "sessionID": sid, "role": "assistant"}}},
                    {"type": "message.part.updated", "properties": {"part": {
                        "id": "part", "sessionID": sid, "messageID": "answer",
                        "type": "text", "text": "项目回复"}}},
                ]
                events.append({"type": "session.idle", "properties": {"sessionID": sid}})
                return httpx.Response(200, text="".join(
                    "data: " + json.dumps({"payload": event}) + "\n\n" for event in events))
            if request.url.params.get("directory") != workspace:
                return httpx.Response(404, json={"message": "project scope mismatch"})
            if path == "/session":
                sid = "session" + str(len(sessions) + 1)
                sessions.append(sid)
                return httpx.Response(200, json={"id": sid})
            if path.endswith("/prompt_async"):
                prompts.append(path)
                return httpx.Response(404 if retry and len(prompts) == 1 else 204)
            if path.endswith("/message"):
                self.assertEqual(request.url.params.get("limit"), "20")
                return httpx.Response(200, json=[{
                    "info": {"role": "assistant"},
                    "parts": [{"type": "text", "text": "项目回复"}],
                }])
            raise AssertionError(path)

        runtime = OpenCodeClient("http://test")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with (
                patch.object(runtime, "ensure_connected", new=AsyncMock()),
                patch.object(runtime, "_get_client", new=AsyncMock(return_value=client)),
                patch.object(runtime, "_validate_model_available_on_server", new=AsyncMock(return_value=None)),
            ):
                if streaming:
                    result = [event async for event in runtime.execute_task_stream(
                        "你好", workspace=workspace, timeout=2)]
                    self.assertFalse([event for event in result if event["type"] == "error"], result)
                    self.assertEqual(result[-1]["content"], "项目回复")
                else:
                    result = await runtime.execute_task("你好", workspace=workspace, timeout=2)
                    self.assertTrue(result.success, result.error)
                    self.assertEqual(result.content, "项目回复")
        self.assertEqual(len(sessions), 2 if retry else 1)

    async def test_project_stream(self):
        await self.check_execution(r"D:\项目 含空格\Codebot", True)

    async def test_project_stream_recovery(self):
        await self.check_execution(r"D:\项目 含空格\Codebot", True, retry=True)

    async def test_project_stream_message_fallback(self):
        await self.check_execution(r"D:\项目 含空格\Codebot", True, fallback=True)

    async def test_project_nonstream(self):
        await self.check_execution(r"D:\项目 含空格\Codebot", False)

    async def test_project_nonstream_recovery(self):
        await self.check_execution(r"D:\项目 含空格\Codebot", False, retry=True)

    async def test_no_project_still_works(self):
        await self.check_execution(None, True, fallback=True)
        await self.check_execution(None, False)

    async def test_abort_uses_project_scope(self):
        def handle(request):
            self.assertEqual(request.url.params.get("directory"), r"D:\项目")
            return httpx.Response(200, json=True)
        runtime = OpenCodeClient("http://test")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch.object(runtime, "_get_client", new=AsyncMock(return_value=client)):
                self.assertTrue(await runtime.abort_session("session", workspace=r"D:\项目"))
