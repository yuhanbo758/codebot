import json
import re
from datetime import datetime
from pathlib import Path
import shutil
from typing import Optional, List
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import settings, app_config
from core.opencode_ws import OpenCodeClient

router = APIRouter()


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


def _ensure_skills_dir() -> Path:
    settings.SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    return settings.SKILLS_DIR


def _skill_path(skill_id: str) -> Path:
    return _ensure_skills_dir() / f"{skill_id}.json"


def _read_skill(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _read_skill_markdown(path: Path) -> Optional[dict]:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    # Try to parse YAML front matter (--- ... ---)
    name = ""
    description = ""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            front = content[3:end].strip()
            for line in front.splitlines():
                if line.startswith("name:"):
                    name = line[len("name:"):].strip().strip('"\'')
                elif line.startswith("description:"):
                    description = line[len("description:"):].strip().strip('"\'')

    # Fall back to first heading / first paragraph if front matter missing
    if not name:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        for line in lines:
            if line.startswith("#"):
                name = line.lstrip("#").strip()
                break
    if not description:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        for line in lines:
            if line.startswith("#") or line.startswith("---"):
                continue
            description = line
            break

    return {
        "name": name or path.parent.name,
        "description": description
    }


def _list_opencode_skills() -> List[dict]:
    skills_dir = Path.home() / ".agents" / "skills"
    if not skills_dir.exists():
        return []
    items: List[dict] = []
    for entry in skills_dir.iterdir():
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        info = _read_skill_markdown(skill_md) or {}
        stat = skill_md.stat()
        installed_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
        items.append({
            "id": f"opencode:{entry.name}",
            "name": info.get("name") or entry.name,
            "description": info.get("description") or "",
            "version": "1.0.0",
            "source": "opencode",
            "enabled": True,
            "installed_at": installed_at
        })
    return items


def _list_custom_dir_skills() -> List[dict]:
    """从用户自定义的技能目录加载技能（支持多个文件夹路径）。"""
    custom_dirs = app_config.skills.custom_skill_dirs if hasattr(app_config, 'skills') else []
    if not custom_dirs:
        return []
    items: List[dict] = []
    seen_names: set = set()
    for dir_path_str in custom_dirs:
        dir_path = Path(dir_path_str)
        if not dir_path.exists() or not dir_path.is_dir():
            continue
        for entry in dir_path.iterdir():
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            # 使用目录路径+名称作为唯一键，避免重复
            unique_key = f"{dir_path_str}:{entry.name}"
            if unique_key in seen_names:
                continue
            seen_names.add(unique_key)
            info = _read_skill_markdown(skill_md) or {}
            stat = skill_md.stat()
            installed_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
            items.append({
                "id": f"custom:{dir_path_str}:{entry.name}",
                "name": info.get("name") or entry.name,
                "description": info.get("description") or "",
                "version": "1.0.0",
                "source": "custom",
                "source_dir": dir_path_str,
                "enabled": True,
                "installed_at": installed_at
            })
    return items


def _delete_opencode_skill(skill_id: str):
    if not skill_id.startswith("opencode:"):
        raise HTTPException(status_code=400, detail="无效的 OpenCode 技能标识")
    slug = skill_id.split(":", 1)[1].strip()
    if not slug:
        raise HTTPException(status_code=400, detail="无效的 OpenCode 技能标识")
    skills_dir = Path.home() / ".agents" / "skills"
    target = skills_dir / slug
    if not target.exists():
        raise HTTPException(status_code=404, detail="OpenCode 技能不存在")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="OpenCode 技能路径无效")
    shutil.rmtree(target)


def _list_builtin_skills() -> List[dict]:
    """List skills from the project's skills/ directory (SKILL.md format)."""
    skills_dir = _ensure_skills_dir()
    items: List[dict] = []
    for entry in skills_dir.iterdir():
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        info = _read_skill_markdown(skill_md) or {}
        stat = skill_md.stat()
        installed_at = datetime.fromtimestamp(stat.st_mtime).isoformat()
        items.append({
            "id": f"builtin:{entry.name}",
            "name": info.get("name") or entry.name,
            "description": info.get("description") or "",
            "version": "1.0.0",
            "source": "builtin",
            "enabled": True,
            "installed_at": installed_at
        })
    return items


def _list_skills() -> List[dict]:
    skills_dir = _ensure_skills_dir()
    items: List[dict] = []
    for file_path in skills_dir.glob("*.json"):
        skill = _read_skill(file_path)
        if skill:
            items.append(skill)
    items.extend(_list_builtin_skills())
    items.extend(_list_opencode_skills())
    items.extend(_list_custom_dir_skills())
    items.sort(key=lambda x: x.get("installed_at", ""), reverse=True)
    return items


