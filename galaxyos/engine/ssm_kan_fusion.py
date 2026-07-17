#!/usr/bin/env python3
"""
ss-Mamba + KAN — KAN 增强的状态空间序列模型

将 KAN (Kolmogorov-Arnold Network) 作为 SSM 的状态更新函数，
替代传统 MLP/线性投影，用 KAN 的 B-spline 学习更复杂的时序模式。

设计理念：
  传统 SSM:    x_{t+1} = A @ x_t + B @ u_t
  KAN 增强:    x_{t+1} = KAN(concat(x_t, u_t)) + x_t
               y_t = KAN_out(x_{t+1}) + D @ u_t

KAN 的 B-spline 基函数可以捕捉：
  - 非线性时序依赖（高阶交互）
  - 输入敏感的状态转移
  - 选择性输出投影

与 Mamba 的融合：
  - 选择机制：B, C 投影也通过 KAN 实现
  - 多通道：每个通道独立或共享 KAN
  - 残余连接：保留线性 SSM 主干

在 GalaxyOS 中的角色：
  - 需要复杂时序模式识别的场景
  - 替代标准 SSM 的线性投影
  - 与其他模块（KAN+LTC 融合、KAN+NeuralODE）互补

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-14
"""

import math
import logging
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field

logger = logging.getLogger("ssm_kan")

import numpy as np

try:
    from kan_network import KANNetwork, KANLinear, _bspline_basis, _create_uniform_knots
except ImportError:
    logger.warning("直接 import KAN 失败，尝试相对路径")
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from kan_network import KANNetwork, KANLinear, _bspline_basis, _create_uniform_knots


# ==================== SSM + KAN 状态更新 ====================

class KANStateUpdate:
    """
    KAN 状态更新函数

    替代 x_{t+1} = A @ x_t + B @ u_t 中的线性组合。
    使用 KAN 网络学习从 (x_t, u_t) 到状态更新的映射。

    公式：
        Δx = KAN_state(concat(x_t, u_t))
        x_{t+1} = x_t + Δx
    """

    def __init__(self, state_dim: int, input_dim: int,
                 hidden_dim: int = 32,
                 n_basis: int = 8,
                 degree: int = 3,
                 use_residual: bool = True,
                 use_linear_skip: bool = True):
        """
        Args:
            state_dim: 状态维度
            input_dim: 输入维度
            hidden_dim: KAN 隐藏层维度
            n_basis: B-spline 基函数数
            degree: B-spline 次数
            use_residual: KAN 使用残差连接
            use_linear_skip: 保留线性 SSM 主干（A@x + B@u 作为残差）
        """
        self.state_dim = state_dim
        self.input_dim = input_dim
        self.use_linear_skip = use_linear_skip

        # KAN 网络: [state_dim + input_dim] → [hidden_dim] → [state_dim]
        self.kan = KANNetwork(
            layer_sizes=[state_dim + input_dim, hidden_dim, state_dim],
            n_basis=n_basis,
            degree=degree,
            use_residual=use_residual,
        )

        # 线性跳过连接 — 使用负对角衰减确保稳定性
        if use_linear_skip:
            limit = math.sqrt(6 / (state_dim + input_dim))
            self.A_skip = np.random.uniform(-limit, limit, (state_dim, state_dim)).astype(np.float32)
            # 对角线设负值 -> 稳态衰减
            self.A_skip -= np.eye(state_dim, dtype=np.float32) * 0.5
            self.B_skip = np.random.uniform(-limit, limit, (state_dim, input_dim)).astype(np.float32) * 0.1
        else:
            self.A_skip = None
            self.B_skip = None

    def forward(self, h: np.ndarray, u: np.ndarray) -> np.ndarray:
        """计算状态更新

        Args:
            h: 当前状态 [state_dim]
            u: 当前输入 [input_dim]

        Returns:
            h_next: 新状态 [state_dim]
        """
        # 拼接输入
        x = np.concatenate([h, u])  # [state_dim + input_dim]

        # KAN 状态更新（用小权重）
        kan_delta = self.kan.forward(x.reshape(1, -1)).reshape(-1)  # [state_dim]
        kan_delta = np.tanh(kan_delta) * 0.1  # 非线性限幅

        # 线性跳过（带负对角）
        if self.use_linear_skip:
            linear_delta = self.A_skip @ h + self.B_skip @ u  # [state_dim]
            delta = kan_delta + linear_delta
        else:
            delta = kan_delta

        return h + delta

    def get_info(self) -> dict:
        return {
            "state_dim": self.state_dim,
            "input_dim": self.input_dim,
            "kan_params": self.kan.total_params(),
            "kan_layers": [l.get_weights()["coeff_shape"] for l in self.kan.layers],
            "use_linear_skip": self.use_linear_skip,
        }


