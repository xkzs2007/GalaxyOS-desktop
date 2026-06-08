#!/usr/bin/env python3
"""
神经记忆管道 — 全链路集成

将 GNN 表示学习 + CfC 动态权重 + NCP 拓扑 + 突触网络持久化整合为单一管道。

数据流:
  JSONL 存储层
    ↓ neurons, synapses
  SynapseGraphBuilder
    ↓ SynapseGraph (node_features, edge_index)
  GNN Encoder (GAT / GraphSAGE)
    ↓ neuron_embeddings (N, output_dim)
  CfCSynapseEngine (用 GNN embedding 代替随机状态)
    ↓ CfC 动态权重
  activate_and_propagate
    ↓ 关联记忆列表
  返回给检索调用方

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-05
"""

import json
import os
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

import torch
import numpy as np

logger = logging.getLogger("neural_pipeline")

# 延迟导入（避免循环依赖）
_GNN_BUILDER = None
_CFC_ENGINE = None
_SYNAPSE_NETWORK = None
_LTC_SYNAPSE = None


def _import_deps():
    global _GNN_BUILDER, _CFC_ENGINE, _SYNAPSE_NETWORK, _LTC_SYNAPSE
    if _GNN_BUILDER is None:
        from gnn_graph_builder import SynapseGraphBuilder, SynapseGATEncoder, \
            SynapseGraphSAGEEncoder, SynapseGraph
        _GNN_BUILDER = (SynapseGraphBuilder, SynapseGATEncoder,
                        SynapseGraphSAGEEncoder, SynapseGraph)
    if _CFC_ENGINE is None:
        from cfc_inference import CfCSynapseEngine, NCPTopology, NeuronRole
        _CFC_ENGINE = (CfCSynapseEngine, NCPTopology, NeuronRole)
    if _SYNAPSE_NETWORK is None:
        from memory_synapse_network import MemorySynapseNetwork
        _SYNAPSE_NETWORK = MemorySynapseNetwork
    if _LTC_SYNAPSE is None:
        from ltc_synapse import PRESETS, evaluate_preset
        _LTC_SYNAPSE = (PRESETS, evaluate_preset)


@dataclass
class PipelineResult:
    """管道输出的结果"""
    __slots__ = (
        "activated_neurons", "neuron_labels", "pipeline_time_ms",
        "gnn_time_ms", "cfc_time_ms", "num_neurons", "num_synapses",
        "embedding_dim", "gnn_model", "predicted_ids",
        "attention_weights",
    )
    def __init__(
        self,
        activated_neurons=None,
        neuron_labels=None,
        pipeline_time_ms: float = 0.0,
        gnn_time_ms: float = 0.0,
        cfc_time_ms: float = 0.0,
        num_neurons: int = 0,
        num_synapses: int = 0,
        embedding_dim: int = 0,
        gnn_model: str = "",
        predicted_ids=None,
        attention_weights=None,
    ):
        self.activated_neurons = activated_neurons or []
        self.neuron_labels = neuron_labels or {}
        self.pipeline_time_ms = pipeline_time_ms
        self.gnn_time_ms = gnn_time_ms
        self.cfc_time_ms = cfc_time_ms
        self.num_neurons = num_neurons
        self.num_synapses = num_synapses
        self.embedding_dim = embedding_dim
        self.gnn_model = gnn_model
        self.predicted_ids = predicted_ids or []
        self.attention_weights = attention_weights or {}


