#!/usr/bin/env python3
"""
skill_compiler.py — SkVM compile pipeline

SkVM (Skill Virtual Machine) 编译管道。

SkillCompiler:
  - compile(skill_text, profile, harness) → CompiledArtifact
  - profile_check(): compute capability gap
  - env_bind(): check dependencies, generate setup scripts
  - skill_prune(): remove steps beyond model capability
  - optimize_text(): compress/rewrite for target model

CompiledArtifact:
  - optimized_text: 优化后的技能文本
  - solidified_scripts: 固化的脚本列表
  - concurrency_dag: 并发执行 DAG
  - env_setup: 环境设置脚本
  - profile_footprint: 能力足迹（编译时依赖的能力）
"""

import json
import os
import re
import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CompiledArtifact:
    """
    编译产物

    Attributes:
        skill_name: 技能名称
        optimized_text: 优化后的文本
        solidified_scripts: 固化的可执行脚本列表
        concurrency_dag: 并发执行 DAG 描述
            {"nodes": [{"id": str, "description": str}],
             "edges": [{"from": str, "to": str}]}
        env_setup: 环境准备脚本
        profile_footprint: 编译时依赖的能力
        compilation_time_ms: 编译耗时
        warnings: 编译警告
    """
    skill_name: str = ""
    optimized_text: str = ""
    solidified_scripts: List[Dict[str, Any]] = field(default_factory=list)
    concurrency_dag: Dict[str, Any] = field(default_factory=dict)
    env_setup: str = ""
    profile_footprint: Dict[str, Any] = field(default_factory=dict)
    compilation_time_ms: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "optimized_text": self.optimized_text,
            "solidified_scripts": self.solidified_scripts,
            "concurrency_dag": self.concurrency_dag,
            "env_setup": self.env_setup,
            "profile_footprint": self.profile_footprint,
            "compilation_time_ms": self.compilation_time_ms,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CompiledArtifact":
        return cls(
            skill_name=data.get("skill_name", ""),
            optimized_text=data.get("optimized_text", ""),
            solidified_scripts=data.get("solidified_scripts", []),
            concurrency_dag=data.get("concurrency_dag", {}),
            env_setup=data.get("env_setup", ""),
            profile_footprint=data.get("profile_footprint", {}),
            compilation_time_ms=data.get("compilation_time_ms", 0.0),
            warnings=data.get("warnings", []),
        )


