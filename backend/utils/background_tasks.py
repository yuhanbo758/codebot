"""后台协程生命周期管理。

asyncio 事件循环只保存 Task 的弱引用。统一保留强引用并消费异常，避免标题生成、
记忆整理、Webhook 等后台工作在完成前被回收，或异常变成无人读取的警告。
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Optional, Set

from loguru import logger


_BACKGROUND_TASKS: Set[asyncio.Task] = set()


def create_background_task(coro: Awaitable, name: Optional[str] = None) -> asyncio.Task:
    """创建并跟踪后台任务；调用方不需要等待，但任务会稳定存活到结束。"""
    task = asyncio.create_task(coro, name=name)
    _BACKGROUND_TASKS.add(task)

    def _finished(done: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(done)
        if done.cancelled():
            return
        try:
            error = done.exception()
        except asyncio.CancelledError:
            return
        if error is not None:
            logger.error(f"后台任务执行失败：{error}")

    task.add_done_callback(_finished)
    return task


async def cancel_background_tasks() -> None:
    """应用关闭时取消仍在运行的后台任务并等待其清理。"""
    pending = [task for task in _BACKGROUND_TASKS if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


def background_task_count() -> int:
    return len(_BACKGROUND_TASKS)