def _write_skill(skill_id: str, data: dict):
    path = _skill_path(skill_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@router.get("")
@router.get("/")
async def list_skills():
    try:
        items = _list_skills()
        return {
            "success": True,
            "data": {
                "items": items,
                "total": len(items)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
@router.post("/")
async def install_skill(request: SkillCreateRequest):
    try:
        skill_id = uuid4().hex
        skill = {
            "id": skill_id,
            "name": request.name,
            "description": request.description,
            "version": request.version,
            "source": request.source,
            "enabled": request.enabled,
            "installed_at": datetime.now().isoformat()
        }
        _write_skill(skill_id, skill)
        return {
            "success": True,
            "data": skill,
            "message": "技能已安装"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{skill_id}")
async def update_skill(skill_id: str, request: SkillUpdateRequest):
    try:
        if skill_id.startswith("opencode:"):
            raise HTTPException(status_code=400, detail="OpenCode 技能不支持更新")
        if skill_id.startswith("builtin:"):
            raise HTTPException(status_code=400, detail="内置技能不支持更新")
        path = _skill_path(skill_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="技能不存在")
        skill = _read_skill(path) or {}
        updates = request.model_dump(exclude_unset=True)
        skill.update(updates)
        _write_skill(skill_id, skill)
        return {
            "success": True,
            "data": skill,
            "message": "技能已更新"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{skill_id}")
async def delete_skill(skill_id: str):
    try:
        if skill_id.startswith("opencode:"):
            _delete_opencode_skill(skill_id)
            return {
                "success": True,
                "message": "OpenCode 技能已卸载"
            }
        if skill_id.startswith("builtin:"):
            raise HTTPException(status_code=400, detail="内置技能不支持卸载")
        if skill_id.startswith("custom:"):
            raise HTTPException(status_code=400, detail="外部目录技能不支持卸载，请在设置中移除对应目录")
        path = _skill_path(skill_id)
        if not path.exists():
            raise HTTPException(status_code=404, detail="技能不存在")
        path.unlink(missing_ok=True)
        return {
            "success": True,
            "message": "技能已卸载"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch-delete")
async def batch_delete_skills(request: BatchDeleteRequest):
    """批量删除/卸载技能。builtin 和 custom 技能会被跳过，其余逐一删除。"""
    results = {"success": [], "failed": [], "skipped": []}
    for skill_id in request.ids:
        try:
            if skill_id.startswith("builtin:") or skill_id.startswith("custom:"):
                results["skipped"].append(skill_id)
                continue
            if skill_id.startswith("opencode:"):
                _delete_opencode_skill(skill_id)
                results["success"].append(skill_id)
                continue
            path = _skill_path(skill_id)
            if not path.exists():
                results["failed"].append({"id": skill_id, "reason": "不存在"})
                continue
            path.unlink(missing_ok=True)
            results["success"].append(skill_id)
        except Exception as e:
            results["failed"].append({"id": skill_id, "reason": str(e)})
    return {
        "success": True,
        "data": results,
        "message": f"已删除 {len(results['success'])} 个，跳过 {len(results['skipped'])} 个只读技能，失败 {len(results['failed'])} 个"
    }


@router.post("/generate")
async def generate_skill(request: SkillGenerateRequest):
    """根据用户描述，调用 AI 生成 SKILL.md 内容并保存到 skills/ 目录。"""
    if not request.description.strip():
        raise HTTPException(status_code=400, detail="请提供技能描述")

    prompt = f"""请根据以下功能描述，生成一个规范的 SKILL.md 文件内容。

功能描述：{request.description}

要求：
1. 文件以 YAML front matter 开头（用 --- 包裹），包含 name（简短英文标识符，如 my_skill）和 description（中文描述）字段
2. 然后是 Markdown 正文，包含：技能概述、使用场景、主要功能列表、使用示例
3. name 只包含小写字母、数字和下划线，不超过 30 个字符
4. description 不超过 80 个字符

请直接输出 SKILL.md 文件内容，不要添加任何额外说明。"""

    try:
        client = OpenCodeClient(app_config.opencode.server_url)
        ok = await client.try_connect(attempts=3, delay=0.4, open_timeout=1.0)
        if not ok:
            raise HTTPException(status_code=503, detail="AI 服务连接失败，请确认 OpenCode 服务正在运行")

        result = await client.execute_task(prompt)
        await client.close()

        if not result.success or not result.content:
            raise HTTPException(status_code=500, detail="AI 生成失败，请重试")

        content = result.content.strip()
        # 提取 name 字段用于目录命名
        slug = None
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                front = content[3:end].strip()
                for line in front.splitlines():
                    if line.startswith("name:"):
                        slug = line[len("name:"):].strip().strip('"\'')
                        break

        if not slug:
            # 从描述生成 slug
            slug = re.sub(r"[^a-z0-9_]", "_", request.description[:20].lower().replace(" ", "_"))
            slug = re.sub(r"_+", "_", slug).strip("_") or "generated_skill"

        # 避免目录冲突
        skills_dir = _ensure_skills_dir()
        target_dir = skills_dir / slug
        counter = 1
        while target_dir.exists():
            target_dir = skills_dir / f"{slug}_{counter}"
            counter += 1

        target_dir.mkdir(parents=True, exist_ok=True)
        skill_md = target_dir / "SKILL.md"
        skill_md.write_text(content, encoding="utf-8")

        info = _read_skill_markdown(skill_md) or {}
        return {
            "success": True,
            "data": {
                "id": f"builtin:{target_dir.name}",
                "name": info.get("name") or target_dir.name,
                "description": info.get("description") or "",
                "source": "builtin",
                "enabled": True,
                "installed_at": datetime.now().isoformat()
            },
            "message": f"技能已生成并保存到 skills/{target_dir.name}/"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
