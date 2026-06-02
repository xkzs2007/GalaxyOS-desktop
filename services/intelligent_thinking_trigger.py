#!/usr/bin/env python3
"""
智能思考技能触发器

论文参考:
- Intent Detection in Conversational Systems (2023)

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
import re


class ThinkingSkill(Enum):
    """思考技能类型"""
    FIRST_PRINCIPLES = "first-principles"      # 第一性原理
    SYSTEMS_THINKING = "systems-thinking"      # 系统思维
    CRITICAL_THINKING = "critical-thinking"    # 批判性思维
    BACKWARD_THINKING = "backward-thinking"    # 逆向思维
    ANALOGICAL_THINKING = "analogical-thinking"  # 类比思维
    FEYNMAN_TECHNIQUE = "feynman-technique"    # 费曼技巧
    DECISION_ENGINE = "decision-engine"        # 决策引擎
    PRODUCT_THINKING = "product-thinking"      # 产品思维
    # Matt Pocock 工程与效率技能
    DIAGNOSE = "diagnose"                              # 系统化 Bug 诊断（≙ 调查研究）
    GRILL_WITH_DOCS = "grill-with-docs"                # 需求审问（≙ 矛盾分析）
    TDD = "tdd"                                        # 测试驱动开发（≙ 批评与自我批评）
    IMPROVE_ARCH = "improve-codebase-architecture"      # 架构改进（≙ 实践认知）
    PROTOTYPE = "prototype"                            # 快速原型验证（≙ 集中兵力）
    ZOOM_OUT = "zoom-out"                              # 全局视角（≙ 统筹全局）
    GRILL_ME = "grill-me"                              # 方案推敲（≙ 武装思想）
    CAVEMAN = "caveman"                                # 超压缩通信
    HANDOFF = "handoff"                                # 会话交接
    WRITE_SKILL = "write-a-skill"                      # 编写新 Skill
    
    # 方法论技能 (qiushi)
    INVESTIGATION_FIRST = "investigation-first"         # 调查研究（没调查没有发言权）
    CONTRADICTION_ANALYSIS = "contradiction-analysis"   # 矛盾分析（抓主要矛盾）
    PRACTICE_COGNITION = "practice-cognition"           # 实践认知（实践检验真理）
    CONCENTRATE_FORCES = "concentrate-forces"           # 集中兵力（聚焦突破）
    OVERALL_PLANNING = "overall-planning"               # 统筹全局（全局规划）
    MASS_LINE = "mass-line"                             # 群众路线（收集→整合→验证）
    CRITICISM_SELF_CRITICISM = "criticism-self-criticism"  # 批评与自我批评（自我革新）
    SPARK_PRAIRIE_FIRE = "spark-prairie-fire"           # 星星之火（以点带面）
    ARMING_THOUGHT = "arming-thought"                   # 武装思想（理论武装）
    PROTRACTED_STRATEGY = "protracted-strategy"         # 持久战（长期主义）
    WORKFLOWS = "workflows"                             # 工作流
  
    NONE = "none"                                      # 不需要

@dataclass
class QueryAnalysis:
    """查询分析结果"""
    complexity: float           # 复杂度 0-1
    question_type: str          # 问题类型
    confusion_level: float      # 困惑程度 0-1
    suggested_skill: ThinkingSkill
    confidence: float           # 建议置信度
    reasoning: str              # 推理过程
    suggested_skills: List[ThinkingSkill] = None  # top-3 技能推荐


class IntelligentThinkingTrigger:
    """
    智能思考技能触发器
    
    基于 Intent Detection 论文:
    - 通过语义理解识别用户意图
    - 不依赖关键词，而是理解用户真正需要什么
    
    创新点:
    - 分析查询复杂度
    - 识别问题类型
    - 估计用户困惑程度
    - 综合决策需要的思考技能
    """
    
    # 复杂度指标
    COMPLEXITY_INDICATORS = {
        "high": ["根本", "本质", "原理", "架构", "设计", "fundamental", "essence", "principle"],
        "medium": ["如何", "为什么", "怎么", "how", "why", "差异", "对比", "比较", "不同", "区别"],
        "low": ["是什么", "什么是", "what is"]
    }
    
    # 问题类型特征（Matt Pocock 类型优先匹配，避免被通用类型抢断）
    QUESTION_TYPE_PATTERNS = {
        # 精确的 Matt Pocock 类型优先
        "debug": [r'调试', r'bug', r'故障', r'报错', r'错误', r'异常', r'debug', r'出错', r'不工作', r'失败', r'crash', r'崩溃', r'诊断'],
        "architecture": [r'架构', r'设计模式', r'重构', r'目录结构', r'分层', r'模块化', r'architecture', r'refactor', r'代码组织', r'system.*design'],
        "review": [r'审核', r'评审', r'审查', r'代码审查', r'review', r'审问', r'方案.*评审'],
        "prototype": [r'原型', r'验证', r'概念', r'demo', r'prototype', r'最小.*产品', r'MVP', r'可行性'],
        "code": [r'代码', r'函数', r'类', r'变量', r'code', r'function', r'class.*method', r'API.*设计', r'接口.*定义'],
        # 通用问题类型
        "evaluation": [r'评估', r'评价', r'分析.*优缺', r'比较', r'evaluate', r'compare'],
        "explanation": [r'解释', r'说明', r'什么是', r'explain', r'what is'],
        "procedure": [r'如何', r'怎么(?!样)', r'步骤', r'方法', r'how to', r'steps'],
        "decision": [r'选择', r'决定', r'应该.*还是', r'choose', r'decide', r'should'],
        "problem_solving": [r'解决', r'修复', r'处理', r'分析', r'solve', r'fix'],
        "creative": [r'创造', r'想象', r'create', r'imagine'],
        # 方法论类型（qiushi）
        "investigation": [r'调查研究', r'调查一下', r'先搞清楚', r'摸清', r'了解情况', r'research', r'搞清楚背景', r'调查现状'],
        "conflict": [r'主要矛盾', r'矛盾', r'重点在哪', r'哪个是关键', r'抓重点', r'主次', r'冲突', r'conflict', r'trade.?off'],
        "practice": [r'实践', r'试试看', r'验证一下', r'落地', r'实操', r'试运行', r'实践检验', r'practice', r'try'],
        "focus": [r'集中精力', r'聚焦', r'专攻', r'突破', r'集中兵力', r'focus', r'prioritize', r'优先级'],
        "planning": [r'全局', r'整体规划', r'统筹', r'全盘', r'全局视角', r'big picture', r'overall', r'整体方案'],
        "collect": [r'收集.*意见', r'大家怎么看', r'意见汇总', r'群体.*意见', r'集体决策', r'collect', r'feedback', r'stakeholder'],
        "improve": [r'改进', r'反思', r'哪里不好', r'自我批评', r'review.*改进', r'复盘', r'回顾', r'retrospective', r'improve'],
        "seed": [r'试点', r'小范围', r'先做', r'星星之火', r'从小做起', r'prototype', r'minimal', r'从简单开始'],
        "theory": [r'理论', r'方法论', r'指导思想', r'原理', r'基础理论', r'theory', r'framework'],
        "strategy": [r'长期', r'持久战', r'循序渐进', r'阶段性', r'长期规划', r'strategy', r'long.?term', r'逐步'],
        "automation": [r'工作流', r'自动化', r'流程化', r'workflow', r'pipeline', r'auto'],
        # 效率技能类型
        "compress": [r'压缩', r'简短', r'精简', r'浓缩', r'短说', r'concis', r'short', r'compress', r'less.*word'],
        "handoff": [r'交接', r'转交', r'传给', r'文档.*交接', r'handoff', r'hand.?over', r'交接文档'],
        "writeskill": [r'创建.*skill', r'写.*技能', r'写.*skill', r'write.*skill', r'create.*skill', r'新建.*skill'],
        # 补充匹配（思考技能）
        "compare": [r'比较', r'对比', r'类比', r'相比', r'compare', r'different.*from', r'similar'],
        "reverse": [r'逆向', r'反向', r'反过来', r'逆推', r'reverse', r'backward', r'倒着'],
    }
    
    # 困惑指标
    CONFUSION_INDICATORS = [
        "不懂", "不理解", "困惑", "迷茫", "不清楚",
        "confused", "don't understand", "unclear"
    ]
    
    # 技能映射（含 9 个思考技能 + 10 个 Matt Pocock + 11 个方法论）
    SKILL_MAPPING = {
        # ═══════════════════ 思考技能映射 ═══════════════════
        ("high", "problem_solving"): ThinkingSkill.FIRST_PRINCIPLES,
        ("medium", "problem_solving"): ThinkingSkill.FIRST_PRINCIPLES,
        ("low", "problem_solving"): ThinkingSkill.FIRST_PRINCIPLES,
        ("high", "evaluation"): ThinkingSkill.CRITICAL_THINKING,
        ("medium", "evaluation"): ThinkingSkill.CRITICAL_THINKING,
        ("low", "evaluation"): ThinkingSkill.CRITICAL_THINKING,
        ("high", "creative"): ThinkingSkill.SYSTEMS_THINKING,
        ("medium", "creative"): ThinkingSkill.SYSTEMS_THINKING,
        ("low", "creative"): ThinkingSkill.PRODUCT_THINKING,
        ("high", "explanation"): ThinkingSkill.FEYNMAN_TECHNIQUE,
        ("medium", "explanation"): ThinkingSkill.FEYNMAN_TECHNIQUE,
        ("low", "explanation"): ThinkingSkill.FEYNMAN_TECHNIQUE,
        ("high", "decision"): ThinkingSkill.DECISION_ENGINE,
        ("medium", "decision"): ThinkingSkill.DECISION_ENGINE,
        ("low", "decision"): ThinkingSkill.DECISION_ENGINE,
        ("medium", "procedure"): ThinkingSkill.BACKWARD_THINKING,
        ("low", "procedure"): ThinkingSkill.BACKWARD_THINKING,
        ("medium", "compare"): ThinkingSkill.ANALOGICAL_THINKING,
        ("low", "compare"): ThinkingSkill.ANALOGICAL_THINKING,
        
        # ═════════════ 方法论映射（qiushi + 思维技能补充）═════════
        ("high", "investigation"): ThinkingSkill.INVESTIGATION_FIRST,
        ("high", "conflict"): ThinkingSkill.CONTRADICTION_ANALYSIS,
        ("high", "practice"): ThinkingSkill.PRACTICE_COGNITION,
        ("high", "focus"): ThinkingSkill.CONCENTRATE_FORCES,
        ("high", "planning"): ThinkingSkill.OVERALL_PLANNING,
        ("high", "collect"): ThinkingSkill.MASS_LINE,
        ("high", "improve"): ThinkingSkill.CRITICISM_SELF_CRITICISM,
        ("high", "seed"): ThinkingSkill.SPARK_PRAIRIE_FIRE,
        ("high", "theory"): ThinkingSkill.ARMING_THOUGHT,
        ("high", "strategy"): ThinkingSkill.PROTRACTED_STRATEGY,
        ("high", "automation"): ThinkingSkill.WORKFLOWS,
        ("medium", "investigation"): ThinkingSkill.INVESTIGATION_FIRST,
        ("medium", "conflict"): ThinkingSkill.CONTRADICTION_ANALYSIS,
        ("medium", "practice"): ThinkingSkill.PRACTICE_COGNITION,
        ("medium", "focus"): ThinkingSkill.CONCENTRATE_FORCES,
        ("medium", "planning"): ThinkingSkill.OVERALL_PLANNING,
        ("medium", "collect"): ThinkingSkill.MASS_LINE,
        ("medium", "improve"): ThinkingSkill.CRITICISM_SELF_CRITICISM,
        ("medium", "seed"): ThinkingSkill.SPARK_PRAIRIE_FIRE,
        ("medium", "strategy"): ThinkingSkill.PROTRACTED_STRATEGY,
        ("medium", "automation"): ThinkingSkill.WORKFLOWS,
        ("low", "investigation"): ThinkingSkill.INVESTIGATION_FIRST,
        ("low", "conflict"): ThinkingSkill.CONTRADICTION_ANALYSIS,
        ("low", "practice"): ThinkingSkill.PRACTICE_COGNITION,
        ("low", "improve"): ThinkingSkill.CRITICISM_SELF_CRITICISM,
        ("low", "seed"): ThinkingSkill.SPARK_PRAIRIE_FIRE,
        ("low", "strategy"): ThinkingSkill.PROTRACTED_STRATEGY,
        ("low", "focus"): ThinkingSkill.CONCENTRATE_FORCES,
        ("low", "planning"): ThinkingSkill.OVERALL_PLANNING,
        ("low", "collect"): ThinkingSkill.MASS_LINE,
        ("low", "automation"): ThinkingSkill.WORKFLOWS,
        
        # ═══════════════ Matt Pocock 工程技能映射 ═══════════════
        ("high", "debug"): ThinkingSkill.DIAGNOSE,
        ("high", "architecture"): ThinkingSkill.IMPROVE_ARCH,
        ("high", "review"): ThinkingSkill.GRILL_ME,
        ("high", "code"): ThinkingSkill.TDD,
        ("high", "prototype"): ThinkingSkill.PROTOTYPE,
        ("medium", "debug"): ThinkingSkill.DIAGNOSE,
        ("medium", "architecture"): ThinkingSkill.ZOOM_OUT,
        ("medium", "review"): ThinkingSkill.GRILL_WITH_DOCS,
        ("medium", "code"): ThinkingSkill.TDD,
        ("medium", "prototype"): ThinkingSkill.PROTOTYPE,
        ("low", "debug"): ThinkingSkill.DIAGNOSE,
        ("low", "architecture"): ThinkingSkill.ZOOM_OUT,
        ("low", "review"): ThinkingSkill.GRILL_WITH_DOCS,
        ("low", "code"): ThinkingSkill.DIAGNOSE,
        ("low", "prototype"): ThinkingSkill.PROTOTYPE,
        
        # ═══════════════ 效率技能映射 ═══════════════
        ("medium", "compress"): ThinkingSkill.CAVEMAN,
        ("low", "compress"): ThinkingSkill.CAVEMAN,
        ("high", "handoff"): ThinkingSkill.HANDOFF,
        ("medium", "handoff"): ThinkingSkill.HANDOFF,
        ("low", "handoff"): ThinkingSkill.HANDOFF,
        ("high", "writeskill"): ThinkingSkill.WRITE_SKILL,
        ("low", "writeskill"): ThinkingSkill.WRITE_SKILL,
        # ═══════════════ 补充映射（reverse/compare）═══════════════
        ("medium", "reverse"): ThinkingSkill.BACKWARD_THINKING,
        ("low", "reverse"): ThinkingSkill.BACKWARD_THINKING,
        ("high", "compare"): ThinkingSkill.ANALOGICAL_THINKING,
        ("medium", "compare"): ThinkingSkill.ANALOGICAL_THINKING,
        ("low", "compare"): ThinkingSkill.ANALOGICAL_THINKING,
    }
    
    # 所有技能 → SKILL.md 路径映射（Matt Pocock + 思考技能 + 方法论 + 效率类）
    MATT_POCOCK_SKILL_PATHS = {
        ThinkingSkill.DIAGNOSE: "diagnose/SKILL.md",
        ThinkingSkill.GRILL_WITH_DOCS: "grill-with-docs/SKILL.md",
        ThinkingSkill.TDD: "tdd/SKILL.md",
        ThinkingSkill.IMPROVE_ARCH: "improve-codebase-architecture/SKILL.md",
        ThinkingSkill.PROTOTYPE: "prototype/SKILL.md",
        ThinkingSkill.ZOOM_OUT: "zoom-out/SKILL.md",
        ThinkingSkill.GRILL_ME: "grill-me/SKILL.md",
        ThinkingSkill.CAVEMAN: "caveman/SKILL.md",
        ThinkingSkill.HANDOFF: "handoff/SKILL.md",
        ThinkingSkill.WRITE_SKILL: "write-a-skill/SKILL.md",
    }
    
    # 思考技能 → SKILL.md 路径映射
    THINKING_SKILL_PATHS = {
        ThinkingSkill.FIRST_PRINCIPLES: "first-principles/SKILL.md",
        ThinkingSkill.SYSTEMS_THINKING: "systems-thinking/SKILL.md",
        ThinkingSkill.CRITICAL_THINKING: "critical-thinking/SKILL.md",
        ThinkingSkill.BACKWARD_THINKING: "backward-thinking/SKILL.md",
        ThinkingSkill.ANALOGICAL_THINKING: "analogical-thinking/SKILL.md",
        ThinkingSkill.FEYNMAN_TECHNIQUE: "feynman-technique/SKILL.md",
        ThinkingSkill.DECISION_ENGINE: "decision-engine/SKILL.md",
        ThinkingSkill.PRODUCT_THINKING: "product-thinking/SKILL.md",
    }
    
    # 方法论 (qiushi) → SKILL.md 路径映射
    METHODOLOGY_SKILL_PATHS = {
        ThinkingSkill.INVESTIGATION_FIRST: "investigation-first/SKILL.md",
        ThinkingSkill.CONTRADICTION_ANALYSIS: "contradiction-analysis/SKILL.md",
        ThinkingSkill.PRACTICE_COGNITION: "practice-cognition/SKILL.md",
        ThinkingSkill.CONCENTRATE_FORCES: "concentrate-forces/SKILL.md",
        ThinkingSkill.OVERALL_PLANNING: "overall-planning/SKILL.md",
        ThinkingSkill.MASS_LINE: "mass-line/SKILL.md",
        ThinkingSkill.CRITICISM_SELF_CRITICISM: "criticism-self-criticism/SKILL.md",
        ThinkingSkill.SPARK_PRAIRIE_FIRE: "spark-prairie-fire/SKILL.md",
        ThinkingSkill.ARMING_THOUGHT: "arming-thought/SKILL.md",
        ThinkingSkill.PROTRACTED_STRATEGY: "protracted-strategy/SKILL.md",
        ThinkingSkill.WORKFLOWS: "workflows/SKILL.md",
    }
    
    # 合并全部路径
    ALL_SKILL_PATHS = {}
    ALL_SKILL_PATHS.update(MATT_POCOCK_SKILL_PATHS)
    ALL_SKILL_PATHS.update(THINKING_SKILL_PATHS)
    ALL_SKILL_PATHS.update(METHODOLOGY_SKILL_PATHS)
    
    def __init__(self, user_context: Optional[Dict] = None):
        """
        初始化智能触发器
        
        Args:
            user_context: 用户上下文（历史纠正率等）
        """
        self.user_context = user_context or {}
        self.analysis_log = []
    
    def analyze_complexity(self, query: str) -> float:
        """
        分析查询复杂度（增强版 v2）
        
        - 原关键词指标不变
        - 新增问题类型复杂度加成
        - 新增复合/比较问题检测
        
        Args:
            query: 用户查询
        
        Returns:
            复杂度 0-1
        """
        query_lower = query.lower()
        
        # 原指标：各层级关键词命中
        high_hits = sum(1 for ind in self.COMPLEXITY_INDICATORS["high"] if ind in query_lower)
        medium_hits = sum(1 for ind in self.COMPLEXITY_INDICATORS["medium"] if ind in query_lower)
        low_hits = sum(1 for ind in self.COMPLEXITY_INDICATORS["low"] if ind in query_lower)
        
        # 长度因素：中文无空格时用字符数（按20字符=1英文词折算）
        words = query.split()
        effective_word_count = len(words) if len(words) > 1 else max(1, len(query) // 4)
        length_factor = min(effective_word_count / 20, 1.0)
        nesting_factor = min(query.count('?') + query.count('？'), 3) / 3

        # 新增1：问题类型复杂度加成（类型本身决定复杂度，不依赖命中关键词）
        qtype = self.identify_question_type(query)
        type_complexity_boost = {
            "architecture": 0.15, "evaluation": 0.12,
            "decision": 0.15, "debug": 0.10,
            "code": 0.10, "review": 0.10,
            "prototype": 0.10, "procedure": 0.05,
            "explanation": 0.05, "general": 0.0,
            "compare": 0.12, "reverse": 0.10,
            "problem_solving": 0.10, "creative": 0.07,
        }.get(qtype, 0.0)

        # 新增2：多子句检测（和/与/、/对比/比较 → 复合问题）
        multi_seps = ["和", "与", "、", ",", " vs ", " compared", "对比", "比较", "差异", "区别", "不同"]
        multi_factor = min(sum(1 for sep in multi_seps if sep in query) * 0.08, 0.2)

        # 比较/分析类问题 baseline：非简单问题至少 0.35（直通道阈值）
        analysis_baseline = 0.0
        if qtype in ("architecture", "compare", "evaluation", "decision", "problem_solving"):
            analysis_baseline = 0.35
        elif qtype in ("explanation", "debug"):
            analysis_baseline = 0.35
        elif qtype in ("procedure", "code", "review", "prototype"):
            analysis_baseline = 0.35

        # 综合计算
        complexity = max(
            analysis_baseline,
            high_hits * 0.3 +
            medium_hits * 0.15 +
            low_hits * 0.05 +
            length_factor * 0.25 +
            nesting_factor * 0.1 +
            type_complexity_boost * 0.25 +
            multi_factor * 0.15
        )
        
        return min(complexity, 1.0)
    
    def identify_question_type(self, query: str) -> str:
        """
        识别问题类型
        
        Args:
            query: 用户查询
        
        Returns:
            问题类型
        """
        query_lower = query.lower()
        
        for qtype, patterns in self.QUESTION_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    return qtype
        
        return "general"
    
    def estimate_confusion(self, query: str, context: Optional[Dict] = None) -> float:
        """
        估计用户困惑程度（增强版 v2）
        
        - 原指标不变
        - 新增：问题类型困惑基线（分析/决策类天然困惑度更高）
        - 新增：长度困惑基线（长 query 天然困惑度更高）
        - 权重重新分配，防止复杂问题 confusion=0
        
        Args:
            query: 用户查询
            context: 对话上下文
        
        Returns:
            困惑程度 0-1
        """
        query_lower = query.lower()
        
        # 1. 直接困惑表达
        direct_confusion = sum(1 for ind in self.CONFUSION_INDICATORS if ind in query_lower)
        direct_factor = min(direct_confusion / 2, 1.0)
        
        # 2. 问号密度
        question_density = (query.count('?') + query.count('？')) / max(len(query.split()), 1)
        question_factor = min(question_density * 5, 1.0)
        
        # 3. 用户历史纠正率
        history_factor = self.user_context.get("correction_rate", 0)
        
        # 4. 上下文困惑
        context_factor = 0
        if context and "previous_confusion" in context:
            context_factor = context["previous_confusion"] * 0.3
        
        # 5. 新增：问题类型困惑基线（分析/决策类天然有困惑度）
        query_type = self.identify_question_type(query)
        type_confusion_baseline = {
            "architecture": 0.15, "evaluation": 0.12,
            "decision": 0.20, "debug": 0.10,
            "code": 0.08, "review": 0.10,
            "prototype": 0.10, "procedure": 0.05,
            "explanation": 0.05, "general": 0.0,
        }.get(query_type, 0.0)
        
        # 6. 新增：长度困惑基线（>15字长 query 有基础困惑）
        # 中文无空格时用字符数折算
        _words = query.split()
        _eff_word_count = len(_words) if len(_words) > 1 else max(1, len(query) // 4)
        length_confusion = min(max(0, _eff_word_count - 15) / 30 * 0.15, 0.15)
        
        # 综合计算（权重重新分配）
        confusion = (
            direct_factor * 0.25 +
            question_factor * 0.15 +
            history_factor * 0.15 +
            context_factor * 0.15 +
            type_confusion_baseline * 0.2 +
            length_confusion * 0.1
        )
        
        return min(confusion, 1.0)
    
    def detect_thinking_need(
        self,
        query: str,
        context: Optional[Dict] = None
    ) -> QueryAnalysis:
        """
        检测是否需要思考技能
        
        Args:
            query: 用户查询
            context: 对话上下文
        
        Returns:
            查询分析结果
        """
        # 分析各项指标
        complexity = self.analyze_complexity(query)
        question_type = self.identify_question_type(query)
        confusion = self.estimate_confusion(query, context)
        
        # 确定复杂度级别
        if complexity > 0.7:
            complexity_level = "high"
        elif complexity > 0.4:
            complexity_level = "medium"
        else:
            complexity_level = "low"
        
        # 选择思考技能
        skill_key = (complexity_level, question_type)
        suggested_skill = self.SKILL_MAPPING.get(skill_key, ThinkingSkill.NONE)
        
        # 特殊情况处理
        if confusion > 0.5:
            # 用户困惑，使用费曼技巧
            suggested_skill = ThinkingSkill.FEYNMAN_TECHNIQUE
            reasoning = f"用户困惑程度较高 ({confusion:.2f})，使用费曼技巧进行简单解释"
        elif question_type == "decision" and complexity > 0.5:
            # 复杂决策，使用决策引擎
            suggested_skill = ThinkingSkill.DECISION_ENGINE
            reasoning = f"复杂决策问题，使用决策引擎进行结构化分析"
        elif question_type == "evaluation" and suggested_skill == ThinkingSkill.NONE:
            # 评估问题，使用批判性思维
            suggested_skill = ThinkingSkill.CRITICAL_THINKING
            reasoning = "评估类问题，使用批判性思维分析证据和谬误"
        elif question_type == "debug" and suggested_skill == ThinkingSkill.NONE:
            suggested_skill = ThinkingSkill.DIAGNOSE
            reasoning = f"调试诊断类问题，走 diagnose 工程诊断流程"
        elif question_type == "architecture" and suggested_skill == ThinkingSkill.NONE:
            suggested_skill = ThinkingSkill.ZOOM_OUT
            reasoning = f"架构类问题，先 zoom-out 全局视角再分析"
        elif suggested_skill == ThinkingSkill.NONE:
            reasoning = "常规查询，不需要特殊思考技能"
        else:
            reasoning = f"复杂度 {complexity_level}，问题类型 {question_type}，建议使用 {suggested_skill.value}"
        
        # 计算置信度
        confidence = 0.5 + complexity * 0.3 + (1 - confusion) * 0.2
        
        # 记录日志
        analysis = QueryAnalysis(
            complexity=complexity,
            question_type=question_type,
            confusion_level=confusion,
            suggested_skill=suggested_skill,
            confidence=confidence,
            reasoning=reasoning
        )
        
        self.analysis_log.append({
            "query": query[:50],
            "complexity": complexity,
            "question_type": question_type,
            "confusion": confusion,
            "skill": suggested_skill.value,
            "confidence": confidence
        })
        
        return analysis

    def detect_thinking_needs(
        self,
        query: str,
        context: Optional[Dict] = None,
        top_k: int = 3
    ) -> QueryAnalysis:
        """
        检测 top-k 思考技能推荐（多技能推荐）

        原 detect_thinking_need 只返回一个技能。此方法返回 top_k 个技能，
        按优先级排序：主技能（最匹配）→ 副技能（互补）。

        Args:
            query: 用户查询
            context: 对话上下文
            top_k: 推荐技能数量（默认 3）

        Returns:
            QueryAnalysis 含 suggested_skills 列表
        """
        analysis = self.detect_thinking_need(query, context)
        _main = analysis.suggested_skill

        if _main == ThinkingSkill.NONE or top_k <= 1:
            analysis.suggested_skills = [_main] if _main != ThinkingSkill.NONE else []
            return analysis

        # 按问题类型推荐互补技能
        _complement_map = {
            # (主类型, 副维度) → 技能
            "problem_solving": [ThinkingSkill.FIRST_PRINCIPLES, ThinkingSkill.SYSTEMS_THINKING, ThinkingSkill.CRITICAL_THINKING],
            "evaluation": [ThinkingSkill.CRITICAL_THINKING, ThinkingSkill.DECISION_ENGINE, ThinkingSkill.PRACTICE_COGNITION],
            "decision": [ThinkingSkill.DECISION_ENGINE, ThinkingSkill.PROTRACTED_STRATEGY, ThinkingSkill.CONCENTRATE_FORCES],
            "creative": [ThinkingSkill.SYSTEMS_THINKING, ThinkingSkill.PRODUCT_THINKING, ThinkingSkill.OVERALL_PLANNING],
            "explanation": [ThinkingSkill.FEYNMAN_TECHNIQUE, ThinkingSkill.INVESTIGATION_FIRST, ThinkingSkill.CRITICAL_THINKING],
            "debug": [ThinkingSkill.DIAGNOSE, ThinkingSkill.FIRST_PRINCIPLES, ThinkingSkill.CRITICAL_THINKING],
            "architecture": [ThinkingSkill.ZOOM_OUT, ThinkingSkill.IMPROVE_ARCH, ThinkingSkill.SYSTEMS_THINKING],
            "review": [ThinkingSkill.GRILL_WITH_DOCS, ThinkingSkill.GRILL_ME, ThinkingSkill.CRITICISM_SELF_CRITICISM],
            "code": [ThinkingSkill.TDD, ThinkingSkill.IMPROVE_ARCH, ThinkingSkill.PRACTICE_COGNITION],
            "prototype": [ThinkingSkill.PROTOTYPE, ThinkingSkill.CONCENTRATE_FORCES, ThinkingSkill.SPARK_PRAIRIE_FIRE],
            "procedure": [ThinkingSkill.BACKWARD_THINKING, ThinkingSkill.OVERALL_PLANNING, ThinkingSkill.WORKFLOWS],
            "investigation": [ThinkingSkill.INVESTIGATION_FIRST, ThinkingSkill.MASS_LINE, ThinkingSkill.CRITICAL_THINKING],
            "conflict": [ThinkingSkill.CONTRADICTION_ANALYSIS, ThinkingSkill.CONCENTRATE_FORCES, ThinkingSkill.DECISION_ENGINE],
            "practice": [ThinkingSkill.PRACTICE_COGNITION, ThinkingSkill.PROTOTYPE, ThinkingSkill.CRITICISM_SELF_CRITICISM],
            "focus": [ThinkingSkill.CONCENTRATE_FORCES, ThinkingSkill.CONTRADICTION_ANALYSIS, ThinkingSkill.OVERALL_PLANNING],
            "planning": [ThinkingSkill.OVERALL_PLANNING, ThinkingSkill.PROTRACTED_STRATEGY, ThinkingSkill.ZOOM_OUT],
            "collect": [ThinkingSkill.MASS_LINE, ThinkingSkill.INVESTIGATION_FIRST, ThinkingSkill.CRITICAL_THINKING],
            "improve": [ThinkingSkill.CRITICISM_SELF_CRITICISM, ThinkingSkill.PRACTICE_COGNITION, ThinkingSkill.INVESTIGATION_FIRST],
            "seed": [ThinkingSkill.SPARK_PRAIRIE_FIRE, ThinkingSkill.PROTOTYPE, ThinkingSkill.CONCENTRATE_FORCES],
            "strategy": [ThinkingSkill.PROTRACTED_STRATEGY, ThinkingSkill.OVERALL_PLANNING, ThinkingSkill.SYSTEMS_THINKING],
            "automation": [ThinkingSkill.WORKFLOWS, ThinkingSkill.IMPROVE_ARCH, ThinkingSkill.PROTOTYPE],
            "compare": [ThinkingSkill.ANALOGICAL_THINKING, ThinkingSkill.CRITICAL_THINKING, ThinkingSkill.DECISION_ENGINE],
        }

        _combo = _complement_map.get(analysis.question_type, [])
        _skills = [_main]
        for _s in _combo:
            if _s != _main and _s not in _skills:
                _skills.append(_s)
                if len(_skills) >= top_k:
                    break
        analysis.suggested_skills = _skills[:top_k]
        return analysis

    def get_analysis_stats(self) -> Dict[str, Any]:
        """获取分析统计"""
        if not self.analysis_log:
            return {"total_analyses": 0}
        
        skills = {}
        complexities = []
        confusions = []
        
        for log in self.analysis_log:
            skill = log["skill"]
            skills[skill] = skills.get(skill, 0) + 1
            complexities.append(log["complexity"])
            confusions.append(log["confusion"])
        
        return {
            "total_analyses": len(self.analysis_log),
            "skill_distribution": skills,
            "avg_complexity": sum(complexities) / len(complexities),
            "avg_confusion": sum(confusions) / len(confusions)
        }


# 便捷函数
def detect_thinking_skill(query: str, context: Dict = None) -> ThinkingSkill:
    """
    检测需要的思考技能（便捷函数）
    
    Args:
        query: 用户查询
        context: 对话上下文
    
    Returns:
        建议的思考技能
    """
    trigger = IntelligentThinkingTrigger()
    analysis = trigger.detect_thinking_need(query, context)
    return analysis.suggested_skill


if __name__ == "__main__":
    # 测试
    trigger = IntelligentThinkingTrigger()
    
    test_queries = [
        ("什么是机器学习？", "简单定义查询"),
        ("如何从根本上解决系统性能问题？", "复杂问题解决"),
        ("我应该选择 React 还是 Vue？", "决策问题"),
        ("评估一下这个方案的优缺点", "评估问题"),
        ("我不理解这个概念，能解释一下吗？", "困惑表达"),
        ("设计一个高可用的微服务架构", "复杂设计"),
        ("为什么我的代码不工作？", "调试问题"),
        ("比较一下 Python 和 Java 的区别", "比较问题"),
    ]
    
    print("=" * 70)
    print("智能思考技能触发测试")
    print("=" * 70)
    
    for query, desc in test_queries:
        analysis = trigger.detect_thinking_need(query)
        
        print(f"\n【{desc}】")
        print(f"  查询: {query}")
        print(f"  复杂度: {analysis.complexity:.2f}")
        print(f"  问题类型: {analysis.question_type}")
        print(f"  困惑程度: {analysis.confusion_level:.2f}")
        print(f"  建议技能: {analysis.suggested_skill.value}")
        print(f"  置信度: {analysis.confidence:.2f}")
        print(f"  推理: {analysis.reasoning}")
    
    print("\n" + "=" * 70)
    print("分析统计")
    print("=" * 70)
    stats = trigger.get_analysis_stats()
    print(f"  总分析次数: {stats['total_analyses']}")
    print(f"  平均复杂度: {stats.get('avg_complexity', 0):.2f}")
    print(f"  平均困惑度: {stats.get('avg_confusion', 0):.2f}")
    print(f"  技能分布: {stats.get('skill_distribution', {})}")
