#!/usr/bin/env python3
"""
concurrency_extractor.py — Concurrency DAG extraction

从技能文本中提取可并发执行的步骤，构建 DAG。

ConcurrencyExtractor:
  - analyze(skill_text) → ConcurrencyDAG
  - extract_steps(): split skill into independent steps
  - detect_dependencies(): scan for data dependencies between steps
  - build_dag(): construct DAG of steps

ConcurrencyDAG:
  - nodes: list[ConcurrencyNode]
  - estimated_parallel_speedup: float（估计的并行加速比）
"""

import re
import logging
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ConcurrencyNode:
    """
    并发 DAG 节点

    Attributes:
        step_id: 步骤 ID（唯一标识）
        description: 步骤描述
        text: 步骤文本
        depends_on: 依赖的步骤 ID 列表
        estimated_cost: 估计耗时（毫秒）
        parallelizable: 是否可并行
    """
    step_id: str
    description: str = ""
    text: str = ""
    depends_on: List[str] = field(default_factory=list)
    estimated_cost: float = 100.0  # ms
    parallelizable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "text": self.text[:100] + "..." if len(self.text) > 100 else self.text,
            "depends_on": self.depends_on,
            "estimated_cost": self.estimated_cost,
            "parallelizable": self.parallelizable,
        }


