"""
记忆管理系统
"""
import sqlite3
import json
import chromadb
import shutil
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
from loguru import logger

from config import settings, MemoryConfig


class MemoryManager:
    """记忆管理器"""
    
    def __init__(self, data_dir: str = None, config: MemoryConfig = None):
        self.data_dir = data_dir or str(settings.DATA_DIR)
        self.config = config or MemoryConfig()
        
        # SQLite 连接
        self.sqlite_db = sqlite3.connect(
            str(Path(self.data_dir) / "conversations.db")
        )
        self.sqlite_db.row_factory = sqlite3.Row
        
        chroma_dir = Path(self.data_dir) / "chroma"
        self.chroma_client, self.memory_collection = self._init_chroma(chroma_dir)
        self.facts_collection = self.chroma_client.get_or_create_collection(name="facts_memory")
        
        # 初始化表结构
        self._init_tables()
        
        logger.info("记忆管理器初始化完成")
    
    def _init_tables(self):
        """初始化表结构"""
        cursor = self.sqlite_db.cursor()
        
        # 对话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_archived BOOLEAN DEFAULT 0,
                is_pinned BOOLEAN DEFAULT 0,
                is_group BOOLEAN DEFAULT 0,
                share_id TEXT
            )
        """)
        self._ensure_conversation_columns(cursor)
        
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_archived BOOLEAN DEFAULT 0
            )
        """)
        self._ensure_long_term_memory_columns(cursor)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL UNIQUE,
                value TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_archived BOOLEAN DEFAULT 0
            )
        """)
        
        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation 
            ON messages(conversation_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_category 
            ON long_term_memories(category)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_facts_key
            ON facts(key)
        """)
        
        self.sqlite_db.commit()
        logger.info("记忆数据库表初始化完成")

    def _ensure_conversation_columns(self, cursor: sqlite3.Cursor):
        columns = cursor.execute("PRAGMA table_info(conversations)").fetchall()
        existing = {row[1] for row in columns}
        if "is_pinned" not in existing:
            cursor.execute("ALTER TABLE conversations ADD COLUMN is_pinned BOOLEAN DEFAULT 0")
        if "is_group" not in existing:
            cursor.execute("ALTER TABLE conversations ADD COLUMN is_group BOOLEAN DEFAULT 0")
        if "share_id" not in existing:
            cursor.execute("ALTER TABLE conversations ADD COLUMN share_id TEXT")

    def _ensure_long_term_memory_columns(self, cursor: sqlite3.Cursor):
        columns = cursor.execute("PRAGMA table_info(long_term_memories)").fetchall()
        existing = {row[1] for row in columns}
        if "updated_at" not in existing:
            cursor.execute("ALTER TABLE long_term_memories ADD COLUMN updated_at TIMESTAMP")
            cursor.execute(
                "UPDATE long_term_memories "
                "SET updated_at = COALESCE(created_at, CURRENT_TIMESTAMP) "
                "WHERE updated_at IS NULL"
            )
    
    async def create_conversation(self, title: str = "新对话") -> int:
        """创建对话"""
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "INSERT INTO conversations (title) VALUES (?)",
            (title,)
        )
        self.sqlite_db.commit()
        conversation_id = cursor.lastrowid
        logger.info(f"创建对话：{conversation_id}")
        return conversation_id
    
    async def get_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
        archived: bool = False
    ) -> List[Dict]:
        """获取对话列表"""
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            """SELECT * FROM conversations 
               WHERE is_archived = ?
               ORDER BY is_pinned DESC, updated_at DESC 
               LIMIT ? OFFSET ?""",
            (1 if archived else 0, limit, offset)
        )
        
        return [dict(row) for row in cursor.fetchall()]
    
    async def get_conversation(self, conversation_id: int) -> Optional[Dict]:
        """获取对话详情"""
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conversation_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    
    async def delete_conversation(self, conversation_id: int):
        """删除对话"""
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conversation_id,)
        )
        self.sqlite_db.commit()
        logger.info(f"删除对话：{conversation_id}")

    async def update_conversation_title(self, conversation_id: int, title: str):
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, conversation_id)
        )
        self.sqlite_db.commit()

    async def set_conversation_pinned(self, conversation_id: int, pinned: bool):
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "UPDATE conversations SET is_pinned = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if pinned else 0, conversation_id)
        )
        self.sqlite_db.commit()

    async def set_conversation_archived(self, conversation_id: int, archived: bool):
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "UPDATE conversations SET is_archived = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if archived else 0, conversation_id)
        )
        self.sqlite_db.commit()

    async def set_conversation_group(self, conversation_id: int, is_group: bool):
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "UPDATE conversations SET is_group = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (1 if is_group else 0, conversation_id)
        )
        self.sqlite_db.commit()

    async def set_conversation_share_id(self, conversation_id: int, share_id: str):
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "UPDATE conversations SET share_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (share_id, conversation_id)
        )
        self.sqlite_db.commit()

    async def get_message_count(self, conversation_id: int) -> int:
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (conversation_id,)
        )
        row = cursor.fetchone()
        return int(row[0]) if row else 0
    
    async def save_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        tokens: int = 0
    ):
        """保存消息"""
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            """INSERT INTO messages (conversation_id, role, content, tokens) 
               VALUES (?, ?, ?, ?)""",
            (conversation_id, role, content, tokens)
        )
        
        # 更新对话时间
        cursor.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,)
        )
        
        self.sqlite_db.commit()
    
    async def get_messages(
        self,
        conversation_id: int,
        limit: int = 100
    ) -> List[Dict]:
        """获取消息历史"""
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            """SELECT * FROM messages 
               WHERE conversation_id = ? 
               ORDER BY created_at ASC 
               LIMIT ?""",
            (conversation_id, limit)
        )
        
        return [dict(row) for row in cursor.fetchall()]

    async def delete_message(self, message_id: int):
        """删除指定消息"""
        cursor = self.sqlite_db.cursor()
        cursor.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        self.sqlite_db.commit()
    
    # 语义去重阈值：ChromaDB 距离低于此值视为"相同或高度相似"的记忆
    _DEDUP_DISTANCE_THRESHOLD = 0.25

    async def save_long_term_memory(
        self,
        content: str,
        category: str = "habit",
        metadata: Dict = None
    ):
        """
        保存长期记忆（带语义去重）。

        保存前先用 ChromaDB 向量搜索检查是否存在相同/高度相似的记忆：
          - 若存在高度相似记忆且新内容更完善 → 更新已有记忆
          - 若存在几乎完全相同的记忆 → 跳过，不重复写入
          - 否则 → 正常新增
        """
        if not content or not content.strip():
            return

        # ── 语义去重：查询 ChromaDB 是否已有相似记忆 ──
        try:
            results = self.memory_collection.query(
                query_texts=[content],
                n_results=3,
            )
            if results and results.get("documents") and results["documents"][0]:
                for doc, meta, dist in zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                ):
                    if dist > self._DEDUP_DISTANCE_THRESHOLD:
                        continue  # 不够相似，跳过

                    existing_id = meta.get("memory_id")
                    if not existing_id:
                        continue

                    # 检查该记忆是否仍在活跃状态（未归档）
                    cursor = self.sqlite_db.cursor()
                    row = cursor.execute(
                        "SELECT id, content, is_archived FROM long_term_memories WHERE id = ?",
                        (existing_id,),
                    ).fetchone()
                    if not row or row["is_archived"]:
                        continue

                    existing_content = str(row["content"] or "")

                    # 若新内容完全被已有内容覆盖（子串），直接跳过
                    if content.strip() in existing_content or existing_content in content.strip():
                        if len(content.strip()) <= len(existing_content):
                            logger.debug(
                                f"[memory_manager] 跳过重复记忆（已有更完整版本），"
                                f"existing_id={existing_id}, dist={dist:.4f}"
                            )
                            return

                    # 新内容比已有内容更长/更完善 → 就地更新
                    if len(content.strip()) > len(existing_content):
                        merged_meta = {}
                        try:
                            old_meta_str = cursor.execute(
                                "SELECT metadata FROM long_term_memories WHERE id = ?",
                                (existing_id,),
                            ).fetchone()
                            if old_meta_str and old_meta_str["metadata"]:
                                merged_meta = json.loads(old_meta_str["metadata"])
                        except Exception:
                            pass
                        merged_meta.update(metadata or {})
                        merged_meta["improved_at"] = datetime.now().isoformat()
                        merged_meta["improved_from"] = existing_content[:200]
                        await self.update_long_term_memory(
                            memory_id=int(existing_id),
                            content=content,
                            metadata=merged_meta,
                        )
                        logger.info(
                            f"[memory_manager] 更新已有记忆（内容更完善），"
                            f"memory_id={existing_id}, dist={dist:.4f}"
                        )
                        return

                    # 距离极近（几乎完全相同）→ 跳过
                    if dist < 0.10:
                        logger.debug(
                            f"[memory_manager] 跳过几乎完全相同的记忆，"
                            f"existing_id={existing_id}, dist={dist:.4f}"
                        )
                        return
        except Exception as dedup_err:
            # 去重失败不阻塞保存
            logger.warning(f"[memory_manager] 语义去重检查失败（继续保存）: {dedup_err}")

        # ── 正常新增 ──
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            """INSERT INTO long_term_memories (category, content, metadata) 
               VALUES (?, ?, ?)""",
            (category, content, json.dumps(metadata or {}, ensure_ascii=False))
        )
        memory_id = cursor.lastrowid
        self.sqlite_db.commit()
        
        # 保存到 ChromaDB (向量搜索)
        chroma_id = f"memory_{memory_id}"
        metadatas = [{"category": category, "memory_id": memory_id}]
        try:
            upsert = getattr(self.memory_collection, "upsert", None)
            if callable(upsert):
                upsert(documents=[content], metadatas=metadatas, ids=[chroma_id])
            else:
                self.memory_collection.add(documents=[content], metadatas=metadatas, ids=[chroma_id])
        except Exception:
            self.memory_collection.add(documents=[content], metadatas=metadatas, ids=[f"{chroma_id}_{datetime.now().timestamp()}"])
        
        logger.info(f"保存长期记忆：{memory_id}")

    async def update_long_term_memory(
        self,
        memory_id: int,
        content: str,
        category: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> bool:
        cursor = self.sqlite_db.cursor()
        updates = ["content = ?", "updated_at = CURRENT_TIMESTAMP"]
        params: list = [content]
        if category is not None:
            updates.append("category = ?")
            params.append(category)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata, ensure_ascii=False))
        params.append(memory_id)
        cursor.execute(
            f"UPDATE long_term_memories SET {', '.join(updates)} WHERE id = ?",
            params
        )
        self.sqlite_db.commit()

        chroma_id = f"memory_{memory_id}"
        metadatas = [{"category": category or "", "memory_id": memory_id}]
        try:
            row = cursor.execute(
                "SELECT category FROM long_term_memories WHERE id = ?",
                (memory_id,)
            ).fetchone()
            stored_category = str(row[0]) if row and row[0] is not None else (category or "")
            metadatas = [{"category": stored_category, "memory_id": memory_id}]
            upsert = getattr(self.memory_collection, "upsert", None)
            if callable(upsert):
                upsert(documents=[content], metadatas=metadatas, ids=[chroma_id])
            else:
                try:
                    self.memory_collection.add(documents=[content], metadatas=metadatas, ids=[chroma_id])
                except Exception:
                    pass
        except Exception:
            pass
        return True

    async def upsert_keyed_long_term_memory(
        self,
        memory_key: str,
        content: str,
        category: str = "profile",
        metadata: Optional[Dict] = None
    ) -> int:
        metadata = dict(metadata or {})
        metadata["memory_key"] = memory_key
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "SELECT id, metadata FROM long_term_memories WHERE category = ? AND is_archived = 0 ORDER BY id DESC",
            (category,)
        )
        target_id: Optional[int] = None
        for row in cursor.fetchall():
            try:
                row_meta = json.loads(row["metadata"] or "{}")
            except Exception:
                row_meta = {}
            if row_meta.get("memory_key") == memory_key:
                target_id = int(row["id"])
                break

        if target_id is None:
            await self.save_long_term_memory(content=content, category=category, metadata=metadata)
            row = cursor.execute(
                "SELECT id FROM long_term_memories WHERE category = ? ORDER BY id DESC LIMIT 1",
                (category,)
            ).fetchone()
            return int(row[0]) if row else 0

        await self.update_long_term_memory(
            memory_id=target_id,
            content=content,
            category=category,
            metadata=metadata
        )
        return target_id

    async def get_keyed_long_term_memory(
        self,
        memory_key: str,
        category: str = "profile",
        include_archived: bool = False
    ) -> Optional[Dict]:
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "SELECT * FROM long_term_memories WHERE category = ? ORDER BY id DESC",
            (category,)
        )
        for row in cursor.fetchall():
            data = self._normalize_memory_row(row)
            if (not include_archived) and bool(data.get("is_archived")):
                continue
            meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
            if meta.get("memory_key") == memory_key:
                return data
        return None

    async def upsert_fact(self, key: str, value: str, metadata: Optional[Dict] = None) -> Dict:
        cursor = self.sqlite_db.cursor()
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        cursor.execute(
            """
            INSERT INTO facts (key, value, metadata, updated_at, is_archived)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP, 0)
            ON CONFLICT(key) DO UPDATE SET
              value = excluded.value,
              metadata = excluded.metadata,
              updated_at = CURRENT_TIMESTAMP,
              is_archived = 0
            """,
            (key, value, payload)
        )
        self.sqlite_db.commit()
        row = cursor.execute("SELECT * FROM facts WHERE key = ?", (key,)).fetchone()
        data = dict(row) if row else {"key": key, "value": value, "metadata": metadata or {}}
        if data.get("metadata"):
            try:
                data["metadata"] = json.loads(data["metadata"]) if isinstance(data["metadata"], str) else data["metadata"]
            except Exception:
                data["metadata"] = {}

        doc = f"{key}: {value}"
        chroma_id = f"fact_{key}"
        metadatas = [{"key": key, "fact_id": data.get("id")}]
        try:
            upsert = getattr(self.facts_collection, "upsert", None)
            if callable(upsert):
                upsert(documents=[doc], metadatas=metadatas, ids=[chroma_id])
            else:
                self.facts_collection.add(documents=[doc], metadatas=metadatas, ids=[chroma_id])
        except Exception:
            pass
        return data

    async def get_fact(self, key: str, include_archived: bool = False) -> Optional[Dict]:
        cursor = self.sqlite_db.cursor()
        row = cursor.execute("SELECT * FROM facts WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        data = dict(row)
        if (not include_archived) and bool(data.get("is_archived")):
            return None
        metadata = data.get("metadata")
        if metadata:
            try:
                data["metadata"] = json.loads(metadata)
            except Exception:
                data["metadata"] = {}
        else:
            data["metadata"] = {}
        return data

    async def get_storage_counts(self) -> Dict[str, int]:
        cursor = self.sqlite_db.cursor()
        counts: Dict[str, int] = {}
        for table in ("long_term_memories", "facts", "conversations", "messages"):
            try:
                counts[table] = int(cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except Exception:
                counts[table] = 0
        counts["active_long_term_memories"] = int(
            cursor.execute("SELECT COUNT(*) FROM long_term_memories WHERE is_archived = 0").fetchone()[0]
        )
        counts["archived_long_term_memories"] = int(
            cursor.execute("SELECT COUNT(*) FROM long_term_memories WHERE is_archived = 1").fetchone()[0]
        )
        counts["active_facts"] = int(
            cursor.execute("SELECT COUNT(*) FROM facts WHERE is_archived = 0").fetchone()[0]
        )
        return counts

    async def sync_facts_to_long_term(self) -> int:
        cursor = self.sqlite_db.cursor()
        rows = cursor.execute(
            "SELECT key, value, metadata, is_archived FROM facts WHERE is_archived = 0 ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        if not rows:
            return 0

        inserted = 0
        for row in rows:
            key = str(row["key"] or "").strip()
            value = str(row["value"] or "").strip()
            if not key or not value:
                continue
            metadata_raw = row["metadata"]
            metadata: Dict = {}
            if metadata_raw:
                try:
                    metadata = json.loads(metadata_raw)
                except Exception:
                    metadata = {}
            memory_key = str(metadata.get("memory_key") or key).strip()
            category = str(metadata.get("category") or "profile").strip() or "profile"
            existing = await self.get_keyed_long_term_memory(
                memory_key=memory_key,
                category=category,
                include_archived=False,
            )
            if existing:
                continue

            birthday_key = ""
            if memory_key == "birthday" or key == "birthday":
                birthday_key = "birthday"
            elif memory_key.endswith("_birthday"):
                birthday_key = memory_key
            elif key.endswith("_birthday"):
                birthday_key = key

            if birthday_key:
                if birthday_key == "birthday":
                    content = f"生日：{value}"
                else:
                    subject = birthday_key[:-9].strip().strip("_")
                    content = f"{subject}的生日：{value}" if subject else f"生日：{value}"
                category = "profile"
            else:
                continue

            await self.save_long_term_memory(
                content=content,
                category=category,
                metadata={
                    **metadata,
                    "memory_key": memory_key,
                    "fact_key": key,
                    "source": metadata.get("source") or "facts_sync",
                },
            )
            inserted += 1
        return inserted

    async def search_facts(
        self,
        query: str,
        top_k: int = None,
        include_archived: bool = False
    ) -> List[Dict]:
        if not query or not query.strip():
            return []
        try:
            results = self.facts_collection.query(
                query_texts=[query],
                n_results=top_k or min(5, self.config.vector_search_top_k)
            )
        except Exception:
            return []

        items: List[Dict] = []
        fact_ids: List[int] = []
        for doc, meta, dist in zip(
            results.get("documents", [[]])[0],
            results.get("metadatas", [[]])[0],
            results.get("distances", [[]])[0]
        ):
            if dist < self.config.similarity_threshold:
                fact_id = meta.get("fact_id") if isinstance(meta, dict) else None
                items.append(
                    {
                        "content": doc,
                        "key": meta.get("key") if isinstance(meta, dict) else None,
                        "fact_id": fact_id,
                        "distance": dist
                    }
                )
                if fact_id is not None:
                    fact_ids.append(int(fact_id))

        if include_archived or not fact_ids:
            return items
        cursor = self.sqlite_db.cursor()
        placeholders = ",".join(["?"] * len(fact_ids))
        rows = cursor.execute(
            f"SELECT id, is_archived FROM facts WHERE id IN ({placeholders})",
            fact_ids
        ).fetchall()
        archived = {int(r[0]): bool(r[1]) for r in rows}
        return [item for item in items if not archived.get(int(item["fact_id"]), False)]
    
    async def search_memories(
        self,
        query: str,
        top_k: int = None,
        category: Optional[str] = None,
        include_archived: bool = False
    ) -> List[Dict]:
        """搜索相关记忆"""
        include_archived = include_archived or self.config.show_archived_in_search
        where = None
        if category:
            where = {"category": category}
        
        results = self.memory_collection.query(
            query_texts=[query],
            n_results=top_k or self.config.vector_search_top_k,
            where=where
        )
        
        memories = []
        memory_ids = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0]
        ):
            if dist < self.config.similarity_threshold:
                memory_id = meta.get("memory_id")
                memories.append({
                    "content": doc,
                    "category": meta.get("category"),
                    "memory_id": memory_id,
                    "distance": dist
                })
                if memory_id is not None:
                    memory_ids.append(memory_id)
        
        if include_archived or not memory_ids:
            return memories
        archived_map = self._fetch_archived_map(memory_ids)
        return [
            item for item in memories
            if item["memory_id"] in archived_map and not archived_map.get(item["memory_id"], False)
        ]
    
    async def get_memories(
        self,
        category: Optional[str] = None,
        archived: bool = False,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """获取记忆列表"""
        cursor = self.sqlite_db.cursor()
        
        if category:
            cursor.execute(
                """SELECT * FROM long_term_memories 
                   WHERE category = ? AND is_archived = ?
                   ORDER BY updated_at DESC, created_at DESC 
                   LIMIT ? OFFSET ?""",
                (category, 1 if archived else 0, limit, offset)
            )
        else:
            cursor.execute(
                """SELECT * FROM long_term_memories 
                   WHERE is_archived = ?
                   ORDER BY updated_at DESC, created_at DESC 
                   LIMIT ? OFFSET ?""",
                (1 if archived else 0, limit, offset)
            )
        
        return [self._normalize_memory_row(row) for row in cursor.fetchall()]
    
    async def archive_memory(self, memory_id: int):
        """归档记忆"""
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "UPDATE long_term_memories SET is_archived = 1 WHERE id = ?",
            (memory_id,)
        )
        self.sqlite_db.commit()
        logger.info(f"归档记忆：{memory_id}")
    
    async def restore_memory(self, memory_id: int):
        """恢复记忆"""
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "UPDATE long_term_memories SET is_archived = 0 WHERE id = ?",
            (memory_id,)
        )
        self.sqlite_db.commit()
        logger.info(f"恢复记忆：{memory_id}")
    
    async def delete_memory(self, memory_id: int):
        """删除记忆"""
        chroma_id = f"memory_{memory_id}"
        try:
            self.memory_collection.delete(ids=[chroma_id])
        except Exception:
            pass
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "DELETE FROM long_term_memories WHERE id = ?",
            (memory_id,)
        )
        self.sqlite_db.commit()
        logger.info(f"删除记忆：{memory_id}")

    async def get_memory(self, memory_id: int) -> Optional[Dict]:
        cursor = self.sqlite_db.cursor()
        row = cursor.execute(
            "SELECT * FROM long_term_memories WHERE id = ?",
            (memory_id,)
        ).fetchone()
        if not row:
            return None
        return self._normalize_memory_row(row)

    async def archive_fact_by_key(self, key: str) -> bool:
        cursor = self.sqlite_db.cursor()
        cursor.execute(
            "UPDATE facts SET is_archived = 1, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
            (key,)
        )
        changed = cursor.rowcount > 0
        self.sqlite_db.commit()
        return changed

    def _repair_chroma_db(self, chroma_dir: Path) -> bool:
        """尝试修复因版本迁移导致的 ChromaDB schema 不兼容问题（补充被高版本删除的列）"""
        try:
            import sqlite3 as _sqlite3
            db_path = chroma_dir / "chroma.sqlite3"
            if not db_path.exists():
                return False
            conn = _sqlite3.connect(str(db_path))
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(collections)").fetchall()}
                if "topic" not in cols:
                    conn.execute("ALTER TABLE collections ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
                    logger.info("已修复 collections 表：补充 topic 列")
                seg_cols = {r[1] for r in conn.execute("PRAGMA table_info(segments)").fetchall()}
                if "topic" not in seg_cols:
                    conn.execute("ALTER TABLE segments ADD COLUMN topic TEXT")
                    logger.info("已修复 segments 表：补充 topic 列")
                conn.commit()
                return True
            finally:
                conn.close()
        except Exception as repair_error:
            logger.warning(f"修复 ChromaDB schema 失败：{repair_error}")
            return False

    def _init_chroma(self, chroma_dir: Path):
        try:
            client = chromadb.PersistentClient(path=str(chroma_dir))
            collection = client.get_or_create_collection(name="long_term_memory")
            return client, collection
        except Exception as e:
            logger.warning(f"ChromaDB 初始化失败：{e}")
            # 尝试修复 schema 不兼容（如高版本迁移后降级使用旧版本 ChromaDB）
            if "no such column" in str(e) and chroma_dir.exists():
                logger.info("检测到 schema 不兼容，尝试自动修复...")
                if self._repair_chroma_db(chroma_dir):
                    try:
                        client = chromadb.PersistentClient(path=str(chroma_dir))
                        collection = client.get_or_create_collection(name="long_term_memory")
                        logger.info("ChromaDB schema 修复成功，已正常初始化")
                        return client, collection
                    except Exception as retry_error:
                        logger.warning(f"修复后重试仍失败：{retry_error}")
                # 修复失败，尝试备份后使用 fallback
                backup_dir = chroma_dir.parent / f"chroma_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                try:
                    shutil.move(str(chroma_dir), str(backup_dir))
                    logger.warning(f"已备份旧 Chroma 数据：{backup_dir}")
                except Exception as move_error:
                    logger.warning(f"备份旧 Chroma 数据失败（文件可能被占用），将直接使用备用目录：{move_error}")
            fallback_dir = chroma_dir.parent / "chroma_v2"
            client = chromadb.PersistentClient(path=str(fallback_dir))
            collection = client.get_or_create_collection(name="long_term_memory")
            return client, collection

    def _fetch_archived_map(self, memory_ids: List[int]) -> Dict[int, bool]:
        cursor = self.sqlite_db.cursor()
        placeholders = ",".join(["?"] * len(memory_ids))
        cursor.execute(
            f"SELECT id, is_archived FROM long_term_memories WHERE id IN ({placeholders})",
            memory_ids
        )
        rows = cursor.fetchall()
        return {row[0]: bool(row[1]) for row in rows}

    def _normalize_memory_row(self, row: sqlite3.Row) -> Dict:
        data = dict(row)
        metadata = data.get("metadata")
        if metadata:
            try:
                data["metadata"] = json.loads(metadata)
            except Exception:
                data["metadata"] = {}
        else:
            data["metadata"] = {}
        return data
    
    async def cleanup_old_memories(self, cleanup_archived_memories: bool = True):
        """清理旧记忆

        Args:
            cleanup_archived_memories: 是否同时删除超期的已归档长期记忆（默认 True）。
                活跃（未归档）长期记忆永远不会被自动删除。
        """
        if not self.config.auto_cleanup_enabled:
            return 0

        if self.config.cleanup_days == 0:  # 永久保留
            return 0

        cutoff_date = datetime.now() - timedelta(days=self.config.cleanup_days)

        cursor = self.sqlite_db.cursor()

        # 1. 删除旧消息
        cursor.execute(
            "DELETE FROM messages WHERE created_at < ?",
            (cutoff_date.isoformat(),)
        )
        messages_deleted = cursor.rowcount

        # 2. 归档或删除旧对话
        if self.config.archive_enabled and self.config.archive_days > 0:
            archive_date = datetime.now() - timedelta(days=self.config.archive_days)
            cursor.execute(
                """UPDATE conversations SET is_archived = 1
                   WHERE created_at < ? AND is_archived = 0""",
                (archive_date.isoformat(),)
            )
        elif self.config.cleanup_days > 0:
            cursor.execute(
                "DELETE FROM conversations WHERE created_at < ?",
                (cutoff_date.isoformat(),)
            )

        # 3. 删除已归档且超期的长期记忆（活跃记忆不受影响）
        memories_deleted = 0
        if cleanup_archived_memories:
            cursor.execute(
                """DELETE FROM long_term_memories
                   WHERE is_archived = 1
                   AND COALESCE(updated_at, created_at) < ?""",
                (cutoff_date.isoformat(),)
            )
            memories_deleted = cursor.rowcount

            # 同步删除 ChromaDB 中对应的向量（尽力删除，失败不影响主流程）
            if memories_deleted > 0:
                try:
                    all_ids = [
                        item["id"]
                        for item in self.memory_collection.get().get("ids", [])
                    ]
                    # memory_id 已不在 SQLite，找出孤立的 chroma 文档
                    cursor2 = self.sqlite_db.cursor()
                    existing_ids = {
                        row[0]
                        for row in cursor2.execute(
                            "SELECT id FROM long_term_memories"
                        ).fetchall()
                    }
                    orphan_chroma_ids = [
                        cid for cid in all_ids
                        if cid.startswith("memory_")
                        and int(cid.split("_", 1)[1]) not in existing_ids
                    ]
                    if orphan_chroma_ids:
                        self.memory_collection.delete(ids=orphan_chroma_ids)
                except Exception as chroma_err:
                    logger.warning(f"清理 ChromaDB 孤立向量时出错（可忽略）：{chroma_err}")

        self.sqlite_db.commit()

        logger.info(
            f"清理完成：删除 {messages_deleted} 条消息，"
            f"删除 {memories_deleted} 条已归档长期记忆"
        )
        return messages_deleted + memories_deleted
    
    async def export_memories(self, output_path: str = None) -> str:
        """导出记忆到 ZIP 文件"""
        import zipfile
        import shutil
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = Path(self.data_dir) / f"export_{timestamp}"
        export_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            # 复制数据库
            shutil.copy(
                str(Path(self.data_dir) / "conversations.db"),
                str(export_dir / "conversations.db")
            )
            
            # 复制 ChromaDB 数据
            chroma_src = Path(self.data_dir) / "chroma"
            chroma_dst = export_dir / "chroma"
            if chroma_src.exists():
                shutil.copytree(chroma_src, chroma_dst)
            
            # 创建元数据
            metadata = {
                "export_date": datetime.now().isoformat(),
                "version": "2.0.0"
            }
            with open(export_dir / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            
            # 打包成 ZIP
            zip_path = output_path or str(
                Path(self.data_dir) / "backups" / f"memory_backup_{timestamp}.zip"
            )
            Path(zip_path).parent.mkdir(parents=True, exist_ok=True)
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in export_dir.rglob('*'):
                    if file_path.is_file():
                        arcname = file_path.relative_to(export_dir)
                        zipf.write(file_path, arcname)
            
            logger.info(f"导出记忆完成：{zip_path}")
            return zip_path
            
        finally:
            # 清理临时目录
            if export_dir.exists():
                shutil.rmtree(export_dir)
    
    async def import_memories(self, zip_path: str, restore: bool = True) -> bool:
        """从 ZIP 文件导入记忆"""
        import zipfile
        import shutil
        import tempfile
        
        extract_dir = Path(tempfile.mkdtemp())
        
        try:
            # 解压
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                zipf.extractall(extract_dir)
            
            if restore:
                # 备份当前数据
                backup_path = await self.export_memories()
                logger.info(f"已创建备份：{backup_path}")
                
                # 恢复数据
                shutil.copy(
                    str(extract_dir / "conversations.db"),
                    str(Path(self.data_dir) / "conversations.db")
                )
                
                # 恢复 ChromaDB
                chroma_src = extract_dir / "chroma"
                chroma_dst = Path(self.data_dir) / "chroma"
                if chroma_src.exists():
                    if chroma_dst.exists():
                        shutil.rmtree(chroma_dst)
                    shutil.copytree(chroma_src, chroma_dst)
                
                # 重新初始化连接
                self.sqlite_db.close()
                self.sqlite_db = sqlite3.connect(
                    str(Path(self.data_dir) / "conversations.db")
                )
                self.sqlite_db.row_factory = sqlite3.Row
                
                self.chroma_client = chromadb.PersistentClient(
                    path=str(Path(self.data_dir) / "chroma")
                )
                self.memory_collection = self.chroma_client.get_or_create_collection(
                    name="long_term_memory"
                )
                
                logger.info("记忆恢复完成")
                return True
            
            return False
            
        finally:
            # 清理临时目录
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
    
    def close(self):
        """关闭连接"""
        if self.sqlite_db:
            self.sqlite_db.close()
        logger.info("记忆管理器已关闭")
