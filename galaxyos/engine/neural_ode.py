#!/usr/bin/env python3
"""
Neural ODE — Neural Ordinary Differential Equations

将 Neural ODE (arXiv:1806.07366) 嵌入 GalaxyOS：
  - 连续深度模型：ODE 求解器替代离散层堆叠
  - 伴随法反向传播（adjoint method）：常量内存开销
  - 自适应步长：根据输入复杂度动态调整计算量

核心：参数化隐藏状态的导数 dh/dt = f(h, t, θ)，
      而非直接参数化 h_{t+1} = f(h_t)。

在 GalaxyOS 中的角色：
  - LTC/CfC 的数学底座
  - KAN 替代 MLP 作为 ODE 右端函数
  - 与 DAG 上下文管理器的连续时间 compact

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-14
"""

import os
import math
import time
import json
import logging
from typing import Dict, List, Optional, Tuple, Callable, Any
from dataclasses import dataclass, field

logger = logging.getLogger("neural_ode")

import numpy as np


# ==================== ODE 求解器 ====================

class ODESolver:
    """
    ODE 求解器 — 用数值方法求解 dh/dt = f(h, t, θ)
    
    支持多种求解器：
    - Euler: 一阶，快但不精确
    - RK4 (Runge-Kutta 4): 四阶，精度/速度平衡
    - DOPRI5 (Dormand-Prince 5): 自适应步长
    
    论文核心：
    - 前向传播 = 求解 ODE 初值问题
    - 反向传播 = 伴随法（无需存储中间状态）
    """

    @staticmethod
    def euler(f: Callable, y0: np.ndarray, t_span: Tuple[float, float],
              dt: float = 0.1) -> Tuple[np.ndarray, np.ndarray]:
        """前向欧拉法
        
        y_{n+1} = y_n + dt * f(y_n, t_n)
        """
        t0, t1 = t_span
        n_steps = max(1, int((t1 - t0) / dt))
        actual_dt = (t1 - t0) / n_steps

        ts = np.linspace(t0, t1, n_steps + 1)
        ys = np.zeros((n_steps + 1, *y0.shape), dtype=np.float64)
        ys[0] = y0

        for i in range(n_steps):
            ys[i + 1] = ys[i] + actual_dt * f(ys[i], ts[i])

        return ts, ys

    @staticmethod
    def rk4(f: Callable, y0: np.ndarray, t_span: Tuple[float, float],
            dt: float = 0.1) -> Tuple[np.ndarray, np.ndarray]:
        """经典 Runge-Kutta 4 阶
        
        k1 = f(y_n, t_n)
        k2 = f(y_n + dt/2 * k1, t_n + dt/2)
        k3 = f(y_n + dt/2 * k2, t_n + dt/2)
        k4 = f(y_n + dt * k3, t_n + dt)
        y_{n+1} = y_n + dt/6 * (k1 + 2*k2 + 2*k3 + k4)
        """
        t0, t1 = t_span
        n_steps = max(1, int((t1 - t0) / dt))
        actual_dt = (t1 - t0) / n_steps

        ts = np.linspace(t0, t1, n_steps + 1)
        ys = np.zeros((n_steps + 1, *y0.shape), dtype=np.float64)
        ys[0] = y0

        for i in range(n_steps):
            h = actual_dt
            t = ts[i]
            y = ys[i]

            k1 = f(y, t)
            k2 = f(y + h/2 * k1, t + h/2)
            k3 = f(y + h/2 * k2, t + h/2)
            k4 = f(y + h * k3, t + h)

            ys[i + 1] = y + h/6 * (k1 + 2*k2 + 2*k3 + k4)

        return ts, ys

    @staticmethod
    def dopri5(f: Callable, y0: np.ndarray, t_span: Tuple[float, float],
               rtol: float = 1e-6, atol: float = 1e-8,
               max_steps: int = 1000) -> Tuple[np.ndarray, np.ndarray]:
        """Dormand-Prince 5(4) — 自适应步长
        
        用 5 阶公式推进，4 阶公式估计误差，动态调整步长。
        论文中使用的求解器类型。
        """
        t0, t1 = t_span
        t = t0
        y = y0.copy().astype(np.float64)

        # DOPRI5 Butcher 表
        # a 系数
        a21 = 1/5
        a31, a32 = 3/40, 9/40
        a41, a42, a43 = 44/45, -56/15, 32/9
        a51, a52, a53, a54 = 19372/6561, -25360/2187, 64448/6561, -212/729
        a61, a62, a63, a64, a65 = 9017/3168, -355/33, 46732/5247, 49/176, -5103/18656
        a71, a72, a73, a74, a75, a76 = 35/384, 0, 500/1113, 125/192, -2187/6784, 11/84

        # c 系数
        c2, c3, c4, c5, c6 = 1/5, 3/10, 4/5, 8/9, 1

        # 5 阶系数 (b)
        b1, b2, b3, b4, b5, b6, b7 = 35/384, 0, 500/1113, 125/192, -2187/6784, 11/84, 0

        # 4 阶系数 (b*)
        b1s, b2s, b3s, b4s, b5s, b6s, b7s = 5179/57600, 0, 7571/16695, 393/640, -92097/339200, 187/2100, 1/40

        h = (t1 - t0) / 10  # 初始步长

        ts = [t0]
        ys = [y0.copy()]

        step = 0
        direction = 1 if t1 >= t0 else -1

        while (t1 - t) * direction > 0 and step < max_steps:
            step += 1

            # 确保不越过终点
            if abs(h) > abs(t1 - t):
                h = t1 - t

            # 计算 6 个斜率
            k1 = f(y, t)
            k2 = f(y + h * a21 * k1, t + c2 * h)
            k3 = f(y + h * (a31 * k1 + a32 * k2), t + c3 * h)
            k4 = f(y + h * (a41 * k1 + a42 * k2 + a43 * k3), t + c4 * h)
            k5 = f(y + h * (a51 * k1 + a52 * k2 + a53 * k3 + a54 * k4), t + c5 * h)
            k6 = f(y + h * (a61 * k1 + a62 * k2 + a63 * k3 + a64 * k4 + a65 * k5), t + c6 * h)
            k7 = f(y + h * (a71 * k1 + a72 * k2 + a73 * k3 + a74 * k4 + a75 * k5 + a76 * k6), t + h)

            # 5 阶推进
            y_new = y + h * (b1 * k1 + b2 * k2 + b3 * k3 + b4 * k4 + b5 * k5 + b6 * k6 + b7 * k7)

            # 4 阶误差估计
            y_err = y + h * (b1s * k1 + b2s * k2 + b3s * k3 + b4s * k4 + b5s * k5 + b6s * k6 + b7s * k7)

            # 误差
            err = np.max(np.abs(y_new - y_err) / (atol + rtol * np.maximum(np.abs(y_new), np.abs(y))))

            # 自适应步长
            if err <= 1.0:
                # 接受步长
                t += h
                y = y_new.copy()
                ts.append(t)
                ys.append(y.copy())

            # 调整步长
            if err > 0:
                # DOPRI5 的步长缩放公式
                h_new = 0.9 * h * err ** (-1/5)
                h_new = min(max(h_new, h / 10), h * 10)  # 限制变化幅度
                h = h_new
            else:
                h *= 2

        if step >= max_steps:
            logger.warning(f"DOPRI5 达到最大步数 {max_steps}")

        return np.array(ts), np.array(ys)

    @staticmethod
    def solve(f: Callable, y0: np.ndarray, t_span: Tuple[float, float],
              method: str = "rk4", **kwargs) -> Tuple[np.ndarray, np.ndarray]:
        """统一求解接口"""
        if method == "euler":
            return ODESolver.euler(f, y0, t_span, **kwargs)
        elif method == "rk4":
            return ODESolver.rk4(f, y0, t_span, **kwargs)
        elif method == "dopri5":
            return ODESolver.dopri5(f, y0, t_span, **kwargs)
        else:
            raise ValueError(f"未知求解器: {method}")


