"""
沙箱 API 路由
提供沙箱（工作目录隔离模式）的管理接口。

端点：
  GET  /api/sandbox/status          — 运行状态
  POST /api/sandbox/prepare         — 初始化沙箱工作目录
  GET  /api/sandbox/config          — 获取沙箱配置
  PATCH /api/sandbox/config         — 更新沙箱配置
  POST /api/sandbox/start           — 兼容接口（本地模式无操作）
  POST /api/sandbox/stop            — 兼容接口（本地模式无操作）
  POST /api/sandbox/test            — 沙箱冒烟测试
  POST /api/sandbox/install-qemu    — 兼容接口（本地模式不需要 QEMU）
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from config import app_config, save_config

router = APIRouter()

# 由 main.py lifespan 注入
sandbox_manager = None  # type: Optional[object]


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────

class SandboxConfigPatch(BaseModel):
    execution_mode: Optional[str] = None
    enabled: Optional[bool] = None
    exec_timeout: Optional[int] = None
    network_enabled: Optional[bool] = None
    workspace_dir: Optional[str] = None
    # 以下字段保留兼容性，但本地模式下无效
    memory_mb: Optional[int] = None
    startup_timeout: Optional[int] = None
    snapshot_mode: Optional[bool] = None
    auto_download: Optional[bool] = None
    image_url: Optional[str] = None
    runtime_url: Optional[str] = None
    image_path: Optional[str] = None
    runtime_binary: Optional[str] = None
    ipc_dir: Optional[str] = None


class SandboxTestRequest(BaseModel):
    prompt: str = "echo hello from sandbox"


# ── 路由 ─────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_sandbox_status():
    """获取沙箱状态"""
    if sandbox_manager is None:
        return {
            "success": True,
            "data": {
                "state": "idle",
                "vm_running": False,
                "enabled": app_config.sandbox.enabled,
                "runtime_ready": True,
                "qemu_available": False,
                "image_available": False,
                "downloading": False,
                "download_progress": 0.0,
                "ready": True,
                "mode": "local_isolation",
                "mode_description": "工作目录隔离模式（参考 LobsterAI 本地执行架构）",
                "platform": _detect_platform(),
            }
        }
    status = sandbox_manager.get_status()
    status["enabled"] = app_config.sandbox.enabled
    return {"success": True, "data": status}


@router.post("/prepare")
async def prepare_sandbox():
    """
    初始化沙箱工作目录（幂等操作）。
    本地模式下只需确保工作目录存在。
    """
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")

    import asyncio

    async def _do_prepare():
        try:
            await sandbox_manager.initialize()
        except Exception as e:
            logger.error(f"沙箱初始化失败: {e}")

    asyncio.create_task(_do_prepare())
    return {"success": True, "message": "沙箱工作目录已就绪（本地隔离模式，无需下载）"}


@router.post("/install-qemu")
async def install_qemu():
    """
    兼容接口。本地隔离模式不需要 QEMU，直接返回成功。
    保留此端点以兼容旧版前端。
    """
    return {
        "success": True,
        "message": "当前使用工作目录隔离模式，无需安装 QEMU。"
    }


@router.get("/config")
async def get_sandbox_config():
    """获取当前沙箱配置"""
    return {"success": True, "data": app_config.sandbox.model_dump()}


@router.patch("/config")
async def update_sandbox_config(patch: SandboxConfigPatch):
    """更新沙箱配置（部分更新）"""
    updates = patch.model_dump(exclude_none=True)
    if not updates:
        return {"success": True, "data": app_config.sandbox.model_dump(), "message": "无变更"}

    current = app_config.sandbox.model_dump()
    current.update(updates)
    from config import SandboxConfig
    validated_config = SandboxConfig(**current)
    for key, value in validated_config.model_dump().items():
        setattr(app_config.sandbox, key, value)
    if sandbox_manager is not None:
        sandbox_manager.update_config(app_config.sandbox)
    try:
        save_config(app_config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存配置失败: {e}")

    if sandbox_manager is not None:
        # 本地模式下只需重新初始化工作目录
        await sandbox_manager.initialize()

    logger.info(f"沙箱配置已更新: {updates}")
    return {"success": True, "data": app_config.sandbox.model_dump(), "message": "配置已保存"}


@router.post("/start")
async def start_sandbox_vm():
    """
    兼容接口。本地隔离模式不需要启动 VM。
    保留此端点以兼容旧版前端。
    """
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")
    # 本地模式下确保工作目录存在即可
    await sandbox_manager.initialize()
    return {"success": True, "message": "沙箱已就绪（工作目录隔离模式）"}


@router.post("/stop")
async def stop_sandbox_vm():
    """
    兼容接口。本地隔离模式不需要停止 VM。
    保留此端点以兼容旧版前端。
    """
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")
    await sandbox_manager.stop_vm()
    return {"success": True, "message": "沙箱已停止（本地模式）"}


@router.post("/test")
async def test_sandbox(request: SandboxTestRequest):
    """
    沙箱冒烟测试。
    在隔离工作目录中执行测试命令并验证结果。
    参考 LobsterAI 的 CoworkRunner 测试流程。
    """
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")
    if not app_config.sandbox.enabled:
        raise HTTPException(status_code=400, detail="沙箱功能未启用，请先在设置中开启")

    try:
        result = await sandbox_manager.execute(request.prompt)
        if result.success:
            content = result.content or "命令执行成功"
            return {
                "success": True,
                "data": {
                    "content": content,
                    "error": "",
                    "exit_code": result.exit_code,
                    "execution_mode": result.execution_mode,
                },
                "message": f"沙箱冒烟测试通过：{content}",
            }

        return {
            "success": False,
            "data": {
                "content": result.content,
                "error": result.error,
                "exit_code": result.exit_code,
                "execution_mode": result.execution_mode,
            },
            "message": f"沙箱测试失败：{result.error or '未返回有效结果'}",
        }
    except Exception as e:
        logger.error(f"沙箱冒烟测试异常: {e}")
        return {
            "success": False,
            "data": {
                "content": "",
                "error": str(e),
                "exit_code": 1,
                "execution_mode": "local",
            },
            "message": f"沙箱测试异常: {e}",
        }


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _detect_platform() -> str:
    import sys
    p = sys.platform
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"
