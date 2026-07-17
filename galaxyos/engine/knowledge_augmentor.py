#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Knowledge Augmentor - 知识补充器
基于 CRAG 论文实现，通过 Web 搜索补充知识

CRAG: https://arxiv.org/abs/2401.15884

核心功能：
1. 判断是否需要补充检索
2. 生成 Web 搜索查询
3. 整合补充知识
"""

import re
import json
import logging
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class AugmentReason(Enum):
    """补充原因"""
    INSUFFICIENT_COVERAGE = "insufficient_coverage"  # 覆盖度不足
    OUTDATED_INFO = "outdated_info"                  # 信息过时
    MISSING_ENTITIES = "missing_entities"            # 缺失实体
    LOW_CONFIDENCE = "low_confidence"                # 置信度低
    CONTRADICTORY = "contradictory"                  # 矛盾信息


@dataclass
class AugmentRequest:
    """补充请求"""
    reason: AugmentReason
    search_queries: List[str]
    priority: float
    context: str


@dataclass
class AugmentResult:
    """补充结果"""
    success: bool
    augmented_content: str
    sources: List[str]
    original_content: str
    added_info: List[str]
    confidence_boost: float


class KnowledgeAugmentor:
    """
    知识补充器

    基于 CRAG 论文的知识补充模块，当检索结果不足时，
    通过 Web 搜索等方式补充知识。

    触发条件：
    1. 检索结果覆盖度不足
    2. 信息过时
    3. 缺失关键实体
    4. 置信度过低
    5. 存在矛盾信息

    处理流程：
    1. 分析补充需求
    2. 生成搜索查询
    3. 执行 Web 搜索
    4. 整合补充知识
    """

    # 时间敏感关键词
    TIME_SENSITIVE_KEYWORDS = [
        '最新', '最近', '近期', '当前', '现在', '目前',
        '今天', '昨天', '本周', '本月', '今年', '去年',
        '新闻', '动态', '消息', '事件', '变化', '趋势'
    ]

    # 需要精确信息的模式
    PRECISION_PATTERNS = [
        r'(价格|费用|成本)',
        r'(时间|日期|期限)',
        r'(数量|人数|规模)',
        r'(排名|排行|榜单)',
        r'(版本|型号|规格)'
    ]

    def __init__(
        self,
        web_searcher: Optional[Callable] = None,
        config: Optional[Dict] = None
    ):
        """
        初始化知识补充器

        Args:
            web_searcher: Web 搜索函数，签名: (query: str) -> List[Dict]
            config: 配置字典
        """
        self.config = config or {}
        self.web_searcher = web_searcher or self._default_web_searcher

        # 补充阈值
        self.coverage_threshold = self.config.get('coverage_threshold', 0.5)
        self.confidence_threshold = self.config.get('confidence_threshold', 0.6)

        # 最大补充次数
        self.max_augments = self.config.get('max_augments', 2)

        logger.info("Knowledge Augmentor initialized")

    def analyze_and_augment(
        self,
        query: str,
        current_content: str,
        evaluation_result: Optional[Dict] = None
    ) -> AugmentResult:
        """
        分析并补充知识

        Args:
            query: 用户查询
            current_content: 当前内容
            evaluation_result: 评估结果（可选）

        Returns:
            AugmentResult: 补充结果
        """
        # 1. 分析是否需要补充
        augment_request = self._analyze_augment_need(
            query, current_content, evaluation_result
        )

        if not augment_request:
            # 不需要补充
            return AugmentResult(
                success=False,
                augmented_content=current_content,
                sources=[],
                original_content=current_content,
                added_info=[],
                confidence_boost=0.0
            )

        # 2. 执行补充
        return self._execute_augment(query, current_content, augment_request)

    def _analyze_augment_need(
        self,
        query: str,
        content: str,
        evaluation: Optional[Dict]
    ) -> Optional[AugmentRequest]:
        """分析是否需要补充"""
        reasons = []
        search_queries = []
        priority = 0.0

        # 检查时间敏感性
        if self._needs_fresh_info(query):
            reasons.append(AugmentReason.OUTDATED_INFO)
            search_queries.append(self._generate_time_sensitive_query(query))
            priority += 0.3

        # 检查覆盖度
        if evaluation:
            coverage = evaluation.get('coverage_score', 1.0)
            if coverage < self.coverage_threshold:
                reasons.append(AugmentReason.INSUFFICIENT_COVERAGE)
                search_queries.extend(self._generate_coverage_queries(query, evaluation))
                priority += 0.25

            confidence = evaluation.get('confidence', 1.0)
            if confidence < self.confidence_threshold:
                reasons.append(AugmentReason.LOW_CONFIDENCE)
                search_queries.append(query)
                priority += 0.2

        # 检查缺失实体
        missing_entities = self._find_missing_entities(query, content)
        if missing_entities:
            reasons.append(AugmentReason.MISSING_ENTITIES)
            for entity in missing_entities[:2]:
                search_queries.append(f"{query} {entity}")
            priority += 0.15

        # 检查精确信息需求
        if self._needs_precise_info(query):
            search_queries.append(self._generate_precise_query(query))
            priority += 0.1

        if not reasons:
            return None

        return AugmentRequest(
            reason=reasons[0],  # 主要原因
            search_queries=list(set(search_queries))[:3],
            priority=min(priority, 1.0),
            context=content[:500]
        )

    def _execute_augment(
        self,
        query: str,
        current_content: str,
        request: AugmentRequest
    ) -> AugmentResult:
        """执行补充"""
        all_new_info = []
        all_sources = []

        for search_query in request.search_queries[:self.max_augments]:
            try:
                # 执行 Web 搜索
                search_results = self.web_searcher(search_query)

                if search_results:
                    # 提取新信息
                    for result in search_results[:3]:
                        new_info = result.get('content', '') or result.get('snippet', '')
                        if new_info and new_info not in current_content:
                            all_new_info.append(new_info)

                        source = result.get('url', '') or result.get('source', '')
                        if source:
                            all_sources.append(source)

            except Exception as e:
                logger.warning(f"Web search failed for '{search_query}': {e}")

        if not all_new_info:
            return AugmentResult(
                success=False,
                augmented_content=current_content,
                sources=[],
                original_content=current_content,
                added_info=[],
                confidence_boost=0.0
            )

        # 整合新信息
        augmented_content = self._integrate_new_info(
            current_content, all_new_info, request.reason
        )

        # 计算置信度提升
        confidence_boost = min(len(all_new_info) * 0.1, 0.3)

        return AugmentResult(
            success=True,
            augmented_content=augmented_content,
            sources=list(set(all_sources)),
            original_content=current_content,
            added_info=all_new_info,
            confidence_boost=round(confidence_boost, 2)
        )

    def _needs_fresh_info(self, query: str) -> bool:
        """检查是否需要最新信息"""
        return any(kw in query for kw in self.TIME_SENSITIVE_KEYWORDS)

    def _needs_precise_info(self, query: str) -> bool:
        """检查是否需要精确信息"""
        return any(re.search(p, query) for p in self.PRECISION_PATTERNS)

    def _generate_time_sensitive_query(self, query: str) -> str:
        """生成时间敏感的搜索查询"""
        # 添加时间限定词
        import datetime
        current_year = datetime.datetime.now().year

        # 移除原有时间词，添加当前年份
        cleaned_query = query
        for kw in self.TIME_SENSITIVE_KEYWORDS:
            cleaned_query = cleaned_query.replace(kw, '')

        return f"{cleaned_query.strip()} {current_year}"

    def _generate_coverage_queries(
        self,
        query: str,
        evaluation: Dict
    ) -> List[str]:
        """生成覆盖度补充查询"""
        queries = []

        # 基于缺失方面生成查询
        missing = evaluation.get('missing_aspects', [])
        for aspect in missing[:2]:
            queries.append(f"{query} {aspect}")

        return queries

    def _generate_precise_query(self, query: str) -> str:
        """生成精确信息查询"""
        # 添加精确限定词
        return f"{query} 具体 数据"

    def _find_missing_entities(self, query: str, content: str) -> List[str]:
        """查找缺失的实体"""
        # 从查询中提取可能的实体
        potential_entities = re.findall(
            r'[\u4e00-\u9fa5]{2,}(?:公司|集团|机构|组织|大学|学院)',
            query
        )

        # 检查这些实体是否在内容中出现
        missing = []
        for entity in potential_entities:
            if entity not in content:
                missing.append(entity)

        return missing

    def _integrate_new_info(
        self,
        original: str,
        new_info: List[str],
        reason: AugmentReason
    ) -> str:
        """整合新信息"""
        if not new_info:
            return original

        # 根据补充原因决定整合方式
        if reason == AugmentReason.OUTDATED_INFO:
            # 更新信息：用新信息替换或补充
            prefix = "【最新信息】"
            integrated = f"{original}\n\n{prefix}\n" + "\n".join(new_info[:2])

        elif reason == AugmentReason.INSUFFICIENT_COVERAGE:
            # 补充覆盖：添加补充信息
            prefix = "【补充信息】"
            integrated = f"{original}\n\n{prefix}\n" + "\n".join(new_info[:2])

        else:
            # 默认：简单追加
            integrated = f"{original}\n\n" + "\n".join(new_info[:2])

        return integrated

    def _default_web_searcher(self, query: str) -> List[Dict]:
        """默认 Web 搜索器（占位）"""
        return [{
            'content': f"关于 '{query}' 的模拟搜索结果。",
            'url': 'https://example.com/search'
        }]

    def generate_search_queries(self, query: str, context: str) -> List[str]:
        """
        生成搜索查询

        Args:
            query: 用户查询
            context: 当前上下文

        Returns:
            List[str]: 搜索查询列表
        """
        queries = [query]  # 原始查询

        # 提取关键词生成变体查询
        keywords = self._extract_keywords(query)
        if keywords:
            # 关键词组合查询
            queries.append(' '.join(keywords[:3]))

        # 如果有时间敏感性，添加时间限定
        if self._needs_fresh_info(query):
            queries.append(self._generate_time_sensitive_query(query))

        return list(set(queries))[:3]

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        stopwords = {'的', '是', '在', '了', '和', '与', '或', '有', '我', '你', '他', '她', '它',
                     '这', '那', '什么', '怎么', '如何', '为什么', '哪', '吗', '呢', '吧'}

        words = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', text.lower())
        return [w for w in words if w not in stopwords and len(w) > 1]


# 便捷函数
def augment_knowledge(
    query: str,
    content: str,
    web_searcher: Optional[Callable] = None,
    evaluation: Optional[Dict] = None
) -> AugmentResult:
    """
    便捷函数：补充知识

    Args:
        query: 用户查询
        content: 当前内容
        web_searcher: Web 搜索函数
        evaluation: 评估结果

    Returns:
        AugmentResult: 补充结果
    """
    augmentor = KnowledgeAugmentor(web_searcher)
    return augmentor.analyze_and_augment(query, content, evaluation)


if __name__ == "__main__":
    # 测试示例
    def mock_web_searcher(query: str) -> List[Dict]:
        return [
            {
                'content': f"2024年{query}的最新进展显示...",
                'url': 'https://example.com/news'
            },
            {
                'content': f"关于{query}的详细数据分析...",
                'url': 'https://example.com/data'
            }
        ]

    augmentor = KnowledgeAugmentor(web_searcher=mock_web_searcher)

    test_cases = [
        {
            "query": "最新的AI发展趋势是什么？",
            "content": "人工智能在过去几年发展迅速，深度学习等技术取得突破。",
            "evaluation": {"coverage_score": 0.4, "confidence": 0.5}
        },
        {
            "query": "什么是机器学习？",
            "content": "机器学习是人工智能的一个分支，使计算机能够从数据中学习。",
            "evaluation": {"coverage_score": 0.8, "confidence": 0.9}
        }
    ]

    print("=" * 60)
    print("Knowledge Augmentor 测试")
    print("=" * 60)

    for case in test_cases:
        result = augmentor.analyze_and_augment(
            case["query"],
            case["content"],
            case.get("evaluation")
        )

        print(f"\n查询: {case['query']}")
        print(f"补充成功: {result.success}")
        print(f"新增信息: {len(result.added_info)}条")
        print(f"置信度提升: {result.confidence_boost:.2f}")
        if result.sources:
            print(f"来源: {result.sources}")
        print(f"补充后内容:\n{result.augmented_content[:200]}...")
