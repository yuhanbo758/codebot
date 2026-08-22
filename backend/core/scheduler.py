"""
定时任务调度器
"""
import asyncio
import hashlib
import sqlite3
import json
import threading
from datetime import datetime
from typing import List, Dict, Optional, Set
from types import SimpleNamespace
from croniter import croniter
from loguru import logger
from pathlib import Path
import re

from config import settings, app_config


def normalize_task_executor(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if raw in {"codex", "codex_cli", "codex-agent", "codex_agent"}:
        return "codex"
    # 兼容一个发布版本：旧 Hermes 任务在读取和写回时立即规范为 Codex。
    if raw in {"hermes", "hermes_cli", "hermes-agent", "hermes_agent"}:
        return "codex"
    return "opencode"


def normalize_task_execution_model(value: Optional[str]) -> str:
    return (value or "").strip()


class ScheduledTask:
    """定时任务"""
    
    def __init__(
        self,
        id: str,
        name: str,
        cron_expression: str,
        task_prompt: str,
        enabled: bool = True,
        last_run: datetime = None,
        next_run: datetime = None,
        notify_channels: List[str] = None,
        created_at: datetime = None,
        run_once: bool = False,
        archived: bool = False,
        executor: str = "opencode",
        execution_model: str = "",
        timeout_seconds: int = 1800,
        max_retries: int = 1,
        retry_delay_seconds: int = 30,
        misfire_grace_seconds: int = 900,
        overlap_policy: str = "skip",
        last_status: str = "",
        last_error: str = "",
        last_duration_ms: int = 0,
        consecutive_failures: int = 0,
    ):
        self.id = id
        self.name = name
        self.cron_expression = cron_expression
        self.task_prompt = task_prompt
        self.enabled = enabled
        self.last_run = last_run
        self.next_run = next_run
        self.notify_channels = notify_channels or []
        self.created_at = created_at or datetime.now()
        self.run_once = run_once
        self.archived = archived
        self.executor = normalize_task_executor(executor)
        self.execution_model = normalize_task_execution_model(execution_model)
        self.timeout_seconds = max(30, int(timeout_seconds or 1800))
        self.max_retries = max(0, min(5, int(max_retries or 0)))
        self.retry_delay_seconds = max(1, int(retry_delay_seconds or 30))
        self.misfire_grace_seconds = max(0, int(misfire_grace_seconds or 0))
        self.overlap_policy = overlap_policy if overlap_policy in {"skip", "parallel"} else "skip"
        self.last_status = last_status or ""
        self.last_error = last_error or ""
        self.last_duration_ms = max(0, int(last_duration_ms or 0))
        self.consecutive_failures = max(0, int(consecutive_failures or 0))
    
    def calculate_next_run(self, base_time: Optional[datetime] = None) -> datetime:
        """计算下次运行时间"""
        try:
            cron = croniter(self.cron_expression, base_time or datetime.now())
            return cron.get_next(datetime)
        except Exception as e:
            logger.error(f"计算下次运行时间失败：{e}")
            raise ValueError(f"无效的 Cron 表达式：{self.cron_expression}") from e
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "id": self.id,
            "name": self.name,
            "cron_expression": self.cron_expression,
            "task_prompt": self.task_prompt,
            "enabled": self.enabled,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "next_run": self.next_run.isoformat() if self.next_run else None,
            "notify_channels": self.notify_channels,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "run_once": self.run_once,
            "archived": self.archived,
            "executor": self.executor,
            "execution_model": self.execution_model,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "retry_delay_seconds": self.retry_delay_seconds,
            "misfire_grace_seconds": self.misfire_grace_seconds,
            "overlap_policy": self.overlap_policy,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "last_duration_ms": self.last_duration_ms,
            "consecutive_failures": self.consecutive_failures,
        }


