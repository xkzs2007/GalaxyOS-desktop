#!/usr/bin/env python3
"""
SSM 状态预测器 — 轻量时序记忆热度预测

基于: Mamba / State Space Model 思想，CPU 友好实现

核心思路:
  每个 memory_id 的检索历史视为时间序列信号
  → 指数衰减 + 自激励（Hawkes 过程近似）
  → 预测"下次应该被召回的概率"和"最佳召回时间窗口"
  → 跟 NeuralMemoryGate 的共现预测互补

公式:
  activation(t) = sum( e^(-(t - ti) / tau) for ti in recall_times )
  intensity(t) = base_rate + alpha * activation(t)  // 自激励
  surprise = |actual_activation - predicted_activation| / predicted_activation

跟 Titans 门控的区别:
  - RecallPatternPredictor: 基于共现（跟谁一起出现）
  - SSMPredictor: 基于时间模式（什么时候出现、趋势如何）
  - 两者输出取加权平均 → 更准确的惊讶度

Layer: L0 (底层预测器)
Author: GalaxyOS
版本: 1.0.0
创建: 2026-06-09
"""

import json
import os
import time
import math
import logging
import threading
from typing import Dict, List, Optional, Tuple, Any, Set
from collections import defaultdict, deque
from pathlib import Path

import numpy as np

logger = logging.getLogger("ssm_state_predictor")

# ============================================================================
# 时间序列处理器
# ============================================================================

class TimeSeriesBuffer:
    """轻量时间序列缓冲区 — 每个 memory_id 保存最近检索时间戳

    用 ring buffer 结构，避免无限增长。
    默认保存最近 100 次检索时间。
    """

    def __init__(self, max_per_memory: int = 100):
        self.max_per_memory = max_per_memory
        self._data: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_per_memory)
        )
        self._lock = threading.Lock()

    def record(self, memory_id: str, timestamp: Optional[float] = None) -> None:
        """记录一次检索事件"""
        ts = timestamp or time.time()
        with self._lock:
            self._data[memory_id].append(ts)

    def batch_record(self, memory_ids: List[str], timestamp: Optional[float] = None) -> None:
        """批量记录检索"""
        ts = timestamp or time.time()
        with self._lock:
            for mid in memory_ids:
                self._data[mid].append(ts)

    def get_timestamps(self, memory_id: str) -> List[float]:
        """获取指定 memory_id 的检索时间戳（从新到旧）"""
        with self._lock:
            d = self._data.get(memory_id)
            if not d:
                return []
            return sorted(d, reverse=True)

    def get_all_ids(self) -> Set[str]:
        with self._lock:
            return set(self._data.keys())

    def size(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._data.values())

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            if not self._data:
                return {"unique_memories": 0, "total_events": 0}
            return {
                "unique_memories": len(self._data),
                "total_events": sum(len(v) for v in self._data.values()),
                "avg_events_per_memory": sum(len(v) for v in self._data.values()) / len(self._data),
            }


# ============================================================================
# SSM 预测器核心
# ============================================================================

