#!/usr/bin/env python3
"""
神经记忆门控 — Test-Time 惊讶度驱动的记忆管理

基于 Titans: Learning to Memorize at Test Time (Google DeepMind, 2025)
arXiv: 2501.00663

核心思路（适配版，非逐字复现）:
  检索召回 → 预测期望召回的模式 → 对比实际召回
    → 惊讶度高 → 新知识 → consolidate（写入 BlobArena / 增强 LTP）
    → 惊讶度低 → 已掌握 → decay（促进 LTD / 标记可淘汰）

跟 Titans 的映射:
  Titans 的 "输入序列"       → GalaxyOS 的 "历史检索记录"
  Titans 的 "token 预测误差" → "检索模式预测误差"
  Titans 的 "神经网络记忆"   → "BlobArena + 突触权重"
  Titans 的 "surprise"      → 当前检索与历史模式差异

Layer: L1 (神经状态管理层)
Author: GalaxyOS
版本: 1.0.0
创建: 2026-06-09
"""

import logging
import threading
from typing import Dict, List, Any
from collections import defaultdict, Counter

import numpy as np

logger = logging.getLogger("neural_memory_gate")

# ============================================================================
# 惊讶度水平枚举
# ============================================================================

class SurpriseLevel:
    LOW = "low"          # 低于阈值 → 常规衰减
    MEDIUM = "medium"    # 接近阈值 → 保留但不加强
    HIGH = "high"        # 超过阈值 → consolidate
    SPIKE = "spike"      # 远超过阈值（3x+）→ 紧急写入

# ============================================================================
# 检索模式预测器
# ============================================================================

class RecallPatternPredictor:
    """检索模式预测器 — 基于共现频率

    工作原理:
      每次检索（recall）记录被召回的 memory_id 列表。
      统计所有 (i, j) 的共现频率。
      当给定 memory_id A 时, 预测跟 A 最常一起被召回的 Top-K 个 memory_id。

    这是 Titans 中 "associative memory loss ||M(k) - v||²"
    的轻量替代: 共现 = 关联强度

    CPU 友好: 全操作 O(n) 或 O(n log n)，无矩阵运算
    """

    def __init__(self, max_cooccurrence: int = 10000, decay_rate: float = 0.01):
        self.max_cooccurrence = max_cooccurrence
        self.decay_rate = decay_rate
        # {memory_id: {other_id: count}}
        self._cooc: Dict[str, Dict[str, int]] = defaultdict(Counter)
        # {memory_id: 总检索次数}
        self._total_recalls: Dict[str, int] = Counter()
        self._global_recall_count = 0
        self._lock = threading.Lock()

    def record_recall(self, memory_ids: List[str]) -> None:
        """记录一次检索召回

        Args:
            memory_ids: 这次检索召回的所有 memory_id 列表
        """
        if not memory_ids or len(memory_ids) < 2:
            return

        with self._lock:
            self._global_recall_count += 1
            for mid in memory_ids:
                self._total_recalls[mid] += 1

            # 更新共现矩阵
            unique_ids = list(set(memory_ids))
            for i in range(len(unique_ids)):
                a = unique_ids[i]
                for j in range(i + 1, len(unique_ids)):
                    b = unique_ids[j]
                    self._cooc[a][b] = self._cooc[a].get(b, 0) + 1
                    self._cooc[b][a] = self._cooc[b].get(a, 0) + 1

            # 裁剪过大 Counter（只保留最高频的）
            if len(self._cooc) > self.max_cooccurrence:
                # 删除总召回次数最少的 10%
                to_remove = sorted(
                    self._cooc.keys(),
                    key=lambda k: self._total_recalls.get(k, 0)
                )[:len(self._cooc) // 10]
                for k in to_remove:
                    self._cooc.pop(k, None)
                    self._total_recalls.pop(k, None)

    def predict(self, memory_ids: List[str], top_k: int = 5) -> List[str]:
        """预测应该被召回的 Top-K memory_id

        Args:
            memory_ids: 本次检索的 query 对应的 memory_id
            top_k: 返回数量

        Returns:
            预测的 memory_id 列表（按共现强度排序）
        """
        with self._lock:
            scores: Dict[str, float] = defaultdict(float)
            for mid in memory_ids:
                for other, count in self._cooc.get(mid, {}).items():
                    if other not in memory_ids:
                        # 使用 PMI 风格得分: count / total_recalls(mid)
                        total = self._total_recalls.get(mid, 1)
                        scores[other] += count / total

            sorted_ids = sorted(scores.items(), key=lambda x: -x[1])
            return [mid for mid, _ in sorted_ids[:top_k]]

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "unique_memories": len(self._cooc),
                "total_records": self._global_recall_count,
                "total_edges": sum(len(v) for v in self._cooc.values()) // 2,
            }


