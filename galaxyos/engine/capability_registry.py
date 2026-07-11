#!/usr/bin/env python3
"""
capability_registry.py — SkVM 26 primitive capabilities + profile matching

SkVM (Skill Virtual Machine) 原语能力注册表。

CapabilityProfile:
  - 26 维原语能力描述
  - 涵盖 web_access, tool_exec, reasoning, multimodal, env_deps 等

HarnessProfile:
  - 当前模型 + 工具 + 环境的能力描述

ProfileMatcher:
  - 计算 skill profile 和 harness profile 之间的 adaptation_score

SkillClassifier:
  - 从 SKILL.md 内容自动检测能力画像

RegistryManager:
  - 加载/保存 profiles 到 JSON
  - 按原语能力查询 assets
"""

import json
import os
import re
import time
import logging
import threading
from typing import Dict, List, Optional, Any, Tuple, Set
from dataclasses import dataclass, field, asdict

import os as _os
import sys as _sys
from galaxyos.shared.paths import workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════
# 26 原语能力定义
# ══════════════════════════════════════════════════

@dataclass
class CapabilityProfile:
    """
    26 维原语能力画像

    每个维度的含义：
    - web_access (bool + level: "read"/"search"/"scrape"/"full"): 网络访问能力
    - tool_exec (bool + type: "shell"/"plugin"/"user_device"/"all"): 工具执行
    - reasoning (int 1-3): 推理深度 (1=basic, 2=analytical, 3=deep)
    - multimodal (bool): 多模态支持（看图/生成图）
    - env_deps (list[str]): 环境依赖列表
    - token_budget (str: "light"/"medium"/"heavy"): 令牌预算
    - parallelism_possible (bool): 是否支持并行
    - code_gen (bool): 代码生成能力
    - math (bool): 数学计算能力
    - search (bool): 搜索/检索能力
    - planning (bool): 规划/拆解能力
    - memory (bool): 记忆存储/检索能力
    - emotion (bool): 情感响应
    - personality (bool): 人格角色扮演
    - self_evolution (bool): 自我进化
    - external_api (bool + protocols: list[str]): 外部 API 调用
    - file_io (bool + formats: list[str]): 文件读写
    - data_analysis (bool): 数据分析
    - translation (bool): 翻译
    - summarization (bool): 摘要/总结
    - qa (bool): 问答
    - creativity (int 1-3): 创造力
    - context_window (str: "small"/"medium"/"large"): 上下文窗口
    - streaming (bool): 流式输出
    - latency_sensitivity (str: "low"/"medium"/"high"): 延迟敏感度
    - offline (bool): 离线可用
    """

    # ── 网络与工具 ──
    web_access: Dict[str, Any] = field(default_factory=lambda: {"enabled": False, "level": "none"})
    tool_exec: Dict[str, Any] = field(default_factory=lambda: {"enabled": False, "type": "none"})

    # ── 认知能力 ──
    reasoning: int = 1                    # 1-3
    multimodal: Dict[str, bool] = field(default_factory=lambda: {"vision": False, "generation": False})
    code_gen: bool = False
    math: bool = False
    search: bool = False
    planning: bool = False
    memory: bool = False
    emotion: bool = False
    personality: bool = False
    self_evolution: bool = False

    # ── I/O ──
    external_api: Dict[str, Any] = field(default_factory=lambda: {"enabled": False, "protocols": []})
    file_io: Dict[str, Any] = field(default_factory=lambda: {"enabled": False, "formats": []})
    streaming: bool = False

    # ── 内容能力 ──
    data_analysis: bool = False
    translation: bool = False
    summarization: bool = False
    qa: bool = False
    creativity: int = 1                   # 1-3

    # ── 环境与性能 ──
    env_deps: List[str] = field(default_factory=list)
    token_budget: str = "medium"          # light / medium / heavy
    parallelism_possible: bool = False
    context_window: str = "medium"        # small / medium / large
    latency_sensitivity: str = "medium"   # low / medium / high
    offline: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityProfile":
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)

    def summary(self) -> str:
        """生成简短的能力摘要"""
        parts = []
        if self.web_access.get("enabled"):
            parts.append(f"web({self.web_access['level']})")
        if self.tool_exec.get("enabled"):
            parts.append(f"tool({self.tool_exec['type']})")
        if self.reasoning >= 2:
            parts.append(f"reasoning(L{self.reasoning})")
        if self.code_gen:
            parts.append("code")
        if self.search:
            parts.append("search")
        if self.memory:
            parts.append("memory")
        if self.multimodal.get("vision") or self.multimodal.get("generation"):
            parts.append("mm")
        return f"CapabilityProfile({', '.join(parts)}), budget={self.token_budget}, window={self.context_window}"


