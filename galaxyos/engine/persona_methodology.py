#!/usr/bin/env python3
"""
Persona-Methodology Matching Engine

根据用户人格底层架构自动匹配相应的推理方法论。

核心思想：
- 不同人格对同一问题会采用不同的思考路径
- 工程严谨主义者会先调查再动手
- 生态构建者会先全局再局部
- 架构师会先矛盾分析再集中兵力突破

集成链路:
  xiaoyi_claw_api.py → _init_methodology() → PersonaMethodologyEngine
  → intelligent_thinking_trigger.py (get_skill_for_query)
  → health_check()

Author: GalaxyOS
Version: 1.0.0
"""

import json
import os
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from enum import Enum


# ── Centralized path resolution ──
import os as _os
import sys as _sys
from galaxyos.shared.paths import workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
logger = logging.getLogger(__name__)

WORKSPACE = os.environ.get(
    "OPENCLAW_WORKSPACE",
    str(path_resolver.WORKSPACE_ROOT),
)


# ═══════════════════════ 枚举定义 ═══════════════════════

class PersonaArchetype(Enum):
    """人格原型"""
    ENGINEERING_RIGORIST = "engineering-rigorist"     # 工程严谨主义者
    ECOSYSTEM_BUILDER = "ecosystem-builder"            # 生态构建者
    DATA_SOVEREIGN = "data-sovereign"                  # 数据主权守卫者
    COGNITIVE_EXPLORER = "cognitive-explorer"          # 认知增强探索者
    PRAGMATIST = "pragmatist"                          # 实用主义者

    @classmethod
    def from_string(cls, s: str) -> "PersonaArchetype":
        for p in cls:
            if p.value == s.lower().replace("_", "-"):
                return p
        return cls.ENGINEERING_RIGORIST  # 默认

    @classmethod
    def from_zh_name(cls, name: str) -> "PersonaArchetype":
        mapping = {
            "工程严谨主义者": cls.ENGINEERING_RIGORIST,
            "生态构建者": cls.ECOSYSTEM_BUILDER,
            "数据主权守卫者": cls.DATA_SOVEREIGN,
            "认知增强探索者": cls.COGNITIVE_EXPLORER,
            "实用主义者": cls.PRAGMATIST,
        }
        return mapping.get(name, cls.ENGINEERING_RIGORIST)


class SceneType(Enum):
    """场景类型"""
    ARCHITECTURE = "architecture"       # 架构设计
    DEBUG = "debug"                     # 调试排错
    CODING = "coding"                   # 编码实现
    STRATEGY = "strategy"               # 策略规划
    ANALYSIS = "analysis"               # 分析评估
    OPTIMIZATION = "optimization"       # 性能优化
    REVIEW = "review"                   # 审查复盘
    INTEGRATION = "integration"         # 集成整合
    RESEARCH = "research"               # 调查研究
    CONVERSATION = "conversation"       # 日常对话

    @classmethod
    def detect(cls, query: str) -> "SceneType":
        """从查询文本检测场景类型"""
        q = query.lower().strip()

        # 优先匹配精确场景
        scene_patterns = [
            (cls.ARCHITECTURE, [r'架构', r'分层', r'模块.*设计', r'system.?design', r'架构设计',
                                r'底层', r'插件', r'层.*关系', r'扩展点', r'依赖关系']),
            (cls.DEBUG, [r'调试', r'bug', r'报错', r'错误', r'异常', r'不工作', r'故障',
                         r'crash', r'崩溃', r'诊断', r'健康检查']),
            (cls.CODING, [r'代码', r'函数', r'类', r'实现', r'coding', r'implement',
                          r'重构', r'refactor', r'代码.*风格', r'类型.*定义']),
            (cls.STRATEGY, [r'规划', r'路线图', r'roadmap', r'长期', r'持久战',
                            r'策略', r'分阶段', r'优先级', r'focus']),
            (cls.OPTIMIZATION, [r'优化', r'性能', r'加速', r'内存', r'吞吐',
                                r'latency', r'响应.*时间', r'resource', r'瓶颈']),
            (cls.REVIEW, [r'审查', r'复盘', r'review', r'改进', r'问题.*点',
                          r'批评', r'反思', r'自我批评']),
            (cls.INTEGRATION, [r'集成', r'对接', r'整合', r'迁移',
                               r'同步', r'协调', r'桥接', r'unify']),
            (cls.RESEARCH, [r'调查', r'研究', r'分析', r'对比', r'比较',
                            r'原理', r'本质', r'为什么', r'根本原因']),
        ]

        for scene, patterns in scene_patterns:
            for pat in patterns:
                if re.search(pat, q):
                    return scene

        return cls.CONVERSATION


