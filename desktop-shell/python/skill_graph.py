#!/usr/bin/env python3
"""
skill_graph.py — SkillGraph + RAG Retrieval + Graph Evolution + GRPO Runner

四合一模块，构成 GalaxyOS 技能图感知检索与进化闭环。

组件:
  SkillGraph           — 技能有向图（144 节点，277+ 边）
  GraphAwareRetriever  — 种子 BFS+Beam 检索器
  GraphEvolutionEngine — Merge/Split/Reinforce/Decay/Prune
  GRPORunner           — Group Normalized Advantage + β warmup-decay
"""

import json
import os
import math
import random
import time
import logging
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── 常量 ──
BFS_DEPTH = 3
BEAM_WIDTH = 5
K_MAX = 8
MERGE_TAU = 0.85
SPLIT_P_HAT_LOW = 0.15
SPLIT_P_HAT_HIGH = 0.40
ALPHA = 0.05
GAMMA = 0.99
GRPO_G = 8
PROGRESSIVE_THETA = 0.6
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
GRAPH_FILE = os.path.join(DATA_DIR, "skill_graph.json")


# ═══════════════════════════════════════════════════════════
# SkillGraph
# ═══════════════════════════════════════════════════════════

@dataclass
class SkillNode:
    """技能图节点"""
    name: str
    description: str = ""
    layer: int = 0
    module_type: str = ""
    success_rate: float = 0.5
    usage_count: int = 0
    dependencies: List[str] = field(default_factory=list)


@dataclass
class SkillEdge:
    """技能图边"""
    source: str
    target: str
    relation: str = "enhance"   # prereq | enhance | co_occur | conflicts
    weight: float = 1.0
    reinforce_count: int = 0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "weight": round(self.weight, 4),
            "reinforce_count": self.reinforce_count,
        }


