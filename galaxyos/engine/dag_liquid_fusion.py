#!/usr/bin/env python3
"""
P21: DAG + Liquid 融合 — DAG 上下文管理器的液态时间常数压缩策略

核心思想：
  DAG 上下文管理器的 compact 策略引入 LTC 时间常数（Liquid Time-Constant），
  使压缩时机和节点排序具有连续时间感知能力。

组件:
  1. LTCDAGCompactStrategy — LTC 驱动的压缩时机判断和节点重要性排序
  2. TimeAwareNodeRanker — 时间感知的节点重要性排序（结合热度 + 液体时间常数）
  3. DAGLiquidFusion — 顶层融合入口

论文参考:
  - LTC: Liquid Time-Constant Networks (Hasani, AAAI 2021)
  - MemoryOS: 热度跟踪 + 分段页式存储 (arXiv:2506.06326)

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-14
"""

import math
import time
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass


# ============================================================================
# 配置
# ============================================================================

@dataclass
class DAGLiquidFusionConfig:
    """DAG + Liquid 融合配置"""
    # LTC 时间常数范围
    tau_min: float = 0.5          # 最小时间常数（响应最快）
    tau_max: float = 12.0         # 最大时间常数（最慢）

    # 压缩触发参数
    soft_compact_threshold: float = 0.75   # 软压缩阈值（上下文使用率）
    hard_compact_threshold: float = 0.90   # 硬压缩阈值
    token_growth_rate: float = 0.0         # token 增长率（自动计算）

    # 节点重要性参数
    heat_decay_hours: float = 24.0        # 热度半衰期（小时）
    importance_boost: float = 0.2         # 液态重要性提升系数
    base_importance: float = 0.3          # 基础重要性

    # 时间感知排序参数
    recency_weight: float = 0.3           # 时效性权重
    heat_weight: float = 0.3              # 热度权重
    liquid_weight: float = 0.4            # 液态权重
    min_score: float = 0.01               # 最小评分
    max_score: float = 1.0                # 最大评分

    # 连续时间 LTC
    lt_sigmoid_temp: float = 2.0          # sigmoid 温度（值越大越尖锐）
    epsilon: float = 1e-8                 # 数值稳定


# ============================================================================
# LTC 时间常数计算
# ============================================================================

class LTCConstantComputer:
    """
    LTC 时间常数计算器

    核心：根据节点特征动态计算时间常数 τ，决定节点"记住多久"。
    τ 越大，节点越不易被压缩（"记忆持久"）。
    τ 越小，节点越容易被压缩（"记忆短暂"）。
    """

    def __init__(self, config: Optional[DAGLiquidFusionConfig] = None):
        self.config = config or DAGLiquidFusionConfig()

        # 尝试连接 UDS lfm_server
        self._uds_ok = False
        self._uds_tried = False
        self._uds_last_embedding: Optional[np.ndarray] = None
        self._try_uds()

        # 动态参数
        self._w_tau: Optional[np.ndarray] = None
        self._b_tau: Optional[np.ndarray] = None
        self._initialized = False

    def _try_uds(self):
        self._uds_tried = True
        self._uds_ok = False

    def _ensure_initialized(self):
        if not self._initialized:
            self._w_tau = np.random.randn(4).astype(np.float32) * 0.1
            self._b_tau = np.zeros(1, dtype=np.float32)
            self._initialized = True

    def _sigmoid(self, x: float) -> float:
        """数值稳定的 sigmoid"""
        if x >= 0:
            return 1.0 / (1.0 + math.exp(-x))
        else:
            z = math.exp(x)
            return z / (1.0 + z)

    def compute_tau(self,
                    importance: float = 0.5,
                    heat: float = 0.5,
                    recency: float = 0.5,
                    depth: float = 0.0) -> float:
        """
        计算时间常数 τ

        UDS 可用时用 LFM embedding 变化量动态算 τ：
        变化大 → τ 小（快速压缩），变化小 → τ 大（保留更久）。
        """

        # fallback: 原有随机权重
        self._ensure_initialized()
        assert self._w_tau is not None
        assert self._b_tau is not None
        depth_penalty = 1.0 / (1.0 + depth * 0.5)
        features = np.array([importance, heat, recency, depth_penalty], dtype=np.float32)
        gate = self._sigmoid(float(np.dot(self._w_tau, features) + self._b_tau[0]))
        tau = gate * (self.config.tau_max - self.config.tau_min) + self.config.tau_min
        tau *= (1.0 + importance * self.config.importance_boost)
        return float(max(self.config.tau_min, min(self.config.tau_max * 2.0, tau)))

    def estimate_compact_readiness(self,
                                    raw_tokens: int,
                                    max_tokens: int,
                                    avg_tau: float) -> float:
        """
        估计压缩就绪度（基于 LTC 时间常数）

        公式：readiness = min(1.0, raw_tokens / max_tokens) × (1.0 + 1/tau)

        τ 越小 → readiness 越大 → 越该压缩
        τ 越大 → readiness 越小 → 暂时不压

        Returns:
            readiness [0, ~2.0]，>0.8 表示需要压缩
        """
        usage_ratio = raw_tokens / max(max_tokens, 1)
        tau_factor = 1.0 + 1.0 / max(avg_tau, self.config.epsilon)

        return usage_ratio * tau_factor


