"""
定时任务调度器
"""
import asyncio
import sqlite3
import json
import threading
from datetime import datetime
from typing import List, Dict, Optional
from types import SimpleNamespace
from croniter import croniter
from loguru import logger
from pathlib import Path
import re

from config import settings


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
        created_at: datetime = None
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
    
    def calculate_next_run(self) -> datetime:
        """计算下次运行时间"""
        try:
            cron = croniter(self.cron_expression, datetime.now())
            return cron.get_next(datetime)
        except Exception as e:
            logger.error(f"计算下次运行时间失败：{e}")
            return datetime.now()
    
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
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class TaskScheduler:
    """定时任务调度器"""
    
    def __init__(self, opencode_ws=None, memory_manager=None, notification_service=None):
        self.db_path = str(settings.DATA_DIR / "scheduled_tasks.db")
        self.opencode_ws = opencode_ws
        self.memory_manager = memory_manager
        self.notification_service = notification_service
        
        self.tasks: Dict[str, ScheduledTask] = {}
        self.running = False
        self._db_lock = threading.Lock()

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

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

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_enabled 
            ON scheduled_tasks(enabled)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_next_run 
            ON scheduled_tasks(next_run)
        """)

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
        await self._load_tasks()
        
        # 启动检查循环
        asyncio.create_task(self._check_loop())
        logger.info("定时任务调度器已启动")
    
    async def stop(self):
        """停止调度器"""
        self.running = False
        logger.info("定时任务调度器已停止")
    
    async def _load_tasks(self):
        """从数据库加载任务（加载所有任务，包括 enabled=0 的禁用任务）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        # Bug1 修复：加载全部任务，而非仅 enabled=1，避免重启后任务丢失
        cursor.execute("SELECT * FROM scheduled_tasks")

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
                created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
            )

            # Bug4 修复：重启后 next_run 若已过期，重新基于当前时间计算下次执行时间
            # 对于周期性任务（非一次性），过期的 next_run 应推进到未来
            is_run_once = (task.task_prompt or "").startswith("__RUN_ONCE__")
            if task.enabled:
                if not task.next_run or (not is_run_once and task.next_run < datetime.now()):
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
        """每分钟检查是否有任务需要执行"""
        while self.running:
            now = datetime.now()

            for task in list(self.tasks.values()):
                if task.enabled and task.next_run:
                    if now >= task.next_run:
                        logger.info(f"执行定时任务：{task.name}")
                        await self._execute_task(task)

                        # Bug2 修复：无论执行成功/失败都必须更新 next_run，
                        # 否则下一分钟循环会再次触发同一任务
                        task.last_run = now
                        if task.enabled:
                            # 仍启用（非一次性/执行失败的一次性）：推进到下次
                            task.next_run = task.calculate_next_run()
                        self._save_task(task)

            await asyncio.sleep(60)  # 每分钟检查一次
    
    async def _execute_task(self, task: ScheduledTask):
        """执行定时任务

        执行逻辑：
        - 带 __REMINDER__ 前缀的纯提醒任务：直接生成提醒内容，不调用 OpenCode
        - 其他所有任务（包括带 __RUN_ONCE__ 前缀的一次性任务）：
          像聊天一样通过 opencode cli 处理，task_prompt 中只包含纯任务内容
          （时间部分已在创建任务时从原始消息中剥离）
        """
        raw_prompt = task.task_prompt or ""
        run_once = False
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
        log_id = f"log_{datetime.now().timestamp()}"
        await self._create_task_log(
            log_id=log_id,
            task_id=task.id,
            task_name=task.name
        )

        try:
            result = None
            if is_reminder:
                # 纯提醒：不消耗 AI，直接返回提醒内容
                result = SimpleNamespace(success=True, content=raw_prompt, error=None, tokens_used=0)
            elif self.opencode_ws and getattr(self.opencode_ws, "connected", False):
                # 像聊天一样通过 opencode cli 执行任务
                # raw_prompt 此时是纯任务内容（已去除时间前缀）
                logger.info(f"通过 OpenCode 执行任务：{task.name}，prompt：{raw_prompt[:100]}...")
                result = await self.opencode_ws.execute_task(raw_prompt)
            else:
                raise RuntimeError("OpenCode 未连接，无法执行该定时任务")

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
            
            # 更新日志
            await self._complete_task_log(
                log_id=log_id,
                status="success" if result.success else "failed",
                result=result.content if result.success else None,
                error=result.error if not result.success else None,
                tokens_used=result.tokens_used
            )
            
            # 发送通知
            if self.notification_service:
                await self.notification_service.send_task_notification(
                    task=task,
                    result=result,
                    is_error=not result.success
                )

            if run_once:
                # Bug3 修复：无论执行成功/失败，一次性任务都应禁用，避免重复触发
                task.enabled = False
                self._save_task(task)
            elif result and result.success:
                pass  # 周期性任务正常成功，next_run 已在 _check_loop 中更新
            
        except Exception as e:
            logger.error(f"定时任务执行失败：{e}")
            
            # 更新日志
            await self._complete_task_log(
                log_id=log_id,
                status="failed",
                error=str(e)
            )
            
            # 发送错误通知
            if self.notification_service:
                await self.notification_service.send_task_notification(
                    task=task,
                    result=None,
                    is_error=True,
                    error_message=str(e)
                )

    def _try_save_markdown_output(self, task_prompt: str, content: str) -> Optional[str]:
        """尝试将任务输出保存为 Markdown 文件。

        支持以下路径格式（按优先级）：
        1. "Markdown 文件到 <dir> 目录"
        2. "保存到 <dir>" / "保存到"<dir>"" （含引号的路径）
        3. 路径中含盘符的 Windows 绝对路径（如 D:\\xxx）
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

        # 格式2：保存到"<dir>" 或 保存到 <dir>（支持引号路径）
        if not out_dir:
            m = re.search(r'保存到\s*["""\'\'](.*?)["""\'\']', task_prompt)
            if m:
                out_dir = m.group(1).strip()
        if not out_dir:
            m = re.search(r'保存到\s*([A-Za-z]:[^\s，。,!！?？;；\n\r]+)', task_prompt)
            if m:
                out_dir = m.group(1).strip().rstrip("，。,!！?？;；")

        if not out_dir:
            return None

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

    async def _create_task_log(self, log_id: str, task_id: str, task_name: str):
        """创建任务日志"""
        with self._db_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO task_logs 
                       (id, task_id, task_name, started_at, status)
                       VALUES (?, ?, ?, ?, ?)""",
                    (log_id, task_id, task_name, datetime.now().isoformat(), "running")
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
        tokens_used: int = 0
    ):
        """完成日志记录"""
        with self._db_lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """UPDATE task_logs 
                       SET completed_at = ?, status = ?, result = ?, 
                           error = ?, tokens_used = ?
                       WHERE id = ?""",
                    (
                        datetime.now().isoformat(),
                        status,
                        result,
                        error,
                        tokens_used,
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
                        last_run, next_run, notify_channels, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task.id,
                        task.name,
                        task.cron_expression,
                        task.task_prompt,
                        task.enabled,
                        task.last_run.isoformat() if task.last_run else None,
                        task.next_run.isoformat() if task.next_run else None,
                        json.dumps(task.notify_channels),
                        task.created_at.isoformat() if task.created_at else None
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
        notify_channels: List[str] = None
    ) -> ScheduledTask:
        """创建新任务"""
        task = ScheduledTask(
            id=f"task_{datetime.now().timestamp()}",
            name=name,
            cron_expression=cron_expression,
            task_prompt=task_prompt,
            enabled=True,
            notify_channels=notify_channels or []
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
        """列出所有任务"""
        return list(self.tasks.values())
    
    async def run_task_now(self, task_id: str) -> bool:
        """立即执行任务"""
        task = self.get_task(task_id)
        if not task:
            logger.error(f"任务不存在：{task_id}")
            return False
        
        # 异步执行
        asyncio.create_task(self._execute_task(task))
        logger.info(f"立即执行任务：{task.name}")
        return True