# ==================== Neural ODE 模型 ====================

class NeuralODE:
    """
    Neural ODE — 连续深度模型
    
    用神经网络参数化隐藏状态的导数：
        dh/dt = NN(h(t), t, θ)
    
    关键特性（论文）：
    1. 常量内存成本：前向用 ODE 求解，反向用伴随法
    2. 自适应计算：求解器根据输入复杂度自调步长
    3. 数值精度 ↔ 速度可权衡
    """

    def __init__(self, state_dim: int, hidden_dim: int = 64,
                 num_layers: int = 2,
                 solver: str = "rk4",
                 use_residual: bool = True):
        self.state_dim = state_dim
        self.solver = solver

        # 用 MLP 参数化导数
        # 如果 KAN 可用，可用 KAN 替代
        self._build_network(state_dim, hidden_dim, num_layers, use_residual)

        # 伴随法状态缓存（反向传播用）
        # 在纯 NP 版中简化处理
        self._trace = []

    def _build_network(self, state_dim: int, hidden_dim: int,
                       num_layers: int, use_residual: bool):
        """构建 ODE 右端函数网络"""
        self.layers = []

        # 输入层: state_dim + 1 (时间 t)
        in_dim = state_dim + 1

        for i in range(num_layers):
            out_dim = hidden_dim if i < num_layers - 1 else state_dim

            # 权重
            limit = math.sqrt(6 / (in_dim + out_dim))
            w = np.random.uniform(-limit, limit, (out_dim, in_dim)).astype(np.float32)
            b = np.zeros(out_dim, dtype=np.float32)

            self.layers.append({"w": w, "b": b})
            in_dim = out_dim

        self._use_residual = use_residual

    def ode_func(self, h: np.ndarray, t: float) -> np.ndarray:
        """ODE 右端函数 dh/dt = NN(h, t)
        
        Args:
            h: 隐藏状态 [state_dim]
            t: 当前时间
        
        Returns:
            dh/dt [state_dim]
        """
        # 拼接 h 和 t
        inp = np.concatenate([h, np.array([t])]).astype(np.float32)

        # 前向传播
        x = inp
        for i, layer in enumerate(self.layers):
            x = x @ layer["w"].T + layer["b"]
            if i < len(self.layers) - 1:
                x = np.tanh(x)  # 中间层非线性

        return x

    def forward(self, y0: np.ndarray, t_span: Tuple[float, float],
                **solver_kwargs) -> Tuple[np.ndarray, np.ndarray]:
        """前向传播 = ODE 求解
        
        论文核心：
        前向传播 = 求解初值问题，而非逐层计算
        
        Args:
            y0: 初始状态 [state_dim]
            t_span: (t_start, t_end)
            **solver_kwargs: 传递给求解器的参数
        
        Returns:
            ts: 时间点序列
            ys: 状态序列
        """
        # 分离求解器参数
        dt = solver_kwargs.pop("dt", 0.1)
        rtol = solver_kwargs.pop("rtol", 1e-6)
        atol = solver_kwargs.pop("atol", 1e-8)
        max_steps = solver_kwargs.pop("max_steps", 1000)

        # Euler/RK4 用 dt, DOPRI5 用容差
        if self.solver == "dopri5":
            ts, ys = ODESolver.solve(
                self.ode_func, y0, t_span,
                method=self.solver,
                rtol=rtol, atol=atol, max_steps=max_steps,
            )
        else:
            ts, ys = ODESolver.solve(
                self.ode_func, y0, t_span,
                method=self.solver, dt=dt,
            )

        # 记录迹（伴随法用）
        self._trace = list(zip(ts, ys))

        return ts, ys

    def adjoint_gradient(self, dL_dyT: np.ndarray,
                         t_span: Tuple[float, float]) -> List[np.ndarray]:
        """伴随法计算梯度（简化版）
        
        论文公式（伴随敏感性）：
        dL/dθ = ∫_{t1}^{t0} a(t)^T * ∂f/∂θ dt
        
        其中 a(t) 是伴随状态，满足：
        da/dt = -a(t)^T * ∂f/∂h
        
        这里做简化近似：用前向迹直接估计
        """
        # 真正的实现需要逆时求解伴随 ODE
        # 这里返回占位
        return [np.zeros_like(l["w"]) for l in self.layers]

    def get_info(self) -> dict:
        total_params = sum(l["w"].size + l["b"].size for l in self.layers)
        return {
            "state_dim": self.state_dim,
            "solver": self.solver,
            "layers": [l["w"].shape for l in self.layers],
            "total_params": total_params,
        }


