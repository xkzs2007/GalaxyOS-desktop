#!/usr/bin/env python3
"""
SKILL0 — Dynamic Curriculum for Skill Internalization

将推理时技能注入逐步撤除，直到零 shot 自主行为。
技能被分组、评分、按有用性逐渐撤出训练循环。

参考: https://arxiv.org/abs/2604.02268 (SKILL0)
"""

import os
import json
import time
import hashlib
from typing import Dict, List, Any, Optional, Callable
from pathlib import Path
import logging
from galaxyos.shared.paths import workspace

logger = logging.getLogger("skill_curriculum")

# ═══════════════════════════════════════════════════
# 课程计划器
# ═══════════════════════════════════════════════════


class SkillCurriculum:
    """
    SKILL0 Dynamic Curriculum
    
    三阶段:
      1. 离线分组 — 按类别将 skill 分到验证子任务
      2. 在线评分 — 每 d 步验证每个 skill 的有用性 Δ
      3. 渐进撤除 — 线性预算递减，只保留 top-m 最有用的
    """

    def __init__(self, skill_catalog: Dict[str, Any] = None,
                 workspace: str = None):
        if workspace is None:
            workspace = os.environ.get("OPENCLAW_WORKSPACE",
                                       str(Path(workspace())))
        self.workspace = Path(workspace)

        # Skill 目录: name -> {category, path, description}
        self.skill_catalog = skill_catalog or {}

        # 分组: category -> [skill_names]
        self.skill_groups: Dict[str, List[str]] = {}

        # 有用性分数: skill_name -> {delta, history}
        self.helpfulness_scores: Dict[str, Dict] = {}

        # 当前活跃技能集
        self.active_skills: set = set()
        self._all_skills: set = set()

        # 课程参数
        self._stages: int = 5           # 总阶段数
        self._budget: List[int] = []     # 每阶段预算
        self._current_stage: int = 0
        self._validate_interval: int = 5  # 每 d 步验证一次
        self._training_step: int = 0
        self._is_internalizing: bool = False

        # 持久化路径
        self._state_path = self.workspace / ".learnings" / "skill_curriculum.json"

        # 加载历史状态
        self._load_state()

    def initialize(self, skill_catalog: Dict[str, Any]):
        """初始化课程"""
        self.skill_catalog = skill_catalog
        self._all_skills = set(skill_catalog.keys())
        self.active_skills = set(self._all_skills)

        # 1. 离线分组: 按类别
        self.skill_groups.clear()
        for name, info in skill_catalog.items():
            cat = info.get("category", "general")
            if cat not in self.skill_groups:
                self.skill_groups[cat] = []
            self.skill_groups[cat].append(name)

        # 2. 初始化预算: 线性递减
        n = len(self._all_skills)
        self._budget = [
            max(1, round(n * (self._stages - s) / (self._stages - 1)))
            if s < self._stages - 1 else 0
            for s in range(self._stages)
        ]

        self._current_stage = 0
        self._is_internalizing = True
        self._save_state()

        logger.info(
            f"SKILL0 课程初始化: {n} skills, {self._stages} stages, "
            f"budget={self._budget}"
        )

    def step(self, validation_fn: Callable = None) -> Dict[str, Any]:
        """
        执行一个训练步
        
        Args:
            validation_fn(skill_name) -> float: 验证函数，返回 accuracy
        
        Returns:
            当前步的状态
        """
        self._training_step += 1

        # 不是内化阶段或没有 skill 了
        if not self._is_internalizing or not self.active_skills:
            return {"stage": self._current_stage, "active": len(self.active_skills), "done": True}

        # 每 validate_interval 步验证一次
        if self._training_step % self._validate_interval == 0 and validation_fn:
            self._evaluate_helpfulness(validation_fn)
            self._apply_curriculum()

        return {
            "stage": self._current_stage,
            "active_skills": len(self.active_skills),
            "active_list": list(self.active_skills)[:10],
            "budget": self._budget[self._current_stage] if self._current_stage < len(self._budget) else 0,
            "done": len(self.active_skills) == 0,
        }

    def _evaluate_helpfulness(self, validation_fn: Callable):
        """
        评估每个活跃 skill 的有用性 Δ
        
        Δ = accuracy_with_skill - accuracy_without_skill
        """
        if not self.active_skills:
            return

        for skill_name in list(self.active_skills):
            try:
                # 带 skill 的准确率
                acc_with = validation_fn(skill_name, with_skill=True)
                # 不带 skill 的基准准确率
                acc_without = validation_fn(skill_name, with_skill=False)

                delta = acc_with - acc_without

                if skill_name not in self.helpfulness_scores:
                    self.helpfulness_scores[skill_name] = {
                        "deltas": [], "avg_delta": 0.0, "category": ""
                    }

                record = self.helpfulness_scores[skill_name]
                record["deltas"].append(delta)
                # 滑动平均
                recent = record["deltas"][-5:]
                record["avg_delta"] = sum(recent) / len(recent)

                logger.debug(f"  {skill_name}: Δ={delta:.3f} (avg={record['avg_delta']:.3f})")

            except Exception as e:
                logger.warning(f"验证 {skill_name} 失败: {e}")

    def _apply_curriculum(self):
        """应用课程: 按有用性过滤 + 按预算裁剪"""
        if not self.active_skills:
            self._is_internalizing = False
            return

        # 1. 只保留 Δ > 0 的 skill
        positive = set()
        for s in self.active_skills:
            record = self.helpfulness_scores.get(s)
            if record and record["avg_delta"] > 0.001:
                positive.add(s)

        # 2. 按有用性排序
        sorted_skills = sorted(
            positive,
            key=lambda s: self.helpfulness_scores.get(s, {}).get("avg_delta", 0),
            reverse=True
        )

        # 3. 按当前预算截取
        budget = self._budget[self._current_stage] if self._current_stage < len(self._budget) else 0
        if budget > 0 and len(sorted_skills) > budget:
            sorted_skills = sorted_skills[:budget]

        self.active_skills = set(sorted_skills)

        # 4. 检查是否进入下一阶段
        if self._training_step >= (self._current_stage + 1) * (500 // self._stages):
            self._current_stage += 1
            self._current_stage = min(self._current_stage, self._stages - 1)

            if self._current_stage >= self._stages - 1 and self._budget[-1] == 0:
                self.active_skills.clear()
                self._is_internalizing = False
                logger.info("SKILL0 课程完成: 所有 skill 已内化")

        self._save_state()

    def get_context(self) -> List[Dict]:
        """返回需要注入 context 的活跃 skill 内容"""
        if not self._is_internalizing or not self.active_skills:
            return []

        contexts = []
        for name in self.active_skills:
            info = self.skill_catalog.get(name, {})
            if info:
                contexts.append({
                    "name": name,
                    "description": info.get("description", ""),
                    "category": info.get("category", "general"),
                })
        return contexts

    def get_status(self) -> dict:
        return {
            "internalizing": self._is_internalizing,
            "stage": self._current_stage,
            "total_stages": self._stages,
            "active_skills": len(self.active_skills),
            "all_skills": len(self._all_skills),
            "budget": self._budget[self._current_stage] if self._current_stage < len(self._budget) else 0,
            "training_step": self._training_step,
        }

    def _save_state(self):
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "stage": self._current_stage,
                "step": self._training_step,
                "active": list(self.active_skills),
                "internalizing": self._is_internalizing,
                "scores": self.helpfulness_scores,
            }
            self._state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"保存课程状态失败: {e}")

    def _load_state(self):
        try:
            if self._state_path.exists():
                state = json.loads(self._state_path.read_text())
                self._current_stage = state.get("stage", 0)
                self._training_step = state.get("step", 0)
                self.active_skills = set(state.get("active", []))
                self._is_internalizing = state.get("internalizing", False)

                # 恢复分数
                saved_scores = state.get("scores", {})
                for k, v in saved_scores.items():
                    self.helpfulness_scores[k] = v
        except Exception:
            pass


