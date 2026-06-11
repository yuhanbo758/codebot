"""
Hermes Agent CLI integration API.

Codebot treats Hermes as a third-party agent runtime: when the user chooses
Hermes in chat, Codebot invokes `hermes -z` and renders the final response.
There is no Hermes Gateway process, no local Hermes HTTP port, and no Gateway
service install prompt in this integration path.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import app_config, save_config, settings
from core.skill_registry import opencode_skill_dirs

router = APIRouter()
_active_hermes_processes: dict[str, asyncio.subprocess.Process] = {}
_aborted_hermes_keys: set[str] = set()


class HermesChatRequest(BaseModel):
    message: str
    model: Optional[str] = None
    conversation_id: Optional[str] = None
    system: Optional[str] = None
    skills: Optional[List[str]] = None


def _default_install_dir() -> Path:
    configured = (app_config.hermes.install_dir or "").strip()
    if configured:
        return Path(configured).expanduser()
    return settings.BASE_DIR / "hermes-agent"


def _bridge_config_path() -> Path:
    return settings.DATA_DIR / "hermes" / "codebot_bridge.json"


def _hermes_home_dir() -> Path:
    return settings.DATA_DIR / "hermes" / "home"


def _hermes_home_skills_dir() -> Path:
    return _hermes_home_dir() / "skills"


def _hermes_venv_dir(install_dir: Path) -> Path:
    return install_dir / ".venv"


def _hermes_venv_python(install_dir: Path) -> Path:
    venv_dir = _hermes_venv_dir(install_dir)
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _hermes_python() -> str:
    python = _hermes_venv_python(_default_install_dir())
    return str(python) if python.exists() else sys.executable


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def _runtime_codebot_port() -> int:
    raw = os.environ.get("CODEBOT_BACKEND_PORT", "").strip()
    if raw:
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
        except Exception:
            pass
    return int(app_config.network.port or 8080)


def _codebot_app_urls() -> dict:
    port = _runtime_codebot_port()
    return {
        "local_url": f"http://127.0.0.1:{port}",
        "lan_url": f"http://{_lan_ip()}:{port}",
    }


def _codebot_openai_base_url() -> str:
    return f"http://127.0.0.1:{_runtime_codebot_port()}/v1"


def _codebot_openai_api_key() -> str:
    return (settings.APP_TOKEN or "codebot").strip() or "codebot"


def _model_for_chat_tasks(model_override: Optional[str] = None) -> str:
    return (model_override or "").strip() or app_config.general.chat_default_model or app_config.models.primary_model or ""


def _model_for_background_tasks() -> str:
    return app_config.memory.organize_model or _model_for_chat_tasks()


def _hermes_skill_dirs() -> List[str]:
    dirs: List[str] = [str(_hermes_home_skills_dir())]
    dirs.extend(_hermes_external_skill_dirs())
    return list(dict.fromkeys(dirs))


def _hermes_external_skill_dirs() -> List[str]:
    dirs: List[str] = [str(settings.SKILLS_DIR)]
    for raw in app_config.hermes.skill_dirs or []:
        value = (raw or "").strip()
        if value:
            dirs.append(value)
    dirs.extend(str(path) for path in opencode_skill_dirs())
    install_dir = _default_install_dir()
    for candidate in [
        install_dir / "skills",
        install_dir / "optional-skills",
    ]:
        dirs.append(str(candidate))
    return list(dict.fromkeys(dirs))


def write_bridge_config(chat_model_override: Optional[str] = None) -> Path:
    target = _bridge_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": datetime.now().isoformat(),
        "mode": "cli",
        "opencode_server_url": app_config.opencode.server_url,
        "codebot_openai_base_url": _codebot_openai_base_url(),
        "codebot_openai_api_key_source": "APP_TOKEN" if settings.APP_TOKEN else "codebot-default",
        "chat_default_model": app_config.general.chat_default_model,
        "active_chat_model": _model_for_chat_tasks(chat_model_override),
        "background_model": _model_for_background_tasks(),
        "memory_db": str(settings.CONVERSATIONS_DB),
        "scheduler_db": str(settings.SCHEDULED_TASKS_DB),
        "skills_dir": str(settings.SKILLS_DIR),
        "hermes_skill_dirs": _hermes_skill_dirs(),
        "obsidian": app_config.obsidian.model_dump(),
        "share_models": app_config.hermes.share_models,
        "share_memory": app_config.hermes.share_memory,
        "share_scheduler": app_config.hermes.share_scheduler,
    }
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _write_hermes_home_config(chat_model_override: Optional[str] = None) -> Path:
    home = _hermes_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    base_url = _codebot_openai_base_url()
    api_key = _codebot_openai_api_key()
    chat_model = _model_for_chat_tasks(chat_model_override)
    background_model = _model_for_background_tasks()
    auxiliary_model = {
        "provider": "custom",
        "model": background_model,
        "base_url": base_url,
        "api_key": api_key,
        "api_mode": "chat_completions",
    }
    config = {
        "model": {
            "provider": "custom",
            "default": chat_model,
            "base_url": base_url,
            "api_key": api_key,
            "api_mode": "chat_completions",
        },
        "auxiliary": {
            "compression": auxiliary_model,
            "web_extract": auxiliary_model,
            "approval": auxiliary_model,
        },
        "skills": {
            "external_dirs": _hermes_external_skill_dirs(),
        },
    }
    target = home / "config.yaml"
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    env_path = home / ".env"
    env_lines = [
        f"OPENAI_BASE_URL={base_url}",
        f"OPENAI_API_KEY={api_key}",
        f"HERMES_CODEBOT_BRIDGE_CONFIG={_bridge_config_path()}",
        f"HERMES_CODEBOT_CHAT_MODEL={chat_model}",
        f"HERMES_CODEBOT_BACKGROUND_MODEL={background_model}",
    ]
    obsidian_vault = (app_config.obsidian.vault_path or "").strip()
    if obsidian_vault:
        env_lines.append(f"OBSIDIAN_VAULT_PATH={obsidian_vault}")
    env_path.write_text("\n".join([*env_lines, ""]), encoding="utf-8")
    return target


def _hermes_env(chat_model_override: Optional[str] = None) -> dict:
    env = os.environ.copy()
    install_dir = _default_install_dir()
    extra_paths = []
    for candidate in [
        install_dir,
        install_dir / ".venv" / "Scripts",
        install_dir / ".venv" / "bin",
        install_dir / "Scripts",
        install_dir / "bin",
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin",
    ]:
        if candidate.exists():
            extra_paths.append(str(candidate))
    if extra_paths:
        env["PATH"] = os.pathsep.join([*extra_paths, env.get("PATH", "")])

    env["HERMES_HOME"] = str(_hermes_home_dir())
    env["OPENAI_BASE_URL"] = _codebot_openai_base_url()
    env["OPENAI_API_KEY"] = _codebot_openai_api_key()
    env["HERMES_INFERENCE_PROVIDER"] = "custom"
    chat_model = _model_for_chat_tasks(chat_model_override)
    if chat_model:
        env["HERMES_INFERENCE_MODEL"] = chat_model
    else:
        env.pop("HERMES_INFERENCE_MODEL", None)
    env["HERMES_CODEBOT_MODE"] = "1"
    env["HERMES_CODEBOT_BRIDGE_CONFIG"] = str(write_bridge_config(chat_model_override))
    env["HERMES_CODEBOT_MEMORY_DB"] = str(settings.CONVERSATIONS_DB)
    env["HERMES_CODEBOT_SCHEDULER_DB"] = str(settings.SCHEDULED_TASKS_DB)
    env["HERMES_CODEBOT_CHAT_MODEL"] = chat_model
    env["HERMES_CODEBOT_BACKGROUND_MODEL"] = _model_for_background_tasks()
    obsidian_vault = (app_config.obsidian.vault_path or "").strip()
    if obsidian_vault:
        env["OBSIDIAN_VAULT_PATH"] = obsidian_vault
    return env


def _resolve_hermes_cli() -> str:
    configured = (app_config.hermes.cli_path or "hermes").strip() or "hermes"
    configured_path = Path(configured).expanduser()
    if configured_path.is_absolute() or any(sep in configured for sep in ("/", "\\")):
        if configured_path.exists() and configured_path.is_file():
            return str(configured_path)
        if configured_path.exists() and configured_path.is_dir():
            raise HTTPException(status_code=400, detail=f"无法启动 Hermes：配置的命令指向目录，不是可执行文件：{configured}")
        raise HTTPException(status_code=404, detail=f"无法启动 Hermes：配置的命令不存在：{configured}")

    env = _hermes_env()
    found = shutil.which(configured, path=env.get("PATH"))
    if found:
        return found

    install_dir = _default_install_dir()
    names = [configured]
    if sys.platform.startswith("win") and not configured.lower().endswith((".exe", ".cmd", ".bat")):
        names = [f"{configured}.exe", f"{configured}.cmd", f"{configured}.bat", configured]
    for parent in [
        install_dir,
        install_dir / ".venv" / "Scripts",
        install_dir / ".venv" / "bin",
        install_dir / "Scripts",
        install_dir / "bin",
    ]:
        for name in names:
            candidate = parent / name
            if candidate.exists():
                return str(candidate)

    raise HTTPException(
        status_code=404,
        detail="无法启动 Hermes：找不到 hermes 命令。请先点击“一键安装 Hermes Agent”，或配置 Hermes 命令的绝对路径。",
    )


def _read_shebang(path: Path) -> str:
    try:
        with path.open("rb") as f:
            first_line = f.readline(256).decode("utf-8", errors="ignore").strip().lower()
        return first_line if first_line.startswith("#!") else ""
    except Exception:
        return ""


def _find_executable(name: str) -> str:
    found = shutil.which(name, path=_hermes_env().get("PATH"))
    return found or name


def _windows_wrapped_command(executable: str, args: List[str]) -> List[str]:
    path = Path(executable)
    suffix = path.suffix.lower()
    if suffix in {".exe", ".com"}:
        return [executable, *args]
    if suffix in {".cmd", ".bat"}:
        return [os.environ.get("ComSpec") or "cmd.exe", "/d", "/s", "/c", executable, *args]
    if suffix == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", executable, *args]
    if suffix in {".py", ".pyw"}:
        return [_hermes_python(), executable, *args]
    if suffix in {".js", ".mjs", ".cjs"}:
        return [_find_executable("node"), executable, *args]
    if suffix == ".sh":
        bash = shutil.which("bash", path=_hermes_env().get("PATH"))
        if bash:
            return [bash, executable, *args]

    shebang = _read_shebang(path)
    if "python" in shebang:
        return [_hermes_python(), executable, *args]
    if "node" in shebang:
        return [_find_executable("node"), executable, *args]
    if "powershell" in shebang or "pwsh" in shebang:
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", executable, *args]
    if "bash" in shebang or "sh" in shebang:
        bash = shutil.which("bash", path=_hermes_env().get("PATH"))
        if bash:
            return [bash, executable, *args]

    raise HTTPException(
        status_code=400,
        detail=f"无法启动 Hermes：解析到的命令不是 Windows 可直接执行的文件：{executable}",
    )


def _hermes_command(args: List[str]) -> List[str]:
    cli = _resolve_hermes_cli()
    if sys.platform.startswith("win"):
        return _windows_wrapped_command(cli, args)
    return [cli, *args]


async def abort_hermes_conversation(conversation_id: str) -> bool:
    key = str(conversation_id or "").strip()
    if not key:
        return False
    process = _active_hermes_processes.get(key)
    if not process:
        return False
    _aborted_hermes_keys.add(key)
    try:
        if process.returncode is None:
            process.kill()
        return True
    except Exception:
        return False


async def _run_command(
    args: List[str],
    cwd: Path,
    timeout: int = 900,
    chat_model_override: Optional[str] = None,
    cancellation_key: Optional[str] = None,
) -> dict:
    _write_hermes_home_config(chat_model_override)
    env = _hermes_env(chat_model_override)
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    key = str(cancellation_key or "").strip()
    if key:
        _active_hermes_processes[key] = process
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        raise HTTPException(status_code=504, detail="Hermes CLI 执行超时")
    finally:
        if key:
            _active_hermes_processes.pop(key, None)
    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    aborted = bool(key and key in _aborted_hermes_keys)
    if aborted:
        _aborted_hermes_keys.discard(key)
        return {"returncode": -999, "output": "任务已被用户终止", "aborted": True}
    return {"returncode": process.returncode, "output": output[-12000:]}


def _latest_request_dump_summary(since: Optional[datetime] = None) -> str:
    dump_dir = _hermes_home_dir() / "sessions"
    try:
        dumps = sorted(dump_dir.glob("request_dump_*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    except Exception:
        return ""

    threshold = since.timestamp() - 2 if since else None
    for dump in dumps[:5]:
        try:
            if threshold is not None and dump.stat().st_mtime < threshold:
                continue
            data = json.loads(dump.read_text(encoding="utf-8"))
            request = data.get("request") if isinstance(data.get("request"), dict) else {}
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            pieces = []
            if data.get("reason"):
                pieces.append(f"reason={data.get('reason')}")
            if request.get("url"):
                pieces.append(f"url={request.get('url')}")
            if error.get("status_code") or error.get("response_status"):
                pieces.append(f"status={error.get('status_code') or error.get('response_status')}")
            if error.get("message"):
                pieces.append(f"error={str(error.get('message'))[:500]}")
            return "Hermes request dump: " + ", ".join(pieces) if pieces else ""
        except Exception:
            continue
    return ""


async def _missing_hermes_modules(python: Path, modules: List[str], cwd: Path) -> List[str]:
    script = (
        "import importlib.util, sys; "
        "missing=[m for m in sys.argv[1:] if importlib.util.find_spec(m) is None]; "
        "print('\\n'.join(missing)); "
        "sys.exit(1 if missing else 0)"
    )
    result = await _run_command([str(python), "-c", script, *modules], cwd=cwd, timeout=60)
    return [line.strip() for line in result.get("output", "").splitlines() if line.strip()]


async def _ensure_hermes_runtime_ready(install_dir: Path, force_install: bool = False) -> dict:
    install_dir = install_dir.resolve()
    outputs: List[str] = []
    _write_hermes_home_config()
    python = _hermes_venv_python(install_dir)
    if not python.exists():
        result = await _run_command([sys.executable, "-m", "venv", str(_hermes_venv_dir(install_dir))], cwd=install_dir, timeout=300)
        outputs.append(result.get("output", ""))
        if result.get("returncode") != 0:
            return {"returncode": result.get("returncode", 1), "output": "\n".join(outputs) or "Failed to create Hermes virtual environment"}

    required_modules = ["openai", "aiohttp", "fastapi", "uvicorn"]
    missing = await _missing_hermes_modules(python, required_modules, install_dir)
    if force_install or missing:
        if missing:
            outputs.append(f"Missing Hermes Python modules: {', '.join(missing)}")
        result = await _run_command([str(python), "-m", "pip", "install", "--upgrade", "pip"], cwd=install_dir, timeout=900)
        outputs.append(result.get("output", ""))
        if result.get("returncode") != 0:
            return {"returncode": result.get("returncode", 1), "output": "\n".join(outputs)}

        result = await _run_command([str(python), "-m", "pip", "install", "-e", ".[messaging]"], cwd=install_dir, timeout=1800)
        outputs.append(result.get("output", ""))
        if result.get("returncode") != 0:
            return {"returncode": result.get("returncode", 1), "output": "\n".join(outputs)}

    missing_after = await _missing_hermes_modules(python, required_modules, install_dir)
    if missing_after:
        outputs.append(f"Hermes Python modules still missing: {', '.join(missing_after)}")
        return {"returncode": 1, "output": "\n".join(outputs)}

    outputs.append(f"Hermes CLI runtime ready: {python}")
    return {"returncode": 0, "output": "\n".join(part for part in outputs if part)}


def _dir_has_content(path: Path) -> bool:
    try:
        return path.exists() and any(path.iterdir())
    except Exception:
        return False


def _has_repo_checkout(install_dir: Path) -> bool:
    return (install_dir / ".git").exists() or (install_dir / "pyproject.toml").exists() or (install_dir / "cli.py").exists()


def _has_only_managed_runtime(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    try:
        entries = [item.name for item in path.iterdir()]
    except Exception:
        return False
    return bool(entries) and all(name in {".venv", "__pycache__"} for name in entries)


def _remove_managed_runtime_shell(path: Path) -> None:
    for name in [".venv", "__pycache__"]:
        target = path / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


async def _ensure_repo_checkout(install_dir: Path) -> dict:
    repo_url = (app_config.hermes.repo_url or "https://github.com/NousResearch/hermes-agent").strip()
    install_dir = install_dir.resolve()
    git_dir = install_dir / ".git"
    if git_dir.exists():
        return await _run_command(["git", "pull", "--ff-only"], cwd=install_dir, timeout=300)

    if _dir_has_content(install_dir):
        if _has_only_managed_runtime(install_dir):
            _remove_managed_runtime_shell(install_dir)
        else:
            return {
                "returncode": 1,
                "output": f"Install directory already has files and is not a git checkout: {install_dir}",
            }

    if _dir_has_content(install_dir):
        return {
            "returncode": 1,
            "output": f"Install directory already has files and is not a git checkout: {install_dir}",
        }

    install_dir.parent.mkdir(parents=True, exist_ok=True)
    if install_dir.exists():
        try:
            install_dir.rmdir()
        except OSError:
            pass
    return await _run_command(["git", "clone", repo_url, install_dir.name], cwd=install_dir.parent, timeout=900)


async def _run_hermes_action(action: str) -> dict:
    install_dir = _default_install_dir()
    install_dir.mkdir(parents=True, exist_ok=True)
    repo_result = None
    if action == "install":
        repo_result = await _ensure_repo_checkout(install_dir)
        if repo_result.get("returncode") != 0:
            app_config.hermes.install_dir = str(install_dir)
            app_config.hermes.last_action = action
            app_config.hermes.last_status = "failed"
            app_config.hermes.last_message = repo_result.get("output", "")
            save_config(app_config)
            raise HTTPException(status_code=500, detail=repo_result.get("output") or "Hermes repository clone failed")
        result = await _ensure_hermes_runtime_ready(install_dir, force_install=True)
    elif action == "repair":
        result = await _ensure_hermes_runtime_ready(install_dir, force_install=True)
    elif action == "update":
        repo_result = await _ensure_repo_checkout(install_dir)
        if repo_result.get("returncode") != 0:
            app_config.hermes.install_dir = str(install_dir)
            app_config.hermes.last_action = action
            app_config.hermes.last_status = "failed"
            app_config.hermes.last_message = repo_result.get("output", "")
            save_config(app_config)
            raise HTTPException(status_code=500, detail=repo_result.get("output") or "Hermes repository update failed")
        result = await _ensure_hermes_runtime_ready(install_dir, force_install=True)
    else:
        raise HTTPException(status_code=400, detail="不支持的 Hermes 操作")

    if repo_result:
        result["repo_output"] = repo_result.get("output", "")
    app_config.hermes.install_dir = str(install_dir)
    app_config.hermes.last_action = action
    app_config.hermes.last_status = "success" if result["returncode"] == 0 else "failed"
    app_config.hermes.last_message = result["output"]
    save_config(app_config)
    if result["returncode"] != 0:
        raise HTTPException(status_code=500, detail=result["output"] or f"Hermes {action} failed")
    return result


async def prepare_cli_if_enabled() -> None:
    if not (app_config.hermes.enabled and app_config.hermes.auto_start):
        return
    try:
        install_dir = _default_install_dir()
        if not _has_repo_checkout(install_dir):
            write_bridge_config()
            _write_hermes_home_config()
            app_config.hermes.last_action = "prepare_cli"
            app_config.hermes.last_status = "not_installed"
            app_config.hermes.last_message = "Hermes Agent is not installed yet. Use one-click install in Settings > Hermes."
            save_config(app_config)
            return
        await _ensure_hermes_runtime_ready(install_dir)
        app_config.hermes.last_action = "prepare_cli"
        app_config.hermes.last_status = "success"
        app_config.hermes.last_message = f"Hermes CLI ready: {_hermes_venv_python(install_dir)}"
        save_config(app_config)
    except Exception as exc:
        app_config.hermes.last_action = "prepare_cli"
        app_config.hermes.last_status = "failed"
        app_config.hermes.last_message = str(exc)
        save_config(app_config)


async def _run_hermes_oneshot(
    message: str,
    model: str,
    system: Optional[str],
    conversation_id: Optional[str],
    skills: Optional[List[str]] = None,
) -> str:
    install_dir = _default_install_dir()
    if not _has_repo_checkout(install_dir):
        raise HTTPException(status_code=404, detail="Hermes Agent 未安装，请先在设置 > Hermes 中点击一键安装。")
    runtime = await _ensure_hermes_runtime_ready(install_dir)
    if runtime.get("returncode") != 0:
        raise HTTPException(status_code=500, detail=runtime.get("output") or "Hermes CLI runtime is not ready")

    write_bridge_config(model)
    # Hermes oneshot does not expose a separate system-prompt channel.
    # Keep Codebot bridge/config data in env + HERMES_HOME/config.yaml so the
    # user prompt stays pure and cannot be echoed as assistant-visible text.
    prompt = message.strip()

    command_args = ["-z", prompt]
    if model:
        command_args.extend(["--provider", "custom", "--model", model])
    for skill_name in skills or []:
        value = (skill_name or "").strip()
        if value:
            command_args.extend(["--skills", value])

    started_at = datetime.now()
    result = await _run_command(
        _hermes_command(command_args),
        cwd=install_dir,
        timeout=900,
        chat_model_override=model,
        cancellation_key=conversation_id,
    )
    output = (result.get("output") or "").strip()
    if result.get("aborted"):
        raise HTTPException(status_code=499, detail=output or "任务已被用户终止")
    if result.get("returncode") != 0:
        detail = output or "Hermes CLI 调用失败"
        dump_summary = _latest_request_dump_summary(since=started_at)
        if dump_summary:
            detail = f"{detail}\n{dump_summary}"
        raise HTTPException(status_code=502, detail=detail)
    if not output:
        raise HTTPException(status_code=502, detail="Hermes CLI 没有返回可显示内容")
    return output


@router.get("/status")
async def hermes_status():
    install_dir = _default_install_dir()
    bridge = write_bridge_config()
    checkout_ready = (install_dir / ".git").exists() or _dir_has_content(install_dir)
    return {
        "success": True,
        "data": {
            "config": app_config.hermes.model_dump(),
            "mode": "cli",
            "install_dir": str(install_dir),
            "hermes_home": str(_hermes_home_dir()),
            "runtime_python": str(_hermes_venv_python(install_dir)),
            "codebot_openai_base_url": _codebot_openai_base_url(),
            "active_chat_model": _model_for_chat_tasks(),
            "background_model": _model_for_background_tasks(),
            "installed": checkout_ready,
            "checkout_ready": checkout_ready,
            "codebot_app": _codebot_app_urls(),
            "bridge_config_path": str(bridge),
            "skill_dirs": _hermes_skill_dirs(),
        },
    }


@router.post("/chat")
async def hermes_chat(request: HermesChatRequest):
    message = (request.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")
    if not app_config.hermes.enabled:
        raise HTTPException(status_code=400, detail="Hermes 未启用，请先在设置中开启 Hermes。")

    model = _model_for_chat_tasks(request.model)
    _write_hermes_home_config(chat_model_override=model)
    content = await _run_hermes_oneshot(
        message=message,
        model=model,
        system=request.system,
        conversation_id=request.conversation_id,
        skills=request.skills,
    )
    return {
        "success": True,
        "data": {
            "content": content,
            "model": model,
            "mode": "cli",
            "runtime_python": str(_hermes_venv_python(_default_install_dir())),
        },
    }


@router.post("/install")
async def install_hermes():
    result = await _run_hermes_action("install")
    return {"success": True, "data": result, "message": "Hermes Agent 安装完成"}


@router.post("/repair")
async def repair_hermes():
    result = await _run_hermes_action("repair")
    return {"success": True, "data": result, "message": "Hermes Agent 修复检查完成"}


@router.post("/update")
async def update_hermes():
    result = await _run_hermes_action("update")
    return {"success": True, "data": result, "message": "Hermes Agent 更新完成"}


@router.post("/sync")
async def sync_hermes_bridge():
    bridge = write_bridge_config()
    _write_hermes_home_config()
    return {"success": True, "data": {"bridge_config_path": str(bridge)}, "message": "Hermes 共享配置已同步"}
