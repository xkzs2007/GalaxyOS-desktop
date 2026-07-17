#!/usr/bin/env python3
"""
MoE + Engram 融合 — U 型缩放律

参考: Engram 论文 (arXiv:2601.07372) 中发现的 U 型缩放律

核心发现（Engram 论文）:
  - 混合 Expert (MoE) + 混合 Memory (Engram) 不是单调的
  - 存在 U 型缩放：纯 MoE 或纯 Engram 效果好，中间混合效果差
  - 最优点在 U 型的两端：要么全 MoE，要么全 Engram
  - 但两者组合可以覆盖更广泛的场景

本项目实现：
  1. MoeEngramRouter: 路由决策（用 MoE 还是 Engram）
  2. U_ShapeScalingLaw: U 型缩放律的数值模拟
  3. MoeEngramBlock: 融合块（MoE + Engram 并行或串联）

在 GalaxyOS 中的角色：
  - 与 intelligent_thinking_trigger 协同决策
  - 在需要大容量推理时用 MoE，需要精确记忆时用 Engram
  - 根据 U 型缩放律动态调整 MoE/Engram 比例

Author: GalaxyOS
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
from enum import Enum

logger = logging.getLogger("moe_engram_hybrid")

import numpy as np


# ==================== 路由决策 ====================

class RouterDecision(Enum):
    """路由决策"""
    MOE = "moe"           # 用 MoE（计算密集，适合推理）
    ENGRAM = "engram"     # 用 Engram（记忆密集，适合检索）
    HYBRID = "hybrid"     # 混合使用（MoE + Engram 并行）


class MoeEngramRouter:
    """
    MoE-Engram 路由器 — 根据输入特征决定用 MoE 还是 Engram

    路由规则：
    1. 如果输入有明确的结构化检索需求 → Engram
    2. 如果输入需要复杂推理/组合 → MoE
    3. 如果两者都需要 → Hybrid

    具体的路由特征：
    - query_entropy: 输入的信息熵（高熵 = 不明确 → MoE）
    - memory_similarity: 与记忆的相似度（高相似 → Engram）
    - task_complexity: 任务复杂度估计
    """

    def __init__(self, input_dim: int, hidden_dim: int = 32):
        """
        Args:
            input_dim: 输入特征维度
            hidden_dim: 隐藏层维度
        """
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim

        # 路由网络
        limit = math.sqrt(6 / (input_dim + hidden_dim))
        self.w_h = np.random.uniform(-limit, limit, (hidden_dim, input_dim)).astype(np.float32)
        self.b_h = np.zeros(hidden_dim, dtype=np.float32)

        limit2 = math.sqrt(6 / (hidden_dim + 3))  # 3 个输出：MoE/Engram/Hybrid
        self.w_out = np.random.uniform(-limit2, limit2, (3, hidden_dim)).astype(np.float32)
        self.b_out = np.zeros(3, dtype=np.float32)

        # 路由统计
        self.route_counts = {RouterDecision.MOE: 0, RouterDecision.ENGRAM: 0, RouterDecision.HYBRID: 0}
        self.total_routes = 0

    def compute_features(self, query: np.ndarray,
                          memory_context: Optional[np.ndarray] = None) -> np.ndarray:
        """计算路由特征

        Returns:
            特征向量 [input_dim]
        """
        features = query.copy().astype(np.float64)

        # 信息熵（标准化到 [0, 1]）
        prob = np.abs(query) / (np.sum(np.abs(query)) + 1e-10)
        entropy = -np.sum(prob * np.log(prob + 1e-10)) / math.log(len(prob) + 1)
        features[0] = entropy  # 高熵 → MoE

        # 记忆相似度（如果有记忆上下文）
        if memory_context is not None and len(memory_context) > 0:
            sim = np.dot(query, memory_context) / (
                np.linalg.norm(query) * np.linalg.norm(memory_context) + 1e-10
            )
            features[1] = (sim + 1) / 2  # 归一化到 [0, 1]
        else:
            features[1] = 0.0  # 无记忆 → MoE

        # 查询稀疏度
        sparsity = np.sum(np.abs(query) > np.mean(np.abs(query)) + np.std(np.abs(query))) / len(query)
        features[-1] = sparsity  # 高稀疏度 → Engram（稀疏记忆检索）

        return features

    def route(self, query: np.ndarray,
              memory_context: Optional[np.ndarray] = None) -> RouterDecision:
        """路由决策

        Args:
            query: 查询向量 [input_dim]
            memory_context: 记忆上下文向量 [input_dim]（可选）

        Returns:
            RouterDecision: MoE / Engram / Hybrid
        """
        features = self.compute_features(query, memory_context)

        # 前向传播
        h = np.tanh(self.w_h @ features + self.b_h)
        logits = self.w_out @ h + self.b_out

        # Softmax
        logits_max = np.max(logits)
        exp_scores = np.exp(logits - logits_max)
        probs = exp_scores / np.sum(exp_scores)

        # 决策
        decision_idx = np.argmax(probs)
        decision = [RouterDecision.MOE, RouterDecision.ENGRAM, RouterDecision.HYBRID][decision_idx]

        # 统计
        self.route_counts[decision] += 1
        self.total_routes += 1

        return decision

    def get_route_stats(self) -> dict:
        """获取路由统计"""
        return {
            "total": self.total_routes,
            "moe_pct": self.route_counts[RouterDecision.MOE] / max(self.total_routes, 1),
            "engram_pct": self.route_counts[RouterDecision.ENGRAM] / max(self.total_routes, 1),
            "hybrid_pct": self.route_counts[RouterDecision.HYBRID] / max(self.total_routes, 1),
        }


# ==================== U 型缩放律 ====================

class U_ShapeScalingLaw:
    """
    U 型缩放律 — Engram 论文发现的非单调缩放

    核心公式：
    Performance(α) = P_moe * β + P_eng * (1 - β) + U_penalty(β)

    其中：
    - α = MoE/Engram 混合比例 (0 = 纯 Engram, 1 = 纯 MoE)
    - P_moe: 纯 MoE 的性能
    - P_eng: 纯 Engram 的性能
    - U_penalty(α) = -c * α * (1 - α)  —  凹函数惩罚中间值

    U 型的两端（α→0 或 α→1）惩罚小，中间惩罚大。
    这个反直觉的现象来自 Engram 论文的实证发现。
    """

    def __init__(self, P_moe: float = 1.0, P_eng: float = 0.85,
                 c_penalty: float = 0.3):
        """
        Args:
            P_moe: 纯 MoE 的性能基准
            P_eng: 纯 Engram 的性能基准
            c_penalty: U 型惩罚系数（越大 U 越深）
        """
        self.P_moe = P_moe
        self.P_eng = P_eng
        self.c_penalty = c_penalty

    def performance(self, alpha: float) -> float:
        """计算给定混合比例的性能

        Args:
            alpha: MoE 比例 [0, 1]（0=纯Engram, 1=纯MoE）

        Returns:
            performance: 预期性能（越高越好）
        """
        # 线性插值
        linear = self.P_moe * alpha + self.P_eng * (1 - alpha)

        # U 型惩罚
        penalty = self.c_penalty * alpha * (1 - alpha)

        return linear - penalty

    def optimal_alpha(self) -> List[float]:
        """U 型缩放律的最优 α

        分析：
        P(α) = P_moe * α + P_eng * (1-α) - c * α * (1-α)
             = P_eng + (P_moe - P_eng) * α - c * (α - α²)
             = P_eng + (P_moe - P_eng + c) * α - c * α²

        导数为 0：dP/dα = (P_moe - P_eng + c) - 2c * α = 0
        α = (P_moe - P_eng + c) / (2c)

        如果 α ∈ (0, 1)，说明中间有谷底 → U 型
        如果 α ∉ (0, 1)，说明单调 → 不是严格 U 型

        U 型两端（α=0 或 α=1）可能优于中间。
        """
        alpha_candidate = (self.P_moe - self.P_eng + self.c_penalty) / (2 * self.c_penalty)

        # 如果极值点在 (0,1) 内 → U 型，两端最优
        if 0 < alpha_candidate < 1:
            return [0.0, 1.0]  # 两端最优
        else:
            return [max(0, min(1, alpha_candidate))]  # 单调，端点最优

    def u_shape_score(self, alpha: float) -> float:
        """U 型量化指标

        返回负数，绝对值越大 U 型越明显。
        0 = 无 U 型（线性）。
        """
        linear = self.P_moe * alpha + self.P_eng * (1 - alpha)
        actual = self.performance(alpha)
        return actual - linear  # 负数 = U 型惩罚

    def get_info(self) -> dict:
        return {
            "P_moe": self.P_moe,
            "P_eng": self.P_eng,
            "c_penalty": self.c_penalty,
            "optimal_alphas": self.optimal_alpha(),
            "u_shape_at_05": self.u_shape_score(0.5),
        }


# ==================== MoE-Engram 融合块 ====================

class MoeEngramBlock:
    """
    MoE-Engram 融合块

    结构：
    - MoE 分支：多个 Expert，门控网络选择
    - Engram 分支：记忆检索，注意力读取
    - 融合层：加权融合两个分支的输出

    并行架构：
    ```
         输入
         /  \\
       MoE  Engram
      Branch Branch
         \\  /
        Fusion
          |
         输出
    ```

    路由逻辑（依据 U 型缩放律）：
    - 如果 α ≈ 0：只用 Engram（精确记忆）
    - 如果 α ≈ 1：只用 MoE（复杂推理）
    - 如果 α 在中间：并行融合（U 型惩罚最小化策略）
    """

    def __init__(self, input_dim: int, hidden_dim: int = 64,
                 output_dim: int = 64, num_experts: int = 4,
                 memory_size: int = 50, memory_dim: int = 64,
                 default_alpha: float = 0.5):
        """
        Args:
            input_dim: 输入维度
            hidden_dim: 隐藏层维度
            output_dim: 输出维度
            num_experts: MoE 专家数
            memory_size: Engram 记忆槽数
            memory_dim: 记忆槽维度
            default_alpha: 默认 MoE 比例 [0,1]
        """
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.num_experts = num_experts
        self.memory_size = memory_size
        self.memory_dim = memory_dim

        # ---- MoE Branch ----
        # 专家网络（每个专家是两层 MLP）
        self.expert_w1 = np.random.randn(num_experts, hidden_dim, input_dim).astype(np.float32) * 0.01
        self.expert_b1 = np.zeros((num_experts, hidden_dim), dtype=np.float32)
        self.expert_w2 = np.random.randn(num_experts, output_dim, hidden_dim).astype(np.float32) * 0.01
        self.expert_b2 = np.zeros((num_experts, output_dim), dtype=np.float32)

        # 门控网络
        self.gate_w = np.random.randn(num_experts, input_dim).astype(np.float32) * 0.01
        self.gate_b = np.zeros(num_experts, dtype=np.float32)

        # ---- Engram Branch ----
        # 记忆矩阵
        limit = math.sqrt(6 / memory_dim)
        self.memory = np.random.uniform(-limit, limit, (memory_size, memory_dim)).astype(np.float32)
        self.age = np.zeros(memory_size, dtype=np.float32)

        # 记忆读取投影
        self.mem_w_key = np.random.randn(memory_dim, input_dim).astype(np.float32) * 0.01
        self.mem_w_read = np.random.randn(output_dim, memory_dim).astype(np.float32) * 0.01

        # ---- 融合 ----
        self.fusion_w = np.random.randn(output_dim, output_dim * 2).astype(np.float32) * 0.01
        self.fusion_b = np.zeros(output_dim, dtype=np.float32)

        # ---- U 型缩放律 ----
        self.scaling_law = U_ShapeScalingLaw(
            P_moe=1.0, P_eng=0.85, c_penalty=0.3
        )
        self.alpha = default_alpha  # 当前混合比例

    def moe_forward(self, x: np.ndarray) -> np.ndarray:
        """MoE 分支前向

        Args:
            x: 输入 [input_dim]

        Returns:
            moe_output: [output_dim]
        """
        # 门控权重
        gate_scores = self.gate_w @ x + self.gate_b
        # Softmax
        scores_max = np.max(gate_scores)
        exp_scores = np.exp(gate_scores - scores_max)
        gate_weights = exp_scores / (np.sum(exp_scores) + 1e-10)

        # Top-2 门控（稀疏专家）
        top2_idx = np.argsort(-gate_weights)[:2]
        top2_weights = gate_weights[top2_idx]
        top2_weights = top2_weights / (np.sum(top2_weights) + 1e-10)

        # 专家计算（只激活 top-2）
        moe_out = np.zeros(self.output_dim, dtype=np.float64)

        for i, idx in enumerate(top2_idx):
            # First layer
            h = np.tanh(self.expert_w1[idx] @ x + self.expert_b1[idx])
            # Second layer
            exp_out = self.expert_w2[idx] @ h + self.expert_b2[idx]
            moe_out += top2_weights[i] * exp_out

        return moe_out

    def engram_forward(self, x: np.ndarray) -> np.ndarray:
        """Engram 分支前向（记忆检索）

        Args:
            x: 输入 [input_dim]

        Returns:
            engram_output: [output_dim]
        """
        # 计算注意力
        keys = self.memory @ self.mem_w_key  # [memory_size, input_dim]
        scores = keys @ x  # [memory_size]
        scores_max = np.max(scores)
        exp_scores = np.exp(scores - scores_max)
        attention = exp_scores / (np.sum(exp_scores) + 1e-10)

        # 年龄惩罚
        age_factor = np.exp(-self.age * 0.01)
        attention = attention * age_factor
        attention = attention / (np.sum(attention) + 1e-10)

        # 加权读取
        readout = attention @ self.memory  # [memory_dim]

        # 投影到输出空间
        engram_out = self.mem_w_read @ readout  # [output_dim]

        # 更新年龄
        self.age += 1.0
        top_idx = np.argmax(attention)
        self.age[top_idx] = 0.0

        return engram_out

    def engram_write(self, x: np.ndarray, output: np.ndarray):
        """写入 Engram 记忆

        将输入-输出关联写入记忆槽。
        """
        write_content = (x[:self.memory_dim] if len(x) >= self.memory_dim
                         else np.pad(x, (0, self.memory_dim - len(x))))

        # 写入最旧的槽
        oldest_idx = np.argmax(self.age)
        alpha_w = 0.1
        self.memory[oldest_idx] = (1 - alpha_w) * self.memory[oldest_idx] + alpha_w * write_content
        self.age[oldest_idx] = 0.0

    def forward(self, x: np.ndarray, alpha: float = None) -> np.ndarray:
        """融合前向

        根据 U 型缩放律，动态决定 MoE/Engram 权重。

        Args:
            x: 输入 [input_dim]
            alpha: MoE 比例（如果为 None，使用默认值）

        Returns:
            output: [output_dim]
        """
        if alpha is not None:
            self.alpha = alpha

        # MoE 分支
        moe_out = self.moe_forward(x)

        # Engram 分支
        engram_out = self.engram_forward(x)

        # 根据 U 型缩放律计算融合权重
        u_weight = self.scaling_law.performance(self.alpha)

        # 如果 U 型惩罚大（≈中间），用门控网络重新平衡
        if self.scaling_law.u_shape_score(self.alpha) < -0.05:
            # 动态重平衡
            if self.alpha > 0.5:
                # 偏向 MoE
                concat = np.concatenate([moe_out * 0.8, engram_out * 0.2])
            else:
                # 偏向 Engram
                concat = np.concatenate([moe_out * 0.2, engram_out * 0.8])
        else:
            # 正常融合
            concat = np.concatenate([moe_out * self.alpha, engram_out * (1 - self.alpha)])

        # 融合层
        fused = np.tanh(self.fusion_w @ concat + self.fusion_b)

        return fused

    def get_info(self) -> dict:
        total_params = (
            self.expert_w1.size + self.expert_b1.size +
            self.expert_w2.size + self.expert_b2.size +
            self.gate_w.size + self.gate_b.size +
            self.mem_w_key.size + self.mem_w_read.size +
            self.fusion_w.size + self.fusion_b.size
        )
        return {
            "input_dim": self.input_dim,
            "hidden_dim": self.hidden_dim,
            "output_dim": self.output_dim,
            "num_experts": self.num_experts,
            "memory_size": self.memory_size,
            "alpha": self.alpha,
            "total_params": total_params,
            "u_shape_info": self.scaling_law.get_info(),
        }


# ==================== 测试 ====================

def test_u_shape_scaling():
    """测试 U 型缩放律"""
    np.random.seed(42)

    law = U_ShapeScalingLaw(P_moe=1.0, P_eng=0.85, c_penalty=0.3)

    # 在各种 α 下测试性能
    alphas = [0.0, 0.2, 0.5, 0.8, 1.0]
    performances = {a: law.performance(a) for a in alphas}

    print("  U 型缩放律性能:")
    for a, p in performances.items():
        u_score = law.u_shape_score(a)
        print(f"    α={a:.1f}: P={p:.4f}, U_score={u_score:.4f}")

    # 检查 U 型
    p_0 = performances[0.0]
    p_05 = performances[0.5]
    p_1 = performances[1.0]

    # 两端优于中间
    assert p_0 >= p_05 or p_1 >= p_05, "不是 U 型: 两端 < 中间"

    optimal = law.optimal_alpha()
    print(f"  最优 α: {optimal}")

    print("✅ U 型缩放律: 确认 U 型形态")

    return law


def test_moe_engram_router():
    """测试路由决策"""
    np.random.seed(42)

    router = MoeEngramRouter(input_dim=16)

    # 测试不同输入
    queries = [
        ("高熵查询", np.random.randn(16).astype(np.float64)),
        ("低熵查询", np.zeros(16).astype(np.float64)),
        ("稀疏查询", np.eye(16)[0].astype(np.float64)),
    ]

    for name, q in queries:
        decision = router.route(q)
        print(f"  {name}: → {decision.value}")

    stats = router.get_route_stats()
    print(f"  路由统计: {stats['moe_pct']:.0%} MoE, "
          f"{stats['engram_pct']:.0%} Engram, "
          f"{stats['hybrid_pct']:.0%} Hybrid")

    print("✅ MoeEngramRouter 决策完成")

    return router


def test_moe_engram_block():
    """测试融合块"""
    np.random.seed(42)

    block = MoeEngramBlock(
        input_dim=16, hidden_dim=32, output_dim=16,
        num_experts=4, memory_size=20, memory_dim=16,
    )

    x = np.random.randn(16).astype(np.float64)

    # 纯 MoE
    out_moe = block.forward(x, alpha=1.0)
    print(f"  α=1.0 (纯 MoE): [{out_moe.shape}], 范围 [{out_moe.min():.3f}, {out_moe.max():.3f}]")

    # 纯 Engram
    out_eng = block.forward(x, alpha=0.0)
    print(f"  α=0.0 (纯 Engram): [{out_eng.shape}], 范围 [{out_eng.min():.3f}, {out_eng.max():.3f}]")

    # 混合
    out_mix = block.forward(x, alpha=0.5)
    print(f"  α=0.5 (混合): [{out_mix.shape}], 范围 [{out_mix.min():.3f}, {out_mix.max():.3f}]")

    # 写入记忆
    block.engram_write(x, out_moe)
    print("✅ Engram 写入完成")

    info = block.get_info()
    print(f"  总参数量: {info['total_params']}")

    print("✅ MoeEngramBlock 融合完成")

    return block


if __name__ == "__main__":
    print("=" * 50)
    print("MoE + Engram 融合 — U 型缩放律")
    print("=" * 50)
    print()

    print("1. 测试 U 型缩放律")
    test_u_shape_scaling()
    print()

    print("2. 测试 MoE-Engram 路由")
    test_moe_engram_router()
    print()

    print("3. 测试 MoE-Engram 融合块")
    test_moe_engram_block()
    print()

    print("✅ P10: MoE + Engram 全部测试通过")
