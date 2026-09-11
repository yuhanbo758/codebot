"""Codebot 的 Codex Agent Harness 运行时适配层。

该模块只负责把官方 ``openai-codex`` SDK 接到 Codebot 的生命周期、
SQLite 会话映射和 NDJSON 流式协议。命令执行、文件修改、线程上下文、
沙箱与模型调用都由 Codex App Server 自身实现，Codebot 不复制 Agent 循环。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

from loguru import logger

from config import app_config, settings
from core.codex_model_bridge import bridge_protocol_metadata
from core.model_route_registry import ModelRoute, model_route_registry


def _model_dump(value: Any) -> Dict[str, Any]:
    """把 SDK 的 Pydantic 对象稳定转换为使用 wire alias 的普通字典。"""
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(by_alias=True, exclude_none=True, mode="json")
        return dumped if isinstance(dumped, dict) else {"value": dumped}
    return dict(value) if isinstance(value, dict) else {"value": str(value)}


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


@dataclass
class PendingCodexRequest:
    """App Server 发给宿主 UI 的同步请求及其等待状态。"""

    request_id: str
    method: str
    params: Dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    result: Optional[Dict[str, Any]] = None


@dataclass
class ActiveCodexTurn:
    """用于把 SDK reader 线程中的审批事件路由回对应的 asyncio 流。"""

    conversation_id: str
    thread_id: str
    turn_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue
    interactive: bool


# 保留旧类型名，避免外部测试和插件导入路径被共享注册表抽取破坏。
OpenCodeResponsesModel = ModelRoute


class CodexRuntime:
    """管理单个 Codex App Server 进程和多个 Codebot 对话线程。"""

    def __init__(self) -> None:
        self._client: Any = None
        self._metadata: Dict[str, Any] = {}
        self._last_error = ""
        self._started_at = 0.0
        self._start_lock = asyncio.Lock()
        self._state_lock = threading.RLock()
        self._pending: Dict[str, PendingCodexRequest] = {}
        self._active_by_turn: Dict[str, ActiveCodexTurn] = {}
        self._active_by_conversation: Dict[str, ActiveCodexTurn] = {}
        self._opencode_models: Dict[str, OpenCodeResponsesModel] = {}
        self._opencode_incompatible: Dict[str, str] = {}
        # 该随机令牌仅在当前后端进程与其子 Codex App Server 之间流转，
        # 不复用 LAN Token，也不持久化到 config.json。
        self._bridge_token = secrets.token_urlsafe(32)
        self._init_db()

    # ------------------------------------------------------------------
    # 生命周期与 SDK 调用
    # ------------------------------------------------------------------
    def _sdk_imports(self) -> Dict[str, Any]:
        """延迟导入 SDK，使缺少依赖时后端仍能返回可诊断的状态。"""
        from openai_codex import __version__ as sdk_version
        from openai_codex.client import CodexClient, CodexConfig
        from openai_codex.generated.v2_all import (
            GetAccountParams,
            GetAccountRateLimitsResponse,
            GetAccountTokenUsageResponse,
            LoginAccountParams,
            SkillsListResponse,
            ThreadRollbackResponse,
        )

        return {
            "sdk_version": sdk_version,
            "CodexClient": CodexClient,
            "CodexConfig": CodexConfig,
            "GetAccountParams": GetAccountParams,
            "GetAccountRateLimitsResponse": GetAccountRateLimitsResponse,
            "GetAccountTokenUsageResponse": GetAccountTokenUsageResponse,
            "LoginAccountParams": LoginAccountParams,
            "SkillsListResponse": SkillsListResponse,
            "ThreadRollbackResponse": ThreadRollbackResponse,
        }

    def _runtime_port(self) -> int:
        raw = os.environ.get("CODEBOT_BACKEND_PORT", "").strip()
        try:
            return int(raw) if raw else int(app_config.network.port)
        except (TypeError, ValueError):
            return 15682

    @staticmethod
    def _opencode_auth_paths() -> List[Path]:
        """返回 OpenCode 官方凭据文件的候选位置，不创建或修改任何文件。"""
        return model_route_registry.opencode_auth_paths()

    @classmethod
    def _load_opencode_api_credentials(cls) -> Dict[str, str]:
        """只读取 OpenCode 中 `type=api` 的密钥；OAuth 凭据不会转交第三方 provider。"""
        return model_route_registry.load_opencode_api_credentials()

    def _opencode_server_url(self) -> str:
        """优先使用 main.py 已连接的真实 OpenCode 地址，兼容打包端口回退。"""
        return model_route_registry.opencode_server_url()

    @staticmethod
    def _provider_env_name(provider_id: str, base_url: str) -> str:
        return model_route_registry.provider_env_name(provider_id, base_url)

    @staticmethod
    def _codex_provider_id(provider_id: str, base_url: str, upstream_protocol: str = "responses") -> str:
        return model_route_registry.codex_provider_id(provider_id, base_url, upstream_protocol)

    @staticmethod
    def _protocol_label(npm_package: str) -> str:
        return model_route_registry.protocol_label(npm_package)

    @classmethod
    def _parse_opencode_provider_catalog(
        cls,
        payload: Any,
        credentials: Dict[str, str],
        environ: Optional[Dict[str, str]] = None,
    ) -> tuple[Dict[str, OpenCodeResponsesModel], Dict[str, str]]:
        """从 OpenCode provider 元数据构建 Codex 可用模型路由。

        Codex 自定义 model provider 当前只支持 Responses。OpenCode 的 `api.npm`
        描述的是 OpenCode 客户端选择的适配器，并不能证明上游只支持这一种协议：
        例如 OpenCode Go 会让 DeepSeek 走 Chat Completions 适配器，但同一 base URL
        实际也提供 `/responses`。因此只要同一上游地址已有任一明确的 Responses
        模型，就把该地址视为双协议端点，让 Codex 直接验证目标模型。其余
        其余模型按 ``codex_model_bridge`` 的显式协议注册表交给 Codebot 本机
        中间层；未知协议继续明确标记为不兼容，绝不偷偷回退到 ChatGPT 账号。
        """
        return model_route_registry.parse_runnable_catalog(payload, credentials, environ)

    def _discover_opencode_models_sync(self) -> tuple[Dict[str, OpenCodeResponsesModel], Dict[str, str]]:
        """读取本机 OpenCode 的公开 provider 元数据；失败时只禁用兼容扩展。"""
        routes, incompatible = model_route_registry.discover_sync(force=True)
        # OpenCode OAuth 的短期 access token 由 Rakazo 宿主采样桥按请求刷新；
        # Codex App Server 的 provider 配置是进程启动快照，无法安全轮换它。
        # Codex 自身已有官方 ChatGPT 账号通道，因此这里继续只注入稳定 API/
        # loopback 路由，避免 OAuth 到期后把 Codex 历史线程变成失效 provider。
        filtered = {
            route_id: route
            for route_id, route in routes.items()
            if route.credential_mode != "opencode_oauth"
        }
        for route_id, route in routes.items():
            if route.credential_mode == "opencode_oauth":
                incompatible[route_id] = "该 OpenCode OAuth 路由由 Rakazo 宿主桥动态刷新；Codex 使用自身官方账号通道"
        return filtered, incompatible

    @staticmethod
    def _route_signature(route: OpenCodeResponsesModel) -> tuple[Any, ...]:
        """比较 provider 配置是否变化；凭据只比较哈希，绝不写日志。"""
        return model_route_registry.route_signature(route)

    async def _refresh_opencode_models_if_changed(self) -> bool:
        """OpenCode 新增/修改 provider 后，在安全空闲点自动重启 App Server。

        App Server 的 model_providers 只能在进程启动参数中注入，所以仅更新内存模型
        列表还不够。若当前有活跃 turn，则本次刷新保持旧进程，避免为了刷新下拉框
        中断正在执行的任务；用户稍后再次刷新即可生效。
        """
        discovered, incompatible = await asyncio.to_thread(self._discover_opencode_models_sync)
        if not discovered:
            # OpenCode 短暂离线时不能把已工作的模型全部清空；显式重启仍会重新探测。
            return False
        current = {
            model_id: self._route_signature(route)
            for model_id, route in self._opencode_models.items()
        }
        latest = {
            model_id: self._route_signature(route)
            for model_id, route in discovered.items()
        }
        if current == latest:
            return False
        with self._state_lock:
            if self._active_by_turn:
                logger.info("OpenCode provider 已变化；当前 Codex turn 仍在运行，延后刷新 App Server")
                return False
        logger.info(
            f"检测到 OpenCode provider/model 变化，准备刷新 Codex App Server："
            f"{len(current)} -> {len(latest)}"
        )
        await self.close()
        # 先放入本次已验证目录；start 仍会再次发现，确保进程参数和最终状态一致。
        self._opencode_models = discovered
        self._opencode_incompatible = incompatible
        await self.start()
        return True

    def _resolve_model_route(
        self,
        model: Optional[str],
    ) -> tuple[Optional[str], str, Dict[str, Any]]:
        """把 UI 模型 ID 转成 Codex 的模型名、provider 和线程级能力配置。"""
        requested = str(model or "").strip()
        if not requested:
            return None, "openai", {}
        route = self._opencode_models.get(requested)
        if route is not None:
            thread_config: Dict[str, Any] = {
                "model_supports_reasoning_summaries": route.reasoning,
            }
            if route.context_window:
                thread_config["model_context_window"] = route.context_window
            return route.codex_model, route.codex_provider, thread_config
        if "/" in requested:
            detail = self._opencode_incompatible.get(requested)
            if detail:
                raise ValueError(detail)
            raise ValueError(
                f"OpenCode 模型 {requested} 不是当前 Codex App Server 已加载的兼容模型。"
                "请刷新模型列表或重启 Codex App Server；未知 provider 协议不会被静默回退。"
            )
        return requested, "openai", {}

    def _model_route_context(
        self,
        requested_model: Optional[str],
        codex_model: Optional[str],
        model_provider: str,
    ) -> tuple[str, Dict[str, Any]]:
        """生成不依赖“模型自我感觉”的权威路由说明与可展示事件数据。"""
        requested = str(requested_model or "").strip()
        route = self._opencode_models.get(requested)
        if route is not None:
            transport = "Codebot 本机协议桥" if route.uses_bridge else "Responses 直连"
            provider_label = route.opencode_provider
            selected_label = route.display_id
            resolved_label = route.codex_model
        else:
            transport = "Codex 原生 provider"
            provider_label = model_provider or "openai"
            selected_label = requested or "Codex 默认模型"
            resolved_label = str(codex_model or "Codex 默认模型")
        data = {
            "executor": "codex",
            "executorLabel": "Codex Agent Harness",
            "requestedModel": selected_label,
            "resolvedModel": resolved_label,
            "provider": provider_label,
            "modelProvider": model_provider,
            "transport": "codebot-bridge" if route and route.uses_bridge else "direct",
        }
        instructions = (
            "【Codebot 本轮权威运行路由】\n"
            "- 执行器：Codex Agent Harness（负责线程、工具、沙箱和审批）\n"
            f"- 用户选择的模型：{selected_label}\n"
            f"- 实际请求的上游模型 ID：{resolved_label}\n"
            f"- 上游 provider：{provider_label}\n"
            f"- 传输方式：{transport}\n"
            "如果用户询问你是什么模型或是否为某个模型，必须区分“Codex 执行器”与"
            "“本轮所选上游模型”：按以上权威路由如实回答，并说明这是实际请求路由，"
            "不要仅因为运行在 Codex 中就推断底层一定是 OpenAI 模型，也不要声称能从"
            "模型自身文本确认训练权重或内部版本。"
        )
        return instructions, data

    def _bridge_base_url(self, codex_provider: str) -> str:
        """返回只供 Codex 子进程访问的本机 Responses 桥地址。"""
        return (
            f"http://127.0.0.1:{self._runtime_port()}"
            f"/api/codex/compat/{codex_provider}/v1"
        )

    def _bridge_route(self, codex_provider: str, model: str) -> OpenCodeResponsesModel:
        """按 App Server provider + 上游 model 精确定位桥接路由，避免串号。"""
        candidates = [
            route
            for route in self._opencode_models.values()
            if route.uses_bridge
            and route.codex_provider == str(codex_provider)
            and route.codex_model == str(model)
        ]
        if len(candidates) != 1:
            if candidates:
                equivalent_routes = {
                    (
                        route.base_url,
                        route.api_key,
                        route.request_headers,
                        route.query_params,
                    )
                    for route in candidates
                }
                if len(equivalent_routes) == 1:
                    # OpenCode 允许多个展示别名指向同一个上游 model ID。只要实际
                    # 地址、凭据和请求参数完全一致，任选其一不会改变调用语义。
                    return candidates[0]
            raise ValueError(
                f"Codex 模型桥路由不存在或不唯一：provider={codex_provider}, model={model}"
            )
        return candidates[0]

    def bridge_authorized(self, authorization: str) -> bool:
        """验证 App Server 进程级随机 Bearer Token，任何失败都拒绝。"""
        scheme, _, token = str(authorization or "").partition(" ")
        return scheme.lower() == "bearer" and bool(token) and secrets.compare_digest(token, self._bridge_token)

    async def bridge_responses(
        self,
        codex_provider: str,
        payload: Dict[str, Any],
    ) -> Any:
        """执行一次本机协议桥接；上游密钥只从内存中的精确路由读取。"""
        from core.codex_model_bridge import bridge_responses_request

        model = str(payload.get("model") or "").strip()
        if not model:
            raise ValueError("Responses 请求缺少 model")
        route = self._bridge_route(codex_provider, model)
        return await bridge_responses_request(route, payload)

    def _build_sdk_config(self) -> Any:
        sdk = self._sdk_imports()
        configured_bin = ""
        if app_config.codex.runtime_source == "custom":
            configured_bin = str(app_config.codex.codex_bin or "").strip()
            if not configured_bin:
                raise ValueError("已选择自定义 Codex runtime，但 codex_bin 为空")

        env = {
            # 即使当前请求来自 loopback 也传入 Token，未来收紧本机鉴权时无需改协议。
            "CODEBOT_MCP_TOKEN": str(app_config.security.lan_api_token or ""),
            "CODEBOT_CODEX_BRIDGE_TOKEN": self._bridge_token,
        }
        mcp_url = f"http://127.0.0.1:{self._runtime_port()}/api/mcp/codebot/mcp"
        overrides: List[str] = [
            f"mcp_servers.codebot.url={json.dumps(mcp_url)}",
            'mcp_servers.codebot.bearer_token_env_var="CODEBOT_MCP_TOKEN"',
        ]
        configured_providers: set[str] = set()
        for route in self._opencode_models.values():
            if route.env_key and not route.uses_bridge:
                env[route.env_key] = route.api_key
            if route.codex_provider in configured_providers:
                continue
            configured_providers.add(route.codex_provider)
            prefix = f"model_providers.{route.codex_provider}"
            provider_base_url = self._bridge_base_url(route.codex_provider) if route.uses_bridge else route.base_url
            overrides.extend([
                f"{prefix}.name={json.dumps(f'OpenCode: {route.opencode_provider}')}",
                f"{prefix}.base_url={json.dumps(provider_base_url)}",
                f'{prefix}.wire_api="responses"',
                f"{prefix}.requires_openai_auth=false",
            ])
            if route.uses_bridge:
                # Codex 只看到一次性本机桥令牌；第三方 API Key 不进入子进程环境。
                overrides.append(f'{prefix}.env_key="CODEBOT_CODEX_BRIDGE_TOKEN"')
            elif route.env_key:
                overrides.append(f"{prefix}.env_key={json.dumps(route.env_key)}")
        return sdk["CodexConfig"](
            codex_bin=configured_bin or None,
            config_overrides=tuple(overrides),
            env=env,
            client_name="codebot",
            client_title="Codebot Codex Harness",
            client_version=str(app_config.version),
            experimental_api=True,
        )

    def _start_sync(self) -> None:
        with self._state_lock:
            if self._client is not None:
                return
            # provider 定义必须在启动 App Server 前通过进程参数注入。这里只读取
            # OpenCode 已连接的 provider，不修改 ~/.codex/config.toml 或 OpenCode 配置。
            self._opencode_models, self._opencode_incompatible = self._discover_opencode_models_sync()
            sdk = self._sdk_imports()
            client = sdk["CodexClient"](
                config=self._build_sdk_config(),
                approval_handler=self._handle_server_request,
            )
            try:
                client.start()
                metadata = client.initialize()
            except Exception:
                client.close()
                raise
            self._client = client
            self._metadata = _model_dump(metadata)
            self._metadata["sdkVersion"] = sdk["sdk_version"]
            self._last_error = ""
            self._started_at = time.time()

    async def start(self) -> None:
        if not app_config.codex.enabled:
            raise RuntimeError("Codex 集成已禁用")
        async with self._start_lock:
            if self._client is not None:
                return
            last_error: Optional[Exception] = None
            for attempt in range(2):
                try:
                    await asyncio.to_thread(self._start_sync)
                    return
                except Exception as exc:
                    last_error = exc
                    self._last_error = str(exc)
                    await asyncio.to_thread(self._close_sync)
                    if attempt == 0:
                        await asyncio.sleep(0.2)
            raise RuntimeError(f"Codex App Server 连续两次启动失败：{last_error}") from last_error

    def _close_sync(self) -> None:
        with self._state_lock:
            client, self._client = self._client, None
            pending = list(self._pending.values())
            self._pending.clear()
            self._active_by_turn.clear()
            self._active_by_conversation.clear()
        for item in pending:
            item.result = self._decline_result(item.method, item.params)
            item.event.set()
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.debug(f"关闭 Codex App Server 失败（跳过）：{exc}")

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _reject_pending_for_turn(self, turn_id: str) -> None:
        """进程退出、中断或流异常时拒绝该 turn 尚未处理的所有请求。"""
        with self._state_lock:
            pending = [
                item for item in self._pending.values()
                if str(item.params.get("turnId") or "") == str(turn_id)
            ]
        for item in pending:
            item.result = self._decline_result(item.method, item.params)
            item.event.set()

    async def restart(self) -> None:
        await self.close()
        await self.start()

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        await self.start()
        client = self._client
        if client is None:
            raise RuntimeError("Codex App Server 未启动")
        try:
            return await asyncio.to_thread(getattr(client, method), *args, **kwargs)
        except Exception as exc:
            # Transport 断开后关闭旧实例，下次请求会创建新进程并恢复持久化 thread。
            if "transport" in str(exc).lower() or "broken pipe" in str(exc).lower():
                self._last_error = str(exc)
                await self.close()
            raise

    def status(self) -> Dict[str, Any]:
        protocol_counts: Dict[str, int] = {}
        for route in self._opencode_models.values():
            protocol_counts[route.upstream_protocol] = protocol_counts.get(route.upstream_protocol, 0) + 1
        protocol_coverage: List[Dict[str, Any]] = [{
            "protocol": "responses",
            "label": "Responses 直连",
            "transport": "direct",
            "models": protocol_counts.get("responses", 0),
            "npmPackages": ["@ai-sdk/openai"],
        }]
        for metadata in bridge_protocol_metadata():
            protocol_coverage.append({
                **metadata,
                "transport": "codebot-bridge",
                "models": protocol_counts.get(str(metadata["protocol"]), 0),
            })
        return {
            "enabled": bool(app_config.codex.enabled),
            "running": self._client is not None,
            "runtimeSource": app_config.codex.runtime_source,
            "codexBin": app_config.codex.codex_bin if app_config.codex.runtime_source == "custom" else "",
            "approvalPolicy": app_config.codex.approval_policy,
            "metadata": dict(self._metadata),
            "startedAt": self._started_at or None,
            "lastError": self._last_error,
            "activeConversations": sorted(self._active_by_conversation),
            "pendingRequests": len(self._pending),
            "openCodeCompatibleModels": len(self._opencode_models),
            # 保留一个发布版本的状态字段兼容；其含义现已扩展为“Codex 可运行模型”。
            "openCodeResponsesModels": len(self._opencode_models),
            "openCodeBridgedModels": sum(1 for route in self._opencode_models.values() if route.uses_bridge),
            "openCodeIncompatibleModels": len(self._opencode_incompatible),
            "openCodeProtocolCoverage": protocol_coverage,
        }

    # ------------------------------------------------------------------
    # SQLite：Codebot 对话、Codex thread 与 turn 的持久化映射
    # ------------------------------------------------------------------
    @contextmanager
    def _connect_db(self) -> Iterator[sqlite3.Connection]:
        """返回会自动提交/回滚并关闭的短连接，避免 Windows 上锁住数据库。"""
        conn = sqlite3.connect(settings.CONVERSATIONS_DB, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with self._connect_db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runtime_sessions (
                    conversation_id INTEGER NOT NULL,
                    runtime TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    project_dir TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (conversation_id, runtime)
                );
                CREATE TABLE IF NOT EXISTS agent_turn_bindings (
                    conversation_id INTEGER NOT NULL,
                    runtime TEXT NOT NULL,
                    user_message_id INTEGER NOT NULL,
                    turn_id TEXT NOT NULL,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (conversation_id, runtime, user_message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_turn_bindings_lookup
                    ON agent_turn_bindings(conversation_id, runtime, user_message_id);
                """
            )

    @staticmethod
    def _project_key(project_dir: Optional[str]) -> str:
        if not str(project_dir or "").strip():
            return ""
        try:
            return str(Path(str(project_dir)).expanduser().resolve())
        except Exception:
            return str(project_dir or "").strip()

    def _get_session(self, conversation_id: int) -> Optional[Dict[str, Any]]:
        with self._connect_db() as conn:
            row = conn.execute(
                "SELECT * FROM agent_runtime_sessions WHERE conversation_id = ? AND runtime = 'codex'",
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    def _save_session(self, conversation_id: int, thread_id: str, project_dir: str) -> None:
        with self._connect_db() as conn:
            conn.execute(
                """
                INSERT INTO agent_runtime_sessions(conversation_id, runtime, thread_id, project_dir)
                VALUES (?, 'codex', ?, ?)
                ON CONFLICT(conversation_id, runtime) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    project_dir = excluded.project_dir,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (conversation_id, thread_id, project_dir),
            )

    def invalidate_session(self, conversation_id: int, *, clear_bindings: bool = True) -> None:
        with self._connect_db() as conn:
            conn.execute(
                "DELETE FROM agent_runtime_sessions WHERE conversation_id = ? AND runtime = 'codex'",
                (conversation_id,),
            )
            if clear_bindings:
                conn.execute(
                    "DELETE FROM agent_turn_bindings WHERE conversation_id = ? AND runtime = 'codex'",
                    (conversation_id,),
                )

    def _save_turn_binding(self, conversation_id: int, user_message_id: int, turn_id: str) -> None:
        with self._connect_db() as conn:
            conn.execute(
                """
                INSERT INTO agent_turn_bindings(conversation_id, runtime, user_message_id, turn_id)
                VALUES (?, 'codex', ?, ?)
                ON CONFLICT(conversation_id, runtime, user_message_id) DO UPDATE SET
                    turn_id = excluded.turn_id,
                    completed_at = CURRENT_TIMESTAMP
                """,
                (conversation_id, user_message_id, turn_id),
            )

    async def rollback_from_message(self, conversation_id: int, user_message_id: int) -> bool:
        """同步撤销 Codex 隐藏上下文；失败时丢弃映射以保证后续安全重建。"""
        session = self._get_session(conversation_id)
        if not session:
            return True
        with self._connect_db() as conn:
            count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM agent_turn_bindings
                    WHERE conversation_id = ? AND runtime = 'codex' AND user_message_id >= ?
                    """,
                    (conversation_id, user_message_id),
                ).fetchone()[0]
            )
        if count <= 0:
            return True
        try:
            sdk = self._sdk_imports()
            await self.start()
            client = self._client
            if client is None:
                raise RuntimeError("Codex App Server 未启动")
            await asyncio.to_thread(
                client.request,
                "thread/rollback",
                {"threadId": session["thread_id"], "numTurns": count},
                response_model=sdk["ThreadRollbackResponse"],
            )
            with self._connect_db() as conn:
                conn.execute(
                    """
                    DELETE FROM agent_turn_bindings
                    WHERE conversation_id = ? AND runtime = 'codex' AND user_message_id >= ?
                    """,
                    (conversation_id, user_message_id),
                )
            return True
        except Exception as exc:
            logger.warning(f"Codex thread rollback 失败，将在下轮安全重建：{exc}")
            self.invalidate_session(conversation_id)
            return False

    # ------------------------------------------------------------------
    # 审批与用户输入
    # ------------------------------------------------------------------
    def _find_active_turn(self, params: Dict[str, Any]) -> Optional[ActiveCodexTurn]:
        turn_id = str(params.get("turnId") or "")
        with self._state_lock:
            return self._active_by_turn.get(turn_id)

    @staticmethod
    def _decline_result(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "decline"}
        if method == "item/permissions/requestApproval":
            return {"permissions": [], "scope": "turn"}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None}
        if method in {"item/tool/requestUserInput", "tool/requestUserInput"}:
            return {"answers": {}}
        return {}

    def _approval_event(
        self,
        request_id: str,
        method: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        reason = str(params.get("reason") or params.get("message") or "").strip()
        command = params.get("command")
        if isinstance(command, list):
            command = " ".join(str(item) for item in command)
        command = str(command or "").strip()
        cwd = str(params.get("cwd") or params.get("grantRoot") or "").strip()
        network = params.get("networkApprovalContext") if isinstance(params.get("networkApprovalContext"), dict) else {}
        metadata = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        is_mcp_tool_approval = (
            method == "mcpServer/elicitation/request"
            and str(metadata.get("codex_approval_kind") or "") == "mcp_tool_call"
        )
        if is_mcp_tool_approval:
            tool_name = str(metadata.get("tool_title") or "").strip()
            if not tool_name:
                message_match = str(params.get("message") or "")
                tool_name = message_match.split('"')[1] if '"' in message_match else "MCP 工具"
            summary = f"Codex 请求调用 {params.get('serverName') or 'MCP'}：{tool_name}"
        elif network:
            host = str(network.get("host") or "")
            protocol = str(network.get("protocol") or "")
            summary = f"Codex 请求访问网络：{protocol}://{host}".rstrip("://")
        elif "fileChange" in method:
            summary = "Codex 请求批准文件修改"
        else:
            summary = f"Codex 请求执行命令：{command or '未提供命令预览'}"
        details = [item for item in (reason, f"工作目录：{cwd}" if cwd else "") if item]
        actions = [
            {"label": "允许一次", "reply": "once", "type": "primary"},
            {"label": "拒绝", "reply": "reject", "type": "danger"},
        ]
        # App Server 通过 _meta.persist 明确声明是否支持会话/永久记忆。
        # Codebot 目前只提供会话级批准，不擅自修改 Codex 的永久审批配置。
        persist = metadata.get("persist")
        persist_values = persist if isinstance(persist, list) else [persist]
        if is_mcp_tool_approval and "session" in persist_values:
            actions.insert(1, {"label": "本会话始终允许", "reply": "always", "type": "success"})
        elif not is_mcp_tool_approval:
            actions.insert(1, {"label": "本会话始终允许", "reply": "always", "type": "success"})
        return {
            "type": "meta_event",
            "source": "codex",
            "event_type": "permission.asked",
            "summary": summary,
            "detail": "\n".join(details),
            "requires_user_action": True,
            "request_id": request_id,
            "actions": actions,
            "data": {"request_id": request_id, "method": method, **params},
        }

    def _mcp_elicitation_event(self, request_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """把普通 MCP 表单 elicitation 转换为 Codebot 可回答的问题事件。"""
        schema = params.get("requestedSchema") if isinstance(params.get("requestedSchema"), dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        questions: List[Dict[str, Any]] = []
        for key, value in properties.items():
            field = value if isinstance(value, dict) else {}
            enum_values = field.get("enum") if isinstance(field.get("enum"), list) else []
            questions.append({
                "id": str(key),
                "header": str(field.get("title") or params.get("serverName") or "MCP"),
                "question": str(field.get("description") or field.get("title") or key),
                "options": [{"label": str(item), "value": item} for item in enum_values],
            })
        message = str(params.get("message") or "MCP 服务正在等待你的输入")
        if not questions:
            questions.append({"id": "response", "header": str(params.get("serverName") or "MCP"), "question": message, "options": []})
        event = self._question_event(request_id, {**params, "questions": questions})
        event["summary"] = message
        event["detail"] = message
        event["data"] = {"request_id": request_id, "method": "mcpServer/elicitation/request", **params}
        return event

    def _question_event(
        self,
        request_id: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        raw_questions = params.get("questions") if isinstance(params.get("questions"), list) else []
        questions: List[Dict[str, Any]] = []
        for index, item in enumerate(raw_questions):
            if not isinstance(item, dict):
                continue
            options = item.get("options") if isinstance(item.get("options"), list) else []
            questions.append({
                "id": str(item.get("id") or f"question_{index}"),
                "header": str(item.get("header") or "Codex"),
                "question": str(item.get("question") or item.get("prompt") or "请选择"),
                "options": options,
            })
        question_text = str((questions[0] if questions else {}).get("question") or "Codex 正在等待你的回答")
        actions: List[Dict[str, Any]] = []
        if questions:
            for option in questions[0].get("options") or []:
                if isinstance(option, dict):
                    label = str(option.get("label") or option.get("value") or "").strip()
                    if label:
                        actions.append({"label": label, "reply": "question_answer", "value": label, "type": "primary"})
        actions.append({"label": "自定义回答", "reply": "question_custom", "custom": True, "type": "info"})
        actions.append({"label": "取消/先不回答", "reply": "question_reject", "type": "danger"})
        return {
            "type": "meta_event",
            "source": "codex",
            "event_type": "question.asked",
            "summary": question_text,
            "detail": question_text,
            "requires_user_action": True,
            "request_id": request_id,
            "question": question_text,
            "questions": questions,
            "actions": actions,
            "data": {"request_id": request_id, **params},
        }

    def _handle_server_request(self, method: str, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = params if isinstance(params, dict) else {}
        active = self._find_active_turn(payload)
        policy = str(app_config.codex.approval_policy or "interactive")
        if active is None or not active.interactive or policy == "deny_all":
            return self._decline_result(method, payload)
        if policy == "auto_review" and method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            return {"decision": "accept"}

        request_id = f"codex-{uuid.uuid4().hex}"
        pending = PendingCodexRequest(request_id=request_id, method=method, params=payload)
        with self._state_lock:
            self._pending[request_id] = pending

        metadata = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
        is_mcp_tool_approval = (
            method == "mcpServer/elicitation/request"
            and str(metadata.get("codex_approval_kind") or "") == "mcp_tool_call"
        )
        if method == "mcpServer/elicitation/request" and not is_mcp_tool_approval:
            event = self._mcp_elicitation_event(request_id, payload)
        elif method in {"item/tool/requestUserInput", "tool/requestUserInput"}:
            event = self._question_event(request_id, payload)
        else:
            event = self._approval_event(request_id, method, payload)
        active.loop.call_soon_threadsafe(active.queue.put_nowait, event)

        auto_ms = payload.get("autoResolutionMs")
        try:
            timeout = max(1.0, min(600.0, float(auto_ms) / 1000.0)) if auto_ms else 600.0
        except (TypeError, ValueError):
            timeout = 600.0
        pending.event.wait(timeout=timeout)
        with self._state_lock:
            self._pending.pop(request_id, None)
        return pending.result or self._decline_result(method, payload)

    def reply_permission(self, request_id: str, reply: str) -> bool:
        with self._state_lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return False
        if pending.method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            decision = {"once": "accept", "always": "acceptForSession", "reject": "decline"}.get(reply)
            if decision is None:
                return False
            pending.result = {"decision": decision}
        elif pending.method == "item/permissions/requestApproval":
            requested = pending.params.get("permissions") if isinstance(pending.params.get("permissions"), list) else []
            pending.result = {"permissions": requested if reply != "reject" else [], "scope": "session" if reply == "always" else "turn"}
        elif pending.method == "mcpServer/elicitation/request":
            if reply == "reject":
                pending.result = {"action": "decline", "content": None}
            else:
                pending.result = {"action": "accept", "content": {}}
                if reply == "always":
                    pending.result["_meta"] = {"persist": "session"}
        else:
            pending.result = self._decline_result(pending.method, pending.params)
        pending.event.set()
        return True

    def reply_question(
        self,
        request_id: str,
        *,
        answers: Optional[List[List[str]]] = None,
        answer: str = "",
        reject: bool = False,
    ) -> bool:
        with self._state_lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return False
        if pending.method == "mcpServer/elicitation/request":
            schema = pending.params.get("requestedSchema") if isinstance(pending.params.get("requestedSchema"), dict) else {}
            properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
            content: Dict[str, Any] = {}
            if not reject:
                for index, (field_name, field_schema) in enumerate(properties.items()):
                    field = field_schema if isinstance(field_schema, dict) else {}
                    values = answers[index] if answers and index < len(answers) else ([answer] if answer else [])
                    cleaned = [str(item) for item in values if str(item).strip()]
                    if field.get("type") == "array":
                        content[str(field_name)] = cleaned
                    elif cleaned:
                        content[str(field_name)] = cleaned[0]
            pending.result = {
                "action": "decline" if reject else "accept",
                "content": None if reject else content,
            }
            pending.event.set()
            return True
        questions = pending.params.get("questions") if isinstance(pending.params.get("questions"), list) else []
        answer_map: Dict[str, Any] = {}
        if not reject:
            for index, question in enumerate(questions):
                question_id = str(question.get("id") or f"question_{index}") if isinstance(question, dict) else f"question_{index}"
                values = answers[index] if answers and index < len(answers) else ([answer] if answer else [])
                answer_map[question_id] = {"answers": [str(item) for item in values if str(item).strip()]}
        pending.result = {"answers": answer_map}
        pending.event.set()
        return True

    # ------------------------------------------------------------------
    # Thread/turn 执行与事件映射
    # ------------------------------------------------------------------
    def workspace_for(self, conversation_id: int, project_dir: Optional[str]) -> str:
        if str(project_dir or "").strip():
            workspace = Path(str(project_dir)).expanduser().resolve()
        else:
            workspace = settings.DATA_DIR / "codex" / "workspaces" / str(conversation_id)
            workspace.mkdir(parents=True, exist_ok=True)
        if not workspace.is_dir():
            raise ValueError(f"Codex 工作目录不存在：{workspace}")
        return str(workspace)

    @staticmethod
    def _approval_settings(interactive: bool) -> Dict[str, Any]:
        policy = str(app_config.codex.approval_policy or "interactive") if interactive else "deny_all"
        if policy == "deny_all":
            return {"approvalPolicy": "never", "approvalsReviewer": "user"}
        if policy == "auto_review":
            return {"approvalPolicy": "on-request", "approvalsReviewer": "auto_review"}
        return {"approvalPolicy": "on-request", "approvalsReviewer": "user"}

    @staticmethod
    def _sandbox_settings(mode: Optional[str], workspace: str) -> tuple[str, Dict[str, Any]]:
        if str(mode or "").lower() == "plan":
            return "read-only", {"type": "readOnly", "networkAccess": False}
        return "workspace-write", {
            "type": "workspaceWrite",
            "writableRoots": [workspace],
            "networkAccess": False,
        }

    async def _get_or_start_thread(
        self,
        conversation_id: int,
        workspace: str,
        *,
        model: Optional[str],
        model_provider: str,
        model_config: Optional[Dict[str, Any]],
        mode: Optional[str],
        developer_instructions: str,
        history_context: str,
        interactive: bool,
    ) -> str:
        session = self._get_session(conversation_id)
        project_key = self._project_key(workspace)
        approval = self._approval_settings(interactive)
        sandbox_mode, _ = self._sandbox_settings(mode, workspace)
        if session and self._project_key(session.get("project_dir")) == project_key:
            try:
                response = await self._call(
                    "thread_resume",
                    session["thread_id"],
                    {
                        "cwd": workspace,
                        "model": model or None,
                        "modelProvider": model_provider,
                        "config": model_config or None,
                        "developerInstructions": developer_instructions or None,
                        "sandbox": sandbox_mode,
                        **approval,
                    },
                )
                return str(response.thread.id)
            except Exception as exc:
                logger.warning(f"恢复 Codex thread 失败，将安全新建：{exc}")
                self.invalidate_session(conversation_id)
        elif session:
            # 工作目录改变时旧 thread 不能继续使用，否则工具 cwd 与隐藏上下文会混淆。
            self.invalidate_session(conversation_id)

        rehydration = developer_instructions
        if history_context.strip():
            rehydration = (
                f"{developer_instructions}\n\n"
                "以下是 Codebot 数据库中仍然保留的历史对话，仅用于恢复上下文。"
                "不要声称这些内容是本轮用户新输入：\n"
                f"{history_context}"
            ).strip()
        response = await self._call(
            "thread_start",
            {
                "cwd": workspace,
                "model": model or None,
                "modelProvider": model_provider,
                "config": model_config or None,
                "developerInstructions": rehydration or None,
                "sandbox": sandbox_mode,
                "serviceName": "codebot",
                **approval,
            },
        )
        thread_id = str(response.thread.id)
        self._save_session(conversation_id, thread_id, project_key)
        return thread_id

    @staticmethod
    def _notification_dict(notification: Any) -> tuple[str, Dict[str, Any]]:
        return str(getattr(notification, "method", "")), _model_dump(getattr(notification, "payload", None))

    @staticmethod
    def _item_summary(item: Dict[str, Any], completed: bool) -> tuple[str, str]:
        item_type = str(item.get("type") or "item")
        if item_type == "commandExecution":
            command = item.get("command")
            if isinstance(command, list):
                command = " ".join(str(value) for value in command)
            status = str(item.get("status") or ("completed" if completed else "started"))
            output = str(item.get("aggregatedOutput") or "")
            return f"Codex 命令 {status}: {command or 'command'}", output[-4000:]
        if item_type == "fileChange":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            paths = [str(change.get("path") or "") for change in changes if isinstance(change, dict)]
            return f"Codex {'已完成' if completed else '准备'}文件修改（{len(paths)} 个）", "\n".join(paths)
        if item_type == "mcpToolCall":
            return f"Codex MCP: {item.get('server') or ''}/{item.get('tool') or ''}", str(item.get("status") or "")
        if item_type == "plan":
            return "Codex 计划", str(item.get("text") or "")
        return f"Codex {item_type}", str(item.get("status") or "")

    async def run_turn_stream(
        self,
        *,
        message: str,
        conversation_id: int,
        user_message_id: Optional[int] = None,
        project_dir: Optional[str] = None,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        mode: Optional[str] = None,
        developer_instructions: str = "",
        history_context: str = "",
        skills: Optional[List[Dict[str, str]]] = None,
        interactive: bool = True,
    ) -> AsyncIterator[Dict[str, Any]]:
        await self.start()
        codex_model, model_provider, model_config = self._resolve_model_route(model)
        route_instructions, route_data = self._model_route_context(model, codex_model, model_provider)
        effective_developer_instructions = (
            f"{developer_instructions.strip()}\n\n{route_instructions}"
            if developer_instructions.strip()
            else route_instructions
        )
        # 先把宿主实际选中的路由告诉 UI。该事件比询问模型“你是谁”可靠，
        # 也便于用户在回复开始前核对 provider/model 是否发生了静默回退。
        yield {
            "type": "meta_event",
            "source": "codex",
            "event_type": "model.route",
            "summary": f"本轮模型：{route_data['requestedModel']}",
            "detail": (
                f"上游 {route_data['provider']}/{route_data['resolvedModel']}；"
                f"执行器 Codex Agent Harness；传输 {route_data['transport']}"
            ),
            "data": route_data,
        }
        workspace = self.workspace_for(conversation_id, project_dir)
        thread_id = await self._get_or_start_thread(
            conversation_id,
            workspace,
            model=codex_model,
            model_provider=model_provider,
            model_config=model_config,
            mode=mode,
            developer_instructions=effective_developer_instructions,
            history_context=history_context,
            interactive=interactive,
        )

        input_items: List[Dict[str, Any]] = [{"type": "text", "text": message}]
        for skill in skills or []:
            name = str(skill.get("name") or "").strip()
            path = str(skill.get("path") or "").strip()
            if name and path:
                input_items.append({"type": "skill", "name": name, "path": path})

        approval = self._approval_settings(interactive)
        _, sandbox_policy = self._sandbox_settings(mode, workspace)
        params: Dict[str, Any] = {
            "cwd": workspace,
            "model": codex_model or None,
            "effort": effort or None,
            "sandboxPolicy": sandbox_policy,
            "clientUserMessageId": str(user_message_id) if user_message_id else None,
            **approval,
        }
        params = {key: value for key, value in params.items() if value is not None}
        started = await self._call("turn_start", thread_id, input_items, params)
        turn_id = str(started.turn.id)
        queue: asyncio.Queue = asyncio.Queue()
        active = ActiveCodexTurn(
            conversation_id=str(conversation_id),
            thread_id=thread_id,
            turn_id=turn_id,
            loop=asyncio.get_running_loop(),
            queue=queue,
            interactive=interactive,
        )
        with self._state_lock:
            self._active_by_turn[turn_id] = active
            self._active_by_conversation[str(conversation_id)] = active

        content = ""
        client = self._client
        if client is None:
            raise RuntimeError("Codex App Server 未启动")
        notification_task: Optional[asyncio.Task] = None
        try:
            while True:
                # 审批 handler 会阻塞 SDK reader 线程等待用户；因此必须同时等待
                # UI queue 和 SDK notification，不能先阻塞在 next_turn_notification。
                if notification_task is None:
                    notification_task = asyncio.create_task(
                        asyncio.to_thread(client.next_turn_notification, turn_id)
                    )
                queue_task = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {notification_task, queue_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if queue_task in done:
                    yield queue_task.result()
                    continue
                queue_task.cancel()
                notification = notification_task.result()
                notification_task = None
                method, payload = self._notification_dict(notification)
                if method == "item/agentMessage/delta":
                    delta = str(payload.get("delta") or "")
                    if delta:
                        content += delta
                        yield {"type": "content_delta", "delta": delta, "content": content, "source": "codex"}
                    continue
                if method in {"item/reasoning/summaryTextDelta", "item/plan/delta"}:
                    delta = str(payload.get("delta") or "")
                    if delta:
                        yield {
                            "type": "meta_event",
                            "source": "codex",
                            "event_type": method,
                            "summary": "Codex 正在更新计划" if "plan" in method else "Codex 正在推理",
                            "detail": delta,
                            "data": payload,
                        }
                    continue
                if method in {"item/started", "item/completed"}:
                    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                    item_type = str(item.get("type") or "")
                    if item_type == "agentMessage" and method == "item/completed":
                        final_text = str(item.get("text") or "")
                        if final_text and not content:
                            content = final_text
                            yield {"type": "content_delta", "delta": final_text, "content": content, "source": "codex"}
                        continue
                    if item_type in {"commandExecution", "fileChange", "mcpToolCall", "plan"}:
                        summary, detail = self._item_summary(item, method == "item/completed")
                        yield {
                            "type": "meta_event",
                            "source": "codex",
                            "event_type": method,
                            "summary": summary,
                            "detail": detail,
                            "data": payload,
                        }
                    continue
                if method == "error":
                    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                    if bool(payload.get("willRetry")):
                        # App Server 会在上游 SSE 中断、限流或临时网络错误后发送
                        # ``willRetry=true``，随后由 Codex 自己继续同一 turn。这里若把
                        # “Reconnecting... 1/5”当成终止错误抛出，Codebot 会提前关闭
                        # reader，导致本来可以恢复的第三方 Responses provider 永远失败。
                        yield {
                            "type": "meta_event",
                            "source": "codex",
                            "event_type": method,
                            "summary": str(error.get("message") or payload.get("message") or "Codex 正在重连"),
                            "detail": "Codex 正在重试上游模型连接",
                            "data": {
                                "threadId": payload.get("threadId"),
                                "turnId": payload.get("turnId"),
                                "willRetry": True,
                            },
                        }
                        continue
                    raise RuntimeError(str(error.get("message") or payload.get("message") or "Codex turn failed"))
                if method == "turn/completed":
                    turn = payload.get("turn") if isinstance(payload.get("turn"), dict) else {}
                    status = _enum_value(turn.get("status"))
                    if status == "failed":
                        error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                        raise RuntimeError(str(error.get("message") or "Codex turn failed"))
                    if user_message_id:
                        self._save_turn_binding(conversation_id, user_message_id, turn_id)
                    yield {
                        "type": "done",
                        "content": content.strip(),
                        "parts": [],
                        "source": "codex",
                        "agent": "Codex Agent Harness",
                        "requestedModel": route_data["requestedModel"],
                        "resolvedModel": route_data["resolvedModel"],
                        "modelProvider": route_data["modelProvider"],
                        "modelTransport": route_data["transport"],
                        "status": status or "completed",
                    }
                    return
        finally:
            if notification_task is not None and not notification_task.done():
                notification_task.cancel()
            self._reject_pending_for_turn(turn_id)
            try:
                client.unregister_turn_notifications(turn_id)
            except Exception:
                pass
            with self._state_lock:
                self._active_by_turn.pop(turn_id, None)
                if self._active_by_conversation.get(str(conversation_id)) is active:
                    self._active_by_conversation.pop(str(conversation_id), None)

    async def abort_conversation(self, conversation_id: int) -> bool:
        with self._state_lock:
            active = self._active_by_conversation.get(str(conversation_id))
        if active is None:
            return False
        try:
            await self._call("turn_interrupt", active.thread_id, active.turn_id)
            return True
        finally:
            # 任何尚未回复的 UI 请求都必须拒绝，避免 reader 线程永久等待。
            self._reject_pending_for_turn(active.turn_id)

    # ------------------------------------------------------------------
    # 账号、模型和 Skills
    # ------------------------------------------------------------------
    async def models(self) -> List[Dict[str, Any]]:
        await self.start()
        await self._refresh_opencode_models_if_changed()
        response = await self._call("model_list", False)
        native_models: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in list(response.data or []):
            model = _model_dump(item)
            model_id = str(model.get("id") or model.get("model") or "").strip()
            if not model_id:
                continue
            model.setdefault("source", "codex")
            model.setdefault("provider", "openai-codex")
            model.setdefault("modelProvider", "openai")
            model.setdefault("runnable", True)
            native_models.append(model)
            seen.add(model_id)
        opencode_models = [
            route.public_model()
            for model_id, route in sorted(self._opencode_models.items())
            if model_id not in seen
        ]
        return [*native_models, *opencode_models]

    async def account(self) -> Dict[str, Any]:
        sdk = self._sdk_imports()
        response = await self._call("account_read", sdk["GetAccountParams"](refresh_token=False))
        data = _model_dump(response)
        client = self._client
        if client is not None:
            for method, model, key in (
                ("account/rateLimits/read", sdk["GetAccountRateLimitsResponse"], "rateLimits"),
                ("account/usage/read", sdk["GetAccountTokenUsageResponse"], "usage"),
            ):
                try:
                    value = await asyncio.to_thread(client.request, method, None, response_model=model)
                    data[key] = _model_dump(value)
                except Exception as exc:
                    data[f"{key}Error"] = str(exc)
        return data

    async def login(self, method: str, api_key: str = "") -> Dict[str, Any]:
        sdk = self._sdk_imports()
        login_type = {
            "chatgpt": "chatgpt",
            "device_code": "chatgptDeviceCode",
            "api_key": "apiKey",
        }.get(method)
        if not login_type:
            raise ValueError("不支持的 Codex 登录方式")
        payload: Dict[str, Any] = {"type": login_type}
        if login_type == "apiKey":
            if not api_key.strip():
                raise ValueError("API Key 不能为空")
            payload["apiKey"] = api_key.strip()
        params = sdk["LoginAccountParams"].model_validate(payload)
        response = await self._call("account_login_start", params)
        return _model_dump(response)

    async def logout(self) -> None:
        await self._call("account_logout")

    async def skills(self, cwd: str, force_reload: bool = False) -> List[Dict[str, Any]]:
        sdk = self._sdk_imports()
        extra_roots: List[str] = []
        for raw in [settings.SKILLS_DIR, *list(app_config.codex.skill_dirs or [])]:
            path = Path(str(raw)).expanduser()
            if path.is_dir():
                value = str(path.resolve())
                if value not in extra_roots:
                    extra_roots.append(value)
        params: Dict[str, Any] = {"cwds": [cwd], "forceReload": force_reload}
        if extra_roots:
            params["perCwdExtraUserRoots"] = [{"cwd": cwd, "extraUserRoots": extra_roots}]
        await self.start()
        client = self._client
        if client is None:
            return []
        response = await asyncio.to_thread(
            client.request,
            "skills/list",
            params,
            response_model=sdk["SkillsListResponse"],
        )
        data = _model_dump(response).get("data") or []
        result: List[Dict[str, Any]] = []
        for group in data if isinstance(data, list) else []:
            if isinstance(group, dict):
                result.extend(item for item in (group.get("skills") or []) if isinstance(item, dict))
        return result


codex_runtime = CodexRuntime()