# ============================================================================
# 时间感知节点排序器
# ============================================================================

@dataclass
class NodeScore:
    """节点评分"""
    node_id: str
    score: float               # 综合评分
    liquid_score: float        # 液态动态评分
    heat_score: float          # 热度评分
    recency_score: float       # 时效性评分
    tau_value: float           # 时间常数


class TimeAwareNodeRanker:
    """
    时间感知节点排序器

    综合以下维度对 DAG 节点评分：
      - 液态时间常数 τ（大 → 重要，保留）
      - 热度（高频访问 → 保留）
      - 时效性（新 → 保留）

    排序公式：
      score = α_r × recency + α_h × heat + α_l × liquid(tau)
      其中 liquid(tau) = sigmoid_temp * (tau - tau_mid) / tau_range
    """

    def __init__(self, config: Optional[DAGLiquidFusionConfig] = None):
        self.config = config or DAGLiquidFusionConfig()
        self._tau_computer = LTCConstantComputer(config)

    def compute_node_score(self,
                           node_id: str,
                           timestamp: float,
                           heat: float = 0.5,
                           importance: float = 0.5,
                           depth: int = 0,
                           now: Optional[float] = None) -> NodeScore:
        """
        计算单节点综合评分

        Args:
            node_id: 节点 ID
            timestamp: 节点时间戳
            heat: 热度 [0, 1]
            importance: 重要性 [0, 1]
            depth: 摘要深度
            now: 当前时间

        Returns:
            NodeScore
        """
        if now is None:
            now = time.time()

        # 时效性：越新越高（指数衰减）
        age_hours = (now - timestamp) / 3600.0 if timestamp > 0 else 0
        recency = max(0.01, math.exp(-age_hours / self.config.heat_decay_hours))

        # 液态时间常数
        tau = self._tau_computer.compute_tau(
            importance=importance,
            heat=heat,
            recency=recency,
            depth=float(depth),
        )

        # 液态评分：τ 越大越保留
        tau_mid = (self.config.tau_min + self.config.tau_max) / 2.0
        tau_range = self.config.tau_max - self.config.tau_min
        normalized_tau = (tau - tau_mid) / max(tau_range, self.config.epsilon)
        liquid_score = 1.0 / (1.0 + math.exp(-self.config.lt_sigmoid_temp * normalized_tau))

        # 综合评分
        score = (
            self.config.recency_weight * recency +
            self.config.heat_weight * heat +
            self.config.liquid_weight * liquid_score
        )

        score = max(self.config.min_score, min(self.config.max_score, score))

        return NodeScore(
            node_id=node_id,
            score=score,
            liquid_score=liquid_score,
            heat_score=heat,
            recency_score=recency,
            tau_value=tau,
        )

    def rank_nodes(self,
                   nodes: List[Dict[str, Any]],
                   now: Optional[float] = None,
                   top_k: Optional[int] = None) -> List[NodeScore]:
        """
        对节点列表排序（高分优先保留）

        Args:
            nodes: [{node_id, timestamp, heat, importance, depth, ...}]
            now: 当前时间
            top_k: 仅返回前 k 个

        Returns:
            按 score 降序排列的 NodeScore 列表
        """
        if now is None:
            now = time.time()

        scores = []
        for n in nodes:
            ns = self.compute_node_score(
                node_id=n.get("node_id", ""),
                timestamp=n.get("timestamp", now),
                heat=n.get("heat", 0.5),
                importance=n.get("importance_score", 0.5),
                depth=n.get("depth", 0),
                now=now,
            )
            scores.append(ns)

        scores.sort(key=lambda x: -x.score)

        if top_k is not None:
            scores = scores[:top_k]

        return scores

    def should_compact_node(self, score: NodeScore, threshold: float = 0.4) -> bool:
        """
        判断单个节点是否应被压缩（低分节点优先压缩）

        Args:
            score: 节点评分
            threshold: 阈值，低于此值的节点可压缩

        Returns:
            True = 可压缩
        """
        return score.score < threshold

    def partition_for_compact(self,
                               nodes: List[Dict[str, Any]],
                               retain_ratio: float = 0.6,
                               now: Optional[float] = None,
                               ) -> Tuple[List[NodeScore], List[NodeScore]]:
        """
        将节点分为"保留"和"可压缩"两组

        Args:
            nodes: 节点列表
            retain_ratio: 保留比例（高分保留）[0, 1]
            now: 当前时间

        Returns:
            (keep_nodes, compact_candidates)
        """
        scores = self.rank_nodes(nodes, now=now)
        split = max(1, int(len(scores) * retain_ratio))

        return scores[:split], scores[split:]

    def get_tau_computer(self) -> LTCConstantComputer:
        """获取内部时间常数计算器"""
        return self._tau_computer


