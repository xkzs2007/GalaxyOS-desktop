#!/usr/bin/env python3
"""
检索公式模块 (Retrieval Formula Module)

基于 Generative Agents 论文实现的记忆检索算法：
- recency: 时效性（时间衰减）
- relevance: 相关性（语义相似度）
- importance: 重要性（记忆评分）

综合得分 = w_r * recency + w_e * relevance + w_i * importance

论文参考: Generative Agents: Interactive Simulacra (2023)
https://arxiv.org/abs/2304.03442

Author: GalaxyOS
Version: 1.0.0
Created: 2026-04-21
"""

import math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

try:
    from .memory_stream import Memory, MemoryType
except ImportError:
    from memory_stream import Memory, MemoryType


# ==================== 配置参数 ====================

@dataclass
class RetrievalWeights:
    """检索权重配置"""
    recency: float = 1.0      # 时效性权重
    relevance: float = 1.0    # 相关性权重
    importance: float = 1.0   # 重要性权重


@dataclass
class RetrievalConfig:
    """检索配置"""
    weights: RetrievalWeights = field(default_factory=RetrievalWeights)

    # 时间衰减参数
    decay_hours: float = 24.0  # 半衰期（小时）
    max_decay: float = 0.99    # 最大衰减值

    # 相关性阈值
    relevance_threshold: float = 0.3  # 最低相关性

    # 返回数量
    top_k: int = 10


# ==================== 检索函数 ====================

def calculate_recency(
    memory: Memory,
    reference_time: Optional[datetime] = None,
    decay_hours: float = 24.0
) -> float:
    """
    计算时效性得分

    使用指数衰减函数：
    recency = exp(-hours_since_creation / decay_hours)

    Args:
        memory: 记忆对象
        reference_time: 参考时间（默认当前时间）
        decay_hours: 衰减半衰期（小时）

    Returns:
        时效性得分 (0-1)
    """
    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    # 计算时间差（小时）
    time_diff = (reference_time - memory.created_at).total_seconds() / 3600

    # 指数衰减
    recency = math.exp(-time_diff / decay_hours)

    return min(1.0, max(0.0, recency))


def calculate_relevance(
    memory: Memory,
    query: str,
    embedding_func: Optional[callable] = None
) -> float:
    """
    计算相关性得分

    方法：
    1. 如果有 embedding_func，使用向量相似度
    2. 否则使用关键词匹配

    Args:
        memory: 记忆对象
        query: 查询文本
        embedding_func: 向量嵌入函数 (可选)

    Returns:
        相关性得分 (0-1)
    """
    if embedding_func and memory.embedding:
        # 使用向量相似度
        query_embedding = embedding_func(query)
        similarity = cosine_similarity(query_embedding, memory.embedding)
        return similarity
    else:
        # 使用关键词匹配
        return keyword_relevance(memory.content, query)


def calculate_importance(memory: Memory) -> float:
    """
    计算重要性得分

    归一化到 0-1 范围

    Args:
        memory: 记忆对象

    Returns:
        重要性得分 (0-1)
    """
    # importance 原始范围是 1-10
    return (memory.importance - 1.0) / 9.0


def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """计算余弦相似度"""
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


def keyword_relevance(content: str, query: str) -> float:
    """
    基于关键词的相关性计算

    Args:
        content: 记忆内容
        query: 查询文本

    Returns:
        相关性得分 (0-1)
    """
    # 分词（简单按空格和标点分割）
    import re

    def tokenize(text: str) -> set:
        # 中文按字符，英文按单词
        chinese_chars = set(re.findall(r'[\u4e00-\u9fff]', text))
        english_words = set(w.lower() for w in re.findall(r'[a-zA-Z]+', text))
        return chinese_chars | english_words

    content_tokens = tokenize(content)
    query_tokens = tokenize(query)

    if not query_tokens:
        return 0.0

    # Jaccard 相似度
    intersection = len(content_tokens & query_tokens)
    union = len(content_tokens | query_tokens)

    if union == 0:
        return 0.0

    jaccard = intersection / union

    # 考虑查询词覆盖率
    coverage = intersection / len(query_tokens) if query_tokens else 0

    # 综合得分
    return 0.5 * jaccard + 0.5 * coverage


# ==================== 检索器 ====================

