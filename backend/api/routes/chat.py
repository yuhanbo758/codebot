"""
聊天 API 路由
"""
from fastapi import APIRouter, HTTPException, Body, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timedelta
from uuid import uuid4
import re
import json
import base64
import tempfile
import os
import hashlib
from urllib.parse import urlparse

import asyncio

from loguru import logger
from config import settings, app_config
from database.init_db import conversations_db
from core.memory_manager import MemoryManager
from core.opencode_ws import OpenCodeClient, _conversation_current_session, is_conversation_running, unmark_conversation_running
from core.memory_extractor import extract_and_save_background
from api.routes import scheduler as scheduler_router
from api.routes import mcp as mcp_router
from core.tool_dispatcher import build_augmented_prompt
from utils.installer import start_opencode_server

router = APIRouter()
opencode_ws: Optional[OpenCodeClient] = None
chat_memory_manager: Optional[MemoryManager] = None
# 由 main.py lifespan 注入（可为 None）
sandbox_manager = None

# 任务队列：key=conversation_id(str), value=asyncio.Queue[dict]
_task_queues: dict = {}
_queue_runners: dict = {}  # key=conversation_id -> asyncio.Task
_runtime_stream_state: Dict[str, Dict[str, Any]] = {}


class MessageRequest(BaseModel):
    """消息请求"""
    content: str
    conversation_id: Optional[int] = None


class MessageResponse(BaseModel):
    """消息响应"""
    success: bool
    data: dict
    message: str


class AttachedFile(BaseModel):
    name: str
    type: str   # mime type
    content: str  # base64 encoded for binary, plain text for text files
    is_text: bool = True


class SendMessageRequest(BaseModel):
    conversation_id: int
    message: str
    model: Optional[str] = None
    mode: Optional[str] = None  # agent mode: "plan" or "build"
    attached_files: Optional[List[AttachedFile]] = None

class UpdateTitleRequest(BaseModel):
    title: str

class TogglePinnedRequest(BaseModel):
    pinned: bool

class ToggleArchiveRequest(BaseModel):
    archived: bool

class ToggleGroupRequest(BaseModel):
    is_group: bool

class SkillGenerateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: str = "1.0.0"
    source: Optional[str] = None
    enabled: bool = True
    message_limit: int = 50

def generate_conversation_title(content: str) -> str:
    text = _sanitize_assistant_output(content)
    text = " ".join(text.strip().split())
    text = re.sub(r"`{1,3}[\s\S]*?`{1,3}", "", text).strip()
    for sep in ["\n", "。", "！", "？", "；", ";"]:
        if sep in text:
            text = text.split(sep, 1)[0].strip()
            break
    if any(token in text.lower() for token in ["system_policy", "conversation_context", "internal_context"]):
        text = ""
    if not text:
        return "新对话"
    max_length = 20
    return text if len(text) <= max_length else f"{text[:max_length]}..."


def _sanitize_assistant_output(content: str) -> str:
    if not content:
        return ""
    text = str(content).replace("\r\n", "\n")
    text = re.sub(r"(?is)<(system_policy|conversation_context)>[\s\S]*?</\1>", "", text)
    text = re.sub(r"(?is)</?(system_policy|conversation_context)>", "", text)
    text = re.sub(r"(?m)^【用户输入】.*$", "", text)
    instruction_idx = text.find("请只输出给用户的最终结果")
    if instruction_idx >= 0:
        suffix = text[instruction_idx:]
        dot_idx = suffix.find("。")
        if dot_idx >= 0:
            text = suffix[dot_idx + 1:]
        else:
            text = ""
    if any(tag in text.lower() for tag in ["system_policy", "conversation_context", "internal_context"]):
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        text = lines[-1] if lines else ""
    text = text.strip()
    return text


def _skill_content_is_noise(text: str) -> bool:
    if not text:
        return True
    lower = text.lower()
    blocked = [
        "system_policy",
        "conversation_context",
        "internal_context",
        "请只输出给用户的最终结果",
    ]
    return any(token in lower for token in blocked)


def _runtime_state(conv_id: str) -> Dict[str, Any]:
    state = _runtime_stream_state.get(conv_id)
    if state is None:
        state = {"seq": 0, "events": [], "content": "", "running": False, "updated_at": datetime.now()}
        _runtime_stream_state[conv_id] = state
    return state


def _cleanup_runtime_states():
    now = datetime.now()
    to_remove = []
    for conv_id, state in _runtime_stream_state.items():
        updated_at = state.get("updated_at") or now
        running = bool(state.get("running"))
        if not running and (now - updated_at) > timedelta(minutes=20):
            to_remove.append(conv_id)
    for conv_id in to_remove:
        _runtime_stream_state.pop(conv_id, None)


def _runtime_start(conv_id: str):
    _cleanup_runtime_states()
    state = _runtime_state(conv_id)
    state["seq"] = 0
    state["events"] = []
    state["content"] = ""
    state["running"] = True
    state["updated_at"] = datetime.now()


def _runtime_set_content(conv_id: str, content: str):
    state = _runtime_state(conv_id)
    state["content"] = content or ""
    state["updated_at"] = datetime.now()


def _runtime_append_event(conv_id: str, event: dict):
    state = _runtime_state(conv_id)
    state["seq"] = int(state.get("seq", 0)) + 1
    payload = dict(event)
    payload["seq"] = state["seq"]
    payload["created_at"] = datetime.utcnow().isoformat() + "Z"
    events = state.get("events", [])
    events.append(payload)
    if len(events) > 200:
        events = events[-200:]
    state["events"] = events
    state["updated_at"] = datetime.now()


def _runtime_finish(conv_id: str, content: str = ""):
    state = _runtime_state(conv_id)
    if content:
        state["content"] = content
    state["running"] = False
    state["updated_at"] = datetime.now()


def _runtime_snapshot(conv_id: str, since_seq: int = 0) -> Dict[str, Any]:
    state = _runtime_stream_state.get(conv_id)
    if not state:
        return {"events": [], "last_seq": int(since_seq), "content": "", "running": False}
    events = state.get("events", [])
    filtered = [e for e in events if int(e.get("seq", 0)) > int(since_seq)]
    return {
        "events": filtered,
        "last_seq": int(state.get("seq", 0)),
        "content": state.get("content", "") or "",
        "running": bool(state.get("running")),
    }


async def _execute_opencode_client(message: str, model: Optional[str] = None, mode: Optional[str] = None, conversation_id: Optional[str] = None) -> Tuple[Optional[str], bool]:
    client = opencode_ws
    created_client = False
    try:
        if client is None:
            client = OpenCodeClient(app_config.opencode.server_url)
            created_client = True
        ok = await client.try_connect(attempts=3, delay=0.4, open_timeout=1.0)
        if not ok:
            return None, False
        result = await client.execute_task(message, model=model, mode=mode, conversation_id=conversation_id)
        if result.success:
            return _sanitize_assistant_output(result.content or "") or None, True
        return result.error or None, True
    except Exception as e:
        logger.error(f"OpenCode 调用失败: {e}")
        return None, False
    finally:
        if created_client and client and getattr(client, "connected", False):
            try:
                await client.disconnect()
            except Exception:
                pass