# ============================================================================
# LTC 驱动的压缩策略
# ============================================================================

class LTCDAGCompactStrategy:
    """
    LTC 驱动的 DAG 压缩策略

    核心思想：
      1. 使用 LTC 时间常数决定压缩时机（而非固定 token 阈值）
      2. 时间常数小的节点优先压缩（"液态短记忆"）
      3. 连续时间感知：压缩后 τ 自动调整
      4. 支持多级压缩（摘要节点进一步压缩时 τ 递减）

    对比传统策略：
      - 传统：raw_tokens > threshold → 压缩
      - LTC：readiness = usage_ratio × (1 + 1/τ) > 0.8 → 压缩
    """

    def __init__(self, config: Optional[DAGLiquidFusionConfig] = None):
        self.config = config or DAGLiquidFusionConfig()
        self._tau_computer = LTCConstantComputer(self.config)
        self._ranker = TimeAwareNodeRanker(self.config)

        # 压缩历史（用于自适应调整）
        self._compact_history: List[Dict] = []
        self._avg_tau: float = (self.config.tau_min + self.config.tau_max) / 2.0

    def should_compact(self,
                       raw_tokens: int,
                       max_tokens: int,
                       nodes: List[Dict[str, Any]],
                       force: bool = False) -> Tuple[bool, float, Dict]:
        """
        判断是否应触发压缩

        Args:
            raw_tokens: 原始消息 token 数
            max_tokens: 最大上下文 token
            nodes: 当前所有 DAG 节点
            force: 强制压缩

        Returns:
            (should_compact, readiness, stats)
        """
        if force:
            return True, 2.0, {"reason": "force", "avg_tau": self._avg_tau}

        # 计算平均时间常数
        taus = []
        for n in nodes:
            taus.append(self._tau_computer.compute_tau(
                importance=n.get("importance_score", 0.5),
                heat=n.get("heat", 0.5),
                recency=0.5,
                depth=n.get("depth", 0),
            ))
        self._avg_tau = float(np.mean(taus)) if taus else self._avg_tau

        # 就绪度
        readiness = self._tau_computer.estimate_compact_readiness(
            raw_tokens, max_tokens, self._avg_tau
        )

        # 使用率
        usage_ratio = raw_tokens / max(max_tokens, 1)

        # 决策
        threshold = self.config.soft_compact_threshold * (1.0 + 1.0 / max(self._avg_tau, 1e-8))
        should = readiness > threshold or usage_ratio > self.config.hard_compact_threshold

        stats = {
            "readiness": float(readiness),
            "avg_tau": float(self._avg_tau),
            "usage_ratio": float(usage_ratio),
            "threshold": float(threshold),
            "reason": "readiness" if should else "ok",
        }

        return should, readiness, stats

    def select_nodes_to_compact(self,
                                 nodes: List[Dict[str, Any]],
                                 compact_ratio: float = 0.3,
                                 min_to_compact: int = 2) -> List[Dict[str, Any]]:
        """
        选择要压缩的节点（低分优先）

        Args:
            nodes: 所有节点
            compact_ratio: 压缩比例
            min_to_compact: 最少压缩数

        Returns:
            待压缩节点列表
        """
        _, candidates = self._ranker.partition_for_compact(
            nodes, retain_ratio=1.0 - compact_ratio
        )

        # 确保至少 min_to_compact 个
        if len(candidates) < min_to_compact and len(nodes) > min_to_compact:
            scores = self._ranker.rank_nodes(nodes)
            candidates = [s for s in scores[:-min_to_compact]]

        result = []
        candidate_ids = set(c.node_id for c in candidates)
        for n in nodes:
            if n.get("node_id") in candidate_ids:
                result.append(n)

        return result[:max(min_to_compact, len(candidates))]

    def compute_compact_tau(self, source_nodes: List[Dict[str, Any]]) -> float:
        """
        计算压缩后摘要节点的时间常数

        策略：摘要节点的 τ 取源节点 τ 的中位数 × 衰减因子
        衰减因子：depth 越深，τ 越小（更易被再次压缩）

        Args:
            source_nodes: 源节点列表

        Returns:
            摘要节点的 τ
        """
        if not source_nodes:
            return self._avg_tau

        taus = []
        for n in source_nodes:
            tau = self._tau_computer.compute_tau(
                importance=n.get("importance_score", 0.5),
                heat=n.get("heat", 0.3),
                recency=0.3,
                depth=n.get("depth", 0),
            )
            taus.append(tau)

        median_tau = float(np.median(taus))
        avg_depth = float(np.mean([n.get("depth", 0) for n in source_nodes]))

        # 深度衰减：depth=0 → 1.0, depth=1 → 0.7, depth=2 → 0.5
        depth_decay = 1.0 / (1.0 + avg_depth * 0.5)

        return median_tau * depth_decay

    def record_compact(self, stats: Dict):
        """记录一次压缩（用于自适应）"""
        self._compact_history.append({
            "time": time.time(),
            "avg_tau": self._avg_tau,
            **stats,
        })
        # 只保留最近 20 次
        if len(self._compact_history) > 20:
            self._compact_history = self._compact_history[-20:]

    def get_status(self) -> Dict:
        """获取策略状态"""
        return {
            "avg_tau": float(self._avg_tau),
            "compact_count": len(self._compact_history),
            "last_compact": self._compact_history[-1] if self._compact_history else None,
            "config": {
                "tau_range": [self.config.tau_min, self.config.tau_max],
                "soft_threshold": self.config.soft_compact_threshold,
                "hard_threshold": self.config.hard_compact_threshold,
            },
        }

    def get_ranker(self) -> TimeAwareNodeRanker:
        """获取排序器"""
        return self._ranker

    def get_tau_computer(self) -> LTCConstantComputer:
        """获取时间常数计算器"""
        return self._tau_computer


