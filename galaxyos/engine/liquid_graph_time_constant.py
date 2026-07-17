#!/usr/bin/env python3
"""
LGTC — Liquid-Graph Time-Constant Network

融合 GraphSAGE 风格的图神经网络与 LTC 连续时间图控制。
论文理念：
  - 图消息传递使用 liquid time-constant 做时序融合
  - 节点特征通过 LTC 微分方程随时间演化
  - 时间常数 τ 由节点状态和图邻域共同决定

在 GalaxyOS 中的角色：
  - 对图结构数据做时序预测（动态图）
  - 替代传统 GNN + RNN 组合，用 LTC 统一时序和图建模

核心公式：
  1. 消息汇集：m_v(t) = AGGREGATE({h_u(t) | u ∈ N(v)})
  2. 节点更新：dh_v/dt = σ(W_h h_v + W_m m_v + b) * (E - h_v) / τ_v
  3. 时间常数：τ_v = σ(W_τ h_v + W_τm m_v + b_τ) * (τ_max - τ_min) + τ_min

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-14
"""

import os
import math
import time
import json
import logging
from typing import Dict, List, Optional, Tuple, Any, Callable, Union
from dataclasses import dataclass, field

logger = logging.getLogger("lgtc")

import numpy as np

# ==================== 图结构 ====================

@dataclass
class GraphData:
    """图数据结构"""
    num_nodes: int
    node_features: np.ndarray          # [num_nodes, feature_dim]
    adjacency_list: List[List[int]]    # 邻接表
    edge_weights: Optional[np.ndarray] = None  # [num_edges] 可选边权重
    node_types: Optional[List[int]] = None     # 可选节点类型

    @property
    def feature_dim(self) -> int:
        return self.node_features.shape[-1]


class GraphNeighborSampler:
    """图邻居采样器

    为每个节点从邻接表中采样固定数量的邻居。
    """

    @staticmethod
    def sample(adj_list: List[List[int]],
               num_samples: int = 10) -> Tuple[np.ndarray, np.ndarray]:
        """采样邻居

        Args:
            adj_list: 邻接表
            num_samples: 需要采样的邻居数量

        Returns:
            sampled_indices: [num_nodes, num_samples] 采样后的邻居索引
            neighbor_masks: [num_nodes, num_samples] 有效邻居掩码 (0/1)
        """
        num_nodes = len(adj_list)
        dtype = np.int64

        if num_samples == 0:
            return np.zeros((num_nodes, 0), dtype=dtype), np.ones((num_nodes, 0), dtype=np.float32)

        sampled = np.zeros((num_nodes, num_samples), dtype=dtype)
        masks = np.zeros((num_nodes, num_samples), dtype=np.float32)

        for i in range(num_nodes):
            nbrs = adj_list[i] if i < len(adj_list) else []

            if len(nbrs) == 0:
                samples = [i] * num_samples
            elif len(nbrs) >= num_samples:
                idx = np.random.choice(len(nbrs), num_samples, replace=False)
                samples = [nbrs[j] for j in idx]
            else:
                n_repeat = num_samples // len(nbrs)
                n_extra = num_samples % len(nbrs)
                samples_base = nbrs * n_repeat
                if n_extra > 0:
                    extra_idx = np.random.choice(len(nbrs), n_extra, replace=False)
                    samples_base += [nbrs[j] for j in extra_idx]
                samples = samples_base[:num_samples]

            for j, idx_val in enumerate(samples):
                sampled[i, j] = idx_val
            masks[i, :len(samples)] = 1.0

        return sampled, masks


# ==================== 均值聚合器 ====================

class MeanAggregator:
    """均值聚合器 — 对邻居特征求均值"""

    def __init__(self, in_dim: int, out_dim: int):
        limit = math.sqrt(6 / (in_dim + out_dim))
        self.w = np.random.uniform(-limit, limit, (out_dim, in_dim)).astype(np.float32)
        self.b = np.zeros(out_dim, dtype=np.float32)

    def forward(self, features: np.ndarray,
                neighbor_idx: np.ndarray,
                neighbor_mask: np.ndarray) -> np.ndarray:
        n_nodes, n_samples = neighbor_idx.shape
        nbr_feats = features[neighbor_idx]
        mask_exp = neighbor_mask[..., np.newaxis]
        nbr_feats = nbr_feats * mask_exp
        valid_counts = np.maximum(neighbor_mask.sum(axis=1, keepdims=True), 1.0)
        agg = nbr_feats.sum(axis=1) / valid_counts
        return agg @ self.w.T + self.b


