"""
数据库初始化
"""
import sqlite3
from pathlib import Path
from loguru import logger
from config import settings


class Database:
    """数据库管理器"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
    
    def connect(self):
        """连接数据库"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        logger.info(f"数据库连接成功：{self.db_path}")
    
    def close(self):
        """关闭连接"""
        if self.conn:
            self.conn.close()
            logger.info("数据库连接已关闭")
    
    def init_tables(self):
        """初始化表结构"""
        cursor = self.conn.cursor()
        
        # 对话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_archived BOOLEAN DEFAULT 0
            )
        """)
        
        # 消息表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tokens INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
        """)
        
        # 长期记忆表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS long_term_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_archived BOOLEAN DEFAULT 0
            )
        """)
        
        # 定时任务表
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
        
        # 任务日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_logs (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                task_name TEXT NOT NULL,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                status TEXT NOT NULL,
                result TEXT,
                error TEXT,
                tokens_used INTEGER DEFAULT 0,
                notify_sent BOOLEAN DEFAULT 0,
                notify_channels TEXT,
                FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
            )
        """)
        
        # 通知表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                type TEXT DEFAULT 'info',
                read BOOLEAN DEFAULT 0,
                task_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 聊天日志表（记录每次聊天的内部提示词、推理过程和最终回复，供用户学习和优化）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                user_message TEXT NOT NULL,
                internal_prompt TEXT,
                tool_events TEXT,
                final_reply TEXT,
                model TEXT,
                mode TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
        """)
        
        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation 
            ON messages(conversation_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_created 
            ON messages(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_category 
            ON long_term_memories(category)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_enabled 
            ON scheduled_tasks(enabled)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tasks_next_run 
            ON scheduled_tasks(next_run)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_task 
            ON task_logs(task_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_logs_started 
            ON task_logs(started_at DESC)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_logs_conversation
            ON chat_logs(conversation_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_chat_logs_created
            ON chat_logs(created_at DESC)
        """)
        
        self.conn.commit()
        logger.info("数据库表初始化完成")


# 全局数据库实例
conversations_db = Database(str(Path(settings.DATA_DIR) / "conversations.db"))
tasks_db = Database(str(Path(settings.DATA_DIR) / "scheduled_tasks.db"))
