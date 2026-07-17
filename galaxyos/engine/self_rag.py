#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Self-RAG 主控制器
基于 Self-RAG 论文实现的完整检索增强生成框架

Self-RAG: https://arxiv.org/abs/2310.11511

核心流程：
1. IsREL: 判断是否需要检索
2. 检索: 从知识库获取相关内容
3. IsSUP: 评估检索结果相关性
4. 生成: 基于检索内容生成回答
5. IsUSE: 评估生成内容可靠性
6. 迭代优化: 根据评估结果调整
"""

import json
import logging
from typing import Dict, List, Optional, Tuple, Callable, Any
from dataclasses import dataclass, field
from enum import Enum

try:
    from .isrel_predictor import IsRELPredictor, RetrievalDecision
    from .issup_predictor import IsSUPPredictor, SupportDecision
    from .isuse_predictor import IsUSEPredictor, ReliabilityDecision
except ImportError:
    from isrel_predictor import IsRELPredictor, RetrievalDecision
    from issup_predictor import IsSUPPredictor, SupportDecision
    from isuse_predictor import IsUSEPredictor, ReliabilityDecision

logger = logging.getLogger(__name__)


class RAGState(Enum):
    """RAG 流程状态"""
    INIT = "init"
    RETRIEVAL_DECISION = "retrieval_decision"
    RETRIEVING = "retrieving"
    EVALUATING_RETRIEVAL = "evaluating_retrieval"
    GENERATING = "generating"
    EVALUATING_GENERATION = "evaluating_generation"
    REFINING = "refining"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RAGStep:
    """RAG 步骤记录"""
    state: RAGState
    input_data: Any
    output_data: Any
    decision: Optional[str] = None
    confidence: float = 0.0
    metadata: Dict = field(default_factory=dict)


@dataclass
class RAGResult:
    """RAG 最终结果"""
    query: str
    answer: str
    is_reliable: bool
    overall_confidence: float
    retrieval_used: bool
    iterations: int
    steps: List[RAGStep]
    sources: List[str]
    metadata: Dict = field(default_factory=dict)


class SelfRAG:
    """
    Self-RAG 主控制器

    实现完整的 Self-RAG 流程，包括：
    - 检索决策 (IsREL)
    - 检索结果评估 (IsSUP)
    - 生成评估 (IsUSE)
    - 迭代优化

    特点：
    1. 自适应检索：只在必要时检索
    2. 质量感知：评估每个环节的输出
    3. 迭代优化：不满意则重新检索或生成
    """

    def __init__(
        self,
        retriever: Optional[Callable] = None,
        generator: Optional[Callable] = None,
        config: Optional[Dict] = None
    ):
        """
        初始化 Self-RAG

        Args:
            retriever: 检索函数，签名: (query: str) -> List[str]
            generator: 生成函数，签名: (query: str, context: str) -> str
            config: 配置字典
        """
        self.config = config or {}

        # 初始化预测器
        self.isrel_predictor = IsRELPredictor(self.config.get('isrel', {}))
        self.issup_predictor = IsSUPPredictor(self.config.get('issup', {}))
        self.isuse_predictor = IsUSEPredictor(self.config.get('isuse', {}))

        # 设置检索器和生成器
        self.retriever = retriever or self._default_retriever
        self.generator = generator or self._default_generator

        # 配置参数
        self.max_iterations = self.config.get('max_iterations', 3)
        self.relevance_threshold = self.config.get('relevance_threshold', 0.6)
        self.reliability_threshold = self.config.get('reliability_threshold', 0.65)

        # 状态追踪
        self.current_state = RAGState.INIT
        self.steps: List[RAGStep] = []

        logger.info("Self-RAG initialized")

    def process(self, query: str, context: Optional[str] = None) -> RAGResult:
        """
        处理查询的主入口

        Args:
            query: 用户查询
            context: 可选的初始上下文

        Returns:
            RAGResult: 处理结果
        """
        self.steps = []
        iteration = 0
        retrieved_contents = []
        sources = []

        while iteration < self.max_iterations:
            iteration += 1
            logger.debug(f"Starting iteration {iteration}")

            # Step 1: 检索决策
            retrieval_decision = self._step_retrieval_decision(query, context)

            if not retrieval_decision.should_retrieve:
                # 不需要检索，直接生成
                answer = self._generate_without_retrieval(query, context)

                # 评估生成结果
                reliability = self._evaluate_generation(query, answer, context)

                if reliability.is_reliable:
                    return self._build_result(
                        query, answer, reliability,
                        False, iteration, sources
                    )
                else:
                    # 不可靠，尝试检索
                    logger.info("Generation unreliable, attempting retrieval")
                    retrieval_decision.should_retrieve = True

            # Step 2: 执行检索
            if retrieval_decision.should_retrieve:
                new_contents, new_sources = self._step_retrieve(query)
                retrieved_contents.extend(new_contents)
                sources.extend(new_sources)

                if not retrieved_contents:
                    # 检索失败，直接生成
                    answer = self._generate_without_retrieval(query, context)
                    reliability = self._evaluate_generation(query, answer, context)
                    return self._build_result(
                        query, answer, reliability,
                        True, iteration, sources
                    )

            # Step 3: 评估检索结果
            best_content, support_decision = self._step_evaluate_retrieval(
                query, retrieved_contents
            )

            if support_decision.support_level.value == "not_relevant":
                # 检索结果不相关
                if iteration >= self.max_iterations:
                    # 达到最大迭代次数
                    answer = self._generate_with_context(query, context or "")
                    reliability = self._evaluate_generation(query, answer, context)
                    return self._build_result(
                        query, answer, reliability,
                        True, iteration, sources
                    )
                continue  # 重新检索

            # Step 4: 生成回答
            combined_context = self._combine_context(context, best_content)
            answer = self._step_generate(query, combined_context)

            # Step 5: 评估生成结果
            reliability = self._evaluate_generation(query, answer, combined_context)

            if reliability.is_reliable:
                return self._build_result(
                    query, answer, reliability,
                    True, iteration, sources
                )

            # 不可靠，检查是否需要重新检索
            if "缺少外部知识支撑" in reliability.issues:
                logger.info("Generation lacks grounding, re-retrieving")
                continue

            # 尝试优化生成
            refined_answer = self._refine_answer(
                query, answer, combined_context, reliability
            )
            refined_reliability = self._evaluate_generation(
                query, refined_answer, combined_context
            )

            if refined_reliability.is_reliable:
                return self._build_result(
                    query, refined_answer, refined_reliability,
                    True, iteration, sources
                )

        # 达到最大迭代次数，返回最佳结果
        answer = self._generate_with_context(
            query,
            self._combine_context(context, retrieved_contents[0] if retrieved_contents else "")
        )
        reliability = self._evaluate_generation(query, answer, context)

        return self._build_result(
            query, answer, reliability,
            bool(retrieved_contents), iteration, sources
        )

    def _step_retrieval_decision(
        self,
        query: str,
        context: Optional[str]
    ) -> RetrievalDecision:
        """检索决策步骤"""
        self.current_state = RAGState.RETRIEVAL_DECISION

        decision = self.isrel_predictor.predict(query, context)

        step = RAGStep(
            state=self.current_state,
            input_data=query,
            output_data=decision,
            decision="retrieve" if decision.should_retrieve else "no_retrieve",
            confidence=decision.confidence
        )
        self.steps.append(step)

        logger.debug(f"Retrieval decision: {decision.should_retrieve} ({decision.confidence:.2f})")
        return decision

    def _step_retrieve(self, query: str) -> Tuple[List[str], List[str]]:
        """执行检索步骤"""
        self.current_state = RAGState.RETRIEVING

        try:
            results = self.retriever(query)

            # 支持返回带来源的结果
            if isinstance(results, tuple):
                contents, sources = results
            else:
                contents = results if isinstance(results, list) else [results]
                sources = [f"source_{i}" for i in range(len(contents))]

            step = RAGStep(
                state=self.current_state,
                input_data=query,
                output_data=f"Retrieved {len(contents)} documents",
                metadata={"num_results": len(contents)}
            )
            self.steps.append(step)

            logger.debug(f"Retrieved {len(contents)} documents")
            return contents, sources

        except Exception as e:
            logger.error(f"Retrieval failed: {e}")
            step = RAGStep(
                state=self.current_state,
                input_data=query,
                output_data=f"Error: {str(e)}",
                decision="failed"
            )
            self.steps.append(step)
            return [], []

    def _step_evaluate_retrieval(
        self,
        query: str,
        contents: List[str]
    ) -> Tuple[str, SupportDecision]:
        """评估检索结果步骤"""
        self.current_state = RAGState.EVALUATING_RETRIEVAL

        if not contents:
            from issup_predictor import SupportLevel
            return "", SupportDecision(
                is_supported=False,
                confidence=0.0,
                support_level=SupportLevel.NOT_RELEVANT,
                relevance_score=0.0,
                coverage_score=0.0,
                key_matches=[],
                missing_aspects=[],
                reasoning="No content to evaluate"
            )

        # 评估每个检索结果
        best_content = None
        best_decision = None
        best_score = -1

        for content in contents:
            decision = self.issup_predictor.predict(query, content)
            if decision.relevance_score > best_score:
                best_score = decision.relevance_score
                best_content = content
                best_decision = decision

        step = RAGStep(
            state=self.current_state,
            input_data=f"{len(contents)} documents",
            output_data=best_decision,
            decision=best_decision.support_level.value,
            confidence=best_decision.confidence
        )
        self.steps.append(step)

        logger.debug(f"Best retrieval score: {best_score:.2f}")
        return best_content, best_decision

    def _step_generate(self, query: str, context: str) -> str:
        """生成回答步骤"""
        self.current_state = RAGState.GENERATING

        try:
            answer = self.generator(query, context)

            step = RAGStep(
                state=self.current_state,
                input_data={"query": query, "context_length": len(context)},
                output_data=answer[:200] + "..." if len(answer) > 200 else answer
            )
            self.steps.append(step)

            logger.debug(f"Generated answer: {len(answer)} chars")
            return answer

        except Exception as e:
            logger.error(f"Generation failed: {e}")
            return f"抱歉，生成回答时出错: {str(e)}"

    def _evaluate_generation(
        self,
        query: str,
        answer: str,
        context: Optional[str]
    ) -> ReliabilityDecision:
        """评估生成结果"""
        self.current_state = RAGState.EVALUATING_GENERATION

        decision = self.isuse_predictor.predict(answer, query, context)

        step = RAGStep(
            state=self.current_state,
            input_data=answer[:100] + "...",
            output_data=decision,
            decision=decision.reliability_level.value,
            confidence=decision.confidence
        )
        self.steps.append(step)

        logger.debug(f"Generation reliability: {decision.is_reliable} ({decision.confidence:.2f})")
        return decision

    def _refine_answer(
        self,
        query: str,
        answer: str,
        context: str,
        reliability: ReliabilityDecision
    ) -> str:
        """优化回答"""
        self.current_state = RAGState.REFINING

        # 基于问题生成优化后的回答
        refined = answer

        # 添加来源说明（如果有上下文）
        if context and "来源" not in answer:
            refined = f"{answer}\n\n(基于检索资料整理)"

        # 添加不确定性声明（如果可靠性较低）
        if reliability.confidence < 0.7:
            refined = f"{refined}\n\n注：以上信息仅供参考，建议进一步核实。"

        step = RAGStep(
            state=self.current_state,
            input_data=answer[:100],
            output_data=refined[:100],
            decision="refined"
        )
        self.steps.append(step)

        return refined

    def _generate_without_retrieval(self, query: str, context: Optional[str]) -> str:
        """不使用检索直接生成"""
        return self.generator(query, context or "")

    def _generate_with_context(self, query: str, context: str) -> str:
        """使用上下文生成"""
        return self.generator(query, context)

    def _combine_context(self, base: Optional[str], new: str) -> str:
        """合并上下文"""
        if not base:
            return new
        if not new:
            return base
        return f"{base}\n\n{new}"

    def _build_result(
        self,
        query: str,
        answer: str,
        reliability: ReliabilityDecision,
        retrieval_used: bool,
        iterations: int,
        sources: List[str]
    ) -> RAGResult:
        """构建最终结果"""
        self.current_state = RAGState.COMPLETED

        return RAGResult(
            query=query,
            answer=answer,
            is_reliable=reliability.is_reliable,
            overall_confidence=reliability.confidence,
            retrieval_used=retrieval_used,
            iterations=iterations,
            steps=self.steps.copy(),
            sources=sources,
            metadata={
                "reliability_level": reliability.reliability_level.value,
                "factuality_score": reliability.factuality_score,
                "consistency_score": reliability.consistency_score,
                "grounding_score": reliability.grounding_score
            }
        )

    def _default_retriever(self, query: str) -> List[str]:
        """默认检索器（占位）"""
        return [f"这是关于 '{query}' 的模拟检索结果。"]

    def _default_generator(self, query: str, context: str) -> str:
        """默认生成器（占位）"""
        if context:
            return f"根据检索到的信息，关于 '{query}' 的回答是：{context[:200]}"
        return f"关于 '{query}'，这是一个基于知识的回答。"


def create_self_rag(
    retriever: Optional[Callable] = None,
    generator: Optional[Callable] = None,
    config: Optional[Dict] = None
) -> SelfRAG:
    """
    创建 Self-RAG 实例的工厂函数

    Args:
        retriever: 检索函数
        generator: 生成函数
        config: 配置字典

    Returns:
        SelfRAG: Self-RAG 实例
    """
    return SelfRAG(retriever, generator, config)


if __name__ == "__main__":
    # 测试示例
    def mock_retriever(query: str):
        return [
            "机器学习是人工智能的核心技术，通过算法让计算机从数据中学习。",
            "深度学习是机器学习的一个子领域，使用神经网络进行学习。"
        ]

    def mock_generator(query: str, context: str) -> str:
        if context:
            return f"根据资料，{context[:150]}"
        return f"关于{query}，这是一个直接回答。"

    rag = SelfRAG(
        retriever=mock_retriever,
        generator=mock_generator,
        config={'max_iterations': 2}
    )

    test_queries = [
        "什么是机器学习？",
        "写一首诗",
        "最新的AI发展情况如何？"
    ]

    print("=" * 60)
    print("Self-RAG 测试")
    print("=" * 60)

    for query in test_queries:
        result = rag.process(query)
        print(f"\n查询: {query}")
        print(f"回答: {result.answer[:100]}...")
        print(f"可靠: {result.is_reliable}")
        print(f"置信度: {result.overall_confidence:.2%}")
        print(f"使用检索: {result.retrieval_used}")
        print(f"迭代次数: {result.iterations}")
        print(f"步骤数: {len(result.steps)}")
