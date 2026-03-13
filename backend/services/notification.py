"""
通知服务
支持应用内通知、系统桌面通知、飞书通知、邮箱通知
"""
import sqlite3
import json
import smtplib
import httpx
import asyncio
import base64
import hmac
import hashlib
import time
import platform
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import List, Dict, Optional
from loguru import logger

from config import settings, NotificationConfig
from core.scheduler import ScheduledTask
from core.opencode_ws import TaskResult


class NotificationService:
    """通知服务"""
    
    def __init__(self, config: NotificationConfig = None):
        self.config = config or NotificationConfig()
        self.db_path = str(settings.DATA_DIR / "conversations.db")
        self._init_db()
        logger.info("通知服务初始化完成")
    
    def _init_db(self):
        """初始化数据库"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                type TEXT DEFAULT 'info',
                read BOOLEAN DEFAULT 0,
                task_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        columns = cursor.execute("PRAGMA table_info(notifications)").fetchall()
        existing = {row[1] for row in columns}
        if "task_id" not in existing:
            cursor.execute("ALTER TABLE notifications ADD COLUMN task_id TEXT")
        self.conn.commit()
    
    async def send_task_notification(
        self,
        task: ScheduledTask,
        result: Optional[TaskResult],
        is_error: bool = False,
        error_message: Optional[str] = None
    ):
        """发送任务执行通知"""
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if is_error:
            title = f"❌ 任务执行失败：{task.name}"
            if result and result.error:
                message = result.error
            else:
                message = error_message or "未知错误"
        else:
            title = f"✅ 任务执行成功：{task.name}"
            if result and getattr(result, "content", None):
                message = str(result.content)
            else:
                message = "任务已完成"
        message = f"{message}\n\n任务ID: {task.id}\n执行时间: {now_text}"
        
        # 发送到所有配置的渠道
        tasks = []
        
        if self.config.app_enabled:
            tasks.append(self._send_app_notification(title, message, task.id))
        
        if self.config.desktop_enabled:
            tasks.append(self._send_desktop_notification(title, message))
        
        if self.config.lark_enabled:
            tasks.append(self._send_lark_notification(title, message))
        
        if self.config.email_enabled:
            tasks.append(self._send_email_notification(title, message))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _send_app_notification(
        self,
        title: str,
        message: str,
        task_id: str = None,
        notif_type: str = "info"
    ):
        """发送应用内通知"""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """INSERT INTO notifications (title, message, type, task_id)
                   VALUES (?, ?, ?, ?)""",
                (title, message, notif_type, task_id)
            )
            self.conn.commit()
            logger.info(f"应用内通知已保存：{title}")
        except Exception as e:
            logger.error(f"发送应用内通知失败：{e}")
    
    async def _send_desktop_notification(self, title: str, message: str):
        """发送系统桌面通知（Windows/macOS/Linux）"""
        def _do_notify():
            system = platform.system()
            # 截断消息，桌面通知不适合过长内容
            short_msg = message[:200] + "..." if len(message) > 200 else message
            # 清理掉换行符，部分系统通知不支持
            short_msg = short_msg.replace("\n", " ").strip()
            try:
                if system == "Windows":
                    # 使用 Windows 10+ 原生 toast 通知
                    try:
                        from windows_toasts import Toast, WindowsToaster
                        toaster = WindowsToaster("Codebot")
                        toast = Toast()
                        toast.text_fields = [title, short_msg]
                        toaster.show_toast(toast)
                        return True
                    except ImportError:
                        pass
                    # 回退：使用 plyer
                    try:
                        from plyer import notification as plyer_notif
                        plyer_notif.notify(
                            title=title,
                            message=short_msg,
                            app_name="Codebot",
                            timeout=10
                        )
                        return True
                    except ImportError:
                        pass
                    # 最终回退：使用 ctypes MessageBox
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(
                        0, short_msg, f"[Codebot] {title}", 0x40
                    )
                    return True
                elif system == "Darwin":
                    # macOS 使用 osascript
                    import subprocess
                    escaped_title = title.replace('"', '\\"')
                    escaped_msg = short_msg.replace('"', '\\"')
                    subprocess.run([
                        "osascript", "-e",
                        f'display notification "{escaped_msg}" with title "Codebot" subtitle "{escaped_title}"'
                    ], timeout=5)
                    return True
                else:
                    # Linux 使用 notify-send
                    import subprocess
                    subprocess.run([
                        "notify-send", "-a", "Codebot", title, short_msg
                    ], timeout=5)
                    return True
            except Exception as inner_e:
                logger.warning(f"桌面通知发送失败：{inner_e}")
                return False
        
        try:
            result = await asyncio.to_thread(_do_notify)
            if result:
                logger.info("系统桌面通知已发送")
            else:
                logger.warning("系统桌面通知发送失败（无可用通知库）")
        except Exception as e:
            logger.error(f"发送系统桌面通知失败：{e}")
    
    async def _send_lark_notification(self, title: str, message: str):
        """发送飞书通知"""
        if not self.config.lark_webhook_url:
            logger.warning("飞书 Webhook URL 未配置")
            return
        
        try:
            card = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": title
                        },
                        "template": "red" if "失败" in title else "green"
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": message
                        }
                    ]
                }
            }
            
            payload = card
            if self.config.lark_secret:
                timestamp = str(int(time.time()))
                sign_key = f"{timestamp}\n{self.config.lark_secret}".encode("utf-8")
                sign = base64.b64encode(hmac.new(self.config.lark_secret.encode("utf-8"), sign_key, hashlib.sha256).digest()).decode("utf-8")
                payload = {
                    **card,
                    "timestamp": timestamp,
                    "sign": sign
                }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.config.lark_webhook_url,
                    json=payload,
                    timeout=10
                )
                
                if response.status_code == 200:
                    logger.info("飞书通知发送成功")
                else:
                    logger.error(f"飞书通知发送失败：{response.text}")
                    
        except Exception as e:
            logger.error(f"发送飞书通知失败：{e}")
    
    async def _send_email_notification(self, title: str, message: str):
        """发送邮件通知"""
        if not self.config.email_username or not self.config.email_password:
            logger.warning("邮箱配置不完整")
            return
        
        recipients = self.config.email_to or [self.config.email_from]
        if not recipients:
            logger.warning("没有收件人")
            return
        
        def send_email():
            msg = MIMEMultipart()
            msg['From'] = self.config.email_from or self.config.email_username
            msg['To'] = ', '.join(recipients)
            msg['Subject'] = f"[Codebot] {title}"
            body = f"""
