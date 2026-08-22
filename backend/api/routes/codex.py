"""Codex Agent Harness 状态、账号和模型 API。"""
from __future__ import annotations

from typing import Literal

import json

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import app_config
from core.codex_runtime import codex_runtime


router = APIRouter()


class CodexLoginRequest(BaseModel):
    """API Key 只作为本次请求参数传给 App Server，不写入 Codebot 配置。"""

    method: Literal["chatgpt", "device_code", "api_key"] = "chatgpt"
    api_key: str = ""


async def prepare_if_enabled() -> None:
    """供 FastAPI lifespan 预热；禁用或关闭自动启动时保持惰性。"""
    if app_config.codex.enabled and app_config.codex.auto_start:
        await codex_runtime.start()


@router.get("/status")
async def codex_status():
    return {"success": True, "data": codex_runtime.status()}


@router.post("/start")
async def start_codex():
    try:
        await codex_runtime.start()
        return {"success": True, "message": "Codex App Server 已启动", "data": codex_runtime.status()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/restart")
async def restart_codex():
    try:
        await codex_runtime.restart()
        return {"success": True, "message": "Codex App Server 已重启", "data": codex_runtime.status()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/models")
async def codex_models():
    """返回 Codex 原生模型及可直连/桥接的 OpenCode 模型。"""
    try:
        return {"success": True, "data": await codex_runtime.models()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/compat/{codex_provider}/v1/responses", include_in_schema=False)
async def codex_compat_responses(codex_provider: str, request: Request):
    """仅供本机 Codex App Server 使用的 Responses 协议桥入口。

    入口使用每次后端启动随机生成的 Bearer Token；即使 Codebot 对外监听，第三方
    也无法借此枚举或调用 OpenCode 中的模型凭据。上游 API Key 不会出现在该请求、
    Codex 子进程环境、前端响应或日志中。
    """
    if not codex_runtime.bridge_authorized(request.headers.get("authorization", "")):
        raise HTTPException(status_code=401, detail="Codex 模型桥鉴权失败")
    raw = await request.body()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Codex Responses 请求超过 8 MiB 安全上限")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Codex Responses 请求不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Codex Responses 请求必须是 JSON 对象")
    try:
        result = await codex_runtime.bridge_responses(codex_provider, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        from core.codex_model_bridge import CodexModelBridgeError

        if isinstance(exc, CodexModelBridgeError):
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if bool(payload.get("stream")):
        return Response(
            content=result.as_sse(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    return JSONResponse(content=result.response)


@router.get("/account")
async def codex_account():
    try:
        return {"success": True, "data": await codex_runtime.account()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/account/login")
async def codex_login(request: CodexLoginRequest):
    try:
        data = await codex_runtime.login(request.method, api_key=request.api_key)
        # 响应只含 OAuth URL、设备码或登录 ID；绝不回显 API Key。
        return {"success": True, "message": "Codex 登录流程已启动", "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/account/logout")
async def codex_logout():
    try:
        await codex_runtime.logout()
        return {"success": True, "message": "已退出 Codex 账号"}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