# ═══════════════════════ 数据模型 ═══════════════════════

@dataclass
class MethodologyWeight:
    """方法论权重"""
    skill_name: str                    # ThinkingSkill 枚举值
    weight: float                      # 权重 0.0-1.0
    persona_relevance: float = 0.5     # 与当前人格的相关度
    scene_affinity: Dict[str, float] = field(default_factory=lambda: {
        "architecture": 0.5,
        "debug": 0.5,
        "coding": 0.5,
        "strategy": 0.5,
        "analysis": 0.5,
        "optimization": 0.5,
        "review": 0.5,
        "integration": 0.5,
        "research": 0.5,
        "conversation": 0.5,
    })

    def score(self, scene: SceneType) -> float:
        """计算在给定场景下的综合得分"""
        scene_w = self.scene_affinity.get(scene.value, 0.3)
        return self.weight * self.persona_relevance * scene_w


@dataclass
class PersonaProfile:
    """人格画像"""
    archetype: PersonaArchetype
    name: str
    description: str
    thinking_style: str                # 推理风格描述
    default_methodology: str           # 默认方法论
    catchphrase: str                   # 代表性金句
    methodology_weights: List[MethodologyWeight] = field(default_factory=list)
    interaction_style: Dict[str, Any] = field(default_factory=lambda: {
        "tone": "直接",
        "detail_level": "high",
        "prefer_structured": True,
        "prefer_code_first": True,
        "verbose_when_uncertain": False,
    })

    def get_ranked_methodologies(self, scene: SceneType, top_k: int = 5) -> List[Tuple[str, float]]:
        """根据场景获取排序后的方法论列表 (name, score)"""
        scored = [(mw.skill_name, mw.score(scene)) for mw in self.methodology_weights]
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k]

    def explain(self, scene: SceneType) -> str:
        """返回人格在给定场景下的推理路径说明"""
        top = self.get_ranked_methodologies(scene, 3)
        methodology_names = {
            "investigation-first": "调查研究",
            "contradiction-analysis": "矛盾分析",
            "practice-cognition": "实践认知",
            "concentrate-forces": "集中兵力",
            "overall-planning": "统筹全局",
            "mass-line": "群众路线",
            "criticism-self-criticism": "批评与自我批评",
            "spark-prairie-fire": "星星之火",
            "arming-thought": "武装思想",
            "protracted-strategy": "持久战",
            "workflows": "工作流",
            "first-principles": "第一性原理",
            "systems-thinking": "系统思维",
            "critical-thinking": "批判性思维",
            "backward-thinking": "逆向思维",
            "analogical-thinking": "类比思维",
            "feynman-technique": "费曼技巧",
            "decision-engine": "决策引擎",
            "diagnose": "系统诊断",
            "grill-with-docs": "需求审问",
            "tdd": "测试驱动",
            "improve-codebase-architecture": "架构改进",
            "prototype": "原型验证",
            "zoom-out": "全局视角",
            "grill-me": "方案推敲",
        }
        names = [methodology_names.get(s, s) for s, _ in top]
        return f"[{self.name}] {self.thinking_style} → 优先{'→'.join(names)}"


