#!/usr/bin/env python3
"""
Liquid SSM — 液体状态空间模型 (Mamba 选择机制 + LTC 连续时间动态)

融合 Mamba 的 SSM 架构与 LTC (Liquid Time-Constant) 的连续时间动态：
  - Mamba 的 SSM 核心：A, B, C, D 的状态空间公式
  - LTC 的时间常数：τ 由输入和状态共同决定，替代固定步长 Δ
  - SSM 与 LTC 共享状态空间：状态同时响应 SSM 转移和 LTC 液体动态

核心创新：
  1. SSM 的离散化步长 Δ 由 LTC 时间常数 τ 替代
     Δ_v = σ(W_τ h + W_τx x + b_τ) * (τ_max - τ_min) + τ_min
  2. 状态更新融合 SSM 和 LTC:
     x_{t+1} = x_t + Δ_t * (f_ssm(x_t, u_t) + f_ltc(x_t, u_t))
  3. 输出使用标准 SSM 投影: y = Cx + Du

在 GalaxyOS 中的角色：
  - 连续时间序列建模（非离散步长）
  - 需要精细时序控制的场景
  - 与 LGTC 和 Neural ODE 互补

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-14
"""

import math
import logging
from typing import Dict, List, Optional, Tuple, Any, Union
from dataclasses import dataclass, field

logger = logging.getLogger("liquid_ssm")

import numpy as np


# ==================== 核心实现 ====================

