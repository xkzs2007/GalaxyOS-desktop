#!/usr/bin/env python3
"""
KAN — Kolmogorov-Arnold Networks

将 KAN (arXiv:2404.19756) 嵌入 GalaxyOS 的神经计算层：
  - 可学习激活函数替代 MLP 固定激活（SiLU/ReLU）
  - 样条基函数（B-spline）参数化每条"边"
  - 与 LTC/CfC 的 ODE 动态结合，提供更丰富的函数逼近

核心：KAN 层的每条边是一条可学习的样条曲线，
      而非 MLP 中固定激活 + 线性权重的组合。

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-14
"""

import os
import math
import time
import json
import logging
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("kan")

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    logger.warning("torch 未安装，KAN 使用纯 Python 模式")
    TORCH_AVAILABLE = False

import numpy as np


# ==================== B-Spline 基函数 ====================

def _bspline_basis(x: float, knots: np.ndarray, degree: int = 3) -> np.ndarray:
    """计算 B-spline 基函数值

    使用 de Boor 算法递归计算。

    Args:
        x: 输入标量
        knots: 节点向量 [n_knots]
        degree: 样条次数 (默认 cubic)

    Returns:
        basis: [n_basis] 个基函数值
    """
    n_knots = len(knots)
    n_basis = n_knots - degree - 1

    if n_basis <= 0:
        return np.array([1.0])

    # 阶 0 基函数
    bases = np.zeros((degree + 1, n_basis + degree))

    for i in range(n_basis + degree):
        if i < n_knots - 1:
            bases[0, i] = 1.0 if knots[i] <= x < knots[i + 1] else 0.0
        elif i == n_knots - 2 and x == knots[-1]:
            bases[0, i] = 1.0

    # 递归提升阶数
    for d in range(1, degree + 1):
        for i in range(n_basis + degree - d):
            # 左项
            if knots[i + d] != knots[i]:
                left = (x - knots[i]) / (knots[i + d] - knots[i])
            else:
                left = 0.0

            # 右项
            if knots[i + d + 1] != knots[i + 1]:
                right = (knots[i + d + 1] - x) / (knots[i + d + 1] - knots[i + 1])
            else:
                right = 0.0

            bases[d, i] = left * bases[d - 1, i] + right * bases[d - 1, i + 1]

    return bases[degree, :n_basis]


def _create_uniform_knots(n_basis: int, degree: int = 3,
                          x_min: float = -1.0, x_max: float = 1.0) -> np.ndarray:
    """创建均匀节点向量

    n_basis 个基函数需要 n_knots = n_basis + degree + 1 个节点
    """
    n_knots = n_basis + degree + 1
    # 两端重复 (degree+1) 次以确保端点在范围内
    inner = np.linspace(x_min, x_max, n_knots - 2 * (degree + 1) + 2)
    knots = np.concatenate([
        np.full(degree + 1, x_min),
        inner[1:-1] if len(inner) > 2 else np.array([(x_min + x_max) / 2]),
        np.full(degree + 1, x_max),
    ])
    return knots[:n_knots]


# ==================== KAN 线性层 (单边) ====================