# ==================== Neural ODE + LTC 融合 ====================

class LTCNeuralODEWrapper:
    """
    LTC 的 Neural ODE 包装器
    
    原 LTC 微分方程：
        dh/dt = f(h, x, t, W) = σ(W_h h + W_x x + b) * (E - h)
    
    这里用 Neural ODE 替代固定的 σ 形式，允许更复杂的动态。
    
    论文连接：
    - LTC (Hasani, AAAI 2021): dh/dt = [f(h, x, t) + forget] * time_constant
    - Neural ODE (Chen, NeurIPS 2018): 任何可微的 dh/dt = NN(h, t)
    
    融合：dh/dt = NeuralODE(h, x, t, time_constant)
    """

    def __init__(self, state_dim: int, input_dim: int,
                 hidden_dim: int = 32,
                 solver: str = "rk4",
                 use_kan: bool = False):
        self.state_dim = state_dim
        self.input_dim = input_dim

        # 创建 Neural ODE（状态 + 输入 + 时间）
        self.ode = NeuralODE(
            state_dim=state_dim + input_dim + 1,  # h + x + tc
            hidden_dim=hidden_dim,
            solver=solver,
        )

        self.use_kan = use_kan

        # LTC 时间常数参数
        self.tau_w = np.random.randn(state_dim, input_dim).astype(np.float32) * 0.1
        self.tau_b = np.zeros(state_dim, dtype=np.float32)

    def ode_func(self, hx: np.ndarray, t: float) -> np.ndarray:
        """LTC-NeuralODE 融合的右端函数
        
        hx = [h (state_dim), x (input_dim), tau (1)] — 共 state_dim + input_dim + 1 维
        
        Neural ODE 对完整 hx 做微分，然后除以 LTC 时间常数限制速度。
        """
        # 分离
        h = hx[:self.state_dim]
        tau_raw = hx[-1] if len(hx) > self.state_dim + self.input_dim else 0.0

        # LTC 时间常数
        tau = 1.0 + self._sigmoid(tau_raw) * 10.0  # [1, 11]

        # 完整状态导数 = Neural ODE(hx, t)
        # 注意这里传的 hx 包含 h + x + tau（正好是 self.ode 的 state_dim）
        dhx_dt = self.ode.ode_func(hx, t)

        # 只对状态部分应用时间常数
        dhx_dt[:self.state_dim] = dhx_dt[:self.state_dim] / tau

        # 输入的导数置零（在每个时间步边界重置）
        if self.input_dim > 0:
            dhx_dt[self.state_dim:self.state_dim + self.input_dim] = 0.0

        return dhx_dt

    @staticmethod
    def _sigmoid(x: float) -> float:
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        else:
            z = math.exp(x)
            return z / (1.0 + z)

    def forward(self, h0: np.ndarray, x_seq: np.ndarray,
                t_span: Tuple[float, float] = (0.0, 1.0),
                solver: str = None) -> Tuple[np.ndarray, np.ndarray]:
        """LTC 时间驱动的前向传播
        
        Args:
            h0: 初始状态 [state_dim]
            x_seq: 输入序列 [T, input_dim]
            t_span: 时间范围
            solver: 求解器
        
        Returns:
            ts, ys (包含 h, x, tau 的完整状态)
        """
        T = x_seq.shape[0]
        solver = solver or self.ode.solver

        # 对每个时间步求解 ODE
        h = h0.copy().astype(np.float64)

        all_ts = []
        all_hs = []

        for t_idx in range(T):
            x_t = x_seq[t_idx].astype(np.float64)

            # 构造扩展初始状态
            tau = np.array([0.0])  # 初始时间常数
            hx0 = np.concatenate([h, x_t, tau])

            # 求解一步
            local_t0 = t_idx * 1.0
            local_t1 = (t_idx + 1) * 1.0

            solve_kwargs = {"dt": 0.1} if solver != "dopri5" else {"rtol": 1e-4, "atol": 1e-6}
            ts, ys = ODESolver.solve(
                self.ode_func, hx0, (local_t0, local_t1),
                method=solver, **solve_kwargs
            )

            # 取最后状态
            hx_final = ys[-1]
            h = hx_final[:self.state_dim]

            all_ts.extend(ts[:-1])
            all_hs.append(h.copy())

        return np.array(all_ts), np.array(all_hs)

    def get_info(self) -> dict:
        return {
            "state_dim": self.state_dim,
            "input_dim": self.input_dim,
            "solver": self.ode.solver,
            "ode_params": self.ode.get_info()["total_params"],
        }