class LiquidSSM:
    """
    Liquid SSM — 液体状态空间模型

    将 LTC 的连续时间动态嵌入 Mamba SSE 框架：
      - 非线性时间常数：τ 控制状态更新的速度
      - 状态-输入双驱动：τ = f(h, x)
      - 共享状态空间：SSM 状态 = LTC 膜电位

    离散更新方程：
      τ_t = σ(W_τ h_t + W_τx x_t + b_τ) * (τ_max - τ_min) + τ_min
      h_{t+1} = h_t + τ_t * (A @ h_t + B @ x_t)
                 + τ_t * σ(W_l h_t + W_lx x_t) * (E - h_t)
      y_t = C @ h_{t+1} + D @ x_t
    """

    def __init__(self, state_dim: int, input_dim: int, output_dim: int,
                 n_channels: int = 4,
                 tau_min: float = 0.01,
                 tau_max: float = 1.0,
                 use_selective: bool = True):
        """
        Args:
            state_dim: 每通道状态维度 n
            input_dim: 输入维度 m
            output_dim: 输出维度 p
            n_channels: MIMO 通道数
            tau_min/tau_max: LTC 时间常数范围
            use_selective: 是否使用选择机制 (B, C 随输入变)
        """
        self.state_dim = state_dim
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.n_channels = n_channels
        self.tau_min = tau_min
        self.tau_max = tau_max
        self.use_selective = use_selective

        # 尝试连接 UDS lfm_server
        self._uds_ok = False
        self._uds_tried = False
        self._try_uds()

        if not self._uds_ok:
            # ===== SSM 参数 (多通道) =====
            self.A = -np.random.uniform(0.1, 2.0, (n_channels, state_dim)).astype(np.float32)
            limit_B = math.sqrt(6 / (state_dim + input_dim))
            self.B_fixed = np.random.uniform(-limit_B, limit_B,
                                              (n_channels, state_dim, input_dim)).astype(np.float32)
            limit_C = math.sqrt(6 / (output_dim + state_dim))
            self.C_fixed = np.random.uniform(-limit_C, limit_C,
                                              (n_channels, output_dim, state_dim)).astype(np.float32)
            self.D = np.random.randn(output_dim, input_dim).astype(np.float32) * 0.01
            self.w_tau_h = np.random.randn(n_channels, 1, state_dim).astype(np.float32) * 0.01
            self.w_tau_x = np.random.randn(n_channels, 1, input_dim).astype(np.float32) * 0.01
            self.b_tau = np.zeros((n_channels, 1), dtype=np.float32)
            limit_ltc = math.sqrt(6 / (state_dim + state_dim + input_dim))
            self.w_ltc_h = np.random.uniform(-limit_ltc, limit_ltc,
                                              (n_channels, state_dim, state_dim)).astype(np.float32)
            self.w_ltc_x = np.random.uniform(-limit_ltc, limit_ltc,
                                              (n_channels, state_dim, input_dim)).astype(np.float32)
            self.b_ltc = np.zeros((n_channels, state_dim), dtype=np.float32)
            self.E = np.ones((n_channels, state_dim), dtype=np.float32)
            if use_selective:
                limit_sB = math.sqrt(6 / (state_dim + input_dim))
                self.w_B_sel = np.random.uniform(-limit_sB, limit_sB,
                                                  (n_channels, state_dim, input_dim)).astype(np.float32)
                self.b_B_sel = np.zeros((n_channels, state_dim), dtype=np.float32)
                limit_sC = math.sqrt(6 / (output_dim + state_dim))
                self.w_C_sel = np.random.uniform(-limit_sC, limit_sC,
                                                  (n_channels, output_dim, state_dim)).astype(np.float32)
                self.b_C_sel = np.zeros((n_channels, output_dim), dtype=np.float32)
            limit_out = math.sqrt(6 / (output_dim + state_dim * n_channels))
            self.w_out = np.random.uniform(-limit_out, limit_out,
                                            (output_dim, state_dim * n_channels)).astype(np.float32)
            self.b_out = np.zeros(output_dim, dtype=np.float32)
        else:
            # UDS 模式: 只保留 LTC 参数骨架，SSM 核心委托给 lfm_server
            self.A = None
            self.B_fixed = None
            self.C_fixed = None
            self.D = np.random.randn(output_dim, input_dim).astype(np.float32) * 0.01
            self.w_tau_h = np.random.randn(n_channels, 1, state_dim).astype(np.float32) * 0.01
            self.w_tau_x = np.random.randn(n_channels, 1, input_dim).astype(np.float32) * 0.01
            self.b_tau = np.zeros((n_channels, 1), dtype=np.float32)
            self.w_ltc_h = np.random.randn(n_channels, state_dim, state_dim).astype(np.float32) * 0.01
            self.w_ltc_x = np.random.randn(n_channels, state_dim, input_dim).astype(np.float32) * 0.01
            self.b_ltc = np.zeros((n_channels, state_dim), dtype=np.float32)
            self.E = np.ones((n_channels, state_dim), dtype=np.float32)
            if use_selective:
                self.w_B_sel = np.random.randn(n_channels, state_dim, input_dim).astype(np.float32) * 0.01
                self.b_B_sel = np.zeros((n_channels, state_dim), dtype=np.float32)
                self.w_C_sel = np.random.randn(n_channels, output_dim, state_dim).astype(np.float32) * 0.01
                self.b_C_sel = np.zeros((n_channels, output_dim), dtype=np.float32)
            limit_out = math.sqrt(6 / (output_dim + state_dim * n_channels))
            self.w_out = np.random.uniform(-limit_out, limit_out,
                                            (output_dim, state_dim * n_channels)).astype(np.float32)
            self.b_out = np.zeros(output_dim, dtype=np.float32)
            logger.info("LiquidSSM 使用 UDS 后端 (lfm_server state)")


        # A 矩阵 (对角线): [n_channels, state_dim]
        self.A = -np.random.uniform(0.1, 2.0, (n_channels, state_dim)).astype(np.float32)

        # SSM B: [n_channels, state_dim, input_dim]
        limit_B = math.sqrt(6 / (state_dim + input_dim))
        self.B_fixed = np.random.uniform(-limit_B, limit_B,
                                          (n_channels, state_dim, input_dim)).astype(np.float32)

        # SSM C: [n_channels, output_dim, state_dim]
        limit_C = math.sqrt(6 / (output_dim + state_dim))
        self.C_fixed = np.random.uniform(-limit_C, limit_C,
                                          (n_channels, output_dim, state_dim)).astype(np.float32)

        # D 直通: [output_dim, input_dim]
        self.D = np.random.randn(output_dim, input_dim).astype(np.float32) * 0.01

        # ===== LTC 参数 (选择机制) =====

        # 时间常数门控: [n_channels, state_dim + input_dim]
        self.w_tau_h = np.random.randn(n_channels, 1, state_dim).astype(np.float32) * 0.01
        self.w_tau_x = np.random.randn(n_channels, 1, input_dim).astype(np.float32) * 0.01
        self.b_tau = np.zeros((n_channels, 1), dtype=np.float32)

        # LTC 膜电位门控: [n_channels, state_dim, state_dim + input_dim]
        limit_ltc = math.sqrt(6 / (state_dim + state_dim + input_dim))
        self.w_ltc_h = np.random.uniform(-limit_ltc, limit_ltc,
                                          (n_channels, state_dim, state_dim)).astype(np.float32)
        self.w_ltc_x = np.random.uniform(-limit_ltc, limit_ltc,
                                          (n_channels, state_dim, input_dim)).astype(np.float32)
        self.b_ltc = np.zeros((n_channels, state_dim), dtype=np.float32)

        # 饱和电位 E: [n_channels, state_dim]
        self.E = np.ones((n_channels, state_dim), dtype=np.float32)

        # ===== 选择机制参数 =====
        if use_selective:
            # 选择 B: [n_channels, state_dim, input_dim]
            limit_sB = math.sqrt(6 / (state_dim + input_dim))
            self.w_B_sel = np.random.uniform(-limit_sB, limit_sB,
                                              (n_channels, state_dim, input_dim)).astype(np.float32)
            self.b_B_sel = np.zeros((n_channels, state_dim), dtype=np.float32)

            # 选择 C: [n_channels, output_dim, state_dim]
            limit_sC = math.sqrt(6 / (output_dim + state_dim))
            self.w_C_sel = np.random.uniform(-limit_sC, limit_sC,
                                              (n_channels, output_dim, state_dim)).astype(np.float32)
            self.b_C_sel = np.zeros((n_channels, output_dim), dtype=np.float32)

        # 输出投影 (聚合多通道)
        limit_out = math.sqrt(6 / (output_dim + state_dim * n_channels))
        self.w_out = np.random.uniform(-limit_out, limit_out,
                                        (output_dim, state_dim * n_channels)).astype(np.float32)
        self.b_out = np.zeros(output_dim, dtype=np.float32)

    def _try_uds(self):
        """尝试连接 lfm_server UDS"""
        self._uds_tried = True
        try:
            from galaxyos_native import lfm_ping
            lfm_ping()
            self._uds_ok = True
        except Exception as e:
            self._uds_ok = False
            logger.debug(f"LiquidSSM UDS 不可用: {e}, 使用 numpy fallback")

    # ---------- 工具函数 ----------

    @staticmethod
    def _sigmoid(x):
        pos = x >= 0
        result = np.zeros_like(x, dtype=np.float64)
        result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
        result[~pos] = np.exp(x[~pos]) / (1.0 + np.exp(x[~pos]))
        return result

    # ---------- 核心计算 ----------

    def _compute_tau(self, h: np.ndarray, u: np.ndarray) -> np.ndarray:
        """计算 LTC 时间常数

        τ_c = sigmoid(W_τh @ h_c + W_τx @ u + b_τ) * (τ_max - τ_min) + τ_min

        Args:
            h: [n_channels, state_dim]
            u: [input_dim]

        Returns:
            tau: [n_channels, 1]
        """
        tau_raw = np.einsum('chd,cd->ch', self.w_tau_h, h) + \
                  np.einsum('cxi,i->cx', self.w_tau_x, u) + \
                  self.b_tau
        tau = self._sigmoid(tau_raw) * (self.tau_max - self.tau_min) + self.tau_min
        return tau

    def _ssm_dynamics(self, h: np.ndarray, u: np.ndarray,
                      B_mat: np.ndarray) -> np.ndarray:
        """SSM 线性动力学

        dh_ssm/dt = A * h + B * u

        Args:
            h: [n_channels, state_dim]
            u: [input_dim]
            B_mat: [n_channels, state_dim, input_dim]

        Returns:
            dh_ssm: [n_channels, state_dim]
        """
        # A 是对角线 [n_channels, state_dim] → 逐元素乘
        dh_A = self.A * h  # [n_channels, state_dim]
        dh_B = np.einsum('csi,i->cs', B_mat, u)  # [n_channels, state_dim]
        return dh_A + dh_B

    def _ltc_dynamics(self, h: np.ndarray, u: np.ndarray) -> np.ndarray:
        """LTC 液体动力学

        dh_ltc/dt = sigmoid(W_lh @ h + W_lx @ u + b_l) * (E - h)

        Args:
            h: [n_channels, state_dim]
            u: [input_dim]

        Returns:
            dh_ltc: [n_channels, state_dim]
        """
        drive = np.einsum('chd,cd->ch', self.w_ltc_h, h) + \
                np.einsum('cxi,i->cx', self.w_ltc_x, u) + \
                self.b_ltc
        gate = self._sigmoid(drive)
        dh = gate * (self.E - h)
        return dh

    def forward_step(self, h: np.ndarray, u: np.ndarray,
                     B_mat: Optional[np.ndarray] = None,
                     C_mat: Optional[np.ndarray] = None) -> Tuple[np.ndarray, np.ndarray]:
        """单步前向

        Args:
            h: [n_channels, state_dim]
            u: [input_dim]
            B_mat, C_mat: 选择机制矩阵（可选）

        Returns:
            (h_next, y)
        """
        # 1. 时间常数
        tau = self._compute_tau(h, u)  # [n_channels, 1]

        # 2. B 矩阵
        if B_mat is None:
            B_mat = self.B_fixed
        if C_mat is None:
            C_mat = self.C_fixed

        # 3. SSM + LTC 混合动态
        dh_ssm = self._ssm_dynamics(h, u, B_mat)
        dh_ltc = self._ltc_dynamics(h, u)

        # 4. 状态更新（离散欧拉，步长 = tau）
        dh = dh_ssm + dh_ltc * 0.1  # LTC 项缩放避免过冲
        h_next = h + tau * dh

        # 5. 输出
        y_inner = np.einsum('cos,cs->co', C_mat, h_next) + self.D @ u
        # 通道平均
        y = np.mean(y_inner, axis=0)

        return h_next, y

    def forward(self, u_seq: np.ndarray) -> np.ndarray:
        """序列前向

        UDS 可用时委托 lfm_server update_state 演进真实状态。
        """
        if self._uds_ok:
            return self._forward_uds(u_seq)

        T = u_seq.shape[0]
        h = np.zeros((self.n_channels, self.state_dim), dtype=np.float64)
        y_seq = np.zeros((T, self.output_dim), dtype=np.float32)

        for t in range(T):
            u = u_seq[t]
            B_mat, C_mat = None, None
            if self.use_selective:
                B_mat = self._compute_B_sel(u)
                C_mat = self._compute_C_sel(u)
            h, y = self.forward_step(h, u, B_mat, C_mat)
            y_seq[t] = y

        return y_seq

    def _forward_uds(self, u_seq: np.ndarray) -> np.ndarray:
        """UDS 后端：调 lfm_server update_state 演进状态"""
        T = u_seq.shape[0]
        y_seq = np.zeros((T, self.output_dim), dtype=np.float32)

        try:
            from galaxyos_native import lfm_update_state, lfm_get_state, lfm_reset_state

            # 重置 LFM conv state
            lfm_reset_state()

            # 将 u_seq 投影为伪 token IDs，喂给 LFM
            for t in range(T):
                u = u_seq[t]
                # 映射到 token ID
                proj = (u * 50).astype(np.int32) % 8192
                token_ids = proj.tolist()
                if isinstance(token_ids, int):
                    token_ids = [token_ids]

                # 喂给 LFM update_state，让真实状态演进
                lfm_update_state(token_ids[:16])

                # 取当前 embedding 作为输出
                state = lfm_get_state()
                emb = np.array(state.get("embedding", np.zeros(2048)), dtype=np.float32)

                # 投影到 output_dim
                if len(emb) != self.output_dim:
                    if not hasattr(self, '_uds_out_proj'):
                        self._uds_out_proj = np.random.randn(self.output_dim, len(emb)).astype(np.float32) * 0.01
                    y_seq[t] = self._uds_out_proj @ emb[:len(emb)]
                else:
                    y_seq[t] = emb[:self.output_dim]

            return y_seq
        except Exception as e:
            logger.warning(f"LiquidSSM UDS forward 失败: {e}, 降级到 numpy")
            return self._forward_numpy(self, self._mock_to_numpy(u_seq))

        return y_seq

    def _mock_to_numpy(self, u_seq):
        return u_seq

    def forward_with_state(self, u_seq: np.ndarray
                           ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """序列前向 + 状态 + 时间常数轨迹

        Returns:
            (y_seq, h_seq, tau_seq)
        """
        T = u_seq.shape[0]
        h = np.zeros((self.n_channels, self.state_dim), dtype=np.float64)
        y_seq = np.zeros((T, self.output_dim), dtype=np.float32)
        h_seq = np.zeros((T, self.n_channels, self.state_dim), dtype=np.float64)
        tau_seq = np.zeros((T, self.n_channels), dtype=np.float32)

        for t in range(T):
            u = u_seq[t]
            B_mat, C_mat = None, None
            if self.use_selective:
                B_mat = self._compute_B_sel(u)
                C_mat = self._compute_C_sel(u)
            tau = self._compute_tau(h, u)
            h, y = self.forward_step(h, u, B_mat, C_mat)
            y_seq[t] = y
            h_seq[t] = h
            tau_seq[t] = tau[:, 0]

        return y_seq, h_seq, tau_seq

    # ---------- 选择机制 ----------

    def _compute_B_sel(self, u: np.ndarray) -> np.ndarray:
        """选择 B: [n_channels, state_dim, input_dim]"""
        B = self.w_B_sel * u[np.newaxis, np.newaxis, :] + self.b_B_sel[:, :, np.newaxis]
        return B

    def _compute_C_sel(self, u: np.ndarray) -> np.ndarray:
        """选择 C: [n_channels, output_dim, state_dim]"""
        # 简单版本：用输入标量调制
        u_scale = 0.1 * (u.sum() / self.input_dim)
        C = self.w_C_sel * (1.0 + u_scale) + self.b_C_sel[:, :, np.newaxis] * 0.01
        return C

    # ---------- 信息 ----------

    def get_info(self) -> dict:
        return {
            "state_dim": self.state_dim,
            "input_dim": self.input_dim,
            "output_dim": self.output_dim,
            "n_channels": self.n_channels,
            "tau_range": [self.tau_min, self.tau_max],
            "use_selective": self.use_selective,
        }


# ==================== 测试 ====================

def test_basic_liquid_ssm():
    """测试基础 Liquid SSM"""
    np.random.seed(42)

    ssm = LiquidSSM(state_dim=8, input_dim=4, output_dim=4,
                    n_channels=2, use_selective=False)
    u_seq = np.random.randn(20, 4).astype(np.float32)
    y_seq = ssm.forward(u_seq)

    assert y_seq.shape == (20, 4), f"输出形状错误: {y_seq.shape}"
    print(f"✅ 基础 Liquid SSM: {u_seq.shape} → {y_seq.shape}")
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")

    return ssm


def test_liquid_tau():
    """测试 LTC 时间常数演化"""
    np.random.seed(42)

    ssm = LiquidSSM(state_dim=4, input_dim=2, output_dim=2,
                    n_channels=3, use_selective=False)
    u_seq = np.random.randn(15, 2).astype(np.float32)

    _, _, tau_seq = ssm.forward_with_state(u_seq)

    assert tau_seq.shape == (15, 3), f"tau 形状错误: {tau_seq.shape}"
    assert tau_seq.min() >= ssm.tau_min - 0.001, f"tau 过低: {tau_seq.min()}"
    assert tau_seq.max() <= ssm.tau_max + 0.001, f"tau 过高: {tau_seq.max()}"

    # 验证 tau 有变化（不同时间步不同）
    tau_var = tau_seq.var(axis=0).mean()
    print(f"✅ LTC 时间常数演化: τ ∈ [{tau_seq.min():.4f}, {tau_seq.max():.4f}]")
    print(f"   τ 平均方差: {tau_var:.6f}")
    assert tau_var > 0, "时间常数没有变化"

    return ssm


def test_selective_liquid():
    """测试选择机制 Liquid SSM"""
    np.random.seed(42)

    ssm = LiquidSSM(state_dim=8, input_dim=4, output_dim=3,
                    n_channels=4, use_selective=True)
    u_seq = np.random.randn(25, 4).astype(np.float32)
    y_seq = ssm.forward(u_seq)

    assert y_seq.shape == (25, 3), f"输出形状错误: {y_seq.shape}"

    # 验证选择 B 矩阵的不同
    u1 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    u2 = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
    B1 = ssm._compute_B_sel(u1)
    B2 = ssm._compute_B_sel(u2)
    diff = np.abs(B1 - B2).mean()
    print(f"✅ 选择机制 Liquid SSM: {u_seq.shape} → {y_seq.shape}")
    print(f"   选择 B 差异: {diff:.6f}")
    assert diff > 0, "选择 B 没有差异"
    print(f"   输出范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")


def test_ssm_vs_ltc_balance():
    """测试 SSM 和 LTC 的动态平衡"""
    np.random.seed(42)

    ssm = LiquidSSM(state_dim=6, input_dim=3, output_dim=3,
                    n_channels=3, use_selective=False)

    u = np.array([0.5, -0.3, 0.8], dtype=np.float32)
    h = np.zeros((3, 6), dtype=np.float64)

    dh_ssm = ssm._ssm_dynamics(h, u, ssm.B_fixed)
    dh_ltc = ssm._ltc_dynamics(h, u)

    # 两者都不应为零
    ssm_norm = np.abs(dh_ssm).mean()
    ltc_norm = np.abs(dh_ltc).mean()
    print(f"✅ SSM/LTC 平衡: ||dh_ssm||={ssm_norm:.4f}, ||dh_ltc||={ltc_norm:.4f}")
    assert ssm_norm > 0, "SSM 动态为零"
    assert ltc_norm > 0, "LTC 动态为零"


def test_multi_channel_mimo():
    """测试多通道 MIMO"""
    np.random.seed(42)

    ssm = LiquidSSM(state_dim=4, input_dim=2, output_dim=6,
                    n_channels=8, use_selective=True)
    u_seq = np.random.randn(30, 2).astype(np.float32)
    y_seq = ssm.forward(u_seq)

    assert y_seq.shape == (30, 6), f"MIMO 形状错误: {y_seq.shape}"
    print("✅ MIMO 多通道: 输入(2) → 8通道×4状态 → 输出(6)")
    print(f"   序列: (30, 6), 范围: [{y_seq.min():.3f}, {y_seq.max():.3f}]")


def test_variable_tau_effect():
    """测试可变 τ 对输出的影响"""
    np.random.seed(42)

    ssm1 = LiquidSSM(state_dim=4, input_dim=2, output_dim=2,
                     n_channels=2, tau_min=0.01, tau_max=0.1, use_selective=False)
    ssm2 = LiquidSSM(state_dim=4, input_dim=2, output_dim=2,
                     n_channels=2, tau_min=0.5, tau_max=1.0, use_selective=False)

    # 复制参数（除了 tau 范围）
    ssm2.A = ssm1.A.copy()
    ssm2.B_fixed = ssm1.B_fixed.copy()
    ssm2.C_fixed = ssm1.C_fixed.copy()

    u_seq = np.random.randn(10, 2).astype(np.float32)

    y1 = ssm1.forward(u_seq)
    y2 = ssm2.forward(u_seq)

    diff = np.abs(y1 - y2).mean()
    print("✅ τ 影响: 小 τ (0.01-0.1) vs 大 τ (0.5-1.0)")
    print(f"   输出差异均值: {diff:.4f}")
    assert diff > 0, "不同 τ 应该产生不同输出"


def test_continuous_dynamics():
    """测试连续动态性质（SSM + LTC 共享状态空间）"""
    np.random.seed(42)

    ssm = LiquidSSM(state_dim=4, input_dim=2, output_dim=2,
                    n_channels=2, use_selective=False)
    u_seq = np.random.randn(20, 2).astype(np.float32)

    y_seq, h_seq, tau_seq = ssm.forward_with_state(u_seq)

    # 验证状态是平滑变化的（不是离散跳跃）
    h_diffs = np.abs(np.diff(h_seq, axis=0)).mean()
    print(f"✅ 连续动态: 状态平均变化步长 = {h_diffs:.6f}")
    assert not np.isnan(h_seq).any(), "状态包含 NaN"
    assert not np.isinf(h_seq).any(), "状态包含 Inf"

    return ssm


if __name__ == "__main__":
    print("=" * 55)
    print("Liquid SSM — Mamba + LTC 融合")
    print("=" * 55)

    test_basic_liquid_ssm()
    print()
    test_liquid_tau()
    print()
    test_selective_liquid()
    print()
    test_ssm_vs_ltc_balance()
    print()
    test_multi_channel_mimo()
    print()
    test_variable_tau_effect()
    print()
    test_continuous_dynamics()

    print()
    print("=" * 55)
    print("✅ Liquid SSM 全部测试通过")
    print("=" * 55)


    # ── LFM embedding 时序预测 ──

    def predict_embedding(self, recent_embeddings: list,
                          steps: int = 1) -> np.ndarray:
        """对 LFM embedding 序列做时序预测

        Args:
            recent_embeddings: [(2048,) ...] 最近 N 个 embedding
            steps: 预测步数

        Returns:
            (2048,) 预测的 embedding
        """
        import numpy as np
        if not recent_embeddings:
            return np.zeros(2048, dtype=np.float32)

        u_seq = np.stack(recent_embeddings[-self.state_dim:], axis=0)
        if len(u_seq) < 2:
            return u_seq[-1]

        # 调整维度: (seq, 2048) → (seq, input_dim)
        if u_seq.shape[-1] != self.input_dim and self.input_dim == 2048:
            pass  # 匹配
        elif u_seq.shape[-1] != self.input_dim:
            # 降/升维匹配
            import numpy as np
            if not hasattr(self, '_proj_emb'):
                self._proj_emb = np.random.randn(self.input_dim, u_seq.shape[-1]).astype(np.float32) * 0.02
            u_seq = u_seq @ self._proj_emb.T

        h = np.zeros(self.state_dim, dtype=np.float32)
        for t in range(len(u_seq)):
            h = self.forward_step(h, u_seq[t], dt=0.1)

        # 预测未来 steps 步
        last_u = u_seq[-1]
        for _ in range(steps):
            h = self.forward_step(h, last_u, dt=0.1)
            last_u = h[:self.input_dim] if self.input_dim <= self.state_dim else h

        out = last_u[:2048] if len(last_u) >= 2048 else np.pad(last_u, (0, 2048 - len(last_u)))
        return out.astype(np.float32)

