import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from loguru import logger

from config import app_config, LarkBotConfig, save_config
from core.memory_manager import MemoryManager
from services.lark_bot import LarkBotService
from api.routes import chat

router = APIRouter()
memory_manager: Optional[MemoryManager] = None


class LarkBotConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    connection_mode: Optional[str] = None
    app_id: Optional[str] = None
    app_secret: Optional[str] = None
    verify_token: Optional[str] = None
    encrypt_key: Optional[str] = None
    receive_id_type: Optional[str] = None


@router.get("/config")
async def get_lark_config():
    try:
        return {"success": True, "data": app_config.lark_bot.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_lark_config(request: LarkBotConfigUpdate):
    try:
        updates = request.model_dump(exclude_unset=True)
        current = app_config.lark_bot.model_dump()
        current.update(updates)
        app_config.lark_bot = LarkBotConfig(**current)
        save_config(app_config)
        return {"success": True, "message": "配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/events")
async def handle_lark_event(request: Request):
    if not app_config.lark_bot.enabled:
        return {"success": True}
    if (app_config.lark_bot.connection_mode or "ws") != "webhook":
        raise HTTPException(status_code=400, detail="当前为长连接模式，Webhook 回调未启用")
    raw = await request.body()
    if not raw:
        return {"success": True}
    try:
        data = json.loads(raw)
    except Exception:
        return {"success": True}
    if "encrypt" in data:
        raise HTTPException(status_code=400, detail="暂不支持加密事件，请在飞书后台关闭加密")
    if data.get("type") == "url_verification":
        token = data.get("token")
        if app_config.lark_bot.verify_token and token != app_config.lark_bot.verify_token:
            raise HTTPException(status_code=403, detail="verify token mismatch")
        return {"challenge": data.get("challenge")}
    if data.get("type") != "event_callback":
        return {"success": True}
    if app_config.lark_bot.verify_token and data.get("token") != app_config.lark_bot.verify_token:
        raise HTTPException(status_code=403, detail="verify token mismatch")

    event = data.get("event") or {}
    message = event.get("message") or {}
    if not message:
        return {"success": True}
    sender = event.get("sender") or {}
    if sender.get("sender_type") == "app":
        return {"success": True}

    chat_id = message.get("chat_id")
    content = message.get("content", "")
    if not chat_id:
        return {"success": True}

    # 立即返回 200，避免飞书 3 秒超时重试
    # 消息处理在后台异步执行
    asyncio.create_task(_process_webhook_message(chat_id, content))
    return {"success": True}


async def _process_webhook_message(chat_id: str, content: str):
    """在后台异步处理飞书 Webhook 消息，避免阻塞响应。"""
    service = LarkBotService(app_config.lark_bot)
    try:
        text = service.normalize_text(service.extract_text(content))
        if not text:
            logger.warning(f"[飞书Webhook] 消息内容为空，content={content!r}")
            return

        logger.info(f"[飞书Webhook] 处理消息: chat_id={chat_id}, text={text[:50]!r}")
        mm = memory_manager or MemoryManager()
        conversation_title = f"飞书对话-{chat_id[:6]}"
        conversation_id = await service.get_or_create_conversation_id(mm, chat_id, conversation_title)
        await mm.save_message(conversation_id=conversation_id, role="user", content=text)
        reply = await chat._execute_opencode(text)
        if reply:
            await mm.save_message(conversation_id=conversation_id, role="assistant", content=reply)
        else:
            reply = "暂时无法生成回复，请稍后再试"
        await service.send_text_message(
            chat_id,
            reply,
            app_config.lark_bot.receive_id_type or "chat_id"
        )
        logger.info(f"[飞书Webhook] 回复已发送: chat_id={chat_id}")
    except Exception as e:
        logger.error(f"[飞书Webhook] 消息处理失败: {e}", exc_info=True)
    finally:
        service.close()
