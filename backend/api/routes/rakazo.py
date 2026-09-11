"""Rakazo 状态、项目、模型、权限、更新和内部采样 API。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import app_config
from core.memory_manager import MemoryManager
from core.rakazo_runtime import (
    MAX_SAMPLING_REQUEST_BYTES,
    RakazoRuntimeError,
    rakazo_runtime,
)


router = APIRouter()
internal_router = APIRouter()
project_mcp_router = APIRouter()
memory_manager: Optional[MemoryManager] = None
_project_open_locks: Dict[str, asyncio.Lock] = {}


class RakazoSessionTokenRequest(BaseModel):
    token: str = Field(min_length=16, max_length=16384)


class RakazoDesktopAuthorizationRequest(BaseModel):
    mode: Literal["create", "restore"]
    secretBundle: Optional[Dict[str, str]] = None


class RakazoOpenProjectRequest(BaseModel):
    project_dir: str
    route_id: Optional[str] = None


class RakazoModelRequest(BaseModel):
    route_id: str


class RakazoPermissionsRequest(BaseModel):
    projectRead: Optional[bool] = None
    projectWrite: Optional[bool] = None
    commandExecution: Optional[bool] = None
    externalNetwork: Optional[bool] = None
    networkPolicy: Optional[Literal["ask", "deny", "allow"]] = None


class RakazoRuntimeActionRequest(BaseModel):
    action: Literal["start", "stop", "restart", "install_experimental"]


class RakazoComputerActionRequest(BaseModel):
    action: Literal["status", "boot", "stop", "restart"]


def _raise_runtime_error(exc: Exception) -> None:
    if isinstance(exc, RakazoRuntimeError):
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    raise HTTPException(status_code=502, detail=str(exc)) from exc


def _manager() -> MemoryManager:
    global memory_manager
    if memory_manager is None:
        memory_manager = MemoryManager()
    return memory_manager


async def prepare_if_enabled() -> None:
    """按用户启用状态启动或收敛受管运行时，不自动安装系统功能。

    ``auto_start`` 关闭时不会启动已经停止的容器；但若受管容器本来就在
    运行，仍需执行一次幂等 ``up -d``，让本次进程随机采样令牌和 OpenCode
    当前模型目录进入 adapter，否则 Codebot 重启后模型请求会稳定返回 401。
    """
    if not app_config.rakazo.enabled:
        return
    should_reconcile = bool(app_config.rakazo.auto_start)
    if not should_reconcile:
        should_reconcile = await rakazo_runtime.managed_runtime_is_running()
    if should_reconcile:
        await rakazo_runtime.control_runtime("start")


@router.get("/status")
async def get_status():
    return {"success": True, "data": await rakazo_runtime.status()}


@router.post("/runtime")
async def control_runtime(request: RakazoRuntimeActionRequest):
    try:
        data = await rakazo_runtime.control_runtime(request.action)
        return {"success": True, "data": data}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.put("/authorization")
async def set_authorization(request: RakazoSessionTokenRequest):
    try:
        rakazo_runtime.set_session_token(request.token)
        # 保存后立即验证，失败则保留令牌供用户修正部署地址，但不声明成功连接。
        health = await rakazo_runtime.health()
        return {"success": True, "data": {"configured": True, "health": health}}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.delete("/authorization")
async def clear_authorization():
    try:
        rakazo_runtime.clear_session_token()
        return {"success": True, "data": {"configured": False}}
    except Exception as exc:
        _raise_runtime_error(exc)


def _desktop_bridge_authorized(request: Request) -> bool:
    """限制包含 Rakazo 明文登录材料的接口只能由同进程 Electron 调用。"""
    expected = str(os.environ.get("CODEBOT_DESKTOP_BRIDGE_TOKEN") or "")
    supplied = str(request.headers.get("x-codebot-desktop-token") or "")
    return bool(expected and supplied) and secrets.compare_digest(expected, supplied)


@router.post("/authorization/desktop-bootstrap", include_in_schema=False)
async def desktop_bootstrap_authorization(request: Request, body: RakazoDesktopAuthorizationRequest):
    if not _desktop_bridge_authorized(request):
        # 不暴露桌面内部接口是否存在，避免局域网客户端把它当作登录入口探测。
        raise HTTPException(status_code=404, detail="Not found")
    try:
        data = await rakazo_runtime.bootstrap_desktop_authorization(
            mode=body.mode,
            secret_bundle=body.secretBundle,
        )
        return {"success": True, "data": data}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.delete("/authorization/desktop-bootstrap", include_in_schema=False)
async def desktop_clear_authorization(request: Request):
    if not _desktop_bridge_authorized(request):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        rakazo_runtime.clear_session_token()
        return {"success": True, "data": {"configured": False}}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/models")
async def list_models(
    refresh: bool = False,
    compatibility_status: Optional[
        Literal["verified", "authorization_required", "protocol_pending", "probe_failed"]
    ] = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: Optional[int] = Query(default=None, ge=1, le=200),
):
    try:
        models = await asyncio.to_thread(rakazo_runtime.models, refresh=refresh)
        counts: Dict[str, int] = {}
        for model in models:
            model_status = str(model.get("compatibilityStatus") or "probe_failed")
            counts[model_status] = counts.get(model_status, 0) + 1
        filtered = [
            model
            for model in models
            if not compatibility_status or model.get("compatibilityStatus") == compatibility_status
        ]

        # 保留旧调用的数组响应，确保内部采样与已有客户端不被迁移破坏；
        # 这里的 catalogTotal 仅统计当前 OpenCode Server 已启用的模型，未连接
        # Provider 的静态全目录不会再进入 Rakazo 设置页或选择器。
        if limit is None and offset == 0 and compatibility_status is None:
            return {"success": True, "data": filtered}

        page = filtered[offset : offset + limit] if limit is not None else filtered[offset:]
        return {
            "success": True,
            "data": {
                "models": page,
                "total": len(filtered),
                "catalogTotal": len(models),
                "openCodeEnabledTotal": len(models),
                "sourceScope": "opencode_enabled",
                "counts": counts,
                "offset": offset,
                "limit": limit,
            },
        }
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/models/probe")
async def probe_model(request: RakazoModelRequest):
    try:
        return {"success": True, "data": await rakazo_runtime.probe_model(request.route_id)}
    except Exception as exc:
        _raise_runtime_error(exc)


async def _open_project_locked_impl(request: RakazoOpenProjectRequest, expected_project_id: Optional[str] = None):
    manager = _manager()
    project_id, _, resolved = rakazo_runtime.normalize_project(request.project_dir)
    if expected_project_id and expected_project_id != project_id:
        raise HTTPException(status_code=409, detail="URL project_id 与项目目录指纹不一致")
    existing = rakazo_runtime.mapping_by_project_id(project_id)
    if existing:
        conversation = await manager.get_conversation(int(existing["conversation_id"]))
        if not conversation:
            raise HTTPException(
                status_code=409,
                detail="Rakazo 映射仍存在但 Codebot 主会话缺失；为避免创建第二个主会话，请先执行恢复",
            )
        # 打开既有主会话时先验证远端 Bot。若固定上游数据库曾被重置且明确
        # 返回 Resource not found，运行时会在项目锁内恢复 Bot/Thread/Computer，
        # 避免用户进入聊天页后才看到 bots.get 500 和空模型选择器。
        await rakazo_runtime.remote_project_state(project_id)
        existing = rakazo_runtime.mapping_by_project_id(project_id) or existing
        if request.route_id and request.route_id != existing.get("model_route_id"):
            await rakazo_runtime.set_project_model(project_id, request.route_id)
            existing = rakazo_runtime.mapping_by_project_id(project_id) or existing
        if conversation.get("is_archived"):
            await manager.set_conversation_archived(int(existing["conversation_id"]), False)
            conversation = await manager.get_conversation(int(existing["conversation_id"]))
        return {
            "success": True,
            "created": False,
            "message": "已打开该项目的 Rakazo 主会话",
            "data": {"project": rakazo_runtime.public_mapping(existing), "conversation": conversation},
        }

    # 新建对话只固定 Rakazo 执行器和项目主会话，不永久固定模型。前端可以把
    # 用户当前 OpenCode 模型作为初始偏好；未提供时由后端从当前可选目录中优先
    # 选择已验证路由。创建完成后仍通过项目模型接口在空闲状态随时切换。
    route_id = rakazo_runtime.select_initial_route(str(request.route_id or "").strip())
    bot: Dict[str, Any] = {}
    conversation_id: Optional[int] = None
    try:
        bot, route = await rakazo_runtime.create_remote_bot(resolved, route_id)
        conversation_id = await manager.create_conversation(
            f"Rakazo · {Path(resolved).name}",
            project_dir=resolved,
            conversation_type="normal",
            executor="rakazo",
            external_thread_id=str(bot.get("threadId") or ""),
        )
        await manager.bind_conversation_runtime(
            conversation_id,
            executor="rakazo",
            external_thread_id=str(bot.get("threadId") or ""),
            runtime_metadata={
                "botId": bot.get("id"),
                "projectId": project_id,
                "executorLocked": True,
            },
        )
        health = await rakazo_runtime.health()
        mapping = rakazo_runtime.save_mapping(
            project_dir=resolved,
            conversation_id=conversation_id,
            bot=bot,
            route=route,
            runtime_version=str(health.get("version") or "") if isinstance(health, dict) else "",
        )
        permissions = rakazo_runtime.get_permissions(project_id)
        await rakazo_runtime._sync_shell_approval_rule(
            project_id=project_id,
            command_allowed=bool(permissions.get("commandExecution")),
        )
        conversation = await manager.get_conversation(conversation_id)
        return {
            "success": True,
            "created": True,
            "message": "已创建该项目唯一的 Rakazo 主会话",
            "data": {"project": rakazo_runtime.public_mapping(mapping), "conversation": conversation},
        }
    except HTTPException:
        raise
    except Exception as exc:
        if conversation_id is not None:
            try:
                await manager.delete_conversation(conversation_id)
            except Exception:
                pass
        bot_id = str(bot.get("id") or "")
        mcp_server_id = str(bot.get("_codebotMcpServerId") or "")
        if mcp_server_id:
            try:
                await rakazo_runtime.rpc("mcp.servers.remove", {"id": mcp_server_id})
            except Exception:
                pass
        if bot_id:
            try:
                await rakazo_runtime.rpc("bots.remove", {"botId": bot_id, "deleteMemories": False})
            except Exception:
                pass
        _raise_runtime_error(exc)


async def _open_project_impl(request: RakazoOpenProjectRequest, expected_project_id: Optional[str] = None):
    """把同一项目的并发打开串行化，避免创建第二个 Bot/Thread/Computer。"""
    project_id, _, _ = rakazo_runtime.normalize_project(request.project_dir)
    lock = _project_open_locks.setdefault(project_id, asyncio.Lock())
    async with lock:
        return await _open_project_locked_impl(request, expected_project_id)


@router.post("/projects/open")
async def open_project(request: RakazoOpenProjectRequest):
    return await _open_project_impl(request)


@router.get("/projects")
async def list_projects():
    """返回已有主会话的项目级权限；不创建或启动任何 Rakazo 对象。"""
    try:
        return {"success": True, "data": rakazo_runtime.list_project_permissions()}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/projects/lookup")
async def lookup_project(project_dir: str):
    """只查询唯一主会话是否存在，不创建 Bot、Thread 或 Computer。"""
    try:
        project_id, _, resolved = rakazo_runtime.normalize_project(project_dir)
        mapping = rakazo_runtime.mapping_by_project_id(project_id)
        return {
            "success": True,
            "data": {
                "exists": bool(mapping),
                "projectId": project_id,
                "projectDir": resolved,
                "project": rakazo_runtime.public_mapping(mapping),
            },
        }
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/projects/{project_id}/open")
async def open_project_by_id(project_id: str, request: RakazoOpenProjectRequest):
    return await _open_project_impl(request, project_id)


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    try:
        return {"success": True, "data": await rakazo_runtime.remote_project_state(project_id)}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.put("/projects/{project_id}/model")
async def set_project_model(project_id: str, request: RakazoModelRequest):
    try:
        return {"success": True, "data": await rakazo_runtime.set_project_model(project_id, request.route_id)}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/projects/{project_id}/permissions")
async def get_project_permissions(project_id: str):
    try:
        return {"success": True, "data": rakazo_runtime.get_permissions(project_id)}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.put("/projects/{project_id}/permissions")
async def set_project_permissions(project_id: str, request: RakazoPermissionsRequest):
    try:
        return {
            "success": True,
            "data": await rakazo_runtime.update_permissions(project_id, request.model_dump(exclude_unset=True)),
        }
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/projects/{project_id}/interrupt")
async def interrupt_project(project_id: str):
    try:
        return {"success": True, "data": await rakazo_runtime.interrupt_project(project_id)}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.post("/projects/{project_id}/computer")
async def computer_action(project_id: str, request: RakazoComputerActionRequest):
    try:
        return {"success": True, "data": await rakazo_runtime.computer_action(project_id, request.action)}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/projects/{project_id}/memory")
async def project_memory(project_id: str):
    try:
        return {"success": True, "data": await rakazo_runtime.memory_summary(project_id)}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/projects/{project_id}/logs")
async def project_logs(project_id: str, limit: int = 50):
    try:
        return {"success": True, "data": rakazo_runtime.recent_audit(project_id, limit)}
    except Exception as exc:
        _raise_runtime_error(exc)


@router.get("/update/check")
async def update_check():
    try:
        return {"success": True, "data": await rakazo_runtime.check_update()}
    except Exception as exc:
        _raise_runtime_error(exc)


def _internal_authorized(request: Request) -> bool:
    scheme, _, token = str(request.headers.get("authorization") or "").partition(" ")
    return scheme.lower() == "bearer" and bool(token) and secrets.compare_digest(token, rakazo_runtime.sampling_token)


@internal_router.get("/v1/models", include_in_schema=False)
async def internal_models(request: Request):
    if not _internal_authorized(request):
        raise HTTPException(status_code=401, detail="Rakazo 采样桥鉴权失败")
    catalog = await asyncio.to_thread(rakazo_runtime.models, refresh=False)
    # Rakazo/Pi 在启动时需要看到与 OpenCode 当前连接一致的可选目录；真正
    # chat/completions 仍由 verified_route 逐模型失败关闭。这样首次选择可由
    # Codebot 自动探测，同时外部绕过 Codebot 直接改 Bot 也无法运行未验证路由。
    models = [item for item in catalog if item.get("selectable")]
    return {
        "object": "list",
        "data": [
            {"id": item["id"], "object": "model", "created": 0, "owned_by": item.get("provider") or "codebot"}
            for item in models
        ],
    }


@internal_router.post("/v1/chat/completions", include_in_schema=False)
async def internal_chat_completions(request: Request):
    if not _internal_authorized(request):
        raise HTTPException(status_code=401, detail="Rakazo 采样桥鉴权失败")
    raw = await request.body()
    if len(raw) > MAX_SAMPLING_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="Rakazo 模型采样请求超过 8 MiB 安全上限")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Rakazo 模型采样请求不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Rakazo 模型采样请求必须是 JSON 对象")
    route_id = str(payload.get("model") or "").strip()
    if not route_id:
        raise HTTPException(status_code=400, detail="Rakazo 模型采样请求缺少 model")
    if bool(payload.get("stream")):
        try:
            # 在发送 SSE 响应头前完成目录、路由指纹与探测门禁校验；上游连接
            # 建立后的错误按 OpenAI-compatible SSE error 事件返回。
            rakazo_runtime.verified_route(route_id)
        except Exception as exc:
            _raise_runtime_error(exc)

        async def stream_events():
            try:
                async for event in rakazo_runtime.sample_stream(route_id, payload, require_verified=True):
                    yield event
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc)[:2000]
                error = {"error": {"message": message, "type": "model_stream_error"}}
                yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    try:
        result = await rakazo_runtime.sample(route_id, payload, require_verified=True)
    except Exception as exc:
        _raise_runtime_error(exc)
    return JSONResponse(content=result.response)


@internal_router.post("/routes/probe", include_in_schema=False)
async def internal_probe(request: Request, route_id: str = Body(embed=True)):
    if not _internal_authorized(request):
        raise HTTPException(status_code=401, detail="Rakazo 采样桥鉴权失败")
    try:
        return await rakazo_runtime.probe_model(route_id)
    except Exception as exc:
        _raise_runtime_error(exc)


def _project_mcp_authorized(request: Request, project_id: str) -> bool:
    scheme, _, token = str(request.headers.get("authorization") or "").partition(" ")
    return scheme.lower() == "bearer" and rakazo_runtime.validate_project_token(project_id, token)


def _mcp_success(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _mcp_error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


@project_mcp_router.post("/{project_id}", include_in_schema=False)
async def project_mcp(project_id: str, request: Request):
    """供 Rakazo Bot 使用的项目范围 Streamable HTTP MCP；每项目独立令牌。"""
    if not _project_mcp_authorized(request, project_id):
        return JSONResponse(status_code=401, content=_mcp_error(None, -32001, "Unauthorized"))
    raw = await request.body()
    if len(raw) > 1024 * 1024:
        return JSONResponse(status_code=413, content=_mcp_error(None, -32600, "Request too large"))
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JSONResponse(status_code=400, content=_mcp_error(None, -32700, "Parse error"))
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return JSONResponse(status_code=400, content=_mcp_error(None, -32600, "Invalid Request"))
    request_id = payload.get("id")
    method = str(payload.get("method") or "")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if method == "notifications/initialized":
        return Response(status_code=202)
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "codebot-rakazo-project", "version": app_config.version},
        }
        return JSONResponse(content=_mcp_success(request_id, result), headers={"MCP-Protocol-Version": "2024-11-05"})
    if method == "ping":
        return JSONResponse(content=_mcp_success(request_id, {}))
    if method == "tools/list":
        try:
            tools = rakazo_runtime.project_tool_definitions(project_id)
            return JSONResponse(content=_mcp_success(request_id, {"tools": tools}))
        except Exception as exc:
            # JSON-RPC 方法级错误仍用 HTTP 200 返回；只有传输/鉴权/解析错误使用
            # 非 2xx，避免 Rakazo MCP 客户端把可恢复的工具错误误判为连接断开。
            return JSONResponse(content=_mcp_error(request_id, -32603, str(exc)))
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            result = rakazo_runtime.call_project_tool(project_id, name, arguments)
            return JSONResponse(content=_mcp_success(request_id, result))
        except Exception as exc:
            rakazo_runtime.audit(project_id, f"project.tool.{name or 'unknown'}", "failed", {"error": str(exc)})
            return JSONResponse(content=_mcp_error(request_id, -32603, str(exc)))
    return JSONResponse(content=_mcp_error(request_id, -32601, f"Method not found: {method}"))


@project_mcp_router.get("/{project_id}", include_in_schema=False)
async def project_mcp_get(project_id: str, request: Request):
    if not _project_mcp_authorized(request, project_id):
        return JSONResponse(status_code=401, content=_mcp_error(None, -32001, "Unauthorized"))
    return Response(status_code=405, headers={"Allow": "POST"})