class SkillGraph:
    """技能有向图 — 144 节点覆盖 GalaxyOS 全模块

    边类型：
      prereq  — A 是 B 的前置依赖
      enhance — A 提升 B 的使用质量
      co_occur — A 和 B 常一起使用
    """

    def __init__(self, auto_load=True):
        self.nodes: Dict[str, SkillNode] = {}
        self.edges: List[SkillEdge] = []
        self._adj: Dict[str, List[SkillEdge]] = {}  # source → edges
        self._rev_adj: Dict[str, List[SkillEdge]] = {}  # target → edges (反查依赖)
        self._dirty = False
        if auto_load:
            self._try_load()

    # ── 节点操作 ──

    def add_node(self, name: str, description="", layer=0, module_type="",
                 dependencies=None) -> "SkillNode":
        if name in self.nodes:
            node = self.nodes[name]
            if description:
                node.description = description
            if layer:
                node.layer = layer
            if module_type:
                node.module_type = module_type
            return node
        node = SkillNode(name=name, description=description,
                         layer=layer, module_type=module_type)
        if dependencies:
            node.dependencies = list(dependencies)
        self.nodes[name] = node
        if name not in self._adj:
            self._adj[name] = []
        if name not in self._rev_adj:
            self._rev_adj[name] = []
        self._dirty = True
        return node

    def remove_node(self, name: str) -> bool:
        if name not in self.nodes:
            return False
        # 删关联边
        self.edges = [e for e in self.edges if e.source != name and e.target != name]
        self._adj.pop(name, None)
        self._rev_adj.pop(name, None)
        # 删出边引用
        for src in self._adj:
            self._adj[src] = [e for e in self._adj[src] if e.target != name]
        for tgt in self._rev_adj:
            self._rev_adj[tgt] = [e for e in self._rev_adj[tgt] if e.source != name]
        del self.nodes[name]
        self._dirty = True
        return True

    def get_node(self, name: str) -> Optional[SkillNode]:
        return self.nodes.get(name)

    # ── 边操作 ──

    def add_edge(self, source: str, target: str, relation="enhance",
                 weight=1.0) -> SkillEdge:
        # 去重
        for e in self.edges:
            if e.source == source and e.target == target and e.relation == relation:
                e.weight = max(e.weight, weight)
                return e
        edge = SkillEdge(source=source, target=target,
                         relation=relation, weight=weight)
        self.edges.append(edge)
        self._adj.setdefault(source, []).append(edge)
        self._rev_adj.setdefault(target, []).append(edge)
        self._dirty = True
        return edge

    def remove_edge(self, source: str, target: str, relation=None) -> bool:
        before = len(self.edges)
        self.edges = [e for e in self.edges
                      if not (e.source == source and e.target == target
                              and (relation is None or e.relation == relation))]
        self._adj[source] = [e for e in self._adj.get(source, [])
                             if not (e.target == target and
                                     (relation is None or e.relation == relation))]
        self._rev_adj[target] = [e for e in self._rev_adj.get(target, [])
                                 if not (e.source == source and
                                         (relation is None or e.relation == relation))]
        if len(self.edges) < before:
            self._dirty = True
            return True
        return False

    def get_successors(self, node: str) -> List[SkillEdge]:
        return self._adj.get(node, [])

    def get_predecessors(self, node: str) -> List[SkillEdge]:
        return self._rev_adj.get(node, [])

    def out_degree(self, node: str) -> int:
        return len(self._adj.get(node, []))

    def in_degree(self, node: str) -> int:
        return len(self._rev_adj.get(node, []))

    # ── 统计 ──

    def stats(self) -> dict:
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "avg_out_degree": round(sum(len(v) for v in self._adj.values()) / max(len(self.nodes), 1), 2),
        }

    # ── 持久化 ──

    def save(self, path=None):
        path = path or GRAPH_FILE
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            "version": "1.0",
            "timestamp": time.time(),
            "nodes": {n: {
                "name": nd.name,
                "description": nd.description,
                "layer": nd.layer,
                "module_type": nd.module_type,
                "success_rate": nd.success_rate,
                "usage_count": nd.usage_count,
                "dependencies": nd.dependencies,
            } for n, nd in self.nodes.items()},
            "edges": [e.to_dict() for e in self.edges],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._dirty = False
        return path

    def load(self, path=None):
        path = path or GRAPH_FILE
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.nodes.clear()
        self.edges.clear()
        self._adj.clear()
        self._rev_adj.clear()
        for n, nd in data.get("nodes", {}).items():
            self.nodes[n] = SkillNode(**nd)
        for ed in data.get("edges", []):
            edge = SkillEdge(**ed)
            self.edges.append(edge)
            self._adj.setdefault(edge.source, []).append(edge)
            self._rev_adj.setdefault(edge.target, []).append(edge)
        self._dirty = False
        return True

    def _try_load(self):
        try:
            self.load()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# GraphAwareRetriever
# ═══════════════════════════════════════════════════════════

