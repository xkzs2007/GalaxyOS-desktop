#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IsSUP Predictor - 检索支持度预测器
基于 Self-RAG 论文实现，判断检索结果是否与查询相关

Self-RAG: https://arxiv.org/abs/2310.11511
"""

import re
import json
import logging
from typing import Tuple, List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SupportLevel(Enum):
    """支持度级别"""
    FULLY_RELEVANT = "fully_relevant"      # 完全相关
    PARTIALLY_RELEVANT = "partially_relevant"  # 部分相关
    NOT_RELEVANT = "not_relevant"          # 不相关
    CONTRADICTORY = "contradictory"        # 矛盾


@dataclass
class SupportDecision:
    """支持度决策结果"""
    is_supported: bool
    confidence: float
    support_level: SupportLevel
    relevance_score: float
    coverage_score: float
    key_matches: List[str]
    missing_aspects: List[str]
    reasoning: str


class IsSUPPredictor:
    """
    IsSUP 预测器 - 判断检索结果是否相关

    基于 Self-RAG 论文的检索结果评估模块，通过分析检索内容与查询的
    相关性来判断检索结果的质量。

    核心评估维度：
    1. 语义相关性：内容是否回答了查询的核心问题
    2. 覆盖度：内容是否覆盖了查询的所有方面
    3. 时效性：内容是否是最新的（如果需要）
    4. 权威性：内容来源是否可靠
    """

    # 相关性评分权重
    WEIGHTS = {
        'keyword_overlap': 0.25,
        'semantic_match': 0.30,
        'coverage': 0.25,
        'recency': 0.10,
        'authority': 0.10
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        初始化 IsSUP 预测器

        Args:
            config: 配置字典，可包含：
                - relevance_threshold: 相关性阈值，默认 0.6
                - coverage_threshold: 覆盖度阈值，默认 0.5
        """
        self.config = config or {}
        self.relevance_threshold = self.config.get('relevance_threshold', 0.6)
        self.coverage_threshold = self.config.get('coverage_threshold', 0.5)

        logger.info(f"IsSUP Predictor initialized with relevance_threshold={self.relevance_threshold}")

    def predict(
        self,
        query: str,
        retrieved_content: str,
        metadata: Optional[Dict] = None
    ) -> SupportDecision:
        """
        预测检索结果的支持度

        Args:
            query: 用户查询
            retrieved_content: 检索到的内容
            metadata: 可选的元数据（来源、时间等）

        Returns:
            SupportDecision: 支持度决策结果
        """
        # 1. 提取查询关键词
        query_keywords = self._extract_keywords(query)

        # 2. 计算关键词重叠度
        keyword_overlap, key_matches = self._calculate_keyword_overlap(
            query_keywords, retrieved_content
        )

        # 3. 计算语义匹配度
        semantic_match = self._calculate_semantic_match(query, retrieved_content)

        # 4. 计算覆盖度
        coverage_score, missing_aspects = self._calculate_coverage(
            query, retrieved_content
        )

        # 5. 计算时效性得分
        recency_score = self._calculate_recency(query, metadata)

        # 6. 计算权威性得分
        authority_score = self._calculate_authority(metadata)

        # 7. 综合评分
        relevance_score = (
            self.WEIGHTS['keyword_overlap'] * keyword_overlap +
            self.WEIGHTS['semantic_match'] * semantic_match +
            self.WEIGHTS['coverage'] * coverage_score +
            self.WEIGHTS['recency'] * recency_score +
            self.WEIGHTS['authority'] * authority_score
        )

        # 8. 确定支持级别
        support_level = self._determine_support_level(
            relevance_score, coverage_score, keyword_overlap
        )

        # 9. 计算置信度
        confidence = self._calculate_confidence(
            keyword_overlap, semantic_match, coverage_score
        )

        # 10. 生成推理说明
        reasoning = self._generate_reasoning(
            support_level, relevance_score, coverage_score,
            key_matches, missing_aspects
        )

        return SupportDecision(
            is_supported=(support_level in [SupportLevel.FULLY_RELEVANT, SupportLevel.PARTIALLY_RELEVANT]),
            confidence=round(confidence, 3),
            support_level=support_level,
            relevance_score=round(relevance_score, 3),
            coverage_score=round(coverage_score, 3),
            key_matches=key_matches,
            missing_aspects=missing_aspects,
            reasoning=reasoning
        )

    def is_supported(
        self,
        query: str,
        retrieved_content: str
    ) -> Tuple[bool, float]:
        """
        简化接口：判断检索结果是否相关

        Args:
            query: 用户查询
            retrieved_content: 检索到的内容

        Returns:
            Tuple[bool, float]: (是否相关, 置信度)
        """
        decision = self.predict(query, retrieved_content)
        return decision.is_supported, decision.confidence

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        stopwords = {'的', '是', '在', '了', '和', '与', '或', '有', '我', '你', '他', '她', '它',
                     '这', '那', '什么', '怎么', '如何', '为什么', '哪', '吗', '呢', '吧'}

        words = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+|\d+', text.lower())

        keywords = [
            w for w in words
            if w not in stopwords and len(w) > 1
        ]

        return list(set(keywords))

    def _calculate_keyword_overlap(
        self,
        query_keywords: List[str],
        content: str
    ) -> Tuple[float, List[str]]:
        """计算关键词重叠度"""
        if not query_keywords:
            return 0.0, []

        content_lower = content.lower()
        matches = []

        for keyword in query_keywords:
            if keyword.lower() in content_lower:
                matches.append(keyword)

        # 提高重叠度的权重
        overlap = len(matches) / len(query_keywords)
        # 给予基础分，只要有匹配
        if matches:
            overlap = max(overlap, 0.3)

        return overlap, matches

    def _calculate_semantic_match(self, query: str, content: str) -> float:
        """计算语义匹配度（简化版本）"""
        # 检查问题类型匹配
        query_lower = query.lower()
        content_lower = content.lower()

        score = 0.0

        # 定义问题类型及其对应的内容指示词
        question_patterns = {
            '定义类': {
                'query': ['什么是', '定义', '概念', '是什么'],
                'content': ['是指', '定义为', '是一种', '意思是', '是', '属于']
            },
            '原因类': {
                'query': ['为什么', '原因', '为何'],
                'content': ['因为', '原因是', '由于', '导致', '所以']
            },
            '方法类': {
                'query': ['如何', '怎么', '怎样', '方法'],
                'content': ['方法', '步骤', '首先', '然后', '最后', '可以', '需要']
            },
            '比较类': {
                'query': ['区别', '差异', '比较', '对比'],
                'content': ['不同', '相比', '区别', '差异', '而', '但是']
            }
        }

        for qtype, patterns in question_patterns.items():
            # 检查查询是否属于该类型
            is_type = any(p in query_lower for p in patterns['query'])
            if is_type:
                # 检查内容是否包含对应的回答模式
                has_answer = any(p in content_lower for p in patterns['content'])
                if has_answer:
                    score += 0.30

        return min(score, 1.0)

    def _calculate_coverage(
        self,
        query: str,
        content: str
    ) -> Tuple[float, List[str]]:
        """计算覆盖度"""
        # 提取查询中的核心概念
        concepts = self._extract_core_concepts(query)

        missing = []
        covered = 0

        for concept in concepts:
            if concept.lower() in content.lower():
                covered += 1
            else:
                missing.append(concept)

        if not concepts:
            return 1.0, []

        coverage = covered / len(concepts)
        return coverage, missing

    def _extract_core_concepts(self, query: str) -> List[str]:
        """提取查询中的核心概念"""
        # 移除疑问词
        question_words = ['什么', '怎么', '如何', '为什么', '哪', '谁', '何时', '何地', '多少']
        cleaned = query
        for word in question_words:
            cleaned = cleaned.replace(word, '')

        # 提取名词性短语
        concepts = re.findall(r'[\u4e00-\u9fa5]{2,}|[A-Za-z]{2,}', cleaned)

        return [c for c in concepts if len(c) >= 2][:5]

    def _calculate_recency(self, query: str, metadata: Optional[Dict]) -> float:
        """计算时效性得分"""
        # 检查查询是否需要最新信息
        time_keywords = ['最新', '最近', '当前', '现在', '今年', '去年', '近期']
        needs_recent = any(kw in query for kw in time_keywords)

        if not needs_recent:
            return 1.0  # 不需要最新信息

        if not metadata:
            return 0.5  # 无元数据，假设中等时效性

        # 检查内容时间
        if 'date' in metadata or 'timestamp' in metadata:
            # 简化处理：假设有日期信息就是较新的
            return 0.8

        return 0.5

    def _calculate_authority(self, metadata: Optional[Dict]) -> float:
        """计算权威性得分"""
        if not metadata:
            return 0.5

        # 检查来源
        source = metadata.get('source', '').lower()

        # 高权威来源
        high_authority = ['官方', '政府', 'edu', 'gov', 'org', 'wikipedia']
        if any(auth in source for auth in high_authority):
            return 0.9

        # 中等权威来源
        medium_authority = ['news', 'blog', 'medium', 'zhihu']
        if any(auth in source for auth in medium_authority):
            return 0.7

        return 0.5

    def _determine_support_level(
        self,
        relevance_score: float,
        coverage_score: float,
        keyword_overlap: float
    ) -> SupportLevel:
        """确定支持级别"""
        # 降低阈值，更容易判定为相关
        if relevance_score >= 0.5 and coverage_score >= 0.4:
            return SupportLevel.FULLY_RELEVANT
        elif relevance_score >= 0.35 or (keyword_overlap >= 0.3 and coverage_score >= 0.3):
            return SupportLevel.PARTIALLY_RELEVANT
        elif relevance_score < 0.2 and keyword_overlap < 0.2:
            return SupportLevel.NOT_RELEVANT
        else:
            return SupportLevel.PARTIALLY_RELEVANT

    def _calculate_confidence(
        self,
        keyword_overlap: float,
        semantic_match: float,
        coverage: float
    ) -> float:
        """计算置信度"""
        # 置信度基于多个指标的一致性
        scores = [keyword_overlap, semantic_match, coverage]
        avg = sum(scores) / len(scores)
        variance = sum((s - avg) ** 2 for s in scores) / len(scores)

        # 方差越小，置信度越高
        consistency = 1.0 - min(variance * 2, 0.5)

        return (avg + consistency) / 2

    def _generate_reasoning(
        self,
        support_level: SupportLevel,
        relevance_score: float,
        coverage_score: float,
        key_matches: List[str],
        missing_aspects: List[str]
    ) -> str:
        """生成推理说明"""
        reasons = [f"支持级别: {support_level.value}"]
        reasons.append(f"相关性得分: {relevance_score:.2f}")
        reasons.append(f"覆盖度: {coverage_score:.2f}")

        if key_matches:
            reasons.append(f"匹配关键词: {', '.join(key_matches[:5])}")

        if missing_aspects:
            reasons.append(f"缺失方面: {', '.join(missing_aspects[:3])}")

        return "; ".join(reasons)

    def batch_predict(
        self,
        query: str,
        contents: List[str]
    ) -> List[SupportDecision]:
        """批量预测多个检索结果"""
        return [self.predict(query, content) for content in contents]

    def rank_by_relevance(
        self,
        query: str,
        contents: List[str]
    ) -> List[Tuple[int, float, str]]:
        """
        按相关性排序检索结果

        Returns:
            List[Tuple[int, float, str]]: (原始索引, 相关性得分, 内容摘要)
        """
        results = []
        for idx, content in enumerate(contents):
            decision = self.predict(query, content)
            summary = content[:100] + "..." if len(content) > 100 else content
            results.append((idx, decision.relevance_score, summary))

        # 按相关性降序排序
        results.sort(key=lambda x: x[1], reverse=True)
        return results


