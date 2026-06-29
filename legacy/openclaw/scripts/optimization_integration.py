#!/usr/bin/env python3
"""
优化模块集成入口

整合所有优化模块，提供统一调用接口

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass

# 导入优化模块
from adaptive_hallucination_params import (
    AdaptiveHallucinationParams,
    AdaptiveThresholds,
    QueryType,
    DomainType,
    get_adaptive_thresholds
)
from dynamic_crag_threshold import (
    DynamicCRAGThreshold,
    RetrievalStrategy,
    CRAGThresholds,
    select_crag_strategy
)
from adaptive_ltp_ltd import (
    AdaptiveLTP_LTD,
    SynapseState,
    LTP_LTDParams,
    apply_adaptive_ltp,
    apply_adaptive_ltd
)
from adaptive_rrf import (
    AdaptiveRRF,
    RRFWeights,
    QueryCategory,
    fuse_with_adaptive_rrf
)
from intelligent_thinking_trigger import (
    IntelligentThinkingTrigger,
    ThinkingSkill,
    QueryAnalysis,
    detect_thinking_skill
)
from heartbeat_task_executor import (
    HeartbeatTaskExecutor,
    HeartbeatResult
)


@dataclass
class OptimizationResult:
    """优化结果"""
    module: str
    status: str
    data: Dict[str, Any]
    message: str


class OptimizationIntegration:
    """
    优化模块集成
    
    统一调用所有优化模块，提供一站式接口
    """
    
    def __init__(self, user_context: Optional[Dict] = None):
        """
        初始化优化集成
        
        Args:
            user_context: 用户上下文
        """
        self.user_context = user_context or {}
        
        # 初始化各模块
        self.hallucination_adapter = AdaptiveHallucinationParams(user_context)
        self.crag_selector = DynamicCRAGThreshold()
        self.ltp_ltd_adapter = AdaptiveLTP_LTD()
        self.rrf_adapter = AdaptiveRRF()
        self.thinking_trigger = IntelligentThinkingTrigger(user_context)
        self.heartbeat_executor = HeartbeatTaskExecutor()
    
    def optimize_query_processing(
        self,
        query: str,
        context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        优化查询处理
        
        整合所有查询相关优化
        
        Args:
            query: 用户查询
            context: 上下文
        
        Returns:
            优化结果
        """
        results = {}
        
        # 1. 防幻觉参数自适应
        hallucination_thresholds = self.hallucination_adapter.adjust_thresholds(query, context)
        results["hallucination"] = {
            "thresholds": {
                "familiarity": hallucination_thresholds.familiarity_threshold,
                "internal_weight": hallucination_thresholds.source_weight_internal,
                "kg_weight": hallucination_thresholds.source_weight_kg,
            },
            "query_type": self.hallucination_adapter.classify_query(query).value,
            "domain": self.hallucination_adapter.detect_domain(query, context).value
        }
        
        # 2. 思考技能触发
        thinking_analysis = self.thinking_trigger.detect_thinking_need(query, context)
        results["thinking"] = {
            "skill": thinking_analysis.suggested_skill.value,
            "complexity": thinking_analysis.complexity,
            "question_type": thinking_analysis.question_type,
            "confidence": thinking_analysis.confidence,
            "reasoning": thinking_analysis.reasoning
        }
        
        # 3. RRF 权重
        rrf_weights = self.rrf_adapter.get_adaptive_weights(query)
        results["rrf"] = {
            "dense_weight": rrf_weights.dense_weight,
            "sparse_weight": rrf_weights.sparse_weight,
            "category": self.rrf_adapter.classify_query(query).value
        }
        
        return results
    
    def optimize_retrieval(
        self,
        scores: List[float],
        query: str = "",
        query_type: str = "general"
    ) -> Dict[str, Any]:
        """
        优化检索
        
        Args:
            scores: 检索分数
            query: 查询
            query_type: 查询类型
        
        Returns:
            优化结果
        """
        # CRAG 策略选择
        strategy, analysis = self.crag_selector.select_strategy(scores, query_type)
        
        return {
            "strategy": strategy.value,
            "analysis": analysis,
            "recommendation": self._get_strategy_recommendation(strategy)
        }
    
    def optimize_memory_synapse(
        self,
        synapse: SynapseState,
        operation: str = "ltp"
    ) -> Dict[str, Any]:
        """
        优化记忆突触
        
        Args:
            synapse: 突触状态
            operation: 操作类型 (ltp/ltd)
        
        Returns:
            优化结果
        """
        if operation == "ltp":
            new_synapse = self.ltp_ltd_adapter.apply_ltp(synapse)
            strength = self.ltp_ltd_adapter.calculate_ltp_strength(synapse)
        else:
            new_synapse = self.ltp_ltd_adapter.apply_ltd(synapse)
            strength = self.ltp_ltd_adapter.calculate_ltd_rate(synapse)
        
        return {
            "operation": operation,
            "old_weight": synapse.weight,
            "new_weight": new_synapse.weight,
            "change": strength,
            "reinforcement_count": new_synapse.reinforcement_count
        }
    
    def execute_heartbeat(self) -> HeartbeatResult:
        """
        执行心跳任务
        
        Returns:
            心跳执行结果
        """
        return self.heartbeat_executor.execute_heartbeat()
    
    def _get_strategy_recommendation(self, strategy: RetrievalStrategy) -> str:
        """获取策略建议"""
        recommendations = {
            RetrievalStrategy.DIRECT_USE: "直接使用检索结果，无需额外处理",
            RetrievalStrategy.REFINE: "建议进行精筛，提高结果质量",
            RetrievalStrategy.WEB_AUGMENT: "建议补充 Web 搜索，增强信息来源",
            RetrievalStrategy.DECOMPOSE: "建议分解查询，分别检索后合并"
        }
        return recommendations.get(strategy, "未知策略")