class GraphAwareRetriever:
    """图感知检索器

    流程：
      1. 种子节点选择 (embedding 相似度 or 关键词匹配)
      2. BFS 反向找依赖/前置（深度 BFS_DEPTH）
      3. Beam Search 正向找协作技能（宽度 BEAM_WIDTH）
      4. Topological Sort + 种子得分排序
    """

    def __init__(self, graph: SkillGraph):
        self.graph = graph
        # 中→英映射（用于中文 query 匹配模块名/描述）
        self._cn_map = self._build_cn_map()

    def _build_cn_map(self) -> Dict[str, str]:
        """从模块描述/名称构建中文关键词→模块名映射"""
        m = {}
        cn_keywords = {
            "幻觉": "hallucination_guard", "记忆": "memory_unified", "检索": "retrieval_hub",
            "嵌入": "embedding", "图谱": "knowledge_graph_gnn", "推理": "causal_reasoning",
            "思维": "graph_of_thoughts", "进化": "self_evolution_engine", "技能": "skill_coordinator",
            "编译": "skill_compiler", "规划": "planning_engine", "评估": "retrieval_evaluator",
            "生成": "generative_agents", "情绪": "emotion_memory", "反射": "memory_reflector",
            "学习": "auto_learner", "遗忘": "smart_forgetter", "压缩": "hierarchical_context",
            "路由": "router", "加权": "rrf", "融合": "ssm_kan_fusion",
            "液态": "liquid_ssm", "验证": "chain_of_verification",
        }
        for word, mod in cn_keywords.items():
            m[word] = mod
        # 从模块描述中提取中文词
        for name, node in self.graph.nodes.items():
            desc = node.description
            if not desc:
                continue
            # 简单提取：将中文description的前10个字作为关键词
            for i in range(0, min(len(desc), 40), 2):
                seg = desc[i:i+4]
                if len(seg) >= 2 and all('\u4e00' <= c <= '\u9fff' for c in seg):
                    m[seg] = name
                    break
        return m

    def retrieve(self, query: str, top_k=K_MAX) -> List[Tuple[str, float]]:
        """主入口：query → [(node_name, score), ...]"""
        seeds = self._seed_selection(query)
        if not seeds:
            return []

        # 反向 BFS：找依赖链
        backward = self._bfs_backward(set(s[0] for s in seeds), BFS_DEPTH)

        # 正向 Beam：找协作技能
        beam_scores = self._beam_forward(set(s[0] for s in seeds) | backward, BEAM_WIDTH)

        # 合并 & 拓扑排序
        candidates = set(s[0] for s in seeds) | backward | set(beam_scores.keys())
        seed_scores = {s[0]: s[1] for s in seeds}
        for node, sc in beam_scores.items():
            seed_scores[node] = max(seed_scores.get(node, 0), sc)

        ranked = self._topological_sort(list(candidates), seed_scores)
        return ranked[:top_k]

    def _seed_selection(self, query: str) -> List[Tuple[str, float]]:
        q_lower = query.lower()
        results = []

        # A: en→cn — 英文关键词匹配中文描述
        for name, node in self.graph.nodes.items():
            desc = node.description.lower()
            score = 0.0
            # 精确匹配模块名
            if name.lower() in q_lower or q_lower in name.lower():
                score = 1.0
            # 描述匹配
            elif desc and any(word in desc for word in q_lower.split()):
                score = 0.8
            if score > 0:
                results.append((name, score))

        # B: cn→en — 中文关键词映射到英文模块名
        for cn_word, en_mod in self._cn_map.items():
            if cn_word in query:
                if en_mod in self.graph.nodes:
                    results.append((en_mod, 0.9))

        # C: cn→cn — 中文 query 直接匹配中文描述
        if any('\u4e00' <= c <= '\u9fff' for c in query):
            for name, node in self.graph.nodes.items():
                desc = node.description
                if not desc:
                    continue
                # 拆 query 为单字或多字词
                query_chars = set(c for c in query if '\u4e00' <= c <= '\u9fff')
                if len(query_chars) >= 2:
                    match_count = sum(1 for c in query_chars if c in desc)
                    if match_count / len(query_chars) >= 0.5:
                        results.append((name, 0.7 * match_count / len(query_chars)))

        # D: en→en — 英文 keyword 硬匹配
        for name, node in self.graph.nodes.items():
            desc = node.description
            en_words = set(w for w in q_lower.split() if w.isascii() and len(w) > 2)
            if en_words:
                matches = sum(1 for w in en_words if w in name.lower() or (desc and w in desc.lower()))
                if matches / len(en_words) >= 0.5:
                    results.append((name, 0.6 * matches / len(en_words)))

        # 去重、合并分数
        merged = {}
        for name, sc in results:
            merged[name] = max(merged.get(name, 0), sc)
        return sorted(merged.items(), key=lambda x: -x[1])

    def _bfs_backward(self, seeds: Set[str], depth=BFS_DEPTH) -> Set[str]:
        """反向 BFS：找 seeds 的前置依赖"""
        visited = set(seeds)
        queue = deque((n, 0) for n in seeds)
        while queue:
            node, d = queue.popleft()
            if d >= depth:
                continue
            for edge in self.graph.get_predecessors(node):
                if edge.source not in visited:
                    visited.add(edge.source)
                    queue.append((edge.source, d + 1))
        return visited - seeds

    def _beam_forward(self, seeds: Set[str], beam_width=BEAM_WIDTH) -> Dict[str, float]:
        """Beam Search 正向：从 seeds 找协作技能"""
        scores = {}
        candidates = []
        for seed in seeds:
            for edge in self.graph.get_successors(seed):
                if edge.target not in seeds:
                    score = edge.weight * (1 + self.graph.nodes.get(edge.target, SkillNode("")).success_rate)
                    candidates.append((edge.target, score))
        # 按分数排序取 top beam_width
        candidates.sort(key=lambda x: -x[1])
        for name, sc in candidates[:beam_width]:
            scores[name] = sc
        return scores

    def _topological_sort(self, candidates: List[str],
                          seed_scores: Dict[str, float]) -> List[Tuple[str, float]]:
        """拓扑排序 + 种子得分"""
        in_degree = {n: 0 for n in candidates}
        for node in candidates:
            for edge in self.graph.get_predecessors(node):
                if edge.source in candidates:
                    in_degree[node] = in_degree.get(node, 0) + 1

        queue = deque(n for n, d in in_degree.items() if d == 0)
        levels = {}
        level = 0
        while queue:
            for _ in range(len(queue)):
                n = queue.popleft()
                levels[n] = level
                for edge in self.graph.get_successors(n):
                    if edge.target in candidates:
                        in_degree[edge.target] -= 1
                        if in_degree[edge.target] == 0:
                            queue.append(edge.target)
            level += 1

        result = []
        for n in candidates:
            lv = levels.get(n, 0)
            sc = seed_scores.get(n, 0)
            # 同层按种子得分降序
            result.append((n, sc, lv))
        result.sort(key=lambda x: (-x[2], -x[1]))

        # 同层内按种子得分进一步排序
        final = []
        current_level = None
        bucket = []
        for n, sc, lv in result:
            if lv != current_level:
                if bucket:
                    bucket.sort(key=lambda x: -x[1])
                    final.extend(bucket)
                current_level = lv
                bucket = [(n, sc)]
            else:
                bucket.append((n, sc))
        if bucket:
            bucket.sort(key=lambda x: -x[1])
            final.extend(bucket)
        return final


