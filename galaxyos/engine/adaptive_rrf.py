#!/usr/bin/env python3
"""
自适应 RRF 融合模块

论文参考:
- Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods (SIGIR 2009)

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
import re


class QueryCategory(Enum):
    """查询类别"""
    EXACT_MATCH = "exact_match"      # 精确匹配
    SEMANTIC = "semantic"            # 语义相似
    HYBRID = "hybrid"                # 混合查询
    KEYWORD_HEAVY = "keyword_heavy"  # 关键词密集
    CONCEPT_HEAVY = "concept_heavy"  # 概念密集


@dataclass
class RRFWeights:
    """RRF 权重配置"""
    dense_weight: float = 0.5
    sparse_weight: float = 0.5
    k: int = 60  # RRF 常数


class AdaptiveRRF:
    """
    自适应 RRF 融合

    基于 RRF 论文:
    - RRF 论文建议 k=60，但权重可以根据查询类型调整
    - 不同查询类型对 Dense/Sparse 的依赖不同

    创新点:
    - 根据查询类型动态调整权重
    - 分析查询特征自动判断类型
    - 支持用户自定义权重策略
    """

    # 默认权重
    DEFAULT_WEIGHTS = RRFWeights()

    # 查询类型权重映射
    CATEGORY_WEIGHTS = {
        QueryCategory.EXACT_MATCH: RRFWeights(dense_weight=0.3, sparse_weight=0.7),
        QueryCategory.SEMANTIC: RRFWeights(dense_weight=0.7, sparse_weight=0.3),
        QueryCategory.HYBRID: RRFWeights(dense_weight=0.5, sparse_weight=0.5),
        QueryCategory.KEYWORD_HEAVY: RRFWeights(dense_weight=0.35, sparse_weight=0.65),
        QueryCategory.CONCEPT_HEAVY: RRFWeights(dense_weight=0.65, sparse_weight=0.35),
    }

    # 精确匹配特征
    EXACT_PATTERNS = [
        r'^"[^"]+"$',  # 引号包裹
        r'\b\d{4}\b',  # 年份
        r'\b[A-Z]{2,}\b',  # 缩写
        r'\b\w+\s*=\s*\w+\b',  # 等式
    ]

    # 关键词特征
    KEYWORD_INDICATORS = [
        '具体', '精确', '准确', 'exact', 'specific', 'precise'
    ]

    # 概念特征
    CONCEPT_INDICATORS = [
        '类似', '相似', '相关', 'similar', 'related', 'like'
    ]

    def __init__(self, weights: Optional[RRFWeights] = None):
        """
        初始化自适应 RRF

        Args:
            weights: 自定义权重
        """
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.adjustment_log = []

    def classify_query(self, query: str) -> QueryCategory:
        """
        分类查询

        Args:
            query: 用户查询

        Returns:
            查询类别
        """
        query_lower = query.lower()

        # 1. 检查精确匹配特征
        for pattern in self.EXACT_PATTERNS:
            if re.search(pattern, query):
                return QueryCategory.EXACT_MATCH

        # 2. 检查关键词指示
        for indicator in self.KEYWORD_INDICATORS:
            if indicator in query_lower:
                return QueryCategory.KEYWORD_HEAVY

        # 3. 检查概念指示
        for indicator in self.CONCEPT_INDICATORS:
            if indicator in query_lower:
                return QueryCategory.CONCEPT_HEAVY

        # 4. 分析查询特征
        words = query.split()
        if not words:
            return QueryCategory.HYBRID

        # 计算特征比例
        has_numbers = any(w.isdigit() for w in words)
        has_special_chars = any(not w.isalnum() for w in words)
        avg_word_length = sum(len(w) for w in words) / len(words)

        # 数字多 → 精确匹配
        if has_numbers and len(words) < 5:
            return QueryCategory.EXACT_MATCH

        # 单词长 → 概念密集
        if avg_word_length > 6:
            return QueryCategory.CONCEPT_HEAVY

        # 特殊字符多 → 关键词密集
        if has_special_chars:
            return QueryCategory.KEYWORD_HEAVY

        # 默认混合
        return QueryCategory.HYBRID

    def has_exact_terms(self, query: str) -> bool:
        """
        检查是否有精确术语

        Args:
            query: 用户查询

        Returns:
            是否有精确术语
        """
        # 检查引号
        if '"' in query or "'" in query:
            return True

        # 检查专有名词（首字母大写）
        words = query.split()
        proper_nouns = sum(1 for w in words if w[0].isupper() and len(w) > 1)

        return proper_nouns > len(words) * 0.3

    def get_adaptive_weights(
        self,
        query: str,
        category: Optional[QueryCategory] = None
    ) -> RRFWeights:
        """
        获取自适应权重

        Args:
            query: 用户查询
            category: 查询类别（可选，自动检测）

        Returns:
            调整后的权重
        """
        # 自动分类
        if category is None:
            category = self.classify_query(query)

        # 获取类别权重
        weights = self.CATEGORY_WEIGHTS.get(category, self.DEFAULT_WEIGHTS)

        # 微调：检查精确术语
        if self.has_exact_terms(query):
            # 有精确术语，稍微提高 sparse 权重
            adjustment = 0.1
            weights = RRFWeights(
                dense_weight=max(0.2, weights.dense_weight - adjustment),
                sparse_weight=min(0.8, weights.sparse_weight + adjustment),
                k=weights.k
            )

        # 记录日志
        self.adjustment_log.append({
            "query": query[:50],
            "category": category.value,
            "dense_weight": weights.dense_weight,
            "sparse_weight": weights.sparse_weight
        })

        return weights

    def fuse_rankings(
        self,
        dense_results: List[Tuple[str, float]],
        sparse_results: List[Tuple[str, float]],
        query: str = "",
        k: int = None
    ) -> List[Tuple[str, float]]:
        """
        融合 Dense 和 Sparse 排序结果

        Args:
            dense_results: Dense 检索结果 [(doc_id, score), ...]
            sparse_results: Sparse 检索结果 [(doc_id, score), ...]
            query: 用户查询（用于自适应权重）
            k: RRF 常数（可选）

        Returns:
            融合后的排序结果
        """
        # 获取自适应权重
        weights = self.get_adaptive_weights(query) if query else self.DEFAULT_WEIGHTS
        k = k or weights.k

        # 构建 rank 映射
        dense_ranks = {doc_id: rank + 1 for rank, (doc_id, _) in enumerate(dense_results)}
        sparse_ranks = {doc_id: rank + 1 for rank, (doc_id, _) in enumerate(sparse_results)}

        # 获取所有文档
        all_docs = set(dense_ranks.keys()) | set(sparse_ranks.keys())

        # 计算 RRF 分数
        fused_scores = {}
        for doc_id in all_docs:
            dense_rank = dense_ranks.get(doc_id, len(dense_results) + 1)
            sparse_rank = sparse_ranks.get(doc_id, len(sparse_results) + 1)

            # 加权 RRF
            dense_score = weights.dense_weight / (k + dense_rank)
            sparse_score = weights.sparse_weight / (k + sparse_rank)

            fused_scores[doc_id] = dense_score + sparse_score

        # 排序
        sorted_results = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)

        return sorted_results

    def get_adjustment_stats(self) -> Dict[str, Any]:
        """获取调整统计"""
        if not self.adjustment_log:
            return {"total_adjustments": 0}

        categories = {}
        dense_weights = []
        sparse_weights = []

        for log in self.adjustment_log:
            cat = log["category"]
            categories[cat] = categories.get(cat, 0) + 1
            dense_weights.append(log["dense_weight"])
            sparse_weights.append(log["sparse_weight"])

        return {
            "total_adjustments": len(self.adjustment_log),
            "category_distribution": categories,
            "avg_dense_weight": sum(dense_weights) / len(dense_weights),
            "avg_sparse_weight": sum(sparse_weights) / len(sparse_weights)
        }


# 便捷函数
def fuse_with_adaptive_rrf(
    dense_results: List[Tuple[str, float]],
    sparse_results: List[Tuple[str, float]],
    query: str = ""
) -> List[Tuple[str, float]]:
    """
    使用自适应 RRF 融合（便捷函数）

    Args:
        dense_results: Dense 检索结果
        sparse_results: Sparse 检索结果
        query: 用户查询

    Returns:
        融合后的排序结果
    """
    rrf = AdaptiveRRF()
    return rrf.fuse_rankings(dense_results, sparse_results, query)


if __name__ == "__main__":
    # 测试
    rrf = AdaptiveRRF()

    test_queries = [
        ('Python 3.11 新特性', '精确版本查询'),
        ('如何设计一个高并发系统', '概念密集查询'),
        ('"机器学习"的定义', '引号精确查询'),
        ('类似 TensorFlow 的框架', '概念相似查询'),
        ('2024年奥运会举办城市', '精确事实查询'),
        ('深度学习和机器学习的区别', '混合查询'),
    ]

    print("=" * 70)
    print("自适应 RRF 融合测试")
    print("=" * 70)

    for query, desc in test_queries:
        weights = rrf.get_adaptive_weights(query)
        category = rrf.classify_query(query)

        print(f"\n【{desc}】")
        print(f"  查询: {query}")
        print(f"  类别: {category.value}")
        print(f"  Dense 权重: {weights.dense_weight:.2f}")
        print(f"  Sparse 权重: {weights.sparse_weight:.2f}")

    print("\n" + "=" * 70)
    print("调整统计")
    print("=" * 70)
    stats = rrf.get_adjustment_stats()
    print(f"  总调整次数: {stats['total_adjustments']}")
    print(f"  平均 Dense 权重: {stats.get('avg_dense_weight', 0):.2f}")
    print(f"  平均 Sparse 权重: {stats.get('avg_sparse_weight', 0):.2f}")
    print(f"  类别分布: {stats.get('category_distribution', {})}")
