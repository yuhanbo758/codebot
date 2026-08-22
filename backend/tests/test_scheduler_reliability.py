"""定时任务可靠性回归测试。

这些测试只使用临时 SQLite 数据库，不读取或修改用户的真实任务。
"""

import asyncio
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.scheduler import TaskScheduler  # noqa: E402
from api.routes.scheduler import generate_cron_from_text  # noqa: E402


class _RetryOnceOpenCode:
    """第一次失败、第二次成功，用来验证有限重试。"""

    def __init__(self):
        self.calls = 0
        self.system_prompts = []

    async def get_models(self):
        return []

    async def execute_task(self, prompt, model=None, mode=None, system=None):
        self.calls += 1
        self.system_prompts.append(system)
        if self.calls == 1:
            return SimpleNamespace(success=False, content="", error="临时失败", tokens_used=0)
        return SimpleNamespace(success=True, content=f"完成：{prompt}", error=None, tokens_used=12)


class SchedulerReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "scheduled_tasks.db")

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_failed_execution_retries_and_persists_status(self):
        client = _RetryOnceOpenCode()
        scheduler = TaskScheduler(opencode_ws=client, db_path=self.db_path)
        task = scheduler.create_task(
            "重试测试",
            "*/5 * * * *",
            "生成报告",
            max_retries=1,
            retry_delay_seconds=1,
            timeout_seconds=30,
        )

        success = await scheduler._execute_task(task, scheduled_for=task.next_run)

        self.assertTrue(success)
        self.assertEqual(client.calls, 2)
        self.assertEqual(task.last_status, "success")
        self.assertEqual(task.consecutive_failures, 0)
        conn = sqlite3.connect(self.db_path)
        try:
            status, attempts = conn.execute(
                "SELECT status, attempts FROM task_logs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual((status, attempts), ("success", 2))
        self.assertTrue(all("定时任务执行契约" in value for value in client.system_prompts))
        self.assertIn("第 2 次尝试", client.system_prompts[-1])

    async def test_misfire_is_recorded_instead_of_silently_discarded(self):
        scheduler = TaskScheduler(db_path=self.db_path)
        task = scheduler.create_task(
            "错过执行测试",
            "*/5 * * * *",
            "生成报告",
            misfire_grace_seconds=0,
        )
        task.next_run = task.next_run.replace(year=2020)

        scheduler._claim_and_schedule(task, task.next_run.replace(year=2021))
        await asyncio.gather(*list(scheduler._background_tasks), return_exceptions=True)

        conn = sqlite3.connect(self.db_path)
        try:
            status, error = conn.execute(
                "SELECT status, error FROM task_logs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(status, "skipped")
        self.assertIn("超过宽限期", error)

    async def test_unrecognized_time_never_defaults_to_daily_nine(self):
        with self.assertRaisesRegex(ValueError, "无法从描述中确定执行时间"):
            await generate_cron_from_text("帮我处理一下资料")

    async def test_relative_time_is_marked_run_once(self):
        generated = await generate_cron_from_text("30 分钟后提醒我休息")
        self.assertTrue(generated["run_once"])


if __name__ == "__main__":
    unittest.main()
