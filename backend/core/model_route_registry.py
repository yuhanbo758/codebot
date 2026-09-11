"""OpenCode 模型目录的共享路由注册表。

该模块是 Codebot 内部唯一的模型目录解析入口。Codex 与 Rakazo 都从同一份
OpenCode ``/provider`` 元数据生成路由，避免分别维护模型白名单或按模型名称
猜测协议。真实 API Key 只保存在后端进程内存中，不会进入公开模型对象、路由
指纹、数据库或容器环境。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from loguru import logger

from config import app_config
from core.codex_model_bridge import bridge_adapter, bridge_protocol_for_npm


_MAX_PROVIDER_CATALOG_BYTES = 16 * 1024 * 1024
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ALLOWED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_OPENAI_OAUTH_PROVIDER = "openai"
_OPENAI_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_OPENAI_OAUTH_ISSUER = "https://auth.openai.com"
# OpenCode 的内置 OpenAI OAuth 插件会把 Responses 请求改写到该 Codex
# 订阅端点。Codebot 只复刻这一条已经由 OpenCode 当前连接声明的模型采样
# 路由，不创建 OpenCode Session，也不进入 OpenCode Agent 循环。
_OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_OAUTH_REFRESH_SKEW_MS = 60_000


@dataclass(frozen=True)
class ModelRoute:
    """一个由 OpenCode 目录解析出的精确模型路由。

    前半部分字段保留 Codex 现有命名，确保抽取共享注册表不会改变 App Server
    配置语义；后半部分提供 Rakazo 所需的兼容状态和无密钥路由指纹。
    """

    display_id: str
    display_name: str
    opencode_provider: str
    opencode_model: str
    codex_provider: str
    codex_model: str
    base_url: str
    env_key: str
    api_key: str = field(repr=False)
    upstream_protocol: str = "responses"
    adapter_package: str = "@ai-sdk/openai"
    request_headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    query_params: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None
    reasoning: bool = False
    reasoning_efforts: tuple[str, ...] = ()
    credential_mode: str = "none"
    compatibility_status: str = "probe_required"
    incompatibility_reason: str = ""
    connected: bool = False
    # OAuth 机密只驻留 Codebot 后端内存；这些字段不进入 repr、公开模型对象、
    # 路由指纹、数据库或 Rakazo 容器环境。
    oauth_refresh_token: str = field(default="", repr=False)
    oauth_expires_at_ms: int = field(default=0, repr=False)
    oauth_account_id: str = field(default="", repr=False)

    @property
    def route_id(self) -> str:
        return self.display_id

    @property
    def provider_id(self) -> str:
        return self.opencode_provider

    @property
    def model_id(self) -> str:
        return self.opencode_model

    @property
    def protocol(self) -> str:
        return self.upstream_protocol

    @property
    def uses_bridge(self) -> bool:
        return self.upstream_protocol != "responses"

    @property
    def route_fingerprint(self) -> str:
        """返回不含凭据、Cookie、header/query 值的稳定路由指纹。"""
        payload = {
            "provider": self.opencode_provider,
            "model": self.opencode_model,
            "upstreamModel": self.codex_model,
            "protocol": self.upstream_protocol,
            "baseUrl": self.base_url,
            "adapter": self.adapter_package,
            "contextWindow": self.context_window,
            "maxOutputTokens": self.max_output_tokens,
            "reasoning": self.reasoning,
            "reasoningEfforts": list(self.reasoning_efforts),
            "headerNames": sorted(key.lower() for key, _ in self.request_headers),
            "queryNames": sorted(key for key, _ in self.query_params),
            "credentialMode": self.credential_mode,
            "connected": self.connected,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def public_model(
        self,
        *,
        compatibility_status: Optional[str] = None,
        incompatibility_reason: Optional[str] = None,
        last_probe_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """返回不含上游地址和凭据的统一模型描述。"""
        adapter = bridge_adapter(self.upstream_protocol)
        protocol_label = (
            "OpenCode Responses"
            if self.upstream_protocol == "responses"
            else f"OpenCode {adapter.label} 兼容桥" if adapter else "OpenCode 协议待适配"
        )
        public_protocol = (
            "responses"
            if self.upstream_protocol == "responses"
            else adapter.public_protocol if adapter else self.upstream_protocol
        )
        # Codex 既有调用不传状态，仍按“路由已可装载”展示为可运行；Rakazo 会
        # 显式传入真实探测状态，在通过文本、流式和工具回环前保持禁用。
        status = (
            compatibility_status
            if compatibility_status is not None
            else ("verified" if self.compatibility_status == "probe_required" else self.compatibility_status)
        )
        reason = self.incompatibility_reason if incompatibility_reason is None else incompatibility_reason
        return {
            "id": self.display_id,
            "routeId": self.route_id,
            "name": self.display_name,
            "displayName": f"{self.display_name} · {protocol_label}",
            "provider": self.opencode_provider,
            "model": self.opencode_model,
            "source": "opencode",
            "runnable": status == "verified",
            "protocol": public_protocol,
            "transport": "codebot-bridge" if self.uses_bridge else "direct",
            "modelProvider": self.codex_provider,
            "adapterPackage": self.adapter_package,
            "supportedReasoningEfforts": list(self.reasoning_efforts),
            "contextWindow": self.context_window,
            "maxOutputTokens": self.max_output_tokens,
            "capabilities": {
                "reasoning": self.reasoning,
                # Agent 模型必须通过探测后才声明工具调用可用。
                "tools": status == "verified",
            },
            "routeFingerprint": self.route_fingerprint,
            "credentialMode": self.credential_mode,
            "compatibilityStatus": status,
            "incompatibilityReason": reason,
            "lastProbeAt": last_probe_at,
        }


class ModelRouteRegistry:
    """发现、解析并缓存 OpenCode 模型目录。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # 同一时刻只允许一个线程刷新 OpenCode OAuth，避免旋转型 refresh token
        # 被并发消费两次。模型采样不会持有目录锁等待外部网络。
        self._oauth_refresh_lock = threading.Lock()
        self._routes: Dict[str, ModelRoute] = {}
        self._catalog: Dict[str, ModelRoute] = {}
        self._incompatible: Dict[str, str] = {}
        self._last_error = ""

    @staticmethod
    def opencode_auth_paths() -> List[Path]:
        """返回 OpenCode 官方凭据文件候选位置，不创建或修改文件。"""
        paths: List[Path] = []
        xdg_data_home = str(os.environ.get("XDG_DATA_HOME") or "").strip()
        if xdg_data_home:
            paths.append(Path(xdg_data_home).expanduser() / "opencode" / "auth.json")
        paths.append(Path.home() / ".local" / "share" / "opencode" / "auth.json")
        if os.name == "nt":
            for variable in ("LOCALAPPDATA", "APPDATA"):
                root = str(os.environ.get(variable) or "").strip()
                if root:
                    paths.append(Path(root) / "opencode" / "auth.json")
        result: List[Path] = []
        for path in paths:
            if path not in result:
                result.append(path)
        return result

    @classmethod
    def load_opencode_credentials(cls) -> Dict[str, Dict[str, Any]]:
        """读取 OpenCode 当前连接的服务端凭据描述。

        API Key 与 OAuth token 只用于 Codebot 宿主进程的纯模型采样。公开 API、
        日志、数据库和 Rakazo 容器都拿不到这些值。未知 OAuth Provider 不会被
        猜测成可复用路由；当前仅显式实现 OpenCode 内置 OpenAI OAuth 合同。
        """
        credentials: Dict[str, Dict[str, Any]] = {}
        for path in cls.opencode_auth_paths():
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning(f"读取 OpenCode provider 凭据索引失败：{path}: {exc}")
                continue
            if not isinstance(raw, dict):
                continue
            for provider_id, value in raw.items():
                if not isinstance(value, dict):
                    continue
                credential_type = str(value.get("type") or "").strip().lower()
                if credential_type == "api":
                    api_key = str(value.get("key") or "").strip()
                    if api_key:
                        credentials[str(provider_id)] = {"type": "api", "key": api_key}
                elif credential_type == "oauth":
                    try:
                        expires = int(value.get("expires") or 0)
                    except (TypeError, ValueError):
                        expires = 0
                    credentials[str(provider_id)] = {
                        "type": "oauth",
                        "access": str(value.get("access") or "").strip(),
                        "refresh": str(value.get("refresh") or "").strip(),
                        "expires": expires,
                        "accountId": str(value.get("accountId") or "").strip(),
                    }
            break
        return credentials

    @classmethod
    def load_opencode_api_credentials(cls) -> Dict[str, str]:
        """保留旧调用接口；只返回 API Key，不把 OAuth 混成普通 API 密钥。"""
        return {
            provider_id: str(value.get("key") or "")
            for provider_id, value in cls.load_opencode_credentials().items()
            if str(value.get("type") or "") == "api" and value.get("key")
        }

    @staticmethod
    def opencode_server_url() -> str:
        """优先使用 main.py 已连接的真实 OpenCode 地址。"""
        try:
            from api.routes import gateway as gateway_router

            client = getattr(gateway_router, "opencode_ws", None)
            actual = str(getattr(client, "base_url", "") or "").strip()
            if actual:
                return actual.rstrip("/")
        except Exception:
            pass
        return str(app_config.opencode.server_url or "http://127.0.0.1:11200").rstrip("/")

    @staticmethod
    def provider_env_name(provider_id: str, base_url: str) -> str:
        digest = hashlib.sha256(f"{provider_id}\0{base_url}".encode("utf-8")).hexdigest()[:10].upper()
        safe_provider = "".join(ch if ch.isalnum() else "_" for ch in provider_id.upper()).strip("_")
        return f"CODEBOT_OPENCODE_{safe_provider[:24] or 'PROVIDER'}_{digest}_KEY"

    @staticmethod
    def codex_provider_id(provider_id: str, base_url: str, protocol: str = "responses") -> str:
        digest = hashlib.sha256(f"{provider_id}\0{base_url}\0{protocol}".encode("utf-8")).hexdigest()[:8]
        safe_provider = "".join(ch if ch.isalnum() else "_" for ch in provider_id.lower()).strip("_")
        return f"codebot_{safe_provider[:28] or 'opencode'}_{digest}"

    @staticmethod
    def protocol_label(npm_package: str) -> str:
        package = str(npm_package or "").strip().lower()
        if package == "@ai-sdk/openai":
            return "Responses API"
        protocol = bridge_protocol_for_npm(package)
        adapter = bridge_adapter(protocol or "")
        return adapter.label if adapter is not None else (package or "未知协议")

    @classmethod
    def parse_provider_catalog(
        cls,
        payload: Any,
        credentials: Mapping[str, Any],
        environ: Optional[Mapping[str, str]] = None,
    ) -> tuple[Dict[str, ModelRoute], Dict[str, str], Dict[str, ModelRoute]]:
        """解析完整目录，同时返回 Codex 可运行路由和不可用原因。

        ``catalog`` 覆盖 OpenCode 当前公开的全部模型；``routes`` 只包含已连接、
        有明确协议且凭据可由 Codebot 服务端安全解析的路由。未知协议从不猜成
        OpenAI-compatible。
        """
        env = dict(environ) if environ is not None else dict(os.environ)
        if not isinstance(payload, dict):
            return {}, {}, {}
        connected_ids = {str(item) for item in (payload.get("connected") or [])}
        providers = payload.get("all") if isinstance(payload.get("all"), list) else []
        routes: Dict[str, ModelRoute] = {}
        catalog: Dict[str, ModelRoute] = {}
        incompatible: Dict[str, str] = {}

        for provider in providers:
            if not isinstance(provider, dict):
                continue
            provider_id = str(provider.get("id") or "").strip()
            if not provider_id:
                continue
            provider_name = str(provider.get("name") or provider_id)
            provider_connected = provider_id in connected_ids
            provider_options = provider.get("options") if isinstance(provider.get("options"), dict) else {}
            configured_base_url = str(
                provider_options.get("baseURL") or provider_options.get("base_url") or ""
            ).strip()
            raw_credential = credentials.get(provider_id)
            # 测试和旧调用仍可传 ``provider -> key``；新目录发现会传带类型的
            # OpenCode 凭据对象，以便显式区分 API 与受支持的 OAuth 合同。
            if isinstance(raw_credential, Mapping):
                credential = dict(raw_credential)
            elif raw_credential:
                credential = {"type": "api", "key": str(raw_credential)}
            else:
                credential = {}
            credential_type = str(credential.get("type") or "").strip().lower()
            is_openai_oauth = provider_id == _OPENAI_OAUTH_PROVIDER and credential_type == "oauth"
            credential_value = credential.get("access") if is_openai_oauth else credential.get("key")
            api_key = str(credential_value or "").strip()
            oauth_refresh_token = str(credential.get("refresh") or "").strip() if is_openai_oauth else ""
            oauth_account_id = str(credential.get("accountId") or "").strip() if is_openai_oauth else ""
            try:
                oauth_expires_at_ms = int(credential.get("expires") or 0) if is_openai_oauth else 0
            except (TypeError, ValueError):
                oauth_expires_at_ms = 0
            if not api_key and credential_type != "oauth":
                for env_name in provider.get("env") or []:
                    candidate = str(env.get(str(env_name)) or "").strip()
                    if candidate:
                        api_key = candidate
                        break
            if not api_key and credential_type != "oauth":
                configured_key = str(provider_options.get("apiKey") or "").strip()
                if configured_key and not configured_key.startswith("{"):
                    api_key = configured_key

            models = provider.get("models") if isinstance(provider.get("models"), dict) else {}

            has_explicit_openai_endpoint = bool(configured_base_url) or any(
                isinstance(item, dict)
                and isinstance(item.get("api"), dict)
                and str(item["api"].get("url") or "").strip()
                for item in models.values()
            )

            for model_key, raw_model in models.items():
                if not isinstance(raw_model, dict):
                    continue
                full_id = f"{provider_id}/{model_key}"
                api = raw_model.get("api") if isinstance(raw_model.get("api"), dict) else {}
                npm_package = str(api.get("npm") or provider.get("npm") or "").strip()
                base_url = str(api.get("url") or configured_base_url).strip().rstrip("/")
                if is_openai_oauth:
                    # OpenCode OpenAI OAuth 的模型元数据故意不暴露普通 API URL；
                    # 内置插件会把这些已筛选模型统一改写到 Codex Responses 端点。
                    base_url = _OPENAI_CODEX_BASE_URL
                parsed_url = urlparse(base_url)
                declared_responses = npm_package == "@ai-sdk/openai"
                bridged_protocol = bridge_protocol_for_npm(npm_package)
                # 协议必须逐模型服从 OpenCode 当前目录声明。即使同一 Provider
                # 地址上的另一个模型使用 Responses，也不能据此把
                # ``@ai-sdk/openai-compatible`` 猜成 Responses：OpenCode 本身会
                # 为该包发送 Chat Completions，部分模型只接受这一合同。
                if declared_responses:
                    protocol = "responses"
                elif bridged_protocol:
                    protocol = bridged_protocol
                else:
                    protocol = "unknown"

                reason = ""
                status = "probe_required"
                if protocol == "unknown":
                    status = "protocol_pending"
                    reason = (
                        f"OpenCode 模型 {full_id} 使用 {cls.protocol_label(npm_package)}，"
                        "Codebot 尚未注册该协议的安全采样驱动。"
                    )
                elif parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                    status = "protocol_pending"
                    reason = f"OpenCode 模型 {full_id} 没有可用的模型 API 地址。"

                is_loopback = (parsed_url.hostname or "").lower() in _LOOPBACK_HOSTS
                oauth_only_openai = (
                    provider_id == "openai"
                    and not is_openai_oauth
                    and (not api_key or not has_explicit_openai_endpoint)
                )
                unsupported_oauth = credential_type == "oauth" and not is_openai_oauth
                refreshable_openai_oauth = is_openai_oauth and bool(api_key or oauth_refresh_token)
                if not reason and (
                    not provider_connected
                    or oauth_only_openai
                    or unsupported_oauth
                    or (not api_key and not is_loopback and not refreshable_openai_oauth)
                ):
                    status = "authorization_required"
                    reason = (
                        f"OpenCode 模型 {full_id} 的当前连接没有可由 Codebot 宿主安全代理的纯模型凭据；"
                        "请先在 OpenCode 中重新连接该 Provider。"
                    )

                variants = raw_model.get("variants") if isinstance(raw_model.get("variants"), dict) else {}
                efforts = tuple(str(value) for value in variants if str(value) in _ALLOWED_REASONING_EFFORTS)
                capabilities = raw_model.get("capabilities") if isinstance(raw_model.get("capabilities"), dict) else {}
                limits = raw_model.get("limit") if isinstance(raw_model.get("limit"), dict) else {}
                try:
                    context_window = int(limits.get("context")) if limits.get("context") else None
                except (TypeError, ValueError):
                    context_window = None
                try:
                    max_output_tokens = int(limits.get("output")) if limits.get("output") else None
                except (TypeError, ValueError):
                    max_output_tokens = None

                provider_headers = provider_options.get("headers") if isinstance(provider_options.get("headers"), dict) else {}
                model_headers = raw_model.get("headers") if isinstance(raw_model.get("headers"), dict) else {}
                request_headers = tuple(
                    (str(key), str(value))
                    for key, value in {**provider_headers, **model_headers}.items()
                    if isinstance(value, (str, int, float, bool))
                )
                if is_openai_oauth:
                    # 与 OpenCode 内置 OAuth 插件保持相同的请求身份合同；账号 ID
                    # 仅存在私有 header 值中，公开指纹只记录 header 名称。
                    oauth_headers: List[tuple[str, str]] = [
                        ("originator", "opencode"),
                        ("User-Agent", "Codebot OpenCode-OAuth model bridge"),
                    ]
                    if oauth_account_id:
                        oauth_headers.append(("ChatGPT-Account-Id", oauth_account_id))
                    residency = cls._oauth_residency(api_key)
                    if residency:
                        oauth_headers.append(("x-openai-internal-codex-residency", residency))
                    request_headers = tuple([*request_headers, *oauth_headers])
                raw_query = (
                    provider_options.get("queryParams")
                    if isinstance(provider_options.get("queryParams"), dict)
                    else provider_options.get("query_params")
                )
                query_params = tuple(
                    (str(key), str(value))
                    for key, value in (raw_query.items() if isinstance(raw_query, dict) else [])
                    if isinstance(value, (str, int, float, bool))
                )
                if protocol == "responses" and (request_headers or query_params):
                    protocol = "responses_proxy"

                routing_scope = base_url
                if request_headers or query_params:
                    routing_scope += "\0" + json.dumps(
                        {"headers": request_headers, "query": query_params},
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                codex_provider = cls.codex_provider_id(provider_id, routing_scope, protocol)
                credential_mode = (
                    "opencode_oauth"
                    if is_openai_oauth
                    else "loopback" if is_loopback and not api_key
                    else "api_key" if api_key
                    else "authorization_required"
                )
                route = ModelRoute(
                    display_id=full_id,
                    display_name=str(raw_model.get("name") or f"{provider_name} {model_key}"),
                    opencode_provider=provider_id,
                    opencode_model=str(model_key),
                    codex_provider=codex_provider,
                    codex_model=str(api.get("id") or raw_model.get("id") or model_key),
                    base_url=base_url,
                    env_key=cls.provider_env_name(provider_id, base_url) if api_key else "",
                    api_key=api_key,
                    upstream_protocol=protocol,
                    adapter_package=npm_package,
                    request_headers=request_headers,
                    query_params=query_params,
                    context_window=context_window,
                    max_output_tokens=max_output_tokens,
                    reasoning=bool(capabilities.get("reasoning")),
                    reasoning_efforts=efforts,
                    credential_mode=credential_mode,
                    compatibility_status=status,
                    incompatibility_reason=reason,
                    connected=provider_connected,
                    oauth_refresh_token=oauth_refresh_token,
                    oauth_expires_at_ms=oauth_expires_at_ms,
                    oauth_account_id=oauth_account_id,
                )
                catalog[full_id] = route
                if status == "probe_required":
                    routes[full_id] = route
                else:
                    incompatible[full_id] = reason
        return routes, incompatible, catalog

    @classmethod
    def parse_runnable_catalog(
        cls,
        payload: Any,
        credentials: Mapping[str, str],
        environ: Optional[Mapping[str, str]] = None,
    ) -> tuple[Dict[str, ModelRoute], Dict[str, str]]:
        routes, incompatible, _ = cls.parse_provider_catalog(payload, credentials, environ)
        return routes, incompatible

    @staticmethod
    def route_signature(route: ModelRoute) -> tuple[Any, ...]:
        """Codex 重启判断保留凭据哈希；哈希只存在内存，不对外展示。"""
        return (
            route.display_id,
            route.codex_provider,
            route.codex_model,
            route.base_url,
            route.upstream_protocol,
            route.adapter_package,
            route.request_headers,
            route.query_params,
            hashlib.sha256(route.api_key.encode("utf-8")).hexdigest() if route.api_key else "",
        )

    def discover_sync(self, *, force: bool = True) -> tuple[Dict[str, ModelRoute], Dict[str, str]]:
        """读取 OpenCode provider 元数据并原子更新共享缓存。"""
        if not force:
            with self._lock:
                if self._catalog:
                    return dict(self._routes), dict(self._incompatible)
        request = Request(
            f"{self.opencode_server_url()}/provider",
            headers={"Accept": "application/json", "User-Agent": "Codebot-ModelRouteRegistry/1"},
        )
        try:
            with urlopen(request, timeout=12) as response:
                raw = response.read(_MAX_PROVIDER_CATALOG_BYTES + 1)
            if len(raw) > _MAX_PROVIDER_CATALOG_BYTES:
                raise ValueError("OpenCode provider 元数据超过 16 MiB 安全上限")
            payload = json.loads(raw.decode("utf-8"))
            routes, incompatible, catalog = self.parse_provider_catalog(
                payload,
                self.load_opencode_credentials(),
            )
            with self._lock:
                self._routes = routes
                self._incompatible = incompatible
                self._catalog = catalog
                self._last_error = ""
            return dict(routes), dict(incompatible)
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
                cached_routes = dict(self._routes)
                cached_incompatible = dict(self._incompatible)
            logger.warning(f"未能加载 OpenCode 模型目录：{exc}")
            return cached_routes, cached_incompatible

    def catalog(self, *, refresh: bool = False) -> Dict[str, ModelRoute]:
        if refresh:
            self.discover_sync(force=True)
        else:
            self.discover_sync(force=False)
        with self._lock:
            return dict(self._catalog)

    def runnable_routes(self, *, refresh: bool = False) -> Dict[str, ModelRoute]:
        if refresh:
            self.discover_sync(force=True)
        else:
            self.discover_sync(force=False)
        with self._lock:
            return dict(self._routes)

    def get(self, route_id: str, *, refresh: bool = False) -> Optional[ModelRoute]:
        return self.catalog(refresh=refresh).get(str(route_id or ""))

    @staticmethod
    def _jwt_claims(token: str) -> Dict[str, Any]:
        """只在内存中解析 JWT 公共 claims；不验证或记录原始 token。"""
        try:
            parts = str(token or "").split(".")
            if len(parts) != 3:
                return {}
            import base64

            encoded = parts[1] + ("=" * (-len(parts[1]) % 4))
            value = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @classmethod
    def _oauth_account_id(cls, access_token: str) -> str:
        claims = cls._jwt_claims(access_token)
        nested = claims.get("https://api.openai.com/auth")
        nested = nested if isinstance(nested, dict) else {}
        organizations = claims.get("organizations") if isinstance(claims.get("organizations"), list) else []
        organization_id = ""
        if organizations and isinstance(organizations[0], dict):
            organization_id = str(organizations[0].get("id") or "")
        return str(
            claims.get("chatgpt_account_id")
            or nested.get("chatgpt_account_id")
            or organization_id
            or ""
        ).strip()

    @classmethod
    def _oauth_residency(cls, access_token: str) -> str:
        claims = cls._jwt_claims(access_token)
        nested = claims.get("https://api.openai.com/auth")
        nested = nested if isinstance(nested, dict) else {}
        residency = str(
            nested.get("chatgpt_compute_residency")
            or claims.get("chatgpt_compute_residency")
            or ""
        ).strip()
        return "" if residency == "no_constraint" else residency

    def ensure_fresh_credentials(self, route_id: str) -> ModelRoute:
        """按 OpenCode 自身合同刷新需要轮换的 OAuth，并返回当前路由。

        刷新结果通过 OpenCode 官方 ``PUT /auth/{providerID}`` 写回同一连接，
        避免 Codebot 与 OpenCode 各维护一份会分叉的 token。API Key 路由不做
        任何写操作。整个流程不创建 Session，也不会触发 OpenCode Agent。
        """
        route = self.get(route_id)
        if route is None:
            raise ValueError("所选模型不在当前 OpenCode 目录中")
        if route.credential_mode != "opencode_oauth":
            return route
        now_ms = int(time.time() * 1000)
        if route.api_key and route.oauth_expires_at_ms > now_ms + _OAUTH_REFRESH_SKEW_MS:
            return route

        with self._oauth_refresh_lock:
            # 另一个线程可能已经刷新；重新读取 OpenCode 的唯一凭据源后再判断。
            credential = self.load_opencode_credentials().get(route.opencode_provider) or {}
            access = str(credential.get("access") or "").strip()
            refresh_token = str(credential.get("refresh") or "").strip()
            account_id = str(credential.get("accountId") or route.oauth_account_id or "").strip()
            try:
                expires_at = int(credential.get("expires") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            now_ms = int(time.time() * 1000)
            if access and expires_at > now_ms + _OAUTH_REFRESH_SKEW_MS:
                self.discover_sync(force=True)
                refreshed = self.get(route_id)
                if refreshed is None:
                    raise ValueError("OpenCode OAuth 刷新后模型已不在当前目录中")
                return refreshed
            if not refresh_token:
                raise ValueError("OpenCode OpenAI OAuth 已过期且没有 refresh token，请先在 OpenCode 中重新登录")

            token_request = Request(
                f"{_OPENAI_OAUTH_ISSUER}/oauth/token",
                data=urlencode({
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": _OPENAI_OAUTH_CLIENT_ID,
                }).encode("utf-8"),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urlopen(token_request, timeout=20) as response:
                    raw = response.read(512 * 1024)
                tokens = json.loads(raw.decode("utf-8"))
            except HTTPError as exc:
                raise ValueError(
                    f"OpenCode OpenAI OAuth 刷新失败（HTTP {exc.code}），请在 OpenCode 中重新登录"
                ) from None
            except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"OpenCode OpenAI OAuth 刷新失败：{type(exc).__name__}") from None
            if not isinstance(tokens, dict):
                raise ValueError("OpenCode OpenAI OAuth 刷新返回结构无效")
            new_access = str(tokens.get("access_token") or "").strip()
            new_refresh = str(tokens.get("refresh_token") or refresh_token).strip()
            if not new_access or not new_refresh:
                raise ValueError("OpenCode OpenAI OAuth 刷新未返回完整 token")
            try:
                expires_in = max(60, int(tokens.get("expires_in") or 3600))
            except (TypeError, ValueError):
                expires_in = 3600
            account_id = self._oauth_account_id(new_access) or account_id
            auth_body: Dict[str, Any] = {
                "type": "oauth",
                "refresh": new_refresh,
                "access": new_access,
                "expires": int(time.time() * 1000) + expires_in * 1000,
            }
            if account_id:
                auth_body["accountId"] = account_id

            # 让正在运行的 OpenCode Server 自己原子地按其 Auth schema 写回，
            # Codebot 不直接改 auth.json，也不在日志中输出请求体或 token。
            auth_request = Request(
                f"{self.opencode_server_url()}/auth/{route.opencode_provider}",
                data=json.dumps(auth_body, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="PUT",
            )
            try:
                with urlopen(auth_request, timeout=12) as response:
                    response.read(64 * 1024)
            except HTTPError as exc:
                raise ValueError(f"OpenCode OAuth 写回失败（HTTP {exc.code}），请在 OpenCode 中重新登录") from None
            except (URLError, TimeoutError) as exc:
                raise ValueError(f"OpenCode OAuth 写回失败：{type(exc).__name__}") from None

            self.discover_sync(force=True)
            refreshed = self.get(route_id)
            if refreshed is None or not refreshed.api_key:
                raise ValueError("OpenCode OAuth 已刷新，但模型路由未能重新加载")
            return refreshed

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error


model_route_registry = ModelRouteRegistry()
