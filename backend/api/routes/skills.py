import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import app_config, settings
from core.opencode_ws import OpenCodeClient
from core.skill_registry import (
    AUTO_GENERATED,
    BUILTIN,
    EXTERNAL,
    HERMES,
    OPENCLAW,
    OPENCODE,
    SOURCE_LABELS,
    capture_opencode_skill_snapshot,
    get_skill_registry,
    migrate_new_opencode_skills_to_codebot,
    read_skill_markdown,
    write_auto_skill_md,
)

router = APIRouter()

CODEBOT_SKILL_PREFIX = "codebot-"


class SkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    version: str = "1.0.0"
    source: Optional[str] = None
    enabled: bool = True


class SkillUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    source: Optional[str] = None
    enabled: Optional[bool] = None


class BatchDeleteRequest(BaseModel):
    ids: List[str]


class SkillGenerateRequest(BaseModel):
    description: str


class SkillContentUpdateRequest(BaseModel):
    content: str


def _ensure_skills_dir() -> Path:
    settings.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    return settings.SKILLS_DIR


def _skill_path(skill_id: str) -> Path:
    return _ensure_skills_dir() / f"{skill_id}.json"


def _decode_skill_id(skill_id: str) -> str:
    return unquote(skill_id or "").strip()


def _read_skill(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_skill(skill_id: str, data: dict):
    path = _skill_path(skill_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_skills() -> List[dict]:
    return get_skill_registry().list_skills()


def _search_tokens(text: str) -> set[str]:
    raw = re.split(r"[\s\u3000,，。；;:：/\\|()（）\[\]【】{}<>《》\"'`]+", (text or "").lower())
    tokens = {item for item in raw if len(item) >= 2}
    compact = re.sub(r"\s+", "", (text or "").lower())
    if compact:
        tokens.add(compact[:32])
    return tokens


def _skill_similarity(requirement: str, skill: dict) -> float:
    req_tokens = _search_tokens(requirement)
    if not req_tokens:
        return 0.0
    skill_tokens = _search_tokens(" ".join([
        str(skill.get("name") or ""),
        str(skill.get("description") or ""),
        str(skill.get("slug") or ""),
    ]))
    if not skill_tokens:
        return 0.0
    return len(req_tokens & skill_tokens) / max(1, len(req_tokens | skill_tokens))


def _find_similar_skill(requirement: str) -> tuple[Optional[dict], float]:
    best: Optional[dict] = None
    best_score = 0.0
    for skill in get_skill_registry().list_skills(include_content=True):
        score = _skill_similarity(requirement, skill)
        if score > best_score:
            best = skill
            best_score = score
    return best, best_score


def _extract_json_object(text: str) -> Optional[dict]:
    """Best-effort JSON extraction for model responses that may include prose/code fences."""
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    if fence_match:
        candidates.append(fence_match.group(1).strip())
    brace_match = re.search(r"\{[\s\S]*\}", raw)
    if brace_match:
        candidates.append(brace_match.group(0).strip())
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _as_similarity(value: object) -> float:
    try:
        return max(0.0, min(float(value or 0), 1.0))
    except Exception:
        return 0.0


def _skill_markdown_excerpt(skill: Optional[dict], limit: int = 12000) -> str:
    if not skill:
        return ""
    content = str(skill.get("skill_md_content") or "").strip()
    if not content:
        skill_md_path = Path(str(skill.get("skill_md_path") or "")).expanduser()
        if skill_md_path.exists():
            try:
                content = skill_md_path.read_text(encoding="utf-8")
            except Exception:
                content = ""
    if len(content) > limit:
        return f"{content[:limit]}\n\n..."
    return content


async def _search_skill_via_find_skills(client: OpenCodeClient, description: str) -> dict:
    """Ask OpenCode to invoke find-skills first, then normalize the best match result."""
    registry = get_skill_registry()
    fallback_skill, fallback_similarity = _find_similar_skill(description)
    prompt = (
        "你在 Codebot 的“生成技能”前置检索阶段，必须先调用 find-skills skill。"
        "请用 skill 名、功能描述、同义词和多词短语搜索最匹配的技能，并完成必要的安装。"
        "然后只输出一个 JSON 对象，不要输出 Markdown、解释或额外文字。\n"
        "JSON 格式："
        "{\"best_match_id\":\"\",\"best_match_name\":\"\",\"similarity\":0.0,"
        "\"gap\":0.0,\"should_adapt\":false,\"reason\":\"\"}\n"
        "说明：similarity 表示候选技能与需求的贴合度（0-1）；gap = 1 - similarity；"
        "当 gap < 0.40 时 should_adapt=true，否则为 false。\n\n"
        f"用户需求：{description}"
    )
    result = await client.execute_task(prompt, timeout=90)
    parsed = _extract_json_object(result.content if result.success else "") or {}
    similarity = _as_similarity(parsed.get("similarity"))
    match_queries = [
        parsed.get("best_match_id"),
        parsed.get("best_match_name"),
    ]
    matched_skill = None
    for query in match_queries:
        query_text = str(query or "").strip()
        if not query_text:
            continue
        matched_skill = registry.find(query_text) or registry.find_by_query(query_text, allow_opencode=True)
        if matched_skill:
            break
    # If the find-skills stage did not return a concrete identifier, keep a
    # deterministic local fallback so the route still behaves predictably.
    if not matched_skill and fallback_skill:
        matched_skill = fallback_skill
        similarity = max(similarity, fallback_similarity)
    gap = _as_similarity(parsed.get("gap"))
    if gap <= 0 and similarity > 0:
        gap = max(0.0, min(1.0, 1.0 - similarity))
    should_adapt = bool(matched_skill and (parsed.get("should_adapt") is True or similarity >= 0.6 or gap < 0.4))
    return {
        "matched_skill": matched_skill,
        "similarity": similarity,
        "gap": gap,
        "reason": str(parsed.get("reason") or "").strip(),
        "raw_result": result.content if result.success else "",
        "should_adapt": should_adapt,
    }


async def _adapt_skill_into_codebot_auto(
    client: OpenCodeClient,
    description: str,
    matched_skill: dict,
    similarity: float,
) -> dict:
    """Rewrite the matched skill into a writable Codebot auto-generated skill."""
    existing_md = _skill_markdown_excerpt(matched_skill)
    prompt = (
        "你在 Codebot 的“生成技能”改造阶段。"
        "请基于下面这个已找到的 skill，把它重写成一个更贴合用户需求的新技能正文。"
        "只输出最终 SKILL.md 的 Markdown 正文，不要输出 front matter，不要解释。\n\n"
        f"用户需求：{description}\n"
        f"匹配技能：{matched_skill.get('name')} ({matched_skill.get('id')})\n"
        f"贴合度：{similarity:.2f}\n\n"
        f"原始 SKILL.md：\n{existing_md}"
    )
    result = await client.execute_task(prompt, timeout=120)
    body = (result.content or "").strip() if result.success else ""
    if not body:
        body = existing_md or (
            "Use this skill when the user asks for the workflow described below.\n\n"
            f"User need: {description}\n"
        )
    title_seed = str(matched_skill.get("name") or matched_skill.get("slug") or description).strip()
    return get_skill_registry().create_auto_skill(
        name=f"自动生成技能（改造）：{title_seed[:24]}",
        description=description[:180],
        body=body,
        user_message=description,
        slug_hint=_slug_from_description(title_seed or description),
    )


async def _create_skill_via_skill_creator(client: OpenCodeClient, description: str) -> tuple[Optional[dict], str]:
    """Create a new skill from scratch; prefer real skill-creator output, then fall back to body text."""
    snapshot = capture_opencode_skill_snapshot()
    started_at = time.time()
    prompt = (
        "你在 Codebot 的“生成技能”创建阶段。"
        "当前没有足够接近的现成技能，请直接调用 skill-creator skill 创建一个新 skill。"
        "如果当前环境无法真正落盘，则至少输出最终 SKILL.md 的 Markdown 正文，不要输出额外解释。"
        "允许先临时写到 OpenCode skills 目录，Codebot 会自动迁移到自己的自动生成目录。\n\n"
        f"用户需求：{description}"
    )
    result = await client.execute_task(prompt, timeout=150)
    migrated = migrate_new_opencode_skills_to_codebot(
        snapshot=snapshot,
        since=started_at,
        reason="codebot_generate_skill_creator",
    )
    if migrated:
        return migrated[0], "skill-creator"
    body = (result.content or "").strip() if result.success else ""
    if not body:
        return None, "skill-creator"
    item = get_skill_registry().create_auto_skill(
        name=f"自动生成技能：{description[:24]}",
        description=description[:180],
        body=body,
        user_message=description,
        slug_hint=_slug_from_description(description),
    )
    return item, "skill-creator-fallback"


def _opencode_agents_skills_dir() -> Path:
    return Path.home() / ".agents" / "skills"


def _get_codebot_skill_slug(skill_dir_name: str) -> str:
    return f"{CODEBOT_SKILL_PREFIX}{skill_dir_name}"


def _opencode_export_allowed() -> bool:
    raw = os.environ.get("CODEBOT_ALLOW_OPENCODE_SKILL_EXPORT", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _require_opencode_export_enabled():
    if not _opencode_export_allowed():
        raise HTTPException(
            status_code=403,
            detail="Codebot 技能默认不再同步到 OpenCode。若确需导出，请设置 CODEBOT_ALLOW_OPENCODE_SKILL_EXPORT=1 后重启。",
        )


def sync_builtin_skills_to_opencode() -> dict:
    _require_opencode_export_enabled()
    result = {"synced": [], "removed": [], "errors": []}
    oc_dir = _opencode_agents_skills_dir()
    oc_dir.mkdir(parents=True, exist_ok=True)

    codebot_slugs = set()
    for item in get_skill_registry().list_skills():
        if item.get("source") not in {AUTO_GENERATED, BUILTIN}:
            continue
        slug = item.get("slug") or ""
        src = Path(item.get("path") or "")
        if not slug or not src.is_dir():
            continue
        codebot_slugs.add(slug)
        dest = oc_dir / _get_codebot_skill_slug(slug)
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(str(src), str(dest))
            result["synced"].append(dest.name)
        except Exception as exc:
            result["errors"].append(f"{slug}: {exc}")

    for entry in oc_dir.iterdir() if oc_dir.exists() else []:
        if not entry.is_dir() or not entry.name.startswith(CODEBOT_SKILL_PREFIX):
            continue
        original = entry.name[len(CODEBOT_SKILL_PREFIX):]
        if original in codebot_slugs:
            continue
        try:
            shutil.rmtree(entry)
            result["removed"].append(entry.name)
        except Exception as exc:
            result["errors"].append(f"{entry.name}: {exc}")

    return result


def _sync_skill_to_opencode(skill_dir_name: str) -> bool:
    _require_opencode_export_enabled()
    src = settings.SKILLS_DIR / skill_dir_name
    if not src.is_dir() or not (src / "SKILL.md").exists():
        return False
    oc_dir = _opencode_agents_skills_dir()
    oc_dir.mkdir(parents=True, exist_ok=True)
    dest = oc_dir / _get_codebot_skill_slug(skill_dir_name)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(str(src), str(dest))
    return True


def _source_is_read_only(source: str) -> bool:
    return source in {EXTERNAL, HERMES, OPENCLAW, OPENCODE}


def _slug_from_description(description: str) -> str:
    base = description.strip().lower().replace(" ", "_")[:40]
    base = re.sub(r"[^a-z0-9_]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    return base or f"generated_{uuid4().hex[:8]}"


@router.get("")
@router.get("/")
async def list_skills():
    try:
        items = _list_skills()
        return {"success": True, "data": {"items": items, "total": len(items)}}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("")
@router.post("/")
async def install_skill(request: SkillCreateRequest):
    try:
        item = get_skill_registry().create_auto_skill(
            name=request.name,
            description=request.description or "",
            body=request.description or "Describe the reusable workflow for this skill here.",
            user_message=request.source or "",
            slug_hint=request.name,
        )
        return {"success": True, "data": item, "message": "技能已安装到 Codebot 自动生成技能目录"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate")
async def generate_skill(request: SkillGenerateRequest):
    description = request.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="请提供技能描述")
    matched_skill = None
    similarity = 0.0
    strategy = "local-fallback"
    try:
        client = OpenCodeClient(app_config.opencode.server_url)
        ok = await client.try_connect(attempts=2, delay=0.3, open_timeout=1.0)
        if ok:
            search_result = await _search_skill_via_find_skills(client, description)
            matched_skill = search_result.get("matched_skill")
            similarity = _as_similarity(search_result.get("similarity"))
            if search_result.get("should_adapt") and matched_skill:
                item = await _adapt_skill_into_codebot_auto(client, description, matched_skill, similarity)
                strategy = "find-skills-adapt"
                await client.disconnect()
                return {
                    "success": True,
                    "data": {
                        **item,
                        "matched_skill": matched_skill,
                        "similarity": similarity,
                        "gap": search_result.get("gap"),
                        "strategy": strategy,
                        "search_reason": search_result.get("reason") or "",
                    },
                    "message": f"已先通过 find-skills 找到近似技能，并改造后保存到自动生成目录：{item.get('slug')}",
                }
            item, strategy = await _create_skill_via_skill_creator(client, description)
            if item:
                await client.disconnect()
                return {
                    "success": True,
                    "data": {
                        **item,
                        "matched_skill": matched_skill,
                        "similarity": similarity,
                        "gap": search_result.get("gap"),
                        "strategy": strategy,
                        "search_reason": search_result.get("reason") or "",
                    },
                    "message": f"已通过 skill-creator 创建并保存到自动生成目录：{item.get('slug')}",
                }
        await client.disconnect()
    except Exception:
        matched_skill, similarity = _find_similar_skill(description)
    body = (
        "Use this skill when the user asks for the workflow described below.\n\n"
        f"User need: {description}\n\n"
        "1. Clarify the exact input and output when needed.\n"
        "2. Prefer existing project conventions and local tools.\n"
        "3. Complete the workflow end to end and verify the result."
    )
    title = description[:32] if len(description) > 32 else description
    name_prefix = "自动生成技能（改造）" if matched_skill and similarity >= 0.6 else "自动生成技能"
    item = get_skill_registry().create_auto_skill(
        name=f"{name_prefix}：{title}",
        description=description[:180],
        body=body,
        user_message=description,
        slug_hint=_slug_from_description(description),
    )
    return {
        "success": True,
        "data": {
            **item,
            "matched_skill": matched_skill if matched_skill and similarity >= 0.6 else None,
            "similarity": similarity,
            "gap": max(0.0, min(1.0, 1.0 - similarity)) if similarity else 1.0,
            "strategy": strategy,
        },
        "message": f"技能已生成到 Codebot 技能目录：{item.get('slug')}",
    }


@router.post("/batch-delete")
async def batch_delete_skills(request: BatchDeleteRequest):
    results = {"success": [], "failed": [], "skipped": []}
    registry = get_skill_registry()
    for raw_id in request.ids:
        skill_id = _decode_skill_id(raw_id)
        try:
            item = registry.find(skill_id)
            if not item:
                results["failed"].append({"id": skill_id, "reason": "不存在"})
                continue
            item_path = Path(item.get("path") or "")
            if item_path.suffix == ".json" and item_path.exists():
                item_path.unlink()
                results["success"].append(skill_id)
                continue
            source = item.get("source")
            if source == AUTO_GENERATED:
                registry.delete_auto_skill(skill_id)
                results["success"].append(skill_id)
            elif source in {BUILTIN, EXTERNAL, HERMES, OPENCLAW, OPENCODE}:
                results["skipped"].append(skill_id)
            else:
                path = _skill_path(skill_id)
                if path.exists():
                    path.unlink()
                    results["success"].append(skill_id)
                else:
                    results["failed"].append({"id": skill_id, "reason": "不存在"})
        except Exception as exc:
            results["failed"].append({"id": skill_id, "reason": str(exc)})
    return {
        "success": True,
        "data": results,
        "message": f"已删除 {len(results['success'])} 个，跳过 {len(results['skipped'])} 个只读技能，失败 {len(results['failed'])} 个",
    }


@router.post("/sync-to-opencode")
async def sync_skills_to_opencode():
    result = sync_builtin_skills_to_opencode()
    return {
        "success": True,
        "data": result,
        "message": f"同步完成：已同步 {len(result.get('synced', []))} 个技能到 OpenCode",
    }


@router.get("/opencode-sync-status")
async def get_opencode_sync_status():
    oc_dir = _opencode_agents_skills_dir()
    codebot_slugs = {
        item.get("slug")
        for item in get_skill_registry().list_skills()
        if item.get("source") in {AUTO_GENERATED, BUILTIN} and item.get("slug")
    }
    synced_slugs = set()
    if oc_dir.exists():
        for entry in oc_dir.iterdir():
            if entry.is_dir() and entry.name.startswith(CODEBOT_SKILL_PREFIX):
                synced_slugs.add(entry.name[len(CODEBOT_SKILL_PREFIX):])
    not_synced = sorted(codebot_slugs - synced_slugs)
    stale = sorted(synced_slugs - codebot_slugs)
    return {
        "success": True,
        "data": {
            "enabled": _opencode_export_allowed(),
            "total_codebot": len(codebot_slugs),
            "total_synced": len(synced_slugs),
            "not_synced": not_synced,
            "stale_in_opencode": stale,
            "in_sync": not not_synced and not stale,
            "opencode_skills_dir": str(oc_dir),
        },
    }


@router.post("/{skill_id:path}/sync-to-opencode")
async def sync_single_skill_to_opencode(skill_id: str):
    skill_id = _decode_skill_id(skill_id)
    item = get_skill_registry().find(skill_id)
    if not item:
        raise HTTPException(status_code=404, detail="技能不存在")
    if item.get("source") not in {AUTO_GENERATED, BUILTIN}:
        raise HTTPException(status_code=400, detail="仅 Codebot 内部技能支持高级导出")
    ok = _sync_skill_to_opencode(item.get("slug") or "")
    if not ok:
        raise HTTPException(status_code=500, detail="同步失败")
    return {
        "success": True,
        "data": {"slug": item.get("slug")},
        "message": "技能已导出到 OpenCode",
    }


@router.get("/{skill_id:path}/content")
async def get_skill_content(skill_id: str):
    skill_id = _decode_skill_id(skill_id)
    try:
        content = get_skill_registry().read_content(skill_id)
        return {"success": True, "data": {"content": content}}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="SKILL.md 不存在")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/{skill_id:path}/content")
async def update_skill_content(skill_id: str, request: SkillContentUpdateRequest):
    skill_id = _decode_skill_id(skill_id)
    registry = get_skill_registry()
    item = registry.find(skill_id)
    if not item:
        raise HTTPException(status_code=404, detail="技能不存在")
    if _source_is_read_only(item.get("source") or ""):
        raise HTTPException(status_code=400, detail=f"{item.get('sourceLabel') or item.get('source')} 技能为只读，请在其原工具中管理")
    try:
        updated = registry.write_content(skill_id, request.content)
        return {
            "success": True,
            "message": "SKILL.md 已保存",
            "data": {
                "id": skill_id,
                "name": updated.get("name"),
                "description": updated.get("description"),
            },
        }
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.patch("/{skill_id:path}")
async def update_skill(skill_id: str, request: SkillUpdateRequest):
    skill_id = _decode_skill_id(skill_id)
    item = get_skill_registry().find(skill_id)
    if item and item.get("source") in {AUTO_GENERATED, BUILTIN, EXTERNAL, HERMES, OPENCLAW, OPENCODE}:
        raise HTTPException(status_code=400, detail="该技能类型请通过 SKILL.md 或原工具管理")
    path = _skill_path(skill_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="技能不存在")
    skill = _read_skill(path) or {}
    skill.update(request.model_dump(exclude_unset=True))
    _write_skill(skill_id, skill)
    return {"success": True, "data": skill, "message": "技能已更新"}


@router.delete("/{skill_id:path}")
async def delete_skill(skill_id: str):
    skill_id = _decode_skill_id(skill_id)
    registry = get_skill_registry()
    item = registry.find(skill_id)
    if item:
        item_path = Path(item.get("path") or "")
        if item_path.suffix == ".json" and item_path.exists():
            item_path.unlink()
            return {"success": True, "message": "技能已卸载"}
        source = item.get("source")
        if source == AUTO_GENERATED:
            registry.delete_auto_skill(skill_id)
            return {"success": True, "message": "自动生成技能已删除"}
        if source == BUILTIN:
            raise HTTPException(status_code=400, detail="内置技能不支持卸载")
        if source == EXTERNAL:
            raise HTTPException(status_code=400, detail="外部兼容目录技能为只读，请在设置中移除对应目录")
        if source == HERMES:
            raise HTTPException(status_code=400, detail="Hermes Agent 技能为只读，请在 Hermes Agent 中管理")
        if source == OPENCLAW:
            raise HTTPException(status_code=400, detail="OpenClaw 技能为只读，请在 OpenClaw/StepClaw 中管理")
        if source == OPENCODE:
            raise HTTPException(status_code=400, detail="OpenCode 技能由 OpenCode CLI 管理，Codebot 不再直接卸载")

    path = _skill_path(skill_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="技能不存在")
    path.unlink()
    return {"success": True, "message": "技能已卸载"}


def update_auto_skill_file(skill_md_path: Path, name: str, description: str, user_message: str, assistant_response: str) -> None:
    info = read_skill_markdown(skill_md_path) or {}
    slug = str(info.get("slug") or skill_md_path.parent.name)
    write_auto_skill_md(
        skill_md_path,
        name=name,
        description=description,
        body=assistant_response,
        user_message=user_message,
        slug=slug,
    )

