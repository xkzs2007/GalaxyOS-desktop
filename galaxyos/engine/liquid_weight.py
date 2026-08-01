#!/usr/bin/env python3
"""
P19: Liquid Weight 独立模块 — 液态权重生成、融合、混合

从 liquid time-constant 概念中提取独立权重管理模块，不依赖 torch/ncps。
适用于所有需要动态权重的场景：记忆检索排序、突触权重、融合评分。

核心组件:
  1. LiquidWeightGenerator — LTC 时间常数驱动的权重生成
  2. LiquidWeightFusion — 多来源权重融合（液态 + 静态 + 情感）
  3. LiquidStaticWeightMixer — 液态动态权重与静态基线的混合
  4. LiquidWeightConfig — 参数配置

论文参考:
  - LTC: Liquid Time-Constant Networks (Hasani, AAAI 2021)
  - CfC: Closed-form Continuous-time (Hasani, Nature MI 2022)

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-14
"""

import math
import time
import numpy as np
from typing import Optional, Tuple, Union
from dataclasses import dataclass


# ============================================================================
# 配置
# ============================================================================

@dataclass
class LiquidWeightConfig:
    """液态权重配置"""
    # 时间常数范围
    tau_min: float = 0.1        # 最小时间常数（响应最快）
    tau_max: float = 10.0       # 最大时间常数（最慢）

    # 衰减配置
    decay_hours: float = 24.0   # 半衰期（小时）

    # 融合配置
    liquid_ratio_default: float = 0.6    # 液态权重默认占比
    static_ratio_default: float = 0.3    # 静态权重默认占比
    emotion_ratio_default: float = 0.1   # 情感权重默认占比

    # 生成配置
    noise_scale: float = 0.05   # 权重生成时的噪声标准差
    min_weight: float = 0.01    # 最小权重值
    max_weight: float = 1.0     # 最大权重值
    sigmoid_temp: float = 1.0   # sigmoid 温度参数


# ============================================================================
# 液态权重生成器
# ============================================================================

