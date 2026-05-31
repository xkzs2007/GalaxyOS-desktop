#!/usr/bin/env python3
"""
检索质量评估模块

2024-2026 行业关键功能：
- NDCG (Normalized Discounted Cumulative Gain)
- MRR (Mean Reciprocal Rank)
- RAG 评估 (RAGAS 框架)
- 端到端检索质量基准测试
"""

import math
import logging
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RelevanceJudgment:
    """相关性判断"""
    query_id: str
    doc_id: str
    relevance: int  # 0=不相关, 1=部分相关, 2=高度相关


@dataclass
class RetrievalEvalResult:
    """检索评估结果"""
    metric: str
    value: float
    details: str = ''
    per_query: Dict[str, float] = field(default_factory=dict)


class NDCGEvaluator:
    """
    NDCG (Normalized Discounted Cumulative Gain) 评估器

    衡量排序质量，考虑相关性等级和位置折扣。
    NDCG@k = DCG@k / IDCG@k
    """

    @staticmethod
    def dcg_at_k(relevances: List[int], k: int) -> float:
        """计算 DCG@k"""
        relevances = relevances[:k]
        if not relevances:
            return 0.0
        return sum(
            (2 ** rel - 1) / math.log2(i + 2)
            for i, rel in enumerate(relevances)
        )

    @staticmethod
    def ndcg_at_k(
        retrieved_relevances: List[int],
        ideal_relevances: List[int],
        k: int,
    ) -> float:
        """
        计算 NDCG@k

        Args:
            retrieved_relevances: 检索结果的相关性列表（按检索顺序）
            ideal_relevances: 理想排序的相关性列表
            k: 截断位置

        Returns:
            float: NDCG@k 值 [0, 1]
        """
        dcg = NDCGEvaluator.dcg_at_k(retrieved_relevances, k)
        idcg = NDCGEvaluator.dcg_at_k(sorted(ideal_relevances, reverse=True), k)
        if idcg == 0:
            return 0.0
        return dcg / idcg

    def evaluate(
        self,
        results: Dict[str, List[str]],           # query_id -> [doc_id, ...]
        judgments: List[RelevanceJudgment],
        k: int = 10,
    ) -> RetrievalEvalResult:
        """
        批量评估 NDCG@k

        Args:
            results: 检索结果 {query_id: [doc_id, ...]}
            judgments: 相关性判断列表
            k: 截断位置

        Returns:
            RetrievalEvalResult
        """
        # 构建查找表
        rel_map: Dict[str, Dict[str, int]] = {}
        for j in judgments:
            rel_map.setdefault(j.query_id, {})[j.doc_id] = j.relevance

        ndcg_values = {}
        for query_id, doc_ids in results.items():
            # 获取检索结果的相关性
            retrieved_rels = [rel_map.get(query_id, {}).get(did, 0) for did in doc_ids]
            # 获取理想排序
            ideal_rels = sorted(rel_map.get(query_id, {}).values(), reverse=True)

            ndcg_values[query_id] = self.ndcg_at_k(retrieved_rels, ideal_rels, k)

        avg = sum(ndcg_values.values()) / len(ndcg_values) if ndcg_values else 0.0

        return RetrievalEvalResult(
            metric=f'NDCG@{k}',
            value=round(avg, 4),
            details=f'共 {len(ndcg_values)} 个查询',
            per_query=ndcg_values,
        )


class MRREvaluator:
    """
    MRR (Mean Reciprocal Rank) 评估器

    衡量第一个相关结果的排名倒数的均值。
    """

    @staticmethod
    def reciprocal_rank(
        doc_ids: List[str],
        relevant_ids: set,
    ) -> float:
        """
        计算单个查询的 Reciprocal Rank

        Args:
            doc_ids: 检索结果文档 ID 列表
            relevant_ids: 相关文档 ID 集合

        Returns:
            float: RR 值
        """
        for i, doc_id in enumerate(doc_ids):
            if doc_id in relevant_ids:
                return 1.0 / (i + 1)
        return 0.0

    def evaluate(
        self,
        results: Dict[str, List[str]],
        judgments: List[RelevanceJudgment],
        min_relevance: int = 1,
    ) -> RetrievalEvalResult:
        """
        批量评估 MRR

        Args:
            results: 检索结果
            judgments: 相关性判断
            min_relevance: 最小相关性阈值

        Returns:
            RetrievalEvalResult
        """
        # 构建相关文档集合
        relevant_map: Dict[str, set] = {}
        for j in judgments:
            if j.relevance >= min_relevance:
                relevant_map.setdefault(j.query_id, set()).add(j.doc_id)

        rr_values = {}
        for query_id, doc_ids in results.items():
            rel_ids = relevant_map.get(query_id, set())
            rr_values[query_id] = self.reciprocal_rank(doc_ids, rel_ids)

        avg = sum(rr_values.values()) / len(rr_values) if rr_values else 0.0

        return RetrievalEvalResult(
            metric='MRR',
            value=round(avg, 4),
            details=f'共 {len(rr_values)} 个查询',
            per_query=rr_values,
        )


