#!/usr/bin/env python3
"""
CRAG 动态阈值模块

论文参考:
- Corrective Retrieval Augmented Generation (CRAG 2024)
- https://arxiv.org/abs/2401.15884

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
import numpy as np


class RetrievalStrategy(Enum):
    """检索策略"""
    DIRECT_USE = "direct_use"           # 直接使用
    REFINE = "refine"                   # 精筛
    WEB_AUGMENT = "web_augment"         # Web 补充
    DECOMPOSE = "decompose"             # 分解查询


@dataclass
class CRAGThresholds:
    """CRAG 阈值配置"""
    high_confidence: float = 0.85       # 高置信度阈值
    medium_confidence: float = 0.5      # 中置信度阈值
    low_confidence: float = 0.3         # 低置信度阈值
    variance_threshold: float = 0.05    # 方差阈值
    min_documents: int = 3              # 最小文档数


class DynamicCRAGThreshold:
    """
    CRAG 动态阈值策略
    
    基于 CRAG 论文核心思想:
    1. 根据检索结果的置信度分布选择策略
    2. 高置信度 → 直接使用
    3. 中置信度 → 分解-重组精筛
    4. 低置信度 → Web 搜索补充
    
    创新点:
    - 不是单一阈值，而是分析整体分布
    - 考虑 Top-K 分数的方差
    - 根据查询类型动态调整
    """
    
    # 默认阈值
    DEFAULT_THRESHOLDS = CRAGThresholds()
    
    # 查询类型阈值调整
    QUERY_TYPE_ADJUSTMENTS = {
        "factual": {"high": 0.90, "medium": 0.55},      # 事实查询更严格
        "creative": {"high": 0.75, "medium": 0.40},     # 创意查询更宽松
        "procedural": {"high": 0.85, "medium": 0.50},   # 流程查询标准
        "analytical": {"high": 0.80, "medium": 0.45},   # 分析查询稍宽松
        "conversational": {"high": 0.70, "medium": 0.35}  # 对话查询最宽松
    }
    
    def __init__(self, thresholds: Optional[CRAGThresholds] = None):
        """
        初始化动态阈值
        
        Args:
            thresholds: 自定义阈值配置
        """
        self.thresholds = thresholds or self.DEFAULT_THRESHOLDS
        self.strategy_log = []
    
    def analyze_retrieval_scores(
        self,
        scores: List[float],
        query_type: str = "general"
    ) -> Dict[str, Any]:
        """
        分析检索分数分布
        
        Args:
            scores: 检索分数列表
            query_type: 查询类型
        
        Returns:
            分析结果
        """
        if not scores:
            return {
                "max_score": 0.0,
                "mean_score": 0.0,
                "variance": 0.0,
                "std": 0.0,
                "gap_top2": 0.0,
                "num_results": 0
            }
        
        scores_array = np.array(scores)
        
        # 基础统计
        max_score = float(scores_array.max())
        mean_score = float(scores_array.mean())
        variance = float(scores_array.var())
        std = float(scores_array.std())
        
        # Top-2 差距（判断结果一致性）
        if len(scores) >= 2:
            sorted_scores = sorted(scores, reverse=True)
            gap_top2 = sorted_scores[0] - sorted_scores[1]
        else:
            gap_top2 = 0.0
        
        return {
            "max_score": max_score,
            "mean_score": mean_score,
            "variance": variance,
            "std": std,
            "gap_top2": gap_top2,
            "num_results": len(scores)
        }
    
    def get_adjusted_thresholds(self, query_type: str) -> CRAGThresholds:
        """
        根据查询类型调整阈值
        
        Args:
            query_type: 查询类型
        
        Returns:
            调整后的阈值
        """
        adjustments = self.QUERY_TYPE_ADJUSTMENTS.get(query_type, {})
        
        adjusted = CRAGThresholds(
            high_confidence=adjustments.get("high", self.thresholds.high_confidence),
            medium_confidence=adjustments.get("medium", self.thresholds.medium_confidence),
            low_confidence=self.thresholds.low_confidence,
            variance_threshold=self.thresholds.variance_threshold,
            min_documents=self.thresholds.min_documents
        )
        
        return adjusted
    
    def select_strategy(
        self,
        scores: List[float],
        query_type: str = "general",
        context: Optional[Dict] = None
    ) -> Tuple[RetrievalStrategy, Dict[str, Any]]:
        """
        选择检索策略
        
        Args:
            scores: 检索分数列表
            query_type: 查询类型
            context: 额外上下文
        
        Returns:
            (策略, 分析结果)
        """
        # 分析分数分布
        analysis = self.analyze_retrieval_scores(scores, query_type)
        
        # 获取调整后的阈值
        thresholds = self.get_adjusted_thresholds(query_type)
        
        # 策略选择逻辑
        max_score = analysis["max_score"]
        variance = analysis["variance"]
        num_results = analysis["num_results"]
        
        # 1. 高置信度 + 低方差 → 结果一致，直接使用
        if max_score >= thresholds.high_confidence and variance < thresholds.variance_threshold:
            strategy = RetrievalStrategy.DIRECT_USE
            reason = "高置信度且结果一致"
        
        # 2. 高置信度 + 高方差 → 需要精筛
        elif max_score >= thresholds.high_confidence:
            strategy = RetrievalStrategy.REFINE
            reason = "高置信度但结果不一致"
        
        # 3. 中置信度 → 精筛
        elif max_score >= thresholds.medium_confidence:
            strategy = RetrievalStrategy.REFINE
            reason = "中等置信度，需要精筛"
        
        # 4. 低置信度 + 有结果 → Web 补充
        elif max_score >= thresholds.low_confidence and num_results >= thresholds.min_documents:
            strategy = RetrievalStrategy.WEB_AUGMENT
            reason = "低置信度，需要 Web 补充"
        
        # 5. 极低置信度或无结果 → 分解查询
        else:
            strategy = RetrievalStrategy.DECOMPOSE
            reason = "极低置信度或无结果，需要分解查询"
        
        # 记录日志
        log_entry = {
            "query_type": query_type,
            "max_score": max_score,
            "variance": variance,
            "strategy": strategy.value,
            "reason": reason
        }
        self.strategy_log.append(log_entry)
        
        # 添加策略信息到分析结果
        analysis["strategy"] = strategy.value
        analysis["reason"] = reason
        analysis["thresholds_used"] = {
            "high": thresholds.high_confidence,
            "medium": thresholds.medium_confidence,
            "low": thresholds.low_confidence
        }
        
        return strategy, analysis
    
    def get_strategy_stats(self) -> Dict[str, Any]:
        """获取策略选择统计"""
        if not self.strategy_log:
            return {"total_selections": 0}
        
        strategies = {}
        for log in self.strategy_log:
            s = log["strategy"]
            strategies[s] = strategies.get(s, 0) + 1
        
        return {
            "total_selections": len(self.strategy_log),
            "strategy_distribution": strategies,
            "recent_selections": self.strategy_log[-5:]
        }


# 便捷函数
def select_crag_strategy(scores: List[float], query_type: str = "general") -> Tuple[RetrievalStrategy, Dict]:
    """
    选择 CRAG 策略（便捷函数）
    
    Args:
        scores: 检索分数列表
        query_type: 查询类型
    
    Returns:
        (策略, 分析结果)
    """
    selector = DynamicCRAGThreshold()
    return selector.select_strategy(scores, query_type)


if __name__ == "__main__":
    # 测试
    selector = DynamicCRAGThreshold()
    
    test_cases = [
        ([0.92, 0.90, 0.88, 0.85], "factual", "高置信度事实查询"),
        ([0.75, 0.72, 0.70, 0.68], "factual", "中置信度事实查询"),
        ([0.92, 0.60, 0.40, 0.30], "factual", "高置信度高方差"),
        ([0.35, 0.32, 0.30, 0.28], "creative", "低置信度创意查询"),
        ([0.20, 0.18, 0.15], "general", "极低置信度"),
        ([], "general", "无结果"),
    ]
    
    print("=" * 70)
    print("CRAG 动态阈值测试")
    print("=" * 70)
    
    for scores, query_type, desc in test_cases:
        strategy, analysis = selector.select_strategy(scores, query_type)
        
        print(f"\n【{desc}】")
        print(f"  分数: {scores[:3]}{'...' if len(scores) > 3 else ''}")
        print(f"  查询类型: {query_type}")
        print(f"  最高分: {analysis['max_score']:.2f}")
        print(f"  方差: {analysis['variance']:.4f}")
        print(f"  策略: {strategy.value}")
        print(f"  原因: {analysis['reason']}")
    
    print("\n" + "=" * 70)
    print("策略统计")
    print("=" * 70)
    stats = selector.get_strategy_stats()
    for strategy, count in stats["strategy_distribution"].items():
        print(f"  {strategy}: {count} 次")
