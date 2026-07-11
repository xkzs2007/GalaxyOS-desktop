#!/usr/bin/env python3
"""
Mamba-3 SSM — 复数值 + MIMO 状态空间模型

实现复数值状态空间模型 (complex-valued SSM) 的 Mamba 增强版本：
  - 复数值 A 矩阵：更丰富的动力学（振荡/旋转模式）
  - MIMO (multi-input multi-output)：多通道并行状态更新
  - 选择机制：B, C, D 矩阵随输入自适应变化
  - 离散化：零阶保持 (ZOH) 将连续 SSM 转为离散

基础 SSM:
    x(t+1) = A @ x(t) + B @ u(t)
    y(t)   = C @ x(t) + D @ u(t)

Mamba-3 增强:
    A ∈ ℂ^(n×n)  — 复数值状态转移矩阵
    B(u) ∈ ℝ^(n×m) — 输入自适应的 B
    C(u) ∈ ℝ^(p×n) — 输入自适应的 C
    D ∈ ℝ^(p×m)   — 直通矩阵（可学习）
    离散化：A_bar, B_bar = ZOH(A, B, Δ)

在 GalaxyOS 中的角色：
  - 时序状态空间建模
  - 与 LTC/CfC/Neural ODE 等连续模型互补
  - 需要快速推断（非 ODE 求解）的场景

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-14
"""

import math
import logging
import warnings
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field

logger = logging.getLogger("mamba3")

import numpy as np

# 压制复数值转实数的警告（新版 numpy 已移除 ComplexWarning，不再需要）


# ==================== 复数值工具函数 ====================

def complex_randn(*shape, dtype=np.complex128) -> np.ndarray:
    """复数值随机初始化（正态分布）"""
    real = np.random.randn(*shape).astype(np.float64)
    imag = np.random.randn(*shape).astype(np.float64)
    return (real + 1j * imag).astype(dtype)


def discretize_zoh(A: np.ndarray, B: np.ndarray, delta: float) -> Tuple[np.ndarray, np.ndarray]:
    """零阶保持 (ZOH) 离散化

    连续 SSM → 离散 SSM:
        A_bar = exp(A * Δ)
        B_bar = (A_bar - I) @ A^{-1} @ B

    支持复数值 A 矩阵。

    Args:
        A: 连续状态矩阵 [n, n] (real or complex)
        B: 连续输入矩阵 [n, m]
        delta: 步长标量

    Returns:
        A_bar: 离散状态矩阵 [n, n]
        B_bar: 离散输入矩阵 [n, m]
    """
    # A_bar = exp(A * Δ) — 矩阵指数
    n = A.shape[-1]
    A_delta = A * delta

    # 泰勒展开
    I = np.eye(n, dtype=np.complex128)
    result = I.copy()
    A_power = I.copy()
    fact = 1.0
    for k in range(1, 13):
        fact *= k
        A_power = A_power @ (A_delta.astype(np.complex128))
        result += A_power / fact

    A_bar = result.astype(A.dtype)

    # B_bar = (A_bar - I) @ A^{-1} @ B
    if np.linalg.cond(A) < 1e10:
        A_inv = np.linalg.inv(A)
    else:
        A_inv = np.linalg.pinv(A)

    B_bar = (A_bar - I) @ A_inv @ B.astype(np.complex128)
    B_bar = B_bar.astype(B.dtype)

    return A_bar, B_bar


# ==================== Mamba-3 SSM 单元 ====================