# ==================== 注意力聚合器 ====================

class AttentionAggregator:
    """注意力聚合器 — 用多头注意力加权邻居"""

    def __init__(self, in_dim: int, out_dim: int, n_heads: int = 4):
        head_dim = out_dim // n_heads
        self.head_dim = head_dim
        self.n_heads = n_heads
        self.out_dim = out_dim

        limit = math.sqrt(6 / (in_dim + out_dim))
        self.w = np.random.uniform(-limit, limit, (out_dim, in_dim)).astype(np.float32)
        # 每头的注意力向量
        self.a = np.random.uniform(-0.2, 0.2, (n_heads, 2 * head_dim)).astype(np.float32)

    def forward(self, features: np.ndarray,
                neighbor_idx: np.ndarray,
                neighbor_mask: np.ndarray) -> np.ndarray:
        n_nodes, n_samples = neighbor_idx.shape

        # 投影到输出空间
        proj = features @ self.w.T  # [n_nodes, out_dim]
        # 每头拆分
        proj_heads = proj.reshape(n_nodes, self.n_heads, self.head_dim)  # [n_nodes, n_heads, head_dim]
        nbr_heads = proj[neighbor_idx].reshape(n_nodes, n_samples, self.n_heads, self.head_dim)
        # [n_nodes, n_samples, n_heads, head_dim]

        # 拼接自身和邻居（每头独立）
        self_heads = proj_heads[:, np.newaxis, :, :]  # [n_nodes, 1, n_heads, head_dim]
        self_exp = np.repeat(self_heads, n_samples, axis=1)  # [n_nodes, n_samples, n_heads, head_dim]
        cat_feats = np.concatenate([self_exp, nbr_heads], axis=-1)  # [n_nodes, n_samples, n_heads, 2*head_dim]

        # 每头独立算注意力
        # cat_feats: [n_nodes, n_samples, n_heads, 2*head_dim]
        # self.a:    [n_heads, 2*head_dim]
        scores = np.einsum('nshd,hd->nsh', cat_feats, self.a)  # [n_nodes, n_samples, n_heads]
        scores = np.where(scores > 0, scores, scores * 0.2)  # LeakyReLU

        # Softmax 带掩码
        mask_exp = neighbor_mask[:, :, np.newaxis]  # [n_nodes, n_samples, 1]
        scores = scores * mask_exp + (-1e9) * (1 - mask_exp)
        scores = scores - scores.max(axis=1, keepdims=True)
        exp_scores = np.exp(scores) * mask_exp
        attn = exp_scores / (exp_scores.sum(axis=1, keepdims=True) + 1e-8)  # [n_nodes, n_samples, n_heads]

        # 多头加权求和
        weighted = (nbr_heads * attn[:, :, :, np.newaxis]).sum(axis=1)  # [n_nodes, n_heads, head_dim]
        # 拼接所有头
        out = weighted.reshape(n_nodes, self.out_dim)
        return out


# ==================== LGTC 层 ====================

