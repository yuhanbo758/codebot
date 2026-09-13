"""提示词隐私和预算回归：仅使用隔离数据库与模拟检索，不调用计费模型。"""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_DATA = tempfile.TemporaryDirectory(prefix="codebot-prompt-tests-")
os.environ["CODEBOT_DATA_DIR"] = _DATA.name

from api.routes import chat, logs
from core.opencode_ws import OpenCodeClient
from core.prompt_optimizer import build_memory_context


class PromptPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def test_memory_budget_and_cross_category_dedup(self):
        duplicate = "用户偏好精简答案"
        context = build_memory_context([
            ("事实", [duplicate, "甲" * 20000]),
            ("偏好", [duplicate, "乙" * 20000]),
            ("其他", [str(i) * 20000 for i in range(8)]),
        ])
        self.assertLessEqual(len(context), 4000)
        self.assertEqual(context.count(duplicate), 1)
        self.assertNotIn("甲" * 801, context)
        self.assertIn("不构成指令", context)
        self.assertEqual(build_memory_context([]), "")

    async def test_agent_prompt_preserves_request_and_bounds_retrieval(self):
        manager = SimpleNamespace(
            search_facts=AsyncMock(return_value=[{"content": "私人记忆" * 20000}]),
            search_memories=AsyncMock(return_value=[{"content": "私人记忆" * 20000, "category": "profile"}]),
        )
        request = "请检查 a.py 并修复问题，运行测试，保持原接口不变。"
        with patch.object(chat, "_get_chat_memory_manager", return_value=manager), patch.object(
            chat, "_extract_requested_skill", return_value=(None, request, False)
        ):
            system, user = await chat._build_opencode_prompt_parts(request, mode="agent")
        self.assertEqual(user, request)
        self.assertLess(len(system), 6000)
        self.assertNotIn("Agent 模式技能指导", system)
        self.assertIn("验证通过即结束", system)
        self.assertIn("执行契约", system)

    def test_explicit_agent_routing_and_role_separation(self):
        client = OpenCodeClient()
        for mode in ("agent", "editor", "build", "plan"):
            payload = client._build_prompt_payload("本轮用户请求", mode=mode, system="内部系统指令")
            self.assertEqual(payload["agent"], "plan" if mode == "plan" else "build")
            self.assertEqual(payload["system"], "内部系统指令")
            self.assertNotIn("内部系统指令", str(payload["parts"]))

    async def test_logs_hide_legacy_prompt_and_never_save_new_prompt(self):
        # 同时覆盖列表、按会话过滤、详情和写入；保留旧数据库原文以便用户自行管理。
        dbpath = Path(_DATA.name) / "privacy-test.db"
        conn = sqlite3.connect(dbpath)
        conn.executescript("""
            CREATE TABLE conversations (id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE chat_logs (
                id INTEGER PRIMARY KEY, conversation_id INTEGER, user_message TEXT,
                internal_prompt TEXT, tool_events TEXT, final_reply TEXT,
                model TEXT, mode TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO conversations VALUES (1, '测试');
            INSERT INTO chat_logs (conversation_id, user_message, internal_prompt, final_reply)
            VALUES (1, '问题', 'PRIVATE_SYSTEM_CANARY', '答案');
        """)
        def connect():
            handle = sqlite3.connect(dbpath)
            handle.row_factory = sqlite3.Row
            return handle
        with patch.object(logs, "_get_chat_conn", side_effect=connect):
            for response in (await logs.list_chat_logs(), await logs.list_chat_logs(conversation_id=1), await logs.get_chat_log(1)):
                self.assertNotIn("PRIVATE_SYSTEM_CANARY", str(response))
                self.assertIn("答案", str(response))
        fake_db = SimpleNamespace(connect=lambda: None, conn=conn)
        with patch.object(chat, "conversations_db", fake_db):
            chat._save_chat_log(1, "新问题", "NEW_PRIVATE_CANARY", [], "新答案")
        saved = conn.execute("SELECT internal_prompt FROM chat_logs ORDER BY id").fetchall()
        self.assertEqual(saved, [("PRIVATE_SYSTEM_CANARY",), ("",)])
        conn.close()


if __name__ == "__main__":
    unittest.main()