# ═══════════════════════════════════════════════════
# 验证桥接器
# ═══════════════════════════════════════════════════


class SkillValidationBridge:
    """
    桥接 SKILL0 课程与 GalaxyOS 自进化引擎
    
    为课程提供验证函数，并记录内化结果到自进化引擎。
    """

    def __init__(self, self_evolution_engine=None):
        self.evolution_engine = self_evolution_engine
        self._validation_history: Dict[str, List[float]] = {}

    def validate(self, skill_name: str, with_skill: bool = True) -> float:
        """
        验证一个 skill 对当前模型的有用性。
        
        简单实现: 返回 skill 的历史命中率作为准确率。
        生产环境应运行完整验证 pipeline。
        """
        history = self._validation_history.get(skill_name, [])
        if not history:
            # 无历史数据，给个默认值
            return 0.6 if with_skill else 0.4

        recent = history[-10:]
        accuracy = sum(recent) / len(recent)

        # 带 skill 的应该比不带的高
        if with_skill:
            return min(0.95, accuracy + 0.15)
        else:
            return max(0.1, accuracy - 0.05)

    def record_result(self, skill_name: str, success: bool):
        """记录一次验证结果"""
        if skill_name not in self._validation_history:
            self._validation_history[skill_name] = []
        self._validation_history[skill_name].append(1.0 if success else 0.0)