class SSMStatePredictor:
    """SSM 状态预测器 — 时序记忆热度预测

    对每个 memory_id 维护一个激活强度模型:
      activation(t) = sum( decay_scale * e^(-(t - ti) / tau) for ti in recall_times )

    其中:
      tau = 衰减时间常数（默认 1 小时 = 3600s）
      decay_scale = 每次检索的衰减起始强度

    预测输出:
      - current_activation: 当前激活强度
      - predicted_next_window: 预测下次被召回的期望时间窗口（秒）
      - trend: "rising" | "falling" | "stable" — 趋势方向

    用法:
        predictor = SSMStatePredictor()

        # 每次检索召回后
        predictor.record_recall(recalled_ids)

        # 查询某个记忆的热度
        act = predictor.get_activation(memory_id)

        # 批量查询
        results = predictor.batch_predict(memory_ids)

        # 跟 NeuralMemoryGate 联动
        surprise = predictor.compute_temporal_surprise(memory_id)
    """

    def __init__(
        self,
        tau: float = 3600.0,          # 衰减时间常数（秒），默认 1 小时
        decay_scale: float = 1.0,      # 每次检索的衰减起始强度
        base_rate: float = 0.001,      # 基础检索率
        alpha: float = 0.5,            # 自激励系数
        activation_window: float = 86400.0,  # 激活窗口（秒），默认 24 小时
    ):
        self.tau = tau
        self.decay_scale = decay_scale
        self.base_rate = base_rate
        self.alpha = alpha
        self.activation_window = activation_window

        self._buffer = TimeSeriesBuffer(max_per_memory=200)
        self._lock = threading.Lock()

        # 统计
        self._total_records = 0
        self._unique_memories = 0

    # ── 记录 ──────────────────────────────────────────────────────────

    def record_recall(self, memory_ids: List[str]) -> None:
        """记录一次检索召回"""
        self._buffer.batch_record(memory_ids)
        with self._lock:
            self._total_records += len(memory_ids)
            self._unique_memories = len(self._buffer.get_all_ids())

    # ── 激活强度计算 ────────────────────────────────────────────────

    def get_activation(self, memory_id: str, now: Optional[float] = None) -> float:
        """计算指定记忆的当前激活强度

        activation = sum( decay_scale * e^(-(t - ti) / tau) for ti in recalls_within_window )
        """
        now = now or time.time()
        timestamps = self._buffer.get_timestamps(memory_id)
        if not timestamps:
            return 0.0

        # 只算激活窗口内的
        window_start = now - self.activation_window
        relevant = [ts for ts in timestamps if ts >= window_start]
        if not relevant:
            return 0.0

        activation = 0.0
        for ts in relevant:
            dt = now - ts
            activation += self.decay_scale * math.exp(-dt / self.tau)

        return activation

    def get_intensity(self, memory_id: str, now: Optional[float] = None) -> float:
        """计算检索强度（含自激励）

        intensity = base_rate + alpha * activation
        """
        act = self.get_activation(memory_id, now)
        return self.base_rate + self.alpha * act

    def get_trend(self, memory_id: str, now: Optional[float] = None) -> str:
        """判断趋势方向

        通过比较前一半窗口和后一半窗口的平均激活强度来判断。
        """
        now = now or time.time()
        timestamps = self._buffer.get_timestamps(memory_id)
        if len(timestamps) < 3:
            return "stable"

        window_start = now - self.activation_window
        relevant = sorted([ts for ts in timestamps if ts >= window_start])
        if len(relevant) < 3:
            return "stable"

        mid = len(relevant) // 2
        early = relevant[:mid]
        late = relevant[mid:]

        if not early or not late:
            return "stable"

        early_act = sum(math.exp(-(now - ts) / self.tau) for ts in early) / len(early)
        late_act = sum(math.exp(-(now - ts) / self.tau) for ts in late) / len(late)

        ratio = late_act / (early_act + 1e-8)
        if ratio > 1.3:
            return "rising"
        elif ratio < 0.7:
            return "falling"
        return "stable"

    # ── 预测 ──────────────────────────────────────────────────────────

    def predict(self, memory_id: str, now: Optional[float] = None) -> Dict[str, Any]:
        """预测指定记忆的状态

        Returns:
            {
                "memory_id": str,
                "activation": float,
                "intensity": float,
                "trend": "rising" | "falling" | "stable",
                "recall_count_window": int,  # 激活窗口内的检索次数
                "predicted_next_window": float,  # 预测下次召回的期望时间（秒）
                "decay_progress": float,  # 0~1, 衰减进度
            }
        """
        now = now or time.time()
        activation = self.get_activation(memory_id, now)
        intensity = self.get_intensity(memory_id, now)
        trend = self.get_trend(memory_id, now)

        timestamps = self._buffer.get_timestamps(memory_id)
        window_start = now - self.activation_window
        relevant = [ts for ts in timestamps if ts >= window_start]

        # 预测下次召回的期望时间窗口
        # 基于平均间隔: 如果有 >= 2 次检索
        predicted_next = float("inf")
        if len(relevant) >= 2:
            intervals = [relevant[i] - relevant[i+1] for i in range(len(relevant)-1)]
            avg_interval = np.mean(intervals)
            last_time = relevant[0]
            time_since_last = now - last_time
            remaining = max(0, avg_interval - time_since_last)
            predicted_next = round(remaining, 1)

        # 衰减进度: 距离上次检索越久越接近 1
        decay_progress = 0.0
        if relevant:
            time_since_last = now - relevant[0]
            decay_progress = min(1.0, time_since_last / (self.tau * 3))

        return {
            "memory_id": memory_id,
            "activation": round(activation, 6),
            "intensity": round(intensity, 6),
            "trend": trend,
            "recall_count_window": len(relevant),
            "predicted_next_window": predicted_next,
            "decay_progress": round(decay_progress, 4),
        }

    def batch_predict(
        self, memory_ids: Optional[List[str]] = None, top_k: int = 20, now: Optional[float] = None
    ) -> Dict[str, Any]:
        """批量预测

        Args:
            memory_ids: 指定列表，None 则预测所有已记录的
            top_k: 返回 Top K 高激活 + Top K 低激活

        Returns:
            {
                "hot": [...],   # 高激活（即将被召回）
                "cold": [...],  # 低激活（可能被遗忘）
                "stats": {...},
            }
        """
        now = now or time.time()
        ids = memory_ids or list(self._buffer.get_all_ids())

        results = []
        for mid in ids:
            r = self.predict(mid, now)
            results.append(r)

        # 按激活强度排序
        results.sort(key=lambda x: -x["activation"])

        hot = results[:top_k]
        cold = [r for r in results if r["activation"] < 0.5 and r["trend"] == "falling"][:top_k]

        return {
            "hot": hot,
            "cold": cold,
            "stats": {
                "total": len(results),
                "rising": sum(1 for r in results if r["trend"] == "rising"),
                "falling": sum(1 for r in results if r["trend"] == "falling"),
                "stable": sum(1 for r in results if r["trend"] == "stable"),
            },
        }

    # ── 跟 NeuralMemoryGate 的联动接口 ──────────────────────────────

    def compute_temporal_surprise(self, memory_id: str, now: Optional[float] = None) -> float:
        """计算时序惊讶度

        当记忆在"不应该被召回"的时候被召回 → 惊讶度高。
        基于强度预测: 低强度的记忆突然被召回 → 惊讶。

        0.0 ~ 1.0
        """
        now = now or time.time()
        intensity = self.get_intensity(memory_id, now)

        # 强度越低 + 趋势下降中 → 如果此时被召回，惊讶度高
        trend = self.get_trend(memory_id, now)
        if trend == "falling" and intensity < 0.5:
            return 0.8  # 意想不到的召回
        elif trend == "falling" and intensity < 1.0:
            return 0.5
        elif trend == "stable" and intensity > 2.0:
            return 0.1  # 预期之中
        else:
            return 0.3  # 中等

    def get_temporal_modulator(self, memory_id: str, now: Optional[float] = None) -> float:
        """返回时序调制器（-1 ~ 1），供 Titans 门控组合使用

        - rising + 高激活 → 正调制（加强）
        - falling + 低激活 → 负调制（衰减）
        """
        now = now or time.time()
        activation = self.get_activation(memory_id, now)
        trend = self.get_trend(memory_id, now)

        if trend == "rising" and activation > 1.0:
            return 0.5
        elif trend == "falling" and activation < 0.5:
            return -0.4
        elif trend == "rising":
            return 0.2
        elif trend == "falling":
            return -0.2
        return 0.0

    # ── 统计 ──────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_records": self._total_records,
                "unique_memories": self._unique_memories,
                "buffer_stats": self._buffer.get_stats(),
                "params": {
                    "tau_hours": round(self.tau / 3600, 2),
                    "decay_scale": self.decay_scale,
                    "base_rate": self.base_rate,
                    "alpha": self.alpha,
                },
            }


