#!/usr/bin/env python3
"""
GNN Graph Builder — neural_pipeline.py 缺失的桥梁

将 gat_layer.py / graphsage_layer.py / graph_constructor.py 包装成
neural_pipeline.py 期望的三个接口：
  SynapseGraph          — 图数据容器
  SynapseGraphBuilder   — 从数据库/JSONL 构建图
  SynapseGATEncoder     — GAT 编码器
  SynapseGraphSAGEEncoder — GraphSAGE 编码器

Author: 小艺 Claw
Created: 2026-06-06
"""

import json
import os
import re
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import torch
import numpy as np

logger = logging.getLogger("gnn_graph_builder")


# ═══════════════════════════════════════════════════
# SynapseGraph — 图数据容器
# ═══════════════════════════════════════════════════

@dataclass
class SynapseGraph:
    """图数据容器，供 GNN 编码器消费"""
    node_ids: List[str] = field(default_factory=list)
    node_labels: List[str] = field(default_factory=list)
    node_features: torch.Tensor = field(default_factory=lambda: torch.empty(0))
    edge_index: torch.Tensor = field(default_factory=lambda: torch.empty(2, 0, dtype=torch.long))
    edge_attr: Optional[torch.Tensor] = None
    id_to_idx: Dict[str, int] = field(default_factory=dict)

    @property
    def num_nodes(self) -> int:
        return len(self.node_ids)

    @property
    def num_edges(self) -> int:
        return self.edge_index.shape[1] if self.edge_index.numel() > 0 else 0


# ═══════════════════════════════════════════════════
# SynapseGraphBuilder — 从 SQLite/JSONL 构建图
# ═══════════════════════════════════════════════════

