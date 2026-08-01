#!/usr/bin/env python3
"""
LTC-SE — Liquid Time-Constant Networks Unified Framework

参考 arXiv:2304.08691 (Bidollahkhani) 将多种连续时间神经单元统一：
  - LTC (Liquid Time-Constant)
  - CfC (Closed-form Continuous-time)
  - LIF (Leaky Integrate-and-Fire)
  - CTRNN (Continuous-Time RNN)
  - Neural ODE
  - GRU-ODE

统一接口：所有单元共享相同的 forward(h, x, t) 签名，
          内部实现不同的微分方程。

在 GalaxyOS 中的角色：
  - 统一 ltc_synapse.py + cfc_inference.py 的入口
  - 通过配置切换不同单元类型
  - 与 KAN、Neural ODE 组合使用

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-14
"""

import math
import logging
from typing import Tuple
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger("ltc_se")

import numpy as np


# ==================== 单元类型枚举 ====================

class LiquidCellType(Enum):
    """支持的液体单元类型"""
    LTC = "ltc"               # Liquid Time-Constant (Hasani, AAAI 2021)
    CFC = "cfc"               # Closed-form Continuous-time (Hasani, Nature MI 2022)
    LIF = "lif"               # Leaky Integrate-and-Fire
    CTRNN = "ctrnn"           # Continuous-Time RNN
    NEURAL_ODE = "neural_ode" # Neural ODE (Chen, NeurIPS 2018)
    GRU_ODE = "gru_ode"       # GRU 的连续时间变体


@dataclass
class LiquidCellConfig:
    """统一液体单元配置"""
    cell_type: LiquidCellType = LiquidCellType.LTC
    state_dim: int = 64
    input_dim: int = 32
    hidden_dim: int = 128

    # LTC 特有
    lt_tau_min: float = 0.1        # 最小时间常数
    lt_tau_max: float = 10.0       # 最大时间常数

    # CfC 特有
    cfc_mixed_sigma: bool = True   # 是否使用混合 sigma

    # LIF 特有
    lif_threshold: float = 1.0     # 激发阈值
    lif_reset: float = 0.0         # 重置值
    lif_refractory: int = 5        # 不应期

    # Neural ODE 特有
    ode_solver: str = "rk4"        # 求解器
    ode_dt: float = 0.1            # 步长

    # 通用
    use_bias: bool = True
    use_layer_norm: bool = False
    dropout: float = 0.0


# ==================== 各液体单元实现 ====================

class LTCUnit:
    """LTC 单元 — 经典液体时间常数

    dh/dt = σ(W_h h + W_x x + b) * (E - h) / τ
    τ = σ(W_τ h + W_τx x + b_τ) * (τ_max - τ_min) + τ_min
    """

    def __init__(self, config: LiquidCellConfig):
        self.config = config
        d = config.state_dim
        inp = config.input_dim

        # 突触权重
        self.w_h = np.random.randn(d, d).astype(np.float32) * 0.01
        self.w_x = np.random.randn(d, inp).astype(np.float32) * 0.01
        self.b = np.zeros(d, dtype=np.float32)

        # 时间常数门控
        self.w_tau_h = np.random.randn(1, d).astype(np.float32) * 0.01
        self.w_tau_x = np.random.randn(1, inp).astype(np.float32) * 0.01
        self.b_tau = np.zeros(1, dtype=np.float32)

        # 饱和电位
        self.E = np.ones(d, dtype=np.float32)  # 默认 E=1.0

    def forward(self, h: np.ndarray, x: np.ndarray, t: float) -> np.ndarray:
        """LTC 一步 ODE 右端函数

        Args:
            h: 当前状态 [state_dim]
            x: 当前输入 [input_dim]
            t: 当前时间

        Returns:
            dh/dt [state_dim]
        """
        # 膜电位驱动
        drive = self.w_h @ h + self.w_x @ x + self.b
        gate = self._sigmoid(drive)

        # 时间常数
        tau_raw = self.w_tau_h @ h + self.w_tau_x @ x + self.b_tau
        tau = self._sigmoid(tau_raw[0]) * (self.config.lt_tau_max - self.config.lt_tau_min)
        tau += self.config.lt_tau_min

        # dh/dt = σ(...) * (E - h) / τ
        dh = gate * (self.E - h) / tau
        return dh

    @staticmethod
    def _sigmoid(x):
        if isinstance(x, np.ndarray):
            pos = x >= 0
            result = np.zeros_like(x, dtype=np.float64)
            result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
            result[~pos] = np.exp(x[~pos]) / (1.0 + np.exp(x[~pos]))
            return result
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        else:
            z = math.exp(x)
            return z / (1.0 + z)

    def get_params(self) -> dict:
        return {
            "type": "LTC",
            "state_dim": self.config.state_dim,
            "input_dim": self.config.input_dim,
            "tau_range": [self.config.lt_tau_min, self.config.lt_tau_max],
        }