# ══════════════════════════════════════════════════
# HarnessProfile（宿主环境画像）
# ══════════════════════════════════════════════════

@dataclass
class HarnessProfile:
    """
    宿主环境画像——描述当前运行环境的实际能力

    model: 模型名称/类型
    tools: 可用工具列表
    skill_paths: 已安装技能路径
    environment: 系统环境信息
    """
    model: str = ""
    tools: List[str] = field(default_factory=list)
    skill_paths: List[str] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)

    def detect_profile(self) -> CapabilityProfile:
        """
        自动检测宿主环境的能力画像
        
        基于当前模型、可用工具、系统环境推断。
        """
        profile = CapabilityProfile()
        env = self.environment

        # Web access: 如果可用网络工具则开启
        if "web_fetch" in self.tools or "xiaoyi_web_search" in self.tools:
            profile.web_access = {"enabled": True, "level": "search"}
        if any("browser" in t for t in self.tools):
            profile.web_access["level"] = "scrape"

        # Tool exec
        if "exec" in self.tools:
            profile.tool_exec = {"enabled": True, "type": "shell"}
        if any("plugin" in t for t in self.tools):
            profile.tool_exec["type"] = "plugin"

        # Reasoning: 根据模型推断
        model_lower = self.model.lower()
        if "thinking" in model_lower or "deepseek" in model_lower:
            profile.reasoning = 3
        elif "gpt" in model_lower or "claude" in model_lower:
            profile.reasoning = 2
        else:
            profile.reasoning = 1

        # Multimodal
        if any("image" in t or "vision" in t for t in self.tools):
            profile.multimodal["vision"] = True
        if any("image_gen" in t or "seedream" in t for t in self.tools):
            profile.multimodal["generation"] = True

        # Memory
        if "claw_recall" in self.tools or "claw_store" in self.tools:
            profile.memory = True

        # Search
        if "claw_recall" in self.tools or "xiaoyi_web_search" in self.tools:
            profile.search = True

        # Code gen
        if profile.reasoning >= 2:
            profile.code_gen = True

        # Planning
        if "planning" in self.tools or "update_plan" in self.tools:
            profile.planning = True

        # File IO
        if "write" in self.tools and "read" in self.tools:
            profile.file_io = {"enabled": True, "formats": ["txt", "json", "py"]}

        # Streaming
        if env.get("streaming", False):
            profile.streaming = True

        # Context window
        if "large" in env.get("context_window", "").lower() or "256k" in str(env.get("context_window", "")).lower():
            profile.context_window = "large"
        elif "small" in str(env.get("context_window", "")).lower():
            profile.context_window = "small"

        # Token budget
        if "heavy" in env.get("token_budget", "").lower():
            profile.token_budget = "heavy"
        elif "light" in env.get("token_budget", "").lower():
            profile.token_budget = "light"

        # Environment deps
        deps = []
        python_major = env.get("python_major", 0)
        if python_major:
            deps.append(f"python>{python_major}")
        if env.get("has_jieba"):
            deps.append("jieba")
        if env.get("has_sklearn"):
            deps.append("sklearn")
        profile.env_deps = deps

        return profile

    @classmethod
    def auto_detect(cls) -> "HarnessProfile":
        """
        自动检测当前环境
        
        扫描 sys.path、已安装包、可用工具。
        """
        hp = cls()

        # 模型检测
        try:
            config_path = path_resolver.XIAOYI_OMEGA_LLM_CONFIG
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
                default = cfg.get("llm", {}).get("default_model", "")
                hp.model = default or os.environ.get("OPENCLAW_DEFAULT_MODEL", "unknown")
        except Exception:
            hp.model = os.environ.get("OPENCLAW_DEFAULT_MODEL", "unknown")

        # 工具检测（从可用 skill 推断）
        skills_dir = path_resolver.SKILLS_DIR
        if os.path.isdir(skills_dir):
            try:
                skills = os.listdir(skills_dir)
                hp.skill_paths = [os.path.join(skills_dir, s) for s in skills if os.path.isdir(os.path.join(skills_dir, s))]
                # 推断工具
                for sk in skills:
                    sk_lower = sk.lower()
                    if "web" in sk_lower or "search" in sk_lower:
                        hp.tools.append("xiaoyi_web_search")
                    if "memory" in sk_lower or "recall" in sk_lower:
                        if "claw_recall" not in hp.tools:
                            hp.tools.append("claw_recall")
                    if "image" in sk_lower:
                        hp.tools.append("image_understanding")
                    if "seedream" in sk_lower or "gen" in sk_lower:
                        hp.tools.append("image_gen")
            except Exception:
                pass

        # 基础工具
        base_tools = ["exec", "read", "write", "edit", "message", "web_fetch"]
        for bt in base_tools:
            if bt not in hp.tools:
                hp.tools.append(bt)

        # 环境检测
        import sys
        hp.environment["python_major"] = sys.version_info.major
        hp.environment["python_minor"] = sys.version_info.minor

        try:
            import jieba
            hp.environment["has_jieba"] = True
        except ImportError:
            hp.environment["has_jieba"] = False

        try:
            from sklearn.mixture import GaussianMixture
            hp.environment["has_sklearn"] = True
        except ImportError:
            hp.environment["has_sklearn"] = False

        hp.environment["streaming"] = True  # 默认支持流式
        hp.environment["context_window"] = "256k"
        hp.environment["token_budget"] = "heavy"

        return hp