class SynapseGraphBuilder:
    """
    突触图构建器

    支持两种数据源：
      - load_from_database(): 从记忆库 SQLite 拉取
      - load_graph_from_memory(): 从已有 list[dict] 构造
    """

    def __init__(
        self,
        workspace_path: str = None,
        feature_dim: int = 64,
        device: str = "cpu",
    ):
        self.ws = workspace_path or os.path.expanduser("~/.openclaw/workspace")
        self.feature_dim = feature_dim
        self.device = torch.device(device)
        self._tokenizer = None  # 懒加载 jieba

    # ── 公共入口 ──

    def load_from_database(self, top_k: int = 300) -> SynapseGraph:
        """
        从记忆库（SQLite/JSONL）加载神经元和突触，构建图。

        优先走现有 ConsolidationEngine 的持久化：
          1. 检查 synapses.jsonl 和 neurons.jsonl
          2. 如果没有，尝试走 dag_context.db
          3. 如果都没有，返回空图
        """
        neurons = self._load_neurons()
        synapses = self._load_synapses()
        if not neurons:
            # 尝试从 dag_context.db 推断（用最近 top_k 对话）
            neurons = self._load_from_dag_db(top_k)
            if not neurons:
                logger.warning("SynapseGraphBuilder: 没有可用的神经元数据")
                # 返回一个空的 edge_index 以避免 downstream 崩溃
                return SynapseGraph(
                    node_features=torch.empty(0, self.feature_dim, device=self.device),
                    edge_index=torch.empty(2, 0, dtype=torch.long, device=self.device),
                )
        return self.load_graph_from_memory(neurons, synapses)

    def load_graph_from_memory(
        self,
        neurons: List[dict],
        synapses: List[dict],
    ) -> SynapseGraph:
        """
        从内存中的神经元/突触列表构建 SynapseGraph。
        """
        # 去重（按 id）
        seen_ids = set()
        unique_neurons = []
        for n in neurons:
            nid = n.get("id", "")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                unique_neurons.append(n)

        node_ids = [n["id"] for n in unique_neurons]
        node_labels = [n.get("content", "")[:80] for n in unique_neurons]
        id_to_idx = {nid: i for i, nid in enumerate(node_ids)}
        n = len(node_ids)
        if n == 0:
            return SynapseGraph(
                node_features=torch.empty(0, self.feature_dim, device=self.device),
                edge_index=torch.empty(2, 0, dtype=torch.long, device=self.device),
            )

        # 节点特征：从 embedding 字段还原，失败则随机
        features = torch.zeros(n, self.feature_dim, device=self.device)
        for i, n in enumerate(unique_neurons):
            emb = n.get("embedding") or n.get("vector") or n.get("features")
            if emb is not None:
                if isinstance(emb, (list, tuple)):
                    arr = np.asarray(emb, dtype=np.float32)
                elif isinstance(emb, np.ndarray):
                    arr = emb.astype(np.float32)
                elif isinstance(emb, str):
                    try:
                        arr = np.frombuffer(bytes.fromhex(emb[2:]), dtype=np.float32) if emb.startswith("0x") else np.frombuffer(emb.encode(), dtype=np.float32)
                    except Exception:
                        arr = None
                else:
                    arr = None
                if arr is not None and len(arr) >= self.feature_dim:
                    features[i] = torch.from_numpy(arr[:self.feature_dim])
                elif arr is not None and len(arr) > 0:
                    features[i, :len(arr)] = torch.from_numpy(arr)
                    features[i, len(arr):] = torch.randn(self.feature_dim - len(arr), device=self.device)
                else:
                    features[i] = torch.randn(self.feature_dim, device=self.device) * 0.01
            else:
                # 用 jieba 词频构造轻量特征
                features[i] = torch.from_numpy(
                    self._text_to_features(n.get("content", ""))
                )

        # 构建边
        src_list = []
        dst_list = []
        edge_weight_list = []
        for s in synapses:
            src = id_to_idx.get(s.get("source_id", ""))
            dst = id_to_idx.get(s.get("target_id", ""))
            if src is not None and dst is not None and src != dst:
                src_list.append(src)
                dst_list.append(dst)
                edge_weight_list.append(s.get("weight", 0.5))

        if not src_list:
            # 无显式边 → 按 jieba 词重叠构造（双向，Top-3 相似）
            self._build_implicit_edges(node_ids, node_labels, src_list, dst_list, edge_weight_list, id_to_idx)

        edge_index = torch.tensor([src_list, dst_list], dtype=torch.long, device=self.device)
        edge_attr = torch.tensor(edge_weight_list, dtype=torch.float32, device=self.device).unsqueeze(1) if edge_weight_list else None

        return SynapseGraph(
            node_ids=node_ids,
            node_labels=node_labels,
            node_features=features,
            edge_index=edge_index,
            edge_attr=edge_attr,
            id_to_idx=id_to_idx,
        )

    # ── 内部方法 ──

    def _load_neurons(self) -> List[dict]:
        """从 JSONL 加载神经元"""
        path = os.path.join(self.ws, ".learnings/synapse_network/neurons.jsonl")
        if not os.path.exists(path):
            # 也查 GalaxyOS 路径
            alt = os.path.join(self.ws, "GalaxyOS/skills/llm-memory-integration/.learnings/synapse_network/neurons.jsonl")
            if os.path.exists(alt):
                path = alt
            else:
                # 从 existing ConsolidationEngine 路径尝试
                alt2 = os.path.join(self.ws, "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/.learnings/synapse_network/neurons.jsonl")
                if os.path.exists(alt2):
                    path = alt2
                else:
                    return []
        result = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        result.append(json.loads(line))
        except Exception as e:
            logger.debug(f"加载 neurons.jsonl 失败: {e}")
        return result

    def _load_synapses(self) -> List[dict]:
        """从 JSONL 加载突触"""
        path = os.path.join(self.ws, ".learnings/synapse_network/synapses.jsonl")
        if not os.path.exists(path):
            alt = os.path.join(self.ws, "GalaxyOS/skills/llm-memory-integration/.learnings/synapse_network/synapses.jsonl")
            if os.path.exists(alt):
                path = alt
            else:
                alt2 = os.path.join(self.ws, "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/.learnings/synapse_network/synapses.jsonl")
                if os.path.exists(alt2):
                    path = alt2
                else:
                    return []
        result = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        result.append(json.loads(line))
        except Exception as e:
            logger.debug(f"加载 synapses.jsonl 失败: {e}")
        return result

    def _load_from_dag_db(self, top_k: int) -> List[dict]:
        """从 dag_context.db 提取最近 top_k 条记录作为虚拟神经元"""
        db_path = os.path.expanduser("~/.openclaw/dag_context.db")
        if not os.path.exists(db_path):
            return []
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT id, content, timestamp FROM dag_nodes ORDER BY timestamp DESC LIMIT ?",
                (top_k,)
            ).fetchall()
            conn.close()
            neurons = []
            for row in rows:
                nid, content, ts = row[0], row[1] or "", row[2] or ""
                # dedup by content[:60]
                if any(n.get("content", "")[:60] == content[:60] for n in neurons):
                    continue
                neurons.append({
                    "id": str(nid),
                    "content": content[:1000],
                    "created_at": ts,
                })
            return neurons
        except Exception as e:
            logger.debug(f"从 DAG 加载失败: {e}")
            return []

    def _build_implicit_edges(
        self,
        node_ids: List[str],
        node_labels: List[str],
        src_list: list,
        dst_list: list,
        weight_list: list,
        id_to_idx: Dict[str, int],
        max_neighbors: int = 3,
    ):
        """为没有显式边的节点按 jieba 词重叠添加隐式边"""
        try:
            import jieba
        except ImportError:
            return
        n = len(node_ids)
        tokens = []
        for lbl in node_labels:
            try:
                tokens.append(set(jieba.lcut(lbl.lower())))
            except Exception:
                tokens.append(set())
        for i in range(n):
            if not tokens[i]:
                continue
            scores = []
            for j in range(n):
                if i == j or not tokens[j]:
                    continue
                overlap = len(tokens[i] & tokens[j])
                if overlap >= 1:
                    scores.append((j, overlap / max(len(tokens[i] | tokens[j]), 1)))
            scores.sort(key=lambda x: -x[1])
            for j, _ in scores[:max_neighbors]:
                src_list.append(i)
                dst_list.append(j)
                weight_list.append(0.3)

    def _text_to_features(self, text: str, dim: int = None) -> np.ndarray:
        dim = dim or self.feature_dim
        """简易文本特征：jieba TF 向量（确定性）"""
        try:
            import jieba
            words = jieba.lcut(text[:500].lower())
        except ImportError:
            words = re.findall(r'[\w\u4e00-\u9fff]+', text[:500].lower())
        if not words:
            return np.random.randn(dim).astype(np.float32) * 0.01
        # hash-based bag-of-words → dim 维向量
        vec = np.zeros(dim, dtype=np.float32)
        seen = set()
        for w in words:
            if w not in seen:
                seen.add(w)
                idx = hash(w) % dim
                vec[idx] += 1.0
        # 归一化
        norm = np.linalg.norm(vec) or 1.0
        return vec / norm


