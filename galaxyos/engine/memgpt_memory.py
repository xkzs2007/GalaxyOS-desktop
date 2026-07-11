#!/usr/bin/env python3
"""
MemGPT 风格记忆管理器

参考论文：MemGPT: Towards LLMs as Operating Systems (2023)
论文地址：https://arxiv.org/abs/2310.08560

实现两级内存架构：
- Core Memory: 始终在上下文中的关键信息（有限容量）
- Working Memory: 当前对话上下文（自动压缩）
- Archival Memory: 历史记忆（无限容量，向量检索）

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import json
import hashlib
import sqlite3
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
import threading
import re
from galaxyos.shared.paths import workspace


class MemoryType(Enum):
    """记忆类型"""
    CORE = "core"           # 核心记忆 - 始终在上下文中
    WORKING = "working"     # 工作记忆 - 当前对话
    ARCHIVAL = "archival"   # 归档记忆 - 历史存储


class MemoryPriority(Enum):
    """记忆优先级"""
    CRITICAL = 1.0    # 关键信息（用户偏好、身份信息）
    HIGH = 0.8        # 高优先级（重要决策、关键事件）
    MEDIUM = 0.5      # 中等优先级（一般信息）
    LOW = 0.3         # 低优先级（临时信息） 


@dataclass
class Memory:
    """记忆单元"""
    id: str
    content: str
    memory_type: MemoryType
    priority: MemoryPriority
    importance: float = 0.5
    created_at: str = ""
    last_accessed: str = ""
    access_count: int = 0
    embedding: Optional[List[float]] = None
    metadata: Dict = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.last_accessed:
            self.last_accessed = self.created_at
    
    def touch(self):
        """更新访问时间和计数"""
        self.last_accessed = datetime.now(timezone.utc).isoformat()
        self.access_count += 1
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "priority": self.priority.value,
            "importance": self.importance,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "metadata": self.metadata,
            "tags": self.tags
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Memory':
        """从字典创建"""
        return cls(
            id=data["id"],
            content=data["content"],
            memory_type=MemoryType(data["memory_type"]),
            priority=MemoryPriority(data["priority"]),
            importance=data.get("importance", 0.5),
            created_at=data.get("created_at", ""),
            last_accessed=data.get("last_accessed", ""),
            access_count=data.get("access_count", 0),
            metadata=data.get("metadata", {}),
            tags=data.get("tags", [])
        )


class CoreMemory:
    """
    核心记忆 - 始终在上下文中的关键信息
    
    特点：
    - 容量有限（默认 2000 tokens）
    - 存储用户偏好、身份信息、关键决策
    - 自动维护，超出容量时压缩或降级
    """
    
    DEFAULT_CAPACITY = 2000  # tokens
    MAX_ITEMS = 50
    
    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self.capacity = capacity
        self.memories: List[Memory] = []
        self.lock = threading.Lock()
        self._token_counter = 0
    
    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数量（简单估算：中文约 1.5 字/token，英文约 4 字符/token）"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars / 1.5 + other_chars / 4) + 1
    
    def _current_tokens(self) -> int:
        """计算当前总 token 数"""
        return sum(self._estimate_tokens(m.content) for m in self.memories)
    
    def add(self, content: str, priority: MemoryPriority = MemoryPriority.HIGH, 
            metadata: Dict = None, tags: List[str] = None) -> str:
        """添加核心记忆"""
        with self.lock:
            # 生成 ID
            memory_id = f"core_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.md5(content.encode()).hexdigest()[:8]}"
            
            memory = Memory(
                id=memory_id,
                content=content,
                memory_type=MemoryType.CORE,
                priority=priority,
                importance=priority.value,
                metadata=metadata or {},
                tags=tags or []
            )
            
            # 检查容量
            new_tokens = self._estimate_tokens(content)
            while self._current_tokens() + new_tokens > self.capacity and len(self.memories) > 0:
                self._evict_lowest_priority()
            
            if len(self.memories) >= self.MAX_ITEMS:
                self._evict_lowest_priority()
            
            self.memories.append(memory)
            self._sort_by_priority()
            
            return memory_id
    
    def _evict_lowest_priority(self) -> Optional[Memory]:
        """驱逐最低优先级的记忆"""
        if not self.memories:
            return None
        
        # 找到最低优先级且最少访问的记忆
        self.memories.sort(key=lambda m: (m.priority.value, -m.access_count))
        evicted = self.memories.pop(0)
        self._sort_by_priority()
        return evicted
    
    def _sort_by_priority(self):
        """按优先级排序"""
        self.memories.sort(key=lambda m: (-m.priority.value, -m.importance))
    
    def get_all(self) -> List[Memory]:
        """获取所有核心记忆"""
        with self.lock:
            for m in self.memories:
                m.touch()
            return list(self.memories)
    
    def get_context_string(self) -> str:
        """获取上下文字符串（用于注入到 prompt）"""
        memories = self.get_all()
        if not memories:
            return ""
        
        lines = ["## 核心记忆（始终记住）"]
        for m in memories:
            lines.append(f"- [{m.priority.name}] {m.content}")
        return "\n".join(lines)
    
    def search(self, query: str) -> List[Memory]:
        """搜索核心记忆"""
        query_lower = query.lower()
        results = []
        with self.lock:
            for m in self.memories:
                if query_lower in m.content.lower():
                    m.touch()
                    results.append(m)
        return results
    
    def remove(self, memory_id: str) -> bool:
        """删除记忆"""
        with self.lock:
            for i, m in enumerate(self.memories):
                if m.id == memory_id:
                    self.memories.pop(i)
                    return True
        return False
    
    def update(self, memory_id: str, content: str) -> bool:
        """更新记忆内容"""
        with self.lock:
            for m in self.memories:
                if m.id == memory_id:
                    m.content = content
                    m.last_accessed = datetime.now(timezone.utc).isoformat()
                    return True
        return False
    
    def stats(self) -> Dict:
        """获取统计信息"""
        with self.lock:
            return {
                "count": len(self.memories),
                "tokens": self._current_tokens(),
                "capacity": self.capacity,
                "utilization": self._current_tokens() / self.capacity,
                "by_priority": {
                    p.name: len([m for m in self.memories if m.priority == p])
                    for p in MemoryPriority
                }
            }


class WorkingMemory:
    """
    工作记忆 - 当前对话上下文
    
    特点：
    - 存储当前对话的消息
    - 自动压缩超出容量的内容
    - 支持滑动窗口和摘要压缩
    """
    
    DEFAULT_CAPACITY = 4000  # tokens
    COMPRESS_THRESHOLD = 0.8  # 达到 80% 容量时触发压缩
    
    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self.capacity = capacity
        self.messages: List[Dict] = []  # [{"role": "user/assistant", "content": "..."}]
        self.summary: str = ""  # 压缩后的摘要
        self.lock = threading.Lock()
    
    def _estimate_tokens(self, text: str) -> int:
        """估算 token 数量"""
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        other_chars = len(text) - chinese_chars
        return int(chinese_chars / 1.5 + other_chars / 4) + 1
    
    def _current_tokens(self) -> int:
        """计算当前总 token 数"""
        msg_tokens = sum(self._estimate_tokens(m["content"]) for m in self.messages)
        summary_tokens = self._estimate_tokens(self.summary) if self.summary else 0
        return msg_tokens + summary_tokens
    
    def add_message(self, role: str, content: str) -> None:
        """添加消息"""
        with self.lock:
            self.messages.append({
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
            
            # 检查是否需要压缩
            if self._current_tokens() > self.capacity * self.COMPRESS_THRESHOLD:
                self._compress()
    
    def _compress(self) -> None:
        """压缩工作记忆（保留最近消息，旧消息转为摘要）"""
        if len(self.messages) <= 2:
            return
        
        # 保留最近的消息
        keep_count = max(2, len(self.messages) // 4)
        to_compress = self.messages[:-keep_count]
        self.messages = self.messages[-keep_count:]
        
        # 生成摘要（简单版本：提取关键信息）
        compressed_summary = self._generate_summary(to_compress)
        
        # 合并到现有摘要
        if self.summary:
            self.summary = f"{self.summary}\n\n[新增摘要]\n{compressed_summary}"
        else:
            self.summary = compressed_summary
    
    def _generate_summary(self, messages: List[Dict]) -> str:
        """生成消息摘要"""
        if not messages:
            return ""
        
        # 简单摘要：提取用户问题和关键决策
        user_msgs = [m["content"] for m in messages if m["role"] == "user"]
        assistant_msgs = [m["content"] for m in messages if m["role"] == "assistant"]
        
        summary_parts = []
        if user_msgs:
            summary_parts.append(f"用户询问了 {len(user_msgs)} 个问题")
        if assistant_msgs:
            summary_parts.append(f"助手提供了 {len(assistant_msgs)} 个回答")
        
        # 提取关键实体（简单版本）
        all_content = " ".join([m["content"] for m in messages])
        # 这里可以接入 NER 或关键词提取
        
        return "。".join(summary_parts) if summary_parts else "对话已压缩"
    
    def get_context(self, include_summary: bool = True) -> str:
        """获取上下文"""
        with self.lock:
            parts = []
            
            if include_summary and self.summary:
                parts.append(f"## 对话摘要\n{self.summary}")
            
            if self.messages:
                parts.append("## 最近对话")
                for m in self.messages:
                    role_name = "用户" if m["role"] == "user" else "助手"
                    parts.append(f"{role_name}: {m['content']}")
            
            return "\n\n".join(parts)
    
    def clear(self) -> None:
        """清空工作记忆"""
        with self.lock:
            self.messages = []
            self.summary = ""
    
    def stats(self) -> Dict:
        """获取统计信息"""
        with self.lock:
            return {
                "message_count": len(self.messages),
                "tokens": self._current_tokens(),
                "capacity": self.capacity,
                "utilization": self._current_tokens() / self.capacity,
                "has_summary": bool(self.summary),
                "summary_tokens": self._estimate_tokens(self.summary) if self.summary else 0
            }


class ArchivalMemory:
    """
    归档记忆 - 无限容量的历史存储
    
    特点：
    - 使用向量数据库进行语义检索
    - 支持无限容量
    - 按重要性、时间、相关性检索
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or Path(workspace()) / ".memgpt" / "archival.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.lock = threading.Lock()
        self._init_db()
        
        # 简单的嵌入函数（实际应用中应使用真实的 embedding 模型）
        self._embedding_cache = {}
    
    def _init_db(self):
        """初始化数据库"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    memory_type TEXT DEFAULT 'archival',
                    priority REAL DEFAULT 0.5,
                    importance REAL DEFAULT 0.5,
                    created_at TEXT,
                    last_accessed TEXT,
                    access_count INTEGER DEFAULT 0,
                    metadata TEXT,
                    tags TEXT
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_importance ON memories(importance)
            """)
            
            conn.commit()
    
    def _simple_embedding(self, text: str) -> List[float]:
        """简单的文本嵌入（基于哈希的伪嵌入，实际应用中应替换为真实模型）"""
        # 使用哈希生成固定维度的伪向量
        # 注意：这不是真正的语义嵌入，仅用于演示
        dim = 128
        words = text.lower().split()
        embedding = np.zeros(dim)
        
        for word in words:
            hash_val = int(hashlib.md5(word.encode()).hexdigest()[:8], 16)
            idx = hash_val % dim
            embedding[idx] += 1
        
        # 归一化
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        
        return embedding.tolist()
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        a = np.array(vec1)
        b = np.array(vec2)
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8)
    
    def add(self, content: str, priority: MemoryPriority = MemoryPriority.MEDIUM,
            importance: float = None, metadata: Dict = None, 
            tags: List[str] = None) -> str:
        """添加归档记忆"""
        with self.lock:
            memory_id = f"arch_{datetime.now().strftime('%Y%m%d%H%M%S')}_{hashlib.md5(content.encode()).hexdigest()[:8]}"
            
            now = datetime.now(timezone.utc).isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO memories (id, content, memory_type, priority, importance,
                                         created_at, last_accessed, access_count, metadata, tags)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """, (
                    memory_id,
                    content,
                    MemoryType.ARCHIVAL.value,
                    priority.value,
                    importance or priority.value,
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    json.dumps(tags or [], ensure_ascii=False)
                ))
                conn.commit()
            
            # 缓存嵌入
            self._embedding_cache[memory_id] = self._simple_embedding(content)
            
            return memory_id
    
    def search(self, query: str, top_k: int = 10, min_importance: float = 0.0) -> List[Memory]:
        """语义搜索"""
        with self.lock:
            query_embedding = self._simple_embedding(query)
            
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM memories 
                    WHERE importance >= ?
                    ORDER BY importance DESC
                """, (min_importance,))
                
                rows = cursor.fetchall()
            
            # 计算相似度
            results = []
            for row in rows:
                memory = Memory(
                    id=row["id"],
                    content=row["content"],
                    memory_type=MemoryType(row["memory_type"]),
                    priority=MemoryPriority(row["priority"]),
                    importance=row["importance"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    tags=json.loads(row["tags"]) if row["tags"] else []
                )
                
                # 获取或计算嵌入
                if memory.id in self._embedding_cache:
                    embedding = self._embedding_cache[memory.id]
                else:
                    embedding = self._simple_embedding(memory.content)
                    self._embedding_cache[memory.id] = embedding
                
                memory.embedding = embedding
                similarity = self._cosine_similarity(query_embedding, embedding)
                
                if similarity > 0.1:  # 相似度阈值
                    results.append((memory, similarity))
            
            # 按相似度排序
            results.sort(key=lambda x: x[1], reverse=True)
            
            # 更新访问计数
            for memory, _ in results[:top_k]:
                self._touch(memory.id)
            
            return [m for m, _ in results[:top_k]]
    
    def _touch(self, memory_id: str):
        """更新访问时间和计数"""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE memories 
                SET last_accessed = ?, access_count = access_count + 1
                WHERE id = ?
            """, (now, memory_id))
            conn.commit()
    
    def get_by_id(self, memory_id: str) -> Optional[Memory]:
        """根据 ID 获取记忆"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                memory = Memory(
                    id=row["id"],
                    content=row["content"],
                    memory_type=MemoryType(row["memory_type"]),
                    priority=MemoryPriority(row["priority"]),
                    importance=row["importance"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    tags=json.loads(row["tags"]) if row["tags"] else []
                )
                
                self._touch(memory_id)
                return memory
    
    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                conn.commit()
                
                if memory_id in self._embedding_cache:
                    del self._embedding_cache[memory_id]
                
                return cursor.rowcount > 0
    
    def get_recent(self, limit: int = 100) -> List[Memory]:
        """获取最近的记忆"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("""
                    SELECT * FROM memories 
                    ORDER BY created_at DESC 
                    LIMIT ?
                """, (limit,))
                
                rows = cursor.fetchall()
                
                return [Memory(
                    id=row["id"],
                    content=row["content"],
                    memory_type=MemoryType(row["memory_type"]),
                    priority=MemoryPriority(row["priority"]),
                    importance=row["importance"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    access_count=row["access_count"],
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    tags=json.loads(row["tags"]) if row["tags"] else []
                ) for row in rows]
    
    def stats(self) -> Dict:
        """获取统计信息"""
        with self.lock:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM memories")
                total = cursor.fetchone()[0]
                
                cursor = conn.execute("SELECT AVG(importance) FROM memories")
                avg_importance = cursor.fetchone()[0] or 0
                
                return {
                    "total_memories": total,
                    "avg_importance": avg_importance,
                    "cache_size": len(self._embedding_cache)
                }


class MemGPTMemory:
    """
    MemGPT 风格记忆管理器
    
    整合三级内存架构：
    - Core Memory: 关键信息
    - Working Memory: 对话上下文
    - Archival Memory: 历史存储
    """
    
    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or Path(workspace()))
        
        # 初始化三级内存
        self.core_memory = CoreMemory()
        self.working_memory = WorkingMemory()
        self.archival_memory = ArchivalMemory(str(self.workspace_path / ".memgpt" / "archival.db"))
        
        # 自主内存管理配置
        self.auto_archive_threshold = 0.3  # 低于此重要性自动归档
        self.auto_promote_threshold = 0.8  # 高于此重要性自动提升到核心
        
        print("✅ MemGPT 记忆系统已启动")
    
    # ==================== 核心接口 ====================
    
    def remember(self, content: str, importance: float = None, 
                 metadata: Dict = None, tags: List[str] = None) -> str:
        """
        存储记忆，返回记忆 ID
        
        自动决定存储位置：
        - importance >= 0.8 → Core Memory
        - importance >= 0.5 → Archival Memory (高优先级)
        - importance < 0.5 → Archival Memory (低优先级)
        """
        importance = importance if importance is not None else 0.5
        
        # 根据重要性决定优先级
        if importance >= self.auto_promote_threshold:
            priority = MemoryPriority.CRITICAL
            memory_id = self.core_memory.add(
                content, priority=priority, 
                metadata=metadata, tags=tags
            )
            # 同时存入归档作为备份
            self.archival_memory.add(
                content, priority=priority, 
                importance=importance, metadata=metadata, tags=tags
            )
        elif importance >= 0.5:
            priority = MemoryPriority.HIGH
            memory_id = self.archival_memory.add(
                content, priority=priority,
                importance=importance, metadata=metadata, tags=tags
            )
        else:
            priority = MemoryPriority.MEDIUM
            memory_id = self.archival_memory.add(
                content, priority=priority,
                importance=importance, metadata=metadata, tags=tags
            )
        
        return memory_id
    
    def recall(self, query: str, top_k: int = 10) -> List[Memory]:
        """
        检索记忆
        
        检索顺序：
        1. Core Memory（精确匹配）
        2. Archival Memory（语义搜索）
        """
        results = []
        
        # 1. 搜索核心记忆
        core_results = self.core_memory.search(query)
        results.extend(core_results)
        
        # 2. 搜索归档记忆
        archival_results = self.archival_memory.search(query, top_k=top_k)
        
        # 去重
        existing_ids = {m.id for m in results}
        for m in archival_results:
            if m.id not in existing_ids:
                results.append(m)
        
        return results[:top_k]
    
    def forget(self, memory_id: str) -> bool:
        """遗忘记忆"""
        # 尝试从核心记忆删除
        if self.core_memory.remove(memory_id):
            return True
        
        # 尝试从归档记忆删除
        return self.archival_memory.delete(memory_id)
    
    # ==================== 工作记忆管理 ====================
    
    def add_message(self, role: str, content: str):
        """添加对话消息到工作记忆"""
        self.working_memory.add_message(role, content)
    
    def get_context(self) -> str:
        """获取完整上下文（用于注入到 prompt）"""
        parts = []
        
        # 核心记忆
        core_context = self.core_memory.get_context_string()
        if core_context:
            parts.append(core_context)
        
        # 工作记忆
        working_context = self.working_memory.get_context()
        if working_context:
            parts.append(working_context)
        
        return "\n\n".join(parts)
    
    # ==================== 自主内存管理 ====================
    
    def auto_manage(self):
        """
        自主内存管理
        
        - 检查核心记忆容量
        - 压缩工作记忆
        - 归档低重要性记忆
        """
        # 工作记忆自动压缩已在 add_message 中实现
        
        # 检查核心记忆是否需要降级
        core_stats = self.core_memory.stats()
        if core_stats["utilization"] > 0.9:
            # 找到最低优先级的记忆，降级到归档
            evicted = self.core_memory._evict_lowest_priority()
            if evicted:
                self.archival_memory.add(
                    evicted.content,
                    priority=evicted.priority,
                    importance=evicted.importance * 0.8,  # 降级时降低重要性
                    metadata=evicted.metadata,
                    tags=evicted.tags
                )
    
    def promote_to_core(self, memory_id: str) -> bool:
        """将归档记忆提升到核心记忆"""
        memory = self.archival_memory.get_by_id(memory_id)
        if not memory:
            return False
        
        # 添加到核心记忆
        self.core_memory.add(
            memory.content,
            priority=MemoryPriority.HIGH,
            metadata=memory.metadata,
            tags=memory.tags
        )
        
        return True
    
    # ==================== 统计与维护 ====================
    
    def stats(self) -> Dict:
        """获取系统统计"""
        return {
            "core_memory": self.core_memory.stats(),
            "working_memory": self.working_memory.stats(),
            "archival_memory": self.archival_memory.stats()
        }
    
    def clear_working_memory(self):
        """清空工作记忆（开始新对话时）"""
        self.working_memory.clear()


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="MemGPT 记忆管理器")
    parser.add_argument("command", choices=[
        "remember", "recall", "forget", "stats", "context", "manage"
    ])
    parser.add_argument("--content", help="记忆内容")
    parser.add_argument("--query", help="查询")
    parser.add_argument("--id", help="记忆 ID")
    parser.add_argument("--importance", type=float, default=0.5, help="重要性")
    
    args = parser.parse_args()
    
    memory = MemGPTMemory()
    
    if args.command == "remember":
        if not args.content:
            print("错误: 需要提供 --content")
            return
        memory_id = memory.remember(args.content, args.importance)
        print(f"✅ 已存储: {memory_id}")
    
    elif args.command == "recall":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        results = memory.recall(args.query)
        print(f"找到 {len(results)} 条记忆:")
        for r in results:
            print(f"  [{r.memory_type.value}] {r.content[:50]}...")
    
    elif args.command == "forget":
        if not args.id:
            print("错误: 需要提供 --id")
            return
        success = memory.forget(args.id)
        print(f"{'✅ 已删除' if success else '❌ 未找到'}")
    
    elif args.command == "stats":
        stats = memory.stats()
        import json
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    
    elif args.command == "context":
        context = memory.get_context()
        print(context if context else "(无上下文)")
    
    elif args.command == "manage":
        memory.auto_manage()
        print("✅ 自动内存管理完成")


if __name__ == "__main__":
    main()