# ══════════════════════════════════════════════════
# ProfileMatcher
# ══════════════════════════════════════════════════

class ProfileMatcher:
    """
    ProfileMatcher: 计算 skill profile 和 harness profile 的适配分数

    adaptation_score ∈ [0, 1]:
      0 = 完全不适配（环境无法运行该 skill）
      1 = 完全适配（环境完美匹配 skill 需求）

    计算方式：
    - 必选项（required capabilities）：不满足则 0 分
    - 可选项（optional capabilities）：按权重累加
    - 偏差惩罚（如 reasoning 过高/过低）
    """

    # 必选项权重
    REQUIRED_WEIGHTS = {
        "web_access.enabled": 0.10,
        "tool_exec.enabled": 0.10,
        "memory": 0.08,
        "multimodal.vision": 0.06,
        "multimodal.generation": 0.06,
    }

    # 可选项权重
    OPTIONAL_WEIGHTS = {
        "reasoning": 0.08,
        "search": 0.06,
        "code_gen": 0.05,
        "planning": 0.05,
        "data_analysis": 0.04,
        "translation": 0.03,
        "summarization": 0.03,
        "qa": 0.03,
        "streaming": 0.04,
        "parallelism_possible": 0.03,
        "self_evolution": 0.03,
        "emotion": 0.03,
        "personality": 0.03,
    }

    def compute_adaptation_score(
        self,
        skill_profile: CapabilityProfile,
        harness_profile: CapabilityProfile,
    ) -> float:
        """
        计算适配分数

        Args:
            skill_profile: 技能需求能力画像
            harness_profile: 宿主环境能力画像

        Returns:
            adaptation_score [0, 1]
        """
        # 1. 必选项检查
        required_score = 0.0
        required_total = 0.0

        skill_web = skill_profile.web_access
        harness_web = harness_profile.web_access
        if skill_web.get("enabled") and not harness_web.get("enabled"):
            # skill 需要网络，但环境不支持
            return 0.0
        # 网络等级检查
        if skill_web.get("enabled") and harness_web.get("enabled"):
            level_order = {"none": 0, "read": 1, "search": 2, "scrape": 3, "full": 4}
            skill_lv = level_order.get(skill_web.get("level", "none"), 0)
            harness_lv = level_order.get(harness_web.get("level", "none"), 0)
            if harness_lv < skill_lv:
                return 0.0  # 环境网络能力不足

        skill_tool = skill_profile.tool_exec
        harness_tool = harness_profile.tool_exec
        if skill_tool.get("enabled") and not harness_tool.get("enabled"):
            return 0.0

        if skill_profile.memory and not harness_profile.memory:
            return 0.0

        # 多模态检查
        if skill_profile.multimodal.get("vision") and not harness_profile.multimodal.get("vision"):
            return 0.0
        if skill_profile.multimodal.get("generation") and not harness_profile.multimodal.get("generation"):
            return 0.0

        # 环境依赖检查
        if skill_profile.env_deps:
            for dep in skill_profile.env_deps:
                if not self._check_dep(dep, harness_profile.env_deps):
                    return 0.0

        # 2. 可选项加权评分
        score = 0.0
        total_weight = 0.0

        for attr, weight in self.OPTIONAL_WEIGHTS.items():
            skill_val = getattr(skill_profile, attr, None)
            harness_val = getattr(harness_profile, attr, None)
            if skill_val is None:
                continue

            match = self._compare_value(attr, skill_val, harness_val)
            if match:
                score += weight
            total_weight += weight

        # 3. 偏差惩罚
        penalty = 0.0

        # reasoning 偏差
        skill_reasoning = skill_profile.reasoning
        harness_reasoning = harness_profile.reasoning
        diff = abs(skill_reasoning - harness_reasoning)
        if diff >= 2:
            penalty += 0.1  # 重大偏差
        elif diff >= 1:
            penalty += 0.05

        # token_budget 偏差
        budget_order = {"light": 1, "medium": 2, "heavy": 3}
        sb = budget_order.get(skill_profile.token_budget, 2)
        hb = budget_order.get(harness_profile.token_budget, 2)
        if hb < sb:
            penalty += 0.05

        # context_window 偏差
        window_order = {"small": 1, "medium": 2, "large": 3}
        sw = window_order.get(skill_profile.context_window, 2)
        hw = window_order.get(harness_profile.context_window, 2)
        if hw < sw:
            penalty += 0.05

        # 4. 最终分数
        final_score = score / max(total_weight, 1e-6) - penalty
        return max(0.0, min(1.0, final_score))

    def _check_dep(self, dep: str, harness_deps: List[str]) -> bool:
        """检查依赖是否满足"""
        dep_lower = dep.lower().strip()
        for hd in harness_deps:
            if dep_lower in hd.lower():
                return True
        # 常见依赖直接检查
        known_deps = {
            "python>3": True,
            "python>2": True,
            "jieba": False,
            "sklearn": False,
            "numpy": False,
        }

        try:
            import importlib
            for known in known_deps:
                if known in dep_lower:
                    try:
                        importlib.import_module(known.split(">")[0].replace("python", "sys"))
                        known_deps[known] = True
                    except ImportError:
                        known_deps[known] = False
                    return known_deps.get(known.split(">")[0].strip(), False)
        except Exception:
            pass

        return True  # 未知依赖默认通过

    def _compare_value(self, attr: str, skill_val: Any, harness_val: Any) -> bool:
        """比较单个属性的匹配度"""
        if isinstance(skill_val, bool):
            return skill_val == harness_val
        elif isinstance(skill_val, int):
            # 数值属性：skill <= harness 即可（环境能力足够）
            return harness_val >= skill_val
        elif isinstance(skill_val, str):
            # 字符串属性：精确匹配
            return skill_val == harness_val
        elif isinstance(skill_val, dict):
            # 字典属性：检查子字段
            # skill 有要求时就要求环境满足
            for k, v in skill_val.items():
                if v and not harness_val.get(k, False):
                    return False
            return True
        return True

    def rank_skills(
        self,
        skill_profiles: Dict[str, CapabilityProfile],
        harness_profile: CapabilityProfile,
    ) -> List[Tuple[str, float]]:
        """
        对多个 skill 排序（按适配分数降序）

        Args:
            skill_profiles: {skill_name: CapabilityProfile}
            harness_profile: 宿主环境画像

        Returns:
            [(skill_name, adaptation_score), ...]
        """
        scored = []
        for name, profile in skill_profiles.items():
            score = self.compute_adaptation_score(profile, harness_profile)
            scored.append((name, score))
        scored.sort(key=lambda x: -x[1])
        return scored


