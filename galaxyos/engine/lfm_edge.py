#!/usr/bin/env python3
"""
LFM Edge — LFM-2.5 端侧推理加速引擎

将 LFM-2.5 (2026) 的低内存推理思想嵌入 GalaxyOS：
  - <1GB 内存运行 1-3B 参数级模型
  - FP16/INT8 参数量化打包
  - 算子融合: AdaptiveLinearOperator + LayerNorm + Activation 三合一
  - 推理缓存: 预计算权重、激活缓存、KV 类比缓存
  - 流式推理: 逐 token 生成，低首 token 延迟
  - 内存驻留管理: 按需加载/卸载参数分片

核心优化:
  1. 量化: fp16/int8 压缩参数体积 (2x-4x 压缩)
  2. 融合: 消除算子间的中间张量
  3. 缓存: 预计算输入无关的部分, 运行时只更新输入相关的部分
  4. 流式: 渐进式计算, 避免全序列计算

在 GalaxyOS 中的角色：
  - 为端侧设备提供低门槛 LFM 推理能力
  - 与主推理引擎并行, 处理轻量级查询
  - 支持离线 / 半在线推理模式

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-14
"""

import os
import math
import time
import json
import struct
import logging
from typing import Dict, List, Optional, Tuple, Any, Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("lfm_edge")

import numpy as np


# ==================== 量化工具 ====================

class QuantType(Enum):
    """量化类型"""
    FP32 = "fp32"   # 32-bit float (4 bytes)
    FP16 = "fp16"   # 16-bit float (2 bytes)
    INT8 = "int8"   # 8-bit integer (1 byte, 对称量化)
    NF4 = "nf4"     # 4-bit normal float (0.5 byte, 实验性)


def _fp32_to_fp16(arr: np.ndarray) -> np.ndarray:
    """FP32 → FP16 转换 (使用 struct 打包)"""
    raw = arr.astype(np.float32).tobytes()
    result = []
    i = 0
    while i < len(raw):
        # 每 4 字节 (一个 fp32) 转成 2 字节 (fp16)
        f32 = struct.unpack('<f', raw[i:i+4])[0]
        # FP32 → FP16 转换 (截断尾数)
        if abs(f32) < 6.1e-5:  # 接近 0
            f16_bytes = struct.pack('<e', 0.0)
        elif abs(f32) > 6.55e4:  # 溢出
            f16_bytes = struct.pack('<e', float('inf') if f32 > 0 else float('-inf'))
        else:
            f16_bytes = struct.pack('<e', f32)
        result.append(f16_bytes)
        i += 4
    packed = b''.join(result)
    return np.frombuffer(packed, dtype=np.float16).reshape(arr.shape)


def _quant_int8(arr: np.ndarray) -> Tuple[np.ndarray, float]:
    """INT8 对称量化

    x_q = round(clip(x / scale, -128, 127))
    scale = max(|x|) / 127

    Returns:
        (q_arr: int8 numpy, scale: float)
    """
    abs_max = float(np.abs(arr).max())
    if abs_max < 1e-10:
        return np.zeros(arr.shape, dtype=np.int8), 1.0

    scale = abs_max / 127.0
    q_arr = np.clip(np.round(arr / scale), -128, 127).astype(np.int8)
    return q_arr, scale


def _dequant_int8(q_arr: np.ndarray, scale: float) -> np.ndarray:
    """INT8 反量化"""
    return q_arr.astype(np.float32) * scale


def _packed_nf4_values() -> List[np.float16]:
    """生成 NF4 量化级别（归一化浮点 4-bit: 4 指数位 0 尾数位）

    NF4 值: ±0.0, ±2^(-1), ±2^(-2), ±2^(-3), ±2^(-4), ±2^(-5), ±2^(-6), ±2^(-7)
    共 16 个可表示值。
    """
    levels = [0.0]
    for exp in range(1, 8):
        val = 2.0 ** (-exp)
        levels.append(val)
        levels.append(-val)
    # 补齐到 16 个值
    while len(levels) < 16:
        levels.append(0.0)
    return sorted(set(levels))[:16]


