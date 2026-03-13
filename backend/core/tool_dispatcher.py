"""
Tool Dispatcher — 自动识别并调用 Skills 和 MCP 工具

工作流程：
1. 接收用户消息
2. 从 Skills 目录加载所有启用的技能描述
3. 从 MCP 配置加载所有启用的 MCP 服务器
4. 基于关键词 + 描述匹配，判断是否有相关的技能/工具
5. 对于匹配到的技能：将 SKILL.md 内容注入 prompt（上下文增强）
6. 对于匹配到的 SSE MCP 工具：调用工具并将结果注入 prompt
7. 返回增强后的 prompt（或原始 prompt，如无命中）
"""
import json
import re
from pathlib import Path
from typing import List, Optional, Tuple
from loguru import logger

try:
    from config import settings as _settings
except ImportError:
    _settings = None


# ── 工具：加载技能列表 ──────────────────────────────────────────────────────

def _get_skills_dir() -> Path:
    return Path(__file__).parent.parent.parent / "skills"


def _read_skill_markdown(path: Path) -> Tuple[str, str, str]:
    """
    读取 SKILL.md，返回 (name, description, full_content)。
    """
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


def _load_all_skills() -> List[dict]:
    """
    加载所有技能（JSON + builtin SKILL.md + opencode SKILL.md + 自定义目录 SKILL.md）。
    返回列表，每项含 {name, description, skill_md_content (可能为空)}。
    """
    skills: List[dict] = []

    skills_dir = _get_skills_dir()
    if skills_dir.exists():
        # JSON 技能（元数据，无 SKILL.md 内容）
        for f in skills_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("enabled", True):
                    skills.append({
                        "id": data.get("id", f.stem),
                        "name": data.get("name", ""),
                        "description": data.get("description", ""),
                        "skill_md_content": "",
                        "source": "json",
                    })
            except Exception:
                pass

        # builtin SKILL.md 技能
        for entry in skills_dir.iterdir():
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            name, description, content = _read_skill_markdown(skill_md)
            skills.append({
                "id": f"builtin:{entry.name}",
                "name": name or entry.name,
                "description": description,
                "skill_md_content": content,
                "source": "builtin",
            })

    # OpenCode 技能（~/.agents/skills/）
    oc_dir = Path.home() / ".agents" / "skills"
    if oc_dir.exists():
        for entry in oc_dir.iterdir():
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            name, description, content = _read_skill_markdown(skill_md)
            skills.append({
                "id": f"opencode:{entry.name}",
                "name": name or entry.name,
                "description": description,
                "skill_md_content": content,
                "source": "opencode",
            })

    # 自定义目录技能
    try:
        from config import app_config as _app_config
        custom_dirs = _app_config.skills.custom_skill_dirs if hasattr(_app_config, 'skills') else []
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


# ── 工具：加载 MCP 服务器列表 ───────────────────────────────────────────────

def _load_enabled_mcp_servers() -> List[dict]:
    """读取所有已启用的 MCP 服务器配置。"""
    try:
        if _settings is None:
            return []
        path = _settings.MCP_SERVERS_FILE
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [s for s in data if isinstance(s, dict) and s.get("enabled", True)]
    except Exception as e:
        logger.warning(f"[ToolDispatcher] 加载 MCP 服务器配置失败: {e}")
        return []


# ── 相关性匹配 ───────────────────────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """简单分词：按空格 + 标点分割，同时保留中文字符 bigram。"""
    # 英文词
    tokens = re.findall(r"[a-zA-Z0-9_\-\.]+", text.lower())
    # 中文 unigram（每个字）
    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    tokens.extend(chinese)
    return tokens


def _relevance_score(message: str, name: str, description: str) -> float:
    """
    粗糙的 TF 相关度打分（0.0 ~ 1.0）。
    """
    msg_tokens = set(_tokenize(message))
    target_tokens = set(_tokenize(f"{name} {description}"))
    if not target_tokens:
        return 0.0
    overlap = msg_tokens & target_tokens
    # 命中比例（相对于描述词汇）
    score = len(overlap) / max(len(target_tokens), 1)
    # 如果名称在消息中直接出现，大幅提升
    if name.lower() in message.lower():
        score += 0.5
    return min(score, 1.0)


_RELEVANCE_THRESHOLD = 0.08   # 低阈值，宁可误触发也不漏触发


def _find_relevant_skills(message: str, skills: List[dict]) -> List[dict]:
    """返回与消息相关的技能列表（按相关度降序）。"""
    scored = []
    for skill in skills:
        score = _relevance_score(message, skill["name"], skill["description"])
        if score >= _RELEVANCE_THRESHOLD:
            scored.append((score, skill))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:3]]   # 最多取前 3 个


def _find_relevant_mcp_servers(message: str, servers: List[dict]) -> List[dict]:
    """返回与消息相关的 MCP 服务器（按相关度降序）。"""
    scored = []
    for server in servers:
        score = _relevance_score(message, server.get("name", ""), server.get("description", ""))
        if score >= _RELEVANCE_THRESHOLD:
            scored.append((score, server))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:2]]   # 最多取前 2 个


# ── MCP SSE 工具调用 ─────────────────────────────────────────────────────────