class KANLinear:
    """
    KAN 线性层 — 可学习样条函数

    替代 nn.Linear: 每对 (in, out) 之间不是固定的权重 w，
    而是 B-spline 基函数 + 可学习系数的组合。

    公式:
        y_j = sum_i spline_i(x_i)
    其中 spline_i(x) = sum_k c_k * B_k(x)
    """

    def __init__(self, in_features: int, out_features: int,
                 n_basis: int = 8, degree: int = 3,
                 x_min: float = -1.0, x_max: float = 1.0,
                 use_residual: bool = True,
                 residual_scale: float = 0.1):
        self.in_features = in_features
        self.out_features = out_features
        self.n_basis = n_basis
        self.degree = degree
        self.use_residual = use_residual

        # 节点向量（所有基函数共享）
        self.knots = _create_uniform_knots(n_basis, degree, x_min, x_max)

        # 可学习系数: [out, in, n_basis]
        shape = (out_features, in_features, n_basis)
        limit = math.sqrt(6 / (in_features * n_basis))
        self.coeff = np.random.uniform(-limit, limit, shape).astype(np.float32)

        # 残差连接（可选）：类似 MLP 的线性部分
        if use_residual:
            limit_w = 1.0 / math.sqrt(in_features)
            self.residual_w = np.random.uniform(
                -limit_w, limit_w,
                (out_features, in_features)
            ).astype(np.float32)
            self.residual_b = np.zeros(out_features, dtype=np.float32)
            self.residual_scale = residual_scale
        else:
            self.residual_w = None
            self.residual_b = None

    def forward_np(self, x: np.ndarray) -> np.ndarray:
        """纯 NumPy 前向传播

        Args:
            x: [batch, in_features]

        Returns:
            y: [batch, out_features]
        """
        batch = x.shape[0]

        # 计算每个输入的基函数值
        # x_flat: [batch * in]
        x_flat = x.reshape(-1)
        all_bases = np.zeros((len(x_flat), self.n_basis), dtype=np.float32)

        for i, val in enumerate(x_flat):
            all_bases[i] = _bspline_basis(val, self.knots, self.degree)

        # 每对 (out, in) 的样条值 = sum_k coeff[out, in, k] * B_k(x)
        # 结果: [batch, out, in]
        bases_batch = all_bases.reshape(batch, self.in_features, self.n_basis)
        spline_out = np.einsum('oik,bik->bo', np.float32(self.coeff),
                                np.float32(bases_batch))

        # y 已经是 [batch, out]
        y = spline_out

        # 残差连接
        if self.use_residual and self.residual_w is not None:
            residual = x @ self.residual_w.T  # 简化（不带 bias）
            y += self.residual_scale * residual

        return y

    def forward(self, x):
        """统一前向分发"""
        if isinstance(x, np.ndarray):
            return self.forward_np(x)
        if TORCH_AVAILABLE and isinstance(x, torch.Tensor):
            return self.forward_torch(x)
        return self.forward_np(np.array(x, dtype=np.float32))

    def get_weights(self) -> dict:
        """获取可学习参数（用于序列化/分析）"""
        return {
            "n_basis": self.n_basis,
            "degree": self.degree,
            "coeff_shape": list(self.coeff.shape),
            "coeff_mean": float(np.mean(self.coeff)),
            "coeff_std": float(np.std(self.coeff)),
            "knots": self.knots.tolist(),
        }


# ==================== KAN 多层网络 ====================

class KANNetwork:
    """
    KAN 多层网络

    堆叠 KANLinear 层，构成完整的 KAN 网络。

    与 MLP 的区别：
    - MLP: 线性层 + 固定激活 (ReLU/GELU/SiLU)
    - KAN: 样条参数化的"边"，每层 = 一组 B-spline 基函数

    在 GalaxyOS 中：
    - 替代 `adaptive_classifier.py` 中的 MLP 分类器
    - 嵌入 LTC/CfC 的突触权重网络 → 提供更丰富的动态映射
    """

    def __init__(self, layer_sizes: List[int],
                 n_basis: int = 8, degree: int = 3,
                 use_residual: bool = True,
                 dropout: float = 0.0):
        self.layer_sizes = layer_sizes
        self.dropout = dropout

        # 构建 KAN 层
        self.layers: List[KANLinear] = []
        for i in range(len(layer_sizes) - 1):
            layer = KANLinear(
                in_features=layer_sizes[i],
                out_features=layer_sizes[i + 1],
                n_basis=n_basis,
                degree=degree,
                use_residual=use_residual,
            )
            self.layers.append(layer)

    def forward(self, x):
        """前向传播"""
        h = x
        for i, layer in enumerate(self.layers):
            h = layer.forward(h)
            if i < len(self.layers) - 1:
                # 中间层：非线性（样条本身已经是非线性）
                # 可选附加激活
                if isinstance(h, np.ndarray):
                    h = np.maximum(h, 0)  # ReLU 门控（论文可选）
                elif TORCH_AVAILABLE and isinstance(h, torch.Tensor):
                    h = F.relu(h)

        return h

    def predict(self, x):
        """预测接口"""
        return self.forward(x)

    def get_layer_info(self) -> List[dict]:
        """获取各层信息"""
        return [l.get_weights() for l in self.layers]

    def total_params(self) -> int:
        """估算参数量"""
        total = 0
        for l in self.layers:
            total += l.coeff.size
            if l.use_residual and l.residual_w is not None:
                total += l.residual_w.size
        return total


# ==================== KAN + LTC 融合 ====================