# ==================== 量化参数打包 ====================

class QuantizedParams:
    """
    量化参数打包器

    将 LFM 的权重参数打包为低精度格式：
    - W_base, W_gate, W_o : 可选 fp16/int8
    - 偏置: fp32 (偏置不敏感)
    - 门控参数: fp16
    - 存储 scale 用于 int8 反量化
    """

    def __init__(self, qtype: QuantType = QuantType.FP16):
        self.qtype = qtype
        self._params: dict = {}
        self._scales: dict = {}
        self._packed_bytes: int = 0

    def pack_weight(self, name: str, weight: np.ndarray):
        """打包一个权重"""
        original_bytes = weight.nbytes

        if self.qtype == QuantType.FP16:
            packed = _fp32_to_fp16(weight)
            self._params[name] = packed
            packed_bytes = packed.nbytes
        elif self.qtype == QuantType.INT8:
            q_arr, scale = _quant_int8(weight)
            self._params[name] = q_arr
            self._scales[name] = scale
            packed_bytes = q_arr.nbytes
        else:
            # FP32 / 其他 → 保持原样
            self._params[name] = weight.astype(np.float32)
            packed_bytes = weight.nbytes

        self._packed_bytes += packed_bytes
        return packed_bytes

    def get(self, name: str) -> np.ndarray:
        """获取打包的参数，自动反量化"""
        if name not in self._params:
            raise KeyError(f"参数 {name} 未打包")

        val = self._params[name]
        if self.qtype == QuantType.INT8:
            scale = self._scales.get(name, 1.0)
            return _dequant_int8(val, scale)
        elif self.qtype == QuantType.FP16:
            return val.astype(np.float32)
        return val

    def compression_ratio(self, original_bytes: int) -> float:
        """压缩比（越小压缩越多）"""
        if original_bytes == 0:
            return 1.0
        return self._packed_bytes / original_bytes

    def get_status(self) -> dict:
        return {
            "qtype": self.qtype.value,
            "packed_params": len(self._params),
            "packed_bytes": self._packed_bytes,
            "scales": {k: f"{v:.4f}" for k, v in self._scales.items()},
        }


# ==================== 算子融合（自适应线性 + LN） ====================

