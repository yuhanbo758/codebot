"""
OpenCode bridge helpers.

This module no longer performs Codebot-side tool orchestration.
It only provides two kinds of shared helpers that are still used by the
current architecture:

1. Skill discovery for lightweight UI features like the chat slash-command list
2. MCP RPC helpers used by the Codebot bridge when proxying third-party MCPs
"""

import json
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from loguru import logger


def _get_skills_dir() -> Path:
    return Path(__file__).parent.parent.parent / "skills"


def _read_skill_markdown(path: Path) -> Tuple[str, str, str]:
    """Read `SKILL.md` and return `(name, description, full_content)`."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return "", "", ""

    name, description = "", ""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            front = content[3:end].strip()
            for line in front.splitlines():
                if line.startswith("name:"):
                    name = line[len("name:"):].strip().strip('"\'')
                elif line.startswith("description:"):
                    description = line[len("description:"):].strip().strip('"\'')

    if not name:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                name = stripped.lstrip("#").strip()
                break
    if not description:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
                description = stripped
                break

    return name, description, content


def _append_skill_dir(skills: List[dict], base_dir: Path, source: str, prefix: str):
    if not base_dir.exists() or not base_dir.is_dir():
        return
    for entry in base_dir.iterdir():
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        name, description, content = _read_skill_markdown(skill_md)
        skills.append({
            "id": f"{prefix}:{entry.name}",
            "name": name or entry.name,
            "description": description,
            "skill_md_content": content,
            "source": source,
        })


def _load_all_skills() -> List[dict]:
    """Load JSON metadata skills plus all `SKILL.md` based skills."""
    skills: List[dict] = []

    skills_dir = _get_skills_dir()
    if skills_dir.exists():
        for file_path in skills_dir.glob("*.json"):
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("enabled", True):
                skills.append({
                    "id": data.get("id", file_path.stem),
                    "name": data.get("name", ""),
                    "description": data.get("description", ""),
                    "skill_md_content": "",
                    "source": "json",
                })

    _append_skill_dir(skills, skills_dir, source="builtin", prefix="builtin")
    _append_skill_dir(skills, Path.home() / ".agents" / "skills", source="opencode", prefix="opencode")

    try:
        from config import app_config as _app_config

        custom_dirs = _app_config.skills.custom_skill_dirs if hasattr(_app_config, "skills") else []
        for dir_path_str in custom_dirs:
            dir_path = Path(dir_path_str)
            if not dir_path.exists() or not dir_path.is_dir():
                continue
            for entry in dir_path.iterdir():
                if not entry.is_dir():
                    continue
                skill_md = entry / "SKILL.md"
                if not skill_md.exists():
                    continue
                name, description, content = _read_skill_markdown(skill_md)
                skills.append({
                    "id": f"custom:{dir_path_str}:{entry.name}",
                    "name": name or entry.name,
                    "description": description,
                    "skill_md_content": content,
                    "source": "custom",
                })
    except Exception:
        pass

    return skills


def _build_mcp_headers(server: dict) -> dict:
    """Build request headers for a proxied MCP server."""
    headers = dict(server.get("headers") or {})

    if "Authorization" not in headers:
        env = server.get("env") or {}
        token = (
            env.get("MODELSCOPE_API_KEY")
            or env.get("API_KEY")
            or env.get("BEARER_TOKEN")
            or ""
        )
        if token:
            headers["Authorization"] = f"Bearer {token}"

    return headers


async def _mcp_sse_rpc(
    sse_url: str,
    method: str,
    params: dict,
    headers: dict,
    call_id: int = 1,
    timeout: float = 20.0,
) -> Optional[dict]:
    """Call an MCP server that still exposes the legacy HTTP+SSE protocol."""
    try:
        import httpx

        sse_headers = dict(headers)
        sse_headers["Accept"] = "text/event-stream"
        sse_headers.pop("Content-Type", None)

        post_headers = dict(headers)
        post_headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            async with client.stream("GET", sse_url, headers=sse_headers) as sse_resp:
                if sse_resp.status_code != 200:
                    logger.debug(f"[MCP] SSE GET failed HTTP {sse_resp.status_code}: {sse_url}")
                    return None

                post_url: Optional[str] = None
                rpc_result: Optional[dict] = None
                event_type = None
                pending_data: Optional[str] = None

                async for raw_line in sse_resp.aiter_lines():
                    line = raw_line.rstrip("\r")

                    if line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        pending_data = line[len("data:"):].strip()
                    elif line == "":
                        if event_type == "endpoint" and pending_data and post_url is None:
                            if pending_data.startswith("http://") or pending_data.startswith("https://"):
                                post_url = pending_data
                            else:
                                parsed = urlparse(sse_url)
                                post_url = f"{parsed.scheme}://{parsed.netloc}{pending_data}"

                            await client.post(
                                post_url,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 0,
                                    "method": "initialize",
                                    "params": {
                                        "protocolVersion": "2024-11-05",
                                        "capabilities": {},
                                        "clientInfo": {"name": "codebot", "version": "1.0"},
                                    },
                                },
                                headers=post_headers,
                            )
                            await client.post(
                                post_url,
                                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                                headers=post_headers,
                            )
                            await client.post(
                                post_url,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": call_id,
                                    "method": method,
                                    "params": params,
                                },
                                headers=post_headers,
                            )
                        elif event_type == "message" and pending_data:
                            try:
                                msg = json.loads(pending_data)
                                if isinstance(msg, dict) and msg.get("id") == call_id:
                                    rpc_result = msg
                                    break
                            except Exception:
                                pass

                        event_type = None
                        pending_data = None

                return rpc_result
    except Exception as exc:
        logger.debug(f"[MCP] SSE RPC failed ({method}): {exc}")
    return None


async def _mcp_streamable_rpc(
    url: str,
    method: str,
    params: dict,
    headers: dict,
    call_id: int = 1,
    timeout: float = 20.0,
) -> Optional[dict]:
    """Call an MCP server using the streamable HTTP protocol."""
    try:
        import httpx

        post_headers = dict(headers)
        post_headers["Content-Type"] = "application/json"
        post_headers["Accept"] = "application/json, text/event-stream"

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            init_resp = await client.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "codebot", "version": "1.0"},
                    },
                },
                headers=post_headers,
            )
            if init_resp.status_code not in (200, 201):
                return None

            session_id = init_resp.headers.get("mcp-session-id") or init_resp.headers.get("Mcp-Session-Id")
            if session_id:
                post_headers["Mcp-Session-Id"] = session_id

            await client.post(
                url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                headers=post_headers,
            )

            rpc_resp = await client.post(
                url,
                json={"jsonrpc": "2.0", "id": call_id, "method": method, "params": params},
                headers=post_headers,
            )

            if rpc_resp.status_code != 200:
                return None

            content_type = rpc_resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                for raw_line in rpc_resp.text.splitlines():
                    line = raw_line.rstrip("\r")
                    if not line.startswith("data:"):
                        continue
                    try:
                        msg = json.loads(line[len("data:"):].strip())
                        if isinstance(msg, dict) and msg.get("id") == call_id:
                            return msg
                    except Exception:
                        pass
                return None

            try:
                return rpc_resp.json()
            except Exception:
                return None
    except Exception as exc:
        logger.debug(f"[MCP] Streamable HTTP RPC failed ({method}): {exc}")
    return None


async def _mcp_rpc(
    server: dict,
    method: str,
    params: dict,
    call_id: int = 1,
    timeout: float = 20.0,
) -> Optional[dict]:
    """Unified MCP RPC entry used by the Codebot third-party bridge."""
    url = server.get("url", "")
    if not url:
        return None

    headers = _build_mcp_headers(server)
    result = await _mcp_streamable_rpc(url, method, params, headers, call_id=call_id, timeout=timeout)
    if result is not None:
        return result
    return await _mcp_sse_rpc(url, method, params, headers, call_id=call_id, timeout=timeout)


async def _list_mcp_tools(server: dict) -> List[dict]:
    """List tools from a proxied MCP server."""
    result = await _mcp_rpc(server, "tools/list", {}, call_id=1)
    if result is None:
        return []
    if "error" in result:
        logger.debug(f"[MCP] tools/list error ({server.get('name')}): {result['error']}")
        return []
    tools = (result.get("result") or {}).get("tools") or []
    return tools if isinstance(tools, list) else []
