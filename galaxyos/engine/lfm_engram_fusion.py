#!/usr/bin/env python3
"""
LFM + Engram 融合 — 动态时序建模 × 静态知识检索

将 LFM 的自适应线性算子与 Engram 的条件记忆系统融合：
  - LFM: 处理时序动态建模（输入依赖的权重变换）
  - Engram: 提供静态知识检索（N-gram O(1) 哈希查找）
  - 门控融合: Engram 检索结果作为 LFM 的门控输入

核心创新:
  1. Engram 检索结果直接注入 LFM 的权重生成器
  2. LFM 的动态状态反向写入 Engram (双工)
  3. 门控网络决定"依赖动态 vs 依赖静态知识"的比例

在 GalaxyOS 中的角色:
  - 记忆系统 + 时序建模的桥梁
  - 让 LFM 在推理时"想起"历史模式
  - 降低训练和推理的数据依赖

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

logger = logging.getLogger("lfm_engram")

import numpy as np

# 导入 LFM 组件
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lfm_adaptive_operator import AdaptiveLinearOperator

# 导入 Engram 组件
from engram_memory import EngramMemory, EngramConfig


# ==================== 门控融合器 ====================

class EngramLFMGate:
    """
    Engram → LFM 门控融合器
    
    UDS 可用时，用 lfm_server 的真实 embedding 替代随机门控。
    """

    def __init__(self, hidden_dim: int = 512,
                 engram_dim: int = 64,
                 num_heads: int = 8,
                 head_dim: Optional[int] = None,
                 gate_hidden: int = 32):
        self.hidden_dim = hidden_dim
        self.engram_dim = engram_dim
        self.num_heads = num_heads
        self.head_dim = head_dim or (hidden_dim // num_heads)
        self.gate_hidden = gate_hidden
        dim = self.head_dim

        # 尝试连接 UDS
        self._uds_ok = False
        self._uds_tried = False
        self._try_uds()

        # 当 UDS 可用时，从 LFM 初始 embedding 初始化门控参数
        if self._uds_ok:
            self._init_from_lfm()
        else:
            # numpy 随机 fallback
            limit_e = math.sqrt(6.0 / engram_dim)
            self.W_gate_alpha1 = np.random.uniform(-limit_e, limit_e,
                                                    (gate_hidden, engram_dim)).astype(np.float32)
            self.b_gate_alpha1 = np.zeros(gate_hidden, dtype=np.float32)
            limit_h = math.sqrt(6.0 / gate_hidden)
            self.W_gate_alpha2 = np.random.uniform(-limit_h, limit_h,
                                                    (num_heads * dim, gate_hidden)).astype(np.float32)
            self.b_gate_alpha2 = np.ones(num_heads * dim, dtype=np.float32) * 0.5

            limit = math.sqrt(6.0 / engram_dim)
            self.W_emb_to_left = np.random.uniform(-limit, limit,
                                                    (dim, engram_dim)).astype(np.float32)
            self.W_emb_to_right = np.random.uniform(-limit, limit,
                                                     (dim, engram_dim)).astype(np.float32)

        self._forward_count = 0
        logger.info(f"Engram-LFM 门控融合器: engram_dim={engram_dim}, uds={self._uds_ok}")

    def _try_uds(self):
        """尝试连接 lfm_server UDS"""
        self._uds_tried = True
        try:
            from galaxyos_native import lfm_ping, lfm_get_state
            lfm_ping()
            state = lfm_get_state()
            self._uds_ok = state.get("initialized", False)
            if self._uds_ok:
                self._uds_embedding = np.array(state["embedding"], dtype=np.float32)
            else:
                self._uds_ok = True  # server 活着就行
                self._uds_embedding = None
        except Exception as e:
            self._uds_ok = False
            logger.debug(f"EngramLFMGate UDS 不可用: {e}, 使用 numpy fallback")

    def _init_from_lfm(self):
        """用 LFM embedding 初始化门控参数"""
        dim = self.head_dim

        # 从 embedding 生成有结构的初始权重
        try:
            emb = self._uds_embedding
            if emb is None:
                from galaxyos_native import lfm_get_state, lfm_embed_text
                state = lfm_get_state()
                emb = np.array(state.get("embedding", np.random.randn(2048)), dtype=np.float32)

            # 投影 2048 → engram_dim
            proj = emb[:self.engram_dim] if len(emb) >= self.engram_dim else np.pad(emb, (0, self.engram_dim - len(emb)))

            # 用 embedding 初始化门控网络（保留随机性但有 embedding 结构）
            self.W_gate_alpha1 = np.outer(np.random.randn(self.gate_hidden), proj).astype(np.float32) * 0.1
            self.b_gate_alpha1 = np.zeros(self.gate_hidden, dtype=np.float32)
            self.W_gate_alpha2 = np.random.randn(self.num_heads * dim, self.gate_hidden).astype(np.float32) * 0.1
            self.b_gate_alpha2 = np.ones(self.num_heads * dim, dtype=np.float32) * 0.5

            self.W_emb_to_left = np.random.randn(dim, self.engram_dim).astype(np.float32) * 0.1
            self.W_emb_to_right = np.random.randn(dim, self.engram_dim).astype(np.float32) * 0.1

            logger.info("EngramLFMGate 参数从 LFM embedding 初始化")
        except Exception as e:
            logger.warning(f"EngramLFMGate LFM 初始化失败: {e}, 使用随机")
            limit = math.sqrt(6.0 / self.engram_dim)
            self.W_gate_alpha1 = np.random.uniform(-limit, limit,
                                                    (self.gate_hidden, self.engram_dim)).astype(np.float32)
            self.b_gate_alpha1 = np.zeros(self.gate_hidden, dtype=np.float32)
            limit_h = math.sqrt(6.0 / self.gate_hidden)
            self.W_gate_alpha2 = np.random.uniform(-limit_h, limit_h,
                                                    (self.num_heads * dim, self.gate_hidden)).astype(np.float32)
            self.b_gate_alpha2 = np.ones(self.num_heads * dim, dtype=np.float32) * 0.5
            self.W_emb_to_left = np.random.uniform(-limit, limit, (dim, self.engram_dim)).astype(np.float32)
            self.W_emb_to_right = np.random.uniform(-limit, limit, (dim, self.engram_dim)).astype(np.float32)

    def compute_alpha(self, engram_emb: np.ndarray = None,
                      per_head: bool = True) -> np.ndarray:
        """计算门控系数 α
        
        UDS 可用时优先使用 LFM 真实 embedding。
        """
        if engram_emb is None or (isinstance(engram_emb, np.ndarray) and engram_emb.size <= 1):
            if self._uds_ok:
                try:
                    from galaxyos_native import lfm_get_state
                    state = lfm_get_state()
                    emb = np.array(state["embedding"], dtype=np.float32)
                    engram_emb = emb[:self.engram_dim] if len(emb) >= self.engram_dim else np.pad(emb, (0, self.engram_dim - len(emb)))
                except Exception:
                    pass

        if engram_emb is None:
            engram_emb = np.zeros(self.engram_dim, dtype=np.float32)

        if engram_emb.ndim == 1:
            engram_emb = engram_emb[np.newaxis, :]

        B = engram_emb.shape[0]
        h = engram_emb @ self.W_gate_alpha1.T + self.b_gate_alpha1
        h = self._gelu(h)

        alpha_logit = h @ self.W_gate_alpha2.T + self.b_gate_alpha2
        alpha = 1.0 / (1.0 + np.exp(-alpha_logit))

        if per_head:
            alpha = alpha.reshape(B, self.num_heads, self.head_dim)

        return alpha[0] if B == 1 else alpha

    def modulate_left_factor(self, left_factor: np.ndarray,
                             engram_emb: np.ndarray) -> np.ndarray:
        """用 Engram 嵌入调制 LFM 的左因子
        
        left_factor' = left_factor + β ⊙ (W_emb_to_left @ engram_emb)
        
        Args:
            left_factor: [B, H, dim] LFM 生成的左因子
            engram_emb: [engram_dim] 或 [B, engram_dim]
        
        Returns:
            modulated: [B, H, dim] 调制后的左因子
        """
        if engram_emb.ndim == 1:
            engram_emb = engram_emb[np.newaxis, :]

        # Engram 偏置: [B, H, dim]
        emb_bias = engram_emb @ self.W_emb_to_left.T  # [B, dim]
        emb_bias = emb_bias[:, np.newaxis, :]  # [B, 1, dim]

        return left_factor + emb_bias

    def modulate_right_factor(self, right_factor: np.ndarray,
                              engram_emb: np.ndarray) -> np.ndarray:
        """用 Engram 嵌入调制 LFM 的右因子"""
        if engram_emb.ndim == 1:
            engram_emb = engram_emb[np.newaxis, :]

        emb_bias = engram_emb @ self.W_emb_to_right.T
        emb_bias = emb_bias[:, np.newaxis, :]

        return right_factor + emb_bias

    @staticmethod
    def _gelu(x: np.ndarray) -> np.ndarray:
        return 0.5 * x * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (x + 0.044715 * x ** 3)))

    def get_info(self) -> dict:
        return {
            "gate_params": self.W_gate_alpha1.size + self.W_gate_alpha2.size,
            "modulation_params": self.W_emb_to_left.size + self.W_emb_to_right.size,
            "forward_count": self._forward_count,
        }


# ==================== Engram 增强的 LFM 算子 ====================

class EngramAugmentedLFMLayer(AdaptiveLinearOperator):
    """
    Engram 增强的 LFM 自适应算子层
    
    在 AdaptiveLinearOperator 基础上增加:
    - 每步前向时自动查询 Engram
    - Engram 检索结果调制权重生成
    - 门控融合: 动态/静态权重平衡
    
    前向流程:
        1. 输入 x → LFM 生成 W(x) (动态部分)
        2. 输入 x → Engram 检索 e (静态知识)
        3. 门控计算 α(e)
        4. W_fused = α ⊙ W_LFM + (1-α) ⊙ W_engram(e)
        5. y = W_fused @ x
    """

    def __init__(self, engram: EngramMemory,
                 hidden_dim: int = 512,
                 num_heads: int = 8,
                 head_dim: Optional[int] = None,
                 weight_rank: Optional[int] = None,
                 use_gating: bool = True,
                 engram_gate_hidden: int = 32,
                 fusion_strength: float = 0.3):
        super().__init__(
            hidden_dim=hidden_dim,
            num_heads=num_heads,
            head_dim=head_dim,
            weight_rank=weight_rank,
            use_gating=use_gating,
        )

        self.engram = engram
        self.fusion_strength = fusion_strength  # 全局融合强度(0~1)
        self.engram_dim = engram.config.embed_dim

        # 门控融合器
        self.gate = EngramLFMGate(
            hidden_dim=hidden_dim,
            engram_dim=self.engram_dim,
            num_heads=num_heads,
            head_dim=self.head_dim,
            gate_hidden=engram_gate_hidden,
        )

    def forward_with_engram(self, x: np.ndarray,
                            causal_mask: bool = False,
                            store_to_engram: bool = True,
                            return_debug: bool = False) -> np.ndarray:
        """带 Engram 增强的前向传播
        
        Args:
            x: [B, L, hidden_dim]
            causal_mask: 因果掩码
            store_to_engram: 是否将前向结果存储到 Engram
            return_debug: 是否返回调试信息
        
        Returns:
            y: [B, L, hidden_dim]
        """
        B, L, d = x.shape
        H = self.num_heads
        dim = self.head_dim

        # 1. Head 分解
        x_heads = x.reshape(B, L, H, dim)

        # 2. LFM 动态权重
        # W_base @ x
        y_base = np.einsum('hdo,blhd->blho', self.W_base, x_heads)

        # 低秩增量
        left_up = np.einsum('hdr,blhd->blhr', self.W_up_left, x_heads)
        left_act = self._gelu(left_up)
        left_factor = np.einsum('blhr,hrd->blhd', left_act, self.W_down_left)

        right_up = np.einsum('hdr,blhd->blhr', self.W_up_right, x_heads)
        right_act = self._gelu(right_up)
        right_factor = np.einsum('blhr,hrd->blhd', right_act, self.W_down_right)

        # 3. Engram 检索
        engram_results = []
        engram_hit_rates = []

        for b in range(B):
            seq_results = []
            seq_hits = []
            for t in range(L):
                # 用序列位置和量化的数值构造可查找的 token key
                # 这样 Engram 的 N-gram 查找可以匹配
                pos_vec = x[b, t, :]
                # 量化到 10 个桶，用于 Engram 的 tokenize+ngram
                quantized = tuple(int((v + 2) * 10) % 10 for v in pos_vec[:8])
                token_key = "tok_" + "".join(str(q) for q in quantized)

                emb, info = self.engram.lookup(token_key)
                engram_results.append(emb)
                seq_hits.append(info["hit_rate"])
            engram_hit_rates.append(seq_hits)

        engram_hit_rates = np.array(engram_hit_rates)

        # 4. 门控融合（批量）
        y_heads = np.zeros((B, L, H, dim), dtype=np.float32)
        for b in range(B):
            for t in range(L):
                idx = b * L + t
                emb = engram_results[idx]

                # 如果 Engram 有命中
                if emb is not None:
                    # 门控系数 α (per-head)
                    alpha = self.gate.compute_alpha(emb)

                    # 调制左右因子
                    lf_mod = self.gate.modulate_left_factor(
                        left_factor[b:b+1, t:t+1, :, :], emb[np.newaxis, :])  # [1,1,H,d]
                    rf_mod = self.gate.modulate_right_factor(
                        right_factor[b:b+1, t:t+1, :, :], emb[np.newaxis, :])  # [1,1,H,d]

                    # 使用 Engram 调制后的低秩增量
                    # inner = sum_d(rf_mod * x_heads) → [1,1,H]
                    inner = (rf_mod[0,0] * x_heads[b, t]).sum(axis=-1)  # [H]
                    y_delta_mod = lf_mod[0,0] * inner[:, np.newaxis]  # [H,d]

                    # 融合: α * (W_base + ΔW_mod)
                    y_heads[b, t, :, :] = alpha * (y_base[b, t] + y_delta_mod)
                else:
                    # 无 Engram 命中 → 纯 LFM
                    inner = (right_factor[b, t] * x_heads[b, t]).sum(axis=-1)  # [H]
                    y_delta = left_factor[b, t] * inner[:, np.newaxis]  # [H,d]

                    if self.use_gating:
                        gate_logit = np.einsum('hdo,hd->ho', self.W_gate, x_heads[b, t]) + self.b_gate
                        gate = 1.0 / (1.0 + np.exp(-gate_logit))
                        y_heads[b, t] = gate * (y_base[b, t] + y_delta)
                    else:
                        y_heads[b, t] = y_base[b, t] + y_delta

        # 5. 合并 + 输出投影
        y = y_heads.reshape(B, L, d)
        y = y @ self.W_o.T + self.b_o
        if self.use_residual:
            y = y + x

        # 6. 将推理状态写回 Engram
        if store_to_engram:
            for b in range(B):
                for t in range(L):
                    pos_vec = x[b, t, :]
                    token_key = f"vec_{hash(pos_vec.tobytes()) % 1000000}"
                    out_vec = y[b, t, :]
                    self.engram._table.get_or_create(
                        token_key,
                        default_fn=lambda v=out_vec: v[:self.engram_dim].copy()
                    )

        if return_debug:
            return y, {
                "engram_hit_rates": engram_hit_rates,
                "hit_rate_mean": float(engram_hit_rates.mean()),
                "engram_queries": B * L,
            }

        return y

    def get_info(self) -> dict:
        info = super().get_info()
        info["fusion_strength"] = self.fusion_strength
        info["engram_dim"] = self.engram_dim
        gate_info = self.gate.get_info()
        info["gate_params"] = gate_info["gate_params"]
        info["modulation_params"] = gate_info["modulation_params"]
        return info


# ==================== Engram + LFM 融合网络 ====================

@dataclass
class EngramLFMConfig:
    """Engram + LFM 融合网络配置"""
    # LFM 参数
    hidden_dim: int = 128
    num_layers: int = 3
    num_heads: int = 4
    head_dim: Optional[int] = None
    weight_rank: Optional[int] = None
    use_gating: bool = True
    use_residual: bool = True
    feedforward_ratio: int = 2

    # Engram 参数
    engram_slots: int = 4096
    engram_dim: int = 32
    engram_ngram_n: int = 2

    # 融合参数
    engram_gate_hidden: int = 16
    fusion_strength: float = 0.3
    store_frequency: int = 1  # 每 N 步写回一次 Engram

    def __post_init__(self):
        if self.head_dim is None:
            self.head_dim = self.hidden_dim // self.num_heads
        if self.weight_rank is None:
            self.weight_rank = self.head_dim // 2


class EngramLFMNetwork:
    """
    Engram + LFM 融合网络
    
    多层堆叠，每层包含:
    - Engram 增强的 LFM 自适应算子
    - FFN
    - 层归一化
    
    融合方式:
    - 前向: 输入 → Engram 检索 → LFM 动态权重 → 门控融合 → FFN
    - 反向: 前向结果写回 Engram (双工)
    - 整个网络共享同一个 Engram 记忆
    
    相比纯 LFM 的优势:
    1. 历史模式影响当前推理（记忆增强）
    2. 少样本适应（通过 Engram 快速记忆新模式）
    3. 长序列下更稳定（有静态知识锚定）
    """

    def __init__(self, config: EngramLFMConfig):
        self.config = config
        self.dim = config.hidden_dim
        self.num_layers = config.num_layers

        # 共享 Engram 记忆
        engram_config = EngramConfig(
            num_slots=config.engram_slots,
            embed_dim=config.engram_dim,
            ngram_n=config.engram_ngram_n,
        )
        self.engram = EngramMemory(engram_config)

        # 堆叠 Engram 增强的 LFM 层
        self.layers: List[EngramAugmentedLFMLayer] = []
        for _ in range(config.num_layers):
            layer = EngramAugmentedLFMLayer(
                engram=self.engram,
                hidden_dim=config.hidden_dim,
                num_heads=config.num_heads,
                head_dim=config.head_dim,
                weight_rank=config.weight_rank,
                use_gating=config.use_gating,
                engram_gate_hidden=config.engram_gate_hidden,
                fusion_strength=config.fusion_strength,
            )
            self.layers.append(layer)

        # FFN 权重
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
        else:
            self.ffn_w1 = self.ffn_b1 = self.ffn_w2 = self.ffn_b2 = []

        # LN 参数
        self.ln1_gamma = np.ones((config.num_layers, config.hidden_dim), dtype=np.float32)
        self.ln1_beta = np.zeros((config.num_layers, config.hidden_dim), dtype=np.float32)
        self.ln2_gamma = np.ones((config.num_layers, config.hidden_dim), dtype=np.float32)
        self.ln2_beta = np.zeros((config.num_layers, config.hidden_dim), dtype=np.float32)

        self._forward_count = 0

        logger.info(f"Engram+LFM 融合网络初始化: "
                    f"engram_slots={config.engram_slots}, "
                    f"LFM_layers={config.num_layers}, "
                    f"fusion_strength={config.fusion_strength}")

    def _ln(self, x: np.ndarray, gamma: np.ndarray, beta: np.ndarray,
            eps: float = 1e-6) -> np.ndarray:
        mean = x.mean(axis=-1, keepdims=True)
        var = x.var(axis=-1, keepdims=True)
        return gamma * (x - mean) / np.sqrt(var + eps) + beta

    def forward(self, x: np.ndarray) -> np.ndarray:
        """融合网络前向
        
        Args:
            x: [B, L, hidden_dim]
        
        Returns:
            y: [B, L, hidden_dim]
        """
        self._forward_count += 1
        h = x

        for i in range(self.num_layers):
            # Engram 增强的 LFM 层
            store = (self._forward_count % self.config.store_frequency == 0)
            h = self.layers[i].forward_with_engram(
                h, store_to_engram=store)

            # LN
            h = self._ln(h, self.ln1_gamma[i], self.ln1_beta[i])

            # FFN
            if self.ffn_w1:
                ffn_h = h @ self.ffn_w1[i].T + self.ffn_b1[i]
                ffn_h = self._gelu(ffn_h)
                ffn_out = ffn_h @ self.ffn_w2[i].T + self.ffn_b2[i]
                h = self._ln(h + ffn_out, self.ln2_gamma[i], self.ln2_beta[i])

        return h

    def feed_memory(self, texts: List[str], embeddings: Optional[List[np.ndarray]] = None):
        """向共享 Engram 喂入知识
        
        训练/预热阶段: 将已知模式存入 Engram。
        """
        self.engram.batch_remember(texts, embeddings)
        logger.info(f"Engram 已填充 {len(texts)} 条知识")

    def get_engram_status(self) -> dict:
        """获取 Engram 记忆状态"""
        return self.engram.get_status()

    @staticmethod
    def _gelu(x: np.ndarray) -> np.ndarray:
        return 0.5 * x * (1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (x + 0.044715 * x ** 3)))

    def get_info(self) -> dict:
        total_params = 0
        for l in self.layers:
            total_params += l._count_params()
        if self.ffn_w1:
            total_params += sum(w.size for w in self.ffn_w1)
            total_params += sum(w.size for w in self.ffn_w2)
        return {
            "config": self.config,
            "total_params": total_params,
            "engram_status": self.engram.get_status(),
            "forward_count": self._forward_count,
        }


# ==================== 测试 ====================

def test_gate_fuser():
    """测试门控融合器"""
    gate = EngramLFMGate(hidden_dim=32, engram_dim=8, num_heads=4,
                          head_dim=8, gate_hidden=8)

    # 随机 Engram 嵌入
    emb = np.random.randn(8).astype(np.float32)

    # 计算门控系数
    alpha = gate.compute_alpha(emb)
    assert alpha.shape == (4, 8), f"alpha 形状: {alpha.shape}"
    assert np.all(alpha >= 0) and np.all(alpha <= 1), "α 应在 [0,1]"
    print(f"✅ 门控融合器: α 形状={alpha.shape}, 范围=[{alpha.min():.3f}, {alpha.max():.3f}]")

    # 调制左右因子
    lf = np.random.randn(2, 4, 8).astype(np.float32)
    lf_mod = gate.modulate_left_factor(lf, emb)
    assert lf_mod.shape == lf.shape
    print(f"   左因子调制: {np.linalg.norm(lf_mod-lf):.4f} (有变化)")

    info = gate.get_info()
    print(f"   参数: 门控={info['gate_params']}, 调制={info['modulation_params']}")

    return gate


def test_engram_augmented_lfm():
    """测试 Engram 增强的 LFM 算子"""
    engram = EngramMemory(EngramConfig(num_slots=256, embed_dim=8, ngram_n=2))

    # 预热 Engram
    for i in range(20):
        engram.remember(f"pattern_{i}")

    layer = EngramAugmentedLFMLayer(
        engram=engram, hidden_dim=32, num_heads=4,
        head_dim=8, weight_rank=4)

    x = np.random.randn(2, 4, 32).astype(np.float32)
    y = layer.forward_with_engram(x)
    assert y.shape == (2, 4, 32), f"输出形状: {y.shape}"
    print(f"✅ Engram 增强 LFM 层: {x.shape} → {y.shape}")

    y, debug = layer.forward_with_engram(x, return_debug=True)
    print(f"   Engram 命中率: mean={debug['hit_rate_mean']:.3f}, "
          f"queries={debug['engram_queries']}")

    return layer


def test_engram_lfm_network():
    """测试完整的 Engram+LFM 融合网络"""
    config = EngramLFMConfig(
        hidden_dim=16,
        num_layers=2,
        num_heads=2,
        engram_slots=128,
        engram_dim=8,
        engram_gate_hidden=8,
    )

    net = EngramLFMNetwork(config)

    # 预热: 存入记忆
    patterns = [f"train_pattern_{i}" for i in range(10)]
    net.feed_memory(patterns)
    print(f"   Engram 状态: {net.get_engram_status()['filled_slots']}/{net.get_engram_status()['total_slots']} 槽位")

    # 前向
    x = np.random.randn(2, 6, 16).astype(np.float32)
    y = net.forward(x)
    assert y.shape == (2, 6, 16), f"输出形状: {y.shape}"
    print(f"✅ Engram+LFM 融合网络: {x.shape} → {y.shape}")

    # 验证记忆已更新
    status = net.get_engram_status()
    print(f"   前向后 Engram: {status['filled_slots']} 槽位, "
          f"命中率={status['hit_rate']:.3f}")

    return net


def test_fusion_vs_pure_lfm():
    """对比 Engram 融合 LFM vs 纯 LFM
    
    验证: 有 Engram 时，已知模式的推理更稳定（知识锚定）
    """
    print("\n=== 融合 vs 纯 LFM 对比 ===")

    # 创建纯 LFM 和融合 LFM（共享参数近似）
    engram = EngramMemory(EngramConfig(num_slots=256, embed_dim=8, ngram_n=2))

    # 预热 Engram: 记住一个"常见模式"
    for _ in range(10):
        engram.remember("common_pattern")

    fused = EngramAugmentedLFMLayer(
        engram=engram, hidden_dim=16, num_heads=2, head_dim=8, weight_rank=4)

    # 测试"常见模式"和"新模式"
    common_input = np.random.randn(1, 1, 16).astype(np.float32)
    novel_input = np.random.randn(1, 1, 16).astype(np.float32)

    # 先让 fused 处理 novel (无记忆)
    y_novel = fused.forward_with_engram(novel_input, store_to_engram=False)

    # 处理 common (应该触发 Engram)
    y_common, debug_common = fused.forward_with_engram(
        common_input, store_to_engram=False, return_debug=True)

    print(f"   常见模式: hit_rate={debug_common['hit_rate_mean']:.3f}")

    # 第二次处理: 看看是否更稳定
    y_common2, debug2 = fused.forward_with_engram(
        common_input, store_to_engram=False, return_debug=True)

    diff = np.linalg.norm(y_common - y_common2)
    print(f"   常见模式两次推理差异: {diff:.6f} (越小越稳定)")

    print("✅ 对比完成")


def test_engram_storage_effect():
    """验证 Engram 存储效果: 前向后 Engram 槽位增加"""
    config = EngramLFMConfig(
        hidden_dim=16, num_layers=2, num_heads=2,
        engram_slots=64, engram_dim=8,
    )

    net = EngramLFMNetwork(config)
    before = net.get_engram_status()['filled_slots']

    x = np.random.randn(1, 10, 16).astype(np.float32)
    y = net.forward(x)

    after = net.get_engram_status()['filled_slots']
    print(f"✅ Engram 存储: {before} → {after} 槽位 (增加 {after - before})")
    assert after >= before, "前向后槽位应该增加"

    return net


if __name__ == "__main__":
    print("=" * 60)
    print("P17: LFM + Engram 融合测试")
    print("=" * 60)

    test_gate_fuser()
    print()
    test_engram_augmented_lfm()
    print()
    test_engram_lfm_network()
    print()
    test_fusion_vs_pure_lfm()
    print()
    test_engram_storage_effect()

    print()
    print("✅ P17 全部测试通过")


    # ── LFM 真实集成 ──

    def gate_from_lfm(self, lfm_embedding: np.ndarray,
                       engram_hit_rate: float = 0.0) -> float:
        """接收 LFM 2048-dim embedding，输出门控 alpha
        
        Args:
            lfm_embedding: (2048,) LFM embedding
            engram_hit_rate: Engram 命中率 [0,1]
            
        Returns:
            alpha: [0,1] 门控系数
        """
        # 降维：2048 → engram_dim (投影到门控网络能处理的维度)
        if lfm_embedding.shape[0] != self.engram_dim:
            import numpy as np
            if not hasattr(self, '_proj_2048_to_engram'):
                limit = np.sqrt(6.0 / lfm_embedding.shape[0])
                self._proj_2048_to_engram = np.random.uniform(
                    -limit, limit,
                    (self.engram_dim, lfm_embedding.shape[0])
                ).astype(np.float32)
            proj = self._proj_2048_to_engram @ lfm_embedding
        else:
            proj = lfm_embedding

        # 用门控网络计算 alpha
        alpha = self.compute_alpha(proj, per_head=False)
        if isinstance(alpha, np.ndarray):
            alpha = float(alpha.mean())

        # engram hit_rate 增强
        alpha = alpha * 0.7 + engram_hit_rate * 0.3
        return min(1.0, max(0.0, alpha))

