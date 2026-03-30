"""
MCP (Model Context Protocol) Server 管理 API
"""
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from config import app_config, settings, McpServerConfig
from core.memory_manager import MemoryManager
from core.opencode_ws import OpenCodeClient
from api.routes import scheduler as scheduler_router
from api.routes import skills as skills_router

router = APIRouter()

CODEBOT_REMOTE_MCP_KEY = "codebot"
CODEBOT_REMOTE_SERVER_NAME = "Codebot Third-Party MCP"
MCP_PROTOCOL_VERSION = "2024-11-05"
_codebot_mcp_sessions: Dict[str, Dict[str, Any]] = {}


# ── 跨平台命令解析工具 ─────────────────────────────────────────────────────────

def _resolve_command(command: str) -> Tuple[List[str], Optional[str]]:
    """
    将命令字符串解析为实际可执行的命令列表（prefix）和错误信息。

    在 Windows 上，npx / npm 等命令实际是 .cmd / .bat 批处理文件。
    asyncio.create_subprocess_exec 底层调用 CreateProcess，不能直接执行 .cmd/.bat，
    但可以传入 shutil.which() 返回的完整路径（含 .CMD 后缀），Windows 会正确处理。

    返回 (cmd_prefix, error_message_or_None)
    """
    if not command:
        return [], "命令为空"

    resolved = shutil.which(command)

    if resolved is None:
        # 给出有针对性的错误提示
        node_path = shutil.which("node")
        if command in ("npx", "npm") and node_path is None:
            if sys.platform == "win32":
                return [], (
                    f"未找到命令「{command}」，且系统未安装 Node.js。\n"
                    "请访问 https://nodejs.org 下载安装 Node.js（LTS 版），"
                    "安装后重启应用即可自动获得 npx。"
                )
            return [], (
                "未找到 npx，系统未安装 Node.js。\n"
                "Linux/macOS 可运行：\n"
                "  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -\n"
                "  sudo apt-get install -y nodejs"
            )
        if command in ("npx", "npm") and node_path:
            return [], (
                f"未找到命令「{command}」。\n"
                f"Node.js 已安装（{node_path}），但 {command} 不在 PATH 中。\n"
                "请尝试重启应用，或在终端中运行 `npm install -g npm` 修复。"
            )
        return [], f"未找到命令「{command}」，请确认已安装并在 PATH 中。"

    # resolved 是完整路径，Windows 上可能是 .CMD/.BAT，直接作为可执行文件路径即可
    # （asyncio.create_subprocess_exec 对完整路径的 .cmd 文件能正常处理）
    return [resolved], None


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
    """删除 MCP Server，并从 opencode 配置中同步移除"""
    servers = _read_all()
    server = _find_by_id(servers, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")
    name = server.get("name", server_id)
    servers = [s for s in servers if s.get("id") != server_id]
    _write_all(servers)
    # 同步删除 opencode 配置中的对应项
    try:
        _remove_from_opencode_config([name])
    except Exception as _e:
        pass  # 非关键操作，失败不影响主流程
    return {
        "success": True,
        "message": f"MCP Server「{name}」已删除"
    }


@router.post("/batch-delete")
async def batch_delete_mcp_servers(request: BatchDeleteRequest):
    """批量删除 MCP Server，并从 opencode 配置中同步移除"""
    servers = _read_all()
    id_set = set(request.ids)
    deleted_servers = [s for s in servers if s.get("id") in id_set]
    success_names = [s["name"] for s in deleted_servers]
    servers = [s for s in servers if s.get("id") not in id_set]
    _write_all(servers)
    # 同步删除 opencode 配置中的对应项
    try:
        _remove_from_opencode_config(success_names)
    except Exception as _e:
        pass  # 非关键操作，失败不影响主流程
    return {
        "success": True,
        "message": f"已删除 {len(success_names)} 个 MCP Server"
    }


# ── stdio MCP 进程测试 ─────────────────────────────────────────────────────

async def _test_stdio_mcp(server: dict, timeout: float = 30.0) -> dict:
    """
    真正启动 stdio MCP 服务器进程，通过 stdin/stdout 执行 MCP JSON-RPC，
    获取工具列表并返回测试结果。

    流程：
      1. 解析命令（Windows .cmd 自动封装）
      2. 启动进程（command + args）
      3. 发送 initialize 请求
      4. 读取 initialize 响应
      5. 发送 notifications/initialized
      6. 发送 tools/list 请求
      7. 读取 tools/list 响应
      8. 终止进程
    """
    import asyncio

    command = server.get("command", "")
    args = server.get("args") or []
    env_extra = server.get("env") or {}

    # 组合完整命令描述（仅用于错误提示）
    cmd_display = f"{command} {' '.join(str(a) for a in args)}".strip()

    # 解析命令（处理 Windows .cmd / .bat 等）
    cmd_prefix, resolve_error = _resolve_command(command)
    if resolve_error:
        return {"success": False, "message": resolve_error, "tools": []}

    # 构建最终命令列表
    full_cmd = cmd_prefix + [str(a) for a in args]

    # 构建进程环境变量
    proc_env = dict(os.environ)
    proc_env.update({str(k): str(v) for k, v in env_extra.items()})

    try:
        proc = await asyncio.create_subprocess_exec(
            *full_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
    except FileNotFoundError:
        _, err = _resolve_command(command)
        return {
            "success": False,
            "message": err or f"启动失败：找不到命令「{command}」",
            "tools": []
        }
    except Exception as e:
        return {"success": False, "message": f"启动进程失败：{e}", "tools": []}

    async def send(payload: dict):
        """向进程 stdin 写入一条 JSON-RPC 消息（Content-Length 帧）。"""
        data = json.dumps(payload, ensure_ascii=False)
        framed = f"Content-Length: {len(data.encode())}\r\n\r\n{data}"
        proc.stdin.write(framed.encode())
        await proc.stdin.drain()

    async def recv(call_id) -> Optional[dict]:
        """
        从进程 stdout 读取带有 Content-Length 头的 JSON-RPC 帧，
        直到匹配 call_id 的响应出现，或超时。
        """
        buf = b""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=min(remaining, 2.0))
            except asyncio.TimeoutError:
                continue
            if not chunk:
                break
            buf += chunk
            # 解析所有完整帧
            while True:
                header_end = buf.find(b"\r\n\r\n")
                if header_end == -1:
                    break
                header = buf[:header_end].decode(errors="replace")
                content_length = None
                for line in header.splitlines():
                    if line.lower().startswith("content-length:"):
                        try:
                            content_length = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass
                if content_length is None:
                    buf = buf[header_end + 4:]
                    continue
                body_start = header_end + 4
                if len(buf) < body_start + content_length:
                    break  # 数据未到齐，继续读
                body = buf[body_start:body_start + content_length]
                buf = buf[body_start + content_length:]
                try:
                    msg = json.loads(body.decode())
                    if isinstance(msg, dict) and msg.get("id") == call_id:
                        return msg
                    # 跳过通知消息（无 id）继续等待
                except Exception:
                    pass
        return None

    try:
        # 1. initialize
        await send({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "codebot", "version": "1.0"},
            }
        })

        init_resp = await asyncio.wait_for(recv(1), timeout=timeout)
        if init_resp is None:
            return {
                "success": False,
                "message": f"服务器启动超时或无响应（命令：{cmd_display}）",
                "tools": []
            }
        if "error" in init_resp:
            err = init_resp["error"]
            return {
                "success": False,
                "message": f"initialize 失败：{err.get('message', str(err))}",
                "tools": []
            }

        # 2. notifications/initialized（无需等待响应）
        await send({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {}
        })

        # 3. tools/list
        await send({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        })

        tools_resp = await asyncio.wait_for(recv(2), timeout=timeout)
        if tools_resp is None:
            return {
                "success": False,
                "message": "tools/list 超时，服务器可能已启动但不支持工具列表",
                "tools": []
            }
        if "error" in tools_resp:
            err = tools_resp["error"]
            return {
                "success": False,
                "message": f"tools/list 失败：{err.get('message', str(err))}",
                "tools": []
            }

        tools = (tools_resp.get("result") or {}).get("tools") or []
        tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]
        return {
            "success": True,
            "message": f"连接成功，发现 {len(tools)} 个工具",
            "tools": tool_names,
            "tool_details": tools
        }

    except asyncio.TimeoutError:
        return {
            "success": False,
            "message": f"测试超时（{timeout:.0f}s），服务器未在规定时间内响应（命令：{cmd_display}）",
            "tools": []
        }
    except Exception as e:
        return {"success": False, "message": f"测试过程出错：{e}", "tools": []}
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            pass