class MemoryRetriever:
    """
    记忆检索器

    实现论文中的检索公式：
    score = w_r * recency + w_e * relevance + w_i * importance
    """

    def __init__(self, config: Optional[RetrievalConfig] = None):
        """
        初始化检索器

        Args:
            config: 检索配置
        """
        self.config = config or RetrievalConfig()
        self.embedding_func = None

    def set_embedding_func(self, func: callable):
        """设置向量嵌入函数"""
        self.embedding_func = func

    def retrieve(
        self,
        memories: List[Memory],
        query: str,
        top_k: Optional[int] = None,
        memory_types: Optional[List[MemoryType]] = None,
        min_score: float = 0.0
    ) -> List[Tuple[Memory, float, Dict[str, float]]]:
        """
        检索相关记忆

        Args:
            memories: 候选记忆列表
            query: 查询文本
            top_k: 返回数量
            memory_types: 限定记忆类型
            min_score: 最低得分阈值

        Returns:
            List of (memory, total_score, score_breakdown)
        """
        if top_k is None:
            top_k = self.config.top_k

        # 过滤类型
        if memory_types:
            memories = [m for m in memories if m.memory_type in memory_types]

        # 计算得分
        scored_memories = []
        reference_time = datetime.now(timezone.utc)

        for memory in memories:
            scores = self._calculate_scores(memory, query, reference_time)
            total = scores["total"]

            if total >= min_score:
                scored_memories.append((memory, total, scores))

        # 排序并返回 top_k
        scored_memories.sort(key=lambda x: x[1], reverse=True)
        return scored_memories[:top_k]

    def retrieve_for_reflection(
        self,
        memories: List[Memory],
        top_k: int = 100
    ) -> List[Memory]:
        """
        为反思检索记忆

        反思需要较长时间窗口的记忆，侧重重要性

        Returns:
            用于反思的记忆列表
        """
        # 反思配置：降低时效性权重，提高重要性权重
        reflection_weights = RetrievalWeights(
            recency=0.5,
            relevance=0.5,
            importance=2.0
        )

        old_weights = self.config.weights
        self.config.weights = reflection_weights

        # 使用空查询（主要靠时效性和重要性）
        results = self.retrieve(
            memories,
            query="",
            top_k=top_k,
            min_score=0.1
        )

        self.config.weights = old_weights

        return [m for m, _, _ in results]

    def retrieve_for_planning(
        self,
        memories: List[Memory],
        context: str,
        top_k: int = 20
    ) -> List[Memory]:
        """
        为规划检索记忆

        规划需要近期相关记忆，侧重时效性和相关性

        Returns:
            用于规划的记忆列表
        """
        # 规划配置：提高时效性和相关性权重
        planning_weights = RetrievalWeights(
            recency=2.0,
            relevance=2.0,
            importance=1.0
        )

        old_weights = self.config.weights
        self.config.weights = planning_weights

        results = self.retrieve(
            memories,
            query=context,
            top_k=top_k
        )

        self.config.weights = old_weights

        return [m for m, _, _ in results]

    def _calculate_scores(
        self,
        memory: Memory,
        query: str,
        reference_time: datetime
    ) -> Dict[str, float]:
        """计算各项得分"""
        w = self.config.weights

        recency = calculate_recency(
            memory,
            reference_time,
            self.config.decay_hours
        )

        relevance = calculate_relevance(
            memory,
            query,
            self.embedding_func
        ) if query else 0.5  # 无查询时给中等相关性

        importance = calculate_importance(memory)

        total = (
            w.recency * recency +
            w.relevance * relevance +
            w.importance * importance
        ) / (w.recency + w.relevance + w.importance)

        return {
            "recency": recency,
            "relevance": relevance,
            "importance": importance,
            "total": total
        }


# ==================== 便捷函数 ====================

def retrieve_memories(
    memories: List[Memory],
    query: str,
    top_k: int = 10
) -> List[Memory]:
    """快速检索记忆"""
    retriever = MemoryRetriever()
    results = retriever.retrieve(memories, query, top_k)
    return [m for m, _, _ in results]


if __name__ == "__main__":
    # 测试代码
    from .memory_stream import MemoryStream, MemoryType

    stream = MemoryStream()

    # 添加测试记忆
    stream.add("今天学习了 Python 异步编程", MemoryType.OBSERVATION, importance=7.0)
    stream.add("异步编程的关键是理解事件循环", MemoryType.REFLECTION, importance=8.5)
    stream.add("明天要复习 asyncio 模块", MemoryType.PLAN, importance=6.0)
    stream.add("Python 是一门优雅的语言", MemoryType.OBSERVATION, importance=5.0)

    # 检索
    retriever = MemoryRetriever()
    results = retriever.retrieve(stream.get_all(), "Python 编程")

    print("检索结果:")
    for memory, score, breakdown in results:
        print(f"  [{score:.3f}] {memory.content[:50]}...")
        print(f"    recency={breakdown['recency']:.2f}, "
              f"relevance={breakdown['relevance']:.2f}, "
              f"importance={breakdown['importance']:.2f}")