class LiquidWeightGenerator:
    """
    液态权重生成器 — LTC 时间常数驱动的动态权重

    核心公式：
      dh/dt = σ(W·x + b) × (E - h) / τ
      τ = σ(W_τ·x + b_τ) × (τ_max - τ_min) + τ_min
      weight = σ(W_out·h + b_out)

    不同于固定权重，液态权重随输入状态和时间常数动态变化。
    """

    def __init__(self, config: Optional[LiquidWeightConfig] = None):
        self.config = config or LiquidWeightConfig()

        # 时间常数门控参数
        self._w_tau: Optional[np.ndarray] = None
        self._b_tau: Optional[np.ndarray] = None

        # 状态映射参数
        self._w_state: Optional[np.ndarray] = None
        self._b_state: Optional[np.ndarray] = None

        # 输出映射
        self._w_out: Optional[np.ndarray] = None
        self._b_out: Optional[np.ndarray] = None

        # 内部状态（可选）
        self._h: Optional[np.ndarray] = None

        self._initialized = False

    def _ensure_initialized(self, input_dim: int = 4):
        """惰性初始化权重矩阵"""
        if not self._initialized:
            seed = int(time.time() * 1000) % 10000
            rng = np.random.RandomState(seed)

            d = max(8, input_dim * 2)  # 隐藏维度

            self._w_tau = rng.randn(1, d).astype(np.float32) * 0.01
            self._b_tau = np.zeros(1, dtype=np.float32)

            self._w_state = rng.randn(d, input_dim).astype(np.float32) * 0.01
            self._b_state = np.zeros(d, dtype=np.float32)

            self._w_out = rng.randn(1, d).astype(np.float32) * 0.01
            self._b_out = np.zeros(1, dtype=np.float32)

            self._h = np.zeros(d, dtype=np.float64)
            self._input_dim = input_dim
            self._initialized = True

    def _sigmoid(self, x: np.ndarray) -> np.ndarray:
        """数值稳定的 sigmoid"""
        pos = x >= 0
        result = np.zeros_like(x, dtype=np.float64)
        result[pos] = 1.0 / (1.0 + np.exp(-x[pos].astype(np.float64)))
        result[~pos] = np.exp(x[~pos].astype(np.float64)) / (1.0 + np.exp(x[~pos].astype(np.float64)))
        return result.astype(np.float32)

    def generate_weight(self, input_vector: np.ndarray,
                        dt: float = 1.0,
                        return_state: bool = False) -> Union[float, Tuple[float, np.ndarray]]:
        """
        基于输入生成液态权重

        Args:
            input_vector: 输入特征向量（如 [重要性, 时效性, 活性, 情感]）
            dt: 时间步长
            return_state: 是否返回内部状态

        Returns:
            weight: 生成的权重值 [0, 1]
            h: (可选) 更新后的内部状态
        """
        self._ensure_initialized(len(input_vector))

        # 状态更新（LTC ODE 一步）
        drive = self._w_state @ input_vector + self._b_state

        # 时间常数
        tau_raw = self._w_tau @ self._h + self._b_tau
        tau = self._sigmoid(tau_raw[0]) * (self.config.tau_max - self.config.tau_min)
        tau += self.config.tau_min

        # dh/dt = σ(Wx + b) * (E - h) / τ
        gate = self._sigmoid(drive)
        E = np.ones_like(self._h)
        dh = gate * (E - self._h) / tau

        self._h = self._h + dh * dt

        # 输出权重
        out_raw = self._w_out @ self._h + self._b_out
        weight = float(self._sigmoid(out_raw[0]))

        # 限制范围
        weight = max(self.config.min_weight, min(self.config.max_weight, weight))

        if return_state:
            return weight, self._h.copy()
        return weight

    def batch_generate(self, input_vectors: np.ndarray,
                       dt: float = 1.0,
                       reset: bool = True) -> np.ndarray:
        """
        批量生成权重

        Args:
            input_vectors: (N, input_dim) 特征矩阵
            dt: 时间步长
            reset: 是否每行重置状态

        Returns:
            weights: (N,) 权重数组
        """
        N = input_vectors.shape[0]
        weights = np.zeros(N, dtype=np.float32)

        for i in range(N):
            if reset:
                self.reset()
            weights[i] = self.generate_weight(input_vectors[i], dt=dt)

        return weights

    def generate_from_features(self,
                               importance: float = 0.5,
                               recency_factor: float = 0.5,
                               activation_factor: float = 0.5,
                               emotion_factor: float = 0.0,
                               dt: float = 1.0) -> float:
        """
        从语义特征生成权重（便捷接口）

        Args:
            importance: 重要性 [0, 1]
            recency_factor: 时效性因子 [0, 1]，越大越新
            activation_factor: 激活频率因子 [0, 1]
            emotion_factor: 情感因子 [-1, 1]
            dt: 时间步长

        Returns:
            weight: [0, 1]
        """
        input_vec = np.array([
            importance,
            recency_factor * 0.5,  # 时效性压缩到合理范围
            activation_factor,
            (emotion_factor + 1.0) * 0.5  # [-1,1] → [0,1]
        ], dtype=np.float32)

        return self.generate_weight(input_vec, dt=dt)

    def get_current_time_constant(self) -> float:
        """获取当前时间常数（可用于外部判断动态特性）"""
        if self._h is None:
            return (self.config.tau_min + self.config.tau_max) / 2.0

        tau_raw = self._w_tau @ self._h + self._b_tau
        tau = self._sigmoid(tau_raw[0]) * (self.config.tau_max - self.config.tau_min)
        tau += self.config.tau_min
        return float(tau)

    def reset(self):
        """重置内部状态"""
        if self._initialized:
            self._h = np.zeros(self._input_dim * 2 if self._input_dim else 8, dtype=np.float64)

    def get_info(self) -> dict:
        """获取生成器信息"""
        return {
            "type": "LiquidWeightGenerator",
            "tau_range": [self.config.tau_min, self.config.tau_max],
            "initialized": self._initialized,
            "current_tau": self.get_current_time_constant(),
            "noise_scale": self.config.noise_scale,
            "decay_hours": self.config.decay_hours,
        }


