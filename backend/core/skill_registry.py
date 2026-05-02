"""
Unified skill discovery for Codebot.

Codebot owns two writable skill classes:
  - auto_generated: user/chat/organizer generated skills in settings.SKILLS_DIR/auto_*
  - builtin: seeded Codebot skills in settings.SKILLS_DIR/*

External skill stores are read-only from Codebot:
  - external: configured Hermes/OpenClaw/custom compatible directories
  - opencode: OpenCode CLI skills in ~/.agents/skills
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from loguru import logger

from config import app_config, settings


AUTO_GENERATED = "auto_generated"
BUILTIN = "builtin"
EXTERNAL = "external"
OPENCODE = "opencode"

SOURCE_LABELS = {
    AUTO_GENERATED: "自动生成",
    BUILTIN: "内置",
    EXTERNAL: "外部兼容",
    OPENCODE: "OpenCode",
}

SOURCE_PRIORITY = {
    AUTO_GENERATED: 10,
    BUILTIN: 20,
    EXTERNAL: 30,
    OPENCODE: 40,
}

WRITABLE_SOURCES = {AUTO_GENERATED, BUILTIN}


@dataclass
class SkillEntry:
    id: str
    slug: str
    name: str
    description: str
    source: str
    path: Path
    skill_md_path: Optional[Path] = None
    version: str = "1.0.0"
    enabled: bool = True
    installed_at: str = ""
    priority: int = 99
    writable: bool = False
    compatibility: List[str] = field(default_factory=list)
    source_label: str = ""
    source_dir: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    skill_md_content: str = ""

    def to_dict(self, include_content: bool = False) -> Dict[str, Any]:
        data = {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "source": self.source,
            "sourceLabel": self.source_label or SOURCE_LABELS.get(self.source, self.source),
            "source_label": self.source_label or SOURCE_LABELS.get(self.source, self.source),
            "priority": self.priority,
            "path": str(self.path),
            "skill_md_path": str(self.skill_md_path) if self.skill_md_path else "",
            "writable": self.writable,
            "enabled": self.enabled,
            "installed_at": self.installed_at,
            "compatibility": self.compatibility,
            "metadata": self.metadata,
        }
        if self.source_dir:
            data["source_dir"] = self.source_dir
        if include_content:
            data["skill_md_content"] = self.skill_md_content
        return data


def _slugify(value: str, fallback: str = "skill") -> str:
    text = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or fallback


def _quote_yaml(value: str) -> str:
    value = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _parse_scalar(value: str) -> Any:
    raw = (value or "").strip()
    if not raw:
        return ""
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        items = [part.strip().strip('"\'') for part in raw[1:-1].split(",")]
        return [item for item in items if item]
    if raw.lower() in {"true", "false"}:
        return raw.lower() == "true"
    return raw


def _parse_front_matter(content: str) -> Dict[str, Any]:
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    front = content[3:end].strip()
    data: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for raw_line in front.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_key:
            existing = data.setdefault(current_key, [])
            if not isinstance(existing, list):
                existing = [existing]
                data[current_key] = existing
            existing.append(line[4:].strip().strip('"\''))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "":
            data[key] = []
        else:
            data[key] = _parse_scalar(value)
    return data


def read_skill_markdown(path: Path) -> Optional[Dict[str, Any]]:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    front = _parse_front_matter(content)
    name = str(front.get("name") or front.get("title") or "").strip()
    slug = str(front.get("slug") or "").strip()
    description = str(front.get("description") or front.get("summary") or "").strip()
    version = str(front.get("version") or "1.0.0").strip() or "1.0.0"
    compatibility = front.get("compatibility") or front.get("compatible_with") or []
    if isinstance(compatibility, str):
        compatibility = [compatibility]
    if not isinstance(compatibility, list):
        compatibility = []

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not name:
        for line in lines:
            if line.startswith("#"):
                name = line.lstrip("#").strip()
                break
    if not description:
        body = content
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                body = content[end + 4:]
        for line in [line.strip() for line in body.splitlines() if line.strip()]:
            if line.startswith("#") or line == "---":
                continue
            description = line
            break

    return {
        "name": name or path.parent.name,
        "slug": slug or path.parent.name,
        "description": description,
        "version": version,
        "compatibility": [str(item) for item in compatibility if str(item).strip()],
        "metadata": front,
        "content": content,
    }


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    except Exception:
        return datetime.now().isoformat()


def _iter_skill_dirs(base_dir: Path, recursive: bool = False) -> Iterable[Path]:
    if not base_dir.exists() or not base_dir.is_dir():
        return []
    if not recursive:
        return [entry for entry in base_dir.iterdir() if entry.is_dir() and (entry / "SKILL.md").exists()]
    matches: List[Path] = []
    for skill_md in base_dir.glob("**/SKILL.md"):
        if any(part in {".git", "node_modules", "__pycache__"} for part in skill_md.parts):
            continue
        matches.append(skill_md.parent)
    return matches


class SkillRegistry:
    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or settings.SKILLS_DIR

    def list_skills(self, include_content: bool = False) -> List[Dict[str, Any]]:
        entries: List[SkillEntry] = []
        entries.extend(self._list_codebot_json_skills())
        entries.extend(self._list_codebot_dir_skills())
        entries.extend(self._list_external_skills())
        entries.extend(self._list_opencode_skills())
        entries = self._dedupe(entries)
        entries.sort(key=lambda item: (item.priority, item.name.lower(), item.slug.lower()))
        return [entry.to_dict(include_content=include_content) for entry in entries]

    def find(self, skill_id: str) -> Optional[Dict[str, Any]]:
        for item in self.list_skills(include_content=True):
            if item.get("id") == skill_id:
                return item
        return None

    def find_by_query(self, query: str, allow_opencode: bool = True) -> Optional[Dict[str, Any]]:
        query_norm = _normalize(query)
        if not query_norm:
            return None
        for item in self.list_skills(include_content=True):
            if not allow_opencode and item.get("source") == OPENCODE:
                continue
            candidates = [
                item.get("id", ""),
                item.get("slug", ""),
                item.get("name", ""),
                item.get("description", ""),
            ]
            if any(query_norm in _normalize(str(value)) or _normalize(str(value)) in query_norm for value in candidates if value):
                return item
        return None

    def read_content(self, skill_id: str) -> str:
        item = self.find(skill_id)
        if not item:
            raise FileNotFoundError(skill_id)
        skill_md_path = item.get("skill_md_path") or ""
        if not skill_md_path:
            return item.get("skill_md_content") or ""
        return Path(skill_md_path).read_text(encoding="utf-8")

    def write_content(self, skill_id: str, content: str) -> Dict[str, Any]:
        item = self.find(skill_id)
        if not item:
            raise FileNotFoundError(skill_id)
        if item.get("source") not in WRITABLE_SOURCES:
            raise PermissionError(f"Skill {skill_id} is read-only")
        skill_md_path = Path(item.get("skill_md_path") or "")
        if not skill_md_path:
            raise FileNotFoundError("SKILL.md")
        skill_md_path.parent.mkdir(parents=True, exist_ok=True)
        skill_md_path.write_text(content, encoding="utf-8")
        refreshed = read_skill_markdown(skill_md_path) or {}
        return {
            **item,
            "name": refreshed.get("name") or item.get("name"),
            "description": refreshed.get("description") or item.get("description"),
            "version": refreshed.get("version") or item.get("version"),
        }

    def delete_auto_skill(self, skill_id: str) -> bool:
        item = self.find(skill_id)
        if not item:
            raise FileNotFoundError(skill_id)
        if item.get("source") != AUTO_GENERATED:
            raise PermissionError("Only auto-generated skills can be deleted here")
        target = Path(item.get("path") or "")
        if target.exists() and target.is_dir():
            import shutil

            shutil.rmtree(target)
            return True
        return False

    def create_auto_skill(
        self,
        name: str,
        description: str,
        body: str,
        user_message: str = "",
        slug_hint: str = "",
    ) -> Dict[str, Any]:
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        slug_base = _slugify(slug_hint or name or description[:30], fallback="generated_skill")
        if not slug_base.startswith("auto_"):
            slug_base = f"auto_{slug_base}"
        slug = slug_base
        target_dir = self.skills_dir / slug
        counter = 1
        while target_dir.exists():
            slug = f"{slug_base}_{counter}"
            target_dir = self.skills_dir / slug
            counter += 1
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_md = target_dir / "SKILL.md"
        write_auto_skill_md(
            skill_md,
            name=name,
            description=description,
            body=body,
            user_message=user_message,
            slug=slug,
        )
        item = self.find(f"auto:{slug}")
        return item or {
            "id": f"auto:{slug}",
            "slug": slug,
            "name": name,
            "description": description,
            "source": AUTO_GENERATED,
            "sourceLabel": SOURCE_LABELS[AUTO_GENERATED],
            "path": str(target_dir),
            "skill_md_path": str(skill_md),
            "writable": True,
        }

    def _list_codebot_json_skills(self) -> List[SkillEntry]:
        entries: List[SkillEntry] = []
        if not self.skills_dir.exists():
            return entries
        for file_path in self.skills_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict) or not data.get("enabled", True):
                continue
            slug = _slugify(str(data.get("id") or file_path.stem), fallback=file_path.stem)
            entries.append(
                SkillEntry(
                    id=str(data.get("id") or file_path.stem),
                    slug=slug,
                    name=str(data.get("name") or slug),
                    description=str(data.get("description") or ""),
                    version=str(data.get("version") or "1.0.0"),
                    source=EXTERNAL,
                    source_label=SOURCE_LABELS[EXTERNAL],
                    priority=SOURCE_PRIORITY[EXTERNAL],
                    writable=True,
                    path=file_path,
                    enabled=True,
                    installed_at=str(data.get("installed_at") or _mtime_iso(file_path)),
                    compatibility=["codebot"],
                    metadata=data,
                )
            )
        return entries

    def _list_codebot_dir_skills(self) -> List[SkillEntry]:
        entries: List[SkillEntry] = []
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        for entry in _iter_skill_dirs(self.skills_dir):
            skill_md = entry / "SKILL.md"
            info = read_skill_markdown(skill_md) or {}
            is_auto = entry.name.startswith("auto_")
            source = AUTO_GENERATED if is_auto else BUILTIN
            compat = info.get("compatibility") or []
            if not compat:
                compat = ["codebot", "hermes-agent", "openclaw"] if is_auto else ["codebot"]
            entries.append(
                SkillEntry(
                    id=f"auto:{entry.name}" if is_auto else f"builtin:{entry.name}",
                    slug=entry.name,
                    name=str(info.get("name") or entry.name),
                    description=str(info.get("description") or ""),
                    version=str(info.get("version") or "1.0.0"),
                    source=source,
                    source_label=SOURCE_LABELS[source],
                    priority=SOURCE_PRIORITY[source],
                    writable=True,
                    path=entry,
                    skill_md_path=skill_md,
                    installed_at=_mtime_iso(skill_md),
                    compatibility=compat,
                    metadata=info.get("metadata") or {},
                    skill_md_content=str(info.get("content") or ""),
                )
            )
        return entries

    def _list_external_skills(self) -> List[SkillEntry]:
        entries: List[SkillEntry] = []
        custom_dirs = app_config.skills.custom_skill_dirs if hasattr(app_config, "skills") else []
        for index, dir_path_str in enumerate(custom_dirs or []):
            dir_path = Path(dir_path_str)
            if not dir_path.exists() or not dir_path.is_dir():
                continue
            for entry in _iter_skill_dirs(dir_path, recursive=True):
                skill_md = entry / "SKILL.md"
                info = read_skill_markdown(skill_md) or {}
                rel_name = entry.name
                slug = _slugify(str(info.get("slug") or rel_name), fallback=rel_name)
                entries.append(
                    SkillEntry(
                        id=f"external:{index}:{slug}",
                        slug=slug,
                        name=str(info.get("name") or rel_name),
                        description=str(info.get("description") or ""),
                        version=str(info.get("version") or "1.0.0"),
                        source=EXTERNAL,
                        source_label=SOURCE_LABELS[EXTERNAL],
                        priority=SOURCE_PRIORITY[EXTERNAL],
                        writable=False,
                        path=entry,
                        skill_md_path=skill_md,
                        source_dir=str(dir_path),
                        installed_at=_mtime_iso(skill_md),
                        compatibility=info.get("compatibility") or ["codebot", "hermes-agent", "openclaw"],
                        metadata=info.get("metadata") or {},
                        skill_md_content=str(info.get("content") or ""),
                    )
                )
        return entries

    def _list_opencode_skills(self) -> List[SkillEntry]:
        entries: List[SkillEntry] = []
        skills_dir = Path.home() / ".agents" / "skills"
        for entry in _iter_skill_dirs(skills_dir):
            skill_md = entry / "SKILL.md"
            info = read_skill_markdown(skill_md) or {}
            entries.append(
                SkillEntry(
                    id=f"opencode:{entry.name}",
                    slug=entry.name,
                    name=str(info.get("name") or entry.name),
                    description=str(info.get("description") or ""),
                    version=str(info.get("version") or "1.0.0"),
                    source=OPENCODE,
                    source_label=SOURCE_LABELS[OPENCODE],
                    priority=SOURCE_PRIORITY[OPENCODE],
                    writable=False,
                    path=entry,
                    skill_md_path=skill_md,
                    installed_at=_mtime_iso(skill_md),
                    compatibility=info.get("compatibility") or ["opencode", "codebot"],
                    metadata=info.get("metadata") or {},
                    skill_md_content=str(info.get("content") or ""),
                )
            )
        return entries

    def _dedupe(self, entries: List[SkillEntry]) -> List[SkillEntry]:
        result: List[SkillEntry] = []
        seen: set[str] = set()
        for entry in sorted(entries, key=lambda item: item.priority):
            key = _normalize(entry.slug or entry.name)
            if not key:
                key = entry.id
            if key in seen:
                logger.debug(f"[skills] skipped duplicate skill {entry.id}")
                continue
            seen.add(key)
            result.append(entry)
        return result


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower().replace("-", "_"))


def write_auto_skill_md(
    skill_md_path: Path,
    name: str,
    description: str,
    body: str,
    user_message: str = "",
    slug: str = "",
) -> None:
    content_body = (body or "").strip()
    if len(content_body) > 3000:
        content_body = content_body[:3000] + "\n\n..."
    now = datetime.now().isoformat()
    source_message = (user_message or "").strip()
    if len(source_message) > 600:
        source_message = source_message[:600] + "..."
    skill_content = f"""---
name: {_quote_yaml(name or slug or "自动生成技能")}
slug: {_quote_yaml(slug or skill_md_path.parent.name)}
description: {_quote_yaml((description or "")[:180])}
version: "1.0.0"
source: auto_generated
compatibility:
  - codebot
  - hermes-agent
  - openclaw
created_at: {_quote_yaml(now)}
---

# {name or slug or "自动生成技能"}

## Overview
This skill was generated by Codebot from a repeated or reusable workflow.

## Source Scenario
{source_message}

## Procedure
{content_body}
"""
    skill_md_path.parent.mkdir(parents=True, exist_ok=True)
    skill_md_path.write_text(skill_content, encoding="utf-8")


def get_skill_registry() -> SkillRegistry:
    return SkillRegistry()
