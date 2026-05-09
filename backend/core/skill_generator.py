"""Helpers for turning chat evidence into real Codebot SKILL.md bodies."""
from __future__ import annotations

import re
from typing import Optional

from loguru import logger

from config import app_config
from core.opencode_ws import OpenCodeClient


def _clean_generated_markdown(text: str) -> str:
    content = (text or "").replace("\r\n", "\n").strip()
    content = re.sub(r"(?is)<think>[\s\S]*?</think>", "", content).strip()
    content = re.sub(r"(?is)<thinking>[\s\S]*?</thinking>", "", content).strip()
    fence = re.fullmatch(r"```(?:markdown|md)?\s*([\s\S]*?)\s*```", content, flags=re.IGNORECASE)
    if fence:
        content = fence.group(1).strip()
    return content


def _clip(text: str, limit: int) -> str:
    value = re.sub(r"\s+\n", "\n", (text or "").strip())
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "\n\n..."


def _looks_like_skill_body(body: str) -> bool:
    text = (body or "").strip()
    if len(text) < 120:
        return False
    has_sections = bool(re.search(r"(?m)^#{1,3}\s+\S+", text))
    has_steps = len(re.findall(r"(?m)^\s*(?:[-*]|\d+[.)、])\s+\S+", text)) >= 2
    return has_sections or has_steps


def build_fallback_skill_body(
    *,
    user_message: str,
    assistant_response: str,
    title: str = "",
    description: str = "",
) -> str:
    """Build a usable skill body without storing the chat transcript verbatim."""
    scenario = _clip(user_message, 500) or "用户需要复用本次对话中形成的处理流程。"
    source = _clip(assistant_response, 2200)
    if not source:
        source = "根据用户输入识别目标、收集必要上下文、按步骤执行，并在完成后说明结果和验证方式。"
    name = title or "自动生成技能"
    summary = description or scenario
    return f"""Use this skill when the user needs the reusable workflow described below.

## When to Use

- 用户请求与“{_clip(summary, 160)}”相近。
- 需要把一次对话中的处理方法复用为稳定步骤，而不是保存聊天记录本身。

## Inputs

- 用户当前需求和目标产物。
- 相关项目路径、文件、配置、账号或运行环境信息。
- 本次来源场景：{scenario}

## Workflow

1. Re-state the concrete goal in operational terms and identify the expected output.
2. Inspect the relevant local context before changing files, configuration, tasks, or data.
3. Apply the procedure distilled from the source conversation:

{source}

4. Keep changes scoped to the requested workflow and avoid unrelated rewrites.
5. Verify the result with the smallest reliable check available, then report the outcome clearly.

## Verification

- Confirm the requested artifact, setting, task, or behavior now exists in the intended Codebot location.
- Mention any skipped checks or follow-up risk if verification cannot be completed.
"""


async def generate_skill_body_from_chat(
    *,
    user_message: str,
    assistant_response: str,
    title: str = "",
    description: str = "",
    opencode_client: Optional[OpenCodeClient] = None,
) -> str:
    """Use OpenCode to synthesize a real skill body, falling back locally."""
    user = _clip(user_message, 1800)
    answer = _clip(assistant_response, 3200)
    fallback = build_fallback_skill_body(
        user_message=user,
        assistant_response=answer,
        title=title,
        description=description,
    )
    if not user and not answer:
        return fallback

    prompt = (
        "你是 Codebot 的 skill 生成器。请根据下面的对话素材，提炼一个真正可复用的 agent skill。"
        "只输出 SKILL.md 的 Markdown 正文，不要输出 YAML front matter，不要创建文件，不要写入任何目录，"
        "不要把聊天记录原样保存为 skill。\n\n"
        "要求：\n"
        "1. 使用中文为主，除非素材本身要求英文命令或英文术语。\n"
        "2. 包含 When to Use、Inputs、Workflow、Verification 等清晰小节。\n"
        "3. Workflow 必须是可执行步骤，去掉闲聊、礼貌语、模型自述和一次性上下文。\n"
        "4. 保留必要的命令、路径模式、配置注意事项和验收标准。\n\n"
        f"建议标题：{title or '自动生成技能'}\n"
        f"建议描述：{description or ''}\n\n"
        f"用户消息：\n{user}\n\n"
        f"助手回复素材：\n{answer}"
    )

    client = opencode_client
    created_client = False
    try:
        if client is None:
            client = OpenCodeClient(app_config.opencode.server_url)
            created_client = True
            ok = await client.try_connect(attempts=2, delay=0.3, open_timeout=1.0)
            if not ok:
                return fallback
        result = await client.execute_task(prompt, timeout=60)
        if result.success and result.content:
            body = _clean_generated_markdown(result.content)
            if _looks_like_skill_body(body):
                return body
    except Exception as exc:
        logger.debug(f"[skill] OpenCode 生成 skill 正文失败，使用本地回退: {exc}")
    finally:
        if created_client and client:
            try:
                await client.disconnect()
            except Exception:
                pass
    return fallback
