#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CRAG 主控制器
基于 CRAG 论文实现的纠错检索增强生成框架

CRAG: https://arxiv.org/abs/2401.15884

核心流程：
1. 检索：从知识库获取相关内容
2. 评估：评估检索结果质量
3. 决策：使用/丢弃/补充/精炼
4. 知识精炼：提取关键信息
5. 知识补充：Web 搜索补充
6. 生成：基于精炼知识生成回答
"""

import json
import logging
from typing import Dict, List, Optional, Tuple, Callable, Any
from dataclasses import dataclass, field
from enum import Enum

try:
    from .retrieval_evaluator import RetrievalEvaluator, EvaluationResult, RetrievalAction
    from .knowledge_refiner import KnowledgeRefiner, RefinedKnowledge
    from .knowledge_augmentor import KnowledgeAugmentor, AugmentResult
except ImportError:
    from retrieval_evaluator import RetrievalEvaluator, EvaluationResult, RetrievalAction
    from knowledge_refiner import KnowledgeRefiner, RefinedKnowledge
    from knowledge_augmentor import KnowledgeAugmentor, AugmentResult

logger = logging.getLogger(__name__)


class CRAGState(Enum):
    """CRAG 流程状态"""
    INIT = "init"
    RETRIEVING = "retrieving"
    EVALUATING = "evaluating"
    DECIDING = "deciding"
    REFINING = "refining"
    AUGMENTING = "augmenting"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CRAGStep:
    """CRAG 步骤记录"""
    state: CRAGState
    action: str
    input_data: Any
    output_data: Any
    confidence: float = 0.0
    metadata: Dict = field(default_factory=dict)


@dataclass
class CRAGResult:
    """CRAG 最终结果"""
    query: str
    answer: str
    confidence: float
    action_taken: RetrievalAction
    refined_knowledge: Optional[RefinedKnowledge]
    augmented: bool
    sources: List[str]
    steps: List[CRAGStep]
    metadata: Dict = field(default_factory=dict)


class CRAG:
    """
    CRAG 主控制器

    实现完整的 CRAG 流程，包括：
    - 检索评估
    - 知识精炼
    - 知识补充
    - 自适应决策

    特点：
    1. 质量感知：评估检索结果质量
    2. 纠错机制：低质量结果触发补充
    3. 知识精炼：提取关键信息
    4. 灵活决策：根据情况选择最优策略
    """

    def __init__(
        self,
        retriever: Optional[Callable] = None,
        generator: Optional[Callable] = None,
        web_searcher: Optional[Callable] = None,
        config: Optional[Dict] = None
    ):
        """
        初始化 CRAG

        Args:
            retriever: 检索函数，签名: (query: str) -> List[str]
            generator: 生成函数，签名: (query: str, context: str) -> str
            web_searcher: Web 搜索函数，签名: (query: str) -> List[Dict]
            config: 配置字典
        """
        self.config = config or {}

        # 初始化组件
        self.evaluator = RetrievalEvaluator(self.config.get('evaluator', {}))
        self.refiner = KnowledgeRefiner(self.config.get('refiner', {}))
        self.augmentor = KnowledgeAugmentor(
            web_searcher,
            self.config.get('augmentor', {})
        )

        # 设置检索器和生成器
        self.retriever = retriever or self._default_retriever
        self.generator = generator or self._default_generator

        # 配置参数
        self.max_refinement_iterations = self.config.get('max_refinement_iterations', 2)
        self.enable_augmentation = self.config.get('enable_augmentation', True)

        # 状态追踪
        self.current_state = CRAGState.INIT
        self.steps: List[CRAGStep] = []

        logger.info("CRAG initialized")

    def process(self, query: str) -> CRAGResult:
        """
        处理查询的主入口

        Args:
            query: 用户查询

        Returns:
            CRAGResult: 处理结果
        """
        self.steps = []
        sources = []
        augmented = False
        refined_knowledge = None

        # Step 1: 检索
        docs, doc_sources = self._step_retrieve(query)
        sources.extend(doc_sources)

        if not docs:
            # 检索失败，尝试 Web 搜索
            if self.enable_augmentation:
                return self._fallback_to_web_search(query)
            return self._direct_generate(query)

        # Step 2: 评估检索结果
        eval_result = self._step_evaluate(query, docs)

        # Step 3: 根据评估结果决策
        action = eval_result.action

        if action == RetrievalAction.DISCARD:
            # 丢弃检索结果，使用 Web 搜索
            if self.enable_augmentation:
                return self._fallback_to_web_search(query)
            return self._direct_generate(query)

        elif action == RetrievalAction.AUGMENT:
            # 补充检索
            docs, sources, augmented = self._step_augment(query, docs, sources, eval_result)

        elif action == RetrievalAction.REFINE:
            # 精炼知识
            refined_knowledge = self._step_refine(query, docs)

        elif action == RetrievalAction.USE:
            # 直接使用，但也进行精炼
            refined_knowledge = self._step_refine(query, docs)

        # Step 4: 准备上下文
        if refined_knowledge:
            context = refined_knowledge.summary
        else:
            context = "\n\n".join(docs)

        # Step 5: 生成回答
        answer = self._step_generate(query, context)

        # Step 6: 计算置信度
        confidence = self._calculate_final_confidence(eval_result, refined_knowledge, augmented)

        return self._build_result(
            query=query,
            answer=answer,
            confidence=confidence,
            action=action,
            refined_knowledge=refined_knowledge,
            augmented=augmented,
            sources=sources
        )

    def _step_retrieve(self, query: str) -> Tuple[List[str], List[str]]:
        """检索步骤"""
        self.current_state = CRAGState.RETRIEVING

        try:
            results = self.retriever(query)

            if isinstance(results, tuple):
                docs, sources = results
            else:
                docs = results if isinstance(results, list) else [results]
                sources = [f"source_{i}" for i in range(len(docs))]

            self.steps.append(CRAGStep(
                state=self.current_state,
                action="retrieve",
                input_data=query,
                output_data=f"Retrieved {len(docs)} documents",
                metadata={"num_docs": len(docs)}
            ))

            logger.debug(f"Retrieved {len(docs)} documents")
            return docs, sources

        except Exception as e:
            logger.error(f"Retrieval failed: {e}")
            self.steps.append(CRAGStep(
                state=self.current_state,
                action="retrieve_failed",
                input_data=query,
                output_data=str(e)
            ))
            return [], []

    def _step_evaluate(self, query: str, docs: List[str]) -> EvaluationResult:
        """评估步骤"""
        self.current_state = CRAGState.EVALUATING

        result = self.evaluator.evaluate(query, docs)

        self.steps.append(CRAGStep(
            state=self.current_state,
            action="evaluate",
            input_data=f"{len(docs)} documents",
            output_data=result.action.value,
            confidence=result.confidence,
            metadata={
                "overall_score": result.score.overall_score,
                "relevance_score": result.score.relevance_score
            }
        ))

        logger.debug(f"Evaluation: {result.action.value} (score: {result.score.overall_score:.2f})")
        return result

    def _step_refine(self, query: str, docs: List[str]) -> RefinedKnowledge:
        """精炼步骤"""
        self.current_state = CRAGState.REFINING

        result = self.refiner.refine(query, docs)

        self.steps.append(CRAGStep(
            state=self.current_state,
            action="refine",
            input_data=f"{len(docs)} documents",
            output_data=f"Refined to {result.refined_length} chars",
            metadata={
                "compression_ratio": result.compression_ratio,
                "num_segments": len(result.segments)
            }
        ))

        logger.debug(f"Refined knowledge: {result.compression_ratio:.2%} compression")
        return result

    def _step_augment(
        self,
        query: str,
        docs: List[str],
        sources: List[str],
        eval_result: EvaluationResult
    ) -> Tuple[List[str], List[str], bool]:
        """补充步骤"""
        self.current_state = CRAGState.AUGMENTING

        # 构建评估字典
        eval_dict = {
            'coverage_score': eval_result.score.coverage_score,
            'confidence': eval_result.confidence
        }

        # 执行补充
        current_content = "\n\n".join(docs)
        augment_result = self.augmentor.analyze_and_augment(
            query, current_content, eval_dict
        )

        if augment_result.success:
            # 合并新信息
            new_docs = [augment_result.augmented_content]
            new_sources = augment_result.sources

            docs = new_docs
            sources.extend(new_sources)

            self.steps.append(CRAGStep(
                state=self.current_state,
                action="augment_success",
                input_data=query,
                output_data=f"Added {len(augment_result.added_info)} pieces of info",
                metadata={"confidence_boost": augment_result.confidence_boost}
            ))

            return docs, sources, True
        else:
            self.steps.append(CRAGStep(
                state=self.current_state,
                action="augment_failed",
                input_data=query,
                output_data="No augmentation performed"
            ))

            return docs, sources, False

    def _step_generate(self, query: str, context: str) -> str:
        """生成步骤"""
        self.current_state = CRAGState.GENERATING

        try:
            answer = self.generator(query, context)

            self.steps.append(CRAGStep(
                state=self.current_state,
                action="generate",
                input_data={"query": query, "context_length": len(context)},
                output_data=answer[:100] + "..." if len(answer) > 100 else answer
            ))

            logger.debug(f"Generated answer: {len(answer)} chars")
            return answer

        except Exception as e:
            logger.error(f"Generation failed: {e}")
            return f"抱歉，生成回答时出错: {str(e)}"

    def _fallback_to_web_search(self, query: str) -> CRAGResult:
        """回退到 Web 搜索"""
        logger.info("Falling back to web search")

        try:
            # 使用 augmentor 的 web searcher
            search_results = self.augmentor.web_searcher(query)

            if search_results:
                # 提取内容
                contents = [
                    r.get('content', '') or r.get('snippet', '')
                    for r in search_results
                ]
                sources = [
                    r.get('url', '') or r.get('source', '')
                    for r in search_results
                ]

                # 精炼
                refined = self.refiner.refine(query, [c for c in contents if c])

                # 生成
                answer = self.generator(query, refined.summary)

                return self._build_result(
                    query=query,
                    answer=answer,
                    confidence=0.6,  # Web 搜索置信度较低
                    action=RetrievalAction.FALLBACK,
                    refined_knowledge=refined,
                    augmented=True,
                    sources=[s for s in sources if s]
                )
        except Exception as e:
            logger.error(f"Web search fallback failed: {e}")

        return self._direct_generate(query)

    def _direct_generate(self, query: str) -> CRAGResult:
        """直接生成（无检索）"""
        answer = self.generator(query, "")

        return self._build_result(
            query=query,
            answer=answer,
            confidence=0.4,
            action=RetrievalAction.FALLBACK,
            refined_knowledge=None,
            augmented=False,
            sources=[]
        )

    def _calculate_final_confidence(
        self,
        eval_result: EvaluationResult,
        refined_knowledge: Optional[RefinedKnowledge],
        augmented: bool
    ) -> float:
        """计算最终置信度"""
        base_confidence = eval_result.confidence

        # 精炼提升置信度
        if refined_knowledge:
            # 压缩比越合理，置信度越高
            compression = refined_knowledge.compression_ratio
            if 0.3 <= compression <= 0.7:
                base_confidence += 0.1

        # 补充提升置信度
        if augmented:
            base_confidence += 0.1

        return min(base_confidence, 1.0)

    def _build_result(
        self,
        query: str,
        answer: str,
        confidence: float,
        action: RetrievalAction,
        refined_knowledge: Optional[RefinedKnowledge],
        augmented: bool,
        sources: List[str]
    ) -> CRAGResult:
        """构建最终结果"""
        self.current_state = CRAGState.COMPLETED

        return CRAGResult(
            query=query,
            answer=answer,
            confidence=round(confidence, 3),
            action_taken=action,
            refined_knowledge=refined_knowledge,
            augmented=augmented,
            sources=list(set(sources)),
            steps=self.steps.copy(),
            metadata={
                "num_steps": len(self.steps),
                "final_state": self.current_state.value
            }
        )

    def _default_retriever(self, query: str) -> List[str]:
        """默认检索器"""
        return [f"这是关于 '{query}' 的模拟检索结果。"]

    def _default_generator(self, query: str, context: str) -> str:
        """默认生成器"""
        if context:
            return f"根据检索到的信息，关于 '{query}' 的回答是：{context[:200]}"
        return f"关于 '{query}'，这是一个基于知识的回答。"


def create_crag(
    retriever: Optional[Callable] = None,
    generator: Optional[Callable] = None,
    web_searcher: Optional[Callable] = None,
    config: Optional[Dict] = None
) -> CRAG:
    """
    创建 CRAG 实例的工厂函数

    Args:
        retriever: 检索函数
        generator: 生成函数
        web_searcher: Web 搜索函数
        config: 配置字典

    Returns:
        CRAG: CRAG 实例
    """
    return CRAG(retriever, generator, web_searcher, config)


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

    def mock_web_searcher(query: str):
        return [{
            'content': f"2024年{query}的最新进展...",
            'url': 'https://example.com/search'
        }]

    crag = CRAG(
        retriever=mock_retriever,
        generator=mock_generator,
        web_searcher=mock_web_searcher,
        config={'enable_augmentation': True}
    )

    test_queries = [
        "什么是机器学习？",
        "最新的AI发展情况如何？",
        "Python和Java的区别是什么？"
    ]

    print("=" * 60)
    print("CRAG 测试")
    print("=" * 60)

    for query in test_queries:
        result = crag.process(query)
        print(f"\n查询: {query}")
        print(f"回答: {result.answer[:100]}...")
        print(f"置信度: {result.confidence:.2%}")
        print(f"行动: {result.action_taken.value}")
        print(f"是否补充: {result.augmented}")
        print(f"来源数: {len(result.sources)}")
        print(f"步骤数: {len(result.steps)}")

        if result.refined_knowledge:
            print(f"压缩比: {result.refined_knowledge.compression_ratio:.2%}")
