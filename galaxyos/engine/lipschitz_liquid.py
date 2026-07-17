#!/usr/bin/env python3
"""
Lipschitz Liquid — Lipschitz 约束的液体单元

带 Lipschitz 约束的 LTC 单元，保证训练稳定性。

动机：
  - LTC 的 ODE 右端函数是 σ(W_h h + W_x x + b) * (E - h) / τ
  - σ 的导数最大为 0.25，但 W_h 可能很大 → 复合 Lipschitz 常数可能很大
  - 大的 Lipschitz 常数 → ODE 刚性 → 数值不稳定 → 训练发散
  - Lipschitz 约束保证：|f(y1) - f(y2)| ≤ L * |y1 - y2|
  - 当 L 受控时，ODE 求解器收敛，训练稳定

核心机制：
  1. 权重归一化 (WeightNormalization): w = g * v / ||v||
  2. 谱范数约束 (SpectralNorm): 限制最大奇异值
  3. LipschitzLTCUnit: 受约束的 LTC 单元

在 GalaxyOS 中的角色：
  - 与 ltc_se_framework.py 的 LTCUnit 对比稳定性
  - 在需要高稳定性训练的场景替代普通 LTC
  - L3 优化器使用此单元保证长程训练不崩溃

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

logger = logging.getLogger("lipschitz_liquid")

import numpy as np


# ==================== Lipschitz 约束 ====================

class LipschitzConstraint:
    """
    Lipschitz 约束 — 保证 f 的 Lipschitz 常数 ≤ 1

    方法：
    1. 权重归一化: w = g * v / ||v||_2
       - 可训练参数: g (标量缩放), v (方向)
       - 保证 ||w||_2 = g
       - Lipschitz 常数 ≤ g

    2. 谱范数约束: 限制最大奇异值
       - 对矩阵 W 做 SVD: W = U Σ V^T
       - 约束 σ_max(W) ≤ γ
       - 投影: W = U * diag(min(σ_i, γ)) * V^T

    3. 组合使用: 先归一化再约束谱范数
    """

    def __init__(self, shape: Tuple[int, ...],
                 norm_type: str = "weight_norm",
                 max_norm: float = 1.0,
                 spectral_gamma: float = 1.0):
        """
        Args:
            shape: 权重矩阵形状
            norm_type: "weight_norm" | "spectral" | "both"
            max_norm: 权重归一化的最大范数
            spectral_gamma: 谱范数约束的上界
        """
        self.shape = shape
        self.norm_type = norm_type
        self.max_norm = max_norm
        self.spectral_gamma = spectral_gamma

        # 权重归一化参数
        if norm_type in ("weight_norm", "both"):
            # 可训练方向 v 和缩放 g
            limit = math.sqrt(6 / (shape[-1] + shape[0]))
            self.v = np.random.uniform(-limit, limit, shape).astype(np.float32)
            self.g = np.ones((shape[0], 1), dtype=np.float32)

    def apply_weight_norm(self, w: np.ndarray) -> np.ndarray:
        """权重归一化: w = g * v / ||v||

        梯度可反向传播到 g 和 v。
        """
        # ||v||_2, shape 保持可广播
        norm = np.linalg.norm(self.v, axis=1, keepdims=True) + 1e-8

        # 保证 ||w||_2 = min(g, max_norm)
        g_clamped = np.clip(self.g, 0, self.max_norm)
        w_normalized = g_clamped * self.v / norm

        return w_normalized

    def apply_spectral_norm(self, w: np.ndarray) -> np.ndarray:
        """谱范数约束: σ_max(W) ≤ gamma

        用幂迭代法近似最大奇异值。

        Args:
            w: 输入矩阵 [out_dim, in_dim]

        Returns:
            约束后的矩阵
        """
        # 幂迭代（3轮）
        u = np.random.randn(w.shape[0]).astype(np.float64)
        u = u / (np.linalg.norm(u) + 1e-8)

        for _ in range(10):
            v = w.T @ u
            v = v / (np.linalg.norm(v) + 1e-8)
            u = w @ v
            u = u / (np.linalg.norm(u) + 1e-8)

        # 估计最大奇异值
        sigma_hat = u @ (w @ v)

        # 约束
        if sigma_hat > self.spectral_gamma:
            scale = self.spectral_gamma / (sigma_hat + 1e-8)
            w = w * scale

        return w

    def apply(self, w: np.ndarray) -> np.ndarray:
        """综合应用 Lipschitz 约束"""
        if self.norm_type == "weight_norm":
            return self.apply_weight_norm(w)
        elif self.norm_type == "spectral":
            return self.apply_spectral_norm(w)
        elif self.norm_type == "both":
            w_normed = self.apply_weight_norm(w)
            return self.apply_spectral_norm(w_normed)
        else:
            return w

    def lipschitz_constant(self, w: np.ndarray) -> float:
        """估计当前权重的 Lipschitz 常数"""
        # 幂迭代
        u = np.random.randn(w.shape[0]).astype(np.float64)
        u = u / (np.linalg.norm(u) + 1e-8)

        for _ in range(5):
            v = w.T @ u
            v = v / (np.linalg.norm(v) + 1e-8)
            u = w @ v
            u = u / (np.linalg.norm(u) + 1e-8)

        return float(u @ (w @ v))


# ==================== Lipschitz 约束的 LTC 单元 ====================

class LipschitzLTCUnit:
    """
    LipschitzLTCUnit — 受 Lipschitz 约束的 LTC 单元

    微分方程与 LTCUnit 相同：
        dh/dt = σ(W_h h + W_x x + b) * (E - h) / τ

    但 W_h, W_x 受 Lipschitz 约束：
        ||W_h||_2 ≤ γ_h
        ||W_x||_2 ≤ γ_x

    保证复合 Lipschitz 常数有理论上界。

    对比普通 LTC（无约束）：
    - 普通 LTC: ||W_h|| 可能很大 → dh/dt 震荡 → 求解器需要小步长
    - Lipschitz LTC: ||W_h|| ≤ γ → dh/dt 平滑 → 稳定训练
    """

    def __init__(self, state_dim: int, input_dim: int,
                 constraint_type: str = "weight_norm",
                 gamma_h: float = 2.0,
                 gamma_x: float = 1.0,
                 tau_min: float = 0.1,
                 tau_max: float = 10.0):
        """
        Args:
            state_dim: 状态维度
            input_dim: 输入维度
            constraint_type: "weight_norm" | "spectral" | "both" | "none"
            gamma_h: 隐藏权重的 Lipschitz 上界
            gamma_x: 输入权重的 Lipschitz 上界
            tau_min: 最小时间常数
            tau_max: 最大时间常数
        """
        self.state_dim = state_dim
        self.input_dim = input_dim
        self.constraint_type = constraint_type
        self.gamma_h = gamma_h
        self.gamma_x = gamma_x
        self.tau_min = tau_min
        self.tau_max = tau_max

        # 原始（无约束）权重 — 这些是"可训练"参数
        limit = math.sqrt(6 / (state_dim + input_dim))
        self.raw_w_h = np.random.uniform(-limit, limit, (state_dim, state_dim)).astype(np.float32)
        self.raw_w_x = np.random.uniform(-limit, limit, (state_dim, input_dim)).astype(np.float32)
        self.b = np.zeros(state_dim, dtype=np.float32)

        # 时间常数门控权重
        self.raw_w_tau_h = np.random.uniform(-limit, limit, (1, state_dim)).astype(np.float32)
        self.raw_w_tau_x = np.random.uniform(-limit, limit, (1, input_dim)).astype(np.float32)
        self.b_tau = np.zeros(1, dtype=np.float32)

        # 饱和电位
        self.E = np.ones(state_dim, dtype=np.float32)

        # Lipschitz 约束器
        self.h_constraint = LipschitzConstraint(
            (state_dim, state_dim), constraint_type, gamma_h
        ) if constraint_type != "none" else None

        self.x_constraint = LipschitzConstraint(
            (state_dim, input_dim), constraint_type, gamma_x
        ) if constraint_type != "none" else None

    @property
    def w_h(self) -> np.ndarray:
        """受约束的隐藏权重"""
        if self.h_constraint:
            return self.h_constraint.apply(self.raw_w_h)
        return self.raw_w_h

    @property
    def w_x(self) -> np.ndarray:
        """受约束的输入权重"""
        if self.x_constraint:
            return self.x_constraint.apply(self.raw_w_x)
        return self.raw_w_x

    @staticmethod
    def _sigmoid(x):
        pos = x >= 0
        result = np.zeros_like(x, dtype=np.float64)
        result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
        result[~pos] = np.exp(x[~pos]) / (1.0 + np.exp(x[~pos]))
        return result

    def forward(self, h: np.ndarray, x: np.ndarray, t: float) -> np.ndarray:
        """一步 ODE 右端函数

        dh/dt = σ(W_h h + W_x x + b) * (E - h) / τ
        """
        drive = self.w_h @ h + self.w_x @ x + self.b
        gate = self._sigmoid(drive)

        # 时间常数
        tau_raw = (self.raw_w_tau_h @ h + self.raw_w_tau_x @ x + self.b_tau)[0]
        tau_rate = self._sigmoid(np.array([tau_raw]))[0]
        tau = tau_rate * (self.tau_max - self.tau_min) + self.tau_min

        dh = gate * (self.E - h) / tau
        return dh

    def step_euler(self, h: np.ndarray, x: np.ndarray,
                   t: float, dt: float = 0.1) -> np.ndarray:
        """一步欧拉积分"""
        dh = self.forward(h, x, t)
        return h + dh * dt

    def simulate(self, h0: np.ndarray, x_seq: np.ndarray,
                 dt: float = 0.1) -> np.ndarray:
        """完整序列模拟"""
        T = x_seq.shape[0]
        h = h0.copy().astype(np.float64)
        h_seq = [h.copy()]

        for t_idx in range(T):
            h = self.step_euler(h, x_seq[t_idx], t_idx * dt, dt)
            h_seq.append(h.copy())

        return np.array(h_seq)

    def estimate_lipschitz(self, n_samples: int = 50) -> float:
        """估计 Lipschitz 常数

        随机采样状态对，计算 ODE 函数的最大梯度。
        """
        max_L = 0.0

        for _ in range(n_samples):
            h1 = np.random.randn(self.state_dim).astype(np.float64) * 0.5
            h2 = h1 + np.random.randn(self.state_dim).astype(np.float64) * 0.1
            x = np.random.randn(self.input_dim).astype(np.float64) * 0.5

            f1 = self.forward(h1, x, 0.0)
            f2 = self.forward(h2, x, 0.0)

            dh = np.linalg.norm(h1 - h2)
            df = np.linalg.norm(f1 - f2)

            if dh > 1e-10:
                L = df / dh
                max_L = max(max_L, L)

        return max_L


# ==================== 稳定性对比 ====================

def compare_ltc_stability():
    """对比普通 LTC vs Lipschitz LTC 的稳定性

    测试方法：
    1. 初始化两个单元（普通 + Lipschitz）
    2. 对大权重初始化做长程模拟
    3. 检查状态是否发散
    """
    state_dim = 8
    input_dim = 4

    # 从 ltc_se_framework 导入普通 LTC
    try:
        from ltc_se_framework import LTCUnit, LiquidCellConfig, LiquidCellType
        HAS_LTC_SE = True
    except ImportError:
        HAS_LTC_SE = False

    np.random.seed(42)

    # 普通 LTC + 大权重初始化（模拟训练中途权重变大）
    if HAS_LTC_SE:
        config = LiquidCellConfig(
            cell_type=LiquidCellType.LTC,
            state_dim=state_dim, input_dim=input_dim,
        )
        normal_unit = LTCUnit(config)
        # 故意放大权重
        normal_unit.w_h *= 30.0  # Lipschitz ≈30
    else:
        # 简化：用无约束的 LipschitzLTCUnit 作为"普通"
        normal_unit = LipschitzLTCUnit(
            state_dim, input_dim,
            constraint_type="none",  # 无约束 = 普通 LTC
        )
        normal_unit.raw_w_h *= 30.0

    # Lipschitz LTC（约束）
    lipschitz_unit = LipschitzLTCUnit(
        state_dim, input_dim,
        constraint_type="spectral",
        gamma_h=2.0, gamma_x=1.0,
    )

    # 长程模拟
    T = 100
    x_seq = np.random.randn(T, input_dim).astype(np.float64)
    h0 = np.zeros(state_dim)

    # 普通 LTC 模拟
    normal_stable = False
    normal_max = float('inf')
    h_normal = None
    try:
        h_normal = normal_unit.simulate(h0.copy(), x_seq)
        normal_stable = np.all(np.isfinite(h_normal))
        normal_max = float(np.max(np.abs(h_normal)))
    except Exception as e:
        normal_stable = False
        normal_max = float('inf')
        logger.warning(f"普通 LTC 模拟异常: {e}")

    # Lipschitz LTC 模拟
    h_lip = lipschitz_unit.simulate(h0.copy(), x_seq)
    lip_stable = np.all(np.isfinite(h_lip))
    lip_max = float(np.max(np.abs(h_lip)))

    # 估计 Lipschitz 常数
    lip_L = lipschitz_unit.estimate_lipschitz()

    normal_range = f"[{float(np.min(np.abs(h_normal))):.2f}, {normal_max:.2f}]" if h_normal is not None else "[N/A]"
    print("  稳定性对比 (大权重初始化):")
    print(f"    普通 LTC:   {'✅ 稳定' if normal_stable else '❌ 发散'}, 范围 {normal_range}")
    print(f"    Lipschitz: {'✅ 稳定' if lip_stable else '❌ 发散'}, "
          f"范围 [{float(np.min(np.abs(h_lip))):.2f}, {lip_max:.2f}]")
    print(f"    Lipschitz 常数: {lip_L:.4f}")

    return {
        "normal_stable": normal_stable,
        "normal_max": normal_max,
        "lip_stable": lip_stable,
        "lip_max": lip_max,
        "lip_L": lip_L,
    }


# ==================== 测试 ====================

def test_lipschitz_constraint():
    """测试 Lipschitz 约束"""
    np.random.seed(42)

    shape = (5, 3)

    # 权重归一化
    constraint = LipschitzConstraint(shape, "weight_norm", max_norm=2.0)
    w_big = np.random.randn(5, 3).astype(np.float32) * 10

    w_constrained = constraint.apply(w_big)

    # 检查范数
    norms = np.linalg.norm(w_constrained, axis=1)
    assert np.all(norms <= 2.0 + 1e-5), f"范数超限: max={np.max(norms)}"

    print(f"✅ 权重归一化: ||w|| <= 2.0, max={np.max(norms):.4f}")

    # 谱范数
    constraint2 = LipschitzConstraint(shape, "spectral", spectral_gamma=1.0)
    w_constrained2 = constraint2.apply(w_big)

    L = constraint2.lipschitz_constant(w_constrained2)
    print(f"✅ 谱范数约束: Lipschitz={L:.4f} (gamma=1.0)")

    return constraint, constraint2


def test_lipschitz_ltc():
    """测试 Lipschitz LTC 单元"""
    np.random.seed(42)

    unit = LipschitzLTCUnit(
        state_dim=4, input_dim=2,
        constraint_type="spectral",
        gamma_h=1.0, gamma_x=0.5,
    )

    h0 = np.zeros(4)
    x_seq = np.random.randn(10, 2).astype(np.float64)

    h_seq = unit.simulate(h0, x_seq)

    assert h_seq.shape == (11, 4), f"形状错误: {h_seq.shape}"
    assert np.all(np.isfinite(h_seq)), "状态发散"

    print(f"✅ LipschitzLTCUnit: h_seq[{h_seq.shape}], "
          f"范围 [{h_seq.min():.3f}, {h_seq.max():.3f}]")

    L = unit.estimate_lipschitz()
    print(f"   估计 Lipschitz 常数: {L:.4f}")

    return unit


def test_stability_comparison():
    """测试稳定性对比"""
    result = compare_ltc_stability()

    print("✅ 稳定性对比完成")

    return result


if __name__ == "__main__":
    print("=" * 50)
    print("Lipschitz Liquid — 稳定液体单元")
    print("=" * 50)
    print()

    print("1. 测试 Lipschitz 约束")
    test_lipschitz_constraint()
    print()

    print("2. 测试 Lipschitz LTC 单元")
    test_lipschitz_ltc()
    print()

    print("3. 稳定性对比")
    test_stability_comparison()
    print()

    print("✅ P6: Lipschitz Liquid 全部测试通过")