class NeuralMemoryPipeline:
    """
    神经记忆管道

    用法:
        pipeline = NeuralMemoryPipeline()
        pipeline.initialize()

        # 激活传播
        result = pipeline.activate("neuron_id_xxx", top_k=5)

        # 批量优化
        pipeline.optimize(epochs=100)
    """

    def __init__(
        self,
        workspace_path: str = None,
        gnn_type: str = "gat",  # "gat" 或 "graphsage"
        feature_dim: int = 64,
        hidden_dim: int = 64,
        gnn_heads: int = 4,
        gnn_layers: int = 2,
        cfc_hidden_size: int = 16,
        device: str = "cpu",
        aggregator_type: str = "lstm",  # "mean" | "lstm" | "pool" (仅 graphsage)
        use_database: bool = True,  # True=从记忆库加载，False=从 JSONL
    ):
        _import_deps()
        SynapseGraphBuilder_cls, _, _, _ = _GNN_BUILDER
        CfCSynapseEngine_cls, NCPTopology_cls, _ = _CFC_ENGINE

        self.workspace_path = workspace_path or os.path.expanduser(
            "~/.openclaw/workspace"
        )
        self.gnn_type = gnn_type.lower()
        self.feature_dim = feature_dim
        self.hidden_dim = hidden_dim
        self.gnn_heads = gnn_heads
        self.gnn_layers = gnn_layers
        self.cfc_hidden_size = cfc_hidden_size
        self.device = torch.device(device)
        self._aggregator_type = aggregator_type

        # 子组件（延迟初始化）
        self.graph_builder = SynapseGraphBuilder_cls(
            workspace_path=workspace_path,
            feature_dim=feature_dim,
            device=device,
        )
        self.gnn_encoder: Optional[torch.nn.Module] = None
        self.cfc_engine: Optional[CfCSynapseEngine_cls] = None
        self.topo: Optional[NCPTopology_cls] = None
        self.graph = None
        self.use_database = use_database
        self.sequence_predictor = None  # CfC 序列预测器（懒加载）

        # 缓存（避免重复读 JSONL）
        self._cached_neurons: List[dict] = []
        self._cached_synapses: List[dict] = []
        self._initialized = False

        # 缓存神经元的 embedding（由 GNN 产出）
        self._neuron_embeddings: Optional[torch.Tensor] = None
        self._embedding_id_to_idx: Dict[str, int] = {}

    def initialize(self):
        """
        初始化管道

        1. 从 JSONL 加载神经元和突触
        2. 构建 SynapseGraph
        3. 分配 NCP 拓扑
        4. 初始化 GNN 编码器
        5. 初始化 CfC 引擎
        6. 运行 GNN 前向获取 embedding
        """
        if self._initialized:
            return

        CfCSynapseEngine_cls, NCPTopology_cls, _ = _CFC_ENGINE

        # 1. 加载数据（优先从记忆库，降级到 JSONL）
        if self.use_database:
            self.graph = self.graph_builder.load_from_database(top_k=300)
            self._cached_neurons = []
            self._cached_synapses = []
            if self.graph and self.graph.num_nodes > 0:
                # 从 graph 还原 neurons 格式
                self._cached_neurons = [
                    {"id": nid, "content": label}
                    for nid, label in zip(self.graph.node_ids, self.graph.node_labels)
                ]
                self._cached_synapses = []
                if self.graph.edge_index.shape[1] > 0:
                    src = self.graph.edge_index[0].tolist()
                    dst = self.graph.edge_index[1].tolist()
                    weights = self.graph.edge_attr.tolist() if self.graph.edge_attr is not None else [0.5]*len(src)
                    for i in range(len(src)):
                        self._cached_synapses.append({
                            'id': f'SYN-IMP-{i}',
                            'source_id': self.graph.node_ids[src[i]],
                            'target_id': self.graph.node_ids[dst[i]],
                            'weight': weights[i] if isinstance(weights, list) else weights,
                        })
        else:
            self._cached_neurons = self.graph_builder._load_neurons()
            self._cached_synapses = self.graph_builder._load_synapses()
            if self._cached_neurons:
                self.graph = self.graph_builder.load_graph_from_memory(
                    self._cached_neurons, self._cached_synapses
                )

        if not self._cached_neurons and (not self.graph or self.graph.num_nodes == 0):
            logger.warning("没有神经元数据，管道进入空状态")
            self._initialized = True
            return

        # 2. 构建图（从数据库加载时 graph 已有，但没 cached_neurons 时从 graph 构建）
        if not self._cached_neurons and self.graph and self.graph.num_nodes > 0:
            self._cached_neurons = [
                {"id": nid, "content": label}
                for nid, label in zip(self.graph.node_ids, self.graph.node_labels)
            ]
            self._cached_synapses = self._cached_synapses or []

        # 3. 分配 NCP 拓扑（用图度数而非随机激活次数）
        self.topo = NCPTopology_cls()
        self.topo.assign_roles(self._cached_neurons, self._cached_synapses)
        logger.info(
            f"NCP 拓扑分配完成: "
            f"sensory={self.topo._role_counts.get('sensory', 0)}, "
            f"inter={self.topo._role_counts.get('inter', 0)}, "
            f"command={self.topo._role_counts.get('command', 0)}, "
            f"motor={self.topo._role_counts.get('motor', 0)}"
        )

        # 4. 初始化 GNN 编码器
        if self.gnn_type == "gat":
            from gnn_graph_builder import SynapseGATEncoder
            self.gnn_encoder = SynapseGATEncoder(
                input_dim=self.feature_dim,
                hidden_dim=self.hidden_dim,
                output_dim=self.cfc_hidden_size,
                num_heads=self.gnn_heads,
                num_layers=self.gnn_layers,
                dropout=0.3,
            )
        elif self.gnn_type == "graphsage":
            from gnn_graph_builder import SynapseGraphSAGEEncoder
            self.gnn_encoder = SynapseGraphSAGEEncoder(
                input_dim=self.feature_dim,
                hidden_dims=[self.hidden_dim * 2, self.hidden_dim],
                output_dim=self.cfc_hidden_size,
                dropout=0.3,
                aggregator_type=self._aggregator_type,
            )
        else:
            raise ValueError(f"未知 GNN 类型: {self.gnn_type}")

        self.gnn_encoder.to(self.device)
        self.gnn_encoder.eval()
        logger.info(f"GNN 编码器初始化完成 ({self.gnn_type})")

        # 5. 初始化 CfC 引擎
        self.cfc_engine = CfCSynapseEngine_cls(
            embedding_dim=self.feature_dim,
            hidden_size=self.cfc_hidden_size,
            backbone_units=0,
            use_embedding_proj=True,
            use_wired_cfc=False,  # 大图用简单 CfCCell，避免 WiredCfCCell 的维度对齐问题
        )
        self.cfc_engine.to(self.device)
        self.cfc_engine.eval()
        self.cfc_engine.set_topology(self.topo)
        self.cfc_engine.load_synapses(self._cached_synapses)
        logger.info("CfC 引擎初始化完成")

        # ── 6a. 初始化 CfC 序列预测器 ──
        if not self.sequence_predictor:
            try:
                from cfc_sequence_predictor import CfCSequencePredictor
                self.sequence_predictor = CfCSequencePredictor(
                    input_dim=self.cfc_hidden_size,
                    hidden_dim=self.cfc_hidden_size * 2,
                    seq_len=5,
                    use_autoncp=True,
                    autoncp_sparsity=0.5,
                    device=str(self.device),
                )
                self.sequence_predictor.eval()
                self.sequence_predictor.to(self.device)
                logger.info("CfC 序列预测器初始化完成")
            except Exception as e:
                logger.debug(f"序列预测器初始化跳过: {e}")

        # 6. 运行 GNN 获取 embedding
        self._run_gnn_embedding()

        self._initialized = True

    def _run_gnn_embedding(self):
        """运行 GNN 编码器，将 embedding 注入 CfC 引擎的状态管理器"""
        self._embedding_id_to_idx = self.graph.id_to_idx
        with torch.no_grad():
            embeddings = self.gnn_encoder(self.graph)
        self._neuron_embeddings = embeddings

        # 注入 CfC 状态管理器
        node_ids = self.graph.node_ids
        for i, nid in enumerate(node_ids):
            self.cfc_engine.state_manager.states[nid] = embeddings[i].clone()

        logger.info(
            f"GNN embedding 注入完成: "
            f"{len(node_ids)} 个神经元, dim={embeddings.size(1)}"
        )

    def activate(
        self,
        neuron_id: str,
        top_k: int = 5,
        max_depth: int = 3,
        activation_strength: float = 0.2,
    ) -> PipelineResult:
        """
        激活传播（主入口，含 CfC 序列预测）

        Args:
            neuron_id: 起始神经元 ID
            top_k: 返回 top K 个关联
            max_depth: 最大传播深度
            activation_strength: 激活强度阈值

        Returns:
            PipelineResult (含 predicted_ids 字段)
        """
        import time

        if not self._initialized:
            self.initialize()

        if not self._cached_neurons:
            return PipelineResult(
                activated_neurons=[],
                neuron_labels={},
                pipeline_time_ms=0,
                gnn_time_ms=0,
                cfc_time_ms=0,
                num_neurons=0,
                num_synapses=0,
                embedding_dim=self.cfc_hidden_size,
                gnn_model=self.gnn_type,
            )

        # 记录时间
        t0 = time.time()

        # GNN embedding（如果缓存过期，重新运行）
        if self._neuron_embeddings is None:
            self._run_gnn_embedding()
        t1 = time.time()

        # CfC 激活传播
        results = self.cfc_engine.activate_and_propagate(
            seed_neuron_id=neuron_id,
            synapses=self._cached_synapses,
            top_k=top_k,
            max_depth=max_depth,
            activation_strength=activation_strength,
        )
        t2 = time.time()

        # ── CfC 序列预测器：记录激活事件 + 预测下一个 ──
        predicted_ids = []
        if self.sequence_predictor is not None:
            try:
                # 从当前 neuron embedding 记录激活事件
                _idx = self.graph.id_to_idx.get(neuron_id)
                if _idx is not None and self._neuron_embeddings is not None:
                    _emb = self._neuron_embeddings[_idx].tolist()
                    self.sequence_predictor.history.record(
                        memory_id=neuron_id,
                        embedding=_emb,
                        strength=1.0,
                    )
                    # 记录关联结果（较弱强度）
                    for _aid, _st in results:
                        _aidx = self.graph.id_to_idx.get(_aid)
                        if _aidx is not None and self._neuron_embeddings is not None:
                            _aemb = self._neuron_embeddings[_aidx].tolist()
                            self.sequence_predictor.history.record(
                                memory_id=_aid,
                                embedding=_aemb,
                                strength=_st,
                            )
                # 运行序列预测
                _pred = self.sequence_predictor.predict_next(
                    seq_len=min(5, 2 if len(results) < 2 else 5)
                )
                if _pred and len(_pred) > 1 and _pred[1]:
                    for _pid in _pred[1]:
                        if _pid not in predicted_ids:
                            predicted_ids.append(_pid)
            except Exception as e:
                logger.debug(f"sequence predict skipped: {e}")

        t3 = time.time()

        # 构造标签映射
        labels = {}
        for n in self._cached_neurons:
            labels[n["id"]] = n.get("content", "")[:30]

        # 更新突触网络的最后使用时间
        self._update_reinforcement(neuron_id, [r[0] for r in results])

        # ── GAT 注意力权重收集 ──
        _attn_weights = {}
        try:
            if self.gnn_type == "gat" and hasattr(self.gnn_encoder, 'gat'):
                _gat_model = self.gnn_encoder.gat
                if self._neuron_embeddings is not None and self.graph.num_nodes > 0:
                    _n = self._neuron_embeddings.size(0)
                    _adj = self._build_adj_from_synapses(_n)
                    _attn_mat = _gat_model.get_attention_weights(
                        self._neuron_embeddings, _adj, layer_idx=0
                    )
                    if _attn_mat is not None and _attn_mat.size(0) == _n:
                        # 每个神经元的"结构重要性" = 各节点对它的注意力之和
                        _incoming = _attn_mat.sum(dim=0).cpu().tolist()
                        for _idx, _w in enumerate(_incoming):
                            _nid = self.graph.node_ids[_idx] if _idx < len(self.graph.node_ids) else ""
                            if _nid:
                                _attn_weights[_nid] = float(_w)
        except Exception:
            pass

        return PipelineResult(
            activated_neurons=results,
            neuron_labels=labels,
            pipeline_time_ms=(t3 - t0) * 1000,
            gnn_time_ms=(t1 - t0) * 1000,
            cfc_time_ms=(t2 - t1) * 1000,
            num_neurons=self.graph.num_nodes,
            num_synapses=self.graph.num_edges,
            embedding_dim=self.cfc_hidden_size,
            gnn_model=self.gnn_type,
            predicted_ids=predicted_ids,
            attention_weights=_attn_weights,
        )

    def _update_reinforcement(self, seed_id: str, activated_ids: List[str]):
        """更新突触强化时间戳（反馈到 JSONL 持久化）"""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        for s in self._cached_synapses:
            if s["source_id"] == seed_id and s["target_id"] in activated_ids:
                s["last_reinforced"] = now
                s["reinforcement_count"] = s.get("reinforcement_count", 0) + 1

    def _build_adj_from_synapses(self, n_nodes: int) -> torch.Tensor:
        """从缓存的突触列表构建邻接矩阵（含自环），用于 GAT 注意力可视化"""
        import torch
        _adj = torch.eye(n_nodes)
        for s in self._cached_synapses:
            _src = self.graph.id_to_idx.get(s.get("source_id") or s.get("source", ""))
            _dst = self.graph.id_to_idx.get(s.get("target_id") or s.get("target", ""))
            if _src is not None and _dst is not None and _src < n_nodes and _dst < n_nodes:
                _adj[_src, _dst] = 1.0
        return _adj

    def batch_optimize_ltc(self, epochs: int = 100):
        """
        批量优化 LTC 参数

        使用 GNN embedding + 实际使用数据训练 CfCCell 的权重矩阵，
        使得 CfC 能产生更准确的权重预测。
        """
        if not self._initialized:
            self.initialize()

        if not self._cached_synapses:
            logger.warning("没有突触数据，跳过优化")
            return 0

        # 准备训练数据
        from ltc_synapse import LTCBatchOptimizer
        from datetime import datetime, timezone

        training_data = []
        for s in self._cached_synapses:
            src_id = s["source_id"]
            dst_id = s["target_id"]
            days = 0.0
            if s.get("last_reinforced"):
                try:
                    t = datetime.fromisoformat(s["last_reinforced"])
                    days = (datetime.now(timezone.utc) - t).total_seconds() / 86400.0
                except Exception:
                    pass

            training_data.append({
                "days": days,
                "recent_uses": s.get("reinforcement_count", 0),
                "total_uses": s.get("reinforcement_count", 0),
                "current_weight": s.get("weight", 0.5),
            })

        if not training_data:
            return 0

        # 使用 PyTorch 优化器训练 CfCCell
        optimizer = torch.optim.Adam(self.cfc_engine.cfc_cell.parameters(), lr=0.01)
        num_synapses = len(training_data)

        device = self.device
        days_t = torch.tensor(
            [d["days"] for d in training_data], dtype=torch.float32, device=device
        ).unsqueeze(1)
        target_w = torch.tensor(
            [d["current_weight"] for d in training_data],
            dtype=torch.float32, device=device
        )

        # GNN embedding 作为输入
        for _ in range(epochs):
            optimizer.zero_grad()

            # 从突触对构造 CfCCell 输入
            src_states = []
            dst_states = []
            for s in self._cached_synapses:
                src_id = s["source_id"]
                dst_id = s["target_id"]
                nid_to_idx = self.graph.id_to_idx

                src_idx = nid_to_idx.get(src_id)
                dst_idx = nid_to_idx.get(dst_id)
                if src_idx is None or dst_idx is None:
                    continue
                src_states.append(self._neuron_embeddings[src_idx])
                dst_states.append(self._neuron_embeddings[dst_idx])

            if not src_states:
                break

            src = torch.stack(src_states)
            dst = torch.stack(dst_states)

            # CfC 输入
            inputs = torch.cat([src, dst, days_t[:len(src)]], dim=1)
            # 修正 days 维度匹配
            actual_days = days_t[:len(src)]
            hx = torch.zeros(len(src), 1, device=device)

            h_out, _ = self.cfc_engine.cfc_cell.forward(inputs, hx, ts=actual_days)
            pred_w = torch.sigmoid(h_out.squeeze(-1))

            loss = torch.nn.MSELoss()(pred_w, target_w[:len(src)])
            loss.backward()
            optimizer.step()

        self.gnn_time_ms = 0
        logger.info(
            f"CfCCell 参数优化完成: {num_synapses} 条突触, {epochs} epochs"
        )
        return num_synapses

    def get_stats(self) -> dict:
        """获取管道统计"""
        if not self._initialized:
            self.initialize()

        stats = {
            "num_neurons": len(self._cached_neurons),
            "num_synapses": len(self._cached_synapses),
            "gnn_type": self.gnn_type,
            "embedding_dim": self.cfc_hidden_size,
            "initialized": self._initialized,
        }

        if self.topo:
            stats["topology"] = self.topo._role_counts

        if self._neuron_embeddings is not None:
            stats["embedding_stats"] = {
                "mean": float(self._neuron_embeddings.mean()),
                "std": float(self._neuron_embeddings.std()),
                "min": float(self._neuron_embeddings.min()),
                "max": float(self._neuron_embeddings.max()),
            }

        return stats