class LiquidGraphLayer:
    """
    LGTC 单层

    设计：输入 [num_nodes, dim_in] → 投影到隐藏空间 → 图消息传递 →
          LTC 微分更新 → 投影回输出空间 [num_nodes, dim_out]

    所有操作在统一维度上进行，避免 broadcast 不匹配。
    """

    def __init__(self, dim_in: int, dim_out: int,
                 dim_hidden: Optional[int] = None,
                 tau_min: float = 0.1, tau_max: float = 10.0,
                 aggregator: str = "mean",
                 n_heads: int = 4):
        self.dim_in = dim_in
        self.dim_out = dim_out
        d = dim_hidden or max(dim_in, dim_out)
        self.dim_hidden = d
        self.tau_min = tau_min
        self.tau_max = tau_max

        # 输入投影
        self.w_in = np.random.randn(d, dim_in).astype(np.float32) * 0.01

        # 聚合器（在隐藏空间操作）
        if aggregator == "mean":
            self.aggregator = MeanAggregator(d, d)
        elif aggregator == "attention":
            self.aggregator = AttentionAggregator(d, d, n_heads=n_heads)
        else:
            raise ValueError(f"未知聚合器: {aggregator}")

        # LTC 在隐藏空间
        self.w_h = np.random.randn(d, d).astype(np.float32) * 0.01
        self.w_m = np.random.randn(d, d).astype(np.float32) * 0.01
        self.b = np.zeros(d, dtype=np.float32)

        self.w_tau_h = np.random.randn(1, d).astype(np.float32) * 0.01
        self.w_tau_m = np.random.randn(1, d).astype(np.float32) * 0.01
        self.b_tau = np.zeros(1, dtype=np.float32)

        self.E = np.ones(d, dtype=np.float32)

        # 输出投影
        self.w_out = np.random.randn(dim_out, d).astype(np.float32) * 0.01

    @staticmethod
    def _sigmoid(x):
        pos = x >= 0
        result = np.zeros_like(x, dtype=np.float64)
        result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
        result[~pos] = np.exp(x[~pos]) / (1.0 + np.exp(x[~pos]))
        return result

    def forward(self, features: np.ndarray,
                neighbor_idx: np.ndarray,
                neighbor_mask: np.ndarray) -> np.ndarray:
        """一步更新

        Args:
            features: 节点特征 [num_nodes, dim_in]
            neighbor_idx: 邻居索引 [num_nodes, num_samples]
            neighbor_mask: 邻居掩码 [num_nodes, num_samples]

        Returns:
            新特征 [num_nodes, dim_out]
        """
        # 投影到隐藏空间
        h_raw = features @ self.w_in.T  # [num_nodes, dim_hidden]

        # 图消息汇集
        msg = self.aggregator.forward(h_raw, neighbor_idx, neighbor_mask)

        # LTC 微分：dh/dt = σ(W_h h + W_m msg + b) * (E - h) / τ
        drive = h_raw @ self.w_h.T + msg @ self.w_m.T + self.b
        gate = self._sigmoid(drive)

        tau_raw = h_raw @ self.w_tau_h.T + msg @ self.w_tau_m.T + self.b_tau
        tau = self._sigmoid(tau_raw) * (self.tau_max - self.tau_min) + self.tau_min

        dh = gate * (self.E[np.newaxis, :] - h_raw) / tau

        # 欧拉积分 (dt=1.0)
        h_new = h_raw + dh

        # 残差连接
        h_out = h_raw + h_new * 0.1

        # 输出投影
        return h_out @ self.w_out.T

    def get_params(self) -> dict:
        return {
            "dim_in": self.dim_in,
            "dim_hidden": self.dim_hidden,
            "dim_out": self.dim_out,
            "tau_range": [self.tau_min, self.tau_max],
        }


# ==================== LGTC 网络 ====================

class LiquidGraphNetwork:
    """
    LGTC 网络 — 多层 LGTC 堆叠
    """

    def __init__(self, feature_dim: int,
                 hidden_dims: List[int],
                 tau_min: float = 0.1,
                 tau_max: float = 10.0,
                 aggregator: str = "mean",
                 n_heads: int = 4,
                 output_dim: Optional[int] = None):
        self.feature_dim = feature_dim
        self.output_dim = output_dim or feature_dim

        dims = [feature_dim] + hidden_dims + [self.output_dim]
        self.layers: List[LiquidGraphLayer] = []
        for i in range(len(dims) - 1):
            layer = LiquidGraphLayer(
                dim_in=dims[i],
                dim_out=dims[i + 1],
                dim_hidden=dims[i + 1],
                tau_min=tau_min,
                tau_max=tau_max,
                aggregator=aggregator if i == len(dims) - 2 else "mean",
                n_heads=n_heads,
            )
            self.layers.append(layer)

        self.num_layers = len(self.layers)

    def forward(self, features: np.ndarray,
                adj_list: List[List[int]],
                num_samples: int = 10) -> np.ndarray:
        sampler = GraphNeighborSampler()
        nbr_idx, nbr_mask = sampler.sample(adj_list, num_samples)

        h = features
        for layer in self.layers:
            h = layer.forward(h, nbr_idx, nbr_mask)
        return h

    def simulate_sequence(self, graph: GraphData,
                          num_steps: int = 10,
                          dt: float = 0.1,
                          num_samples: int = 10,
                          external_inputs: Optional[np.ndarray] = None) -> np.ndarray:
        sampler = GraphNeighborSampler()
        nbr_idx, nbr_mask = sampler.sample(graph.adjacency_list, num_samples)

        h = graph.node_features.copy()
        h_seq = [h.copy()]

        for t in range(num_steps):
            h_new = h
            for layer in self.layers:
                h_new = layer.forward(h_new, nbr_idx, nbr_mask)
            if external_inputs is not None:
                h_new = h_new + external_inputs[t] * dt
            h = h + (h_new - h) * dt
            h_seq.append(h.copy())

        return np.array(h_seq)

    def predict_node_states(self, graph: GraphData,
                            seq_len: int = 5,
                            num_samples: int = 10) -> np.ndarray:
        sampler = GraphNeighborSampler()
        nbr_idx, nbr_mask = sampler.sample(graph.adjacency_list, num_samples)

        h = graph.node_features.copy()
        preds = []
        for _ in range(seq_len):
            for layer in self.layers:
                h = layer.forward(h, nbr_idx, nbr_mask)
            preds.append(h.copy())
        return np.array(preds)

    def get_params(self) -> dict:
        return {
            "feature_dim": self.feature_dim,
            "output_dim": self.output_dim,
            "num_layers": self.num_layers,
            "layers": [l.get_params() for l in self.layers],
        }


