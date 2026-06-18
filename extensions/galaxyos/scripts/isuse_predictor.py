#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IsUSE Predictor - 生成可靠性预测器
基于 Self-RAG 论文实现，判断生成的内容是否可靠

Self-RAG: https://arxiv.org/abs/2310.11511
"""

import re
import json
import logging
from typing import Tuple, List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ReliabilityLevel(Enum):
    """可靠性级别"""
    HIGHLY_RELIABLE = "highly_reliable"      # 高度可靠
    RELIABLE = "reliable"                    # 可靠
    UNCERTAIN = "uncertain"                  # 不确定
    UNRELIABLE = "unreliable"                # 不可靠
    HALLUCINATED = "hallucinated"            # 幻觉


@dataclass
class ReliabilityDecision:
    """可靠性决策结果"""
    is_reliable: bool
    confidence: float
    reliability_level: ReliabilityLevel
    factuality_score: float
    consistency_score: float
    grounding_score: float
    issues: List[str]
    suggestions: List[str]
    reasoning: str


class IsUSEPredictor:
    """
    IsUSE 预测器 - 判断生成内容是否可靠
    
    基于 Self-RAG 论文的生成评估模块，通过多维度分析判断
    生成内容的可靠性，检测潜在的幻觉和不一致性。
    
    核心评估维度：
    1. 事实性：内容是否符合已知事实
    2. 一致性：内容是否自洽
    3. 基础性：内容是否有检索结果支撑
    4. 完整性：内容是否完整回答了问题
    """
    
    # 幻觉检测模式
    HALLUCINATION_PATTERNS = [
        r'(我(不)?确定|可能|也许|大概|似乎)',
        r'(据我所知|据我了解|我记得)',
        r'(一般来说|通常情况下|大多数)',
        r'(一些|某些|很多|许多)\s*(人|研究|专家)',
        r'(没有具体|缺乏详细|信息不足)',
    ]
    
    # 高置信度模式
    HIGH_CONFIDENCE_PATTERNS = [
        r'(根据|依据|按照)\s*(研究|数据|报告|调查)',
        r'(证明|表明|显示|证实)',
        r'(具体|明确|确切|详细)',
        r'(官方|权威|正式)',
    ]
    
    # 矛盾检测模式
    CONTRADICTION_INDICATORS = [
        ('但是', '然而'),
        ('虽然', '但是'),
        ('一方面', '另一方面'),
        ('尽管', '仍然'),
    ]
    
    def __init__(self, config: Optional[Dict] = None):
        """
        初始化 IsUSE 预测器
        
        Args:
            config: 配置字典，可包含：
                - reliability_threshold: 可靠性阈值，默认 0.65
                - enable_hallucination_detection: 启用幻觉检测，默认 True
        """
        self.config = config or {}
        self.reliability_threshold = self.config.get('reliability_threshold', 0.65)
        self.enable_hallucination_detection = self.config.get(
            'enable_hallucination_detection', True
        )
        
        # 编译正则表达式
        self._hallucination_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.HALLUCINATION_PATTERNS
        ]
        self._high_confidence_patterns = [
            re.compile(p, re.IGNORECASE) for p in self.HIGH_CONFIDENCE_PATTERNS
        ]
        
        logger.info(f"IsUSE Predictor initialized with threshold={self.reliability_threshold}")
    
    def predict(
        self, 
        generated_content: str,
        query: str,
        retrieved_context: Optional[str] = None,
        known_facts: Optional[List[str]] = None
    ) -> ReliabilityDecision:
        """
        预测生成内容的可靠性
        
        Args:
            generated_content: 生成的内容
            query: 原始查询
            retrieved_context: 检索到的上下文（可选）
            known_facts: 已知事实列表（可选）
            
        Returns:
            ReliabilityDecision: 可靠性决策结果
        """
        # 1. 计算事实性得分
        factuality_score, factuality_issues = self._calculate_factuality(
            generated_content, known_facts
        )
        
        # 2. 计算一致性得分
        consistency_score, consistency_issues = self._calculate_consistency(
            generated_content
        )
        
        # 3. 计算基础性得分
        grounding_score, grounding_issues = self._calculate_grounding(
            generated_content, retrieved_context
        )
        
        # 4. 计算完整性得分
        completeness_score, completeness_issues = self._calculate_completeness(
            generated_content, query
        )
        
        # 5. 幻觉检测
        hallucination_score, hallucination_indicators = self._detect_hallucination(
            generated_content
        )
        
        # 6. 综合评分
        overall_score = self._calculate_overall_score(
            factuality_score,
            consistency_score,
            grounding_score,
            completeness_score,
            hallucination_score
        )
        
        # 7. 收集问题和建议
        all_issues = (
            factuality_issues + 
            consistency_issues + 
            grounding_issues + 
            completeness_issues
        )
        suggestions = self._generate_suggestions(all_issues, hallucination_indicators)
        
        # 8. 确定可靠性级别
        reliability_level = self._determine_reliability_level(
            overall_score, hallucination_score
        )
        
        # 9. 计算置信度
        confidence = self._calculate_confidence(
            factuality_score, consistency_score, grounding_score
        )
        
        # 10. 生成推理说明
        reasoning = self._generate_reasoning(
            reliability_level, overall_score, factuality_score,
            consistency_score, grounding_score
        )
        
        return ReliabilityDecision(
            is_reliable=(reliability_level in [
                ReliabilityLevel.HIGHLY_RELIABLE, 
                ReliabilityLevel.RELIABLE
            ]),
            confidence=round(confidence, 3),
            reliability_level=reliability_level,
            factuality_score=round(factuality_score, 3),
            consistency_score=round(consistency_score, 3),
            grounding_score=round(grounding_score, 3),
            issues=all_issues,
            suggestions=suggestions,
            reasoning=reasoning
        )
    
    def is_reliable(
        self, 
        generated_content: str,
        query: str,
        retrieved_context: Optional[str] = None
    ) -> Tuple[bool, float]:
        """
        简化接口：判断生成内容是否可靠
        
        Args:
            generated_content: 生成的内容
            query: 原始查询
            retrieved_context: 检索到的上下文
            
        Returns:
            Tuple[bool, float]: (是否可靠, 置信度)
        """
        decision = self.predict(generated_content, query, retrieved_context)
        return decision.is_reliable, decision.confidence
    
    def _calculate_factuality(
        self, 
        content: str, 
        known_facts: Optional[List[str]]
    ) -> Tuple[float, List[str]]:
        """计算事实性得分"""
        issues = []
        
        if not known_facts:
            # 无已知事实，基于启发式规则
            score = 0.75  # 提高基础分
            
            # 检查不确定性表达
            uncertainty_count = sum(
                1 for p in self._hallucination_patterns 
                if p.search(content)
            )
            score -= uncertainty_count * 0.15
            
            # 检查高置信度表达
            high_confidence_count = sum(
                1 for p in self._high_confidence_patterns 
                if p.search(content)
            )
            score += high_confidence_count * 0.1
            
            if uncertainty_count > 2:
                issues.append("包含多处不确定性表达")
            
            return max(score, 0.3), issues
        
        # 有已知事实，进行验证
        verified_count = 0
        for fact in known_facts:
            # 简化的匹配逻辑
            fact_keywords = set(re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', fact.lower()))
            content_keywords = set(re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', content.lower()))
            
            overlap = len(fact_keywords & content_keywords)
            if overlap >= len(fact_keywords) * 0.5:
                verified_count += 1
        
        score = verified_count / len(known_facts) if known_facts else 0.5
        
        if score < 0.5:
            issues.append("部分内容与已知事实不符")
        
        return score, issues
    
    def _calculate_consistency(
        self, 
        content: str
    ) -> Tuple[float, List[str]]:
        """计算一致性得分"""
        issues = []
        score = 1.0
        
        # 检查自相矛盾的表述
        sentences = re.split(r'[。！？\n]', content)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        # 检测矛盾指示词
        contradiction_count = 0
        for indicator1, indicator2 in self.CONTRADICTION_INDICATORS:
            if indicator1 in content and indicator2 in content:
                # 可能存在矛盾
                contradiction_count += 1
        
        if contradiction_count > 0:
            score -= contradiction_count * 0.15
            issues.append(f"检测到{contradiction_count}处可能的矛盾")
        
        # 检查数值一致性
        numbers = re.findall(r'\d+(?:\.\d+)?', content)
        if len(numbers) >= 2:
            # 检查是否有明显不一致的数值
            unique_numbers = set(numbers)
            if len(unique_numbers) != len(numbers):
                # 有重复数值，可能是引用同一数据
                pass
        
        return max(score, 0.3), issues
    
    def _calculate_grounding(
        self, 
        content: str, 
        context: Optional[str]
    ) -> Tuple[float, List[str]]:
        """计算基础性得分"""
        issues = []
        
        if not context:
            # 无检索上下文
            # 检查是否有引用来源
            has_source = bool(re.search(r'(根据|来源|引用|参考|研究显示|数据显示)', content))
            if has_source:
                return 0.7, []  # 提高有来源的得分
            return 0.6, []  # 提高无上下文的基础分
        
        # 计算内容与上下文的重叠
        content_words = set(re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', content.lower()))
        context_words = set(re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', context.lower()))
        
        if not content_words:
            return 0.5, ["内容为空"]
        
        overlap = len(content_words & context_words)
        grounding_ratio = overlap / len(content_words)
        
        if grounding_ratio < 0.2:
            issues.append("生成内容与检索上下文关联度低")
        elif grounding_ratio > 0.9:
            issues.append("生成内容过度依赖检索上下文")
        
        # 检查高置信度模式
        high_confidence_count = sum(
            1 for p in self._high_confidence_patterns 
            if p.search(content)
        )
        
        bonus = min(high_confidence_count * 0.1, 0.2)
        
        # 提高基础性得分
        score = min(grounding_ratio + bonus + 0.2, 1.0)
        
        return score, issues
    
    def _calculate_completeness(
        self, 
        content: str, 
        query: str
    ) -> Tuple[float, List[str]]:
        """计算完整性得分"""
        issues = []
        
        # 提取查询的核心问题
        question_type = self._identify_question_type(query)
        
        # 检查内容长度
        if len(content) < 20:
            issues.append("回答过于简短")
            return 0.3, issues
        
        # 根据问题类型检查完整性
        score = 0.75  # 提高基础分
        
        if question_type == 'definition':
            if not re.search(r'(是指|定义为|是一种|意思是|是)', content):
                issues.append("缺少定义性表述")
                score -= 0.15
        
        elif question_type == 'how':
            if not re.search(r'(首先|然后|最后|步骤|方法|可以|需要)', content):
                issues.append("缺少步骤性说明")
                score -= 0.15
        
        elif question_type == 'why':
            if not re.search(r'(因为|原因是|由于|导致|所以)', content):
                issues.append("缺少因果解释")
                score -= 0.15
        
        elif question_type == 'comparison':
            if not re.search(r'(不同|区别|相比|差异|而|但是)', content):
                issues.append("缺少比较分析")
                score -= 0.15
        
        return max(score, 0.4), issues
    
    def _detect_hallucination(
        self, 
        content: str
    ) -> Tuple[float, List[str]]:
        """检测幻觉"""
        indicators = []
        
        if not self.enable_hallucination_detection:
            return 1.0, []
        
        hallucination_score = 1.0
        
        # 检查不确定性表达
        for pattern in self._hallucination_patterns:
            matches = pattern.findall(content)
            if matches:
                indicators.append(f"不确定性表达: {matches[0]}")
                hallucination_score -= 0.08  # 降低惩罚
        
        # 检查过度具体的细节（可能是编造的）
        specific_details = re.findall(r'\d{4}年\d{1,2}月\d{1,2}日', content)
        if specific_details and len(specific_details) > 2:
            indicators.append("包含多个具体日期，需验证")
            hallucination_score -= 0.03
        
        # 检查引用格式
        fake_citations = re.findall(r'\[\d+\]', content)
        if fake_citations and not re.search(r'(参考文献|引用|来源)', content):
            indicators.append("包含未解释的引用标记")
            hallucination_score -= 0.05
        
        return max(hallucination_score, 0.5), indicators
    
    def _identify_question_type(self, query: str) -> str:
        """识别问题类型"""
        if re.search(r'(什么是|定义|概念)', query):
            return 'definition'
        elif re.search(r'(如何|怎么|怎样|方法)', query):
            return 'how'
        elif re.search(r'(为什么|原因|为何)', query):
            return 'why'
        elif re.search(r'(区别|差异|比较|对比)', query):
            return 'comparison'
        elif re.search(r'(谁|哪位|人物)', query):
            return 'who'
        elif re.search(r'(何时|什么时候|时间)', query):
            return 'when'
        elif re.search(r'(哪里|何地|地点)', query):
            return 'where'
        else:
            return 'general'
    
    def _calculate_overall_score(
        self,
        factuality: float,
        consistency: float,
        grounding: float,
        completeness: float,
        hallucination: float
    ) -> float:
        """计算综合得分"""
        weights = {
            'factuality': 0.30,
            'consistency': 0.20,
            'grounding': 0.25,
            'completeness': 0.15,
            'hallucination': 0.10
        }
        
        return (
            weights['factuality'] * factuality +
            weights['consistency'] * consistency +
            weights['grounding'] * grounding +
            weights['completeness'] * completeness +
            weights['hallucination'] * hallucination
        )
    
    def _determine_reliability_level(
        self, 
        overall_score: float,
        hallucination_score: float
    ) -> ReliabilityLevel:
        """确定可靠性级别"""
        if hallucination_score < 0.4:
            return ReliabilityLevel.HALLUCINATED
        
        if overall_score >= 0.75:  # 降低阈值
            return ReliabilityLevel.HIGHLY_RELIABLE
        elif overall_score >= 0.60:  # 降低阈值
            return ReliabilityLevel.RELIABLE
        elif overall_score >= 0.45:  # 降低阈值
            return ReliabilityLevel.UNCERTAIN
        else:
            return ReliabilityLevel.UNRELIABLE
    
    def _calculate_confidence(
        self, 
        factuality: float, 
        consistency: float, 
        grounding: float
    ) -> float:
        """计算置信度"""
        scores = [factuality, consistency, grounding]
        avg = sum(scores) / len(scores)
        variance = sum((s - avg) ** 2 for s in scores) / len(scores)
        
        consistency_factor = 1.0 - min(variance * 2, 0.4)
        return (avg + consistency_factor) / 2
    
    def _generate_suggestions(
        self, 
        issues: List[str], 
        hallucination_indicators: List[str]
    ) -> List[str]:
        """生成改进建议"""
        suggestions = []
        
        if "包含多处不确定性表达" in issues:
            suggestions.append("建议补充具体数据或来源以增强可信度")
        
        if "缺少外部知识支撑" in issues:
            suggestions.append("建议检索相关资料以支撑回答")
        
        if "生成内容与检索上下文关联度低" in issues:
            suggestions.append("建议重新检索更相关的资料")
        
        if "回答过于简短" in issues:
            suggestions.append("建议扩展回答内容，提供更多细节")
        
        if hallucination_indicators:
            suggestions.append("建议核实具体细节，避免潜在幻觉")
        
        if not suggestions:
            suggestions.append("内容质量良好，无需特别改进")
        
        return suggestions
    
    def _generate_reasoning(
        self,
        reliability_level: ReliabilityLevel,
        overall_score: float,
        factuality: float,
        consistency: float,
        grounding: float
    ) -> str:
        """生成推理说明"""
        reasons = [f"可靠性级别: {reliability_level.value}"]
        reasons.append(f"综合得分: {overall_score:.2f}")
        reasons.append(f"事实性: {factuality:.2f}")
        reasons.append(f"一致性: {consistency:.2f}")
        reasons.append(f"基础性: {grounding:.2f}")
        
        return "; ".join(reasons)


# 便捷函数
def is_reliable(
    content: str, 
    query: str, 
    context: Optional[str] = None,
    config: Optional[Dict] = None
) -> Tuple[bool, float]:
    """
    便捷函数：判断生成内容是否可靠
    
    Args:
        content: 生成的内容
        query: 原始查询
        context: 检索到的上下文
        config: 可选配置
        
    Returns:
        Tuple[bool, float]: (是否可靠, 置信度)
    """
    predictor = IsUSEPredictor(config)
    return predictor.is_reliable(content, query, context)


if __name__ == "__main__":
    # 测试示例
    test_cases = [
        {
            "query": "什么是机器学习？",
            "content": "机器学习是人工智能的一个分支，它使计算机能够从数据中学习并做出决策。根据MIT的研究，机器学习已在图像识别、自然语言处理等领域取得突破。",
            "context": "机器学习是AI的分支，通过数据训练模型。"
        },
        {
            "query": "Python的创始人是谁？",
            "content": "我记得Python好像是由Guido van Rossum创建的，大概是在1991年发布的吧。",
            "context": None
        },
        {
            "query": "如何学习编程？",
            "content": "首先选择一门语言，然后买本书看，最后多练习就行了。",
            "context": "学习编程需要系统的方法：选择语言、学习基础、实践项目、参与社区。"
        }
    ]
    
    predictor = IsUSEPredictor()
    
    print("=" * 60)
    print("IsUSE Predictor 测试")
    print("=" * 60)
    
    for case in test_cases:
        decision = predictor.predict(
            case["content"], 
            case["query"], 
            case.get("context")
        )
        print(f"\n查询: {case['query']}")
        print(f"内容: {case['content'][:50]}...")
        print(f"  是否可靠: {decision.is_reliable}")
        print(f"  置信度: {decision.confidence:.2%}")
        print(f"  可靠性级别: {decision.reliability_level.value}")
        print(f"  事实性: {decision.factuality_score:.2f}")
        print(f"  一致性: {decision.consistency_score:.2f}")
        print(f"  基础性: {decision.grounding_score:.2f}")
        print(f"  问题: {decision.issues}")
        print(f"  建议: {decision.suggestions}")
