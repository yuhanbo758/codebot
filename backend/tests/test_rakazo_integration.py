"""Rakazo 原生集成的模型、权限、映射、MCP 与失败关闭回归测试。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 单独运行本文件时也必须在导入 config/rakazo 单例前隔离数据库。
_MODULE_DATA_DIR = tempfile.TemporaryDirectory(prefix="codebot-rakazo-tests-")
os.environ["CODEBOT_DATA_DIR"] = _MODULE_DATA_DIR.name

from config import app_config, settings  # noqa: E402
from core.codex_model_bridge import (  # noqa: E402
    bridge_chat_completions_request,
    bridge_chat_completions_stream,
)
from core.model_route_registry import ModelRoute, ModelRouteRegistry, model_route_registry  # noqa: E402
from core.rakazo_runtime import (  # noqa: E402
    RAKAZO_LOCAL_ADAPTER_SHARED_KEY,
    RAKAZO_LOCAL_CREDENTIAL_LABEL,
    RakazoRuntime,
    RakazoRuntimeError,
    _rpc_error_message,
)
from api.routes.chat import _build_handoff_summary  # noqa: E402
from main import app  # noqa: E402


def make_route(
    *,
    route_id: str = "provider/model-one",
    base_url: str = "https://provider.example/v1",
    api_key: str = "test-secret-key",
    protocol: str = "chat_completions",
    connected: bool = True,
    credential_mode: str = "api_key",
) -> ModelRoute:
    provider, model = route_id.split("/", 1)
    return ModelRoute(
        display_id=route_id,
        display_name="Model One",
        opencode_provider=provider,
        opencode_model=model,
        codex_provider="codebot_provider",
        codex_model="upstream-model-one",
        base_url=base_url,
        env_key="CODEBOT_TEST_KEY",
        api_key=api_key,
        upstream_protocol=protocol,
        adapter_package="@ai-sdk/openai-compatible",
        request_headers=(("X-Tenant", "private-tenant"),),
        query_params=(("api-version", "private-version"),),
        context_window=8192,
        max_output_tokens=1024,
        reasoning=True,
        reasoning_efforts=("low", "high"),
        credential_mode=credential_mode,
        compatibility_status="probe_required",
        connected=connected,
    )


class ModelRouteRegistryTests(unittest.TestCase):
    def test_full_catalog_matches_opencode_and_unknown_protocol_fails_closed(self):
        payload = {
            "connected": ["known", "oauth"],
            "all": [
                {
                    "id": "known",
                    "name": "Known",
                    "models": {
                        "chat": {
                            "name": "Known Chat",
                            "api": {
                                "id": "chat-upstream",
                                "url": "https://known.example/v1",
                                "npm": "@ai-sdk/openai-compatible",
                            },
                        },
                        "native": {
                            "name": "Unknown Native",
                            "api": {
                                "id": "native-upstream",
                                "url": "https://known.example/v1",
                                "npm": "@ai-sdk/google",
                            },
                        },
                    },
                },
                {
                    "id": "oauth",
                    "name": "OAuth only",
                    "models": {
                        "model": {
                            "api": {
                                "id": "model",
                                "url": "https://oauth.example/v1",
                                "npm": "@ai-sdk/openai-compatible",
                            },
                        },
                    },
                },
            ],
        }

        routes, incompatible, catalog = ModelRouteRegistry.parse_provider_catalog(
            payload,
            {"known": "super-secret-api-key"},
            {},
        )

        self.assertEqual(set(catalog), {"known/chat", "known/native", "oauth/model"})
        self.assertEqual(set(routes), {"known/chat"})
        self.assertEqual(catalog["known/native"].compatibility_status, "protocol_pending")
        self.assertEqual(catalog["oauth/model"].compatibility_status, "authorization_required")
        self.assertIn("known/native", incompatible)

    def test_public_route_and_fingerprint_never_contain_credentials(self):
        route = make_route()
        public = route.public_model(compatibility_status="verified")
        serialized = json.dumps(public, ensure_ascii=False)

        self.assertNotIn(route.api_key, route.route_fingerprint)
        self.assertNotIn("private-tenant", route.route_fingerprint)
        self.assertNotIn("private-version", route.route_fingerprint)
        self.assertNotIn(route.api_key, serialized)
        self.assertNotIn(route.base_url, serialized)
        self.assertEqual(public["compatibilityStatus"], "verified")

    def test_openai_oauth_models_reuse_the_opencode_connection_as_raw_responses(self):
        """OpenCode 已选的订阅模型应进入纯采样路由，不要求 Rakazo 再登录。"""
        payload = {
            "connected": ["openai"],
            "all": [{
                "id": "openai",
                "name": "OpenAI",
                "models": {
                    "gpt-5.6-sol": {
                        "name": "GPT-5.6 Sol",
                        "api": {"id": "gpt-5.6-sol", "url": "", "npm": "@ai-sdk/openai"},
                        "capabilities": {"reasoning": True},
                        "limit": {"context": 400000, "output": 128000},
                    },
                },
            }],
        }
        secret_access = "oauth-access-must-stay-private"
        routes, incompatible, catalog = ModelRouteRegistry.parse_provider_catalog(
            payload,
            {
                "openai": {
                    "type": "oauth",
                    "access": secret_access,
                    "refresh": "oauth-refresh-must-stay-private",
                    "expires": 9999999999999,
                    "accountId": "private-account-id",
                }
            },
            {},
        )

        route = catalog["openai/gpt-5.6-sol"]
        public = route.public_model(compatibility_status="probe_failed")
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertEqual(set(routes), {route.route_id})
        self.assertEqual(incompatible, {})
        self.assertEqual(route.base_url, "https://chatgpt.com/backend-api/codex")
        self.assertEqual(route.upstream_protocol, "responses_proxy")
        self.assertEqual(route.credential_mode, "opencode_oauth")
        self.assertNotIn(secret_access, serialized)
        self.assertNotIn("private-account-id", serialized)

    def test_handoff_preview_uses_only_visible_messages_and_redacts_secrets(self):
        secret = "sk-this-must-never-cross-executors"
        summary = _build_handoff_summary(
            {"executor": "opencode", "title": "Source", "project_dir": r"D:\project"},
            [
                {"role": "system", "content": "hidden system contract"},
                {"role": "user", "content": f"<system-reminder>private instructions</system-reminder> api_key={secret}"},
                {"role": "assistant", "content": "Visible result\n```text\n" + ("x" * 1200) + "\n```"},
                {"role": "tool", "content": "full private tool output"},
            ],
            "rakazo",
        )

        self.assertNotIn("hidden system contract", summary)
        self.assertNotIn("full private tool output", summary)
        self.assertNotIn("private instructions", summary)
        self.assertNotIn(secret, summary)
        self.assertIn("[REDACTED]", summary)
        self.assertIn("已省略长代码或工具输出", summary)


class RawSamplingBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_route_samples_once_without_fallback(self):
        route = make_route()
        upstream = {
            "id": "chat-one",
            "model": "upstream-model-one",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "MODEL_OK"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        with patch("core.codex_model_bridge._post_upstream", new=AsyncMock(return_value=upstream)) as post:
            result = await bridge_chat_completions_request(
                route,
                {"model": route.route_id, "messages": [{"role": "user", "content": "hello"}]},
            )

        self.assertEqual(post.await_count, 1)
        url, headers, query, body = post.await_args.args
        self.assertEqual(url, "https://provider.example/v1/chat/completions")
        self.assertEqual(body["model"], "upstream-model-one")
        self.assertEqual(headers["Authorization"], "Bearer test-secret-key")
        self.assertEqual(query["api-version"], "private-version")
        self.assertEqual(result.response["model"], route.route_id)
        self.assertEqual(result.response["codebot_route"]["actualModel"], "upstream-model-one")
        self.assertIn("data: [DONE]", result.as_sse())

    async def test_opencode_oauth_uses_codex_responses_contract_without_leaking_token(self):
        """订阅连接沿用 OpenCode 合同，但 OAuth 材料只能停留在上游 header。"""
        route = make_route(
            base_url="https://chatgpt.com/backend-api/codex",
            protocol="responses_proxy",
            credential_mode="opencode_oauth",
        )
        upstream = {
            "id": "resp-oauth-one",
            "model": "upstream-model-one",
            "status": "completed",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "OAUTH_OK"}],
            }],
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        }
        with patch("core.codex_model_bridge._post_upstream", new=AsyncMock(return_value=upstream)) as post:
            result = await bridge_chat_completions_request(
                route,
                {
                    "model": route.route_id,
                    "messages": [{"role": "user", "content": "hello"}],
                    "max_tokens": 64,
                },
            )

        url, headers, _query, body = post.await_args.args
        self.assertEqual(url, "https://chatgpt.com/backend-api/codex/responses")
        self.assertEqual(headers["Authorization"], "Bearer test-secret-key")
        self.assertTrue(headers.get("session-id"))
        self.assertFalse(body["store"])
        self.assertNotIn("max_output_tokens", body)
        self.assertTrue(body.get("instructions"))
        self.assertNotIn(route.api_key, json.dumps(result.response, ensure_ascii=False))

    async def test_mismatched_requested_model_is_rejected_before_sampling(self):
        runtime = object.__new__(RakazoRuntime)
        route = make_route()
        with patch.object(model_route_registry, "get", return_value=route):
            with patch("core.rakazo_runtime.bridge_chat_completions_request", new=AsyncMock()) as bridge:
                with self.assertRaisesRegex(RakazoRuntimeError, "拒绝静默换模"):
                    await runtime.sample(
                        route.route_id,
                        {"model": "provider/different", "messages": []},
                        require_verified=False,
                    )
        bridge.assert_not_awaited()

    async def test_chat_completions_uses_native_upstream_sse_and_preserves_identity(self):
        route = make_route()

        async def upstream(*_args, **_kwargs):
            yield "", {
                "id": "chat-stream-one",
                "model": "upstream-model-one",
                "choices": [{"index": 0, "delta": {"content": "原生"}, "finish_reason": None}],
            }
            yield "", {
                "id": "chat-stream-one",
                "model": "upstream-model-one",
                "choices": [{"index": 0, "delta": {"content": "流"}, "finish_reason": None}],
            }
            yield "", {
                "id": "chat-stream-one",
                "model": "upstream-model-one",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            }
            yield "", "[DONE]"

        with patch("core.codex_model_bridge._stream_upstream_sse", new=upstream):
            chunks = [item async for item in bridge_chat_completions_stream(
                route,
                {"model": route.route_id, "messages": [{"role": "user", "content": "hello"}]},
            )]

        serialized = "".join(chunks)
        self.assertIn("原生", serialized)
        self.assertIn("流", serialized)
        self.assertIn('"actualModel": "upstream-model-one"', serialized)
        self.assertIn(f'"model": "{route.route_id}"', serialized)
        self.assertTrue(serialized.endswith("data: [DONE]\n\n"))

    async def test_responses_sse_is_translated_incrementally(self):
        route = make_route(protocol="responses")

        async def upstream(*_args, **_kwargs):
            yield "response.created", {
                "type": "response.created",
                "response": {"id": "resp-stream", "model": "upstream-model-one", "created_at": 1},
            }
            yield "response.output_text.delta", {"type": "response.output_text.delta", "delta": "R"}
            yield "response.completed", {
                "type": "response.completed",
                "response": {
                    "id": "resp-stream",
                    "model": "upstream-model-one",
                    "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
                },
            }
            yield "", "[DONE]"

        with patch("core.codex_model_bridge._stream_upstream_sse", new=upstream):
            serialized = "".join([item async for item in bridge_chat_completions_stream(
                route,
                {"model": route.route_id, "messages": [{"role": "user", "content": "hello"}]},
            )])

        self.assertIn('"content": "R"', serialized)
        self.assertIn('"finish_reason": "stop"', serialized)
        self.assertIn('"total_tokens": 4', serialized)
        self.assertTrue(serialized.endswith("data: [DONE]\n\n"))

    async def test_responses_incomplete_is_a_length_terminal_not_a_protocol_error(self):
        route = make_route(protocol="responses")

        async def upstream(*_args, **_kwargs):
            yield "response.created", {
                "type": "response.created",
                "response": {"id": "resp-incomplete", "model": "upstream-model-one", "created_at": 1},
            }
            yield "response.reasoning_text.delta", {
                "type": "response.reasoning_text.delta",
                "delta": "thinking",
            }
            yield "response.incomplete", {
                "type": "response.incomplete",
                "response": {
                    "id": "resp-incomplete",
                    "model": "upstream-model-one",
                    "status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 3, "output_tokens": 64, "total_tokens": 67},
                },
            }

        with patch("core.codex_model_bridge._stream_upstream_sse", new=upstream):
            serialized = "".join([item async for item in bridge_chat_completions_stream(
                route,
                {"model": route.route_id, "messages": [{"role": "user", "content": "hello"}]},
            )])

        self.assertIn('"reasoning_content": "thinking"', serialized)
        self.assertIn('"finish_reason": "length"', serialized)
        self.assertTrue(serialized.endswith("data: [DONE]\n\n"))

    async def test_anthropic_sse_preserves_incremental_tool_arguments(self):
        route = make_route(protocol="anthropic")

        async def upstream(*_args, **_kwargs):
            yield "message_start", {
                "type": "message_start",
                "message": {"id": "msg-stream", "model": "upstream-model-one", "usage": {"input_tokens": 4}},
            }
            yield "content_block_start", {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tool-one", "name": "echo_probe", "input": {}},
            }
            yield "content_block_delta", {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"value":"probe"}'},
            }
            yield "message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 5},
            }
            yield "message_stop", {"type": "message_stop"}

        with patch("core.codex_model_bridge._stream_upstream_sse", new=upstream):
            serialized = "".join([item async for item in bridge_chat_completions_stream(
                route,
                {"model": route.route_id, "messages": [{"role": "user", "content": "call tool"}]},
            )])

        self.assertIn('"name": "echo_probe"', serialized)
        self.assertIn('\\"value\\":\\"probe\\"', serialized)
        self.assertIn('"finish_reason": "tool_calls"', serialized)
        self.assertIn('"total_tokens": 9', serialized)


class RakazoRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="codebot-rakazo-runtime-")
        self.original_data_dir = settings.DATA_DIR
        self.original_rakazo_db = settings.RAKAZO_DB
        self.original_read = app_config.rakazo.default_project_read
        self.original_write = app_config.rakazo.default_project_write
        self.original_command = app_config.rakazo.default_command_execution
        self.original_network = app_config.rakazo.default_external_network
        self.original_channel = app_config.rakazo.release_channel
        self.original_experimental_enabled = app_config.rakazo.experimental_runtime_enabled
        self.original_compose = app_config.rakazo.compose_file
        self.original_enabled = app_config.rakazo.enabled
        settings.DATA_DIR = Path(self.temp_dir.name) / "data"
        settings.RAKAZO_DB = settings.DATA_DIR / "rakazo.db"
        app_config.rakazo.default_project_read = True
        app_config.rakazo.default_project_write = False
        app_config.rakazo.default_command_execution = False
        app_config.rakazo.default_external_network = False
        app_config.rakazo.release_channel = "stable"
        app_config.rakazo.experimental_runtime_enabled = False
        app_config.rakazo.compose_file = ""
        self.runtime = RakazoRuntime()
        self.project = Path(self.temp_dir.name) / "project"
        self.project.mkdir()
        (self.project / "README.md").write_text("hello", encoding="utf-8")
        self.route = make_route()
        self.project_token = "project-token-with-enough-entropy"
        self.mapping = self.runtime.save_mapping(
            project_dir=str(self.project),
            conversation_id=41,
            bot={
                "id": "bot-one",
                "threadId": "thread-one",
                "computer": {"id": "computer-one"},
                "_codebotMcpServerId": "mcp-one",
                "_codebotProjectTokenHash": self.runtime._token_digest(self.project_token),
            },
            route=self.route,
            runtime_version="v0.1.0-beta",
        )

    async def asyncTearDown(self):
        settings.DATA_DIR = self.original_data_dir
        settings.RAKAZO_DB = self.original_rakazo_db
        app_config.rakazo.default_project_read = self.original_read
        app_config.rakazo.default_project_write = self.original_write
        app_config.rakazo.default_command_execution = self.original_command
        app_config.rakazo.default_external_network = self.original_network
        app_config.rakazo.release_channel = self.original_channel
        app_config.rakazo.experimental_runtime_enabled = self.original_experimental_enabled
        app_config.rakazo.compose_file = self.original_compose
        app_config.rakazo.enabled = self.original_enabled
        self.temp_dir.cleanup()

    def enable_fixed_experimental_runtime(self):
        """把随包官方 Compose 复制到测试数据目录并显式启用实验通道。"""
        entry = self.runtime.compatibility_entry(
            "0.1.0",
            channel="experimental",
            revision="a4ebad0cae4f9d0d3f6e7b3c30316b2bf6d924db",
        )
        self.assertIsNotNone(entry)
        source = self.runtime._rakazo_resource_path(entry["composeResource"])
        destination = self.runtime._managed_compose_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        app_config.rakazo.release_channel = "experimental"
        app_config.rakazo.experimental_runtime_enabled = True
        app_config.rakazo.compose_file = str(destination)
        return entry

    async def test_project_identity_is_unique_and_public_mapping_hides_token_hash(self):
        public = self.runtime.public_mapping(self.mapping)
        self.assertNotIn("project_token_hash", public)
        self.assertTrue(self.runtime.validate_project_token(self.mapping["project_id"], self.project_token))
        self.assertFalse(self.runtime.validate_project_token(self.mapping["project_id"], "wrong-token"))

        with self.assertRaisesRegex(RakazoRuntimeError, "拒绝覆盖"):
            self.runtime.save_mapping(
                project_dir=str(self.project),
                conversation_id=42,
                bot={"id": "bot-two", "threadId": "thread-two"},
                route=self.route,
            )

    async def test_running_managed_adapter_is_detected_without_starting_containers(self):
        """启动阶段只能检查已有容器，不能把用户主动停止的运行时重新拉起。"""
        completed = subprocess.CompletedProcess(
            args=["docker", "ps"],
            returncode=0,
            stdout="adapter-container-id\n",
            stderr="",
        )
        with (
            patch.object(self.runtime, "_is_managed_compose", return_value=True),
            patch.object(self.runtime, "_docker_executable", return_value="docker.exe"),
            patch.object(self.runtime, "_run_process", new=AsyncMock(return_value=completed)) as run,
        ):
            self.assertTrue(await self.runtime.managed_runtime_is_running())
        command = run.await_args.args[0]
        self.assertIn("label=com.docker.compose.service=model-adapter", command)
        self.assertNotIn("start", command)

        stopped = subprocess.CompletedProcess(
            args=["docker", "ps"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with (
            patch.object(self.runtime, "_is_managed_compose", return_value=True),
            patch.object(self.runtime, "_docker_executable", return_value="docker.exe"),
            patch.object(self.runtime, "_run_process", new=AsyncMock(return_value=stopped)),
        ):
            self.assertFalse(await self.runtime.managed_runtime_is_running())

    def test_nested_orpc_error_keeps_the_real_contract_message(self):
        payload = {
            "error": {
                "json": {
                    "message": "Connect that model provider first",
                    "data": {"token": "must-not-be-returned"},
                }
            }
        }
        self.assertEqual(
            _rpc_error_message(payload, 400),
            "Connect that model provider first",
        )

    async def test_local_provider_connection_migrates_legacy_key_and_then_reuses_contract(self):
        """旧随机占位值必须轮换为 adapter 协议键，随后不能重复写 Secret。"""
        rpc = AsyncMock(side_effect=[[], {"provider": "local", "id": "credential-one"}])
        with patch.object(self.runtime, "rpc", new=rpc):
            await self.runtime._ensure_local_provider_connection(self.route.route_id)

        self.assertEqual(rpc.await_args_list[0].args, ("models.credentials", {}))
        procedure, payload = rpc.await_args_list[1].args
        self.assertEqual(procedure, "models.connect")
        self.assertEqual(payload["provider"], "local")
        self.assertEqual(payload["modelId"], self.route.route_id)
        self.assertEqual(payload["apiKey"], RAKAZO_LOCAL_ADAPTER_SHARED_KEY)
        self.assertEqual(payload["label"], RAKAZO_LOCAL_CREDENTIAL_LABEL)
        self.assertNotEqual(payload["apiKey"], self.route.api_key)

        legacy = AsyncMock(side_effect=[
            [{"provider": "local", "id": "credential-one", "label": "Codebot private model bridge"}],
            {"provider": "local", "id": "credential-one"},
        ])
        with patch.object(self.runtime, "rpc", new=legacy):
            await self.runtime._ensure_local_provider_connection(self.route.route_id)
        self.assertEqual(legacy.await_count, 2)
        self.assertEqual(legacy.await_args_list[1].args[1]["apiKey"], RAKAZO_LOCAL_ADAPTER_SHARED_KEY)

        existing = AsyncMock(return_value=[{
            "provider": "local",
            "id": "credential-one",
            "label": RAKAZO_LOCAL_CREDENTIAL_LABEL,
        }])
        with patch.object(self.runtime, "rpc", new=existing):
            await self.runtime._ensure_local_provider_connection(self.route.route_id)
        existing.assert_awaited_once_with("models.credentials", {})

    async def test_disabled_rakazo_startup_never_probes_or_starts_docker_runtime(self):
        """默认关闭时，应用启动不能探测 Docker Compose、更不能启动或安装环境。"""
        from api.routes import rakazo as rakazo_routes

        original_enabled = app_config.rakazo.enabled
        app_config.rakazo.enabled = False
        try:
            with (
                patch.object(
                    rakazo_routes.rakazo_runtime,
                    "managed_runtime_is_running",
                    new=AsyncMock(),
                ) as managed,
                patch.object(
                    rakazo_routes.rakazo_runtime,
                    "control_runtime",
                    new=AsyncMock(),
                ) as control,
            ):
                await rakazo_routes.prepare_if_enabled()
            managed.assert_not_awaited()
            control.assert_not_awaited()
        finally:
            app_config.rakazo.enabled = original_enabled

    def test_new_project_initial_model_is_a_preference_not_a_permanent_lock(self):
        catalog = [
            {"id": "provider/unprobed", "selectable": True, "compatibilityStatus": "probe_failed"},
            {"id": "provider/verified", "selectable": True, "compatibilityStatus": "verified"},
        ]
        with patch.object(self.runtime, "models", return_value=catalog):
            self.assertEqual(
                self.runtime.select_initial_route("provider/unprobed"),
                "provider/unprobed",
            )
            self.assertEqual(self.runtime.select_initial_route(), "provider/verified")

    async def test_bot_creation_connects_local_provider_before_model_update(self):
        """回归用户实测的 bots.update 400：连接记录必须先于 Bot 创建和绑定。"""
        events = []

        async def ensure_provider(route_id):
            events.append(("provider", route_id))

        async def rpc(procedure, payload):
            events.append((procedure, dict(payload)))
            if procedure == "mcp.servers.list":
                return []
            if procedure == "bots.create":
                return {"id": "created-bot", "threadId": "created-thread"}
            if procedure == "bots.update":
                return {
                    "id": "created-bot",
                    "threadId": "created-thread",
                    "modelProvider": "local",
                    "modelId": self.route.route_id,
                }
            if procedure == "computer.boot":
                return {"id": "created-computer", "state": "running"}
            if procedure == "mcp.servers.create":
                return {"id": "created-mcp"}
            if procedure == "mcp.assignments.approve":
                return {"ok": True}
            raise AssertionError(f"unexpected procedure: {procedure}")

        new_project = Path(self.temp_dir.name) / "new-project"
        new_project.mkdir()
        app_config.rakazo.enabled = True
        entry = {"rakazoVersion": "v0.1.0", "sourceRevision": "a" * 40}
        with (
            patch.object(self.runtime, "health", new=AsyncMock(return_value={"version": "0.1.0"})),
            patch.object(self.runtime, "assert_runtime_compatible", return_value={"compatible": True}),
            patch.object(self.runtime, "compatibility_entry", return_value=entry),
            patch.object(self.runtime, "verify_running_runtime_identity", new=AsyncMock()),
            patch.object(self.runtime, "ensure_route_ready", new=AsyncMock(return_value=self.route)),
            patch.object(self.runtime, "_ensure_local_provider_connection", new=ensure_provider),
            patch.object(self.runtime, "rpc", new=rpc),
        ):
            bot, route = await self.runtime.create_remote_bot(str(new_project), self.route.route_id)

        self.assertEqual(route.route_id, self.route.route_id)
        self.assertEqual(bot["modelProvider"], "local")
        self.assertEqual(bot["modelId"], self.route.route_id)
        self.assertEqual(
            [item[0] for item in events[:4]],
            ["provider", "mcp.servers.list", "bots.create", "bots.update"],
        )

    async def test_model_switch_updates_remote_first_and_keeps_turn_history_independent(self):
        new_route = make_route(route_id="provider/model-two")
        remote_bot = {
            "id": self.mapping["bot_id"],
            "modelProvider": "local",
            "modelId": self.route.route_id,
        }
        with (
            patch.object(
                self.runtime,
                "_ensure_remote_project",
                new=AsyncMock(return_value=(self.mapping, remote_bot)),
            ),
            patch.object(self.runtime, "ensure_route_ready", new=AsyncMock(return_value=new_route)),
            patch.object(self.runtime, "_ensure_local_provider_connection", new=AsyncMock()) as ensure_provider,
            patch.object(
                self.runtime,
                "rpc",
                new=AsyncMock(return_value={
                    "id": self.mapping["bot_id"],
                    "modelProvider": "local",
                    "modelId": new_route.route_id,
                }),
            ) as rpc,
        ):
            updated = await self.runtime.set_project_model(self.mapping["project_id"], new_route.route_id)

        ensure_provider.assert_awaited_once_with(new_route.route_id)
        rpc.assert_awaited_once_with(
            "bots.update",
            {"botId": self.mapping["bot_id"], "modelProvider": "local", "modelId": new_route.route_id},
        )
        self.assertEqual(updated["model_route_id"], new_route.route_id)
        # 旧轮次路由表与项目当前模型是独立表；切模不能重写已有历史快照。
        with self.runtime._connect() as conn:
            turn_count = conn.execute("SELECT COUNT(*) FROM rakazo_turn_routes").fetchone()[0]
        self.assertEqual(turn_count, 0)

    async def test_existing_conversation_repairs_local_provider_before_sending(self):
        """已有主会话首次再发送时必须先迁移旧 adapter Bearer，不能继续返回 401。"""
        ensure_provider = AsyncMock()
        failed_rpc = AsyncMock(side_effect=RakazoRuntimeError("stop after migration"))
        with (
            patch.object(self.runtime, "verified_route", return_value=self.route),
            patch.object(
                self.runtime,
                "_ensure_remote_project",
                new=AsyncMock(return_value=(self.mapping, {"id": self.mapping["bot_id"]})),
            ),
            patch.object(self.runtime, "_ensure_local_provider_connection", new=ensure_provider),
            patch.object(self.runtime, "rpc", new=failed_rpc),
        ):
            stream = self.runtime.run_turn_stream(conversation_id=41, message="hello")
            with self.assertRaisesRegex(RakazoRuntimeError, "stop after migration"):
                await anext(stream)
        ensure_provider.assert_awaited_once_with(self.route.route_id)
        failed_rpc.assert_awaited_once_with("bots.get", {"botId": self.mapping["bot_id"]})

    async def test_project_tools_enforce_permission_path_and_sensitive_file_boundaries(self):
        project_id = self.mapping["project_id"]
        read = self.runtime.call_project_tool(project_id, "codebot_project_read", {"path": "README.md"})
        self.assertIn("hello", read["content"][0]["text"])

        with self.assertRaisesRegex(RakazoRuntimeError, "写入权限未开启"):
            self.runtime.call_project_tool(project_id, "codebot_project_write", {"path": "note.md", "content": "no"})
        with patch.object(
            self.runtime,
            "_ensure_remote_project",
            new=AsyncMock(return_value=(self.mapping, {"id": self.mapping["bot_id"]})),
        ):
            await self.runtime.update_permissions(project_id, {"projectWrite": True})
        self.runtime.call_project_tool(project_id, "codebot_project_write", {"path": "note.md", "content": "written"})
        self.assertEqual((self.project / "note.md").read_text(encoding="utf-8"), "written")

        for unsafe_path in ("../outside.txt", ".env", "folder/private.key"):
            with self.subTest(path=unsafe_path):
                with self.assertRaises(RakazoRuntimeError):
                    self.runtime.resolve_project_path(project_id, unsafe_path, for_write=True)

    async def test_project_permission_listing_is_editable_without_exposing_token_hash(self):
        """设置页应读取真实逐项目权限，但绝不能把 MCP 鉴权哈希发给前端。"""
        project_id = self.mapping["project_id"]
        with patch.object(
            self.runtime,
            "_ensure_remote_project",
            new=AsyncMock(return_value=(self.mapping, {"id": self.mapping["bot_id"]})),
        ):
            await self.runtime.update_permissions(project_id, {"projectRead": False, "projectWrite": True})
        rows = self.runtime.list_project_permissions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"]["project_id"], project_id)
        self.assertNotIn("project_token_hash", rows[0]["project"])
        self.assertFalse(rows[0]["permissions"]["projectRead"])
        self.assertTrue(rows[0]["permissions"]["projectWrite"])

    async def test_command_and_network_permissions_apply_real_runtime_controls(self):
        project_id = self.mapping["project_id"]
        ensure_remote = AsyncMock(return_value=(self.mapping, {"id": self.mapping["bot_id"]}))
        sync_shell = AsyncMock()
        network = AsyncMock()
        with (
            patch.object(self.runtime, "_ensure_remote_project", new=ensure_remote),
            patch.object(self.runtime, "_is_managed_compose", return_value=True),
            patch.object(self.runtime, "_sync_shell_approval_rule", new=sync_shell),
            patch.object(self.runtime, "_reconfigure_project_network", new=network),
        ):
            saved = await self.runtime.update_permissions(
                project_id,
                {"commandExecution": True, "externalNetwork": True, "networkPolicy": "allow"},
            )
        self.assertTrue(saved["commandExecution"])
        self.assertTrue(saved["externalNetwork"])
        self.assertEqual(saved["commandMode"], "allowed")
        self.assertEqual(saved["externalNetworkMode"], "allowed")
        sync_shell.assert_awaited_once_with(project_id=project_id, command_allowed=True)
        network.assert_awaited_once_with(
            project_id=project_id,
            bot_id=self.mapping["bot_id"],
            allow_external=True,
        )

    async def test_failed_retry_mcp_cleanup_only_removes_exact_codebot_slug(self):
        """自愈重试只能清理本项目确定性 slug，不能触碰用户的其他 MCP。"""
        project_id = self.mapping["project_id"]
        exact_slug = self.runtime._project_mcp_slug(project_id)

        async def rpc_handler(procedure, payload):
            if procedure == "mcp.servers.list":
                return [
                    {"id": "orphan-one", "slug": exact_slug},
                    {"id": "user-server", "slug": f"{exact_slug}-manual"},
                    {"id": "other-project", "slug": "codebot-project-other"},
                ]
            if procedure == "mcp.servers.remove":
                return {"ok": True}
            raise AssertionError(f"unexpected procedure: {procedure}")

        rpc = AsyncMock(side_effect=rpc_handler)
        with patch.object(self.runtime, "rpc", new=rpc):
            removed = await self.runtime._remove_project_mcp_by_slug(project_id)

        self.assertEqual(removed, ["orphan-one"])
        self.assertEqual(
            rpc.await_args_list[1].args,
            ("mcp.servers.remove", {"id": "orphan-one"}),
        )
        self.assertEqual(rpc.await_count, 2)

    async def test_missing_remote_bot_is_reconciled_once_without_replacing_codebot_conversation(self):
        """只有明确 Resource not found 才能替换远端身份，并保留原会话与权限。"""
        project_id = self.mapping["project_id"]
        new_bot = {
            "id": "bot-repaired",
            "threadId": "thread-repaired",
            "computer": {"id": "computer-repaired"},
            "_codebotMcpServerId": "mcp-repaired",
            "_codebotProjectTokenHash": self.runtime._token_digest("replacement-project-token"),
            "modelProvider": "local",
            "modelId": self.route.route_id,
        }

        async def rpc(procedure, payload):
            if procedure == "bots.get":
                raise RakazoRuntimeError("Rakazo bots.get 失败：Resource not found", status_code=500)
            if procedure == "mcp.servers.remove":
                return {"ok": True}
            if procedure == "computer.stop":
                return {"ok": True}
            if procedure == "computer.boot":
                return {"id": "computer-repaired", "state": "running"}
            raise AssertionError(f"unexpected procedure: {procedure}")

        fake_manager = MagicMock()
        fake_manager.bind_conversation_runtime = AsyncMock()

        with (
            patch.object(self.runtime, "rpc", new=AsyncMock(side_effect=rpc)),
            patch.object(self.runtime, "_remove_project_computer_runtime", new=AsyncMock(return_value={})),
            patch.object(self.runtime, "create_remote_bot", new=AsyncMock(return_value=(new_bot, self.route))),
            patch.object(self.runtime, "_project_home_operation", new=AsyncMock(return_value=True)) as home,
            patch.object(self.runtime, "health", new=AsyncMock(return_value={"version": "0.1.0"})),
            patch("core.memory_manager.MemoryManager", return_value=fake_manager),
        ):
            repaired, bot = await self.runtime._ensure_remote_project(project_id)

        self.assertEqual(repaired["conversation_id"], 41)
        self.assertEqual(repaired["bot_id"], "bot-repaired")
        self.assertEqual(repaired["thread_id"], "thread-repaired")
        self.assertEqual(bot["id"], "bot-repaired")
        self.assertEqual(bot["computer"]["state"], "running")
        self.assertEqual(home.await_args_list[0].args, ("copy", "bot-one", "bot-repaired"))
        self.assertEqual(home.await_args_list[1].args, ("delete", "bot-one"))
        with self.runtime._connect() as conn:
            retired = conn.execute(
                "SELECT COUNT(*) FROM rakazo_retired_bots WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        self.assertEqual(retired, 0)

    async def test_project_delete_cleans_remote_docker_home_and_local_mapping_but_keeps_project_files(self):
        """删除主会话只清理项目专属资源，绝不删除宿主项目目录或共享运行时。"""
        project_id = self.mapping["project_id"]
        with self.runtime._connect() as conn:
            conn.execute(
                "INSERT INTO rakazo_retired_bots(project_id, bot_id, created_at) VALUES (?, ?, ?)",
                (project_id, "bot-retired", "2026-09-01T00:00:00+00:00"),
            )
        async def rpc_handler(procedure, payload):
            if procedure == "mcp.servers.list":
                return []
            return {"ok": True}

        rpc = AsyncMock(side_effect=rpc_handler)
        remove_runtime = AsyncMock(return_value={"computerContainers": [], "computerNetwork": ""})
        home = AsyncMock(return_value=True)
        with (
            patch.object(self.runtime, "rpc", new=rpc),
            patch.object(self.runtime, "_remove_project_computer_runtime", new=remove_runtime),
            patch.object(self.runtime, "_project_home_operation", new=home),
            patch.object(self.runtime, "_sync_shell_approval_rule", new=AsyncMock()),
        ):
            result = await self.runtime.delete_project(project_id)

        self.assertIsNone(self.runtime.mapping_by_project_id(project_id))
        self.assertTrue((self.project / "README.md").is_file())
        self.assertEqual(result["botIds"], ["bot-one", "bot-retired"])
        self.assertTrue(result["retained"]["dockerDesktop"])
        self.assertTrue(result["retained"]["sharedRakazoRuntime"])
        self.assertEqual(
            rpc.await_args_list[0].args,
            ("mcp.servers.remove", {"id": "mcp-one"}),
        )
        self.assertEqual(
            rpc.await_args_list[2].args,
            ("bots.remove", {"botId": "bot-one", "deleteMemories": True}),
        )
        self.assertEqual(remove_runtime.await_count, 2)
        self.assertEqual(home.await_count, 2)

    async def test_audit_recursively_redacts_nested_secrets(self):
        project_id = self.mapping["project_id"]
        self.runtime.audit(
            project_id,
            "redaction.test",
            "success",
            {
                "metadata": {
                    "headers": {"Authorization": "Bearer token-value"},
                    "note": "api_key=plain-secret Cookie: abc",
                }
            },
        )
        item = self.runtime.recent_audit(project_id, 1)[0]
        serialized = json.dumps(item["detail"], ensure_ascii=False)
        self.assertNotIn("token-value", serialized)
        self.assertNotIn("plain-secret", serialized)
        self.assertNotIn("Cookie: abc", serialized)
        self.assertIn("[REDACTED]", serialized)

    async def test_desktop_authorization_creates_random_local_account_and_keeps_token_in_memory(self):
        with patch.object(self.runtime, "_email_auth", new=AsyncMock(return_value="session-token-with-entropy")) as auth:
            with patch.object(self.runtime, "rpc", new=AsyncMock(return_value={"ok": True})) as rpc:
                result = await self.runtime.bootstrap_desktop_authorization(mode="create")

        submitted = auth.await_args.args[1]
        self.assertEqual(auth.await_args.args[0], "sign-up/email")
        self.assertTrue(str(submitted["email"]).endswith("@localhost.invalid"))
        self.assertGreaterEqual(len(str(submitted["password"])), 32)
        self.assertEqual(result["secretBundle"]["sessionToken"], "session-token-with-entropy")
        self.assertEqual(self.runtime._read_session_token(), "session-token-with-entropy")
        rpc.assert_awaited_once_with("health", {})

    async def test_desktop_authorization_restores_valid_encrypted_session_without_password_login(self):
        bundle = {
            "email": "codebot@localhost.invalid",
            "password": "random-password-with-entropy",
            "name": "Codebot Local",
            "sessionToken": "existing-session-token-with-entropy",
        }
        with patch.object(self.runtime, "_email_auth", new=AsyncMock()) as auth:
            with patch.object(self.runtime, "rpc", new=AsyncMock(return_value={"ok": True})):
                result = await self.runtime.bootstrap_desktop_authorization(mode="restore", secret_bundle=bundle)
        auth.assert_not_awaited()
        self.assertTrue(result["health"]["ok"])
        self.assertEqual(self.runtime._read_session_token(), bundle["sessionToken"])

    async def test_official_ask_block_maps_to_one_time_question_and_exact_answer_contract(self):
        event = await self.runtime._register_pending_input(
            mapping=self.mapping,
            run_id="run-ask",
            message_id="message-ask",
            block={"kind": "ask", "text": "Which environment?", "status": "pending"},
        )
        duplicate = await self.runtime._register_pending_input(
            mapping=self.mapping,
            run_id="run-ask",
            message_id="message-ask",
            block={"kind": "ask", "text": "Which environment?", "status": "pending"},
        )

        self.assertEqual(event["event_type"], "question.asked")
        self.assertTrue(event["requires_user_action"])
        self.assertTrue(event["request_id"].startswith("rakazo-"))
        self.assertEqual(duplicate["request_id"], event["request_id"])
        self.assertNotIn("run-ask", event["request_id"])
        with patch.object(self.runtime, "rpc", new=AsyncMock(return_value={"ok": True})) as rpc:
            result = await self.runtime.reply_question(
                event["request_id"],
                answer="staging",
                conversation_id=41,
            )
        rpc.assert_awaited_once_with(
            "threads.answer",
            {
                "botId": "bot-one",
                "runId": "run-ask",
                "messageId": "message-ask",
                "answer": "staging",
            },
        )
        self.assertEqual(result["reply"], "staging")
        with self.assertRaisesRegex(RakazoRuntimeError, "已过期"):
            await self.runtime.reply_question(event["request_id"], answer="again", conversation_id=41)

    async def test_rakazo_approval_maps_codebot_labels_to_upstream_action_ids(self):
        event = await self.runtime._register_pending_input(
            mapping=self.mapping,
            run_id="run-approval",
            message_id="message-approval",
            block={
                "kind": "ask",
                "text": "Review before writing",
                "approvalEffectId": "effect-one",
                "status": "pending",
                "actions": [
                    {"id": "allow", "label": "Allow once"},
                    {"id": "always", "label": "Always allow"},
                    {"id": "deny", "label": "Deny"},
                ],
            },
        )
        self.assertEqual(event["event_type"], "permission.requested")
        self.assertEqual([item["reply"] for item in event["actions"]], ["once", "always", "reject"])
        with patch.object(self.runtime, "rpc", new=AsyncMock(return_value={"ok": True})) as rpc:
            await self.runtime.reply_permission(event["request_id"], "once", conversation_id=41)
        self.assertEqual(rpc.await_args.args[0], "threads.answer")
        self.assertEqual(rpc.await_args.args[1]["answer"], "allow")

    async def test_secret_answer_is_never_persisted_and_wrong_conversation_is_rejected(self):
        secret = "private-value-that-must-not-be-logged"
        event = await self.runtime._register_pending_input(
            mapping=self.mapping,
            run_id="run-secret",
            message_id="message-secret",
            block={"kind": "ask", "text": "API token", "input": "secret", "status": "pending"},
        )
        self.assertEqual(event["questions"][0]["input_type"], "password")
        with patch.object(self.runtime, "rpc", new=AsyncMock(return_value={"ok": True})) as rpc:
            with self.assertRaisesRegex(RakazoRuntimeError, "不属于当前对话"):
                await self.runtime.reply_question(event["request_id"], answer=secret, conversation_id=999)
            rpc.assert_not_awaited()
            result = await self.runtime.reply_question(event["request_id"], answer=secret, conversation_id=41)
        self.assertTrue(result["secret"])
        self.assertEqual(result["reply"], "")
        serialized_audit = json.dumps(self.runtime.recent_audit(self.mapping["project_id"], 10), ensure_ascii=False)
        self.assertNotIn(secret, serialized_audit)

    async def test_rejecting_plain_ask_stops_run_instead_of_forging_empty_answer(self):
        event = await self.runtime._register_pending_input(
            mapping=self.mapping,
            run_id="run-cancel",
            message_id="message-cancel",
            block={"kind": "ask", "text": "Continue?", "status": "pending"},
        )
        with patch.object(self.runtime, "rpc", new=AsyncMock(return_value={"ok": True})) as rpc:
            result = await self.runtime.reply_question(
                event["request_id"],
                reject=True,
                conversation_id=41,
            )
        rpc.assert_awaited_once_with("threads.stop", {"botId": "bot-one"})
        self.assertTrue(result["rejected"])

    async def test_probe_is_invalidated_when_route_fingerprint_changes(self):
        now = "2026-08-30T00:00:00+00:00"
        checks = {"text": True}
        with self.runtime._connect() as conn:
            conn.execute(
                """INSERT INTO rakazo_model_probes
                   (route_id, route_fingerprint, status, checks_json, actual_model, error_summary, last_probe_at)
                   VALUES (?, ?, 'verified', ?, ?, '', ?)""",
                (self.route.route_id, self.route.route_fingerprint, json.dumps(checks), self.route.codex_model, now),
            )
        with patch.object(model_route_registry, "catalog", return_value={self.route.route_id: self.route}):
            current = self.runtime.models()[0]
        self.assertEqual(current["compatibilityStatus"], "verified")

        changed = make_route(base_url="https://changed.example/v1")
        with patch.object(model_route_registry, "catalog", return_value={changed.route_id: changed}):
            invalidated = self.runtime.models()[0]
        self.assertEqual(invalidated["compatibilityStatus"], "probe_failed")
        self.assertEqual(invalidated["probeState"], "unprobed")
        self.assertTrue(invalidated["selectable"])
        self.assertIn("首次选择", invalidated["incompatibilityReason"])

    async def test_first_selection_automatically_probes_once(self):
        """聊天选择 OpenCode 模型时自动探测，不再要求用户先去设置页逐个点。"""
        with patch.object(model_route_registry, "ensure_fresh_credentials", return_value=self.route):
            with patch.object(
                self.runtime,
                "verified_route",
                side_effect=[
                    RakazoRuntimeError("尚未探测", status_code=409),
                    RakazoRuntimeError("尚未探测", status_code=409),
                    self.route,
                ],
            ) as verified:
                with patch.object(
                    self.runtime,
                    "probe_model",
                    new=AsyncMock(return_value={"status": "verified", "errorSummary": ""}),
                ) as probe:
                    route = await self.runtime.ensure_route_ready(self.route.route_id)

        self.assertEqual(route.route_id, self.route.route_id)
        probe.assert_awaited_once_with(self.route.route_id)
        self.assertEqual(verified.call_count, 3)

    async def test_models_only_include_models_enabled_in_current_opencode_server(self):
        """未连接 Provider 的静态模型不能再污染 Rakazo 兼容性列表。"""
        enabled = make_route(route_id="enabled/model", connected=True)
        disabled = make_route(route_id="disabled/model", connected=False)
        with patch.object(
            model_route_registry,
            "catalog",
            return_value={enabled.route_id: enabled, disabled.route_id: disabled},
        ):
            models = self.runtime.models()

        self.assertEqual([item["id"] for item in models], [enabled.route_id])

    async def test_incomplete_stream_never_passes_native_stream_probe(self):
        async def fake_sample(_route_id, payload, **_kwargs):
            messages = payload.get("messages") or []
            if payload.get("tools"):
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call-probe",
                        "type": "function",
                        "function": {"name": "echo_probe", "arguments": '{"value":"probe"}'},
                    }],
                }
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "probe-ok"}
                finish = "stop"
            return type("ProbeResult", (), {
                "response": {
                    "choices": [{"message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                    "codebot_route": {"actualModel": self.route.codex_model},
                }
            })()

        async def fake_incomplete_stream(_route_id, _payload, **_kwargs):
            # 有正文和 [DONE] 仍不够：缺少上游模型身份与 finish_reason 必须失败。
            yield 'data: {"choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            yield "data: [DONE]\n\n"

        with patch.object(model_route_registry, "get", return_value=self.route):
            with patch.object(self.runtime, "sample", side_effect=fake_sample):
                with patch.object(self.runtime, "sample_stream", side_effect=fake_incomplete_stream):
                    result = await self.runtime.probe_model(self.route.route_id)
        self.assertEqual(result["status"], "probe_failed")
        self.assertFalse(result["checks"]["stream"])
        self.assertIn("正文/推理增量、结束原因、模型身份", result["errorSummary"])

    async def test_runtime_rejects_manually_edited_remote_api_url(self):
        original_api_url = app_config.rakazo.api_url
        try:
            app_config.rakazo.api_url = "https://example.com/rakazo"
            with self.assertRaisesRegex(RakazoRuntimeError, "本机回环"):
                self.runtime._rpc_url("health")
        finally:
            app_config.rakazo.api_url = original_api_url

    async def test_stop_does_not_depend_on_opencode_catalog_and_status_hides_sampling_token(self):
        with patch.object(model_route_registry, "catalog") as catalog:
            with patch.object(
                self.runtime,
                "_validate_compose_for_entry",
                return_value={
                    "appImage": "ghcr.io/elie222/rakazo/app@sha256:" + ("a" * 64),
                    "computerImage": "ghcr.io/elie222/rakazo/computer@sha256:" + ("b" * 64),
                },
            ):
                environment = self.runtime._compose_environment("stop")
        catalog.assert_not_called()
        self.assertEqual(environment["CODEBOT_RAKAZO_MODEL_IDS"], "codebot-stop-placeholder")

        with patch.object(self.runtime, "docker_status", new=AsyncMock(return_value={"ready": True})):
            with patch.object(self.runtime, "health", new=AsyncMock(return_value={"ok": True, "version": "test"})):
                status = await self.runtime.status()
        self.assertNotIn(self.runtime.sampling_token, json.dumps(status, ensure_ascii=False))

    async def test_published_beta_is_listed_but_runtime_handshake_fails_closed(self):
        entry = self.runtime.compatibility_entry("0.1.0")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["sourceRelease"], "v0.1.0-beta")
        self.assertFalse(entry["runtimeCompatible"])
        self.assertFalse(entry["installable"])
        with self.assertRaisesRegex(RakazoRuntimeError, "不在 Codebot 当前通道"):
            self.runtime.assert_runtime_compatible({"ok": True, "version": "0.1.0"})
        with self.assertRaisesRegex(RakazoRuntimeError, "不在 Codebot 当前通道"):
            self.runtime.assert_runtime_compatible({"ok": True, "version": "9.9.9"})

        policy = self.runtime.runtime_start_policy()
        self.assertFalse(policy["allowed"])
        self.assertEqual(policy["approvedVersions"], [])
        with patch.object(self.runtime, "_run_process", new=AsyncMock()) as run_process:
            with self.assertRaisesRegex(RakazoRuntimeError, "启动门禁未通过"):
                await self.runtime.control_runtime("start")
        run_process.assert_not_awaited()

    async def test_fixed_experimental_revision_is_installable_but_not_production_ready(self):
        entry = self.enable_fixed_experimental_runtime()
        policy = self.runtime.runtime_start_policy()
        self.assertTrue(policy["allowed"])
        self.assertEqual(policy["channel"], "experimental")
        self.assertEqual(policy["approvedRevisions"], [entry["sourceRevision"]])
        compatibility = self.runtime.assert_runtime_compatible({
            "ok": True,
            "revision": entry["sourceRevision"],
        })
        self.assertTrue(compatibility["compatible"])
        self.assertFalse(compatibility["productionReady"])
        self.assertIn("@sha256:", compatibility["appImage"])
        with self.assertRaisesRegex(RakazoRuntimeError, "提交不匹配"):
            self.runtime.assert_runtime_compatible({"ok": True, "version": "0.1.0", "revision": "0" * 40})

    async def test_prepare_experimental_runtime_copies_verified_compose_and_encrypts_secrets(self):
        with patch.object(
            self.runtime,
            "docker_status",
            new=AsyncMock(return_value={"ready": True, "platform": "linux/amd64"}),
        ):
            prepared = await self.runtime.prepare_experimental_runtime()
        compose = Path(prepared["composeFile"])
        entry = self.runtime.compatibility_entry("0.1.0", channel="experimental", revision=prepared["sourceRevision"])
        self.assertTrue(compose.is_file())
        self.assertEqual(self.runtime._sha256_file(compose), entry["composeSha256"])
        self.assertFalse((compose.parent / ".env").exists())
        secret_store = self.runtime._runtime_secret_store_path()
        self.assertTrue(secret_store.is_file())
        if os.name == "nt":
            self.assertTrue(secret_store.read_bytes().startswith(b"DPAPI1\n"))
        self.assertEqual(app_config.rakazo.release_channel, "experimental")
        self.assertTrue(app_config.rakazo.experimental_runtime_enabled)

    async def test_compose_digest_tolerates_crlf_from_windows_checkout(self):
        """Windows CI autocrlf 检出会把随包 Compose 转成 CRLF；摘要必须仍能对齐。"""
        entry = next(
            item for item in self.runtime._compatibility_entries()
            if item.get("channel") == "experimental" and item.get("composeSha256")
        )
        source = self.runtime._rakazo_resource_path(str(entry["composeResource"]))
        crlf_copy = Path(source.parent) / f".crlf-{source.name}.tmp"
        try:
            crlf_copy.write_bytes(source.read_bytes().replace(b"\n", b"\r\n"))
            self.assertEqual(self.runtime._sha256_file(crlf_copy), entry["composeSha256"])
        finally:
            crlf_copy.unlink(missing_ok=True)

    async def test_compose_exposes_all_selectable_opencode_models_and_restart_recreates_services(self):
        self.enable_fixed_experimental_runtime()
        catalog = [
            {"id": "provider/verified", "compatibilityStatus": "verified", "selectable": True},
            {"id": "provider/first-use", "compatibilityStatus": "probe_failed", "selectable": True},
            {"id": "provider/pending", "compatibilityStatus": "protocol_pending", "selectable": False},
        ]
        with patch.object(self.runtime, "models", return_value=catalog):
            environment = self.runtime._compose_environment("start")
        self.assertEqual(
            environment["CODEBOT_RAKAZO_MODEL_IDS"],
            "provider/first-use,provider/verified",
        )
        self.assertEqual(
            environment["CODEBOT_RAKAZO_ADAPTER_SHARED_KEY"],
            RAKAZO_LOCAL_ADAPTER_SHARED_KEY,
        )
        adapter_context = Path(environment["CODEBOT_RAKAZO_ADAPTER_CONTEXT"])
        self.assertTrue((adapter_context / "Dockerfile").is_file())
        self.assertNotIn("provider/pending", environment["CODEBOT_RAKAZO_MODEL_IDS"])
        self.assertIn("@sha256:", environment["CODEBOT_RAKAZO_APP_IMAGE"])
        self.assertIn("@sha256:", environment["CODEBOT_RAKAZO_COMPUTER_IMAGE"])
        self.assertNotIn(":edge", environment["CODEBOT_RAKAZO_APP_IMAGE"])

        compose_file = Path(self.temp_dir.name) / "docker-compose.images.yml"
        compose_file.write_text("services: {}\n", encoding="utf-8")
        original_compose = app_config.rakazo.compose_file
        try:
            app_config.rakazo.compose_file = str(compose_file)
            with patch.object(self.runtime, "_docker_executable", return_value="docker"):
                argv = self.runtime._compose_argv("restart")
        finally:
            app_config.rakazo.compose_file = original_compose
        self.assertIn("--force-recreate", argv)
        self.assertNotIn("restart", argv)

        overlay_text = self.runtime._compose_overlay_path().read_text(encoding="utf-8")
        # 只按两空格缩进的顶层服务标题切分，不能把 depends_on 中更深缩进的
        # ``api:`` / ``worker:`` 误当作服务边界。
        api_sidecar = overlay_text.split("\n  project-mcp-api:\n", 1)[1].split("\n  project-mcp-worker:\n", 1)[0]
        worker_sidecar = overlay_text.split("\n  project-mcp-worker:\n", 1)[1].split("\n  api:\n", 1)[0]
        api_service = overlay_text.split("\n  api:\n", 1)[1].split("\n  worker:\n", 1)[0]
        self.assertNotIn("extra_hosts:", api_sidecar)
        self.assertNotIn("extra_hosts:", worker_sidecar)
        self.assertIn("extra_hosts:", api_service)
        self.assertIn("corepack-init:", overlay_text)
        self.assertIn("corepack-cache:/corepack", overlay_text)

    async def test_compose_retry_only_accepts_transient_registry_failures(self):
        transient = subprocess.CompletedProcess(
            ["docker", "compose", "up"],
            1,
            "",
            "failed to fetch anonymous token from ghcr.io: EOF",
        )
        deterministic = subprocess.CompletedProcess(
            ["docker", "compose", "up"],
            1,
            "",
            "services.api.environment must be a mapping",
        )
        corepack = subprocess.CompletedProcess(
            ["docker", "compose", "up"],
            1,
            "",
            "dependency failed to start: container codebot-rakazo-corepack-init-1 exited (1)",
        )
        self.assertTrue(self.runtime._is_transient_compose_network_failure(transient))
        self.assertTrue(self.runtime._is_transient_compose_network_failure(corepack))
        self.assertFalse(self.runtime._is_transient_compose_network_failure(deterministic))

    async def test_project_mcp_uses_per_project_token_and_json_rpc_errors_stay_transport_success(self):
        from api.routes import rakazo as rakazo_routes

        project_id = self.mapping["project_id"]
        with patch.object(rakazo_routes, "rakazo_runtime", self.runtime):
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://codebot.test") as client:
                unauthorized = await client.post(
                    f"/api/internal/rakazo-project-mcp/{project_id}",
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                )
                self.assertEqual(unauthorized.status_code, 401)

                headers = {"Authorization": f"Bearer {self.project_token}"}
                initialized = await client.post(
                    f"/api/internal/rakazo-project-mcp/{project_id}",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}},
                )
                self.assertEqual(initialized.status_code, 200)
                self.assertEqual(initialized.json()["result"]["serverInfo"]["name"], "codebot-rakazo-project")

                denied = await client.post(
                    f"/api/internal/rakazo-project-mcp/{project_id}",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "codebot_project_read", "arguments": {"path": ".env"}},
                    },
                )
                self.assertEqual(denied.status_code, 200)
                self.assertIn("error", denied.json())

    async def test_model_api_supports_server_side_status_filter_and_pagination(self):
        from api.routes import rakazo as rakazo_routes

        catalog = [
            {"id": "p/one", "compatibilityStatus": "verified"},
            {"id": "p/two", "compatibilityStatus": "probe_failed"},
            {"id": "p/three", "compatibilityStatus": "verified"},
        ]
        with patch.object(rakazo_routes.rakazo_runtime, "models", return_value=catalog):
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://codebot.test") as client:
                response = await client.get(
                    "/api/rakazo/models",
                    params={"status": "verified", "offset": 1, "limit": 1},
                )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["data"]
        self.assertEqual(payload["catalogTotal"], 3)
        self.assertEqual(payload["openCodeEnabledTotal"], 3)
        self.assertEqual(payload["sourceScope"], "opencode_enabled")
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["models"], [{"id": "p/three", "compatibilityStatus": "verified"}])
        self.assertEqual(payload["counts"]["probe_failed"], 1)

    async def test_internal_model_catalog_lists_selectable_routes_but_sampling_gate_stays_separate(self):
        from api.routes import rakazo as rakazo_routes

        catalog = [
            {"id": "p/verified", "provider": "p", "compatibilityStatus": "verified", "selectable": True},
            {"id": "p/first-use", "provider": "p", "compatibilityStatus": "probe_failed", "selectable": True},
            {"id": "p/blocked", "provider": "p", "compatibilityStatus": "protocol_pending", "selectable": False},
        ]
        headers = {"Authorization": f"Bearer {rakazo_routes.rakazo_runtime.sampling_token}"}
        with patch.object(rakazo_routes.rakazo_runtime, "models", return_value=catalog):
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://codebot.test") as client:
                response = await client.get("/api/internal/model-sampling/v1/models", headers=headers)

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["data"]}
        self.assertEqual(ids, {"p/verified", "p/first-use"})

if __name__ == "__main__":
    unittest.main()