<html>
<body>
    <h2>{title}</h2>
    <p>{message}</p>
    <hr>
    <p style="color: #999; font-size: 12px;">
        此邮件由 Codebot 自动发送<br>
        时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    </p>
</body>
</html>
            """
            msg.attach(MIMEText(body, 'html', 'utf-8'))
            if self.config.email_smtp_port == 465:
                with smtplib.SMTP_SSL(self.config.email_smtp_host, self.config.email_smtp_port) as server:
                    server.login(self.config.email_username, self.config.email_password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.config.email_smtp_host, self.config.email_smtp_port) as server:
                    server.starttls()
                    server.login(self.config.email_username, self.config.email_password)
                    server.send_message(msg)
        
        try:
            await asyncio.to_thread(send_email)
            logger.info("邮件通知发送成功")
        except Exception as e:
            logger.error(f"发送邮件通知失败：{e}")
    
    async def get_notifications(
        self,
        unread_only: bool = False,
        limit: int = 50
    ) -> List[Dict]:
        """获取通知列表"""
        cursor = self.conn.cursor()
        
        if unread_only:
            cursor.execute(
                """SELECT * FROM notifications 
                   WHERE read = 0 
                   ORDER BY created_at DESC 
                   LIMIT ?""",
                (limit,)
            )
        else:
            cursor.execute(
                """SELECT * FROM notifications 
                   ORDER BY created_at DESC 
                   LIMIT ?""",
                (limit,)
            )
        
        return [dict(row) for row in cursor.fetchall()]
    
    async def mark_as_read(self, notification_id: int):
        """标记通知为已读"""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE notifications SET read = 1 WHERE id = ?",
            (notification_id,)
        )
        self.conn.commit()
        logger.info(f"标记通知为已读：{notification_id}")
    
    async def mark_all_as_read(self):
        """标记所有通知为已读"""
        cursor = self.conn.cursor()
        cursor.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        self.conn.commit()
        logger.info("标记所有通知为已读")
    
    async def get_unread_count(self) -> int:
        """获取未读通知数量"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notifications WHERE read = 0")
        return cursor.fetchone()[0]
    
    async def clear_notifications(self):
        """清空所有通知"""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM notifications")
        self.conn.commit()
        logger.info("清空所有通知")
    
    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()