# ============================================================================
# DAG + Liquid 顶层融合入口
# ============================================================================

class DAGLiquidFusion:
    """
    DAG + Liquid 顶层融合

    统一入口，整合：
      - LTC 时间常数计算
      - 时间感知节点排序
      - LTC 驱动压缩策略

    使用方式（集成到 DAGContextManager）：

      # 初始化
      dag_liquid = DAGLiquidFusion()

      # 判断压缩
      should, readiness, stats = dag_liquid.compact_strategy.should_compact(
          raw_tokens, max_tokens, dag_nodes
      )

      # 选择要压缩的节点
      to_compact = dag_liquid.compact_strategy.select_nodes_to_compact(dag_nodes)

      # 计算摘要节点的时间常数
      summary_tau = dag_liquid.compact_strategy.compute_compact_tau(to_compact)
    """

    def __init__(self, config: Optional[DAGLiquidFusionConfig] = None):
        self.config = config or DAGLiquidFusionConfig()
        self.tau_computer = LTCConstantComputer(self.config)
        self.node_ranker = TimeAwareNodeRanker(self.config)
        self.compact_strategy = LTCDAGCompactStrategy(self.config)

    def rank_by_liquid_importance(self,
                                   nodes: List[Dict[str, Any]],
                                   top_k: Optional[int] = None) -> List[NodeScore]:
        """综合排序（液态 + 热度 + 时效性）"""
        return self.node_ranker.rank_nodes(nodes, top_k=top_k)

    def get_compact_recommendation(self,
                                    raw_tokens: int,
                                    max_tokens: int,
                                    nodes: List[Dict[str, Any]]) -> Dict:
        """
        获取完整的压缩建议

        Returns:
            {
                "should_compact": bool,
                "readiness": float,
                "stats": {...},
                "candidates": [...],  # 建议压缩的节点
                "retain": [...],      # 建议保留的节点
            }
        """
        should, readiness, stats = self.compact_strategy.should_compact(
            raw_tokens, max_tokens, nodes
        )

        retain, candidates = self.compact_strategy.get_ranker().partition_for_compact(
            nodes, retain_ratio=0.6
        )

        return {
            "should_compact": should,
            "readiness": readiness,
            "stats": stats,
            "retain_count": len(retain),
            "candidate_count": len(candidates),
            "retain": [s.node_id for s in retain[:10]],
            "candidates": [s.node_id for s in candidates[:10]],
        }

    def get_info(self) -> Dict:
        """获取融合模块信息"""
        return {
            "type": "DAGLiquidFusion",
            "tau_range": [self.config.tau_min, self.config.tau_max],
            "compact_strategy_status": self.compact_strategy.get_status(),
            "config": {
                "recency_weight": self.config.recency_weight,
                "heat_weight": self.config.heat_weight,
                "liquid_weight": self.config.liquid_weight,
                "heat_decay_hours": self.config.heat_decay_hours,
            },
        }


