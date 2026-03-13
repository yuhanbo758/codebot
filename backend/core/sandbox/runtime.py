"""
沙箱运行时管理
负责：
  - 检测 QEMU 二进制是否可用
  - 检测 / 下载沙箱磁盘镜像
  - SHA-256 校验
  - 平台相关路径处理（Windows / macOS / Linux）

设计参考 LobsterAI coworkSandboxRuntime.ts
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import httpx
from loguru import logger

# ── 默认下载配置 ─────────────────────────────────────────────────────────────

# 来自 LobsterAI (NetEase Youdao) 生产 CDN 的真实镜像 URL
# 参考: coworkSandboxRuntime.ts
_DEFAULT_IMAGE_URL_ARM64 = "https://ydhardwarecommon.nosdn.127.net/59d9df60ce9c0463c54e3043af60cb10.qcow2"
_DEFAULT_IMAGE_URL_AMD64 = "https://ydhardwarebusiness.nosdn.127.net/3ba0e509b60aaf8b5a969618d1b4e170.qcow2"


def _default_image_url() -> str:
    """根据当前 CPU 架构返回 LobsterAI CDN 上对应的默认镜像 URL。"""
    env_override = os.getenv("CODEBOT_SANDBOX_IMAGE_URL", "")
    if env_override:
        return env_override
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return _DEFAULT_IMAGE_URL_ARM64
    return _DEFAULT_IMAGE_URL_AMD64


def _default_image_filename() -> str:
    """根据当前 CPU 架构返回默认镜像文件名。"""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "linux-arm64.qcow2"
    return "linux-x86_64.qcow2"


DEFAULT_IMAGE_URL = _default_image_url()
DEFAULT_IMAGE_FILENAME = _default_image_filename()
DEFAULT_IMAGE_SHA256 = os.getenv("CODEBOT_SANDBOX_IMAGE_SHA256", "")

# QEMU 二进制候选列表（按优先级）
_QEMU_CANDIDATES_POSIX = [
    "qemu-system-x86_64",
    "/usr/bin/qemu-system-x86_64",
    "/usr/local/bin/qemu-system-x86_64",
    "/opt/homebrew/bin/qemu-system-x86_64",  # macOS Homebrew ARM
]

_QEMU_CANDIDATES_WINDOWS = [
    r"C:\Program Files\qemu\qemu-system-x86_64.exe",
    r"C:\Program Files (x86)\qemu\qemu-system-x86_64.exe",
    "qemu-system-x86_64.exe",
    "qemu-system-x86_64",
]

# Windows QEMU 安装包（Stefan Weil 官方构建，NSIS installer，/S 静默安装）
QEMU_INSTALLER_URL_WIN = "https://qemu.weilnetz.de/w64/qemu-w64-setup-20251224.exe"
QEMU_INSTALLER_FILENAME = "qemu-w64-setup-20251224.exe"


# ── 状态数据类 ───────────────────────────────────────────────────────────────

@dataclass
class RuntimeStatus:
    qemu_available: bool = False
    qemu_path: str = ""
    image_available: bool = False
    image_path: str = ""
    image_size_bytes: int = 0
    downloading: bool = False
    download_progress: float = 0.0   # 0.0 – 1.0
    installing_qemu: bool = False
    install_qemu_progress: float = 0.0   # 0.0 – 1.0  (下载阶段)
    install_qemu_error: str = ""
    download_error: str = ""
    ready: bool = False


# ── 核心类 ───────────────────────────────────────────────────────────────────

class SandboxRuntime:
    """
    管理 QEMU 二进制和沙箱镜像的检测与下载。

    用法::

        runtime = SandboxRuntime(data_dir=Path("/path/to/data"), config=sandbox_cfg)
        await runtime.ensure_ready()
        if runtime.status.ready:
            runner = VmRunner(runtime, ...)
    """

    def __init__(self, data_dir: Path, config=None):
        self._data_dir = data_dir
        self._cfg = config
        self._sandbox_dir = data_dir / "sandbox"
        self._sandbox_dir.mkdir(parents=True, exist_ok=True)

        self.status = RuntimeStatus()
        self._download_lock = asyncio.Lock()

    # ── 公开接口 ─────────────────────────────────────────────────────────────

    async def ensure_ready(
        self,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """
        检查 QEMU 和镜像是否就绪；如果 `auto_download=True` 且配置了有效 URL，则自动下载缺失的镜像。

        注意：此方法始终更新 status 字段（包括错误信息），不会因下载失败而抛出异常。
        :param progress_cb: 下载进度回调，参数为 0.0–1.0 的浮点数。
        :return: True 表示 QEMU 和镜像均就绪，False 表示仍不可用。
        """
        self._detect_qemu()
        self._detect_image()

        if not self.status.qemu_available:
            logger.warning("QEMU 未找到，沙箱不可用。请安装 qemu-system-x86_64。")

        if not self.status.image_available:
            auto_dl = True
            if self._cfg is not None:
                auto_dl = getattr(self._cfg, "auto_download", True)

            if auto_dl:
                # 优先使用用户配置的 URL，否则使用 LobsterAI CDN 的架构对应默认 URL
                image_url = getattr(self._cfg, "image_url", "") if self._cfg else ""
                image_url = image_url or DEFAULT_IMAGE_URL
                logger.info(f"沙箱镜像不存在，将从以下地址自动下载: {image_url}")
                await self._download_image(progress_cb=progress_cb, override_url=image_url)
                # 下载结果（成功/失败）已写入 status，不再中断流程
            else:
                logger.warning("沙箱镜像未找到，auto_download=False，跳过下载。")

        self.status.ready = self.status.qemu_available and self.status.image_available
        return self.status.ready

    def get_qemu_path(self) -> str:
        return self.status.qemu_path

    def get_image_path(self) -> str:
        return self.status.image_path

    # ── QEMU 检测 ─────────────────────────────────────────────────────────────

    def _detect_qemu(self):
        # 优先使用配置中的显式路径
        explicit = ""
        if self._cfg:
            explicit = getattr(self._cfg, "runtime_binary", "") or ""
        if explicit and Path(explicit).is_file():
            self.status.qemu_available = True
            self.status.qemu_path = explicit
            logger.debug(f"QEMU 使用配置路径: {explicit}")
            return

        candidates = (
            _QEMU_CANDIDATES_WINDOWS
            if sys.platform == "win32"
            else _QEMU_CANDIDATES_POSIX
        )

        # shutil.which 会搜 PATH
        which_result = shutil.which("qemu-system-x86_64") or shutil.which(
            "qemu-system-x86_64.exe"
        )
        if which_result:
            self.status.qemu_available = True
            self.status.qemu_path = which_result
            logger.debug(f"QEMU 在 PATH 中找到: {which_result}")
            return

        for candidate in candidates:
            if Path(candidate).is_file():
                self.status.qemu_available = True
                self.status.qemu_path = candidate
                logger.debug(f"QEMU 在候选路径中找到: {candidate}")
                return

        self.status.qemu_available = False
        self.status.qemu_path = ""
        logger.debug("QEMU 未找到")

    # ── 镜像检测 ──────────────────────────────────────────────────────────────

    def _detect_image(self):
        # 优先使用配置中的显式路径
        explicit = ""
        if self._cfg:
            explicit = getattr(self._cfg, "image_path", "") or ""
        if explicit and Path(explicit).is_file():
            self.status.image_available = True
            self.status.image_path = explicit
            self.status.image_size_bytes = Path(explicit).stat().st_size
            logger.debug(f"沙箱镜像使用配置路径: {explicit}")
            return

        # 在默认目录中查找
        default_path = self._sandbox_dir / DEFAULT_IMAGE_FILENAME
        if default_path.is_file():
            self.status.image_available = True
            self.status.image_path = str(default_path)
            self.status.image_size_bytes = default_path.stat().st_size
            logger.debug(f"沙箱镜像在默认路径找到: {default_path}")
            return

        self.status.image_available = False
        self.status.image_path = ""
        self.status.image_size_bytes = 0
        logger.debug("沙箱镜像未找到")

    # ── 镜像下载 ──────────────────────────────────────────────────────────────

    async def _download_image(
        self,
        progress_cb: Optional[Callable[[float], None]] = None,
        override_url: str = "",
    ) -> bool:
        async with self._download_lock:
            if self.status.image_available:
                return True
            if self.status.downloading:
                return False

            image_url = override_url or (getattr(self._cfg, "image_url", "") if self._cfg else "") or DEFAULT_IMAGE_URL
            if not image_url:
                self.status.download_error = "未配置镜像下载 URL，无法下载"
                logger.error(self.status.download_error)
                return False

            dest = self._sandbox_dir / DEFAULT_IMAGE_FILENAME
            tmp = dest.with_suffix(".tmp")

            self.status.downloading = True
            self.status.download_progress = 0.0
            self.status.download_error = ""

            logger.info(f"开始下载沙箱镜像: {image_url}")
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
                    async with client.stream("GET", image_url) as resp:
                        resp.raise_for_status()
                        total = int(resp.headers.get("content-length", 0))
                        downloaded = 0
                        sha = hashlib.sha256()
                        with open(tmp, "wb") as f:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                f.write(chunk)
                                sha.update(chunk)
                                downloaded += len(chunk)
                                if total > 0:
                                    progress = downloaded / total
                                    self.status.download_progress = progress
                                    if progress_cb:
                                        progress_cb(progress)

                # 校验 SHA-256（如果提供了预期摘要）
                expected_sha = DEFAULT_IMAGE_SHA256
                if self._cfg:
                    # 配置中暂无 sha256 字段，预留
                    pass
                if expected_sha:
                    actual = sha.hexdigest()
                    if actual != expected_sha:
                        tmp.unlink(missing_ok=True)
                        self.status.download_error = f"SHA-256 校验失败: expected={expected_sha} actual={actual}"
                        logger.error(self.status.download_error)
                        return False

                # 原子重命名
                tmp.replace(dest)
                self.status.image_available = True
                self.status.image_path = str(dest)
                self.status.image_size_bytes = dest.stat().st_size
                self.status.download_progress = 1.0
                logger.info(f"沙箱镜像下载完成: {dest}")
                return True

            except Exception as e:
                tmp.unlink(missing_ok=True)
                self.status.download_error = str(e)
                logger.error(f"沙箱镜像下载失败: {e}")
                return False
            finally:
                self.status.downloading = False

    # ── 工具方法 ──────────────────────────────────────────────────────────────

    @staticmethod
    def detect_platform() -> str:
        """返回 'windows' | 'macos' | 'linux'"""
        p = sys.platform
        if p == "win32":
            return "windows"
        if p == "darwin":
            return "macos"
        return "linux"

    @staticmethod
    def is_windows() -> bool:
        return sys.platform == "win32"

    @staticmethod
    def is_macos() -> bool:
        return sys.platform == "darwin"

    # ── QEMU 自动安装 ─────────────────────────────────────────────────────────

    async def install_qemu(
        self,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """
        自动安装 QEMU：
        - Windows: 下载官方 NSIS installer → /S 静默安装
        - macOS:   brew install qemu
        - Linux:   apt / dnf / pacman 安装 qemu-system-x86_64

        进度 0.0–0.5 对应下载阶段，0.5–1.0 对应安装阶段。
        安装结果写入 status.installing_qemu / install_qemu_error。
        安装成功后自动重新检测并更新 status.qemu_available。
        """
        if self.status.installing_qemu:
            logger.warning("QEMU 安装已在进行中，跳过重复请求")
            return False

        self.status.installing_qemu = True
        self.status.install_qemu_progress = 0.0
        self.status.install_qemu_error = ""

        try:
            if sys.platform == "win32":
                ok = await self._install_qemu_windows(progress_cb)
            elif sys.platform == "darwin":
                ok = await self._install_qemu_macos(progress_cb)
            else:
                ok = await self._install_qemu_linux(progress_cb)

            if ok:
                self._detect_qemu()
                if self.status.qemu_available:
                    logger.info(f"QEMU 安装成功，路径: {self.status.qemu_path}")
                else:
                    ok = False
                    self.status.install_qemu_error = "安装程序执行完毕，但仍未检测到 QEMU 二进制"
                    logger.error(self.status.install_qemu_error)
            return ok

        except Exception as e:
            self.status.install_qemu_error = str(e)
            logger.error(f"QEMU 安装异常: {e}")
            return False
        finally:
            self.status.installing_qemu = False
            self.status.install_qemu_progress = 1.0

    async def _install_qemu_windows(
        self,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """Windows: 下载 NSIS installer → 通过 ShellExecuteW runas 提权静默安装

        QEMU NSIS 安装程序需要管理员权限才能写入 Program Files。
        直接调用 subprocess.run 会触发 [WinError 740] 请求的操作需要提升。
        正确做法是使用 ctypes.windll.shell32.ShellExecuteW 以 'runas' 动词
        触发 UAC 弹窗，由用户确认后以管理员权限完成安装。
        """
        import ctypes
        import subprocess
        import tempfile

        # 将安装包下载到用户可写的临时目录，避免写入 Program Files 权限问题
        tmp_dir = Path(tempfile.gettempdir())
        installer_path = tmp_dir / QEMU_INSTALLER_FILENAME
        tmp_download = installer_path.with_suffix(".tmp")

        # ── 阶段一：下载（0.0 → 0.5）─────────────────────────────────────────
        logger.info(f"正在下载 QEMU 安装程序: {QEMU_INSTALLER_URL_WIN}")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
                async with client.stream("GET", QEMU_INSTALLER_URL_WIN) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("content-length", 0))
                    downloaded = 0
                    with open(tmp_download, "wb") as f:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                p = (downloaded / total) * 0.5
                                self.status.install_qemu_progress = p
                                if progress_cb:
                                    progress_cb(p)
            tmp_download.replace(installer_path)
        except Exception as e:
            tmp_download.unlink(missing_ok=True)
            self.status.install_qemu_error = f"下载失败: {e}"
            logger.error(self.status.install_qemu_error)
            return False

        self.status.install_qemu_progress = 0.5
        if progress_cb:
            progress_cb(0.5)

        # ── 阶段二：提权静默安装（0.5 → 1.0）────────────────────────────────
        # ShellExecuteW 使用 'runas' 动词触发 UAC 弹窗，用户同意后以管理员
        # 权限运行安装程序。SW_HIDE=0 隐藏窗口，/S 为 NSIS 静默安装标志。
        # ShellExecuteW 是异步触发的，需要额外等待进程结束。
        logger.info("正在通过 UAC 提权静默安装 QEMU（将弹出用户账户控制对话框）…")
        try:
            loop = asyncio.get_event_loop()
            done = asyncio.Event()
            result_box: list = []

            def _run_elevated():
                try:
                    # 使用 ShellExecuteEx（支持等待进程结束）代替 ShellExecuteW
                    # SEE_MASK_NOCLOSEPROCESS = 0x40，让我们能拿到进程句柄
                    import ctypes.wintypes

                    SEE_MASK_NOCLOSEPROCESS = 0x00000040
                    SW_HIDE = 0

                    class SHELLEXECUTEINFO(ctypes.Structure):
                        _fields_ = [
                            ("cbSize",       ctypes.wintypes.DWORD),
                            ("fMask",        ctypes.wintypes.ULONG),
                            ("hwnd",         ctypes.wintypes.HWND),
                            ("lpVerb",       ctypes.c_wchar_p),
                            ("lpFile",       ctypes.c_wchar_p),
                            ("lpParameters", ctypes.c_wchar_p),
                            ("lpDirectory",  ctypes.c_wchar_p),
                            ("nShow",        ctypes.c_int),
                            ("hInstApp",     ctypes.wintypes.HINSTANCE),
                            ("lpIDList",     ctypes.c_void_p),
                            ("lpClass",      ctypes.c_wchar_p),
                            ("hkeyClass",    ctypes.wintypes.HKEY),
                            ("dwHotKey",     ctypes.wintypes.DWORD),
                            ("hIconOrMonitor", ctypes.wintypes.HANDLE),
                            ("hProcess",     ctypes.wintypes.HANDLE),
                        ]

                    sei = SHELLEXECUTEINFO()
                    sei.cbSize       = ctypes.sizeof(SHELLEXECUTEINFO)
                    sei.fMask        = SEE_MASK_NOCLOSEPROCESS
                    sei.hwnd         = None
                    sei.lpVerb       = "runas"
                    sei.lpFile       = str(installer_path)
                    sei.lpParameters = "/S"
                    sei.lpDirectory  = None
                    sei.nShow        = SW_HIDE

                    shell32 = ctypes.windll.shell32
                    ok = shell32.ShellExecuteExW(ctypes.byref(sei))
                    if not ok:
                        err = ctypes.GetLastError()
                        # 错误 1223 = ERROR_CANCELLED，用户拒绝了 UAC 提示
                        if err == 1223:
                            result_box.append(RuntimeError(
                                "用户取消了管理员权限请求（UAC 被拒绝）。"
                                "请以管理员身份重新运行应用后再安装 QEMU，"
                                "或手动下载安装: https://qemu.weilnetz.de/w64/"
                            ))
                        else:
                            result_box.append(RuntimeError(
                                f"ShellExecuteExW 失败，错误码: {err}"
                            ))
                        return

                    # 等待安装进程结束
                    if sei.hProcess:
                        kernel32 = ctypes.windll.kernel32
                        kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)  # INFINITE
                        exit_code = ctypes.wintypes.DWORD()
                        kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code))
                        kernel32.CloseHandle(sei.hProcess)
                        result_box.append(exit_code.value)
                    else:
                        # 没有拿到句柄时退回轮询等待
                        import time
                        time.sleep(30)
                        result_box.append(0)

                except Exception as exc:
                    result_box.append(exc)
                finally:
                    loop.call_soon_threadsafe(done.set)

            import threading
            t = threading.Thread(target=_run_elevated, daemon=True)
            t.start()

            # 等待安装完成，期间每 2 秒推进一次假进度
            while not done.is_set():
                try:
                    await asyncio.wait_for(done.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    p = min(self.status.install_qemu_progress + 0.02, 0.95)
                    self.status.install_qemu_progress = p
                    if progress_cb:
                        progress_cb(p)

            if not result_box:
                self.status.install_qemu_error = "安装线程未返回结果"
                logger.error(self.status.install_qemu_error)
                return False

            res = result_box[0]
            if isinstance(res, Exception):
                raise res
            # exit_code 为整数，0 表示成功
            if isinstance(res, int) and res != 0:
                self.status.install_qemu_error = (
                    f"安装程序退出码 {res}（安装失败，"
                    "请尝试手动下载安装: https://qemu.weilnetz.de/w64/）"
                )
                logger.error(self.status.install_qemu_error)
                return False

        except Exception as e:
            self.status.install_qemu_error = f"安装执行失败: {e}"
            logger.error(self.status.install_qemu_error)
            return False
        finally:
            installer_path.unlink(missing_ok=True)  # 清理安装包

        return True

    async def _install_qemu_macos(
        self,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """macOS: brew install qemu"""
        brew = shutil.which("brew")
        if not brew:
            self.status.install_qemu_error = "未找到 Homebrew，请先安装 Homebrew (https://brew.sh)"
            logger.error(self.status.install_qemu_error)
            return False

        self.status.install_qemu_progress = 0.1
        if progress_cb:
            progress_cb(0.1)
        logger.info("正在执行 brew install qemu …")
        try:
            proc = await asyncio.create_subprocess_exec(
                brew, "install", "qemu",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            while True:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                    break
                except asyncio.TimeoutError:
                    p = min(self.status.install_qemu_progress + 0.05, 0.95)
                    self.status.install_qemu_progress = p
                    if progress_cb:
                        progress_cb(p)

            if proc.returncode != 0:
                out = (await proc.stdout.read()).decode(errors="replace")
                self.status.install_qemu_error = f"brew install qemu 失败 (exit {proc.returncode}): {out[-300:]}"
                logger.error(self.status.install_qemu_error)
                return False
        except Exception as e:
            self.status.install_qemu_error = f"brew install qemu 异常: {e}"
            logger.error(self.status.install_qemu_error)
            return False
        return True

    async def _install_qemu_linux(
        self,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> bool:
        """Linux: 自动选择包管理器安装 qemu-system-x86_64"""
        pkg_managers = [
            (shutil.which("apt-get"), ["apt-get", "install", "-y", "qemu-system-x86-64"]),
            (shutil.which("apt"),     ["apt", "install", "-y", "qemu-system-x86-64"]),
            (shutil.which("dnf"),     ["dnf", "install", "-y", "qemu-system-x86_64"]),
            (shutil.which("yum"),     ["yum", "install", "-y", "qemu-system-x86_64"]),
            (shutil.which("pacman"),  ["pacman", "-S", "--noconfirm", "qemu-system-x86_64"]),
            (shutil.which("zypper"),  ["zypper", "install", "-y", "qemu-x86"]),
        ]
        cmd = None
        for pm_path, pm_cmd in pkg_managers:
            if pm_path:
                cmd = pm_cmd
                break

        if not cmd:
            self.status.install_qemu_error = "未找到支持的包管理器（apt/dnf/yum/pacman/zypper）"
            logger.error(self.status.install_qemu_error)
            return False

        self.status.install_qemu_progress = 0.1
        if progress_cb:
            progress_cb(0.1)
        logger.info(f"正在执行: {' '.join(cmd)}")
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            while True:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                    break
                except asyncio.TimeoutError:
                    p = min(self.status.install_qemu_progress + 0.05, 0.95)
                    self.status.install_qemu_progress = p
                    if progress_cb:
                        progress_cb(p)

            if proc.returncode != 0:
                out = (await proc.stdout.read()).decode(errors="replace")
                self.status.install_qemu_error = f"包管理器安装失败 (exit {proc.returncode}): {out[-300:]}"
                logger.error(self.status.install_qemu_error)
                return False
        except Exception as e:
            self.status.install_qemu_error = f"包管理器执行异常: {e}"
            logger.error(self.status.install_qemu_error)
            return False
        return True