@router.post("/{server_id}/test")
async def test_mcp_server(server_id: str):
    """测试 MCP Server 连接和工具列表"""
    servers = _read_all()
    server = _find_by_id(servers, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="MCP Server 不存在")

    transport = server.get("transport", "stdio")

    if transport == "sse":
        url = server.get("url", "")
        if not url:
            return {"success": False, "message": "SSE 模式下 URL 为空", "tools": []}

        # 使用与 tool_dispatcher 一致的 MCP 协议（支持 Streamable HTTP + 旧版 SSE）
        from core.tool_dispatcher import _mcp_rpc
        try:
            result = await _mcp_rpc(server, "tools/list", {}, call_id=1, timeout=20.0)
            if result is None:
                return {
                    "success": False,
                    "message": "连接失败：服务器无响应或协议不兼容，请检查 URL 和 API Key",
                    "tools": []
                }
            if "error" in result:
                err = result["error"]
                return {
                    "success": False,
                    "message": f"MCP 服务器返回错误: {err.get('message', str(err))}",
                    "tools": []
                }
            tools = (result.get("result") or {}).get("tools") or []
            tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]
            return {
                "success": True,
                "message": f"连接成功，发现 {len(tools)} 个工具",
                "tools": tool_names,
                "tool_details": tools
            }
        except Exception as e:
            return {"success": False, "message": f"测试失败: {str(e)}", "tools": []}

    elif transport == "stdio":
        command = server.get("command", "")
        if not command:
            return {"success": False, "message": "stdio 模式下命令为空", "tools": []}
        # 真正启动进程并通过 stdin/stdout 执行 MCP JSON-RPC
        try:
            result = await _test_stdio_mcp(server, timeout=30.0)
            return result
        except Exception as e:
            return {"success": False, "message": f"stdio 测试失败: {str(e)}", "tools": []}
    else:
        return {"success": False, "message": f"未知 transport 类型: {transport}", "tools": []}


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


# ── ModelScope MCP Hub ────────────────────────────────────────────────────

def _get_modelscope_token() -> str:
    """从集成配置或环境变量中获取 ModelScope API Token"""
    # 优先从 app_config 读取（对应 data/config.json 中的 integration.modelscope_api_key）
    try:
        from config import app_config
        token = (app_config.integration.modelscope_api_key or "").strip()
        if token:
            return token
    except Exception:
        pass
    # 回退到环境变量
    return os.environ.get("MODELSCOPE_API_KEY", "").strip()