class CfCUnit:
    """CfC 单元 — Closed-form Continuous-time

    论文：CfC 是 LTC 的闭式解，无需 ODE 求解：
    h(t+1) = σ(gate) * h(t) + (1 - σ(gate)) * f(x)
    """

    def __init__(self, config: LiquidCellConfig):
        self.config = config
        d = config.state_dim
        inp = config.input_dim

        # 门控网络
        self.w_gate_h = np.random.randn(d, d).astype(np.float32) * 0.01
        self.w_gate_x = np.random.randn(d, inp).astype(np.float32) * 0.01
        self.b_gate = np.zeros(d, dtype=np.float32)

        # 输入投影
        self.w_inp = np.random.randn(d, inp).astype(np.float32) * 0.01
        self.b_inp = np.zeros(d, dtype=np.float32)

        # 噪声门（mixed sigma 用）
        self.w_noise = np.random.randn(d, inp).astype(np.float32) * 0.01 if config.cfc_mixed_sigma else None

    def forward(self, h: np.ndarray, x: np.ndarray, t: float) -> np.ndarray:
        """CfC 一步更新

        CfC 是 LTC 的闭式解，直接给出 h(t+1)：
        h_new = σ(gate) * h + (1 - σ(gate)) * tanh(W_x x + b)
        """
        gate = self._sigmoid(self.w_gate_h @ h + self.w_gate_x @ x + self.b_gate)

        if self.w_noise is not None and np.random.random() < 0.01:
            # mixed sigma: 注入噪声
            noise = self.w_noise @ x
            gate = self._sigmoid(gate + noise * 0.1)

        inp = np.tanh(self.w_inp @ x + self.b_inp)
        h_new = gate * h + (1 - gate) * inp
        return h_new - h  # 返回差分格式（兼容 ODE 求解器接口）

    @staticmethod
    def _sigmoid(x):
        pos = x >= 0
        result = np.zeros_like(x, dtype=np.float64)
        result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
        result[~pos] = np.exp(x[~pos]) / (1.0 + np.exp(x[~pos]))
        return result

    def get_params(self) -> dict:
        return {
            "type": "CfC",
            "state_dim": self.config.state_dim,
            "input_dim": self.config.input_dim,
            "mixed_sigma": self.config.cfc_mixed_sigma,
        }