# ============================================================================
# 组合预测器 — 共现 + 时序融合
# ============================================================================

class CompositePredictor:
    """组合预测器：融合 RecallPatternPredictor（共现）和 SSMStatePredictor（时序）

    最终惊讶度 = w_ssm * temporal_surprise + w_cooc * cooc_surprise
    """

    def __init__(
        self,
        w_ssm: float = 0.4,
        w_cooc: float = 0.6,
        tau: float = 3600.0,
    ):
        from services.neural_memory_gate import RecallPatternPredictor

        self.ssm = SSMStatePredictor(tau=tau)
        self.cooc = RecallPatternPredictor()
        self.w_ssm = w_ssm
        self.w_cooc = w_cooc
        self._lock = threading.Lock()

    def record_recall(self, memory_ids: List[str]) -> None:
        """同时记录到两个预测器"""
        self.ssm.record_recall(memory_ids)
        self.cooc.record_recall(memory_ids)

    def predict(self, query_ids: List[str], memory_ids: List[str]) -> Dict[str, Any]:
        """综合预测 + 计算融合惊讶度

        Args:
            query_ids: 本次查询的 memory_id（给共现预测器）
            memory_ids: 实际召回结果（给时序预测器）

        Returns:
            {
                "surprise": float,          # 融合惊讶度
                "ssm_surprise": float,      # 时序惊讶度
                "cooc_surprise": float,     # 共现惊讶度
                "ssm_modulator": float,     # 时序调制器
                "trends": {...},            # 时序趋势摘要
            }
        """
        # 共现惊讶度
        predicted_cooc = self.cooc.predict(query_ids, top_k=5)
        if memory_ids:
            cooc_surprise = 1 - len(set(predicted_cooc) & set(memory_ids)) / len(set(memory_ids))
        else:
            cooc_surprise = 0.0

        # 时序惊讶度（取所有实际召回记忆的平均）
        ssm_surprise = 0.0
        trends = {}
        if memory_ids:
            surprises = []
            for mid in memory_ids:
                s = self.ssm.compute_temporal_surprise(mid)
                surprises.append(s)
                trends[mid] = self.ssm.predict(mid)
            ssm_surprise = np.mean(surprises) if surprises else 0.0

        # 融合
        combined = self.w_ssm * ssm_surprise + self.w_cooc * cooc_surprise

        # 首个时序调制器
        ssm_mod = 0.0
        for mid in memory_ids:
            m = self.ssm.get_temporal_modulator(mid)
            ssm_mod = max(ssm_mod, m)

        return {
            "surprise": round(combined, 4),
            "ssm_surprise": round(ssm_surprise, 4),
            "cooc_surprise": round(cooc_surprise, 4),
            "ssm_modulator": round(ssm_mod, 4),
            "trends": trends,
        }


