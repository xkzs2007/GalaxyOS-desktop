#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Retrieval Evaluator - 检索评估器
基于 CRAG 论文实现，评估检索结果的质量

CRAG: https://arxiv.org/abs/2401.15884

核心功能：
1. 检索结果质量评估
2. 相关性打分
3. 决策：使用/丢弃/补充检索
"""

import re
import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class RetrievalAction(Enum):
    """检索评估后的行动"""
    USE = "use"                    # 直接使用
    DISCARD = "discard"            # 丢弃
    AUGMENT = "augment"            # 补充检索
    REFINE = "refine"              # 精炼后使用
    FALLBACK = "fallback"          # 回退到知识库


@dataclass
class RetrievalScore:
    """检索得分详情"""
    overall_score: float
    relevance_score: float
    coverage_score: float
    freshness_score: float
    authority_score: float
    coherence_score: float


@dataclass
class EvaluationResult:
    """评估结果"""
    action: RetrievalAction
    score: RetrievalScore
    confidence: float
    selected_indices: List[int]
    discard_indices: List[int]
    reasoning: str
    suggestions: List[str]


class RetrievalEvaluator:
    """
    检索评估器
    
    基于 CRAG 论文的检索评估模块，对检索结果进行细粒度评估，
    决定如何处理检索到的内容。
    
    评估维度：
    1. 相关性：内容与查询的相关程度
    2. 覆盖度：内容覆盖查询各方面的程度
    3. 新鲜度：内容的时效性
    4. 权威性：内容来源的可信度
    5. 连贯性：多个检索结果之间的一致性
    """
    
    # 评分权重
    SCORE_WEIGHTS = {
        'relevance': 0.35,
        'coverage': 0.25,
        'freshness': 0.15,
        'authority': 0.15,
        'coherence': 0.10
    }
    
    # 决策阈值
    THRESHOLDS = {
        'use': 0.75,        # 高于此分数直接使用
        'discard': 0.35,    # 低于此分数丢弃
        'augment': 0.55,    # 低于此分数需要补充
    }
    
    def __init__(self, config: Optional[Dict] = None):
        """
        初始化检索评估器
        
        Args:
            config: 配置字典
        """
        self.config = config or {}
        
        # 自定义阈值
        self.thresholds = self.config.get('thresholds', self.THRESHOLDS)
        
        # 权威来源列表
        self.authoritative_sources = self.config.get(
            'authoritative_sources',
            ['wikipedia.org', 'edu', 'gov', 'official', '官网']
        )
        
        logger.info("Retrieval Evaluator initialized")
    
    def evaluate(
        self,
        query: str,
        retrieved_docs: List[str],
        metadata: Optional[List[Dict]] = None
    ) -> EvaluationResult:
        """
        评估检索结果
        
        Args:
            query: 用户查询
            retrieved_docs: 检索到的文档列表
            metadata: 文档元数据列表
            
        Returns:
            EvaluationResult: 评估结果
        """
        if not retrieved_docs:
            return self._empty_result("No documents to evaluate")
        
        # 1. 计算各项得分
        relevance_scores = self._calculate_relevance_scores(query, retrieved_docs)
        coverage_score = self._calculate_coverage_score(query, retrieved_docs)
        freshness_score = self._calculate_freshness_scores(query, metadata, len(retrieved_docs))
        authority_scores = self._calculate_authority_scores(metadata, len(retrieved_docs))
        coherence_score = self._calculate_coherence_score(retrieved_docs)
        
        # 2. 计算综合得分
        overall_scores = []
        for i in range(len(retrieved_docs)):
            score = (
                self.SCORE_WEIGHTS['relevance'] * relevance_scores[i] +
                self.SCORE_WEIGHTS['coverage'] * coverage_score +
                self.SCORE_WEIGHTS['freshness'] * freshness_score[i] +
                self.SCORE_WEIGHTS['authority'] * authority_scores[i] +
                self.SCORE_WEIGHTS['coherence'] * coherence_score
            )
            overall_scores.append(score)
        
        # 3. 选择和丢弃文档
        selected_indices, discard_indices = self._select_documents(
            overall_scores, relevance_scores
        )
        
        # 4. 计算平均得分
        avg_score = sum(overall_scores) / len(overall_scores)
        
        retrieval_score = RetrievalScore(
            overall_score=round(avg_score, 3),
            relevance_score=round(sum(relevance_scores) / len(relevance_scores), 3),
            coverage_score=round(coverage_score, 3),
            freshness_score=round(sum(freshness_score) / len(freshness_score), 3),
            authority_score=round(sum(authority_scores) / len(authority_scores), 3),
            coherence_score=round(coherence_score, 3)
        )
        
        # 5. 决定行动
        action = self._determine_action(avg_score, selected_indices)
        
        # 6. 生成建议
        suggestions = self._generate_suggestions(
            action, retrieval_score, selected_indices, discard_indices
        )
        
        # 7. 生成推理说明
        reasoning = self._generate_reasoning(
            action, retrieval_score, selected_indices, discard_indices
        )
        
        # 8. 计算置信度
        confidence = self._calculate_confidence(relevance_scores, coherence_score)
        
        return EvaluationResult(
            action=action,
            score=retrieval_score,
            confidence=round(confidence, 3),
            selected_indices=selected_indices,
            discard_indices=discard_indices,
            reasoning=reasoning,
            suggestions=suggestions
        )
    
    def _calculate_relevance_scores(
        self, 
        query: str, 
        docs: List[str]
    ) -> List[float]:
        """计算每个文档的相关性得分"""
        scores = []
        
        # 提取查询关键词
        query_keywords = self._extract_keywords(query)
        
        for doc in docs:
            # 关键词匹配
            doc_keywords = self._extract_keywords(doc)
            keyword_overlap = len(set(query_keywords) & set(doc_keywords))
            keyword_score = keyword_overlap / max(len(query_keywords), 1)
            
            # 语义匹配（简化版）
            semantic_score = self._semantic_match_score(query, doc)
            
            # 综合得分
            score = 0.6 * keyword_score + 0.4 * semantic_score
            scores.append(min(score, 1.0))
        
        return scores
    
    def _calculate_coverage_score(
        self, 
        query: str, 
        docs: List[str]
    ) -> float:
        """计算覆盖度得分"""
        # 提取查询的核心概念
        concepts = self._extract_core_concepts(query)
        
        if not concepts:
            return 1.0
        
        # 合并所有文档
        combined_text = " ".join(docs).lower()
        
        # 计算覆盖的概念比例
        covered = sum(1 for c in concepts if c.lower() in combined_text)
        
        return covered / len(concepts)
    
    def _calculate_freshness_scores(
        self, 
        query: str, 
        metadata: Optional[List[Dict]],
        num_docs: int
    ) -> List[float]:
        """计算新鲜度得分"""
        # 检查查询是否需要最新信息
        time_keywords = ['最新', '最近', '当前', '现在', '今年', '近期', '新闻']
        needs_fresh = any(kw in query for kw in time_keywords)
        
        if not needs_fresh:
            return [1.0] * num_docs
        
        if not metadata or len(metadata) < num_docs:
            # 无元数据或元数据不足，填充默认值
            if metadata:
                scores = []
                for i in range(num_docs):
                    if i < len(metadata):
                        meta = metadata[i]
                        if 'date' in meta or 'timestamp' in meta:
                            scores.append(0.8)
                        else:
                            scores.append(0.5)
                    else:
                        scores.append(0.5)
                return scores
            return [0.5] * num_docs
        
        scores = []
        for meta in metadata[:num_docs]:
            if 'date' in meta or 'timestamp' in meta:
                # 有日期信息，假设较新
                scores.append(0.8)
            else:
                scores.append(0.5)
        
        return scores
    
    def _calculate_authority_scores(
        self, 
        metadata: Optional[List[Dict]],
        num_docs: int
    ) -> List[float]:
        """计算权威性得分"""
        if not metadata or len(metadata) < num_docs:
            return [0.5] * num_docs
        
        scores = []
        for meta in metadata[:num_docs]:
            source = meta.get('source', '').lower()
            
            # 检查是否来自权威来源
            is_authoritative = any(
                auth in source for auth in self.authoritative_sources
            )
            
            if is_authoritative:
                scores.append(0.9)
            elif source:
                scores.append(0.6)
            else:
                scores.append(0.5)
        
        # 确保返回正确数量的分数
        while len(scores) < num_docs:
            scores.append(0.5)
        
        return scores[:num_docs]
    
    def _calculate_coherence_score(self, docs: List[str]) -> float:
        """计算连贯性得分"""
        if len(docs) <= 1:
            return 1.0
        
        # 检查文档之间的一致性
        # 简化版：检查是否有矛盾的关键词
        contradiction_pairs = [
            ('是', '不是'),
            ('有', '没有'),
            ('正确', '错误'),
            ('真', '假')
        ]
        
        combined = " ".join(docs)
        contradictions = 0
        
        for word1, word2 in contradiction_pairs:
            if word1 in combined and word2 in combined:
                contradictions += 1
        
        # 矛盾越多，连贯性越低
        score = 1.0 - min(contradictions * 0.2, 0.5)
        
        return score
    
    def _select_documents(
        self, 
        overall_scores: List[float],
        relevance_scores: List[float]
    ) -> Tuple[List[int], List[int]]:
        """选择和丢弃文档"""
        selected = []
        discarded = []
        
        for i, (overall, relevance) in enumerate(zip(overall_scores, relevance_scores)):
            if overall >= self.thresholds['discard'] and relevance >= 0.3:
                selected.append(i)
            else:
                discarded.append(i)
        
        # 如果全部被丢弃，保留得分最高的一个
        if not selected and overall_scores:
            best_idx = overall_scores.index(max(overall_scores))
            selected.append(best_idx)
            discarded.remove(best_idx)
        
        return selected, discarded
    
    def _determine_action(
        self, 
        avg_score: float, 
        selected_indices: List[int]
    ) -> RetrievalAction:
        """决定行动"""
        if avg_score >= self.thresholds['use']:
            return RetrievalAction.USE
        elif avg_score < self.thresholds['discard']:
            return RetrievalAction.DISCARD
        elif avg_score < self.thresholds['augment']:
            return RetrievalAction.AUGMENT
        elif len(selected_indices) == 0:
            return RetrievalAction.FALLBACK
        else:
            return RetrievalAction.REFINE
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        stopwords = {'的', '是', '在', '了', '和', '与', '或', '有', '我', '你', '他', '她', '它',
                     '这', '那', '什么', '怎么', '如何', '为什么', '哪', '吗', '呢', '吧', '一个', '一些'}
        
        words = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', text.lower())
        
        return [w for w in words if w not in stopwords and len(w) > 1]
    
    def _extract_core_concepts(self, query: str) -> List[str]:
        """提取核心概念"""
        # 移除疑问词
        question_words = ['什么', '怎么', '如何', '为什么', '哪', '谁', '何时', '何地', '多少']
        cleaned = query
        for word in question_words:
            cleaned = cleaned.replace(word, '')
        
        # 提取名词性短语
        concepts = re.findall(r'[\u4e00-\u9fa5]{2,}|[A-Za-z]{2,}', cleaned)
        
        return [c for c in concepts if len(c) >= 2][:5]
    
    def _semantic_match_score(self, query: str, doc: str) -> float:
        """语义匹配得分（简化版）"""
        query_lower = query.lower()
        doc_lower = doc.lower()
        
        # 检查问题类型匹配
        score = 0.0
        
        # 定义问题类型及其对应的内容指示词
        patterns = {
            '定义': (['什么是', '定义'], ['是指', '定义为', '是一种']),
            '原因': (['为什么', '原因'], ['因为', '原因是', '由于']),
            '方法': (['如何', '怎么', '方法'], ['方法', '步骤', '首先']),
            '比较': (['区别', '差异', '比较'], ['不同', '区别', '相比'])
        }
        
        for qtype, (query_patterns, doc_patterns) in patterns.items():
            if any(p in query_lower for p in query_patterns):
                if any(p in doc_lower for p in doc_patterns):
                    score += 0.25
        
        return min(score, 1.0)
    
    def _generate_suggestions(
        self,
        action: RetrievalAction,
        score: RetrievalScore,
        selected: List[int],
        discarded: List[int]
    ) -> List[str]:
        """生成建议"""
        suggestions = []
        
        if action == RetrievalAction.DISCARD:
            suggestions.append("检索结果质量过低，建议重新检索或使用Web搜索")
        
        elif action == RetrievalAction.AUGMENT:
            suggestions.append("检索结果不够充分，建议补充检索")
            if score.coverage_score < 0.5:
                suggestions.append("覆盖度不足，建议扩展查询关键词")
        
        elif action == RetrievalAction.REFINE:
            suggestions.append("建议精炼检索结果，去除无关内容")
        
        if score.freshness_score < 0.6:
            suggestions.append("内容时效性不足，建议检索最新资料")
        
        if score.authority_score < 0.5:
            suggestions.append("来源权威性较低，建议核实信息")
        
        return suggestions
    
    def _generate_reasoning(
        self,
        action: RetrievalAction,
        score: RetrievalScore,
        selected: List[int],
        discarded: List[int]
    ) -> str:
        """生成推理说明"""
        parts = [
            f"行动: {action.value}",
            f"综合得分: {score.overall_score:.2f}",
            f"相关性: {score.relevance_score:.2f}",
            f"覆盖度: {score.coverage_score:.2f}",
            f"选中文档: {len(selected)}个",
            f"丢弃文档: {len(discarded)}个"
        ]
        
        return "; ".join(parts)
    
    def _calculate_confidence(
        self, 
        relevance_scores: List[float], 
        coherence_score: float
    ) -> float:
        """计算置信度"""
        if not relevance_scores:
            return 0.5
        
        avg_relevance = sum(relevance_scores) / len(relevance_scores)
        
        # 得分方差越小，置信度越高
        variance = sum((s - avg_relevance) ** 2 for s in relevance_scores) / len(relevance_scores)
        consistency = 1.0 - min(variance * 2, 0.4)
        
        return (avg_relevance + coherence_score + consistency) / 3
    
    def _empty_result(self, reason: str) -> EvaluationResult:
        """返回空结果"""
        return EvaluationResult(
            action=RetrievalAction.FALLBACK,
            score=RetrievalScore(0, 0, 0, 0, 0, 0),
            confidence=0.0,
            selected_indices=[],
            discard_indices=[],
            reasoning=reason,
            suggestions=["无检索结果，建议使用其他方式获取信息"]
        )
    
    def get_selected_docs(
        self, 
        docs: List[str], 
        result: EvaluationResult
    ) -> List[str]:
        """获取选中的文档"""
        return [docs[i] for i in result.selected_indices if i < len(docs)]


# 便捷函数
def evaluate_retrieval(
    query: str, 
    docs: List[str],
    metadata: Optional[List[Dict]] = None
) -> EvaluationResult:
    """
    便捷函数：评估检索结果
    
    Args:
        query: 用户查询
        docs: 检索到的文档
        metadata: 文档元数据
        
    Returns:
        EvaluationResult: 评估结果
    """
    evaluator = RetrievalEvaluator()
    return evaluator.evaluate(query, docs, metadata)


if __name__ == "__main__":
    # 测试示例
    test_cases = [
        {
            "query": "什么是机器学习？",
            "docs": [
                "机器学习是人工智能的一个分支，它使计算机能够从数据中学习。",
                "深度学习是机器学习的子领域，使用神经网络进行学习。",
                "Python是一种流行的编程语言，广泛用于机器学习开发。"
            ]
        },
        {
            "query": "最新的AI发展趋势是什么？",
            "docs": [
                "2023年，大语言模型取得了重大突破。",
                "AI绘画工具在艺术创作领域引发热议。",
                "自动驾驶技术在2020年开始商业化应用。"
            ]
        }
    ]
    
    evaluator = RetrievalEvaluator()
    
    print("=" * 60)
    print("Retrieval Evaluator 测试")
    print("=" * 60)
    
    for case in test_cases:
        result = evaluator.evaluate(case["query"], case["docs"])
        print(f"\n查询: {case['query']}")
        print(f"行动: {result.action.value}")
        print(f"综合得分: {result.score.overall_score:.2f}")
        print(f"相关性: {result.score.relevance_score:.2f}")
        print(f"覆盖度: {result.score.coverage_score:.2f}")
        print(f"选中文档: {result.selected_indices}")
        print(f"丢弃文档: {result.discard_indices}")
        print(f"建议: {result.suggestions}")
