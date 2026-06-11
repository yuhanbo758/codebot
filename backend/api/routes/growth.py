from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from core.growth import ACCEPTED, REJECTED, get_candidate, list_candidates, mark_candidate, update_candidate
from core.memory_manager import MemoryManager
from api.routes import scheduler as scheduler_router
from api.routes import chat as chat_router

router = APIRouter()


class GrowthTaskPayloadUpdateRequest(BaseModel):
    name: str
    cron_expression: Optional[str] = None
    schedule_text: Optional[str] = None
    task_prompt: str
    run_once: bool = False
    notify_channels: Optional[List[str]] = None
    executor: Optional[str] = None
    execution_model: Optional[str] = None


class GrowthCandidateStructuredUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    evidence: Optional[str] = None
    payload: Optional[dict] = None
    task: Optional[GrowthTaskPayloadUpdateRequest] = None


def _task_schedule_source(candidate: dict, payload: dict) -> str:
    for value in (
        payload.get("schedule_text"),
        payload.get("cron_prompt"),
        candidate.get("evidence"),
        candidate.get("content"),
        payload.get("task_prompt"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


@router.get("/candidates")
async def get_growth_candidates(status: str = "pending", limit: int = 50):
    return {
        "success": True,
        "data": {
            "items": list_candidates(status=status, limit=limit),
        },
    }


@router.post("/candidates/{candidate_id}/accept")
async def accept_growth_candidate(candidate_id: str):
    candidate = get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选不存在")
    if candidate.get("status") != "pending":
        return {"success": True, "data": candidate, "message": "候选已处理"}

    kind = candidate.get("kind")
    payload = candidate.get("payload") or {}
    created = None
    if kind == "memory":
        manager = MemoryManager()
        await manager.save_long_term_memory(
            content=candidate.get("content") or "",
            category=payload.get("category") or "note",
            metadata={"source": "growth_candidate", "candidate_id": candidate_id},
        )
        created = {"kind": "memory"}
    elif kind == "skill":
        source_user_message = payload.get("user_message") or candidate.get("evidence") or ""
        source_assistant_response = payload.get("assistant_response") or candidate.get("content") or ""
        item = await chat_router._materialize_skill_content(
            name=candidate.get("title") or "自动生成技能",
            description=(candidate.get("evidence") or candidate.get("content") or "")[:180],
            user_message=source_user_message,
            assistant_response=source_assistant_response,
            slug_hint=candidate.get("title") or "auto_skill",
        )
        created = {"kind": "skill", "skill": item} if item else {"kind": "skill"}
    elif kind == "task":
        cron_expression = str(payload.get("cron_expression") or payload.get("cron") or "").strip()
        task_prompt = str(payload.get("task_prompt") or candidate.get("content") or "").strip()
        name = str(payload.get("name") or candidate.get("title") or "成长候选定时任务").strip()
        notify_channels = payload.get("notify_channels")
        if not isinstance(notify_channels, list) or not notify_channels:
            notify_channels = ["app"]
        executor = chat_router._task_executor_from_target(payload.get("executor") or payload.get("target") or "opencode")
        execution_model = str(payload.get("execution_model") or "").strip()
        if not scheduler_router.scheduler:
            raise HTTPException(status_code=400, detail="定时任务调度器未就绪")
        if not cron_expression:
            schedule_source = _task_schedule_source(candidate, payload)
            if schedule_source and chat_router._looks_like_schedule_message(schedule_source):
                try:
                    cron_data = await scheduler_router.generate_cron_from_text(schedule_source)
                    cron_expression = str((cron_data or {}).get("cron") or "").strip()
                    if cron_expression:
                        payload["cron_expression"] = cron_expression
                        payload.setdefault("schedule_text", schedule_source)
                except Exception:
                    cron_expression = ""
        if not cron_expression:
            raise HTTPException(status_code=400, detail="该任务候选缺少明确 cron，请在定时任务页面手动创建或补充时间后再执行")
        payload["executor"] = executor
        payload["execution_model"] = execution_model
        update_candidate(candidate_id, {"payload": payload})
        task = scheduler_router.scheduler.create_task(
            name=name[:60] or "成长候选定时任务",
            cron_expression=cron_expression,
            task_prompt=task_prompt or name,
            notify_channels=notify_channels,
            run_once=bool(payload.get("run_once", False)),
            executor=executor,
            execution_model=execution_model,
        )
        created = {"kind": "task", "task": {"id": task.id, "name": task.name, "cron_expression": task.cron_expression, "executor": task.executor, "execution_model": task.execution_model}}
    else:
        raise HTTPException(status_code=400, detail="未知候选类型")

    updated = mark_candidate(candidate_id, ACCEPTED)
    return {"success": True, "data": {"candidate": updated, "created": created}, "message": "候选已接受"}


@router.post("/candidates/{candidate_id}/reject")
async def reject_growth_candidate(candidate_id: str):
    candidate = mark_candidate(candidate_id, REJECTED)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选不存在")
    return {"success": True, "data": candidate, "message": "候选已拒绝"}


@router.patch("/candidates/{candidate_id}")
async def edit_growth_candidate(candidate_id: str, request: GrowthCandidateStructuredUpdateRequest):
    existing = get_candidate(candidate_id)
    if not existing:
        raise HTTPException(status_code=404, detail="候选不存在")
    if existing.get("status") != "pending":
        raise HTTPException(status_code=400, detail="仅可编辑待处理候选")

    kind = str(existing.get("kind") or "")
    payload = dict(existing.get("payload") or {})
    evidence = str(request.evidence).strip() if request.evidence is not None else str(existing.get("evidence") or "")

    if kind == "task" and request.task is not None:
        title = str(request.task.name or "").strip()
        task_prompt = str(request.task.task_prompt or "").strip()
        if not title or not task_prompt:
            raise HTTPException(status_code=400, detail="任务名称和执行内容不能为空")

        cron_expression = str(request.task.cron_expression or "").strip()
        schedule_text = str(request.task.schedule_text or "").strip()
        if not cron_expression and schedule_text:
            try:
                cron_data = await scheduler_router.generate_cron_from_text(schedule_text)
                cron_expression = str((cron_data or {}).get("cron") or "").strip()
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"无法解析任务时间：{exc}")
        if not cron_expression:
            raise HTTPException(status_code=400, detail="请填写 Cron 表达式或自然语言时间")

        notify_channels = request.task.notify_channels if isinstance(request.task.notify_channels, list) and request.task.notify_channels else ["app"]
        executor = chat_router._task_executor_from_target(request.task.executor or payload.get("executor") or "opencode")
        model_source = request.task.execution_model if request.task.execution_model is not None else payload.get("execution_model")
        execution_model = str(model_source or "").strip()
        payload.update({
            "name": title,
            "task_prompt": task_prompt,
            "cron_expression": cron_expression,
            "notify_channels": notify_channels,
            "run_once": bool(request.task.run_once),
            "executor": executor,
            "execution_model": execution_model,
        })
        if schedule_text:
            payload["schedule_text"] = schedule_text
        updated = update_candidate(candidate_id, {
            "title": title,
            "content": task_prompt,
            "payload": payload,
            "evidence": evidence,
        })
        if not updated:
            raise HTTPException(status_code=500, detail="候选更新失败")
        return {"success": True, "data": updated, "message": "候选已更新"}

    title = str(request.title if request.title is not None else existing.get("title") or "").strip()
    content = str(request.content if request.content is not None else existing.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="标题和内容不能为空")

    if isinstance(request.payload, dict):
        payload = request.payload

    if kind == "task":
        if not payload.get("cron_expression"):
            try:
                cron_data = await scheduler_router.generate_cron_from_text(content)
                cron_expression = str((cron_data or {}).get("cron") or "").strip()
                if cron_expression:
                    payload["cron_expression"] = cron_expression
            except Exception:
                pass
        payload["task_prompt"] = content
        payload["name"] = title
    elif kind == "skill":
        payload["assistant_response"] = content
    elif kind == "memory":
        payload["category"] = str(payload.get("category") or "note").strip() or "note"

    updated = update_candidate(candidate_id, {
        "title": title,
        "content": content,
        "payload": payload,
        "evidence": evidence,
    })
    if not updated:
        raise HTTPException(status_code=500, detail="候选更新失败")
    return {"success": True, "data": updated, "message": "候选已更新"}