# ══════════════════════════════════════════════════
# SkillClassifier
# ══════════════════════════════════════════════════

class SkillClassifier:
    """
    从 SKILL.md 内容自动检测技能的能力画像
    
    使用关键词匹配 + 简单规则推断各个原语能力。
    """

    # 关键词映射
    KEYWORD_MAP = {
        "web_access": [
            "web", "internet", "搜索", "search", "fetch", "http", "api",
            "scrape", "爬", "网页", "url", "网络",
        ],
        "tool_exec": [
            "tool", "命令", "shell", "exec", "bash", "terminal", "执行",
            "运行", "command", "plugin", "cli",
        ],
        "reasoning": [  # 分数叠加：每个关键词 +1
            "推理", "reason", "think", "思考", "logic", "逻辑",
            "analyze", "分析", "deep", "深度",
        ],
        "memory": [
            "memory", "记忆", "recall", "检索", "store", "存储",
            "remember", "forget", "consolidat",
        ],
        "search": [
            "search", "检索", "search", "recall", "查询", "query",
            "hybrid", "recall", "dense",
        ],
        "code_gen": [
            "code", "代码", "program", "编程", "函数", "function",
            "脚本", "script", "generate", "生成",
        ],
        "math": [
            "math", "数学", "计算", "calculate", "统计", "statistic",
            "numerical", "algorithm",
        ],
        "planning": [
            "plan", "规划", "schedule", "计划", "安排", "project",
            "step", "步骤", "workflow", "流程",
        ],
        "data_analysis": [
            "analysis", "分析", "data", "数据", "chart", "图",
            "visualize", "可视化", "statistics",
        ],
        "translation": [
            "translat", "翻译", "language", "语言", "i18n", "local",
        ],
        "summarization": [
            "summar", "摘要", "总结", "abstract", "overview", "概览",
            "condense", "压缩",
        ],
        "qa": [
            "qa", "问答", "question", "问题", "回答", "answer",
            "faq", "query",
        ],
        "multimodal": [
            "image", "图片", "vision", "视觉", "multimodal", "多模态",
            "ocr", "photo", "picture", "diagram",
        ],
        "image_gen": [
            "generate image", "image gen", "文生图", "图生图",
            "seedream", "画图", "draw", "paint",
        ],
        "streaming": [
            "stream", "流式", "chunk", "渐进", "real-time", "实时",
        ],
        "parallelism": [
            "parallel", "并行", "concurrent", "并发", "async",
            "batch", "批量", "multi-thread",
        ],
        "emotion": [
            "emotion", "情感", "情绪", "feeling", "共情", "empathy",
            "qiqing", "七情", "六欲",
        ],
        "personality": [
            "persona", "人格", "角色", "character", "identity",
            "soul", "风格", "style",
        ],
        "self_evolution": [
            "evolution", "进化", "自我改进", "self-improve",
            "learn", "学习", "adapt", "适应",
        ],
        "offline": [
            "offline", "离线", "local", "本地", "cached", "缓存",
        ],
    }

    # 环境依赖检测
    DEP_PATTERNS = [
        (r"import\s+(\w+)", "python"),
        (r"require\([\"']([^\"']+)[\"']\)", "node"),
        (r"pip\s+install\s+(\S+)", "python"),
        (r"npm\s+install\s+(\S+)", "node"),
        (r"apt(-get)?\s+install\s+(\S+)", "system"),
        (r"docker", "docker"),
    ]

    def classify(self, skill_content: str, skill_name: str = "") -> CapabilityProfile:
        """
        从 SKILL.md 内容分类技能

        Args:
            skill_content: SKILL.md 的完整文本内容
            skill_name: 技能名称（可选，用于名称推断）

        Returns:
            CapabilityProfile
        """
        profile = CapabilityProfile()

        if not skill_content:
            return profile

        content_lower = skill_content.lower()
        name_lower = skill_name.lower()

        # 1. Web access
        web_hits = sum(
            1 for kw in self.KEYWORD_MAP["web_access"]
            if kw in content_lower or kw in name_lower
        )
        if web_hits >= 2:
            profile.web_access = {"enabled": True, "level": "search"}
        if any(kw in content_lower for kw in ["scrape", "爬虫", "crawl"]):
            profile.web_access["level"] = "scrape"
        if any(kw in content_lower for kw in ["full web", "full browser", "完整浏览器"]):
            profile.web_access["level"] = "full"

        # 2. Tool exec
        tool_hits = sum(
            1 for kw in self.KEYWORD_MAP["tool_exec"]
            if kw in content_lower or kw in name_lower
        )
        if tool_hits >= 2:
            profile.tool_exec = {"enabled": True, "type": "shell"}

        # 3. Reasoning
        reason_hits = sum(
            1 for kw in self.KEYWORD_MAP["reasoning"]
            if kw in content_lower
        )
        if reason_hits >= 4:
            profile.reasoning = 3
        elif reason_hits >= 2:
            profile.reasoning = 2

        # 4. Memory
        if any(kw in content_lower for kw in self.KEYWORD_MAP["memory"]):
            profile.memory = True

        # 5. Search
        if any(kw in content_lower for kw in self.KEYWORD_MAP["search"]):
            profile.search = True

        # 6. Code gen
        if any(kw in content_lower for kw in self.KEYWORD_MAP["code_gen"]):
            profile.code_gen = True

        # 7. Math
        if any(kw in content_lower for kw in self.KEYWORD_MAP["math"]):
            profile.math = True

        # 8. Planning
        if any(kw in content_lower for kw in self.KEYWORD_MAP["planning"]):
            profile.planning = True

        # 9. Data analysis
        if any(kw in content_lower for kw in self.KEYWORD_MAP["data_analysis"]):
            profile.data_analysis = True

        # 10. Translation
        if any(kw in content_lower for kw in self.KEYWORD_MAP["translation"]):
            profile.translation = True

        # 11. Summarization
        if any(kw in content_lower for kw in self.KEYWORD_MAP["summarization"]):
            profile.summarization = True

        # 12. QA
        if any(kw in content_lower for kw in self.KEYWORD_MAP["qa"]):
            profile.qa = True

        # 13. Multimodal
        if any(kw in content_lower for kw in self.KEYWORD_MAP["multimodal"]):
            profile.multimodal["vision"] = True
        if any(kw in content_lower for kw in self.KEYWORD_MAP.get("image_gen", [])):
            profile.multimodal["generation"] = True

        # 14. Streaming
        if any(kw in content_lower for kw in self.KEYWORD_MAP["streaming"]):
            profile.streaming = True

        # 15. Parallelism
        if any(kw in content_lower for kw in self.KEYWORD_MAP["parallelism"]):
            profile.parallelism_possible = True

        # 16. Emotion
        if any(kw in content_lower for kw in self.KEYWORD_MAP["emotion"]):
            profile.emotion = True

        # 17. Personality
        if any(kw in content_lower for kw in self.KEYWORD_MAP["personality"]):
            profile.personality = True

        # 18. Self-evolution
        if any(kw in content_lower for kw in self.KEYWORD_MAP["self_evolution"]):
            profile.self_evolution = True

        # 19. Offline
        if any(kw in content_lower for kw in self.KEYWORD_MAP["offline"]):
            profile.offline = True

        # 20. Creativity (基于内容综合判断)
        creative_indicators = ["creative", "创造力", "novel", "新颖", "imagine",
                               "想象", "invent", "发明", "generate", "生成"]
        creativity_score = sum(1 for c in creative_indicators if c in content_lower)
        if creativity_score >= 4:
            profile.creativity = 3
        elif creativity_score >= 2:
            profile.creativity = 2

        # 21. Env deps 检测
        for pattern, dep_type in self.DEP_PATTERNS:
            matches = re.findall(pattern, skill_content)
            for m in matches:
                dep_str = f"{dep_type}:{m}" if isinstance(m, str) else f"{dep_type}:{'|'.join(m)}"
                if dep_str not in profile.env_deps:
                    profile.env_deps.append(dep_str)

        # 22. Token budget 估计
        total_chars = len(skill_content)
        if total_chars > 5000:
            profile.token_budget = "heavy"
        elif total_chars > 2000:
            profile.token_budget = "medium"
        else:
            profile.token_budget = "light"

        # 23. Context window 估计（复杂技能需要大窗口）
        if profile.reasoning >= 3 or any(kw in content_lower for kw in ["planning", "comprehensive"]):
            profile.context_window = "large"

        # 24. Latency sensitivity (默认 medium)
        if any(kw in content_lower for kw in ["realtime", "实时", "fast", "快速", "instant"]):
            profile.latency_sensitivity = "high"

        return profile

    def classify_from_file(self, file_path: str) -> Optional[CapabilityProfile]:
        """从文件内容分类"""
        if not os.path.exists(file_path):
            logger.warning(f"SkillClassifier: file not found: {file_path}")
            return None
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
            skill_name = os.path.basename(os.path.dirname(file_path))
            return self.classify(content, skill_name)
        except Exception as e:
            logger.error(f"SkillClassifier: read {file_path} failed: {e}")
            return None


