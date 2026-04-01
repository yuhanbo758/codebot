"""
自动记忆提取模块

从对话中自动提取用户的习惯、偏好、个人信息等，并保存到记忆系统。
该模块在对话完成后异步执行，不阻塞主对话流程。
"""
import re
import asyncio
import json
from typing import List, Optional, Tuple
from loguru import logger


# ── 分类定义 ────────────────────────────────────────────────────────────────

MEMORY_CATEGORIES = {
    "habit":      "习惯",
    "preference": "偏好",
    "profile":    "个人信息",
    "note":       "笔记",
    "contact":    "联系人",
    "address":    "地址",
}

# ── 规则匹配模式 ─────────────────────────────────────────────────────────────

# (pattern, category, label)  —  在用户消息中匹配到则提取
_EXTRACTION_RULES: List[Tuple[re.Pattern, str]] = [
    # ── preference ──
    (re.compile(r"我(比较|很|非常|挺|超|特别|蛮)?(喜欢|爱|偏好|倾向于|习惯用)\s*(.+?)(?:[，。,.]|$)"), "preference"),
    (re.compile(r"我(不喜欢|讨厌|不想|不用|不爱|不习惯)\s*(.+?)(?:[，。,.]|$)"),                     "preference"),
    (re.compile(r"我(更|还是)?(喜欢|倾向|偏向|首选)\s*(.+?)(?:[，。,.]|$)"),                         "preference"),
    (re.compile(r"我(比较|很|非常)?(偏好|倾向)\s*(.+?)(?:[，。,.]|$)"),                              "preference"),

    # ── habit ──
    (re.compile(r"我(通常|一般|平时|习惯|经常|常常|总是|每天|每次)\s*(.+?)(?:[，。,.]|$)"),           "habit"),
    (re.compile(r"我的习惯(是|就是)?\s*(.+?)(?:[，。,.]|$)"),                                        "habit"),
    (re.compile(r"我(一般|通常)(会|都会|都)\s*(.+?)(?:[，。,.]|$)"),                                  "habit"),

    # ── profile ──
    (re.compile(r"我(叫|是|名叫|名字是|的名字是)\s*([^\s，。,.]{1,20})(?:[，。,.\s]|$)"),             "profile"),
    (re.compile(r"我(在|住在|目前在|工作在|在职于)\s*(.+?)(?:[，。,.]|$)"),                           "profile"),
    (re.compile(r"我的(职业|工作|岗位|职位|职称)\s*(是|为)?\s*(.+?)(?:[，。,.]|$)"),                  "profile"),
    (re.compile(r"我(今年|现在)?\s*(\d{1,3})\s*岁"),                                                  "profile"),

    # ── contact ──
    (re.compile(r"(我的|联系方式|手机号|电话|微信|邮箱)\s*(是|为|：|:)?\s*([\w@.+\-]{3,})"),          "contact"),

    # ── address ──
    (re.compile(r"(我家|我住|公司|家庭住址|家的地址|收货地址)\s*(在|是|：|:)?\s*(.{5,50})(?:[，。,.]|$)"), "address"),
]

# 跳过过短或无意义的提取结果
_MIN_CONTENT_LEN = 4
_SKIP_KEYWORDS = {"一下", "这里", "那里", "这个", "那个", "什么", "怎么"}
_AI_MAX_ITEMS = 5
_AI_EXTRACT_SYSTEM_PROMPT = """你是记忆抽取助手。请从用户消息中提取“值得长期记住”的信息，规则：
1) 仅提取稳定或高价值信息：偏好、习惯、身份信息、联系方式、地址、长期计划、明确备忘。
2) 忽略一次性闲聊、无关寒暄、临时状态。
3) 分类只允许：habit, preference, profile, note, contact, address。
4) 返回 JSON 数组，每项为 {"content":"...", "category":"..."}。
5) 不要输出任何解释文字。"""


def _clean(text: str) -> str:
    """去除首尾标点与空白"""
    return re.sub(r"^[\s，。,.：:！!？?\-–—]+|[\s，。,.：:！!？?\-–—]+$", "", text).strip()


def _extract_candidates(message: str) -> List[Tuple[str, str]]:
    """
    从单条用户消息中，用规则匹配提取 (content, category) 列表。
    返回去重后的结果。
    """
    results: List[Tuple[str, str]] = []
    seen: set = set()

    for pattern, category in _EXTRACTION_RULES:
        for m in pattern.finditer(message):
            # 取最后一个捕获组作为内容（含具体描述）
            groups = [g for g in m.groups() if g]
            if not groups:
                continue
            raw = groups[-1]
            content = _clean(raw)
            if not content or len(content) < _MIN_CONTENT_LEN:
                continue
            if any(kw in content for kw in _SKIP_KEYWORDS):
                continue
            # 拼上上下文前缀使内容可读
            full_content = _clean(m.group(0))
            if len(full_content) >= _MIN_CONTENT_LEN and full_content not in seen:
                seen.add(full_content)
                results.append((full_content, category))

    return results


def _dedup_with_existing(
    candidates: List[Tuple[str, str]],
    existing_contents: List[str]
) -> List[Tuple[str, str]]:
    """简单去重：内容与已有记忆高度重叠则跳过"""
    filtered = []
    for content, category in candidates:
        already = any(
            content in ex or ex in content
            for ex in existing_contents
        )
        if not already:
            filtered.append((content, category))
    return filtered


