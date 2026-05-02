from fastapi import APIRouter, HTTPException

from core.growth import ACCEPTED, REJECTED, get_candidate, list_candidates, mark_candidate
from core.memory_manager import MemoryManager
from core.skill_registry import get_skill_registry

router = APIRouter()


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
        item = get_skill_registry().create_auto_skill(
            name=candidate.get("title") or "自动生成技能",
            description=(candidate.get("evidence") or candidate.get("content") or "")[:180],
            body=candidate.get("content") or "",
            user_message=payload.get("user_message") or candidate.get("evidence") or "",
        )
        created = {"kind": "skill", "skill": item}
    elif kind == "task":
        raise HTTPException(status_code=400, detail="该任务候选缺少明确 cron，请在定时任务页面手动创建或补充时间后再执行")
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