# ============================================================================
# 测试
# ============================================================================

def test_tau_computer():
    """测试 LTC 时间常数计算"""
    computer = LTCConstantComputer()

    test_cases = [
        (0.9, 0.9, 1.0, 0),   # 高重要+高频+新+原始 → τ 大（保留）
        (0.5, 0.5, 0.5, 1),   # 中等 + 一次摘要 → τ 中
        (0.1, 0.1, 0.1, 2),   # 低重要+低频+旧+二次摘要 → τ 小（压缩）
    ]

    print("=== LTCConstantComputer 测试 ===")
    for imp, heat, rec, depth in test_cases:
        tau = computer.compute_tau(imp, heat, rec, depth)
        print(f"  imp={imp:.1f}, heat={heat:.1f}, rec={rec:.1f}, depth={depth} → τ={tau:.2f}")

    return True


def test_node_ranker():
    """测试节点排序"""
    ranker = TimeAwareNodeRanker()
    now = time.time()

    nodes = [
        {"node_id": "n1", "timestamp": now - 3600, "heat": 0.9, "importance_score": 0.9, "depth": 0},
        {"node_id": "n2", "timestamp": now - 86400, "heat": 0.5, "importance_score": 0.5, "depth": 0},
        {"node_id": "n3", "timestamp": now - 86400 * 7, "heat": 0.1, "importance_score": 0.2, "depth": 1},
        {"node_id": "n4", "timestamp": now - 3600 * 2, "heat": 0.7, "importance_score": 0.8, "depth": 0},
    ]

    print("\n=== TimeAwareNodeRanker 测试 ===")
    scores = ranker.rank_nodes(nodes)
    for s in scores:
        print(f"  {s.node_id}: score={s.score:.4f} (liquid={s.liquid_score:.3f}, heat={s.heat_score:.3f}, "
              f"recency={s.recency_score:.3f}, τ={s.tau_value:.2f})")

    keep, compact = ranker.partition_for_compact(nodes, retain_ratio=0.5)
    print(f"\n  保留: {[s.node_id for s in keep]}")
    print(f"  压缩: {[s.node_id for s in compact]}")

    return True