# 便捷函数
def optimize_query(query: str, context: str = None) -> Dict[str, Any]:
    """优化查询（便捷函数）"""
    integration = OptimizationIntegration()
    return integration.optimize_query_processing(query, context)


def optimize_retrieval(scores: List[float], query_type: str = "general") -> Dict[str, Any]:
    """优化检索（便捷函数）"""
    integration = OptimizationIntegration()
    return integration.optimize_retrieval(scores, query_type=query_type)


if __name__ == "__main__":
    # 测试集成
    integration = OptimizationIntegration()
    
    print("=" * 70)
    print("优化模块集成测试")
    print("=" * 70)
    
    # 测试查询优化
    test_query = "如何从根本上解决系统性能问题？"
    print(f"\n【查询优化测试】")
    print(f"查询: {test_query}")
    
    result = integration.optimize_query_processing(test_query)
    
    print(f"\n防幻觉参数:")
    print(f"  熟悉度阈值: {result['hallucination']['thresholds']['familiarity']:.2f}")
    print(f"  查询类型: {result['hallucination']['query_type']}")
    print(f"  领域: {result['hallucination']['domain']}")
    
    print(f"\n思考技能:")
    print(f"  建议技能: {result['thinking']['skill']}")
    print(f"  复杂度: {result['thinking']['complexity']:.2f}")
    print(f"  推理: {result['thinking']['reasoning']}")
    
    print(f"\nRRF 权重:")
    print(f"  Dense: {result['rrf']['dense_weight']:.2f}")
    print(f"  Sparse: {result['rrf']['sparse_weight']:.2f}")
    
    # 测试检索优化
    print(f"\n【检索优化测试】")
    test_scores = [0.75, 0.72, 0.70, 0.68]
    print(f"分数: {test_scores}")
    
    retrieval_result = integration.optimize_retrieval(test_scores, query_type="factual")
    print(f"策略: {retrieval_result['strategy']}")
    print(f"建议: {retrieval_result['recommendation']}")
    
    print("\n" + "=" * 70)
    print("集成测试完成")
    print("=" * 70)
