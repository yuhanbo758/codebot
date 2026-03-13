import json
import re
import sqlite3
import time
from typing import Optional

import httpx
from loguru import logger

from config import settings, LarkBotConfig


class LarkBotService:
    def __init__(self, config: Optional[LarkBotConfig] = None):
        self.config = config or LarkBotConfig()
        self._token = None
        self._token_expires_at = 0
        self._db = sqlite3.connect(str(settings.DATA_DIR / "conversations.db"))
        self._db.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        cursor = self._db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lark_conversations (
                chat_id TEXT PRIMARY KEY,
                conversation_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._db.commit()

    def close(self):
        try:
            self._db.close()
        except Exception:
            pass

    def extract_text(self, content: str) -> str:
        if not content:
            return ""
        try:
            data = json.loads(content)
        except Exception:
            return ""
        return data.get("text", "") or ""

    def normalize_text(self, text: str) -> str:
        cleaned = re.sub(r"<at[^>]*>.*?</at>", "", text, flags=re.IGNORECASE)
        return cleaned.strip()

    def _get_conversation_id(self, chat_id: str) -> Optional[int]:
        cursor = self._db.cursor()
        cursor.execute(
            "SELECT conversation_id FROM lark_conversations WHERE chat_id = ?",
            (chat_id,)
        )
        row = cursor.fetchone()
        return int(row[0]) if row else None

    def _set_conversation_id(self, chat_id: str, conversation_id: int):
        cursor = self._db.cursor()
        cursor.execute(
            "SELECT chat_id FROM lark_conversations WHERE chat_id = ?",
            (chat_id,)
        )
        exists = cursor.fetchone() is not None
        if exists:
            cursor.execute(
                """UPDATE lark_conversations
                   SET conversation_id = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE chat_id = ?""",
                (conversation_id, chat_id)
            )
        else:
            cursor.execute(
                "INSERT INTO lark_conversations (chat_id, conversation_id) VALUES (?, ?)",
                (chat_id, conversation_id)
            )
        self._db.commit()

    async def get_or_create_conversation_id(self, memory_manager, chat_id: str, title: str) -> int:
        existing = self._get_conversation_id(chat_id)
        if existing:
            return existing
        conversation_id = await memory_manager.create_conversation(title)
        self._set_conversation_id(chat_id, conversation_id)
        return conversation_id

    async def _get_tenant_token(self) -> Optional[str]:
        if not self.config.app_id or not self.config.app_secret:
            return None
        now = time.time()
        if self._token and now < self._token_expires_at:
            return self._token
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.config.app_id,
            "app_secret": self.config.app_secret
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload)
                data = response.json()
                if data.get("code") != 0:
                    logger.error(f"飞书获取 tenant_access_token 失败: {data}")
                    return None
                token = data.get("tenant_access_token")
                expire = int(data.get("expire", 0))
                if token:
                    self._token = token
                    self._token_expires_at = now + max(expire - 60, 0)
                return token
        except Exception as e:
            logger.error(f"飞书获取 tenant_access_token 异常: {e}")
            return None

    async def send_text_message(self, receive_id: str, text: str, receive_id_type: str = "chat_id") -> bool:
        token = await self._get_tenant_token()
        if not token:
            logger.error("飞书发送消息失败：未获取到 tenant_access_token")
            return False
        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False)
        }
        headers = {
            "Authorization": f"Bearer {token}"
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload, headers=headers)
                data = response.json()
                if response.status_code == 200 and data.get("code") == 0:
                    return True
                logger.error(f"飞书发送消息失败: {data}")
        except Exception as e:
            logger.error(f"飞书发送消息异常: {e}")
        return False
