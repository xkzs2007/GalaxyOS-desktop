"""
skill_scorer.py — RCR-Router 动态技能评分引擎

论文:
- RCR-Router: Efficient Role-Aware Context Routing for Multi-Agent LLM Systems (arXiv 2508.04903)

核心机制:
1. 轻量评分策略：技能语义相似度 + 角色相关性 + 阶段优先级 + 时序新鲜度
2. 贪心路由：token 预算控制下最大化整体重要性评分
3. 迭代反馈：历史推荐+用户反馈影响未来评分

替换 IntelligentThinkingTrigger 原有的 SKILL_MAPPING 静态映射表。
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import os
import time
import re


# ── 技能定义 ──

class ThinkingSkill(Enum):
    """思考技能类型（与 intelligent_thinking_trigger.py 保持同步）"""
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
class SkillDescriptor:
    """技能描述元数据（RCR-Router 的角色/阶段定义）"""
    skill: ThinkingSkill
    name: str
    description: str                  # 语义描述（用于 embedding 匹配）
    tags: List[str] = field(default_factory=list)  # 标签（用于关键词兜底）
    role_category: str = "general"    # 角色分类: thinking/engineering/methodology
    best_stage: str = "any"           # 最适阶段: explore/analyze/verify/stuck/any
    priority: float = 1.0             # 基础优先级（0-1）
    token_estimate: int = 200         # 加载此技能 SKILL.md 的 token 估算
    complementary: List[str] = field(default_factory=list)  # 互补技能列表


Skill = ThinkingSkill  # 别名


# ── 技能目录（论文来源 + 语义描述） ──

SKILL_CATALOG: List[SkillDescriptor] = [
    # ═══ 通用思考技能 ═══
    SkillDescriptor(
        skill=Skill.FIRST_PRINCIPLES,
        name="第一性原理",
        description="从最基本的不可动摇的事实或假设出发，推导出问题的本质解决方案。"
                    "不依赖类比或已有框架，而是回归物理定律、数学公理或业务核心逻辑。"
                    "适用于根本性分析、颠覆式创新、打破思维定式。",
        tags=["本质", "原理", "根源", "基础", "假设", "推导", "逻辑链条"],
        role_category="thinking",
        best_stage="analyze",
        priority=0.85,
        complementary=["systems-thinking", "critical-thinking"],
    ),
    SkillDescriptor(
        skill=Skill.SYSTEMS_THINKING,
        name="系统思维",
        description="将问题视为一个整体系统，分析要素之间的相互作用、反馈回路、涌现行为。"
                    "关注系统结构而非个别事件，识别杠杆点和延迟效应。"
                    "适用于复杂架构、跨领域问题、组织变革。",
        tags=["系统", "架构", "整体", "要素", "反馈", "涌现", "耦合"],
        role_category="thinking",
        best_stage="analyze",
        priority=0.85,
        complementary=["first-principles", "overall-planning"],
    ),
    SkillDescriptor(
        skill=Skill.CRITICAL_THINKING,
        name="批判性思维",
        description="系统性地审视论点的前提、逻辑和证据。识别谬误、偏见、假设的漏洞。"
                    "区分事实与观点，评估证据的充分性。"
                    "适用于方案评估、决策验证、争议分析。",
        tags=["评估", "判断", "证据", "谬误", "逻辑", "分析"],
        role_category="thinking",
        best_stage="verify",
        priority=0.80,
        complementary=["decision-engine", "evaluation"],
    ),
    SkillDescriptor(
        skill=Skill.BACKWARD_THINKING,
        name="逆向思维",
        description="从期望的结果出发，反向推导需要满足的前置条件和步骤。"
                    "识别关键路径上的障碍和必要条件。"
                    "适用于目标规划、路径设计、风险识别。",
        tags=["反向", "倒推", "目标", "路径", "条件", "可行性", "从结果"],
        role_category="thinking",
        best_stage="explore",
        priority=0.70,
        complementary=["first-principles", "overall-planning"],
    ),
    SkillDescriptor(
        skill=Skill.ANALOGICAL_THINKING,
        name="类比思维",
        description="将当前问题映射到已知的类似问题上，借用已有解决方案或模式。"
                    "识别跨领域的结构相似性，进行知识迁移。"
                    "适用于技术选型、方案比较、创新设计。",
        tags=["类比", "比较", "映射", "借鉴", "模式", "相似"],
        role_category="thinking",
        best_stage="explore",
        priority=0.65,
        complementary=["first-principles", "critical-thinking"],
    ),
    SkillDescriptor(
        skill=Skill.FEYNMAN_TECHNIQUE,
        name="费曼技巧",
        description="用最通俗的语言解释复杂概念。先尝试说清楚自己理解的内容，"
                    "发现卡点就回去查资料，直到能用大白话讲明白。"
                    "适用于学习、教学、概念澄清、知识检验。",
        tags=["解释", "通俗", "简化", "教学", "澄清", "大白话", "理解"],
        role_category="thinking",
        best_stage="stuck",
        priority=0.90,
        complementary=["investigation-first", "workflows"],
    ),
    SkillDescriptor(
        skill=Skill.DECISION_ENGINE,
        name="决策引擎",
        description="结构化决策分析：定义决策目标、列出可选方案、评估标准、权衡。"
                    "使用决策矩阵、加权评分、敏感性分析等方法。"
                    "适用于关键抉择、资源配置、不确定环境下的选择。",
        tags=["决策", "选择", "权衡", "优先级", "矩阵", "评分"],
        role_category="thinking",
        best_stage="verify",
        priority=0.85,
        complementary=["critical-thinking", "concentrate-forces"],
    ),
    SkillDescriptor(
        skill=Skill.PRODUCT_THINKING,
        name="产品思维",
        description="从用户需求和价值出发思考问题。关注用户体验、痛点、使用场景。"
                    "用 MVP 思维快速验证假设。"
                    "适用于产品设计、功能规划、用户体验优化。",
        tags=["产品", "用户", "需求", "体验", "场景", "价值", "MVP"],
        role_category="thinking",
        best_stage="explore",
        priority=0.60,
        complementary=["prototype", "overall-planning"],
    ),

    # ═══ Matt Pocock 工程技能 ═══
    SkillDescriptor(
        skill=Skill.DIAGNOSE,
        name="系统化 Bug 诊断",
        description="结构化调试方法：复现问题→隔离变量→验证假设→缩小范围→定位根因。"
                    "适用于故障排查、bug 修复、性能分析。",
        tags=["调试", "诊断", "故障", "Bug", "定位", "复现", "排查"],
        role_category="engineering",
        best_stage="verify",
        priority=0.90,
        complementary=["first-principles", "investigation-first"],
        token_estimate=300,
    ),
    SkillDescriptor(
        skill=Skill.GRILL_WITH_DOCS,
        name="需求审问",
        description="用文档和代码证据严格审核需求/方案的合理性。检查边界条件、异常路径、"
                    "性能影响，确保方案经得起推敲。",
        tags=["审核", "审问", "方案", "边界", "异常"],
        role_category="engineering",
        best_stage="verify",
        priority=0.75,
        complementary=["critical-thinking", "contradiction-analysis"],
    ),
    SkillDescriptor(
        skill=Skill.TDD,
        name="测试驱动开发",
        description="红-绿-重构循环：先写测试再写实现。在代码变更前建立测试安全网。"
                    "适用于代码开发、功能实现、重构。",
        tags=["测试", "TDD", "开发", "重构", "测试驱动"],
        role_category="engineering",
        best_stage="verify",
        priority=0.70,
        complementary=["practice-cognition", "improve-codebase-architecture"],
    ),
    SkillDescriptor(
        skill=Skill.IMPROVE_ARCH,
        name="架构改进",
        description="系统化的代码架构评估和改进：识别设计问题、提出重构方案、"
                    "评估影响范围、分步实施。",
        tags=["架构", "重构", "设计", "模块化", "耦合", "代码组织"],
        role_category="engineering",
        best_stage="analyze",
        priority=0.80,
        complementary=["systems-thinking", "zoom-out"],
    ),
    SkillDescriptor(
        skill=Skill.PROTOTYPE,
        name="快速原型验证",
        description="用最小可行原型快速验证想法。聚焦核心假设，忽略边缘情况。"
                    "适用于概念验证、可行性分析、技术选型验证。",
        tags=["原型", "验证", "概念证", "MVP", "快速", "demo"],
        role_category="engineering",
        best_stage="explore",
        priority=0.70,
        complementary=["product-thinking", "concentrate-forces"],
    ),
    SkillDescriptor(
        skill=Skill.ZOOM_OUT,
        name="全局视角",
        description="从当前问题中抽身出来，看更大的画面。梳理依赖关系、影响范围、"
                    "上下游链路，给出全景图。"
                    "适用于状态汇总、架构梳理、复杂系统理解。",
        tags=["全局", "整体", "全景", "大图", "梳理", "汇总"],
        role_category="engineering",
        best_stage="explore",
        priority=0.75,
        complementary=["systems-thinking", "investigation-first"],
    ),
    SkillDescriptor(
        skill=Skill.GRILL_ME,
        name="方案推敲",
        description="对自己的方案进行压力测试。模拟各种质疑和反对意见，"
                    "找出方案的薄弱环节。适用于方案评审前的自我检查。",
        tags=["推敲", "质疑", "压力测试", "自检", "方案评审"],
        role_category="engineering",
        best_stage="verify",
        priority=0.70,
        complementary=["critical-thinking", "criticism-self-criticism"],
    ),
    SkillDescriptor(
        skill=Skill.CAVEMAN,
        name="超压缩通信",
        description="用最少的文字传递最核心的信息。去除修饰、冗余和背景铺垫。"
                    "适用于状态同步、紧急协、上下文受限的回复。",
        tags=["精简", "压缩", "简短", "浓缩", "高效"],
        role_category="engineering",
        best_stage="any",
        priority=0.50,
        token_estimate=100,
    ),
    SkillDescriptor(
        skill=Skill.HANDOFF,
        name="会话交接",
        description="结构化地交接工作上下文。包括当前状态、已完成事项、待办事项、"
                    "已知风险、关键决策记录。适用于任务交接、团队协作。",
        tags=["交接", "转交", "协作", "上下文", "文档"],
        role_category="engineering",
        best_stage="any",
        priority=0.55,
        token_estimate=300,
    ),
    SkillDescriptor(
        skill=Skill.WRITE_SKILL,
        name="编写新 Skill",
        description="按照 Skill 标准模板编写新的 OpenClaw 技能。包含元数据、指令、"
                    "脚本、示例。适用于将重复性工作技能化。",
        tags=["创建", "技能", "Skill", "模板", "自动化"],
        role_category="engineering",
        best_stage="explore",
        priority=0.60,
        token_estimate=400,
    ),

    # ═══ 方法论技能 (qiushi) ═══
    SkillDescriptor(
        skill=Skill.INVESTIGATION_FIRST,
        name="调查研究",
        description="没有调查就没有发言权。先搞清楚问题的背景、现状、数据、"
                    "利益相关方和约束条件，再给结论。"
                    "适用于需要先了解情况再决策的场景。",
        tags=["调查", "研究", "摸底", "了解", "背景", "现状"],
        role_category="methodology",
        best_stage="explore",
        priority=0.90,
        complementary=["mass-line", "critical-thinking"],
    ),
    SkillDescriptor(
        skill=Skill.CONTRADICTION_ANALYSIS,
        name="矛盾分析",
        description="抓主要矛盾。在复杂局面中找出最关键的对立关系和约束条件。"
                    "识别矛盾的主次方面，确定工作重心。"
                    "适用于资源有限需要抓重点的场景。",
        tags=["矛盾", "重点", "主次", "关键", "冲突", "权衡"],
        role_category="methodology",
        best_stage="analyze",
        priority=0.85,
        complementary=["concentrate-forces", "decision-engine"],
    ),
    SkillDescriptor(
        skill=Skill.PRACTICE_COGNITION,
        name="实践认知",
        description="实践是检验真理的唯一标准。先干起来，在行动中获取反馈，"
                    "根据结果修正认知。适用于验证假设、优化方案、迭代改进。",
        tags=["实践", "验证", "落实", "检验", "迭代", "试运行"],
        role_category="methodology",
        best_stage="analyze",
        priority=0.80,
        complementary=["prototype", "spark-prairie-fire"],
    ),
    SkillDescriptor(
        skill=Skill.CONCENTRATE_FORCES,
        name="集中兵力",
        description="在关键问题上投入全部精力，避免分散资源。先解决主要矛盾，"
                    "次要矛盾让路。适用于优先级决策、资源分配。",
        tags=["聚焦", "突破", "集中", "优先级", "专注"],
        role_category="methodology",
        best_stage="explore",
        priority=0.75,
        complementary=["contradiction-analysis", "protracted-strategy"],
    ),
    SkillDescriptor(
        skill=Skill.OVERALL_PLANNING,
        name="统筹全局",
        description="从全局视角出发进行整体规划。识别各环节的依赖关系、"
                    "资源约束和时间窗口，制定可执行的路径。",
        tags=["全局", "规划", "统筹", "整体", "路径", "计划"],
        role_category="methodology",
        best_stage="explore",
        priority=0.80,
        complementary=["systems-thinking", "zoom-out"],
    ),
    SkillDescriptor(
        skill=Skill.MASS_LINE,
        name="群众路线",
        description="收集各方意见→整合分析→验证反馈。从分散的个体信息中"
                    "提炼出高质量决策依据。适用于群体决策、需求收集。",
        tags=["收集", "意见", "汇总", "群体", "反馈", "决策"],
        role_category="methodology",
        best_stage="explore",
        priority=0.60,
        complementary=["investigation-first", "overall-planning"],
    ),
    SkillDescriptor(
        skill=Skill.CRITICISM_SELF_CRITICISM,
        name="批评与自我批评",
        description="坦诚地回顾和反思：哪些做得好、哪些做得不好、为什么。"
                    "从失败中找教训，从成功中找经验。"
                    "适用于复盘、回顾、持续改进。",
        tags=["反思", "批评", "复盘", "回顾", "改进", "自我批评"],
        role_category="methodology",
        best_stage="verify",
        priority=0.80,
        complementary=["practice-cognition", "investigation-first"],
    ),
    SkillDescriptor(
        skill=Skill.SPARK_PRAIRIE_FIRE,
        name="星星之火",
        description="从小处着手，以点带面。先用最小规模验证可行性，"
                    "成功后再逐步扩展。适用于新想法落地、变革推进。",
        tags=["试点", "小范围", "突破", "以点带面", "先从"],
        role_category="methodology",
        best_stage="explore",
        priority=0.60,
        complementary=["prototype", "concentrate-forces"],
    ),
    SkillDescriptor(
        skill=Skill.ARMING_THOUGHT,
        name="武装思想",
        description="用成熟的方法论和理论体系指导实践。建立清晰的理论框架，"
                    "确保行动有据可依。适用于建立知识体系、学习新领域。",
        tags=["理论", "方法论", "思想", "框架", "体系"],
        role_category="methodology",
        best_stage="explore",
        priority=0.70,
        complementary=["feynman-technique", "first-principles"],
    ),
    SkillDescriptor(
        skill=Skill.PROTRACTED_STRATEGY,
        name="持久战",
        description="长期主义策略：分阶段推进、持久积累。不被短期波动干扰，"
                    "保持战略定力。适用于长期项目、能力建设、战略规划。",
        tags=["长期", "持久", "阶段", "渐进", "战略", "积累"],
        role_category="methodology",
        best_stage="analyze",
        priority=0.70,
        complementary=["overall-planning", "concentrate-forces"],
    ),
    SkillDescriptor(
        skill=Skill.WORKFLOWS,
        name="工作流",
        description="将重复性操作标准化为可执行的工作流。定义步骤、输入输出、"
                    "异常处理和验证环节。适用于自动化、流程化、操作标准化。",
        tags=["工作流", "自动化", "流程", "标准化", "pipeline"],
        role_category="methodology",
        best_stage="any",
        priority=0.70,
        complementary=["practice-cognition", "improve-codebase-architecture"],
    ),
]

# 构建查找映射
_SKILL_BY_VALUE = {s.skill.value: s for s in SKILL_CATALOG}
_SKILL_BY_ENUM = {s.skill: s for s in SKILL_CATALOG}


# ── 轻量评分引擎 ──

@dataclass
class ScoredSkill:
    """带评分的技能"""
    skill: Skill
    descriptor: SkillDescriptor
    score: float                   # 综合评分
    semantic_score: float = 0.0    # 语义匹配分（关键词）
    role_score: float = 0.0        # 角色适配分
    stage_score: float = 0.0       # 阶段适配分
    history_score: float = 0.0     # 历史反馈分
    freshness_score: float = 1.0   # 时序新鲜度（新技能鼓励探索）


class SkillScorer:
    """
    RCR-Router 风格的动态技能评分器

    RCR-Router 核心公式:
        π_route(C|R,S,M) = argmax Σ α(m; R_i, S_t)
        s.t. Σ TokenLength(m) ≤ B_i

    等价到 ThinkingTrigger:
        skill 替代 memory item
        role 替代 query/question_type
        stage 替代 cognitive_stage
        token_budget 替代 top_k/skill_count
    """

    def __init__(self, catalog: Optional[List[SkillDescriptor]] = None):
        self.catalog = catalog or SKILL_CATALOG
        self._build_index()

    def _build_index(self):
        """构建标签倒排索引"""
        self._tag_index: Dict[str, List[SkillDescriptor]] = {}
        for sd in self.catalog:
            for tag in sd.tags:
                self._tag_index.setdefault(tag, []).append(sd)

    # ── 评分组件 ──

    def compute_semantic_score(self, query: str, desc: SkillDescriptor) -> float:
        """语义匹配分（关键词+描述命中）
        
        简化版：用 tag 命中和描述命中来估算语义相似度。
        未来可升级为 embedding 匹配。
        """
        ql = query.lower()
        score = 0.0

        # tag 命中
        tag_hits = sum(1 for t in desc.tags if t.lower() in ql or t in ql)
        if tag_hits > 0:
            score += min(tag_hits / len(desc.tags) * 0.6, 0.6)

        # 描述关键词命中
        desc_words = re.findall(r'[\w\u4e00-\u9fff]+', desc.description.lower())
        desc_hits = sum(1 for w in desc_words[:50] if len(w) > 1 and w in ql)
        score += min(desc_hits / 30 * 0.4, 0.4)

        return min(score, 1.0)

    @staticmethod
    def compute_role_score(question_type: str, desc: SkillDescriptor) -> float:
        """角色适配分
        
        RCR-Router: 评估 skill 与 query 类型的适配程度。
        
        扩展类型映射，覆盖更多 question_type。
        """
        # role_category 匹配（扩展映射表）
        role_map = {
            "debug": "engineering",
            "architecture": "engineering",
            "code": "engineering",
            "review": "engineering",
            "prototype": "engineering",
            "planning": "methodology",
            "investigation": "methodology",
            "conflict": "methodology",
            "practice": "methodology",
            "focus": "methodology",
            "collect": "methodology",
            "improve": "methodology",
            "seed": "methodology",
            "theory": "methodology",
            "strategy": "methodology",
            "automation": "methodology",
            # 补充缺失的映射
            "evaluation": "thinking",       # 评估 → 批判性思维
            "decision": "thinking",         # 决策 → 决策引擎
            "explanation": "thinking",      # 解释 → 费曼技巧
            "problem_solving": "thinking",  # 问题解决 → 第一性原理
            "creative": "thinking",         # 创意 → 系统思维
            "compare": "thinking",          # 比较 → 类比思维
            "reverse": "thinking",          # 逆向 → 逆向思维
            "procedure": "thinking",        # 步骤 → 逆向思维
            "status_report": "engineering", # 状态报告 → zoom-out
            "changelog": "methodology",     # 变更日志 → 实践认知
            "retrospective": "methodology", # 复盘 → 批评与自我批评
            "meta": "thinking",             # 元问题 → 调查研究
            "compress": "engineering",      # 压缩 → caveman
            "handoff": "engineering",       # 交接 → handoff
            "writeskill": "engineering",    # 写 skill → write-a-skill
        }
        expected_role = role_map.get(question_type, "")
        if expected_role and desc.role_category == expected_role:
            return 1.0
        if not expected_role and desc.role_category == "thinking":
            return 0.7
        # tag 兜底：tag 命中时不管 role 都给分
        return 0.3

    @staticmethod
    def compute_stage_score(cognitive_stage: str, desc: SkillDescriptor) -> float:
        """阶段适配分（v2 抑制版）
        
        A-ToM 启发：技能推荐应与用户当前认知阶段对齐。
        认知阶段: explore / analyze / verify / stuck / any
        
        抑制逻辑：当语义匹配分低时（无关键词匹配），阶段分的权重也降低。
        避免"全上下文都不匹配但阶段对就赢了"的情况。
        """
        if desc.best_stage == cognitive_stage:
            return 1.0
        if desc.best_stage == "any":
            return 0.7

        # 相邻阶段分降低，防止无语义匹配时纯靠阶段取胜
        adj = {
            "explore": ["analyze"],
            "analyze": ["explore", "verify"],
            "verify": ["analyze", "stuck"],
            "stuck": ["verify", "explore"],
        }
        if cognitive_stage in adj.get(desc.best_stage, []):
            return 0.4  # 从 0.5 降到 0.4
        return 0.15  # 从 0.2 降到 0.15

    @staticmethod
    def compute_history_score(history_weights: Dict[str, float],
                              desc: SkillDescriptor) -> float:
        """历史反馈分
        
        Springdrift CBR 层：用历史推荐 + 用户反馈修正评分。
        正反馈加分，负反馈减分，无历史 = 中性。
        """
        key = desc.skill.value
        if key in history_weights:
            return max(0.0, min(1.0, history_weights[key]))
        return 0.5  # 中性

    # ── 综合评分 ──

    def score_all(self, query: str,
                  question_type: str = "general",
                  cognitive_stage: str = "explore",
                  history_weights: Optional[Dict[str, float]] = None,
                  token_budget: int = 3,
                  base_pri_factor: float = 0.3,
                  semantic_weight: float = 0.35,
                  role_weight: float = 0.20,
                  stage_weight: float = 0.20,
                  history_weight: float = 0.10,
                  freshness_weight: float = 0.05) -> List[ScoredSkill]:
        """对所有技能评分（RCR-Router 路由策略）
        
        Args:
            query: 用户查询文本
            question_type: 识别出的问题类型
            cognitive_stage: 认知阶段
            history_weights: {skill_key: weight} 历史反馈权重
            token_budget: 最多推荐几个 skill
            *weight: 各维度权重

        Returns:
            降序排列的 ScoredSkill 列表（前 token_budget 个为路由结果）
        """
        hw = history_weights or {}
        results = []

        for sd in self.catalog:
            s_score = self.compute_semantic_score(query, sd)
            r_score = self.compute_role_score(question_type, sd)
            st_score = self.compute_stage_score(cognitive_stage, sd)
            h_score = self.compute_history_score(hw, sd)

            # 基础优先级
            base = sd.priority * base_pri_factor

            # 综合得分（RCR-Router: α = Σ weighted_scores）
            total = (
                s_score * semantic_weight +
                r_score * role_weight +
                st_score * stage_weight +
                h_score * history_weight +
                base +
                freshness_weight * 0.5  # 新技能固定新鲜度偏置
            )

            results.append(ScoredSkill(
                skill=sd.skill,
                descriptor=sd,
                score=total,
                semantic_score=s_score,
                role_score=r_score,
                stage_score=st_score,
                history_score=h_score,
            ))

        # 降序排列
        results.sort(key=lambda x: x.score, reverse=True)

        # 贪心路由：在 token_budget 下取 top
        selected = []
        used_tokens = 0
        max_tokens = token_budget * 500  # 粗略预算：top-1=500, top-2=600, top-3=800
        for rs in results:
            if len(selected) >= token_budget:
                break
            est = rs.descriptor.token_estimate
            if used_tokens + est > max_tokens and len(selected) > 0:
                continue
            selected.append(rs)
            used_tokens += est

        return selected

    def skill_by_value(self, value: str) -> Optional[SkillDescriptor]:
        """按 value 查找技能描述"""
        return _SKILL_BY_VALUE.get(value)

    def skill_by_enum(self, skill: Skill) -> Optional[SkillDescriptor]:
        """按 enum 查找技能描述"""
        return _SKILL_BY_ENUM.get(skill)


# ── 便捷函数 ──

def score_skills(query: str,
                 question_type: str = "general",
                 cognitive_stage: str = "explore",
                 top_k: int = 3) -> List[ScoredSkill]:
    """便捷函数：获取 top-k 技能推荐"""
    scorer = SkillScorer()
    return scorer.score_all(
        query=query,
        question_type=question_type,
        cognitive_stage=cognitive_stage,
        token_budget=top_k,
    )