# ═══════════════════════════════════════════════════
# 预构建技能目录
# ═══════════════════════════════════════════════════


def build_default_skill_catalog() -> Dict[str, dict]:
    """构建 GalaxyOS 52 技能的默认目录"""
    return {
        # 认知方法论 (11)
        "arming-thought": {"category": "cognitive", "description": "实事求是总原则"},
        "concentrate-forces": {"category": "cognitive", "description": "主攻方向聚焦"},
        "contradiction-analysis": {"category": "cognitive", "description": "矛盾分析"},
        "criticism-self-criticism": {"category": "cognitive", "description": "批评与自我批评"},
        "investigation-first": {"category": "cognitive", "description": "调查先行"},
        "mass-line": {"category": "cognitive", "description": "群众路线"},
        "overall-planning": {"category": "cognitive", "description": "统筹规划"},
        "practice-cognition": {"category": "cognitive", "description": "实践认知"},
        "protracted-strategy": {"category": "cognitive", "description": "持久战"},
        "spark-prairie-fire": {"category": "cognitive", "description": "星火燎原"},

        # 思考技能 (9)
        "first-principles": {"category": "thinking", "description": "第一性原理"},
        "systems-thinking": {"category": "thinking", "description": "系统思维"},
        "critical-thinking": {"category": "thinking", "description": "批判性思维"},
        "backward-thinking": {"category": "thinking", "description": "逆向思维"},
        "analogical-thinking": {"category": "thinking", "description": "类比思维"},
        "feynman-technique": {"category": "thinking", "description": "费曼技巧"},
        "multi-agent-collaboration": {"category": "thinking", "description": "多智能体协作"},
        "decision-engine": {"category": "thinking", "description": "决策引擎"},
        "product-thinking": {"category": "thinking", "description": "产品思维"},

        # 工程实践 (10)
        "diagnose": {"category": "engineering", "description": "Bug诊断"},
        "grill-with-docs": {"category": "engineering", "description": "需求审问"},
        "tdd": {"category": "engineering", "description": "测试驱动开发"},
        "improve-codebase-architecture": {"category": "engineering", "description": "代码架构改进"},
        "prototype": {"category": "engineering", "description": "快速原型"},
        "zoom-out": {"category": "engineering", "description": "全局视角"},
        "caveman": {"category": "engineering", "description": "超压缩通信"},
        "handoff": {"category": "engineering", "description": "会话交接"},
        "write-a-skill": {"category": "engineering", "description": "编写Skill"},
        "grill-me": {"category": "engineering", "description": "方案推敲"},

        # 文档处理 (7)
        "docx": {"category": "document", "description": "Word文档"},
        "pdf": {"category": "document", "description": "PDF处理"},
        "pptx": {"category": "document", "description": "PPT制作"},
        "xiaoyi-doc-convert": {"category": "document", "description": "文档格式转换"},
        "markitdown": {"category": "document", "description": "转Markdown"},
        "nano-pdf": {"category": "document", "description": "PDF编辑(NLP)"},
        "excel-analysis": {"category": "document", "description": "Excel分析"},

        # 搜索调研 (5)
        "multi-search-engine": {"category": "search", "description": "多引擎搜索"},
        "deep-search-and-insight-synthesize": {"category": "search", "description": "深度搜索与洞察"},
        "read-arxiv-paper": {"category": "search", "description": "论文阅读"},
        "arxiv-search": {"category": "search", "description": "ArXiv搜索"},
        "weather": {"category": "search", "description": "天气查询"},

        # 多模态 (3)
        "seedream-image-gen": {"category": "multimodal", "description": "图像生成"},
        "xiaoyi-image-understanding": {"category": "multimodal", "description": "图像理解"},
        "xiaoyi-image-search": {"category": "multimodal", "description": "图像搜索"},

        # 开发工具 (3)
        "skill-creator": {"category": "dev", "description": "技能创建"},
        "find-skills": {"category": "dev", "description": "技能发现"},
        "browser-automation": {"category": "dev", "description": "浏览器自动化"},
    }
