import asyncio
import json
import sqlite3
import threading
import time
from typing import Optional

from loguru import logger

from config import settings, LarkBotConfig
from services.lark_bot import LarkBotService
from api.routes import chat


class LarkWsBot:
    def __init__(self, config: LarkBotConfig, loop: asyncio.AbstractEventLoop, memory_manager):
        self.config = config
        self.loop = loop
        self.memory_manager = memory_manager
        self._thread: Optional[threading.Thread] = None
        self._client = None
        # SQLite 连接延迟到 _run 线程内创建，避免跨线程使用
        self._db: Optional[sqlite3.Connection] = None

    def _init_db(self):
        """在 _run 所在线程内调用，保证 SQLite 连接线程安全。"""
        self._db = sqlite3.connect(str(settings.DATA_DIR / "conversations.db"))
        self._db.row_factory = sqlite3.Row
        cursor = self._db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lark_event_dedup (
                event_id TEXT PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 定期清理 7 天前的去重记录，避免无限增长
        cursor.execute(
            "DELETE FROM lark_event_dedup WHERE created_at < datetime('now', '-7 days')"
        )
        self._db.commit()

    def close(self):
        try:
            if self._db:
                self._db.close()
                self._db = None
        except Exception:
            pass

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_in_new_loop, daemon=True)
        self._thread.start()

    def _run_in_new_loop(self):
        """
        在独立线程中创建全新的事件循环运行 lark ws client。

        lark_oapi.ws.client 模块在 import 时会执行:
            loop = asyncio.get_event_loop()
        将 FastAPI 主事件循环缓存到模块级变量 `loop` 中。
        后续 Client.start() 调用 loop.run_until_complete()，
        因主循环已在运行而报 'This event loop is already running'。

        解决方案：在新线程中创建全新的事件循环，
        并直接替换 SDK 模块级的 loop 变量。
        """
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            # 关键：替换 lark_oapi.ws.client 模块中缓存的事件循环
            import lark_oapi.ws.client as _lark_ws_client_mod
            _lark_ws_client_mod.loop = new_loop
            logger.debug(f"[飞书WS] 已替换 SDK 模块级事件循环为新线程循环")

            self._run()
        finally:
            try:
                new_loop.close()
            except Exception:
                pass
            asyncio.set_event_loop(None)

    def stop(self):
        client = self._client
        if client is not None and hasattr(client, "stop"):
            try:
                client.stop()
            except Exception:
                pass

    def _seen_event(self, event_id: str) -> bool:
        if not event_id:
            return False
        cursor = self._db.cursor()
        cursor.execute("SELECT event_id FROM lark_event_dedup WHERE event_id = ?", (event_id,))
        row = cursor.fetchone()
        if row:
            return True
        cursor.execute("INSERT INTO lark_event_dedup (event_id) VALUES (?)", (event_id,))
        self._db.commit()
        return False

    def _run(self):
        # 在当前线程内初始化 SQLite 连接（线程安全）
        self._init_db()

        try:
            import lark_oapi as lark
        except Exception as e:
            logger.error(f"飞书长连接启动失败：缺少 lark-oapi 依赖: {e}")
            return
        
        required_attrs = ["JSON", "LogLevel", "EventDispatcherHandler"]
        missing = [name for name in required_attrs if not hasattr(lark, name)]
        if missing or (not hasattr(lark, "ws")):
            logger.error(
                "飞书长连接启动失败：当前 lark-oapi 版本不支持长连接（ws）能力。"
                f"缺少: {', '.join(missing + ([] if hasattr(lark, 'ws') else ['ws']))}\n"
                "请在飞书配置中将 connection_mode 设置为 webhook，并启用 /api/lark/events 回调。"
            )
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("飞书长连接启动失败：缺少 app_id/app_secret")
            return

        def _extract_message_from_sdk_event(data, lark_module):
            """
            从 lark_oapi SDK 的事件对象中提取消息信息。
            SDK 回调传入的是强类型对象，需要优先使用属性访问，
            JSON 序列化作为备用。
            返回 (event_id, chat_id, msg_type, content, sender_type)
            """
            # 方法1：直接使用 SDK 对象属性（推荐）
            try:
                header = getattr(data, "header", None)
                event_id = ""
                if header:
                    event_id = getattr(header, "event_id", "") or ""

                body = getattr(data, "event", None)
                if body is None:
                    body = getattr(data, "body", None)

                if body is not None:
                    sender = getattr(body, "sender", None)
                    sender_type = ""
                    if sender:
                        sender_id = getattr(sender, "sender_id", None)
                        sender_type = getattr(sender, "sender_type", "") or ""

                    message = getattr(body, "message", None)
                    if message is not None:
                        chat_id = getattr(message, "chat_id", "") or ""
                        msg_type = getattr(message, "message_type", "") or ""
                        content = getattr(message, "content", "") or ""
                        if chat_id:
                            logger.debug(f"[飞书WS] SDK属性解析成功: event_id={event_id}, chat_id={chat_id}, msg_type={msg_type}")
                            return event_id, chat_id, msg_type, content, sender_type
            except Exception as e:
                logger.debug(f"[飞书WS] SDK属性解析失败，尝试JSON回退: {e}")

            # 方法2：JSON 序列化后解析（备用）
            try:
                raw = lark_module.JSON.marshal(data)
                logger.debug(f"[飞书WS] 原始事件JSON: {raw[:500] if raw else 'empty'}")
                obj = json.loads(raw) if raw else {}
            except Exception as e:
                logger.warning(f"[飞书WS] JSON序列化失败: {e}")
                obj = {}

            # 尝试多种结构路径提取事件数据
            event_id = (
                obj.get("header", {}).get("event_id")
                or obj.get("event_id")
                or ""
            )
            # 路径1：标准结构 {header: ..., event: {sender, message}}
            event = obj.get("event") or {}
            # 路径2：嵌套结构 {data: {event: ...}}
            if not event:
                event = obj.get("data", {}).get("event") or {}
            # 路径3：body 结构（某些版本）
            if not event:
                event = obj.get("body", {}).get("event") or {}

            message = event.get("message") or {}
            sender = event.get("sender") or {}
            sender_type = sender.get("sender_type") or ""
            chat_id = message.get("chat_id") or ""
            msg_type = message.get("message_type") or ""
            content = message.get("content") or ""

            if chat_id:
                logger.debug(f"[飞书WS] JSON解析成功: event_id={event_id}, chat_id={chat_id}")
            else:
                logger.warning(f"[飞书WS] 无法提取chat_id，原始结构keys: {list(obj.keys())}")

            return event_id, chat_id, msg_type, content, sender_type

        def on_message(data):
            try:
                event_id, chat_id, msg_type, content, sender_type = _extract_message_from_sdk_event(data, lark)

                if sender_type == "app":
                    logger.debug("[飞书WS] 跳过机器人自身消息")
                    return

                if event_id and self._seen_event(event_id):
                    logger.debug(f"[飞书WS] 重复事件，跳过: {event_id}")
                    return

                if not chat_id:
                    logger.warning("[飞书WS] 消息缺少 chat_id，跳过")
                    return

                if msg_type and msg_type != "text":
                    logger.debug(f"[飞书WS] 非文本消息类型 {msg_type}，跳过")
                    return

                service = LarkBotService(self.config)
                text = service.normalize_text(service.extract_text(content))
                service.close()

                if not text:
                    logger.warning(f"[飞书WS] 消息内容为空，content={content!r}")
                    return

                logger.info(f"[飞书WS] 收到消息: chat_id={chat_id}, text={text[:50]!r}")

                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self._handle_message(chat_id, text),
                        self.loop
                    )
                    # 不等待结果，避免阻塞 WebSocket 事件循环
                except Exception as e:
                    logger.error(f"[飞书WS] 提交消息处理任务失败: {e}")
                    return

            except Exception as e:
                logger.error(f"[飞书WS] on_message 异常: {e}", exc_info=True)

        try:
            # verify_token 和 encrypt_key 传空字符串（不启用验证/加密）
            verify_token = self.config.verify_token or ""
            encrypt_key = self.config.encrypt_key or ""
            event_handler = lark.EventDispatcherHandler.builder(encrypt_key, verify_token) \
                .register_p2_im_message_receive_v1(on_message) \
                .build()

            client = lark.ws.Client(
                self.config.app_id,
                self.config.app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO
            )
            self._client = client
            logger.info(f"[飞书WS] 正在连接，app_id={self.config.app_id}")
            client.start()
        except Exception as e:
            logger.error(f"飞书长连接运行异常: {e}", exc_info=True)
        finally:
            self._client = None

    async def _handle_message(self, chat_id: str, text: str):
        service = LarkBotService(self.config)
        try:
            conversation_title = f"飞书对话-{chat_id[:6]}"
            conversation_id = await service.get_or_create_conversation_id(
                self.memory_manager,
                chat_id,
                conversation_title
            )
            await self.memory_manager.save_message(conversation_id=conversation_id, role="user", content=text)
            reply = await chat._execute_opencode(text)
            reply_text = reply or "暂时无法生成回复，请稍后再试"
            await self.memory_manager.save_message(conversation_id=conversation_id, role="assistant", content=reply_text)
            await service.send_text_message(
                chat_id,
                reply_text,
                self.config.receive_id_type or "chat_id"
            )
        except Exception as e:
            logger.error(f"飞书消息处理失败: {e}")
        finally:
            service.close()
