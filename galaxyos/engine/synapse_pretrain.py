#!/usr/bin/env python3
"""
自监督突触对比学习 (Synapse Contrastive Pretraining)

基于 GraphCL 框架的简单对比学习模块:
  1. SynapseContrastor — 用突触图做 node-level contrastive learning
  2. 数据增强: 子图采样 + 特征掩码 + 边扰动
  3. InfoNCE loss

学到的嵌入存储为 models/synapse_pretrain.pth，供 GAT 初始化用。

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-07
"""

import json
import os
import math
import logging
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

import os as _os, sys as _sys
from galaxyos.shared.paths import workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
logger = logging.getLogger("synapse_pretrain")


# ════════════════════════════════════════════════════════════
# 数据增强
# ════════════════════════════════════════════════════════════

class GraphAugmentor:
    """
    GraphCL-style 数据增强:
      - subgraph_sample: 子图采样（保留 top-degree 节点）
      - feature_mask: 特征掩码（随机 masking 部分特征）
      - edge_perturb: 边扰动（随机添加/删除边）
    """

    def __init__(self, feature_dim: int = 64, mask_ratio: float = 0.3,
                 edge_drop_ratio: float = 0.2, subgraph_ratio: float = 0.8):
        self.feature_dim = feature_dim
        self.mask_ratio = mask_ratio
        self.edge_drop_ratio = edge_drop_ratio
        self.subgraph_ratio = subgraph_ratio

    def subgraph_sample(self, features: torch.Tensor,
                        edge_index: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """子图采样: 保留 top-(subgraph_ratio * N) 高连接度的节点"""
        n = features.size(0)
        if n <= 2:
            return features, edge_index

        # 计算节点度数
        deg = torch.zeros(n, device=features.device, dtype=torch.long)
        if edge_index.numel() > 0:
            src, dst = edge_index[0], edge_index[1]
            deg.scatter_add_(0, src, torch.ones_like(src))
            deg.scatter_add_(0, dst, torch.ones_like(dst))

        keep_n = max(2, int(n * self.subgraph_ratio))
        _, keep_idx = torch.topk(deg, keep_n)

        # 构建节点映射
        keep_set = set(keep_idx.tolist())
        old_to_new = {old.item(): new for new, old in enumerate(keep_idx)}

        # 过滤边
        new_edges = []
        if edge_index.numel() > 0:
            src, dst = edge_index[0], edge_index[1]
            for i in range(src.size(0)):
                s, d = src[i].item(), dst[i].item()
                if s in keep_set and d in keep_set and s != d:
                    new_edges.append([old_to_new[s], old_to_new[d]])

        new_features = features[keep_idx]
        new_edge_index = torch.tensor(new_edges, device=features.device,
                                      dtype=torch.long).T if new_edges else torch.empty(
            2, 0, dtype=torch.long, device=features.device)

        return new_features, new_edge_index

    def feature_mask(self, features: torch.Tensor) -> torch.Tensor:
        """特征掩码: 以 mask_ratio 概率随机将特征置零"""
        mask = torch.rand_like(features) > self.mask_ratio
        return features * mask

    def edge_perturb(self, features: torch.Tensor,
                     edge_index: torch.Tensor) -> torch.Tensor:
        """边扰动: 以 edge_drop_ratio 随机丢弃边"""
        if edge_index.numel() == 0:
            return edge_index

        e = edge_index.size(1)
        keep_mask = torch.rand(e, device=features.device) > self.edge_drop_ratio
        return edge_index[:, keep_mask]


# ════════════════════════════════════════════════════════════
# 对比学习编码器
# ════════════════════════════════════════════════════════════

class SynapseContrastor(nn.Module):
    """
    突触对比学习模块

    架构:
      GAT 编码器 → 投影头 (MLP) → 对比 loss

    用法:
      model = SynapseContrastor(input_dim=64, hidden_dim=64, output_dim=64)
      loss = model(features, edge_index)  # 自监督训练一步
      embeddings = model.encode(features, edge_index)  # 提取嵌入
    """

    def __init__(
        self,
        input_dim: int = 64,
        hidden_dim: int = 64,
        output_dim: int = 64,
        proj_dim: int = 128,
        temperature: float = 0.5,
        dropout: float = 0.3,
    ):
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.temperature = temperature

        # GAT 编码器（简易版，不依赖复杂堆叠）
        self.W = nn.Parameter(torch.empty(input_dim, hidden_dim))
        self.a_src = nn.Parameter(torch.empty(hidden_dim, 1))
        self.a_dst = nn.Parameter(torch.empty(hidden_dim, 1))

        # 输出投影
        self.W_out = nn.Linear(hidden_dim, output_dim)

        # 对比学习投影头 (MLP)
        self.projection = nn.Sequential(
            nn.Linear(output_dim, proj_dim),
            nn.BatchNorm1d(proj_dim),
            nn.ReLU(),
            nn.Linear(proj_dim, proj_dim),
        )

        # 数据增强
        self.augmentor = GraphAugmentor(feature_dim=input_dim)

        self.reset_parameters()

    def reset_parameters(self):
        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.a_src)
        nn.init.xavier_uniform_(self.a_dst)

    def _gat_encode(self, features: torch.Tensor,
                    edge_index: torch.Tensor) -> torch.Tensor:
        """单层 GAT 编码"""
        Wh = torch.mm(features, self.W)  # (N, hidden)

        if edge_index.numel() > 0:
            src, dst = edge_index[0], edge_index[1]
            # 注意力分数
            e_src = torch.mm(Wh, self.a_src).squeeze(-1)  # (N,)
            e_dst = torch.mm(Wh, self.a_dst).squeeze(-1)  # (N,)
            edge_attn = F.leaky_relu(e_src[src] + e_dst[dst], 0.2)  # (E,)

            # 稀疏 softmax（按目标节点分组）
            n = Wh.size(0)
            attn_sparse = torch.sparse_coo_tensor(
                edge_index, F.leaky_relu(edge_attn),
                size=(n, n), check_invariants=False,
            )
            attn_sparse = torch.sparse.softmax(attn_sparse, dim=1)
            h = torch.sparse.mm(attn_sparse, Wh)
        else:
            h = Wh

        h = F.elu(h)
        h = self.W_out(h)
        return h

    def encode(self, features: torch.Tensor,
               edge_index: torch.Tensor) -> torch.Tensor:
        """提取节点嵌入（训练后调用）"""
        return self._gat_encode(features, edge_index)

    def forward(self, features: torch.Tensor,
                edge_index: torch.Tensor) -> torch.Tensor:
        """
        计算对比学习 loss

        Args:
            features: 节点特征 (N, input_dim)
            edge_index: 边索引 (2, E)

        Returns:
            InfoNCE loss (标量)
        """
        # 从原始图得到节点嵌入
        h = self._gat_encode(features, edge_index)

        # 数据增强: 两个不同的视角
        # View 1: 子图采样 + 特征掩码
        v1_f, v1_e = self.augmentor.subgraph_sample(features, edge_index)
        v1_f = self.augmentor.feature_mask(v1_f)
        v1_e = self.augmentor.edge_perturb(v1_f, v1_e) if v1_e.numel() > 0 else v1_e
        h1 = self._gat_encode(v1_f, v1_e)

        # View 2: 边扰动 + 特征掩码
        v2_f = self.augmentor.feature_mask(features)
        v2_e = self.augmentor.edge_perturb(features, edge_index) if edge_index.numel() > 0 else edge_index
        h2 = self._gat_encode(v2_f, v2_e)

        # 投影到对比空间
        z1 = self.projection(h1)
        z2 = self.projection(h2)

        # InfoNCE loss: 同节点正对 ↔ 异节点负对
        n1 = z1.size(0)
        n2 = z2.size(0)
        n = min(n1, n2)

        z1 = z1[:n]
        z2 = z2[:n]

        # 归一化
        z1 = F.normalize(z1, dim=1)
        z2 = F.normalize(z2, dim=1)

        # 相似度矩阵
        sim = torch.mm(z1, z2.T) / self.temperature  # (n, n)

        # InfoNCE: 对角线为正例
        labels = torch.arange(n, device=features.device)
        loss = F.cross_entropy(sim, labels)

        return loss

    def get_embedding_for_gat_init(self) -> Dict[str, torch.Tensor]:
        """
        返回可用作 GAT 初始化的参数
        """
        return {
            'gat_encoder.W': self.W.data,
            'gat_encoder.a_src': self.a_src.data,
            'gat_encoder.a_dst': self.a_dst.data,
            'gat_encoder.W_out.weight': self.W_out.weight.data,
            'gat_encoder.W_out.bias': self.W_out.bias.data if hasattr(self.W_out, 'bias') and self.W_out.bias is not None else None,
        }