# ============================================================================
# 检索惊讶度计算器
# ============================================================================

class RetrievalSurpriseCalculator:
    """检索惊讶度计算

    核心:
      predicted = 基于历史共现预测的 Top-K memory_id 集合
      actual = 实际召回结果

      surprise = 1 - |predicted ∩ actual| / |actual|
        (0 = 全部命中 → 不惊讶, 1 = 全部新 → 非常惊讶)

    比 ||predicted - actual||² 更适合离散数据
    """

    def __init__(self, history_size: int = 200, k: float = 1.5):
        self._history: List[float] = []
        self._history_size = history_size
        self.k = k
        self._threshold: float = 0.5
        self._lock = threading.Lock()

    def compute(self, predicted: List[str], actual: List[str]) -> float:
        """计算惊讶度: 实际召回中有多少不在预测中

        Returns:
            0.0 ~ 1.0
            0 = 完全预测命中, 1 = 全部超预期
        """
        if not actual:
            return 0.0
        predicted_set = set(predicted)
        actual_set = set(actual)
        novel = actual_set - predicted_set
        return len(novel) / len(actual_set)

    def record(self, surprise: float) -> None:
        with self._lock:
            self._history.append(surprise)
            if len(self._history) > self._history_size:
                self._history.pop(0)

            if len(self._history) >= 10:
                mu = np.mean(self._history)
                sigma = np.std(self._history) + 1e-8
                self._threshold = mu + self.k * sigma

    @property
    def threshold(self) -> float:
        return self._threshold

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            if self._history:
                return {
                    "mean": float(np.mean(self._history)),
                    "std": float(np.std(self._history)),
                    "threshold": self._threshold,
                    "count": len(self._history),
                }
            return {"mean": 0.0, "std": 0.0, "threshold": self._threshold, "count": 0}


# ============================================================================
# 记忆门控核心
# ============================================================================