# ==================== LTC 时间常数图仿真器 ====================

class LTCTimeGraphSimulator:
    """
    LTC 时间常数图仿真器 — 每个节点由 LTC 驱动，耦合通过图结构
    """

    def __init__(self, state_dim: int, input_dim: int,
                 tau_min: float = 0.1, tau_max: float = 10.0):
        self.state_dim = state_dim
        self.input_dim = input_dim

        self.w_h = np.random.randn(state_dim, state_dim).astype(np.float32) * 0.01
        self.w_x = np.random.randn(state_dim, input_dim).astype(np.float32) * 0.01
        self.b = np.zeros(state_dim, dtype=np.float32)

        self.w_tau_h = np.random.randn(1, state_dim).astype(np.float32) * 0.01
        self.w_tau_x = np.random.randn(1, input_dim).astype(np.float32) * 0.01
        self.b_tau = np.zeros(1, dtype=np.float32)

        self.tau_min = tau_min
        self.tau_max = tau_max
        self.E = np.ones(state_dim, dtype=np.float32)

    @staticmethod
    def _sigmoid(x):
        pos = x >= 0
        result = np.zeros_like(x, dtype=np.float64)
        result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
        result[~pos] = np.exp(x[~pos]) / (1.0 + np.exp(x[~pos]))
        return result

    def simulate(self, h0: np.ndarray, adj_list: List[List[int]],
                 x_seq: np.ndarray, dt: float = 0.1) -> np.ndarray:
        T = x_seq.shape[0]
        num_nodes = h0.shape[0]

        nbr_idx, nbr_mask = GraphNeighborSampler.sample(adj_list, min(10, num_nodes))

        h = h0.copy().astype(np.float64)
        h_seq = [h.copy()]

        for t in range(T):
            nbr_states = h[nbr_idx]
            nbr_agg = (nbr_states * nbr_mask[:, :, np.newaxis]).sum(axis=1)
            valid = np.maximum(nbr_mask.sum(axis=1, keepdims=True), 1.0)
            nbr_agg = nbr_agg / valid

            # node ODE
            drive = h @ self.w_h.T + nbr_agg + x_seq[t] @ self.w_x.T + self.b
            gate = self._sigmoid(drive)

            tau_raw = h @ self.w_tau_h.T + x_seq[t] @ self.w_tau_x.T + self.b_tau
            tau = self._sigmoid(tau_raw) * (self.tau_max - self.tau_min) + self.tau_min

            dh = gate * (self.E[np.newaxis, :] - h) / tau
            h = h + dh * dt
            h_seq.append(h.copy())

        return np.array(h_seq)


# ==================== 测试 ====================

def _build_test_graph(num_nodes: int = 20, feature_dim: int = 8,
                      neighbor_prob: float = 0.25) -> GraphData:
    np.random.seed(42)
    features = np.random.randn(num_nodes, feature_dim).astype(np.float32)
    adj_list = []
    for i in range(num_nodes):
        nbrs = [j for j in range(num_nodes) if i != j and np.random.random() < neighbor_prob]
        adj_list.append(nbrs)
    return GraphData(num_nodes=num_nodes, node_features=features, adjacency_list=adj_list)