# ═══════════════════════════════════════════════════════════
# GraphEvolutionEngine
# ═══════════════════════════════════════════════════════════

class GraphEvolutionEngine:
    """图演化引擎

    机制:
      Merge    — 高度共现(τ=0.85)且成功率接近的节点合并
      Split    — 技能太宽泛(p̂ ∈ [0.15,0.40])时拆分
      Deprecate — 长期低成功率/低使用率节点标记 deprecated
      Reinforce — 成功调用增强边权重 (α=0.05)
      Decay    — 长期不用的边衰减 (γ=0.99)
      Prune    — 权重 < 0.1 或孤立的边修剪
      Progressive — 按层渐进解锁 (θ=0.6)
    """

    def __init__(self, graph: SkillGraph):
        self.graph = graph
        self.step_count = 0

    def step(self, usage_log: List[Dict] = None) -> dict:
        """执行一轮演化，返回操作统计"""
        self.step_count += 1
        ops = {"merge": 0, "split": 0, "deprecate": 0,
               "reinforce": 0, "decay": 0, "prune": 0}

        # 1. Reinforce — 成功调用增强边权重
        if usage_log:
            for log in usage_log:
                name = log.get("name")
                success = log.get("success", False)
                if name and success:
                    for edge in self.graph.get_successors(name):
                        edge.weight = min(2.0, edge.weight * (1 + ALPHA))
                        edge.reinforce_count += 1
                        ops["reinforce"] += 1

        # 2. Decay — 长期不用的边衰减
        for edge in self.graph.edges:
            if edge.reinforce_count < self.step_count * 0.1:  # 使用率低
                edge.weight *= GAMMA
                ops["decay"] += 1

        # 3. Prune — 权重过低或孤立的边
        before = len(self.graph.edges)
        self.graph.edges = [e for e in self.graph.edges if e.weight >= 0.1]
        ops["prune"] = before - len(self.graph.edges)

        # 4. Merge — 高度共现节点合并
        merge_candidates = []
        for edge in self.graph.edges:
            if edge.relation == "co_occur" and edge.weight >= MERGE_TAU:
                src_node = self.graph.get_node(edge.source)
                tgt_node = self.graph.get_node(edge.target)
                if src_node and tgt_node:
                    rate_diff = abs(src_node.success_rate - tgt_node.success_rate)
                    if rate_diff < 0.1:
                        merge_candidates.append((edge.source, edge.target))
        for src, tgt in merge_candidates[:3]:  # 每轮最多 3 组
            self._merge_nodes(src, tgt)
            ops["merge"] += 1

        # 5. Split — 宽泛节点拆分
        split_candidates = []
        for name, node in self.graph.nodes.items():
            if SPLIT_P_HAT_LOW <= node.success_rate <= SPLIT_P_HAT_HIGH:
                split_candidates.append(name)
        for name in split_candidates[:2]:  # 每轮最多 2 个
            self._split_node(name)
            ops["split"] += 1

        # 6. Progressive — 低层模块逐步解锁
        for name, node in self.graph.nodes.items():
            if node.usage_count > 0:
                continue
            if node.layer <= 2:
                continue
            # 检查前置依赖是否已经稳定
            deps_ready = all(
                self.graph.nodes.get(d) and self.graph.nodes[d].success_rate > PROGRESSIVE_THETA
                for d in node.dependencies
            )
            if not deps_ready:
                # 标记为 locked
                if node.module_type and "locked" not in node.module_type:
                    node.module_type = f"{node.module_type}_locked"

        # 清理 locked 节点的边
        self._cleanup_locked()

        self.graph._dirty = True
        return ops

    def _merge_nodes(self, node_a: str, node_b: str):
        """合并 node_b 到 node_a"""
        if node_a not in self.graph.nodes or node_b not in self.graph.nodes:
            return
        node_a_obj = self.graph.nodes[node_a]
        node_b_obj = self.graph.nodes[node_b]
        # 合并描述
        if node_b_obj.description and node_b_obj.description not in node_a_obj.description:
            node_a_obj.description = f"{node_a_obj.description}; {node_b_obj.description}"
        # 转移边
        for edge in self.graph.get_successors(node_b):
            if edge.target != node_a:
                if not any(e.target == edge.target for e in self.graph.get_successors(node_a)):
                    self.graph.add_edge(node_a, edge.target, edge.relation, edge.weight)
        # 删旧节点
        self.graph.remove_node(node_b)

    def _split_node(self, name: str):
        """拆分宽泛节点为两个子技能"""
        node = self.graph.get_node(name)
        if not node:
            return
        desc = node.description or name
        # 简单拆分：取前/后半段描述作为子技能
        mid = len(desc) // 2
        sub_a_name = f"{name}_sub_a"
        sub_b_name = f"{name}_sub_b"
        # 复制继承
        self.graph.add_node(sub_a_name, desc[:mid], node.layer, node.module_type)
        self.graph.add_node(sub_b_name, desc[mid:], node.layer, node.module_type)
        # 子技能之间建立 co_occur 边
        self.graph.add_edge(sub_a_name, sub_b_name, "co_occur", 0.8)
        # 原节点的前置依赖分给子技能
        for edge in self.graph.get_predecessors(name):
            self.graph.add_edge(edge.source, sub_a_name, edge.relation, edge.weight * 0.7)
            self.graph.add_edge(edge.source, sub_b_name, edge.relation, edge.weight * 0.7)
        # 原节点的后置依赖分给子技能
        for edge in self.graph.get_successors(name):
            self.graph.add_edge(sub_a_name, edge.target, edge.relation, edge.weight * 0.7)
            self.graph.add_edge(sub_b_name, edge.target, edge.relation, edge.weight * 0.7)
        # 删原节点
        self.graph.remove_node(name)

    def _cleanup_locked(self):
        """清理 locked → 常规引用以外的边"""
        locked_nodes = {
            n for n, nd in self.graph.nodes.items()
            if nd.module_type and "locked" in nd.module_type
        }
        for n in locked_nodes:
            self.graph._adj[n] = [
                e for e in self.graph._adj.get(n, [])
                if e.relation not in ("prereq", "enhance") or
                self.graph.nodes.get(e.target, SkillNode("")).usage_count > 0
            ]