class NeuralMemoryGate:
    """神经记忆门控 — 检索惊讶度驱动的记忆管理

    数据流:
        DAG/突触网络 检索召回
          → 记录实际召回结果 record_recall()
          → 预测下次召回的 Top-K predict()
          → 对比实际召回 compute()
            → 惊讶度过高 → consolidate 信号（新知识）
            → 惊讶度过低 → decay 信号（已掌握）

    用法:
        gate = NeuralMemoryGate()

        # 每次检索召回后记录
        gate.record_recall(recalled_ids)  # 训练预测器

        # 下次检索前预测期望结果
        predicted = gate.predict_recall(query_ids)

        # 检索后计算惊讶度
        result = gate.compute_surprise(predicted, actual_recalled_ids)
        if result["action"] == "consolidate":
            # 写入 BlobArena / 增强 LTP
            pass

    与 Titans 的关键区别:
        Titans 在 token 级别做预测 → 我们在 检索/记忆 级别做预测
        Titans 用梯度下降更新记忆 → 我们用共现矩阵
        Titans 的 surprise = ||M(k) - v||² → 我们的 surprise = (1 - recall_precision)
    """

    def __init__(
        self,
        prediction_top_k: int = 5,
        surprise_k: float = 1.5,
        history_size: int = 200,
    ):
        self.predictor = RecallPatternPredictor()
        self.surprise = RetrievalSurpriseCalculator(
            history_size=history_size, k=surprise_k
        )
        self._prediction_top_k = prediction_top_k

        # 统计
        self._total = 0
        self._consolidate_count = 0
        self._decay_count = 0
        self._spike_count = 0
        self._lock = threading.Lock()

    # ── 公开 API ──────────────────────────────────────────────────────

    def record_recall(self, memory_ids: List[str]) -> None:
        """记录一次检索召回（训练预测器）

        每次 DAG/突触网络检索成功后调用。
        """
        self.predictor.record_recall(memory_ids)

    def predict_recall(self, query_memory_ids: List[str]) -> List[str]:
        """预测本次检索应该召回哪些 memory_id

        Args:
            query_memory_ids: query 对应的 memory_id（可能不止一个）

        Returns:
            预测的 Top-K memory_id
        """
        return self.predictor.predict(
            query_memory_ids, top_k=self._prediction_top_k
        )

    def compute_surprise(
        self,
        predicted_ids: List[str],
        actual_ids: List[str],
    ) -> Dict[str, Any]:
        """计算检索结果的惊讶度并返回门控信号

        Args:
            predicted_ids: predict_recall 的预测结果
            actual_ids: 实际检索召回结果

        Returns:
            {
                "surprise": 0.0~1.0,
                "threshold": 自适应阈值,
                "level": SurpriseLevel,
                "action": "consolidate" | "decay" | "none",
                "ltp_modulator": float,   # -1 ~ 1
                "novel_ids": [str],       # 实际召回中超出预测的 ID
            }
        """
        with self._lock:
            self._total += 1
            surprise = self.surprise.compute(predicted_ids, actual_ids)
            self.surprise.record(surprise)

            threshold = self.surprise.threshold
            novel_set = set(actual_ids) - set(predicted_ids)

            if surprise > threshold * 3.0:
                level = SurpriseLevel.SPIKE
                action = "consolidate"
                ltp_mod = 1.0
                self._spike_count += 1
                self._consolidate_count += 1
            elif surprise > threshold * 1.5:
                level = SurpriseLevel.HIGH
                action = "consolidate"
                ltp_mod = 0.6
                self._consolidate_count += 1
            elif surprise > threshold:
                level = SurpriseLevel.MEDIUM
                action = "none"
                ltp_mod = 0.2
            else:
                level = SurpriseLevel.LOW
                action = "decay"
                ltp_mod = -0.3
                self._decay_count += 1

            return {
                "surprise": round(surprise, 4),
                "threshold": round(threshold, 4),
                "level": level,
                "action": action,
                "ltp_modulator": ltp_mod,
                "novel_ids": list(novel_set)[:10],
            }

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total": self._total,
                "consolidate": self._consolidate_count,
                "decay": self._decay_count,
                "spike": self._spike_count,
                "predictor": self.predictor.get_stats(),
                "surprise": self.surprise.get_stats(),
            }

    def get_state(self) -> Dict[str, Any]:
        """序列化门控状态（用于持久化）"""
        with self._lock:
            return {
                "total": self._total,
                "consolidate_count": self._consolidate_count,
                "decay_count": self._decay_count,
                "spike_count": self._spike_count,
                "surprise_threshold": self.surprise.threshold,
                "surprise_history": self.surprise._history[-100:] if hasattr(self.surprise, '_history') else [],
            }

    def load_state(self, state: Dict[str, Any]) -> None:
        """恢复门控状态（从持久化）"""
        # 简单恢复统计（预测器的共现矩阵需另外持久化）
        with self._lock:
            self._total = state.get("total", 0)
            self._consolidate_count = state.get("consolidate_count", 0)
            self._decay_count = state.get("decay_count", 0)
            self._spike_count = state.get("spike_count", 0)


# ============================================================================
# 集成示例 / 命令行入口
# ============================================================================

def demo():
    """用模拟数据演示惊讶度计算"""
    gate = NeuralMemoryGate()

    # 模拟历史检索模式：某些 memory_id 经常一起出现
    print("=== 训练预测器 ===")
    for _ in range(50):
        core_set = ["mem_A", "mem_B", "mem_C"]
        var_set = np.random.choice(["mem_D", "mem_E", "mem_F", "mem_G", "mem_H"], 2).tolist()
        gate.record_recall(core_set + var_set)

    # 模拟一次检索
    print("\n=== 检索: 输入 mem_A, mem_B ===")
    predicted = gate.predict_recall(["mem_A", "mem_B"])
    print(f"预测召回: {predicted}")

    # 场景1: 实际召回了预期内容
    actual_expected = predicted + ["mem_X"]
    r1 = gate.compute_surprise(predicted, actual_expected)
    print(f"\n场景1 (预期+1个新): 惊讶度={r1['surprise']}, action={r1['action']}")
    print(f"  新颖ID: {r1['novel_ids']}")

    # 场景2: 实际全是新内容
    r2 = gate.compute_surprise(predicted, ["mem_X", "mem_Y", "mem_Z"])
    print(f"\n场景2 (全新内容): 惊讶度={r2['surprise']}, action={r2['action']}")
    print(f"  新颖ID: {r2['novel_ids']}")

    # 场景3: 实际就是预测的结果
    r3 = gate.compute_surprise(predicted, predicted)
    print(f"\n场景3 (完全命中): 惊讶度={r3['surprise']}, action={r3['action']}")

    print("\n=== 统计 ===")
    for k, v in gate.get_stats().items():
        print(f"  {k}: {v}")


def main():
    import sys
    if "--demo" in sys.argv:
        demo()
    else:
        demo()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
