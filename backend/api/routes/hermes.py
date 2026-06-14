"""
Hermes Agent CLI integration.

This module is intentionally a thin adapter around the Hermes CLI.  Codebot
does not import Hermes' internal agent classes here; it only prepares the local
runtime, writes shared Codebot context, starts the CLI process, streams terminal
output, and forwards explicit user replies to stdin when the CLI asks for input.
"""
from __future__ import annotations

import asyncio
import codecs
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from config import app_config, save_config, settings
from core.skill_registry import (
    get_skill_registry,
    hermes_excluded_auto_skill_dirs,
    hermes_native_skill_dirs,
    hermes_repo_skill_dirs,
    hermes_skill_dirs,
    opencode_skill_dirs,
)

router = APIRouter()

_active_processes: Dict[str, asyncio.subprocess.Process] = {}
_aborted_keys: set[str] = set()
_pending_replies: Dict[str, asyncio.Queue] = {}
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_BOX_BORDER_RE = re.compile(r"^[\s\u2500-\u257f\u2580-\u259f]+$")


# #region debug-point A:report
def _debug_report(hypothesis_id: str, location: str, msg: str, data: Optional[Dict[str, Any]] = None) -> None:
    try:
        debug_url = "http://127.0.0.1:7777/event"
        session_id = "hermes-skill-stuck"
        env_dir = settings.BASE_DIR / ".dbg"
        env_candidates = [
            env_dir / "hermes-skill-stuck.env",
            env_dir / "hermes-cli-stuck.env",
        ]
        env_candidates.extend(sorted(env_dir.glob("hermes-*.env"), reverse=True) if env_dir.exists() else [])
        for env_path in env_candidates:
            if not env_path.exists():
                continue
            content = env_path.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                if line.startswith("DEBUG_SERVER_URL="):
                    debug_url = line.split("=", 1)[1].strip() or debug_url
                elif line.startswith("DEBUG_SESSION_ID="):
                    session_id = line.split("=", 1)[1].strip() or session_id
            if session_id and debug_url:
                break
        payload = {
            "sessionId": session_id,
            "runId": "pre",
            "hypothesisId": hypothesis_id,
            "location": location,
            "msg": f"[DEBUG] {msg}",
            "data": data or {},
        }
        urllib.request.urlopen(
            urllib.request.Request(
                debug_url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            ),
            timeout=1,
        ).read()
    except Exception:
        pass


# #endregion


class HermesChatRequest(BaseModel):
    message: str
    model: Optional[str] = None
    conversation_id: Optional[str] = None
    system: Optional[str] = None
    skills: Optional[List[str]] = None


def _runtime_backend_port() -> int:
    raw = os.environ.get("CODEBOT_BACKEND_PORT", "").strip()
    if raw:
        try:
            return int(raw)
        except Exception:
            pass
    return int(getattr(app_config.network, "port", 15682) or 15682)


def _local_codebot_url() -> str:
    return f"http://127.0.0.1:{_runtime_backend_port()}"


def _lan_codebot_url() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return f"http://{ip}:{_runtime_backend_port()}"


def _codebot_openai_base_url() -> str:
    return f"{_local_codebot_url()}/v1"


def _codebot_openai_api_key() -> str:
    return os.environ.get("CODEBOT_GATEWAY_API_KEY", "codebot")


def _default_install_dir() -> Path:
    configured = (app_config.hermes.install_dir or "").strip()
    if configured:
        return Path(configured).expanduser()
    return settings.BASE_DIR / "hermes-agent"


def _hermes_home_dir() -> Path:
    return settings.DATA_DIR / "hermes" / "home"


def _bridge_config_path() -> Path:
    return settings.DATA_DIR / "hermes" / "codebot_bridge.json"


def _hermes_venv_dir(install_dir: Path) -> Path:
    return install_dir / ".venv"


