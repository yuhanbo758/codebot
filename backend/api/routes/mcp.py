"""
MCP (Model Context Protocol) Server 管理 API
"""
import json
import re
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings, McpServerConfig

router = APIRouter()


# ── Pydantic 请求/响应模型 ──────────────────────────────────────────────────

class McpServerCreateRequest(BaseModel):
    name: str
    description: str = ""
    transport: str = "stdio"          # stdio | sse
    command: Optional[str] = None
    args: List[str] = []
    url: Optional[str] = None
    env: dict = {}
    enabled: bool = True


class McpServerUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    transport: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    url: Optional[str] = None
    env: Optional[dict] = None
    enabled: Optional[bool] = None


class BatchDeleteRequest(BaseModel):
    ids: List[str]


# ── 持久化工具函数 ──────────────────────────────────────────────────────────

def _ensure_data_dir():
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_all() -> List[dict]:
    _ensure_data_dir()
    path = settings.MCP_SERVERS_FILE
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def _write_all(servers: List[dict]):
    _ensure_data_dir()
    with open(settings.MCP_SERVERS_FILE, "w", encoding="utf-8") as f:
        json.dump(servers, f, ensure_ascii=False, indent=2)


def _find_by_id(servers: List[dict], server_id: str) -> Optional[dict]:
    for s in servers:
        if s.get("id") == server_id:
            return s
    return None


# ── 校验辅助 ───────────────────────────────────────────────────────────────

def _validate_create(req: McpServerCreateRequest):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="MCP Server 名称不能为空")
    if req.transport not in ("stdio", "sse"):
        raise HTTPException(status_code=400, detail="transport 必须是 stdio 或 sse")
    if req.transport == "stdio" and not (req.command or "").strip():
        raise HTTPException(status_code=400, detail="stdio 模式下 command 不能为空")
    if req.transport == "sse" and not (req.url or "").strip():
        raise HTTPException(status_code=400, detail="sse 模式下 url 不能为空")


# ── API 端点 ───────────────────────────────────────────────────────────────

@router.get("")
@router.get("/")
async def list_mcp_servers():
    """列出所有 MCP Server"""
    try:
        items = _read_all()
        return {
            "success": True,
            "data": {
                "items": items,
                "total": len(items)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
@router.post("/")
async def add_mcp_server(request: McpServerCreateRequest):
    """添加 MCP Server"""
    _validate_create(request)
    try:
        servers = _read_all()
        server_id = uuid4().hex
        server = {
            "id": server_id,
            "name": request.name.strip(),
            "description": request.description.strip(),
            "transport": request.transport,
            "command": (request.command or "").strip() or None,
            "args": request.args or [],
            "url": (request.url or "").strip() or None,
            "env": request.env or {},
            "enabled": request.enabled,
            "installed_at": datetime.now().isoformat()
        }
        servers.append(server)
        _write_all(servers)
        return {
            "success": True,
            "data": server,
            "message": f"MCP Server「{server['name']}」已添加"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{server_id}")
async def get_mcp_server(server_id: str):
    """获取单个 MCP Server"""
    servers = _read_all()
    server = _find_by_id(servers, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    return {"success": True, "data": server}


@router.patch("/{server_id}")
async def update_mcp_server(server_id: str, request: McpServerUpdateRequest):
    """更新 MCP Server"""
    servers = _read_all()
    server = _find_by_id(servers, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    updates = request.model_dump(exclude_unset=True)
    server.update(updates)
    # 校验更新后的 transport
    transport = server.get("transport", "stdio")
    if transport not in ("stdio", "sse"):
        raise HTTPException(status_code=400, detail="transport 必须是 stdio 或 sse")
    _write_all(servers)
    return {
        "success": True,
        "data": server,
        "message": f"MCP Server「{server['name']}」已更新"
    }


@router.delete("/{server_id}")
async def delete_mcp_server(server_id: str):
    """删除 MCP Server"""
    servers = _read_all()
    server = _find_by_id(servers, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    name = server.get("name", server_id)
    servers = [s for s in servers if s.get("id") != server_id]
    _write_all(servers)
    return {
        "success": True,
        "message": f"MCP Server「{name}」已删除"
    }


@router.post("/batch-delete")
async def batch_delete_mcp_servers(request: BatchDeleteRequest):
    """批量删除 MCP Server"""
    servers = _read_all()
    id_set = set(request.ids)
    success_names = [s["name"] for s in servers if s.get("id") in id_set]
    servers = [s for s in servers if s.get("id") not in id_set]
    _write_all(servers)
    return {
        "success": True,
        "message": f"已删除 {len(success_names)} 个 MCP Server"
    }


@router.post("/{server_id}/toggle")
async def toggle_mcp_server(server_id: str):
    """启用/禁用 MCP Server"""
    servers = _read_all()
    server = _find_by_id(servers, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    server["enabled"] = not server.get("enabled", True)
    _write_all(servers)
    state = "已启用" if server["enabled"] else "已禁用"
    return {
        "success": True,
        "data": server,
        "message": f"MCP Server「{server['name']}」{state}"
    }


# ── 聊天快速添加接口（给 chat 路由调用）────────────────────────────────────

def add_server_from_chat(
    name: str,
    transport: str,
    command: Optional[str] = None,
    args: Optional[List[str]] = None,
    url: Optional[str] = None,
    env: Optional[dict] = None,
    description: str = "",
) -> dict:
    """从聊天解析结果直接添加 MCP Server（同步版本，供 chat 路由调用）"""
    servers = _read_all()
    server_id = uuid4().hex
    server = {
        "id": server_id,
        "name": name.strip(),
        "description": description.strip(),
        "transport": transport,
        "command": (command or "").strip() or None,
        "args": args or [],
        "url": (url or "").strip() or None,
        "env": env or {},
        "enabled": True,
        "installed_at": datetime.now().isoformat()
    }
    servers.append(server)
    _write_all(servers)
    return server