@router.get("/modelscope/services")
async def list_modelscope_services(token: Optional[str] = Query(default=None)):
    """
    获取 ModelScope MCP Hub 上所有可用的 MCP 服务列表。
    token 参数可选；若未提供则从集成配置/环境变量中读取。
    """
    api_token = (token or "").strip() or _get_modelscope_token()
    if not api_token:
        raise HTTPException(
            status_code=400,
            detail="未找到 ModelScope API Key，请在设置→集成配置中填写，或通过 token 参数传入"
        )

    url = "https://www.modelscope.cn/api/v1/mcp/services/operational"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_token}"}
            )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="ModelScope API Key 无效或已过期")
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"ModelScope API 返回错误: HTTP {resp.status_code}"
            )
        body = resp.json()
        services = (body.get("Data") or {}).get("Result") or []
        # 规范化输出字段
        result = []
        for svc in services:
            urls = svc.get("operational_urls") or []
            sse_url = urls[0]["url"] if urls else None
            if not sse_url:
                continue
            result.append({
                "id": svc.get("id") or svc.get("name", ""),
                "name": svc.get("name", ""),
                "chinese_name": svc.get("chinese_name") or svc.get("name", ""),
                "description": svc.get("description") or svc.get("readme") or "",
                "url": sse_url,
                "tags": svc.get("tags") or [],
            })
        return {"success": True, "data": {"services": result, "total": len(result)}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 ModelScope 服务列表失败: {str(e)}")


class ModelScopeImportRequest(BaseModel):
    name: str                        # 显示名称
    service_id: str                  # ModelScope 服务 id / slug
    url: str                         # 从 API 返回的真实 SSE URL
    description: str = ""
    token: Optional[str] = None      # 若不传则从配置读取


@router.post("/modelscope/import")
async def import_modelscope_service(request: ModelScopeImportRequest):
    """
    将一个 ModelScope MCP 服务导入到本地 MCP 服务器列表。
    URL 必须是从 /modelscope/services 返回的真实 URL，不得手动构造。
    """
    if not request.url.startswith("http"):
        raise HTTPException(status_code=400, detail="URL 无效")

    api_token = (request.token or "").strip() or _get_modelscope_token()

    servers = _read_all()
    # 检查是否已存在相同 URL
    for s in servers:
        if s.get("url") == request.url:
            return {
                "success": False,
                "message": f"该服务已存在（名称：{s['name']}），无需重复添加",
                "data": s
            }

    server_id = uuid4().hex
    server = {
        "id": server_id,
        "name": request.name.strip() or request.service_id,
        "description": (request.description or f"ModelScope MCP: {request.service_id}").strip(),
        "transport": "sse",
        "command": None,
        "args": [],
        "url": request.url,
        # 认证通过 HTTP headers 传递（与 Cherry Studio 保持一致），而不是 env
        "headers": {"Authorization": f"Bearer {api_token}"} if api_token else {},
        "env": {},
        "enabled": True,
        "source": "modelscope",
        "service_id": request.service_id,
        "installed_at": datetime.now().isoformat()
    }
    servers.append(server)
    _write_all(servers)
    return {
        "success": True,
        "data": server,
        "message": f"ModelScope MCP「{server['name']}」已导入"
    }


# ── OpenCode CLI MCP 同步 ──────────────────────────────────────────────────

def _opencode_config_path() -> Optional[Path]:
    """返回 opencode 全局配置文件路径（opencode.json）"""
    home = Path.home()
    # Windows / Linux / macOS 统一放在 ~/.config/opencode/opencode.json
    candidate = home / ".config" / "opencode" / "opencode.json"
    if candidate.exists():
        return candidate
    return None


def _read_opencode_config() -> dict:
    path = _opencode_config_path()
    if path is None:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_opencode_config(data: dict):
    path = _opencode_config_path()
    if path is None:
        raise FileNotFoundError("找不到 opencode 配置文件（~/.config/opencode/opencode.json）")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _remove_from_opencode_config(server_names: List[str]):
    """
    从 opencode 配置文件中删除指定名称的 MCP 服务器条目。
    server_names: MCP 服务器的原始名称列表（将自动转为 sanitized key）。
    """
    if not server_names:
        return
    if _opencode_config_path() is None:
        return
    oc_config = _read_opencode_config()
    oc_mcp = oc_config.get("mcp") or {}
    changed = False
    for raw_name in server_names:
        key = _sanitize_mcp_key(raw_name) if raw_name else ""
        if key and key in oc_mcp:
            del oc_mcp[key]
            changed = True
    if changed:
        oc_config["mcp"] = oc_mcp
        _write_opencode_config(oc_config)


def _sanitize_mcp_key(name: str) -> str:
    """
    将 MCP 名称转为 opencode 合法的 key（纯 ASCII，空格和特殊字符替换为 -）。
    opencode 的 JSON schema 要求 mcp key 只能包含 ASCII 字母、数字、连字符、下划线、点。
    """
    import unicodedata
    import re
    # 先尝试把 Unicode 字符转为 ASCII 等价（如全角→半角）
    result = unicodedata.normalize("NFKC", name)
    # 替换空格和不合法字符为连字符
    result = re.sub(r"[^\w\-.]", "-", result, flags=re.ASCII)
    # 压缩连续连字符，去掉首尾连字符
    result = re.sub(r"-{2,}", "-", result).strip("-")
    return result or "mcp-server"


def _normalize_mcp_url_for_opencode(url: str) -> str:
    """
    将 MCP URL 规范化为 opencode remote 类型能正确连接的 SSE URL。

    opencode 的 remote 类型使用旧版 SSE 协议（GET 请求 + Accept: text/event-stream），
    因此需要确保 URL 指向 /sse 端点，而不是 /mcp（streamable-http）端点。

    魔搭（ModelScope）的 MCP 服务器同时提供两个端点：
    - /sse  → 旧版 SSE 协议，opencode remote 可正常连接
    - /mcp  → streamable-http 协议，opencode 不支持（会报 Invalid content type）

    规则：
    - URL 以 /mcp 结尾且域名是魔搭 → 换成 /sse
    - URL 以 /sse 结尾 → 保留不变（已经是正确格式）
    - 其他 URL → 不变
    """
    import re
    # 魔搭的 /mcp 端点 → 换成 /sse（opencode 只支持 SSE）
    if re.search(r"/mcp$", url) and "modelscope" in url:
        return re.sub(r"/mcp$", "/sse", url)
    return url


def _codebot_bind_host() -> str:
    host = str(getattr(app_config.network, "host", "") or "").strip()
    if host in ["", "0.0.0.0", "::"]:
        return "127.0.0.1"
    return host


def get_codebot_remote_sse_url() -> str:
    return f"http://{_codebot_bind_host()}:{app_config.network.port}/api/mcp/codebot/sse"


def _build_codebot_remote_mcp_entry() -> dict:
    return {
        "type": "remote",
        "url": get_codebot_remote_sse_url(),
        "enabled": True,
        "oauth": False,
    }


def _entry_matches(expected: dict, actual: Optional[dict]) -> bool:
    if not isinstance(actual, dict):
        return False
    return (
        actual.get("type") == expected.get("type")
        and actual.get("url") == expected.get("url")
        and bool(actual.get("enabled", True)) == bool(expected.get("enabled", True))
        and bool(actual.get("oauth", False)) == bool(expected.get("oauth", False))
    )


def get_codebot_remote_mcp_status() -> dict:
    has_opencode = _opencode_config_path() is not None
    expected = _build_codebot_remote_mcp_entry()
    if not has_opencode:
        return {
            "has_opencode": False,
            "registered": False,
            "key": CODEBOT_REMOTE_MCP_KEY,
            "url": expected["url"],
            "expected_entry": expected,
            "actual_entry": None,
        }
    oc_config = _read_opencode_config()
    actual = (oc_config.get("mcp") or {}).get(CODEBOT_REMOTE_MCP_KEY)
    return {
        "has_opencode": True,
        "registered": _entry_matches(expected, actual),
        "key": CODEBOT_REMOTE_MCP_KEY,
        "url": expected["url"],
        "expected_entry": expected,
        "actual_entry": actual,
    }


async def _is_opencode_connected() -> bool:
    client = OpenCodeClient(app_config.opencode.server_url)
    try:
        return await client.try_connect(attempts=1, delay=0)
    except Exception:
        return False
    finally:
        if getattr(client, "connected", False):
            try:
                await client.disconnect()
            except Exception:
                pass


def ensure_codebot_remote_mcp_in_opencode() -> dict:
    if _opencode_config_path() is None:
        return {"success": False, "changed": False, "message": "未找到 opencode 配置文件"}
    expected = _build_codebot_remote_mcp_entry()
    oc_config = _read_opencode_config()
    oc_config.setdefault("mcp", {})
    actual = oc_config["mcp"].get(CODEBOT_REMOTE_MCP_KEY)
    if _entry_matches(expected, actual):
        return {"success": True, "changed": False, "entry": expected}
    oc_config["mcp"][CODEBOT_REMOTE_MCP_KEY] = expected
    _write_opencode_config(oc_config)
    return {"success": True, "changed": True, "entry": expected}


def _codebot_server_to_opencode(server: dict) -> Optional[dict]:
    """把 codebot MCP server 转为 opencode MCP 条目格式，不支持的类型返回 None"""
    transport = server.get("transport", "stdio")
    if transport == "stdio":
        command = server.get("command", "")
        args = server.get("args") or []
        if not command:
            return None
        cmd_list = [command] + list(args)
        entry: dict = {"type": "local", "command": cmd_list, "enabled": server.get("enabled", True)}
        env = server.get("env") or {}
        if env:
            entry["environment"] = env  # opencode local 用 environment 字段
        return entry
    elif transport == "sse":
        url = server.get("url", "")
        if not url:
            return None
        # 将 SSE URL 转为 opencode 兼容的 streamable-http URL
        url = _normalize_mcp_url_for_opencode(url)
        # opencode 远程 MCP 统一用 "remote" type
        entry = {
            "type": "remote",
            "url": url,
            "enabled": server.get("enabled", True),
            # 使用 Bearer token 时必须禁用 OAuth，否则 opencode 会尝试 OAuth 流程然后失败
            "oauth": False,
        }
        # 将认证 headers 同步到 opencode 配置（opencode remote 支持 headers 字段）
        headers = server.get("headers") or {}
        if headers:
            entry["headers"] = headers
        elif server.get("env", {}).get("MODELSCOPE_API_KEY"):
            # 向后兼容：旧版存在 env 里的 token 转为 headers
            token = server["env"]["MODELSCOPE_API_KEY"]
            entry["headers"] = {"Authorization": f"Bearer {token}"}
        return entry
    return None


@router.get("/opencode/sync-status")
async def opencode_sync_status():
    """
    返回 codebot MCP 服务器与 opencode CLI 配置的同步状态。
    """
    oc_config = _read_opencode_config()
    oc_mcp: dict = oc_config.get("mcp") or {}
    cb_servers = _read_all()
    proxyable_servers = [s for s in cb_servers if _is_proxyable_external_server(s)]
    proxy_tools = await _list_external_proxy_tool_definitions()
    has_opencode = _opencode_config_path() is not None
    codebot_bridge = get_codebot_remote_mcp_status()
    only_in_oc = [{"name": k, "entry": v} for k, v in oc_mcp.items() if k != CODEBOT_REMOTE_MCP_KEY]
    direct_entries_in_opencode = [item for item in only_in_oc if isinstance(item.get("entry"), dict)]
    bridge_registered = bool(codebot_bridge.get("registered", False))
    in_sync = bool(has_opencode and bridge_registered and not direct_entries_in_opencode)
    return {
        "success": True,
        "data": {
            "has_opencode": has_opencode,
            "opencode_config_path": str(_opencode_config_path()) if has_opencode else None,
            "opencode_mcp_count": len(oc_mcp),
            "codebot_mcp_count": len(cb_servers),
            "missing_in_opencode": [],
            "only_in_opencode": only_in_oc,
            "direct_entries_in_opencode": direct_entries_in_opencode,
            "codebot_bridge": codebot_bridge,
            "bridge_registered": bridge_registered,
            "proxied_server_count": len(proxyable_servers),
            "proxied_tool_count": len(proxy_tools),
            "managed_mode": "codebot_proxy",
            "in_sync": in_sync,
        }
    }


@router.post("/opencode/sync")
async def sync_to_opencode():
    """
    将 codebot 中所有 MCP 服务器同步写入 opencode CLI 配置（仅追加/更新，不删除 opencode 独有的）。
    """
    if _opencode_config_path() is None:
        raise HTTPException(
            status_code=404,
            detail="找不到 opencode 配置文件，请确认已安装 opencode CLI（配置位于 ~/.config/opencode/opencode.json）"
        )

    oc_config = _read_opencode_config()
    if "mcp" not in oc_config:
        oc_config["mcp"] = {}

    cb_servers = _read_all()
    removed = []
    for s in cb_servers:
        raw_name = s.get("name", "").strip()
        if not raw_name:
            continue
        key = _sanitize_mcp_key(raw_name)
        if key in oc_config["mcp"]:
            del oc_config["mcp"][key]
            removed.append(key)

    added, updated = [], []
    if CODEBOT_REMOTE_MCP_KEY in oc_config["mcp"]:
        updated.append(CODEBOT_REMOTE_MCP_KEY)
    else:
        added.append(CODEBOT_REMOTE_MCP_KEY)
    oc_config["mcp"][CODEBOT_REMOTE_MCP_KEY] = _build_codebot_remote_mcp_entry()

    _write_opencode_config(oc_config)
    return {
        "success": True,
        "message": f"同步完成：新增 {len(added)} 个，更新 {len(updated)} 个，移除 {len(removed)} 个直接 MCP 条目",
        "data": {"added": added, "updated": updated, "removed": removed, "skipped": [], "codebot_bridge": get_codebot_remote_mcp_status()}
    }


# ── 自动同步 MCP 到 opencode（供 chat 路由在消息发送前调用）──────────────

# 记录上次同步时的 mcp_servers.json mtime，避免每次请求都重写文件
_last_sync_mtime: float = 0.0


def auto_sync_mcp_to_opencode() -> bool:
    """
    将 codebot MCP 服务器配置自动同步到 opencode CLI 配置文件。
    仅当 mcp_servers.json 有变化时才执行写入，避免频繁 I/O。
    同步策略：
    - 更新/新增 codebot 管理的 MCP 服务器到 opencode 配置
    - 删除 opencode 配置中已被 codebot 移除的服务器（仅针对 codebot 曾同步过的条目）
    返回 True 表示执行了同步，False 表示无需同步或 opencode 不可用。
    """
    global _last_sync_mtime

    # opencode 配置不存在则跳过（不报错）
    if _opencode_config_path() is None:
        return False

    mcp_file = settings.MCP_SERVERS_FILE
    current_mtime = 0.0
    if mcp_file.exists():
        try:
            current_mtime = mcp_file.stat().st_mtime
        except Exception:
            current_mtime = 0.0

    try:
        oc_config = _read_opencode_config()
        if "mcp" not in oc_config:
            oc_config["mcp"] = {}

        changed = False
        expected_bridge = _build_codebot_remote_mcp_entry()
        if not _entry_matches(expected_bridge, oc_config["mcp"].get(CODEBOT_REMOTE_MCP_KEY)):
            oc_config["mcp"][CODEBOT_REMOTE_MCP_KEY] = expected_bridge
            changed = True

        if mcp_file.exists() and current_mtime > _last_sync_mtime:
            cb_servers = _read_all()
            for s in cb_servers:
                raw_name = s.get("name", "").strip()
                if not raw_name:
                    continue
                key = _sanitize_mcp_key(raw_name)
                if key in oc_config["mcp"]:
                    del oc_config["mcp"][key]
                    changed = True

        if not changed:
            return False

        _write_opencode_config(oc_config)
        _last_sync_mtime = current_mtime
        return True
    except Exception:
        return False


def full_sync_mcp_to_opencode() -> dict:
    """
    将 codebot 所有 MCP 服务器完整同步到 opencode CLI 配置文件。
    这是强制全量同步版本，会：
    1. 写入/更新 codebot 管理的所有 MCP 服务器
    2. 删除 opencode 配置中，之前由 codebot 同步但现已被 codebot 删除的条目
    返回同步结果 dict。
    """
    global _last_sync_mtime

    if _opencode_config_path() is None:
        return {"success": False, "message": "未找到 opencode 配置文件", "added": [], "updated": [], "removed": []}

    try:
        oc_config = _read_opencode_config()
        if "mcp" not in oc_config:
            oc_config["mcp"] = {}

        cb_servers = _read_all()

        added, updated, removed = [], [], []

        for s in cb_servers:
            raw_name = s.get("name", "").strip()
            if not raw_name:
                continue
            key = _sanitize_mcp_key(raw_name)
            if key in oc_config["mcp"]:
                del oc_config["mcp"][key]
                removed.append(key)

        if CODEBOT_REMOTE_MCP_KEY in oc_config["mcp"]:
            updated.append(CODEBOT_REMOTE_MCP_KEY)
        else:
            added.append(CODEBOT_REMOTE_MCP_KEY)
        oc_config["mcp"][CODEBOT_REMOTE_MCP_KEY] = _build_codebot_remote_mcp_entry()

        _write_opencode_config(oc_config)
        # 更新 mtime 缓存，避免 auto_sync 再次写入
        mcp_file = settings.MCP_SERVERS_FILE
        try:
            _last_sync_mtime = mcp_file.stat().st_mtime
        except Exception:
            pass

        return {
            "success": True,
            "message": f"同步完成：新增 {len(added)} 个，更新 {len(updated)} 个，清理 {len(removed)} 个",
            "added": added,
            "updated": updated,
            "removed": removed
        }
    except Exception as e:
        return {"success": False, "message": f"同步失败：{e}", "added": [], "updated": [], "removed": []}


def get_enabled_mcp_servers_info() -> list:
    """
    返回所有已启用的 MCP 服务器摘要信息（name, description, transport, tools 等），
    供 prompt 增强时使用。
    """
    servers = _read_all()
    result = []
    for s in servers:
        if not s.get("enabled", True):
            continue
        result.append({
            "name": s.get("name", ""),
            "description": s.get("description", ""),
            "transport": s.get("transport", "stdio"),
            "url": s.get("url"),
            "command": s.get("command"),
        })
    return result


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


def _is_proxyable_external_server(server: dict) -> bool:
    return bool(
        isinstance(server, dict)
        and server.get("enabled", True)
        and str(server.get("transport") or "").lower() == "sse"
        and str(server.get("url") or "").strip()
    )


def _proxy_tool_name(server: dict, tool_name: str) -> str:
    return f"codebot_mcp__{server.get('id', '')}__{tool_name}"


def _parse_proxy_tool_name(tool_name: str) -> Optional[Tuple[str, str]]:
    prefix = "codebot_mcp__"
    if not str(tool_name or "").startswith(prefix):
        return None
    body = str(tool_name)[len(prefix):]
    if "__" not in body:
        return None
    server_id, original_name = body.split("__", 1)
    if not server_id or not original_name:
        return None
    return server_id, original_name


async def _list_proxy_tools_for_server(server: dict) -> List[dict]:
    from core.tool_dispatcher import _list_mcp_tools

    tools = await _list_mcp_tools(server)
    server_name = str(server.get("name") or server.get("id") or "unknown").strip()
    result: List[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        original_name = str(tool.get("name") or "").strip()
        if not original_name:
            continue
        description = str(tool.get("description") or "").strip()
        label = f"通过 Codebot 代理到第三方 MCP「{server_name}」"
        full_description = f"{label}。{description}" if description else label
        result.append({
            "name": _proxy_tool_name(server, original_name),
            "description": full_description,
            "inputSchema": tool.get("inputSchema") or {"type": "object", "properties": {}},
        })
    return result


async def _list_external_proxy_tool_definitions() -> List[dict]:
    servers = [server for server in _read_all() if _is_proxyable_external_server(server)]
    if not servers:
        return []
    groups = await asyncio.gather(
        *[_list_proxy_tools_for_server(server) for server in servers],
        return_exceptions=True,
    )
    result: List[dict] = []
    for group in groups:
        if isinstance(group, list):
            result.extend(group)
    return result


async def _call_external_proxy_tool(tool_name: str, arguments: dict) -> dict:
    parsed = _parse_proxy_tool_name(tool_name)
    if not parsed:
        raise ValueError("无效的第三方 MCP 工具名称")
    server_id, original_name = parsed
    servers = _read_all()
    server = _find_by_id(servers, server_id)
    if not server:
        raise ValueError("第三方 MCP 服务器不存在")
    if not _is_proxyable_external_server(server):
        raise ValueError("第三方 MCP 服务器未启用或配置无效")
    from core.tool_dispatcher import _mcp_rpc

    response = await _mcp_rpc(
        server,
        "tools/call",
        {"name": original_name, "arguments": arguments or {}},
        call_id=2,
        timeout=40.0,
    )
    if response is None:
        raise ValueError(f"第三方 MCP 调用无响应：{server.get('name')} / {original_name}")
    if "error" in response:
        error = response.get("error") or {}
        raise ValueError(str(error.get("message") or error or "第三方 MCP 调用失败"))
    return response.get("result") or {"content": []}


def _build_codebot_tool_definitions() -> List[dict]:
    return [
        {
            "name": "codebot_get_runtime_overview",
            "description": "获取 Codebot 当前第三方运行总览，包括 OpenCode 连接、MCP/技能同步、任务数与记忆统计。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "codebot_list_conversations",
            "description": "列出 Codebot 中最近的对话，用于让 OpenCode 了解历史会话与展示上下文。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "archived": {"type": "boolean"},
                },
            },
        },
        {
            "name": "codebot_get_conversation_messages",
            "description": "读取指定对话的消息列表。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "conversation_id": {"type": "integer"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["conversation_id"],
            },
        },
        {
            "name": "codebot_search_memories",
            "description": "按语义搜索 Codebot 记忆。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                    "category": {"type": "string"},
                    "include_archived": {"type": "boolean"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "codebot_save_memory",
            "description": "向 Codebot 保存一条长期记忆。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "category": {"type": "string"},
                    "metadata": {"type": "object"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "codebot_list_memories",
            "description": "列出 Codebot 记忆。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "archived": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "offset": {"type": "integer", "minimum": 0},
                },
            },
        },
        {
            "name": "codebot_create_task",
            "description": "在 Codebot 中创建定时任务，可直接给 cron_expression，或用 schedule_description 让 Codebot 生成 cron。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "task_prompt": {"type": "string"},
                    "cron_expression": {"type": "string"},
                    "schedule_description": {"type": "string"},
                    "notify_channels": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                },
                "required": ["name", "task_prompt"],
            },
        },
        {
            "name": "codebot_list_tasks",
            "description": "列出 Codebot 定时任务。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "enabled_only": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
        },
        {
            "name": "codebot_delete_task",
            "description": "删除 Codebot 定时任务。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "codebot_list_skills",
            "description": "列出 Codebot 技能目录与已同步到 OpenCode 的技能。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
        },
        {
            "name": "codebot_read_skill",
            "description": "读取指定技能的 SKILL.md 内容。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "skill_id": {"type": "string"},
                },
                "required": ["skill_id"],
            },
        },
        {
            "name": "codebot_list_mcp_servers",
            "description": "列出 Codebot 管理的第三方 MCP 服务器。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "enabled_only": {"type": "boolean"},
                },
            },
        },
    ]


