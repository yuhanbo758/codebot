"""Codex Harness 迁移、线程、审批、事件和 MCP 边界回归测试。"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 配置模块在导入时会执行一次旧 Hermes → Codex 迁移并保存结果。测试必须先把
# 数据根目录隔离到临时目录，绝不能改写仓库内用户的 data/config.json。
_MODULE_DATA_DIR = tempfile.TemporaryDirectory(prefix="codebot-codex-tests-")
os.environ["CODEBOT_DATA_DIR"] = _MODULE_DATA_DIR.name

from api.routes.chat import _normalized_chat_target, _stream_codex_proxy_events  # noqa: E402
from config import app_config, migrate_legacy_agent_config, settings  # noqa: E402
from core import growth  # noqa: E402
from core.codex_runtime import (  # noqa: E402
    ActiveCodexTurn,
    CodexRuntime,
    PendingCodexRequest,
)
from core.codex_model_bridge import (  # noqa: E402
    BridgedResponsesResult,
    bridge_adapter,
    bridge_protocol_for_npm,
    bridge_protocol_metadata,
    anthropic_to_responses,
    build_anthropic_request,
    build_chat_completions_request,
    build_responses_proxy_request,
    chat_completion_to_responses,
    responses_proxy_to_result,
)
from core.prompt_optimizer import optimize_agent_prompt  # noqa: E402
from core.scheduler import TaskScheduler, normalize_task_executor  # noqa: E402
from main import app  # noqa: E402


class LegacyMigrationTests(unittest.TestCase):
    def test_config_target_and_executor_migration_is_idempotent(self):
        source = {
            "hermes": {
                "enabled": False,
                "auto_start": False,
                "cli_path": "hermes",
                "share_memory": False,
                "share_scheduler": True,
                "skill_dirs": ["D:/skills"],
                "repo_url": "https://example.invalid/ignored",
            }
        }

        migrated, changed = migrate_legacy_agent_config(source)
        self.assertTrue(changed)
        self.assertNotIn("hermes", migrated)
        self.assertEqual(migrated["codex"]["runtime_source"], "bundled")
        self.assertEqual(migrated["codex"]["skill_dirs"], ["D:/skills"])
        self.assertEqual(
            set(migrated["codex"]),
            {
                "enabled", "auto_start", "runtime_source", "codex_bin",
                "approval_policy", "share_memory", "share_scheduler", "skill_dirs",
            },
        )
        migrated_again, changed_again = migrate_legacy_agent_config(migrated)
        self.assertFalse(changed_again)
        self.assertEqual(migrated_again, migrated)
        self.assertEqual(_normalized_chat_target("hermes_obsidian"), "codex_obsidian")
        self.assertEqual(normalize_task_executor("hermes_cli"), "codex")

    def test_growth_candidate_executor_migrates_on_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "growth_candidates.json"
            path.write_text(
                json.dumps([{"id": "one", "payload": {"executor": "hermes", "target": "hermes_obsidian"}}]),
                encoding="utf-8",
            )
            with patch("core.growth._store_path", return_value=path):
                items = growth._load()
                second = growth._load()
            self.assertEqual(items[0]["payload"]["executor"], "codex")
            self.assertEqual(items[0]["payload"]["target"], "codex_obsidian")
            self.assertEqual(second, items)

    def test_scheduler_database_migrates_legacy_executor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "tasks.db")
            scheduler = TaskScheduler(db_path=db_path)
            task = scheduler.create_task("旧任务", "0 9 * * *", "生成日报")
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("UPDATE scheduled_tasks SET executor = 'hermes' WHERE id = ?", (task.id,))
                conn.commit()
            finally:
                conn.close()
            TaskScheduler(db_path=db_path)
            conn = sqlite3.connect(db_path)
            try:
                executor = conn.execute("SELECT executor FROM scheduled_tasks WHERE id = ?", (task.id,)).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(executor, "codex")


class _FakeNotificationClient:
    def __init__(self, notifications):
        self.notifications = list(notifications)
        self.unregistered = []

    def next_turn_notification(self, _turn_id):
        return self.notifications.pop(0)

    def unregister_turn_notifications(self, turn_id):
        self.unregistered.append(turn_id)

    def close(self):
        return None


class CodexRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db = settings.CONVERSATIONS_DB
        self.original_data_dir = settings.DATA_DIR
        settings.CONVERSATIONS_DB = Path(self.temp_dir.name) / "conversations.db"
        settings.DATA_DIR = Path(self.temp_dir.name) / "data"
        self.runtime = CodexRuntime()

    async def asyncTearDown(self):
        await self.runtime.close()
        settings.CONVERSATIONS_DB = self.original_db
        settings.DATA_DIR = self.original_data_dir
        self.temp_dir.cleanup()

    async def test_thread_start_resume_and_project_switch(self):
        calls = []

        async def fake_call(method, *args, **_kwargs):
            calls.append((method, args))
            thread_id = "thread-two" if len([item for item in calls if item[0] == "thread_start"]) > 1 else "thread-one"
            return SimpleNamespace(thread=SimpleNamespace(id=thread_id))

        self.runtime._call = fake_call
        first_workspace = Path(self.temp_dir.name) / "project-one"
        second_workspace = Path(self.temp_dir.name) / "project-two"
        first_workspace.mkdir()
        second_workspace.mkdir()

        first = await self.runtime._get_or_start_thread(
            7, str(first_workspace), model=None, model_provider="openai", model_config={}, mode="build",
            developer_instructions="", history_context="", interactive=True,
        )
        resumed = await self.runtime._get_or_start_thread(
            7, str(first_workspace), model=None, model_provider="openai", model_config={}, mode="build",
            developer_instructions="", history_context="", interactive=True,
        )
        switched = await self.runtime._get_or_start_thread(
            7, str(second_workspace), model=None, model_provider="openai", model_config={}, mode="build",
            developer_instructions="", history_context="", interactive=True,
        )

        self.assertEqual((first, resumed, switched), ("thread-one", "thread-one", "thread-two"))
        self.assertEqual([item[0] for item in calls], ["thread_start", "thread_resume", "thread_start"])

    async def test_opencode_responses_models_use_custom_provider_without_leaking_key(self):
        payload = {
            "connected": ["opencode-go"],
            "all": [{
                "id": "opencode-go",
                "name": "OpenCode Go",
                "env": ["OPENCODE_API_KEY"],
                "options": {},
                "models": {
                    "gpt-response": {
                        "name": "Responses Model",
                        "api": {
                            "id": "gpt-response",
                            "url": "https://provider.example/v1",
                            "npm": "@ai-sdk/openai",
                        },
                        "capabilities": {"reasoning": True, "toolcall": True},
                        "limit": {"context": 123456},
                        "variants": {"low": {}, "high": {}},
                    },
                    "deepseek-v4-flash": {
                        "name": "DeepSeek V4 Flash",
                        "api": {
                            "id": "deepseek-v4-flash",
                            "url": "https://provider.example/v1",
                            "npm": "@ai-sdk/openai-compatible",
                        },
                    },
                    "chat-only": {
                        "name": "Chat-only Model",
                        "api": {
                            "id": "chat-only",
                            "url": "https://chat-only.example/v1",
                            "npm": "@ai-sdk/openai-compatible",
                        },
                    },
                    "chat-alias": {
                        "name": "Chat-only Alias",
                        "api": {
                            "id": "chat-only",
                            "url": "https://chat-only.example/v1",
                            "npm": "@ai-sdk/openai-compatible",
                        },
                    },
                    "claude-compatible": {
                        "name": "Claude Compatible",
                        "api": {
                            "id": "claude-compatible",
                            "url": "https://anthropic.example/v1",
                            "npm": "@ai-sdk/anthropic",
                        },
                    },
                    "google-native": {
                        "name": "Google Native",
                        "api": {
                            "id": "gemini-test",
                            "url": "https://google.example/v1",
                            "npm": "@ai-sdk/google",
                        },
                    },
                },
            }],
        }
        routes, incompatible = self.runtime._parse_opencode_provider_catalog(
            payload,
            {"opencode-go": "super-secret-provider-key"},
            {},
        )
        self.runtime._opencode_models = routes
        self.runtime._opencode_incompatible = incompatible

        route = routes["opencode-go/gpt-response"]
        self.assertEqual(route.codex_model, "gpt-response")
        self.assertEqual(route.reasoning_efforts, ("low", "high"))
        self.assertNotIn("api_key", route.public_model())
        # OpenCode Go 网关要求稳定 x-opencode-session 会话头；公开指纹只记录头名。
        headers = dict(route.request_headers)
        self.assertIn("x-opencode-session", headers)
        self.assertIn("x-opencode-session", route.route_fingerprint_payload["headerNames"])
        # 所有桥接厂商直连请求都要带 Codebot 身份 UA，不暴露泛用 SDK httpx 特征；
        # 该修复覆盖 x-opencode-go 之外的全部 Provider（Chat/Anthropic/Responses）。
        for other_id in (
            "opencode-go/chat-only",
            "opencode-go/claude-compatible",
        ):
            other_headers = dict(routes[other_id].request_headers)
            self.assertEqual(
                other_headers.get("User-Agent"),
                f"Codebot/{app_config.version}",
            )
        self.assertIn("opencode-go/deepseek-v4-flash", routes)
        self.assertEqual(
            routes["opencode-go/deepseek-v4-flash"].upstream_protocol,
            "chat_completions",
        )
        self.assertEqual(routes["opencode-go/chat-only"].upstream_protocol, "chat_completions")
        self.assertEqual(routes["opencode-go/claude-compatible"].upstream_protocol, "anthropic")
        self.assertEqual(routes["opencode-go/chat-only"].adapter_package, "@ai-sdk/openai-compatible")
        self.assertNotIn("opencode-go/chat-only", incompatible)
        self.assertIn("opencode-go/google-native", incompatible)

        codex_model, provider, config = self.runtime._resolve_model_route("opencode-go/gpt-response")
        self.assertEqual(codex_model, "gpt-response")
        self.assertTrue(provider.startswith("codebot_opencode_go_"))
        self.assertEqual(config["model_context_window"], 123456)
        chat_model, chat_provider, _ = self.runtime._resolve_model_route("opencode-go/chat-only")
        self.assertEqual(chat_model, "chat-only")
        self.assertNotEqual(chat_provider, provider)
        self.assertEqual(
            self.runtime._bridge_route(chat_provider, chat_model).base_url,
            "https://chat-only.example/v1",
        )

        # 系统 pytest 环境不要求安装 runtime SDK；这里用同字段的轻量对象
        # 验证配置边界，真实 SDK/App Server 另由端到端测试覆盖。
        with patch.object(
            self.runtime,
            "_sdk_imports",
            return_value={"CodexConfig": lambda **kwargs: SimpleNamespace(**kwargs)},
        ):
            sdk_config = self.runtime._build_sdk_config()
        # 全部 opencode-go 模型走 Codebot 桥后，第三方 Key 不再进入 Codex 子进程环境。
        self.assertNotIn("super-secret-provider-key", sdk_config.env.values())
        self.assertNotIn("super-secret-provider-key", "\n".join(sdk_config.config_overrides))
        self.assertTrue(any("/api/codex/compat/" in item for item in sdk_config.config_overrides))

        status = self.runtime.status()
        counts = {
            item["protocol"]: item["models"]
            for item in status["openCodeProtocolCoverage"]
        }
        # opencode-go 全部模型现需注入 x-opencode-session，Responses 模型
        # 升级为 responses_proxy 桥接，由 Codebot 内存中补齐该 header。
        self.assertEqual(counts["responses_proxy"], 1)
        self.assertEqual(counts["responses"], 0)
        self.assertEqual(counts["chat_completions"], 3)
        self.assertEqual(counts["anthropic"], 1)

    async def test_bridge_protocol_registry_is_explicit_and_extensible(self):
        """不能把未知 SDK 猜成 Chat；新增协议必须先注册真实转换器。"""
        self.assertEqual(
            bridge_protocol_for_npm("@ai-sdk/openai-compatible"),
            "chat_completions",
        )
        self.assertEqual(bridge_protocol_for_npm("@ai-sdk/anthropic"), "anthropic")
        self.assertIsNone(bridge_protocol_for_npm("@ai-sdk/google"))
        self.assertEqual(bridge_adapter("anthropic").endpoint_suffix, "/messages")
        self.assertEqual(
            {item["protocol"] for item in bridge_protocol_metadata()},
            {"responses_proxy", "chat_completions", "anthropic"},
        )
        request, _ = build_responses_proxy_request({"model": "gpt-api", "stream": True})
        self.assertFalse(request["stream"])
        proxied = responses_proxy_to_result(
            {
                "id": "resp-upstream",
                "model": "gpt-api",
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "OK"}],
                }],
            },
            requested_model="gpt-api",
            tool_kinds={},
        )
        self.assertEqual(proxied.response["id"], "resp-upstream")

    async def test_opencode_api_key_and_loopback_models_are_not_hardcoded_out(self):
        """OpenAI API Key、Ollama/LM Studio 都应按元数据路由，而非按名称排除。"""
        payload = {
            "connected": ["openai", "lmstudio"],
            "all": [
                {
                    "id": "openai",
                    "env": ["OPENAI_API_KEY"],
                    "options": {"headers": {"X-Tenant": "tenant-one"}},
                    "models": {
                        "gpt-api": {
                            "api": {
                                "id": "gpt-api",
                                "url": "https://api.openai.com/v1",
                                "npm": "@ai-sdk/openai",
                            }
                        }
                    },
                },
                {
                    "id": "lmstudio",
                    "env": [],
                    "models": {
                        "local-model": {
                            "api": {
                                "id": "local-model",
                                "url": "http://127.0.0.1:1234/v1",
                                "npm": "@ai-sdk/openai-compatible",
                            }
                        }
                    },
                },
            ],
        }
        routes, _ = self.runtime._parse_opencode_provider_catalog(
            payload,
            {"openai": "sk-user-configured"},
            {},
        )
        self.assertIn("openai/gpt-api", routes)
        self.assertIn("lmstudio/local-model", routes)
        self.assertEqual(routes["openai/gpt-api"].upstream_protocol, "responses_proxy")
        self.assertTrue(routes["openai/gpt-api"].uses_bridge)

        oauth_only_routes, _ = self.runtime._parse_opencode_provider_catalog(payload, {}, {})
        self.assertNotIn("openai/gpt-api", oauth_only_routes)
        self.assertIn("lmstudio/local-model", oauth_only_routes)

    async def test_thread_start_and_resume_forward_explicit_model_provider(self):
        calls = []

        async def fake_call(method, *args, **_kwargs):
            calls.append((method, args))
            return SimpleNamespace(thread=SimpleNamespace(id="thread-provider"))

        self.runtime._call = fake_call
        workspace = Path(self.temp_dir.name) / "provider-project"
        workspace.mkdir()
        kwargs = {
            "model": "gpt-response",
            "model_provider": "codebot_opencode_go_test",
            "model_config": {"model_context_window": 100000},
            "mode": "build",
            "developer_instructions": "",
            "history_context": "",
            "interactive": True,
        }

        await self.runtime._get_or_start_thread(8, str(workspace), **kwargs)
        await self.runtime._get_or_start_thread(8, str(workspace), **kwargs)

        start_params = calls[0][1][0]
        resume_params = calls[1][1][1]
        self.assertEqual(start_params["modelProvider"], "codebot_opencode_go_test")
        self.assertEqual(resume_params["modelProvider"], "codebot_opencode_go_test")
        self.assertEqual(resume_params["config"]["model_context_window"], 100000)

    async def test_event_mapping_preserves_content_plan_tool_and_completion(self):
        notifications = [
            SimpleNamespace(method="item/reasoning/summaryTextDelta", payload={"delta": "分析中"}),
            SimpleNamespace(method="item/started", payload={"item": {"type": "commandExecution", "command": ["git", "status"], "status": "inProgress"}}),
            SimpleNamespace(method="item/agentMessage/delta", payload={"delta": "O"}),
            SimpleNamespace(method="item/agentMessage/delta", payload={"delta": "K"}),
            SimpleNamespace(method="turn/completed", payload={"turn": {"status": "completed"}}),
        ]
        self.runtime._client = _FakeNotificationClient(notifications)
        self.runtime._get_or_start_thread = AsyncMock(return_value="thread-one")
        self.runtime._call = AsyncMock(return_value=SimpleNamespace(turn=SimpleNamespace(id="turn-one")))

        events = [event async for event in self.runtime.run_turn_stream(message="test", conversation_id=9)]

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["content"], "OK")
        self.assertEqual(events[0]["event_type"], "model.route")
        self.assertTrue(any(event.get("event_type") == "item/reasoning/summaryTextDelta" for event in events))
        self.assertTrue(any(event.get("summary", "").startswith("Codex 命令") for event in events))

    async def test_retryable_error_notification_does_not_abort_turn(self):
        """App Server 声明会重试时，Codebot 必须继续读取到最终完成事件。"""
        notifications = [
            SimpleNamespace(
                method="error",
                payload={
                    "threadId": "thread-one",
                    "turnId": "turn-one",
                    "willRetry": True,
                    "error": {"message": "Reconnecting... 1/5"},
                },
            ),
            SimpleNamespace(method="item/agentMessage/delta", payload={"delta": "RECOVERED"}),
            SimpleNamespace(method="turn/completed", payload={"turn": {"status": "completed"}}),
        ]
        self.runtime._client = _FakeNotificationClient(notifications)
        self.runtime._get_or_start_thread = AsyncMock(return_value="thread-one")
        self.runtime._call = AsyncMock(return_value=SimpleNamespace(turn=SimpleNamespace(id="turn-one")))

        events = [event async for event in self.runtime.run_turn_stream(message="test", conversation_id=91)]

        self.assertTrue(any(event.get("summary") == "Reconnecting... 1/5" for event in events))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["content"], "RECOVERED")

    async def test_model_route_context_distinguishes_harness_from_selected_upstream(self):
        route = self.runtime._parse_opencode_provider_catalog(
            {
                "connected": ["deepseek"],
                "all": [{
                    "id": "deepseek",
                    "name": "DeepSeek",
                    "env": ["DEEPSEEK_API_KEY"],
                    "models": {
                        "deepseek-v4-flash": {
                            "name": "DeepSeek V4 Flash",
                            "api": {
                                "id": "deepseek-v4-flash",
                                "url": "https://api.deepseek.com",
                                "npm": "@ai-sdk/openai-compatible",
                            },
                        }
                    },
                }],
            },
            {"deepseek": "secret"},
            {},
        )[0]["deepseek/deepseek-v4-flash"]
        self.runtime._opencode_models = {route.display_id: route}

        instructions, data = self.runtime._model_route_context(
            route.display_id,
            route.codex_model,
            route.codex_provider,
        )

        self.assertEqual(data["requestedModel"], "deepseek/deepseek-v4-flash")
        self.assertEqual(data["transport"], "codebot-bridge")
        self.assertIn("不要仅因为运行在 Codex 中就推断底层一定是 OpenAI", instructions)

    async def test_chat_bridge_preserves_custom_tool_loop(self):
        payload = {
            "model": "deepseek-v4-flash",
            "instructions": "使用工具完成任务",
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "修复文件"}]}],
            "tools": [{"type": "custom", "name": "apply_patch", "description": "修改文件"}],
        }
        request, tool_kinds = build_chat_completions_request(payload)
        self.assertEqual(request["messages"][0]["role"], "system")
        self.assertEqual(request["tools"][0]["function"]["name"], "apply_patch")

        result = chat_completion_to_responses(
            {
                "id": "chat-one",
                "model": "deepseek-v4-flash",
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call-one",
                            "type": "function",
                            "function": {"name": "apply_patch", "arguments": '{"input":"*** Begin Patch"}'},
                        }],
                    }
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
            requested_model="deepseek-v4-flash",
            tool_kinds=tool_kinds,
        )
        self.assertEqual(result.response["output"][0]["type"], "custom_tool_call")
        self.assertEqual(result.response["output"][0]["input"], "*** Begin Patch")
        self.assertIn("event: response.output_item.done", result.as_sse())

    async def test_anthropic_bridge_preserves_custom_tool_loop(self):
        payload = {
            "model": "minimax-m3",
            "instructions": "使用工具完成任务",
            "input": [{"type": "message", "role": "user", "content": "读取目录"}],
            "tools": [{"type": "custom", "name": "shell", "description": "执行命令"}],
            "tool_choice": "required",
        }
        request, tool_kinds = build_anthropic_request(payload)
        self.assertEqual(request["tools"][0]["name"], "shell")
        self.assertEqual(request["tool_choice"], {"type": "any"})
        result = anthropic_to_responses(
            {
                "id": "anthropic-one",
                "model": "minimax-m3",
                "content": [{
                    "type": "tool_use",
                    "id": "call-shell",
                    "name": "shell",
                    "input": {"input": "Get-Location"},
                }],
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
            requested_model="minimax-m3",
            tool_kinds=tool_kinds,
        )
        self.assertEqual(result.response["output"][0]["type"], "custom_tool_call")
        self.assertEqual(result.response["output"][0]["input"], "Get-Location")

    async def test_compat_bridge_endpoint_requires_process_token_and_returns_sse(self):
        from core.codex_runtime import codex_runtime as shared_runtime

        result = BridgedResponsesResult({
            "id": "resp-test",
            "object": "response",
            "status": "completed",
            "model": "model-test",
            "output": [{
                "type": "message",
                "role": "assistant",
                "id": "msg-test",
                "content": [{"type": "output_text", "text": "OK"}],
            }],
            "usage": {"input_tokens": 1, "input_tokens_details": None, "output_tokens": 1, "output_tokens_details": None, "total_tokens": 2},
        })
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:15682") as client:
            denied = await client.post(
                "/api/codex/compat/provider-test/v1/responses",
                json={"model": "model-test", "stream": True},
            )
            self.assertEqual(denied.status_code, 401)
            with (
                patch.object(shared_runtime, "bridge_authorized", return_value=True),
                patch.object(shared_runtime, "bridge_responses", new=AsyncMock(return_value=result)),
            ):
                accepted = await client.post(
                    "/api/codex/compat/provider-test/v1/responses",
                    headers={"Authorization": "Bearer process-token"},
                    json={"model": "model-test", "stream": True},
                )
        self.assertEqual(accepted.status_code, 200)
        self.assertIn("response.completed", accepted.text)

    async def test_agent_prompt_optimization_is_complexity_gated(self):
        simple = optimize_agent_prompt("你好", "agent")
        complex_task = optimize_agent_prompt(
            "请检查当前项目的模型路由，同时修复兼容问题，并运行后端测试和前端构建，最后说明风险与未验证边界。",
            "agent",
        )
        self.assertFalse(simple.applied)
        self.assertTrue(complex_task.applied)
        self.assertIn("执行契约", complex_task.developer_contract)

    async def test_approval_timeout_and_deny_all_fail_closed(self):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        active = ActiveCodexTurn("11", "thread", "turn", loop, queue, True)
        self.runtime._active_by_turn["turn"] = active
        params = {"turnId": "turn", "command": ["dangerous"], "autoResolutionMs": 1}

        result_task = asyncio.create_task(asyncio.to_thread(
            self.runtime._handle_server_request,
            "item/commandExecution/requestApproval",
            params,
        ))
        event = await asyncio.wait_for(queue.get(), timeout=1)
        self.assertEqual(event["event_type"], "permission.asked")
        result = await asyncio.wait_for(result_task, timeout=2)
        self.assertEqual(result, {"decision": "decline"})

        original_policy = app_config.codex.approval_policy
        try:
            app_config.codex.approval_policy = "deny_all"
            immediate = self.runtime._handle_server_request(
                "item/fileChange/requestApproval",
                {"turnId": "turn"},
            )
            self.assertEqual(immediate, {"decision": "decline"})
        finally:
            app_config.codex.approval_policy = original_policy

    async def test_mcp_tool_approval_uses_elicitation_response_shape(self):
        pending = PendingCodexRequest(
            request_id="codex-mcp-approval",
            method="mcpServer/elicitation/request",
            params={
                "turnId": "turn",
                "serverName": "codebot",
                "requestedSchema": {"type": "object", "properties": {}},
                "_meta": {"codex_approval_kind": "mcp_tool_call", "persist": ["session"]},
            },
        )
        self.runtime._pending[pending.request_id] = pending

        self.assertTrue(self.runtime.reply_permission(pending.request_id, "always"))
        self.assertEqual(
            pending.result,
            {"action": "accept", "content": {}, "_meta": {"persist": "session"}},
        )

    async def test_generic_mcp_form_elicitation_returns_structured_content(self):
        pending = PendingCodexRequest(
            request_id="codex-mcp-form",
            method="mcpServer/elicitation/request",
            params={
                "turnId": "turn",
                "requestedSchema": {
                    "type": "object",
                    "properties": {"confirmation": {"type": "string"}},
                },
            },
        )
        self.runtime._pending[pending.request_id] = pending

        self.assertTrue(self.runtime.reply_question(pending.request_id, answer="confirmed"))
        self.assertEqual(
            pending.result,
            {"action": "accept", "content": {"confirmation": "confirmed"}},
        )

    async def test_close_rejects_pending_requests(self):
        pending = PendingCodexRequest(
            request_id="codex-test",
            method="item/fileChange/requestApproval",
            params={"turnId": "turn"},
        )
        self.runtime._pending[pending.request_id] = pending
        self.runtime._client = _FakeNotificationClient([])

        await self.runtime.close()

        self.assertTrue(pending.event.is_set())
        self.assertEqual(pending.result, {"decision": "decline"})

    async def test_codex_obsidian_uses_codebot_workspace_instead_of_selected_project(self):
        captured = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield {"type": "done", "content": "OK"}

        with (
            patch("api.routes.chat._build_opencode_prompt_parts", new=AsyncMock(return_value=("system", "user"))),
            patch("api.routes.chat._codex_selected_skills", return_value=[]),
            patch("api.routes.chat._codex_history_context", new=AsyncMock(return_value="")),
            patch("core.codex_runtime.codex_runtime.run_turn_stream", new=fake_stream),
        ):
            events = [
                event
                async for event in _stream_codex_proxy_events(
                    "test",
                    conversation_id="17",
                    project_dir="D:/should-not-be-workspace",
                    obsidian_enabled=True,
                )
            ]

        self.assertEqual(events[-1]["type"], "done")
        self.assertIsNone(captured["project_dir"])


class CodexMcpTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamable_http_auth_initialize_tools_and_task_call(self):
        original_token = app_config.security.lan_api_token
        app_config.security.lan_api_token = "codex-mcp-test-token"
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        try:
            with patch("api.routes.mcp._list_external_proxy_tool_definitions", new=AsyncMock(return_value=[])):
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:15682") as client:
                    unauthenticated = await client.post("/api/mcp/codebot/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
                    self.assertEqual(unauthenticated.status_code, 401)
                    headers = {"Authorization": "Bearer codex-mcp-test-token"}
                    initialized = await client.post(
                        "/api/mcp/codebot/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}},
                    )
                    self.assertEqual(initialized.status_code, 200)
                    self.assertEqual(initialized.json()["result"]["serverInfo"]["name"], "Codebot Third-Party MCP")
                    listed = await client.post(
                        "/api/mcp/codebot/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                    )
                    names = {item["name"] for item in listed.json()["result"]["tools"]}
                    self.assertIn("codebot_list_memories", names)
                    self.assertIn("codebot_create_task", names)
                    task_call = await client.post(
                        "/api/mcp/codebot/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                            "params": {"name": "codebot_list_tasks", "arguments": {}},
                        },
                    )
                    self.assertEqual(task_call.status_code, 200)
                    self.assertIn("result", task_call.json())
        finally:
            app_config.security.lan_api_token = original_token

    async def test_streamable_http_respects_memory_and_scheduler_share_switches(self):
        original_token = app_config.security.lan_api_token
        original_memory = app_config.codex.share_memory
        original_scheduler = app_config.codex.share_scheduler
        app_config.security.lan_api_token = "codex-mcp-test-token"
        app_config.codex.share_memory = False
        app_config.codex.share_scheduler = False
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        try:
            with patch("api.routes.mcp._list_external_proxy_tool_definitions", new=AsyncMock(return_value=[])):
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:15682") as client:
                    headers = {"Authorization": "Bearer codex-mcp-test-token"}
                    listed = await client.post(
                        "/api/mcp/codebot/mcp",
                        headers=headers,
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    )
                    names = {item["name"] for item in listed.json()["result"]["tools"]}
                    self.assertNotIn("codebot_list_memories", names)
                    self.assertNotIn("codebot_create_task", names)
                    self.assertIn("codebot_list_skills", names)

                    denied = await client.post(
                        "/api/mcp/codebot/mcp",
                        headers=headers,
                        json={
                            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "codebot_list_tasks", "arguments": {}},
                        },
                    )
                    self.assertIn("error", denied.json())
        finally:
            app_config.security.lan_api_token = original_token
            app_config.codex.share_memory = original_memory
            app_config.codex.share_scheduler = original_scheduler


if __name__ == "__main__":
    unittest.main()