async def _dedup_with_vector_search(
    candidates: List[Tuple[str, str]],
    memory_manager,
) -> List[Tuple[str, str]]:
    """
    使用 ChromaDB 向量搜索进行语义去重。
    对每个候选记忆检查是否已存在高度相似的记忆，如果已存在且现有内容已足够完善则跳过。
    注意：即使这里放行，save_long_term_memory 内部也有二次去重保护。
    """
    if not candidates:
        return []

    _VECTOR_DEDUP_THRESHOLD = 0.20  # 距离低于此值视为已有相同记忆

    filtered = []
    for content, category in candidates:
        try:
            results = memory_manager.memory_collection.query(
                query_texts=[content],
                n_results=1,
            )
            if (
                results
                and results.get("distances")
                and results["distances"][0]
                and results["distances"][0][0] < _VECTOR_DEDUP_THRESHOLD
            ):
                # 高度相似的记忆已存在 → 跳过
                existing_doc = (results["documents"][0][0] if results.get("documents") and results["documents"][0] else "")
                dist = results["distances"][0][0]
                # 如果新内容显著长于已有（信息量更大），仍然放行让 save_long_term_memory 处理更新
                if len(content.strip()) > len(existing_doc) * 1.3:
                    filtered.append((content, category))
                else:
                    logger.debug(
                        f"[memory_extractor] 向量去重跳过: dist={dist:.4f}, "
                        f"content={content[:50]}"
                    )
                continue
        except Exception:
            pass
        filtered.append((content, category))
    return filtered


async def extract_and_save(
    user_message: str,
    assistant_response: str,
    memory_manager,
    *,
    existing_contents: Optional[List[str]] = None,
    opencode_ws=None,
) -> int:
    """
    从一轮对话（用户消息 + AI 回复）中提取记忆并保存。

    参数：
        user_message:      用户发送的消息
        assistant_response: AI 的回复（暂只分析用户消息，AI 回复预留）
        memory_manager:    MemoryManager 实例
        existing_contents: 已有记忆内容列表，用于去重（可选）

    返回：
        保存成功的条目数
    """
    if not user_message or not user_message.strip():
        return 0

    candidates = _extract_candidates(user_message)
    ai_candidates = await _extract_candidates_by_ai(user_message, assistant_response, opencode_ws)
    for content, category in ai_candidates:
        if (content, category) not in candidates:
            candidates.append((content, category))
    if not candidates:
        return 0

    if existing_contents:
        candidates = _dedup_with_existing(candidates, existing_contents)

    # 使用 ChromaDB 向量搜索做语义级去重（比字符串包含更精准）
    if candidates and hasattr(memory_manager, "memory_collection"):
        try:
            candidates = await _dedup_with_vector_search(candidates, memory_manager)
        except Exception as vec_err:
            logger.debug(f"[memory_extractor] 向量去重失败（继续）: {vec_err}")

    if not candidates:
        return 0

    saved = 0
    for content, category in candidates:
        try:
            await memory_manager.save_long_term_memory(
                content=content,
                category=category,
                metadata={"source": "auto_extract", "raw": user_message[:200], "assistant": (assistant_response or "")[:200]},
            )
            saved += 1
            logger.debug(f"[memory_extractor] 自动保存记忆 [{category}]: {content}")
        except Exception as exc:
            logger.warning(f"[memory_extractor] 保存失败: {exc}")

    if saved:
        logger.info(f"[memory_extractor] 从对话中自动提取并保存 {saved} 条记忆")
    return saved


async def extract_and_save_background(
    user_message: str,
    assistant_response: str,
    memory_manager,
    opencode_ws=None,
) -> None:
    """
    后台异步包装器：捕获所有异常，不影响主流程。
    可直接 asyncio.create_task() 调用。
    """
    try:
        # 获取近期记忆内容用于去重（最多取 50 条）
        existing: List[str] = []
        try:
            recent = await memory_manager.get_memories(limit=50)
            existing = [str(m.get("content", "")) for m in recent if m.get("content")]
        except Exception:
            pass

        await extract_and_save(
            user_message=user_message,
            assistant_response=assistant_response,
            memory_manager=memory_manager,
            existing_contents=existing,
            opencode_ws=opencode_ws,
        )
    except Exception as exc:
        logger.warning(f"[memory_extractor] 后台提取任务异常: {exc}")


async def _extract_candidates_by_ai(
    user_message: str,
    assistant_response: str,
    opencode_ws=None,
) -> List[Tuple[str, str]]:
    if opencode_ws is None or not getattr(opencode_ws, "connected", False):
        return []
    payload = json.dumps(
        {
            "user_message": user_message,
            "assistant_response": assistant_response or "",
            "max_items": _AI_MAX_ITEMS,
        },
        ensure_ascii=False,
    )
    try:
        session_id = f"memory_extract_{int(asyncio.get_event_loop().time() * 1000)}"
        response = await opencode_ws.send_message(
            session_id=session_id,
            message=payload,
            system_prompt=_AI_EXTRACT_SYSTEM_PROMPT,
        )
        if not response:
            return []
        match = re.search(r"\[[\s\S]*\]", response)
        if not match:
            return []
        data = json.loads(match.group(0))
        if not isinstance(data, list):
            return []
        out: List[Tuple[str, str]] = []
        seen = set()
        for item in data:
            if not isinstance(item, dict):
                continue
            content = _clean(str(item.get("content") or ""))
            category = str(item.get("category") or "").strip()
            if not content or len(content) < _MIN_CONTENT_LEN:
                continue
            if category not in MEMORY_CATEGORIES:
                continue
            key = (content, category)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
            if len(out) >= _AI_MAX_ITEMS:
                break
        return out
    except Exception as exc:
        logger.warning(f"[memory_extractor] AI 提取失败，回退规则提取: {exc}")
        return []