class TaskScheduler:
    """定时任务调度器"""
    
    def __init__(self, opencode_ws=None, memory_manager=None, notification_service=None, db_path: Optional[str] = None, check_interval_seconds: float = 5.0, max_concurrent_tasks: int = 2):
        self.db_path = str(db_path or (settings.DATA_DIR / "scheduled_tasks.db"))
        self.opencode_ws = opencode_ws
        self.memory_manager = memory_manager
        self.notification_service = notification_service
        
        self.tasks: Dict[str, ScheduledTask] = {}
        self.running = False
        self._check_task: Optional[asyncio.Task] = None
        self._db_lock = threading.Lock()
        self._check_interval_seconds = max(1.0, float(check_interval_seconds))
        self._execution_slots = asyncio.Semaphore(max(1, int(max_concurrent_tasks)))
        # 必须保存后台 Task 的强引用，否则事件循环可能在任务结束前回收它。
        self._background_tasks: Set[asyncio.Task] = set()
        self._active_task_ids: Set[str] = set()

        # 初始化数据库
        self._init_db()
        
        logger.info("定时任务调度器初始化完成")
    
    def _init_db(self):
        """初始化数据库（仅建表，不持久保留连接）"""
        # Bug5 修复：使用 check_same_thread=False 并通过 _db_lock 保证线程安全；
        # 持久连接仅在初始化时使用，后续操作每次新建短连接。
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                cron_expression TEXT NOT NULL,
                task_prompt TEXT NOT NULL,
                enabled BOOLEAN DEFAULT 1,
                last_run TIMESTAMP,
                next_run TIMESTAMP,
                notify_channels TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                run_once BOOLEAN DEFAULT 0,
                archived BOOLEAN DEFAULT 0,
                executor TEXT DEFAULT 'opencode',
                execution_model TEXT DEFAULT ''
            )
        """)

        # Migration: add columns if they don't exist yet
        existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(scheduled_tasks)")}
        if "run_once" not in existing_cols:
            cursor.execute("ALTER TABLE scheduled_tasks ADD COLUMN run_once BOOLEAN DEFAULT 0")
        if "archived" not in existing_cols:
            cursor.execute("ALTER TABLE scheduled_tasks ADD COLUMN archived BOOLEAN DEFAULT 0")
        if "executor" not in existing_cols:
            cursor.execute("ALTER TABLE scheduled_tasks ADD COLUMN executor TEXT DEFAULT 'opencode'")
        if "execution_model" not in existing_cols:
            cursor.execute("ALTER TABLE scheduled_tasks ADD COLUMN execution_model TEXT DEFAULT ''")
        task_column_defaults = {
            "timeout_seconds": "INTEGER DEFAULT 1800",
            "max_retries": "INTEGER DEFAULT 1",
            "retry_delay_seconds": "INTEGER DEFAULT 30",
            "misfire_grace_seconds": "INTEGER DEFAULT 900",
            "overlap_policy": "TEXT DEFAULT 'skip'",
            "last_status": "TEXT DEFAULT ''",
            "last_error": "TEXT DEFAULT ''",
            "last_duration_ms": "INTEGER DEFAULT 0",
            "consecutive_failures": "INTEGER DEFAULT 0",
        }
        for column, definition in task_column_defaults.items():
            if column not in existing_cols:
                cursor.execute(f"ALTER TABLE scheduled_tasks ADD COLUMN {column} {definition}")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_logs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                task_name TEXT,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                status TEXT DEFAULT 'running',
                result TEXT,
                error TEXT,
                tokens_used INTEGER DEFAULT 0,
                FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
            )
        """)
        log_cols = {row[1] for row in cursor.execute("PRAGMA table_info(task_logs)")}
        for column, definition in {
            "scheduled_for": "TIMESTAMP",
            "trigger_type": "TEXT DEFAULT 'schedule'",
            "attempts": "INTEGER DEFAULT 1",
            "duration_ms": "INTEGER DEFAULT 0",
        }.items():
            if column not in log_cols:
                cursor.execute(f"ALTER TABLE task_logs ADD COLUMN {column} {definition}")

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_enabled 
            ON scheduled_tasks(enabled)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_next_run 
            ON scheduled_tasks(next_run)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_archived
            ON scheduled_tasks(archived)
        """)

        # 数据库内旧执行器一次性迁移；重复执行 UPDATE 仍保持幂等。
        cursor.execute(
            """UPDATE scheduled_tasks SET executor = 'codex'
               WHERE lower(COALESCE(executor, '')) IN
               ('hermes', 'hermes_cli', 'hermes-agent', 'hermes_agent')"""
        )
        conn.commit()
        conn.close()
        logger.info("定时任务数据库初始化完成")

    def _get_conn(self) -> sqlite3.Connection:
        """获取线程安全的数据库连接（调用方负责 close）"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    
    async def start(self):
        """启动调度器"""
        self.running = True
        self._recover_interrupted_runs()
        await self._load_tasks()
        
        # 启动检查循环
        if self._check_task is None or self._check_task.done():
            self._check_task = asyncio.create_task(self._check_loop())
        logger.info("定时任务调度器已启动")
    
    async def stop(self):
        """停止调度器"""
        self.running = False
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        self._check_task = None
        pending = list(self._background_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info("定时任务调度器已停止")

    def _recover_interrupted_runs(self):
        """应用异常退出后，将没有完成时间的运行记录标记为已中断。"""
        with self._db_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """UPDATE task_logs SET status = 'interrupted', completed_at = ?,
                       error = COALESCE(NULLIF(error, ''), '应用重启前任务未正常结束')
                       WHERE status = 'running' AND completed_at IS NULL""",
                    (datetime.now().isoformat(),),
                )
                conn.commit()
            finally:
                conn.close()
    
    async def _load_tasks(self):
        """从数据库加载任务（加载所有非归档任务，包括 enabled=0 的禁用任务）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # 只加载未归档的任务到内存，归档任务仅留在数据库供查询
        cursor.execute("SELECT * FROM scheduled_tasks WHERE archived = 0 OR archived IS NULL")

        for row in cursor.fetchall():
            task = ScheduledTask(
                id=row["id"],
                name=row["name"],
                cron_expression=row["cron_expression"],
                task_prompt=row["task_prompt"],
                enabled=bool(row["enabled"]),
                last_run=datetime.fromisoformat(row["last_run"]) if row["last_run"] else None,
                next_run=datetime.fromisoformat(row["next_run"]) if row["next_run"] else None,
                notify_channels=json.loads(row["notify_channels"]) if row["notify_channels"] else [],
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                run_once=bool(row["run_once"]) if row["run_once"] is not None else False,
                archived=bool(row["archived"]) if row["archived"] is not None else False,
                executor=row["executor"] if "executor" in row.keys() else "opencode",
                execution_model=row["execution_model"] if "execution_model" in row.keys() else "",
                timeout_seconds=row["timeout_seconds"] if "timeout_seconds" in row.keys() else 1800,
                max_retries=row["max_retries"] if "max_retries" in row.keys() else 1,
                retry_delay_seconds=row["retry_delay_seconds"] if "retry_delay_seconds" in row.keys() else 30,
                misfire_grace_seconds=row["misfire_grace_seconds"] if "misfire_grace_seconds" in row.keys() else 900,
                overlap_policy=row["overlap_policy"] if "overlap_policy" in row.keys() else "skip",
                last_status=row["last_status"] if "last_status" in row.keys() else "",
                last_error=row["last_error"] if "last_error" in row.keys() else "",
                last_duration_ms=row["last_duration_ms"] if "last_duration_ms" in row.keys() else 0,
                consecutive_failures=row["consecutive_failures"] if "consecutive_failures" in row.keys() else 0,
            )

            if task.enabled:
                # 过期的 next_run 不能在加载时静默丢弃，由检查循环按 misfire 策略记录并补跑/跳过。
                if not task.next_run:
                    task.next_run = task.calculate_next_run()
                    # 同步回数据库
                    conn.execute(
                        "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?",
                        (task.next_run.isoformat(), task.id)
                    )

            self.tasks[task.id] = task

        conn.commit()
        conn.close()
        logger.info(f"加载了 {len(self.tasks)} 个定时任务")
    
    async def _check_loop(self):
        """短周期检查到期任务；到期任务后台并发运行，慢任务不阻塞其他任务。"""
        while self.running:
            now = datetime.now()

            for task in list(self.tasks.values()):
                if task.enabled and task.next_run and now >= task.next_run:
                    self._claim_and_schedule(task, now)

            await asyncio.sleep(self._check_interval_seconds)

    def _track_background_task(self, coroutine) -> asyncio.Task:
        background = asyncio.create_task(coroutine)
        self._background_tasks.add(background)
        background.add_done_callback(self._background_tasks.discard)
        return background

    def _claim_and_schedule(self, task: ScheduledTask, now: datetime):
        """先持久化推进时间再执行，避免检查循环重复触发同一时刻。"""
        scheduled_for = task.next_run
        run_once = task.run_once or (task.task_prompt or "").startswith("__RUN_ONCE__")
        delay_seconds = max(0, int((now - scheduled_for).total_seconds()))

        if run_once:
            task.enabled = False
            task.next_run = None
        else:
            # coalesce：直接以当前时间计算下一次，不逐个遍历停机期间可能成千上万次的触发点。
            task.next_run = task.calculate_next_run(now)
        self._save_task(task)

        if delay_seconds > task.misfire_grace_seconds:
            task.last_status = "skipped"
            task.last_error = f"错过计划时间 {delay_seconds} 秒，超过宽限期 {task.misfire_grace_seconds} 秒"
            self._save_task(task)
            self._track_background_task(self._record_skipped_run(task, scheduled_for, task.last_error))
            return

        if task.overlap_policy == "skip" and task.id in self._active_task_ids:
            reason = "上一次运行尚未结束，已按防重入策略跳过"
            task.last_status = "skipped"
            task.last_error = reason
            self._save_task(task)
            self._track_background_task(self._record_skipped_run(task, scheduled_for, reason))
            return

        self._track_background_task(self._run_claimed_task(task, scheduled_for, "schedule"))

    async def _record_skipped_run(self, task: ScheduledTask, scheduled_for: datetime, reason: str):
        log_id = f"log_{task.id}_{datetime.now().timestamp()}"
        await self._create_task_log(log_id, task.id, task.name, scheduled_for, "schedule")
        await self._complete_task_log(log_id, "skipped", error=reason, attempts=0)

    async def _run_claimed_task(self, task: ScheduledTask, scheduled_for: Optional[datetime], trigger_type: str, preclaimed: bool = False):
        if not preclaimed and task.overlap_policy == "skip" and task.id in self._active_task_ids:
            await self._record_skipped_run(task, scheduled_for or datetime.now(), "上一次运行尚未结束，已按防重入策略跳过")
            return
        if not preclaimed:
            self._active_task_ids.add(task.id)
        try:
            async with self._execution_slots:
                await self._execute_task(task, scheduled_for=scheduled_for, trigger_type=trigger_type)
        finally:
            self._active_task_ids.discard(task.id)
    
    async def _execute_task(self, task: ScheduledTask, scheduled_for: Optional[datetime] = None, trigger_type: str = "schedule"):
        """执行定时任务

        执行逻辑：
        - 带 __REMINDER__ 前缀的纯提醒任务：直接生成提醒内容，不调用 OpenCode
        - 其他所有任务（包括带 __RUN_ONCE__ 前缀的一次性任务）：
          按任务自身的 executor 分别交给 Codex Agent Harness 或 OpenCode 处理，
          task_prompt 中只包含纯任务内容
          （时间部分已在创建任务时从原始消息中剥离）
        """
        raw_prompt = task.task_prompt or ""
        run_once = task.run_once
        if raw_prompt.startswith("__RUN_ONCE__"):
            run_once = True
            raw_prompt = raw_prompt.split("\n", 1)[1] if "\n" in raw_prompt else ""

        # 只有明确带 __REMINDER__ 标志的才走提醒路径（不通过 AI 执行）
        reminder_payload = None
        if raw_prompt.startswith("__REMINDER__"):
            reminder_payload = raw_prompt.split("\n", 1)[1] if "\n" in raw_prompt else ""
            raw_prompt = reminder_payload

        # is_reminder 严格限定：仅当任务 prompt 显式带 __REMINDER__ 前缀时才走纯提醒路径
        # 不再根据任务名称中的"提醒"二字来判断，避免误将真实任务（如"提醒写周报"）走提醒路径
        is_reminder = reminder_payload is not None

        # 创建任务日志
        started_at = datetime.now()
        log_id = f"log_{task.id}_{started_at.timestamp()}"
        await self._create_task_log(
            log_id=log_id,
            task_id=task.id,
            task_name=task.name,
            scheduled_for=scheduled_for,
            trigger_type=trigger_type,
        )

        final_result = None
        final_error = ""
        attempts = 0
        for attempt in range(task.max_retries + 1):
          attempts = attempt + 1
          try:
            execution_model = None
            model_notice = ""
            result = None
            if is_reminder:
                # 纯提醒：不消耗 AI，直接返回提醒内容
                result = SimpleNamespace(success=True, content=raw_prompt, error=None, tokens_used=0)
            else:
                execution_model, model_notice = await self._resolve_task_execution_model(task)

            execution_instructions = ""
            if not is_reminder:
                # 定时任务无人值守，不能依赖聊天中的临时上下文。每次尝试都生成一份
                # 包含计划时间、重试序号、证据和停止条件的短契约；不改写持久化 prompt。
                from core.prompt_optimizer import build_scheduled_task_instructions

                optimization = build_scheduled_task_instructions(
                    raw_prompt,
                    task_name=task.name,
                    scheduled_for=scheduled_for,
                    trigger_type=trigger_type,
                    attempt=attempts,
                    max_retries=task.max_retries,
                )
                execution_instructions = optimization.developer_contract
                logger.debug(
                    f"[prompt-optimizer] 定时任务执行契约已启用："
                    f"task={task.name}, score={optimization.score}, attempt={attempts}"
                )

            if result is not None:
                pass
            elif normalize_task_executor(getattr(task, "executor", "opencode")) == "codex":
                result = await asyncio.wait_for(
                    self._execute_codex_task(
                        task,
                        raw_prompt,
                        model=execution_model,
                        developer_instructions=execution_instructions,
                    ),
                    timeout=task.timeout_seconds,
                )
            elif self.opencode_ws:
                # 像聊天一样通过 opencode cli 执行任务
                # raw_prompt 此时是纯任务内容（已去除时间前缀）
                logger.info(f"通过 OpenCode 执行任务：{task.name}，model={execution_model or 'default'}，prompt：{raw_prompt[:100]}...")
                result = await asyncio.wait_for(
                    self.opencode_ws.execute_task(
                        raw_prompt,
                        model=execution_model or None,
                        mode="agent",
                        system=execution_instructions or None,
                    ),
                    timeout=task.timeout_seconds,
                )
            else:
                raise RuntimeError("OpenCode 客户端未初始化，无法执行该定时任务")

            if model_notice and result and result.success:
                result = SimpleNamespace(
                    success=True,
                    content=f"{result.content}\n\n{model_notice}",
                    error=None,
                    tokens_used=getattr(result, "tokens_used", 0)
                )

            saved_path = None
            if result and result.success:
                saved_path = self._try_save_markdown_output(task_prompt=task.task_prompt or "", content=str(result.content or ""))
                if saved_path:
                    result = SimpleNamespace(
                        success=True,
                        content=f"{result.content}\n\n已保存：{saved_path}",
                        error=None,
                        tokens_used=getattr(result, "tokens_used", 0)
                    )
            
            if result and result.success:
                final_result = result
                final_error = ""
                break
            final_error = str(getattr(result, "error", "任务返回失败") or "任务返回失败")
          except asyncio.CancelledError:
            duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
            task.last_run = datetime.now()
            task.last_status = "interrupted"
            task.last_error = "应用关闭或任务被取消"
            task.last_duration_ms = duration_ms
            self._save_task(task)
            await self._complete_task_log(
                log_id, "interrupted", error=task.last_error,
                attempts=attempts, duration_ms=duration_ms,
            )
            raise
          except asyncio.TimeoutError:
            final_error = f"执行超过 {task.timeout_seconds} 秒，已超时终止"
          except Exception as e:
            final_error = str(e)
          if attempt < task.max_retries:
            logger.warning(f"定时任务执行失败，{task.retry_delay_seconds} 秒后重试：{task.name}，{final_error}")
            await asyncio.sleep(task.retry_delay_seconds)

        duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
        success = bool(final_result and final_result.success)
        task.last_run = datetime.now()
        task.last_status = "success" if success else "failed"
        task.last_error = "" if success else final_error
        task.last_duration_ms = duration_ms
        task.consecutive_failures = 0 if success else task.consecutive_failures + 1
        self._save_task(task)
        await self._complete_task_log(
            log_id=log_id,
            status=task.last_status,
            result=final_result.content if success else None,
            error=None if success else final_error,
            tokens_used=getattr(final_result, "tokens_used", 0) if final_result else 0,
            attempts=attempts,
            duration_ms=duration_ms,
        )

        # 通知失败不能反向覆盖真实执行结果。
        if self.notification_service:
            try:
                await self.notification_service.send_task_notification(
                    task=task,
                    result=final_result,
                    is_error=not success,
                    error_message=final_error if not success else None,
                )
            except Exception as notify_error:
                logger.error(f"定时任务通知发送失败：{notify_error}")
        return success

    def _fallback_execution_model(self) -> str:
        return (
            app_config.memory.organize_model
            or app_config.general.chat_default_model
            or app_config.models.primary_model
            or ""
        ).strip()

    @staticmethod
    def _match_available_model(requested: str, available_ids: set[str]) -> Optional[str]:
        requested = normalize_task_execution_model(requested)
        if not requested:
            return None
        if requested in available_ids:
            return requested
        if "/" not in requested:
            suffix = f"/{requested}"
            matches = [mid for mid in available_ids if mid.endswith(suffix)]
            if len(matches) == 1:
                return matches[0]
        return None

    async def _available_model_ids(self, executor: str = "opencode") -> Optional[set[str]]:
        if normalize_task_executor(executor) == "codex":
            try:
                from core.codex_runtime import codex_runtime

                models = await codex_runtime.models()
            except Exception as exc:
                logger.warning(f"检查 Codex 定时任务模型可用性失败：{exc}")
                return None
            return {
                str(item.get("id") or item.get("model") or "").strip()
                for item in models
                if isinstance(item, dict) and str(item.get("id") or item.get("model") or "").strip()
            }
        if not self.opencode_ws:
            return None
        try:
            models = await self.opencode_ws.get_models()
        except Exception as exc:
            logger.warning(f"检查定时任务模型可用性失败：{exc}")
            return None
        if not isinstance(models, list):
            return set()
        return {
            str(item.get("id") or "").strip()
            for item in models
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }

    async def _resolve_task_execution_model(self, task: ScheduledTask) -> tuple[Optional[str], str]:
        requested = normalize_task_execution_model(getattr(task, "execution_model", ""))
        fallback = self._fallback_execution_model()
        available_ids = await self._available_model_ids(getattr(task, "executor", "opencode"))

        if available_ids is None:
            model = requested or fallback
            if model:
                logger.warning(f"未能验证定时任务模型可用性，暂按配置模型执行：{model}")
            return (model or None), ""

        if requested:
            matched = self._match_available_model(requested, available_ids)
            if matched:
                return matched, ""
            logger.warning(f"定时任务模型不可用，准备使用备用模型：task={task.name}, requested={requested}, fallback={fallback or '未配置'}")

        if fallback:
            matched_fallback = self._match_available_model(fallback, available_ids)
            if matched_fallback:
                if requested and requested != matched_fallback:
                    return matched_fallback, f"注意：原执行模型 `{requested}` 当前不可用，已改用记忆整理备用模型 `{matched_fallback}`。"
                return matched_fallback, ""
            if requested:
                raise RuntimeError(f"定时任务执行模型不可用：{requested}；备用模型也不可用：{fallback}")

        if requested:
            raise RuntimeError(f"定时任务执行模型不可用：{requested}；且未配置可用的记忆整理备用模型")
        return None, ""

    async def _execute_codex_task(
        self,
        task: ScheduledTask,
        raw_prompt: str,
        model: Optional[str] = None,
        developer_instructions: str = "",
    ):
        """使用非交互、禁止提权的 Codex turn 执行定时任务。"""
        from core.codex_runtime import codex_runtime

        # 定时任务没有 Codebot 对话外键，使用稳定负整数隔离其持久化 thread，
        # 既能续接同一任务，也不会与正常的正数 conversation_id 冲突。
        task_digest = hashlib.sha256(str(task.id).encode("utf-8")).hexdigest()[:15]
        runtime_conversation_id = -int(task_digest, 16) - 1
        logger.info(
            f"通过 Codex Agent Harness 执行任务：{task.name}，"
            f"model={model or 'default'}，prompt：{raw_prompt[:100]}..."
        )
        content = ""
        async for event in codex_runtime.run_turn_stream(
            message=raw_prompt,
            conversation_id=runtime_conversation_id,
            model=model,
            mode="agent",
            developer_instructions=(
                f"{developer_instructions.strip()}\n\n"
                "该任务必须全程非交互运行；不得请求人工审批，"
                "不得尝试越过 Codex 沙箱。"
            ),
            interactive=False,
        ):
            if event.get("type") in {"content_delta", "done"}:
                content = str(event.get("content") or content)
        content = content.strip()
        if not content:
            raise RuntimeError("Codex 定时任务已结束，但没有返回可显示内容")
        return SimpleNamespace(success=True, content=content, error=None, tokens_used=0)

    def _try_save_markdown_output(self, task_prompt: str, content: str) -> Optional[str]:
        """尝试将任务输出保存为 Markdown 文件。

        支持以下路径格式（按优先级）：
        1. "Markdown 文件到 <dir> 目录"
        2. "保存到/存放到 <dir>" / "保存到/存放到"<dir>"" （含引号的路径）
        3. "下载" / "Downloads" 文件夹别名
        4. 路径中含盘符的 Windows 绝对路径（如 D:\\xxx）
        """
        if not task_prompt or not content:
            return None

        out_dir = None

        # 格式1：Markdown 文件到 <dir> 目录
        m = re.search(r"Markdown\s*文件\s*到\s*(.+?)\s*目录", task_prompt, flags=re.IGNORECASE)
        if not m:
            m = re.search(r"Markdown\s*文件\s*到\s*(.+)", task_prompt, flags=re.IGNORECASE)
        if m:
            out_dir = (m.group(1) or "").strip()
            out_dir = re.split(r"[，。,.!！?？;；\n\r]", out_dir, maxsplit=1)[0].strip().strip("\"''\u201c\u201d")

        # 格式2：保存到/存放到"<dir>" 或 保存到/存放到 <dir>（支持引号路径）
        if not out_dir:
            m = re.search(r'(?:保存|存放|存|放)(?:到|至)\s*["“\'](.*?)["”\']', task_prompt)
            if m:
                out_dir = m.group(1).strip()
        if not out_dir:
            m = re.search(r'(?:保存|存放|存|放)(?:到|至)\s*([A-Za-z]:[^\s，。,!！?？;；\n\r]+)', task_prompt)
            if m:
                out_dir = m.group(1).strip().rstrip("，。,!！?？;；")
        if not out_dir:
            m = re.search(r'(?:保存|存放|存|放)(?:到|至)\s*["“\']?([^"“”\'\s，。,!！?？;；\n\r]+)["”\']?\s*(?:文件夹|目录)?', task_prompt)
            if m:
                out_dir = m.group(1).strip().rstrip("，。,!！?？;；")

        if not out_dir:
            return None

        if out_dir.lower() in {"下载", "下载文件夹", "downloads", "download"}:
            out_path = Path.home() / "Downloads"
        else:
            out_path = Path(out_dir)
        try:
            out_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None

        body = content.strip()
        fenced = re.search(r"```(?:markdown|md)?\s*([\s\S]*?)```", body, flags=re.IGNORECASE)
        if fenced:
            extracted = fenced.group(1).strip()
            if extracted:
                body = extracted

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = out_path / f"{ts}.md"
        try:
            file_path.write_text(body, encoding="utf-8")
            return str(file_path)
        except Exception:
            return None

    async def _create_task_log(self, log_id: str, task_id: str, task_name: str, scheduled_for: Optional[datetime] = None, trigger_type: str = "schedule"):
        """创建任务日志"""
        with self._db_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO task_logs
                       (id, task_id, task_name, started_at, status, scheduled_for, trigger_type)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (log_id, task_id, task_name, datetime.now().isoformat(), "running",
                     scheduled_for.isoformat() if scheduled_for else None, trigger_type)
                )
                conn.commit()
            finally:
                conn.close()
    
    async def _complete_task_log(
        self,
        log_id: str,
        status: str,
        result: str = None,
        error: str = None,
        tokens_used: int = 0,
        attempts: int = 1,
        duration_ms: int = 0,
    ):
        """完成日志记录"""
        with self._db_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """UPDATE task_logs 
                       SET completed_at = ?, status = ?, result = ?, 
                           error = ?, tokens_used = ?, attempts = ?, duration_ms = ?
                       WHERE id = ?""",
                    (
                        datetime.now().isoformat(),
                        status,
                        result,
                        error,
                        tokens_used,
                        attempts,
                        duration_ms,
                        log_id
                    )
                )
                conn.commit()
            finally:
                conn.close()
    
    def _save_task(self, task: ScheduledTask):
        """保存任务到数据库"""
        with self._db_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO scheduled_tasks 
                       (id, name, cron_expression, task_prompt, enabled, 
                        last_run, next_run, notify_channels, created_at,
                        run_once, archived, executor, execution_model,
                        timeout_seconds, max_retries, retry_delay_seconds,
                        misfire_grace_seconds, overlap_policy, last_status,
                        last_error, last_duration_ms, consecutive_failures)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task.id,
                        task.name,
                        task.cron_expression,
                        task.task_prompt,
                        task.enabled,
                        task.last_run.isoformat() if task.last_run else None,
                        task.next_run.isoformat() if task.next_run else None,
                        json.dumps(task.notify_channels),
                        task.created_at.isoformat() if task.created_at else None,
                        task.run_once,
                        task.archived,
                        task.executor,
                        task.execution_model,
                        task.timeout_seconds,
                        task.max_retries,
                        task.retry_delay_seconds,
                        task.misfire_grace_seconds,
                        task.overlap_policy,
                        task.last_status,
                        task.last_error,
                        task.last_duration_ms,
                        task.consecutive_failures,
                    )
                )
                conn.commit()
            finally:
                conn.close()
    
    def create_task(
        self,
        name: str,
        cron_expression: str,
        task_prompt: str,
        notify_channels: List[str] = None,
        run_once: bool = False,
        executor: str = "opencode",
        execution_model: str = "",
        timeout_seconds: int = 1800,
        max_retries: int = 1,
        retry_delay_seconds: int = 30,
        misfire_grace_seconds: int = 900,
        overlap_policy: str = "skip",
    ) -> ScheduledTask:
        """创建新任务"""
        task = ScheduledTask(
            id=f"task_{datetime.now().timestamp()}",
            name=name,
            cron_expression=cron_expression,
            task_prompt=task_prompt,
            enabled=True,
            notify_channels=notify_channels or [],
            run_once=run_once,
            archived=False,
            executor=executor,
            execution_model=execution_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            misfire_grace_seconds=misfire_grace_seconds,
            overlap_policy=overlap_policy,
        )
        
        # 计算下次运行时间
        task.next_run = task.calculate_next_run()
        
        self.tasks[task.id] = task
        self._save_task(task)
        
        logger.info(f"创建定时任务：{task.name}")
        return task
    
    def update_task(self, task_id: str, **kwargs) -> Optional[ScheduledTask]:
        """更新任务"""
        if task_id not in self.tasks:
            logger.error(f"任务不存在：{task_id}")
            return None
        
        task = self.tasks[task_id]
        
        for key, value in kwargs.items():
            if hasattr(task, key):
                if key == "executor":
                    value = normalize_task_executor(value)
                elif key == "execution_model":
                    value = normalize_task_execution_model(value)
                elif key == "timeout_seconds":
                    value = max(30, int(value or 1800))
                elif key == "max_retries":
                    value = max(0, min(5, int(value or 0)))
                elif key == "retry_delay_seconds":
                    value = max(1, int(value or 30))
                elif key == "misfire_grace_seconds":
                    value = max(0, int(value or 0))
                elif key == "overlap_policy":
                    value = value if value in {"skip", "parallel"} else "skip"
                setattr(task, key, value)
        
        # 如果 cron 表达式改变，重新计算下次运行时间
        if "cron_expression" in kwargs:
            task.next_run = task.calculate_next_run()
        
        self._save_task(task)
        logger.info(f"更新定时任务：{task.name}")
        return task
    
    def delete_task(self, task_id: str):
        """删除任务"""
        if task_id in self.tasks:
            del self.tasks[task_id]

            with self._db_lock:
                conn = self._get_conn()
                try:
                    conn.execute(
                        "DELETE FROM scheduled_tasks WHERE id = ?",
                        (task_id,)
                    )
                    conn.commit()
                finally:
                    conn.close()

            logger.info(f"删除定时任务：{task_id}")
    
    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        """获取任务详情"""
        return self.tasks.get(task_id)
    
    def list_tasks(self) -> List[ScheduledTask]:
        """列出所有未归档任务"""
        return list(self.tasks.values())

    def archive_task(self, task_id: str) -> bool:
        """归档任务：标记 archived=True、disabled，从内存列表移除，保留在数据库"""
        task = self.tasks.get(task_id)
        if not task:
            # 可能已不在内存（已禁用），直接从数据库操作
            with self._db_lock:
                conn = self._get_conn()
                try:
                    rows = conn.execute(
                        "SELECT id FROM scheduled_tasks WHERE id = ?", (task_id,)
                    ).fetchone()
                    if not rows:
                        return False
                    conn.execute(
                        "UPDATE scheduled_tasks SET archived = 1, enabled = 0 WHERE id = ?",
                        (task_id,)
                    )
                    conn.commit()
                finally:
                    conn.close()
            return True

        task.archived = True
        task.enabled = False
        self._save_task(task)
        del self.tasks[task_id]
        logger.info(f"归档定时任务：{task_id}")
        return True

    def list_archived_tasks(self) -> List[dict]:
        """从数据库读取已归档任务列表"""
        with self._db_lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE archived = 1"
                ).fetchall()
                result = []
                for row in rows:
                    t = ScheduledTask(
                        id=row["id"],
                        name=row["name"],
                        cron_expression=row["cron_expression"],
                        task_prompt=row["task_prompt"],
                        enabled=bool(row["enabled"]),
                        last_run=datetime.fromisoformat(row["last_run"]) if row["last_run"] else None,
                        next_run=datetime.fromisoformat(row["next_run"]) if row["next_run"] else None,
                        notify_channels=json.loads(row["notify_channels"]) if row["notify_channels"] else [],
                        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
                        run_once=bool(row["run_once"]) if row["run_once"] is not None else False,
                        archived=True,
                        executor=row["executor"] if "executor" in row.keys() else "opencode",
                        execution_model=row["execution_model"] if "execution_model" in row.keys() else "",
                        timeout_seconds=row["timeout_seconds"] if "timeout_seconds" in row.keys() else 1800,
                        max_retries=row["max_retries"] if "max_retries" in row.keys() else 1,
                        retry_delay_seconds=row["retry_delay_seconds"] if "retry_delay_seconds" in row.keys() else 30,
                        misfire_grace_seconds=row["misfire_grace_seconds"] if "misfire_grace_seconds" in row.keys() else 900,
                        overlap_policy=row["overlap_policy"] if "overlap_policy" in row.keys() else "skip",
                        last_status=row["last_status"] if "last_status" in row.keys() else "",
                        last_error=row["last_error"] if "last_error" in row.keys() else "",
                        last_duration_ms=row["last_duration_ms"] if "last_duration_ms" in row.keys() else 0,
                        consecutive_failures=row["consecutive_failures"] if "consecutive_failures" in row.keys() else 0,
                    )
                    result.append(t.to_dict())
                return result
            finally:
                conn.close()
    
    async def run_task_now(self, task_id: str) -> bool:
        """立即执行任务"""
        task = self.get_task(task_id)
        if not task:
            logger.error(f"任务不存在：{task_id}")
            return False
        
        if task.overlap_policy == "skip" and task.id in self._active_task_ids:
            logger.warning(f"任务正在运行，拒绝重复启动：{task.name}")
            return False

        # 保留后台任务强引用；手动运行不改变原定的下一次计划时间。
        if task.overlap_policy == "skip":
            self._active_task_ids.add(task.id)
        self._track_background_task(self._run_claimed_task(task, datetime.now(), "manual", preclaimed=task.overlap_policy == "skip"))
        logger.info(f"立即执行任务：{task.name}")
        return True

    def runtime_status(self) -> Dict:
        """返回调度器实时状态，供 UI/健康检查观察。"""
        return {
            "running": self.running,
            "check_interval_seconds": self._check_interval_seconds,
            "active_task_ids": sorted(self._active_task_ids),
            "background_task_count": len(self._background_tasks),
            "enabled_task_count": sum(1 for task in self.tasks.values() if task.enabled),
        }