async def _execute_opencode_client_with_parts(
    message: str,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Tuple[Optional[str], bool, List[dict]]:
    client = opencode_ws
    created_client = False
    try:
        if client is None:
            client = OpenCodeClient(app_config.opencode.server_url)
            created_client = True
        ok = await client.try_connect(attempts=3, delay=0.4, open_timeout=1.0)
        if not ok:
            return None, False, []
        result = await client.execute_task(message, model=model, mode=mode, conversation_id=conversation_id)
        if result.success:
            return _sanitize_assistant_output(result.content or "") or None, True, result.parts or []
        return result.error or None, True, []
    except Exception as e:
        logger.error(f"OpenCode 调用失败: {e}")
        return None, False, []
    finally:
        if created_client and client and getattr(client, "connected", False):
            try:
                await client.disconnect()
            except Exception:
                pass


# ── MCP 聊天意图识别与处理 ─────────────────────────────────────────────────

def _looks_like_mcp_message(message: str) -> bool:
    """判断消息是否与 MCP Server 管理相关"""
    if not message:
        return False
    text = message.strip()
    mcp_keywords = ["mcp", "MCP", "model context protocol"]
    action_keywords = ["添加", "新增", "安装", "配置", "删除", "移除", "卸载", "启用", "禁用", "列出", "查看", "查询"]
    has_mcp = any(kw.lower() in text.lower() for kw in mcp_keywords)
    if not has_mcp:
        return False
    has_action = any(kw in text for kw in action_keywords) or re.search(
        r"(server|服务器|服务|工具)", text, re.IGNORECASE
    )
    return bool(has_action) or has_mcp


def _extract_mcp_command_from_message(message: str) -> Optional[dict]:
    """
    尝试从聊天消息中提取 MCP Server 配置。
    支持格式示例：
      - 添加 MCP 服务器 名称=filesystem 命令=npx 参数=-y @modelcontextprotocol/server-filesystem
      - 帮我配置一个 MCP，命令：uvx mcp-server-git
      - 新增 SSE 类型 MCP 服务器，URL=http://localhost:3000/sse
    返回 dict 或 None（解析失败）。
    """
    text = message.strip()

    # 优先尝试 JSON 格式（用户直接粘贴）
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if isinstance(data, dict) and ("command" in data or "url" in data):
                return {
                    "name": str(data.get("name") or data.get("id") or "").strip(),
                    "description": str(data.get("description") or "").strip(),
                    "transport": str(data.get("transport") or "stdio"),
                    "command": str(data.get("command") or "").strip() or None,
                    "args": data.get("args") or [],
                    "url": str(data.get("url") or "").strip() or None,
                    "env": data.get("env") or {},
                }
        except Exception:
            pass

    result: dict = {
        "name": "",
        "description": "",
        "transport": "stdio",
        "command": None,
        "args": [],
        "url": None,
        "env": {},
    }

    # SSE URL 识别
    url_match = re.search(r"url\s*[=:=]\s*(\S+)", text, re.IGNORECASE)
    if not url_match:
        url_match = re.search(r"https?://\S+", text)
    if url_match:
        result["url"] = url_match.group(1) if "=" in url_match.group(0) else url_match.group(0)
        result["transport"] = "sse"

    # stdio 命令识别
    cmd_match = re.search(
        r"(?:命令|command)\s*[=:＝]\s*([^\n，,;；]+)",
        text, re.IGNORECASE
    )
    if cmd_match:
        cmd_str = cmd_match.group(1).strip()
        parts = cmd_str.split()
        result["command"] = parts[0] if parts else None
        result["args"] = parts[1:] if len(parts) > 1 else []
    elif not result["url"]:
        # 尝试识别常见命令行模式：npx/uvx/node/python
        cmd_inline = re.search(
            r"\b(npx|uvx|node|python|python3|uv|deno|bun)\s+([^\n，,;；\"\']+)",
            text
        )
        if cmd_inline:
            parts = cmd_inline.group(0).split()
            result["command"] = parts[0]
            result["args"] = parts[1:]

    # 名称识别
    name_match = re.search(r"(?:名称|name|叫做|叫)\s*[=:＝]?\s*([^\s，,;；\n]+)", text, re.IGNORECASE)
    if name_match:
        result["name"] = name_match.group(1).strip()
    elif result["command"]:
        # 从命令自动命名
        cmd_base = result["args"][-1] if result["args"] else result["command"]
        result["name"] = re.sub(r"[@/].*", "", cmd_base).strip() or result["command"]
    elif result["url"]:
        try:
            from urllib.parse import urlparse as _urlparse
            result["name"] = _urlparse(result["url"]).hostname or "mcp-sse"
        except Exception:
            result["name"] = "mcp-sse"

    # 描述识别
    desc_match = re.search(r"(?:描述|description|说明)\s*[=:＝]\s*([^\n，,;；]+)", text, re.IGNORECASE)
    if desc_match:
        result["description"] = desc_match.group(1).strip()

    if not result["command"] and not result["url"]:
        return None

    return result


async def _try_handle_mcp_message(message: str) -> Optional[str]:
    """尝试将聊天消息路由为 MCP 操作，成功返回回复字符串，否则返回 None。"""
    if not _looks_like_mcp_message(message):
        return None

    text = message.strip()
    lower = text.lower()

    # ── 查询/列出 ──────────────────────────────────────────────────────────
    list_keywords = ["列出", "查看", "查询", "显示", "有哪些", "有什么", "list"]
    if any(kw in lower for kw in list_keywords):
        from api.routes.mcp import _read_all
        servers = _read_all()
        if not servers:
            return "当前没有配置任何 MCP Server。\n可在「MCP」页面手动添加，也可以告诉我想添加什么 MCP Server。"
        lines = [f"当前共有 {len(servers)} 个 MCP Server："]
        for s in servers:
            state = "启用" if s.get("enabled", True) else "禁用"
            transport = s.get("transport", "stdio")
            cmd_or_url = s.get("command") or s.get("url") or ""
            lines.append(f"- [{state}] **{s['name']}** ({transport}) `{cmd_or_url}`")
        lines.append("\n可在「MCP」页面管理。")
        return "\n".join(lines)

    # ── 删除/移除 ──────────────────────────────────────────────────────────
    delete_keywords = ["删除", "移除", "卸载", "remove", "delete"]
    if any(kw in lower for kw in delete_keywords):
        from api.routes.mcp import _read_all, _write_all
        servers = _read_all()
        # 尝试按名称匹配
        name_match = re.search(
            r"(?:\u5220\u9664|\u79fb\u9664|\u5378\u8f7d|remove|delete)\s+(?:mcp\s*)?(?:\u670d\u52a1\u5668?|server)?\s*\S*?([A-Za-z0-9_\-\.]+)",
            text, re.IGNORECASE
        )
        if not name_match:
            # 更宽松匹配：删除/移除 后面的第一个非空词
            name_match = re.search(
                r"(?:\u5220\u9664|\u79fb\u9664|\u5378\u8f7d|remove|delete)[^\w]*([A-Za-z0-9_\-\.]+)",
                text, re.IGNORECASE
            )
        if name_match:
            target_name = name_match.group(1).strip().strip("\"'")
            matched = [s for s in servers if target_name.lower() in s["name"].lower()]
            if matched:
                s = matched[0]
                servers = [sv for sv in servers if sv["id"] != s["id"]]
                _write_all(servers)
                return f"已删除 MCP Server「{s['name']}」。"
            return f"未找到名称包含「{target_name}」的 MCP Server，请检查名称或在「MCP」页面操作。"
        return "请告诉我要删除哪个 MCP Server 的名称，例如：删除 MCP 服务器 filesystem"

    # ── 添加/新增/安装/配置 ────────────────────────────────────────────────
    add_keywords = ["添加", "新增", "安装", "配置", "add", "install", "register", "注册"]
    if any(kw in lower for kw in add_keywords) or re.search(
        r"(npx|uvx|node|python)\s+", text
    ):
        parsed = _extract_mcp_command_from_message(message)
        if not parsed:
            return (
                "我识别到你想添加 MCP Server，但没能解析出有效的命令或 URL。\n"
                "请参考以下格式：\n"
                "- **stdio 模式**：`添加 MCP 服务器，命令=npx -y @modelcontextprotocol/server-filesystem`\n"
                "- **SSE 模式**：`添加 MCP 服务器，URL=http://localhost:3000/sse`\n"
                "也可以在「MCP」页面手动填写。"
            )
        try:
            server = mcp_router.add_server_from_chat(
                name=parsed["name"] or "未命名",
                transport=parsed["transport"],
                command=parsed["command"],
                args=parsed["args"],
                url=parsed["url"],
                env=parsed["env"],
                description=parsed["description"],
            )
            transport_label = "SSE" if parsed["transport"] == "sse" else "stdio"
            detail = f"URL：`{parsed['url']}`" if parsed["url"] else f"命令：`{parsed['command']} {' '.join(parsed['args'])}`.strip()"
            return (
                f"已添加 MCP Server「{server['name']}」（{transport_label} 模式）。\n"
                f"{detail}\n"
                "可在「MCP」页面查看和管理。"
            )
        except Exception as e:
            logger.error(f"聊天添加 MCP Server 失败: {e}")
            return f"添加 MCP Server 失败：{e}\n请在「MCP」页面手动添加。"

    return None


async def _execute_sandbox(prompt: str) -> Optional[str]:
    """尝试在沙箱 VM 中执行任务，返回结果字符串；沙箱未就绪时返回 None（降级到本地执行）。"""
    if sandbox_manager is None:
        return None
    if not app_config.sandbox.enabled:
        return None
    try:
        result = await sandbox_manager.execute(prompt)
        if result.success:
            return result.content or ""
        logger.warning(f"沙箱执行失败（降级本地）: {result.error}")
        return None
    except Exception as e:
        logger.warning(f"沙箱执行异常（降级本地）: {e}")
        return None


def _should_use_sandbox(message: str) -> bool:
    """根据执行模式配置决定是否路由到沙箱。"""
    mode = getattr(app_config.sandbox, "execution_mode", "auto")
    if not app_config.sandbox.enabled:
        return False
    if mode == "sandbox":
        return True
    if mode == "local":
        return False
    # auto 模式：含文件操作/代码执行等高风险关键词时走沙箱
    if mode == "auto":
        risky_patterns = [
            r"执行代码", r"运行脚本", r"execute\s+code", r"run\s+script",
            r"rm\s+-rf", r"删除文件", r"格式化", r"sudo", r"chmod",
            r"pip\s+install", r"npm\s+install", r"apt[-\s]",
        ]
        import re as _re
        for pat in risky_patterns:
            if _re.search(pat, message, _re.IGNORECASE):
                return True
    return False


async def _execute_opencode(message: str, model: Optional[str] = None, mode: Optional[str] = None, conversation_id: Optional[str] = None) -> str:
    content = ""

    # MCP 操作优先（不走 AI 路由）
    mcp_reply = await _try_handle_mcp_message(message)
    if mcp_reply:
        return mcp_reply

    # 定时任务优先判断（不受记忆判断干扰）
    schedule_like = _looks_like_schedule_message(message)
    birthday_reminder_like = _looks_like_birthday_reminder_message(message)
    # 记忆判断：已在 _looks_like_memory_message 内部排除定时任务消息
    memory_like = _looks_like_memory_message(message) or _looks_like_birthday_memory_intent(message)
    # 若已识别为定时任务，则不再路由到记忆
    if schedule_like and not birthday_reminder_like:
        memory_like = False

    wants_action = schedule_like or memory_like

    if wants_action:
        # 定时任务优先处理
        if schedule_like:
            scheduled = await _try_create_scheduled_task(message)
            if scheduled:
                if birthday_reminder_like:
                    saved = await _try_save_memory(message)
                    if saved:
                        return f"{saved}\n\n{scheduled}"
                return scheduled

        # 记忆处理（仅在非定时任务场景下）
        if memory_like:
            saved = await _try_save_memory(message)
            if saved:
                return saved

        ai_routed, opencode_available = await _try_ai_route_action(message)
        if ai_routed:
            return ai_routed

        if not opencode_available:
            if schedule_like:
                return (
                    "我识别到你想创建定时任务，但当前未能解析时间表达。\n"
                    "建议用类似格式：\n"
                    "- 今天 20:05 提醒我休息\n"
                    "- 每天 09:00 提醒我喝水"
                )
            if memory_like:
                return (
                    "我识别到你想保存记忆，但当前未能提取要记住的内容。\n"
                    "建议用类似格式：帮我记住：xxx"
                )
            return "OpenCode 正在启动或未连接，请稍后重试。"

        return "未能识别你的意图，请更明确描述要创建的定时任务或要保存的记忆。"

    memory_answer = await _try_answer_from_memory(message)
    if memory_answer:
        return memory_answer

    # 沙箱执行路由（在增强 prompt 之前判断，避免暴露内部记忆数据到沙箱）
    if _should_use_sandbox(message):
        sandbox_result = await _execute_sandbox(message)
        if sandbox_result is not None:
            return sandbox_result
        # 沙箱失败，降级继续走本地 OpenCode

    # 自动匹配并注入相关 Skills / MCP 工具上下文
    try:
        augmented = await build_augmented_prompt(message)
    except Exception as _disp_err:
        logger.warning(f"[ToolDispatcher] 增强 prompt 失败（跳过）: {_disp_err}")
        augmented = message

    prompt = await _build_opencode_prompt_with_memory(augmented, mode=mode)
    content, _ = await _execute_opencode_client(prompt, model=model, mode=mode, conversation_id=conversation_id)
    content = _sanitize_assistant_output(content or "")

    if not content:
        memory_fallback = await _try_answer_from_semantic_memory(message)
        if memory_fallback:
            return memory_fallback
        content = "OpenCode 未连接，请先启动本地 opencode server 或检查 server_url 配置"

    return content


async def _execute_opencode_with_meta(
    message: str,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    conversation_id: Optional[str] = None
) -> Tuple[str, List[dict]]:
    mcp_reply = await _try_handle_mcp_message(message)
    if mcp_reply:
        return mcp_reply, []

    schedule_like = _looks_like_schedule_message(message)
    birthday_reminder_like = _looks_like_birthday_reminder_message(message)
    memory_like = _looks_like_memory_message(message) or _looks_like_birthday_memory_intent(message)
    if schedule_like and not birthday_reminder_like:
        memory_like = False

    wants_action = schedule_like or memory_like
    if wants_action:
        if schedule_like:
            scheduled = await _try_create_scheduled_task(message)
            if scheduled:
                if birthday_reminder_like:
                    saved = await _try_save_memory(message)
                    if saved:
                        return f"{saved}\n\n{scheduled}", []
                return scheduled, []
        if memory_like:
            saved = await _try_save_memory(message)
            if saved:
                return saved, []
        ai_routed, opencode_available = await _try_ai_route_action(message)
        if ai_routed:
            return ai_routed, []
        if not opencode_available:
            if schedule_like:
                return (
                    "我识别到你想创建定时任务，但当前未能解析时间表达。\n"
                    "建议用类似格式：\n"
                    "- 今天 20:05 提醒我休息\n"
                    "- 每天 09:00 提醒我喝水",
                    []
                )
            if memory_like:
                return (
                    "我识别到你想保存记忆，但当前未能提取要记住的内容。\n"
                    "建议用类似格式：帮我记住：xxx",
                    []
                )
            return "OpenCode 正在启动或未连接，请稍后重试。", []
        return "未能识别你的意图，请更明确描述要创建的定时任务或要保存的记忆。", []

    memory_answer = await _try_answer_from_memory(message)
    if memory_answer:
        return memory_answer, []

    if _should_use_sandbox(message):
        sandbox_result = await _execute_sandbox(message)
        if sandbox_result is not None:
            return sandbox_result, []

    try:
        augmented = await build_augmented_prompt(message)
    except Exception as _disp_err:
        logger.warning(f"[ToolDispatcher] 增强 prompt 失败（跳过）: {_disp_err}")
        augmented = message

    prompt = await _build_opencode_prompt_with_memory(augmented, mode=mode)
    content, _, parts = await _execute_opencode_client_with_parts(
        prompt,
        model=model,
        mode=mode,
        conversation_id=conversation_id
    )
    content = _sanitize_assistant_output(content or "")

    if not content:
        memory_fallback = await _try_answer_from_semantic_memory(message)
        if memory_fallback:
            return memory_fallback, []
        content = "OpenCode 未连接，请先启动本地 opencode server 或检查 server_url 配置"
        return content, []

    return content, parts or []


async def _stream_execute_opencode_with_meta(
    message: str,
    model: Optional[str] = None,
    mode: Optional[str] = None,
    conversation_id: Optional[str] = None
):
    mcp_reply = await _try_handle_mcp_message(message)
    if mcp_reply:
        yield {"type": "done", "content": mcp_reply, "parts": []}
        return

    schedule_like = _looks_like_schedule_message(message)
    birthday_reminder_like = _looks_like_birthday_reminder_message(message)
    memory_like = _looks_like_memory_message(message) or _looks_like_birthday_memory_intent(message)
    if schedule_like and not birthday_reminder_like:
        memory_like = False

    wants_action = schedule_like or memory_like
    if wants_action:
        if schedule_like:
            scheduled = await _try_create_scheduled_task(message)
            if scheduled:
                if birthday_reminder_like:
                    saved = await _try_save_memory(message)
                    if saved:
                        yield {"type": "done", "content": f"{saved}\n\n{scheduled}", "parts": []}
                        return
                yield {"type": "done", "content": scheduled, "parts": []}
                return
        if memory_like:
            saved = await _try_save_memory(message)
            if saved:
                yield {"type": "done", "content": saved, "parts": []}
                return
        ai_routed, opencode_available = await _try_ai_route_action(message)
        if ai_routed:
            yield {"type": "done", "content": ai_routed, "parts": []}
            return
        if not opencode_available:
            if schedule_like:
                yield {
                    "type": "done",
                    "content": "我识别到你想创建定时任务，但当前未能解析时间表达。\n建议用类似格式：\n- 今天 20:05 提醒我休息\n- 每天 09:00 提醒我喝水",
                    "parts": []
                }
                return
            if memory_like:
                yield {
                    "type": "done",
                    "content": "我识别到你想保存记忆，但当前未能提取要记住的内容。\n建议用类似格式：帮我记住：xxx",
                    "parts": []
                }
                return
            yield {"type": "done", "content": "OpenCode 正在启动或未连接，请稍后重试。", "parts": []}
            return
        yield {"type": "done", "content": "未能识别你的意图，请更明确描述要创建的定时任务或要保存的记忆。", "parts": []}
        return

    memory_answer = await _try_answer_from_memory(message)
    if memory_answer:
        yield {"type": "done", "content": memory_answer, "parts": []}
        return

    if _should_use_sandbox(message):
        sandbox_result = await _execute_sandbox(message)
        if sandbox_result is not None:
            yield {"type": "done", "content": sandbox_result, "parts": []}
            return

    try:
        augmented = await build_augmented_prompt(message)
    except Exception as _disp_err:
        logger.warning(f"[ToolDispatcher] 增强 prompt 失败（跳过）: {_disp_err}")
        augmented = message

    prompt = await _build_opencode_prompt_with_memory(augmented, mode=mode)

    client = opencode_ws
    created_client = False
    try:
        if client is None:
            client = OpenCodeClient(app_config.opencode.server_url)
            created_client = True
        ok = await client.try_connect(attempts=3, delay=0.4, open_timeout=1.0)
        if not ok:
            memory_fallback = await _try_answer_from_semantic_memory(message)
            if memory_fallback:
                yield {"type": "done", "content": memory_fallback, "parts": []}
                return
            yield {"type": "done", "content": "OpenCode 未连接，请先启动本地 opencode server 或检查 server_url 配置", "parts": []}
            return
        async for event in client.execute_task_stream(
            prompt=prompt,
            model=model,
            mode=mode,
            conversation_id=conversation_id
        ):
            yield event
    finally:
        if created_client and client and getattr(client, "connected", False):
            try:
                await client.disconnect()
            except Exception:
                pass


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    candidate = match.group(0)
    try:
        return json.loads(candidate)
    except Exception:
        return None


async def _try_ai_route_action(message: str) -> Tuple[Optional[str], bool]:
    opencode_available = False
    if not message or not message.strip():
        return None, opencode_available

    memory_like = _looks_like_memory_message(message)
    schedule_like = _looks_like_schedule_message(message)
    birthday_reminder_like = _looks_like_birthday_reminder_message(message)
    # 定时任务优先：已识别为定时任务则不再判断记忆
    if schedule_like and not birthday_reminder_like:
        memory_like = False
    allowed_actions = []
    if schedule_like:
        allowed_actions.append("create_scheduled_task")
    if memory_like:
        allowed_actions.append("save_memory")
    if not allowed_actions:
        return None, opencode_available

    prompt = (
        "你是意图识别与结构化指令提取器，用于驱动一个本地个人助理。\n"
        "任务：从用户输入中提取结构化指令。\n"
        f"本次允许的 action 只有：{ '|'.join(allowed_actions) }。\n"
        "只输出 JSON（不要输出任何解释、不要输出 Markdown、不要输出代码块）。\n"
        "输出格式：\n"
        "{"
        "\"action\":\"create_scheduled_task\"|\"save_memory\","
        "\"confidence\":0.0,"
        "\"task\":{"
        "\"name\":\"\","
        "\"cron\":\"\","
        "\"cron_prompt\":\"\","
        "\"task_prompt\":\"\","
        "\"run_once\":false,"
        "\"notify_channels\":[\"app\"]"
        "},"
        "\"memory\":{"
        "\"content\":\"\","
        "\"category\":\"note\""
        "},"
        "\"reply\":\"\""
        "}\n"
        "要求：\n"
        "1) 当允许 action 里包含 create_scheduled_task 且用户表达“到某个时间/周期要做某事”（例如提醒/生成/写作/整理/保存）并包含明确时间（例如 HH:MM 或 X点），必须选择 action=create_scheduled_task。\n"
        "2) action=create_scheduled_task 时，task.task_prompt 必须是未来执行时要做的事（例如“提醒用户：xxx”），不要在 reply 中输出任何自然语言回复。\n"
        "3) 如果用户表达“今天/明天/后天/只提醒一次/仅一次”，则 run_once=true。\n"
        "4) task.cron 是标准 5 段 cron（分 时 日 月 周），能给则给；不确定就把时间表达写入 task.cron_prompt。\n"
        "5) action=save_memory 时，将要记住的核心内容放入 memory.content，不要生成多余回复。\n"
        "6) 只允许输出 JSON，对 JSON 以外的任何字符都视为失败。\n"
        f"用户输入：{message}"
    )

    result_content, opencode_available = await _execute_opencode_client(prompt)
    if not result_content:
        return None, opencode_available
    data = _extract_json_object(result_content)
    if not isinstance(data, dict):
        repair_prompt = (
            "你的上一条输出不是合法 JSON。"
            "请仅输出一个 JSON 对象，必须以 { 开头、以 } 结尾，且不包含任何额外字符。\n"
            f"允许的 action 只有：{ '|'.join(allowed_actions) }。\n"
            "JSON 格式同上一次要求。\n"
            f"用户输入：{message}\n"
            f"你的上一条输出：{result_content}"
        )
        repaired_content, opencode_available = await _execute_opencode_client(repair_prompt)
        if not repaired_content:
            return None, opencode_available
        data = _extract_json_object(repaired_content)
        if not isinstance(data, dict):
            return None, opencode_available

    action = str(data.get("action") or "").strip()
    confidence = data.get("confidence")
    try:
        confidence_value = float(confidence)
    except Exception:
        confidence_value = 0.0

    if confidence_value < 0.35 and len(allowed_actions) > 1:
        return None, opencode_available

    if action not in allowed_actions:
        return None, opencode_available

    if action == "create_scheduled_task":
        if not scheduler_router.scheduler:
            return None, opencode_available
        task_data = data.get("task") if isinstance(data.get("task"), dict) else {}
        name = str(task_data.get("name") or "").strip()
        cron_expression = str(task_data.get("cron") or "").strip()
        cron_prompt = str(task_data.get("cron_prompt") or "").strip()
        task_prompt = str(task_data.get("task_prompt") or "").strip()
        notify_channels = task_data.get("notify_channels")
        if not isinstance(notify_channels, list) or not notify_channels:
            notify_channels = ["app"]
        run_once = (
            bool(task_data.get("run_once"))
            or any(key in (message or "") for key in ["今天", "明天", "后天", "一次", "只提醒一次", "仅提醒一次", "提醒一次"])
            or bool(re.search(r"\d+\s*(分钟|小时|天)\s*(后|之后|以后)", message or ""))
            or bool(re.search(r"(半小时|一小时|一天)\s*(后|之后|以后)", message or ""))
        )

        if not task_prompt:
            content = _extract_reminder_content(message)
            task_prompt = f"请提醒用户：{content}" if content else message

        if run_once:
            task_prompt = f"__RUN_ONCE__\n{task_prompt}"

        if not cron_expression:
            cron_source = cron_prompt or message
            cron_payload = await scheduler_router.ai_generate_cron(prompt=cron_source)
            cron_data = cron_payload.get("data") if isinstance(cron_payload, dict) else None
            cron_expression = cron_data.get("cron") if cron_data else None
        if not cron_expression:
            return None, opencode_available

        if not name:
            content = _extract_reminder_content(message)
            name = f"{'一次性' if run_once else ''}提醒：{content}" if content else ("一次性定时提醒" if run_once else "定时提醒")
        if len(name) > 30:
            name = f"{name[:30]}..."

        task = scheduler_router.scheduler.create_task(
            name=name,
            cron_expression=cron_expression,
            task_prompt=task_prompt,
            notify_channels=notify_channels
        )
        next_run = task.next_run.isoformat() if task.next_run else "待计算"
        return f"已创建定时任务：{task.name}\nCron：{task.cron_expression}\n下次运行：{next_run}\n可在“定时任务”查看和管理。", opencode_available

    if action == "save_memory":
        mem = data.get("memory") if isinstance(data.get("memory"), dict) else {}
        content = str(mem.get("content") or "").strip()
        category = str(mem.get("category") or "note").strip() or "note"
        if not content:
            return None, opencode_available
        await _save_memory_content(content=content, category=category, raw_message=message)
        return f"好的，我已经记住了：{content}\n可在“记忆”页面查看。", opencode_available

    return None, opencode_available


def _looks_like_birthday_reminder_message(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if "生日" not in text:
        return False
    if not _extract_birthday_value(text):
        return False
    reminder_keys = ["提醒", "闹钟", "提示我", "叫我", "通知我", "别忘了", "记得"]
    return any(key in text for key in reminder_keys)


def _extract_time_for_reminder(message: str, default_hour: int = 9, default_minute: int = 0) -> Tuple[int, int]:
    text = (message or "").strip()
    if not text:
        return default_hour, default_minute
    time_match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", text)
    if time_match:
        hour = max(0, min(23, int(time_match.group(1))))
        minute = max(0, min(59, int(time_match.group(2))))
        return hour, minute
    half_match = re.search(r"(\d{1,2})\s*点\s*半", text)
    if half_match:
        hour = int(half_match.group(1))
        if ("下午" in text or "晚上" in text) and hour < 12:
            hour += 12
        if "凌晨" in text and hour == 12:
            hour = 0
        return max(0, min(23, hour)), 30
    dot_match = re.search(r"(\d{1,2})\s*点(?:\s*(\d{1,2}))?\s*分?", text)
    if dot_match:
        hour = int(dot_match.group(1))
        minute = int(dot_match.group(2)) if dot_match.group(2) else 0
        if ("下午" in text or "晚上" in text) and hour < 12:
            hour += 12
        if "凌晨" in text and hour == 12:
            hour = 0
        minute = max(0, min(59, minute))
        return max(0, min(23, hour)), minute
    return default_hour, default_minute


def _looks_like_schedule_message(message: str) -> bool:
    if not message:
        return False
    triggers = ["提醒", "定时", "闹钟", "日程", "定时任务", "通知", "叫我", "提示我"]
    time_hints = [
        "每天", "每周", "每月", "每年", "每小时", "每分钟",
        "早上", "上午", "中午", "下午", "晚上", "凌晨",
        "周一", "周二", "周三", "周四", "周五", "周六", "周日",
        "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日",
        "工作日", "周末"
    ]
    task_verbs = ["写", "生成", "总结", "整理", "保存", "备份", "发送", "推送", "提醒", "通知", "检查", "同步", "下载", "导出"]
    has_time_hint = bool(
        any(item in message for item in time_hints)
        or re.search(r"\d{1,2}\s*点|\d{1,2}\s*[:：]\s*\d{2}", message)
        or re.search(r"\d+\s*(分钟|小时|天|周|个月|年)\s*(后|之后|以后)", message)
        or re.search(r"(半小时|一小时|一天|一周|一个月)\s*(后|之后|以后)", message)
    )
    has_date_hint = bool(
        re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*(日|号)?", message)
        or re.search(r"(?<!\d)\d{1,2}\s*[\/\-.]\s*\d{1,2}(?!\d)", message)
    )
    if not has_time_hint and not has_date_hint:
        return False
    if _looks_like_birthday_reminder_message(message):
        return True
    # 含"保存到X盘"是明确的文件任务，有时间就是定时任务
    if re.search(r"保存到\s*[a-zA-Z]\s*盘", message):
        return True
    has_trigger = any(item in message for item in triggers)
    has_repeat_hint = any(item in message for item in time_hints)
    has_task_verb = any(item in message for item in task_verbs)
    return has_trigger or has_repeat_hint or has_task_verb


def _extract_reminder_content(message: str) -> str:
    text = message.strip()
    match = re.search(r"(提醒|记得|别忘了|闹钟|定时)\s*(我|我在|我去|我把|一下|一下子)?(.*)", text)
    if match and match.group(3).strip():
        content = match.group(3).strip()
    else:
        content = text
    content = re.sub(
        r"(每天|每周|每月|每年|每小时|每分钟|早上|上午|中午|下午|晚上|凌晨|今天|明天|后天|周一|周二|周三|周四|周五|周六|周日|星期一|星期二|星期三|星期四|星期五|星期六|星期日|工作日|周末)",
        " ",
        content
    )
    # 清理相对时间（10分钟后、2小时后、3天后、半小时后 等）
    content = re.sub(r"\d+\s*(分钟|小时|天|周|个月|年)\s*(后|之后|以后)", " ", content)
    content = re.sub(r"(半小时|一小时|一天|一周|一个月)\s*(后|之后|以后)", " ", content)
    content = re.sub(r"\d{1,2}\s*点半|\d{1,2}\s*点\s*\d{0,2}\s*分?|\d{1,2}\s*[:：]\s*\d{2}", " ", content)
    content = re.sub(r"[，。,.!！?？;；:：]", " ", content)
    content = " ".join(content.split())
    return content or text


def _extract_task_content(message: str) -> str:
    """从用户消息中提取纯任务内容，去掉时间相关的描述部分。

    例如：
      "5 分钟后，写首春天的诗，保存到 D:\\temp" → "写首春天的诗，保存到 D:\\temp"
      "每天早上 9 点总结今日新闻"              → "总结今日新闻"
      "明天上午10点写周报"                    → "写周报"
    """
    text = message.strip()

    # 1. 去掉句首的相对时间前缀（如"5分钟后，"、"半小时后 "）
    text = re.sub(
        r"^((\d+\s*(分钟|小时|天|周|个月|年)|半小时|一小时|一天|一周|一个月)\s*(后|之后|以后)[，,\s]*)+",
        "",
        text
    )

    # 2. 反复去除句首的时间词（每次去一个单元，循环直到不再变化）
    # 注意：更具体/更长的模式排在前面，避免被短模式抢先匹配
    _TIME_PREFIX_PATTERN = (
        r"^(?:"
        r"每月\d{1,2}[号日]?|"          # 每月X号
        r"每周[一二三四五六七日]|"        # 每周一/每周二...
        r"每[天周月年小时分钟]|"          # 每天/每周/每月等
        r"今天|明天|后天|工作日|周末|"
        r"周[一二三四五六七日]|星期[一二三四五六七日]|"
        r"早上|上午|中午|下午|晚上|凌晨|"
        r"\d{1,2}\s*[:：]\s*\d{2}|"     # HH:MM
        r"\d{1,2}\s*点半|"
        r"\d{1,2}\s*点\s*\d{1,2}\s*分|" # X点Y分
        r"\d{1,2}\s*(?:点钟?)|"          # X点/X点钟
        r"\d{1,2}\s*[号日](?!\d)"        # X号/X日
        r")\s*[，,\s]*"
    )
    prev = None
    while prev != text:
        prev = text
        text = re.sub(_TIME_PREFIX_PATTERN, "", text, count=1)
        text = text.lstrip("，,、 ")

    # 3. 去掉内嵌的相对时间表达（如任务中夹着的"X分钟后"）
    text = re.sub(r"\d+\s*(分钟|小时|天)\s*(后|之后|以后)", "", text)
    text = re.sub(r"(半小时|一小时|一天)\s*(后|之后|以后)", "", text)

    # 4. 清理多余空白和前导标点
    text = re.sub(r"^[，,、\s]+", "", text)
    text = " ".join(text.split())

    return text if text else message.strip()


async def _try_create_scheduled_task(message: str) -> Optional[str]:
    if not _looks_like_schedule_message(message):
        return None
    if not scheduler_router.scheduler:
        return None
    try:
        if _looks_like_birthday_reminder_message(message):
            birthday = _extract_birthday_value(message or "")
            if birthday:
                md = re.search(r"(\d{1,2})月(\d{1,2})日", birthday)
                if md:
                    month = int(md.group(1))
                    day = int(md.group(2))
                    hour, minute = _extract_time_for_reminder(message)
                    subject = _extract_birthday_subject(message) or "我"
                    if subject == "我":
                        remind_text = "今天是你的生日，生日快乐！"
                        name = f"生日提醒：每年{month}月{day}日"
                    else:
                        remind_text = f"今天是{subject}的生日，记得送上祝福。"
                        name = f"生日提醒：{subject}每年{month}月{day}日"
                    task_prompt = f"__REMINDER__\n{remind_text}"
                    cron_expression = f"{minute} {hour} {day} {month} *"
                    task = scheduler_router.scheduler.create_task(
                        name=name,
                        cron_expression=cron_expression,
                        task_prompt=task_prompt,
                        notify_channels=["app"]
                    )
                    next_run = task.next_run.isoformat() if task.next_run else "待计算"
                    return (
                        f"已创建定时任务：{task.name}\n"
                        f"Cron：{task.cron_expression}\n"
                        f"下次运行：{next_run}\n"
                        f"可在“定时任务”查看和管理。"
                    )
        cron_payload = await scheduler_router.ai_generate_cron(prompt=message)
        cron_data = cron_payload.get("data") if isinstance(cron_payload, dict) else None
        cron_expression = cron_data.get("cron") if cron_data else None
        if not cron_expression:
            return None
        run_once = (
            any(key in (message or "") for key in ["今天", "明天", "后天", "一次", "只提醒一次", "仅提醒一次", "提醒一次"])
            or bool(re.search(r"\d+\s*(分钟|小时|天)\s*(后|之后|以后)", message or ""))
            or bool(re.search(r"(半小时|一小时|一天)\s*(后|之后|以后)", message or ""))
        )
        is_reminder = any(key in (message or "") for key in ["提醒", "闹钟", "提示我", "叫我", "通知我", "别忘了", "记得"])
        if is_reminder:
            content = _extract_reminder_content(message)
            name = f"{'一次性' if run_once else ''}提醒：{content}" if content else ("一次性定时提醒" if run_once else "定时提醒")
            task_payload = f"提醒：{content}" if content else message.strip()
            task_prompt = f"__REMINDER__\n{task_payload}"
        else:
            # 提取纯任务内容（去掉时间相关描述，只保留真正要执行的任务）
            task_content = _extract_task_content(message)
            # 用于任务名称展示的简短版本（进一步去除标点和空白）
            action_text = re.sub(r"[，。,.!！?？;；:：]", " ", task_content)
            action_text = " ".join(action_text.split())
            if not action_text:
                action_text = message.strip()
            name = f"{'一次性' if run_once else ''}任务：{action_text}"
            # task_prompt 使用提取后的纯任务内容，而非原始消息
            # 这样执行时不会将"5分钟后"等时间描述混入任务指令
            task_prompt = task_content
            drive_match = re.search(r"保存到\s*([a-zA-Z])\s*盘", message)
            if drive_match:
                drive = drive_match.group(1).upper()
                output_dir = f"{drive}:\\codebot_tasks"
                task_prompt = (
                    f"{task_content}\n"
                    f"请将产出保存为 Markdown 文件到 {output_dir} 目录（如不存在请创建），"
                    f"文件名包含日期时间（例如 20260301_0800.md），并在完成后输出保存路径。"
                )

        if len(name) > 30:
            name = f"{name[:30]}..."
        if run_once:
            task_prompt = f"__RUN_ONCE__\n{task_prompt}"
        task = scheduler_router.scheduler.create_task(
            name=name,
            cron_expression=cron_expression,
            task_prompt=task_prompt,
            notify_channels=["app"]
        )
        next_run = task.next_run.isoformat() if task.next_run else "待计算"
        return f"已创建定时任务：{task.name}\nCron：{task.cron_expression}\n下次运行：{next_run}\n可在“定时任务”查看和管理。"
    except Exception as e:
        logger.error(f"创建定时任务失败: {e}")
        return None


def _get_chat_memory_manager() -> MemoryManager:
    global chat_memory_manager
    if chat_memory_manager is None:
        chat_memory_manager = MemoryManager()
    return chat_memory_manager


async def _save_memory_content(content: str, category: str, raw_message: str) -> bool:
    manager = _get_chat_memory_manager()
    metadata = {"source": "chat", "raw": raw_message}
    try:
        await manager.save_long_term_memory(content=content, category=category, metadata=metadata)
        return True
    except Exception as e:
        try:
            cursor = manager.sqlite_db.cursor()
            cursor.execute(
                "SELECT id FROM long_term_memories WHERE category = ? AND content = ? ORDER BY id DESC LIMIT 1",
                (category, content)
            )
            exists = cursor.fetchone()
            if not exists:
                cursor.execute(
                    "INSERT INTO long_term_memories (category, content, metadata) VALUES (?, ?, ?)",
                    (category, content, json.dumps(metadata, ensure_ascii=False))
                )
                manager.sqlite_db.commit()
            return True
        except Exception as inner:
            logger.error(f"保存记忆失败: {inner}")
            return False
        finally:
            logger.error(f"记忆向量索引失败: {e}")


def _looks_like_memory_message(message: str) -> bool:
    if not message:
        return False
    text = message.strip()
    # 如果消息明显是定时任务（含时间+任务动词），不识别为记忆
    if _looks_like_schedule_message(text):
        return False
    triggers = [
        "帮我记住", "请记住", "记住", "记一下", "记下",
        "写入记忆", "保存记忆", "保存到记忆", "存为记忆", "存进记忆",
        "更新记忆", "修改记忆", "更正记忆"
    ]
    # "保存到X盘" 是文件保存，不是记忆
    if re.search(r"保存到\s*[a-zA-Z]\s*盘", text):
        return False
    return any(trigger in text for trigger in triggers)


def _looks_like_birthday_memory_intent(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if "生日" not in text:
        return False
    value = _extract_birthday_value(text)
    if not value:
        return False
    if any(key in text for key in ["记住", "保存", "写入记忆", "保存记忆", "更新记忆", "修改记忆", "更正记忆"]):
        return True
    if _looks_like_memory_message(text):
        return True
    if re.search(r"^(我的生日|我生日)\s*(是|为|改为|改成)", text):
        return True
    if any(key in text for key in ["更新", "修改", "更正", "改为", "改成"]):
        return True
    if any(key in text for key in ["吗", "？", "?", "几号", "哪天", "什么时候"]):
        return False
    if re.search(r"(我的|我|姐姐|哥哥|妈妈|爸爸|父母|老婆|妻子|老公|女儿|儿子|朋友|同事|领导|老师|老板).{0,8}(生日)", text) and ("是" in text or "为" in text):
        return True
    match = re.search(r"(.{1,12})的生日\s*(是|为)", text)
    if match:
        subject = (match.group(1) or "").strip()
        if subject and subject not in ["什么", "谁", "哪天", "几号"]:
            return True
    return False


def _extract_birthday_subject(message: str) -> Optional[str]:
    text = (message or "").strip()
    if not text:
        return None
    if re.search(r"(我的生日|我生日)", text):
        return "我"
    match = re.search(r"是\s*([^，。,.!！?？;；:：\s]{1,12})\s*的生日", text)
    if match:
        subject = (match.group(1) or "").strip()
        if subject and (not re.search(r"[\d月日号]", subject)):
            return subject
    match = re.search(r"([^，。,.!！?？;；:：\s]{1,12})的生日", text)
    if match:
        subject = (match.group(1) or "").strip()
        if subject and (not re.search(r"[\d月日号]", subject)):
            return subject
    for candidate in ["姐姐", "哥哥", "妈妈", "爸爸", "父母", "老婆", "妻子", "老公", "女儿", "儿子"]:
        if candidate in text and "生日" in text:
            return candidate
    return None


def _extract_birthday_value(message: str) -> Optional[str]:
    text = (message or "").strip()
    if not text:
        return None

    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*(日|号)?", text)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month}月{day}日"

    match = re.search(r"(?<!\d)(\d{1,2})\s*[\/\-.]\s*(\d{1,2})(?!\d)", text)
    if match:
        month = int(match.group(1))
        day = int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month}月{day}日"

    return None


def _is_birthday_question(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if "生日" in text:
        return True
    if any(key in text for key in ["出生日期", "生日是哪天", "生日是什么时候", "几号生日"]):
        return True
    return False


async def _try_answer_from_memory(message: str) -> Optional[str]:
    if not _is_birthday_question(message):
        return None
    manager = _get_chat_memory_manager()
    subject = _extract_birthday_subject(message) or "我"
    key = "birthday" if subject == "我" else f"{subject}_birthday"
    try:
        fact = await manager.get_fact(key)
        if fact and fact.get("value"):
            if subject == "我":
                return f"你的生日是 {fact['value']}。"
            return f"{subject}的生日是 {fact['value']}。"
    except Exception:
        pass

    try:
        keyed = await manager.get_keyed_long_term_memory(key, category="profile", include_archived=False)
        if keyed and keyed.get("content"):
            extracted = _extract_birthday_value(str(keyed["content"]))
            if extracted:
                if subject == "我":
                    return f"你的生日是 {extracted}。"
                return f"{subject}的生日是 {extracted}。"
    except Exception:
        pass
    return None


async def _try_answer_from_semantic_memory(message: str) -> Optional[str]:
    manager = _get_chat_memory_manager()
    try:
        facts = await manager.search_facts(message, top_k=3, include_archived=False)
        if facts:
            best = facts[0]
            content = str(best.get("content") or "").strip()
            if content:
                return f"我记得：{content}"
    except Exception:
        pass

    try:
        memories = await manager.search_memories(message, top_k=3, include_archived=False)
        if memories:
            best = memories[0]
            content = str(best.get("content") or "").strip()
            if content:
                return f"我记得：{content}"
    except Exception:
        pass
    return None


def _build_autonomous_execution_policy(mode: Optional[str] = None) -> List[str]:
    lines = [
        "以下为内部执行约束：请自主决策并持续执行，不要向用户索要流程决策；失败时自动切换替代方案。",
        "优先复用已安装技能与已启用MCP；需要时先检索可用技能，缺失则生成并保存可复用技能定义；MCP优先检查魔搭可用源，缺失再使用网络检索方案。",
        "严禁在最终回答中复述本段或输出 internal_context、技能参考、MCP 工具等内部标签。",
    ]
    if mode == "plan":
        lines.append("当前是规划模式，直接给出可执行计划，不要让用户做流程选择。")
    return lines


async def _build_opencode_prompt_with_memory(message: str, mode: Optional[str] = None) -> str:
    manager = _get_chat_memory_manager()
    facts_context: List[str] = []
    memories_context: List[str] = []
    habit_context: List[str] = []
    preference_context: List[str] = []
    profile_context: List[str] = []

    # ── 事实记忆（结构化 key-value）──────────────────────────────────────────
    try:
        facts = await manager.search_facts(message, top_k=5, include_archived=False)
        for item in facts:
            content = str(item.get("content") or "").strip()
            if content and content not in facts_context:
                facts_context.append(content)
    except Exception:
        pass

    # ── 向量语义记忆（跨所有分类）────────────────────────────────────────────
    try:
        memories = await manager.search_memories(message, top_k=5, include_archived=False)
        for item in memories:
            content = str(item.get("content") or "").strip()
            cat = str(item.get("category") or "")
            if not content:
                continue
            if cat == "habit" and content not in habit_context:
                habit_context.append(content)
            elif cat == "preference" and content not in preference_context:
                preference_context.append(content)
            elif cat == "profile" and content not in profile_context:
                profile_context.append(content)
            elif content not in memories_context:
                memories_context.append(content)
    except Exception:
        pass

    # ── 专项分类检索（补充语义检索未命中的内容）──────────────────────────────
    for cat, target in [
        ("habit", habit_context),
        ("preference", preference_context),
        ("profile", profile_context),
    ]:
        if len(target) < 3:
            try:
                extra = await manager.search_memories(
                    message, top_k=3, category=cat, include_archived=False
                )
                for item in extra:
                    content = str(item.get("content") or "").strip()
                    if content and content not in target:
                        target.append(content)
            except Exception:
                pass

    policy_lines: List[str] = _build_autonomous_execution_policy(mode=mode)
    lines: List[str] = []
    has_any = any([facts_context, habit_context, preference_context, profile_context, memories_context])
    if facts_context:
        lines.append("【用户事实记忆（可信，优先使用；如有冲突以最新为准）】")
        for item in facts_context[:5]:
            lines.append(f"- {item}")
    if profile_context:
        lines.append("【用户个人信息】")
        for item in profile_context[:3]:
            lines.append(f"- {item}")
    if preference_context:
        lines.append("【用户偏好】")
        for item in preference_context[:3]:
            lines.append(f"- {item}")
    if habit_context:
        lines.append("【用户习惯】")
        for item in habit_context[:3]:
            lines.append(f"- {item}")
    if memories_context:
        lines.append("【用户长期记忆（可信，尽量参考）】")
        for item in memories_context[:5]:
            lines.append(f"- {item}")
    lines.append(f"【用户输入】{message}")
    if has_any:
        lines.append("请基于以上记忆回答用户；若记忆与用户输入冲突，以用户最新输入为准。")
    return "\n".join([
        "<system_policy>",
        " ".join(policy_lines),
        "</system_policy>",
        "<conversation_context>",
        "\n".join(lines),
        "</conversation_context>",
        "请只输出给用户的最终结果，不要输出 system_policy 或 conversation_context 标签及其原文。"
    ])

def _extract_memory_content(message: str) -> str:
    text = (message or "").strip()
    if not text:
        return ""

    quoted_match = re.search(r"[“\"'‘](.+?)[”\"'’]", text)
    if quoted_match and quoted_match.group(1).strip():
        candidate = quoted_match.group(1).strip()
    else:
        match = re.search(
            r"(帮我|请|麻烦)?(记住|保存|记一下|记下|存一下|存下)\s*(一下|下)?\s*[:：]?\s*(.*)",
            text
        )
        candidate = (match.group(4) if match else text).strip()

    candidate = re.sub(r"(这个|该)?(地址|位置|地点|信息|内容)\s*$", "", candidate).strip()
    candidate = re.sub(r"[，。,.!！?？;；]\s*$", "", candidate).strip()
    return candidate


def _guess_memory_category(message: str, content: str) -> str:
    text = f"{message} {content}".strip()
    if any(key in text for key in ["生日", "个人信息", "姓名", "名字", "年龄", "职业", "工作", "学校", "身份", "身份证", "账号", "账户", "密码", "口令"]):
        return "profile"
    if any(key in text for key in ["地址", "住址", "位置", "地点"]):
        return "address"
    if any(key in text for key in ["电话", "手机号", "联系方式", "号码", "微信", "邮箱"]):
        return "contact"
    return "note"


async def _try_save_memory(message: str) -> Optional[str]:
    manager = _get_chat_memory_manager()
    birthday_value = _extract_birthday_value(message)
    if birthday_value and _looks_like_birthday_memory_intent(message):
        subject = _extract_birthday_subject(message) or "我"
        key = "birthday" if subject == "我" else f"{subject}_birthday"
        metadata = {"source": "chat", "raw": message, "memory_key": key, "subject": subject}
        try:
            await manager.upsert_fact(key=key, value=birthday_value, metadata=metadata)
        except Exception:
            pass
        try:
            await manager.upsert_keyed_long_term_memory(
                memory_key=key,
                content=f"{'生日' if subject == '我' else subject + '的生日'}：{birthday_value}",
                category="profile",
                metadata=metadata
            )
        except Exception:
            return "记忆保存失败，请稍后重试"
        if subject == "我":
            return f"我已记录你的生日是 {birthday_value}。"
        return f"我已记录{subject}的生日是 {birthday_value}。"

    if not _looks_like_memory_message(message):
        return None

    content = _extract_memory_content(message)
    if not content:
        return None

    category = _guess_memory_category(message, content)
    metadata = {"source": "chat", "raw": message}

    try:
        await manager.save_long_term_memory(content=content, category=category, metadata=metadata)
    except Exception as e:
        try:
            cursor = manager.sqlite_db.cursor()
            cursor.execute(
                "SELECT id FROM long_term_memories WHERE category = ? AND content = ? ORDER BY id DESC LIMIT 1",
                (category, content)
            )
            exists = cursor.fetchone()
            if not exists:
                cursor.execute(
                    "INSERT INTO long_term_memories (category, content, metadata) VALUES (?, ?, ?)",
                    (category, content, json.dumps(metadata, ensure_ascii=False))
                )
                manager.sqlite_db.commit()
        except Exception as inner:
            logger.error(f"保存记忆失败: {inner}")
            return "记忆保存失败，请稍后重试"
        logger.error(f"记忆向量索引失败: {e}")

    return f"好的，我已经记住了：{content}\n可在“记忆”页面查看。"


def _build_skill_from_conversation(messages: List[dict], request: SkillGenerateRequest) -> dict:
    user_messages = [item.get("content") for item in messages if item.get("role") == "user"]
    last_user_message = ""
    for item in reversed(user_messages):
        if item and str(item).strip():
            last_user_message = str(item).strip()
            break
    fallback_name = generate_conversation_title(last_user_message or "对话技能")
    description = (request.description or last_user_message).strip()
    if len(description) > 200:
        description = f"{description[:200]}..."
    return {
        "name": (request.name or fallback_name or "未命名技能").strip(),
        "description": description,
        "version": request.version or "1.0.0",
        "source": request.source or "chat",
        "enabled": bool(request.enabled)
    }


def _should_materialize_skill(user_message: str, assistant_response: str) -> bool:
    user_text = (user_message or "").strip()
    answer_text = _sanitize_assistant_output(assistant_response or "")
    if _skill_content_is_noise(answer_text):
        return False
    if not user_text or len(user_text) < 8:
        return False
    if re.fullmatch(r"(你好|您好|在吗|hi|hello|hey)[!！。,. ]*", user_text, flags=re.IGNORECASE):
        return False
    if len(answer_text) < 220:
        return False
    if len(re.findall(r"(?:\n\s*[-*]|\n\s*\d+[.)、])", answer_text)) < 2:
        return False
    trigger_words = [
        "步骤", "流程", "脚本", "命令", "自动化", "排查", "修复", "部署", "配置", "方案", "实现", "改造",
        "workflow", "pipeline", "script", "troubleshoot", "deploy", "automation", "refactor", "migration"
    ]
    hit = sum(1 for w in trigger_words if w.lower() in f"{user_text}\n{answer_text}".lower())
    return hit >= 3


def _materialize_reusable_skill(user_message: str, assistant_response: str) -> None:
    cleaned_response = _sanitize_assistant_output(assistant_response or "")
    if not _should_materialize_skill(user_message, cleaned_response):
        return
    digest = hashlib.sha1(f"{user_message}\n{cleaned_response[:300]}".encode("utf-8")).hexdigest()[:16]
    skill_id = f"auto_{digest}"
    path = settings.SKILLS_DIR / f"{skill_id}.json"
    if path.exists():
        return
    title = generate_conversation_title(user_message or "自动技能")
    desc = cleaned_response.strip().replace("\n", " ")
    if _skill_content_is_noise(desc):
        return
    if len(desc) > 180:
        desc = f"{desc[:180]}..."
    data = {
        "id": skill_id,
        "name": f"自动技能-{title}",
        "description": desc,
        "version": "1.0.0",
        "source": "auto",
        "enabled": True,
        "installed_at": datetime.now().isoformat(),
    }
    settings.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _write_skill(skill_id: str, data: dict):
    settings.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    path = settings.SKILLS_DIR / f"{skill_id}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@router.post("/conversations", response_model=MessageResponse)
async def create_conversation(title: str = Body("新对话", embed=True)):
    """创建对话"""
    try:
        # 初始化数据库连接
        conversations_db.connect()
        
        memory_manager = MemoryManager()
        conversation_id = await memory_manager.create_conversation(title)
        
        return MessageResponse(
            success=True,
            data={"id": conversation_id, "title": title},
            message="对话创建成功"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations")
async def list_conversations(
    limit: int = 50,
    offset: int = 0,
    archived: bool = False
):
    """获取对话列表"""
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        
        conversations = await memory_manager.get_conversations(
            limit=limit,
            offset=offset,
            archived=archived
        )
        
        return {
            "success": True,
            "data": {
                "items": conversations,
                "total": len(conversations)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: int):
    """获取对话详情"""
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        
        conversation = await memory_manager.get_conversation(conversation_id)
        
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")
        
        return {
            "success": True,
            "data": conversation
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int):
    """删除对话"""
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        
        await memory_manager.delete_conversation(conversation_id)
        
        return {
            "success": True,
            "message": "对话已删除"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/conversations/{conversation_id}/title")
async def update_conversation_title(conversation_id: int, request: UpdateTitleRequest):
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        await memory_manager.update_conversation_title(conversation_id, request.title)
        return {
            "success": True,
            "message": "标题已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversations/{conversation_id}/pin")
async def toggle_conversation_pinned(conversation_id: int, request: TogglePinnedRequest):
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        await memory_manager.set_conversation_pinned(conversation_id, request.pinned)
        return {
            "success": True,
            "message": "置顶状态已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversations/{conversation_id}/archive")
async def toggle_conversation_archived(conversation_id: int, request: ToggleArchiveRequest):
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        await memory_manager.set_conversation_archived(conversation_id, request.archived)
        return {
            "success": True,
            "message": "归档状态已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversations/{conversation_id}/group")
async def toggle_conversation_group(conversation_id: int, request: ToggleGroupRequest):
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        await memory_manager.set_conversation_group(conversation_id, request.is_group)
        return {
            "success": True,
            "message": "群聊状态已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversations/{conversation_id}/share")
async def share_conversation(conversation_id: int):
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        conversation = await memory_manager.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="对话不存在")
        share_id = conversation.get("share_id") or uuid4().hex
        await memory_manager.set_conversation_share_id(conversation_id, share_id)
        return {
            "success": True,
            "data": {
                "share_id": share_id,
                "share_path": f"/share/{share_id}"
            },
            "message": "分享链接已生成"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    request: MessageRequest
):
    """发送消息"""
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        
        # 保存用户消息
        await memory_manager.save_message(
            conversation_id=conversation_id,
            role="user",
            content=request.content
        )
        
        return {
            "success": True,
            "message": "消息已发送"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: int,
    limit: int = 100
):
    """获取消息历史"""
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        
        messages = await memory_manager.get_messages(
            conversation_id=conversation_id,
            limit=limit
        )
        
        return {
            "success": True,
            "data": {
                "items": messages,
                "total": len(messages)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/conversations/{conversation_id}/skills")
async def generate_skill_from_conversation(
    conversation_id: int,
    request: SkillGenerateRequest
):
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        messages = await memory_manager.get_messages(
            conversation_id=conversation_id,
            limit=request.message_limit
        )
        if not messages:
            raise HTTPException(status_code=404, detail="对话不存在或无消息")

        skill_id = uuid4().hex
        generated = _build_skill_from_conversation(messages, request)
        skill = {
            "id": skill_id,
            "name": generated.get("name"),
            "description": generated.get("description"),
            "version": generated.get("version"),
            "source": generated.get("source"),
            "enabled": generated.get("enabled"),
            "installed_at": datetime.now().isoformat()
        }
        _write_skill(skill_id, skill)


        return {
            "success": True,
            "data": skill,
            "message": "技能已生成"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 文件上传与内容提取 ──────────────────────────────────────────────────────

def _extract_text_from_file(filename: str, content_bytes: bytes) -> Optional[str]:
    """
    从文件字节内容提取纯文本。
    支持：txt, md, csv, py, js, ts, json, yaml, html, xml
    以及 docx, xlsx, pptx, pdf（如果安装了对应库）。
    """
    ext = os.path.splitext(filename)[1].lower()

    # ── 纯文本类 ───────────────────────────────────────────────────────
    text_exts = {
        ".txt", ".md", ".csv", ".py", ".js", ".ts", ".jsx", ".tsx",
        ".json", ".yaml", ".yml", ".html", ".htm", ".xml", ".css",
        ".scss", ".less", ".sh", ".bash", ".zsh", ".bat", ".ps1",
        ".sql", ".toml", ".ini", ".cfg", ".conf", ".log", ".rst",
        ".tex", ".r", ".rb", ".go", ".java", ".c", ".cpp", ".h",
        ".hpp", ".cs", ".php", ".swift", ".kt", ".rs", ".dart",
        ".vue", ".svelte",
    }
    if ext in text_exts:
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return content_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return content_bytes.decode("utf-8", errors="replace")

    # ── Word 文档 ───────────────────────────────────────────────────────
    if ext in (".docx",):
        try:
            from docx import Document as DocxDocument
            import io
            doc = DocxDocument(io.BytesIO(content_bytes))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            return f"[Word 文档 {filename}，需安装 python-docx 以提取内容]"
        except Exception as e:
            return f"[Word 文档 {filename} 解析失败: {e}]"

    # ── Excel 表格 ──────────────────────────────────────────────────────
    if ext in (".xlsx", ".xls", ".xlsm"):
        try:
            import openpyxl
            import io
            wb = openpyxl.load_workbook(io.BytesIO(content_bytes), read_only=True, data_only=True)
            lines = []
            for sheet in wb.worksheets:
                lines.append(f"## Sheet: {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    if any(cell is not None for cell in row):
                        lines.append("\t".join(str(c) if c is not None else "" for c in row))
            return "\n".join(lines) if lines else f"[Excel 文件 {filename} 内容为空]"
        except ImportError:
            return f"[Excel 文件 {filename}，需安装 openpyxl 以提取内容]"
        except Exception as e:
            return f"[Excel 文件 {filename} 解析失败: {e}]"

    # ── PowerPoint ─────────────────────────────────────────────────────
    if ext in (".pptx",):
        try:
            from pptx import Presentation
            import io
            prs = Presentation(io.BytesIO(content_bytes))
            lines = []
            for i, slide in enumerate(prs.slides, 1):
                lines.append(f"## 幻灯片 {i}")
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        lines.append(shape.text.strip())
            return "\n".join(lines) if lines else f"[PPT 文件 {filename} 内容为空]"
        except ImportError:
            return f"[PPT 文件 {filename}，需安装 python-pptx 以提取内容]"
        except Exception as e:
            return f"[PPT 文件 {filename} 解析失败: {e}]"

    # ── PDF ─────────────────────────────────────────────────────────────
    if ext in (".pdf",):
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
                texts = [page.extract_text() or "" for page in pdf.pages]
            text = "\n".join(t for t in texts if t.strip())
            return text if text.strip() else f"[PDF 文件 {filename} 无可提取文本]"
        except ImportError:
            pass
        try:
            import PyPDF2
            import io
            reader = PyPDF2.PdfReader(io.BytesIO(content_bytes))
            texts = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(t for t in texts if t.strip())
            return text if text.strip() else f"[PDF 文件 {filename} 无可提取文本]"
        except ImportError:
            return f"[PDF 文件 {filename}，需安装 pdfplumber 或 PyPDF2 以提取内容]"
        except Exception as e:
            return f"[PDF 文件 {filename} 解析失败: {e}]"

    # ── 图片 ─────────────────────────────────────────────────────────────
    if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"):
        # 图片以 base64 编码返回描述信息，不提取文本
        return None  # 由调用方处理图片

    return f"[不支持的文件类型 {ext}，文件名: {filename}]"


def _build_files_context(attached_files: List[AttachedFile]) -> str:
    """
    将附件列表构建为注入 prompt 的上下文字符串。
    """
    if not attached_files:
        return ""

    parts = []
    for f in attached_files:
        if f.is_text:
            # 文本内容直接注入
            content_preview = f.content
            # 限制单文件最多 50000 字符
            if len(content_preview) > 50000:
                content_preview = content_preview[:50000] + "\n... [内容已截断]"
            parts.append(f"【附件：{f.name}】\n```\n{content_preview}\n```")
        else:
            # 二进制（图片等）：只标注存在
            parts.append(f"【附件：{f.name}（二进制文件，类型：{f.type}）】")

    return "\n\n".join(parts)


@router.post("/upload_file")
async def upload_file(file: UploadFile = File(...)):
    """
    接收前端上传的文件，提取内容后返回供前端附加到消息中。
    返回格式：{name, type, content, is_text}
    """
    try:
        content_bytes = await file.read()
        filename = file.filename or "unknown"
        mime_type = file.content_type or "application/octet-stream"

        # 尝试提取文本内容
        text_content = _extract_text_from_file(filename, content_bytes)

        if text_content is not None:
            # 文本文件
            return {
                "success": True,
                "data": {
                    "name": filename,
                    "type": mime_type,
                    "content": text_content,
                    "is_text": True,
                }
            }
        else:
            # 图片等二进制文件：base64 编码
            b64 = base64.b64encode(content_bytes).decode("utf-8")
            return {
                "success": True,
                "data": {
                    "name": filename,
                    "type": mime_type,
                    "content": b64,
                    "is_text": False,
                }
            }
    except Exception as e:
        logger.error(f"文件上传处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/commands")
async def get_slash_commands():
    """
    返回可用的 / 命令列表（用于前端弹出命令面板）。
    """
    from core.tool_dispatcher import _load_all_skills
    skills = _load_all_skills()

    commands = [
        {
            "name": "skill",
            "label": "/skill",
            "description": "调用一个已安装的技能",
            "icon": "MagicStick",
            "type": "category",
        },
        {
            "name": "clear",
            "label": "/clear",
            "description": "清除当前对话（保留历史）",
            "icon": "Delete",
            "type": "action",
        },
        {
            "name": "memory",
            "label": "/memory",
            "description": "查看与当前话题相关的记忆",
            "icon": "Collection",
            "type": "action",
        },
        {
            "name": "plan",
            "label": "/plan",
            "description": "切换到规划模式（Plan）",
            "icon": "List",
            "type": "action",
        },
        {
            "name": "build",
            "label": "/build",
            "description": "切换到构建模式（Build）",
            "icon": "Tools",
            "type": "action",
        },
    ]

    # 把技能列表附加为子命令
    skill_commands = []
    for sk in skills:
        skill_commands.append({
            "name": f"skill:{sk['id']}",
            "label": f"/skill {sk['name']}",
            "description": sk.get("description", ""),
            "icon": "MagicStick",
            "type": "skill",
            "skill_id": sk["id"],
            "skill_name": sk["name"],
        })

    return {
        "success": True,
        "data": {
            "commands": commands,
            "skills": skill_commands,
        }
    }


@router.get("/files/search")
async def search_files(query: str = "", limit: int = 20):
    """
    @文件搜索：在工作目录中搜索文件（用于前端 @ 触发）。
    """
    import glob as glob_module

    base_dir = settings.BASE_DIR
    results = []

    try:
        # 递归搜索常见文件类型
        patterns = ["**/*.md", "**/*.txt", "**/*.py", "**/*.js", "**/*.ts",
                    "**/*.json", "**/*.yaml", "**/*.yml", "**/*.csv",
                    "**/*.xlsx", "**/*.docx", "**/*.pdf"]
        seen = set()
        for pattern in patterns:
            for path in base_dir.glob(pattern):
                # 排除 node_modules / .git / __pycache__ / data
                parts = path.parts
                if any(p in parts for p in ("node_modules", ".git", "__pycache__",
                                            "dist", "build", ".venv", "venv")):
                    continue
                rel = str(path.relative_to(base_dir)).replace("\\", "/")
                if rel in seen:
                    continue
                # 关键词过滤
                if query and query.lower() not in rel.lower():
                    continue
                seen.add(rel)
                results.append({
                    "path": rel,
                    "name": path.name,
                    "ext": path.suffix.lower(),
                })
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
    except Exception as e:
        logger.warning(f"文件搜索失败: {e}")

    return {
        "success": True,
        "data": {"files": results[:limit]}
    }


@router.post("/read_file")
async def read_file_content(path: str = Body(..., embed=True)):
    """
    读取指定相对路径的文件内容，供 @ 文件插入使用。
    """
    try:
        base_dir = settings.BASE_DIR
        full_path = (base_dir / path).resolve()
        # 安全检查：不允许读取 base_dir 以外的文件
        if not str(full_path).startswith(str(base_dir.resolve())):
            raise HTTPException(status_code=403, detail="不允许读取此路径")
        if not full_path.exists() or not full_path.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")

        content_bytes = full_path.read_bytes()
        text_content = _extract_text_from_file(full_path.name, content_bytes)
        if text_content is None:
            b64 = base64.b64encode(content_bytes).decode("utf-8")
            return {
                "success": True,
                "data": {
                    "name": full_path.name,
                    "path": path,
                    "content": b64,
                    "is_text": False,
                }
            }
        return {
            "success": True,
            "data": {
                "name": full_path.name,
                "path": path,
                "content": text_content,
                "is_text": True,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/send")
async def send_to_opencode(request: SendMessageRequest):
    """发送消息到 OpenCode。支持多任务排队：如果该对话已有任务在运行，新任务会加入队列。"""
    conv_id = str(request.conversation_id)

    # 构建包含附件内容的完整消息
    full_message = request.message
    if request.attached_files:
        files_context = _build_files_context(request.attached_files)
        if files_context:
            full_message = f"{files_context}\n\n【用户消息】{request.message}" if request.message.strip() else files_context

    # 如果该对话已有队列，把任务入队并立刻返回"已排队"
    if conv_id in _task_queues and not _task_queues[conv_id].empty():
        await _task_queues[conv_id].put({
            "message": full_message,
            "model": request.model,
            "mode": request.mode,
        })
        return {
            "success": True,
            "data": {"content": None, "queued": True},
            "message": "任务已排队，将在当前任务完成后执行"
        }

    # 初始化队列并启动 runner
    if conv_id not in _task_queues:
        _task_queues[conv_id] = asyncio.Queue()

    try:
        conversations_db.connect()
        memory_manager = MemoryManager()
        content = await _execute_opencode(
            full_message,
            model=request.model,
            mode=request.mode,
            conversation_id=conv_id
        )

        if content:
            await memory_manager.save_message(
                conversation_id=request.conversation_id,
                role="assistant",
                content=content
            )
            conversation = await memory_manager.get_conversation(request.conversation_id)
            if conversation:
                existing_title = conversation.get("title") or ""
                if existing_title == "新对话" or existing_title.strip() == "":
                    new_title = generate_conversation_title(content)
                    await memory_manager.update_conversation_title(request.conversation_id, new_title)

            # 后台自动提取用户习惯/偏好（不阻塞响应）
            asyncio.create_task(
                extract_and_save_background(
                    user_message=request.message,
                    assistant_response=content,
                    memory_manager=_get_chat_memory_manager(),
                    opencode_ws=opencode_ws,
                )
            )
            _materialize_reusable_skill(
                user_message=request.message,
                assistant_response=content
            )

        # 处理队列中等待的任务（非阻塞，后台运行）
        asyncio.create_task(_drain_queue(conv_id, request.conversation_id))

        return {
            "success": True,
            "data": {"content": content, "queued": False},
            "message": "消息已处理"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _chunk_text(text: str, chunk_size: int = 24) -> List[str]:
    if not text:
        return []
    return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]


def _part_to_stream_event(part: dict) -> Optional[dict]:
    if not isinstance(part, dict):
        return None
    part_type = part.get("type")
    if part_type == "text":
        return None
    if part_type in ["step-start", "step-finish", "tool-call", "tool-result", "reasoning", "plan"]:
        return {
            "type": "tool_event",
            "event_type": part_type,
            "data": part
        }
    return {
        "type": "meta_event",
        "event_type": part_type or "unknown",
        "data": part
    }


@router.post("/send_stream")
async def send_to_opencode_stream(request: SendMessageRequest):
    conv_id = str(request.conversation_id)
    full_message = request.message
    if request.attached_files:
        files_context = _build_files_context(request.attached_files)
        if files_context:
            full_message = f"{files_context}\n\n【用户消息】{request.message}" if request.message.strip() else files_context

    if conv_id in _task_queues and not _task_queues[conv_id].empty():
        async def queued_stream():
            event = {"type": "queued", "message": "任务已排队，将在当前任务完成后执行"}
            yield json.dumps(event, ensure_ascii=False) + "\n"
        return StreamingResponse(
            queued_stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            }
        )

    if conv_id not in _task_queues:
        _task_queues[conv_id] = asyncio.Queue()

    async def _run_stream_worker(event_queue: asyncio.Queue):
        _runtime_start(conv_id)
        try:
            conversations_db.connect()
            memory_manager = MemoryManager()
            await event_queue.put({"type": "status", "phase": "started"})
            _runtime_append_event(conv_id, {"type": "status", "phase": "started"})
            raw_content = ""
            content = ""
            parts: List[dict] = []
            async for stream_event in _stream_execute_opencode_with_meta(
                full_message,
                model=request.model,
                mode=request.mode,
                conversation_id=conv_id
            ):
                event_type = stream_event.get("type")
                if event_type == "content_delta":
                    raw_content = stream_event.get("content") or f"{raw_content}{stream_event.get('delta', '')}"
                    next_content = _sanitize_assistant_output(raw_content)
                    if next_content == content:
                        continue
                    delta = next_content[len(content):] if next_content.startswith(content) else next_content
                    content = next_content
                    _runtime_set_content(conv_id, content)
                    await event_queue.put({
                        "type": "content_delta",
                        "delta": delta,
                        "content": content
                    })
                    continue
                if event_type == "tool_event":
                    part = stream_event.get("part")
                    if isinstance(part, dict):
                        parts.append(part)
                        converted = _part_to_stream_event(part)
                        if converted is not None:
                            _runtime_append_event(conv_id, converted)
                            await event_queue.put(converted)
                    continue
                if event_type == "done":
                    raw_content = stream_event.get("content") or raw_content
                    content = _sanitize_assistant_output(raw_content) or content
                    _runtime_set_content(conv_id, content)
                    stream_parts = stream_event.get("parts")
                    if isinstance(stream_parts, list):
                        for p in stream_parts:
                            if isinstance(p, dict):
                                parts.append(p)
                    continue
                if event_type == "error":
                    raise RuntimeError(stream_event.get("error") or "OpenCode 流式调用失败")

            if content:
                await memory_manager.save_message(
                    conversation_id=request.conversation_id,
                    role="assistant",
                    content=content
                )
                conversation = await memory_manager.get_conversation(request.conversation_id)
                if conversation:
                    existing_title = conversation.get("title") or ""
                    if existing_title == "新对话" or existing_title.strip() == "":
                        new_title = generate_conversation_title(content)
                        await memory_manager.update_conversation_title(request.conversation_id, new_title)

                asyncio.create_task(
                    extract_and_save_background(
                        user_message=request.message,
                        assistant_response=content,
                        memory_manager=_get_chat_memory_manager(),
                        opencode_ws=opencode_ws,
                    )
                )
                _materialize_reusable_skill(
                    user_message=request.message,
                    assistant_response=content
                )

            asyncio.create_task(_drain_queue(conv_id, request.conversation_id))
            _runtime_append_event(conv_id, {"type": "done", "content": content or ""})
            await event_queue.put({"type": "done", "content": content or ""})
        except Exception as e:
            _runtime_append_event(conv_id, {"type": "error", "message": str(e)})
            await event_queue.put({"type": "error", "message": str(e)})
        finally:
            _runtime_finish(conv_id, content)
            await event_queue.put({"type": "__worker_done__"})

    async def event_stream():
        event_queue: asyncio.Queue = asyncio.Queue()
        worker_task = asyncio.create_task(_run_stream_worker(event_queue))
        try:
            while True:
                event = await event_queue.get()
                if event.get("type") == "__worker_done__":
                    break
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except asyncio.CancelledError:
            logger.info(f"对话 {conv_id} 的流式连接已断开，任务继续在后台执行")
            return
        finally:
            if worker_task.done():
                try:
                    _ = worker_task.result()
                except Exception:
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


async def _drain_queue(conv_id: str, conversation_id: int):
    """处理队列中排队的任务"""
    if conv_id not in _task_queues:
        return
    q = _task_queues[conv_id]
    while not q.empty():
        try:
            task = await asyncio.wait_for(q.get(), timeout=0.1)
        except asyncio.TimeoutError:
            break
        try:
            conversations_db.connect()
            memory_manager = MemoryManager()

            # 先保存用户消息
            await memory_manager.save_message(
                conversation_id=conversation_id,
                role="user",
                content=task["message"]
            )

            content = await _execute_opencode(
                task["message"],
                model=task.get("model"),
                mode=task.get("mode"),
                conversation_id=conv_id
            )
            if content:
                await memory_manager.save_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=content
                )
                asyncio.create_task(
                    extract_and_save_background(
                        user_message=task["message"],
                        assistant_response=content,
                        memory_manager=_get_chat_memory_manager(),
                        opencode_ws=opencode_ws,
                    )
                )
                _materialize_reusable_skill(
                    user_message=task["message"],
                    assistant_response=content
                )
        except Exception as e:
            logger.error(f"处理排队任务失败: {e}")
        finally:
            q.task_done()


class AbortRequest(BaseModel):
    conversation_id: int


@router.post("/abort")
async def abort_task(request: AbortRequest):
    """终止指定对话的当前运行任务"""
    conv_id = str(request.conversation_id)
    client = opencode_ws

    # 清空队列中等待的任务
    if conv_id in _task_queues:
        q = _task_queues[conv_id]
        cleared = 0
        while not q.empty():
            try:
                q.get_nowait()
                q.task_done()
                cleared += 1
            except Exception:
                break
        if cleared:
            logger.info(f"已清空对话 {conv_id} 的 {cleared} 个排队任务")

    # 终止当前正在运行的 OpenCode session
    session_id = _conversation_current_session.get(conv_id)
    if session_id and client:
        try:
            ok = await client.abort_session(session_id)
            logger.info(f"终止 session {session_id}: {'成功' if ok else '失败'}")
        except Exception as e:
            logger.warning(f"终止 session 出错: {e}")
        finally:
            _conversation_current_session.pop(conv_id, None)
            unmark_conversation_running(conv_id)
            _runtime_append_event(conv_id, {"type": "error", "message": "任务已被用户终止"})
            _runtime_finish(conv_id)

    return {
        "success": True,
        "message": "已发送终止信号"
    }


@router.get("/queue_status/{conversation_id}")
async def get_queue_status(conversation_id: int, since_seq: int = 0):
    """获取对话的任务队列状态"""
    conv_id = str(conversation_id)
    queue_size = 0
    if conv_id in _task_queues:
        queue_size = _task_queues[conv_id].qsize()
    has_running = is_conversation_running(conv_id)
    runtime = _runtime_snapshot(conv_id, since_seq=since_seq)
    return {
        "success": True,
        "data": {
            "running": has_running,
            "queued": queue_size,
            "runtime_events": runtime.get("events", []),
            "runtime_last_seq": runtime.get("last_seq", int(since_seq)),
            "runtime_content": runtime.get("content", ""),
        }
    }


class UndoMessageRequest(BaseModel):
    message_id: int
    conversation_id: int


@router.post("/conversations/{conversation_id}/undo")
async def undo_message(conversation_id: int, request: UndoMessageRequest):
    """撤销指定消息及其之后的所有消息"""
    try:
        conversations_db.connect()
        memory_manager = MemoryManager()

        # 获取所有消息
        messages = await memory_manager.get_messages(conversation_id=conversation_id, limit=1000)
        # 找到目标消息的位置
        target_idx = None
        for i, msg in enumerate(messages):
            if msg.get("id") == request.message_id:
                target_idx = i
                break

        if target_idx is None:
            raise HTTPException(status_code=404, detail="消息不存在")

        # 删除该消息及其之后的所有消息
        to_delete = messages[target_idx:]
        deleted_count = 0
        for msg in to_delete:
            msg_id = msg.get("id")
            if msg_id:
                try:
                    await memory_manager.delete_message(msg_id)
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"删除消息 {msg_id} 失败: {e}")

        return {
            "success": True,
            "data": {"deleted_count": deleted_count},
            "message": f"已撤销 {deleted_count} 条消息"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def get_models():
    """获取 OpenCode 可用模型列表"""
    global opencode_ws
    client = opencode_ws
    created_client = False
    try:
        if client is None:
            client = OpenCodeClient(app_config.opencode.server_url)
            created_client = True
        ok = await client.try_connect(attempts=2, delay=0.3, open_timeout=1.0)
        if not ok:
            parsed = urlparse(app_config.opencode.server_url or "")
            configured_port = parsed.port or 1120
            candidate_ports = []
            for p in [1120, configured_port, 4096]:
                if isinstance(p, int) and 1 <= p <= 65535 and p not in candidate_ports:
                    candidate_ports.append(p)
            actual_port = 0
            for port in candidate_ports:
                actual_port = await start_opencode_server(port)
                if actual_port:
                    break
            if actual_port:
                new_url = f"http://127.0.0.1:{actual_port}"
                client.base_url = new_url
                if opencode_ws is not None:
                    opencode_ws.base_url = new_url
                ok = await client.try_connect(attempts=4, delay=0.4, open_timeout=1.0)
            if not ok:
                return {"success": False, "data": {"models": []}, "message": "OpenCode 未连接"}
        models = await client.get_models()
        return {"success": True, "data": {"models": models}, "message": ""}
    except Exception as e:
        logger.error(f"获取模型列表失败: {e}")
        return {"success": False, "data": {"models": []}, "message": str(e)}
    finally:
        if created_client and client and getattr(client, "connected", False):
            try:
                await client.disconnect()
            except Exception:
                pass


@router.get("/agents")
async def get_agents():
    """获取 OpenCode 支持的 agent 模式列表"""
    return {
        "success": True,
        "data": {
            "agents": [
                {"id": "plan", "name": "Plan 模式", "description": "规划模式：分析需求、制定计划，适合复杂任务拆解"},
                {"id": "build", "name": "Build 模式", "description": "构建模式：直接执行编码任务，适合快速实现"}
            ]
        },
        "message": ""
    }