def _hermes_venv_python(install_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return _hermes_venv_dir(install_dir) / "Scripts" / "python.exe"
    return _hermes_venv_dir(install_dir) / "bin" / "python"


def _is_codebot_backend_executable(value: str) -> bool:
    if not value:
        return False
    name = Path(value).name.lower()
    return "codebot" in name and "backend" in name and name.endswith((".exe", ".bin"))


def _base_python_candidates() -> List[str]:
    candidates: List[str] = []
    explicit = (os.environ.get("HERMES_PYTHON") or os.environ.get("PYTHON") or "").strip()
    if explicit:
        candidates.append(explicit)
    if not _is_codebot_backend_executable(sys.executable):
        candidates.append(sys.executable)
    if sys.platform.startswith("win"):
        candidates.extend(["py", "python", "python3"])
    else:
        candidates.extend(["python3", "python"])

    result: List[str] = []
    seen = set()
    for candidate in candidates:
        value = shutil.which(candidate) or candidate
        key = value.lower()
        if key in seen or _is_codebot_backend_executable(value):
            continue
        seen.add(key)
        result.append(value)
    return result


async def _select_base_python() -> str:
    outputs: List[str] = []
    for candidate in _base_python_candidates():
        result = await _run_command([candidate, "--version"], timeout=30)
        output = (result.get("output") or "").strip()
        if result.get("returncode") == 0:
            return candidate
        outputs.append(f"{candidate}: {output}")
    detail = "\n".join(outputs) or "No Python executable candidates were found."
    raise HTTPException(status_code=500, detail=f"无法创建 Hermes venv：未找到可用 Python。\n{detail}")


def _model_for_chat_tasks(model: Optional[str] = None) -> str:
    return (
        (model or "").strip()
        or (app_config.general.chat_default_model or "").strip()
        or (app_config.models.primary_model or "").strip()
        or (app_config.memory.organize_model or "").strip()
    )


def _model_for_background_tasks() -> str:
    return (
        (app_config.memory.organize_model or "").strip()
        or (app_config.general.chat_default_model or "").strip()
        or (app_config.models.primary_model or "").strip()
    )


def _dedupe_dir_values(dirs: List[str]) -> List[str]:
    """Normalize and deduplicate absolute skill directories for Hermes."""
    seen = set()
    result: List[str] = []
    for item in dirs:
        value = str(item or "").strip()
        if not value:
            continue
        try:
            key = str(Path(value).expanduser().resolve()).lower()
            normalized = str(Path(value).expanduser().resolve())
        except Exception:
            key = value.lower()
            normalized = value
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def _manual_skill_dirs() -> List[str]:
    dirs: List[str] = []
    for raw in getattr(app_config.hermes, "skill_dirs", []) or []:
        value = str(raw or "").strip()
        if value:
            dirs.append(value)
    return _dedupe_dir_values(dirs)


def _all_auto_shared_skill_dirs() -> List[str]:
    dirs: List[str] = []
    builtin_root = settings.SKILLS_DIR
    if builtin_root.exists():
        dirs.append(str(builtin_root))
    # Auto-share both Hermes' runtime skill root and repo-bundled official
    # skill catalogs by default. Users can exclude any unwanted auto-shared
    # directory from Codebot settings.
    dirs.extend(str(path) for path in hermes_native_skill_dirs(include_excluded=True))
    dirs.extend(str(path) for path in opencode_skill_dirs())
    return _dedupe_dir_values(dirs)


def _shared_skill_dirs() -> List[str]:
    excluded = set(_dedupe_dir_values(hermes_excluded_auto_skill_dirs()))
    if not excluded:
        return _all_auto_shared_skill_dirs()
    return [path for path in _all_auto_shared_skill_dirs() if path not in excluded]


def _configured_skill_dirs(selected_skills: Optional[List[str]] = None) -> List[str]:
    """Return the effective Hermes skill roots.

    When the user chooses Hermes, Codebot builtin skills and OpenCode shared
    skills are exposed as Hermes' own external skill directories. Explicit
    `--skills` should also narrow which external skill directories are mounted,
    otherwise Hermes has to rescan every shared root even when the user already
    pointed to a specific skill.
    """
    selected_names = [str(skill).strip() for skill in (selected_skills or []) if str(skill).strip()]
    if not selected_names:
        return _dedupe_dir_values([*_manual_skill_dirs(), *_shared_skill_dirs()])

    registry = get_skill_registry()
    chosen_dirs: List[str] = []
    for item in registry.list_skills(include_content=True):
        item_id = str(item.get("id") or "").strip()
        slug = str(item.get("slug") or "").strip()
        name = str(item.get("name") or "").strip()
        skill_dir = str(item.get("path") or "").strip()
        if not skill_dir:
            continue
        if not any(chosen in {item_id, slug, name} for chosen in selected_names):
            continue
        # Hermes scans external_dirs recursively with os.walk, so mounting the
        # exact skill directory keeps the selected skill loadable while avoiding
        # a full rescan of every shared root.
        chosen_dirs.append(skill_dir)

    result = _dedupe_dir_values(chosen_dirs)
    if not result:
        # Fall back to the legacy full-share behavior if we fail to map the
        # requested skill name back to a concrete directory.
        result = _dedupe_dir_values([*_manual_skill_dirs(), *_shared_skill_dirs()])
    return result


def write_bridge_config(chat_model_override: Optional[str] = None, selected_skills: Optional[List[str]] = None) -> Path:
    """Write the shared Codebot context consumed by Hermes skills/tools."""
    target = _bridge_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "generated_at": datetime.now().isoformat(),
        "codebot": {
            "local_url": _local_codebot_url(),
            "lan_url": _lan_codebot_url(),
            "openai_base_url": _codebot_openai_base_url(),
            "openai_api_key": _codebot_openai_api_key(),
        },
        "models": {
            "provider": "custom",
            "chat": _model_for_chat_tasks(chat_model_override),
            "background": _model_for_background_tasks(),
        },
        "memory": {
            "enabled": bool(app_config.hermes.share_memory),
            "conversations_db": str(settings.CONVERSATIONS_DB),
            "chroma_dir": str(settings.CHROMA_DIR),
        },
        "scheduler": {
            "enabled": bool(app_config.hermes.share_scheduler),
            "tasks_db": str(settings.SCHEDULED_TASKS_DB),
        },
        "skills": {
            "external_dirs": _configured_skill_dirs(selected_skills),
            "hermes_roots": [str(path) for path in hermes_skill_dirs()],
            "opencode_roots": [str(path) for path in opencode_skill_dirs()],
            "requested_skills": [str(skill).strip() for skill in (selected_skills or []) if str(skill).strip()],
        },
        "obsidian": {
            "vault_path": (app_config.obsidian.vault_path or "").strip(),
            "knowledge_bases": [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in (app_config.obsidian.knowledge_bases or [])
            ],
        },
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def _write_hermes_home_config(
    chat_model_override: Optional[str] = None,
    selected_skills: Optional[List[str]] = None,
) -> Path:
    home = _hermes_home_dir()
    home.mkdir(parents=True, exist_ok=True)
    bridge_path = write_bridge_config(chat_model_override, selected_skills)
    chat_model = _model_for_chat_tasks(chat_model_override)
    background_model = _model_for_background_tasks()
    base_url = _codebot_openai_base_url()
    api_key = _codebot_openai_api_key()

    config = {
        "model": {
            "provider": "custom",
            "default": chat_model,
            "base_url": base_url,
            "api_key": api_key,
            "api_mode": "chat_completions",
        },
        "auxiliary": {
            "compression": {
                "provider": "custom",
                "model": background_model,
                "base_url": base_url,
                "api_key": api_key,
                "api_mode": "chat_completions",
            },
            "web_extract": {
                "provider": "custom",
                "model": background_model,
                "base_url": base_url,
                "api_key": api_key,
                "api_mode": "chat_completions",
            },
            "approval": {
                "provider": "custom",
                "model": background_model,
                "base_url": base_url,
                "api_key": api_key,
                "api_mode": "chat_completions",
            },
        },
        "skills": {
            "external_dirs": _configured_skill_dirs(selected_skills),
        },
        "codebot": {
            "bridge_config": str(bridge_path),
            "memory_enabled": bool(app_config.hermes.share_memory),
            "scheduler_enabled": bool(app_config.hermes.share_scheduler),
        },
    }
    config_path = home / "config.yaml"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    env_lines = [
        f"OPENAI_BASE_URL={base_url}",
        f"OPENAI_API_KEY={api_key}",
        "HERMES_INFERENCE_PROVIDER=custom",
        f"HERMES_INFERENCE_MODEL={chat_model}",
        f"HERMES_CODEBOT_BRIDGE_CONFIG={bridge_path}",
        f"HERMES_CODEBOT_MEMORY_DB={settings.CONVERSATIONS_DB}",
        f"HERMES_CODEBOT_SCHEDULER_DB={settings.SCHEDULED_TASKS_DB}",
        f"HERMES_CODEBOT_CHAT_MODEL={chat_model}",
        f"HERMES_CODEBOT_BACKGROUND_MODEL={background_model}",
    ]
    if app_config.obsidian.vault_path:
        env_lines.append(f"OBSIDIAN_VAULT_PATH={app_config.obsidian.vault_path}")
    (home / ".env").write_text("\n".join([*env_lines, ""]), encoding="utf-8")
    return config_path


def _extra_path_entries(install_dir: Path) -> List[str]:
    candidates = [
        install_dir,
        install_dir / ".venv" / "Scripts",
        install_dir / ".venv" / "bin",
        install_dir / "Scripts",
        install_dir / "bin",
        Path.home() / ".local" / "bin",
        Path.home() / ".cargo" / "bin",
    ]
    return [str(path) for path in candidates if path.exists()]


def _hermes_env(chat_model_override: Optional[str] = None, selected_skills: Optional[List[str]] = None) -> Dict[str, str]:
    install_dir = _default_install_dir()
    env = os.environ.copy()
    extra_paths = _extra_path_entries(install_dir)
    if extra_paths:
        env["PATH"] = os.pathsep.join([*extra_paths, env.get("PATH", "")])
    env["HERMES_HOME"] = str(_hermes_home_dir())
    env["OPENAI_BASE_URL"] = _codebot_openai_base_url()
    env["OPENAI_API_KEY"] = _codebot_openai_api_key()
    env["HERMES_INFERENCE_PROVIDER"] = "custom"
    chat_model = _model_for_chat_tasks(chat_model_override)
    if chat_model:
        env["HERMES_INFERENCE_MODEL"] = chat_model
    env["HERMES_CODEBOT_BRIDGE_CONFIG"] = str(write_bridge_config(chat_model_override, selected_skills))
    env["HERMES_CODEBOT_MEMORY_DB"] = str(settings.CONVERSATIONS_DB)
    env["HERMES_CODEBOT_SCHEDULER_DB"] = str(settings.SCHEDULED_TASKS_DB)
    env["HERMES_CODEBOT_CHAT_MODEL"] = chat_model
    env["HERMES_CODEBOT_BACKGROUND_MODEL"] = _model_for_background_tasks()
    env["HERMES_YOLO_MODE"] = "1"
    env["HERMES_ACCEPT_HOOKS"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    if app_config.obsidian.vault_path:
        env["OBSIDIAN_VAULT_PATH"] = app_config.obsidian.vault_path
    return env


def _path_command(path: Path) -> List[str]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return [str(_hermes_venv_python(_default_install_dir())), str(path)]
    if suffix == ".js":
        node = shutil.which("node") or "node"
        return [node, str(path)]
    if suffix == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    if suffix in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/s", "/c", str(path)]
    return [str(path)]


def _resolve_hermes_command() -> List[str]:
    install_dir = _default_install_dir()
    configured = (app_config.hermes.cli_path or "hermes").strip() or "hermes"
    configured_path = Path(configured).expanduser()
    if configured_path.is_absolute() and configured_path.exists():
        return _path_command(configured_path)

    names = [configured]
    if configured == "hermes":
        names.extend(["hermes.exe", "hermes.cmd", "hermes.bat", "hermes.ps1"])
    search_dirs = [
        install_dir / ".venv" / "Scripts",
        install_dir / ".venv" / "bin",
        install_dir / "Scripts",
        install_dir / "bin",
        install_dir,
    ]
    for folder in search_dirs:
        for name in names:
            candidate = folder / name
            if candidate.exists():
                return _path_command(candidate)

    env = _hermes_env()
    found = shutil.which(configured, path=env.get("PATH"))
    if found:
        return _path_command(Path(found))
    raise HTTPException(
        status_code=404,
        detail=f"未找到 Hermes 命令：{configured}。请先一键安装，或在设置中填写 Hermes CLI 绝对路径。",
    )


def _has_repo_checkout(install_dir: Path) -> bool:
    return (
        install_dir.exists()
        and (
            (install_dir / ".git").exists()
            or (install_dir / "pyproject.toml").exists()
            or (install_dir / "cli.py").exists()
        )
    )


async def _run_command(command: List[str], cwd: Optional[Path] = None, timeout: int = 600, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd else None,
            env=env or os.environ.copy(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:
        return {"returncode": 1, "output": str(exc)}
    try:
        output_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return {"returncode": 124, "output": f"Command timed out after {timeout}s: {' '.join(command)}"}
    output = (output_bytes or b"").decode("utf-8", errors="replace")
    return {"returncode": process.returncode, "output": output}


async def _python_is_usable(python: Path) -> Dict[str, Any]:
    if not python.exists():
        return {"ok": False, "output": f"Python not found: {python}"}
    result = await _run_command([str(python), "--version"], timeout=30)
    return {
        "ok": result.get("returncode") == 0,
        "output": result.get("output") or "",
        "returncode": result.get("returncode"),
    }


async def _stop_active_processes(timeout: int = 5) -> None:
    processes = list(_active_processes.items())
    if not processes:
        return
    for key, process in processes:
        _aborted_keys.add(key)
        try:
            process.terminate()
        except Exception:
            pass
    for key, process in processes:
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        finally:
            _active_processes.pop(key, None)


def _remove_venv_dir(install_dir: Path) -> str:
    venv_dir = _hermes_venv_dir(install_dir)
    if not venv_dir.exists():
        return ""
    try:
        shutil.rmtree(venv_dir)
        return f"Removed broken Hermes venv: {venv_dir}"
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"无法重建 Hermes venv：删除旧环境失败。请关闭正在运行的 Hermes/Codebot 进程后重试。\n{venv_dir}\n{exc}",
        )


async def _ensure_repo(install_dir: Path, update: bool = False) -> Dict[str, Any]:
    repo_url = (app_config.hermes.repo_url or "https://github.com/NousResearch/hermes-agent").strip()
    if not _has_repo_checkout(install_dir):
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        return await _run_command(["git", "clone", repo_url, str(install_dir)], timeout=900)
    if update and (install_dir / ".git").exists():
        return await _run_command(["git", "pull", "--ff-only"], cwd=install_dir, timeout=600)
    return {"returncode": 0, "output": "Hermes repository already exists"}


async def _ensure_runtime(install_dir: Path, force_install: bool = False) -> Dict[str, Any]:
    outputs: List[str] = []
    _write_hermes_home_config()
    python = _hermes_venv_python(install_dir)
    python_check = await _python_is_usable(python)
    if python_check.get("ok") and not force_install:
        return {"returncode": 0, "output": f"Hermes runtime ready: {python}\n{python_check.get('output', '').strip()}"}

    if not python_check.get("ok"):
        if python.exists() or _hermes_venv_dir(install_dir).exists():
            removed = _remove_venv_dir(install_dir)
            if removed:
                outputs.append(removed)
        base_python = await _select_base_python()
        result = await _run_command([base_python, "-m", "venv", str(_hermes_venv_dir(install_dir))], cwd=install_dir, timeout=600)
        outputs.append(result.get("output") or "")
        if result.get("returncode") != 0:
            return {"returncode": result.get("returncode", 1), "output": "\n".join(outputs)}
        python_check = await _python_is_usable(python)
        if not python_check.get("ok"):
            outputs.append(python_check.get("output") or f"Created venv but Python is not usable: {python}")
            return {"returncode": python_check.get("returncode", 1), "output": "\n".join(outputs)}

    install_commands = [
        [str(python), "-m", "pip", "install", "-U", "pip", "setuptools", "wheel"],
    ]
    requirements = install_dir / "requirements.txt"
    if requirements.exists():
        install_commands.append([str(python), "-m", "pip", "install", "-r", str(requirements)])
    if (install_dir / "pyproject.toml").exists() or (install_dir / "setup.py").exists():
        install_commands.append([str(python), "-m", "pip", "install", "-e", "."])

    for command in install_commands:
        result = await _run_command(command, cwd=install_dir, timeout=900)
        outputs.append(result.get("output") or "")
        if result.get("returncode") != 0:
            return {"returncode": result.get("returncode", 1), "output": "\n".join(outputs)}

    outputs.append(f"Hermes runtime ready: {python}")
    return {"returncode": 0, "output": "\n".join(item for item in outputs if item)}


async def _run_action(action: str) -> Dict[str, Any]:
    install_dir = _default_install_dir()
    update_repo = action == "update"
    force_install = action in {"install", "repair", "update"}
    await _stop_active_processes()
    repo_result = await _ensure_repo(install_dir, update=update_repo)
    if repo_result.get("returncode") != 0:
        app_config.hermes.install_dir = str(install_dir)
        app_config.hermes.last_action = action
        app_config.hermes.last_status = "failed"
        app_config.hermes.last_message = repo_result.get("output", "")
        save_config(app_config)
        raise HTTPException(status_code=500, detail=repo_result.get("output") or "Hermes repository setup failed")

    runtime_result = await _ensure_runtime(install_dir, force_install=force_install)
    app_config.hermes.install_dir = str(install_dir)
    app_config.hermes.last_action = action
    app_config.hermes.last_status = "success" if runtime_result.get("returncode") == 0 else "failed"
    app_config.hermes.last_message = runtime_result.get("output", "")
    save_config(app_config)
    if runtime_result.get("returncode") != 0:
        raise HTTPException(status_code=500, detail=runtime_result.get("output") or f"Hermes {action} failed")
    return {
        "install_dir": str(install_dir),
        "repo": repo_result,
        "runtime": runtime_result,
        "runtime_python": str(_hermes_venv_python(install_dir)),
        "bridge_config_path": str(write_bridge_config()),
    }


def _build_cli_args(message: str, model: Optional[str], skills: Optional[List[str]]) -> List[str]:
    # Use Hermes' official chat single-query path instead of top-level
    # --oneshot/-z. Hermes documents --oneshot as "stdout = final response only"
    # and it bypasses cli.py entirely, which does not match Codebot's need to
    # mirror real CLI behavior and stream the actual terminal session.
    args = ["--cli", "chat", "-q", message.strip(), "--yolo", "--accept-hooks"]
    effective_model = _model_for_chat_tasks(model)
    if effective_model:
        args.extend(["--provider", "custom", "--model", effective_model])
    for skill in skills or []:
        value = str(skill or "").strip()
        if value:
            args.extend(["--skills", value])
    return args


def _append_args(command: List[str], args: List[str]) -> List[str]:
    if len(command) >= 4 and command[:4] == ["cmd.exe", "/d", "/s", "/c"]:
        return [*command, *args]
    return [*command, *args]


def _last_nonempty_line(text: str) -> str:
    lines = [line.strip() for line in re.split(r"[\r\n]+", text or "") if line.strip()]
    return lines[-1] if lines else (text or "").strip()


def _clean_hermes_terminal_line(line: str) -> Optional[str]:
    text = _ANSI_ESCAPE_RE.sub("", str(line or "").replace("\r", ""))
    text = _CONTROL_CHAR_RE.sub("", text).rstrip()
    stripped = text.strip()
    if not stripped:
        return ""
    stripped = re.sub(r"^\?{3,}\s*", "", stripped)
    stripped = re.sub(r"\s*\?{3,}$", "", stripped)
    if not stripped:
        return ""
    lowered = stripped.lower()
    if lowered.startswith("initializing agent"):
        return None
    if lowered.startswith("resume this session with"):
        return None
    if lowered.startswith("hermes --resume "):
        return None
    if lowered.startswith("session:") or lowered.startswith("duration:") or lowered.startswith("messages:"):
        return None
    if re.match(r"(?i)^query:\s*", stripped):
        return None
    if re.match(r"(?i)^session_id:\s*\S+", stripped):
        return None
    unboxed = re.sub(r"[\u2500-\u257f]+", " ", stripped)
    unboxed = re.sub(r"\s+", " ", unboxed).strip(" ?")
    if unboxed in {"Hermes", "Response", "Assistant", "⚕ Hermes"}:
        return None
    if _BOX_BORDER_RE.fullmatch(stripped):
        return None

    # Hermes single-query mode uses Rich panels. Unwrap their box borders so the
    # chat view shows just the readable text, closer to OpenCode CLI output.
    panel_match = re.match(r"^\s*[│║]\s?(.*?)\s*[│║]\s*$", text)
    if panel_match:
        inner = panel_match.group(1).strip()
        if not inner:
            return ""
        return inner

    return stripped


def _consume_hermes_terminal_stream(buffer: str, *, flush: bool = False) -> tuple[str, str]:
    normalized = str(buffer or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = normalized.split("\n")
    if flush:
        lines = parts
        remainder = ""
    else:
        lines = parts[:-1]
        remainder = parts[-1] if parts else ""

    emitted: List[str] = []
    blank_pending = False
    for raw_line in lines:
        clean_line = _clean_hermes_terminal_line(raw_line)
        if clean_line is None:
            continue
        if clean_line == "":
            if emitted and not emitted[-1].endswith("\n\n"):
                blank_pending = True
            continue
        if blank_pending:
            emitted.append("\n")
            blank_pending = False
        emitted.append(f"{clean_line}\n")
    return "".join(emitted), remainder


def _looks_like_input_prompt(text: str) -> bool:
    line = _last_nonempty_line(text)
    if not line:
        return False
    lowered = line.lower()
    patterns = [
        r"\by/n\b",
        r"\(y/n\)",
        r"\[y/n\]",
        r"\bcontinue\?",
        r"\bproceed\?",
        r"\ballow\b",
        r"\bapprove\b",
        r"\bconfirm\b",
        r"\bpassword\b",
        r"\bsecret\b",
        r"\bapi key\b",
        r"\benter\b.+:",
        r"是否",
        r"确认",
        r"继续",
        r"允许",
        r"输入",
        r"密码",
        r"密钥",
    ]
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)


def _interaction_event(request_id: str, question: str) -> Dict[str, Any]:
    lowered = question.lower()
    is_secret = any(token in lowered for token in ["password", "secret", "api key", "密码", "密钥"])
    options = []
    if not is_secret and any(token in lowered for token in ["y/n", "yes/no", "是否", "确认", "继续", "允许", "approve", "allow"]):
        options = [
            {"label": "Yes", "value": "y", "description": "继续执行"},
            {"label": "No", "value": "n", "description": "拒绝或停止当前操作"},
        ]
    actions = [
        {"label": item["label"], "reply": "question_answer", "type": "primary", "answers": [[item["value"]]]}
        for item in options
    ]
    actions.append({"label": "自定义回答", "reply": "question_custom", "type": "info", "custom": True})
    actions.append({"label": "取消/先不回答", "reply": "question_reject", "type": "danger"})
    question_item = {
        "header": "Hermes",
        "question": question,
        "options": options,
        "multiple": False,
        "custom": True,
        "input_type": "password" if is_secret else "textarea",
        "placeholder": "输入后将发送给 Hermes CLI",
    }
    return {
        "type": "tool_event",
        "source": "hermes",
        "event_type": "question.asked",
        "summary": "Hermes 正在等待你的输入",
        "detail": question,
        "requires_user_action": True,
        "request_id": request_id,
        "question": question,
        "actions": actions,
        "data": {
            "id": request_id,
            "source": "hermes",
            "questions": [question_item],
        },
        "questions": [question_item],
        "cli_inline": True,
    }


def _idle_status_event(idle_seconds: int) -> Dict[str, Any]:
    return _progress_status_event(
        event_type="session.idle",
        summary=f"Hermes CLI 正在后台处理，已静默 {idle_seconds}s",
        detail=f"Hermes CLI 已连续 {idle_seconds}s 没有新的终端输出，Codebot 会继续等待真实 CLI 输出。",
        data={
            "source": "hermes",
            "idle_seconds": idle_seconds,
        },
    )


def _progress_status_event(event_type: str, summary: str, detail: str = "", data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "type": "meta_event",
        "source": "hermes",
        "event_type": event_type,
        "summary": summary,
        "detail": detail,
        "data": {"source": "hermes", **(data or {})},
    }


def _trace_events_from_delta(delta: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw_line in re.split(r"[\r\n]+", delta or ""):
        text = str(raw_line or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        lowered = text.lower()
        if re.search(r"\b(skill|skills|tool|tools)\b", lowered) or re.search(r"\b(call|calling|invoke|invoking|run|running|load|loading|search|searching|scan|scanning)\b", lowered):
            events.append({
                "type": "tool_event",
                "source": "hermes",
                "event_type": "tool-call",
                "summary": text if len(text) <= 120 else f"{text[:117]}...",
                "detail": text,
                "data": {"source": "hermes", "text": text},
            })
            continue
        events.append(
            _progress_status_event(
                event_type="session.trace",
                summary=text if len(text) <= 120 else f"{text[:117]}...",
                detail=text,
                data={"text": text},
            )
        )
    return events


def _register_pending_reply(request_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    _pending_replies[request_id] = queue
    return queue


def _drop_pending_reply(request_id: str) -> None:
    _pending_replies.pop(request_id, None)


async def _wait_for_interaction_reply(request_id: str, timeout: int = 840) -> Optional[Dict[str, Any]]:
    queue = _register_pending_reply(request_id)
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        _drop_pending_reply(request_id)


def reply_hermes_interaction(
    request_id: str,
    answers: Optional[List[List[str]]] = None,
    answer: str = "",
    reject: bool = False,
    response_dir: Optional[str] = None,
) -> bool:
    queue = _pending_replies.get(request_id)
    if queue is None:
        return False
    text = (answer or "").strip()
    if reject:
        text = "n"
    if not text and answers:
        flattened = []
        for group in answers:
            if isinstance(group, list):
                flattened.extend(str(item).strip() for item in group if str(item).strip())
        text = "\n".join(flattened).strip()
    if not text:
        return False
    try:
        queue.put_nowait({"text": text, "reject": reject})
        return True
    except Exception:
        return False


async def abort_hermes_conversation(conversation_id: str) -> bool:
    key = str(conversation_id or "")
    process = _active_processes.get(key)
    if process is None:
        return False
    _aborted_keys.add(key)
    try:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
        return True
    except Exception:
        return False


async def run_hermes_oneshot_stream(
    message: str,
    model: str = "",
    system: Optional[str] = None,
    conversation_id: Optional[str] = None,
    skills: Optional[List[str]] = None,
    interactive: bool = True,
) -> AsyncIterator[Dict[str, Any]]:
    if not app_config.hermes.enabled:
        raise HTTPException(status_code=400, detail="Hermes 未启用，请先在设置中开启 Hermes。")
    install_dir = _default_install_dir()
    if not _has_repo_checkout(install_dir):
        raise HTTPException(status_code=404, detail="Hermes Agent 未安装，请先在设置 > Hermes 中点击一键安装。")
    runtime = await _ensure_runtime(install_dir, force_install=False)
    if runtime.get("returncode") != 0:
        raise HTTPException(status_code=500, detail=runtime.get("output") or "Hermes CLI runtime is not ready")

    _write_hermes_home_config(model, skills)
    command = _append_args(_resolve_hermes_command(), _build_cli_args(message, model, skills))
    env = _hermes_env(model, skills)
    env["HERMES_CODEBOT_REQUESTED_SKILLS"] = ",".join(
        str(skill).strip() for skill in (skills or []) if str(skill).strip()
    )
    key = str(conversation_id or f"standalone:{uuid.uuid4().hex}")
    visible_output = ""
    clean_output = ""
    stream_buffer = ""
    utf8_decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    last_visible_line = ""
    loop = asyncio.get_running_loop()
    last_output_at = loop.time()
    last_idle_notice_at = 0.0
    last_idle_debug_at = 0.0
    stdout_debugged = False
    startup_restart_count = 0
    idle_timeout = int(os.environ.get("CODEBOT_HERMES_IDLE_TIMEOUT", "900") or "900")
    startup_no_output_restart_seconds = int(
        os.environ.get("CODEBOT_HERMES_STARTUP_NO_OUTPUT_RESTART_SECONDS", "90") or "90"
    )
    startup_no_output_restart_limit = int(
        os.environ.get("CODEBOT_HERMES_STARTUP_NO_OUTPUT_RESTART_LIMIT", "1") or "1"
    )
    # Hermes can run silently for a long time. Emit visible heartbeat events so
    # the user can distinguish "still processing" from "stuck", but keep them
    # non-blocking and separate from explicit question/permission prompts.
    idle_notice_seconds = int(os.environ.get("CODEBOT_HERMES_IDLE_NOTICE_SECONDS", "15") or "15")
    idle_notice_repeat_seconds = int(os.environ.get("CODEBOT_HERMES_IDLE_NOTICE_REPEAT_SECONDS", "30") or "30")
    # #region debug-point B:enter
    _debug_report(
        "A",
        "backend/api/routes/hermes.py:run_hermes_oneshot_stream:enter",
        "entered Hermes stream",
        {
            "conversation_id": conversation_id or "",
            "install_dir": str(install_dir),
            "command": command,
            "skills": skills or [],
            "model": _model_for_chat_tasks(model),
        },
    )
    # #endregion

    async def _spawn_process() -> asyncio.subprocess.Process:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(install_dir),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            # #region debug-point B:spawn-error
            _debug_report(
                "A",
                "backend/api/routes/hermes.py:run_hermes_oneshot_stream:spawn_error",
                "Hermes subprocess spawn failed",
                {"error": str(exc), "command": command, "cwd": str(install_dir)},
            )
            # #endregion
            raise HTTPException(status_code=502, detail=f"Hermes CLI 启动失败：{exc}")

        _active_processes[key] = process
        # #region debug-point B:spawned
        _debug_report(
            "A",
            "backend/api/routes/hermes.py:run_hermes_oneshot_stream:spawned",
            "Hermes subprocess spawned",
            {"pid": process.pid, "command": command, "cwd": str(install_dir)},
        )
        # #endregion
        return process

    def _session_status_event(process: asyncio.subprocess.Process, summary: str, event_type: str = "session.status") -> Dict[str, Any]:
        return {
            "type": "event",
            "event": {
                "type": "meta_event",
                "source": "hermes",
                "event_type": event_type,
                "summary": summary,
                "detail": " ".join(command[:4] + (["..."] if len(command) > 4 else [])),
                "data": {
                    "conversation_id": conversation_id,
                    "model": _model_for_chat_tasks(model),
                    "pid": process.pid,
                    "retry_count": startup_restart_count,
                },
            },
        }

    process = await _spawn_process()
    try:
        yield _session_status_event(process, "Hermes CLI 已启动，正在处理")
        while True:
            if process.stdout is None:
                break
            try:
                chunk = await asyncio.wait_for(process.stdout.read(4096), timeout=1)
            except asyncio.TimeoutError:
                if process.returncode is not None:
                    break
                now = loop.time()
                idle_for = int(now - last_output_at)
                if idle_for >= 5 and (last_idle_debug_at <= 0 or now - last_idle_debug_at >= 30):
                    last_idle_debug_at = now
                    # #region debug-point C:idle
                    _debug_report(
                        "C",
                        "backend/api/routes/hermes.py:run_hermes_oneshot_stream:idle",
                        "Hermes subprocess still idle",
                        {"pid": process.pid, "idle_seconds": idle_for, "visible_output_len": len(visible_output)},
                    )
                    # #endregion
                should_restart_startup = (
                    not stdout_debugged
                    and startup_no_output_restart_seconds > 0
                    and idle_for >= startup_no_output_restart_seconds
                    and startup_restart_count < startup_no_output_restart_limit
                )
                if should_restart_startup:
                    previous_pid = process.pid
                    startup_restart_count += 1
                    # #region debug-point B:startup-retry
                    _debug_report(
                        "B",
                        "backend/api/routes/hermes.py:run_hermes_oneshot_stream:startup_retry",
                        "Hermes subprocess had no stdout after startup window, restarting",
                        {
                            "previous_pid": previous_pid,
                            "idle_seconds": idle_for,
                            "retry_count": startup_restart_count,
                            "retry_limit": startup_no_output_restart_limit,
                        },
                    )
                    # #endregion
                    yield {
                        "type": "event",
                        "event": {
                            "type": "meta_event",
                            "source": "hermes",
                            "event_type": "session.retry",
                            "summary": f"Hermes CLI 启动后 {idle_for}s 无输出，正在自动重试 {startup_restart_count}/{startup_no_output_restart_limit}",
                            "detail": "Codebot 检测到当前 Hermes 进程可能卡在静默启动阶段，已终止并重新拉起。",
                            "data": {
                                "conversation_id": conversation_id,
                                "previous_pid": previous_pid,
                                "retry_count": startup_restart_count,
                                "retry_limit": startup_no_output_restart_limit,
                            },
                        },
                    }
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                    process = await _spawn_process()
                    last_output_at = loop.time()
                    last_idle_notice_at = 0.0
                    last_idle_debug_at = 0.0
                    yield _session_status_event(process, "Hermes CLI 已重新启动，正在处理", event_type="session.status")
                    continue
                if now - last_output_at > idle_timeout:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    # #region debug-point E:idle-timeout
                    _debug_report(
                        "E",
                        "backend/api/routes/hermes.py:run_hermes_oneshot_stream:idle_timeout",
                        "Hermes subprocess hit idle timeout",
                        {"pid": process.pid, "idle_timeout": idle_timeout, "visible_output_len": len(visible_output)},
                    )
                    # #endregion
                    yield {
                        "type": "error",
                        "status_code": 499,
                        "message": f"Hermes CLI 空闲超时（{idle_timeout}s 内没有新的输出或交互）",
                        "output": visible_output[-12000:],
                        "returncode": 499,
                    }
                    return
                should_notice = (
                    interactive
                    and idle_notice_seconds > 0
                    and idle_for >= idle_notice_seconds
                    and (last_idle_notice_at <= 0 or now - last_idle_notice_at >= idle_notice_repeat_seconds)
                )
                if should_notice:
                    last_idle_notice_at = now
                    idle_event = _idle_status_event(idle_for)
                    if last_visible_line:
                        idle_event["detail"] = (
                            f"Hermes CLI 已连续 {idle_for}s 没有新的终端输出，Codebot 会继续等待真实 CLI 输出。"
                            f"\n\n最后可见输出：{last_visible_line}"
                        )
                        idle_event["data"]["last_visible_line"] = last_visible_line
                    yield {"type": "event", "event": idle_event}
                continue
            if not chunk:
                break
            delta = utf8_decoder.decode(chunk)
            visible_output += delta
            last_output_at = loop.time()
            last_idle_notice_at = 0.0
            if not stdout_debugged:
                stdout_debugged = True
                # #region debug-point D:first-stdout
                _debug_report(
                    "D",
                    "backend/api/routes/hermes.py:run_hermes_oneshot_stream:first_stdout",
                    "Hermes subprocess produced stdout",
                    {"pid": process.pid, "chunk_len": len(delta), "preview": delta[:400]},
                )
                # #endregion
            stream_buffer = f"{stream_buffer}{delta}"
            clean_delta, stream_buffer = _consume_hermes_terminal_stream(stream_buffer, flush=False)
            if clean_delta:
                clean_output = f"{clean_output}{clean_delta}"
                trace_events = _trace_events_from_delta(clean_delta)
                if trace_events:
                    last_visible_line = str(trace_events[-1].get("summary") or last_visible_line)
                yield {"type": "stdout", "delta": clean_delta, "content": clean_output[-12000:]}
                for trace_event in trace_events:
                    yield {"type": "event", "event": trace_event}

            prompt_probe = _clean_hermes_terminal_line(delta) or delta
            if _looks_like_input_prompt(prompt_probe):
                question = _last_nonempty_line(prompt_probe)
                request_id = f"hermes-{uuid.uuid4().hex[:12]}"
                # #region debug-point D:prompt
                _debug_report(
                    "D",
                    "backend/api/routes/hermes.py:run_hermes_oneshot_stream:prompt",
                    "Hermes subprocess requested interactive input",
                    {"pid": process.pid, "question": question},
                )
                # #endregion
                yield {"type": "interaction", "event": _interaction_event(request_id, question)}
                if not interactive:
                    return
                reply = await _wait_for_interaction_reply(request_id)
                if reply is None:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    yield {
                        "type": "error",
                        "status_code": 499,
                        "message": "Hermes CLI 正在等待输入，但用户长时间未回复。",
                        "output": visible_output[-12000:],
                        "returncode": 499,
                    }
                    return
                answer = str(reply.get("text") or "").strip()
                if process.stdin is not None and answer:
                    process.stdin.write((answer + "\n").encode("utf-8", errors="replace"))
                    await process.stdin.drain()
                    yield {
                        "type": "interaction_resolved",
                        "request_id": request_id,
                        "answer": "***" if _interaction_event(request_id, question)["questions"][0]["input_type"] == "password" else answer,
                    }

        flush_tail = utf8_decoder.decode(b"", final=True)
        if flush_tail:
            visible_output += flush_tail
            stream_buffer = f"{stream_buffer}{flush_tail}"
        final_delta, stream_buffer = _consume_hermes_terminal_stream(stream_buffer, flush=True)
        if final_delta:
            clean_output = f"{clean_output}{final_delta}"
            trace_events = _trace_events_from_delta(final_delta)
            if trace_events:
                last_visible_line = str(trace_events[-1].get("summary") or last_visible_line)
            yield {"type": "stdout", "delta": final_delta, "content": clean_output[-12000:]}
            for trace_event in trace_events:
                yield {"type": "event", "event": trace_event}

        await process.wait()
        aborted = key in _aborted_keys
        # #region debug-point E:exit
        _debug_report(
            "E",
            "backend/api/routes/hermes.py:run_hermes_oneshot_stream:exit",
            "Hermes subprocess exited",
            {
                "pid": process.pid,
                "returncode": process.returncode,
                "aborted": aborted,
                "visible_output_len": len(visible_output),
                "stdout_seen": stdout_debugged,
            },
        )
        # #endregion
        if aborted:
            _aborted_keys.discard(key)
            yield {"type": "aborted", "returncode": -999, "output": (clean_output or visible_output)[-12000:]}
            return
        yield {"type": "done", "returncode": process.returncode, "output": (clean_output or visible_output)[-12000:]}
    finally:
        _active_processes.pop(key, None)


async def _run_oneshot_collect(message: str, model: str = "", conversation_id: Optional[str] = None, skills: Optional[List[str]] = None) -> str:
    output = ""
    async for event in run_hermes_oneshot_stream(
        message=message,
        model=model,
        conversation_id=conversation_id,
        skills=skills,
        interactive=False,
    ):
        event_type = event.get("type")
        if event_type == "stdout":
            output = event.get("content") or output
        elif event_type == "done":
            if event.get("returncode") not in (0, None):
                raise HTTPException(status_code=502, detail=(event.get("output") or output or "Hermes CLI 调用失败"))
            output = event.get("output") or output
        elif event_type == "aborted":
            raise HTTPException(status_code=499, detail=event.get("output") or "任务已被用户终止")
        elif event_type == "error":
            raise HTTPException(status_code=int(event.get("status_code") or 502), detail=event.get("message") or "Hermes CLI 调用失败")
        elif event_type == "interaction":
            raise HTTPException(status_code=409, detail="Hermes CLI 需要人工输入，定时/非流式任务无法继续。请在聊天窗口中运行该任务。")
    content = (output or "").strip()
    if not content:
        raise HTTPException(status_code=502, detail="Hermes CLI 没有返回可显示内容")
    return content


async def prepare_cli_if_enabled() -> None:
    if not (app_config.hermes.enabled and app_config.hermes.auto_start):
        return
    install_dir = _default_install_dir()
    try:
        if not _has_repo_checkout(install_dir):
            app_config.hermes.last_action = "prepare"
            app_config.hermes.last_status = "not_installed"
            app_config.hermes.last_message = "Hermes Agent is not installed yet."
            save_config(app_config)
            return
        result = await _ensure_runtime(install_dir, force_install=False)
        app_config.hermes.last_action = "prepare"
        app_config.hermes.last_status = "success" if result.get("returncode") == 0 else "failed"
        app_config.hermes.last_message = result.get("output", "")
        save_config(app_config)
    except Exception as exc:
        app_config.hermes.last_action = "prepare"
        app_config.hermes.last_status = "failed"
        app_config.hermes.last_message = str(exc)
        save_config(app_config)


@router.get("/status")
async def hermes_status():
    install_dir = _default_install_dir()
    command_found = True
    command: List[str] = []
    try:
        command = _resolve_hermes_command()
    except Exception:
        command_found = False
    return {
        "success": True,
        "data": {
            "mode": "cli",
            "enabled": app_config.hermes.enabled,
            "installed": _has_repo_checkout(install_dir),
            "install_dir": str(install_dir),
            "command": command,
            "command_found": command_found,
            "config": app_config.hermes.model_dump(),
            "hermes_home": str(_hermes_home_dir()),
            "runtime_python": str(_hermes_venv_python(install_dir)),
            "bridge_config_path": str(write_bridge_config()),
            "codebot_app": {"local_url": _local_codebot_url(), "lan_url": _lan_codebot_url()},
            "active_chat_model": _model_for_chat_tasks(),
            "background_model": _model_for_background_tasks(),
            "configured_skill_dirs": _manual_skill_dirs(),
            "excluded_auto_skill_dirs": _dedupe_dir_values(hermes_excluded_auto_skill_dirs()),
            "hermes_native_skill_dirs": [str(path) for path in hermes_native_skill_dirs()],
            "hermes_repo_skill_dirs": [str(path) for path in hermes_repo_skill_dirs()],
            "shared_skill_candidates": _all_auto_shared_skill_dirs(),
            "shared_skill_dirs": _shared_skill_dirs(),
            "skill_dirs": _configured_skill_dirs(),
        },
    }


@router.post("/chat")
async def hermes_chat(request: HermesChatRequest):
    content = await _run_oneshot_collect(
        message=request.message,
        model=request.model or "",
        conversation_id=request.conversation_id,
        skills=request.skills,
    )
    return {"success": True, "data": {"content": content, "runtime": "cli", "model": _model_for_chat_tasks(request.model)}}


@router.post("/install")
async def install_hermes():
    result = await _run_action("install")
    return {"success": True, "data": result, "message": "Hermes Agent 安装完成"}


@router.post("/repair")
async def repair_hermes():
    result = await _run_action("repair")
    return {"success": True, "data": result, "message": "Hermes Agent 修复检查完成"}


@router.post("/update")
async def update_hermes():
    result = await _run_action("update")
    return {"success": True, "data": result, "message": "Hermes Agent 更新完成"}


@router.post("/sync")
async def sync_hermes_bridge():
    config_path = _write_hermes_home_config()
    bridge_path = write_bridge_config()
    return {
        "success": True,
        "data": {"config_path": str(config_path), "bridge_config_path": str(bridge_path)},
        "message": "Hermes 共享配置已同步",
    }