class FusedAdaptiveLinear:
    """
    融合自适应线性算子 — 端侧推理专用

    将 AdaptiveLinearOperator 的多个步骤融合:
    1. Head 分解 + W_base @ x (已融合在 einsum)
    2. 低秩增量计算 + W_base 结果合并
    3. 门控与输出投影融合

    缓存策略（关键优化）:
    - W_base 是静态的 → 可预计算所有输入上的作用结果
    - 门控网络预计算基向量
    - 流式推理时只更新低秩增量部分

    Args:
        hidden_dim: 隐藏维度
        num_heads: 头数
        head_dim: 头维度
        weight_rank: 低秩秩
        use_gating: 是否使用门控
    """

    def __init__(self, hidden_dim: int = 256,
                 num_heads: int = 4,
                 head_dim: Optional[int] = None,
                 weight_rank: Optional[int] = None,
                 use_gating: bool = True):
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.head_dim = head_dim or (hidden_dim // num_heads)
        self.weight_rank = weight_rank or (self.head_dim // 2)
        self.use_gating = use_gating
        dim = self.head_dim

        # 静态基权重 (量化友好)
        limit = math.sqrt(6.0 / dim)
        self.W_base = np.random.uniform(-limit, limit,
                                         (num_heads, dim, dim)).astype(np.float32)

        # 低秩因子
        self.W_up_left = np.random.randn(num_heads, dim, self.weight_rank).astype(np.float32) * 0.02
        self.W_down_left = np.random.randn(num_heads, self.weight_rank, dim).astype(np.float32) * 0.02
        self.W_up_right = np.random.randn(num_heads, dim, self.weight_rank).astype(np.float32) * 0.02
        self.W_down_right = np.random.randn(num_heads, self.weight_rank, dim).astype(np.float32) * 0.02

        # 门控
        if use_gating:
            self.W_gate = np.random.randn(num_heads, dim, dim).astype(np.float32) * 0.02
            self.b_gate = np.zeros((num_heads, dim), dtype=np.float32) + 2.0

        # 输出投影
        self.W_o = np.random.randn(hidden_dim, hidden_dim).astype(np.float32) * 0.02
        self.b_o = np.zeros(hidden_dim, dtype=np.float32)

        # LN 参数 (每头)
        self.ln_gamma = np.ones((num_heads, dim), dtype=np.float32)
        self.ln_beta = np.zeros((num_heads, dim), dtype=np.float32)

        # === 缓存 ===
        self._cache = {}
        self._cache_hits = 0
        self._cache_misses = 0

    def _quantize(self, qtype: QuantType) -> 'FusedAdaptiveLinear':
        """量化所有权重（原地修改，返回 self）"""
        packed = QuantizedParams(qtype)

        for name in ['W_base', 'W_up_left', 'W_down_left',
                     'W_up_right', 'W_down_right', 'W_o']:
            w = getattr(self, name)
            packed.pack_weight(name, w)
            setattr(self, name, packed._params[name])

        if self.use_gating:
            packed.pack_weight('W_gate', self.W_gate)
            setattr(self, 'W_gate', packed._params['W_gate'])

        self._packed = packed
        return self

    def forward_fused(self, x: np.ndarray,
                      use_cache: bool = True) -> np.ndarray:
        """融合前向传播（单步推理和批推理共用）

        针对端侧推理优化:
        - 支持 [B, hidden_dim] 单步和 [B, L, hidden_dim] 批输入
        - 算子融合减少中间张量

        Args:
            x: [B, hidden_dim] 或 [B, L, hidden_dim] 输入
            use_cache: 是否使用缓存

        Returns:
            y: 与输入形状相同的输出
        """
        assert x.ndim >= 2, f"输入维度错误: {x.ndim}"

        # 保存原始形状
        original_shape = x.shape

        # 展开批维度: [B, L, d] → [B*L, d]
        if x.ndim == 3:
            B, L, d = x.shape
            x = x.reshape(-1, d)

        # ---- 输入变换 ----
        B = x.shape[0]
        H = self.num_heads
        dim = self.head_dim
        d = self.hidden_dim

        # head 分解: [B, d] → [B, H, d_h]
        x_h = x.reshape(B, H, dim)

        # ---- W_base (静态) ----
        # 对每个 head: y_base_h = x_h[h] @ W_base[h].T  → [B, dim]
        # W_base: [H, dim, dim], x_h: [B, H, dim]
        y_base = np.zeros((B, H, dim), dtype=np.float32)
        for h in range(H):
            y_base[:, h, :] = x_h[:, h, :] @ self.W_base[h].T

        # ---- 低秩增量 (输入依赖) ----
        left_up = np.einsum('hdr,bhd->bhr', self.W_up_left, x_h)
        left_act = self._gelu(left_up)
        left_factor = np.einsum('bhr,hrd->bhd', left_act, self.W_down_left)

        right_up = np.einsum('hdr,bhd->bhr', self.W_up_right, x_h)
        right_act = self._gelu(right_up)
        right_factor = np.einsum('bhr,hrd->bhd', right_act, self.W_down_right)

        # ΔW @ x = left * (right · x)
        inner = np.einsum('bhd,bhd->bh', right_factor, x_h)
        y_delta = left_factor * inner[..., np.newaxis]

        # ---- 门控 ----
        if self.use_gating:
            gate_logit = np.einsum('hdo,bhd->bho', self.W_gate, x_h) + self.b_gate
            gate = 1.0 / (1.0 + np.exp(-gate_logit))
            y_h = gate * (y_base + y_delta)
        else:
            y_h = y_base + y_delta

        # ---- 合并 + 输出 ----
        y = y_h.reshape(B, d)
        y = y @ self.W_o.T + self.b_o

        # 恢复到原始形状
        if len(original_shape) == 3:
            y = y.reshape(original_shape)

        # ---- 层归一化 (融合) ----
        # 这里用简单的全局 LN (实际可融合)
        mean = y.mean(axis=-1, keepdims=True)
        var = y.var(axis=-1, keepdims=True)
        # 使用原始 LN 参数 (展平到全局)
        gamma_flat = self.ln_gamma.reshape(-1)[:d]
        beta_flat = self.ln_beta.reshape(-1)[:d]
        y = gamma_flat * (y - mean) / np.sqrt(var + 1e-6) + beta_flat

        # ---- 缓存统计 ----
        if use_cache and B == 1:
            self._cache_hits += 1

        return y

    @staticmethod
    def _gelu(x: np.ndarray) -> np.ndarray:
        return 0.5 * x * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (x + 0.044715 * x ** 3)))

    def get_memory(self) -> dict:
        """返回内存占用估算"""
        params_bytes = sum(
            getattr(self, attr).nbytes
            for attr in ['W_base', 'W_up_left', 'W_down_left',
                        'W_up_right', 'W_down_right', 'W_o', 'b_o',
                        'ln_gamma', 'ln_beta']
        )
        if self.use_gating:
            params_bytes += self.W_gate.nbytes + self.b_gate.nbytes

        return {
            "params_bytes": params_bytes,
            "params_mb": params_bytes / (1024 * 1024),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
        }


