"""Aegis 适配与回复设置回归：隔离配置，模拟执行器，不调用计费模型。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_DATA = tempfile.TemporaryDirectory(prefix="codebot-agent-methods-tests-")
os.environ.setdefault("CODEBOT_DATA_DIR", _DATA.name)

from config import AgentConfig, AppConfig, app_config, settings
from api.routes import chat
from api.routes import config as config_routes
from core.opencode_ws import OpenCodeClient
from core.prompt_optimizer import build_reply_instructions, optimize_agent_prompt


class AgentMethodTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config_patch = patch.object(app_config, "agent", AgentConfig())
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.manager = SimpleNamespace(search_facts=AsyncMock(return_value=[]),
                                       search_memories=AsyncMock(return_value=[]))

    def test_workflows_and_fast_path(self):
        cases = [
            ("修复 a.py 报错", "debug"),
            ("Diagnose this Python bug without editing files", "debug"),
            ("审查这个修复报错的代码 diff", "review"),
            ("Review the API diff for regressions", "review"),
            ("为后端接口添加分页并验证兼容性", "coding"),
            ("Implement pagination in this repo", "coding"),
        ]
        for message, workflow in cases:
            with self.subTest(message=message):
                result = optimize_agent_prompt(message, "agent")
                self.assertTrue(result.applied)
                self.assertEqual(result.workflow, workflow)
                self.assertIn("项目规则优先", result.developer_contract)
                self.assertIn("审查、解释、诊断请求不自动授权改代码", result.developer_contract)
                self.assertLess(len(result.developer_contract), 1500)
        for message in ("你好", "1+1", "解释 API 是什么", "Python 是什么", "帮我写一首诗"):
            with self.subTest(message=message):
                self.assertFalse(optimize_agent_prompt(message, "agent").applied)
        for mode in (None, "build", "plan", "editor", "jevai"):
            self.assertFalse(optimize_agent_prompt("修复 a.py 报错并验证", mode).applied)

    def test_disabling_aegis_keeps_original_complex_task_contract(self):
        result = optimize_agent_prompt("检查 a.py、修复接口错误并运行测试，保持兼容", "agent", aegis_enabled=False)
        self.assertTrue(result.applied)
        self.assertEqual(result.workflow, "")
        self.assertNotIn("Aegis", result.developer_contract)
        self.assertIn("执行契约", result.developer_contract)
        self.assertFalse(optimize_agent_prompt("修复 a.py", "agent", aegis_enabled=False).applied)

    def test_non_coding_complex_work_does_not_load_coding_rules(self):
        result = optimize_agent_prompt("请分析活动策划方案，并且对比不同城市，然后验证预算，最后给出风险。", "agent")
        self.assertTrue(result.applied)
        self.assertEqual(result.workflow, "")
        self.assertNotIn("Aegis", result.developer_contract)

    def test_reply_preferences_preserve_precision_and_user_format(self):
        for style in ("concise", "balanced", "detailed"):
            instructions = build_reply_instructions(style)
            self.assertIn("用户本轮指定的语言、格式和详略优先", instructions)
            self.assertIn("不确定性", instructions)
            self.assertIn("没有执行的检查明确说明", instructions)
        self.assertNotEqual(build_reply_instructions("concise"), build_reply_instructions("detailed"))
        self.assertEqual(build_reply_instructions("off"), "")

    async def test_settings_api_validation_partial_update_and_disk_roundtrip(self):
        app = FastAPI()
        app.include_router(config_routes.router, prefix="/api/config")
        with tempfile.TemporaryDirectory() as root, patch.object(settings, "DATA_DIR", Path(root)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                initial = await client.get("/api/config/agent")
                self.assertEqual(initial.json()["data"], {"aegis_enabled": True, "reply_style": "balanced"})
                response = await client.patch("/api/config/agent", json={"aegis_enabled": False})
                self.assertEqual(response.json()["data"], {"aegis_enabled": False, "reply_style": "balanced"})
                response = await client.patch("/api/config/agent", json={"reply_style": "concise"})
                self.assertEqual(response.json()["data"], {"aegis_enabled": False, "reply_style": "concise"})
                persisted = json.loads((Path(root) / "config.json").read_text(encoding="utf-8"))
                self.assertEqual(AppConfig(**persisted).agent.model_dump(), response.json()["data"])
                invalid = await client.patch("/api/config/agent", json={"reply_style": "arbitrary"})
                self.assertEqual(invalid.status_code, 422)
                unchanged = await client.patch("/api/config/agent", json={"reply_style": None})
                self.assertEqual(unchanged.json()["data"], response.json()["data"])
        self.assertEqual(AppConfig().agent, AgentConfig())
        with self.assertRaises(ValidationError):
            AgentConfig(reply_style="arbitrary")

    async def test_opencode_http_receives_method_and_keeps_user_message(self):
        request_text = "修复 api.py 报错，保留原接口并验证。\n最后只输出 JSON。"
        reply = '{"result":"已修复","verified":false}'
        sent = []

        def handler(request):
            if request.url.path == "/session":
                return httpx.Response(200, json={"id": "agent-test"})
            if request.url.path.endswith("/prompt_async"):
                sent.append(json.loads(request.content))
                return httpx.Response(204)
            if request.url.path.endswith("/message"):
                return httpx.Response(200, json=[{"info": {"role": "assistant"}, "parts": [{"type": "text", "text": reply}]}])
            raise AssertionError(str(request.url))

        with patch.object(chat, "_get_chat_memory_manager", return_value=self.manager), patch.object(
            chat, "_extract_requested_skill", return_value=(None, request_text, False)
        ):
            system, user = await chat._build_opencode_prompt_parts(request_text, mode="agent")
        runtime = OpenCodeClient("http://test")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.object(runtime, "ensure_connected", new=AsyncMock()), patch.object(
                runtime, "_get_client", new=AsyncMock(return_value=client)
            ), patch.object(runtime, "_validate_model_available_on_server", new=AsyncMock(return_value=None)):
                result = await runtime.execute_task(user, system=system, mode="agent", timeout=2)
        self.assertTrue(result.success, result.error)
        self.assertEqual(result.content, reply)
        self.assertEqual(sent[0]["parts"], [{"type": "text", "text": request_text}])
        self.assertEqual(sent[0]["agent"], "build")
        self.assertIn("Aegis 编程方法", sent[0]["system"])
        self.assertIn("故障任务", sent[0]["system"])
        self.assertEqual(sent[0]["system"].count("【回复表达】"), 1)

    async def test_failed_save_does_not_change_active_preferences(self):
        with patch.object(config_routes, "save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                await config_routes.update_agent_config(config_routes.AgentConfigUpdateRequest(
                    aegis_enabled=False, reply_style="off"
                ))
        self.assertEqual(app_config.agent, AgentConfig())

    async def test_codex_stream_receives_contract_without_rewriting_answer(self):
        captured = {}
        request_text = "审查代码 diff，不要修改代码；只输出 JSON"
        reply = '{"findings":[]}'

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield {"type": "content_delta", "delta": reply, "content": reply}
            yield {"type": "done", "content": reply}

        with patch.object(chat, "_get_chat_memory_manager", return_value=self.manager), patch.object(
            chat, "_extract_requested_skill", return_value=(None, request_text, False)
        ), patch.object(chat, "_codex_history_context", new=AsyncMock(return_value="")), patch.object(
            chat, "_codex_selected_skills", return_value=[]
        ), patch("core.codex_runtime.codex_runtime.run_turn_stream", new=fake_stream):
            events = [event async for event in chat._stream_codex_proxy_events(
                request_text, conversation_id="987", mode="agent"
            )]
        self.assertEqual(captured["message"], request_text)
        self.assertIn("审查任务", captured["developer_instructions"])
        self.assertIn("用户本轮指定的语言、格式和详略优先", captured["developer_instructions"])
        self.assertEqual(events[-1]["content"], reply)
        self.assertFalse(any(event["type"] == "internal_prompt" for event in events))

    async def test_reply_changes_on_next_turn_and_plan_stays_read_only(self):
        message = "修复 a.py 报错"
        with patch.object(chat, "_get_chat_memory_manager", return_value=self.manager), patch.object(
            chat, "_extract_requested_skill", return_value=(None, message, False)
        ):
            app_config.agent.reply_style = "concise"
            first, _ = await chat._build_opencode_prompt_parts(message, mode="plan")
            self.assertIn("当前是规划模式", first)
            self.assertIn("少量短段", first)
            self.assertNotIn("Aegis", first)
            app_config.agent.reply_style = "off"
            app_config.agent.aegis_enabled = False
            second, user = await chat._build_opencode_prompt_parts(message, mode="agent")
            self.assertNotIn("【回复表达】", second)
            self.assertNotIn("Aegis", second)
            self.assertEqual(user, message)


if __name__ == "__main__":
    unittest.main()