# ══════════════════════════════════════════════════
# RegistryManager
# ══════════════════════════════════════════════════

class RegistryManager:
    """
    能力画像注册表管理器
    
    维护能力画像的存储（JSON 文件）、查询、更新。
    """

    def __init__(self, data_dir: str = ""):
        self._data_dir = data_dir or path_resolver.GALAXYOS_CAPABILITY
        os.makedirs(self._data_dir, exist_ok=True)

        self._profiles: Dict[str, CapabilityProfile] = {}
        self._lock = threading.Lock()
        self._load()

    def register(self, name: str, profile: CapabilityProfile):
        """注册或更新能力画像"""
        with self._lock:
            self._profiles[name] = profile
            self._save_locked()

    def get(self, name: str) -> Optional[CapabilityProfile]:
        with self._lock:
            return self._profiles.get(name)

    def list_names(self) -> List[str]:
        with self._lock:
            return list(self._profiles.keys())

    def list_profiles(self) -> Dict[str, CapabilityProfile]:
        with self._lock:
            return dict(self._profiles)

    def query_by_capability(self, attr: str, min_val: Any = True) -> List[Tuple[str, CapabilityProfile]]:
        """
        按能力维度查询

        Args:
            attr: 属性名，如 "web_access", "memory", "reasoning"
            min_val: 最低阈值

        Returns:
            [(name, profile), ...]
        """
        results = []
        with self._lock:
            for name, profile in self._profiles.items():
                val = getattr(profile, attr, None)
                if val is None:
                    continue
                if isinstance(val, bool):
                    if val:
                        results.append((name, profile))
                elif isinstance(val, (int, float)):
                    if val >= min_val if isinstance(min_val, (int, float)) else True:
                        results.append((name, profile))
                elif isinstance(val, dict):
                    # web_access: {enabled, level}
                    if val.get("enabled", False):
                        results.append((name, profile))
        return results

    def delete(self, name: str) -> bool:
        with self._lock:
            if name in self._profiles:
                del self._profiles[name]
                self._save_locked()
                return True
            return False

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_profiles": len(self._profiles),
                "names": list(self._profiles.keys()),
                "data_dir": self._data_dir,
            }

    def _load(self):
        """从 JSON 文件加载"""
        idx_path = os.path.join(self._data_dir, "registry_index.json")
        if not os.path.exists(idx_path):
            return

        try:
            with open(idx_path) as f:
                data = json.load(f)
            for name, profile_data in data.items():
                self._profiles[name] = CapabilityProfile.from_dict(profile_data)
            logger.info(f"RegistryManager: loaded {len(self._profiles)} profiles")
        except Exception as e:
            logger.warning(f"RegistryManager: load failed: {e}")

    def _save_locked(self):
        """保存到 JSON 文件（需持有锁）"""
        idx_path = os.path.join(self._data_dir, "registry_index.json")
        try:
            data = {
                name: profile.to_dict()
                for name, profile in self._profiles.items()
            }
            os.makedirs(os.path.dirname(idx_path), exist_ok=True)
            with open(idx_path, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"RegistryManager: save failed: {e}")

    def scan_skills_directory(self, skills_dir: str = "") -> int:
        """
        扫描 skills 目录，为每个 SKILL.md 生成能力画像

        Args:
            skills_dir: 技能目录路径

        Returns:
            注册的技能数
        """
        if not skills_dir:
            skills_dir = path_resolver.SKILLS_DIR
        if not os.path.isdir(skills_dir):
            logger.warning(f"RegistryManager: skills dir not found: {skills_dir}")
            return 0

        classifier = SkillClassifier()
        count = 0

        for item in os.listdir(skills_dir):
            skill_dir = os.path.join(skills_dir, item)
            skill_md = os.path.join(skill_dir, "SKILL.md")
            if os.path.isdir(skill_dir) and os.path.exists(skill_md):
                profile = classifier.classify_from_file(skill_md)
                if profile:
                    self.register(item, profile)
                    count += 1

        logger.info(f"RegistryManager: scanned {count} skills")
        return count


