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
    async def test_codex_native_skill_omits_duplicate_body_and_keeps_fallback(self):
        # 同一技能用原生 input 传递时不得再注入正文；没有原生 input 时必须保留。
        skill_path = Path(_DATA.name) / "SKILL.md"
        body = "SKILL_BODY_CANARY\n" + "执行要求。" * 1000
        skill_path.write_text(body, encoding="utf-8")
        skill = {"id": "test", "slug": "test", "source": chat.BUILTIN,
                 "skill_md_path": str(skill_path), "skill_md_content": body}
        manager = SimpleNamespace(search_facts=AsyncMock(return_value=[]),
                                  search_memories=AsyncMock(return_value=[]))
        with patch.object(chat, "_get_chat_memory_manager", return_value=manager), patch.object(
            chat, "_extract_requested_skill", return_value=(skill, "执行任务", False)
        ):
            fallback, user = await chat._build_opencode_prompt_parts("@test 执行任务", mode="agent")
            native, native_user = await chat._build_opencode_prompt_parts(
                "@test 执行任务", mode="agent", target="codex", native_skill_paths=[str(skill_path)]
            )
        self.assertIn(body, fallback)
        self.assertNotIn("SKILL_BODY_CANARY", native)
        self.assertGreater(len(fallback) - len(native), len(body))
        self.assertEqual(user, native_user)

    async def test_codex_stream_delivers_native_skill_without_internal_prompt_event(self):
        captured = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield {"type": "done", "content": "完成"}

        skill_path = Path(_DATA.name) / "native-SKILL.md"
        skill_path.write_text("NATIVE_PRIVATE_CANARY", encoding="utf-8")
        skill = {"id": "native", "slug": "native", "source": chat.BUILTIN,
                 "skill_md_path": str(skill_path)}
        manager = SimpleNamespace(search_facts=AsyncMock(return_value=[]),
                                  search_memories=AsyncMock(return_value=[]))
        with patch.object(chat, "_get_chat_memory_manager", return_value=manager), patch.object(
            chat, "_extract_requested_skill", return_value=(skill, "执行任务", False)
        ), patch.object(chat, "_codex_history_context", new=AsyncMock(return_value="")), patch(
            "core.codex_runtime.codex_runtime.run_turn_stream", new=fake_stream
        ):
            events = [event async for event in chat._stream_codex_proxy_events(
                "@native 执行任务", conversation_id="987", mode="agent"
            )]
        self.assertEqual(captured["skills"], [{"name": "native", "path": str(skill_path)}])
        self.assertNotIn("NATIVE_PRIVATE_CANARY", captured["developer_instructions"])
        self.assertEqual(captured["message"], "执行任务")
        self.assertEqual(events, [{"type": "done", "content": "完成"}])

    async def test_obsidian_native_skill_and_opencode_marker_fallback(self):
        skill_path = Path(_DATA.name) / "obsidian-SKILL.md"
        skill_path.write_text("OBSIDIAN_BODY_CANARY", encoding="utf-8")
        skill = {"id": "obsidian", "slug": "obsidian", "source": chat.BUILTIN,
                 "skill_md_path": str(skill_path)}
        manager = SimpleNamespace(search_facts=AsyncMock(return_value=[]),
                                  search_memories=AsyncMock(return_value=[]))
        with patch.object(chat, "_get_chat_memory_manager", return_value=manager), patch.object(
            chat, "_extract_requested_skill", return_value=(None, "整理笔记", False)
        ), patch.object(chat, "_find_skill_prefer_non_opencode", return_value=skill), patch.object(
            chat, "_build_obsidian_context", return_value="知识库资料"
        ):
            native, _ = await chat._build_opencode_prompt_parts(
                "整理笔记", target="codex_obsidian", native_skill_paths=[str(skill_path)]
            )
            fallback, _ = await chat._build_opencode_prompt_parts("整理笔记", target="codex_obsidian")
        self.assertNotIn("OBSIDIAN_BODY_CANARY", native)
        self.assertIn("知识库资料", native)
        self.assertIn("OBSIDIAN_BODY_CANARY", fallback)
        # OpenCode 来源的 slash 指令也只在没有原生输入时保留。
        with patch.object(chat, "_get_chat_memory_manager", return_value=manager), patch.object(
            chat, "_extract_requested_skill", return_value=(skill, "整理笔记", True)
        ):
            _, native_user = await chat._build_opencode_prompt_parts(
                "@obsidian 整理笔记", native_skill_paths=[str(skill_path)]
            )
            _, fallback_user = await chat._build_opencode_prompt_parts("@obsidian 整理笔记")
        self.assertEqual(native_user, "整理笔记")
        self.assertTrue(fallback_user.startswith("/skill obsidian\n"))

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
