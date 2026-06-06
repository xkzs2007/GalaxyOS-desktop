"""
智能思考技能触发器 v2.0

论文集成:
- RCR-Router (arXiv 2508.04903): 动态评分 + 贪心路由替代静态映射
- Springdrift (arXiv 2604.04660): CBR 记忆 + sensorium 持续感知 
- A-ToM (AAAI 2026): 认知阶段推断 + 阶段对齐推荐

Author: 小艺 Claw
Version: 2.0.0
Created: 2026-06-05
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import re
import time
import json
import os


# ════════════════════════════════════════════════════════
# 1. 导入新模块（RCR-Router + Springdrift）
# ════════════════════════════════════════════════════════

try:
    from skill_scorer import SkillScorer, ThinkingSkill, ScoredSkill, SKILL_CATALOG
    _HAS_SCORER = True
except ImportError:
    _HAS_SCORER = False
    ThinkingSkill = None

try:
    from thinking_memory import ThinkingMemory, ThinkingCase, Sensorium
    _HAS_MEMORY = True
except ImportError:
    _HAS_MEMORY = False
    ThinkingMemory = None
    ThinkingCase = None


# ════════════════════════════════════════════════════════
# 2. 类型定义
# ════════════════════════════════════════════════════════

if not _HAS_SCORER:
    class ThinkingSkill(Enum):
        """思考技能类型（兜底定义）"""
        FIRST_PRINCIPLES = "first-principles"
        SYSTEMS_THINKING = "systems-thinking"
        CRITICAL_THINKING = "critical-thinking"
        BACKWARD_THINKING = "backward-thinking"
        ANALOGICAL_THINKING = "analogical-thinking"
        FEYNMAN_TECHNIQUE = "feynman-technique"
        DECISION_ENGINE = "decision-engine"
        PRODUCT_THINKING = "product-thinking"
        DIAGNOSE = "diagnose"
        GRILL_WITH_DOCS = "grill-with-docs"
        TDD = "tdd"
        IMPROVE_ARCH = "improve-codebase-architecture"
        PROTOTYPE = "prototype"
        ZOOM_OUT = "zoom-out"
        GRILL_ME = "grill-me"
        CAVEMAN = "caveman"
        HANDOFF = "handoff"
        WRITE_SKILL = "write-a-skill"
        INVESTIGATION_FIRST = "investigation-first"
        CONTRADICTION_ANALYSIS = "contradiction-analysis"
        PRACTICE_COGNITION = "practice-cognition"
        CONCENTRATE_FORCES = "concentrate-forces"
        OVERALL_PLANNING = "overall-planning"
        MASS_LINE = "mass-line"
        CRITICISM_SELF_CRITICISM = "criticism-self-criticism"
        SPARK_PRAIRIE_FIRE = "spark-prairie-fire"
        ARMING_THOUGHT = "arming-thought"
        PROTRACTED_STRATEGY = "protracted-strategy"
        WORKFLOWS = "workflows"
        NONE = "none"


@dataclass
class QueryAnalysis:
    """查询分析结果"""
    complexity: float
    question_type: str
    confusion_level: float
    suggested_skill: ThinkingSkill
    confidence: float
    reasoning: str
    suggested_skills: List[ThinkingSkill] = None
    cognitive_stage: str = "explore"   # A-ToM: 认知阶段
    analysis_source: str = "v2"        # 标识使用新引擎


# ════════════════════════════════════════════════════════
# 3. 升级版 IntelligentThinkingTrigger
# ════════════════════════════════════════════════════════

class IntelligentThinkingTrigger:
    """
    智能思考技能触发器 v2.0

    架构升级:
    ┌──────────────────────┐
    │  IntelligentThought  │ ← A-ToM: 认知阶段推断
    │  Trigger             │
    │  ┌────────────────┐  │
    │  │ SkillScorer    │  │ ← RCR-Router: 动态评分+贪心路由
    │  │ (RCR-论文)     │  │
    │  ├────────────────┤  │
    │  │ ThinkingMemory │  │ ← Springdrift: CBR记忆+传感器
    │  │ (CBR+传感器)   │  │
    │  └────────────────┘  │
    └──────────────────────┘
    """

    # 兼容旧接口：保留原有的常量（v2 实际不用它们做映射，但 fallback 用）
    COMPLEXITY_INDICATORS = {
        "high": ["根本", "本质", "原理", "架构", "设计", "fundamental", "essence", "principle"],
        "medium": ["如何", "为什么", "怎么", "how", "why", "差异", "对比", "比较", "不同", "区别"],
        "low": ["是什么", "什么是", "what is"]
    }
    QUESTION_TYPE_PATTERNS = {
        "debug": [r'调试', r'bug', r'故障', r'报错', r'错误', r'异常', r'debug', r'出错', r'不工作', r'失败', r'crash', r'崩溃', r'诊断', r'超时', r'timeout', r'响应慢', r'慢', r'连接不上', r'连不上', r'不通'],
        "architecture": [r'架构', r'设计模式', r'重构', r'目录结构', r'分层', r'模块化', r'architecture', r'refactor', r'代码组织', r'system.*design'],
        "review": [r'审核', r'评审', r'审查', r'代码审查', r'review', r'审问', r'方案.*评审'],
        "prototype": [r'原型', r'验证', r'概念', r'demo', r'prototype', r'最小.*产品', r'MVP', r'可行性'],
        "code": [r'代码', r'函数', r'类', r'变量', r'code', r'function', r'class.*method', r'API.*设计', r'接口.*定义'],
        "evaluation": [r'评估', r'评价', r'分析.*优缺', r'比较', r'evaluate', r'compare'],
        "explanation": [r'解释', r'说明', r'什么是', r'explain', r'what is'],
        "procedure": [r'如何', r'怎么(?!样|从根)', r'步骤', r'方法', r'how to', r'steps'],
        "decision": [r'选择', r'决定', r'应该.*还是', r'该选', r'选哪个', r'选什么', r'哪个好', r'choose', r'decide', r'should'],
        "problem_solving": [r'解决', r'修复', r'处理', r'分析', r'solve', r'fix', r'从根本上'],
        "creative": [r'创造', r'想象', r'create', r'imagine'],
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
        "status_report": [r'状态报告', r'运行状态', r'当前状态', r'报告一下', r'汇报一下', r'总结一下', r'梳理一下', r'状态查询', r'状态检查', r'状态.*报告', r'运行.*报告', r'看看.*状态', r'系统状态', r'运行情况', r'状态.*怎么样'],
        "changelog": [r'修了哪些', r'修改了', r'改了啥', r'做了什么', r'干了啥', r'变更', r'改动', r'changelog', r'改了什么', r'修复了'],
        "retrospective": [r'应该是.*啊', r'为什么.*不', r'为什么.*没', r'不用.*干啥', r'之前.*干啥', r'怎么.*不', r'怎么没', r'怎么会', r'为何.*不用', r'为何.*不'],
        "meta": [r'推荐.*什么', r'什么能力', r'推荐.*能力', r'你怎么.*判断', r'你.*推荐', r'推荐.*思考', r'推荐.*技能', r'推荐什么'],
        "compress": [r'压缩', r'简短', r'精简', r'浓缩', r'短说', r'concis', r'short', r'compress', r'less.*word'],
        "handoff": [r'交接', r'转交', r'传给', r'文档.*交接', r'handoff', r'hand.?over', r'交接文档'],
        "writeskill": [r'创建.*skill', r'写.*技能', r'写.*skill', r'write.*skill', r'create.*skill', r'新建.*skill'],
        "compare": [r'比较', r'对比', r'类比', r'相比', r'compare', r'different.*from', r'similar'],
        "reverse": [r'逆向', r'反向', r'反过来', r'逆推', r'reverse', r'backward', r'倒着'],
    }
    CONFUSION_INDICATORS = [
        "不懂", "不理解", "困惑", "迷茫", "不清楚",
        "confused", "don't understand", "unclear"
    ]

    def __init__(self, user_context: Optional[Dict] = None,
                 session_id: str = "default",
                 use_memory: bool = True):
        """
        初始化 v2.0 触发器
        
        Args:
            user_context: 用户上下文（兼容旧接口）
            session_id: Springdrift 会话 ID
            use_memory: 是否启用 CBR 记忆层
        """
        self.user_context = user_context or {}
        self.session_id = session_id
        self.analysis_log = []

        # ── v2 新组件 ──
        self._scorer = SkillScorer() if _HAS_SCORER else None
        self._memory = None
        if use_memory and _HAS_MEMORY:
            self._memory = ThinkingMemory(session_id=session_id)

        self._has_v2 = _HAS_SCORER

    # ════════════════════════════════════════════════════
    # 旧接口：保持兼容
    # ════════════════════════════════════════════════════

    def analyze_complexity(self, query: str) -> float:
        return self._legacy_complexity(query)

    def identify_question_type(self, query: str) -> str:
        return self._legacy_qtype(query)

    def estimate_confusion(self, query: str, context: Optional[Dict] = None) -> float:
        return self._legacy_confusion(query, context)

    def detect_thinking_need(self, query: str,
                             context: Optional[Dict] = None) -> QueryAnalysis:
        """
        v2.0 主入口（单技能推荐）
        
        使用三论文联合引擎:
        1. A-ToM: 认知阶段推断
        2. RCR-Router: 动态评分
        3. Springdrift: CBR 历史+ sensorium
        """
        if self._has_v2:
            return self._v2_analyze(query, context, top_k=1)
        return self._legacy_analyze(query, context, top_k=1)

    def detect_thinking_needs(self, query: str,
                              context: Optional[Dict] = None,
                              top_k: int = 3) -> QueryAnalysis:
        """
        v2.0 多技能推荐（top-k）
        
        使用三论文联合引擎:
        1. RCR-Router 贪心路由 + token 预算
        2. A-ToM 阶段对齐
        3. Springdrift CBR 历史修正
        """
        if self._has_v2:
            return self._v2_analyze(query, context, top_k=top_k)
        return self._legacy_analyze(query, context, top_k=top_k)

    def get_analysis_stats(self) -> Dict[str, Any]:
        stats = {"total_analyses": len(self.analysis_log)}
        if self._memory:
            stats["memory_cases"] = self._memory.sensorium.total_cases
            stats["sensorium"] = self._memory.get_sensorium_state()
        if self.analysis_log:
            skills = {}
            for log in self.analysis_log:
                skill = log.get("skill", "none")
                skills[skill] = skills.get(skill, 0) + 1
            stats["skill_distribution"] = skills
        return stats

    # ════════════════════════════════════════════════════
    # v2 核心引擎（三论文集成）
    # ════════════════════════════════════════════════════

    def _v2_analyze(self, query: str,
                    context: Optional[Dict],
                    top_k: int = 1) -> QueryAnalysis:
        """
        三论文联合推理引擎

        Step 1 (A-ToM): 从查询+上下文推断认知阶段
        Step 2 (RCR-Router): 动态评分所有技能
        Step 3 (Springdrift): CBR 历史修正评分
        Step 4: 贪心路由 + token 预算 → top-k
        Step 5 (Springdrift): 记录案例 + 更新 sensorium
        """
        # ── 基础分析 ──
        complexity = self._legacy_complexity(query)
        question_type = self._legacy_qtype(query)
        confusion = self._legacy_confusion(query, context)

        # ── Step 1: A-ToM 认知阶段推断 ──
        cognitive_stage = self._infer_cognitive_stage(
            query, complexity, confusion, context
        )

        # ── Step 2-3: RCR-Router 动态评分 + Springdrift 历史修正 ──
        history_weights = {}
        if self._memory:
            history_weights = self._memory.get_history_weights()
            # 更新 sensorium
            corr_rate = self.user_context.get("correction_rate", 0)
            self._memory.update_sensorium(
                query, complexity, confusion, corr_rate
            )

        try:
            scored = self._scorer.score_all(
                query=query,
                question_type=question_type,
                cognitive_stage=cognitive_stage,
                history_weights=history_weights,
                token_budget=top_k,
            )
        except Exception:
            # fallback
            return self._legacy_analyze(query, context, top_k=top_k)

        # ── Step 4: 取 top-k 结果 ──
        if not scored:
            scored = []

        primary = scored[0] if scored else None
        suggested_skill = primary.skill if primary else ThinkingSkill.NONE
        suggested_skills = [s.skill for s in scored[:top_k]]
        confidence = primary.score if primary else 0.5

        # 推理说明
        reasoning = self._build_reasoning(
            scored, question_type, cognitive_stage, confusion
        )

        # ── Step 5: 记录案例 ──
        if self._memory and suggested_skill != ThinkingSkill.NONE:
            import hashlib
            case = ThinkingCase(
                query=query,
                query_hash=hashlib.md5(query.encode('utf-8')).hexdigest(),
                question_type=question_type,
                cognitive_stage=cognitive_stage,
                recommended_skills=[s.value for s in suggested_skills],
                timestamp=time.time(),
                session_id=self.session_id,
            )
            self._memory.add_case(case)

        # ── 构建结果 ──
        analysis = QueryAnalysis(
            complexity=complexity,
            question_type=question_type,
            confusion_level=confusion,
            suggested_skill=suggested_skill,
            confidence=min(confidence, 1.0),
            reasoning=reasoning,
            suggested_skills=suggested_skills,
            cognitive_stage=cognitive_stage,
            analysis_source="v2",
        )

        self._log_analysis(query, analysis)
        return analysis

    # ════════════════════════════════════════════════════
    # A-ToM: 认知阶段推断
    # ════════════════════════════════════════════════════

    COGNITIVE_PATTERNS = {
        "explore": [
            # 探索期：概念入门、选型推荐
            r'什么是', r'有哪些', r'有什么', r'怎么用', r'介绍',
            r'推荐', r'好用的', r'值得', r'入门', r'基础',
            r'概念', r'what is', r'introduction', r'overview',
            r'推荐什么', r'什么能力', r'方案推荐', r'选哪个',
            r'哪个好', r'选什么',
        ],
        "analyze": [
            # 分析期：对比、设计、架构、本质（不含'为什么'——它在 verify）
            r'对比', r'架构', r'设计', r'区别',
            r'分析', r'评估', r'关系', r'compare',
            r'difference', r'architecture', r'本质', r'原理',
            r'优缺点', r'方案', r'系统',
        ],
        "verify": [
            # 验证/调试期：问题错误、bug排查、'为什么'出错了
            r'问题', r'错误', r'测试', r'bug', r'检查',
            r'validate', r'verify', r'debug', r'对不对',
            r'哪里不对', r'确认', r'核查',
            r'不工作', r'出错', r'出问题', r'报错', r'异常',
            r'崩溃', r'故障', r'失败', r'不兼容', r'超时', r'timeout', r'连接不上', r'慢', r'排查',
            r'为什么.*不', r'为什么.*没', r'什么原因',
        ],
        "stuck": [
            # 卡住：不懂、困惑、不知怎么办
            r'不懂', r'不理解', r'不明白', r'怎么解决',
            r'怎么办', r'困惑', r'confused', r'stuck',
            r'卡住', r'不清楚', r'不会', r'怎么处理',
        ],
    }

    def _infer_cognitive_stage(self, query: str,
                                complexity: float,
                                confusion: float,
                                context: Optional[Dict] = None) -> str:
        """
        A-ToM 认知阶段推断

        综合判断：
        1. 困惑度优先（高困惑 → stuck）
        2. 关键词模式匹配
        3. 复杂度兜底
        4. sensorium 交叉验证
        """
        ql = query.lower()
        stage_scores = {"explore": 0.0, "analyze": 0.0,
                        "verify": 0.0, "stuck": 0.0}

        # 1. 困惑度优先
        if confusion > 0.6:
            stage_scores["stuck"] += 0.6
        elif confusion > 0.3:
            stage_scores["stuck"] += 0.2

        # 2. 关键词匹配
        for stage, patterns in self.COGNITIVE_PATTERNS.items():
            hits = sum(1 for p in patterns if re.search(p, ql))
            if hits > 0:
                stage_scores[stage] += min(hits * 0.3, 0.8)

        # 3. 复杂度修正
        if complexity > 0.6 and confusion < 0.3:
            stage_scores["analyze"] += 0.3
        elif complexity > 0.4 and confusion < 0.3:
            stage_scores["analyze"] += 0.15

        # 4. 简短短查询 → explore（中文兼容）
        _has_cjk = bool(re.search(r'[一-鿿]', query))
        if _has_cjk:
            _eff = max(1, len(query) // 2)  # 中文每2字≈1词
        else:
            _eff = len(query.split()) if len(query.split()) > 1 else max(1, len(query) // 5)
        if _eff < 3:  # 1-2个词/字才是真简单
            stage_scores["explore"] += 0.3

        # 5. Springdrift sensorium 交叉验证
        if self._memory:
            mem_stage = self._memory.sensorium.infer_cognitive_stage()
            stage_scores[mem_stage] += 0.2

        # 取最高分阶段
        best_stage = max(stage_scores, key=stage_scores.get)
        if stage_scores[best_stage] == 0:
            best_stage = "explore"

        return best_stage

    # ════════════════════════════════════════════════════
    # 推理说明生成
    # ════════════════════════════════════════════════════

    def _build_reasoning(self, scored: List['ScoredSkill'],
                         question_type: str,
                         cognitive_stage: str,
                         confusion: float) -> str:
        """生成推理说明"""
        if not scored:
            return "没有找到匹配的思考技能"

        parts = []
        # 认知阶段
        stage_names = {
            "explore": "探索期", "analyze": "分析期",
            "verify": "验证期", "stuck": "卡住",
        }
        stage_cn = stage_names.get(cognitive_stage, cognitive_stage)
        parts.append(f"认知阶段: {stage_cn}")

        # 问题类型
        parts.append(f"类型: {question_type}")

        # 困惑度
        if confusion > 0.3:
            parts.append(f"困惑度中高({confusion:.2f})")

        # top-1 评分明细
        top = scored[0]
        detail = []
        if top.semantic_score > 0.1:
            detail.append(f"语义匹配{top.semantic_score:.2f}")
        if top.stage_score > 0.5:
            detail.append(f"阶段对齐{top.stage_score:.2f}")
        if top.history_score != 0.5:  # 非中性
            detail.append(f"历史修正{top.history_score:.2f}")

        if detail:
            parts.append(f"主推: {top.descriptor.name} ({', '.join(detail)})")
        else:
            parts.append(f"主推: {top.descriptor.name}")

        # top-2, top-3
        if len(scored) > 1:
            others = ", ".join(s.descriptor.name for s in scored[1:])
            parts.append(f"备选: {others}")

        return " | ".join(parts)

    # ════════════════════════════════════════════════════
    # 旧版 fallback（v1 完全兼容）
    # ════════════════════════════════════════════════════

    SKILL_MAPPING = {
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
        ("medium", "compress"): ThinkingSkill.CAVEMAN,
        ("low", "compress"): ThinkingSkill.CAVEMAN,
        ("high", "handoff"): ThinkingSkill.HANDOFF,
        ("medium", "handoff"): ThinkingSkill.HANDOFF,
        ("low", "handoff"): ThinkingSkill.HANDOFF,
        ("high", "writeskill"): ThinkingSkill.WRITE_SKILL,
        ("low", "writeskill"): ThinkingSkill.WRITE_SKILL,
        ("medium", "reverse"): ThinkingSkill.BACKWARD_THINKING,
        ("low", "reverse"): ThinkingSkill.BACKWARD_THINKING,
        ("high", "compare"): ThinkingSkill.ANALOGICAL_THINKING,
        ("medium", "compare"): ThinkingSkill.ANALOGICAL_THINKING,
        ("low", "compare"): ThinkingSkill.ANALOGICAL_THINKING,
        ("high", "status_report"): ThinkingSkill.ZOOM_OUT,
        ("medium", "status_report"): ThinkingSkill.ZOOM_OUT,
        ("low", "status_report"): ThinkingSkill.ZOOM_OUT,
        ("high", "changelog"): ThinkingSkill.PRACTICE_COGNITION,
        ("medium", "changelog"): ThinkingSkill.PRACTICE_COGNITION,
        ("low", "changelog"): ThinkingSkill.PRACTICE_COGNITION,
        ("high", "retrospective"): ThinkingSkill.CRITICISM_SELF_CRITICISM,
        ("medium", "retrospective"): ThinkingSkill.CRITICISM_SELF_CRITICISM,
        ("low", "retrospective"): ThinkingSkill.CRITICISM_SELF_CRITICISM,
        ("high", "meta"): ThinkingSkill.INVESTIGATION_FIRST,
        ("medium", "meta"): ThinkingSkill.INVESTIGATION_FIRST,
        ("low", "meta"): ThinkingSkill.INVESTIGATION_FIRST,
    }

    # 技能路径映射
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
    ALL_SKILL_PATHS = {}
    ALL_SKILL_PATHS.update(MATT_POCOCK_SKILL_PATHS)
    ALL_SKILL_PATHS.update(THINKING_SKILL_PATHS)
    ALL_SKILL_PATHS.update(METHODOLOGY_SKILL_PATHS)

    def _legacy_complexity(self, query: str) -> float:
        # 保持 v1 复杂度计算不变
        ql = query.lower()
        high_hits = sum(1 for ind in self.COMPLEXITY_INDICATORS["high"] if ind in ql)
        medium_hits = sum(1 for ind in self.COMPLEXITY_INDICATORS["medium"] if ind in ql)
        low_hits = sum(1 for ind in self.COMPLEXITY_INDICATORS["low"] if ind in ql)
        words = query.split()
        eff_cnt = len(words) if len(words) > 1 else max(1, len(query) // 4)
        length_factor = min(eff_cnt / 20, 1.0)
        nesting = min(query.count('?') + query.count('？'), 3) / 3

        qtype = self._legacy_qtype(query)
        type_boost = {
            "architecture": 0.15, "evaluation": 0.12,
            "decision": 0.15, "debug": 0.10,
            "code": 0.10, "review": 0.10,
            "prototype": 0.10, "procedure": 0.05,
            "explanation": 0.05, "general": 0.0,
            "compare": 0.12, "reverse": 0.10,
            "problem_solving": 0.10, "creative": 0.07,
            "status_report": 0.10, "changelog": 0.10,
            "retrospective": 0.15, "meta": 0.10,
        }.get(qtype, 0.0)

        multi_seps = ["和", "与", "、", ",", " vs ", " compared",
                      "对比", "比较", "差异", "区别", "不同"]
        multi_factor = min(sum(1 for sep in multi_seps if sep in ql) * 0.08, 0.2)

        analysis_base = 0.0
        if qtype in ("architecture", "compare", "evaluation", "decision", "problem_solving"):
            analysis_base = 0.35
        elif qtype in ("explanation", "debug"):
            analysis_base = 0.35
        elif qtype in ("procedure", "code", "review", "prototype"):
            analysis_base = 0.35
        elif qtype in ("status_report", "changelog", "retrospective", "meta"):
            analysis_base = 0.50

        complexity = max(
            analysis_base,
            high_hits * 0.3 + medium_hits * 0.15 + low_hits * 0.05
            + length_factor * 0.25 + nesting * 0.1
            + type_boost * 0.25 + multi_factor * 0.15
        )
        return min(complexity, 1.0)

    def _legacy_qtype(self, query: str) -> str:
        ql = query.lower()
        for qtype, patterns in self.QUESTION_TYPE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, ql):
                    return qtype
        return "general"

    def _legacy_confusion(self, query: str, context: Optional[Dict] = None) -> float:
        ql = query.lower()
        direct_conf = sum(1 for ind in self.CONFUSION_INDICATORS if ind in ql)
        direct_factor = min(direct_conf / 2, 1.0)
        question_density = (query.count('?') + query.count('？')) / max(len(query.split()), 1)
        question_factor = min(question_density * 5, 1.0)
        history_factor = self.user_context.get("correction_rate", 0)
        context_factor = 0
        if context and "previous_confusion" in context:
            context_factor = context["previous_confusion"] * 0.3

        qtype = self._legacy_qtype(query)
        type_bs = {
            "architecture": 0.15, "evaluation": 0.12,
            "decision": 0.20, "debug": 0.10,
            "code": 0.08, "review": 0.10,
            "prototype": 0.10, "procedure": 0.05,
            "explanation": 0.05, "general": 0.0,
            "status_report": 0.05, "changelog": 0.10,
            "retrospective": 0.12, "meta": 0.10,
        }.get(qtype, 0.0)

        _w = query.split()
        _eff = len(_w) if len(_w) > 1 else max(1, len(query) // 4)
        length_conf = min(max(0, _eff - 15) / 30 * 0.15, 0.15)

        confusion = (
            direct_factor * 0.25 + question_factor * 0.15
            + history_factor * 0.15 + context_factor * 0.15
            + type_bs * 0.2 + length_conf * 0.1
        )
        return min(confusion, 1.0)

    def _legacy_analyze(self, query: str, context: Optional[Dict],
                        top_k: int = 1) -> QueryAnalysis:
        """v1 fallback (原 logic)"""
        complexity = self._legacy_complexity(query)
        question_type = self._legacy_qtype(query)
        confusion = self._legacy_confusion(query, context)

        if complexity > 0.7:
            cl = "high"
        elif complexity > 0.4:
            cl = "medium"
        else:
            cl = "low"

        skill_key = (cl, question_type)
        suggested_skill = self.SKILL_MAPPING.get(skill_key, ThinkingSkill.NONE)

        if confusion > 0.5:
            suggested_skill = ThinkingSkill.FEYNMAN_TECHNIQUE
            reasoning = f"用户困惑程度较高 ({confusion:.2f})，使用费曼技巧"
        elif question_type == "debug" and suggested_skill == ThinkingSkill.NONE:
            suggested_skill = ThinkingSkill.DIAGNOSE
            reasoning = "调试诊断类问题，走 diagnose 工程诊断流程"
        elif question_type == "status_report" and suggested_skill == ThinkingSkill.NONE:
            suggested_skill = ThinkingSkill.ZOOM_OUT
            reasoning = f"状态汇总类问题，先 zoom-out 全局视角梳理全貌"
        elif question_type == "retrospective" and suggested_skill == ThinkingSkill.NONE:
            suggested_skill = ThinkingSkill.CRITICISM_SELF_CRITICISM
            reasoning = f"追问/纠正类问题，批评与自我批评：反思为何没做对"
        elif question_type == "meta" and suggested_skill == ThinkingSkill.NONE:
            suggested_skill = ThinkingSkill.INVESTIGATION_FIRST
            reasoning = f"元问题：先搞清楚系统的决策逻辑"
        elif suggested_skill == ThinkingSkill.NONE:
            reasoning = "常规查询，不需要特殊思考技能"
        else:
            reasoning = f"复杂度 {cl}，问题类型 {question_type}，建议使用 {suggested_skill.value}"

        confidence = 0.5 + complexity * 0.3 + (1 - confusion) * 0.2

        suggested_skills = [suggested_skill]
        if top_k > 1 and suggested_skill != ThinkingSkill.NONE:
            complements = {
                "problem_solving": [ThinkingSkill.FIRST_PRINCIPLES, ThinkingSkill.SYSTEMS_THINKING, ThinkingSkill.CRITICAL_THINKING],
                "evaluation": [ThinkingSkill.CRITICAL_THINKING, ThinkingSkill.DECISION_ENGINE, ThinkingSkill.PRACTICE_COGNITION],
                "decision": [ThinkingSkill.DECISION_ENGINE, ThinkingSkill.PROTRACTED_STRATEGY, ThinkingSkill.CONCENTRATE_FORCES],
                "creative": [ThinkingSkill.SYSTEMS_THINKING, ThinkingSkill.PRODUCT_THINKING, ThinkingSkill.OVERALL_PLANNING],
            }
            combo = complements.get(question_type, [])
            for s in combo:
                if s != suggested_skill and s not in suggested_skills:
                    suggested_skills.append(s)
                    if len(suggested_skills) >= top_k:
                        break
            suggested_skills = suggested_skills[:top_k]

        analysis = QueryAnalysis(
            complexity=complexity,
            question_type=question_type,
            confusion_level=confusion,
            suggested_skill=suggested_skill,
            confidence=confidence,
            reasoning=reasoning,
            suggested_skills=suggested_skills,
            analysis_source="v1",
        )
        self._log_analysis(query, analysis)
        return analysis

    def _log_analysis(self, query: str, analysis: QueryAnalysis):
        self.analysis_log.append({
            "query": query[:50],
            "complexity": analysis.complexity,
            "question_type": analysis.question_type,
            "confusion": analysis.confusion_level,
            "skill": analysis.suggested_skill.value,
            "confidence": analysis.confidence,
            "cognitive_stage": analysis.cognitive_stage,
            "source": analysis.analysis_source,
        })


# ════════════════════════════════════════════════════════
# 便捷函数
# ════════════════════════════════════════════════════════

def detect_thinking_skill(query: str, context: Dict = None) -> ThinkingSkill:
    """便捷函数：检测需要的思考技能"""
    trigger = IntelligentThinkingTrigger()
    analysis = trigger.detect_thinking_need(query, context)
    return analysis.suggested_skill


# ════════════════════════════════════════════════════════
# 测试
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    trigger = IntelligentThinkingTrigger(session_id="test")

    test_queries = [
        ("什么是机器学习？", "简单定义查询"),
        ("如何从根本上解决系统性能问题？", "复杂问题解决"),
        ("我应该选择 React 还是 Vue？", "决策问题"),
        ("评估一下这个方案的优缺点", "评估问题"),
        ("我不理解这个概念，能解释一下吗？", "困惑表达"),
        ("设计一个高可用的微服务架构", "复杂设计"),
        ("为什么我的代码不工作？", "调试问题"),
        ("比较一下 Python 和 Java 的区别", "比较问题"),
        ("帮我看看今天系统运行状态", "状态汇总"),
        ("你之前为什么不用这个方案", "复盘质问"),
        ("推荐什么思考能力给我", "元问题"),
    ]

    print("=" * 75)
    print("智能思考技能触发测试 v2.0")
    print(f"引擎模式: {'RCR+Springdrift+A-ToM (v2)' if _HAS_SCORER else 'v1 fallback'}")
    print("=" * 75)

    for query, desc in test_queries:
        analysis = trigger.detect_thinking_needs(query, top_k=3)

        v2_tag = "🆕 v2" if analysis.analysis_source == "v2" else "v1"
        print(f"\n【{desc}】{v2_tag}")
        print(f"  查询: {query}")
        print(f"  复杂度: {analysis.complexity:.2f}")
        print(f"  问题类型: {analysis.question_type}")
        print(f"  认知阶段: {analysis.cognitive_stage}")
        print(f"  困惑程度: {analysis.confusion_level:.2f}")
        print(f"  建议技能: {[s.value for s in (analysis.suggested_skills or [])]}")
        print(f"  置信度: {analysis.confidence:.2f}")
        print(f"  推理: {analysis.reasoning}")

    print("\n" + "=" * 75)
    print("分析统计")
    print("=" * 75)
    stats = trigger.get_analysis_stats()
    print(f"  总分析次数: {stats['total_analyses']}")
    print(f"  v2 引擎: {_HAS_SCORER}")
    print(f"  CBR 记忆: {_HAS_MEMORY}")
    if "skill_distribution" in stats:
        print(f"  技能分布: {stats['skill_distribution']}")
    if "memory_cases" in stats:
        print(f"  记忆案例: {stats['memory_cases']}")
        s = stats.get("sensorium", {})
        print(f"  Sensorium:")
        print(f"    采纳率: {s.get('skill_adoption_rate', 0):.2f}")
        print(f"    纠正率: {s.get('correction_rate', 0):.2f}")
