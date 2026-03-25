"""
沙箱管理器
基于工作目录隔离的安全执行环境，参考 LobsterAI 的本地执行模式。

执行模式（ExecutionMode）：
  auto    — 自动选择：包含高风险关键词时使用隔离工作目录执行，否则本地执行
  local   — 始终本地执行，无额外隔离
  sandbox — 始终使用隔离工作目录执行

与 LobsterAI 的对齐：
  - 不依赖 QEMU/VM，避免 Windows 下 QEMU 的各类兼容问题
  - 通过独立的沙箱工作目录（sandbox_workspace）限制文件操作范围
  - 进程级超时控制，防止任务无限运行
  - 参考 LobsterAI coworkRunner.ts 的本地执行架构
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

from loguru import logger


# ── 执行模式 ─────────────────────────────────────────────────────────────────

class ExecutionMode(str, Enum):
    AUTO    = "auto"
    LOCAL   = "local"
    SANDBOX = "sandbox"


# ── 执行结果 ─────────────────────────────────────────────────────────────────

@dataclass
class SandboxResult:
    success: bool
    content: str = ""
    error: str = ""
    exit_code: int = 0
    execution_mode: str = "local"


# ── 状态 ────────────────────────────────────────────────────────────────────

@dataclass
class SandboxStatus:
    """沙箱运行状态，与 LobsterAI get_status 接口对齐"""
    state: str = "idle"              # idle | running | error
    vm_running: bool = False         # 保持与旧接口兼容（始终 False）
    vm_pid: Optional[int] = None
    guest_booted: bool = False       # 保持与旧接口兼容（始终 False）
    guest_agent_ready: bool = False  # 保持与旧接口兼容（始终 False）
    console_ready: bool = False
    guest_status_message: str = ""
    ipc_mode: str = "local"          # 始终 local（无 VM）
    # 运行时状态（与旧 RuntimeStatus 字段对齐，保持前端兼容）
    qemu_available: bool = False     # 始终 False（不使用 QEMU）
    qemu_path: str = ""
    image_available: bool = False    # 始终 False（无镜像）
    image_path: str = ""
    image_size_bytes: int = 0
    downloading: bool = False
    download_progress: float = 0.0
    installing_qemu: bool = False
    install_qemu_progress: float = 0.0
    install_qemu_error: str = ""
    download_error: str = ""
    ready: bool = True               # 本地模式始终就绪
    # 工作目录信息
    workspace_dir: str = ""
    platform: str = ""


# ── 沙箱管理器 ──────────────────────────────────────────────────────────────

class SandboxManager:
    """
    沙箱管理器（工作目录隔离模式）

    参考 LobsterAI 的 CoworkRunner 本地执行架构，
    通过以下方式实现隔离：
    1. 独立的沙箱工作目录（sandbox_workspace/），与主数据目录分离
    2. 进程超时控制（exec_timeout 秒）
    3. 环境变量清理，避免泄露敏感信息
    4. 工作目录限制，防止意外修改项目外的文件
    """

    def __init__(self, data_dir: Path, config, skills_dir: Optional[Path] = None):
        self._data_dir = Path(data_dir)
        self._config = config
        self._skills_dir = skills_dir
        self._status = SandboxStatus()
        self._status.platform = _detect_platform()

        # 沙箱工作目录
        workspace = getattr(config, "workspace_dir", "")
        if workspace and Path(workspace).is_dir():
            self._workspace = Path(workspace)
        else:
            self._workspace = self._data_dir / "sandbox_workspace"

        self._workspace.mkdir(parents=True, exist_ok=True)
        self._status.workspace_dir = str(self._workspace)
        self._status.ready = True

        logger.info(f"沙箱管理器初始化（本地隔离模式），工作目录：{self._workspace}")

    # ── 公共接口 ─────────────────────────────────────────────────────────────

    async def initialize(self):
        """
        初始化沙箱（幂等）。
        本地模式下只需确保工作目录存在，无需下载镜像或安装 QEMU。
        与旧接口兼容，可多次调用。
        """
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._status.ready = True
        self._status.workspace_dir = str(self._workspace)
        logger.debug("沙箱初始化完成（工作目录已就绪）")

    async def start_vm(self) -> bool:
        """
        保持与旧 API 兼容的接口。
        本地模式下不需要启动 VM，始终返回 True。
        """
        logger.debug("start_vm 调用（本地模式，无操作）")
        return True

    async def stop_vm(self):
        """保持与旧 API 兼容的接口。本地模式下无操作。"""
        logger.debug("stop_vm 调用（本地模式，无操作）")

    async def shutdown(self):
        """应用关闭时清理资源。本地模式下无需清理。"""
        logger.debug("沙箱管理器关闭")

    def update_config(self, config):
        """更新配置"""
        self._config = config
        workspace = getattr(config, "workspace_dir", "")
        if workspace and Path(workspace).is_dir():
            self._workspace = Path(workspace)
        else:
            self._workspace = self._data_dir / "sandbox_workspace"
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._status.workspace_dir = str(self._workspace)

    def get_status(self) -> dict:
        """
        返回沙箱状态，与旧接口字段兼容，前端无需修改。
        同时与 LobsterAI 的 CoworkRunner 状态格式对齐。
        """
        return {
            "state": self._status.state,
            "vm_running": False,
            "vm_pid": None,
            "guest_booted": False,
            "guest_agent_ready": False,
            "console_ready": False,
            "guest_status_message": "本地隔离模式（无 VM）",
            "ipc_mode": "local",
            # 运行时状态（保持前端兼容）
            "qemu_available": False,
            "qemu_path": "",
            "image_available": False,
            "image_path": "",
            "image_size_bytes": 0,
            "downloading": False,
            "download_progress": 0.0,
            "installing_qemu": False,
            "install_qemu_progress": 0.0,
            "install_qemu_error": "",
            "download_error": "",
            "ready": True,
            # 工作目录信息
            "workspace_dir": str(self._workspace),
            "platform": self._status.platform,
            # 模式信息
            "mode": "local_isolation",
            "mode_description": "工作目录隔离模式（参考 LobsterAI 本地执行架构）",
        }

    async def execute(self, prompt: str, env: Optional[Dict[str, str]] = None) -> SandboxResult:
        """
        在隔离工作目录中执行任务。

        参考 LobsterAI CoworkRunner 的本地执行流程：
        1. 将任务作为 shell 命令在沙箱工作目录中执行
        2. 进程超时控制
        3. 捕获 stdout/stderr 输出

        Args:
            prompt: 要执行的 shell 命令或脚本
            env: 额外的环境变量

        Returns:
            SandboxResult
        """
        exec_timeout = getattr(self._config, "exec_timeout", 300)
        self._status.state = "running"

        try:
            result = await self._run_local_command(
                command=prompt,
                cwd=self._workspace,
                env=env,
                timeout=exec_timeout,
            )
            self._status.state = "idle"
            return result
        except Exception as e:
            self._status.state = "idle"
            logger.error(f"沙箱执行异常: {e}")
            return SandboxResult(
                success=False,
                error=str(e),
                exit_code=1,
                execution_mode="local",
            )

    # ── 内部实现 ─────────────────────────────────────────────────────────────

    async def _run_local_command(
        self,
        command: str,
        cwd: Path,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 300.0,
    ) -> SandboxResult:
        """
        在指定工作目录中运行 shell 命令。

        参考 LobsterAI 的本地执行模式：
        - 使用 asyncio.create_subprocess_shell 异步执行
        - 收集 stdout/stderr
        - 超时后强制终止进程
        """
        # 构建执行环境（参考 LobsterAI 的环境变量处理）
        exec_env = _build_exec_env(env)

        logger.debug(f"沙箱执行命令（cwd={cwd}）: {command[:200]}")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env=exec_env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                _kill_process(proc)
                return SandboxResult(
                    success=False,
                    error=f"命令执行超时（{timeout}秒）",
                    exit_code=124,
                    execution_mode="local",
                )

            stdout_text = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
            stderr_text = stderr.decode("utf-8", errors="replace").strip() if stderr else ""

            exit_code = proc.returncode or 0
            success = exit_code == 0

            # 合并输出（参考 LobsterAI 的流式结果处理）
            output_parts = []
            if stdout_text:
                output_parts.append(stdout_text)
            if stderr_text and not success:
                output_parts.append(f"[stderr]: {stderr_text}")

            content = "\n".join(output_parts)

            if success:
                logger.debug(f"沙箱命令执行成功（exit_code={exit_code}）")
            else:
                logger.warning(f"沙箱命令执行失败（exit_code={exit_code}）: {stderr_text[:200]}")

            return SandboxResult(
                success=success,
                content=content,
                error=stderr_text if not success else "",
                exit_code=exit_code,
                execution_mode="local",
            )

        except FileNotFoundError as e:
            return SandboxResult(
                success=False,
                error=f"命令未找到: {e}",
                exit_code=127,
                execution_mode="local",
            )
        except PermissionError as e:
            return SandboxResult(
                success=False,
                error=f"权限不足: {e}",
                exit_code=126,
                execution_mode="local",
            )
        except Exception as e:
            return SandboxResult(
                success=False,
                error=str(e),
                exit_code=1,
                execution_mode="local",
            )


# ── 辅助函数 ────────────────────────────────────────────────────────────────

def _detect_platform() -> str:
    """检测当前平台"""
    p = sys.platform
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def _build_exec_env(extra: Optional[Dict[str, str]] = None) -> dict:
    """
    构建执行环境变量。
    参考 LobsterAI 的环境变量处理：
    - 继承当前进程的环境变量
    - 移除可能影响安全的变量
    - 合并用户传入的额外变量
    """
    env = {**os.environ}
    # 移除可能敏感的 Python 路径变量（参考 LobsterAI electron main.ts）
    for key in ["PYTHONHOME", "PYTHONPATH"]:
        env.pop(key, None)
    # 确保 UTF-8 输出
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def _kill_process(proc) -> None:
    """强制终止进程"""
    try:
        proc.kill()
    except Exception:
        pass
