import json
import os
import re
import shutil
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
    OPENCLAW,
    OPENCODE,
    SOURCE_LABELS,
    get_skill_registry,
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
    return source in {EXTERNAL, OPENCLAW, OPENCODE}


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

    body = ""
    try:
        client = OpenCodeClient(app_config.opencode.server_url)
        ok = await client.try_connect(attempts=2, delay=0.3, open_timeout=1.0)
        if ok:
            prompt = (
                "请根据下面的需求生成一个可复用的 agent skill 操作说明。"
                "只输出 Markdown 正文步骤，不要创建文件，不要写入 ~/.agents/skills。\n\n"
                f"需求：{description}"
            )
            result = await client.execute_task(prompt, timeout=60)
            if result.success and result.content:
                body = result.content.strip()
        await client.disconnect()
    except Exception:
        body = ""

    if not body:
        body = (
            "Use this skill when the user asks for the workflow described below.\n\n"
            f"User need: {description}\n\n"
            "1. Clarify the exact input and output when needed.\n"
            "2. Prefer existing project conventions and local tools.\n"
            "3. Complete the workflow end to end and verify the result."
        )

    title = description[:32] if len(description) > 32 else description
    item = get_skill_registry().create_auto_skill(
        name=f"自动生成技能：{title}",
        description=description[:180],
        body=body,
        user_message=description,
        slug_hint=_slug_from_description(description),
    )
    return {
        "success": True,
        "data": item,
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
            elif source in {BUILTIN, EXTERNAL, OPENCLAW, OPENCODE}:
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
    if item and item.get("source") in {AUTO_GENERATED, BUILTIN, EXTERNAL, OPENCLAW, OPENCODE}:
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
