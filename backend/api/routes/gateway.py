"""
本地模型网关 - 提供 OpenAI 兼容的 API 代理接口。

用户可以在 Python 代码中通过配置 codebot 本地 URL 和模型名
间接调用 codebot 已配置的 AI 模型，无需自行管理 API key。

建议先请求 `GET /v1/models` 获取当前真实可用的模型 ID，
再发起 `POST /v1/chat/completions`。

用法示例:
    from openai import OpenAI
    client = OpenAI(base_url="http://127.0.0.1:8080/v1", api_key="any")
    resp = client.chat.completions.create(
        model="github-copilot/gpt-4.1",
        messages=[{"role": "user", "content": "Hello"}],
    )
    print(resp.choices[0].message.content)
"""
import time
import uuid
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
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
    role: str  # "system" | "user" | "assistant"
    content: str

class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False

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


def _build_user_prompt(messages: List[ChatMessage]) -> tuple[str, Optional[str]]:
    """将 OpenAI 格式的 messages 数组转换为 OpenCode 需要的 prompt + system。
    
    返回 (prompt, system)
    """
    system_parts = []
    conversation_parts = []
    
    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
        elif msg.role == "user":
            conversation_parts.append(f"用户: {msg.content}")
        elif msg.role == "assistant":
            conversation_parts.append(f"助手: {msg.content}")
    
    system = "\n\n".join(system_parts) if system_parts else None
    
    # 如果只有一条用户消息，直接使用其内容
    user_messages = [m for m in messages if m.role == "user"]
    if len(user_messages) == 1 and len([m for m in messages if m.role == "assistant"]) == 0:
        prompt = user_messages[-1].content
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
    prompt, system = _build_user_prompt(request.messages)
    request_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    # 用于响应体中的 model 字段（不能为 None）
    response_model = model or "default"
    
    if request.stream:
        return StreamingResponse(
            _stream_response(client, prompt, model, system, request_id, created, response_model),
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
    
    content = result.content or ""
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
):
    """生成 SSE 流式响应，兼容 OpenAI 流式格式"""
    import json
    
    try:
        async for event in client.execute_task_stream(
            prompt=prompt,
            model=model,
            system=system,
            timeout=300,
        ):
            event_type = event.get("type", "")
            
            if event_type == "content_delta":
                delta = event.get("delta", "")
                if delta:
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
                yield "data: [DONE]\n\n"
                return
        
        # 如果流正常结束但没有 done 事件，补发结束标记
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