# 便捷函数
def is_supported(query: str, content: str, config: Optional[Dict] = None) -> Tuple[bool, float]:
    """
    便捷函数：判断检索结果是否相关

    Args:
        query: 用户查询
        content: 检索到的内容
        config: 可选配置

    Returns:
        Tuple[bool, float]: (是否相关, 置信度)
    """
    predictor = IsSUPPredictor(config)
    return predictor.is_supported(query, content)


if __name__ == "__main__":
    # 测试示例
    test_cases = [
        {
            "query": "什么是机器学习？",
            "content": "机器学习是人工智能的一个分支，它使计算机能够从数据中学习并做出决策或预测，而无需显式编程。机器学习算法通过训练数据构建模型，然后使用该模型对新数据进行预测。"
        },
        {
            "query": "Python如何安装？",
            "content": "Java是一种面向对象的编程语言，具有跨平台、安全性高等特点。Java广泛应用于企业级开发..."
        },
        {
            "query": "最新的iPhone价格是多少？",
            "content": "iPhone 15系列于2023年9月发布，起售价为5999元。iPhone 15 Pro Max最高配置售价为13999元。"
        }
    ]

    predictor = IsSUPPredictor()

    print("=" * 60)
    print("IsSUP Predictor 测试")
    print("=" * 60)

    for case in test_cases:
        decision = predictor.predict(case["query"], case["content"])
        print(f"\n查询: {case['query']}")
        print(f"内容: {case['content'][:50]}...")
        print(f"  是否相关: {decision.is_supported}")
        print(f"  置信度: {decision.confidence:.2%}")
        print(f"  支持级别: {decision.support_level.value}")
        print(f"  相关性得分: {decision.relevance_score:.2f}")
        print(f"  覆盖度: {decision.coverage_score:.2f}")
        print(f"  推理: {decision.reasoning}")
