"""
记忆管理 API 路由
"""
from fastapi import APIRouter, HTTPException, Body, Query, UploadFile, File
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime

from config import settings, MemoryConfig
from core.memory_manager import MemoryManager

router = APIRouter()
memory_manager: Optional[MemoryManager] = None


class MemoryCreateRequest(BaseModel):
    """创建记忆请求"""
    content: str
    category: str = "habit"
    metadata: Dict = Field(default_factory=dict)


class MemoryConfigRequest(BaseModel):
    """记忆配置请求"""
    auto_cleanup_enabled: bool
    cleanup_days: int
    archive_enabled: bool
    archive_days: int
    vector_search_top_k: int
    similarity_threshold: float
    show_archived_in_search: bool = True
    organize_enabled: bool = False
    organize_time: str = "03:00"


@router.get("/memories")
async def list_memories(
    category: Optional[str] = None,
    archived: bool = False,
    limit: int = 100,
    offset: int = 0
):
    """获取记忆列表"""
    try:
        manager = _get_memory_manager()
        
        memories = await manager.get_memories(
            category=category,
            archived=archived,
            limit=limit,
            offset=offset
        )
        
        return {
            "success": True,
            "data": {
                "items": memories,
                "total": len(memories)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memories/archived")
async def list_archived_memories(
    limit: int = 100,
    offset: int = 0
):
    """获取归档记忆列表"""
    try:
        manager = _get_memory_manager()
        
        memories = await manager.get_memories(
            archived=True,
            limit=limit,
            offset=offset
        )
        
        return {
            "success": True,
            "data": {
                "items": memories,
                "total": len(memories)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memories")
async def create_memory(request: MemoryCreateRequest):
    """创建记忆"""
    try:
        manager = _get_memory_manager()
        
        await manager.save_long_term_memory(
            content=request.content,
            category=request.category,
            metadata=request.metadata
        )
        
        return {
            "success": True,
            "message": "记忆已保存"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memories/search")
async def search_memories(
    query: str,
    top_k: int = 5,
    category: Optional[str] = None,
    include_archived: bool = False
):
    """搜索记忆"""
    try:
        manager = _get_memory_manager()
        
        memories = await manager.search_memories(
            query=query,
            top_k=top_k,
            category=category,
            include_archived=include_archived
        )
        
        return {
            "success": True,
            "data": memories
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memories/{memory_id}/archive")
async def archive_memory(memory_id: int):
    """归档记忆"""
    try:
        manager = _get_memory_manager()
        await manager.archive_memory(memory_id)
        
        return {
            "success": True,
            "message": "记忆已归档"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memories/{memory_id}/restore")
async def restore_memory(memory_id: int):
    """恢复记忆"""
    try:
        manager = _get_memory_manager()
        await manager.restore_memory(memory_id)
        
        return {
            "success": True,
            "message": "记忆已恢复"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: int):
    """删除记忆"""
    try:
        manager = _get_memory_manager()
        await manager.delete_memory(memory_id)
        
        return {
            "success": True,
            "message": "记忆已删除"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/export")
async def export_memories():
    """导出记忆"""
    try:
        manager = _get_memory_manager()
        zip_path = await manager.export_memories()
        
        return {
            "success": True,
            "data": {"path": zip_path},
            "message": "记忆导出成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import")
async def import_memories(file: UploadFile = File(...)):
    """导入记忆"""
    try:
        import tempfile
        import shutil
        
        # 保存上传的文件
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = tmp.name
        
        # 导入记忆
        manager = _get_memory_manager()
        await manager.import_memories(tmp_path, restore=True)
        
        return {
            "success": True,
            "message": "记忆导入成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cleanup")
async def cleanup_memories(
    days: int = Body(180, embed=True),
    dry_run: bool = Body(False, embed=True),
    cleanup_archived_memories: bool = Body(True, embed=True)
):
    """清理旧记忆

    - **cleanup_archived_memories**: 是否同时删除超期的已归档长期记忆（活跃记忆永不删除，默认 True）
    """
    try:
        manager = _get_memory_manager()
        deleted = await manager.cleanup_old_memories(
            cleanup_archived_memories=cleanup_archived_memories
        )

        return {
            "success": True,
            "data": {"deleted_count": deleted},
            "message": f"清理了 {deleted} 条旧数据"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def get_memory_config():
    """获取记忆配置"""
    try:
        from config import app_config
        
        return {
            "success": True,
            "data": app_config.memory.model_dump()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_memory_config(request: MemoryConfigRequest):
    """更新记忆配置"""
    try:
        from config import app_config, save_config
        
        app_config.memory = MemoryConfig(
            auto_cleanup_enabled=request.auto_cleanup_enabled,
            cleanup_days=request.cleanup_days,
            archive_enabled=request.archive_enabled,
            archive_days=request.archive_days,
            vector_search_top_k=request.vector_search_top_k,
            similarity_threshold=request.similarity_threshold,
            show_archived_in_search=request.show_archived_in_search,
            organize_enabled=request.organize_enabled,
            organize_time=request.organize_time,
            organize_last_run=app_config.memory.organize_last_run,  # 保持不变
        )
        
        save_config(app_config)
        
        return {
            "success": True,
            "message": "配置已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_memory_manager() -> MemoryManager:
    global memory_manager
    if memory_manager is None:
        memory_manager = MemoryManager()
    return memory_manager


@router.get("/hints")
async def get_memory_hints(
    query: str = Query(..., description="当前用户输入，用于检索相关记忆"),
    top_k: int = Query(5, ge=1, le=20, description="最多返回条数"),
):
    """
    根据用户当前输入，返回最相关的记忆提示。
    前端可在发送消息前调用，把命中的记忆气泡显示给用户。
    """
    try:
        manager = _get_memory_manager()
        hints: List[Dict] = []
        seen_contents: set = set()

        # 1. 精确事实检索
        try:
            facts = await manager.search_facts(query, top_k=top_k, include_archived=False)
            for item in facts:
                content = str(item.get("content") or "").strip()
                if content and content not in seen_contents:
                    seen_contents.add(content)
                    hints.append({
                        "content": content,
                        "category": "fact",
                        "category_label": "事实",
                        "score": round(1 - float(item.get("distance", 0.5)), 3),
                    })
        except Exception:
            pass

        # 2. 向量语义检索（长期记忆）
        try:
            memories = await manager.search_memories(query, top_k=top_k, include_archived=False)
            for item in memories:
                content = str(item.get("content") or "").strip()
                cat = str(item.get("category") or "note")
                if content and content not in seen_contents:
                    seen_contents.add(content)
                    from core.memory_extractor import MEMORY_CATEGORIES
                    hints.append({
                        "content": content,
                        "category": cat,
                        "category_label": MEMORY_CATEGORIES.get(cat, cat),
                        "score": round(1 - float(item.get("distance", 0.5)), 3),
                    })
        except Exception:
            pass

        # 按相关性降序排列，取前 top_k 条
        hints.sort(key=lambda x: x["score"], reverse=True)
        hints = hints[:top_k]

        return {
            "success": True,
            "data": hints,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 记忆整理端点 ──────────────────────────────────────────────────────────────

@router.post("/organize")
async def trigger_organize():
    """
    手动触发一次记忆整理。
    整理在后台异步执行，立即返回（通过 /organize/status 查询进度）。
    """
    import asyncio
    from core.memory_organizer import organize_memories, _RUNNING

    if _RUNNING:
        return {
            "success": False,
            "message": "整理任务已在运行中，请稍后再试"
        }

    # 拿到 opencode_ws（从 main 模块）
    try:
        import main as _main
        ws = getattr(_main, "opencode_ws", None)
    except Exception:
        ws = None

    manager = _get_memory_manager()
    asyncio.create_task(organize_memories(manager, ws))

    return {
        "success": True,
        "message": "记忆整理已在后台启动"
    }


@router.get("/organize/status")
async def organize_status():
    """查询整理任务状态及上次运行信息。"""
    try:
        from config import app_config
        from core.memory_organizer import _RUNNING

        mem_cfg = app_config.memory
        return {
            "success": True,
            "data": {
                "running": _RUNNING,
                "organize_enabled": mem_cfg.organize_enabled,
                "organize_time": mem_cfg.organize_time,
                "organize_last_run": mem_cfg.organize_last_run,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