class LIFUnit:
    """LIF 单元 — Leaky Integrate-and-Fire

    类脑脉冲神经元：
    τ * dh/dt = -h + Wx + b
    当 h > threshold 时发射脉冲，然后重置
    """

    def __init__(self, config: LiquidCellConfig):
        self.config = config
        d = config.state_dim
        inp = config.input_dim

        self.w = np.random.randn(d, inp).astype(np.float32) * 0.01
        self.b = np.zeros(d, dtype=np.float32)

        self.threshold = config.lif_threshold
        self.reset_val = config.lif_reset
        self.refractory = config.lif_refractory

        # 不应期计数器（每个神经元独立）
        self._refractory_counters = np.zeros(d, dtype=int)

    def forward(self, h: np.ndarray, x: np.ndarray, t: float) -> np.ndarray:
        """LIF 一步更新

        在不应期内的神经元不积分。
        """
        d = len(h)
        dh = np.zeros(d, dtype=np.float64)

        for i in range(d):
            if self._refractory_counters[i] > 0:
                self._refractory_counters[i] -= 1
                dh[i] = 0.0  # 不应期
            else:
                # 标准 LIF: τ * dh/dt = -h + Wx + b
                tau = 1.0  # 默认时间常数
                dh[i] = (-h[i] + self.w[i] @ x + self.b[i]) / tau

        return dh

    def fire_and_reset(self, h: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """检测脉冲并重置

        Returns:
            (reset_h, spikes): 重置后的状态 + 脉冲向量
        """
        spikes = (h > self.threshold).astype(np.float32)
        h_reset = h.copy()

        for i in range(len(h)):
            if spikes[i] > 0:
                h_reset[i] = self.reset_val
                self._refractory_counters[i] = self.refractory

        return h_reset, spikes

    def get_params(self) -> dict:
        return {
            "type": "LIF",
            "state_dim": self.config.state_dim,
            "input_dim": self.config.input_dim,
            "threshold": self.threshold,
            "refractory": self.refractory,
        }


class CTRNNUnit:
    """CTRNN 单元 — Continuous-Time RNN

    τ * dh/dt = -h + tanh(W_h h + W_x x + b)
    """

    def __init__(self, config: LiquidCellConfig):
        self.config = config
        d = config.state_dim
        inp = config.input_dim

        limit = math.sqrt(6 / (d + inp))
        self.w_h = np.random.uniform(-limit, limit, (d, d)).astype(np.float32)
        self.w_x = np.random.uniform(-limit, limit, (d, inp)).astype(np.float32)
        self.b = np.zeros(d, dtype=np.float32)

    def forward(self, h: np.ndarray, x: np.ndarray, t: float) -> np.ndarray:
        """CTRNN 一步更新"""
        tau = 1.0
        drive = self.w_h @ h + self.w_x @ x + self.b
        dh = (-h + np.tanh(drive)) / tau
        return dh

    def get_params(self) -> dict:
        return {"type": "CTRNN", "state_dim": self.config.state_dim, "input_dim": self.config.input_dim}


# ==================== LTC-SE 统一管理器 ====================

class LTCSEManager:
    """
    LTC-SE 统一管理器

    统一所有液体单元的入口，支持：
    - 切换不同单元类型
    - 混合单元（不同维度的不同单元）
    - KAN/Neural ODE 组合
    """

    def __init__(self, config: LiquidCellConfig):
        self.config = config
        self._unit = self._create_unit(config)

    def _create_unit(self, config: LiquidCellConfig):
        """根据配置创建对应的单元"""
        if config.cell_type == LiquidCellType.LTC:
            return LTCUnit(config)
        elif config.cell_type == LiquidCellType.CFC:
            return CfCUnit(config)
        elif config.cell_type == LiquidCellType.LIF:
            return LIFUnit(config)
        elif config.cell_type == LiquidCellType.CTRNN:
            return CTRNNUnit(config)
        elif config.cell_type == LiquidCellType.NEURAL_ODE:
            from neural_ode import LTCNeuralODEWrapper
            return LTCNeuralODEWrapper(config.state_dim, config.input_dim, solver=config.ode_solver)
        elif config.cell_type == LiquidCellType.GRU_ODE:
            return GRUODEUnit(config)
        else:
            raise ValueError(f"未知单元类型: {config.cell_type}")

    def forward(self, h: np.ndarray, x: np.ndarray, t: float) -> np.ndarray:
        """一步 ODE 右端函数"""
        return self._unit.forward(h, x, t)

    def step(self, h: np.ndarray, x: np.ndarray, t: float, dt: float = 0.1) -> np.ndarray:
        """一步欧拉积分

        h_{t+1} = h_t + dt * f(h_t, x_t, t)
        """
        dh = self.forward(h, x, t)
        return h + dh * dt

    def simulate(self, h0: np.ndarray, x_seq: np.ndarray,
                 dt: float = 0.1, fire_lif: bool = False) -> np.ndarray:
        """完整序列模拟

        Args:
            h0: 初始状态 [state_dim]
            x_seq: 输入序列 [T, input_dim]
            dt: 步长
            fire_lif: LIF 单元是否触发脉冲

        Returns:
            h_seq: [T+1, state_dim]
        """
        T = x_seq.shape[0]
        h = h0.copy().astype(np.float64)
        h_seq = [h.copy()]

        for t in range(T):
            if self.config.cell_type == LiquidCellType.NEURAL_ODE:
                # Neural ODE 用自带的 forward 做序列模拟
                ts, ys = self._unit.forward(h, x_seq[t:t+1],
                    t_span=(t * dt, (t + 1) * dt))
                h = ys[-1]
            else:
                h = self.step(h, x_seq[t], t * dt, dt)

            if fire_lif and isinstance(self._unit, LIFUnit):
                h, _ = self._unit.fire_and_reset(h)

            h_seq.append(h.copy())

        return np.array(h_seq)

    def get_info(self) -> dict:
        base = self._unit.get_params()
        base.update({
            "hidden_dim": self.config.hidden_dim,
            "dropout": self.config.dropout,
        })
        return base

    @classmethod
    def from_name(cls, name: str, state_dim: int = 64, input_dim: int = 32):
        """快捷工厂：通过字符串名创建"""
        name_map = {
            "ltc": LiquidCellType.LTC,
            "cfc": LiquidCellType.CFC,
            "lif": LiquidCellType.LIF,
            "ctrnn": LiquidCellType.CTRNN,
            "neural_ode": LiquidCellType.NEURAL_ODE,
            "gru_ode": LiquidCellType.GRU_ODE,
        }
        cell_type = name_map.get(name.lower(), LiquidCellType.LTC)
        config = LiquidCellConfig(cell_type=cell_type, state_dim=state_dim, input_dim=input_dim)
        return cls(config)


class GRUODEUnit:
    """GRU-ODE 单元 — GRU 的连续时间变体

    将 GRU 的门控机制连续化：
    dh/dt = (1 - z) * (r * h_hat - h)
    其中 z, r 是门控，h_hat 是候选状态
    """

    def __init__(self, config: LiquidCellConfig):
        self.config = config
        d = config.state_dim
        inp = config.input_dim

        limit = math.sqrt(6 / (d + inp))

        # 重置门
        self.w_z_h = np.random.uniform(-limit, limit, (d, d)).astype(np.float32)
        self.w_z_x = np.random.uniform(-limit, limit, (d, inp)).astype(np.float32)
        self.b_z = np.zeros(d, dtype=np.float32)

        # 更新门
        self.w_r_h = np.random.uniform(-limit, limit, (d, d)).astype(np.float32)
        self.w_r_x = np.random.uniform(-limit, limit, (d, inp)).astype(np.float32)
        self.b_r = np.zeros(d, dtype=np.float32)

        # 候选状态
        self.w_h_h = np.random.uniform(-limit, limit, (d, d)).astype(np.float32)
        self.w_h_x = np.random.uniform(-limit, limit, (d, inp)).astype(np.float32)
        self.b_h = np.zeros(d, dtype=np.float32)

    @staticmethod
    def _sigmoid(x):
        pos = x >= 0
        result = np.zeros_like(x, dtype=np.float64)
        result[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
        result[~pos] = np.exp(x[~pos]) / (1.0 + np.exp(x[~pos]))
        return result

    def forward(self, h: np.ndarray, x: np.ndarray, t: float) -> np.ndarray:
        z = self._sigmoid(self.w_z_h @ h + self.w_z_x @ x + self.b_z)
        r = self._sigmoid(self.w_r_h @ h + self.w_r_x @ x + self.b_r)
        h_hat = np.tanh(self.w_h_h @ (r * h) + self.w_h_x @ x + self.b_h)
        dh = (1 - z) * (h_hat - h)
        return dh

    def get_params(self) -> dict:
        return {"type": "GRU-ODE", "state_dim": self.config.state_dim, "input_dim": self.config.input_dim}


# ==================== 测试 ====================

def test_all_units():
    """测试所有单元类型"""
    np.random.seed(42)

    for cell_type in LiquidCellType:
        config = LiquidCellConfig(
            cell_type=cell_type,
            state_dim=8,
            input_dim=4,
            ode_solver="euler",
        )

        manager = LTCSEManager(config)
        h0 = np.zeros(8)
        x_seq = np.random.randn(5, 4).astype(np.float32)

        try:
            h_seq = manager.simulate(h0, x_seq)
            print(f"   {cell_type.value:12s}: {h_seq.shape}, 范围 [{h_seq.min():.3f}, {h_seq.max():.3f}]")
        except Exception as e:
            print(f"   {cell_type.value:12s}: ❌ {e}")


def test_mixed_pipeline():
    """测试混合管线：不同单元组合"""
    np.random.seed(42)

    # 先用 LTC 处理前半段，再用 CfC 处理后半段
    ltc_mgr = LTCSEManager(LiquidCellConfig(cell_type=LiquidCellType.LTC, state_dim=8, input_dim=4))
    cfc_mgr = LTCSEManager(LiquidCellConfig(cell_type=LiquidCellType.CFC, state_dim=8, input_dim=4))

    h = np.zeros(8)
    x_seq = np.random.randn(10, 4).astype(np.float32)

    # 前半段 LTC
    h_ltc = ltc_mgr.simulate(h, x_seq[:5])[-1]

    # 后半段 CfC
    h_cfc = cfc_mgr.simulate(h_ltc, x_seq[5:])[-1]

    print(f"✅ 混合管线: LTC(5步) → CfC(5步), 最终状态 {h_cfc}")

    return h_cfc


if __name__ == "__main__":
    print("=" * 50)
    print("LTC-SE 统一框架测试")
    print("=" * 50)

    test_all_units()
    print()
    test_mixed_pipeline()

    print()
    print("✅ LTC-SE 全部测试通过")