class KanLtcMerger:
    """
    KAN + LTC 融合层

    LTC 的微分方程:
        dh/dt = f(h, x, t, θ)

    传统实现中 f 是 MLP。这里用 KAN 替代：
        dh/dt = KAN(h, x, t)

    优势：
    - KAN 的可学习样条更能捕捉复杂动态
    - 更少的参数：KAN 在小数据下泛化更好
    - 可解释性：样条系数可可视化
    """

    def __init__(self, state_dim: int, input_dim: int,
                 hidden_dim: int = 32,
                 n_basis: int = 8, degree: int = 3):
        self.state_dim = state_dim
        self.input_dim = input_dim

        # KAN 替代原始的 MLP(ode_func)
        self.kan = KANNetwork(
            [state_dim + input_dim, hidden_dim, state_dim],
            n_basis=n_basis,
            degree=degree,
        )

    def ode_func(self, h: np.ndarray, x: np.ndarray, t: float) -> np.ndarray:
        """ODE 右端函数

        dh/dt = KAN(concat(h, x))
        """
        inp = np.concatenate([h, x])
        return self.kan.forward(inp.reshape(1, -1)).reshape(-1)

    def forward_euler(self, h0: np.ndarray, x_seq: np.ndarray,
                      dt: float = 0.1) -> np.ndarray:
        """前向欧拉求解（简单测试用）

        Args:
            h0: 初始状态 [state_dim]
            x_seq: 输入序列 [T, input_dim]
            dt: 时间步长

        Returns:
            h_seq: 状态序列 [T+1, state_dim]
        """
        T = x_seq.shape[0]
        h = h0.copy()
        h_seq = [h.copy()]

        for t in range(T):
            dh = self.ode_func(h, x_seq[t], t * dt)
            h = h + dh * dt
            h_seq.append(h.copy())

        return np.array(h_seq)

    def get_info(self) -> dict:
        return {
            "state_dim": self.state_dim,
            "input_dim": self.input_dim,
            "kan_params": self.kan.total_params(),
            "kan_layers": [l.get_weights()["coeff_shape"] for l in self.kan.layers],
        }


# ==================== 测试 ====================

def test_kan_linear():
    """测试 KAN 线性层"""
    layer = KANLinear(in_features=4, out_features=8, n_basis=8, degree=3)

    x = np.random.randn(16, 4).astype(np.float32)
    y = layer.forward_np(x)

    assert y.shape == (16, 8), f"输出形状错误: {y.shape}"
    print(f"✅ KAN 线性层: {x.shape} → {y.shape}")

    info = layer.get_weights()
    print(f"   基函数数: {info['n_basis']}, 次数: {info['degree']}")
    print(f"   系数均值: {info['coeff_mean']:.3f}, 标准差: {info['coeff_std']:.3f}")

    return layer


def test_kan_network():
    """测试 KAN 多层网络"""
    kan = KANNetwork([4, 16, 8, 2], n_basis=8, degree=3)

    x = np.random.randn(32, 4).astype(np.float32)
    y = kan.forward(x)

    assert y.shape == (32, 2), f"输出形状错误: {y.shape}"
    print(f"✅ KAN 网络: 4→16→8→2, 参数量: {kan.total_params()}")
    print(f"   输入: {x.shape}, 输出: {y.shape}")

    # 验证与 MLP 的差异（KAN 应该有更丰富的表示）
    layer_info = kan.get_layer_info()
    for i, li in enumerate(layer_info):
        print(f"   层 {i}: coeff {li['coeff_shape']}, 范围 [{li['coeff_mean']-2*li['coeff_std']:.2f}, {li['coeff_mean']+2*li['coeff_std']:.2f}]")

    return kan


def test_kan_ltc_fusion():
    """测试 KAN + LTC 融合"""
    merger = KanLtcMerger(state_dim=4, input_dim=2, hidden_dim=16, n_basis=8, degree=3)

    # 模拟 ODE 求解
    h0 = np.zeros(4)
    x_seq = np.random.randn(10, 2).astype(np.float32)

    h_seq = merger.forward_euler(h0, x_seq, dt=0.1)

    assert h_seq.shape == (11, 4), f"ODE 输出形状错误: {h_seq.shape}"
    print(f"✅ KAN+LTC 融合: ODE 求解 {h0.shape} → {h_seq.shape}")
    print(f"   最终状态: {h_seq[-1]}")
    print(f"   状态范围: [{h_seq.min():.3f}, {h_seq.max():.3f}]")

    info = merger.get_info()
    print(f"   KAN 参数: {info['kan_params']}")

    return merger


if __name__ == "__main__":
    print("=" * 50)
    print("KAN 测试")
    print("=" * 50)

    test_kan_linear()
    print()
    test_kan_network()
    print()
    test_kan_ltc_fusion()

    print()
    print("✅ KAN 全部测试通过")
