#!/usr/bin/env python3
"""
entropy_router.py — 基于香农熵的自适应通道权重分配 (MemGAS)

对每个 query 计算各检索通道的香农熵，
低熵（高确定度）通道给高权重，高熵（模糊）通道给低权重。

用法：
    from entropy_router import EntropyRouter
    router = EntropyRouter()
    weights = router.route(query, channel_scores)
"""

import math
from typing import List, Dict, Optional


def softmax(x: List[float], temperature: float = 1.0) -> List[float]:
    """稳定的 softmax 归一化"""
    if not x:
        return []
    max_val = max(x)
    exps = [math.exp((v - max_val) / max(temperature, 1e-6)) for v in x]
    s = sum(exps)
    if s == 0:
        return [1.0 / len(x)] * len(x)
    return [e / s for e in exps]


def entropy(probs: List[float]) -> float:
    """
    计算归一化香农熵。

    H = -sum(p_i * log(p_i + epsilon)) / log(N)
    范围 [0, 1]：
      0 = 完全确定（一个概率接近 1，其余接近 0）
      1 = 完全均匀（所有概率相等）→ 不确定
    """
    n = len(probs)
    if n <= 1:
        return 0.0
    eps = 1e-10
    h = -sum(p * math.log(p + eps) for p in probs)
    h_normalized = h / math.log(n)
    return min(1.0, max(0.0, h_normalized))


class EntropyRouter:
    """
    基于香农熵的自适应通道权重分配。

    MemGAS 思想：对每个 query 计算各通道的熵，
    低熵（高确定度）通道给高权重，高熵（模糊）通道给低权重。
    并用 0.3 * entropy_weight + 0.7 * uniform 平滑防止极端。
    """

    def compute_channel_entropy(self, channel_scores: List[float]) -> float:
        """
        计算单个通道的香农熵（基于该通道内各候选的分数分布）。

        Args:
            channel_scores: 该通道内各候选文档的原始相似度分数列表

        Returns:
            归一化香农熵 [0, 1]，低=确定，高=模糊
        """
        if not channel_scores:
            return 1.0  # 无结果=完全不确定
        probs = softmax(channel_scores)
        return entropy(probs)

    def route(
        self,
        query: str,
        channel_scores: Dict[str, List[float]],
    ) -> Dict[str, float]:
        """
        输入 query + 各通道的初步相似度分数列表，
        输出各通道的融合权重（已 softmax 归一化）。

        Args:
            query: 原始查询文本
            channel_scores: {channel_name: [score1, score2, ...]}
                每个通道的候选 scores，用于计算熵

        Returns:
            {channel_name: weight} 归一化权重，满足 sum(weights) == 1.0
        """
        if not channel_scores:
            return {}

        # 1. 对每个 channel 的 scores 算香农熵
        entropies: Dict[str, float] = {}
        for ch, scores in channel_scores.items():
            entropies[ch] = self.compute_channel_entropy(scores)

        # 2. 熵越低 → 权重越大 (weight = 1 - entropy + eps)
        eps = 1e-6
        raw_weights: Dict[str, float] = {}
        for ch, h in entropies.items():
            raw_weights[ch] = 1.0 - h + eps

        # 3. softmax 归一化得到 entropy_weight
        ch_names = list(raw_weights.keys())
        weight_vals = [raw_weights[ch] for ch in ch_names]
        norm_entropy_weights = softmax(weight_vals, temperature=1.0)
        entropy_weight_map = dict(zip(ch_names, norm_entropy_weights))

        # 4. 平滑：最终权重 = entropy_weight * 0.3 + uniform_weight * 0.7
        n = len(ch_names)
        uniform_weight = 1.0 / n
        result: Dict[str, float] = {}
        for ch in ch_names:
            result[ch] = entropy_weight_map[ch] * 0.3 + uniform_weight * 0.7

        # 5. 再次归一化确保 sum == 1.0
        total = sum(result.values())
        if total > 0:
            for ch in result:
                result[ch] /= total

        return result

    def get_entropies(self, channel_scores: Dict[str, List[float]]) -> Dict[str, float]:
        """
        仅返回各通道的熵值（不计算权重），用于调试和监控。

        Returns:
            {channel_name: entropy_value}
        """
        result = {}
        for ch, scores in channel_scores.items():
            result[ch] = self.compute_channel_entropy(scores)
        return result
