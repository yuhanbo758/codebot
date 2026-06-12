"""
OpenCode 安装检查工具
"""
import subprocess
import sys
import os
import json
import platform
import shutil
import socket
import time
import asyncio
from typing import Optional, List
from pathlib import Path
from urllib.parse import urlparse
from loguru import logger

import httpx


_opencode_server_process: Optional[subprocess.Popen] = None


def _is_runnable_opencode_path(p: Path) -> bool:
    if not p.is_file():
        return False
    if os.name == "nt" and p.suffix.lower() == ".exe":
        try:
            with open(p, "rb") as f:
                return f.read(2) == b"MZ"
        except Exception:
            return False
    return True


def _collect_opencode_commands() -> List[List[str]]:
    commands: List[List[str]] = []
    seen = set()

    def _append_path(p: Path):
        key = str(p).strip().lower()
        if not key or key in seen:
            return
        if _is_runnable_opencode_path(p):
            seen.add(key)
            commands.append([str(p)])

    def _append_opencode_dir(directory: Path):
        # Windows packaged / npm / scoop installs may expose either `opencode`
        # or `opencode-ai` as the actual shim name. Probe both so the desktop
        # app can still discover the CLI even when PATH differs from the shell.
        if os.name == "nt":
            base_names = ["opencode", "opencode-ai"]
            suffixes = [".cmd", ".bat", ".ps1", ".exe", ""]
        else:
            base_names = ["opencode", "opencode-ai"]
            suffixes = ["", ".exe"]
        for base_name in base_names:
            for suffix in suffixes:
                _append_path(directory / f"{base_name}{suffix}")

    def _append_windows_known_dirs():
        if os.name != "nt":
            return

        candidate_dirs: List[Path] = []
        seen_dirs = set()

        def _append_dir(raw_dir):
            if not raw_dir:
                return
            directory = Path(raw_dir)
            key = str(directory).strip().lower()
            if not key or key in seen_dirs:
                return
            seen_dirs.add(key)
            candidate_dirs.append(directory)

        user_profile = os.environ.get("USERPROFILE", "").strip()
        app_data = os.environ.get("APPDATA", "").strip()
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        program_data = os.environ.get("ProgramData", "").strip()
        home_dir = Path.home()

        # Electron / packaged app processes often miss the user's shell PATH.
        # Probe the most common Windows shim directories directly so model
        # refresh can still find the same CLI that works in PowerShell.
        _append_dir(Path(app_data) / "npm" if app_data else None)
        _append_dir(Path(user_profile) / "AppData" / "Roaming" / "npm" if user_profile else None)
        _append_dir(home_dir / "AppData" / "Roaming" / "npm")
        _append_dir(Path(user_profile) / "scoop" / "shims" if user_profile else None)
        _append_dir(home_dir / "scoop" / "shims")
        _append_dir(Path(local_app_data) / "Microsoft" / "WinGet" / "Links" if local_app_data else None)
        _append_dir(Path(program_data) / "chocolatey" / "bin" if program_data else None)

        for directory in candidate_dirs:
            _append_opencode_dir(directory)

    configured_paths = [os.environ.get("CODEBOT_OPENCODE_PATH", "").strip()]
    try:
        from config import app_config
        configured_paths.append(getattr(app_config.opencode, "cli_path", "").strip())
    except Exception:
        pass

    for configured_path in configured_paths:
        if not configured_path:
            continue
        configured = Path(configured_path)
        candidates = [configured]
        if configured.is_dir():
            _append_opencode_dir(configured)
        for p in candidates:
            _append_path(p)

    # 1. Check for a bundled binary shipped alongside the packaged app.
    #    Electron sets CODEBOT_RESOURCES_DIR to process.resourcesPath.
    resources_dir = os.environ.get("CODEBOT_RESOURCES_DIR", "").strip()
    if resources_dir:
        _append_opencode_dir(Path(resources_dir) / "opencode")

    repo_root = Path(__file__).resolve().parents[2]
    for directory in [
        repo_root / "electron" / "vendor" / "opencode",
        repo_root / "vendor" / "opencode",
    ]:
        _append_opencode_dir(directory)

    _append_windows_known_dirs()

    # 2. Fall back to system PATH
    if os.name == "nt":
        path_names = [
            "opencode.cmd",
            "opencode.bat",
            "opencode.ps1",
            "opencode.exe",
            "opencode",
            "opencode-ai.cmd",
            "opencode-ai.bat",
            "opencode-ai.ps1",
            "opencode-ai.exe",
            "opencode-ai",
        ]
    else:
        path_names = ["opencode", "opencode-ai"]
    for name in path_names:
        path_found = shutil.which(name)
        if path_found:
            _append_path(Path(path_found))

    return commands


