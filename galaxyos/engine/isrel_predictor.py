#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IsREL Predictor - 检索必要性预测器
基于 Self-RAG 论文实现，判断给定查询是否需要检索外部知识

Self-RAG: https://arxiv.org/abs/2310.11511
"""

import re
import json
import logging
from typing import Tuple, List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class QueryType(Enum):
    """查询类型枚举"""
    FACTUAL = "factual"           # 事实性问题，需要检索
    PROCEDURAL = "procedural"     # 步骤性问题，可能需要检索
    CREATIVE = "creative"         # 创意性问题，通常不需要检索
    OPINION = "opinion"           # 观点性问题，通常不需要检索
    CONVERSATIONAL = "conversational"  # 对话性问题，通常不需要检索
    MATHEMATICAL = "mathematical" # 数学计算，通常不需要检索
    CODING = "coding"             # 编程问题，可能需要检索
    AMBIGUOUS = "ambiguous"       # 模糊问题，需要检索澄清


@dataclass
class RetrievalDecision:
    """检索决策结果"""
    should_retrieve: bool
    confidence: float
    query_type: QueryType
    reasoning: str
    keywords: List[str]


class IsRELPredictor:
    """
    IsREL 预测器 - 判断是否需要检索
    
    基于 Self-RAG 论文的检索决策模块，通过分析查询特征判断是否需要
    从外部知识库检索信息来增强回答。
    
    核心判断逻辑：
    1. 事实性问题 → 需要检索
    2. 时间敏感问题 → 需要检索
    3. 特定实体问题 → 需要检索
    4. 创意/观点问题 → 不需要检索
    5. 简单计算/逻辑 → 不需要检索
    """

    # 需要检索的关键词模式
    RETRIEVAL_PATTERNS = [
        # 事实查询
        r'(什么是|介绍|解释|定义|概念)',
        r'(谁|哪位|哪个|哪些)',
        r'(什么时候|何时|时间)',
        r'(在哪里|何处|地点|位置)',
        r'(为什么|原因|为何)',
        r'(多少|数量|统计|数据)',

        # 最新信息
        r'(最新|最近|近期|当前|今年|去年)',
        r'(新闻|消息|动态|事件)',
        r'(现在|目前|当今)',

        # 特定实体
        r'(公司|企业|组织|机构)',
        r'(人物|名人|专家|作者)',
        r'(产品|服务|工具|软件)',
        r'(研究|论文|报告|文献)',

        # 比较和评价
        r'(比较|对比|区别|差异)',
        r'(评价|评测|怎么样|好不好)',
        r'(排名|排行|榜单)',

        # 操作指南
        r'(如何|怎么|怎样|方法|步骤)',
        r'(教程|指南|手册|文档)',

        # 问号结尾
        r'\?$',
        r'？$',
    ]

    # 不需要检索的模式
    NO_RETRIEVAL_PATTERNS = [
        # 创意写作
        r'(写一篇|创作|编故事|想象)',
        r'(小说|诗歌|散文|剧本)',

        # 观点讨论
        r'(你认为|你觉得|你的看法)',
        r'(观点|意见|想法)',

        # 简单计算
        r'(计算|算一下|等于多少)',
        r'(\d+\s*[\+\-\*\/]\s*\d+)',

        # 纯逻辑推理
        r'(如果.*那么|假设.*则)',

        # 个人问题
        r'(我|我的|我们|我们的)',
        r'(帮我|给我|为我)',
    ]

    # 时间敏感关键词
    TIME_SENSITIVE_KEYWORDS = [
        '最新', '最近', '近期', '当前', '现在', '目前',
        '今天', '昨天', '本周', '本月', '今年', '去年',
        '新闻', '动态', '消息', '事件', '变化'
    ]

    def __init__(self, config: Optional[Dict] = None):
        """
        初始化 IsREL 预测器
        
        Args:
            config: 配置字典，可包含：
                - confidence_threshold: 置信度阈值，默认 0.7
                - use_llm_fallback: 是否使用 LLM 作为后备，默认 False
        """
        self.config = config or {}
        self.confidence_threshold = self.config.get('confidence_threshold', 0.7)
        self.use_llm_fallback = self.config.get('use_llm_fallback', False)

        # 编译正则表达式以提高性能
        self._retrieval_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.RETRIEVAL_PATTERNS
        ]
        self._no_retrieval_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.NO_RETRIEVAL_PATTERNS
        ]

        logger.info(f"IsREL Predictor initialized with threshold={self.confidence_threshold}")

    def predict(self, query: str, context: Optional[str] = None) -> RetrievalDecision:
        """
        预测是否需要检索
        
        Args:
            query: 用户查询
            context: 可选的上下文信息
            
        Returns:
            RetrievalDecision: 检索决策结果
        """
        # 1. 分析查询类型
        query_type = self._classify_query_type(query)

        # 2. 计算检索得分
        retrieval_score, retrieval_matches = self._calculate_retrieval_score(query)
        no_retrieval_score, no_retrieval_matches = self._calculate_no_retrieval_score(query)

        # 3. 综合判断
        net_score = retrieval_score - no_retrieval_score

        # 4. 提取关键词
        keywords = self._extract_keywords(query)

        # 5. 生成推理说明
        reasoning = self._generate_reasoning(
            query_type, retrieval_matches, no_retrieval_matches, net_score
        )

        # 6. 最终决策
        if net_score > 0.3:
            should_retrieve = True
            confidence = min(0.95, 0.6 + net_score)
        elif net_score < -0.3:
            should_retrieve = False
            confidence = min(0.95, 0.6 - net_score)
        else:
            # 边界情况，根据查询类型决定
            should_retrieve = query_type in [QueryType.FACTUAL, QueryType.AMBIGUOUS]
            confidence = 0.5 + abs(net_score)

        return RetrievalDecision(
            should_retrieve=should_retrieve,
            confidence=round(confidence, 3),
            query_type=query_type,
            reasoning=reasoning,
            keywords=keywords
        )

    def should_retrieve(self, query: str, context: Optional[str] = None) -> Tuple[bool, float]:
        """
        简化接口：判断是否需要检索
        
        Args:
            query: 用户查询
            context: 可选的上下文
            
        Returns:
            Tuple[bool, float]: (是否需要检索, 置信度)
        """
        decision = self.predict(query, context)
        return decision.should_retrieve, decision.confidence

    def _classify_query_type(self, query: str) -> QueryType:
        """分类查询类型"""
        query_lower = query.lower()

        # 检查数学计算
        if re.search(r'\d+\s*[\+\-\*\/\^]\s*\d+', query) or \
           re.search(r'(计算|等于|求值)', query):
            return QueryType.MATHEMATICAL

        # 检查编程问题
        if re.search(r'(代码|函数|类|方法|bug|error|exception|api)', query_lower):
            return QueryType.CODING

        # 检查创意写作
        if re.search(r'(写|创作|编|故事|小说|诗歌|文章)', query) and \
           not re.search(r'(如何|怎么|方法)', query):
            return QueryType.CREATIVE

        # 检查观点问题
        if re.search(r'(你认为|你觉得|看法|观点|意见)', query):
            return QueryType.OPINION

        # 检查对话性问题
        if re.search(r'^(你好|嗨|hi|hello|谢谢|再见|好的)$', query_lower):
            return QueryType.CONVERSATIONAL

        # 检查步骤性问题
        if re.search(r'(如何|怎么|怎样|步骤|方法|教程)', query):
            return QueryType.PROCEDURAL

        # 检查模糊问题
        if len(query) < 5 or re.search(r'(它|这个|那个)$', query):
            return QueryType.AMBIGUOUS

        # 默认为事实性问题
        return QueryType.FACTUAL

    def _calculate_retrieval_score(self, query: str) -> Tuple[float, List[str]]:
        """计算检索得分"""
        matches = []
        score = 0.0

        for pattern in self._retrieval_patterns:
            if pattern.search(query):
                matches.append(pattern.pattern)
                score += 0.20  # 提高每个匹配的得分

        # 时间敏感性加分
        for keyword in self.TIME_SENSITIVE_KEYWORDS:
            if keyword in query:
                score += 0.25
                break

        # 特定实体加分
        if re.search(r'[A-Z][a-z]+', query):  # 英文专有名词
            score += 0.15

        # 问号加分
        if '?' in query or '？' in query:
            score += 0.15

        # 事实性问题加分
        if re.search(r'(什么是|是谁|在哪|何时|为什么|如何)', query):
            score += 0.20

        return min(score, 1.0), matches

    def _calculate_no_retrieval_score(self, query: str) -> Tuple[float, List[str]]:
        """计算不检索得分"""
        matches = []
        score = 0.0

        for pattern in self._no_retrieval_patterns:
            if pattern.search(query):
                matches.append(pattern.pattern)
                score += 0.2

        # 短查询减分（可能是闲聊）
        if len(query) < 10:
            score += 0.15

        return min(score, 1.0), matches

    def _extract_keywords(self, query: str) -> List[str]:
        """提取关键词"""
        # 移除停用词
        stopwords = {'的', '是', '在', '了', '和', '与', '或', '有', '我', '你', '他', '她', '它'}

        # 简单分词（实际应用中可使用更复杂的分词器）
        words = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+|\d+', query)

        keywords = [
            w for w in words
            if w not in stopwords and len(w) > 1
        ]

        return keywords[:10]  # 最多返回10个关键词

    def _generate_reasoning(
        self,
        query_type: QueryType,
        retrieval_matches: List[str],
        no_retrieval_matches: List[str],
        net_score: float
    ) -> str:
        """生成推理说明"""
        reasons = []

        reasons.append(f"查询类型: {query_type.value}")

        if retrieval_matches:
            reasons.append(f"检索信号: 匹配{len(retrieval_matches)}个模式")

        if no_retrieval_matches:
            reasons.append(f"不检索信号: 匹配{len(no_retrieval_matches)}个模式")

        if net_score > 0:
            reasons.append("倾向于检索外部知识")
        elif net_score < 0:
            reasons.append("倾向于直接回答")
        else:
            reasons.append("边界情况，需综合判断")

        return "; ".join(reasons)

    def batch_predict(self, queries: List[str]) -> List[RetrievalDecision]:
        """批量预测"""
        return [self.predict(q) for q in queries]


# 便捷函数
def should_retrieve(query: str, config: Optional[Dict] = None) -> Tuple[bool, float]:
    """
    便捷函数：判断是否需要检索
    
    Args:
        query: 用户查询
        config: 可选配置
        
    Returns:
        Tuple[bool, float]: (是否需要检索, 置信度)
    """
    predictor = IsRELPredictor(config)
    return predictor.should_retrieve(query)


if __name__ == "__main__":
    # 测试示例
    test_queries = [
        "什么是机器学习？",
        "写一首关于春天的诗",
        "最新的iPhone价格是多少？",
        "计算 123 + 456",
        "你认为人工智能会取代人类吗？",
        "如何学习Python编程？",
        "你好",
        "马斯克是谁？",
    ]

    predictor = IsRELPredictor()

    print("=" * 60)
    print("IsREL Predictor 测试")
    print("=" * 60)

    for query in test_queries:
        decision = predictor.predict(query)
        print(f"\n查询: {query}")
        print(f"  需要检索: {decision.should_retrieve}")
        print(f"  置信度: {decision.confidence:.2%}")
        print(f"  类型: {decision.query_type.value}")
        print(f"  关键词: {decision.keywords}")
        print(f"  推理: {decision.reasoning}")
