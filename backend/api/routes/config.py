"""
通用配置 API（读写 data/config.json 中的 integration、skills 等配置段）
"""
from pathlib import Path
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from config import app_config, save_config, SkillsConfig, settings, AppConfig

router = APIRouter()


class IntegrationUpdateRequest(BaseModel):
    modelscope_api_key: Optional[str] = None


class SkillsConfigUpdateRequest(BaseModel):
    custom_skill_dirs: Optional[List[str]] = None


class LoadConfigFromPathRequest(BaseModel):
    path: str


class GeneralConfigUpdateRequest(BaseModel):
    link_open_mode: Optional[str] = None
    file_storage_path: Optional[str] = None


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
        "data": app_config.integration.model_dump()
    }


@router.patch("/integration")
async def update_integration_config(request: IntegrationUpdateRequest):
    """更新第三方集成配置"""
    try:
        updates = request.model_dump(exclude_unset=True)
        for k, v in updates.items():
            if hasattr(app_config.integration, k):
                setattr(app_config.integration, k, v)
        save_config(app_config)
        return {
            "success": True,
            "data": app_config.integration.model_dump(),
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
        # 验证 link_open_mode
        if "link_open_mode" in updates:
            if updates["link_open_mode"] not in ("system", "builtin"):
                raise HTTPException(status_code=400, detail="link_open_mode 仅支持 'system' 或 'builtin'")
        # 验证 file_storage_path
        if "file_storage_path" in updates and updates["file_storage_path"]:
            p = Path(updates["file_storage_path"])
            if not p.is_absolute():
                raise HTTPException(status_code=400, detail="文件存储路径必须是绝对路径")
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
