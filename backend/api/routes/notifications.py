import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings, app_config, NotificationConfig, save_config
from utils.secrets import merge_masked_secrets, redact_secrets

router = APIRouter()
notification_service = None


class NotificationConfigUpdate(BaseModel):
    app_enabled: Optional[bool] = None
    desktop_enabled: Optional[bool] = None
    lark_enabled: Optional[bool] = None
    lark_webhook_url: Optional[str] = None
    lark_secret: Optional[str] = None
    email_enabled: Optional[bool] = None
    email_smtp_host: Optional[str] = None
    email_smtp_port: Optional[int] = None
    email_username: Optional[str] = None
    email_password: Optional[str] = None
    email_from: Optional[str] = None
    email_to: Optional[list[str]] = None
    poll_interval: Optional[int] = None


class TestEmailRequest(BaseModel):
    recipient: Optional[str] = None


def _get_conn():
    conn = sqlite3.connect(str(settings.DATA_DIR / "conversations.db"))
    conn.row_factory = sqlite3.Row
    return conn


@router.get("")
@router.get("/")
async def list_notifications(limit: int = 50):
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM notifications 
               ORDER BY created_at DESC 
               LIMIT ?""",
            (limit,)
        )
        items = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return {"success": True, "data": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/unread-count")
async def get_unread_count():
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notifications WHERE read = 0")
        count = cursor.fetchone()[0]
        conn.close()
        return {"success": True, "count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{notification_id}/read")
async def mark_notification_as_read(notification_id: int):
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "已标记为已读"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/read-all")
async def mark_all_notifications_as_read():
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        conn.commit()
        conn.close()
        return {"success": True, "message": "已全部标记为已读"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("")
@router.delete("/")
async def clear_notifications():
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notifications")
        conn.commit()
        conn.close()
        return {"success": True, "message": "通知已清空"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_notification_config():
    try:
        return {"success": True, "data": redact_secrets(app_config.notification.model_dump())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_notification_config(request: NotificationConfigUpdate):
    try:
        updates = request.model_dump(exclude_unset=True)
        current = app_config.notification.model_dump()
        current = merge_masked_secrets(current, updates)
        app_config.notification = NotificationConfig(**current)
        save_config(app_config)
        if notification_service:
            notification_service.config = app_config.notification
        return {"success": True, "message": "配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test-email")
async def test_email_config(request: TestEmailRequest):
    if not notification_service:
        raise HTTPException(status_code=503, detail="通知服务未初始化")
    try:
        await notification_service.send_test_email(recipient=request.recipient)
        return {"success": True, "message": "测试邮件发送成功，请检查收件箱"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"测试邮件发送失败：{e}")
