"""
本地模型网关 - 提供 OpenAI 兼容的 API 代理接口。

用户可以在 Python 代码中通过配置 codebot 本地 URL 和模型名
间接调用 codebot 已配置的 AI 模型，无需自行管理 API key。

建议先请求 `GET /v1/models` 获取当前真实可用的模型 ID，
再发起 `POST /v1/chat/completions`。

用法示例（既支持 OpenAI SDK，也支持 requests 等普通 HTTP 客户端）:
    from openai import OpenAI
    client = OpenAI(base_url="http://127.0.0.1:15682/v1", api_key="any")
    resp = client.chat.completions.create(
        model="github-copilot/gpt-4.1",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print(resp.choices[0].message.content)
"""
import json
import re
import time
import uuid
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from loguru import logger

from config import app_config
from core.opencode_ws import OpenCodeClient

router = APIRouter()

_MODEL_PROVIDER_ALIASES = {
    "copilot": "github-copilot",
}


def _normalize_model_name(model_name: Optional[str]) -> Optional[str]:
    model_name = (model_name or "").strip()
    if not model_name:
        return None
    if "/" not in model_name:
        return model_name
    provider_id, model_id = model_name.split("/", 1)
    provider_id = _MODEL_PROVIDER_ALIASES.get(provider_id, provider_id)
    return f"{provider_id}/{model_id}"

# 由 main.py lifespan 注入的全局 OpenCode 客户端（与 chat.py 共享同一实例）
opencode_ws: Optional[OpenCodeClient] = None

# ── 请求/响应模型 ──────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    """兼容新旧 OpenAI Chat Completions 消息格式。

    新版客户端会把 content 发送为内容块数组，而不是单一字符串；工具调用
    消息还可能没有 content，因此这里先宽松接收，再由 `_content_to_text()`
    统一转换为 OpenCode 可以消费的文本。
    """

    model_config = ConfigDict(extra="allow")

    role: str  # system | developer | user | assistant | tool | function
    content: Any = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

class ChatCompletionRequest(BaseModel):
    # 保留未知字段，兼容新版 SDK 新增的 metadata、parallel_tool_calls 等参数。
    model_config = ConfigDict(extra="allow")

    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    stream: Optional[bool] = False
    stream_options: Optional[Dict[str, Any]] = None

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str

class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: str = "stop"

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Choice]
    usage: Usage

class ModelObject(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "opencode"

class ModelsResponse(BaseModel):
    object: str = "list"
    data: List[ModelObject]


# ── 辅助函数 ──────────────────────────────────────────────────────────────

def _get_opencode_client() -> OpenCodeClient:
    """获取 OpenCode 客户端实例，优先复用全局已连接的客户端"""
    if opencode_ws is not None and getattr(opencode_ws, "connected", False):
        return opencode_ws
    # 回退：创建新实例（启动早期全局 client 可能还没连接）
    return OpenCodeClient(app_config.opencode.server_url)


def _short_media_reference(value: Any, media_name: str) -> str:
    """将无法直接转发的多媒体块转换为安全、简短的文本引用。"""
    if isinstance(value, dict):
        value = value.get("url") or value.get("file_id")
    if not isinstance(value, str) or not value:
        return f"[{media_name}]"
    # base64 data URL 可能长达数 MB，不能把它完整拼进文本提示词。
    if value.startswith("data:"):
        return f"[{media_name}：内嵌数据]"
    return f"[{media_name}：{value}]"


def _content_to_text(content: Any) -> str:
    """把 OpenAI 新旧 content 结构统一转换为纯文本。

    支持旧版字符串，以及新版 Chat Completions/Responses 风格的文本内容块。
    图片、音频等当前 OpenCode 文本网关无法原样传递的块会保留为简短引用，
    避免静默丢失上下文，也避免将大段 base64 数据塞入提示词。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("messages[].content 必须是字符串、内容块数组或 null")

    parts: List[str] = []
    for index, part in enumerate(content):
        if isinstance(part, str):
            parts.append(part)
            continue
        if not isinstance(part, dict):
            raise ValueError(f"messages[].content[{index}] 必须是字符串或对象")

        part_type = str(part.get("type") or "text").lower()
        if part_type in {"text", "input_text", "output_text"}:
            text_value = part.get("text", "")
            # 少数兼容客户端会使用 {"text": {"value": "..."}}。
            if isinstance(text_value, dict):
                text_value = text_value.get("value", "")
            if not isinstance(text_value, str):
                raise ValueError(f"messages[].content[{index}].text 必须是字符串")
            parts.append(text_value)
        elif part_type in {"image_url", "input_image"}:
            image_value = part.get("image_url") or part.get("url") or part.get("file_id")
            parts.append(_short_media_reference(image_value, "图片输入"))
        elif part_type in {"input_audio", "audio"}:
            audio_value = part.get("input_audio") or part.get("audio") or part.get("file_id")
            parts.append(_short_media_reference(audio_value, "音频输入"))
        elif part_type in {"refusal"}:
            refusal = part.get("refusal", "")
            if isinstance(refusal, str):
                parts.append(refusal)
        else:
            # 面向未来字段保持可用：未知块若带有文本就保留文本，否则给出类型标记。
            fallback_text = part.get("text") or part.get("content")
            parts.append(fallback_text if isinstance(fallback_text, str) else f"[{part_type} 内容块]")

    return "\n".join(part for part in parts if part)