class SkillCompiler:
    """
    SkVM 编译管道

    编译流程：
    1. profile_check(): 检查能力差距，确定编译策略
    2. env_bind(): 检查依赖，生成 setup 脚本
    3. skill_prune(): 移除超出模型能力的步骤
    4. optimize_text(): 压缩/重写以适应目标模型
    5. 生成 CompiledArtifact
    """

    def __init__(self, max_steps: int = 10, max_step_length: int = 2000):
        self.max_steps = max_steps
        self.max_step_length = max_step_length

    def compile(
        self,
        skill_text: str,
        skill_name: str = "",
        profile: Optional[Dict] = None,
        harness: Optional[Dict] = None,
    ) -> CompiledArtifact:
        """
        编译技能到 CompiledArtifact

        Args:
            skill_text: 原始技能文本（SKILL.md 全量内容）
            skill_name: 技能名称
            profile: 技能能力画像（CapabilityProfile.to_dict）
            harness: 宿主环境画像（CapabilityProfile.to_dict）

        Returns:
            CompiledArtifact
        """
        start_time = time.time()
        artifact = CompiledArtifact(skill_name=skill_name or "unnamed_skill")

        try:
            # 1. Profile check
            gap_info = self.profile_check(profile or {}, harness or {})
            if gap_info["critical_gaps"]:
                artifact.warnings.append(
                    f"Critical capability gaps: {gap_info['critical_gaps']}"
                )

            # 2. Environment binding
            setup_scripts = self.env_bind(skill_text, profile or {})
            artifact.env_setup = "\n".join(setup_scripts) if setup_scripts else ""

            # 3. Skill pruning
            steps = self._extract_steps(skill_text)
            pruned_steps = self.skill_prune(steps, gap_info)
            artifact.profile_footprint = gap_info["capability_footprint"]

            # 4. Optimize text
            artifact.optimized_text = self.optimize_text(
                skill_text, pruned_steps, harness or {}
            )

            # 5. Build concurrency DAG
            from concurrency_extractor import ConcurrencyExtractor
            extractor = ConcurrencyExtractor()
            dag = extractor.analyze(artifact.optimized_text)
            artifact.concurrency_dag = {
                "nodes": [
                    {"id": n.step_id, "description": n.description}
                    for n in dag.nodes
                ] if hasattr(dag, 'nodes') else [],
            }
            if hasattr(dag, 'nodes'):
                edges = []
                for n in dag.nodes:
                    for dep in n.depends_on:
                        edges.append({"from": dep, "to": n.step_id})
                artifact.concurrency_dag["edges"] = edges

            # 6. Solidify scripts
            from cde_solidifier import CodeSolidifier
            solidifier = CodeSolidifier()
            templates = solidifier.detect_templates(artifact.optimized_text)
            for template in templates:
                try:
                    script_text = solidifier.solidify(template, {})
                    artifact.solidified_scripts.append({
                        "description": template.get("description", ""),
                        "script": script_text,
                        "language": template.get("language", "text"),
                    })
                except Exception as e:
                    artifact.warnings.append(
                        f"Solidify failed for template '{template.get('description', '')}': {e}"
                    )

        except Exception as e:
            artifact.warnings.append(f"Compilation error: {e}")
            logger.error(f"SkillCompiler.compile failed: {e}", exc_info=True)

        artifact.compilation_time_ms = (time.time() - start_time) * 1000
        return artifact

    def profile_check(
        self,
        profile: Dict[str, Any],
        harness: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        检查能力差距

        Returns:
            {
                "critical_gaps": [str],  # 必须但不满足的能力
                "optional_gaps": [str],  # 可选但不满足的能力
                "capability_footprint": {"memory": bool, ...},  # 编译足迹
            }
        """
        critical_gaps = []
        optional_gaps = []
        footprint = {}

        # 关键能力检查
        critical_checks = [
            ("memory", profile.get("memory", False), harness.get("memory", False)),
            ("web_access", profile.get("web_access", {}).get("enabled", False),
             harness.get("web_access", {}).get("enabled", False)),
        ]

        for name, profile_val, harness_val in critical_checks:
            footprint[name] = profile_val
            if profile_val and not harness_val:
                critical_gaps.append(name)

        # 可选能力检查
        optional_checks = [
            ("reasoning", "reasoning"),
            ("search", "search"),
            ("code_gen", "code_gen"),
            ("multimodal.vision", "multimodal", "vision"),
        ]

        for check in optional_checks:
            name = check[0]
            p_val = self._nested_get(profile, list(check[1:]) if len(check) > 2 else [check[1]], False)
            h_val = self._nested_get(harness, list(check[1:]) if len(check) > 2 else [check[1]], False)
            if isinstance(p_val, bool) and p_val and not h_val:
                optional_gaps.append(name)

        # reasoning 等级检查
        p_reasoning = profile.get("reasoning", 1)
        h_reasoning = harness.get("reasoning", 1)
        footprint["reasoning"] = p_reasoning
        if p_reasoning > h_reasoning:
            optional_gaps.append(f"reasoning(L{p_reasoning}>L{h_reasoning})")

        return {
            "critical_gaps": critical_gaps,
            "optional_gaps": optional_gaps,
            "capability_footprint": footprint,
        }

    def env_bind(self, skill_text: str, profile: Dict[str, Any]) -> List[str]:
        """
        检查依赖并生成环境设置脚本

        Args:
            skill_text: 技能文本（含依赖声明）
            profile: 能力画像

        Returns:
            环境准备脚本列表（shell 或 pip 命令）
        """
        setup_scripts = []
        text_lower = skill_text.lower()

        # 1. pip 依赖
        pip_deps = set()

        # imports
        import_matches = re.findall(
            r'(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)', skill_text
        )
        for imp in import_matches:
            known_pkgs = {
                "jieba": "jieba",
                "sklearn": "scikit-learn",
                "numpy": "numpy",
                "torch": "torch",
                "onnxruntime": "onnxruntime",
                "openai": "openai",
                "hnswlib": "hnswlib",
                "matplotlib": "matplotlib",
                "pandas": "pandas",
                "PIL": "pillow",
                "cv2": "opencv-python",
            }
            if imp in known_pkgs:
                pip_deps.add(known_pkgs[imp])

        # pip install 声明
        pip_matches = re.findall(r'pip\s+install\s+(\S+)', skill_text)
        for pm in pip_matches:
            pip_deps.add(pm)

        if pip_deps:
            setup_scripts.append(f"pip install {' '.join(sorted(pip_deps))}")

        # 2. 系统依赖
        sys_deps = set()
        apt_matches = re.findall(r'apt(-get)?\s+install\s+([^\n]+)', skill_text)
        for _, pkgs in apt_matches:
            for pkg in pkgs.strip().split():
                pkg = pkg.strip()
                if pkg:
                    sys_deps.add(pkg)

        # requirements.txt
        if "requirements.txt" in text_lower or "requirements" in text_lower:
            setup_scripts.append("[ -f requirements.txt ] && pip install -r requirements.txt || true")

        # Node.js
        if "npm install" in text_lower or "package.json" in text_lower:
            setup_scripts.append("npm install")

        # 3. Docker
        if "docker" in text_lower and "dockerfile" in text_lower:
            setup_scripts.append("docker build -t skill-env .")

        return setup_scripts

    def skill_prune(
        self,
        steps: List[Dict[str, Any]],
        gap_info: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        移除超出模型能力的步骤

        根据能力差距信息，移除或降级不满足的步骤。
        """
        if not steps:
            return []

        critical_gaps = set(gap_info.get("critical_gaps", []))
        optional_gaps = gap_info.get("optional_gaps", [])

        pruned = []
        for step in steps:
            step_text = step.get("text", "").lower()
            description = step.get("description", "").lower()
            combined = f"{step_text} {description}"

            # 如果步骤依赖 critical 能力且环境不支持 → 跳过
            if "web" in combined and "web_access" in critical_gaps:
                pruned.append(self._make_pruned_note(step, "Web access unavailable"))
                continue

            if "memory" in combined and "memory" in critical_gaps:
                pruned.append(self._make_pruned_note(step, "Memory unavailable"))
                continue

            # Optional 能力缺失：标记但保留
            for gap in optional_gaps:
                if "reasoning" in gap.lower() and gap.lower() in combined:
                    step["note"] = f"Required reasoning: {gap}"

            pruned.append(step)

        return pruned[:self.max_steps]

    def optimize_text(
        self,
        original_text: str,
        pruned_steps: List[Dict[str, Any]],
        harness: Dict[str, Any],
    ) -> str:
        """
        压缩/重写原始文本以适应目标模型

        策略：
        1. 移除注释和调试信息
        2. 压缩过长的步骤说明
        3. 重写为简洁的指令格式
        4. 确保 token 长度在合理范围内
        """
        if not pruned_steps:
            return original_text[:self.max_step_length * 3]

        # 构建优化后的文本
        parts = []
        parts.append(f"# {pruned_steps[0].get('skill_name', 'Optimized Skill')}")

        for step in pruned_steps:
            text = step.get("text", "")
            # 缩短
            if len(text) > self.max_step_length:
                text = text[:self.max_step_length - 3] + "..."

            note = step.get("note", "")
            if note:
                parts.append(f"> [!NOTE] {note}")
            parts.append(text)

        optimized = "\n\n".join(parts)

        # 总长度限制
        total_limit = self.max_steps * self.max_step_length * 2
        if len(optimized) > total_limit:
            optimized = optimized[:total_limit - 3] + "..."

        return optimized

    def _extract_steps(self, skill_text: str) -> List[Dict[str, Any]]:
        """从 SKILL.md 中提取步骤"""
        steps = []

        # 按 ## 或 --- 分割
        sections = re.split(r'\n##\s+|\n---\s*\n', skill_text)
        for section in sections:
            section = section.strip()
            if not section:
                continue

            # 提取标题
            title_match = re.match(r'(.+?)[\n:]+', section[:100])
            title = title_match.group(1).strip() if title_match else ""

            # 提取代码块
            code_blocks = re.findall(r'```(\w*)\n(.*?)```', section, re.DOTALL)

            steps.append({
                "skill_name": title,
                "description": title,
                "text": section,
                "code_blocks": code_blocks,
                "note": "",
            })

        return steps

    def _make_pruned_note(self, step: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """生成已裁剪步骤的标记"""
        return {
            "skill_name": step.get("skill_name", ""),
            "description": step.get("description", ""),
            "text": f"<!-- PRUNED: {reason} --> {step.get('description', '')}",
            "code_blocks": [],
            "note": f"Pruned: {reason}",
        }

    def _nested_get(self, d: Dict, keys: List[str], default: Any = None) -> Any:
        """安全嵌套字典取值"""
        current = d
        for key in keys:
            if isinstance(current, dict):
                current = current.get(key, {})
            else:
                return default
        return current if current != {} else default


# ── 便捷编译函数 ──

def compile_skill(
    skill_text: str,
    skill_name: str = "",
    profile: Optional[Dict] = None,
    harness: Optional[Dict] = None,
) -> CompiledArtifact:
    """
    便捷的 skill 编译入口
    
    Args:
        skill_text: SKILL.md 全量内容
        skill_name: 技能名称
        profile: 技能能力画像（可选，会自动检测）
        harness: 宿主环境画像（可选，会自动检测）

    Returns:
        CompiledArtifact
    """
    compiler = SkillCompiler()

    # 自动检测 profile 和 harness（如未提供）
    if profile is None:
        from capability_registry import SkillClassifier
        classifier = SkillClassifier()
        cp = classifier.classify(skill_text, skill_name)
        profile = cp.to_dict()

    if harness is None:
        from capability_registry import HarnessProfile
        hp = HarnessProfile.auto_detect()
        harness = hp.detect_profile().to_dict()

    return compiler.compile(skill_text, skill_name, profile, harness)


# ── 测试入口 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    test_skill = """
# test-skill
A test skill for web search and memory recall.

## Step 1: Analyze Query
Use reasoning to understand the user intent.

```python
import jieba
words = jieba.lcut(query)
```

## Step 2: Web Search
Fetch results from web API. Requires: web_access enabled.

```bash
curl -X GET https://api.example.com/search?q={query}
```

## Step 3: Memory Recall
Recall relevant memories using the hybrid engine.

Requires memory module. Also needs: numpy, sklearn.

## Step 4: Generate Response
Combine results and format the output.
    """

    artifact = compile_skill(test_skill, "test-skill")
    print(f"Compilation time: {artifact.compilation_time_ms:.1f}ms")
    print(f"Warnings: {artifact.warnings}")
    print(f"Env setup: {artifact.env_setup[:200] if artifact.env_setup else '(none)'}")
    print(f"Solidified scripts: {len(artifact.solidified_scripts)}")
    print(f"Concurrency DAG: {json.dumps(artifact.concurrency_dag, indent=2)}")
    print(f"Profile footprint: {artifact.profile_footprint}")