# ════════════════════════════════════════════════════════════
# 训练器
# ════════════════════════════════════════════════════════════

class SynapsePretrainer:
    """
    突触对比学习预训练器

    管理数据加载、训练循环、模型持久化。
    """

    def __init__(
        self,
        workspace_path: str = None,
        model_dir: str = None,
        input_dim: int = 64,
        hidden_dim: int = 64,
        output_dim: int = 64,
        lr: float = 1e-3,
        device: str = "cpu",
    ):
        self.ws = workspace_path or path_resolver.WORKSPACE_ROOT
        self.model_dir = model_dir or os.path.join(self.ws, "models")
        os.makedirs(self.model_dir, exist_ok=True)

        self.input_dim = input_dim
        self.device = torch.device(device)

        self.model = SynapseContrastor(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            output_dim=output_dim,
        ).to(self.device)

        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        self._graph_builder = None

    def _get_graph(self):
        """从突触网络构建图"""
        import sys as _sys
        if self._graph_builder is None:
            _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from gnn_graph_builder import SynapseGraphBuilder
            self._graph_builder = SynapseGraphBuilder(
                workspace_path=self.ws,
                feature_dim=self.input_dim,
                device=str(self.device),
            )
        graph = self._graph_builder.load_from_database(top_k=500)
        return graph

    def train_step(self, features: torch.Tensor,
                   edge_index: torch.Tensor) -> float:
        """单步训练"""
        self.model.train()
        self.optimizer.zero_grad()

        features = features.to(self.device)
        edge_index = edge_index.to(self.device)

        loss = self.model(features, edge_index)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), 5.0)
        self.optimizer.step()

        return loss.item()

    def pretrain(self, epochs: int = 50, log_interval: int = 10) -> Dict[str, Any]:
        """
        执行预训练

        Args:
            epochs: 训练轮数
            log_interval: 日志间隔

        Returns:
            训练统计
        """
        import sys as _sys

        graph = self._get_graph()
        if graph.num_nodes < 3:
            logger.warning(f"图节点数 < 3 ({graph.num_nodes})，跳过预训练")
            return {"status": "skipped", "reason": "too_few_nodes"}

        features = graph.node_features
        edge_index = graph.edge_index

        if features.numel() == 0 or edge_index.numel() == 0:
            return {"status": "skipped", "reason": "empty_graph"}

        losses = []
        for epoch in range(epochs):
            loss = self.train_step(features, edge_index)
            losses.append(loss)

            if (epoch + 1) % log_interval == 0:
                logger.info(f"[Pretrain {epoch+1}/{epochs}] loss={loss:.4f}")

        # 保存模型
        self.save_model()

        stats = {
            "status": "completed",
            "epochs": epochs,
            "final_loss": losses[-1] if losses else 0,
            "avg_loss": sum(losses) / len(losses) if losses else 0,
            "num_nodes": graph.num_nodes,
            "num_edges": graph.num_edges,
        }

        return stats

    def save_model(self):
        """保存模型权重"""
        path = os.path.join(self.model_dir, "synapse_pretrain.pth")
        torch.save(self.model.state_dict(), path)
        logger.info(f"预训练模型已保存到 {path}")

    def load_model(self) -> bool:
        """加载已保存的模型权重"""
        path = os.path.join(self.model_dir, "synapse_pretrain.pth")
        if not os.path.exists(path):
            return False
        try:
            self.model.load_state_dict(torch.load(path, map_location=self.device))
            logger.info(f"预训练模型已加载: {path}")
            return True
        except Exception as e:
            logger.warning(f"加载预训练模型失败: {e}")
            return False

    def get_gat_init_params(self) -> Optional[Dict[str, torch.Tensor]]:
        """
        获取用于 GAT 初始化的参数

        如果预训练模型存在，返回其嵌入参数供 GAT 使用。
        """
        if not self.load_model():
            return None
        return self.model.get_embedding_for_gat_init()


