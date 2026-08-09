"""
Codebot 隔离执行管理器。

执行模式（ExecutionMode）：
  auto    — 自动选择：包含高风险关键词时使用隔离工作目录执行，否则本地执行
  local   — 始终本地执行，无额外隔离
  sandbox — 使用 Windows Sandbox 执行；运行时不可用时拒绝执行，不降级

与 LobsterAI 的对齐：
  - 不依赖 QEMU/VM，避免 Windows 下 QEMU 的各类兼容问题
  - 通过独立的沙箱工作目录（sandbox_workspace）限制文件操作范围
  - 进程级超时控制，防止任务无限运行
  - 参考 LobsterAI coworkRunner.ts 的本地执行架构
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, Optional
from xml.sax.saxutils import escape as xml_escape

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
    隔离执行管理器。

    提供两类明确区分的执行边界：
    1. 用户显式选择的 Windows Sandbox 虚拟化隔离后端
    2. 仅用于可信命令的宿主机本地工作目录模式

    默认不选择、不探测或启动 Windows Sandbox。需要强隔离但后端未配置或
    不可用时失败关闭，绝不自动降级到宿主机。
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
        self._current_proc: Optional[asyncio.subprocess.Process] = None
        self._proc_lock = asyncio.Lock()
        self._isolation_lock = asyncio.Lock()

        logger.info(f"隔离执行管理器初始化，工作目录：{self._workspace}")

    # ── 公共接口 ─────────────────────────────────────────────────────────────

    async def initialize(self):
        """
        初始化共享工作目录（幂等）。

        该操作不会探测、安装或启动任何强隔离后端；运行时探测只发生在用户
        已明确选择 Windows Sandbox 且查询状态或实际执行命令时。
        """
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._status.ready = True
        self._status.workspace_dir = str(self._workspace)
        logger.debug("沙箱初始化完成（工作目录已就绪）")

    async def start_vm(self) -> bool:
        """
        保持与旧 API 兼容的接口。
        Windows Sandbox 按命令一次性启动，不维护常驻 VM，因此这里只返回工作目录状态。
        """
        logger.debug("start_vm 调用（本地模式，无操作）")
        return True

    async def stop_vm(self):
        """停止当前隔离执行实例。"""
        async with self._proc_lock:
            proc = self._current_proc
            self._current_proc = None
        if proc and proc.returncode is None:
            _kill_process_tree(proc)
        self._status.state = "idle"

    async def shutdown(self):
        """应用关闭时清理资源。"""
        async with self._proc_lock:
            proc = self._current_proc
            self._current_proc = None
        if proc and proc.returncode is None:
            _kill_process_tree(proc)
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
        backend = str(getattr(self._config, "isolation_backend", "none") or "none")
        backend_selected = backend == "windows_sandbox"
        # 只有用户显式选择 Windows Sandbox 后才探测系统能力，避免把它变成隐式依赖。
        runtime = self._resolve_windows_sandbox() if backend_selected else None
        isolated = bool(runtime)
        if not backend_selected:
            guest_status_message = "未选择强隔离后端"
            mode = "not_configured"
            mode_description = "未选择强隔离后端；Windows Sandbox 是可选项，默认不会探测或启动"
        elif isolated:
            guest_status_message = "Windows Sandbox 可用"
            mode = "windows_sandbox"
            mode_description = "已选择 Windows Sandbox 虚拟化隔离"
        else:
            guest_status_message = "Windows Sandbox 未安装或未启用"
            mode = "isolation_unavailable"
            mode_description = "已选择 Windows Sandbox，但运行时不可用；需要隔离的任务将被拒绝"
        return {
            "state": self._status.state,
            "vm_running": False,
            "vm_pid": None,
            "guest_booted": False,
            "guest_agent_ready": False,
            "console_ready": False,
            "guest_status_message": guest_status_message,
            "ipc_mode": "mapped_folder" if isolated else "unavailable",
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
            "ready": isolated,
            "runtime_ready": isolated,
            "runtime_path": runtime or "",
            "isolation_backend": backend,
            "backend_selected": backend_selected,
            # 工作目录信息
            "workspace_dir": str(self._workspace),
            "platform": self._status.platform,
            # 模式信息
            "mode": mode,
            "mode_description": mode_description,
            "network_isolated": bool(isolated and not getattr(self._config, "network_enabled", False)),
            "supports_agent_execution": False,
        }

    @property
    def workspace_dir(self) -> str:
        """返回 OpenCode 可使用的隔离工作目录。"""
        return str(self._workspace)

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
            mode = str(getattr(self._config, "execution_mode", "auto") or "auto")
            if mode != ExecutionMode.LOCAL.value:
                backend = str(getattr(self._config, "isolation_backend", "none") or "none")
                if backend == "none":
                    self._status.state = "idle"
                    return SandboxResult(
                        success=False,
                        error="未选择强隔离后端；Windows Sandbox 是可选项。为避免在宿主机运行不可信命令，本次执行已拒绝",
                        exit_code=503,
                        execution_mode="isolation_not_configured",
                    )
                if backend != "windows_sandbox":
                    self._status.state = "idle"
                    return SandboxResult(
                        success=False,
                        error=f"不支持的强隔离后端：{backend}",
                        exit_code=503,
                        execution_mode="isolation_unavailable",
                    )
                runtime = self._resolve_windows_sandbox()
                if not runtime:
                    self._status.state = "idle"
                    return SandboxResult(
                        success=False,
                        error="Windows Sandbox 未安装或未启用；为避免在宿主机运行不可信命令，本次执行已拒绝",
                        exit_code=503,
                        execution_mode="isolation_unavailable",
                    )
                async with self._isolation_lock:
                    result = await self._run_windows_sandbox_command(
                        command=prompt,
                        runtime=runtime,
                        timeout=exec_timeout,
                    )
                self._status.state = "idle"
                return result

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

    def _resolve_windows_sandbox(self) -> Optional[str]:
        """解析 Windows Sandbox 可执行文件；非 Windows 平台不伪装成已隔离。"""
        if sys.platform != "win32":
            return None
        configured = str(getattr(self._config, "runtime_binary", "") or "").strip()
        candidates = [configured] if configured else []
        candidates.extend([
            shutil.which("WindowsSandbox.exe") or "",
            str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"),
        ])
        for candidate in candidates:
            candidate_path = Path(candidate) if candidate else None
            # 兼容旧 runtime_binary 配置，但绝不能把任意宿主机程序伪装成隔离后端启动。
            if (
                candidate_path
                and candidate_path.name.lower() == "windowssandbox.exe"
                and candidate_path.is_file()
            ):
                return str(candidate_path.resolve())
        return None

    async def _run_windows_sandbox_command(self, command: str, runtime: str, timeout: float) -> SandboxResult:
        """通过映射目录在一次性 Windows Sandbox 中运行 PowerShell 命令并回收结果。"""
        run_root = self._data_dir / "sandbox_runs"
        run_root.mkdir(parents=True, exist_ok=True)
        run_dir = run_root / uuid.uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=False)
        command_file = run_dir / "command.ps1"
        runner_file = run_dir / "runner.ps1"
        result_file = run_dir / "result.json"
        config_file = run_dir / "codebot.wsb"

        command_file.write_text(command, encoding="utf-8")
        runner_file.write_text(
            "$ErrorActionPreference = 'Continue'\n"
            "Set-Location 'C:\\CodebotWorkspace'\n"
            "$outputPath = 'C:\\CodebotRun\\output.txt'\n"
            "& powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File 'C:\\CodebotRun\\command.ps1' *> $outputPath\n"
            "$exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }\n"
            "$result = @{ exit_code = [int]$exitCode; completed = $true } | ConvertTo-Json -Compress\n"
            "Set-Content -LiteralPath 'C:\\CodebotRun\\result.json' -Value $result -Encoding UTF8\n"
            "Stop-Computer -Force\n",
            encoding="utf-8",
        )
        networking = "Enable" if getattr(self._config, "network_enabled", False) else "Disable"
        config_file.write_text(
            "<Configuration>\n"
            "  <VGpu>Disable</VGpu>\n"
            f"  <Networking>{networking}</Networking>\n"
            "  <ClipboardRedirection>Disable</ClipboardRedirection>\n"
            "  <PrinterRedirection>Disable</PrinterRedirection>\n"
            "  <AudioInput>Disable</AudioInput>\n"
            "  <VideoInput>Disable</VideoInput>\n"
            "  <ProtectedClient>Enable</ProtectedClient>\n"
            "  <MappedFolders>\n"
            f"    <MappedFolder><HostFolder>{xml_escape(str(self._workspace.resolve()))}</HostFolder><SandboxFolder>C:\\CodebotWorkspace</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>\n"
            f"    <MappedFolder><HostFolder>{xml_escape(str(run_dir.resolve()))}</HostFolder><SandboxFolder>C:\\CodebotRun</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>\n"
            "  </MappedFolders>\n"
            "  <LogonCommand><Command>powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\\CodebotRun\\runner.ps1</Command></LogonCommand>\n"
            "</Configuration>\n",
            encoding="utf-8",
        )

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                runtime,
                str(config_file),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            async with self._proc_lock:
                self._current_proc = proc
            deadline = asyncio.get_running_loop().time() + float(timeout)
            while asyncio.get_running_loop().time() < deadline:
                if result_file.exists():
                    payload = json.loads(result_file.read_text(encoding="utf-8-sig"))
                    output_file = run_dir / "output.txt"
                    output = ""
                    if output_file.exists():
                        output = _decode_command_output(output_file.read_bytes()[:4_000_000]).strip()
                    exit_code = int(payload.get("exit_code", 1))
                    return SandboxResult(
                        success=exit_code == 0,
                        content=output,
                        error="" if exit_code == 0 else output,
                        exit_code=exit_code,
                        execution_mode="windows_sandbox",
                    )
                await asyncio.sleep(0.5)
            return SandboxResult(
                success=False,
                error=f"Windows Sandbox 执行超时（{timeout}秒）",
                exit_code=124,
                execution_mode="windows_sandbox",
            )
        except Exception as exc:
            return SandboxResult(
                success=False,
                error=f"Windows Sandbox 启动或通信失败：{exc}",
                exit_code=1,
                execution_mode="windows_sandbox",
            )
        finally:
            if proc and proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=15)
                except asyncio.TimeoutError:
                    try:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=10)
                    except (ProcessLookupError, asyncio.TimeoutError):
                        pass
            async with self._proc_lock:
                if self._current_proc is proc:
                    self._current_proc = None
            # Windows Sandbox 退出后映射目录句柄可能短暂存留，进行有限重试。
            for _ in range(10):
                try:
                    shutil.rmtree(run_dir)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    await asyncio.sleep(0.5)

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
        proc = None

        logger.debug(f"沙箱执行命令（cwd={cwd}）: {command[:200]}")

        try:
            async with self._proc_lock:
                running = self._current_proc
                if running and running.returncode is None:
                    return SandboxResult(
                        success=False,
                        error="已有沙箱任务正在执行，请等待当前任务完成",
                        exit_code=409,
                        execution_mode="local",
                    )

                creationflags = 0
                if sys.platform == "win32":
                    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(cwd),
                    env=exec_env,
                    creationflags=creationflags,
                )
                self._current_proc = proc

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                _kill_process_tree(proc)
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
        finally:
            async with self._proc_lock:
                if proc is not None and self._current_proc is proc:
                    self._current_proc = None


# ── 辅助函数 ────────────────────────────────────────────────────────────────

def _detect_platform() -> str:
    """检测当前平台"""
    p = sys.platform
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "macos"
    return "linux"


def _decode_command_output(data: bytes) -> str:
    """兼容 Windows PowerShell 重定向产生的 UTF-16LE 与新版 UTF-8 输出。"""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data[:200].count(b"\x00") > max(4, len(data[:200]) // 4):
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def _build_exec_env(extra: Optional[Dict[str, str]] = None) -> dict:
    """
    构建执行环境变量。
    参考 LobsterAI 的环境变量处理：
    - 继承当前进程的环境变量
    - 移除可能影响安全的变量
    - 合并用户传入的额外变量
    """
    env = {**os.environ}
    # 移除路径注入和常见凭据。确需传入的值只能通过本次调用的 extra 显式提供。
    for key in ["PYTHONHOME", "PYTHONPATH"]:
        env.pop(key, None)
    for key in list(env):
        upper = key.upper()
        if any(marker in upper for marker in ("TOKEN", "API_KEY", "SECRET", "PASSWORD", "CREDENTIAL")):
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


def _kill_process_tree(proc) -> None:
    """尽量终止整个进程树，避免 Windows 下 shell 子进程残留。"""
    try:
        pid = int(getattr(proc, "pid", 0) or 0)
    except Exception:
        pid = 0

    if pid and sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            return
        except Exception:
            pass

    _kill_process(proc)
