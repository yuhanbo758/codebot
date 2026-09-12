"""Codebot 到 Rakazo 的受控运行时适配层。

本模块只使用 Rakazo 官方 oRPC/SSE 合同，并把 Bot、Thread、Computer 与 Codebot
项目主会话建立一对一映射。模型调用通过 ``model_route_registry`` 和原始采样桥
完成，不创建 OpenCode Session，不调用 OpenCode Agent，也不修改 Rakazo 源码。
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from typing import Any, AsyncIterator, Dict, Iterator, List, Mapping, Optional
from urllib.parse import unquote, urlparse

import httpx
from loguru import logger

from config import app_config, save_config, settings
from core.codex_model_bridge import (
    BridgedChatResult,
    CodexModelBridgeError,
    bridge_chat_completions_request,
    bridge_chat_completions_stream,
)
from core.model_route_registry import ModelRoute, model_route_registry


MAX_RPC_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_SAMPLING_REQUEST_BYTES = 8 * 1024 * 1024
MAX_PROJECT_FILE_BYTES = 2 * 1024 * 1024
RAKAZO_REPOSITORY = "elie222/rakazo"
RAKAZO_LOCAL_PROVIDER = "local"
# Rakazo 固定实验版会优先使用 models.connect 保存的用户凭据，而不是
# pi-local-provider.ts 中 resolve() 返回的默认 ``local``。该值只是 Rakazo
# 私有 Docker 网络内 api/worker 到 model-adapter 的协议键，不是 OpenCode
# 或模型 Provider 凭据；真正访问 Codebot 采样接口仍使用每进程随机令牌。
RAKAZO_LOCAL_ADAPTER_SHARED_KEY = "codebot-local-bridge"
RAKAZO_LOCAL_CREDENTIAL_LABEL = "Codebot private model bridge v2"
RAKAZO_ADAPTER_VERSION = "0.2.0"
_RUNTIME_SECRET_NAMES = (
    "POSTGRES_PASSWORD",
    "BETTER_AUTH_SECRET",
    "ENCRYPTION_KEY",
    "SCREEN_PROXY_SECRET",
    "SANDBOX_SUPERVISOR_TOKEN",
)
_COMPOSE_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_SENSITIVE_PROJECT_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}
_SENSITIVE_PROJECT_SUFFIXES = {".pem", ".p12", ".pfx", ".key"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(error: Any) -> str:
    """截断并脱敏适合前端和审计记录的错误文本。"""
    text = str(error or "未知错误")
    text = re.sub(r"(?i)(authorization|api[-_ ]?key|token|secret|cookie)\s*[:=]\s*\S+", r"\1=[REDACTED]", text)
    text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    if len(text) <= 2000:
        return text
    # Docker Compose 会在真正错误前输出大量镜像层进度。只保留开头会把根因
    # 截掉，因此同时保留命令上下文与末尾错误；敏感字段已在上方统一脱敏。
    return f"{text[:500]}\n...[中间 {len(text) - 1900} 字符已省略]...\n{text[-1400:]}"


def _rpc_error_message(payload: Any, fallback: Any) -> str:
    """从 oRPC 的多层错误包装中提取可读消息。

    oRPC 的错误可能位于 ``error.message``、``error.json.message`` 或
    ``error.data.message``。旧实现只检查第一层，遇到固定实验版返回的
    ``error.json`` 时最终只显示 HTTP 状态码，掩盖了真正的合同错误。
    这里只遍历已知错误字段并最终统一经过 ``_safe_error``，既保留根因，也
    避免把请求载荷、认证头或任意上游对象完整回显到前端。
    """

    def _walk(value: Any, depth: int = 0) -> str:
        if depth > 6:
            return ""
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, Mapping):
            return ""
        for key in ("message", "json", "data", "cause", "error"):
            if key not in value:
                continue
            message = _walk(value.get(key), depth + 1)
            if message:
                return message
        code = value.get("code")
        return str(code).strip() if isinstance(code, (str, int)) else ""

    root = payload.get("error") if isinstance(payload, Mapping) and "error" in payload else payload
    return _safe_error(_walk(root) or fallback)


_SECRET_FIELD_MARKERS = (
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "session_token",
    "password",
    "secret",
    "cookie",
)


def _redact_sensitive(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """递归清理可进入前端、日志或审计表的 Rakazo 载荷。

    Rakazo 工具事件可能把认证 header 嵌在多层 ``input``/``metadata`` 内，单纯
    检查顶层字段不足以阻止泄漏。这里既按字段名替换，也清理字符串中的 Bearer
    与常见 key/value 片段，同时限制递归深度和单个字符串长度。
    """
    lowered_key = str(key or "").lower().replace("-", "_")
    if any(marker in lowered_key for marker in _SECRET_FIELD_MARKERS):
        return "[REDACTED]"
    if depth > 12:
        return "[TRUNCATED]"
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:500]:
            result[str(raw_key)] = _redact_sensitive(raw_value, key=str(raw_key), depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_sensitive(item, depth=depth + 1) for item in list(value)[:500]]
    if isinstance(value, str):
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        text = re.sub(
            r"(?i)(authorization|api[-_ ]?key|access[-_ ]?token|refresh[-_ ]?token|password|secret|cookie)\s*[:=]\s*[^\s,;]+",
            r"\1=[REDACTED]",
            text,
        )
        return text[:20_000] + ("…" if len(text) > 20_000 else "")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2000]


def _windows_dpapi(data: bytes, *, protect: bool) -> bytes:
    """使用当前 Windows 用户的 DPAPI 加解密本机运行时密钥。

    Compose 启动时确实需要 Postgres/认证密钥的明文环境值，但这些值不应长期
    以明文文件保存。Codebot 只在执行 Compose 的短暂窗口生成 ``.env``，持久
    副本由当前用户 DPAPI 保护；其他 Windows 用户和其他机器都无法解密。
    """
    if os.name != "nt":
        return data
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    incoming = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    outgoing = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(incoming),
            "Codebot Rakazo runtime secrets",
            None,
            None,
            None,
            flags,
            ctypes.byref(outgoing),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(incoming),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(outgoing),
        )
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows DPAPI operation failed")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel32.LocalFree(outgoing.pbData)


def _protect_runtime_secret_payload(data: bytes) -> bytes:
    protected = _windows_dpapi(data, protect=True)
    prefix = b"DPAPI1\n" if os.name == "nt" else b"LOCAL1\n"
    return prefix + base64.b64encode(protected)


def _unprotect_runtime_secret_payload(data: bytes) -> bytes:
    prefix, separator, encoded = data.partition(b"\n")
    if not separator or prefix not in {b"DPAPI1", b"LOCAL1"}:
        raise ValueError("unsupported Rakazo runtime secret store")
    raw = base64.b64decode(encoded, validate=True)
    if prefix == b"DPAPI1":
        if os.name != "nt":
            raise ValueError("Windows DPAPI secret store cannot be opened on this platform")
        return _windows_dpapi(raw, protect=False)
    if os.name == "nt":
        raise ValueError("refusing non-DPAPI Rakazo secret store on Windows")
    return raw


class RakazoRuntimeError(RuntimeError):
    """带可安全展示消息的 Rakazo 运行时错误。"""

    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(_safe_error(message))
        self.status_code = max(400, min(int(status_code), 599))


class RakazoRuntime:
    """管理 Rakazo API、项目映射、模型探测和运行态审计。"""

    def __init__(self) -> None:
        # 内部采样令牌每次 Codebot 后端进程启动都重新生成，不写入 config.json。
        self._sampling_token = secrets.token_urlsafe(32)
        # Rakazo 登录令牌默认只驻留当前后端进程；需要跨重启时由用户通过
        # CODEBOT_RAKAZO_SESSION_TOKEN 注入。Windows chmod 不能提供可靠 ACL，
        # 因此不把令牌伪装成“安全文件”持久化。
        self._session_token = ""
        self._active_runs: Dict[str, str] = {}
        self._active_lock = asyncio.Lock()
        # Rakazo 的可回答输入由 ``thread.message.created`` 中的 ask block
        # 携带，真正回复时必须同时提交 bot/run/message 三个服务端标识。
        # 这些标识只保存在后端的一次性随机请求映射中，前端不能自行拼接或
        # 篡改上游对象 ID；后端重启后映射自然失效，避免重放旧审批。
        self._pending_inputs: Dict[str, Dict[str, Any]] = {}
        self._pending_input_keys: Dict[str, str] = {}
        self._pending_input_lock = asyncio.Lock()
        self._db_lock = asyncio.Lock()
        # 首次选择模型时做懒探测；串行锁避免同一路由被新建会话、交接和模型
        # 切换同时重复探测并产生多次计费请求。
        self._automatic_probe_lock = asyncio.Lock()
        # Rakazo 的固定实验版虽然会从 RAKAZO_LOCAL_MODELS 注册 ``local``
        # Provider，但 bots.update 仍要求当前本机账号存在一条 Provider 连接
        # 记录。串行锁保证并发打开两个项目时只创建一条官方连接记录。
        self._local_provider_lock = asyncio.Lock()
        # 同一项目的远端 Bot 失效时只允许一个协程执行修复。否则聊天页状态刷新、
        # 模型切换和发送消息可能同时发现 404，并各自创建一套 Bot/Thread/Computer。
        self._project_repair_locks: Dict[str, asyncio.Lock] = {}
        self._last_error = ""
        # Pi 的 local provider 在进程启动时读取模型目录。这里装载 OpenCode
        # 当前可选且具有安全纯采样路由的完整集合；内部采样端点仍逐模型要求
        # 已验证，因此直接绕过 Codebot 修改 Bot 也不能执行未验证模型。
        self._pending_model_ids: set[str] = set()
        self._loaded_model_ids: set[str] = set()
        # 最近一次通过的容器镜像身份，仅作运行期缓存；后端重启后必须重新从
        # Docker 镜像标签与正在运行的 api 容器验证，不能信任配置文件自报。
        self._verified_runtime_identity: Dict[str, Any] = {}
        self._init_db()

    @property
    def sampling_token(self) -> str:
        """仅供本机私有适配器注入；任何状态 API 都不能返回它。"""
        return self._sampling_token

    @property
    def session_token_path(self) -> Path:
        return settings.DATA_DIR / ".rakazo_session_token"

    def session_token_configured(self) -> bool:
        return bool(self._read_session_token())

    def _read_session_token(self) -> str:
        env_token = str(os.environ.get("CODEBOT_RAKAZO_SESSION_TOKEN") or "").strip()
        if env_token:
            return env_token
        return self._session_token

    def set_session_token(self, token: str) -> None:
        """保存用户主动提供的 Rakazo 会话令牌，仅驻留当前进程内存。"""
        value = str(token or "").strip()
        if len(value) < 16 or len(value) > 16384:
            raise RakazoRuntimeError("Rakazo 会话令牌长度无效", status_code=400)
        self._session_token = value
        # 清理曾由早期预览实现写下的明文文件；不读取、不迁移其内容。
        try:
            self.session_token_path.unlink(missing_ok=True)
        except OSError:
            pass

    def clear_session_token(self) -> None:
        self._session_token = ""
        try:
            self.session_token_path.unlink(missing_ok=True)
        except OSError as exc:
            raise RakazoRuntimeError(f"删除 Rakazo 会话令牌失败：{exc}", status_code=500) from exc

    @staticmethod
    def _auth_token_from_response(response: httpx.Response, payload: Any) -> str:
        """从 Better Auth JSON 或 HttpOnly Cookie 中提取会话令牌。"""
        if isinstance(payload, Mapping):
            direct = payload.get("token")
            if isinstance(direct, str) and direct:
                return direct
            session = payload.get("session")
            if isinstance(session, Mapping) and isinstance(session.get("token"), str):
                return str(session.get("token") or "")
        for cookie in response.headers.get_list("set-cookie"):
            match = re.search(r"(?:^|;\s*)better-auth\.session_token=([^;]+)", cookie)
            if match:
                return unquote(match.group(1))
        return ""

    async def _email_auth(self, endpoint: str, payload: Mapping[str, str]) -> str:
        """只向已验证的本机 Rakazo 地址提交自动生成的本机账号凭据。"""
        base_url = self._validated_api_url()
        timeout = httpx.Timeout(30.0, connect=5.0, read=30.0, write=10.0, pool=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    f"{base_url}/api/auth/{endpoint}",
                    headers={
                        "Content-Type": "application/json",
                        "Origin": base_url,
                        "User-Agent": f"Codebot/{app_config.version} RakazoBootstrap/1",
                    },
                    json=dict(payload),
                )
        except httpx.HTTPError as exc:
            raise RakazoRuntimeError(f"Rakazo 本机授权连接失败：{exc}") from exc
        if len(response.content) > 1024 * 1024:
            raise RakazoRuntimeError("Rakazo 授权响应超过 1 MiB 安全上限")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            detail = ""
            if isinstance(data, Mapping):
                detail = str(data.get("message") or data.get("error") or data.get("code") or "")
            raise RakazoRuntimeError(
                f"Rakazo 本机授权失败（HTTP {response.status_code}）：{detail or '上游拒绝请求'}",
                status_code=response.status_code,
            )
        token = self._auth_token_from_response(response, data)
        if len(token) < 16:
            raise RakazoRuntimeError("Rakazo 授权成功但没有返回有效会话令牌")
        return token

    async def bootstrap_desktop_authorization(
        self,
        *,
        mode: str,
        secret_bundle: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建或恢复由 Electron safeStorage 托管的 Rakazo 本机账号。

        该方法仅由带随机桌面桥令牌的内部路由调用。返回的邮箱、密码和会话
        令牌只能由 Electron 主进程接收并立即加密，普通 REST/UI 永不回显。
        """
        if mode not in {"create", "restore"}:
            raise RakazoRuntimeError("不支持的 Rakazo 桌面授权模式", status_code=400)
        bundle = dict(secret_bundle or {})
        if mode == "create":
            bundle = {
                "email": f"codebot-{secrets.token_hex(12)}@localhost.invalid",
                "password": secrets.token_urlsafe(36),
                "name": "Codebot Local",
                "sessionToken": "",
            }
            token = await self._email_auth(
                "sign-up/email",
                {
                    "email": bundle["email"],
                    "password": bundle["password"],
                    "name": bundle["name"],
                },
            )
        else:
            email = str(bundle.get("email") or "").strip()
            password = str(bundle.get("password") or "")
            token = str(bundle.get("sessionToken") or "").strip()
            if not email or len(password) < 8:
                raise RakazoRuntimeError("加密的 Rakazo 本机账号数据不完整，请重新一键授权", status_code=409)
            if token:
                self.set_session_token(token)
                try:
                    health = await self.rpc("health", {})
                    return {
                        "secretBundle": {**bundle, "sessionToken": token},
                        "health": health if isinstance(health, dict) else {"ok": True},
                    }
                except Exception:
                    # 会话过期时只使用 safeStorage 中的本机账号重新登录；不把异常
                    # 或凭据写入日志，也不回退到匿名/默认账号。
                    self._session_token = ""
            token = await self._email_auth(
                "sign-in/email",
                {"email": email, "password": password},
            )

        self.set_session_token(token)
        try:
            health = await self.rpc("health", {})
        except Exception:
            self._session_token = ""
            raise
        return {
            "secretBundle": {
                "email": str(bundle.get("email") or ""),
                "password": str(bundle.get("password") or ""),
                "name": str(bundle.get("name") or "Codebot Local"),
                "sessionToken": token,
            },
            "health": health if isinstance(health, dict) else {"ok": True},
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(settings.RAKAZO_DB), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        """通过增量建表保存映射和审计，不复制 Rakazo 消息/记忆数据库。"""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rakazo_projects (
                    project_id TEXT PRIMARY KEY,
                    project_key TEXT NOT NULL UNIQUE,
                    project_dir TEXT NOT NULL,
                    conversation_id INTEGER NOT NULL UNIQUE,
                    bot_id TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL UNIQUE,
                    computer_id TEXT,
                    mcp_server_id TEXT,
                    project_token_hash TEXT,
                    model_route_id TEXT,
                    model_route_fingerprint TEXT,
                    runtime_version TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rakazo_permissions (
                    project_id TEXT PRIMARY KEY,
                    project_read INTEGER NOT NULL DEFAULT 1,
                    project_write INTEGER NOT NULL DEFAULT 0,
                    command_execution INTEGER NOT NULL DEFAULT 0,
                    external_network INTEGER NOT NULL DEFAULT 0,
                    network_policy TEXT NOT NULL DEFAULT 'ask',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES rakazo_projects(project_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS rakazo_model_probes (
                    route_id TEXT PRIMARY KEY,
                    route_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checks_json TEXT NOT NULL,
                    actual_model TEXT,
                    error_summary TEXT,
                    last_probe_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rakazo_turn_routes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    conversation_id INTEGER NOT NULL,
                    run_id TEXT,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    route_fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rakazo_retired_bots (
                    project_id TEXT NOT NULL,
                    bot_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, bot_id)
                );

                CREATE TABLE IF NOT EXISTS rakazo_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_rakazo_turn_project
                ON rakazo_turn_routes(project_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_rakazo_audit_project
                ON rakazo_audit(project_id, created_at DESC);
                """
            )
            # 已安装过早期预览版数据库时，以增量列迁移保持原映射可用。
            columns = {row[1] for row in conn.execute("PRAGMA table_info(rakazo_projects)").fetchall()}
            if "mcp_server_id" not in columns:
                conn.execute("ALTER TABLE rakazo_projects ADD COLUMN mcp_server_id TEXT")
            if "project_token_hash" not in columns:
                conn.execute("ALTER TABLE rakazo_projects ADD COLUMN project_token_hash TEXT")

    @staticmethod
    def normalize_project(project_dir: str) -> tuple[str, str, str]:
        """返回 ``(project_id, project_key, resolved_path)`` 并拒绝模糊根目录。"""
        raw = str(project_dir or "").strip()
        if not raw:
            raise RakazoRuntimeError("Rakazo 主会话必须选择项目目录", status_code=400)
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise RakazoRuntimeError("Rakazo 项目目录必须是绝对路径", status_code=400)
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise RakazoRuntimeError(f"Rakazo 项目目录不存在或不可访问：{exc}", status_code=400) from exc
        if not resolved.is_dir():
            raise RakazoRuntimeError("Rakazo 项目路径不是目录", status_code=400)
        anchor = Path(resolved.anchor)
        if resolved == anchor:
            raise RakazoRuntimeError("拒绝把磁盘根目录作为 Rakazo 项目", status_code=400)
        # 根目录本身若是符号链接/联接点，保存真实路径，避免同一项目产生第二映射。
        resolved_text = str(resolved)
        project_key = os.path.normcase(resolved_text) if os.name == "nt" else resolved_text
        digest = hashlib.sha256(project_key.encode("utf-8")).hexdigest()
        return f"rkz_{digest[:24]}", digest, resolved_text

    def mapping_by_project_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rakazo_projects WHERE project_id = ?",
                (str(project_id or ""),),
            ).fetchone()
        return dict(row) if row else None

    def mapping_by_project_dir(self, project_dir: str) -> Optional[Dict[str, Any]]:
        _, key, _ = self.normalize_project(project_dir)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rakazo_projects WHERE project_key = ?",
                (key,),
            ).fetchone()
        return dict(row) if row else None

    def mapping_by_conversation(self, conversation_id: int | str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rakazo_projects WHERE conversation_id = ?",
                (int(conversation_id),),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def public_mapping(mapping: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """返回可发给前端的映射，永不暴露项目 MCP 鉴权哈希。"""
        result = dict(mapping or {})
        result.pop("project_token_hash", None)
        return result

    def list_project_permissions(self) -> List[Dict[str, Any]]:
        """列出已有 Rakazo 项目及其真实权限，供设置页逐项目管理。

        全局“默认权限”只影响以后创建的项目，不能代替项目级确认。这里返回的
        是数据库中已经绑定的项目权限，并继续移除 MCP 鉴权哈希；命令与外网是否
        可用仍由 ``get_permissions`` 的安全能力字段决定，前端不能绕过。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM rakazo_projects ORDER BY updated_at DESC, project_id ASC"
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            mapping = self.public_mapping(dict(row))
            result.append({
                "project": mapping,
                "permissions": self.get_permissions(str(mapping["project_id"])),
            })
        return result

    def save_mapping(
        self,
        *,
        project_dir: str,
        conversation_id: int,
        bot: Mapping[str, Any],
        route: Optional[ModelRoute],
        runtime_version: str = "",
        replace_missing_remote: bool = False,
    ) -> Dict[str, Any]:
        project_id, project_key, resolved = self.normalize_project(project_dir)
        bot_id = str(bot.get("id") or "").strip()
        thread_id = str(bot.get("threadId") or bot.get("thread_id") or "").strip()
        computer = bot.get("computer") if isinstance(bot.get("computer"), dict) else {}
        computer_id = str(computer.get("id") or bot.get("computerId") or "").strip() or None
        mcp_server_id = str(bot.get("_codebotMcpServerId") or "").strip() or None
        project_token_hash = str(bot.get("_codebotProjectTokenHash") or "").strip() or None
        if not bot_id or not thread_id:
            raise RakazoRuntimeError("Rakazo 创建 Bot 后未返回 Bot/Thread 标识")
        existing = self.mapping_by_project_id(project_id)
        if existing:
            same_identity = (
                int(existing.get("conversation_id") or 0) == int(conversation_id)
                and str(existing.get("bot_id") or "") == bot_id
                and str(existing.get("thread_id") or "") == thread_id
            )
            # 正常打开永远不能覆盖项目唯一身份。只有已通过真实 bots.get
            # “资源不存在”验证的自愈流程，才可在保留原 Codebot 主会话的前提下
            # 原子替换远端 Bot/Thread/Computer 身份。
            if not same_identity and not replace_missing_remote:
                raise RakazoRuntimeError(
                    "该项目已绑定唯一 Rakazo Bot、Thread 和 Codebot 主会话；拒绝覆盖既有身份",
                    status_code=409,
                )
        now = _utc_now()
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO rakazo_projects
                       (project_id, project_key, project_dir, conversation_id, bot_id, thread_id,
                        computer_id, mcp_server_id, project_token_hash, model_route_id,
                        model_route_fingerprint, runtime_version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(project_key) DO UPDATE SET
                         conversation_id = excluded.conversation_id,
                         bot_id = excluded.bot_id,
                         thread_id = excluded.thread_id,
                         computer_id = excluded.computer_id,
                         mcp_server_id = excluded.mcp_server_id,
                         project_token_hash = excluded.project_token_hash,
                         model_route_id = excluded.model_route_id,
                         model_route_fingerprint = excluded.model_route_fingerprint,
                         runtime_version = excluded.runtime_version,
                         updated_at = excluded.updated_at""",
                    (
                        project_id,
                        project_key,
                        resolved,
                        int(conversation_id),
                        bot_id,
                        thread_id,
                        computer_id,
                        mcp_server_id,
                        project_token_hash,
                        route.route_id if route else None,
                        route.route_fingerprint if route else None,
                        runtime_version,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO rakazo_permissions
                       (project_id, project_read, project_write, command_execution,
                        external_network, network_policy, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        1 if app_config.rakazo.default_project_read else 0,
                        # 默认权限只影响以后创建的项目；设置页保存这些值本身就是
                        # 用户的显式选择，已有项目仍必须逐项目修改。
                        1 if app_config.rakazo.default_project_write else 0,
                        1 if app_config.rakazo.default_command_execution else 0,
                        1 if app_config.rakazo.default_external_network else 0,
                        "allow" if app_config.rakazo.default_external_network else "deny",
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RakazoRuntimeError(
                "Rakazo Bot、Thread 或 Codebot 主会话已绑定到其他项目，拒绝重复映射",
                status_code=409,
            ) from exc
        self.audit(project_id, "project.open", "success", {"conversationId": int(conversation_id)})
        return self.mapping_by_project_id(project_id) or {}

    def delete_mapping(self, project_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM rakazo_projects WHERE project_id = ?", (project_id,))

    def audit(self, project_id: Optional[str], action: str, status: str, detail: Mapping[str, Any]) -> None:
        safe_detail = _redact_sensitive(detail or {})
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO rakazo_audit (project_id, action, status, detail_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, str(action), str(status), json.dumps(safe_detail, ensure_ascii=False), _utc_now()),
            )

    def recent_audit(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT id, project_id, action, status, detail_json, created_at
                   FROM rakazo_audit WHERE project_id = ? ORDER BY id DESC LIMIT ?""",
                (project_id, max(1, min(int(limit), 200))),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.pop("detail_json"))
            except Exception:
                item["detail"] = {}
                item.pop("detail_json", None)
            result.append(item)
        return result

    def get_permissions(self, project_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rakazo_permissions WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if not row:
            raise RakazoRuntimeError("Rakazo 项目映射不存在", status_code=404)
        item = dict(row)
        return {
            "projectId": project_id,
            "projectRead": bool(item["project_read"]),
            "projectWrite": bool(item["project_write"]),
            "commandExecution": bool(item["command_execution"]),
            "externalNetwork": bool(item["external_network"]),
            "networkPolicy": item["network_policy"],
            # 命令只在该 Bot 的专属 Linux Computer 内执行，不会把宿主项目目录
            # 或 Docker Socket 暴露给 Agent。关闭时通过官方 approvalRules 合同
            # 要求每次 shell 调用由用户审批；开启后才允许无逐次审批执行。
            "commandSupported": True,
            # 受管 Compose 可以通过每 Bot 独立 Docker 网络的 Internal 属性真实
            # 阻断或放行外网；非受管部署没有该控制面，继续失败关闭。
            "externalNetworkSupported": self._is_managed_compose(),
            "commandMode": "allowed" if bool(item["command_execution"]) else "approval_required",
            "externalNetworkMode": "allowed" if bool(item["external_network"]) else "blocked",
            "permissionNote": (
                "命令在项目专属 Rakazo Computer 容器内执行；关闭时每次 shell 调用需要审批。"
                "外部网络通过该 Bot 的 Docker 私有网络真实隔离，切换时会重建 Computer 容器，"
                "但保留其专属 home 数据。"
            ),
            "updatedAt": item["updated_at"],
        }

    async def update_permissions(self, project_id: str, updates: Mapping[str, Any]) -> Dict[str, Any]:
        current = self.get_permissions(project_id)
        mapping, _ = await self._ensure_remote_project(project_id)
        requested_external = bool(updates.get("externalNetwork", current["externalNetwork"]))
        if requested_external and not self._is_managed_compose():
            raise RakazoRuntimeError(
                "当前 Rakazo 不是 Codebot 受管 Compose，无法可靠重建项目私有网络；外网权限保持关闭",
                status_code=409,
            )
        values = {
            "project_read": bool(updates.get("projectRead", current["projectRead"])),
            "project_write": bool(updates.get("projectWrite", current["projectWrite"])),
            "command_execution": bool(updates.get("commandExecution", current["commandExecution"])),
            "external_network": requested_external,
            "network_policy": str(
                updates.get(
                    "networkPolicy",
                    "allow" if requested_external else "deny",
                )
            ),
        }
        if values["network_policy"] not in {"ask", "deny", "allow"}:
            raise RakazoRuntimeError("networkPolicy 仅支持 ask/deny/allow", status_code=400)
        # 外部网络为 deny 时不能留下一个实际放行的布尔状态。
        if values["network_policy"] == "deny":
            values["external_network"] = False
        if values["network_policy"] == "allow":
            values["external_network"] = True

        # 先让上游审批规则和 Docker 网络达到目标状态，再落库。这样外部操作
        # 失败时前端不会看到一个实际并未生效的“已开启”开关。
        if values["command_execution"] != bool(current["commandExecution"]):
            await self._sync_shell_approval_rule(
                project_id=project_id,
                command_allowed=values["command_execution"],
            )
        if values["external_network"] != bool(current["externalNetwork"]):
            await self._reconfigure_project_network(
                project_id=project_id,
                bot_id=str(mapping["bot_id"]),
                allow_external=values["external_network"],
            )
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE rakazo_permissions SET
                   project_read = ?, project_write = ?, command_execution = ?,
                   external_network = ?, network_policy = ?, updated_at = ?
                   WHERE project_id = ?""",
                (
                    int(values["project_read"]),
                    int(values["project_write"]),
                    int(values["command_execution"]),
                    int(values["external_network"]),
                    values["network_policy"],
                    now,
                    project_id,
                ),
            )
        self.audit(project_id, "permissions.update", "success", values)
        return self.get_permissions(project_id)

    async def _sync_shell_approval_rule(self, *, project_id: str, command_allowed: bool) -> None:
        """把 Codebot 项目命令授权同步到 Rakazo 官方审批合同。

        固定实验版的审批规则作用域是当前本机账号/工作区，并没有 botId 字段。
        因此采用保守合并：只要任一 Codebot 项目尚未授权命令，所有 Bot 的
        ``shell`` 都继续逐次审批；只有全部现存项目都已授权时才移除该规则。
        这不会让一个项目的授权意外放开另一个项目。
        """
        with self._connect() as conn:
            other_denied = int(
                conn.execute(
                    """SELECT COUNT(*) FROM rakazo_permissions
                       WHERE project_id <> ? AND command_execution = 0""",
                    (project_id,),
                ).fetchone()[0]
            )
        require_approval = (not command_allowed) or other_denied > 0
        rules = await self.rpc("approvalRules.list", {})
        shell_rules = [
            item
            for item in (rules if isinstance(rules, list) else [])
            if isinstance(item, Mapping)
            and str(item.get("matchKind") or "") == "tool"
            and str(item.get("matchValue") or "").lower() == "shell"
        ]
        for rule in shell_rules:
            effect = str(rule.get("effect") or "")
            if (require_approval and effect == "require_approval") or (
                not require_approval and effect != "require_approval"
            ):
                continue
            rule_id = str(rule.get("id") or "")
            if rule_id:
                await self.rpc("approvalRules.remove", {"id": rule_id})
        if require_approval and not any(
            str(item.get("effect") or "") == "require_approval" for item in shell_rules
        ):
            await self.rpc(
                "approvalRules.set",
                {"effect": "require_approval", "matchKind": "tool", "matchValue": "shell"},
            )

    @staticmethod
    def _remote_resource_missing(exc: Exception) -> bool:
        """只把官方已知的资源不存在错误视为可自愈/幂等删除。

        当前固定提交的 ``bots.get`` 会把 IsolationError 包装成 HTTP 500，但
        消息仍稳定为 ``Resource not found``。不能把其它 500 当成资源丢失，
        否则数据库故障时会误建第二个 Bot。
        """
        message = str(exc or "").lower()
        return any(
            marker in message
            for marker in (
                "resource not found",
                "computer not found",
                "bot not found",
                "mcp server not found",
            )
        )

    async def _confirm_bot_missing(self, bot_id: str, exc: Exception) -> bool:
        """用 ``bots.list`` 复核被固定上游吞成 generic 500 的 IsolationError。"""
        if self._remote_resource_missing(exc):
            return True
        if "internal server error" not in str(exc or "").lower():
            return False
        try:
            bots = await self.rpc("bots.list", {})
        except Exception:
            return False
        if not isinstance(bots, list):
            return False
        return all(
            not isinstance(item, Mapping) or str(item.get("id") or "") != str(bot_id)
            for item in bots
        )

    async def _confirm_mcp_server_missing(self, server_id: str, exc: Exception) -> bool:
        """复核被上游包装成 generic 500 的 MCP Server 不存在错误。"""
        if self._remote_resource_missing(exc):
            return True
        if "internal server error" not in str(exc or "").lower():
            return False
        try:
            servers = await self.rpc("mcp.servers.list", {})
        except Exception:
            return False
        if not isinstance(servers, list):
            return False
        return all(
            not isinstance(item, Mapping) or str(item.get("id") or "") != str(server_id)
            for item in servers
        )

    @staticmethod
    def _token_digest(token: str) -> str:
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    def validate_project_token(self, project_id: str, token: str) -> bool:
        mapping = self.mapping_by_project_id(project_id)
        expected = str((mapping or {}).get("project_token_hash") or "")
        supplied = self._token_digest(token) if token else ""
        return bool(expected and supplied and secrets.compare_digest(expected, supplied))

    @staticmethod
    def _project_mcp_endpoint(project_id: str) -> str:
        # Rakazo 只允许非 TLS MCP 指向 localhost。Compose 为 api/worker 各启动
        # 一个共享网络命名空间的回环 sidecar，再由 sidecar 转发到宿主 Codebot。
        return f"http://127.0.0.1:8788/{project_id}"

    @staticmethod
    def _project_mcp_slug(project_id: str) -> str:
        """返回 Codebot 独占的项目 MCP slug，供重试和删除做精确清理。"""
        return f"codebot-project-{project_id[-12:]}"

    async def _remove_project_mcp_by_slug(self, project_id: str) -> List[str]:
        """删除同一项目以前失败重试遗留的 MCP Server。

        Rakazo 对 ``workspaceId/userId/slug`` 有唯一约束。Bot 自愈若在 MCP
        创建完成后、Codebot 映射提交前失败，旧映射无法保存这次新 ID；因此
        必须根据 Codebot 自己的确定性 slug 查找。这里只删除完全匹配的 slug，
        不会碰用户手工创建的其他 MCP。
        """
        servers = await self.rpc("mcp.servers.list", {})
        if not isinstance(servers, list):
            raise RakazoRuntimeError("Rakazo mcp.servers.list 返回结构无效")
        slug = self._project_mcp_slug(project_id)
        removed: List[str] = []
        for item in servers:
            if not isinstance(item, Mapping) or str(item.get("slug") or "") != slug:
                continue
            server_id = str(item.get("id") or "")
            if not server_id:
                raise RakazoRuntimeError("Rakazo 项目 MCP 缺少可验证标识")
            try:
                await self.rpc("mcp.servers.remove", {"id": server_id})
            except Exception as exc:
                if not await self._confirm_mcp_server_missing(server_id, exc):
                    raise
            removed.append(server_id)
        return removed

    @staticmethod
    def _is_sensitive_project_path(relative_path: Path) -> bool:
        lowered = [part.lower() for part in relative_path.parts]
        if any(part in {".ssh", ".aws", ".azure", ".kube", ".gnupg"} for part in lowered):
            return True
        name = relative_path.name.lower()
        return name in _SENSITIVE_PROJECT_NAMES or name.startswith(".env.") or relative_path.suffix.lower() in _SENSITIVE_PROJECT_SUFFIXES

    def resolve_project_path(self, project_id: str, raw_path: str, *, for_write: bool = False) -> tuple[Dict[str, Any], Path, Path]:
        """把 MCP 相对路径解析到项目根内，并拒绝符号链接/联接点逃逸。"""
        mapping = self.mapping_by_project_id(project_id)
        if not mapping:
            raise RakazoRuntimeError("Rakazo 项目映射不存在", status_code=404)
        relative = Path(str(raw_path or ".").replace("\\", "/"))
        if relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise RakazoRuntimeError("项目工具只接受不含 .. 的相对路径", status_code=400)
        if self._is_sensitive_project_path(relative):
            raise RakazoRuntimeError("该路径属于凭据或敏感配置，Rakazo 项目工具拒绝访问", status_code=403)
        root = Path(str(mapping["project_dir"])).resolve(strict=True)
        candidate = root.joinpath(relative)
        try:
            if for_write and not candidate.exists():
                resolved_parent = candidate.parent.resolve(strict=True)
                resolved = resolved_parent / candidate.name
            else:
                resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise RakazoRuntimeError(f"项目路径不存在或不可访问：{raw_path}", status_code=404) from exc
        if resolved != root and root not in resolved.parents:
            raise RakazoRuntimeError("项目路径解析后逃逸项目根目录", status_code=403)
        if candidate.is_symlink():
            raise RakazoRuntimeError("拒绝通过符号链接访问或写入项目文件", status_code=403)
        return mapping, root, resolved

    def project_tool_definitions(self, project_id: str) -> List[Dict[str, Any]]:
        permissions = self.get_permissions(project_id)
        tools: List[Dict[str, Any]] = []
        if permissions["projectRead"]:
            tools.extend(
                [
                    {
                        "name": "codebot_project_list",
                        "description": "列出 Codebot 明确绑定项目中的目录内容；只能使用相对路径，敏感配置不可见。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string", "default": "."}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}},
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "codebot_project_read",
                        "description": "读取 Codebot 明确绑定项目内不超过 2 MiB 的 UTF-8 文本文件。",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                            "additionalProperties": False,
                        },
                    },
                ]
            )
        if permissions["projectWrite"]:
            tools.append(
                {
                    "name": "codebot_project_write",
                    "description": "经用户项目级授权后，原子写入绑定项目内不超过 2 MiB 的 UTF-8 文本文件。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "content": {"type": "string", "maxLength": MAX_PROJECT_FILE_BYTES}},
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                }
            )
        return tools

    def call_project_tool(self, project_id: str, name: str, arguments: Mapping[str, Any]) -> Dict[str, Any]:
        permissions = self.get_permissions(project_id)
        if name == "codebot_project_list":
            if not permissions["projectRead"]:
                raise RakazoRuntimeError("项目读取权限未开启", status_code=403)
            _, root, target = self.resolve_project_path(project_id, str(arguments.get("path") or "."))
            if not target.is_dir():
                raise RakazoRuntimeError("目标不是目录", status_code=400)
            limit = max(1, min(int(arguments.get("limit") or 200), 500))
            entries: List[Dict[str, Any]] = []
            for item in sorted(target.iterdir(), key=lambda value: value.name.lower()):
                relative = item.relative_to(root)
                if self._is_sensitive_project_path(relative) or item.is_symlink():
                    continue
                entries.append({"path": relative.as_posix(), "type": "directory" if item.is_dir() else "file", "size": item.stat().st_size if item.is_file() else None})
                if len(entries) >= limit:
                    break
            result: Any = {"root": ".", "path": target.relative_to(root).as_posix() or ".", "entries": entries, "truncated": len(entries) >= limit}
        elif name == "codebot_project_read":
            if not permissions["projectRead"]:
                raise RakazoRuntimeError("项目读取权限未开启", status_code=403)
            _, root, target = self.resolve_project_path(project_id, str(arguments.get("path") or ""))
            if not target.is_file():
                raise RakazoRuntimeError("目标不是文件", status_code=400)
            if target.stat().st_size > MAX_PROJECT_FILE_BYTES:
                raise RakazoRuntimeError("项目文件超过 2 MiB 读取上限", status_code=413)
            try:
                content = target.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise RakazoRuntimeError("项目读取工具仅支持 UTF-8 文本文件", status_code=415) from exc
            result = {"path": target.relative_to(root).as_posix(), "content": content}
        elif name == "codebot_project_write":
            if not permissions["projectWrite"]:
                raise RakazoRuntimeError("项目写入权限未开启", status_code=403)
            content = str(arguments.get("content") or "")
            if len(content.encode("utf-8")) > MAX_PROJECT_FILE_BYTES:
                raise RakazoRuntimeError("项目写入内容超过 2 MiB 上限", status_code=413)
            _, root, target = self.resolve_project_path(project_id, str(arguments.get("path") or ""), for_write=True)
            if target.exists() and not target.is_file():
                raise RakazoRuntimeError("写入目标不是普通文件", status_code=400)
            temporary = target.with_name(f".{target.name}.codebot-rakazo-{secrets.token_hex(6)}.tmp")
            try:
                temporary.write_text(content, encoding="utf-8", newline="")
                temporary.replace(target)
            finally:
                # replace 成功后临时文件已不存在；失败时也不在用户项目中遗留内容副本。
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            result = {"path": target.relative_to(root).as_posix(), "bytes": len(content.encode("utf-8"))}
        else:
            raise RakazoRuntimeError("未知的 Rakazo 项目工具", status_code=404)
        self.audit(project_id, f"project.tool.{name}", "success", {"path": str(arguments.get("path") or ".")})
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False}

    def _rpc_headers(self) -> Dict[str, str]:
        token = self._read_session_token()
        if not token:
            raise RakazoRuntimeError("尚未配置 Rakazo 授权令牌", status_code=401)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": self._validated_api_url(),
            "User-Agent": f"Codebot/{app_config.version} RakazoAdapter/1",
        }

    @staticmethod
    def _validated_api_url() -> str:
        """即使用户手改 config.json，也不把 Rakazo 会话令牌发送到远端地址。"""
        raw = str(app_config.rakazo.api_url or "").strip().rstrip("/")
        parsed = urlparse(raw)
        if (
            parsed.scheme not in {"http", "https"}
            or (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise RakazoRuntimeError(
                "Rakazo API 必须是无凭据、无路径的本机回环 http(s) 地址",
                status_code=400,
            )
        try:
            port = parsed.port
        except ValueError as exc:
            raise RakazoRuntimeError("Rakazo API 端口无效", status_code=400) from exc
        if port is not None and not 1 <= port <= 65535:
            raise RakazoRuntimeError("Rakazo API 端口超出范围", status_code=400)
        return raw

    def _rpc_url(self, procedure: str) -> str:
        name = str(procedure or "").strip("./").replace(".", "/")
        if not name or not re.fullmatch(r"[A-Za-z0-9_/-]+", name):
            raise RakazoRuntimeError("Rakazo oRPC procedure 无效", status_code=400)
        return f"{self._validated_api_url()}/rpc/{name}"

    async def rpc(self, procedure: str, input_data: Optional[Mapping[str, Any]] = None) -> Any:
        """调用官方 oRPC JSON 合同并限制重定向、响应大小和错误泄漏。"""
        timeout = httpx.Timeout(60.0, connect=10.0, read=60.0, write=20.0, pool=10.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    self._rpc_url(procedure),
                    headers=self._rpc_headers(),
                    json={"json": dict(input_data or {})},
                )
        except httpx.HTTPError as exc:
            self._last_error = _safe_error(exc)
            raise RakazoRuntimeError(f"Rakazo 连接失败：{exc}") from exc
        if len(response.content) > MAX_RPC_RESPONSE_BYTES:
            raise RakazoRuntimeError("Rakazo oRPC 响应超过 16 MiB 安全上限")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RakazoRuntimeError(f"Rakazo oRPC 返回非 JSON（HTTP {response.status_code}）") from exc
        if response.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
            # Rakazo 上游错误可能携带请求头、凭据片段或内部路径，对外抛出前必须统一脱敏。
            safe_detail = _rpc_error_message(payload, response.status_code)
            raise RakazoRuntimeError(f"Rakazo {procedure} 失败：{safe_detail}", status_code=response.status_code)
        self._last_error = ""
        if isinstance(payload, dict) and "json" in payload:
            return payload["json"]
        return payload

    async def _public_health(self) -> Dict[str, Any]:
        """读取无需账号的官方 /health，并保留镜像注入的不可变 revision。"""
        timeout = httpx.Timeout(5.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(f"{self._validated_api_url()}/health")
        if response.status_code >= 400:
            raise RakazoRuntimeError(f"Rakazo 公开健康检查失败（HTTP {response.status_code}）")
        if len(response.content) > 1024 * 1024:
            raise RakazoRuntimeError("Rakazo 公开健康响应超过 1 MiB 安全上限")
        data = response.json()
        return data if isinstance(data, dict) else {"ok": True}

    async def health(self) -> Dict[str, Any]:
        """合并官方 oRPC 版本合同与公开镜像 revision 健康证据。

        未授权阶段 oRPC 会返回 401，但公开 ``/health`` 已包含 ``GIT_SHA``。
        固定实验版可以据此完成启动握手，随后再创建本机账号；这也避免了旧实现
        “必须先授权才能启动、必须先启动才能授权”的循环门禁。
        """
        public: Dict[str, Any] = {}
        try:
            public = await self._public_health()
        except Exception:
            public = {}
        try:
            data = await self.rpc("health", {})
            rpc_health = data if isinstance(data, dict) else {"ok": True, "value": data}
            return {**public, **rpc_health}
        except RakazoRuntimeError as rpc_error:
            if public and bool(public.get("ok", True)):
                return public
            raise rpc_error

    @staticmethod
    def _normalized_version(value: Any) -> str:
        version = str(value or "").strip()
        return version if version.startswith("v") else f"v{version}" if version else ""

    def _compatibility_entries(self) -> List[Dict[str, Any]]:
        manifest = self.compatibility_manifest()
        entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
        return [dict(item) for item in entries if isinstance(item, dict)]

    def compatibility_entry(
        self,
        version: Any = "",
        *,
        channel: Optional[str] = None,
        revision: Any = "",
    ) -> Optional[Dict[str, Any]]:
        """按通道、版本和不可变提交读取适配清单。

        Rakazo 当前 ``main`` 与旧 beta 的公开 health 都可能返回 ``0.1.0``，只
        比版本会把两个不同源码误认为同一运行时。实验通道必须优先匹配官方
        镜像公开的完整 Git revision；版本只作为合同版本的第二层核对。
        """
        normalized = self._normalized_version(version)
        revision_value = str(revision or "").strip().lower()
        selected_channel = str(channel or app_config.rakazo.release_channel or "stable")
        entries = self._compatibility_entries()
        if revision_value:
            for item in entries:
                if (
                    str(item.get("channel") or "") == selected_channel
                    and str(item.get("sourceRevision") or "").strip().lower() == revision_value
                ):
                    return item
        candidates = [
            item
            for item in entries
            if normalized and self._normalized_version(item.get("rakazoVersion")) == normalized
        ]
        for item in candidates:
            if str(item.get("channel") or "") == selected_channel:
                return item
        if candidates:
            return candidates[0]

        # 兼容早期清单的 versions map；新实验通道不使用此不含镜像身份的格式。
        manifest = self.compatibility_manifest()
        versions = manifest.get("versions") if isinstance(manifest.get("versions"), dict) else {}
        mapped = versions.get(normalized)
        if isinstance(mapped, dict):
            return {"rakazoVersion": normalized, **mapped}
        return None

    def assert_runtime_compatible(self, health: Mapping[str, Any]) -> Dict[str, Any]:
        """验证当前 Rakazo 版本与 Codebot 外置适配器合同，不匹配即拒绝工作。"""
        revision = str(health.get("revision") or "").strip().lower()
        version = self._normalized_version(health.get("version"))
        entry = self.compatibility_entry(version, revision=revision)
        if not entry or not bool(entry.get("runtimeCompatible")):
            raise RakazoRuntimeError(
                f"Rakazo {version or revision or 'unknown'} 不在 Codebot 当前通道的已验证兼容清单中；拒绝创建或恢复 Bot",
                status_code=409,
            )
        version = version or self._normalized_version(entry.get("rakazoVersion"))
        if not version:
            raise RakazoRuntimeError("Rakazo health 未返回可验证的版本或提交身份", status_code=409)
        channel = str(entry.get("channel") or "stable")
        expected_revision = str(entry.get("sourceRevision") or "").strip().lower()
        if channel == "experimental":
            if not app_config.rakazo.experimental_runtime_enabled:
                raise RakazoRuntimeError("固定实验运行时尚未由用户显式启用", status_code=409)
            if not revision or revision != expected_revision:
                raise RakazoRuntimeError(
                    f"Rakazo 实验运行时提交不匹配：expected={expected_revision or 'missing'}, actual={revision or 'missing'}",
                    status_code=409,
                )
        declared_contract = self._normalized_version(entry.get("contractVersion"))
        reported_contract = self._normalized_version(health.get("contractVersion") or version or declared_contract)
        if not declared_contract or declared_contract != reported_contract:
            raise RakazoRuntimeError(
                f"Rakazo 合同版本不匹配：expected={declared_contract or 'missing'}, actual={reported_contract or 'missing'}",
                status_code=409,
            )
        minimum_adapter = str(entry.get("minimumAdapterVersion") or "").strip()
        if minimum_adapter and minimum_adapter != RAKAZO_ADAPTER_VERSION:
            raise RakazoRuntimeError(
                f"Rakazo 需要 Codebot Adapter {minimum_adapter}，当前为 {RAKAZO_ADAPTER_VERSION}",
                status_code=409,
            )
        return {
            "compatible": True,
            "rakazoVersion": version,
            "contractVersion": declared_contract,
            "adapterVersion": RAKAZO_ADAPTER_VERSION,
            "channel": channel,
            "productionReady": bool(entry.get("productionReady")),
            "sourceRevision": expected_revision,
            "appImage": str(entry.get("appImage") or ""),
            "computerImage": str(entry.get("computerImage") or ""),
        }

    def runtime_start_policy(self) -> Dict[str, Any]:
        """返回当前通道是否存在可启动的不可变官方运行时。"""
        entries = self._compatibility_entries()
        selected_channel = str(app_config.rakazo.release_channel or "stable")
        experimental_entries = [
            item
            for item in entries
            if str(item.get("channel") or "") == "experimental"
            and bool(item.get("runtimeCompatible"))
            and bool(item.get("installable"))
        ]
        if selected_channel == "experimental" and not app_config.rakazo.experimental_runtime_enabled:
            return {
                "allowed": False,
                "channel": selected_channel,
                "approvedVersions": [],
                "approvedRevisions": [],
                "experimentalAvailable": bool(experimental_entries),
                "reason": "请点击“安装并启动固定实验版”确认实验风险；Codebot 不会自动跟踪 main/edge。",
                "entry": None,
            }
        approved_entries = [
            item
            for item in entries
            if bool(item.get("runtimeCompatible"))
            and bool(item.get("installable"))
            and str(item.get("channel") or "") == selected_channel
        ]
        approved = sorted({
            self._normalized_version(item.get("rakazoVersion"))
            for item in approved_entries
        } - {""})
        revisions = sorted({
            str(item.get("sourceRevision") or "").strip()
            for item in approved_entries
        } - {""})
        if approved_entries:
            return {
                "allowed": True,
                "channel": selected_channel,
                "approvedVersions": approved,
                "approvedRevisions": revisions,
                "experimentalAvailable": bool(experimental_entries),
                "reason": "",
                "entry": approved_entries[0],
            }
        rejected_reason = next(
            (
                str(item.get("reason") or "")
                for item in entries
                if str(item.get("channel") or "") == selected_channel
                and str(item.get("reason") or "").strip()
            ),
            "",
        )
        return {
            "allowed": False,
            "channel": selected_channel,
            "approvedVersions": [],
            "approvedRevisions": [],
            "experimentalAvailable": bool(experimental_entries),
            "reason": rejected_reason or f"当前兼容清单没有获准启动的 Rakazo {selected_channel} 版本。",
            "entry": None,
        }

    def _docker_executable(self) -> Optional[str]:
        """发现 Docker CLI，包括 Docker Desktop 的用户自选安装目录。

        Docker Desktop 安装完成后，已运行的 Codebot 后端不会自动获得新的 PATH；
        自选目录也不会位于系统 PATH。Windows 上必须读取 Docker 官方卸载注册表
        的 InstallLocation，不能把 Electron 已就绪状态误判为后端不可用。
        """
        candidates: List[Path] = []
        configured = str(os.environ.get("CODEBOT_DOCKER_PATH") or "").strip()
        if configured:
            path = Path(configured).expanduser()
            candidates.append(path if path.suffix.lower() == ".exe" else path / "docker.exe")
        discovered = shutil.which("docker")
        if discovered:
            candidates.append(Path(discovered))
        if os.name == "nt":
            try:
                import winreg

                registry_paths = (
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop",
                )
                for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                    for key_name in registry_paths:
                        try:
                            with winreg.OpenKey(hive, key_name) as key:
                                install_location = str(winreg.QueryValueEx(key, "InstallLocation")[0] or "").strip()
                        except OSError:
                            continue
                        if install_location:
                            root = Path(install_location)
                            candidates.extend((root / "resources" / "bin" / "docker.exe", root / "docker.exe"))
            except (ImportError, OSError):
                pass
            for variable in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
                base = str(os.environ.get(variable) or "").strip()
                if base:
                    candidates.append(Path(base) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe")
        seen: set[str] = set()
        for candidate in candidates:
            value = str(candidate)
            normalized = os.path.normcase(os.path.abspath(value))
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.is_file():
                return str(candidate.resolve())
        return None

    def _docker_environment(self) -> Dict[str, str]:
        """让 Docker CLI 能找到同目录的 compose 插件和 credential helper。"""
        environment = dict(os.environ)
        docker = self._docker_executable()
        if docker:
            docker_dir = str(Path(docker).parent)
            current = str(environment.get("PATH") or "")
            path_items = [item for item in current.split(os.pathsep) if item]
            if os.path.normcase(docker_dir) not in {os.path.normcase(item) for item in path_items}:
                environment["PATH"] = os.pathsep.join([docker_dir, *path_items])
        return environment

    @staticmethod
    def _sha256_file(path: Path) -> str:
        """计算 Compose 摘要；兼容清单以 LF 原文为基准。

        Windows CI 检出或打包链路可能把 LF 转成 CRLF，语义内容并未改变；
        归一化后再计算，避免把换行符差异误判为资源被篡改。
        """
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            pending = b""
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                data = pending + chunk
                if data.endswith(b"\r"):
                    # 尾字节可能是被块边界截断的 \r\n 前半，留到下一块合并判断。
                    pending = b"\r"
                    data = data[:-1]
                else:
                    pending = b""
                digest.update(data.replace(b"\r\n", b"\n"))
            digest.update(pending)
        return digest.hexdigest()

    @staticmethod
    def _rakazo_resource_path(name: str) -> Path:
        safe_name = Path(str(name or "")).name
        if not safe_name or safe_name != str(name or ""):
            raise RakazoRuntimeError("Rakazo 运行时资源名无效", status_code=500)
        resources = str(os.environ.get("CODEBOT_RESOURCES_DIR") or "").strip()
        candidates: List[Path] = []
        if resources:
            candidates.append(Path(resources) / "integrations" / "rakazo" / safe_name)
        candidates.extend((
            Path(__file__).resolve().parents[2] / "integrations" / "rakazo" / safe_name,
            settings.BASE_DIR / "integrations" / "rakazo" / safe_name,
        ))
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise RakazoRuntimeError(f"缺少随 Codebot 发布的 Rakazo 资源：{safe_name}", status_code=500)

    @staticmethod
    def _managed_runtime_dir() -> Path:
        return settings.DATA_DIR / "rakazo-runtime"

    @classmethod
    def _managed_compose_path(cls) -> Path:
        return cls._managed_runtime_dir() / "docker-compose.images.yml"

    @classmethod
    def _runtime_secret_store_path(cls) -> Path:
        return cls._managed_runtime_dir() / ("runtime-secrets.dpapi" if os.name == "nt" else "runtime-secrets.local")

    def _runtime_secrets(self) -> Dict[str, str]:
        """读取或创建稳定运行时密钥；Windows 持久副本必须经过 DPAPI。"""
        runtime_dir = self._managed_runtime_dir()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        path = self._runtime_secret_store_path()
        if path.is_file():
            try:
                payload = json.loads(_unprotect_runtime_secret_payload(path.read_bytes()).decode("utf-8"))
            except Exception as exc:
                raise RakazoRuntimeError(f"无法解密 Rakazo 本机运行时密钥：{exc}", status_code=500) from exc
            if not isinstance(payload, dict):
                raise RakazoRuntimeError("Rakazo 本机运行时密钥格式无效", status_code=500)
            values = {name: str(payload.get(name) or "") for name in _RUNTIME_SECRET_NAMES}
            if any(len(value) < 32 for value in values.values()) or len(set(values.values())) != len(values):
                raise RakazoRuntimeError("Rakazo 本机运行时密钥不完整或发生复用", status_code=500)
            return values

        values = {name: secrets.token_urlsafe(48) for name in _RUNTIME_SECRET_NAMES}
        encoded = _protect_runtime_secret_payload(json.dumps(values, separators=(",", ":")).encode("utf-8"))
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            temporary.write_bytes(encoded)
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return values

    async def prepare_experimental_runtime(self) -> Dict[str, Any]:
        """安装固定提交实验版的受管 Compose，不下载或信任漂移标签。"""
        docker = await self.docker_status()
        if not docker.get("ready"):
            raise RakazoRuntimeError(str(docker.get("message") or "Docker Engine 尚未就绪"), status_code=409)
        entries = [
            item
            for item in self._compatibility_entries()
            if str(item.get("channel") or "") == "experimental"
            and bool(item.get("runtimeCompatible"))
            and bool(item.get("installable"))
        ]
        if not entries:
            raise RakazoRuntimeError("当前 Codebot 版本没有获准安装的固定 Rakazo 实验运行时", status_code=409)
        entry = entries[0]
        allowed_platforms = {str(item) for item in entry.get("architectures") or []}
        platform = str(docker.get("platform") or "")
        if allowed_platforms and platform and platform not in allowed_platforms:
            raise RakazoRuntimeError(f"固定 Rakazo 实验镜像不支持当前 Docker 平台 {platform}", status_code=409)
        source = self._rakazo_resource_path(str(entry.get("composeResource") or ""))
        expected_hash = str(entry.get("composeSha256") or "").lower()
        actual_hash = self._sha256_file(source)
        if not expected_hash or actual_hash != expected_hash:
            raise RakazoRuntimeError("随包 Rakazo 官方 Compose 摘要校验失败", status_code=500)
        destination = self._managed_compose_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(6)}.tmp")
        try:
            shutil.copyfile(source, temporary)
            if self._sha256_file(temporary) != expected_hash:
                raise RakazoRuntimeError("复制 Rakazo 官方 Compose 后摘要不一致", status_code=500)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        # 先创建 DPAPI 密钥，再保存不含任何密钥的普通配置。
        self._runtime_secrets()
        stale_env = destination.parent / ".env"
        try:
            stale_env.unlink(missing_ok=True)
        except OSError:
            pass
        app_config.rakazo.enabled = True
        app_config.rakazo.release_channel = "experimental"
        app_config.rakazo.experimental_runtime_enabled = True
        app_config.rakazo.compose_file = str(destination.resolve())
        save_config(app_config)
        self.audit(None, "runtime.experimental.prepare", "success", {
            "sourceRevision": entry.get("sourceRevision"),
            "appImage": entry.get("appImage"),
            "computerImage": entry.get("computerImage"),
        })
        return {
            "prepared": True,
            "channel": "experimental",
            "sourceRevision": str(entry.get("sourceRevision") or ""),
            "appImage": str(entry.get("appImage") or ""),
            "computerImage": str(entry.get("computerImage") or ""),
            "composeFile": str(destination.resolve()),
            "productionReady": False,
        }

    async def _run_process(
        self,
        argv: List[str],
        *,
        timeout: int = 60,
        environment: Optional[Mapping[str, str]] = None,
    ) -> subprocess.CompletedProcess[str]:
        """使用固定 argv 执行 Docker；不经 shell，避免 Compose 路径注入。"""
        def run() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=dict(environment) if environment is not None else None,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

        return await asyncio.to_thread(run)

    async def docker_status(self) -> Dict[str, Any]:
        docker = self._docker_executable()
        if not docker:
            return {
                "installed": False,
                "ready": False,
                "oneClickInstallAvailable": os.name == "nt",
                "message": "未检测到 Docker CLI；可在 Codebot 桌面端选择目录并一键安装 Docker Desktop",
            }
        try:
            result = await self._run_process(
                [docker, "version", "--format", "{{json .Server}}"],
                timeout=15,
                environment=self._docker_environment(),
            )
        except Exception as exc:
            return {"installed": True, "ready": False, "message": _safe_error(exc)}
        if result.returncode != 0:
            return {"installed": True, "ready": False, "message": _safe_error(result.stderr or result.stdout)}
        version = ""
        platform = ""
        try:
            payload = json.loads(result.stdout.strip())
            if isinstance(payload, dict):
                version = str(payload.get("Version") or "")
                os_type = str(payload.get("Os") or payload.get("OSType") or "").lower()
                architecture = str(payload.get("Arch") or payload.get("Architecture") or "").lower()
                if os_type and architecture:
                    platform = f"{os_type}/{architecture}"
        except ValueError:
            version = result.stdout.strip()[:100]
        if not platform:
            info = await self._run_process(
                [docker, "info", "--format", "{{.OSType}}/{{.Architecture}}"],
                timeout=15,
                environment=self._docker_environment(),
            )
            if info.returncode == 0:
                platform = info.stdout.strip().lower()
        return {
            "installed": True,
            "ready": True,
            "serverVersion": version,
            "platform": platform,
            "dockerExecutable": docker,
        }

    @staticmethod
    def _computer_network_name(bot_id: str) -> str:
        """复现固定 Rakazo supervisor 的每 Bot 加盐网络名合同。"""
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "", str(bot_id or ""))[:40] or "box"
        digest = hashlib.sha256(str(bot_id or "").encode("utf-8")).hexdigest()[:32]
        return f"rakazo-computer-{safe[:32]}-{digest}"

    async def _managed_computer_container_ids(self, bot_id: str) -> List[str]:
        """按两个官方标签查找并再次 inspect 校验项目 Computer。"""
        docker = self._docker_executable()
        if not docker:
            raise RakazoRuntimeError("未检测到 Docker CLI", status_code=409)
        listed = await self._run_process(
            [
                docker,
                "ps",
                "-aq",
                "--filter",
                "label=rakazo.managed=true",
                "--filter",
                f"label=rakazo.botId={bot_id}",
            ],
            timeout=30,
            environment=self._docker_environment(),
        )
        if listed.returncode != 0:
            raise RakazoRuntimeError(f"读取项目 Computer 容器失败：{listed.stderr or listed.stdout}")
        result: List[str] = []
        for container_id in [item.strip() for item in listed.stdout.splitlines() if item.strip()]:
            inspected = await self._run_process(
                [docker, "inspect", container_id],
                timeout=30,
                environment=self._docker_environment(),
            )
            if inspected.returncode != 0:
                continue
            try:
                payload = json.loads(inspected.stdout)
                info = payload[0] if isinstance(payload, list) and payload else {}
            except ValueError:
                continue
            labels = ((info.get("Config") or {}).get("Labels") or {}) if isinstance(info, Mapping) else {}
            name = str(info.get("Name") or "").lstrip("/") if isinstance(info, Mapping) else ""
            if (
                isinstance(labels, Mapping)
                and str(labels.get("rakazo.managed") or "") == "true"
                and str(labels.get("rakazo.botId") or "") == bot_id
                and name == f"rakazo-bot-{re.sub(r'[^a-zA-Z0-9_.-]', '', bot_id)[:40] or 'box'}"
            ):
                result.append(container_id)
        return result

    async def _remove_project_computer_runtime(self, bot_id: str) -> Dict[str, Any]:
        """只删除精确 botId 的 Computer 容器与加盐私有网络。"""
        docker = self._docker_executable()
        if not docker:
            raise RakazoRuntimeError("未检测到 Docker CLI", status_code=409)
        removed_containers: List[str] = []
        for container_id in await self._managed_computer_container_ids(bot_id):
            removed = await self._run_process(
                [docker, "rm", "-f", container_id],
                timeout=60,
                environment=self._docker_environment(),
            )
            if removed.returncode != 0 and "no such container" not in (removed.stderr or "").lower():
                raise RakazoRuntimeError(f"删除项目 Computer 容器失败：{removed.stderr or removed.stdout}")
            removed_containers.append(container_id)

        network_name = self._computer_network_name(bot_id)
        inspected = await self._run_process(
            [docker, "network", "inspect", network_name],
            timeout=30,
            environment=self._docker_environment(),
        )
        network_removed = False
        if inspected.returncode == 0:
            try:
                networks = json.loads(inspected.stdout)
                network = networks[0] if isinstance(networks, list) and networks else {}
                attached = list(((network.get("Containers") or {}) if isinstance(network, Mapping) else {}).keys())
            except ValueError as exc:
                raise RakazoRuntimeError("项目 Computer 网络 inspect 返回无效 JSON") from exc
            # 名称包含完整 botId 摘要，且由固定 supervisor 合同生成；仅从这一
            # 精确网络断开端点，绝不枚举或删除 Compose 的共享 app/internal 网络。
            for container_id in attached:
                await self._run_process(
                    [docker, "network", "disconnect", "-f", network_name, container_id],
                    timeout=30,
                    environment=self._docker_environment(),
                )
            removed = await self._run_process(
                [docker, "network", "rm", network_name],
                timeout=30,
                environment=self._docker_environment(),
            )
            remove_error = (removed.stderr or "").lower()
            network_missing = "no such network" in remove_error or (
                "network " in remove_error and " not found" in remove_error
            )
            if removed.returncode != 0 and not network_missing:
                raise RakazoRuntimeError(f"删除项目 Computer 私有网络失败：{removed.stderr or removed.stdout}")
            network_removed = True
        else:
            inspect_error = (inspected.stderr or "").lower()
            network_missing = "no such network" in inspect_error or (
                "network " in inspect_error and " not found" in inspect_error
            )
            if not network_missing:
                raise RakazoRuntimeError(f"读取项目 Computer 私有网络失败：{inspected.stderr or inspected.stdout}")
        return {
            "computerContainers": removed_containers,
            "computerNetwork": network_name if network_removed else "",
        }

    async def _compose_service_container_id(self, service: str) -> str:
        docker = self._docker_executable()
        if not docker:
            raise RakazoRuntimeError("未检测到 Docker CLI", status_code=409)
        project_name = str(app_config.rakazo.docker_project_name or "")
        if not _COMPOSE_PROJECT_RE.fullmatch(project_name):
            raise RakazoRuntimeError("Rakazo Docker 项目名无效", status_code=400)
        result = await self._run_process(
            [
                docker,
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--filter",
                f"label=com.docker.compose.service={service}",
            ],
            timeout=30,
            environment=self._docker_environment(),
        )
        ids = [item.strip() for item in result.stdout.splitlines() if item.strip()]
        if result.returncode != 0 or len(ids) != 1:
            raise RakazoRuntimeError(f"无法唯一定位 Rakazo {service} 容器")
        return ids[0]

    async def _project_home_operation(
        self,
        action: str,
        source_bot_id: str,
        target_bot_id: str = "",
    ) -> bool:
        """在共享 appdata 卷内只操作 ``homes/<精确 botId>``。

        不把卷挂到任意 Agent Computer，也不向 shell 拼接路径；固定 Node 脚本
        对标识和 resolve 后父目录做双重校验。``copy`` 用于失效映射自愈时保留
        旧 Computer 文件，``delete`` 只在项目明确删除或迁移成功后执行。
        """
        if action not in {"copy", "delete"}:
            raise RakazoRuntimeError("不支持的 Rakazo home 操作", status_code=400)
        docker = self._docker_executable()
        if not docker:
            raise RakazoRuntimeError("未检测到 Docker CLI", status_code=409)
        api_container = await self._compose_service_container_id("api")
        script = """
const fs = require('node:fs');
const path = require('node:path');
const [action, sourceId, targetId = ''] = process.argv.slice(1);
const valid = (value) => /^[A-Za-z0-9_.-]{1,128}$/.test(value || '');
if (!valid(sourceId) || (action === 'copy' && !valid(targetId))) process.exit(41);
const root = path.resolve(process.env.DATA_DIR || '/app/data', 'homes');
const checked = (id) => {
  const value = path.resolve(root, id);
  if (path.dirname(value) !== root) process.exit(42);
  return value;
};
const source = checked(sourceId);
if (!fs.existsSync(source)) process.exit(0);
if (action === 'delete') fs.rmSync(source, { recursive: true, force: true });
if (action === 'copy') {
  const target = checked(targetId);
  const runtimeLocks = new Set(['SingletonLock', 'SingletonCookie', 'SingletonSocket']);
  const copyTree = (from, to) => {
    const stat = fs.lstatSync(from);
    if (stat.isDirectory()) {
      fs.mkdirSync(to, { recursive: true });
      for (const name of fs.readdirSync(from)) {
        if (runtimeLocks.has(name)) continue;
        copyTree(path.join(from, name), path.join(to, name));
      }
      return;
    }
    if (fs.existsSync(to)) return;
    if (stat.isSymbolicLink()) {
      fs.symlinkSync(fs.readlinkSync(from), to);
      return;
    }
    if (stat.isFile()) fs.copyFileSync(from, to, fs.constants.COPYFILE_EXCL);
  };
  copyTree(source, target);
}
""".strip()
        result = await self._run_process(
            [docker, "exec", api_container, "node", "-e", script, action, source_bot_id, target_bot_id],
            timeout=120,
            environment=self._docker_environment(),
        )
        if result.returncode != 0:
            raise RakazoRuntimeError(
                f"Rakazo 项目 Computer home {action} 失败（退出码 {result.returncode}）："
                f"{_safe_error(result.stderr or result.stdout)}"
            )
        return True

    async def _ensure_project_network(self, bot_id: str, *, allow_external: bool) -> str:
        """在 Computer 启动前创建外网放行或 ``--internal`` 的精确私有网络。"""
        if not self._is_managed_compose():
            if allow_external:
                raise RakazoRuntimeError("非受管 Rakazo 不能可靠控制 Computer 外网", status_code=409)
            return ""
        docker = self._docker_executable()
        if not docker:
            raise RakazoRuntimeError("未检测到 Docker CLI", status_code=409)
        network_name = self._computer_network_name(bot_id)
        args = [docker, "network", "create", "--driver", "bridge"]
        if not allow_external:
            args.append("--internal")
        args.extend(["--label", f"codebot.rakazo.botId={bot_id}", network_name])
        created = await self._run_process(args, timeout=30, environment=self._docker_environment())
        if created.returncode != 0 and "already exists" not in (created.stderr or "").lower():
            raise RakazoRuntimeError(f"创建项目 Computer 私有网络失败：{created.stderr or created.stdout}")
        return network_name

    async def _project_network_allows_external(self, bot_id: str) -> Optional[bool]:
        docker = self._docker_executable()
        if not docker:
            return None
        network_name = self._computer_network_name(bot_id)
        result = await self._run_process(
            [docker, "network", "inspect", network_name],
            timeout=30,
            environment=self._docker_environment(),
        )
        if result.returncode != 0:
            return None
        try:
            payload = json.loads(result.stdout)
            info = payload[0] if isinstance(payload, list) and payload else {}
            return not bool(info.get("Internal")) if isinstance(info, Mapping) else None
        except ValueError:
            return None

    async def _reconfigure_project_network(
        self,
        *,
        project_id: str,
        bot_id: str,
        allow_external: bool,
    ) -> None:
        """重建单个 Bot 的 Computer 网络，保留专属 home 数据。"""
        if project_id in self._active_runs:
            raise RakazoRuntimeError("Rakazo 正在执行任务，不能切换 Computer 外网", status_code=409)
        actual = await self._project_network_allows_external(bot_id)
        if actual is allow_external:
            return
        try:
            await self.rpc("computer.stop", {"botId": bot_id})
        except Exception as exc:
            if not await self._confirm_bot_missing(bot_id, exc):
                raise
        await self._remove_project_computer_runtime(bot_id)
        await self._ensure_project_network(bot_id, allow_external=allow_external)
        booted = await self.rpc("computer.boot", {"botId": bot_id})
        if not isinstance(booted, Mapping):
            raise RakazoRuntimeError("Rakazo 未确认 Computer 网络重建结果")
        self.audit(
            project_id,
            "computer.network",
            "success",
            {"externalNetwork": allow_external, "computerId": str(booted.get("id") or "")},
        )

    async def managed_runtime_is_running(self) -> bool:
        """只读判断受管 Rakazo 是否已经在运行。

        后端进程每次启动都会生成新的内部采样令牌。若用户上次退出 Codebot
        时保留了容器，仅检查 Rakazo API 健康会漏掉 model-adapter 仍持有旧
        令牌的问题。这里不启动任何已停止的容器，只查 Compose 项目下正在
        运行的 model-adapter，供启动阶段决定是否执行一次 ``compose up -d``
        来收敛新令牌和最新模型目录。
        """
        if not self._is_managed_compose():
            return False
        docker = self._docker_executable()
        if not docker:
            return False
        project_name = str(app_config.rakazo.docker_project_name or "")
        if not _COMPOSE_PROJECT_RE.fullmatch(project_name):
            return False
        result = await self._run_process(
            [
                docker,
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--filter",
                "label=com.docker.compose.service=model-adapter",
                "--format",
                "{{.ID}}",
            ],
            timeout=30,
            environment=self._docker_environment(),
        )
        return bool(result.returncode == 0 and result.stdout.strip())

    def _compose_argv(self, action: str, *services: str) -> List[str]:
        docker = self._docker_executable()
        if not docker:
            raise RakazoRuntimeError("未检测到 Docker CLI；请在 Rakazo 设置中一键安装并启动 Docker Desktop", status_code=409)
        compose_file = Path(str(app_config.rakazo.compose_file or "")).expanduser()
        if not compose_file.is_absolute() or not compose_file.is_file():
            raise RakazoRuntimeError("尚未配置有效的 Rakazo 官方 Compose 文件", status_code=409)
        project_name = str(app_config.rakazo.docker_project_name or "")
        if not _COMPOSE_PROJECT_RE.fullmatch(project_name):
            raise RakazoRuntimeError("Rakazo Docker 项目名无效", status_code=400)
        overlay = self._compose_overlay_path()
        if not overlay.is_file():
            raise RakazoRuntimeError("缺少 Codebot Rakazo 私有模型适配 Compose 覆盖文件", status_code=409)
        argv = [
            docker,
            "compose",
            "-p",
            project_name,
            "--project-directory",
            str(compose_file.parent),
            "-f",
            str(compose_file),
            "-f",
            str(overlay),
        ]
        if action == "start":
            argv.extend(["up", "-d"])
        elif action == "stop":
            # 只停止，不删除 volume、数据库或网络，保证可恢复。
            argv.append("stop")
        elif action == "restart":
            # ``docker compose restart`` 不会应用新的随机采样令牌或模型目录；
            # 使用不删除 volume 的 recreate，确保 api/worker/adapter 读取新环境。
            argv.extend(["up", "-d", "--force-recreate"])
        else:
            raise RakazoRuntimeError("不支持的 Rakazo Docker 操作", status_code=400)
        argv.extend(str(item) for item in services if item)
        return argv

    @staticmethod
    def _compose_overlay_path() -> Path:
        resources = str(os.environ.get("CODEBOT_RESOURCES_DIR") or "").strip()
        if resources:
            packaged = Path(resources) / "integrations" / "rakazo" / "docker-compose.codebot.override.yml"
            if packaged.is_file():
                return packaged
        # ``CODEBOT_DATA_DIR`` 会把 settings.BASE_DIR 指向用户可写数据目录；
        # 源码资源不能随之漂移。开发/测试环境固定从仓库根解析。
        source = Path(__file__).resolve().parents[2] / "integrations" / "rakazo" / "docker-compose.codebot.override.yml"
        if source.is_file():
            return source
        return settings.BASE_DIR / "integrations" / "rakazo" / "docker-compose.codebot.override.yml"

    @staticmethod
    def _adapter_context_path() -> Path:
        resources = str(os.environ.get("CODEBOT_RESOURCES_DIR") or "").strip()
        if resources:
            packaged = Path(resources) / "integrations" / "rakazo-adapter"
            if packaged.is_dir():
                return packaged
        source = Path(__file__).resolve().parents[2] / "integrations" / "rakazo-adapter"
        if source.is_dir():
            return source
        return settings.BASE_DIR / "integrations" / "rakazo-adapter"

    def _runtime_entry_for_action(self, action: str) -> Dict[str, Any]:
        policy = self.runtime_start_policy()
        entry = policy.get("entry") if isinstance(policy.get("entry"), dict) else None
        if entry:
            return dict(entry)
        if action == "stop":
            # stop 只需要让 Compose 完成变量插值。即使用户把通道改回 stable，
            # 也只能复用兼容清单里的固定摘要，绝不能退回漂移的 edge。
            for item in self._compatibility_entries():
                if item.get("appImage") and item.get("computerImage"):
                    return item
        raise RakazoRuntimeError(str(policy.get("reason") or "没有可用的 Rakazo 运行时条目"), status_code=409)

    def _validate_compose_for_entry(self, entry: Mapping[str, Any]) -> Dict[str, Any]:
        compose_file = Path(str(app_config.rakazo.compose_file or "")).expanduser()
        expected_hash = str(entry.get("composeSha256") or "").strip().lower()
        if not compose_file.is_absolute() or not compose_file.is_file():
            raise RakazoRuntimeError("尚未安装受管 Rakazo 官方 Compose；请先安装固定实验版", status_code=409)
        actual_hash = self._sha256_file(compose_file)
        if expected_hash and actual_hash != expected_hash:
            raise RakazoRuntimeError(
                f"Rakazo Compose 摘要不匹配：expected={expected_hash}, actual={actual_hash}",
                status_code=409,
            )
        app_image = str(entry.get("appImage") or "").strip()
        computer_image = str(entry.get("computerImage") or "").strip()
        if "@sha256:" not in app_image or "@sha256:" not in computer_image:
            raise RakazoRuntimeError("Rakazo 兼容清单没有固定不可变镜像摘要", status_code=500)
        return {
            "composeFile": str(compose_file.resolve()),
            "composeSha256": actual_hash,
            "appImage": app_image,
            "computerImage": computer_image,
        }

    def _is_managed_compose(self) -> bool:
        try:
            configured = Path(str(app_config.rakazo.compose_file or "")).expanduser().resolve()
            return configured == self._managed_compose_path().resolve()
        except OSError:
            return False

    def _materialize_managed_compose_env(self, environment: Mapping[str, str]) -> Optional[Path]:
        """短暂写出官方 Compose 强制要求的 .env，命令结束后立即删除。"""
        if not self._is_managed_compose():
            return None
        path = self._managed_compose_path().parent / ".env"
        names = [
            "POSTGRES_USER",
            "POSTGRES_PASSWORD",
            "POSTGRES_DB",
            "BETTER_AUTH_SECRET",
            "ENCRYPTION_KEY",
            "SCREEN_PROXY_SECRET",
            "SANDBOX_SUPERVISOR_TOKEN",
            "BETTER_AUTH_URL",
            "WEB_ORIGIN",
            "API_URL",
            "RAKAZO_HOST",
            "SIGNUPS_ENABLED",
            "SIGNUP_ALLOWLIST",
            "SANDBOX_PROVIDER",
        ]
        missing = [name for name in _RUNTIME_SECRET_NAMES if not str(environment.get(name) or "")]
        if missing:
            raise RakazoRuntimeError(f"Rakazo 运行时缺少必要密钥：{', '.join(missing)}", status_code=500)
        lines = [f"{name}={str(environment.get(name) or '')}" for name in names]
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    async def _run_compose(self, action: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
        environment = self._compose_environment(action)
        env_file = self._materialize_managed_compose_env(environment)
        try:
            return await self._run_process(
                self._compose_argv(action),
                timeout=timeout,
                environment=environment,
            )
        finally:
            if env_file is not None:
                try:
                    env_file.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(f"清理 Rakazo 临时 Compose 环境文件失败：{_safe_error(exc)}")

    @staticmethod
    def _is_postgres_auth_failure(result: subprocess.CompletedProcess[str]) -> bool:
        """识别 Prisma P1000：Compose .env 密钥与既有 Postgres volume 密码失配。"""
        output = f"{result.stderr or ''}\n{result.stdout or ''}"
        return "P1000" in output or "Authentication failed against database server" in output

    async def _realign_postgres_password(self) -> bool:
        """把既有 Postgres volume 内的数据库密码对齐到当前受管密钥。

        Postgres 只在 volume 首次初始化时写入密码；Codebot 密钥存储重建后
        新凭证会被旧 volume 拒绝（P1000），api 容器陷入重启循环。这里通过
        容器本地 trust socket 执行 ``ALTER USER``，保留全部数据；非受管
        Compose、密钥异常或容器不可达时一律失败关闭，不猜测旧密码。
        """
        if not self._is_managed_compose():
            return False
        docker = self._docker_executable()
        if not docker:
            return False
        try:
            secrets_payload = self._runtime_secrets()
        except RakazoRuntimeError:
            return False
        password = str(secrets_payload.get("POSTGRES_PASSWORD") or "")
        if not password or "'" in password:
            return False
        project_name = str(app_config.rakazo.docker_project_name or "")
        if not _COMPOSE_PROJECT_RE.fullmatch(project_name):
            return False
        listed = await self._run_process(
            [
                docker,
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--filter",
                "label=com.docker.compose.service=postgres",
                "--format",
                "{{.ID}}",
            ],
            timeout=30,
            environment=self._docker_environment(),
        )
        if listed.returncode != 0:
            return False
        container_id = next((line.strip() for line in (listed.stdout or "").splitlines() if line.strip()), "")
        if not container_id:
            return False
        realigned = await self._run_process(
            [
                docker,
                "exec",
                container_id,
                "psql",
                "-U",
                "rakazo",
                "-d",
                "rakazo",
                "-c",
                f"ALTER USER rakazo WITH PASSWORD '{password}';",
            ],
            timeout=60,
            environment=self._docker_environment(),
        )
        return realigned.returncode == 0

    @staticmethod
    def _is_transient_compose_network_failure(result: subprocess.CompletedProcess[str]) -> bool:
        """只识别可安全重试的 Docker Registry/传输层临时错误。

        配置、镜像摘要、权限、端口或 Compose 语法错误不能进入重试，避免一键安装
        用无意义循环掩盖确定性根因。
        """
        output = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
        markers = (
            "failed to fetch anonymous token",
            "failed to authorize",
            "tls handshake timeout",
            "i/o timeout",
            "connection reset by peer",
            "unexpected eof",
            "temporary failure in name resolution",
            "no such host",
            "too many requests",
            "http 429",
            "http 502",
            "http 503",
            "http 504",
        )
        # Registry 请求末尾的裸 EOF 也是 Docker Desktop/GHCR 常见的瞬时断连，
        # 但只有在同一输出包含 pull/registry/authorize 语义时才允许重试。
        if any(marker in output for marker in markers):
            return True
        if "corepack-init" in output and any(marker in output for marker in ("exited", "dependency failed")):
            return True
        return "eof" in output and any(marker in output for marker in ("pull", "registry", "authorize", "token"))

    async def _inspect_docker_image(self, image: str) -> Dict[str, Any]:
        docker = self._docker_executable()
        if not docker:
            raise RakazoRuntimeError("未检测到 Docker CLI", status_code=409)
        result = await self._run_process(
            [docker, "image", "inspect", image],
            timeout=60,
            environment=self._docker_environment(),
        )
        if result.returncode != 0:
            raise RakazoRuntimeError(f"无法检查固定 Rakazo 镜像：{result.stderr or result.stdout}")
        try:
            payload = json.loads(result.stdout)
        except ValueError as exc:
            raise RakazoRuntimeError("Docker image inspect 返回非 JSON") from exc
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise RakazoRuntimeError("Docker image inspect 返回结构无效")
        return payload[0]

    async def verify_running_runtime_identity(self, entry: Mapping[str, Any]) -> Dict[str, Any]:
        """核对固定镜像标签，并证明正在运行的 api 容器使用该镜像 ID。"""
        expected = self._validate_compose_for_entry(entry)
        app = await self._inspect_docker_image(expected["appImage"])
        computer = await self._inspect_docker_image(expected["computerImage"])
        app_labels = ((app.get("Config") or {}).get("Labels") or {}) if isinstance(app.get("Config"), dict) else {}
        computer_labels = (
            ((computer.get("Config") or {}).get("Labels") or {})
            if isinstance(computer.get("Config"), dict)
            else {}
        )
        app_revision = str(app_labels.get("org.opencontainers.image.revision") or "").lower()
        computer_revision = str(computer_labels.get("org.opencontainers.image.revision") or "").lower()
        expected_app_revision = str(entry.get("appImageRevision") or entry.get("sourceRevision") or "").lower()
        expected_computer_revision = str(entry.get("computerImageRevision") or "").lower()
        if app_revision != expected_app_revision or computer_revision != expected_computer_revision:
            raise RakazoRuntimeError(
                "Rakazo 官方镜像 revision 与 Codebot 兼容清单不一致；拒绝继续运行",
                status_code=409,
            )
        docker = self._docker_executable()
        assert docker is not None
        project_name = str(app_config.rakazo.docker_project_name or "")
        container_result = await self._run_process(
            [
                docker,
                "ps",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--filter",
                "label=com.docker.compose.service=api",
                "--format",
                "{{.ID}}",
            ],
            timeout=30,
            environment=self._docker_environment(),
        )
        container_id = container_result.stdout.strip().splitlines()[0] if container_result.returncode == 0 and container_result.stdout.strip() else ""
        if not container_id:
            raise RakazoRuntimeError("未找到正在运行的 Rakazo api 容器", status_code=409)
        running = await self._run_process(
            [docker, "inspect", "--format", "{{.Image}}", container_id],
            timeout=30,
            environment=self._docker_environment(),
        )
        running_image_id = running.stdout.strip() if running.returncode == 0 else ""
        if not running_image_id or running_image_id != str(app.get("Id") or ""):
            raise RakazoRuntimeError("Rakazo api 容器没有使用兼容清单固定的镜像", status_code=409)
        identity = {
            "verified": True,
            "channel": str(entry.get("channel") or ""),
            "sourceRevision": str(entry.get("sourceRevision") or ""),
            "appImage": expected["appImage"],
            "computerImage": expected["computerImage"],
            "appImageRevision": app_revision,
            "computerImageRevision": computer_revision,
            "verifiedAt": _utc_now(),
        }
        self._verified_runtime_identity = identity
        return identity

    def _compose_environment(self, action: str) -> Dict[str, str]:
        """构造只传给 Compose 子进程的环境；密钥永不进入 config.json。"""
        environment = self._docker_environment()
        # start/restart 必须拿到 OpenCode 当前可选目录；stop 只需满足 Compose
        # 变量插值，不能因为 OpenCode 暂时离线而阻止用户停止 Rakazo 容器。
        route_ids = (
            sorted(
                str(model.get("id") or "")
                for model in self.models(refresh=True)
                if model.get("selectable") and model.get("id")
            )
            if action != "stop"
            else []
        )
        if action != "stop" and not route_ids:
            raise RakazoRuntimeError(
                "OpenCode 当前没有可由 Codebot 安全代理的模型连接，拒绝启动不可用的 Agent 运行时",
                status_code=409,
            )
        if action == "stop" and not route_ids:
            route_ids = ["codebot-stop-placeholder"]
        model_ids = ",".join(route_ids)
        if len(model_ids) > 28_000:
            raise RakazoRuntimeError("已验证 Rakazo 模型过多，超过 Windows Compose 环境变量安全上限", status_code=409)
        adapter_context = self._adapter_context_path().resolve()
        if action != "stop" and not (adapter_context / "Dockerfile").is_file():
            raise RakazoRuntimeError("缺少 Codebot Rakazo Adapter 构建目录", status_code=409)
        raw_port = str(os.environ.get("CODEBOT_BACKEND_PORT") or app_config.network.port).strip()
        try:
            backend_port = int(raw_port)
        except ValueError as exc:
            raise RakazoRuntimeError("Codebot 后端端口无效", status_code=500) from exc
        entry = self._runtime_entry_for_action(action)
        runtime_identity = self._validate_compose_for_entry(entry)
        if self._is_managed_compose():
            environment.update(self._runtime_secrets())
            environment.update({
                "POSTGRES_USER": "rakazo",
                "POSTGRES_DB": "rakazo",
                # Better Auth 的公开 base URL 指向 API 本身；Web 仍作为单独可信
                # Origin。这样 Codebot 直连 /api/auth 时不会被 CSRF Origin 拒绝。
                "BETTER_AUTH_URL": "http://127.0.0.1:3100",
                "WEB_ORIGIN": "http://127.0.0.1:5173",
                "API_URL": "http://127.0.0.1:5173",
                "RAKAZO_HOST": "localhost",
                "SIGNUPS_ENABLED": "true",
                "SIGNUP_ALLOWLIST": "",
                "SANDBOX_PROVIDER": "docker",
            })
        environment.update(
            {
                "CODEBOT_RAKAZO_SAMPLING_TOKEN": self.sampling_token,
                "CODEBOT_RAKAZO_ADAPTER_SHARED_KEY": RAKAZO_LOCAL_ADAPTER_SHARED_KEY,
                "CODEBOT_RAKAZO_INTERNAL_URL": f"http://host.docker.internal:{backend_port}/api/internal/model-sampling/v1",
                "CODEBOT_RAKAZO_PROJECT_MCP_URL": f"http://host.docker.internal:{backend_port}/api/internal/rakazo-project-mcp",
                "CODEBOT_RAKAZO_MODEL_IDS": model_ids,
                "CODEBOT_RAKAZO_ADAPTER_CONTEXT": str(adapter_context),
                "CODEBOT_RAKAZO_APP_IMAGE": runtime_identity["appImage"],
                "CODEBOT_RAKAZO_COMPUTER_IMAGE": runtime_identity["computerImage"],
            }
        )
        self._pending_model_ids = set(route_ids) if action != "stop" else set()
        return environment

    async def control_runtime(self, action: str) -> Dict[str, Any]:
        requested_action = action
        preparation: Dict[str, Any] = {}
        if action == "install_experimental":
            preparation = await self.prepare_experimental_runtime()
            action = "start"
        if action not in {"start", "stop", "restart"}:
            raise RakazoRuntimeError("不支持的运行时操作", status_code=400)
        start_entry: Dict[str, Any] = {}
        if action in {"start", "restart"}:
            start_policy = self.runtime_start_policy()
            if not start_policy["allowed"]:
                raise RakazoRuntimeError(
                    f"Rakazo 启动门禁未通过：{start_policy['reason']}",
                    status_code=409,
                )
            start_entry = dict(start_policy.get("entry") or {})
            self._validate_compose_for_entry(start_entry)
        if action in {"stop", "restart"} and self._active_runs:
            raise RakazoRuntimeError("仍有 Rakazo 任务运行，拒绝停止或重启", status_code=409)
        max_attempts = 4 if action in {"start", "restart"} else 1
        result: Optional[subprocess.CompletedProcess[str]] = None
        realigned_auth_failure = False
        for attempt in range(1, max_attempts + 1):
            # restart 首次失败后只做 ``up -d`` 收敛，不再次强制重建整套容器；
            # 这可恢复镜像拉取或健康检查瞬断留下的 Created 服务。
            compose_action = action if attempt == 1 else ("start" if action == "restart" else action)
            result = await self._run_compose(
                compose_action,
                timeout=1800 if compose_action in {"start", "restart"} else 180,
            )
            if result.returncode == 0:
                break
            if (
                action in {"start", "restart"}
                and not realigned_auth_failure
                and self._is_postgres_auth_failure(result)
            ):
                # Postgres volume 保留首次初始化密码，密钥存储重建后新凭证会被
                # 拒绝（P1000）；对齐受管密码后只重试一次，其余失败关闭不变。
                realigned_auth_failure = True
                realigned = await self._realign_postgres_password()
                self.audit(
                    None,
                    f"runtime.{action}.postgres-realign",
                    "success" if realigned else "failed",
                    {"attempt": attempt},
                )
                if realigned:
                    await asyncio.sleep(2)
                    continue
                break
            if attempt >= max_attempts or not self._is_transient_compose_network_failure(result):
                break
            self.audit(
                None,
                f"runtime.{action}.retry",
                "pending",
                {"attempt": attempt, "error": _safe_error(result.stderr or result.stdout)},
            )
            await asyncio.sleep(min(2 ** attempt, 8))
        assert result is not None
        if result.returncode != 0:
            self.audit(None, f"runtime.{action}", "failed", {"error": _safe_error(result.stderr or result.stdout)})
            raise RakazoRuntimeError(f"Rakazo {action} 失败：{result.stderr or result.stdout}")
        compatibility: Dict[str, Any] = {}
        if action in {"start", "restart"}:
            # Compose 返回只代表容器已提交启动；在向用户报告成功前必须完成健康与
            # 合同握手。短暂重试仅等待服务 ready，不会安装或修改系统组件。
            last_error: Optional[Exception] = None
            for _ in range(60):
                try:
                    compatibility = self.assert_runtime_compatible(await self.health())
                    compatibility["imageIdentity"] = await self.verify_running_runtime_identity(start_entry)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    await asyncio.sleep(1)
            if last_error is not None:
                # 不兼容运行时不能留在后台继续接受请求；仅停止容器，不删除 volume。
                try:
                    await self._run_compose("stop", timeout=180)
                except Exception as stop_error:
                    logger.warning(f"停止不兼容 Rakazo 容器失败：{_safe_error(stop_error)}")
                self.audit(None, f"runtime.{action}", "failed", {"error": _safe_error(last_error)})
                raise RakazoRuntimeError(f"Rakazo 已启动但兼容握手失败：{last_error}", status_code=409)
        self.audit(None, f"runtime.{action}", "success", {})
        if action in {"start", "restart"}:
            self._loaded_model_ids = set(self._pending_model_ids)
        elif action == "stop":
            self._loaded_model_ids.clear()
            self._verified_runtime_identity = {}
        return {
            "ok": True,
            "action": requested_action,
            "runtimeAction": action,
            "message": (result.stdout or "").strip()[-1000:],
            "compatibility": compatibility,
            "preparation": preparation,
        }

    async def status(self) -> Dict[str, Any]:
        docker = await self.docker_status()
        start_policy = self.runtime_start_policy()
        health: Dict[str, Any] = {}
        connected = False
        try:
            health = await self.health()
            connected = bool(health.get("ok", True))
        except Exception as exc:
            self._last_error = _safe_error(exc)
        with self._connect() as conn:
            project_count = int(conn.execute("SELECT COUNT(*) FROM rakazo_projects").fetchone()[0])
        # 只统计当前 OpenCode 目录及当前路由指纹，不能把已断开的 Provider 或
        # 旧指纹探测记录继续显示为可用。
        current_models = self.models(refresh=False)
        verified_count = sum(1 for item in current_models if item.get("compatibilityStatus") == "verified")
        selectable_count = sum(1 for item in current_models if item.get("selectable"))
        compatibility: Dict[str, Any] = {"compatible": False, "reason": self._last_error}
        if connected:
            try:
                compatibility = self.assert_runtime_compatible(health)
                entry = self.compatibility_entry(
                    health.get("version"),
                    revision=health.get("revision"),
                )
                if entry:
                    compatibility["imageIdentity"] = await self.verify_running_runtime_identity(entry)
                self._last_error = ""
            except Exception as exc:
                connected = False
                compatibility = {"compatible": False, "reason": _safe_error(exc)}
                self._last_error = _safe_error(exc)
        selected_entry = start_policy.get("entry") if isinstance(start_policy.get("entry"), dict) else {}
        experimental_entry = next(
            (
                item
                for item in self._compatibility_entries()
                if str(item.get("channel") or "") == "experimental"
                and bool(item.get("runtimeCompatible"))
                and bool(item.get("installable"))
            ),
            {},
        )
        display_entry = selected_entry or experimental_entry
        return {
            "enabled": bool(app_config.rakazo.enabled),
            "connected": connected,
            "health": health,
            "runtimeVersion": str(health.get("version") or compatibility.get("rakazoVersion") or ""),
            "contractVersion": str(
                health.get("contractVersion")
                or health.get("version")
                or compatibility.get("contractVersion")
                or ""
            ),
            "compatibility": compatibility,
            "sessionAuthorized": self.session_token_configured(),
            "docker": docker,
            "projectCount": project_count,
            "verifiedModelCount": verified_count,
            "openCodeEnabledModelCount": len(current_models),
            "selectableModelCount": selectable_count,
            "activeRuns": len(self._active_runs),
            "lastError": self._last_error,
            "apiUrl": str(app_config.rakazo.api_url),
            "updateChannel": str(app_config.rakazo.release_channel),
            "runtimeChannel": str(app_config.rakazo.release_channel),
            "productionReady": bool(compatibility.get("productionReady")) if connected else False,
            "experimentalRuntimeEnabled": bool(app_config.rakazo.experimental_runtime_enabled),
            "experimentalRuntimeAvailable": bool(start_policy.get("experimentalAvailable")),
            "managedRuntime": self._is_managed_compose(),
            "runtimeSourceRevision": str(
                compatibility.get("sourceRevision")
                or display_entry.get("sourceRevision")
                or ""
            ),
            "runtimeImages": {
                "app": str(compatibility.get("appImage") or display_entry.get("appImage") or ""),
                "computer": str(compatibility.get("computerImage") or display_entry.get("computerImage") or ""),
            },
            "runtimeStartAllowed": bool(start_policy["allowed"]),
            "approvedRuntimeVersions": start_policy["approvedVersions"],
            "approvedRuntimeRevisions": start_policy.get("approvedRevisions") or [],
            "runtimeStartReason": start_policy["reason"],
            "samplingBridge": {
                "singleInference": True,
                "openCodeAgentLoop": False,
                "nativeStreaming": True,
                "productionReady": True,
                "reason": "直接复用 OpenCode 当前连接的纯模型路由；首次选择时自动验证文本、流式、工具、取消、用量与模型身份，不进入 OpenCode Agent。",
            },
        }

    def _probe_row(self, route_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM rakazo_model_probes WHERE route_id = ?",
                (route_id,),
            ).fetchone()
        return dict(row) if row else None

    def _probe_rows(self) -> Dict[str, Dict[str, Any]]:
        """一次读取全部探测记录，避免完整 OpenCode 目录上的 N+1 SQLite 查询。"""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM rakazo_model_probes").fetchall()
        return {str(row["route_id"]): dict(row) for row in rows}

    def models(self, *, refresh: bool = False) -> List[Dict[str, Any]]:
        """只返回当前 OpenCode Server 已启用的模型，再叠加 Rakazo 实测状态。

        OpenCode 的 ``/provider`` 会同时公开数千个未连接 Provider 的静态目录。
        Rakazo 不应把这些未启用模型复制到设置页；``connected`` 才表示该模型
        已进入用户当前 OpenCode Server 的可用目录。协议、凭据和 Rakazo 探测
        仍是后续独立门禁，不能因为 OpenCode 已启用就直接标记为已验证。
        """
        full_catalog = model_route_registry.catalog(refresh=refresh)
        catalog = {
            route_id: route
            for route_id, route in full_catalog.items()
            if route.connected
        }
        probe_rows = self._probe_rows()
        result: List[Dict[str, Any]] = []
        for route_id in sorted(catalog):
            route = catalog[route_id]
            status = route.compatibility_status
            reason = route.incompatibility_reason
            last_probe_at: Optional[str] = None
            selectable = route.compatibility_status == "probe_required"
            probe_state = "blocked" if not selectable else "unprobed"
            probe = probe_rows.get(route_id)
            if status == "probe_required":
                if probe and probe.get("route_fingerprint") == route.route_fingerprint:
                    status = str(probe.get("status") or "probe_failed")
                    reason = str(probe.get("error_summary") or "")
                    last_probe_at = str(probe.get("last_probe_at") or "") or None
                    probe_state = "verified" if status == "verified" else "failed"
                else:
                    status = "probe_failed"
                    reason = "首次选择时由 Codebot 自动验证文本、流式、工具调用、取消和模型身份。"
            public = route.public_model(
                compatibility_status=status,
                incompatibility_reason=reason,
                last_probe_at=last_probe_at,
            )
            public.update({
                # selectable 表示“可选择并自动验证”，verified 才表示已经可以
                # 进入内部采样端点；两者分离避免继续要求用户手工逐个探测。
                "selectable": selectable,
                "probeRequired": bool(selectable and status != "verified"),
                "probeState": probe_state,
                "runnable": selectable,
            })
            result.append(public)
        return result

    def verified_route(self, route_id: str) -> ModelRoute:
        route = model_route_registry.get(route_id)
        if route is None:
            raise RakazoRuntimeError("所选模型不在当前 OpenCode 目录中", status_code=400)
        if route.compatibility_status != "probe_required":
            raise RakazoRuntimeError(route.incompatibility_reason or "所选模型不可用于 Rakazo", status_code=409)
        probe = self._probe_row(route.route_id)
        if not probe or probe.get("route_fingerprint") != route.route_fingerprint or probe.get("status") != "verified":
            raise RakazoRuntimeError("所选模型尚未通过当前路由指纹的 Rakazo 真实兼容探测", status_code=409)
        return route

    async def ensure_route_ready(self, route_id: str) -> ModelRoute:
        """保证 OpenCode 当前可选模型在第一次 Rakazo 使用前完成真实验证。"""
        try:
            route = await asyncio.to_thread(model_route_registry.ensure_fresh_credentials, route_id)
        except Exception as exc:
            raise RakazoRuntimeError(f"无法复用 OpenCode 模型连接：{_safe_error(exc)}", status_code=409) from exc
        if not route.connected:
            raise RakazoRuntimeError("所选模型已不在 OpenCode 当前连接中", status_code=409)
        if route.compatibility_status != "probe_required":
            raise RakazoRuntimeError(route.incompatibility_reason or "所选模型不能用于 Rakazo", status_code=409)
        try:
            return self.verified_route(route_id)
        except RakazoRuntimeError:
            pass

        async with self._automatic_probe_lock:
            # 等待锁期间可能已由另一条选择请求完成探测，先再次检查再调用模型。
            try:
                return self.verified_route(route_id)
            except RakazoRuntimeError:
                pass
            result = await self.probe_model(route_id)
            if str(result.get("status") or "") != "verified":
                reason = str(result.get("errorSummary") or "未知兼容错误")
                raise RakazoRuntimeError(f"模型首次自动验证失败：{reason}", status_code=409)
            return self.verified_route(route_id)

    async def sample(self, route_id: str, payload: Mapping[str, Any], *, require_verified: bool = True) -> BridgedChatResult:
        try:
            route = await asyncio.to_thread(model_route_registry.ensure_fresh_credentials, route_id)
        except Exception as exc:
            raise RakazoRuntimeError(f"模型连接不可用：{_safe_error(exc)}", status_code=409) from exc
        if require_verified:
            route = self.verified_route(route_id)
        if route is None:
            raise RakazoRuntimeError("模型路由不存在", status_code=404)
        if route.compatibility_status != "probe_required":
            raise RakazoRuntimeError(route.incompatibility_reason or "模型路由不可采样", status_code=409)
        selected = str(payload.get("model") or "").strip()
        if selected and selected != route.route_id:
            raise RakazoRuntimeError("请求模型与受权路由不一致，拒绝静默换模", status_code=400)
        normalized = dict(payload)
        normalized["model"] = route.route_id
        try:
            return await bridge_chat_completions_request(route, normalized)
        except asyncio.CancelledError:
            raise
        except CodexModelBridgeError as exc:
            raise RakazoRuntimeError(f"模型采样失败：{exc}", status_code=exc.status_code) from exc
        except Exception as exc:
            raise RakazoRuntimeError(f"模型采样失败：{_safe_error(exc)}") from exc

    async def sample_stream(
        self,
        route_id: str,
        payload: Mapping[str, Any],
        *,
        require_verified: bool = True,
    ) -> AsyncIterator[str]:
        """返回真实上游模型流；停止迭代会关闭 provider 的 HTTP 流。"""
        try:
            route = await asyncio.to_thread(model_route_registry.ensure_fresh_credentials, route_id)
        except Exception as exc:
            raise RakazoRuntimeError(f"模型连接不可用：{_safe_error(exc)}", status_code=409) from exc
        if require_verified:
            route = self.verified_route(route_id)
        if route is None:
            raise RakazoRuntimeError("模型路由不存在", status_code=404)
        if route.compatibility_status != "probe_required":
            raise RakazoRuntimeError(route.incompatibility_reason or "模型路由不可采样", status_code=409)
        selected = str(payload.get("model") or "").strip()
        if selected and selected != route.route_id:
            raise RakazoRuntimeError("请求模型与受权路由不一致，拒绝静默换模", status_code=400)
        normalized = dict(payload)
        normalized["model"] = route.route_id
        normalized["stream"] = True
        bridge_stream = bridge_chat_completions_stream(route, normalized)
        try:
            async for item in bridge_stream:
                yield item
        except asyncio.CancelledError:
            raise
        except CodexModelBridgeError as exc:
            raise RakazoRuntimeError(f"模型流式采样失败：{exc}", status_code=exc.status_code) from exc
        except RakazoRuntimeError:
            raise
        except Exception as exc:
            raise RakazoRuntimeError(f"模型流式采样失败：{_safe_error(exc)}") from exc
        finally:
            # 调用方中断 Rakazo 流时同步关闭协议桥和真实上游 HTTP 响应，
            # 避免异步生成器终结器稍后并发执行第二次 aclose。
            await bridge_stream.aclose()

    @staticmethod
    def _probe_output_budget(route: ModelRoute) -> int:
        """为推理模型保留足够预算，避免 reasoning 用尽 64 token 的误判。"""
        desired = 512 if route.reasoning else 256
        if route.max_output_tokens:
            return max(1, min(int(route.max_output_tokens), desired))
        return desired

    async def probe_model(self, route_id: str) -> Dict[str, Any]:
        """执行真实文本、流式、工具回环、限制、用量和身份探测。"""
        route = await asyncio.to_thread(model_route_registry.get, route_id, refresh=True)
        if route is None:
            raise RakazoRuntimeError("模型不在当前 OpenCode 目录中", status_code=404)
        if route.compatibility_status != "probe_required":
            raise RakazoRuntimeError(route.incompatibility_reason or "该模型当前不能探测", status_code=409)

        checks: Dict[str, bool] = {
            "text": False,
            "stream": False,
            "toolCall": False,
            "toolResultContinuation": False,
            "cancellation": False,
            "limits": False,
            "usageAndFinishReason": False,
            "modelIdentity": False,
        }
        actual_model = ""
        error_summary = ""
        marker = f"CODEBOT_RAKAZO_PROBE_{secrets.token_hex(6)}"
        probe_budget = self._probe_output_budget(route)
        try:
            text_result = await self.sample(
                route_id,
                {
                    "model": route_id,
                    "messages": [{"role": "user", "content": f"只回复 {marker}"}],
                    "max_tokens": probe_budget,
                },
                require_verified=False,
            )
            choice = text_result.response["choices"][0]
            message = choice.get("message") or {}
            text = str(message.get("content") or "")
            checks["text"] = bool(text)
            stream_content_events = 0
            stream_done = False
            stream_finish_reason = ""
            stream_actual_model = ""
            async for sse_event in self.sample_stream(
                route_id,
                {
                    "model": route_id,
                    "messages": [{"role": "user", "content": f"流式回复 {marker}"}],
                    "max_tokens": probe_budget,
                },
                require_verified=False,
            ):
                for line in str(sse_event).splitlines():
                    if not line.startswith("data:"):
                        continue
                    raw_data = line[5:].strip()
                    if raw_data == "[DONE]":
                        stream_done = True
                        continue
                    try:
                        chunk = json.loads(raw_data)
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    route_meta = chunk.get("codebot_route") if isinstance(chunk.get("codebot_route"), dict) else {}
                    stream_actual_model = str(route_meta.get("actualModel") or stream_actual_model)
                    choices = chunk.get("choices") if isinstance(chunk.get("choices"), list) else []
                    if choices and isinstance(choices[0], dict):
                        delta = choices[0].get("delta") if isinstance(choices[0].get("delta"), dict) else {}
                        if delta.get("content") or delta.get("reasoning_content") or delta.get("tool_calls"):
                            stream_content_events += 1
                        stream_finish_reason = str(choices[0].get("finish_reason") or stream_finish_reason)
            checks["stream"] = bool(
                stream_content_events > 0
                and stream_done
                and stream_finish_reason
                and stream_actual_model
            )
            usage = text_result.response.get("usage") or {}
            checks["usageAndFinishReason"] = (
                isinstance(usage.get("total_tokens"), int)
                and bool(choice.get("finish_reason"))
            )
            route_meta = text_result.response.get("codebot_route") or {}
            actual_model = str(route_meta.get("actualModel") or "")
            expected = str(route.codex_model)
            checks["modelIdentity"] = bool(actual_model) and (
                actual_model == expected
                or actual_model == route.route_id
                or actual_model.startswith(f"{expected}-")
            )
            # OpenCode 的 OpenAI OAuth 插件会按官方合同移除每请求输出上限；
            # 该路由改为核验模型目录声明的总上限。其他协议仍验证请求预算。
            effective_limit = (
                int(route.max_output_tokens or probe_budget)
                if route.credential_mode == "opencode_oauth"
                else probe_budget
            )
            checks["limits"] = (
                int(usage.get("completion_tokens") or 0) <= effective_limit
                and (route.context_window is None or route.context_window > 0)
            )

            tool_payload: Dict[str, Any] = {
                "model": route_id,
                "messages": [{"role": "user", "content": "必须调用 echo_probe 工具，参数 value 填 probe；不要直接回答。"}],
                "tools": [{
                    "type": "function",
                    "function": {
                        "name": "echo_probe",
                        "description": "兼容探测工具",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    },
                }],
                "tool_choice": {"type": "function", "function": {"name": "echo_probe"}},
                "max_tokens": probe_budget,
            }
            try:
                tool_result = await self.sample(route_id, tool_payload, require_verified=False)
            except RakazoRuntimeError as exc:
                # 部分推理模型支持工具调用但明确拒绝强制 tool_choice。只在上游
                # 明确报告该参数不兼容时移除它并再做一次真实工具调用；若模型
                # 随后没有产生 tool_calls，探测仍然失败，绝不按名称放行。
                message = str(exc).lower()
                if "tool_choice" not in message and "thinking mode" not in message:
                    raise
                tool_payload.pop("tool_choice", None)
                tool_result = await self.sample(route_id, tool_payload, require_verified=False)
            tool_message = tool_result.response["choices"][0].get("message") or {}
            tool_calls = tool_message.get("tool_calls") or []
            checks["toolCall"] = bool(tool_calls)
            if tool_calls:
                call = tool_calls[0]
                continuation = await self.sample(
                    route_id,
                    {
                        "model": route_id,
                        "messages": [
                            {"role": "user", "content": "调用工具后总结结果"},
                            {"role": "assistant", "content": None, "tool_calls": [call]},
                            {"role": "tool", "tool_call_id": call.get("id"), "content": "probe-ok"},
                        ],
                        "max_tokens": probe_budget,
                    },
                    require_verified=False,
                )
                continuation_text = str(
                    (continuation.response["choices"][0].get("message") or {}).get("content") or ""
                )
                checks["toolResultContinuation"] = bool(continuation_text)

            # 建立第二条真实上游流，收到首个事件后主动关闭异步生成器。生成器的
            # httpx 上下文会关闭 provider 连接；这比取消一个尚未开始的协程更能
            # 验证 Rakazo“停止”能传递到上游。
            cancellation_stream = self.sample_stream(
                route_id,
                {
                    "model": route_id,
                    "messages": [{"role": "user", "content": "持续输出若干内容，用于取消探测"}],
                    "max_tokens": probe_budget,
                },
                require_verified=False,
            )
            try:
                await asyncio.wait_for(anext(cancellation_stream), timeout=30)
                await cancellation_stream.aclose()
                checks["cancellation"] = True
            except Exception:
                try:
                    await cancellation_stream.aclose()
                except Exception:
                    pass
                checks["cancellation"] = False
        except Exception as exc:
            error_summary = _safe_error(exc)

        required = [
            "text", "stream", "toolCall", "toolResultContinuation", "cancellation",
            "limits", "usageAndFinishReason", "modelIdentity",
        ]
        status = "verified" if all(checks[name] for name in required) else "probe_failed"
        if status != "verified" and not error_summary:
            failed = [name for name in required if not checks[name]]
            error_summary = f"未通过探测项：{', '.join(failed)}"
            if "stream" in failed:
                error_summary += "；上游没有返回完整的正文/推理增量、结束原因、模型身份或 [DONE]"
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO rakazo_model_probes
                   (route_id, route_fingerprint, status, checks_json, actual_model,
                    error_summary, last_probe_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(route_id) DO UPDATE SET
                     route_fingerprint = excluded.route_fingerprint,
                     status = excluded.status,
                     checks_json = excluded.checks_json,
                     actual_model = excluded.actual_model,
                     error_summary = excluded.error_summary,
                     last_probe_at = excluded.last_probe_at""",
                (
                    route.route_id,
                    route.route_fingerprint,
                    status,
                    json.dumps(checks, ensure_ascii=False),
                    actual_model,
                    error_summary,
                    now,
                ),
            )
        self.audit(None, "model.probe", status, {"routeId": route.route_id, "checks": checks, "error": error_summary})
        return {
            "routeId": route.route_id,
            "routeFingerprint": route.route_fingerprint,
            "status": status,
            "checks": checks,
            "actualModel": actual_model,
            "errorSummary": error_summary,
            "lastProbeAt": now,
        }

    def select_initial_route(self, preferred_route_id: str = "") -> str:
        """为新项目选择一次性的初始模型，不把模型永久固定到对话。

        优先采用调用方当前选择，其次采用 Codebot 通用默认模型，再选择已经
        通过 Rakazo 门禁的路由；只有完全没有已验证路由时才选第一个可探测
        路由。真正创建前仍会调用 ``ensure_route_ready``，因此不会把未验证模型
        偷偷当作可用模型，也不会在失败后静默换模。
        """
        selectable = [item for item in self.models() if bool(item.get("selectable"))]
        if not selectable:
            raise RakazoRuntimeError("OpenCode 当前没有可供 Rakazo 使用的模型", status_code=409)

        by_id = {str(item.get("id") or ""): item for item in selectable}
        candidates = [
            str(preferred_route_id or "").strip(),
            str(app_config.general.chat_default_model or "").strip(),
        ]
        for candidate in candidates:
            if candidate and candidate in by_id:
                return candidate

        for item in selectable:
            if str(item.get("compatibilityStatus") or "") == "verified":
                return str(item.get("id") or "")
        return str(selectable[0].get("id") or "")

    async def _ensure_local_provider_connection(self, default_route_id: str) -> None:
        """通过 Rakazo 官方合同补齐 keyless ``local`` Provider 的连接记录。

        固定实验版的 ``bots.update`` 要求数据库中存在连接记录，而且真正执行
        时会优先把该记录作为 model-adapter 的 Bearer。旧实现写入随机占位值，
        与 adapter 约定不一致，导致消息发送稳定返回 401。现在使用一个版本化
        标签和私网协议键；旧标签会经官方 ``models.connect`` 原位轮换一次。

        该协议键不属于 OpenCode/API/OAuth 凭据，也不能直接访问 Codebot：
        model-adapter 转发到内部采样接口时仍会替换为每次启动随机生成的进程令牌。
        """
        route_id = str(default_route_id or "").strip()
        if not route_id:
            raise RakazoRuntimeError("建立 Rakazo 本地模型连接时缺少初始模型", status_code=400)

        async with self._local_provider_lock:
            credentials = await self.rpc("models.credentials", {})
            if not isinstance(credentials, list):
                raise RakazoRuntimeError("Rakazo models.credentials 返回结构无效")
            current = next(
                (
                    item
                    for item in credentials
                    if isinstance(item, Mapping)
                    and str(item.get("provider") or "") == RAKAZO_LOCAL_PROVIDER
                ),
                None,
            )
            if isinstance(current, Mapping) and (
                str(current.get("label") or "") == RAKAZO_LOCAL_CREDENTIAL_LABEL
            ):
                return

            connected = await self.rpc(
                "models.connect",
                {
                    "provider": RAKAZO_LOCAL_PROVIDER,
                    # 上游合同对非 OpenAI-compatible Provider 强制至少 8 个字符。
                    # 这是 Docker 私网协议键，不是用户模型凭据；adapter 会在
                    # 转发时把它替换成随机进程采样令牌。
                    "apiKey": RAKAZO_LOCAL_ADAPTER_SHARED_KEY,
                    "label": RAKAZO_LOCAL_CREDENTIAL_LABEL,
                    "modelId": route_id,
                },
            )
            if not isinstance(connected, Mapping) or (
                str(connected.get("provider") or "") != RAKAZO_LOCAL_PROVIDER
            ):
                raise RakazoRuntimeError("Rakazo 未确认 Codebot 本地模型连接")
            self.audit(None, "model.provider.local.connect", "success", {"provider": RAKAZO_LOCAL_PROVIDER})

    async def create_remote_bot(
        self,
        project_dir: str,
        route_id: str,
        *,
        external_network: Optional[bool] = None,
    ) -> tuple[Dict[str, Any], ModelRoute]:
        """创建一个 dedicated Bot；失败时尽力删除半成品，避免孤儿资源。"""
        if not app_config.rakazo.enabled:
            raise RakazoRuntimeError("Rakazo 集成已禁用", status_code=409)
        health = await self.health()
        self.assert_runtime_compatible(health)
        entry = self.compatibility_entry(health.get("version"), revision=health.get("revision"))
        if not entry:
            raise RakazoRuntimeError("无法定位当前 Rakazo 运行时兼容条目", status_code=409)
        await self.verify_running_runtime_identity(entry)
        route = await self.ensure_route_ready(route_id)
        # 必须在创建 Bot 前补齐上游 local Provider 的账号级连接记录。该记录
        # 只满足 Rakazo bots.update 的合同门禁；真正的 OpenCode 凭据仍只在
        # Codebot 宿主内存中，容器侧 local Provider 继续通过私网 adapter 采样。
        await self._ensure_local_provider_connection(route.route_id)
        project_id, _, resolved = self.normalize_project(project_dir)
        name = Path(resolved).name[:60] or "Codebot Project"
        bot: Dict[str, Any] = {}
        mcp_server_id = ""
        try:
            # 同一项目只有一个 Codebot MCP。先清理以前在“创建成功、映射落库前”
            # 异常退出留下的确定性 slug，否则 Rakazo 唯一约束会让之后每次重试
            # 都永久报 500。
            await self._remove_project_mcp_by_slug(project_id)
            created = await self.rpc(
                "bots.create",
                {
                    "name": name,
                    "title": f"Codebot · {name}",
                    "description": "由 Codebot 管理的项目唯一 Rakazo 主会话",
                    "instructions": (
                        "你由 Codebot 的 Rakazo 原生集成管理。项目文件访问必须通过 Codebot "
                        "授予的项目工具和审批边界；不要假设宿主机任意路径已挂载，也不要请求或输出密钥。"
                    ),
                    "notifyOnFinish": True,
                    "computerMode": "dedicated",
                },
            )
            if not isinstance(created, dict):
                raise RakazoRuntimeError("Rakazo bots.create 返回结构无效")
            bot = created
            updated = await self.rpc(
                "bots.update",
                {
                    "botId": str(bot.get("id") or ""),
                    "modelProvider": RAKAZO_LOCAL_PROVIDER,
                    "modelId": route.route_id,
                },
            )
            if isinstance(updated, dict):
                bot = updated
            if (
                str(bot.get("modelProvider") or "") != RAKAZO_LOCAL_PROVIDER
                or str(bot.get("modelId") or "") != route.route_id
            ):
                raise RakazoRuntimeError("Rakazo bots.update 未保存所选模型，已拒绝创建不一致的主会话")
            # supervisor 默认创建可访问外网的 bridge。Codebot 在首次 boot 前
            # 预先创建同名网络：未授权时使用 --internal 真实断网，已授权时使用
            # 普通 bridge。项目文件仍只经带令牌的 MCP 访问，不挂载宿主目录。
            allow_external = (
                bool(app_config.rakazo.default_external_network)
                if external_network is None
                else bool(external_network)
            )
            await self._ensure_project_network(str(bot.get("id") or ""), allow_external=allow_external)
            # Computer 生命周期独立；创建后显式启动，失败则整次打开失败。
            computer = await self.rpc("computer.boot", {"botId": str(bot.get("id") or "")})
            if isinstance(computer, dict):
                bot = {**bot, "computer": computer}
            # 项目文件不直接挂进 Rakazo Computer。使用每项目随机令牌的受控 MCP
            # 暴露读取/写入工具；令牌由 Rakazo 加密保存，Codebot 只持有哈希。
            project_token = secrets.token_urlsafe(32)
            mcp_server = await self.rpc(
                "mcp.servers.create",
                {
                    "slug": self._project_mcp_slug(project_id),
                    "name": f"Codebot Project · {name}",
                    "description": "Codebot 项目根目录受控工具；路径逃逸和敏感凭据访问会被服务端拒绝。",
                    "enabled": True,
                    "transport": "streamable_http",
                    "endpoint": self._project_mcp_endpoint(project_id),
                    "headers": {"Authorization": f"Bearer {project_token}"},
                },
            )
            mcp_server_id = str((mcp_server or {}).get("id") or "") if isinstance(mcp_server, dict) else ""
            if not mcp_server_id:
                raise RakazoRuntimeError("Rakazo 未返回项目 MCP Server 标识")
            await self.rpc(
                "mcp.assignments.approve",
                {"botId": str(bot.get("id") or ""), "serverId": mcp_server_id},
            )
            bot = {
                **bot,
                "_codebotMcpServerId": mcp_server_id,
                "_codebotProjectTokenHash": self._token_digest(project_token),
            }
            return bot, route
        except Exception:
            if mcp_server_id:
                try:
                    await self.rpc("mcp.servers.remove", {"id": mcp_server_id})
                except Exception:
                    logger.warning(f"清理 Rakazo 半成品项目 MCP 失败：{mcp_server_id}")
            bot_id = str(bot.get("id") or "")
            if bot_id:
                try:
                    await self.rpc("bots.remove", {"botId": bot_id, "deleteMemories": False})
                except Exception:
                    logger.warning(f"清理 Rakazo 半成品 Bot 失败：{bot_id}")
            raise

    async def _ensure_remote_project(self, project_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """验证远端 Bot；仅在明确不存在时恢复原 Codebot 主会话映射。"""
        lock = self._project_repair_locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            mapping = self.mapping_by_project_id(project_id)
            if not mapping:
                raise RakazoRuntimeError("Rakazo 项目映射不存在", status_code=404)
            try:
                bot = await self.rpc("bots.get", {"botId": mapping["bot_id"]})
                if not isinstance(bot, dict):
                    raise RakazoRuntimeError("Rakazo bots.get 返回结构无效")
                return mapping, bot
            except Exception as exc:
                if not await self._confirm_bot_missing(str(mapping["bot_id"]), exc):
                    raise

            old_bot_id = str(mapping["bot_id"])
            old_mcp_id = str(mapping.get("mcp_server_id") or "")
            permissions = self.get_permissions(project_id)
            # 先登记退役身份。即使进程在后续复制/清理之间退出，用户删除项目时
            # 仍能枚举并清理旧 Computer home，而不会形成永久大文件孤儿。
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO rakazo_retired_bots(project_id, bot_id, created_at)
                       VALUES (?, ?, ?)""",
                    (project_id, old_bot_id, _utc_now()),
                )
            await self._remove_project_computer_runtime(old_bot_id)
            if old_mcp_id:
                try:
                    await self.rpc("mcp.servers.remove", {"id": old_mcp_id})
                except Exception as exc:
                    if not await self._confirm_mcp_server_missing(old_mcp_id, exc):
                        raise

            new_bot: Dict[str, Any] = {}
            try:
                new_bot, route = await self.create_remote_bot(
                    str(mapping["project_dir"]),
                    str(mapping.get("model_route_id") or ""),
                    external_network=bool(permissions["externalNetwork"]),
                )
                new_bot_id = str(new_bot.get("id") or "")
                # 远端数据库中的 Thread/Memory 已经不存在，无法伪造恢复；但旧
                # Computer home 仍可能包含用户文件。先停止刚启动的新 Computer，
                # 避免 Chromium 等进程一边写目标 home、一边迁移旧文件；迁移完成
                # 后再恢复运行，整个过程都不会覆盖新运行时已经创建的同名文件。
                await self.rpc("computer.stop", {"botId": new_bot_id})
                await self._project_home_operation("copy", old_bot_id, new_bot_id)
                restored_computer = await self.rpc("computer.boot", {"botId": new_bot_id})
                if isinstance(restored_computer, Mapping):
                    new_bot = {**new_bot, "computer": dict(restored_computer)}
                health = await self.health()
                repaired = self.save_mapping(
                    project_dir=str(mapping["project_dir"]),
                    conversation_id=int(mapping["conversation_id"]),
                    bot=new_bot,
                    route=route,
                    runtime_version=str(health.get("version") or "") if isinstance(health, Mapping) else "",
                    replace_missing_remote=True,
                )
                try:
                    from core.memory_manager import MemoryManager

                    await MemoryManager().bind_conversation_runtime(
                        int(mapping["conversation_id"]),
                        executor="rakazo",
                        external_thread_id=str(new_bot.get("threadId") or ""),
                        runtime_metadata={
                            "botId": new_bot_id,
                            "projectId": project_id,
                            "executorLocked": True,
                            "repairedMissingRemote": True,
                        },
                    )
                except Exception as bind_error:
                    # 主执行链只依赖 Rakazo 映射，conversation 的外部标识属于
                    # 辅助元数据；保留已恢复的新 Bot，并记录可诊断告警。
                    logger.warning(f"更新自愈后的 Rakazo 对话元数据失败：{_safe_error(bind_error)}")
                await self._project_home_operation("delete", old_bot_id)
                with self._connect() as conn:
                    conn.execute(
                        "DELETE FROM rakazo_retired_bots WHERE project_id = ? AND bot_id = ?",
                        (project_id, old_bot_id),
                    )
                self.audit(
                    project_id,
                    "project.reconcile_missing_bot",
                    "success",
                    {"oldBotId": old_bot_id, "newBotId": new_bot_id},
                )
                return repaired, new_bot
            except Exception:
                new_bot_id = str(new_bot.get("id") or "")
                new_mcp_id = str(new_bot.get("_codebotMcpServerId") or "")
                if new_mcp_id:
                    try:
                        await self.rpc("mcp.servers.remove", {"id": new_mcp_id})
                    except Exception as cleanup_error:
                        if not await self._confirm_mcp_server_missing(new_mcp_id, cleanup_error):
                            logger.warning(f"清理 Rakazo 自愈半成品项目 MCP 失败：{new_mcp_id}")
                if new_bot_id:
                    try:
                        await self.rpc("bots.remove", {"botId": new_bot_id, "deleteMemories": True})
                    except Exception:
                        logger.warning(f"清理 Rakazo 自愈半成品 Bot 失败：{new_bot_id}")
                raise

    async def remote_project_state(self, project_id: str) -> Dict[str, Any]:
        mapping, bot = await self._ensure_remote_project(project_id)
        computer: Dict[str, Any] = {}
        try:
            data = await self.rpc("computer.status", {"botId": mapping["bot_id"]})
            if isinstance(data, dict):
                computer = data
        except Exception as exc:
            computer = {"state": "unknown", "error": _safe_error(exc)}
        expected_model = str(mapping.get("model_route_id") or "")
        actual_provider = str((bot or {}).get("modelProvider") or "") if isinstance(bot, dict) else ""
        actual_model = str((bot or {}).get("modelId") or "") if isinstance(bot, dict) else ""
        route_drift = bool(
            expected_model
            and (actual_provider != RAKAZO_LOCAL_PROVIDER or actual_model != expected_model)
        )
        return {
            "mapping": self.public_mapping(mapping),
            "bot": bot,
            "computer": computer,
            "permissions": self.get_permissions(project_id),
            "active": project_id in self._active_runs,
            "routeDrift": route_drift,
            "routeDriftReason": (
                f"Rakazo Bot 当前为 {actual_provider or 'unknown'}/{actual_model or 'unknown'}，"
                f"与 Codebot 当前映射路由 local/{expected_model} 不一致"
                if route_drift else ""
            ),
        }

    async def set_project_model(self, project_id: str, route_id: str) -> Dict[str, Any]:
        mapping, _ = await self._ensure_remote_project(project_id)
        if project_id in self._active_runs:
            raise RakazoRuntimeError("Rakazo 正在执行任务，模型只能在空闲状态切换", status_code=409)
        route = await self.ensure_route_ready(route_id)
        await self._ensure_local_provider_connection(route.route_id)
        updated = await self.rpc(
            "bots.update",
            {"botId": mapping["bot_id"], "modelProvider": RAKAZO_LOCAL_PROVIDER, "modelId": route.route_id},
        )
        if not isinstance(updated, Mapping) or (
            str(updated.get("modelProvider") or "") != RAKAZO_LOCAL_PROVIDER
            or str(updated.get("modelId") or "") != route.route_id
        ):
            raise RakazoRuntimeError("Rakazo bots.update 未确认新模型，Codebot 映射保持原值")
        with self._connect() as conn:
            conn.execute(
                """UPDATE rakazo_projects SET model_route_id = ?, model_route_fingerprint = ?,
                   updated_at = ? WHERE project_id = ?""",
                (route.route_id, route.route_fingerprint, _utc_now(), project_id),
            )
        self.audit(project_id, "model.change", "success", {"routeId": route.route_id})
        return self.public_mapping(self.mapping_by_project_id(project_id))

    def _record_turn_route(self, mapping: Mapping[str, Any], run_id: str, route: ModelRoute) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO rakazo_turn_routes
                   (project_id, conversation_id, run_id, provider_id, model_id,
                    protocol, route_fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mapping["project_id"],
                    int(mapping["conversation_id"]),
                    run_id,
                    route.opencode_provider,
                    route.route_id,
                    route.upstream_protocol,
                    route.route_fingerprint,
                    _utc_now(),
                ),
            )

    @staticmethod
    def _message_text(blocks: Any) -> str:
        if not isinstance(blocks, list):
            return ""
        texts: List[str] = []
        for block in blocks:
            if isinstance(block, dict) and block.get("kind") == "text":
                texts.append(str(block.get("text") or ""))
        return "\n".join(part for part in texts if part)

    async def _latest_bot_text(self, bot_id: str) -> str:
        snapshot = await self.rpc("threads.get", {"botId": bot_id})
        messages = snapshot.get("messages") if isinstance(snapshot, dict) else []
        for message in reversed(messages or []):
            if isinstance(message, dict) and message.get("role") == "bot":
                text = self._message_text(message.get("blocks"))
                if text:
                    return text
        return ""

    @staticmethod
    def _pending_input_key(project_id: str, run_id: str, message_id: str) -> str:
        """构造仅供进程内去重的 key，不把上游 ID 暴露为前端请求 ID。"""
        return f"{project_id}\0{run_id}\0{message_id}"

    async def _register_pending_input(
        self,
        *,
        mapping: Mapping[str, Any],
        run_id: str,
        message_id: str,
        block: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """把官方 ask block 映射为 Codebot 可交互事件。"""
        project_id = str(mapping["project_id"])
        key = self._pending_input_key(project_id, run_id, message_id)
        raw_actions = block.get("actions") if isinstance(block.get("actions"), list) else []
        upstream_actions: List[Dict[str, str]] = []
        for item in raw_actions:
            if not isinstance(item, Mapping):
                continue
            action_id = str(item.get("id") or "").strip()
            label = str(item.get("label") or action_id).strip()
            if action_id and label:
                upstream_actions.append({"id": action_id, "label": label})
        approval = bool(block.get("approvalEffectId")) and {item["id"] for item in upstream_actions}.issuperset(
            {"allow", "deny"}
        )
        secret_input = str(block.get("input") or "").lower() == "secret" and not approval

        async with self._pending_input_lock:
            request_id = self._pending_input_keys.get(key)
            if not request_id:
                request_id = f"rakazo-{secrets.token_urlsafe(24)}"
                self._pending_input_keys[key] = request_id
            self._pending_inputs[request_id] = {
                "request_id": request_id,
                "key": key,
                "project_id": project_id,
                "conversation_id": int(mapping["conversation_id"]),
                "bot_id": str(mapping["bot_id"]),
                "run_id": run_id,
                "message_id": message_id,
                "approval": approval,
                "secret": secret_input,
                "action_ids": [item["id"] for item in upstream_actions],
                "state": "pending",
            }

        question = str(block.get("text") or "Rakazo 正在等待你的回答").strip()
        detail = str(block.get("detail") or "").strip()
        if approval:
            label_by_id = {item["id"]: item["label"] for item in upstream_actions}
            actions: List[Dict[str, Any]] = []
            if "allow" in label_by_id:
                actions.append({"label": "允许一次", "reply": "once", "type": "primary"})
            if "always" in label_by_id:
                actions.append({"label": "总是允许", "reply": "always", "type": "warning"})
            if "deny" in label_by_id:
                actions.append({"label": "拒绝", "reply": "reject", "type": "danger"})
            return {
                "type": "tool_event",
                "source": "rakazo",
                "event_type": "permission.requested",
                "summary": question,
                "detail": detail,
                "requires_user_action": True,
                "request_id": request_id,
                "actions": actions,
                "data": {"request_id": request_id, "source": "rakazo"},
            }

        options = [
            {"label": item["label"], "value": item["id"], "description": ""}
            for item in upstream_actions
        ]
        question_payload = {
            "id": "answer",
            "header": "Rakazo",
            "question": question,
            "multiple": False,
            "custom": not bool(options),
            "input_type": "password" if secret_input else "textarea",
            "options": options,
        }
        return {
            "type": "meta_event",
            "source": "rakazo",
            "event_type": "question.asked",
            "summary": question,
            "detail": detail or question,
            "requires_user_action": True,
            "request_id": request_id,
            "question": question,
            "questions": [question_payload],
            "actions": [],
            "data": {"request_id": request_id, "source": "rakazo", "questions": [question_payload]},
        }

    async def _clear_pending_inputs(
        self,
        *,
        project_id: Optional[str] = None,
        run_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> None:
        async with self._pending_input_lock:
            stale = [
                request_id
                for request_id, item in self._pending_inputs.items()
                if (project_id is None or item.get("project_id") == project_id)
                and (run_id is None or item.get("run_id") == run_id)
                and (message_id is None or item.get("message_id") == message_id)
            ]
            for request_id in stale:
                item = self._pending_inputs.pop(request_id, None)
                if item:
                    self._pending_input_keys.pop(str(item.get("key") or ""), None)

    async def _has_pending_input(self, *, project_id: str, run_id: str) -> bool:
        async with self._pending_input_lock:
            return any(
                item.get("project_id") == project_id
                and item.get("run_id") == run_id
                and item.get("state") == "pending"
                for item in self._pending_inputs.values()
            )

    async def _claim_pending_input(
        self,
        request_id: str,
        *,
        conversation_id: Optional[int | str],
        expected_approval: bool,
    ) -> Dict[str, Any]:
        async with self._pending_input_lock:
            item = self._pending_inputs.get(str(request_id or ""))
            if not item or item.get("state") != "pending":
                raise RakazoRuntimeError("Rakazo 请求已过期、已回复或不存在", status_code=404)
            if bool(item.get("approval")) != expected_approval:
                raise RakazoRuntimeError("Rakazo 请求类型与回复接口不匹配", status_code=409)
            if conversation_id is not None and int(item["conversation_id"]) != int(conversation_id):
                raise RakazoRuntimeError("Rakazo 请求不属于当前对话", status_code=403)
            item["state"] = "replying"
            return dict(item)

    async def _finish_pending_input(self, item: Mapping[str, Any], *, restore: bool = False) -> None:
        async with self._pending_input_lock:
            request_id = str(item.get("request_id") or "")
            current = self._pending_inputs.get(request_id)
            if restore and current:
                current["state"] = "pending"
                return
            removed = self._pending_inputs.pop(request_id, None)
            if removed:
                self._pending_input_keys.pop(str(removed.get("key") or ""), None)

    async def reply_permission(
        self,
        request_id: str,
        reply: str,
        *,
        conversation_id: Optional[int | str] = None,
    ) -> Dict[str, Any]:
        """回复 Rakazo 审批卡；Codebot 语义被显式映射到上游 action id。"""
        item = await self._claim_pending_input(
            request_id,
            conversation_id=conversation_id,
            expected_approval=True,
        )
        upstream_answer = {"once": "allow", "always": "always", "reject": "deny"}.get(str(reply or ""))
        if not upstream_answer or upstream_answer not in set(item.get("action_ids") or []):
            await self._finish_pending_input(item, restore=True)
            raise RakazoRuntimeError("Rakazo 审批动作无效或上游未提供该动作", status_code=400)
        try:
            await self.rpc(
                "threads.answer",
                {
                    "botId": item["bot_id"],
                    "runId": item["run_id"],
                    "messageId": item["message_id"],
                    "answer": upstream_answer,
                },
            )
        except Exception:
            await self._finish_pending_input(item, restore=True)
            raise
        await self._finish_pending_input(item)
        self.audit(str(item["project_id"]), "permission.reply", "success", {"runId": item["run_id"], "reply": reply})
        return {"ok": True, "secret": False, "reply": reply}

    async def reply_question(
        self,
        request_id: str,
        *,
        answer: str = "",
        answers: Optional[List[List[str]]] = None,
        reject: bool = False,
        conversation_id: Optional[int | str] = None,
    ) -> Dict[str, Any]:
        """回复普通/敏感 ask；敏感答案不写日志、审计或返回体。"""
        item = await self._claim_pending_input(
            request_id,
            conversation_id=conversation_id,
            expected_approval=False,
        )
        selected = str(answer or "").strip()
        if not selected and answers is not None:
            flattened = [str(value).strip() for group in answers if isinstance(group, list) for value in group if str(value).strip()]
            if len(flattened) == 1:
                selected = flattened[0]
            elif len(flattened) > 1:
                await self._finish_pending_input(item, restore=True)
                raise RakazoRuntimeError("Rakazo 当前 ask 合同只接受一个回答", status_code=400)
        try:
            if reject:
                # 普通 ask 没有官方 reject RPC。取消意味着明确停止这一轮，不能
                # 伪造空答案或让运行永远停在 waiting_input。
                await self.rpc("threads.stop", {"botId": item["bot_id"]})
            else:
                if not selected:
                    raise RakazoRuntimeError("缺少 Rakazo 问题回答", status_code=400)
                allowed = set(item.get("action_ids") or [])
                if allowed and selected not in allowed:
                    raise RakazoRuntimeError("回答不属于 Rakazo 提供的可选动作", status_code=400)
                await self.rpc(
                    "threads.answer",
                    {
                        "botId": item["bot_id"],
                        "runId": item["run_id"],
                        "messageId": item["message_id"],
                        "answer": selected,
                    },
                )
        except Exception:
            await self._finish_pending_input(item, restore=True)
            raise
        await self._finish_pending_input(item)
        self.audit(
            str(item["project_id"]),
            "question.reply",
            "success",
            {"runId": item["run_id"], "rejected": bool(reject), "secret": bool(item.get("secret"))},
        )
        return {
            "ok": True,
            "secret": bool(item.get("secret")),
            "rejected": bool(reject),
            "reply": "" if item.get("secret") else selected,
        }

    async def _subscribe_events(self, bot_id: str, cursor: int) -> AsyncIterator[Dict[str, Any]]:
        timeout = httpx.Timeout(660.0, connect=10.0, read=660.0, write=20.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "POST",
                self._rpc_url("threads.subscribe"),
                headers={**self._rpc_headers(), "Accept": "text/event-stream"},
                json={"json": {"botId": bot_id, "cursor": int(cursor)}},
            ) as response:
                if response.status_code >= 400:
                    raw = (await response.aread())[:4096]
                    raise RakazoRuntimeError(
                        f"Rakazo 事件订阅失败（HTTP {response.status_code}）：{raw.decode('utf-8', 'replace')}"
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        payload = json.loads(raw)
                    except ValueError:
                        continue
                    event = payload.get("json") if isinstance(payload, dict) and "json" in payload else payload
                    if isinstance(event, dict):
                        yield event

    async def run_turn_stream(
        self,
        *,
        conversation_id: int | str,
        message: str,
    ) -> AsyncIterator[Dict[str, Any]]:
        """向固定 Rakazo Thread 发送消息并映射为 Codebot NDJSON 事件。"""
        mapping = self.mapping_by_conversation(conversation_id)
        if not mapping:
            raise RakazoRuntimeError("该 Codebot 对话没有 Rakazo 主会话映射", status_code=409)
        project_id = str(mapping["project_id"])
        # 聊天页可能在远端数据库被重置后直接发送。发送前复核并在唯一项目锁内
        # 修复失效 Bot，保留 Codebot 对话、所选模型和项目权限。
        mapping, _ = await self._ensure_remote_project(project_id)
        route = self.verified_route(str(mapping.get("model_route_id") or ""))
        if mapping.get("model_route_fingerprint") != route.route_fingerprint:
            raise RakazoRuntimeError("项目所选模型路由已变化，请重新探测并明确选择模型", status_code=409)
        async with self._active_lock:
            if project_id in self._active_runs:
                raise RakazoRuntimeError("该项目已有一个 Rakazo 任务正在运行", status_code=409)
            self._active_runs[project_id] = "starting"

        bot_id = str(mapping["bot_id"])
        run_id = ""
        final_text = ""
        seen_by_message: Dict[str, str] = {}
        try:
            # 发送前幂等迁移旧版随机占位凭据。这样已经创建的主会话无需删除、
            # 重新授权或重装 Docker，首次再次发送即可恢复正确的私网 Bearer。
            await self._ensure_local_provider_connection(route.route_id)
            # Rakazo 自身 UI 也能修改 Bot。每轮发送前再次核对远端身份，避免映射
            # 仍记录模型 A、实际却由模型 B 回答，绝不静默修正或 fallback。
            remote_bot = await self.rpc("bots.get", {"botId": bot_id})
            actual_provider = str((remote_bot or {}).get("modelProvider") or "") if isinstance(remote_bot, dict) else ""
            actual_model = str((remote_bot or {}).get("modelId") or "") if isinstance(remote_bot, dict) else ""
            if actual_provider != RAKAZO_LOCAL_PROVIDER or actual_model != route.route_id:
                raise RakazoRuntimeError(
                    "Rakazo Bot 模型已在 Codebot 外部变更；请在 Codebot 中重新选择已验证模型后再发送",
                    status_code=409,
                )
            head = await self.rpc("threads.head", {"botId": bot_id})
            cursor = int((head or {}).get("cursor", -1)) if isinstance(head, dict) else -1
            sent = await self.rpc(
                "threads.send",
                {"botId": bot_id, "text": str(message), "clientNonce": f"codebot-{secrets.token_hex(12)}"},
            )
            if not isinstance(sent, dict):
                raise RakazoRuntimeError("Rakazo threads.send 返回结构无效")
            run_id = str(sent.get("runId") or "")
            if not run_id:
                raise RakazoRuntimeError("Rakazo threads.send 未返回 runId")
            self._active_runs[project_id] = run_id
            self._record_turn_route(mapping, run_id, route)
            self.audit(project_id, "turn.start", "success", {"runId": run_id, "routeId": route.route_id})
            yield {
                "type": "tool_event",
                "source": "rakazo",
                "event_type": "model.route",
                "summary": f"Rakazo · {route.route_id}",
                "data": {
                    "executor": "rakazo",
                    "provider": route.opencode_provider,
                    "model": route.route_id,
                    "protocol": route.upstream_protocol,
                    "routeFingerprint": route.route_fingerprint,
                },
            }
            async for event in self._subscribe_events(bot_id, cursor):
                event_run_id = str(event.get("runId") or "")
                if event_run_id and event_run_id != run_id:
                    continue
                event_type = str(event.get("type") or "")
                raw_payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                payload = _redact_sensitive(raw_payload)
                if event_type in {"thread.message.created", "thread.message.updated"} and payload.get("role") == "bot":
                    message_id = str(payload.get("messageId") or "bot")
                    text = self._message_text(payload.get("blocks"))
                    previous = seen_by_message.get(message_id, "")
                    delta = text[len(previous):] if text.startswith(previous) else text
                    seen_by_message[message_id] = text
                    if delta:
                        final_text = text
                        yield {
                            "type": "content_delta",
                            "delta": delta,
                            "content": final_text,
                            "event_type": "assistant.delta",
                            "source": "rakazo",
                        }
                    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), list) else []
                    pending_ask = next(
                        (
                            block for block in blocks
                            if isinstance(block, Mapping)
                            and block.get("kind") == "ask"
                            and block.get("status") != "answered"
                        ),
                        None,
                    )
                    if pending_ask:
                        yield await self._register_pending_input(
                            mapping=mapping,
                            run_id=run_id,
                            message_id=message_id,
                            block=pending_ask,
                        )
                    elif event_type == "thread.message.updated":
                        await self._clear_pending_inputs(
                            project_id=project_id,
                            run_id=run_id,
                            message_id=message_id,
                        )
                elif event_type in {"thread.progress", "agent.tool.called", "thread.meta"}:
                    yield {
                        "type": "tool_event",
                        "source": "rakazo",
                        "event_type": "tool.updated" if event_type == "thread.progress" else "tool.started",
                        "summary": str(payload.get("text") or payload.get("name") or event_type),
                        "data": payload,
                    }
                elif event_type in {"thread.computer", "computer.status"}:
                    yield {"type": "tool_event", "source": "rakazo", "event_type": "computer.updated", "summary": "Computer 状态已更新", "data": payload}
                elif event_type == "memory.revised":
                    yield {"type": "tool_event", "source": "rakazo", "event_type": "memory.updated", "summary": "Rakazo 记忆已更新", "data": payload}
                elif event_type == "run.waiting_input":
                    if not await self._has_pending_input(project_id=project_id, run_id=run_id):
                        raise RakazoRuntimeError(
                            "Rakazo 进入等待输入状态，但事件流没有提供可验证的 ask messageId；已失败关闭",
                            status_code=409,
                        )
                    yield {
                        "type": "tool_event",
                        "source": "rakazo",
                        "event_type": "status",
                        "summary": "Rakazo 已暂停并等待用户回答",
                        "data": payload,
                    }
                elif event_type in {"thread.ask", "thread.choice"}:
                    # 当前官方实现把可回答 ask 放在 message.created 中；孤立旧事件
                    # 没有 threads.answer 所需的 messageId，不能伪造可点击审批。
                    yield {
                        "type": "tool_event",
                        "source": "rakazo",
                        "event_type": "status",
                        "summary": "收到 Rakazo 旧式提示事件，等待对应的持久消息",
                        "data": payload,
                    }
                elif event_type == "computer.takeover.requested":
                    try:
                        await self.rpc("threads.stop", {"botId": bot_id})
                    finally:
                        raise RakazoRuntimeError(
                            "Rakazo 请求实时 Computer 接管；Codebot 尚未提供安全的屏幕控制租约界面，本轮已停止",
                            status_code=409,
                        )
                elif event_type == "usage.recorded":
                    yield {"type": "meta_event", "source": "rakazo", "event_type": "usage", "data": payload}
                elif event_type == "run.failed":
                    raise RakazoRuntimeError(str(payload.get("error") or payload.get("message") or "Rakazo 运行失败"))
                elif event_type == "run.cancelled":
                    yield {"type": "error", "source": "rakazo", "content": "Rakazo 任务已中断", "event_type": "run.cancelled"}
                    return
                elif event_type == "run.completed":
                    if not final_text:
                        final_text = await self._latest_bot_text(bot_id)
                        if final_text:
                            yield {
                                "type": "content_delta",
                                "delta": final_text,
                                "content": final_text,
                                "event_type": "assistant.delta",
                                "source": "rakazo",
                            }
                    self.audit(project_id, "turn.complete", "success", {"runId": run_id})
                    yield {"type": "done", "source": "rakazo", "content": final_text, "event_type": "done"}
                    return
            raise RakazoRuntimeError("Rakazo 事件流在任务完成前结束")
        except asyncio.CancelledError:
            if run_id:
                try:
                    await self.rpc("threads.stop", {"botId": bot_id})
                except Exception:
                    pass
            raise
        except Exception as exc:
            self.audit(project_id, "turn.complete", "failed", {"runId": run_id, "error": _safe_error(exc)})
            raise
        finally:
            if run_id:
                await self._clear_pending_inputs(project_id=project_id, run_id=run_id)
            async with self._active_lock:
                self._active_runs.pop(project_id, None)

    async def interrupt_project(self, project_id: str) -> Dict[str, Any]:
        mapping, _ = await self._ensure_remote_project(project_id)
        await self.rpc("threads.stop", {"botId": mapping["bot_id"]})
        await self._clear_pending_inputs(project_id=project_id)
        self.audit(project_id, "turn.interrupt", "success", {"runId": self._active_runs.get(project_id, "")})
        return {"ok": True}

    async def delete_project(self, project_id: str) -> Dict[str, Any]:
        """彻底删除一个 Rakazo 主会话的项目专属资源。

        删除范围只包含该项目的 Bot/Thread/Memory、项目 MCP、Computer 容器、
        加盐私有网络、专属 home 和 Codebot Rakazo 映射/路由记录。Docker
        Desktop、共享 Rakazo Compose/Postgres/镜像以及宿主项目目录永远不在范围内。
        """
        mapping = self.mapping_by_project_id(project_id)
        if not mapping:
            raise RakazoRuntimeError("Rakazo 项目映射不存在", status_code=404)
        if project_id in self._active_runs:
            raise RakazoRuntimeError("Rakazo 正在执行任务，请先停止后再删除项目", status_code=409)
        await self._clear_pending_inputs(project_id=project_id)
        bot_id = str(mapping["bot_id"])
        mcp_server_id = str(mapping.get("mcp_server_id") or "")
        with self._connect() as conn:
            retired = [
                str(row[0])
                for row in conn.execute(
                    "SELECT bot_id FROM rakazo_retired_bots WHERE project_id = ?",
                    (project_id,),
                ).fetchall()
            ]
        bot_ids = list(dict.fromkeys([bot_id, *retired]))

        if mcp_server_id:
            try:
                await self.rpc("mcp.servers.remove", {"id": mcp_server_id})
            except Exception as exc:
                if not await self._confirm_mcp_server_missing(mcp_server_id, exc):
                    raise
        orphan_mcp_ids = await self._remove_project_mcp_by_slug(project_id)
        try:
            await self.rpc("bots.remove", {"botId": bot_id, "deleteMemories": True})
        except Exception as exc:
            # 上游 Bot 已丢失时把删除视为幂等成功，但仍继续清理宿主 Docker
            # 遗留；认证、数据库或合同错误不能被吞掉。
            if not await self._confirm_bot_missing(bot_id, exc):
                raise

        cleanup: List[Dict[str, Any]] = []
        for cleanup_bot_id in bot_ids:
            runtime_cleanup = await self._remove_project_computer_runtime(cleanup_bot_id)
            await self._project_home_operation("delete", cleanup_bot_id)
            cleanup.append({"botId": cleanup_bot_id, **runtime_cleanup})

        # 只有远端与 Docker 项目资源全部确认清理后才删除本地映射；任一步失败
        # 时保留 Codebot 对话，用户可查看错误并安全重试，不产生“界面已消失但
        # 容器还在”的半删除状态。
        with self._connect() as conn:
            conn.execute("DELETE FROM rakazo_turn_routes WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM rakazo_audit WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM rakazo_retired_bots WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM rakazo_projects WHERE project_id = ?", (project_id,))
        try:
            await self._sync_shell_approval_rule(project_id=project_id, command_allowed=True)
        except Exception as exc:
            # 项目本体已完成删除；审批规则只可能因此更保守（继续逐次询问），
            # 不能为了优化剩余项目而伪造删除失败。
            logger.warning(f"删除项目后收敛 Rakazo shell 审批规则失败：{_safe_error(exc)}")
        return {
            "projectId": project_id,
            "conversationId": int(mapping["conversation_id"]),
            "botIds": bot_ids,
            "mcpServerIds": list(dict.fromkeys([mcp_server_id, *orphan_mcp_ids])) if mcp_server_id else orphan_mcp_ids,
            "cleanup": cleanup,
            "deleted": {
                "botThreadMemory": True,
                "projectMcp": True,
                "computer": True,
                "privateNetwork": True,
                "computerHome": True,
                "codebotMapping": True,
            },
            "retained": {
                "projectFiles": str(mapping["project_dir"]),
                "dockerDesktop": True,
                "sharedRakazoRuntime": True,
                "sharedPostgres": True,
                "images": True,
            },
        }

    async def computer_action(self, project_id: str, action: str) -> Dict[str, Any]:
        mapping, _ = await self._ensure_remote_project(project_id)
        procedure = {
            "status": "computer.status",
            "boot": "computer.boot",
            "stop": "computer.stop",
            "restart": "computer.recover",
        }.get(action)
        if not procedure:
            raise RakazoRuntimeError("不支持的 Computer 操作", status_code=400)
        if action in {"stop", "restart"} and project_id in self._active_runs:
            raise RakazoRuntimeError("任务运行中不能停止或重启 Computer", status_code=409)
        result = await self.rpc(procedure, {"botId": mapping["bot_id"]})
        self.audit(project_id, f"computer.{action}", "success", {})
        return result if isinstance(result, dict) else {"value": result}

    async def memory_summary(self, project_id: str) -> Dict[str, Any]:
        mapping, _ = await self._ensure_remote_project(project_id)
        documents = await self.rpc("memory.list", {"botId": mapping["bot_id"], "scope": "bot"})
        items = documents if isinstance(documents, list) else []
        summaries = []
        for item in items[:20]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "")
            summaries.append({
                "id": item.get("id"),
                "title": item.get("title") or item.get("name") or item.get("path") or "长期记忆",
                "summary": content[:280] + ("…" if len(content) > 280 else ""),
                "updatedAt": item.get("updatedAt") or item.get("updated_at"),
            })
        return {"enabled": True, "count": len(items), "items": summaries}

    def _compatibility_manifest_path(self) -> Path:
        candidates: List[Path] = []
        resources = str(os.environ.get("CODEBOT_RESOURCES_DIR") or "").strip()
        if resources:
            candidates.append(Path(resources) / "integrations" / "rakazo" / "compatibility.json")
        candidates.extend([
            Path(__file__).resolve().parents[2] / "integrations" / "rakazo" / "compatibility.json",
            settings.BASE_DIR / "integrations" / "rakazo" / "compatibility.json",
        ])
        return next((path for path in candidates if path.is_file()), candidates[0])

    def compatibility_manifest(self) -> Dict[str, Any]:
        path = self._compatibility_manifest_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {"schemaVersion": 1, "versions": {}}
        return payload if isinstance(payload, dict) else {"schemaVersion": 1, "versions": {}}

    async def check_update(self) -> Dict[str, Any]:
        """只查询官方最新稳定 Release；草稿、预发布和 moving tag 一律忽略。"""
        url = f"https://api.github.com/repos/{RAKAZO_REPOSITORY}/releases/latest"
        timeout = httpx.Timeout(15.0, connect=8.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.get(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Codebot-Rakazo-Update/1"})
        if response.status_code == 404:
            return {
                "available": False,
                "installable": False,
                "reason": "Rakazo 尚无官方稳定 Release；beta/edge/latest 不进入生产更新通道。",
            }
        if response.status_code >= 400:
            raise RakazoRuntimeError(f"查询 Rakazo 官方 Release 失败：HTTP {response.status_code}")
        data = response.json()
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            return {"available": False, "installable": False, "reason": "官方最新条目不是稳定 Release"}
        tag = str(data.get("tag_name") or "")
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            return {"available": False, "installable": False, "reason": f"拒绝非稳定不可变版本标签：{tag or 'unknown'}"}
        entry = self.compatibility_entry(tag)
        manifest_approved = (
            isinstance(entry, dict)
            and bool(entry.get("installable"))
            and bool(entry.get("imageDigest"))
        )
        return {
            "available": True,
            "version": tag,
            "name": data.get("name") or tag,
            "publishedAt": data.get("published_at"),
            "releaseUrl": data.get("html_url"),
            # 用户要求：不能完成真实候选环境与故障回滚时就放弃手动更新。
            # 即使未来有人只修改兼容清单，这个只读接口也不能突然变成更新器。
            "installable": False,
            "manualUpdateSupported": False,
            "manifestApprovedForFutureUpdater": manifest_approved,
            "compatibility": entry,
            "reason": (
                "该版本已进入未来更新器候选清单，但当前 Codebot 不提供手动安装或回滚。"
                if manifest_approved
                else "发现新版本，但 Codebot 兼容清单尚未批准，且当前不提供手动安装或回滚。"
            ),
        }

rakazo_runtime = RakazoRuntime()