# ═══════════════════════ 人格画像构建 ═══════════════════════

def _build_engineer() -> PersonaProfile:
    """工程严谨主义者 — 默认人格，与当前用户匹配"""
    return PersonaProfile(
        archetype=PersonaArchetype.ENGINEERING_RIGORIST,
        name="工程严谨主义者",
        description="系统架构设计者，追求生产级交付标准，防御性思维，代码先验",
        thinking_style="先调查后方案 → 矛盾分析找主次 → 集中兵力突破 → 实践验证",
        default_methodology="investigation-first",
        catchphrase="没有调查就没有发言权",
        methodology_weights=[
            MethodologyWeight("investigation-first", 1.0, 0.95, {
                "architecture": 1.0, "debug": 1.0, "coding": 0.9,
                "strategy": 0.8, "analysis": 0.9, "optimization": 0.8,
                "review": 0.9, "integration": 0.95, "research": 1.0,
                "conversation": 0.3,
            }),
            MethodologyWeight("contradiction-analysis", 0.9, 0.90, {
                "architecture": 1.0, "debug": 0.85, "coding": 0.7,
                "strategy": 0.9, "analysis": 0.9, "optimization": 0.7,
                "review": 0.85, "integration": 0.8, "research": 0.8,
                "conversation": 0.2,
            }),
            MethodologyWeight("concentrate-forces", 0.85, 0.85, {
                "architecture": 0.8, "debug": 0.7, "coding": 0.85,
                "strategy": 0.8, "analysis": 0.6, "optimization": 1.0,
                "review": 0.5, "integration": 0.7, "research": 0.5,
                "conversation": 0.1,
            }),
            MethodologyWeight("practice-cognition", 0.85, 0.85, {
                "architecture": 0.6, "debug": 0.9, "coding": 1.0,
                "strategy": 0.4, "analysis": 0.5, "optimization": 0.8,
                "review": 0.9, "integration": 0.7, "research": 0.3,
                "conversation": 0.1,
            }),
            MethodologyWeight("overall-planning", 0.75, 0.85, {
                "architecture": 1.0, "debug": 0.3, "coding": 0.4,
                "strategy": 1.0, "analysis": 0.5, "optimization": 0.5,
                "review": 0.6, "integration": 0.9, "research": 0.4,
                "conversation": 0.1,
            }),
            MethodologyWeight("systems-thinking", 0.9, 0.90, {
                "architecture": 1.0, "debug": 0.7, "coding": 0.6,
                "strategy": 0.9, "analysis": 0.8, "optimization": 0.8,
                "review": 0.8, "integration": 1.0, "research": 0.7,
                "conversation": 0.2,
            }),
            MethodologyWeight("diagnose", 0.85, 0.90, {
                "architecture": 0.5, "debug": 1.0, "coding": 0.6,
                "strategy": 0.2, "analysis": 0.8, "optimization": 0.6,
                "review": 0.7, "integration": 0.5, "research": 0.6,
                "conversation": 0.1,
            }),
            MethodologyWeight("tdd", 0.8, 0.85, {
                "architecture": 0.4, "debug": 0.6, "coding": 1.0,
                "strategy": 0.2, "analysis": 0.3, "optimization": 0.4,
                "review": 0.7, "integration": 0.5, "research": 0.2,
                "conversation": 0.0,
            }),
            MethodologyWeight("criticism-self-criticism", 0.7, 0.80, {
                "architecture": 0.7, "debug": 0.6, "coding": 0.5,
                "strategy": 0.7, "analysis": 0.7, "optimization": 0.6,
                "review": 1.0, "integration": 0.6, "research": 0.4,
                "conversation": 0.2,
            }),
            MethodologyWeight("first-principles", 0.7, 0.75, {
                "architecture": 0.8, "debug": 0.9, "coding": 0.3,
                "strategy": 0.6, "analysis": 0.8, "optimization": 0.5,
                "review": 0.4, "integration": 0.5, "research": 0.9,
                "conversation": 0.1,
            }),
            MethodologyWeight("prototype", 0.6, 0.70, {
                "architecture": 0.5, "debug": 0.3, "coding": 0.8,
                "strategy": 0.3, "analysis": 0.2, "optimization": 0.3,
                "review": 0.2, "integration": 0.4, "research": 0.3,
                "conversation": 0.0,
            }),
        ],
    )