# ============================================================================
# 命令行入口
# ============================================================================

def demo():
    """模拟检索序列演示 SSM 预测器"""
    import random

    pred = SSMStatePredictor(tau=30.0, activation_window=120.0)  # 快速衰减便于演示

    # 模拟检索序列: 某些记忆频繁出现，某些偶尔
    print("=== 模拟检索序列 ===")
    hot_memories = ["mem_hot_A", "mem_hot_B", "mem_hot_C"]
    cold_memories = ["mem_cold_X", "mem_cold_Y"]

    for i in range(20):
        batch = []
        # 热记忆频繁出现
        batch.extend(random.sample(hot_memories, 2))
        # 冷记忆偶尔出现
        if random.random() < 0.2:
            batch.append(random.choice(cold_memories))
        # 模拟时间流逝
        if i > 0:
            time.sleep(0.05)  # 50ms
        pred.record_recall(batch)

    print("\n=== 热记忆状态 ===")
    for mid in hot_memories:
        r = pred.predict(mid)
        print(f"  {mid}: 激活={r['activation']:.4f}, 趋势={r['trend']}, 近期召回={r['recall_count_window']}")

    print("\n=== 冷记忆状态 ===")
    for mid in cold_memories:
        r = pred.predict(mid)
        print(f"  {mid}: 激活={r['activation']:.4f}, 趋势={r['trend']}, 近期召回={r['recall_count_window']}")

    print("\n=== 批量预测（高激活 vs 低激活）===")
    batch = pred.batch_predict(top_k=5)
    print(f"  Hot (前{len(batch['hot'])}): {[r['memory_id'] for r in batch['hot']]}")
    print(f"  Cold (前{len(batch['cold'])}): {[r['memory_id'] for r in batch['cold']]}")
    print(f"  统计: {batch['stats']}")

    print("\n=== 时序惊讶度 ===")
    for mid in hot_memories[:1] + cold_memories[:1]:
        s = pred.compute_temporal_surprise(mid)
        m = pred.get_temporal_modulator(mid)
        print(f"  {mid}: 惊讶度={s:.3f}, 调制器={m:.3f}")


def main():
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        demo()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