@dataclass
class ConcurrencyDAG:
    """
    并发 DAG

    Attributes:
        nodes: DAG 节点列表
        estimated_parallel_speedup: 估计并行加速比（>1 表示可并行加速）
        critical_path: 关键路径节点 ID 列表（最长依赖链）
        parallel_groups: 可并行执行的节点分组
    """
    nodes: List[ConcurrencyNode] = field(default_factory=list)
    estimated_parallel_speedup: float = 1.0
    critical_path: List[str] = field(default_factory=list)
    parallel_groups: List[List[str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "estimated_parallel_speedup": self.estimated_parallel_speedup,
            "critical_path": self.critical_path,
            "parallel_groups": self.parallel_groups,
        }


class ConcurrencyExtractor:
    """
    并发提取器

    从技能文本中提取可并行的步骤，构建 DAG。
    """

    # 依赖关键词（步骤 B 提到步骤 A 输出的内容 → 存在依赖）
    DEPENDENCY_KEYWORDS = [
        "result", "output", "above", "previous", "from step", "as input",
        "using the", "based on", "pass to", "feed into", "then",
        "结果", "输出", "以上", "上一步", "传入", "输入到",
        "基于", "利用", "使用",
    ]

    # IO 操作关键词（IO 密集型步骤通常不可并行或并行度受限）
    IO_KEYWORDS = [
        "file", "write", "read", "disk", "database", "db",
        "网络", "network", "http", "request", "response",
        "文件", "写入", "读取",
    ]

    # 估计成本关键词
    COST_INDICATORS = {
        "heavy": ["train", "build", "index", "full scan", "large",
                  "训练", "构建", "索引", "全量扫描", "大规模"],
        "medium": ["search", "fetch", "analyze", "process",
                   "搜索", "获取", "分析", "处理"],
        "light": ["check", "validate", "format", "transform",
                  "检查", "验证", "格式化", "转换"],
    }

    def __init__(self):
        self._cached_dag: Optional[ConcurrencyDAG] = None

    def analyze(self, skill_text: str) -> ConcurrencyDAG:
        """
        分析技能文本，提取并发 DAG

        Args:
            skill_text: 技能文本

        Returns:
            ConcurrencyDAG
        """
        if not skill_text or not skill_text.strip():
            return ConcurrencyDAG()

        steps = self.extract_steps(skill_text)
        if not steps:
            return ConcurrencyDAG()

        dependencies = self.detect_dependencies(steps)
        dag = self.build_dag(steps, dependencies)

        self._cached_dag = dag
        return dag

    def extract_steps(self, skill_text: str) -> List[ConcurrencyNode]:
        """
        从技能文本中提取独立的步骤

        按 ## 或 ### 标题分割，每个 section 作为一个步骤。
        """
        steps = []

        # 按标题分割
        sections = re.split(r'\n(?:#{1,3}\s+)', skill_text)
        section_counter = 0

        for section in sections:
            section = section.strip()
            if not section or len(section) < 20:
                continue

            # 提取标题行
            lines = section.split("\n")
            title_line = lines[0].strip()
            body = "\n".join(lines[1:]).strip()

            # 清理标题（移除 Markdown 标记）
            title = re.sub(r'[#*`]', '', title_line).strip()

            # 过滤掉非步骤内容
            if not title or len(title) < 2:
                continue
            if any(title.lower().startswith(w) for w in [
                "note", "example", "config", "setup", "install",
                "备注", "示例", "配置", "设置", "安装",
            ]):
                continue

            step_id = f"step_{section_counter}"

            # 估计成本
            cost = self._estimate_cost(body)

            # 是否可并行
            parallelizable = self._is_parallelizable(body)

            steps.append(ConcurrencyNode(
                step_id=step_id,
                description=title[:100],
                text=section,
                estimated_cost=cost,
                parallelizable=parallelizable,
            ))

            section_counter += 1

        logger.info(f"ConcurrencyExtractor: extracted {len(steps)} steps")
        return steps

    def detect_dependencies(self, steps: List[ConcurrencyNode]) -> Dict[str, List[str]]:
        """
        检测步骤间的数据依赖关系

        策略：
        1. 步骤 B 的文本提到步骤 A 的标题或描述 → 依赖
        2. 步骤 B 使用步骤 A 中定义的变量/函数 → 依赖
        3. 步骤 B 引用步骤 A 的 ID → 依赖
        4. 没有明确依赖 → 独立（可并行）

        Returns:
            {step_id: [depended_step_ids]}
        """
        dependencies: Dict[str, List[str]] = {s.step_id: [] for s in steps}

        for i, step_b in enumerate(steps):
            b_text_lower = step_b.text.lower()
            b_title_lower = step_b.description.lower()

            for j, step_a in enumerate(steps):
                if i <= j:
                    continue  # 不检查自己或后面的步骤

                # 检查 B 是否引用了 A 的标题
                a_title_words = set(step_a.description.lower().split())
                if len(a_title_words) >= 2:
                    # 至少 2 个词的标题匹配才认为有依赖
                    overlap = sum(
                        1 for w in a_title_words
                        if len(w) >= 3 and w in b_text_lower
                    )
                    if overlap >= 2:
                        dependencies[step_b.step_id].append(step_a.step_id)
                        continue

                # 检查依赖关键词
                for kw in self.DEPENDENCY_KEYWORDS:
                    if kw in b_text_lower and step_a.description.lower()[:10] in b_text_lower:
                        dependencies[step_b.step_id].append(step_a.step_id)
                        break

                # 检查变量引用（代码块中的变量被后续使用）
                a_vars = self._extract_variables(step_a.text)
                if a_vars:
                    for var in a_vars:
                        if var in b_text_lower:
                            dependencies[step_b.step_id].append(step_a.step_id)
                            break

            # 时序顺序依赖：如果步骤之间有明确的前后关系词
            if not dependencies[step_b.step_id]:
                for j, step_a in enumerate(steps):
                    if i <= j:
                        continue
                    if any(seq in b_text_lower for seq in [
                        "after", "then", "finally", "subsequently",
                        "之后", "然后", "最后", "随后",
                    ]):
                        # 检查是否提到了上一步
                        if step_a.description.lower()[:20] in b_text_lower:
                            dependencies[step_b.step_id].append(step_a.step_id)
                            break

        return dependencies

    def build_dag(
        self,
        steps: List[ConcurrencyNode],
        dependencies: Dict[str, List[str]],
    ) -> ConcurrencyDAG:
        """
        构建 DAG 并计算并行加速比

        1. 设置节点的 depends_on
        2. 计算拓扑层级
        3. 找出关键路径
        4. 分组可并行节点
        5. 估计并行加速比
        """
        # 1. 设置依赖关系
        node_map = {n.step_id: n for n in steps}
        for step_id, deps in dependencies.items():
            node = node_map.get(step_id)
            if node:
                node.depends_on = deps

        # 2. 拓扑排序和层级计算
        levels = self._compute_levels(steps, dependencies)

        # 3. 关键路径
        critical_path = self._find_critical_path(steps, dependencies, node_map)

        # 4. 并行分组
        parallel_groups = self._compute_parallel_groups(levels, node_map)

        # 5. 加速比计算
        total_serial_cost = sum(n.estimated_cost for n in steps)
        if levels:
            # 关键路径成本（最大层级成本）
            critical_cost = 0
            for level_nodes in levels:
                level_cost = max(
                    (n.estimated_cost for n in level_nodes if n.step_id in node_map),
                    default=0
                )
                critical_cost += level_cost
            speedup = total_serial_cost / max(critical_cost, 1)
        else:
            speedup = 1.0

        dag = ConcurrencyDAG(
            nodes=steps,
            estimated_parallel_speedup=round(speedup, 2),
            critical_path=critical_path,
            parallel_groups=parallel_groups,
        )

        logger.info(
            f"ConcurrencyExtractor: DAG built - {len(steps)} nodes, "
            f"{len(parallel_groups)} parallel groups, "
            f"speedup={dag.estimated_parallel_speedup}"
        )

        return dag

    def _compute_levels(
        self,
        steps: List[ConcurrencyNode],
        dependencies: Dict[str, List[str]],
    ) -> List[List[ConcurrencyNode]]:
        """
        计算拓扑层级（拓扑排序的 BFS 版本）

        Returns:
            [ [level_0_nodes], [level_1_nodes], ... ]
            同层的节点可并行执行
        """
        node_map = {n.step_id: n for n in steps}
        in_degree: Dict[str, int] = {}
        adj: Dict[str, List[str]] = {n.step_id: [] for n in steps}

        for step_id, deps in dependencies.items():
            in_degree.setdefault(step_id, 0)
            for dep_id in deps:
                in_degree[step_id] = in_degree.get(step_id, 0) + 1
                adj.setdefault(dep_id, []).append(step_id)

        # 初始化 in_degree（没有依赖的节点）
        for n in steps:
            if n.step_id not in in_degree:
                in_degree[n.step_id] = 0

        # BFS 拓扑
        levels = []
        queue = [sid for sid, deg in in_degree.items() if deg == 0]

        while queue:
            level_nodes = []
            next_queue = []
            for sid in queue:
                node = node_map.get(sid)
                if node:
                    level_nodes.append(node)
                for neighbor in adj.get(sid, []):
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_queue.append(neighbor)
            if level_nodes:
                levels.append(level_nodes)
            queue = next_queue

        return levels

    def _find_critical_path(
        self,
        steps: List[ConcurrencyNode],
        dependencies: Dict[str, List[str]],
        node_map: Dict[str, ConcurrencyNode],
    ) -> List[str]:
        """
        找出关键路径（最长依赖链）

        使用最长路径算法（DAG 上适用）。
        """
        if not steps:
            return []

        # 拓扑排序
        in_degree: Dict[str, int] = {}
        adj: Dict[str, List[str]] = {n.step_id: [] for n in steps}
        for sid, deps in dependencies.items():
            in_degree.setdefault(sid, 0)
            for dep_id in deps:
                in_degree[sid] = in_degree.get(sid, 0) + 1
                adj.setdefault(dep_id, []).append(sid)
        for n in steps:
            in_degree.setdefault(n.step_id, 0)

        # dist[node] = (累计成本, 前驱节点)
        dist: Dict[str, Tuple[float, str]] = {
            n.step_id: (n.estimated_cost, "")
            for n in steps
        }

        # Kahn 拓扑排序 + DP
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        topo_order = []
        while queue:
            sid = queue.pop(0)
            topo_order.append(sid)
            for neighbor in adj.get(sid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # DP 计算最长路径
        for sid in topo_order:
            current_cost, _ = dist.get(sid, (0, ""))
            for neighbor in adj.get(sid, []):
                n_cost = node_map[neighbor].estimated_cost if neighbor in node_map else 0
                new_cost = current_cost + n_cost
                if new_cost > dist.get(neighbor, (0, ""))[0]:
                    dist[neighbor] = (new_cost, sid)

        # 回溯路径
        if not dist:
            return []

        # 找到最远的终点
        max_cost = -1
        max_node = ""
        for sid, (cost, _) in dist.items():
            if cost > max_cost:
                max_cost = cost
                max_node = sid

        # 回溯
        path = []
        current = max_node
        while current:
            path.insert(0, current)
            _, prev = dist.get(current, (0, ""))
            current = prev

        return path

    def _compute_parallel_groups(
        self,
        levels: List[List[ConcurrencyNode]],
        node_map: Dict[str, ConcurrencyNode],
    ) -> List[List[str]]:
        """
        计算可并行执行的节点分组

        同层且互相没有依赖关系 → 可并行。
        但如果同层节点中 IO 密集且不可并行 → 拆分到不同组。
        """
        groups = []

        for level in levels:
            group = []
            for node in level:
                if node.parallelizable:
                    group.append(node.step_id)
            if group:
                groups.append(group)

        # 对大量节点的层级进一步分拆（避免 IO 竞争）
        MAX_GROUP_SIZE = 5
        refined_groups = []
        for group in groups:
            if len(group) > MAX_GROUP_SIZE:
                for i in range(0, len(group), MAX_GROUP_SIZE):
                    refined_groups.append(group[i:i + MAX_GROUP_SIZE])
            else:
                refined_groups.append(group)

        return refined_groups

    def _estimate_cost(self, text: str) -> float:
        """根据文本内容估计步骤耗时（毫秒）"""
        text_lower = text.lower()

        for weight_name, indicators in self.COST_INDICATORS["heavy"].items() if False else []:
            pass  # 下面直接处理

        # 按关键词匹配
        for indicators, cost in [
            (self.COST_INDICATORS["heavy"], 5000),    # 5 秒
            (self.COST_INDICATORS["medium"], 1000),   # 1 秒
            (self.COST_INDICATORS["light"], 200),     # 0.2 秒
        ]:
            for ind in indicators:
                if ind in text_lower:
                    return cost

        # 没有明确指示：按文本长度估算
        char_count = len(text)
        if char_count > 500:
            return 1000
        elif char_count > 200:
            return 500
        return 100  # 默认 100ms

    def _is_parallelizable(self, text: str) -> bool:
        """判断步骤是否可并行执行"""
        text_lower = text.lower()

        # IO 密集型 → 并行度受限（但仍可能并行）
        io_count = sum(1 for kw in self.IO_KEYWORDS if kw in text_lower)
        if io_count >= 3:
            return False  # 重度 IO，不可并行

        # 依赖外部资源 → 限制并行度
        if any(w in text_lower for w in [
            "exclusive", "lock", "mutex", "single", "sequential",
            "独占", "锁", "互斥", "顺序执行",
        ]):
            return False

        return True

    def _extract_variables(self, text: str) -> Set[str]:
        """从文本中提取定义的变量名"""
        variables = set()

        # Python 变量赋值
        py_vars = re.findall(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=', text, re.MULTILINE)
        variables.update(py_vars)

        # Bash 变量赋值
        bash_vars = re.findall(r'^([a-zA-Z_][a-zA-Z0-9_]*)=', text, re.MULTILINE)
        variables.update(bash_vars)

        # const / let / var (JS)
        js_vars = re.findall(r'(?:const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)', text)
        variables.update(js_vars)

        return variables

    def get_cached_dag(self) -> Optional[ConcurrencyDAG]:
        """获取缓存的 DAG"""
        return self._cached_dag


# ── 便捷函数 ──

def extract_concurrency(skill_text: str) -> Dict[str, Any]:
    """
    便捷的并发提取入口

    Args:
        skill_text: 技能文本

    Returns:
        ConcurrencyDAG 的 dict 表示
    """
    extractor = ConcurrencyExtractor()
    dag = extractor.analyze(skill_text)
    return dag.to_dict()


# ── 测试入口 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    test_skill = """
## Step 1: Fetch Search Results
```python
import requests
results = requests.get(f"https://api.example.com/search?q={query}")
```

## Step 2: Fetch User Profile
```python
profile = requests.get("https://api.example.com/user/profile")
```
Both search results and profile are independent and can be fetched in parallel.

## Step 3: Analyze Data
```python
combined = analyze(results, profile)
print(combined)
```
This depends on results from Step 1 and Step 2.

## Step 4: Generate Report
```python
report = generate_report(combined)
```
Depends on Step 3 output.

## Step 5: Save to File
```python
with open("report.json", "w") as f:
    json.dump(report, f)
```
    """

    extractor = ConcurrencyExtractor()
    dag = extractor.analyze(test_skill)
    print(f"Nodes: {len(dag.nodes)}")
    for node in dag.nodes:
        deps = node.depends_on if node.depends_on else "(none)"
        cost = node.estimated_cost
        para = "✓" if node.parallelizable else "✗"
        print(f"  {node.step_id}: {node.description[:40]} | deps={deps} | cost={cost}ms | P={para}")
    print(f"Critical path: {dag.critical_path}")
    print(f"Parallel groups: {dag.parallel_groups}")
    print(f"Estimated speedup: {dag.estimated_parallel_speedup}x")