def _build_builder() -> PersonaProfile:
    """生态构建者"""
    return PersonaProfile(
        archetype=PersonaArchetype.ECOSYSTEM_BUILDER,
        name="生态构建者",
        description="跨系统整合者、创新架构师，善用已有轮子搭桥",
        thinking_style="统筹全局 → 矛盾分析 → 工作流编排 → 星星之火",
        default_methodology="overall-planning",
        catchphrase="好架构不是设计出来的，是长出来的",
        methodology_weights=[
            MethodologyWeight("overall-planning", 1.0, 0.95, {
                "architecture": 1.0, "debug": 0.4, "coding": 0.5,
                "strategy": 1.0, "analysis": 0.6, "optimization": 0.6,
                "review": 0.7, "integration": 1.0, "research": 0.5,
                "conversation": 0.2,
            }),
            MethodologyWeight("systems-thinking", 0.95, 0.90, {
                "architecture": 1.0, "debug": 0.5, "coding": 0.6,
                "strategy": 0.9, "analysis": 0.8, "optimization": 0.7,
                "review": 0.7, "integration": 1.0, "research": 0.6,
                "conversation": 0.2,
            }),
            MethodologyWeight("workflows", 0.85, 0.90, {
                "architecture": 0.8, "debug": 0.3, "coding": 0.5,
                "strategy": 0.8, "analysis": 0.3, "optimization": 0.4,
                "review": 0.3, "integration": 0.9, "research": 0.2,
                "conversation": 0.0,
            }),
            MethodologyWeight("mass-line", 0.8, 0.85, {
                "architecture": 0.5, "debug": 0.3, "coding": 0.3,
                "strategy": 0.8, "analysis": 0.6, "optimization": 0.2,
                "review": 0.7, "integration": 0.6, "research": 0.5,
                "conversation": 0.3,
            }),
            MethodologyWeight("prototype", 0.8, 0.80, {
                "architecture": 0.6, "debug": 0.4, "coding": 0.9,
                "strategy": 0.5, "analysis": 0.3, "optimization": 0.3,
                "review": 0.3, "integration": 0.7, "research": 0.3,
                "conversation": 0.0,
            }),
            MethodologyWeight("contradiction-analysis", 0.75, 0.85, {
                "architecture": 0.9, "debug": 0.6, "coding": 0.5,
                "strategy": 0.9, "analysis": 0.8, "optimization": 0.5,
                "review": 0.8, "integration": 0.7, "research": 0.7,
                "conversation": 0.2,
            }),
        ],
    )


