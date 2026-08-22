"""Codebot 的轻量、按需提示词执行契约。

这里不额外调用另一个模型改写用户原文，避免增加延迟、费用和语义漂移。优化器只在
Agent 复杂任务或真正的 AI 定时任务上追加短 developer/system 契约，明确目标、验收、
证据和停止条件；用户消息本身始终原样保留。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class PromptOptimizationDecision:
    """记录优化是否生效，便于测试和运行日志解释。"""

    applied: bool
    score: int
    reason: str
    developer_contract: str


_ACTION_GROUPS = (
    re.compile(r"(?:检查|诊断|排查|分析|review|inspect|diagnos)", re.IGNORECASE),
    re.compile(r"(?:修改|修复|实现|重构|优化|替换|兼容|edit|fix|implement|refactor|optimi)", re.IGNORECASE),
    re.compile(r"(?:测试|验证|构建|打包|验收|test|verify|build|package)", re.IGNORECASE),
    re.compile(r"(?:搜索|调研|查询|对比|research|search|compare)", re.IGNORECASE),
)


def _complexity_score(prompt: str) -> int:
    """使用可预测的结构信号判断任务复杂度，不理解或改写业务含义。"""
    text = str(prompt or "").strip()
    if not text:
        return 0
    score = 0
    if len(text) >= 120:
        score += 1
    if len(text) >= 320:
        score += 1
    if text.count("\n") >= 2 or len(re.findall(r"(?:^|\n)\s*(?:[-*]|\d+[.)、])\s*", text)) >= 2:
        score += 1
    if len(re.findall(r"(?:同时|并且|另外|以及|然后|最后|同时还|and|also|then)", text, re.IGNORECASE)) >= 2:
        score += 1
    action_groups = sum(1 for pattern in _ACTION_GROUPS if pattern.search(text))
    if action_groups >= 2:
        score += 2
    elif action_groups == 1:
        score += 1
    if re.search(r"(?:[A-Za-z]:\\|/[^\s]+/|\.py\b|\.js\b|\.vue\b|API\b|数据库|配置|源码)", text, re.IGNORECASE):
        score += 1
    if re.search(r"(?:验收|必须|不能|不要|保持|兼容|回滚|风险|success criteria|constraint)", text, re.IGNORECASE):
        score += 1
    return score


def optimize_agent_prompt(prompt: str, mode: Optional[str]) -> PromptOptimizationDecision:
    """仅为复杂 Agent 请求生成短执行契约；简单问答完全不加提示。"""
    if str(mode or "").lower() != "agent":
        return PromptOptimizationDecision(False, 0, "非 Agent 模式", "")
    score = _complexity_score(prompt)
    if score < 3:
        return PromptOptimizationDecision(False, score, "任务较简单，保留原始提示", "")
    contract = (
        "【本轮按需提示词优化：执行契约】\n"
        "先把用户原始请求归纳为一个明确结果，但不要改写、缩小或扩张用户范围。\n"
        "执行时优先确认真实代码/配置/运行证据；只规划完成目标所需的最短路径。\n"
        "成功标准：逐项覆盖用户要求，完成授权范围内的实际操作，并运行与风险相称的验证。\n"
        "停止条件：目标已验证完成，或出现必须由用户授权/提供信息才能继续的真实阻塞。\n"
        "最终答复区分已完成、验证证据、仍未验证边界与风险，不把计划冒充结果。"
    )
    return PromptOptimizationDecision(True, score, "复杂 Agent 任务", contract)


def build_scheduled_task_instructions(
    prompt: str,
    *,
    task_name: str,
    scheduled_for: Optional[datetime],
    trigger_type: str,
    attempt: int,
    max_retries: int,
) -> PromptOptimizationDecision:
    """为非交互 AI 定时任务构造稳定执行契约，纯提醒不会调用本函数。"""
    score = _complexity_score(prompt)
    scheduled_text = scheduled_for.isoformat() if scheduled_for else "手动触发或未提供"
    retry_text = f"第 {attempt} 次尝试（最多 {max_retries + 1} 次）"
    complex_rule = "复杂任务先形成短计划再执行并验证。" if score >= 3 else "任务较直接，立即执行并核对结果。"
    contract = (
        "【Codebot 定时任务执行契约】\n"
        f"任务名称：{str(task_name or '未命名任务')[:200]}\n"
        f"计划执行时间：{scheduled_text}；触发方式：{str(trigger_type or 'schedule')[:40]}；{retry_text}。\n"
        "这是无人值守执行：不得等待用户回答；缺少必要信息时安全停止并在结果中写明阻塞。\n"
        f"{complex_rule}\n"
        "以持久化状态、文件或工具返回值作为完成证据；重试前检查现状，不要盲目重复不可逆副作用。\n"
        "最终输出必须包含完成结果；如有失败，同时给出错误、已完成部分和可恢复建议。"
    )
    return PromptOptimizationDecision(True, score, "非交互 AI 定时任务", contract)