def test_basic_layer():
    np.random.seed(42)
    feat_dim, hidden_dim, num_nodes = 8, 16, 10
    features = np.random.randn(num_nodes, feat_dim).astype(np.float32)
    adj_list = [[j for j in range(num_nodes) if j != i] for i in range(num_nodes)]
    nbr_idx, nbr_mask = GraphNeighborSampler.sample(adj_list, 5)

    layer = LiquidGraphLayer(dim_in=feat_dim, dim_out=feat_dim, dim_hidden=hidden_dim)
    out = layer.forward(features, nbr_idx, nbr_mask)
    assert out.shape == (num_nodes, feat_dim), f"输出形状错误: {out.shape}"
    print(f"✅ 基础 LGTC 层: {features.shape} → {out.shape}")
    print(f"   输出范围: [{out.min():.3f}, {out.max():.3f}]")


def test_lgtc_network():
    np.random.seed(42)
    graph = _build_test_graph(num_nodes=20, feature_dim=8)

    net = LiquidGraphNetwork(feature_dim=8, hidden_dims=[16, 8], output_dim=4)
    out = net.forward(graph.node_features, graph.adjacency_list, num_samples=5)
    assert out.shape == (20, 4), f"网络输出形状错误: {out.shape}"
    print(f"✅ LGTC 网络 (8→16→8→4): {graph.node_features.shape} → {out.shape}")
    print(f"   层数: {net.num_layers}")


def test_temporal_sequence():
    np.random.seed(42)
    graph = _build_test_graph(num_nodes=10, feature_dim=4)
    net = LiquidGraphNetwork(feature_dim=4, hidden_dims=[8], output_dim=4)
    seq = net.simulate_sequence(graph, num_steps=10, dt=0.1, num_samples=4)
    assert seq.shape == (11, 10, 4), f"序列形状错误: {seq.shape}"
    diff = np.abs(seq[-1] - seq[0]).mean()
    print(f"✅ 时序序列模拟: {seq.shape}, 平均状态变化: {diff:.4f}")
    assert diff > 0, "状态没有演化"


def test_prediction():
    np.random.seed(42)
    graph = _build_test_graph(num_nodes=10, feature_dim=4)
    net = LiquidGraphNetwork(feature_dim=4, hidden_dims=[8, 4])
    preds = net.predict_node_states(graph, seq_len=5, num_samples=4)
    assert preds.shape == (5, 10, 4), f"预测形状错误: {preds.shape}"
    print(f"✅ 节点状态预测: {preds.shape}")


def test_ltc_graph_simulator():
    np.random.seed(42)
    num_nodes, state_dim, input_dim = 10, 4, 2
    sim = LTCTimeGraphSimulator(state_dim=state_dim, input_dim=input_dim)
    h0 = np.zeros((num_nodes, state_dim))
    adj_list = [[j for j in range(num_nodes) if j != i and np.random.random() < 0.3]
                for i in range(num_nodes)]
    x_seq = np.random.randn(8, num_nodes, input_dim).astype(np.float32)
    h_seq = sim.simulate(h0, adj_list, x_seq, dt=0.1)
    assert h_seq.shape == (9, num_nodes, state_dim), f"仿真形状错误: {h_seq.shape}"
    print(f"✅ LTC 图仿真器: {h_seq.shape}, 范围 [{h_seq.min():.3f}, {h_seq.max():.3f}]")


def test_attention_aggregator():
    np.random.seed(42)
    feat_dim, num_nodes = 8, 10
    features = np.random.randn(num_nodes, feat_dim).astype(np.float32)
    adj_list = [[j for j in range(num_nodes) if j != i and np.random.random() < 0.4]
                for i in range(num_nodes)]
    nbr_idx, nbr_mask = GraphNeighborSampler.sample(adj_list, 5)
    agg = AttentionAggregator(feat_dim, feat_dim * 2, n_heads=4)
    out = agg.forward(features, nbr_idx, nbr_mask)
    assert out.shape == (num_nodes, feat_dim * 2), f"注意力输出形状错误: {out.shape}"
    print(f"✅ 注意力聚合器: {features.shape} → {out.shape}")


if __name__ == "__main__":
    print("=" * 55)
    print("LGTC — Liquid-Graph Time-Constant Network 测试")
    print("=" * 55)

    test_basic_layer()
    print()
    test_lgtc_network()
    print()
    test_temporal_sequence()
    print()
    test_prediction()
    print()
    test_ltc_graph_simulator()
    print()
    test_attention_aggregator()

    print()
    print("=" * 55)
    print("✅ LGTC 全部测试通过")
    print("=" * 55)