def _build_sovereign() -> PersonaProfile:
    """数据主权守卫者"""
    return PersonaProfile(
        archetype=PersonaArchetype.DATA_SOVEREIGN,
        name="数据主权守卫者",
        description="安全优先、边界管控，数据完整性高于一切",
        thinking_style="矛盾分析 → 调查研究 → 批判性思维 → 持久战",
        default_methodology="contradiction-analysis",
        catchphrase="边界不可逾越",
        methodology_weights=[
            MethodologyWeight("contradiction-analysis", 1.0, 0.95, {
                "architecture": 0.9, "debug": 0.8, "coding": 0.7,
                "strategy": 0.8, "analysis": 0.9, "optimization": 0.6,
                "review": 1.0, "integration": 0.8, "research": 0.8,
                "conversation": 0.2,
            }),
            MethodologyWeight("critical-thinking", 0.95, 0.95, {
                "architecture": 0.8, "debug": 0.9, "coding": 0.6,
                "strategy": 0.7, "analysis": 1.0, "optimization": 0.5,
                "review": 0.9, "integration": 0.7, "research": 0.9,
                "conversation": 0.3,
            }),
            MethodologyWeight("investigation-first", 0.9, 0.90, {
                "architecture": 0.8, "debug": 1.0, "coding": 0.7,
                "strategy": 0.6, "analysis": 0.9, "optimization": 0.6,
                "review": 0.8, "integration": 0.8, "research": 1.0,
                "conversation": 0.3,
            }),
            MethodologyWeight("protracted-strategy", 0.8, 0.90, {
                "architecture": 0.7, "debug": 0.3, "coding": 0.3,
                "strategy": 1.0, "analysis": 0.4, "optimization": 0.6,
                "review": 0.5, "integration": 0.6, "research": 0.3,
                "conversation": 0.1,
            }),
            MethodologyWeight("criticism-self-criticism", 0.8, 0.85, {
                "architecture": 0.7, "debug": 0.6, "coding": 0.5,
                "strategy": 0.6, "analysis": 0.7, "optimization": 0.5,
                "review": 1.0, "integration": 0.5, "research": 0.5,
                "conversation": 0.1,
            }),
        ],
    )


def _build_explorer() -> PersonaProfile:
    """认知增强探索者"""
    return PersonaProfile(
        archetype=PersonaArchetype.COGNITIVE_EXPLORER,
        name="认知增强探索者",
        description="持续推动认知边界，崇尚仿生智能与推理增强",
        thinking_style="第一性原理 → 类比思维 → 费曼技巧 → 实践验证",
        default_methodology="first-principles",
        catchphrase="知其然，更知其所以然",
        methodology_weights=[
            MethodologyWeight("first-principles", 1.0, 0.95, {
                "architecture": 0.8, "debug": 0.9, "coding": 0.4,
                "strategy": 0.7, "analysis": 1.0, "optimization": 0.5,
                "review": 0.5, "integration": 0.6, "research": 1.0,
                "conversation": 0.3,
            }),
            MethodologyWeight("analogical-thinking", 0.9, 0.90, {
                "architecture": 0.7, "debug": 0.8, "coding": 0.5,
                "strategy": 0.8, "analysis": 0.8, "optimization": 0.4,
                "review": 0.6, "integration": 0.7, "research": 0.9,
                "conversation": 0.4,
            }),
            MethodologyWeight("feynman-technique", 0.85, 0.90, {
                "architecture": 0.5, "debug": 0.6, "coding": 0.4,
                "strategy": 0.5, "analysis": 0.8, "optimization": 0.3,
                "review": 0.6, "integration": 0.5, "research": 0.9,
                "conversation": 0.5,
            }),
            MethodologyWeight("systems-thinking", 0.8, 0.85, {
                "architecture": 0.9, "debug": 0.6, "coding": 0.5,
                "strategy": 0.8, "analysis": 0.7, "optimization": 0.6,
                "review": 0.6, "integration": 0.9, "research": 0.7,
                "conversation": 0.2,
            }),
            MethodologyWeight("backward-thinking", 0.75, 0.80, {
                "architecture": 0.6, "debug": 0.8, "coding": 0.7,
                "strategy": 0.7, "analysis": 0.6, "optimization": 0.5,
                "review": 0.5, "integration": 0.4, "research": 0.7,
                "conversation": 0.2,
            }),
            MethodologyWeight("practice-cognition", 0.7, 0.80, {
                "architecture": 0.5, "debug": 0.8, "coding": 0.9,
                "strategy": 0.4, "analysis": 0.5, "optimization": 0.7,
                "review": 0.7, "integration": 0.5, "research": 0.5,
                "conversation": 0.1,
            }),
        ],
    )