def _make_mcp_success_response(request_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_mcp_error_response(request_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def _codebot_overview_payload() -> dict:
    manager = MemoryManager()
    try:
        memory_counts = await manager.get_storage_counts()
        conversations = await manager.get_conversations(limit=5, archived=False)
    finally:
        manager.close()
    skills_sync = await skills_router.get_opencode_sync_status()
    skills_sync_data = skills_sync.get("data") if isinstance(skills_sync, dict) else {}
    bridge_status = get_codebot_remote_mcp_status()
    scheduler = scheduler_router.scheduler
    tasks = scheduler.list_tasks() if scheduler else []
    return {
        "mode": "third_party",
        "mcp_server_name": CODEBOT_REMOTE_SERVER_NAME,
        "remote_sse_url": get_codebot_remote_sse_url(),
        "bridge_status": bridge_status,
        "skills_sync": skills_sync_data,
        "memory_counts": memory_counts,
        "recent_conversations": conversations,
        "task_count": len(tasks),
        "opencode_server_url": app_config.opencode.server_url,
    }


async def _call_codebot_tool(name: str, arguments: dict) -> dict:
    args = arguments or {}
    if name == "codebot_get_runtime_overview":
        payload = await _codebot_overview_payload()
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}

    if name == "codebot_list_conversations":
        manager = MemoryManager()
        try:
            items = await manager.get_conversations(
                limit=max(1, min(int(args.get("limit", 20) or 20), 100)),
                archived=bool(args.get("archived", False)),
            )
        finally:
            manager.close()
        return {"content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False)}]}

    if name == "codebot_get_conversation_messages":
        conversation_id = int(args.get("conversation_id"))
        manager = MemoryManager()
        try:
            items = await manager.get_messages(
                conversation_id=conversation_id,
                limit=max(1, min(int(args.get("limit", 50) or 50), 200)),
            )
        finally:
            manager.close()
        return {"content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False)}]}

    if name == "codebot_search_memories":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        manager = MemoryManager()
        try:
            items = await manager.search_memories(
                query=query,
                top_k=max(1, min(int(args.get("top_k", 5) or 5), 20)),
                category=(str(args.get("category") or "").strip() or None),
                include_archived=bool(args.get("include_archived", False)),
            )
        finally:
            manager.close()
        return {"content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False)}]}

    if name == "codebot_save_memory":
        content = str(args.get("content") or "").strip()
        if not content:
            raise ValueError("content 不能为空")
        category = str(args.get("category") or "note").strip() or "note"
        metadata = args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
        manager = MemoryManager()
        try:
            await manager.save_long_term_memory(content=content, category=category, metadata=metadata)
        finally:
            manager.close()
        payload = {"saved": True, "content": content, "category": category, "metadata": metadata}
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}

    if name == "codebot_list_memories":
        manager = MemoryManager()
        try:
            items = await manager.get_memories(
                category=(str(args.get("category") or "").strip() or None),
                archived=bool(args.get("archived", False)),
                limit=max(1, min(int(args.get("limit", 50) or 50), 200)),
                offset=max(0, int(args.get("offset", 0) or 0)),
            )
        finally:
            manager.close()
        return {"content": [{"type": "text", "text": json.dumps(items, ensure_ascii=False)}]}

    if name == "codebot_create_task":
        scheduler = scheduler_router.scheduler
        if scheduler is None:
            raise ValueError("调度器未初始化")
        task_name = str(args.get("name") or "").strip()
        task_prompt = str(args.get("task_prompt") or "").strip()
        if not task_name or not task_prompt:
            raise ValueError("name 和 task_prompt 不能为空")
        cron_expression = str(args.get("cron_expression") or "").strip()
        if not cron_expression:
            schedule_description = str(args.get("schedule_description") or "").strip()
            if not schedule_description:
                raise ValueError("cron_expression 与 schedule_description 至少提供一个")
            cron_data = await scheduler_router.generate_cron_from_text(schedule_description)
            cron_expression = str((cron_data or {}).get("cron") or "").strip()
        if not cron_expression:
            raise ValueError("无法生成有效的 cron_expression")
        notify_channels = args.get("notify_channels")
        if not isinstance(notify_channels, list) or not notify_channels:
            # 从全局通知配置读取默认渠道
            from config import app_config as _app_config
            _nc = _app_config.notification
            notify_channels = []
            if _nc.app_enabled:
                notify_channels.append("app")
            if _nc.desktop_enabled:
                notify_channels.append("desktop")
            if _nc.lark_enabled:
                notify_channels.append("lark")
            if _nc.email_enabled:
                notify_channels.append("email")
            if not notify_channels:
                notify_channels = ["app"]
        task = scheduler.create_task(
            name=task_name,
            cron_expression=cron_expression,
            task_prompt=task_prompt,
            notify_channels=[str(item) for item in notify_channels if str(item).strip()],
        )
        return {"content": [{"type": "text", "text": json.dumps(task.to_dict(), ensure_ascii=False)}]}

    if name == "codebot_list_tasks":
        scheduler = scheduler_router.scheduler
        if scheduler is None:
            return {"content": [{"type": "text", "text": "[]"}]}
        items = [task.to_dict() for task in scheduler.list_tasks()]
        if bool(args.get("enabled_only", False)):
            items = [item for item in items if item.get("enabled")]
        limit = max(1, min(int(args.get("limit", 50) or 50), 200))
        return {"content": [{"type": "text", "text": json.dumps(items[:limit], ensure_ascii=False)}]}

    if name == "codebot_delete_task":
        scheduler = scheduler_router.scheduler
        if scheduler is None:
            raise ValueError("调度器未初始化")
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("task_id 不能为空")
        scheduler.delete_task(task_id)
        return {"content": [{"type": "text", "text": json.dumps({"deleted": True, "task_id": task_id}, ensure_ascii=False)}]}

    if name == "codebot_list_skills":
        items = skills_router._list_skills()
        source = str(args.get("source") or "").strip()
        if source:
            items = [item for item in items if str(item.get("source") or "") == source]
        limit = max(1, min(int(args.get("limit", 100) or 100), 200))
        return {"content": [{"type": "text", "text": json.dumps(items[:limit], ensure_ascii=False)}]}

    if name == "codebot_read_skill":
        skill_id = str(args.get("skill_id") or "").strip()
        if not skill_id:
            raise ValueError("skill_id 不能为空")
        content = _read_skill_content_by_id(skill_id)
        if content is None:
            raise ValueError("找不到对应技能内容")
        return {"content": [{"type": "text", "text": content}]}

    if name == "codebot_list_mcp_servers":
        items = _read_all()
        if bool(args.get("enabled_only", False)):
            items = [item for item in items if item.get("enabled", True)]
        payload = {
            "codebot_bridge": get_codebot_remote_mcp_status(),
            "servers": items,
        }
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}

    raise ValueError(f"未知工具：{name}")


def _read_skill_content_by_id(skill_id: str) -> Optional[str]:
    if skill_id.startswith("builtin:"):
        slug = skill_id.split(":", 1)[1]
        path = settings.SKILLS_DIR / slug / "SKILL.md"
        return path.read_text(encoding="utf-8") if path.exists() else None
    if skill_id.startswith("opencode:"):
        slug = skill_id.split(":", 1)[1]
        path = Path.home() / ".agents" / "skills" / slug / "SKILL.md"
        return path.read_text(encoding="utf-8") if path.exists() else None
    if skill_id.startswith("custom:"):
        body = skill_id[len("custom:"):]
        raw_path, slug = body.rsplit(":", 1)
        path = Path(raw_path) / slug / "SKILL.md"
        return path.read_text(encoding="utf-8") if path.exists() else None
    return None


async def _handle_codebot_jsonrpc(payload: dict) -> Optional[dict]:
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return _make_mcp_error_response(payload.get("id") if isinstance(payload, dict) else None, -32600, "Invalid Request")
    request_id = payload.get("id")
    method = str(payload.get("method") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _make_mcp_success_response(
            request_id,
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": CODEBOT_REMOTE_SERVER_NAME,
                    "version": app_config.version,
                },
            },
        )
    if method == "ping":
        return _make_mcp_success_response(request_id, {})
    if method == "tools/list":
        proxy_tools = await _list_external_proxy_tool_definitions()
        return _make_mcp_success_response(request_id, {"tools": _build_codebot_tool_definitions() + proxy_tools})
    if method == "resources/list":
        return _make_mcp_success_response(request_id, {"resources": []})
    if method == "prompts/list":
        return _make_mcp_success_response(request_id, {"prompts": []})
    if method == "tools/call":
        tool_name = str(params.get("name") or "").strip()
        if not tool_name:
            return _make_mcp_error_response(request_id, -32602, "缺少工具名称")
        try:
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            if _parse_proxy_tool_name(tool_name):
                result = await _call_external_proxy_tool(tool_name, arguments)
            else:
                result = await _call_codebot_tool(tool_name, arguments)
            return _make_mcp_success_response(request_id, result)
        except Exception as exc:
            return _make_mcp_error_response(request_id, -32603, str(exc))
    return _make_mcp_error_response(request_id, -32601, f"Method not found: {method}")


def _format_sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


@router.get("/codebot/status")
async def get_codebot_third_party_status():
    skills_sync = await skills_router.get_opencode_sync_status()
    proxied_servers = [server for server in _read_all() if _is_proxyable_external_server(server)]
    proxy_tools = await _list_external_proxy_tool_definitions()
    opencode_connected = await _is_opencode_connected()
    skills_sync_data = skills_sync.get("data") if isinstance(skills_sync, dict) else {}
    return {
        "success": True,
        "data": {
            "mode": "third_party",
            "desktop_role": "OpenCode Desktop Companion",
            "server_name": CODEBOT_REMOTE_SERVER_NAME,
            "sse_url": get_codebot_remote_sse_url(),
            "message_url_template": f"http://{_codebot_bind_host()}:{app_config.network.port}/api/mcp/codebot/messages?sessionId={{session_id}}",
            "bridge_status": get_codebot_remote_mcp_status(),
            "opencode_server_url": app_config.opencode.server_url,
            "opencode_connected": opencode_connected,
            "skills_sync": skills_sync_data,
            "builtin_skill_count": int(skills_sync_data.get("total_codebot") or 0),
            "synced_skill_count": int(skills_sync_data.get("total_synced") or 0),
            "proxied_servers": [
                {
                    "id": server.get("id"),
                    "name": server.get("name"),
                    "url": server.get("url"),
                    "source": server.get("source"),
                    "service_id": server.get("service_id"),
                }
                for server in proxied_servers
            ],
            "proxied_server_count": len(proxied_servers),
            "proxied_tool_count": len(proxy_tools),
        },
    }


@router.get("/codebot/opencode-entry")
async def get_codebot_opencode_entry():
    return {
        "success": True,
        "data": {
            "key": CODEBOT_REMOTE_MCP_KEY,
            "entry": _build_codebot_remote_mcp_entry(),
        },
    }


@router.post("/codebot/register")
async def register_codebot_to_opencode():
    result = ensure_codebot_remote_mcp_in_opencode()
    status = get_codebot_remote_mcp_status()
    return {
        "success": bool(result.get("success")),
        "message": "Codebot bridge 已写入 OpenCode 配置" if result.get("success") else result.get("message"),
        "data": {
            "result": result,
            "status": status,
        },
    }


@router.get("/codebot/sse")
async def codebot_mcp_sse(request: Request):
    session_id = uuid4().hex
    queue: asyncio.Queue = asyncio.Queue()
    _codebot_mcp_sessions[session_id] = {
        "queue": queue,
        "created_at": time.time(),
        "last_seen": time.time(),
    }
    endpoint = f"{request.base_url}api/mcp/codebot/messages?sessionId={session_id}"
    await queue.put(_format_sse("endpoint", endpoint))

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=15)
                    yield chunk
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _codebot_mcp_sessions.pop(session_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/codebot/messages")
async def codebot_mcp_messages(
    request: Request,
    sessionId: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
):
    session_key = sessionId or session_id
    if not session_key or session_key not in _codebot_mcp_sessions:
        return JSONResponse(status_code=400, content={"error": "invalid session"})
    session = _codebot_mcp_sessions[session_key]
    session["last_seen"] = time.time()
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content=_make_mcp_error_response(None, -32700, "Parse error"))

    if isinstance(payload, list):
        responses = [await _handle_codebot_jsonrpc(item) for item in payload]
        for item in responses:
            if item is not None:
                await session["queue"].put(_format_sse("message", json.dumps(item, ensure_ascii=False)))
        return Response(status_code=202)

    response_payload = await _handle_codebot_jsonrpc(payload)
    if response_payload is not None:
        await session["queue"].put(_format_sse("message", json.dumps(response_payload, ensure_ascii=False)))
    return Response(status_code=202)