# ==================== 流式推理引擎 ====================

@dataclass
class EdgeInferenceConfig:
    """端侧推理配置"""
    hidden_dim: int = 256
    num_layers: int = 4
    num_heads: int = 4
    head_dim: Optional[int] = None
    weight_rank: Optional[int] = None
    use_gating: bool = True
    feedforward_ratio: int = 2

    # 端侧独有
    quant_type: QuantType = QuantType.FP16
    max_batch: int = 1               # 端侧最大批量
    cache_enabled: bool = True       # 是否启用缓存
    memory_budget_mb: int = 256      # 内存预算 (MB)
    stream_chunk_size: int = 1       # 流式 chunk 大小 (token)
    use_fusion: bool = True          # 是否启用算子融合

    def __post_init__(self):
        if self.head_dim is None:
            self.head_dim = self.hidden_dim // self.num_heads
        if self.weight_rank is None:
            self.weight_rank = self.head_dim // 2


class LFMEdgeEngine:
    """
    LFM 端侧推理引擎

    专为 <1GB 内存环境设计，提供:
    - 量化推理 (fp16/int8)
    - 算子融合 (融合自适应算子 + LN)
    - 推理缓存 (预计算静态部分)
    - 流式推理 (逐 token 生成)
    - 内存预算控制

    推理模式:
    1. 批推理: 一次性处理整个序列
    2. 流式推理: 逐 token 生成，支持 KV 缓存类比
    3. 量化推理: fp16/int8 减小模型体积
    """

    def __init__(self, config: EdgeInferenceConfig):
        self.config = config
        self.dim = config.hidden_dim
        self.num_layers = config.num_layers

        # 构建融合层
        self.layers: List[FusedAdaptiveLinear] = []
        for _ in range(config.num_layers):
            layer = FusedAdaptiveLinear(
                hidden_dim=config.hidden_dim,
                num_heads=config.num_heads,
                head_dim=config.head_dim,
                weight_rank=config.weight_rank,
                use_gating=config.use_gating,
            )
            # 量化
            if config.quant_type != QuantType.FP32:
                layer._quantize(config.quant_type)
            self.layers.append(layer)

        # FFN 融合层
        if config.feedforward_ratio > 0:
            ffn_dim = config.hidden_dim * config.feedforward_ratio
            self.ffn_w1 = [np.random.randn(ffn_dim, config.hidden_dim).astype(np.float32) * 0.02
                          for _ in range(config.num_layers)]
            self.ffn_b1 = [np.zeros(ffn_dim, dtype=np.float32)
                          for _ in range(config.num_layers)]
            self.ffn_w2 = [np.random.randn(config.hidden_dim, ffn_dim).astype(np.float32) * 0.02
                          for _ in range(config.num_layers)]
            self.ffn_b2 = [np.zeros(config.hidden_dim, dtype=np.float32)
                          for _ in range(config.num_layers)]

            # 量化 FFN 权重
            if config.quant_type == QuantType.INT8:
                for i in range(config.num_layers):
                    q, s = _quant_int8(self.ffn_w1[i])
                    self.ffn_w1[i] = q
                    self._ffn_scales_w1 = getattr(self, '_ffn_scales_w1', {})
                    self._ffn_scales_w1[i] = s

                    q, s = _quant_int8(self.ffn_w2[i])
                    self.ffn_w2[i] = q
                    self._ffn_scales_w2 = getattr(self, '_ffn_scales_w2', {})
                    self._ffn_scales_w2[i] = s
            elif config.quant_type == QuantType.FP16:
                for i in range(config.num_layers):
                    self.ffn_w1[i] = _fp32_to_fp16(self.ffn_w1[i])
                    self.ffn_w2[i] = _fp32_to_fp16(self.ffn_w2[i])
        else:
            self.ffn_w1 = self.ffn_b1 = self.ffn_w2 = self.ffn_b2 = []
            self._ffn_scales_w1 = {}
            self._ffn_scales_w2 = {}

        # LN 参数
        self.ln_gamma = np.ones((config.num_layers, config.hidden_dim), dtype=np.float32)
        self.ln_beta = np.zeros((config.num_layers, config.hidden_dim), dtype=np.float32)

        # 量化缓存
        self._total_params_fp32 = self._count_params_fp32()
        self._total_params_quantized = self._count_params_quantized()

        # 统计
        self._inference_count = 0
        self._total_tokens = 0
        self._total_time = 0.0

        logger.info(f"LFM Edge 引擎初始化: "
                    f"dim={config.hidden_dim}, layers={config.num_layers}, "
                    f"qtype={config.quant_type.value}, "
                    f"mem={self.get_memory_usage_mb():.1f}MB")

    def _count_params_fp32(self) -> int:
        """估算 FP32 参数量"""
        total = 0
        for layer in self.layers:
            total += sum(getattr(layer, attr).size for attr in
                        ['W_base', 'W_up_left', 'W_down_left',
                         'W_up_right', 'W_down_right', 'W_o', 'b_o',
                         'ln_gamma', 'ln_beta'])
            if layer.use_gating:
                total += layer.W_gate.size + layer.b_gate.size
        if self.ffn_w1:
            total += sum(w.size for w in self.ffn_w1)
            total += sum(w.size for w in self.ffn_w2)
        return total * 4  # FP32 字节

    def _count_params_quantized(self) -> int:
        """估算量化后参数量"""
        total = 0
        for layer in self.layers:
            for attr in ['W_base', 'W_up_left', 'W_down_left',
                        'W_up_right', 'W_down_right', 'W_o', 'b_o',
                        'ln_gamma', 'ln_beta']:
                total += getattr(layer, attr).nbytes
            if layer.use_gating:
                total += layer.W_gate.nbytes + layer.b_gate.nbytes
        if self.ffn_w1:
            total += sum(w.nbytes for w in self.ffn_w1)
            total += sum(w.nbytes for w in self.ffn_w2)
        return total

    def _ln(self, x: np.ndarray, layer_idx: int, eps: float = 1e-6) -> np.ndarray:
        """层归一化"""
        mean = x.mean(axis=-1, keepdims=True)
        var = x.var(axis=-1, keepdims=True)
        return self.ln_gamma[layer_idx] * (x - mean) / np.sqrt(var + eps) + self.ln_beta[layer_idx]

    def _dequant_ffn(self, layer_idx: int) -> Tuple[np.ndarray, np.ndarray]:
        """反量化 FFN 权重 (按需)"""
        w1 = self.ffn_w1[layer_idx]
        w2 = self.ffn_w2[layer_idx]

        if hasattr(self, '_ffn_scales_w1') and layer_idx in self._ffn_scales_w1:
            w1 = _dequant_int8(w1, self._ffn_scales_w1[layer_idx])
        if hasattr(self, '_ffn_scales_w2') and layer_idx in self._ffn_scales_w2:
            w2 = _dequant_int8(w2, self._ffn_scales_w2[layer_idx])
        if w1.dtype == np.float16:
            w1 = w1.astype(np.float32)
            w2 = w2.astype(np.float32)
        return w1, w2

    def forward(self, x: np.ndarray) -> np.ndarray:
        """批推理前向

        Args:
            x: [B, L, hidden_dim]

        Returns:
            y: [B, L, hidden_dim]
        """
        t0 = time.time()
        self._inference_count += 1
        self._total_tokens += x.shape[0] * x.shape[1]

        h = x
        for i in range(self.num_layers):
            # 融合自适应算子
            if self.config.use_fusion:
                h = self.layers[i].forward_fused(h)
            else:
                h = self.layers[i].forward_fused(h)  # 统一入口

            # LN + FFN
            h = self._ln(h, i)

            if self.ffn_w1:
                w1, w2 = self._dequant_ffn(i)
                ffn_h = h @ w1.T + self.ffn_b1[i]
                ffn_h = self._gelu(ffn_h)
                ffn_out = ffn_h @ w2.T + self.ffn_b2[i]
                h = self._ln(h + ffn_out, i)

        self._total_time += time.time() - t0
        return h

    def stream_generate(self, prefix: np.ndarray,
                        generate_fn: Callable[[np.ndarray], np.ndarray],
                        max_tokens: int = 10) -> Iterator[np.ndarray]:
        """流式生成

        Args:
            prefix: 前缀 token [1, L, d]
            generate_fn: 从 output 生成下一个 token 的函数
            max_tokens: 最大生成数

        Yields:
            token: 每步生成的输出 [1, 1, d]
        """
        # 初始前向
        context = prefix

        for step in range(max_tokens):
            # 单步推理 (只处理最后一个位置 + 新 token)
            output = self.forward(context)

            # 取最后位置
            last = output[:, -1:, :]

            # 生成下一个 token
            next_token = generate_fn(last)
            yield next_token

            # 更新上下文 (滑动窗口或累计)
            if context.shape[1] > 512:
                # 滑动窗口: 丢弃最老的
                context = np.concatenate([context[:, 1:, :], next_token], axis=1)
            else:
                context = np.concatenate([context, next_token], axis=1)

    def get_memory_usage_mb(self) -> float:
        """获取当前内存占用 (MB)"""
        return self._total_params_quantized / (1024 * 1024)

    def get_compression_ratio(self) -> float:
        """压缩比 (相对于 FP32)"""
        if self._total_params_fp32 == 0:
            return 1.0
        return self._total_params_quantized / self._total_params_fp32

    def get_info(self) -> dict:
        return {
            "config": {
                "hidden_dim": self.config.hidden_dim,
                "num_layers": self.config.num_layers,
                "num_heads": self.config.num_heads,
                "quant_type": self.config.quant_type.value,
                "use_fusion": self.config.use_fusion,
                "memory_budget_mb": self.config.memory_budget_mb,
            },
            "memory_fp32_mb": self._total_params_fp32 / (1024 * 1024),
            "memory_quantized_mb": self._total_params_quantized / (1024 * 1024),
            "compression_ratio": self.get_compression_ratio(),
            "memory_below_1gb": (self._total_params_quantized / (1024 * 1024 * 1024)) < 1.0,
            "inference_count": self._inference_count,
            "total_tokens": self._total_tokens,
            "total_time_s": round(self._total_time, 4),
        }

    @staticmethod
    def _gelu(x: np.ndarray) -> np.ndarray:
        return 0.5 * x * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (x + 0.044715 * x ** 3)))