# ═══════════════════════════════════════════════════
# SynapseGATEncoder — GAT 编码器封装
# ═══════════════════════════════════════════════════

class SynapseGATEncoder(torch.nn.Module):
    """
    GAT 编码器，支持稠密/稀疏切换

    - dense: O(N²·d) — 小图 (<500 节点) 更快
    - sparse: O(E·d) — 大图 (>500 节点) 省内存，默认

    输入: SynapseGraph
    输出: (N, output_dim) 节点嵌入
    """

    def __init__(
        self,
        input_dim: int = 64,
        hidden_dim: int = 64,
        output_dim: int = 64,
        num_heads: int = 4,
        num_layers: int = 2,
        dropout: float = 0.3,
        use_sparse: bool = True,  # 默认稀疏 GAT
    ):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.num_heads = num_heads
        self.num_layers = num_layers
        self.use_sparse = use_sparse

        if use_sparse:
            from gat_layer import SparseGAT
            self.gat = SparseGAT(
                input_dim=input_dim,
                hidden_dim=hidden_dim,
                output_dim=output_dim,
                num_heads=num_heads,
                num_layers=num_layers,
                dropout=dropout,
            )
        else:
            from gat_layer import GAT
            self.gat = GAT(
                input_dim=input_dim,
                hidden_dim=hidden_dim,
                output_dim=output_dim,
                num_heads=num_heads,
                num_layers=num_layers,
                dropout=dropout,
            )

    def forward(self, graph: SynapseGraph) -> torch.Tensor:
        if graph.num_nodes == 0:
            return torch.empty(0, self.output_dim, device=graph.node_features.device)

        if self.use_sparse:
            edge_index = graph.edge_index
            _n = graph.node_features.size(0)
            _self_loop = torch.arange(_n, device=edge_index.device).unsqueeze(0).expand(2, -1)
            edge_index = torch.cat([edge_index, _self_loop], dim=1)
            return self.gat(graph.node_features, edge_index)
        else:
            features = graph.node_features
            n = features.size(0)
            adj = torch.zeros(n, n, device=features.device)
            if graph.num_edges > 0:
                src, dst = graph.edge_index[0], graph.edge_index[1]
                adj[src, dst] = 1.0
            adj = adj + torch.eye(n, device=features.device)
            return self.gat(features, adj)