def collect_opencode_commands() -> List[List[str]]:
    return _collect_opencode_commands()


def _external_opencode_config_candidates() -> List[Path]:
    candidates: List[Path] = []
    seen = set()

    def _append(path: Path):
        key = str(path).strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append(path)

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg_config_home:
        _append(Path(xdg_config_home) / "opencode" / "opencode.json")

    app_data = os.environ.get("APPDATA", "").strip()
    if app_data:
        _append(Path(app_data) / "opencode" / "opencode.json")

    # OpenCode CLI on this Windows machine stores config under ~/.config/opencode/.
    _append(Path.home() / ".config" / "opencode" / "opencode.json")
    _append(Path.home() / ".opencode" / "opencode.json")
    return candidates


def _sync_external_provider_config(managed_config_dir: Path) -> None:
    """
    Merge user-level provider definitions into Codebot's managed OpenCode config.

    Codebot keeps its own MCP/skill config under DATA_DIR/opencode-config to avoid
    mutating the user's global OpenCode setup. However, newly added providers
    (such as volcengine) are usually saved in the global opencode.json. Copy only
    the provider section so the managed server can load the same models while
    preserving Codebot's isolated MCP bridge config.
    """
    managed_config_path = managed_config_dir / "opencode.json"
    source_path: Optional[Path] = None
    source_data: dict = {}
    for candidate in _external_opencode_config_candidates():
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("provider"), dict) and data["provider"]:
            source_path = candidate
            source_data = data
            break

    if not source_path:
        return

    try:
        managed_data = json.loads(managed_config_path.read_text(encoding="utf-8")) if managed_config_path.exists() else {}
    except Exception:
        managed_data = {}
    if not isinstance(managed_data, dict):
        managed_data = {}

    source_providers = source_data.get("provider") if isinstance(source_data.get("provider"), dict) else {}
    managed_providers = managed_data.get("provider") if isinstance(managed_data.get("provider"), dict) else {}

    merged_providers = dict(source_providers)
    merged_providers.update(managed_providers)

    if managed_providers == merged_providers:
        return

    managed_data["provider"] = merged_providers
    managed_config_dir.mkdir(parents=True, exist_ok=True)
    managed_config_path.write_text(
        json.dumps(managed_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "已同步全局 OpenCode provider 配置到 Codebot managed config: "
        f"{source_path} -> {managed_config_path}"
    )


def is_managed_opencode_server_running() -> bool:
    return _opencode_server_process is not None and _opencode_server_process.poll() is None


def _find_opencode_command() -> Optional[List[str]]:
    commands = _collect_opencode_commands()
    return commands[0] if commands else None


def _is_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False


async def _is_opencode_http_ready(base_url: str) -> bool:
    # OpenCode >= 1.x 服务端仅支持 HTTP/2，先用 http2=True 检测
    for http2 in (True, False):
        try:
            async with httpx.AsyncClient(timeout=3, http2=http2) as client:
                response = await client.get(f"{base_url}/global/health")
                response.raise_for_status()
                data = response.json()
                return bool(data.get("healthy"))
        except Exception:
            continue
    return False


def _collect_opencode_base_urls() -> List[str]:
    """收集可能存在 OpenCode HTTP 服务的本地地址。"""
    urls: List[str] = []
    seen = set()

    def _append(url: str):
        value = (url or "").strip().rstrip("/")
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        urls.append(value)

    try:
        from config import app_config

        configured_url = app_config.opencode.server_url
        if configured_url:
            parsed = urlparse(configured_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port
            scheme = parsed.scheme or "http"
            if port:
                _append(f"{scheme}://{host}:{port}")
    except Exception:
        pass

    for raw in [
        os.environ.get("CODEBOT_OPENCODE_PREFERRED_PORT", ""),
        os.environ.get("CODEBOT_OPENCODE_FALLBACK_PORT", ""),
        "11200",
        "4096",
        "50690",
        "1120",
    ]:
        try:
            port = int(str(raw).strip())
        except Exception:
            continue
        if 1 <= port <= 65535:
            _append(f"http://127.0.0.1:{port}")

    return urls


async def _find_running_opencode_service() -> Optional[str]:
    """查找本机已运行的 OpenCode 服务。"""
    for base_url in _collect_opencode_base_urls():
        if await _is_opencode_http_ready(base_url):
            return base_url
    return None


async def check_opencode_installed() -> bool:
    """检查 OpenCode 是否已安装"""
    commands = _collect_opencode_commands()
    if not commands:
        return False
    for command in commands:
        try:
            result = subprocess.run(
                [*command, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                logger.info(f"OpenCode 已安装：{result.stdout.strip()} ({command[0]})")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return False


async def install_opencode() -> bool:
    """安装 OpenCode"""
    system = platform.system()
    logger.info(f"检测到操作系统：{system}")
    
    try:
        if system == "Windows":
            # 尝试使用 scoop
            try:
                logger.info("尝试使用 Scoop 安装 OpenCode...")
                result = subprocess.run(
                    ["scoop", "install", "opencode"],
                    stdin=subprocess.DEVNULL,
                    check=True,
                    capture_output=True,
                    text=True
                )
                logger.info("Scoop 安装成功")
                return True
            except (subprocess.CalledProcessError, FileNotFoundError):
                # 回退到 npm
                logger.info("Scoop 不可用，使用 npm 安装...")
                result = subprocess.run(
                    ["npm", "install", "-g", "opencode-ai"],
                    stdin=subprocess.DEVNULL,
                    check=True,
                    capture_output=True,
                    text=True
                )
                logger.info("npm 安装成功")
                return True
                
        elif system == "Darwin":
            # macOS - 使用 Homebrew
            logger.info("使用 Homebrew 安装 OpenCode...")
            result = subprocess.run(
                ["brew", "install", "anomalyco/tap/opencode"],
                stdin=subprocess.DEVNULL,
                check=True,
                capture_output=True,
                text=True
            )
            logger.info("Homebrew 安装成功")
            return True
            
        else:
            # Linux - 使用安装脚本
            logger.info("使用官方脚本安装 OpenCode...")
            script = "curl -fsSL https://opencode.ai/install | bash"
            result = subprocess.run(
                script,
                shell=True,
                stdin=subprocess.DEVNULL,
                check=True,
                capture_output=True,
                text=True
            )
            logger.info("安装成功")
            return True
            
    except subprocess.CalledProcessError as e:
        logger.error(f"安装失败：{e.stderr}")
        return False
    except Exception as e:
        logger.error(f"安装失败：{e}")
        return False


async def check_and_install_opencode() -> bool:
    """检查并安装 OpenCode"""
    running_service = await _find_running_opencode_service()
    if running_service:
        logger.info(f"检测到运行中的 OpenCode 服务：{running_service}，跳过安装检查")
        return True

    # 检查是否已安装
    if await check_opencode_installed():
        return True
    
    # 未安装，执行安装
    logger.info("OpenCode 未安装，开始安装...")
    return await install_opencode()


async def start_opencode_server(port: int = 11200) -> int:
    """
    启动 codebot 专用的 OpenCode Server，返回实际监听的端口号（失败返回 0）。

    策略：
    - 若端口已是 codebot 自己管理的健康 opencode 服务（_opencode_server_process 存活），直接复用。
    - 若端口被外部进程占用（如 OpenCode 桌面应用）且通过健康检查，直接复用。
      codebot 创建独立的 session，不会干扰桌面端的会话。
    - 若端口被占用但健康检查失败（非 opencode 服务），跳过。
    - 否则尝试启动新实例。
    """
    try:
        global _opencode_server_process

        # 若指定端口已被占用，先检查是否是可用的 OpenCode 服务
        if _is_port_open("127.0.0.1", port):
            base_url_check = f"http://127.0.0.1:{port}"
            if await _is_opencode_http_ready(base_url_check):
                # 检查是否是 codebot 自己管理的进程（进程仍然存活）
                if _opencode_server_process is not None and _opencode_server_process.poll() is None:
                    logger.info(f"端口 {port} 已是 codebot 管理的健康 OpenCode 服务，直接复用")
                    return port
                else:
                    # 端口被外部进程占用（如 OpenCode 桌面应用）
                    # 直接复用：codebot 使用独立的 session，与桌面端互不干扰
                    logger.info(
                        f"端口 {port} 检测到外部健康 OpenCode 服务（可能是 OpenCode 桌面应用），"
                        f"直接复用，codebot 将创建独立的会话"
                    )
                    return port
            else:
                logger.warning(f"端口 {port} 已被占用但非 OpenCode 服务，跳过")
                return 0

        commands = _collect_opencode_commands()
        if not commands:
            logger.error("未找到 OpenCode 可执行文件（已检查打包目录和系统 PATH）")
            return 0

        try:
            from config import settings
            settings.LOGS_DIR.mkdir(parents=True, exist_ok=True)
            stdout_path = settings.LOGS_DIR / "opencode_server.stdout.log"
            stderr_path = settings.LOGS_DIR / "opencode_server.stderr.log"
            # codebot 专用 opencode 配置目录（与系统级 ~/.config/opencode/ 隔离）
            opencode_config_dir = str(settings.DATA_DIR / "opencode-config")
        except Exception:
            stdout_path = Path.cwd() / "opencode_server.stdout.log"
            stderr_path = Path.cwd() / "opencode_server.stderr.log"
            stdout_path.parent.mkdir(parents=True, exist_ok=True)
            opencode_config_dir = str(Path.home() / ".codebot" / "opencode-config")

        # 确保 opencode 配置目录存在
        managed_config_dir = Path(opencode_config_dir)
        managed_config_dir.mkdir(parents=True, exist_ok=True)
        _sync_external_provider_config(managed_config_dir)

        # 构建子进程环境变量：
        # - 使用 OPENCODE_CONFIG_HOME 指向 managed config 目录
        # - 不再同时覆写 XDG_CONFIG_HOME；实测这会让 opencode 回到另一套
        #   配置解析分支，导致 data/opencode-config/opencode.json 中的新
        #   provider（如 volcengine）无法被当前 server 加载
        # - 保留原有环境，以免 PATH 等必要变量丢失
        proc_env = dict(os.environ)
        # Desktop OpenCode injects these variables into child processes. If they
        # leak into Codebot's managed server, OpenCode enables Basic Auth and
        # Codebot's local HTTP client receives 401 on every request.
        for key in [
            "OPENCODE_SERVER_USERNAME",
            "OPENCODE_SERVER_PASSWORD",
            "OPENCODE_CLIENT",
            "OPENCODE_PID",
            "OPENCODE_PROCESS_ROLE",
            "OPENCODE_RUN_ID",
        ]:
            proc_env.pop(key, None)
        # 明确移除 XDG 覆盖，只保留 OPENCODE_CONFIG_HOME。
        proc_env.pop("XDG_CONFIG_HOME", None)
        proc_env["OPENCODE_CONFIG_HOME"] = opencode_config_dir

        stdout_file = open(stdout_path, "a", encoding="utf-8")
        stderr_file = open(stderr_path, "a", encoding="utf-8")
        base_url = f"http://127.0.0.1:{port}"
        for command in commands:
            try:
                proc = subprocess.Popen(
                    [*command, "serve", "--port", str(port), "--hostname", "127.0.0.1"],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    cwd=str(Path.home()),
                    env=proc_env,
                )
                for _ in range(30):
                    if await _is_opencode_http_ready(base_url):
                        _opencode_server_process = proc
                        logger.info(f"OpenCode Server 已启动 ({base_url})，命令：{command[0]}")
                        return port
                    if proc.poll() is not None:
                        break
                    await asyncio.sleep(0.5)
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                logger.warning(f"OpenCode 命令未就绪，尝试下一个候选：{command[0]}")
            except Exception as cmd_err:
                logger.warning(f"OpenCode 命令启动失败，尝试下一个候选：{command[0]} ({cmd_err})")

        logger.error(f"OpenCode Server 启动失败，所有候选命令均未就绪 ({base_url})")
        return 0
    except Exception as e:
        logger.error(f"启动 OpenCode Server 失败：{e}")
        return 0


def stop_opencode_server():
    """停止 codebot 自己启动的 OpenCode 实例。不会影响外部 OpenCode 进程（如桌面应用）。"""
    global _opencode_server_process
    proc = _opencode_server_process
    _opencode_server_process = None
    if not proc:
        logger.debug("无 codebot 管理的 OpenCode 进程需要停止（可能是复用了外部实例）")
        return
    try:
        logger.info("正在停止 codebot 管理的 OpenCode 实例...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("OpenCode 实例未及时退出，正在强制结束...")
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        pass


async def restart_managed_opencode_server(port: int = 11200) -> int:
    """Restart only Codebot's own OpenCode process on the same configured port."""
    if not is_managed_opencode_server_running():
        return 0
    stop_opencode_server()
    for _ in range(20):
        if not _is_port_open("127.0.0.1", port):
            break
        await asyncio.sleep(0.25)
    return await start_opencode_server(port)


if __name__ == "__main__":
    import asyncio
    
    async def main():
        installed = await check_and_install_opencode()
        if installed:
            print("✅ OpenCode 已就绪")
        else:
            print("❌ OpenCode 安装失败")
    
    asyncio.run(main())
