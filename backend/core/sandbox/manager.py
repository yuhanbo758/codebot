"""
沙箱管理器
负责：
  - 生命周期：启动 / 停止 / 状态查询
  - 将任务请求分发到 VM（9p IPC 或 Windows serial bridge）
  - 技能文件同步到 VM
  - 环境变量 localhost → 10.0.2.2 重映射
  - 执行结果轮询

设计参考 LobsterAI coworkRunner.ts
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .runtime import SandboxRuntime
from .vm_runner import VmRunner


# ── 枚举 ─────────────────────────────────────────────────────────────────────

class ExecutionMode(str, Enum):
    AUTO = "auto"
    LOCAL = "local"
    SANDBOX = "sandbox"


class SandboxState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


# ── 执行结果 ──────────────────────────────────────────────────────────────────

@dataclass
class SandboxResult:
    success: bool
    content: str = ""
    error: str = ""
    exit_code: int = 0
    execution_mode: str = "sandbox"


# ── SandboxManager ────────────────────────────────────────────────────────────

class SandboxManager:
    """
    高层沙箱管理器。

    用法（由 main.py 生命周期初始化）::

        manager = SandboxManager(data_dir=settings.DATA_DIR, config=app_config.sandbox)
        await manager.initialize()

        # 在 chat 路由中：
        result = await manager.execute(task_prompt)

        # 关闭时：
        await manager.shutdown()
    """

    def __init__(self, data_dir: Path, config=None, skills_dir: Optional[Path] = None):
        self._data_dir = data_dir
        self._cfg = config
        self._skills_dir = skills_dir

        # 运行时 / 进程
        self._runtime: Optional[SandboxRuntime] = None
        self._runner: Optional[VmRunner] = None

        self.state = SandboxState.IDLE
        self._lock = asyncio.Lock()

        # IPC 目录
        self._ipc_dir: Path = self._resolve_ipc_dir()
        self._workspace_dir: Optional[Path] = self._resolve_workspace_dir()

    # ── 初始化 ────────────────────────────────────────────────────────────────

    async def initialize(self):
        """初始化运行时（检测 QEMU + 镜像），不自动启动 VM。可安全地重复调用。"""
        if self._runtime is None:
            self._runtime = SandboxRuntime(data_dir=self._data_dir, config=self._cfg)
        await self._runtime.ensure_ready()
        logger.info(
            f"沙箱运行时初始化完成: "
            f"qemu={self._runtime.status.qemu_available} "
            f"image={self._runtime.status.image_available}"
        )

    async def shutdown(self):
        """停止 VM 并清理资源。不竞争 _lock，直接强制停止。"""
        # 直接调用无锁版本，避免与 start_vm() 持有的锁死锁
        await self._stop_vm_nolock()
        # 额外扫描：清理可能的孤立 QEMU 进程
        self._kill_orphan_qemu_processes()

    # ── VM 生命周期 ───────────────────────────────────────────────────────────

    async def start_vm(self) -> bool:
        """启动沙箱 VM（幂等：已运行则直接返回 True）。"""
        async with self._lock:
            if self._runner and self._runner.running:
                return True
            if self.state == SandboxState.STARTING:
                return False

            if not self._runtime:
                logger.error("沙箱运行时未初始化，请先调用 initialize()")
                return False
            if not self._runtime.status.qemu_available:
                logger.error(
                    "QEMU 未安装或未找到，无法启动沙箱 VM。"
                    "请安装 qemu-system-x86_64 并确保其在 PATH 中，"
                    "或在「沙箱配置」→「QEMU 路径」中填写完整路径。"
                )
                return False
            if not self._runtime.status.image_available:
                err = self._runtime.status.download_error
                hint = f"（{err}）" if err else ""
                logger.error(
                    f"沙箱镜像不可用，无法启动 VM{hint}。"
                    "请在「沙箱配置」中填写「镜像下载 URL」并点击「准备沙箱」，"
                    "或将镜像文件手动放置到数据目录的 sandbox/ 子目录。"
                )
                return False

            self.state = SandboxState.STARTING
            startup_timeout = 60
            if self._cfg:
                startup_timeout = getattr(self._cfg, "startup_timeout", startup_timeout)

            self._runner = VmRunner(
                runtime=self._runtime,
                ipc_dir=self._ipc_dir,
                workspace_dir=self._workspace_dir,
                config=self._cfg,
            )

            ok = await self._runner.start(startup_timeout=startup_timeout)
            if ok:
                self.state = SandboxState.RUNNING
                await self._sync_skills()
                logger.info("沙箱 VM 启动成功")
            else:
                self.state = SandboxState.ERROR
                logger.error("沙箱 VM 启动失败")
            return ok

    async def stop_vm(self) -> None:
        """公开接口：加锁停止 VM（供 API 路由调用）。"""
        async with self._lock:
            await self._stop_vm_nolock()

    async def _stop_vm(self):
        """内部使用：加锁停止 VM。"""
        async with self._lock:
            await self._stop_vm_nolock()

    async def _stop_vm_nolock(self):
        """无锁停止 VM 核心逻辑，可由 shutdown() 直接调用以避免死锁。"""
        if self._runner:
            self.state = SandboxState.STOPPING
            await self._runner.stop()
            self._runner = None
        self.state = SandboxState.IDLE

    @staticmethod
    def _kill_orphan_qemu_processes():
        """使用 psutil 扫描并强制结束所有孤立的 qemu-system-x86_64 子进程。"""
        try:
            import psutil
            current_pid = os.getpid()
            for proc in psutil.process_iter(["pid", "name", "ppid"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    if "qemu-system" in name and proc.info.get("ppid") == current_pid:
                        logger.warning(f"强制终止孤立 QEMU 进程: pid={proc.pid}")
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except ImportError:
            pass  # psutil 未安装时跳过

    # ── 执行任务 ──────────────────────────────────────────────────────────────

    async def execute(
        self,
        prompt: str,
        env: Optional[Dict[str, str]] = None,
    ) -> SandboxResult:
        """
        在沙箱 VM 内执行任务。VM 必须已通过 start_vm() 启动，否则返回错误。

        :param prompt: 任务指令文本
        :param env:    额外环境变量（会被重映射 localhost → 10.0.2.2）
        :return: SandboxResult
        """
        exec_timeout = 300
        if self._cfg:
            exec_timeout = getattr(self._cfg, "exec_timeout", exec_timeout)

        # VM 必须已在运行，不自动启动（避免在未经用户确认的情况下拉起 QEMU）
        if not (self._runner and self._runner.running):
            return SandboxResult(
                success=False,
                error="沙箱 VM 未运行，请先在「设置 → 沙箱配置」中启动 VM",
            )

        # 重映射 env 中的 localhost → QEMU host 地址
        remapped_env = self._remap_env(env or {})

        payload = {
            "prompt": prompt,
            "env": remapped_env,
        }

        try:
            if SandboxRuntime.is_windows():
                return await self._execute_via_serial(payload, timeout=exec_timeout)
            else:
                return await self._execute_via_ipc(payload, timeout=exec_timeout)
        except Exception as e:
            logger.error(f"沙箱执行失败: {e}")
            return SandboxResult(success=False, error=str(e))

    # ── IPC 执行（9p，macOS/Linux） ───────────────────────────────────────────

    async def _execute_via_ipc(self, payload: dict, timeout: float) -> SandboxResult:
        if not self._runner:
            return SandboxResult(success=False, error="VM 未运行")

        self._runner.write_ipc_request(payload)
        resp = await self._runner.wait_for_ipc_response(timeout=timeout)

        if resp is None:
            return SandboxResult(success=False, error="沙箱执行超时（无响应）")

        return SandboxResult(
            success=resp.get("success", False),
            content=resp.get("content", ""),
            error=resp.get("error", ""),
            exit_code=int(resp.get("exit_code", 0)),
        )

    # ── Serial 执行（Windows） ────────────────────────────────────────────────

    async def _execute_via_serial(self, payload: dict, timeout: float) -> SandboxResult:
        if not self._runner or not self._runner.serial_bridge:
            return SandboxResult(success=False, error="VM 串口桥未就绪")

        bridge = self._runner.serial_bridge
        prompt_bytes = json.dumps(payload, ensure_ascii=False).encode()

        # 推送请求文件到 VM
        ok = await bridge.push_file("/tmp/codebot_request.json", prompt_bytes)
        if not ok:
            return SandboxResult(success=False, error="推送请求文件失败")

        # 触发执行（写触发文件）
        await bridge.push_file("/tmp/codebot_trigger", b"1")

        # 轮询响应文件
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            resp_bytes = await bridge.read_file("/tmp/codebot_response.json")
            if resp_bytes is not None:
                try:
                    resp = json.loads(resp_bytes.decode())
                    return SandboxResult(
                        success=resp.get("success", False),
                        content=resp.get("content", ""),
                        error=resp.get("error", ""),
                        exit_code=int(resp.get("exit_code", 0)),
                    )
                except Exception as e:
                    return SandboxResult(success=False, error=f"解析响应失败: {e}")
            await asyncio.sleep(0.5)

        return SandboxResult(success=False, error="沙箱执行超时（serial 模式）")

    # ── 技能同步 ──────────────────────────────────────────────────────────────

    async def _sync_skills(self):
        """将宿主机技能目录同步到 VM（9p 模式自动挂载，serial 模式逐文件推送）。"""
        if not self._skills_dir or not self._skills_dir.exists():
            return

        if SandboxRuntime.is_windows():
            await self._sync_skills_via_serial()
        else:
            # 9p 直接挂载，无需额外同步；如需软链或复制，可在此扩展
            logger.debug("技能目录通过 9p 挂载，无需额外同步")

    async def _sync_skills_via_serial(self):
        if not self._runner or not self._runner.serial_bridge:
            return
        bridge = self._runner.serial_bridge
        for skill_file in self._skills_dir.glob("*.json"):
            try:
                content = skill_file.read_bytes()
                guest_path = f"/opt/codebot/skills/{skill_file.name}"
                ok = await bridge.push_file(guest_path, content)
                if not ok:
                    logger.warning(f"推送技能文件失败: {skill_file.name}")
            except Exception as e:
                logger.warning(f"同步技能文件 {skill_file.name} 失败: {e}")

    # ── 环境变量重映射 ────────────────────────────────────────────────────────

    @staticmethod
    def _remap_env(env: Dict[str, str]) -> Dict[str, str]:
        """
        将 env 中所有指向 localhost / 127.0.0.1 的值替换为 QEMU 宿主机地址 10.0.2.2，
        使 VM 内进程能访问宿主机服务（如 OpenCode server）。
        """
        from .vm_runner import QEMU_HOST_ALIAS
        remapped = {}
        pattern = re.compile(r"(localhost|127\.0\.0\.1)", re.IGNORECASE)
        for k, v in env.items():
            remapped[k] = pattern.sub(QEMU_HOST_ALIAS, v)
        return remapped

    # ── 状态查询 ──────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        runtime_status = {}
        if self._runtime:
            s = self._runtime.status
            runtime_status = {
                "qemu_available": s.qemu_available,
                "qemu_path": s.qemu_path,
                "image_available": s.image_available,
                "image_path": s.image_path,
                "image_size_bytes": s.image_size_bytes,
                "downloading": s.downloading,
                "download_progress": s.download_progress,
                "download_error": s.download_error,
                "installing_qemu": s.installing_qemu,
                "install_qemu_progress": s.install_qemu_progress,
                "install_qemu_error": s.install_qemu_error,
                "runtime_ready": s.ready,
            }
        return {
            "state": self.state.value,
            "vm_running": bool(self._runner and self._runner.running),
            "vm_pid": self._runner.pid if self._runner else None,
            "ipc_dir": str(self._ipc_dir),
            "platform": SandboxRuntime.detect_platform(),
            "ipc_mode": "serial" if SandboxRuntime.is_windows() else "9p",
            **runtime_status,
        }

    # ── 辅助 ──────────────────────────────────────────────────────────────────

    def _resolve_ipc_dir(self) -> Path:
        if self._cfg:
            explicit = getattr(self._cfg, "ipc_dir", "") or ""
            if explicit:
                return Path(explicit)
        return self._data_dir / "sandbox" / "ipc"

    def _resolve_workspace_dir(self) -> Optional[Path]:
        if self._cfg:
            explicit = getattr(self._cfg, "workspace_dir", "") or ""
            if explicit:
                return Path(explicit)
        return self._data_dir / "sandbox" / "workspace"