def _build_pragmatist() -> PersonaProfile:
    """实用主义者"""
    return PersonaProfile(
        archetype=PersonaArchetype.PRAGMATIST,
        name="实用主义者",
        description="能跑就行，不要过度设计，快速交付价值",
        thinking_style="原型验证 → 实践认知 → 逆向思维 → 集中兵力",
        default_methodology="prototype",
        catchphrase="先跑通，再跑快，再跑好",
        methodology_weights=[
            MethodologyWeight("prototype", 1.0, 0.95, {
                "architecture": 0.4, "debug": 0.5, "coding": 1.0,
                "strategy": 0.4, "analysis": 0.2, "optimization": 0.5,
                "review": 0.2, "integration": 0.6, "research": 0.2,
                "conversation": 0.1,
            }),
            MethodologyWeight("practice-cognition", 0.9, 0.90, {
                "architecture": 0.4, "debug": 0.8, "coding": 1.0,
                "strategy": 0.3, "analysis": 0.4, "optimization": 0.7,
                "review": 0.6, "integration": 0.6, "research": 0.3,
                "conversation": 0.1,
            }),
            MethodologyWeight("backward-thinking", 0.8, 0.85, {
                "architecture": 0.3, "debug": 0.7, "coding": 0.8,
                "strategy": 0.6, "analysis": 0.4, "optimization": 0.5,
                "review": 0.3, "integration": 0.4, "research": 0.3,
                "conversation": 0.1,
            }),
            MethodologyWeight("concentrate-forces", 0.75, 0.80, {
                "architecture": 0.3, "debug": 0.5, "coding": 0.7,
                "strategy": 0.6, "analysis": 0.3, "optimization": 0.9,
                "review": 0.3, "integration": 0.4, "research": 0.2,
                "conversation": 0.1,
            }),
            MethodologyWeight("spark-prairie-fire", 0.7, 0.75, {
                "architecture": 0.3, "debug": 0.2, "coding": 0.5,
                "strategy": 0.7, "analysis": 0.2, "optimization": 0.3,
                "review": 0.2, "integration": 0.3, "research": 0.2,
                "conversation": 0.0,
            }),
        ],
    )


# ═══════════════════════ 人格检测 ═══════════════════════

# 人格检测关键词
PERSONA_DETECT_PATTERNS = {
    PersonaArchetype.ENGINEERING_RIGORIST: [
        r'架构(设计)?', r'模块.*设计', r'分层', r'系统.*设计',
        r'生产(级|环境)', r'交付.*标准', r'代码质量',
        r'回滚', r'安全.*默认', r'防御性',
        r'边界', r'验证.*再.*执行', r'先.*后',
    ],
    PersonaArchetype.ECOSYSTEM_BUILDER: [
        r'集成', r'对接', r'生态', r'跨.*系统',
        r'整合', r'桥接', r'通道', r'unify',
        r'工作流', r'编排', r'orchestrat',
    ],
    PersonaArchetype.DATA_SOVEREIGN: [
        r'备份', r'数据.*安全', r'权限', r'主权',
        r'边界.*不可', r'审计', r'脱敏',
        r'隐私', r'数据.*保护',
    ],
    PersonaArchetype.COGNITIVE_EXPLORER: [
        r'原理', r'本质', r'为什么', r'根因',
        r'第一性', r'认知', r'推理', r'思考.*增强',
        r'仿生', r'神经网络', r'创新',
    ],
    PersonaArchetype.PRAGMATIST: [
        r'快速', r'先跑', r'原型', r'demo',
        r'最小(可行|化)', r'MVP', r'能跑就行',
        r'别.*过度', r'简单.*方案',
    ],
}