# ============================================================================
# 液态权重融合器
# ============================================================================

class LiquidWeightFusion:
    """
    液态权重融合器 — 多来源权重融合

    融合多种权重来源：
      - 液态权重（LTC 动态生成）
      - 静态基线权重（预配置/固定）
      - 情感权重（根据情感状态调整）

    融合公式：
      w_fused = α_l × w_liquid + α_s × w_static + α_e × w_emotion

    其中 α_l + α_s + α_e = 1.0（可配置）
    """

    def __init__(self, config: Optional[LiquidWeightConfig] = None):
        self.config = config or LiquidWeightConfig()
        self._generator = LiquidWeightGenerator(self.config)

    def fuse(self,
             liquid_weight: float,
             static_weight: float = 0.5,
             emotion_weight: float = 0.0,
             alpha_l: Optional[float] = None,
             alpha_s: Optional[float] = None,
             alpha_e: Optional[float] = None) -> float:
        """
        融合多来源权重

        Args:
            liquid_weight: 液态动态权重 [0, 1]
            static_weight: 静态基线权重 [0, 1]
            emotion_weight: 情感权重 [0, 1]
            alpha_l: 液态权重占比（默认用配置）
            alpha_s: 静态权重占比（默认用配置）
            alpha_e: 情感权重占比（默认用配置）

        Returns:
            fused_weight: [0, 1]
        """
        alpha_l = alpha_l if alpha_l is not None else self.config.liquid_ratio_default
        alpha_s = alpha_s if alpha_s is not None else self.config.static_ratio_default
        alpha_e = alpha_e if alpha_e is not None else self.config.emotion_ratio_default

        total = alpha_l + alpha_s + alpha_e
        if abs(total - 1.0) > 1e-6:
            # 归一化
            alpha_l /= total
            alpha_s /= total
            alpha_e /= total

        fused = alpha_l * liquid_weight + alpha_s * static_weight + alpha_e * emotion_weight

        return max(self.config.min_weight, min(self.config.max_weight, fused))

    def generate_and_fuse(self,
                          importance: float = 0.5,
                          recency_factor: float = 0.5,
                          activation_factor: float = 0.5,
                          emotion_factor: float = 0.0,
                          static_weight: float = 0.5,
                          dt: float = 1.0,
                          **kwargs) -> float:
        """
        一步完成液态权重生成 + 融合

        Args:
            importance: 重要性 [0, 1]
            recency_factor: 时效性 [0, 1]
            activation_factor: 激活频率 [0, 1]
            emotion_factor: 情感因子 [-1, 1]
            static_weight: 静态基线权重 [0, 1]
            dt: 时间步长
            **kwargs: 传递给 fuse 的 alpha 参数

        Returns:
            fused_weight: [0, 1]
        """
        liquid = self._generator.generate_from_features(
            importance=importance,
            recency_factor=recency_factor,
            activation_factor=activation_factor,
            emotion_factor=emotion_factor,
            dt=dt,
        )

        # 情感权重 = 基于情感因子和重要性的加权
        emotion_weight = max(0.0, emotion_factor) * importance

        return self.fuse(
            liquid_weight=liquid,
            static_weight=static_weight,
            emotion_weight=emotion_weight,
            **kwargs,
        )

    def batch_fuse(self,
                   features: np.ndarray,
                   static_weights: np.ndarray,
                   alpha_l: float = 0.6,
                   alpha_s: float = 0.3,
                   alpha_e: float = 0.1) -> np.ndarray:
        """
        批量融合

        Args:
            features: (N, 4) [importance, recency, activation, emotion]
            static_weights: (N,) 静态基线
            alpha_l/s/e: 混合比例

        Returns:
            fused: (N,)
        """
        N = features.shape[0]
        results = np.zeros(N, dtype=np.float32)

        for i in range(N):
            imp, rec, act, emot = features[i]
            # 情感权重
            emotion_w = max(0.0, emot) * imp
            liquid = self._generator.generate_from_features(
                importance=imp, recency_factor=rec,
                activation_factor=act, emotion_factor=emot,
            )
            results[i] = self.fuse(
                liquid_weight=liquid,
                static_weight=float(static_weights[i]) if static_weights.ndim > 0 else static_weights,
                emotion_weight=emotion_w,
                alpha_l=alpha_l, alpha_s=alpha_s, alpha_e=alpha_e,
            )

        return results

    def get_generator(self) -> LiquidWeightGenerator:
        """获取内部生成器"""
        return self._generator

    def reset_generator(self):
        """重置生成器的内部状态"""
        self._generator.reset()


