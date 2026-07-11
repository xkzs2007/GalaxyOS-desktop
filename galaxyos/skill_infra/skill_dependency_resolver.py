"""
SkillDependencyResolver — 技能依赖关系解析器

支持：
1. 从 SKILL.md frontmatter 的 dependencies 字段解析依赖声明
2. 构建依赖关系图
3. 循环依赖检测
4. 拓扑排序（确定安装顺序）
5. 缺失依赖检查
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from galaxyos.skill_infra.skill_md_parser import SKILLMDParser, ParsedSkill
from pathlib import Path


@dataclass
class SkillNode:
    skill_name: str
    invocation_type: str = "user-invoked"
    dependencies: List[str] = field(default_factory=list)
    dependents: List[str] = field(default_factory=list)
    leading_words: List[str] = field(default_factory=list)


class DependencyGraph:
    def __init__(self):
        self._nodes: Dict[str, SkillNode] = {}

    def add_node(self, node: SkillNode) -> None:
        self._nodes[node.skill_name] = node

    def add_edge(self, dependent: str, dependency: str) -> None:
        if dependent in self._nodes and dependency in self._nodes:
            if dependency not in self._nodes[dependent].dependencies:
                self._nodes[dependent].dependencies.append(dependency)
            if dependent not in self._nodes[dependency].dependents:
                self._nodes[dependency].dependents.append(dependent)

    def get_node(self, skill_name: str) -> Optional[SkillNode]:
        return self._nodes.get(skill_name)

    @property
    def nodes(self) -> Dict[str, SkillNode]:
        return self._nodes

    def topological_sort(self) -> List[str]:
        in_degree: Dict[str, int] = {name: 0 for name in self._nodes}
        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep in in_degree:
                    in_degree[node.skill_name] += 1

        queue = [name for name, deg in in_degree.items() if deg == 0]
        result = []

        while queue:
            name = queue.pop(0)
            result.append(name)
            node = self._nodes[name]
            for dependent in node.dependents:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(self._nodes):
            remaining = set(self._nodes.keys()) - set(result)
            raise ValueError(f"Circular dependency detected among: {remaining}")

        return result

    def detect_cycles(self) -> List[List[str]]:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {name: WHITE for name in self._nodes}
        cycles = []
        path = []

        def dfs(node_name: str) -> None:
            color[node_name] = GRAY
            path.append(node_name)
            node = self._nodes[node_name]
            for dep in node.dependencies:
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    cycle_start = path.index(dep)
                    cycles.append(path[cycle_start:].copy())
                elif color[dep] == WHITE:
                    dfs(dep)
            path.pop()
            color[node_name] = BLACK

        for name in self._nodes:
            if color[name] == WHITE:
                dfs(name)

        return cycles


class SkillDependencyResolver:
    def __init__(self, parser: Optional[SKILLMDParser] = None):
        self._parser = parser or SKILLMDParser()
        self._graph = DependencyGraph()
        self._installed: Set[str] = set()

    def register_skill(self, parsed: ParsedSkill) -> None:
        invocation_type = "model-invoked" if not parsed.disable_model_invocation else "user-invoked"
        node = SkillNode(
            skill_name=parsed.name,
            invocation_type=invocation_type,
            dependencies=parsed.dependencies,
            leading_words=parsed.leading_words,
        )
        self._graph.add_node(node)

    def resolve_all(self) -> None:
        for node in self._graph.nodes.values():
            for dep in node.dependencies:
                if dep in self._graph.nodes:
                    self._graph.add_edge(node.skill_name, dep)

    @property
    def graph(self) -> DependencyGraph:
        return self._graph

    def check_missing(self, skill_name: str) -> List[str]:
        node = self._graph.get_node(skill_name)
        if not node:
            return []
        return [dep for dep in node.dependencies if dep not in self._installed]

    def mark_installed(self, skill_name: str) -> None:
        self._installed.add(skill_name)

    def install_order(self) -> List[str]:
        return self._graph.topological_sort()

    def detect_cycles(self) -> List[List[str]]:
        return self._graph.detect_cycles()