def _detect_persona_from_query(query: str) -> Dict[PersonaArchetype, float]:
    """从查询文本检测人格倾向，返回各人格的匹配分数"""
    scores = {archetype: 0.0 for archetype in PersonaArchetype}
    q = query.lower()

    for archetype, patterns in PERSONA_DETECT_PATTERNS.items():
        matches = 0
        for pat in patterns:
            if re.search(pat, q):
                matches += 1
        if matches > 0:
            # 匹配数越多分数越高，但上限 0.85（留余量给默认人格）
            scores[archetype] = min(0.3 + 0.15 * matches, 0.85)

    return scores


# ═══════════════════════ 引擎主类 ═══════════════════════

class PersonaMethodologyEngine:
    """
    人格-方法论匹配引擎

    职责:
    1. 加载和缓存人格画像
    2. 根据查询和上下文检测用户人格
    3. 按人格+场景排序方法论
    4. 提供健康检查接口
    """

    def __init__(self, config_path: Optional[str] = None):
        self._profiles: Dict[str, PersonaProfile] = {}
        self._active_archetype: PersonaArchetype = PersonaArchetype.ENGINEERING_RIGORIST
        self._config_path = config_path
        self._load_defaults()

    def _load_defaults(self):
        """加载内置人格画像"""
        profiles = [
            _build_engineer(),
            _build_builder(),
            _build_sovereign(),
            _build_explorer(),
            _build_pragmatist(),
        ]
        for p in profiles:
            self._profiles[p.archetype.value] = p

    @property
    def active_profile(self) -> PersonaProfile:
        return self._profiles.get(self._active_archetype.value, self._profiles["engineering-rigorist"])

    def detect_persona(self, query: str, context: Optional[Dict] = None) -> PersonaArchetype:
        """
        检测给定查询最匹配的人格原型。

        匹配策略:
        1. 查询文本关键词匹配 → 人格分数
        2. 历史上下文倾向 → 微调
        3. 无明确匹配 → 返回当前激活的人格
        """
        scores = _detect_persona_from_query(query)

        # 历史上下文微调：如果之前激活的人格有基础分，给予 0.2 加成
        current = self._active_archetype
        if current in scores:
            scores[current] = min(scores[current] + 0.2, 1.0)

        # 取最高分
        best = max(scores, key=scores.get)
        best_score = scores[best]

        # 分数太低（< 0.2）说明无明确匹配，保持当前人格
        if best_score < 0.2:
            return current

        return best

    def get_skills_for_query(self, query: str, context: Optional[Dict] = None,
                             top_k: int = 3) -> List[str]:
        """
        获取适合当前查询的思考技能列表。

        返回: [skill_name, ...] 按优先级排序
        """
        scene = SceneType.detect(query)
        archetype = self.detect_persona(query, context)
        profile = self._profiles.get(archetype.value, self.active_profile)

        ranked = profile.get_ranked_methodologies(scene, top_k)
        return [name for name, _ in ranked]

    def set_persona(self, archetype: PersonaArchetype) -> bool:
        """手动切换人格"""
        if archetype.value in self._profiles:
            self._active_archetype = archetype
            logger.info(f"人格已切换: {self.active_profile.name}")
            return True
        return False

    def list_profiles(self) -> List[Dict[str, Any]]:
        """列出所有可用人格画像"""
        result = []
        for p in self._profiles.values():
            result.append({
                "archetype": p.archetype.value,
                "name": p.name,
                "description": p.description,
                "thinking_style": p.thinking_style,
                "default_methodology": p.default_methodology,
                "catchphrase": p.catchphrase,
            })
        return result

    def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        profile = self.active_profile
        return {
            "active_persona": profile.name,
            "active_archetype": profile.archetype.value,
            "thinking_style": profile.thinking_style,
            "available_profiles": len(self._profiles),
            "profiles": [p.name for p in self._profiles.values()],
            "healthy": True,
        }

    def explain_response(self, query: str) -> str:
        """解释当前查询会采用什么方法论路径"""
        scene = SceneType.detect(query)
        archetype = self.detect_persona(query)
        profile = self._profiles[archetype.value]
        return profile.explain(scene)