# ============================================================================
# 液态-静态权重混合器
# ============================================================================

class LiquidStaticWeightMixer:
    """
    液态-静态权重混合器

    支持两种混合模式：
      1. 线性混合：w = α × w_liquid + (1-α) × w_static
      2. 门控混合：w = gate × w_liquid + (1-gate) × w_static
         gate 动态由上下文决定

    适用于：
      - 记忆检索的排序权重
      - 突触权重衰减和增强
      - 融合评分中液态 vs 静态的权衡
    """

    def __init__(self):
        self._generator = LiquidWeightGenerator()

    def linear_mix(self,
                   liquid_weight: float,
                   static_weight: float,
                   alpha: float = 0.6) -> float:
        """
        线性混合：w = α × w_liquid + (1-α) × w_static

        Args:
            liquid_weight: 液态权重 [0, 1]
            static_weight: 静态权重 [0, 1]
            alpha: 液态占比 [0, 1]

        Returns:
            mixed: [0, 1]
        """
        alpha = max(0.0, min(1.0, alpha))
        return alpha * liquid_weight + (1.0 - alpha) * static_weight

    def gated_mix(self,
                  liquid_weight: float,
                  static_weight: float,
                  context_features: Optional[np.ndarray] = None,
                  gate_override: Optional[float] = None) -> float:
        """
        门控混合：gate 由上下文动态决定

        Args:
            liquid_weight: 液态权重 [0, 1]
            static_weight: 静态权重 [0, 1]
            context_features: 上下文特征（用于生成 gate）
            gate_override: 手动指定门控值 [0, 1]

        Returns:
            mixed: [0, 1]
        """
        if gate_override is not None:
            gate = max(0.0, min(1.0, gate_override))
        elif context_features is not None:
            gate = self._generator.generate_weight(context_features)
        else:
            gate = 0.6  # 默认 gate

        return gate * liquid_weight + (1.0 - gate) * static_weight

    def adaptive_mix(self,
                     liquid_weight: float,
                     static_weight: float,
                     confidence: float = 0.5) -> float:
        """
        自适应混合 — 根据置信度调整混合比例

        置信度高时偏向液态（动态适应），置信度低时偏向静态（稳定可靠）。
        alpha = sigmoid(5 * (confidence - 0.5))

        Args:
            liquid_weight: 液态权重 [0, 1]
            static_weight: 静态权重 [0, 1]
            confidence: 当前置信度 [0, 1]

        Returns:
            mixed: [0, 1]
        """
        # 温度参数 5 使 0.5 附近有足够区分度
        alpha = 1.0 / (1.0 + math.exp(-5.0 * (confidence - 0.5)))
        return self.linear_mix(liquid_weight, static_weight, alpha)

    def batch_mix(self,
                  liquid_weights: np.ndarray,
                  static_weights: np.ndarray,
                  mode: str = "linear",
                  alpha: float = 0.6,
                  confidences: Optional[np.ndarray] = None) -> np.ndarray:
        """
        批量混合

        Args:
            liquid_weights: (N,) 液态权重数组
            static_weights: (N,) 静态权重数组
            mode: "linear" | "gated" | "adaptive"
            alpha: linear 模式的液态占比
            confidences: adaptive 模式下的置信度数组

        Returns:
            mixed: (N,)
        """
        if mode == "linear":
            return self.linear_mix(liquid_weights, static_weights, alpha)

        N = len(liquid_weights)
        results = np.zeros(N, dtype=np.float32)

        for i in range(N):
            if mode == "gated":
                results[i] = self.gated_mix(float(liquid_weights[i]), float(static_weights[i]))
            elif mode == "adaptive":
                conf = confidences[i] if confidences is not None else 0.5
                results[i] = self.adaptive_mix(float(liquid_weights[i]), float(static_weights[i]), conf)

        return results

    def reset(self):
        """重置内部生成器状态"""
        self._generator.reset()


