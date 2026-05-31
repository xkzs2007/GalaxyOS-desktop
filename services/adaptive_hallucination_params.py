#!/usr/bin/env python3
"""
防幻觉系统参数自适应模块

论文参考:
- Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection (2023)
- Corrective Retrieval Augmented Generation (CRAG 2024)

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import json


class QueryType(Enum):
    """查询类型"""
    FACTUAL = "factual"        # 事实查询
    CREATIVE = "creative"      # 创意查询
    PROCEDURAL = "procedural"  # 流程查询
    ANALYTICAL = "analytical"  # 分析查询
    CONVERSATIONAL = "conversational"  # 对话查询


class DomainType(Enum):
    """领域类型"""
    GENERAL = "general"
    MEDICAL = "medical"
    LEGAL = "legal"
    TECHNICAL = "technical"
    FINANCIAL = "financial"


@dataclass
class AdaptiveThresholds:
    """自适应阈值配置"""
    familiarity_threshold: float = 0.4
    source_weight_internal: float = 0.80
    source_weight_web: float = 0.70
    source_weight_kg: float = 0.85
    verification_level_none: float = 0.9
    verification_level_light: float = 0.7
    verification_level_moderate: float = 0.5
    verification_level_deep: float = 0.3


class AdaptiveHallucinationParams:
    """
    防幻觉系统参数自适应调整器
    
    基于 Self-RAG 和 CRAG 论文的核心思想:
    1. 不同任务需要不同的检索和验证策略
    2. 根据置信度动态选择纠正策略
    3. 领域和用户历史影响阈值选择
    """
    
    # 基础阈值
    BASE_THRESHOLDS = AdaptiveThresholds()
    
    # 查询类型调整因子
    QUERY_TYPE_FACTORS = {
        QueryType.FACTUAL: {"threshold_delta": 0.1, "stricter": True},
        QueryType.CREATIVE: {"threshold_delta": -0.1, "stricter": False},
        QueryType.PROCEDURAL: {"threshold_delta": 0.05, "stricter": True},
        QueryType.ANALYTICAL: {"threshold_delta": 0.0, "stricter": False},
        QueryType.CONVERSATIONAL: {"threshold_delta": -0.05, "stricter": False},
    }
    
    # 领域调整因子
    DOMAIN_FACTORS = {
        DomainType.GENERAL: {"threshold_delta": 0.0},
        DomainType.MEDICAL: {"threshold_delta": 0.2, "stricter": True},
        DomainType.LEGAL: {"threshold_delta": 0.2, "stricter": True},
        DomainType.TECHNICAL: {"threshold_delta": 0.1, "stricter": True},
        DomainType.FINANCIAL: {"threshold_delta": 0.15, "stricter": True},
    }
    
    def __init__(self, user_history: Optional[Dict] = None):
        """
        初始化自适应参数调整器
        
        Args:
            user_history: 用户历史数据，包含纠正率等
        """
        self.user_history = user_history or {}
        self.adjustment_log = []
    
    def classify_query(self, query: str) -> QueryType:
        """
        分类查询类型
        
        Args:
            query: 用户查询
        
        Returns:
            查询类型
        """
        query_lower = query.lower()
        
        # 事实查询特征
        factual_keywords = ["是什么", "什么是", "多少", "什么时候", "谁", "哪里", 
                          "what is", "how many", "when", "who", "where"]
        if any(kw in query_lower for kw in factual_keywords):
            return QueryType.FACTUAL
        
        # 创意查询特征
        creative_keywords = ["想象", "创造", "设计", "构思", "假如", "如果",
                           "imagine", "create", "design", "what if"]
        if any(kw in query_lower for kw in creative_keywords):
            return QueryType.CREATIVE
        
        # 流程查询特征
        procedural_keywords = ["如何", "怎么", "步骤", "方法", "怎样",
                             "how to", "steps", "method"]
        if any(kw in query_lower for kw in procedural_keywords):
            return QueryType.PROCEDURAL
        
        # 分析查询特征
        analytical_keywords = ["分析", "比较", "评估", "为什么", "原因",
                             "analyze", "compare", "evaluate", "why"]
        if any(kw in query_lower for kw in analytical_keywords):
            return QueryType.ANALYTICAL
        
        return QueryType.CONVERSATIONAL
    
    def detect_domain(self, query: str, context: Optional[str] = None) -> DomainType:
        """
        检测查询领域
        
        Args:
            query: 用户查询
            context: 上下文
        
        Returns:
            领域类型
        """
        text = (query + " " + (context or "")).lower()
        
        # 医疗领域
        medical_keywords = ["症状", "疾病", "药物", "治疗", "医院", "医生",
                          "symptom", "disease", "medicine", "treatment"]
        if any(kw in text for kw in medical_keywords):
            return DomainType.MEDICAL
        
        # 法律领域
        legal_keywords = ["法律", "法规", "合同", "诉讼", "律师", "条款",
                         "law", "legal", "contract", "lawsuit"]
        if any(kw in text for kw in legal_keywords):
            return DomainType.LEGAL
        
        # 技术领域
        technical_keywords = ["代码", "编程", "算法", "系统", "软件", "硬件",
                            "code", "programming", "algorithm", "system"]
        if any(kw in text for kw in technical_keywords):
            return DomainType.TECHNICAL
        
        # 金融领域
        financial_keywords = ["投资", "股票", "基金", "财务", "银行", "贷款",
                            "investment", "stock", "fund", "finance"]
        if any(kw in text for kw in financial_keywords):
            return DomainType.FINANCIAL
        
        return DomainType.GENERAL
    
    def adjust_thresholds(
        self,
        query: str,
        context: Optional[str] = None,
        query_type: Optional[QueryType] = None,
        domain: Optional[DomainType] = None
    ) -> AdaptiveThresholds:
        """
        自适应调整阈值
        
        Args:
            query: 用户查询
            context: 上下文
            query_type: 查询类型（可选，自动检测）
            domain: 领域类型（可选，自动检测）
        
        Returns:
            调整后的阈值配置
        """
        # 自动检测类型和领域
        if query_type is None:
            query_type = self.classify_query(query)
        if domain is None:
            domain = self.detect_domain(query, context)
        
        # 基础阈值
        thresholds = AdaptiveThresholds()
        
        # 查询类型调整
        query_factor = self.QUERY_TYPE_FACTORS.get(query_type, {})
        threshold_delta = query_factor.get("threshold_delta", 0)
        thresholds.familiarity_threshold += threshold_delta
        
        # 领域调整
        domain_factor = self.DOMAIN_FACTORS.get(domain, {})
        threshold_delta = domain_factor.get("threshold_delta", 0)
        thresholds.familiarity_threshold += threshold_delta
        
        # 专业领域提高来源权重要求
        if domain in [DomainType.MEDICAL, DomainType.LEGAL, DomainType.FINANCIAL]:
            thresholds.source_weight_internal = 0.85
            thresholds.source_weight_kg = 0.90
        
        # 用户历史调整
        correction_rate = self.user_history.get("correction_rate", 0)
        if correction_rate > 0.3:
            # 用户经常纠正，提高阈值
            thresholds.familiarity_threshold += 0.1
            thresholds.verification_level_moderate += 0.1
        elif correction_rate < 0.1:
            # 用户很少纠正，可以适当放宽
            thresholds.familiarity_threshold -= 0.05
        
        # 确保阈值在合理范围内
        thresholds.familiarity_threshold = max(0.2, min(0.8, thresholds.familiarity_threshold))
        
        # 记录调整日志
        self.adjustment_log.append({
            "query": query[:50],
            "query_type": query_type.value,
            "domain": domain.value,
            "final_threshold": thresholds.familiarity_threshold
        })
        
        return thresholds
    
    def get_verification_level(self, confidence: float, thresholds: AdaptiveThresholds) -> str:
        """
        根据置信度和阈值确定验证级别
        
        Args:
            confidence: 初始置信度
            thresholds: 阈值配置
        
        Returns:
            验证级别: NONE, LIGHT, MODERATE, DEEP, EXHAUSTIVE
        """
        if confidence >= thresholds.verification_level_none:
            return "NONE"
        elif confidence >= thresholds.verification_level_light:
            return "LIGHT"
        elif confidence >= thresholds.verification_level_moderate:
            return "MODERATE"
        elif confidence >= thresholds.verification_level_deep:
            return "DEEP"
        else:
            return "EXHAUSTIVE"
    
    def get_adjustment_stats(self) -> Dict[str, Any]:
        """获取调整统计"""
        if not self.adjustment_log:
            return {"total_adjustments": 0}
        
        query_types = {}
        domains = {}
        thresholds = []
        
        for log in self.adjustment_log:
            qt = log["query_type"]
            d = log["domain"]
            t = log["final_threshold"]
            
            query_types[qt] = query_types.get(qt, 0) + 1
            domains[d] = domains.get(d, 0) + 1
            thresholds.append(t)
        
        return {
            "total_adjustments": len(self.adjustment_log),
            "query_type_distribution": query_types,
            "domain_distribution": domains,
            "avg_threshold": sum(thresholds) / len(thresholds),
            "min_threshold": min(thresholds),
            "max_threshold": max(thresholds)
        }


# 便捷函数
def get_adaptive_thresholds(query: str, context: str = None, user_history: dict = None) -> AdaptiveThresholds:
    """
    获取自适应阈值（便捷函数）
    
    Args:
        query: 用户查询
        context: 上下文
        user_history: 用户历史
    
    Returns:
        调整后的阈值配置
    """
    adjuster = AdaptiveHallucinationParams(user_history)
    return adjuster.adjust_thresholds(query, context)


if __name__ == "__main__":
    # 测试
    adjuster = AdaptiveHallucinationParams({"correction_rate": 0.2})
    
    test_queries = [
        "什么是机器学习？",
        "如何设计一个登录系统？",
        "想象一下未来的城市是什么样子",
        "阿司匹林的副作用有哪些？",
        "合同违约如何起诉？"
    ]
    
    print("=" * 60)
    print("防幻觉参数自适应测试")
    print("=" * 60)
    
    for query in test_queries:
        thresholds = adjuster.adjust_thresholds(query)
        query_type = adjuster.classify_query(query)
        domain = adjuster.detect_domain(query)
        
        print(f"\n查询: {query}")
        print(f"  类型: {query_type.value}")
        print(f"  领域: {domain.value}")
        print(f"  熟悉度阈值: {thresholds.familiarity_threshold:.2f}")
        print(f"  内部来源权重: {thresholds.source_weight_internal:.2f}")