# ════════════════════════════════════════════════════════════
# 便捷接口
# ════════════════════════════════════════════════════════════

_PRETRAINER_INSTANCE = None


def get_pretrainer(workspace_path: str = None) -> SynapsePretrainer:
    """获取全局预训练器实例"""
    global _PRETRAINER_INSTANCE
    if _PRETRAINER_INSTANCE is None:
        _PRETRAINER_INSTANCE = SynapsePretrainer(workspace_path=workspace_path)
    return _PRETRAINER_INSTANCE


def run_pretrain(epochs: int = 50, workspace_path: str = None) -> Dict:
    """运行预训练"""
    return get_pretrainer(workspace_path).pretrain(epochs=epochs)


def load_pretrained_weights(model: torch.nn.Module,
                            workspace_path: str = None) -> bool:
    """将预训练权重加载到已有模型"""
    trainer = get_pretrainer(workspace_path)
    params = trainer.get_gat_init_params()
    if params is None:
        return False

    # 尝试匹配层名
    model_sd = model.state_dict()
    matched = 0
    for name, param in params.items():
        if param is None:
            continue
        # 尝试找到对应层
        for m_name, m_param in model_sd.items():
            if m_param.shape == param.shape and (
                    'W' in name and 'W' in m_name
                    or 'a_src' in name and 'a_src' in m_name
                    or 'a_dst' in name and 'a_dst' in m_name
                    or 'W_out' in name and any(k in m_name for k in ['W', 'weight'])):
                model_sd[m_name] = param
                matched += 1
                break

    if matched > 0:
        model.load_state_dict(model_sd, strict=False)
        logger.info(f"预训练权重加载: {matched} 层匹配")
        return True
    return False


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    print("=== 突触对比学习测试 ===\n")

    # 创建测试图
    n, d = 20, 64
    features = torch.randn(n, d)
    edge_index = torch.randint(0, n, (2, 40))

    model = SynapseContrastor(input_dim=d, output_dim=32)

    # 测试 loss
    loss = model(features, edge_index)
    print(f"InfoNCE loss: {loss.item():.4f}")

    # 测试编码
    emb = model.encode(features, edge_index)
    print(f"嵌入形状: {emb.shape}")

    # 测试预训练器
    trainer = SynapsePretrainer(input_dim=d, output_dim=32)
    stats = trainer.pretrain(epochs=5, log_interval=5)
    print(f"预训练统计: {stats}")

    # 测试 GAT 初始化参数
    init_params = trainer.get_gat_init_params()
    print(f"GAT 初始化参数量: {len(init_params) if init_params else 0}")

    print("\n✅ 测试完成")