# ═══════════════════════════════════════════════════
# SynapseGraphSAGEEncoder — GraphSAGE 编码器封装
# ═══════════════════════════════════════════════════

class SynapseGraphSAGEEncoder(torch.nn.Module):
    """
    GraphSAGE 编码器，包装 graphsage_layer.GraphSAGE

    输入: SynapseGraph
    输出: (N, output_dim) 节点嵌入
    """

    def __init__(
        self,
        input_dim: int = 64,
        hidden_dims: List[int] = None,
        output_dim: int = 64,
        dropout: float = 0.3,
        aggregator_type: str = "lstm",
    ):
        super().__init__()
        from graphsage_layer import GraphSAGE
        hidden_dims = hidden_dims or [128, 64]

        self.input_dim = input_dim
        self.output_dim = output_dim
        self.hidden_dims = hidden_dims

        layers = []

        # 输入层
        layers.append(GraphSAGE(
            input_dim=input_dim,
            hidden_dim=hidden_dims[0] if hidden_dims else output_dim,
            output_dim=hidden_dims[1] if len(hidden_dims) > 1 else output_dim,
            dropout=dropout,
            aggregator_type=aggregator_type,
        ))

        # 中间层
        for i in range(1, len(hidden_dims) - 1):
            layers.append(GraphSAGE(
                input_dim=hidden_dims[i],
                hidden_dim=hidden_dims[i],
                output_dim=hidden_dims[i + 1] if i + 1 < len(hidden_dims) else output_dim,
                dropout=dropout,
                aggregator_type=aggregator_type,
            ))

        self.layers = torch.nn.ModuleList(layers)

    def forward(self, graph: SynapseGraph) -> torch.Tensor:
        if graph.num_nodes == 0:
            return torch.empty(0, self.output_dim, device=graph.node_features.device)
        h = graph.node_features
        for layer in self.layers:
            h = layer(h, graph.edge_index, None)
        return h


# ═══════════════════════════════════════════════════
# 单测
# ═══════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 测试 SynapseGraphBuilder
    builder = SynapseGraphBuilder()
    graph = builder.load_from_database(top_k=50)
    print(f"图节点数: {graph.num_nodes}, 边数: {graph.num_edges}, 特征维: {graph.node_features.shape}")

    if graph.num_nodes > 0:
        # 测试 GAT 编码器
        gat = SynapseGATEncoder(input_dim=64, output_dim=16, num_heads=2, num_layers=1)
        with torch.no_grad():
            emb = gat(graph)
        print(f"GAT 嵌入: {emb.shape}")

        # 测试 SAGE 编码器
        sage = SynapseGraphSAGEEncoder(input_dim=64, output_dim=16)
        with torch.no_grad():
            emb2 = sage(graph)
        print(f"GraphSAGE 嵌入: {emb2.shape}")

    print("✅ gnn_graph_builder 测试通过")
