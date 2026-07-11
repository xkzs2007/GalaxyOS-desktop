#!/usr/bin/env python3
"""
记忆银行 (Memory Bank)

无限容量的记忆存储系统：
- 向量化存储
- 语义检索
- 记忆衰减
- 记忆巩固

参考 MemGPT 论文中的 Archival Memory 设计。

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import json
import sqlite3
import hashlib
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
import threading
import re
from galaxyos.shared.paths import workspace


class MemoryBankError(Exception):
    """记忆银行异常"""
    pass


@dataclass
class BankMemory:
    """记忆银行的记忆单元"""
    id: str
    content: str
    embedding: List[float] = field(default_factory=list)
    importance: float = 0.5
    emotional_weight: float = 0.0
    created_at: str = ""
    last_accessed: str = ""
    access_count: int = 0
    decay_factor: float = 1.0
    consolidated: bool = False
    source: str = "unknown"
    metadata: Dict = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.last_accessed:
            self.last_accessed = self.created_at
    
    def get_age_days(self) -> float:
        """获取记忆年龄（天）"""
        created = datetime.fromisoformat(self.created_at.replace('Z', '+00:00'))
        age = datetime.now(timezone.utc) - created
        return age.total_seconds() / 86400
    
    def get_retention_score(self) -> float:
        """
        计算保留分数
        
        综合考虑：
        - 重要性
        - 情感权重
        - 访问频率
        - 衰减因子
        - 年龄
        """
        # 基础分数
        base_score = self.importance * 0.4 + self.emotional_weight * 0.2
        
        # 访问频率加成
        access_bonus = min(0.2, self.access_count * 0.02)
        
        # 时间衰减
        age_days = self.get_age_days()
        time_decay = np.exp(-age_days / 365)  # 一年后衰减到 37%
        
        # 综合分数
        score = (base_score + access_bonus) * self.decay_factor * time_decay
        
        return min(1.0, max(0.0, score))
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "id": self.id,
            "content": self.content,
            "importance": self.importance,
            "emotional_weight": self.emotional_weight,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "decay_factor": self.decay_factor,
            "consolidated": self.consolidated,
            "source": self.source,
            "metadata": self.metadata,
            "tags": self.tags
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'BankMemory':
        """从字典创建"""
        return cls(
            id=data["id"],
            content=data["content"],
            importance=data.get("importance", 0.5),
            emotional_weight=data.get("emotional_weight", 0.0),
            created_at=data.get("created_at", ""),
            last_accessed=data.get("last_accessed", ""),
            access_count=data.get("access_count", 0),
            decay_factor=data.get("decay_factor", 1.0),
            consolidated=data.get("consolidated", False),
            source=data.get("source", "unknown"),
            metadata=data.get("metadata", {}),
            tags=data.get("tags", [])
        )


class MemoryBank:
    """
    记忆银行
    
    无限容量的记忆存储系统，支持：
    - 向量化存储和检索
    - 记忆衰减和巩固
    - 批量操作
    - 记忆统计和分析
    """
    
    EMBEDDING_DIM = 128  # 嵌入维度
    
    def __init__(self, db_path: str = None):
        """
        初始化记忆银行
        
        Args:
            db_path: 数据库路径
        """
        self.db_path = Path(db_path or 
            Path(workspace()) / ".memgpt" / "memory_bank.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.lock = threading.RLock()
        self._embedding_cache: Dict[str, List[float]] = {}
        
        self._init_db()
    
    def _init_db(self):
        """初始化数据库"""
        with sqlite3.connect(self.db_path) as conn:
            # 主记忆表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    importance REAL DEFAULT 0.5,
                    emotional_weight REAL DEFAULT 0.0,
                    created_at TEXT,
                    last_accessed TEXT,
                    access_count INTEGER DEFAULT 0,
                    decay_factor REAL DEFAULT 1.0,
                    consolidated INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'unknown',
                    metadata TEXT,
                    tags TEXT
                )
            """)
            
            # 向量索引表（简化版，实际应用中应使用专门的向量数据库）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    memory_id TEXT PRIMARY KEY,
                    embedding BLOB,
                    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
                )
            """)
            
            # 索引
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_retention ON memories(
                    importance * decay_factor
                )
            """)
            
            conn.commit()
    
    # ==================== 嵌入函数 ====================
    
    def _compute_embedding(self, text: str) -> List[float]:
        """
        计算文本嵌入
        
        注意：这是简化版的嵌入函数，实际应用中应使用：
        - OpenAI embeddings
        - Sentence Transformers
        - 其他预训练模型
        """
        # 使用哈希生成伪向量（仅用于演示）
        words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+|\d+', text.lower())
        
        embedding = np.zeros(self.EMBEDDING_DIM)
        
        for word in words:
            # 使用单词哈希作为索引
            hash_val = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
            idx = hash_val % self.EMBEDDING_DIM
            
            # TF-IDF 风格的权重
            embedding[idx] += 1.0 / (1 + words.count(word))
        
        # 归一化
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        
        return embedding.tolist()
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        a = np.array(vec1)
        b = np.array(vec2)
        
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return dot / (norm_a * norm_b)
    
    def _embedding_to_blob(self, embedding: List[float]) -> bytes:
        """嵌入向量转二进制"""
        return np.array(embedding, dtype=np.float32).tobytes()
    
    def _blob_to_embedding(self, blob: bytes) -> List[float]:
        """二进制转嵌入向量"""
        arr = np.frombuffer(blob, dtype=np.float32)
        return arr.tolist()
    
    # ==================== CRUD 操作 ====================
    
    def store(
        self,
        content: str,
        importance: float = 0.5,
        emotional_weight: float = 0.0,
        source: str = "unknown",
        metadata: Dict = None,
        tags: List[str] = None
    ) -> str:
        """
        存储记忆
        
        Args:
            content: 记忆内容
            importance: 重要性 (0.0-1.0)
            emotional_weight: 情感权重 (0.0-1.0)
            source: 来源
            metadata: 元数据
            tags: 标签
        
        Returns:
            memory_id: 记忆 ID
        """
        with self.lock:
            # 生成 ID
            memory_id = f"bank_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.md5(content.encode()).hexdigest()[:8]}"
            
            now = datetime.now(timezone.utc).isoformat()
            
            # 计算嵌入
            embedding = self._compute_embedding(content)
            
            # 存储到数据库
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO memories (
                        id, content, importance, emotional_weight,
                        created_at, last_accessed, access_count,
                        decay_factor, consolidated, source, metadata, tags
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 1.0, 0, ?, ?, ?)
                """, (
                    memory_id,
                    content,
                    importance,
                    emotional_weight,
                    now,
                    now,
                    source,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    json.dumps(tags or [], ensure_ascii=False)
                ))
                
                # 存储嵌入
                conn.execute("""
                    INSERT INTO embeddings (memory_id, embedding)
                    VALUES (?, ?)
                """, (memory_id, self._embedding_to_blob(embedding)))
                
                conn.commit()
            
            # 缓存嵌入
            self._embedding_cache[memory_id] = embedding
            
            return memory_id
    
    def retrieve(self, memory_id: str) -> Optional[BankMemory]:
        """
        检索单条记忆
        
        Args:
            memory_id: 记忆 ID
        
        Returns:
            BankMemory 或 None
        """
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM memories WHERE id = ?
                """, (memory_id,))
                
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                memory = BankMemory(
                    id=row["id"],
                    content=row["content"],
                    importance=row["importance"],
                    emotional_weight=row["emotional_weight"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    decay_factor=row["decay_factor"],
                    consolidated=bool(row["consolidated"]),
                    source=row["source"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    tags=json.loads(row["tags"]) if row["tags"] else []
                )
                
                # 更新访问信息
                self._touch(memory_id)
                
                return memory
    
    def search(
        self,
        query: str,
        top_k: int = 10,
        min_importance: float = 0.0,
        min_retention: float = 0.0,
        tags: List[str] = None,
        source: str = None
    ) -> List[Tuple[BankMemory, float]]:
        """
        语义搜索
        
        Args:
            query: 查询文本
            top_k: 返回数量
            min_importance: 最小重要性
            min_retention: 最小保留分数
            tags: 标签过滤
            source: 来源过滤
        
        Returns:
            [(BankMemory, similarity), ...]
        """
        with self.lock:
            # 计算查询嵌入
            query_embedding = self._compute_embedding(query)
            
            # 加载所有候选记忆
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                
                # 构建查询
                sql = "SELECT * FROM memories WHERE importance >= ?"
                params = [min_importance]
                
                if source:
                    sql += " AND source = ?"
                    params.append(source)
                
                cursor = conn.execute(sql, params)
                rows = cursor.fetchall()
            
            # 计算相似度
            results = []
            
            for row in rows:
                memory = BankMemory(
                    id=row["id"],
                    content=row["content"],
                    importance=row["importance"],
                    emotional_weight=row["emotional_weight"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    decay_factor=row["decay_factor"],
                    consolidated=bool(row["consolidated"]),
                    source=row["source"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    tags=json.loads(row["tags"]) if row["tags"] else []
                )
                
                # 标签过滤
                if tags:
                    if not any(tag in memory.tags for tag in tags):
                        continue
                
                # 保留分数过滤
                retention = memory.get_retention_score()
                if retention < min_retention:
                    continue
                
                # 获取嵌入
                if memory.id in self._embedding_cache:
                    embedding = self._embedding_cache[memory.id]
                else:
                    # 从数据库加载
                    with sqlite3.connect(self.db_path) as conn:
                        cursor = conn.execute(
                            "SELECT embedding FROM embeddings WHERE memory_id = ?",
                            (memory.id,)
                        )
                        emb_row = cursor.fetchone()
                        if emb_row:
                            embedding = self._blob_to_embedding(emb_row[0])
                            self._embedding_cache[memory.id] = embedding
                        else:
                            embedding = self._compute_embedding(memory.content)
                
                # 计算相似度
                similarity = self._cosine_similarity(query_embedding, embedding)
                
                # 综合分数 = 相似度 * 保留分数
                combined_score = similarity * (0.5 + 0.5 * retention)
                
                if combined_score > 0.05:  # 阈值
                    results.append((memory, combined_score))
            
            # 排序
            results.sort(key=lambda x: x[1], reverse=True)
            
            # 更新访问信息
            for memory, _ in results[:top_k]:
                self._touch(memory.id)
            
            return results[:top_k]
    
    def update(
        self,
        memory_id: str,
        content: str = None,
        importance: float = None,
        emotional_weight: float = None,
        metadata: Dict = None,
        tags: List[str] = None
    ) -> bool:
        """更新记忆"""
        with self.lock:
            # 检查是否存在
            memory = self.retrieve(memory_id)
            if not memory:
                return False
            
            # 构建更新
            updates = []
            params = []
            
            if content is not None:
                updates.append("content = ?")
                params.append(content)
                # 重新计算嵌入
                embedding = self._compute_embedding(content)
                self._embedding_cache[memory_id] = embedding
            
            if importance is not None:
                updates.append("importance = ?")
                params.append(importance)
            
            if emotional_weight is not None:
                updates.append("emotional_weight = ?")
                params.append(emotional_weight)
            
            if metadata is not None:
                updates.append("metadata = ?")
                params.append(json.dumps(metadata, ensure_ascii=False))
            
            if tags is not None:
                updates.append("tags = ?")
                params.append(json.dumps(tags, ensure_ascii=False))
            
            if not updates:
                return True
            
            params.append(memory_id)
            
            with sqlite3.connect(self.db_path) as conn:
                sql = f"UPDATE memories SET {', '.join(updates)} WHERE id = ?"
                conn.execute(sql, params)
                
                # 更新嵌入
                if content is not None:
                    conn.execute("""
                        UPDATE embeddings SET embedding = ?
                        WHERE memory_id = ?
                    """, (self._embedding_to_blob(embedding), memory_id))
                
                conn.commit()
            
            return True
    
    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                conn.commit()
                
                if memory_id in self._embedding_cache:
                    del self._embedding_cache[memory_id]
                
                return cursor.rowcount > 0
    
    def _touch(self, memory_id: str):
        """更新访问信息"""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE memories 
                SET last_accessed = ?, access_count = access_count + 1
                WHERE id = ?
            """, (now, memory_id))
            conn.commit()
    
    # ==================== 批量操作 ====================
    
    def batch_store(self, memories: List[Dict]) -> List[str]:
        """
        批量存储
        
        Args:
            memories: [{"content": ..., "importance": ..., ...}, ...]
        
        Returns:
            [memory_id, ...]
        """
        ids = []
        for m in memories:
            memory_id = self.store(
                content=m["content"],
                importance=m.get("importance", 0.5),
                emotional_weight=m.get("emotional_weight", 0.0),
                source=m.get("source", "batch"),
                metadata=m.get("metadata"),
                tags=m.get("tags")
            )
            ids.append(memory_id)
        return ids
    
    def batch_delete(self, memory_ids: List[str]) -> int:
        """批量删除"""
        count = 0
        for mid in memory_ids:
            if self.delete(mid):
                count += 1
        return count
    
    # ==================== 记忆衰减与巩固 ====================
    
    def apply_decay(self, decay_rate: float = 0.99):
        """
        应用记忆衰减
        
        Args:
            decay_rate: 衰减率 (0.99 表示每次衰减 1%)
        """
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    UPDATE memories 
                    SET decay_factor = decay_factor * ?
                    WHERE consolidated = 0
                """, (decay_rate,))
                conn.commit()
    
    def consolidate(self, threshold: float = 0.7):
        """
        记忆巩固
        
        将高保留分数的记忆标记为巩固
        
        Args:
            threshold: 巩固阈值
        """
        with self.lock:
            # 获取所有未巩固的记忆
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM memories WHERE consolidated = 0
                """)
                rows = cursor.fetchall()
            
            for row in rows:
                memory = BankMemory(
                    id=row["id"],
                    content=row["content"],
                    importance=row["importance"],
                    emotional_weight=row["emotional_weight"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    decay_factor=row["decay_factor"],
                    consolidated=False,
                    source=row["source"],
                    metadata={},
                    tags=[]
                )
                
                if memory.get_retention_score() >= threshold:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.execute("""
                            UPDATE memories SET consolidated = 1 WHERE id = ?
                        """, (memory.id,))
                        conn.commit()
    
    def cleanup(self, min_retention: float = 0.1, max_age_days: int = 365):
        """
        清理低价值记忆
        
        Args:
            min_retention: 最小保留分数
            max_age_days: 最大年龄（天）
        
        Returns:
            删除的记忆数量
        """
        with self.lock:
            # 获取所有记忆
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM memories")
                rows = cursor.fetchall()
            
            to_delete = []
            
            for row in rows:
                memory = BankMemory(
                    id=row["id"],
                    content=row["content"],
                    importance=row["importance"],
                    emotional_weight=row["emotional_weight"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    decay_factor=row["decay_factor"],
                    consolidated=bool(row["consolidated"]),
                    source=row["source"],
                    metadata={},
                    tags=[]
                )
                
                # 巩固的记忆不删除
                if memory.consolidated:
                    continue
                
                # 检查保留分数
                if memory.get_retention_score() < min_retention:
                    to_delete.append(memory.id)
                    continue
                
                # 检查年龄
                if memory.get_age_days() > max_age_days and memory.access_count == 0:
                    to_delete.append(memory.id)
            
            # 批量删除
            return self.batch_delete(to_delete)
    
    # ==================== 统计与分析 ====================
    
    def stats(self) -> Dict:
        """获取统计信息"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                # 总数
                cursor = conn.execute("SELECT COUNT(*) FROM memories")
                total = cursor.fetchone()[0]
                
                # 平均重要性
                cursor = conn.execute("SELECT AVG(importance) FROM memories")
                avg_importance = cursor.fetchone()[0] or 0
                
                # 巩固数量
                cursor = conn.execute("SELECT COUNT(*) FROM memories WHERE consolidated = 1")
                consolidated = cursor.fetchone()[0]
                
                # 按来源统计
                cursor = conn.execute("""
                    SELECT source, COUNT(*) as cnt 
                    FROM memories 
                    GROUP BY source
                """)
                by_source = {row[0]: row[1] for row in cursor.fetchall()}
                
                # 最近访问
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM memories 
                    WHERE last_accessed > datetime('now', '-7 days')
                """)
                recent_accessed = cursor.fetchone()[0]
                
                return {
                    "total_memories": total,
                    "consolidated_memories": consolidated,
                    "avg_importance": avg_importance,
                    "by_source": by_source,
                    "recent_accessed": recent_accessed,
                    "cache_size": len(self._embedding_cache)
                }
    
    def get_memories_by_importance(self, limit: int = 100) -> List[BankMemory]:
        """按重要性获取记忆"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM memories 
                    ORDER BY importance DESC 
                    LIMIT ?
                """, (limit,))
                
                rows = cursor.fetchall()
                
                return [BankMemory(
                    id=row["id"],
                    content=row["content"],
                    importance=row["importance"],
                    emotional_weight=row["emotional_weight"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    decay_factor=row["decay_factor"],
                    consolidated=bool(row["consolidated"]),
                    source=row["source"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    tags=json.loads(row["tags"]) if row["tags"] else []
                ) for row in rows]
    
    def get_memories_by_age(self, max_age_days: int = 30) -> List[BankMemory]:
        """获取指定天数内的记忆"""
        with self.lock:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM memories 
                    WHERE created_at > ?
                    ORDER BY created_at DESC
                """, (cutoff,))
                
                rows = cursor.fetchall()
                
                return [BankMemory(
                    id=row["id"],
                    content=row["content"],
                    importance=row["importance"],
                    emotional_weight=row["emotional_weight"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    decay_factor=row["decay_factor"],
                    consolidated=bool(row["consolidated"]),
                    source=row["source"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    tags=json.loads(row["tags"]) if row["tags"] else []
                ) for row in rows]


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="记忆银行")
    parser.add_argument("command", choices=[
        "store", "retrieve", "search", "delete", "stats", "decay", "cleanup"
    ])
    parser.add_argument("--content", help="记忆内容")
    parser.add_argument("--id", help="记忆 ID")
    parser.add_argument("--query", help="搜索查询")
    parser.add_argument("--importance", type=float, default=0.5, help="重要性")
    parser.add_argument("--top-k", type=int, default=10, help="返回数量")
    parser.add_argument("--tags", nargs="+", help="标签")
    
    args = parser.parse_args()
    
    bank = MemoryBank()
    
    if args.command == "store":
        if not args.content:
            print("错误: 需要提供 --content")
            return
        memory_id = bank.store(
            args.content,
            importance=args.importance,
            tags=args.tags
        )
        print(f"✅ 已存储: {memory_id}")
    
    elif args.command == "retrieve":
        if not args.id:
            print("错误: 需要提供 --id")
            return
        memory = bank.retrieve(args.id)
        if memory:
            print(f"内容: {memory.content}")
            print(f"重要性: {memory.importance}")
            print(f"保留分数: {memory.get_retention_score():.3f}")
        else:
            print("❌ 未找到")
    
    elif args.command == "search":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        results = bank.search(args.query, top_k=args.top_k, tags=args.tags)
        print(f"找到 {len(results)} 条记忆:")
        for memory, score in results:
            print(f"  [{score:.3f}] {memory.content[:50]}...")
    
    elif args.command == "delete":
        if not args.id:
            print("错误: 需要提供 --id")
            return
        success = bank.delete(args.id)
        print(f"{'✅ 已删除' if success else '❌ 未找到'}")
    
    elif args.command == "stats":
        stats = bank.stats()
        import json
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    
    elif args.command == "decay":
        bank.apply_decay()
        print("✅ 已应用记忆衰减")
    
    elif args.command == "cleanup":
        deleted = bank.cleanup()
        print(f"✅ 已清理 {deleted} 条低价值记忆")


if __name__ == "__main__":
    main()
