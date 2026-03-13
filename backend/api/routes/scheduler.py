"""
定时任务 API 路由
"""
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import re
import json

from core.scheduler import TaskScheduler

router = APIRouter()

# 全局调度器实例 (将在 main.py 中初始化)
scheduler: Optional[TaskScheduler] = None


class CreateTaskRequest(BaseModel):
    """创建任务请求"""
    name: str
    cron_expression: str
    task_prompt: str
    notify_channels: List[str] = []


class UpdateTaskRequest(BaseModel):
    """更新任务请求"""
    name: Optional[str] = None
    cron_expression: Optional[str] = None
    task_prompt: Optional[str] = None
    enabled: Optional[bool] = None
    notify_channels: Optional[List[str]] = None


@router.get("/tasks")
async def list_tasks():
    """列出所有定时任务"""
    try:
        tasks = scheduler.list_tasks() if scheduler else []
        
        return {
            "success": True,
            "data": [task.to_dict() for task in tasks]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks")
async def create_task(request: CreateTaskRequest):
    """创建定时任务"""
    try:
        from croniter import croniter
        # 验证 Cron 表达式
        try:
            croniter(request.cron_expression)
        except:
            raise HTTPException(400, detail="无效的 Cron 表达式")
        
        task = scheduler.create_task(
            name=request.name,
            cron_expression=request.cron_expression,
            task_prompt=request.task_prompt,
            notify_channels=request.notify_channels
        )
        
        return {
            "success": True,
            "data": task.to_dict(),
            "message": "任务创建成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """获取任务详情"""
    try:
        task = scheduler.get_task(task_id)
        
        if not task:
            raise HTTPException(404, detail="任务不存在")
        
        return {
            "success": True,
            "data": task.to_dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/tasks/{task_id}")
async def update_task(task_id: str, request: UpdateTaskRequest):
    """更新任务"""
    try:
        update_data = request.model_dump(exclude_unset=True)
        result = scheduler.update_task(task_id, **update_data)
        
        if not result:
            raise HTTPException(404, detail="任务不存在")
        
        return {
            "success": True,
            "data": result.to_dict(),
            "message": "任务已更新"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除任务"""
    try:
        scheduler.delete_task(task_id)
        
        return {
            "success": True,
            "message": "任务已删除"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{task_id}/run")
async def run_task_now(task_id: str):
    """立即执行任务"""
    try:
        success = await scheduler.run_task_now(task_id)
        
        if not success:
            raise HTTPException(404, detail="任务不存在")
        
        return {
            "success": True,
            "message": "任务执行中"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai-generate")
async def ai_generate_cron(prompt: str = Body(..., embed=True)):
    """
    使用 AI 根据自然语言生成 Cron 表达式
    
    示例:
    - "每天早上 9 点" -> "0 9 * * *"
    - "每周一上午 10 点" -> "0 10 * * 1"
    """
    try:
        def _extract_time_parts(text: str):
            time_match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", text)
            if time_match:
                return int(time_match.group(1)), int(time_match.group(2))
            half_match = re.search(r"(\d{1,2})\s*点\s*半", text)
            if half_match:
                return int(half_match.group(1)), 30
            dot_match = re.search(r"(\d{1,2})\s*点(?:\s*(\d{1,2}))?\s*分?", text)
            if dot_match:
                hour = int(dot_match.group(1))
                minute = int(dot_match.group(2)) if dot_match.group(2) else 0
                return hour, minute
            return None, None

        def _normalize_hour(text: str, hour: int):
            if hour is None:
                return None
            if "下午" in text or "晚上" in text:
                if hour < 12:
                    return hour + 12
            if "凌晨" in text and hour == 12:
                return 0
            if "中午" in text and hour < 11:
                return hour + 12
            return hour

        def _guess_cron_from_prompt(text: str):
            if not text:
                return None, None

            # ── 相对时间：X分钟后 / X小时后 / X天后 ──────────────────────────
            rel_minute = re.search(r"(\d+)\s*分钟\s*(?:后|之后|以后)", text)
            rel_hour = re.search(r"(\d+)\s*小时\s*(?:后|之后|以后)", text)
            rel_day = re.search(r"(\d+)\s*天\s*(?:后|之后|以后)", text)
            half_hour = re.search(r"半小时\s*(?:后|之后|以后)", text)

            from datetime import timedelta
            now = datetime.now()
            target = None
            label = None

            if rel_minute:
                delta_min = int(rel_minute.group(1))
                target = now + timedelta(minutes=delta_min)
                label = f"{delta_min}分钟后 {target.strftime('%H:%M')}"
            elif half_hour:
                target = now + timedelta(minutes=30)
                label = f"半小时后 {target.strftime('%H:%M')}"
            elif rel_hour:
                delta_hr = int(rel_hour.group(1))
                target = now + timedelta(hours=delta_hr)
                label = f"{delta_hr}小时后 {target.strftime('%H:%M')}"
            elif rel_day:
                delta_day = int(rel_day.group(1))
                target = now + timedelta(days=delta_day)
                label = f"{delta_day}天后 {target.strftime('%m-%d %H:%M')}"

            if target is not None:
                cron_expr = f"{target.minute} {target.hour} {target.day} {target.month} *"
                return cron_expr, label or f"一次性 {target.strftime('%m-%d %H:%M')}"
            # ─────────────────────────────────────────────────────────────────

            hour, minute = _extract_time_parts(text)
            hour = _normalize_hour(text, hour)
            if minute is None:
                minute = 0
            if hour is None:
                hour = 9
            if "每分钟" in text:
                return "* * * * *", "每分钟执行"
            if "每小时" in text:
                return f"{minute} * * * *", f"每小时 {minute:02d} 分执行"
            day_map = {
                "周一": 1,
                "周二": 2,
                "周三": 3,
                "周四": 4,
                "周五": 5,
                "周六": 6,
                "周日": 0,
                "星期一": 1,
                "星期二": 2,
                "星期三": 3,
                "星期四": 4,
                "星期五": 5,
                "星期六": 6,
                "星期日": 0
            }
            if "工作日" in text:
                return f"{minute} {hour} * * 1-5", f"工作日 {hour:02d}:{minute:02d} 执行"
            if "周末" in text:
                return f"{minute} {hour} * * 6,0", f"周末 {hour:02d}:{minute:02d} 执行"
            for key, value in day_map.items():
                if key in text:
                    return f"{minute} {hour} * * {value}", f"{key} {hour:02d}:{minute:02d} 执行"
            if "每周" in text:
                return f"{minute} {hour} * * 1", f"每周一 {hour:02d}:{minute:02d} 执行"
            if "每月" in text:
                dom_match = re.search(r"每月\s*(\d{1,2})[号日]?", text)
                dom = int(dom_match.group(1)) if dom_match else 1
                return f"{minute} {hour} {dom} * *", f"每月 {dom} 日 {hour:02d}:{minute:02d} 执行"
            if "每天" in text:
                return f"{minute} {hour} * * *", f"每天 {hour:02d}:{minute:02d} 执行"
            if any(key in text for key in ["今天", "明天", "后天"]):
                tag = "今天" if "今天" in text else ("明天" if "明天" in text else "后天")
                return f"{minute} {hour} * * *", f"{tag} {hour:02d}:{minute:02d} 执行"
            return None, None

        cron_expression = None
        description = None
        if scheduler and scheduler.opencode_ws:
            ai_prompt = (
                "你是 Cron 表达式生成器。"
                "根据用户的自然语言描述输出标准 5 段 Cron 表达式。"
                "只返回 JSON，格式为 {\"cron\":\"...\",\"description\":\"...\"}，"
                "description 用中文简要说明执行时间，不要包含多余字段。\n"
                f"用户描述：{prompt}"
            )
            try:
                result = await scheduler.opencode_ws.execute_task(ai_prompt)
            except Exception:
                result = None
            result_content = None
            if result and result.success and result.content:
                result_content = result.content
            if result_content:
                match = re.search(r"\{[\s\S]*\}", result_content)
                if match:
                    data = json.loads(match.group(0))
                    cron_expression = data.get("cron")
                    description = data.get("description")
        if not cron_expression:
            cron_expression, description = _guess_cron_from_prompt(prompt)
        if not cron_expression:
            cron_expression = "0 9 * * *"
            description = "每天 09:00 执行"
        from croniter import croniter
        croniter(cron_expression)
        next_run = croniter(cron_expression, datetime.now()).get_next(datetime)
        return {
            "success": True,
            "data": {
                "cron": cron_expression,
                "description": description,
                "nextRun": next_run.isoformat()
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
