"""Codebot 局域网 API 的共享 Token 与短时配对认证。"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Optional

from fastapi import Request

from config import app_config


SESSION_COOKIE = "codebot_lan_session"
_pairing_code_digest: Optional[str] = None
_pairing_code_expires_at = 0.0
_failed_pair_attempts: dict[str, Deque[float]] = defaultdict(deque)


def _client_host(request: Request) -> str:
    """只信任真实 socket 地址，不采信可伪造的 X-Forwarded-For。"""
    return str(request.client.host if request.client else "")


def is_loopback_request(request: Request) -> bool:
    host = _client_host(request)
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host.lower() == "localhost"


def _master_token() -> str:
    return str(app_config.security.lan_api_token or "")


def _sign_session_payload(payload: str) -> str:
    return hmac.new(_master_token().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token() -> tuple[str, int]:
    expires_at = int(time.time() + int(app_config.security.session_hours) * 3600)
    payload = f"{expires_at}.{secrets.token_urlsafe(24)}"
    return f"{payload}.{_sign_session_payload(payload)}", expires_at


def validate_session_token(token: str) -> bool:
    try:
        expires_raw, nonce, signature = token.split(".", 2)
        payload = f"{expires_raw}.{nonce}"
        if int(expires_raw) <= int(time.time()):
            return False
        return hmac.compare_digest(signature, _sign_session_payload(payload))
    except (TypeError, ValueError):
        return False


def request_is_authenticated(request: Request) -> bool:
    if not app_config.security.lan_auth_enabled or is_loopback_request(request):
        return True

    authorization = (request.headers.get("authorization") or "").strip()
    supplied_token = ""
    if authorization.lower().startswith("bearer "):
        supplied_token = authorization[7:].strip()
    if not supplied_token:
        supplied_token = (request.headers.get("x-codebot-token") or "").strip()
    if supplied_token and hmac.compare_digest(supplied_token, _master_token()):
        return True

    return validate_session_token(request.cookies.get(SESSION_COOKIE, ""))


def path_is_auth_exempt(path: str) -> bool:
    """返回由独立鉴权边界保护、无需 LAN 会话的路径。

    内部模型采样入口使用每次后端启动随机生成的独立 Bearer Token；如果先套
    LAN Token 校验，Docker 私有适配器的 Authorization 会在到达采样路由前被
    误拒绝。
    """
    return (
        path == "/api/health"
        or path == "/api/security/status"
        or path == "/api/security/pair"
        or path.startswith("/api/chat/share/")
        or path.startswith("/api/internal/model-sampling/")
        or path.startswith("/api/internal/rakazo-project-mcp/")
    )


def issue_pairing_code(request: Request) -> tuple[str, int]:
    if not is_loopback_request(request):
        raise PermissionError("配对码只能在运行 Codebot 的本机生成")
    global _pairing_code_digest, _pairing_code_expires_at
    code = f"{secrets.randbelow(1_000_000):06d}"
    _pairing_code_digest = hashlib.sha256(code.encode("ascii")).hexdigest()
    ttl = int(app_config.security.pairing_code_ttl_seconds)
    _pairing_code_expires_at = time.time() + ttl
    return code, ttl


def consume_pairing_code(request: Request, code: str) -> bool:
    """校验一次性配对码，并对单个来源地址施加滑动窗口限速。"""
    host = _client_host(request) or "unknown"
    now = time.time()
    attempts = _failed_pair_attempts[host]
    while attempts and attempts[0] < now - 300:
        attempts.popleft()
    if len(attempts) >= 5:
        raise RuntimeError("配对失败次数过多，请 5 分钟后重试")

    global _pairing_code_digest, _pairing_code_expires_at
    supplied = hashlib.sha256(str(code or "").strip().encode("ascii", errors="ignore")).hexdigest()
    valid = bool(
        _pairing_code_digest
        and now <= _pairing_code_expires_at
        and hmac.compare_digest(supplied, _pairing_code_digest)
    )
    if not valid:
        attempts.append(now)
        return False

    _pairing_code_digest = None
    _pairing_code_expires_at = 0.0
    _failed_pair_attempts.pop(host, None)
    return True


def invalidate_pairing_code() -> None:
    global _pairing_code_digest, _pairing_code_expires_at
    _pairing_code_digest = None
    _pairing_code_expires_at = 0.0