# ═══════════════════════════════════════════════════════════
# GRPORunner
# ═══════════════════════════════════════════════════════════

class GRPORunner:
    """GRPO 策略优化执行器（arXiv:2606.04036 SDPG 风格）

    核心：
      - Group Normalized Advantage: (r_i - μ_group) / σ_group
      - β warmup-decay: 训练早期 β 从 0.01 线性升温到 0.1，后期衰减
    """

    def __init__(self, graph: SkillGraph):
        self.graph = graph
        self.beta = 0.01
        self.epsilon = 0.2
        self._step = 0
        self._group_size = GRPO_G
        self._reward_history: List[float] = []

    def update_beta(self, total_steps: int, warmup_steps: int = 100,
                    decay_start: int = 500):
        """β warmup-decay 调度"""
        self._step += 1
        if self._step < warmup_steps:
            # 升温 0.01 → 0.1
            ratio = self._step / max(warmup_steps, 1)
            self.beta = 0.01 + ratio * (0.1 - 0.01)
        elif self._step >= decay_start:
            # 衰减 0.1 → 0.01
            decay_ratio = (self._step - decay_start) / 500.0
            self.beta = max(0.01, 0.1 - decay_ratio * 0.09)
        else:
            self.beta = 0.1

    def compute_advantage(self, rewards: List[float]) -> List[float]:
        """Group Normalized Advantage

        A_i = (r_i - μ_group) / (σ_group + ε)
        按 GRPO_G 分组归一化
        """
        advantages = []
        for i in range(0, len(rewards), self._group_size):
            group = rewards[i:i + self._group_size]
            mu = sum(group) / max(len(group), 1)
            var = sum((r - mu) ** 2 for r in group) / max(len(group), 1)
            sigma = math.sqrt(var + 1e-8)
            for r in group:
                adv = (r - mu) / sigma
                advantages.append(adv)
        self._reward_history.extend(rewards)
        return advantages

    def optimize_step(self, usage_log: List[Dict]) -> dict:
        """单步优化

        usage_log: [{"name": "module_name", "reward": 0.0~1.0}, ...]
        """
        if not usage_log:
            return {"nodes_updated": 0}

        rewards = [log.get("reward", 0.5) for log in usage_log]
        advantages = self.compute_advantage(rewards)
        updated = 0

        for log, adv in zip(usage_log, advantages):
            name = log.get("name")
            node = self.graph.get_node(name)
            if not node:
                continue
            # 裁剪 (clipped surrogate objective)
            adv_clipped = max(-self.epsilon, min(self.epsilon, adv))
            # 更新 success_rate
            delta = self.beta * adv_clipped * (1 - node.success_rate)
            node.success_rate = max(0.0, min(1.0, node.success_rate + delta))
            node.usage_count += 1
            updated += 1

        return {"nodes_updated": updated, "beta": round(self.beta, 4)}

    def stats(self) -> dict:
        if not self._reward_history:
            return {"beta": round(self.beta, 4), "avg_reward": 0, "steps": self._step}
        return {
            "beta": round(self.beta, 4),
            "avg_reward": round(sum(self._reward_history[-100:]) / max(len(self._reward_history[-100:]), 1), 4),
            "steps": self._step,
            "total_rewards": len(self._reward_history),
        }


