"""Lightweight growth candidates for Codebot's learning loop."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger

from config import settings


PENDING = "pending"
ACCEPTED = "accepted"
REJECTED = "rejected"


def _store_path() -> Path:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return settings.DATA_DIR / "growth_candidates.json"


def _load() -> List[Dict[str, Any]]:
    path = _store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(items: List[Dict[str, Any]]) -> None:
    _store_path().write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def list_candidates(status: str = PENDING, limit: int = 50) -> List[Dict[str, Any]]:
    items = _load()
    if status:
        items = [item for item in items if item.get("status") == status]
    items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return items[: max(1, min(limit, 200))]


def get_candidate(candidate_id: str) -> Optional[Dict[str, Any]]:
    for item in _load():
        if item.get("id") == candidate_id:
            return item
    return None


def add_candidate(
    kind: str,
    title: str,
    content: str,
    confidence: float = 0.5,
    payload: Optional[Dict[str, Any]] = None,
    evidence: str = "",
) -> Optional[Dict[str, Any]]:
    title = (title or "").strip()
    content = (content or "").strip()
    if not kind or not title or not content:
        return None
    items = _load()
    fingerprint = _fingerprint(kind, title, content)
    now = datetime.now().isoformat()
    for item in items:
        if item.get("fingerprint") == fingerprint and item.get("status") == PENDING:
            item["hit_count"] = int(item.get("hit_count") or 1) + 1
            item["updated_at"] = now
            item["confidence"] = max(float(item.get("confidence") or 0), float(confidence))
            _save(items)
            return item
    item = {
        "id": uuid4().hex,
        "kind": kind,
        "title": title[:120],
        "content": content[:4000],
        "confidence": float(confidence),
        "payload": payload or {},
        "evidence": evidence[:1200],
        "fingerprint": fingerprint,
        "status": PENDING,
        "hit_count": 1,
        "created_at": now,
        "updated_at": now,
    }
    items.append(item)
    _save(items[-500:])
    logger.info(f"[growth] candidate added: {kind} {title[:40]}")
    return item


def mark_candidate(candidate_id: str, status: str) -> Optional[Dict[str, Any]]:
    items = _load()
    for item in items:
        if item.get("id") == candidate_id:
            item["status"] = status
            item["updated_at"] = datetime.now().isoformat()
            _save(items)
            return item
    return None


def record_chat_growth_candidates(user_message: str, assistant_response: str, conversation_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Capture medium-confidence learning opportunities without auto-applying them."""
    created: List[Dict[str, Any]] = []
    user = (user_message or "").strip()
    answer = (assistant_response or "").strip()
    if not user and not answer:
        return created
    joined = f"{user}\n{answer}"

    if any(token in user for token in ["以后", "下次", "偏好", "喜欢", "不喜欢", "总是", "不要", "记住"]):
        explicit_memory = any(token in user for token in ["记住", "以后", "下次", "今后", "往后"])
        item = add_candidate(
            kind="memory",
            title="可能需要沉淀为记忆",
            content=user[:1000],
            confidence=0.85 if explicit_memory else 0.6,
            payload={"category": "preference", "conversation_id": conversation_id},
            evidence=user[:1000],
        )
        if item:
            created.append(item)

    schedule_words = ["提醒", "定时", "每天", "每周", "每月", "以后", "明天", "后天", "明早", "今晚", "下周", "下个月"]
    has_schedule_word = any(word in user for word in schedule_words)
    has_clear_time = bool(re.search(r"\d{1,2}\s*[:点]\s*\d{0,2}", user))
    if has_schedule_word and not has_clear_time:
        item = add_candidate(
            kind="task",
            title="可能需要创建定时任务",
            content=user[:1000],
            confidence=0.5,
            payload={"conversation_id": conversation_id},
            evidence=user[:1000],
        )
        if item:
            created.append(item)

    workflow_words = ["步骤", "流程", "脚本", "命令", "自动化", "排查", "修复", "部署", "配置", "workflow", "script", "automation"]
    bullet_count = len(re.findall(r"(?:\n\s*[-*]|\n\s*\d+[.)、])", answer))
    hit = sum(1 for word in workflow_words if word.lower() in joined.lower())
    explicit_skill = any(token in user.lower() for token in ["生成skill", "生成技能", "沉淀为技能", "保存为技能", "做成技能", "以后遇到", "下次遇到"])
    if explicit_skill or (len(answer) >= 360 and bullet_count >= 1 and hit >= 2):
        item = add_candidate(
            kind="skill",
            title="可能需要沉淀为技能",
            content=answer[:3000],
            confidence=0.85 if explicit_skill else 0.65,
            payload={"user_message": user, "assistant_response": answer[:6000], "conversation_id": conversation_id},
            evidence=user[:1000],
        )
        if item:
            created.append(item)

    return created


def _fingerprint(kind: str, title: str, content: str) -> str:
    normalized = re.sub(r"\s+", " ", f"{kind}:{title}:{content[:500]}").strip().lower()
    import hashlib

    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()