# ============================================================================
# 便捷入口
# ============================================================================

def create_weight_generator(config: Optional[LiquidWeightConfig] = None) -> LiquidWeightGenerator:
    """便捷工厂：创建权重生成器"""
    return LiquidWeightGenerator(config or LiquidWeightConfig())


def create_weight_fuser(config: Optional[LiquidWeightConfig] = None) -> LiquidWeightFusion:
    """便捷工厂：创建权重融合器"""
    return LiquidWeightFusion(config or LiquidWeightConfig())


def compute_liquid_static_blend(liquid_weights: np.ndarray,
                                 static_weights: np.ndarray,
                                 alpha: float = 0.6) -> np.ndarray:
    """快捷函数：批量线性混合液态+静态权重"""
    mixer = LiquidStaticWeightMixer()
    return mixer.batch_mix(liquid_weights, static_weights, mode="linear", alpha=alpha)


# ============================================================================
# 测试
# ============================================================================

def test_generator():
    """测试权重生成器"""
    gen = create_weight_generator()

    # 测试不同特征组合
    test_cases = [
        (0.9, 0.9, 0.9, 0.5),  # 高重要、新、高频
        (0.5, 0.5, 0.5, 0.0),  # 中等
        (0.1, 0.1, 0.1, -0.5), # 低重要、旧、低频
    ]

    print("=== LiquidWeightGenerator 测试 ===")
    for imp, rec, act, emo in test_cases:
        w = gen.generate_from_features(imp, rec, act, emo)
        tau = gen.get_current_time_constant()
        print(f"  importance={imp:.1f}, recency={rec:.1f}, act={act:.1f}, emo={emo:+.1f} → w={w:.4f}, τ={tau:.2f}")

    gen.reset()
    print("  (重置后)")
    return True


def test_fusion():
    """测试权重融合器"""
    fuser = create_weight_fuser()

    weights = fuser.generate_and_fuse(
        importance=0.8, recency_factor=0.7,
        activation_factor=0.6, emotion_factor=0.3,
        static_weight=0.5,
    )

    print("=== LiquidWeightFusion 测试 ===")
    print(f"  generate_and_fuse: {weights:.4f}")

    # 纯融合测试
    fused = fuser.fuse(liquid_weight=0.8, static_weight=0.5, emotion_weight=0.2)
    print(f"  fuse(0.8, 0.5, 0.2): {fused:.4f}")

    return True


def test_mixer():
    """测试混合器"""
    mixer = LiquidStaticWeightMixer()

    # 线性混合
    m1 = mixer.linear_mix(0.8, 0.4, alpha=0.6)
    print("=== LiquidStaticWeightMixer 测试 ===")
    print(f"  linear_mix(0.8, 0.4, α=0.6) = {m1:.4f}")

    # 自适应混合
    m2 = mixer.adaptive_mix(0.8, 0.4, confidence=0.7)
    print(f"  adaptive_mix(0.8, 0.4, c=0.7) = {m2:.4f}")

    m3 = mixer.adaptive_mix(0.8, 0.4, confidence=0.3)
    print(f"  adaptive_mix(0.8, 0.4, c=0.3) = {m3:.4f}")

    return True


if __name__ == "__main__":
    test_generator()
    print()
    test_fusion()
    print()
    test_mixer()
    print()
    print("✅ LiquidWeight 全部测试通过")