# ── 全局单例 ──

_REGISTRY_MANAGER: Optional[RegistryManager] = None
_REGISTRY_LOCK = threading.Lock()


def get_registry_manager() -> RegistryManager:
    global _REGISTRY_MANAGER
    if _REGISTRY_MANAGER is None:
        with _REGISTRY_LOCK:
            if _REGISTRY_MANAGER is None:
                _REGISTRY_MANAGER = RegistryManager()
    return _REGISTRY_MANAGER


# ── 测试入口 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 1. 能力画像
    skill_profile = CapabilityProfile(
        web_access={"enabled": True, "level": "search"},
        tool_exec={"enabled": True, "type": "shell"},
        reasoning=3,
        memory=True,
        search=True,
        code_gen=True,
        token_budget="heavy",
        context_window="large",
    )
    print(f"Skill Profile: {skill_profile.summary()}")

    # 2. 宿主环境检测
    hp = HarnessProfile.auto_detect()
    harness_profile = hp.detect_profile()
    print(f"Harness Profile: {harness_profile.summary()}")

    # 3. 适配分数
    matcher = ProfileMatcher()
    score = matcher.compute_adaptation_score(skill_profile, harness_profile)
    print(f"Adaptation Score: {score:.3f}")

    # 4. SkillClassifier
    sample_skill = """
# web-search
Search the web using xiaoyi-web-search API.
Fetches results from multiple search engines.
Requires: node, fetch API
Supports: search, scrape, query expansion
"""
    classifier = SkillClassifier()
    detected = classifier.classify(sample_skill, "web-search")
    print(f"Detected Profile: {detected.summary()}")

    # 5. RegistryManager
    rm = get_registry_manager()
    rm.register("web_search_mock", detected)
    print(f"Registry: {rm.get_stats()}")
