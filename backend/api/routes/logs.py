import sqlite3
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings, app_config, LogConfig, save_config

router = APIRouter()


class LogConfigUpdate(BaseModel):
    task_log_retention_days: Optional[int] = None
    chat_log_retention_days: Optional[int] = None
    system_log_retention_days: Optional[int] = None
    log_level: Optional[str] = None


class BatchDeleteRequest(BaseModel):
    ids: List[str]


def _get_conn():
    conn = sqlite3.connect(str(settings.DATA_DIR / "scheduled_tasks.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _get_chat_conn():
    conn = sqlite3.connect(str(settings.DATA_DIR / "conversations.db"))
    conn.row_factory = sqlite3.Row
    return conn


@router.get("/task-logs")
async def list_task_logs(task_id: Optional[str] = None, limit: int = 50, offset: int = 0):
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        if task_id:
            cursor.execute(
                """SELECT * FROM task_logs 
                   WHERE task_id = ? 
                   ORDER BY started_at DESC 
                   LIMIT ? OFFSET ?""",
                (task_id, limit, offset)
            )
            count_cursor = conn.cursor()
            count_cursor.execute("SELECT COUNT(*) FROM task_logs WHERE task_id = ?", (task_id,))
            total = count_cursor.fetchone()[0]
        else:
            cursor.execute(
                """SELECT * FROM task_logs 
                   ORDER BY started_at DESC 
                   LIMIT ? OFFSET ?""",
                (limit, offset)
            )
            count_cursor = conn.cursor()
            count_cursor.execute("SELECT COUNT(*) FROM task_logs")
            total = count_cursor.fetchone()[0]
        items = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return {"success": True, "data": {"items": items, "total": total}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/task-logs/{log_id}")
async def delete_task_log(log_id: str):
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM task_logs WHERE id = ?", (log_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="日志不存在")
        cursor.execute("DELETE FROM task_logs WHERE id = ?", (log_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "日志已删除"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/task-logs/batch-delete")
async def batch_delete_task_logs(request: BatchDeleteRequest):
    try:
        if not request.ids:
            return {"success": True, "data": {"deleted": 0}, "message": "没有要删除的日志"}
        conn = _get_conn()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(request.ids))
        cursor.execute(f"DELETE FROM task_logs WHERE id IN ({placeholders})", request.ids)
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return {"success": True, "data": {"deleted": deleted}, "message": f"已删除 {deleted} 条日志"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_log_config():
    try:
        return {"success": True, "data": app_config.logs.model_dump()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_log_config(request: LogConfigUpdate):
    try:
        updates = request.model_dump(exclude_unset=True)
        current = app_config.logs.model_dump()
        current.update(updates)
        app_config.logs = LogConfig(**current)
        save_config(app_config)
        return {"success": True, "message": "配置已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cleanup")
async def cleanup_logs(days: Optional[int] = None):
    try:
        retention_days = days if days is not None else app_config.logs.task_log_retention_days
        if retention_days == 0:
            return {"success": True, "data": {"deleted": 0}, "message": "日志保留为永久"}
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM task_logs WHERE started_at < ?", (cutoff_date.isoformat(),))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return {"success": True, "data": {"deleted": deleted}, "message": f"已清理 {deleted} 条日志"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 聊天日志 API ─────────────────────────────────────────────────────────────

@router.get("/chat-logs")
async def list_chat_logs(conversation_id: Optional[int] = None, limit: int = 50, offset: int = 0):
    """获取聊天日志列表，包含内部提示词、推理过程和最终回复。"""
    try:
        conn = _get_chat_conn()
        cursor = conn.cursor()
        if conversation_id:
            cursor.execute(
                """SELECT cl.*, c.title as conversation_title
                   FROM chat_logs cl
                   LEFT JOIN conversations c ON c.id = cl.conversation_id
                   WHERE cl.conversation_id = ?
                   ORDER BY cl.created_at DESC
                   LIMIT ? OFFSET ?""",
                (conversation_id, limit, offset)
            )
            count_cursor = conn.cursor()
            count_cursor.execute("SELECT COUNT(*) FROM chat_logs WHERE conversation_id = ?", (conversation_id,))
            total = count_cursor.fetchone()[0]
        else:
            cursor.execute(
                """SELECT cl.*, c.title as conversation_title
                   FROM chat_logs cl
                   LEFT JOIN conversations c ON c.id = cl.conversation_id
                   ORDER BY cl.created_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset)
            )
            count_cursor = conn.cursor()
            count_cursor.execute("SELECT COUNT(*) FROM chat_logs")
            total = count_cursor.fetchone()[0]
        items = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return {"success": True, "data": {"items": items, "total": total}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat-logs/{log_id}")
async def get_chat_log(log_id: int):
    """获取单条聊天日志的详情。"""
    try:
        conn = _get_chat_conn()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT cl.*, c.title as conversation_title
               FROM chat_logs cl
               LEFT JOIN conversations c ON c.id = cl.conversation_id
               WHERE cl.id = ?""",
            (log_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="聊天日志不存在")
        return {"success": True, "data": dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/chat-logs/{log_id}")
async def delete_chat_log(log_id: int):
    """删除单条聊天日志。"""
    try:
        conn = _get_chat_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM chat_logs WHERE id = ?", (log_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail="聊天日志不存在")
        cursor.execute("DELETE FROM chat_logs WHERE id = ?", (log_id,))
        conn.commit()
        conn.close()
        return {"success": True, "message": "聊天日志已删除"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat-logs/batch-delete")
async def batch_delete_chat_logs(request: BatchDeleteRequest):
    """批量删除聊天日志。"""
    try:
        if not request.ids:
            return {"success": True, "data": {"deleted": 0}, "message": "没有要删除的日志"}
        conn = _get_chat_conn()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(request.ids))
        ids_as_int = [int(i) for i in request.ids]
        cursor.execute(f"DELETE FROM chat_logs WHERE id IN ({placeholders})", ids_as_int)
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return {"success": True, "data": {"deleted": deleted}, "message": f"已删除 {deleted} 条聊天日志"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat-logs/cleanup")
async def cleanup_chat_logs(days: Optional[int] = None):
    """按保留天数清理旧聊天日志。"""
    try:
        retention_days = days if days is not None else app_config.logs.chat_log_retention_days
        if retention_days == 0:
            return {"success": True, "data": {"deleted": 0}, "message": "日志保留为永久"}
        cutoff_date = datetime.now() - timedelta(days=retention_days)
        conn = _get_chat_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_logs WHERE created_at < ?", (cutoff_date.isoformat(),))
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        return {"success": True, "data": {"deleted": deleted}, "message": f"已清理 {deleted} 条聊天日志"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