# ==================== CLI 入口 ====================

def main():
    """快速测试 CLI"""
    import argparse

    parser = argparse.ArgumentParser(description="神经记忆管道")
    parser.add_argument("command", choices=["stats", "activate", "optimize", "demo"])
    parser.add_argument("--neuron", help="起始神经元 ID")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=100)

    args = parser.parse_args()
    pipeline = NeuralMemoryPipeline(gnn_type="gat")

    if args.command == "stats":
        stats = pipeline.get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    elif args.command == "activate":
        if not args.neuron:
            print("错误: 需要 --neuron")
            return
        result = pipeline.activate(args.neuron, top_k=args.top_k)
        print(f"激活传播结果 (耗时 {result.pipeline_time_ms:.1f}ms):")
        for nid, strength in result.activated_neurons:
            label = result.neuron_labels.get(nid, nid)
            print(f"  {strength:.4f}  {label}")

    elif args.command == "optimize":
        count = pipeline.batch_optimize_ltc(epochs=args.epochs)
        print(f"优化完成: {count} 条突触")

    elif args.command == "demo":
        print("=" * 55)
        print("神经记忆管道 — 快速演示")
        print("=" * 55)

        # 先创建一些测试数据
        from memory_synapse_network import MemorySynapseNetwork
        net = MemorySynapseNetwork()

        names = [
            "Python 入门", "变量和类型", "函数和作用域",
            "闭包", "装饰器", "生成器", "异步编程",
        ]
        neurons = [net.create_neuron(name) for name in names]

        pairs = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6),
                 (0, 3), (2, 5)]
        synapses = []
        for s, t in pairs:
            synapses.append(net.create_synapse(neurons[s].id, neurons[t].id))

        print(f"创建 {len(neurons)} 个神经元, {len(synapses)} 条突触")

        # 初始化管道
        print()
        print("初始化管道...")
        pipeline.initialize()
        stats = pipeline.get_stats()
        print(f"  节点: {stats['num_neurons']}, 边: {stats['num_synapses']}")
        print(f"  拓扑: {stats.get('topology', {})}")
        if "embedding_stats" in stats:
            es = stats["embedding_stats"]
            print(f"  Embedding: 均值={es['mean']:.4f}, 标准差={es['std']:.4f}")

        # 激活传播
        print()
        print("激活传播 (从 'Python 入门'):")
        result = pipeline.activate(neurons[0].id, top_k=5)
        print(f"  耗时: GNN {result.gnn_time_ms:.1f}ms + "
              f"CfC {result.cfc_time_ms:.1f}ms = "
              f"总计 {result.pipeline_time_ms:.1f}ms")
        for nid, strength in result.activated_neurons:
            label = result.neuron_labels.get(nid, nid)
            print(f"  {strength:.4f}  {label}")

        # 对比：原始 LTC 预设
        print()
        print("对比: 无拓扑 BFS（传统模式）:")
        from memory_synapse_network import ActivationSpreader
        spreader = ActivationSpreader(net.network)
        associated = spreader.find_associated_memories(neurons[0].id, top_k=5)
        for neuron, strength in associated:
            print(f"  {strength:.4f}  {neuron.content}")

        print()
        print("✅ 演示完成")


if __name__ == "__main__":
    main()