# ═══════════════════════════════════════════════════════════
# 便捷入口：从 MODULE_REGISTRY 构建全量图
# ═══════════════════════════════════════════════════════════

_GRAPH_SINGLETON = None
_RETRIEVER_SINGLETON = None
_EVOLUTION_SINGLETON = None
_GRPO_SINGLETON = None


def build_graph_from_registry(registry: dict) -> SkillGraph:
    """从 MODULE_REGISTRY 构建全量 SkillGraph"""
    g = SkillGraph(auto_load=False)
    layer_names = defaultdict(list)
    for name, mod in registry.items():
        layer = getattr(mod, "layer", 0)
        desc = getattr(mod, "description", "") or ""
        mt = getattr(mod, "module_type", "") or ""
        deps = getattr(mod, "dependencies", []) or []
        g.add_node(name, desc, layer, mt, deps)
        layer_names[layer].append(name)

    # 添加边：同层常 co_occur
    for layer, names in layer_names.items():
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                g.add_edge(a, b, "co_occur", 0.5)
                g.add_edge(b, a, "co_occur", 0.5)

    # 添加依赖边
    for name, mod in registry.items():
        deps = getattr(mod, "dependencies", []) or []
        for dep in deps:
            if dep in g.nodes:
                g.add_edge(dep, name, "prereq", 0.9)

    return g


