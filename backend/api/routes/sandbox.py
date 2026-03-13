"""
沙箱 API 路由
提供沙箱 VM 的管理接口。

端点：
  GET  /api/sandbox/status          — 运行时 & VM 状态
  POST /api/sandbox/prepare         — 触发运行时/镜像下载与检测
  POST /api/sandbox/install-qemu    — 自动下载并安装 QEMU
  GET  /api/sandbox/config          — 获取沙箱配置
  PATCH /api/sandbox/config         — 更新沙箱配置
  POST /api/sandbox/start           — 启动 VM
  POST /api/sandbox/stop            — 停止 VM
  POST /api/sandbox/test            — 沙箱冒烟测试
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
    memory_mb: Optional[int] = None
    startup_timeout: Optional[int] = None
    exec_timeout: Optional[int] = None
    snapshot_mode: Optional[bool] = None
    network_enabled: Optional[bool] = None
    auto_download: Optional[bool] = None
    image_url: Optional[str] = None
    runtime_url: Optional[str] = None
    image_path: Optional[str] = None
    runtime_binary: Optional[str] = None
    workspace_dir: Optional[str] = None
    ipc_dir: Optional[str] = None


class SandboxTestRequest(BaseModel):
    prompt: str = "echo hello from sandbox"


# ── 路由 ─────────────────────────────────────────────────────────────────────

@router.get("/status")
async def get_sandbox_status():
    """获取沙箱运行时 & VM 状态"""
    if sandbox_manager is None:
        return {
            "success": True,
            "data": {
                "state": "idle",
                "vm_running": False,
                "enabled": app_config.sandbox.enabled,
                "runtime_ready": False,
                "qemu_available": False,
                "image_available": False,
                "downloading": False,
                "download_progress": 0.0,
                "platform": _detect_platform(),
            }
        }
    status = sandbox_manager.get_status()
    status["enabled"] = app_config.sandbox.enabled
    return {"success": True, "data": status}


@router.post("/prepare")
async def prepare_sandbox():
    """
    触发运行时检测与镜像下载（如果尚未就绪）。
    这是一个异步触发操作；客户端应轮询 /status 来跟踪进度。
    """
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")

    import asyncio

    async def _do_prepare():
        try:
            # initialize() 是幂等的：会创建 _runtime（如果还没有）并调用 ensure_ready()
            await sandbox_manager.initialize()
        except Exception as e:
            logger.error(f"沙箱准备失败: {e}")

    asyncio.create_task(_do_prepare())
    return {"success": True, "message": "正在检测/下载沙箱运行时，请轮询 /api/sandbox/status 获取进度"}


@router.post("/install-qemu")
async def install_qemu():
    """
    自动下载并安装 QEMU：
    - Windows: 下载官方 NSIS installer → /S 静默安装（需要管理员权限）
    - macOS: brew install qemu
    - Linux: apt / dnf / pacman 安装

    这是一个异步触发操作；客户端应轮询 /status 中的
    installing_qemu / install_qemu_progress / install_qemu_error 字段来跟踪进度。
    """
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")

    # 确保 runtime 对象已创建
    await sandbox_manager.initialize()

    runtime = sandbox_manager._runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="沙箱运行时未初始化")

    if runtime.status.qemu_available:
        return {"success": True, "message": f"QEMU 已安装: {runtime.status.qemu_path}"}

    if runtime.status.installing_qemu:
        return {"success": True, "message": "QEMU 安装已在进行中，请轮询 /api/sandbox/status"}

    import asyncio

    async def _do_install():
        try:
            await runtime.install_qemu()
        except Exception as e:
            logger.error(f"QEMU 安装任务异常: {e}")

    asyncio.create_task(_do_install())
    return {"success": True, "message": "已触发 QEMU 安装，请轮询 /api/sandbox/status 获取进度"}


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
    app_config.sandbox = SandboxConfig(**current)
    try:
        save_config(app_config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存配置失败: {e}")

    logger.info(f"沙箱配置已更新: {updates}")
    return {"success": True, "data": app_config.sandbox.model_dump(), "message": "配置已保存"}


@router.post("/start")
async def start_sandbox_vm():
    """手动启动沙箱 VM"""
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")
    if not app_config.sandbox.enabled:
        raise HTTPException(status_code=400, detail="沙箱功能未启用，请先在配置中开启")

    ok = await sandbox_manager.start_vm()
    if not ok:
        raise HTTPException(status_code=500, detail="启动沙箱 VM 失败，请检查 QEMU 安装和镜像配置")
    return {"success": True, "message": "沙箱 VM 已启动"}


@router.post("/stop")
async def stop_sandbox_vm():
    """手动停止沙箱 VM"""
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")
    await sandbox_manager.stop_vm()
    return {"success": True, "message": "沙箱 VM 已停止"}


@router.post("/test")
async def test_sandbox(request: SandboxTestRequest):
    """
    在沙箱中运行冒烟测试任务。
    如果 VM 未运行则自动启动。
    """
    if sandbox_manager is None:
        raise HTTPException(status_code=503, detail="沙箱管理器未初始化")
    if not app_config.sandbox.enabled:
        raise HTTPException(status_code=400, detail="沙箱功能未启用")

    result = await sandbox_manager.execute(request.prompt)
    return {
        "success": result.success,
        "data": {
            "content": result.content,
            "error": result.error,
            "exit_code": result.exit_code,
            "execution_mode": result.execution_mode,
        },
        "message": "沙箱测试完成" if result.success else f"沙箱测试失败: {result.error}",
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