class RAGEvaluator:
    """
    RAG 评估器（RAGAS 风格）

    评估维度:
    1. Faithfulness (忠实度): 回复是否忠于检索上下文
    2. Relevancy (相关性): 回复是否与问题相关
    3. Context Precision (上下文精度): 检索的上下文是否精确
    4. Context Recall (上下文召回): 需要的信息是否都被检索到
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLM 客户端（用于 LLM-as-Judge 评估）
        """
        self.llm_client = llm_client

    def evaluate_faithfulness(
        self,
        query: str,
        context: str,
        response: str,
    ) -> Dict[str, Any]:
        """
        评估忠实度（无需 LLM 的简化版）

        通过检查回复中的关键声明是否在上下文中有支撑。

        Args:
            query: 查询
            context: 检索上下文
            response: 生成的回复

        Returns:
            Dict: 评估结果
        """
        # 简化实现：句子级覆盖检查
        response_sentences = [s.strip() for s in response.split('。') if s.strip() and len(s.strip()) > 5]
        if not response_sentences:
            response_sentences = [s.strip() for s in response.split('.') if s.strip() and len(s.strip()) > 5]

        if not response_sentences:
            return {'faithfulness': 1.0, 'details': '回复过短，无法评估'}

        # 检查每个回复句子是否在上下文中有支撑
        supported = 0
        for sent in response_sentences:
            # 关键词匹配（简化版）
            keywords = set(sent.lower().split()) & set(context.lower().split())
            overlap_ratio = len(keywords) / max(len(sent.split()), 1)
            if overlap_ratio >= 0.3:
                supported += 1

        faithfulness = supported / len(response_sentences) if response_sentences else 1.0

        return {
            'faithfulness': round(faithfulness, 3),
            'total_claims': len(response_sentences),
            'supported_claims': supported,
            'details': f'{supported}/{len(response_sentences)} 个声明有上下文支撑',
        }

    def evaluate_context_precision(
        self,
        query: str,
        contexts: List[str],
        relevant_indices: List[int],
    ) -> Dict[str, Any]:
        """
        评估上下文精度

        Args:
            query: 查询
            contexts: 检索到的上下文列表
            relevant_indices: 相关上下文的索引列表

        Returns:
            Dict: 评估结果
        """
        if not contexts:
            return {'context_precision': 0.0, 'details': '无上下文'}

        if not relevant_indices:
            return {'context_precision': 0.0, 'details': '无相关上下文'}

        relevant_set = set(relevant_indices)

        # 计算加权精度
        precision_sum = 0.0
        relevant_count = 0

        for i in range(len(contexts)):
            if i in relevant_set:
                relevant_count += 1
                precision_at_i = relevant_count / (i + 1)
                precision_sum += precision_at_i

        precision = precision_sum / len(relevant_set) if relevant_set else 0.0

        return {
            'context_precision': round(precision, 3),
            'total_contexts': len(contexts),
            'relevant_contexts': len(relevant_set),
            'details': f'{len(relevant_set)}/{len(contexts)} 个上下文相关',
        }

    def evaluate_context_recall(
        self,
        query: str,
        contexts: List[str],
        expected_answers: List[str],
    ) -> Dict[str, Any]:
        """
        评估上下文召回

        Args:
            query: 查询
            contexts: 检索到的上下文列表
            expected_answers: 期望答案中需要被覆盖的关键信息

        Returns:
            Dict: 评估结果
        """
        if not expected_answers:
            return {'context_recall': 1.0, 'details': '无期望答案'}

        # 检查每个期望答案是否在上下文中被覆盖
        covered = 0
        all_context = ' '.join(contexts).lower()

        for answer in expected_answers:
            # 关键词覆盖检查
            answer_keywords = set(answer.lower().split())
            context_keywords = set(all_context.split())
            overlap = answer_keywords & context_keywords
            if len(overlap) / max(len(answer_keywords), 1) >= 0.3:
                covered += 1

        recall = covered / len(expected_answers) if expected_answers else 1.0

        return {
            'context_recall': round(recall, 3),
            'total_expected': len(expected_answers),
            'covered': covered,
            'details': f'{covered}/{len(expected_answers)} 个关键信息被检索到',
        }

    def full_evaluation(
        self,
        query: str,
        contexts: List[str],
        response: str,
        relevant_context_indices: Optional[List[int]] = None,
        expected_answers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        完整 RAG 评估

        Args:
            query: 查询
            contexts: 检索上下文列表
            response: 生成的回复
            relevant_context_indices: 相关上下文索引
            expected_answers: 期望关键信息

        Returns:
            Dict: 完整评估结果
        """
        context_text = ' '.join(contexts)

        faithfulness = self.evaluate_faithfulness(query, context_text, response)

        context_precision = self.evaluate_context_precision(
            query, contexts, relevant_context_indices or []
        )

        context_recall = self.evaluate_context_recall(
            query, contexts, expected_answers or []
        )

        # 综合分数
        scores = [
            faithfulness['faithfulness'],
            context_precision['context_precision'],
            context_recall['context_recall'],
        ]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        return {
            'query': query,
            'faithfulness': faithfulness,
            'context_precision': context_precision,
            'context_recall': context_recall,
            'overall_score': round(avg_score, 3),
            'response_length': len(response),
            'num_contexts': len(contexts),
        }


# 全局实例
ndcg_evaluator = NDCGEvaluator()
mrr_evaluator = MRREvaluator()
rag_evaluator = RAGEvaluator()


# ============ 导出 ============

__all__ = [
    'NDCGEvaluator',
    'MRREvaluator',
    'RAGEvaluator',
    'RelevanceJudgment',
    'RetrievalEvalResult',
    'ndcg_evaluator',
    'mrr_evaluator',
    'rag_evaluator',
]


if __name__ == "__main__":
    print("=" * 60)
    print("   检索质量评估模块测试")
    print("=" * 60)

    # NDCG 测试
    print("\n1. NDCG 评估")
    print("-" * 40)

    results = {
        'q1': ['d1', 'd2', 'd3', 'd4', 'd5'],
        'q2': ['d3', 'd1', 'd5', 'd2', 'd4'],
    }
    judgments = [
        RelevanceJudgment('q1', 'd1', 2),
        RelevanceJudgment('q1', 'd2', 1),
        RelevanceJudgment('q1', 'd3', 0),
        RelevanceJudgment('q1', 'd4', 2),
        RelevanceJudgment('q1', 'd5', 1),
        RelevanceJudgment('q2', 'd1', 1),
        RelevanceJudgment('q2', 'd3', 2),
    ]
    result = ndcg_evaluator.evaluate(results, judgments, k=5)
    print(f"  {result.metric}: {result.value}")
    for qid, val in result.per_query.items():
        print(f"    {qid}: {val}")

    # MRR 测试
    print("\n2. MRR 评估")
    print("-" * 40)
    result = mrr_evaluator.evaluate(results, judgments)
    print(f"  {result.metric}: {result.value}")

    # RAG 评估测试
    print("\n3. RAG 评估")
    print("-" * 40)

    query = "什么是 RAG？"
    contexts = [
        "RAG（检索增强生成）是一种结合检索和生成的 AI 技术。",
        "RAG 通过检索外部知识来增强 LLM 的生成质量。",
        "向量搜索是 RAG 系统中常用的检索方法。",
    ]
    response = "RAG 是检索增强生成技术，它通过检索外部知识来增强大语言模型的生成质量，向量搜索是常用的检索方法。"

    result = rag_evaluator.full_evaluation(
        query=query,
        contexts=contexts,
        response=response,
        relevant_context_indices=[0, 1, 2],
        expected_answers=["检索增强生成", "外部知识", "向量搜索"],
    )

    print(f"  忠实度: {result['faithfulness']['faithfulness']}")
    print(f"  上下文精度: {result['context_precision']['context_precision']}")
    print(f"  上下文召回: {result['context_recall']['context_recall']}")
    print(f"  综合分数: {result['overall_score']}")

    print("\n" + "=" * 60)