# ==================== KAN 增强的 B 和 C 投影 ====================

class KANProjection:
    """
    KAN 投影层 — 替代线性投影

    用于 SSM 的选择机制中替代 B(u) 和 C(u) 的线性投影：
        B_kan(u) = KAN_B(u)  — 输入依赖的状态投影
        C_kan(u) = KAN_C(u)  — 输入依赖的输出投影
    """

    def __init__(self, input_dim: int, proj_dim: int,
                 n_basis: int = 6,
                 degree: int = 3):
        """
        Args:
            input_dim: 输入维度
            proj_dim: 投影的目标维度
            n_basis: B-spline 基函数数
            degree: B-spline 次数
        """
        self.input_dim = input_dim
        self.proj_dim = proj_dim

        # KAN 网络: [input_dim] → [hidden_dim] → [proj_dim]
        hidden = max(proj_dim // 2, 4)
        self.kan = KANNetwork(
            layer_sizes=[input_dim, hidden, proj_dim],
            n_basis=n_basis,
            degree=degree,
            use_residual=True,
        )

        # 线性跳过
        limit = math.sqrt(6 / (proj_dim + input_dim))
        self.linear_w = np.random.uniform(-limit, limit, (proj_dim, input_dim)).astype(np.float32)
        self.linear_b = np.zeros(proj_dim, dtype=np.float32)

    def forward(self, u: np.ndarray) -> np.ndarray:
        """投影

        Args:
            u: 输入 [input_dim]

        Returns:
            投影输出 [proj_dim]
        """
        kan_out = self.kan.forward(u.reshape(1, -1)).reshape(-1)  # [proj_dim]
        linear_out = self.linear_w @ u + self.linear_b  # [proj_dim]
        return kan_out * 0.1 + linear_out


# ==================== KAN-B 矩阵投影 ====================

class KANBMatrixProjection:
    """
    KAN B 矩阵投影 — 从输入生成全 B 矩阵

    标准: B(u) ∈ ℝ^(state_dim × input_dim) — 线性投影
    这里: B 矩阵的每个条目由 KAN 从输入生成（更丰富的映射）
    """

    def __init__(self, state_dim: int, input_dim: int,
                 n_basis: int = 6, degree: int = 3):
        self.state_dim = state_dim
        self.input_dim = input_dim

        # 每个 (state_dim, input_dim) 条目共享一个 KAN
        # 简化实现：在状态/输入维度上展开
        hidden = max(max(state_dim, input_dim) // 2, 4)
        self.kan = KANNetwork(
            layer_sizes=[state_dim, hidden, state_dim * input_dim],
            n_basis=n_basis,
            degree=degree,
            use_residual=True,
        )

    def forward(self, u: np.ndarray, h: np.ndarray) -> np.ndarray:
        """生成 B 矩阵

        Args:
            u: 输入 [input_dim]
            h: 当前状态 [state_dim]

        Returns:
            B: [state_dim, input_dim]
        """
        # 用 h 作为 KAN 输入，生成展平的 B
        flat = self.kan.forward(h.reshape(1, -1)).reshape(-1)  # [state_dim * input_dim]
        B = flat.reshape(self.state_dim, self.input_dim)

        # 用 u 调制
        B = B * u[np.newaxis, :] * 0.1

        return B


# ==================== SSM + KAN 融合模型 ====================

class SSMWithKAN:
    """
    ss-Mamba + KAN — KAN 增强的 SSM 序列模型

    完整融合：
      - 状态更新: h_{t+1} = KAN_state(concat(h_t, u_t)) + h_t
      - B 矩阵: 可选 KAN_B(h_t, u_t)
      - C 矩阵: 可选 KAN_C(u_t) 线性投影
      - 输出投影: y = KAN_out(h_{t+1}) + D @ u_t
    """

    def __init__(self, state_dim: int, input_dim: int, output_dim: int,
                 n_channels: int = 4,
                 hidden_dim: int = 32,
                 n_basis: int = 8,
                 degree: int = 3,
                 use_kan_state: bool = True,
                 use_kan_output: bool = True,
                 use_kan_b: bool = False):
        """
        Args:
            state_dim: 状态维度
            input_dim: 输入维度
            output_dim: 输出维度
            n_channels: 多通道数
            hidden_dim: KAN 隐藏层维度
            n_basis: B-spline 基函数数
            degree: B-spline 次数
            use_kan_state: KAN 状态更新（替代线性 SSM）
            use_kan_output: KAN 输出投影
            use_kan_b: KAN B 矩阵生成
        """
        self.state_dim = state_dim
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.n_channels = n_channels
        self.use_kan_state = use_kan_state
        self.use_kan_output = use_kan_output
        self.use_kan_b = use_kan_b

        # ===== 状态更新器（每通道） =====
        if use_kan_state:
            self.state_updaters = [
                KANStateUpdate(state_dim, input_dim, hidden_dim, n_basis, degree)
                for _ in range(n_channels)
            ]
        else:
            self.state_updaters = None
            # 退化为标准 SSM（稳定衰减）
            self.A = -np.random.uniform(0.1, 1.0, (n_channels, state_dim)).astype(np.float32)
            self.A -= 0.5  # 确保负对角
            self.B_fixed = np.random.randn(n_channels, state_dim, input_dim).astype(np.float32) * 0.01

        # ===== B 矩阵（选择或 KAN） =====
        if use_kan_b:
            self.b_projectors = [
                KANBMatrixProjection(state_dim, input_dim, n_basis, degree)
                for _ in range(n_channels)
            ]
        else:
            self.b_projectors = None
            self.B_fixed = np.random.randn(n_channels, state_dim, input_dim).astype(np.float32) * 0.01

        # ===== C 矩阵 + 输出 =====
        if use_kan_output:
            self.c_projectors = [
                KANProjection(state_dim, output_dim, n_basis, degree)
                for _ in range(n_channels)
            ]
        else:
            self.c_projectors = None
            self.C_fixed = np.random.randn(n_channels, output_dim, state_dim).astype(np.float32) * 0.01

            # 输出投影（聚合多通道）
            limit_out = math.sqrt(6 / (output_dim + state_dim * n_channels))
            self.w_out = np.random.uniform(-limit_out, limit_out,
                                            (output_dim, state_dim * n_channels)).astype(np.float32)
            self.b_out = np.zeros(output_dim, dtype=np.float32)

        # D 直通（小值）
        self.D = np.random.randn(output_dim, input_dim).astype(np.float32) * 0.001

        # KAN 输出投影（如果 use_kan_output 为 True）
        if use_kan_output:
            self.kan_output = KANNetwork(
                layer_sizes=[n_channels * state_dim, hidden_dim, output_dim],
                n_basis=n_basis,
                degree=degree,
            )

    def forward_step(self, h: np.ndarray, u: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """单步前向

        Args:
            h: [n_channels, state_dim]
            u: [input_dim]

        Returns:
            (h_next, y)
        """
        h_next = np.zeros_like(h, dtype=np.float64)

        for c in range(self.n_channels):
            if self.use_kan_state:
                # KAN 状态更新
                h_next[c] = self.state_updaters[c].forward(h[c].astype(np.float32), u)
            else:
                # 标准 SSM 状态更新
                B_c = self.b_projectors[c].forward(u, h[c]) if self.use_kan_b else self.B_fixed[c]
                dh = self.A[c] * h[c] + B_c @ u
                h_next[c] = h[c] + dh

        # 状态限幅防爆炸
        h_norm = np.linalg.norm(h_next.reshape(-1))
        if h_norm > 200.0:
            h_next = h_next * (200.0 / h_norm)

        y = self._compute_output(h_next, u)
        return h_next, y

    def _compute_output(self, h: np.ndarray, u: np.ndarray) -> np.ndarray:
        """计算输出（带稳定性处理 + 归一化）"""
        # 对 h 做归一化防止爆炸
        h_f32 = h.astype(np.float32)
        h_norm = np.linalg.norm(h_f32.reshape(-1))
        if h_norm > 50.0:
            h_f32 = h_f32 * (50.0 / h_norm)

        if self.use_kan_output:
            h_flat = h_f32.reshape(-1)
            y = self.kan_output.forward(h_flat.reshape(1, -1)).reshape(-1)
            y = np.tanh(y / 10.0) * 20.0  # 柔化+限幅
            y = y + self.D @ u
        else:
            h_flat = h_f32.reshape(-1)
            y = self.w_out @ h_flat + self.b_out + self.D @ u
            # 线性输出也限幅
            y = np.clip(y, -50.0, 50.0)

        return y

    def forward(self, u_seq: np.ndarray) -> np.ndarray:
        """序列前向

        Args:
            u_seq: [T, input_dim]

        Returns:
            y_seq: [T, output_dim]
        """
        T = u_seq.shape[0]
        h = np.zeros((self.n_channels, self.state_dim), dtype=np.float64)
        y_seq = np.zeros((T, self.output_dim), dtype=np.float32)

        for t in range(T):
            h, y = self.forward_step(h, u_seq[t])
            y_seq[t] = y

        return y_seq

    def forward_with_state(self, u_seq: np.ndarray
                           ) -> Tuple[np.ndarray, np.ndarray]:
        """序列前向 + 状态

        Returns:
            (y_seq, h_seq)
        """
        T = u_seq.shape[0]
        h = np.zeros((self.n_channels, self.state_dim), dtype=np.float64)
        y_seq = np.zeros((T, self.output_dim), dtype=np.float32)
        h_seq = np.zeros((T, self.n_channels, self.state_dim), dtype=np.float64)

        for t in range(T):
            h, y = self.forward_step(h, u_seq[t])
            y_seq[t] = y
            h_seq[t] = h

        return y_seq, h_seq

    def compute_kernel(self, T: int) -> np.ndarray:
        """计算脉冲响应核"""
        kernel = np.zeros((T, self.output_dim, self.input_dim), dtype=np.float32)
        for i in range(self.input_dim):
            impulse = np.zeros((T, self.input_dim), dtype=np.float32)
            impulse[0, i] = 1.0
            kernel[:, :, i] = self.forward(impulse)
        return kernel

    def get_info(self) -> dict:
        info = {
            "state_dim": self.state_dim,
            "input_dim": self.input_dim,
            "output_dim": self.output_dim,
            "n_channels": self.n_channels,
            "use_kan_state": self.use_kan_state,
            "use_kan_output": self.use_kan_output,
            "use_kan_b": self.use_kan_b,
        }
        if self.use_kan_state:
            info["state_kan_params"] = [u.get_info()["kan_params"] for u in self.state_updaters]
        return info


# ==================== 测试 ====================

def test_kan_state_update():
    """测试 KAN 状态更新"""
    np.random.seed(42)

    updater = KANStateUpdate(state_dim=4, input_dim=2, hidden_dim=16)
    h = np.random.randn(4).astype(np.float32)
    u = np.random.randn(2).astype(np.float32)

    h_next = updater.forward(h, u)
    assert h_next.shape == (4,), f"输出形状错误: {h_next.shape}"
    print(f"✅ KAN 状态更新: {h} → {h_next}")
    print(f"   更新量: {np.abs(h_next - h).mean():.4f}")

    info = updater.get_info()
    print(f"   KAN 参数: {info['kan_params']}")

    return updater


def test_kan_projections():
    """测试 KAN 投影"""
    np.random.seed(42)

    proj = KANProjection(input_dim=4, proj_dim=8, n_basis=6, degree=3)
    u = np.random.randn(4).astype(np.float32)
    out = proj.forward(u)
    assert out.shape == (8,), f"投影形状错误: {out.shape}"
    print(f"✅ KAN 投影: input(4) → {out.shape}")
    print(f"   范围: [{out.min():.3f}, {out.max():.3f}]")

    return proj


def test_kan_b_matrix():
    """测试 KAN B 矩阵生成"""
    np.random.seed(42)

    b_proj = KANBMatrixProjection(state_dim=6, input_dim=3, n_basis=6, degree=3)
    u = np.random.randn(3).astype(np.float32)
    h = np.random.randn(6).astype(np.float32)
    B = b_proj.forward(u, h)
    assert B.shape == (6, 3), f"B 矩阵形状错误: {B.shape}"
    print(f"✅ KAN B 矩阵: {B.shape}")
    print(f"   范围: [{B.min():.3f}, {B.max():.3f}]")

    return b_proj


def test_ssm_with_kan_full():
    """测试完整 SSM + KAN 模型（全 KAN 模式）"""
    np.random.seed(42)

    ssm = SSMWithKAN(
        state_dim=8, input_dim=4, output_dim=4,
        n_channels=2, hidden_dim=16, n_basis=6,
        use_kan_state=True, use_kan_output=True, use_kan_b=False,
    )

    u_seq = np.random.randn(20, 4).astype(np.float32)
    y_seq = ssm.forward(u_seq)

    assert y_seq.shape == (20, 4), f"输出形状错误: {y_seq.shape}"
    print(f"✅ SSM+KAN (全 KAN 状态+输出): {u_seq.shape} → {y_seq.shape}")
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")

    return ssm


def test_ssm_with_kan_hybrid():
    """测试 SSM + KAN 混合模式（KAN 状态 + 线性输出）"""
    np.random.seed(42)

    ssm = SSMWithKAN(
        state_dim=8, input_dim=4, output_dim=3,
        n_channels=3, hidden_dim=16, n_basis=6,
        use_kan_state=True, use_kan_output=False, use_kan_b=False,
    )

    u_seq = np.random.randn(20, 4).astype(np.float32)
    y_seq = ssm.forward(u_seq)

    assert y_seq.shape == (20, 3), f"输出形状错误: {y_seq.shape}"
    print(f"✅ SSM+KAN (混合模式): {u_seq.shape} → {y_seq.shape}")
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")

    return ssm


def test_ssm_with_kan_kernel():
    """测试 SSM + KAN 核函数"""
    np.random.seed(42)

    ssm = SSMWithKAN(
        state_dim=4, input_dim=2, output_dim=2,
        n_channels=2, hidden_dim=8, n_basis=6,
        use_kan_state=True, use_kan_output=True,
    )

    kernel = ssm.compute_kernel(T=10)
    assert kernel.shape == (10, 2, 2), f"核形状错误: {kernel.shape}"
    print(f"✅ SSM+KAN 脉冲响应核: {kernel.shape}")

    return kernel


def test_comparison_kan_vs_linear():
    """对比 KAN 增强 vs 线性 SSM"""
    np.random.seed(42)

    # KAN 模式
    ssm_kan = SSMWithKAN(
        state_dim=8, input_dim=4, output_dim=4,
        n_channels=2, hidden_dim=16, n_basis=6,
        use_kan_state=True, use_kan_output=True,
    )

    # 线性模式（退化到标准 SSM）
    ssm_linear = SSMWithKAN(
        state_dim=8, input_dim=4, output_dim=4,
        n_channels=2,
        use_kan_state=False, use_kan_output=False,
    )

    u_seq = np.random.randn(25, 4).astype(np.float32)
    y_kan = ssm_kan.forward(u_seq)
    y_linear = ssm_linear.forward(u_seq)

    diff = np.abs(y_kan - y_linear).mean()
    print(f"✅ KAN vs 线性对比: 输出差异均值 = {diff:.4f}")
    assert diff > 0, "KAN 与线性应该产生不同输出"

    return ssm_kan, ssm_linear


def test_mimo_with_kan():
    """测试 MIMO + KAN"""
    np.random.seed(42)

    ssm = SSMWithKAN(
        state_dim=6, input_dim=2, output_dim=6,
        n_channels=4, hidden_dim=16, n_basis=6,
        use_kan_state=True, use_kan_output=True,
    )

    u_seq = np.random.randn(30, 2).astype(np.float32)
    y_seq = ssm.forward(u_seq)

    assert y_seq.shape == (30, 6), f"MIMO 形状错误: {y_seq.shape}"
    print(f"✅ MIMO+KAN: 输入(2)→{y_seq.shape}")
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")


if __name__ == "__main__":
    print("=" * 55)
    print("ss-Mamba + KAN — KAN 增强的 SSM")
    print("=" * 55)

    test_kan_state_update()
    print()
    test_kan_projections()
    print()
    test_kan_b_matrix()
    print()
    test_ssm_with_kan_full()
    print()
    test_ssm_with_kan_hybrid()
    print()
    test_ssm_with_kan_kernel()
    print()
    test_comparison_kan_vs_linear()
    print()
    test_mimo_with_kan()

    print()
    print("=" * 55)
    print("✅ ss-Mamba + KAN 全部测试通过")
    print("=" * 55)