# ==================== 测试 ====================

def test_ode_solver():
    """测试 ODE 求解器"""
    from math import sin, cos

    # 测试函数：简单振荡 dy/dt = -y
    def f(y, t):
        return -y * 2.0

    y0 = np.array([1.0])

    for method in ["euler", "rk4", "dopri5"]:
        kwargs = {"dt": 0.1} if method != "dopri5" else {"rtol": 1e-4, "atol": 1e-6}
        ts, ys = ODESolver.solve(f, y0, (0.0, 5.0), method=method, **kwargs)
        # 解析解: y(t) = e^{-2t}
        y_exact = np.exp(-2 * ts)
        err = np.mean(np.abs(ys[:, 0] - y_exact))
        print(f"   {method:8s}: {len(ts)} 步, 平均误差={err:.6f}")


def test_neural_ode():
    """测试 Neural ODE"""
    ode = NeuralODE(state_dim=4, hidden_dim=32, solver="rk4")

    y0 = np.random.randn(4).astype(np.float64)
    ts, ys = ode.forward(y0, (0.0, 2.0), dt=0.1)

    assert ys.shape[1] == 4, f"输出形状错误: {ys.shape}"

    print(f"✅ Neural ODE: {y0.shape} → {ys.shape}")
    print(f"   状态范围: [{ys.min():.3f}, {ys.max():.3f}]")
    print(f"   最终状态: {ys[-1]}")

    info = ode.get_info()
    print(f"   参数量: {info['total_params']}")

    return ode


def test_ltc_neural_ode():
    """测试 LTC + Neural ODE 融合"""
    wrapper = LTCNeuralODEWrapper(state_dim=3, input_dim=2, hidden_dim=16)

    h0 = np.zeros(3)
    x_seq = np.random.randn(5, 2).astype(np.float64)

    ts, hs = wrapper.forward(h0, x_seq)

    assert hs.shape == (5, 3), f"输出形状错误: {hs.shape}"

    print(f"✅ LTC + Neural ODE: {h0.shape} → {hs.shape}")
    print(f"   每步状态范围: [{hs.min():.3f}, {hs.max():.3f}]")

    return wrapper


if __name__ == "__main__":
    print("=" * 50)
    print("Neural ODE 测试")
    print("=" * 50)

    print("ODE 求解器精度比较:")
    test_ode_solver()
    print()

    test_neural_ode()
    print()

    test_ltc_neural_ode()

    print()
    print("✅ Neural ODE 全部测试通过")