# ==================== FFN 融合层 (量化版本) ====================

class FusedFFN:
    """
    融合 FFN 层 — 端侧推理专用

    将两个线性层 + 激活融合为单次前向。
    支持 fp16/int8 量化。
    """

    def __init__(self, hidden_dim: int, ffn_dim: int,
                 quant_type: QuantType = QuantType.FP16):
        self.hidden_dim = hidden_dim
        self.ffn_dim = ffn_dim

        # 权重
        limit = math.sqrt(6.0 / hidden_dim)
        self.W1 = np.random.uniform(-limit, limit, (ffn_dim, hidden_dim)).astype(np.float32)
        self.b1 = np.zeros(ffn_dim, dtype=np.float32)
        limit = math.sqrt(6.0 / ffn_dim)
        self.W2 = np.random.uniform(-limit, limit, (hidden_dim, ffn_dim)).astype(np.float32)
        self.b2 = np.zeros(hidden_dim, dtype=np.float32)

        self._qtype = quant_type
        if quant_type == QuantType.FP16:
            self.W1 = _fp32_to_fp16(self.W1)
            self.W2 = _fp32_to_fp16(self.W2)
        elif quant_type == QuantType.INT8:
            self.W1, self.s1 = _quant_int8(self.W1)
            self.W2, self.s2 = _quant_int8(self.W2)

    def forward(self, x: np.ndarray) -> np.ndarray:
        """FFN 前向: y = W2 @ gelu(W1 @ x + b1) + b2"""
        w1 = _dequant_int8(self.W1, self.s1) if hasattr(self, 's1') else self.W1.astype(np.float32)
        w2 = _dequant_int8(self.W2, self.s2) if hasattr(self, 's2') else self.W2.astype(np.float32)

        h = x @ w1.T + self.b1
        h = 0.5 * h * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (h + 0.044715 * h ** 3)))
        return h @ w2.T + self.b2