def _tool_calls_to_text(tool_calls: Optional[List[Dict[str, Any]]]) -> str:
    """将 assistant.tool_calls 保存进多轮文本，避免工具调用上下文丢失。"""
    if not tool_calls:
        return ""
    return json.dumps(tool_calls, ensure_ascii=False, separators=(",", ":"))


_SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder(?:\s[^>]*)?>.*?</system-reminder\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_USER_INPUT_RE = re.compile(
    r"<user_input(?:\s[^>]*)?>(.*?)</user_input\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _split_ide_wrapped_user_content(content: str) -> tuple[str, Optional[str]]:
    """分离 IDE 注入的内部上下文与用户真正输入的内容。

    Trae 等新版 OpenAI 兼容客户端可能把多个 ``<system-reminder>`` 和
    ``<user_input>`` 一起放进一条 user 消息。若直接拼接，OpenCode 会把
    隐藏提示词当成用户正文，模型便可能在回答前复述全部内部上下文。

    返回值为 ``(用户可见问题, 内部上下文)``。普通用户消息不包含这些
    标签时保持原样，避免影响既有客户端。
    """
    if "<system-reminder" not in content.lower():
        return content, None

    user_inputs = [item.strip() for item in _USER_INPUT_RE.findall(content) if item.strip()]
    reminder_blocks = _SYSTEM_REMINDER_RE.findall(content)

    # system-reminder 之外仍可能存在普通问题文本，需要与显式 user_input 合并。
    outside_text = _SYSTEM_REMINDER_RE.sub("", content)
    # 若上游截断了闭合标签，从未闭合的开始标签起全部视为内部内容。
    unclosed_index = outside_text.lower().find("<system-reminder")
    if unclosed_index >= 0:
        outside_text = outside_text[:unclosed_index]
    outside_text = _USER_INPUT_RE.sub("", outside_text).strip()
    visible_parts = [part for part in [outside_text, *user_inputs] if part]

    # 内部上下文继续提供给 OpenCode，但移除其中的 user_input，防止问题重复。
    internal_parts = []
    for block in reminder_blocks:
        cleaned = _USER_INPUT_RE.sub("", block).strip()
        if cleaned:
            internal_parts.append(cleaned)

    visible_text = "\n\n".join(visible_parts).strip()
    internal_context = "\n\n".join(internal_parts).strip() or None
    return visible_text, internal_context


def _sanitize_assistant_content(content: str) -> str:
    """删除模型意外复述的内部 system-reminder，避免隐藏提示词泄漏。"""
    if not content:
        return ""
    sanitized = _SYSTEM_REMINDER_RE.sub("", content)
    # 回答被 max_tokens 等限制截断时，未闭合的提醒块同样不能对外返回。
    unclosed_index = sanitized.lower().find("<system-reminder")
    if unclosed_index >= 0:
        sanitized = sanitized[:unclosed_index]
    return sanitized.lstrip()


class _ReminderStreamSanitizer:
    """跨 SSE 分片过滤 ``<system-reminder>``，同时保持正常内容流式输出。

    标签可能被模型拆成多个 delta，因此不能逐块使用正则。该状态机仅缓存
    最长标签长度附近的少量文本，不会把整段回答延迟到流结束。
    """

    _OPEN = "<system-reminder"
    _CLOSE = "</system-reminder>"

    def __init__(self) -> None:
        self._pending = ""
        self._inside_reminder = False

    def feed(self, delta: str, *, final: bool = False) -> str:
        self._pending += delta
        visible_parts: List[str] = []

        while self._pending:
            lowered = self._pending.lower()
            if self._inside_reminder:
                close_index = lowered.find(self._CLOSE)
                if close_index >= 0:
                    self._pending = self._pending[close_index + len(self._CLOSE):]
                    self._inside_reminder = False
                    continue
                if final:
                    self._pending = ""
                else:
                    # 只保留可能属于闭合标签前缀的尾部，其余隐藏内容立即丢弃。
                    self._pending = self._pending[-(len(self._CLOSE) - 1):]
                break

            open_index = lowered.find(self._OPEN)
            if open_index >= 0:
                visible_parts.append(self._pending[:open_index])
                self._pending = self._pending[open_index + len(self._OPEN):]
                self._inside_reminder = True
                continue

            if final:
                visible_parts.append(self._pending)
                self._pending = ""
            else:
                # 开始标签可能横跨两个 delta，保留足够长的尾部等待下一块。
                keep = min(len(self._pending), len(self._OPEN) - 1)
                if len(self._pending) > keep:
                    visible_parts.append(self._pending[:-keep])
                    self._pending = self._pending[-keep:]
            break

        return "".join(visible_parts)


def _build_user_prompt(messages: List[ChatMessage]) -> tuple[str, Optional[str]]:
    """将 OpenAI 格式的 messages 数组转换为 OpenCode 需要的 prompt + system。
    
    返回 (prompt, system)
    """
    system_parts = []
    conversation_parts = []
    
    normalized_messages = []
    for msg in messages:
        content = _content_to_text(msg.content)
        if msg.role == "user":
            content, internal_context = _split_ide_wrapped_user_content(content)
            if internal_context:
                system_parts.append(internal_context)
        elif msg.role == "assistant":
            # 清理历史轮次中可能已经泄漏的提醒，避免它在后续多轮对话中反复传播。
            content = _sanitize_assistant_content(content)
        normalized_messages.append((msg, content))
        if msg.role in {"system", "developer"}:
            if content:
                system_parts.append(content)
        elif msg.role == "user":
            conversation_parts.append(f"用户: {content}")
        elif msg.role == "assistant":
            conversation_parts.append(f"助手: {content}")
            tool_calls_text = _tool_calls_to_text(msg.tool_calls)
            if tool_calls_text:
                conversation_parts.append(f"助手工具调用: {tool_calls_text}")
        elif msg.role in {"tool", "function"}:
            tool_name = msg.name or msg.tool_call_id or "未命名工具"
            conversation_parts.append(f"工具结果({tool_name}): {content}")
    
    system = "\n\n".join(system_parts) if system_parts else None
    
    # 如果只有一条用户消息，直接使用其内容
    user_messages = [(m, content) for m, content in normalized_messages if m.role == "user"]
    if len(user_messages) == 1 and len([m for m in messages if m.role == "assistant"]) == 0:
        prompt = user_messages[-1][1]
    else:
        # 多轮对话：拼接所有非 system 消息
        prompt = "\n".join(conversation_parts)
    
    return prompt, system


# ── API 端点 ──────────────────────────────────────────────────────────────

@router.get("/models")
async def list_models():
    """列出所有可用模型 - 兼容 OpenAI GET /v1/models"""
    client = _get_opencode_client()
    ok = await client.try_connect(attempts=2, delay=0.3, open_timeout=2.0)
    if not ok:
        raise HTTPException(status_code=503, detail="无法连接 OpenCode 服务")
    
    models = await client.get_models()
    return ModelsResponse(
        data=[
            ModelObject(id=m["id"], owned_by=m.get("provider", "opencode"))
            for m in models
        ]
    )


async def _resolve_model(
    requested_model: Optional[str],
    client: OpenCodeClient,
) -> Optional[str]:
    """解析实际使用的模型名。

    优先级：
      1. 请求中明确指定的 model
      2. app_config.general.chat_default_model（聊天页默认模型）
      3. app_config.memory.organize_model（记忆整理使用模型）
      4. app_config.models.primary_model（用户在设置中配置的主模型）
      5. None — 让 OpenCode 使用自身默认模型
    """
    if requested_model and requested_model.strip().lower() not in {"default", "codebot-default"}:
        normalized = _normalize_model_name(requested_model)
        if normalized != requested_model:
            logger.info(f"[gateway] 模型别名已规范化: {requested_model} -> {normalized}")
        return normalized

    chat_default = _normalize_model_name(app_config.general.chat_default_model)
    if chat_default:
        logger.debug(f"[gateway] no model requested, using chat default model: {chat_default}")
        return chat_default

    organize_model = _normalize_model_name(app_config.memory.organize_model)
    if organize_model:
        logger.debug(f"[gateway] 请求未指定 model，使用记忆整理模型: {organize_model}")
        return organize_model

    primary = _normalize_model_name(app_config.models.primary_model)
    if primary:
        logger.debug(f"[gateway] 请求未指定 model，使用配置主模型: {primary}")
        return primary

    # 都没有配置时交给 OpenCode 自行决定
    logger.debug("[gateway] 请求未指定 model 且无主模型配置，由 OpenCode 决定")
    return None


async def _validate_model_if_needed(
    model: Optional[str],
    client: OpenCodeClient,
) -> None:
    if not model:
        return

    models = await client.get_models()
    if not models:
        return

    available_ids = {item.get("id") for item in models if isinstance(item, dict) and item.get("id")}
    if model in available_ids:
        return

    suggestions = [mid for mid in sorted(available_ids) if isinstance(mid, str) and mid.endswith(f"/{model.split('/', 1)[-1]}")][:5]
    detail = f"模型不存在: {model}"
    if suggestions:
        detail += f"。可尝试: {', '.join(suggestions)}"
    raise HTTPException(status_code=400, detail=detail)


@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """聊天补全 - 兼容 OpenAI POST /v1/chat/completions"""
    if not request.messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")
    
    client = _get_opencode_client()
    ok = await client.try_connect(attempts=2, delay=0.5, open_timeout=3.0)
    if not ok:
        raise HTTPException(status_code=503, detail="无法连接 OpenCode 服务")
    
    model = await _resolve_model(request.model, client)
    await _validate_model_if_needed(model, client)
    try:
        prompt, system = _build_user_prompt(request.messages)
    except ValueError as exc:
        # 使用 400 返回清晰的兼容格式错误，而不是让 Pydantic 提前抛出难读的 422。
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="messages 中没有可处理的用户内容")
    request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    # 用于响应体中的 model 字段（不能为 None）
    response_model = model or "default"
    
    if request.stream:
        return StreamingResponse(
            _stream_response(
                client,
                prompt,
                model,
                system,
                request_id,
                created,
                response_model,
                include_usage=bool((request.stream_options or {}).get("include_usage")),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
    
    # 非流式：直接调用 execute_task
    result = await client.execute_task(
        prompt=prompt,
        model=model,
        system=system,
        timeout=300,
    )
    
    if not result.success:
        detail = result.error or "未知错误"
        if "ProviderModelNotFoundError" in detail:
            detail = f"模型不存在或 provider 名称错误: {detail}"
        raise HTTPException(status_code=502, detail=f"AI 调用失败: {detail}")
    
    # 防御性清理：即使底层模型复述了 IDE 内部提醒，也不允许网关返回给用户。
    content = _sanitize_assistant_content(result.content or "")
    return ChatCompletionResponse(
        id=request_id,
        created=created,
        model=response_model,
        choices=[
            Choice(
                message=ChoiceMessage(content=content),
                finish_reason="stop"
            )
        ],
        usage=Usage(
            prompt_tokens=len(prompt) // 4,  # 粗略估算
            completion_tokens=len(content) // 4,
            total_tokens=(len(prompt) + len(content)) // 4,
        )
    )


async def _stream_response(
    client: OpenCodeClient,
    prompt: str,
    model: Optional[str],
    system: Optional[str],
    request_id: str,
    created: int,
    response_model: str = "default",
    include_usage: bool = False,
):
    """生成 SSE 流式响应，兼容 OpenAI 流式格式"""
    completion_text = ""
    reminder_sanitizer = _ReminderStreamSanitizer()

    def usage_chunk() -> str:
        """新版 SDK 在 include_usage=true 时需要的末尾用量块。"""
        payload = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": response_model,
            "choices": [],
            "usage": {
                "prompt_tokens": len(prompt) // 4,
                "completion_tokens": len(completion_text) // 4,
                "total_tokens": (len(prompt) + len(completion_text)) // 4,
            },
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    try:
        async for event in client.execute_task_stream(
            prompt=prompt,
            model=model,
            system=system,
            timeout=300,
        ):
            event_type = event.get("type", "")
            
            if event_type == "content_delta":
                raw_delta = event.get("delta", "")
                delta = reminder_sanitizer.feed(raw_delta) if isinstance(raw_delta, str) else ""
                if delta:
                    completion_text += delta
                    chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": response_model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": delta},
                            "finish_reason": None,
                        }]
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            
            elif event_type == "done":
                final_delta = reminder_sanitizer.feed("", final=True)
                if final_delta:
                    completion_text += final_delta
                    chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": response_model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": final_delta},
                            "finish_reason": None,
                        }],
                    }
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                # 发送结束标记
                chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": response_model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }]
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                if include_usage:
                    yield usage_chunk()
                yield "data: [DONE]\n\n"
                return
        
        # 如果流正常结束但没有 done 事件，先刷新过滤器，再补发结束标记。
        final_delta = reminder_sanitizer.feed("", final=True)
        if final_delta:
            completion_text += final_delta
            chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": response_model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": final_delta},
                    "finish_reason": None,
                }],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": response_model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }]
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        if include_usage:
            yield usage_chunk()
        yield "data: [DONE]\n\n"
        
    except Exception as e:
        logger.error(f"流式响应出错: {e}")
        error_chunk = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": response_model,
            "choices": [{
                "index": 0,
                "delta": {"content": f"\n\n[错误: {str(e)}]"},
                "finish_reason": "stop",
            }]
        }
        yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