def get_skill_graph() -> SkillGraph:
    global _GRAPH_SINGLETON
    if _GRAPH_SINGLETON is None:
        _GRAPH_SINGLETON = SkillGraph()
    return _GRAPH_SINGLETON


def get_retriever() -> GraphAwareRetriever:
    global _RETRIEVER_SINGLETON
    if _RETRIEVER_SINGLETON is None:
        _RETRIEVER_SINGLETON = GraphAwareRetriever(get_skill_graph())
    return _RETRIEVER_SINGLETON


def get_evolution() -> GraphEvolutionEngine:
    global _EVOLUTION_SINGLETON
    if _EVOLUTION_SINGLETON is None:
        _EVOLUTION_SINGLETON = GraphEvolutionEngine(get_skill_graph())
    return _EVOLUTION_SINGLETON


def get_grpo() -> GRPORunner:
    global _GRPO_SINGLETON
    if _GRPO_SINGLETON is None:
        _GRPO_SINGLETON = GRPORunner(get_skill_graph())
    return _GRPO_SINGLETON


def reset_graph():
    """重置单例（测试用）"""
    global _GRAPH_SINGLETON, _RETRIEVER_SINGLETON, _EVOLUTION_SINGLETON, _GRPO_SINGLETON
    _GRAPH_SINGLETON = None
    _RETRIEVER_SINGLETON = None
    _EVOLUTION_SINGLETON = None
    _GRPO_SINGLETON = None


# ── 快捷方法 ──

def skill_graph_retrieve(query: str, top_k=K_MAX) -> List[Tuple[str, float]]:
    """快捷: 检索与 query 相关的技能"""
    return get_retriever().retrieve(query, top_k)


def skill_graph_stats() -> dict:
    """快捷: 图统计"""
    return get_skill_graph().stats()


def skill_graph_evolution_step(usage_log: List[Dict] = None) -> dict:
    """快捷: 执行一轮图演化"""
    return get_evolution().step(usage_log)


def skill_graph_grpo_step(usage_log: List[Dict]) -> dict:
    """快捷: 执行一轮 GRPO 优化"""
    g = get_grpo()
    g.update_beta(100, 100, 500)
    return g.optimize_step(usage_log)
