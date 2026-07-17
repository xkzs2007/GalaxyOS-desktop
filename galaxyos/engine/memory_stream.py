#!/usr/bin/env python3
"""
记忆流模块 (Memory Stream Module)

基于 Generative Agents 论文实现的记忆存储系统：
- 每条记忆带重要性评分 (importance score: 1-10)
- 支持时效性衰减
- 提供高效的记忆检索接口

论文参考: Generative Agents: Interactive Simulacra (2023)
https://arxiv.org/abs/2304.03442

Author: GalaxyOS
Version: 1.0.0
Created: 2026-04-21
"""

import json
import math
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
import threading
from collections import defaultdict


# ==================== 数据结构 ====================

class MemoryType(Enum):
    """记忆类型"""
    OBSERVATION = "observation"      # 观察：感知到的事件
    REFLECTION = "reflection"        # 反思：生成的洞察
    PLAN = "plan"                    # 计划：未来的行动
    ACTION = "action"                # 行动：已执行的动作
    CONVERSATION = "conversation"    # 对话：与他人的交流


@dataclass
class Memory:
    """
    记忆单元

    核心属性：
    - content: 记忆内容
    - importance: 重要性评分 (1-10)
    - created_at: 创建时间戳
    - last_accessed: 最后访问时间
    - access_count: 访问次数
    """
    id: str
    content: str
    memory_type: MemoryType
    importance: float  # 1-10 分
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0

    # 可选元数据
    keywords: List[str] = field(default_factory=list)
    embedding: Optional[List[float]] = None
    related_memories: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """确保 importance 在有效范围内"""
        self.importance = max(1.0, min(10.0, self.importance))

    def to_dict(self) -> Dict:
        """序列化为字典"""
        return {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "importance": self.importance,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
            "keywords": self.keywords,
            "related_memories": self.related_memories,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Memory':
        """从字典反序列化"""
        return cls(
            id=data["id"],
            content=data["content"],
            memory_type=MemoryType(data["memory_type"]),
            importance=data["importance"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_accessed=datetime.fromisoformat(data["last_accessed"]),
            access_count=data.get("access_count", 0),
            keywords=data.get("keywords", []),
            related_memories=data.get("related_memories", []),
            metadata=data.get("metadata", {})
        )

    def touch(self):
        """更新访问时间和计数"""
        self.last_accessed = datetime.now(timezone.utc)
        self.access_count += 1


class MemoryStream:
    """
    记忆流

    核心功能：
    - 存储带重要性评分的记忆
    - 支持时间衰减
    - 提供高效检索接口
    - 持久化存储
    """

    def __init__(self, storage_path: Optional[str] = None):
        """
        初始化记忆流

        Args:
            storage_path: 存储路径，None 则使用内存模式
        """
        self.memories: Dict[str, Memory] = {}
        self.storage_path = Path(storage_path) if storage_path else None
        self._lock = threading.RLock()

        # 索引加速
        self._type_index: Dict[MemoryType, List[str]] = defaultdict(list)
        self._time_index: List[Tuple[datetime, str]] = []  # 按时间排序

        # 加载持久化数据
        if self.storage_path and self.storage_path.exists():
            self._load()

    def add(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.OBSERVATION,
        importance: Optional[float] = None,
        keywords: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Memory:
        """
        添加新记忆

        Args:
            content: 记忆内容
            memory_type: 记忆类型
            importance: 重要性评分 (1-10)，None 则自动计算
            keywords: 关键词列表
            metadata: 额外元数据

        Returns:
            创建的记忆对象
        """
        with self._lock:
            now = datetime.now(timezone.utc)

            # 自动计算重要性
            if importance is None:
                importance = self._calculate_importance(content, memory_type)

            # 生成唯一 ID
            memory_id = self._generate_id(content, now)

            memory = Memory(
                id=memory_id,
                content=content,
                memory_type=memory_type,
                importance=importance,
                created_at=now,
                last_accessed=now,
                keywords=keywords or [],
                metadata=metadata or {}
            )

            # 存储
            self.memories[memory_id] = memory

            # 更新索引
            self._type_index[memory_type].append(memory_id)
            self._time_index.append((now, memory_id))
            self._time_index.sort(key=lambda x: x[0])

            # 持久化
            self._save()

            return memory

    def get(self, memory_id: str) -> Optional[Memory]:
        """获取记忆并更新访问时间"""
        with self._lock:
            memory = self.memories.get(memory_id)
            if memory:
                memory.touch()
                self._save()
            return memory

    def get_all(self) -> List[Memory]:
        """获取所有记忆"""
        return list(self.memories.values())

    def get_by_type(self, memory_type: MemoryType) -> List[Memory]:
        """按类型获取记忆"""
        with self._lock:
            ids = self._type_index.get(memory_type, [])
            return [self.memories[i] for i in ids if i in self.memories]

    def get_recent(self, hours: float = 24.0) -> List[Memory]:
        """获取最近 N 小时的记忆"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self._lock:
            return [
                self.memories[mid]
                for ts, mid in self._time_index
                if ts >= cutoff and mid in self.memories
            ]

    def get_important(self, threshold: float = 7.0) -> List[Memory]:
        """获取高重要性记忆"""
        return [m for m in self.memories.values() if m.importance >= threshold]

    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        with self._lock:
            if memory_id not in self.memories:
                return False

            memory = self.memories[memory_id]

            # 从索引移除
            self._type_index[memory.memory_type].remove(memory_id)
            self._time_index = [(ts, mid) for ts, mid in self._time_index if mid != memory_id]

            # 删除记忆
            del self.memories[memory_id]
            self._save()

            return True

    def clear_old(self, days: int = 30) -> int:
        """清理旧记忆（保留高重要性）"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = 0

        with self._lock:
            to_delete = [
                mid for mid, m in self.memories.items()
                if m.created_at < cutoff and m.importance < 5.0
            ]

            for mid in to_delete:
                self.delete(mid)
                deleted += 1

        return deleted

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            if not self.memories:
                return {"total": 0, "by_type": {}, "avg_importance": 0}

            by_type = {}
            for mt in MemoryType:
                by_type[mt.value] = len(self._type_index.get(mt, []))

            avg_importance = sum(m.importance for m in self.memories.values()) / len(self.memories)

            return {
                "total": len(self.memories),
                "by_type": by_type,
                "avg_importance": round(avg_importance, 2),
                "oldest": min(m.created_at for m in self.memories.values()).isoformat(),
                "newest": max(m.created_at for m in self.memories.values()).isoformat()
            }

    # ==================== 私有方法 ====================

    def _generate_id(self, content: str, timestamp: datetime) -> str:
        """生成唯一 ID"""
        hash_input = f"{content}:{timestamp.isoformat()}:{len(self.memories)}"
        return hashlib.md5(hash_input.encode()).hexdigest()[:12]

    def _calculate_importance(self, content: str, memory_type: MemoryType) -> float:
        """
        自动计算重要性评分

        规则：
        - 反思类记忆默认高分 (8.0)
        - 计划类记忆中等分数 (6.0)
        - 观察/行动类根据内容长度和关键词估算
        """
        base_scores = {
            MemoryType.REFLECTION: 8.0,
            MemoryType.PLAN: 6.0,
            MemoryType.ACTION: 5.0,
            MemoryType.CONVERSATION: 5.0,
            MemoryType.OBSERVATION: 4.0
        }

        base = base_scores.get(memory_type, 5.0)

        # 根据内容长度微调
        length_factor = min(len(content) / 500, 1.0) * 0.5

        # 关键词加分
        important_keywords = ["重要", "关键", "紧急", "错误", "学习", "决定"]
        keyword_bonus = sum(0.3 for kw in important_keywords if kw in content)

        return min(10.0, base + length_factor + keyword_bonus)

    def _save(self):
        """持久化存储"""
        if not self.storage_path:
            return

        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "memories": [m.to_dict() for m in self.memories.values()],
            "version": "1.0.0",
            "updated_at": datetime.now(timezone.utc).isoformat()
        }

        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _load(self):
        """加载持久化数据"""
        if not self.storage_path or not self.storage_path.exists():
            return

        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for mem_data in data.get("memories", []):
                memory = Memory.from_dict(mem_data)
                self.memories[memory.id] = memory

                # 重建索引
                self._type_index[memory.memory_type].append(memory.id)
                self._time_index.append((memory.created_at, memory.id))

            self._time_index.sort(key=lambda x: x[0])

        except Exception as e:
            print(f"[MemoryStream] 加载失败: {e}")


# ==================== 便捷函数 ====================

def create_memory_stream(storage_path: Optional[str] = None) -> MemoryStream:
    """创建记忆流实例"""
    return MemoryStream(storage_path)


if __name__ == "__main__":
    # 测试代码
    stream = MemoryStream()

    # 添加记忆
    m1 = stream.add("今天学习了 Python 异步编程", MemoryType.OBSERVATION, importance=7.0)
    m2 = stream.add("异步编程的关键是理解事件循环", MemoryType.REFLECTION, importance=8.5)
    m3 = stream.add("明天要复习 asyncio 模块", MemoryType.PLAN, importance=6.0)

    print(f"总记忆数: {len(stream.get_all())}")
    print(f"统计: {stream.get_stats()}")

    # 获取最近记忆
    recent = stream.get_recent(hours=1)
    print(f"最近记忆: {len(recent)} 条")