class Mamba3SSM:
    """
    Mamba-3 SSM — 复数值、MIMO、选择机制状态空间模型

    架构：
      输入 u ∈ ℝ^(m)  →  SSM 核心  →  输出 y ∈ ℝ^(p)

    SSM 核心（选择机制版本）：
        Δ = softplus(W_Δ u + b_Δ)          — 步长自适应
        B(u) = W_B u + b_B                  — 输入矩阵自适应
        C(u) = W_C u + b_C (through proj)  — 输出矩阵自适应
        A_bar, B_bar = ZOH(A, B(u), Δ)      — 离散化
        x_{t+1} = A_bar @ x_t + B_bar @ u_t — 状态更新
        y_t = C(u) @ x_{t+1} + D @ u_t       — 输出
    """

    def __init__(self, input_dim: int, state_dim: int, output_dim: int,
                 n_channels: int = 4,
                 use_selective: bool = True,
                 use_complex: bool = True,
                 dt_min: float = 0.001,
                 dt_max: float = 0.1):
        """
        Args:
            input_dim: 输入维度 m
            state_dim: 每状态维度 n
            output_dim: 输出维度 p
            n_channels: MIMO 通道数（并行 SSM）
            use_selective: 使用选择机制（B, C, Δ 随输入变化）
            use_complex: A 矩阵使用复数值
            dt_min/dt_max: 步长范围
        """
        self.input_dim = input_dim
        self.state_dim = state_dim
        self.output_dim = output_dim
        self.n_channels = n_channels
        self.use_selective = use_selective
        self.use_complex = use_complex
        self.dt_min = dt_min
        self.dt_max = dt_max

        # — 输出投影（C 的全局投影） —
        # w_out: [output_dim, state_dim * n_channels]
        limit_out = math.sqrt(6 / (output_dim + state_dim * n_channels))
        self.w_out = np.random.uniform(-limit_out, limit_out,
                                        (output_dim, state_dim * n_channels)).astype(np.float32)
        self.b_out = np.zeros(output_dim, dtype=np.float32)

        # D 直通矩阵: [output_dim, input_dim]
        self.D = np.random.randn(output_dim, input_dim).astype(np.float32) * 0.01

        # — SSM 参数 (多通道并行) —
        if use_complex:
            # 复数值 A: 对角线主导 + 复特征值
            A_real = -np.random.uniform(0.1, 1.0, (n_channels, state_dim)).astype(np.float64)
            A_imag = np.zeros((n_channels, state_dim), dtype=np.float64)
            for c in range(n_channels):
                for i in range(state_dim // 2):
                    A_imag[c, i * 2] = np.random.uniform(-2.0, 2.0)
                    A_imag[c, i * 2 + 1] = -A_imag[c, i * 2]
            self.A = np.zeros((n_channels, state_dim, state_dim), dtype=np.complex128)
            for c in range(n_channels):
                self.A[c] = np.diag(A_real[c].astype(np.complex128) + 1j * A_imag[c].astype(np.complex128))
        else:
            self.A = -np.random.uniform(0.1, 1.0, (n_channels, state_dim)).astype(np.float64)
            self.A_diag = np.array([np.diag(self.A[c]) for c in range(n_channels)])

        # — 选择机制参数 —
        if use_selective:
            # Δ 门: [n_channels, input_dim]
            self.w_delta = np.random.randn(n_channels, input_dim).astype(np.float32) * 0.01
            self.b_delta = np.zeros(n_channels, dtype=np.float32)

            # B 投影: [n_channels, state_dim, input_dim]
            limit_B = math.sqrt(6 / (state_dim + input_dim))
            self.w_B = np.random.uniform(-limit_B, limit_B,
                                          (n_channels, state_dim, input_dim)).astype(np.float32)
            self.b_B = np.zeros((n_channels, state_dim), dtype=np.float32)

            # C 投影: [n_channels, output_dim, state_dim]
            limit_C = math.sqrt(6 / (output_dim + state_dim))
            self.w_C = np.random.uniform(-limit_C, limit_C,
                                          (n_channels, output_dim, state_dim)).astype(np.float32)
            self.b_C = np.zeros((n_channels, output_dim), dtype=np.float32)
        else:
            self.w_delta = None
            self.b_delta = np.full(n_channels, 0.05, dtype=np.float32)

            # 固定 B: [n_channels, state_dim, input_dim]
            self.B_fixed = np.random.randn(n_channels, state_dim, input_dim).astype(np.float32) * 0.01
            # 固定 C: [n_channels, output_dim, state_dim]
            self.C_fixed = np.random.randn(n_channels, output_dim, state_dim).astype(np.float32) * 0.01

        # — 状态缓存 —
        self._state = None

    # ---------- 选择机制 ----------

    def _compute_delta(self, u: np.ndarray) -> np.ndarray:
        """计算步长 Δ"""
        if not self.use_selective:
            return np.full(self.n_channels, float(self.b_delta[0]), dtype=np.float32)

        # Δ = softplus(W_Δ u + b_Δ) * (dt_max - dt_min) + dt_min
        raw = u @ self.w_delta.T + self.b_delta
        delta = np.log(1.0 + np.exp(raw)) * (self.dt_max - self.dt_min) + self.dt_min
        return delta

    def _compute_B(self, u: np.ndarray) -> np.ndarray:
        """计算 B 矩阵: [n_channels, state_dim, input_dim]"""
        if not self.use_selective:
            return self.B_fixed

        # B = W_B * u + b_B (broadcast)
        # w_B: [n_channels, state_dim, input_dim]
        # u:   [input_dim]
        # 结果: [n_channels, state_dim, input_dim]
        B = self.w_B * u[np.newaxis, np.newaxis, :] + self.b_B[:, :, np.newaxis]
        return B

    def _compute_C(self, u: np.ndarray) -> np.ndarray:
        """计算 C 矩阵: [n_channels, output_dim, state_dim]"""
        if not self.use_selective:
            return self.C_fixed

        # C = W_C * u + b_C (element-wise for full matrix)
        # w_C: [n_channels, output_dim, state_dim]
        # u:   [input_dim]
        # 结果: [n_channels, output_dim, state_dim]
        # 但这里我们希望 C 每个通道是 [output_dim, state_dim] 的矩阵
        # 所以 u 的每个维度驱动一个矩阵条目
        # 简化：用输入标量乘积 + 偏移
        C = self.w_C * u.sum() * 0.1 + self.b_C[:, :, np.newaxis] * 0.1
        # 更准确但更复杂的实现：每个状态维独立
        return C

    # ---------- 前向传播 ----------

    def forward_step(self, x: np.ndarray, u: np.ndarray,
                     delta: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
        """单步前向

        Args:
            x: [n_channels, state_dim] (complex or real)
            u: [input_dim]
            delta: 可选步长

        Returns:
            (x_next, y)
        """
        # 2. 计算选择参数
        if delta is None:
            delta = self._compute_delta(u)
        B_mat = self._compute_B(u)
        C_mat = self._compute_C(u)

        # 3. 多通道并行 SSM
        x_next = np.zeros_like(x, dtype=np.complex128 if self.use_complex else np.float64)
        y_components = []

        for c in range(self.n_channels):
            A_c = self.A[c].astype(np.complex128) if self.use_complex else self.A_diag[c]

            # B, C 取决于选择模式
            if self.use_selective:
                B_c = B_mat[c].astype(np.complex128) if self.use_complex else B_mat[c]
                # C 是 [output_dim, state_dim]
                C_c = C_mat[c]  # [output_dim]
            else:
                B_c = self.B_fixed[c].astype(np.complex128) if self.use_complex else self.B_fixed[c]
                C_c = self.C_fixed[c]

            # 离散化
            d = delta[c] if isinstance(delta, np.ndarray) else delta
            A_bar, B_bar = discretize_zoh(A_c, B_c, d)

            # 状态更新: x = A_bar @ x + B_bar @ u
            x_next_c = A_bar @ x[c] + B_bar @ u
            x_next[c] = x_next_c

            # 输出: y_c = C @ x + D @ u
            if self.use_complex and hasattr(x_next_c, 'real'):
                y_c = C_c @ x_next_c.real  # [output_dim]
            else:
                y_c = C_c @ x_next_c
            y_components.append(y_c)

        # 平均所有通道 + D
        y_avg = np.mean(y_components, axis=0)  # [output_dim]
        y = y_avg + self.D @ u

        self._state = x_next
        return x_next, y

    def forward(self, u_seq: np.ndarray) -> np.ndarray:
        """序列前向传播

        Args:
            u_seq: [T, input_dim]

        Returns:
            y_seq: [T, output_dim]
        """
        T = u_seq.shape[0]
        x = np.zeros((self.n_channels, self.state_dim),
                     dtype=np.complex128 if self.use_complex else np.float64)

        y_seq = np.zeros((T, self.output_dim), dtype=np.float32)
        for t in range(T):
            _, y = self.forward_step(x, u_seq[t])
            y_seq[t] = y
            x = self._state.copy()

        return y_seq

    def forward_with_state(self, u_seq: np.ndarray
                           ) -> Tuple[np.ndarray, np.ndarray]:
        """序列前向 + 返回所有中间状态

        Returns:
            (y_seq, x_seq)
        """
        T = u_seq.shape[0]
        dt = np.complex128 if self.use_complex else np.float64
        x = np.zeros((self.n_channels, self.state_dim), dtype=dt)

        y_seq = np.zeros((T, self.output_dim), dtype=np.float32)
        x_seq = np.zeros((T, self.n_channels, self.state_dim), dtype=dt)

        for t in range(T):
            x_next, y = self.forward_step(x, u_seq[t])
            y_seq[t] = y
            x_seq[t] = x_next
            x = x_next

        return y_seq, x_seq

    def reset_state(self):
        self._state = None

    # ---------- 核方法 ----------

    def compute_kernel(self, T: int) -> np.ndarray:
        """计算 SSM 的脉冲响应

        Returns:
            kernel: [T, output_dim, input_dim]
        """
        kernel = np.zeros((T, self.output_dim, self.input_dim), dtype=np.float32)
        for i in range(self.input_dim):
            impulse = np.zeros((T, self.input_dim), dtype=np.float32)
            impulse[0, i] = 1.0
            response = self.forward(impulse)
            kernel[:, :, i] = response
        return kernel

    # ---------- 信息 ----------

    def get_info(self) -> dict:
        return {
            "input_dim": self.input_dim,
            "state_dim": self.state_dim,
            "output_dim": self.output_dim,
            "n_channels": self.n_channels,
            "use_selective": self.use_selective,
            "use_complex": self.use_complex,
            "A_dtype": str(self.A.dtype if self.use_complex else self.A_diag.dtype),
        }


# ==================== SSM 离散化工具 ====================

class SSMDiscretizer:
    """SSM 离散化工具集"""

    @staticmethod
    def zero_order_hold(A, B, delta):
        return discretize_zoh(A, B, delta)

    @staticmethod
    def bilinear_transform(A, B, delta):
        """双线性变换（Tustin）"""
        n = A.shape[-1]
        I = np.eye(n, dtype=A.dtype)
        A_bar = np.linalg.solve(I - A * delta / 2, I + A * delta / 2)
        B_bar = np.linalg.solve(I - A * delta / 2, B * delta)
        return A_bar, B_bar

    @staticmethod
    def euler_forward(A, B, delta):
        """前向欧拉离散化"""
        n = A.shape[-1]
        I = np.eye(n, dtype=A.dtype)
        return I + A * delta, B * delta


# ==================== 测试 ====================

def test_real_ssm():
    np.random.seed(42)
    ssm = Mamba3SSM(input_dim=4, state_dim=16, output_dim=4,
                    n_channels=2, use_complex=False, use_selective=False)
    u_seq = np.random.randn(20, 4).astype(np.float32)
    y_seq = ssm.forward(u_seq)
    assert y_seq.shape == (20, 4), f"输出形状错误: {y_seq.shape}"
    print(f"✅ 实数值 SSM (无选择): {u_seq.shape} → {y_seq.shape}")
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")


def test_complex_ssm():
    np.random.seed(42)
    ssm = Mamba3SSM(input_dim=4, state_dim=16, output_dim=4,
                    n_channels=3, use_complex=True, use_selective=False)
    u_seq = np.random.randn(20, 4).astype(np.float32)
    y_seq = ssm.forward(u_seq)
    assert y_seq.shape == (20, 4), f"输出形状错误: {y_seq.shape}"
    info = ssm.get_info()
    print(f"✅ 复数值 SSM (无选择): {u_seq.shape} → {y_seq.shape}")
    print(f"   A 类型: {info['A_dtype']}")
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")


def test_selective_ssm():
    np.random.seed(42)
    ssm = Mamba3SSM(input_dim=4, state_dim=8, output_dim=4,
                    n_channels=4, use_complex=True, use_selective=True)
    u_seq = np.random.randn(20, 4).astype(np.float32)
    y_seq = ssm.forward(u_seq)
    assert y_seq.shape == (20, 4), f"输出形状错误: {y_seq.shape}"

    deltas = np.array([ssm._compute_delta(u_seq[t]) for t in range(20)])
    delta_var = deltas.var()
    print(f"✅ 选择机制 SSM: {u_seq.shape} → {y_seq.shape}")
    print(f"   Δ 方差: {delta_var:.6f}")
    assert delta_var > 0, "选择机制没有产生变化"
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")


def test_mimo_multi_channel():
    np.random.seed(42)
    ssm = Mamba3SSM(input_dim=2, state_dim=8, output_dim=6,
                    n_channels=8, use_complex=True, use_selective=True)
    u_seq = np.random.randn(30, 2).astype(np.float32)
    y_seq = ssm.forward(u_seq)
    assert y_seq.shape == (30, 6), f"MIMO 输出形状错误: {y_seq.shape}"
    print("✅ MIMO 多通道: (2→8维状态×8通道→6维输出)")
    print("   输入: (30, 2), 输出: (30, 6)")
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")


def test_discretization_methods():
    np.random.seed(42)
    A = -np.eye(3, dtype=np.float64) * 0.5
    B = np.random.randn(3, 2).astype(np.float64)
    delta = 0.1

    for name, fn in [("ZOH", SSMDiscretizer.zero_order_hold),
                     ("Bilinear", SSMDiscretizer.bilinear_transform),
                     ("Euler", SSMDiscretizer.euler_forward)]:
        A_bar, B_bar = fn(A, B, delta)
        assert A_bar.shape == (3, 3), f"{name} A_bar 形状错误"
        assert B_bar.shape == (3, 2), f"{name} B_bar 形状错误"
        print(f"✅ {name}: A_bar[0,0]={A_bar[0,0]:.4f}, B_bar[0,0]={B_bar[0,0]:.4f}")

    # 复数值
    A_complex = np.diag(np.array([-0.5 + 2j, -0.5 - 2j, -1.0], dtype=np.complex128))
    A_bar, B_bar = SSMDiscretizer.zero_order_hold(A_complex, B.astype(np.complex128), delta)
    print(f"✅ 复数值 ZOH: |A_bar[0,0]|={abs(A_bar[0,0]):.4f}")


def test_kernel():
    np.random.seed(42)
    ssm = Mamba3SSM(input_dim=2, state_dim=8, output_dim=2,
                    n_channels=2, use_complex=True, use_selective=False)
    kernel = ssm.compute_kernel(T=10)
    assert kernel.shape == (10, 2, 2), f"核形状错误: {kernel.shape}"
    print(f"✅ 脉冲响应核: {kernel.shape}")


def test_varying_input_context():
    np.random.seed(42)
    ssm = Mamba3SSM(input_dim=4, state_dim=8, output_dim=2,
                    n_channels=4, use_complex=True, use_selective=True)
    u1 = np.random.randn(15, 4).astype(np.float32) * 0.5
    u2 = np.random.randn(15, 4).astype(np.float32) * 2.0
    y1 = ssm.forward(u1)
    y2 = ssm.forward(u2)
    diff = np.abs(y1 - y2).mean()
    print(f"✅ 输入上下文敏感: 两组输出差异均值={diff:.4f}")
    assert diff > 0, "选择机制没有产生上下文差异"


if __name__ == "__main__":
    print("=" * 55)
    print("Mamba-3 SSM — 复数值 + MIMO")
    print("=" * 55)

    test_real_ssm()
    print()
    test_complex_ssm()
    print()
    test_selective_ssm()
    print()
    test_mimo_multi_channel()
    print()
    test_discretization_methods()
    print()
    test_kernel()
    print()
    test_varying_input_context()

    print()
    print("=" * 55)
    print("✅ Mamba-3 SSM 全部测试通过")
    print("=" * 55)