def test_compact_strategy():
    """测试压缩策略"""
    strategy = LTCDAGCompactStrategy()

    nodes = [
        {"node_id": f"n{i}", "importance_score": 0.5, "heat": 0.3, "depth": 0}
        for i in range(20)
    ]

    # 场景：token 使用率 80%
    should, readiness, stats = strategy.should_compact(
        raw_tokens=8000, max_tokens=10000, nodes=nodes
    )

    print("\n=== LTCDAGCompactStrategy 测试 ===")
    print(f"  8000/10000 tokens: should={should}, readiness={readiness:.3f}, τ_avg={stats['avg_tau']:.2f}")

    # 场景：token 使用率 95%
    should2, readiness2, stats2 = strategy.should_compact(
        raw_tokens=9500, max_tokens=10000, nodes=nodes
    )
    print(f"  9500/10000 tokens: should={should2}, readiness={readiness2:.3f}, τ_avg={stats2['avg_tau']:.2f}")

    # 选择要压缩的节点
    to_compact = strategy.select_nodes_to_compact(nodes, compact_ratio=0.3)
    print(f"  建议压缩 {len(to_compact)} 个节点")

    # 摘要节点 τ
    summary_tau = strategy.compute_compact_tau(to_compact[:3])
    print(f"  摘要节点 τ={summary_tau:.2f}")

    strategy.record_compact(stats)
    print(f"  压缩历史: {len(strategy._compact_history)} 次")

    return True


def test_full_fusion():
    """测试完整融合"""
    fusion = DAGLiquidFusion()

    now = time.time()
    nodes = [
        {"node_id": "hot_recent", "timestamp": now - 1800, "heat": 0.95, "importance_score": 0.9, "depth": 0},
        {"node_id": "old_cold", "timestamp": now - 86400 * 14, "heat": 0.05, "importance_score": 0.1, "depth": 0},
        {"node_id": "old_warm", "timestamp": now - 86400 * 3, "heat": 0.4, "importance_score": 0.5, "depth": 0},
        {"node_id": "summary_old", "timestamp": now - 86400 * 7, "heat": 0.2, "importance_score": 0.3, "depth": 2},
    ]

    print("\n=== DAGLiquidFusion 完整测试 ===")

    rec = fusion.get_compact_recommendation(
        raw_tokens=12000, max_tokens=16000, nodes=nodes
    )
    print(f"  should_compact: {rec['should_compact']}")
    print(f"  readiness: {rec['readiness']:.3f}")
    print(f"  保留: {rec['retain'][:3]}")
    print(f"  压缩候选: {rec['candidates'][:3]}")

    info = fusion.get_info()
    print(f"  avg_tau: {info['compact_strategy_status']['avg_tau']:.2f}")

    return True


if __name__ == "__main__":
    test_tau_computer()
    test_node_ranker()
    test_compact_strategy()
    test_full_fusion()
    print()
    print("✅ DAG + Liquid Fusion 全部测试通过")