async def _call_mcp_sse_tool(server: dict, tool_name: str, tool_args: dict) -> Optional[str]:
    """
    调用 SSE 类型 MCP 服务器的工具。
    使用 MCP JSON-RPC over HTTP POST（非流式简化版）。
    """
    url = server.get("url", "")
    if not url:
        return None

    # 构造 Bearer token：优先从 env 中取 MODELSCOPE_API_KEY / API_KEY
    env = server.get("env") or {}
    token = (
        env.get("MODELSCOPE_API_KEY")
        or env.get("API_KEY")
        or env.get("BEARER_TOKEN")
        or ""
    )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # MCP JSON-RPC 调用消息
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": tool_args,
        }
    }

    try:
        import httpx
        # SSE endpoint 通常是 .../sse，但 JSON-RPC 端点可能是 .../rpc 或根路径
        rpc_url = url.removesuffix("/sse").rstrip("/") + "/rpc" if "/sse" in url else url
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(rpc_url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                result = data.get("result") or {}
                content = result.get("content") or []
                if isinstance(content, list):
                    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                    return "\n".join(texts) if texts else None
                if isinstance(content, str):
                    return content
    except Exception as e:
        logger.debug(f"[ToolDispatcher] MCP 工具调用失败 ({server.get('name')}): {e}")
    return None


async def _list_mcp_tools(server: dict) -> List[dict]:
    """
    列出 SSE MCP 服务器的可用工具。
    返回工具列表 [{name, description, inputSchema}]。
    """
    url = server.get("url", "")
    if not url:
        return []

    env = server.get("env") or {}
    token = (
        env.get("MODELSCOPE_API_KEY")
        or env.get("API_KEY")
        or env.get("BEARER_TOKEN")
        or ""
    )
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

    try:
        import httpx
        rpc_url = url.removesuffix("/sse").rstrip("/") + "/rpc" if "/sse" in url else url
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(rpc_url, json=payload, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                tools = (data.get("result") or {}).get("tools") or []
                return tools if isinstance(tools, list) else []
    except Exception as e:
        logger.debug(f"[ToolDispatcher] 列出工具失败 ({server.get('name')}): {e}")
    return []


# ── 工具参数提取 ─────────────────────────────────────────────────────────────

def _extract_tool_args(message: str, tool: dict) -> dict:
    """
    尝试从消息中提取工具参数。
    目前仅处理简单的 string 类型必填参数（取消息全文或关键部分）。
    """
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = schema.get("required") or list(props.keys())[:1]
    args: dict = {}
    for param_name in required:
        prop = props.get(param_name) or {}
        ptype = prop.get("type", "string")
        if ptype == "string":
            # 尝试从消息中提取参数值：先看有没有 key=value 模式
            match = re.search(
                rf"(?:{re.escape(param_name)})\s*[=:：]\s*([^\n，,;；]+)",
                message,
                re.IGNORECASE,
            )
            args[param_name] = match.group(1).strip() if match else message.strip()
        elif ptype == "integer":
            match = re.search(r"\d+(?:\.\d+)?", message)
            args[param_name] = int(float(match.group(0))) if match else 0
        elif ptype == "number":
            match = re.search(r"\d+(?:\.\d+)?", message)
            args[param_name] = float(match.group(0)) if match else 0.0
    return args


# ── 主入口：构建增强 prompt ──────────────────────────────────────────────────

async def build_augmented_prompt(message: str) -> str:
    """
    给定用户消息，自动匹配相关 Skills 和 MCP 工具，返回增强后的 prompt。
    若无命中，直接返回原始 message。
    """
    skills = _load_all_skills()
    mcp_servers = _load_enabled_mcp_servers()

    relevant_skills = _find_relevant_skills(message, skills)
    relevant_mcps = _find_relevant_mcp_servers(message, mcp_servers)

    if not relevant_skills and not relevant_mcps:
        return message

    augment_lines: List[str] = []

    # ── 注入 Skills 上下文 ──────────────────────────────────────────────────
    for skill in relevant_skills:
        content = skill.get("skill_md_content", "").strip()
        if content:
            augment_lines.append(f"【技能参考：{skill['name']}】\n{content}")
        elif skill.get("description"):
            augment_lines.append(f"【技能参考：{skill['name']}】\n{skill['description']}")

    # ── 调用 MCP 工具并注入结果 ─────────────────────────────────────────────
    for server in relevant_mcps:
        if server.get("transport") != "sse":
            # stdio 工具需要本地进程，不在此处直接调用
            augment_lines.append(
                f"【MCP 工具可用：{server['name']}（本地 stdio）】\n"
                f"{server.get('description', '')}"
            )
            continue

        tools = await _list_mcp_tools(server)
        if not tools:
            continue

        # 选取与消息最相关的工具
        best_tool = None
        best_score = 0.0
        for tool in tools:
            s = _relevance_score(
                message,
                tool.get("name", ""),
                tool.get("description", "")
            )
            if s > best_score:
                best_score = s
                best_tool = tool

        if best_tool and best_score >= _RELEVANCE_THRESHOLD:
            # 尝试从消息中自动提取参数（简单情形）
            tool_args = _extract_tool_args(message, best_tool)
            result_text = await _call_mcp_sse_tool(server, best_tool["name"], tool_args)
            if result_text:
                augment_lines.append(
                    f"【MCP 工具结果：{server['name']}/{best_tool['name']}】\n{result_text}"
                )
            else:
                # 工具调用失败，至少注入工具描述作为上下文
                augment_lines.append(
                    f"【MCP 工具可用：{server['name']}/{best_tool['name']}】\n"
                    f"{best_tool.get('description', '')}"
                )

    if not augment_lines:
        return message

    parts = augment_lines + [f"【用户消息】{message}"]
    return "\n\n".join(parts)
