"""局域网设备配对与 API Token 管理端点。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from config import app_config, load_or_create_lan_api_token, save_config
from core.lan_auth import (
    SESSION_COOKIE,
    consume_pairing_code,
    create_session_token,
    invalidate_pairing_code,
    is_loopback_request,
    issue_pairing_code,
    request_is_authenticated,
)


router = APIRouter()


class PairRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class SecurityConfigPatch(BaseModel):
    lan_auth_enabled: bool | None = None
    pairing_code_ttl_seconds: int | None = Field(default=None, ge=60, le=1800)
    session_hours: int | None = Field(default=None, ge=1, le=24 * 365)


def _require_loopback(request: Request) -> None:
    if not is_loopback_request(request):
        raise HTTPException(status_code=403, detail="该操作只能在运行 Codebot 的本机执行")


@router.get("/status")
async def security_status(request: Request):
    local = is_loopback_request(request)
    authenticated = request_is_authenticated(request)
    return {
        "success": True,
        "data": {
            "local_request": local,
            "lan_auth_enabled": app_config.security.lan_auth_enabled,
            "authentication_required": bool(app_config.security.lan_auth_enabled and not local),
            "authenticated": authenticated,
        },
    }


@router.get("/config")
async def get_security_config(request: Request):
    _require_loopback(request)
    return {
        "success": True,
        "data": {
            "lan_auth_enabled": app_config.security.lan_auth_enabled,
            "pairing_code_ttl_seconds": app_config.security.pairing_code_ttl_seconds,
            "session_hours": app_config.security.session_hours,
            "token_configured": bool(app_config.security.lan_api_token),
        },
    }


@router.patch("/config")
async def update_security_config(request: Request, patch: SecurityConfigPatch):
    _require_loopback(request)
    for key, value in patch.model_dump(exclude_none=True).items():
        setattr(app_config.security, key, value)
    save_config(app_config)
    return await get_security_config(request)


@router.post("/pair-code")
async def create_pair_code(request: Request):
    _require_loopback(request)
    code, ttl = issue_pairing_code(request)
    return {"success": True, "data": {"code": code, "expires_in": ttl}}


@router.post("/pair")
async def pair_device(request: Request, payload: PairRequest, response: Response):
    try:
        valid = consume_pairing_code(request, payload.code)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    if not valid:
        raise HTTPException(status_code=401, detail="配对码无效或已过期")

    session_token, expires_at = create_session_token()
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=int(app_config.security.session_hours) * 3600,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        path="/",
    )
    return {"success": True, "data": {"expires_at": expires_at}, "message": "设备配对成功"}


@router.post("/unpair")
async def unpair_device(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"success": True, "message": "当前设备已解除配对"}


@router.post("/token/regenerate")
async def regenerate_api_token(request: Request):
    _require_loopback(request)
    app_config.security.lan_api_token = load_or_create_lan_api_token(rotate=True)
    invalidate_pairing_code()
    save_config(app_config)
    return {
        "success": True,
        "data": {"token": app_config.security.lan_api_token},
        "message": "API Token 已重新生成，已有配对会话将失效",
    }


@router.get("/token")
async def get_api_token(request: Request):
    _require_loopback(request)
    return {"success": True, "data": {"token": app_config.security.lan_api_token}}
