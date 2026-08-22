"""
通用配置 API（读写 data/config.json 中的 integration、skills 等配置段）
"""
from pathlib import Path
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal, Optional, List

from config import (
    app_config,
    save_config,
    SkillsConfig,
    CodexConfig,
    ObsidianConfig,
    ObsidianKnowledgeBase,
    settings,
    AppConfig,
    migrate_legacy_agent_config,
)
from utils.secrets import merge_masked_secrets, redact_secrets

router = APIRouter()


class IntegrationUpdateRequest(BaseModel):
    modelscope_api_key: Optional[str] = None


class SkillsConfigUpdateRequest(BaseModel):
    custom_skill_dirs: Optional[List[str]] = None


class CodexConfigUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    auto_start: Optional[bool] = None
    runtime_source: Optional[Literal["bundled", "custom"]] = None
    codex_bin: Optional[str] = None
    approval_policy: Optional[Literal["interactive", "auto_review", "deny_all"]] = None
    share_memory: Optional[bool] = None
    share_scheduler: Optional[bool] = None
    skill_dirs: Optional[List[str]] = None


class ObsidianKnowledgeBaseRequest(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    path: str
    description: Optional[str] = None
    enabled: Optional[bool] = True


class ObsidianConfigUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    cli_path: Optional[str] = None
    vault_path: Optional[str] = None
    knowledge_bases: Optional[List[ObsidianKnowledgeBaseRequest]] = None


class LoadConfigFromPathRequest(BaseModel):
    path: str


class GeneralConfigUpdateRequest(BaseModel):
    language: Optional[str] = None
    compact_mode: Optional[bool] = None
    opencode_cli_display: Optional[bool] = None
    link_open_mode: Optional[str] = None
    file_storage_path: Optional[str] = None
    file_search_dirs: Optional[List[str]] = None
    chat_default_model: Optional[str] = None
    growth_candidate_decision: Optional[bool] = None
    task_candidate_notification_enabled: Optional[bool] = None


@router.get("/file-info")
async def get_config_file_info():
    config_path = settings.DATA_DIR / "config.json"
    return {
        "success": True,
        "data": {
            "active_config_path": str(config_path),
            "exists": config_path.exists()
        }
    }


@router.post("/load-from-path")
async def load_config_from_path(request: LoadConfigFromPathRequest):
    try:
        source_path = Path(request.path).expanduser()
        if not source_path.is_absolute():
            source_path = source_path.resolve()
        if not source_path.exists() or not source_path.is_file():
            raise HTTPException(status_code=400, detail="配置文件不存在")
        if source_path.suffix.lower() != ".json":
            raise HTTPException(status_code=400, detail="仅支持 JSON 配置文件")

        with open(source_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data, _ = migrate_legacy_agent_config(data)
        loaded = AppConfig(**data)

        for key in loaded.model_fields.keys():
            setattr(app_config, key, getattr(loaded, key))

        save_config(app_config)
        target_path = settings.DATA_DIR / "config.json"

        return {
            "success": True,
            "data": {
                "loaded_from": str(source_path),
                "active_config_path": str(target_path)
            },
            "message": "配置已加载并应用"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/integration")
async def get_integration_config():
    """获取第三方集成配置"""
    return {
        "success": True,
        "data": redact_secrets(app_config.integration.model_dump())
    }


@router.patch("/integration")
async def update_integration_config(request: IntegrationUpdateRequest):
    """更新第三方集成配置"""
    try:
        updates = merge_masked_secrets(
            app_config.integration.model_dump(),
            request.model_dump(exclude_unset=True),
        )
        for k, v in updates.items():
            if hasattr(app_config.integration, k):
                setattr(app_config.integration, k, v)
        save_config(app_config)
        return {
            "success": True,
            "data": redact_secrets(app_config.integration.model_dump()),
            "message": "集成配置已保存"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/skills")
async def get_skills_config():
    """获取技能目录配置"""
    return {
        "success": True,
        "data": app_config.skills.model_dump()
    }


@router.put("/skills")
async def update_skills_config(request: SkillsConfigUpdateRequest):
    """更新技能目录配置"""
    try:
        updates = request.model_dump(exclude_unset=True)
        current = app_config.skills.model_dump()
        current.update(updates)
        app_config.skills = SkillsConfig(**current)
        save_config(app_config)
        return {
            "success": True,
            "data": app_config.skills.model_dump(),
            "message": "技能目录配置已保存"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _validate_abs_dir_list(values: Optional[List[str]], label: str) -> List[str]:
    result: List[str] = []
    for raw in values or []:
        value = (raw or "").strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise HTTPException(status_code=400, detail=f"{label} 必须是绝对路径: {value}")
        result.append(str(path))
    return result


@router.get("/codex")
async def get_codex_config():
    return {"success": True, "data": app_config.codex.model_dump()}


@router.patch("/codex")
async def update_codex_config(request: CodexConfigUpdateRequest):
    try:
        updates = request.model_dump(exclude_unset=True)
        if "codex_bin" in updates and updates["codex_bin"]:
            codex_bin = Path(updates["codex_bin"]).expanduser()
            if not codex_bin.is_absolute():
                raise HTTPException(status_code=400, detail="Codex 可执行文件必须使用绝对路径")
            updates["codex_bin"] = str(codex_bin)
        if "skill_dirs" in updates:
            updates["skill_dirs"] = _validate_abs_dir_list(updates.get("skill_dirs"), "Codex skill 目录")
        current = app_config.codex.model_dump()
        current.update(updates)
        app_config.codex = CodexConfig(**current)
        save_config(app_config)
        return {"success": True, "data": app_config.codex.model_dump(), "message": "Codex 配置已保存；运行时设置变更将在重启 Codex 后生效"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/obsidian")
async def get_obsidian_config():
    return {"success": True, "data": app_config.obsidian.model_dump()}


@router.patch("/obsidian")
async def update_obsidian_config(request: ObsidianConfigUpdateRequest):
    try:
        updates = request.model_dump(exclude_unset=True)
        if "vault_path" in updates and updates["vault_path"]:
            vault_path = Path(updates["vault_path"]).expanduser()
            if not vault_path.is_absolute():
                raise HTTPException(status_code=400, detail="Obsidian 库路径必须是绝对路径")
            updates["vault_path"] = str(vault_path)
        if "knowledge_bases" in updates:
            normalized = []
            for idx, item in enumerate(updates.get("knowledge_bases") or []):
                path = Path(item["path"]).expanduser()
                if not path.is_absolute():
                    raise HTTPException(status_code=400, detail=f"知识库路径必须是绝对路径: {item['path']}")
                name = (item.get("name") or path.name or f"知识库{idx + 1}").strip()
                kb_id = (item.get("id") or name or str(idx + 1)).strip()
                normalized.append(ObsidianKnowledgeBase(
                    id=kb_id,
                    name=name,
                    path=str(path),
                    description=(item.get("description") or "").strip(),
                    enabled=bool(item.get("enabled", True)),
                ).model_dump())
            updates["knowledge_bases"] = normalized
        current = app_config.obsidian.model_dump()
        current.update(updates)
        app_config.obsidian = ObsidianConfig(**current)
        save_config(app_config)
        return {"success": True, "data": app_config.obsidian.model_dump(), "message": "Obsidian 配置已保存"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/general")
async def get_general_config():
    """获取通用配置（浏览器设置、文件存储路径等）"""
    return {
        "success": True,
        "data": app_config.general.model_dump()
    }


@router.patch("/general")
async def update_general_config(request: GeneralConfigUpdateRequest):
    """更新通用配置"""
    try:
        updates = request.model_dump(exclude_unset=True)
        # 验证 language
        if "language" in updates:
            if updates["language"] not in ("zh-CN", "en-US"):
                raise HTTPException(status_code=400, detail="language 仅支持 'zh-CN' 或 'en-US'")
        # 验证 link_open_mode
        if "link_open_mode" in updates:
            if updates["link_open_mode"] not in ("system", "builtin"):
                raise HTTPException(status_code=400, detail="link_open_mode 仅支持 'system' 或 'builtin'")
        # 验证 file_storage_path
        if "file_storage_path" in updates and updates["file_storage_path"]:
            p = Path(updates["file_storage_path"])
            if not p.is_absolute():
                raise HTTPException(status_code=400, detail="文件存储路径必须是绝对路径")
        # 验证 file_search_dirs
        if "file_search_dirs" in updates and updates["file_search_dirs"]:
            for d in updates["file_search_dirs"]:
                if d and not Path(d).is_absolute():
                    raise HTTPException(status_code=400, detail=f"文件搜索目录必须是绝对路径: {d}")
        for k, v in updates.items():
            if hasattr(app_config.general, k):
                setattr(app_config.general, k, v)
        save_config(app_config)
        return {
            "success": True,
            "data": app_config.general.model_dump(),
            "message": "通用配置已保存"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