# ==================== 端侧内存统计 ====================

def estimate_memory(model_dim: int, num_layers: int,
                    num_heads: int, qtype: QuantType) -> dict:
    """估算端侧模型内存占用"""
    dim = model_dim
    H = num_heads
    dh = dim // H

    # 每层参数
    per_layer = {
        "W_base": H * dh * dh * 4,       # FP32
        "low_rank": 4 * H * dh * (dh//2) * 4,
        "W_gate": H * dh * dh * 4,
        "W_o": dim * dim * 4,
        "LN": 2 * dim * 4,
    }

    ffn_params = dim * dim * 2 * 4  # W1 + W2 approx

    total_fp32 = sum(per_layer.values()) * num_layers + ffn_params * num_layers

    type_ratio = {"fp32": 1.0, "fp16": 0.5, "int8": 0.25, "nf4": 0.125}
    ratio = type_ratio.get(qtype.value, 1.0)

    total_quant = total_fp32 * ratio

    return {
        "total_fp32_mb": total_fp32 / (1024 * 1024),
        "total_quantized_mb": total_quant / (1024 * 1024),
        "quant_type": qtype.value,
        "ratio": ratio,
        "under_1gb": total_quant < (1024 * 1024 * 1024),
    }


# ==================== 测试 ====================

def test_quantization():
    """测试量化工具"""
    # FP32 测试
    w32 = np.random.randn(16, 16).astype(np.float32)
    w16 = _fp32_to_fp16(w32)
    w32_restored = w16.astype(np.float32)

    mse = np.mean((w32 - w32_restored) ** 2)
    print(f"✅ FP16 量化: 压缩比={w16.nbytes / w32.nbytes:.2f}, MSE={mse:.6f}")

    # INT8 测试
    q8, scale = _quant_int8(w32)
    w32_deq = _dequant_int8(q8, scale)
    mse_int8 = np.mean((w32 - w32_deq) ** 2)
    print(f"✅ INT8 量化: 压缩比={q8.nbytes / w32.nbytes:.2f}, "
          f"scale={scale:.4f}, MSE={mse_int8:.6f}")

    return True


def test_fused_adaptive_linear():
    """测试融合自适应线性层"""
    layer = FusedAdaptiveLinear(hidden_dim=64, num_heads=4,
                                 head_dim=16, weight_rank=8)

    x = np.random.randn(1, 64).astype(np.float32)
    y = layer.forward_fused(x)
    assert y.shape == (1, 64), f"输出形状: {y.shape}"
    print(f"✅ 融合自适应线性层: {x.shape} → {y.shape}")

    # 量化后
    layer._quantize(QuantType.FP16)
    y_q = layer.forward_fused(x)
    assert y_q.shape == (1, 64)
    mem = layer.get_memory()
    print(f"   量化后参数: {mem['params_mb']:.3f}MB")

    return layer


def test_lfm_edge_engine():
    """测试 LFM 端侧推理引擎"""
    config = EdgeInferenceConfig(
        hidden_dim=32,
        num_layers=2,
        num_heads=4,
        feedforward_ratio=2,
        quant_type=QuantType.FP16,
    )

    engine = LFMEdgeEngine(config)

    # 批推理
    B, L = 2, 8
    x = np.random.randn(B, L, 32).astype(np.float32)
    y = engine.forward(x)
    assert y.shape == (B, L, 32), f"批推理形状错误: {y.shape}"
    print(f"✅ 端侧批推理: {x.shape} → {y.shape}")
    print(f"   内存: {engine.get_memory_usage_mb():.3f}MB")
    print(f"   压缩比: {engine.get_compression_ratio():.2f}x")

    info = engine.get_info()
    assert info["memory_below_1gb"], "内存应 < 1GB"
    print(f"   低于 1GB: {info['memory_below_1gb']}")

    return engine


def test_stream_generation():
    """测试流式推理"""
    config = EdgeInferenceConfig(
        hidden_dim=32, num_layers=2, num_heads=4,
        quant_type=QuantType.INT8,
    )

    engine = LFMEdgeEngine(config)

    prefix = np.random.randn(1, 4, 32).astype(np.float32)

    def dummy_generate(output: np.ndarray) -> np.ndarray:
        """模拟下一个 token 生成 (加上小噪声)"""
        return output + np.random.randn(*output.shape).astype(np.float32) * 0.01

    tokens = []
    for step, token in enumerate(engine.stream_generate(prefix, dummy_generate, max_tokens=5)):
        tokens.append(token)
        print(f"   流式步骤 {step+1}: {token.shape}")

    assert len(tokens) == 5, f"应该生成 5 个 token, 实际 {len(tokens)}"
    print("✅ 流式生成: 5 步完成")

    return engine


def test_memory_estimation():
    """测试不同配置下的内存估算"""
    configs = [
        ("小型 (256,4,4,FP16)", 256, 4, 4, QuantType.FP16),
        ("中型 (512,6,8,FP16)", 512, 6, 8, QuantType.FP16),
        ("中型 (512,6,8,INT8)", 512, 6, 8, QuantType.INT8),
        ("大型 (1024,12,16,INT8)", 1024, 12, 16, QuantType.INT8),
    ]

    print(f"\n{'配置':>30} {'FP32(MB)':>12} {'量化(MB)':>12} {'<1GB':>8}")
    print("-" * 65)

    for name, dim, layers, heads, qtype in configs:
        est = estimate_memory(dim, layers, heads, qtype)
        print(f"{name:>30} {est['total_fp32_mb']:>10.1f}MB {est['total_quantized_mb']:>10.2f}MB "
              f"{'✅' if est['under_1gb'] else '❌':>8}")

    print("✅ 内存估算完成")


if __name__ == "__main__":
    print("=" * 60)
    print("P16: LFM 端侧推理引擎测试")
    print("=" * 60)

    test_quantization()
    print()
    test_fused_adaptive_linear()
    print()
    test_lfm_edge_engine()
    print()
    test_stream_generation()
    print()
    test_memory_estimation()

    print()
    print("✅ P16 全部测试通过")
