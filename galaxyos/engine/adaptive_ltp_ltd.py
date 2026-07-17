#!/usr/bin/env python3
"""
自适应 LTP/LTD 模块

论文参考:
- Hebbian Learning: A Review (Nature Neuroscience)
- Spike-Timing-Dependent Plasticity (STDP)

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime, timedelta
import math


@dataclass
class SynapseState:
    """突触状态"""
    weight: float
    reinforcement_count: int
    last_reinforced: datetime
    importance: float
    created_at: datetime


@dataclass
class LTP_LTDParams:
    """LTP/LTD 参数"""
    base_ltp_strength: float = 0.1
    base_ltd_rate: float = 0.01
    decay_threshold_days: int = 7
    max_weight: float = 1.0
    min_weight: float = 0.0
    importance_preservation: float = 0.5  # 重要记忆保留系数

    # 艾宾浩斯遗忘曲线参数
    ebbinghaus_base_s: float = 3.0       # 基础记忆强度S（天数）
    ebbinghaus_min_s: float = 1.0        # 最小S值（最弱记忆）
    ebbinghaus_max_s: float = 60.0       # 最大S值（最强记忆）
    ebbinghaus_reinforce_boost: float = 2.0  # 每次强化S的乘数


class AdaptiveLTP_LTD:
    """
    自适应 LTP/LTD 机制

    基于 Hebbian Learning 和 Ebbinghaus 遗忘曲线:
    1. 突触强度应根据使用频率动态调整
    2. 遗忘遵循艾宾浩斯指数衰减规律：R = e^(-t/S)
    3. 重要记忆应该保留更久（记忆强度S更大）

    创新点:
    - LTP 强度根据当前权重、激活频率、时间间隔动态计算
    - LTD 衰减率基于艾宾浩斯指数衰减模型
    - 每次强化后记忆强度S倍增，曲线趋平缓
    """

    # 默认参数
    DEFAULT_PARAMS = LTP_LTDParams()

    def __init__(self, params: Optional[LTP_LTDParams] = None):
        """
        初始化自适应 LTP/LTD

        Args:
            params: 自定义参数
        """
        self.params = params or self.DEFAULT_PARAMS
        self.adjustment_log = []

    def calculate_ltp_strength(
        self,
        synapse: SynapseState,
        context: Optional[Dict] = None,
        modulator: float = 0.0,
    ) -> float:
        """
        动态计算 LTP 强度

        考虑因素:
        1. 当前权重（接近上限时减弱增强）
        2. 激活频率（高频激活时增强效果）
        3. 时间间隔（短间隔内重复激活时增强）
        4. 惊讶度调制器 (v3: Titans 门控): modulator > 0 增强LTP，< 0 削弱

        Args:
            synapse: 突触状态
            context: 额外上下文
            modulator: 惊讶度调制器（-1~1），来自 NeuralMemoryGate

        Returns:
            LTP 强度
        """
        base_strength = self.params.base_ltp_strength

        # 1. 权重因子：接近上限时，减弱增强效果
        # 使用 sigmoid 函数平滑过渡
        weight_ratio = synapse.weight / self.params.max_weight
        weight_factor = 1.0 - math.pow(weight_ratio, 2)  # 二次衰减

        # 2. 激活频率因子：高频激活时增强效果
        # 但避免过度强化
        freq_factor = min(1.0 + synapse.reinforcement_count * 0.05, 1.5)

        # 3. 时间间隔因子：短间隔内重复激活时增强
        now = datetime.now()
        time_since_last = (now - synapse.last_reinforced).total_seconds() / 3600  # 小时

        if time_since_last < 1:  # 1小时内
            time_factor = 1.2
        elif time_since_last < 24:  # 1天内
            time_factor = 1.0
        else:
            time_factor = 0.8  # 超过1天，降低增强效果

        # 4. 重要性因子：重要记忆增强更多
        importance_factor = 1.0 + synapse.importance * 0.3

        # 5. 惊讶度调制器 (v3, Titans 门控)
        # modulator > 0 → 增强（惊讶，新知识），< 0 → 削弱（可预测，已掌握）
        modulator_factor = 1.0 + modulator * 0.5  # [-1, 1] → [0.5, 1.5]

        # 综合计算
        ltp_strength = (
            base_strength *
            weight_factor *
            freq_factor *
            time_factor *
            importance_factor *
            modulator_factor
        )

        # 确保不超过最大权重
        new_weight = synapse.weight + ltp_strength
        if new_weight > self.params.max_weight:
            ltp_strength = self.params.max_weight - synapse.weight

        # 记录日志
        self.adjustment_log.append({
            "type": "ltp",
            "weight_factor": weight_factor,
            "freq_factor": freq_factor,
            "time_factor": time_factor,
            "importance_factor": importance_factor,
            "modulator_factor": modulator_factor,
            "final_strength": ltp_strength
        })

        return max(0, ltp_strength)

    def _calc_memory_strength(self, synapse: SynapseState) -> float:
        """
        计算记忆强度 S（艾宾浩斯曲线参数）

        决定遗忘曲线 R = e^(-t/S) 的陡峭程度。
        强化次数越多、重要性越高 → S 越大 → 曲线越平缓 → 忘得越慢。
        """
        base = self.params.ebbinghaus_base_s
        # 每次强化使 S 倍增
        reinforce = self.params.ebbinghaus_reinforce_boost ** min(synapse.reinforcement_count, 5)
        # 重要性缩放（0~1 → 1~2x）
        importance_scale = 1.0 + synapse.importance
        s = base * reinforce * importance_scale
        return max(self.params.ebbinghaus_min_s, min(s, self.params.ebbinghaus_max_s))

    def _calc_retention(self, synapse: SynapseState, days_unused: float) -> float:
        """
        计算当前保留率（艾宾浩斯遗忘曲线）

        R = e^(-t/S)

        返回 0~1 的保留率。保留率越低，LTD 衰减量越大。
        """
        if days_unused <= 0:
            return 1.0
        S = self._calc_memory_strength(synapse)
        return math.exp(-days_unused / S)

    def calculate_ltd_rate(
        self,
        synapse: SynapseState,
        days_unused: Optional[int] = None
    ) -> float:
        """
        基于艾宾浩斯遗忘曲线计算 LTD 衰减率

        核心逻辑：
        - 遗忘不是匀速发生，而是指数衰减 R = e^(-t/S)
        - S（记忆强度）由回忆次数×重要性决定
        - 每条记忆有独立的遗忘曲线，非固定阈值触发

        Args:
            synapse: 突触状态
            days_unused: 未使用天数（可选，自动计算）

        Returns:
            LTD 衰减率（权重的减少量）
        """
        # 计算未使用天数（精确到小时）
        if days_unused is None:
            now = datetime.now()
            days_unused = (now - synapse.last_reinforced).total_seconds() / 86400

        # 1. 计算当前艾宾浩斯保留率
        retention = self._calc_retention(synapse, days_unused)

        # 2. 期望权重 = 理论保留率 × 原始强度
        # 原始强度 ≈ 最后一次加强后的权重（近似用当前权重+已衰减部分估算）
        decayed_portion = synapse.weight * (1 - retention)

        # 3. LTD 衰减量 = 当前权重 × (1 - 保留率²) × 基础衰减率
        # 用平方放大低保留率的衰减，模拟"快忘期"
        base_rate = self.params.base_ltd_rate
        ltd_rate = synapse.weight * (1 - retention ** 2) * base_rate * 10

        # 4. 确保不低于最小权重
        new_weight = max(synapse.weight - ltd_rate, self.params.min_weight)
        ltd_rate = synapse.weight - new_weight

        # 记录日志
        self.adjustment_log.append({
            "type": "ltd_ebbinghaus",
            "days_unused": round(days_unused, 2),
            "memory_strength_S": round(self._calc_memory_strength(synapse), 2),
            "retention": round(retention, 4),
            "final_rate": round(ltd_rate, 6)
        })

        return max(0, ltd_rate)

    def apply_ltp(
        self,
        synapse: SynapseState,
        context: Optional[Dict] = None
    ) -> SynapseState:
        """
        应用 LTP（长时程增强）

        Args:
            synapse: 突触状态
            context: 额外上下文

        Returns:
            更新后的突触状态
        """
        ltp_strength = self.calculate_ltp_strength(synapse, context)

        return SynapseState(
            weight=min(synapse.weight + ltp_strength, self.params.max_weight),
            reinforcement_count=synapse.reinforcement_count + 1,
            last_reinforced=datetime.now(),
            importance=synapse.importance,
            created_at=synapse.created_at
        )

    def apply_ltd(
        self,
        synapse: SynapseState,
        days_unused: Optional[int] = None
    ) -> SynapseState:
        """
        应用 LTD（长时程抑制）

        Args:
            synapse: 突触状态
            days_unused: 未使用天数

        Returns:
            更新后的突触状态
        """
        ltd_rate = self.calculate_ltd_rate(synapse, days_unused)

        return SynapseState(
            weight=max(synapse.weight - ltd_rate, self.params.min_weight),
            reinforcement_count=synapse.reinforcement_count,
            last_reinforced=synapse.last_reinforced,
            importance=synapse.importance,
            created_at=synapse.created_at
        )

    def get_adjustment_stats(self) -> Dict[str, Any]:
        """获取调整统计"""
        if not self.adjustment_log:
            return {"total_adjustments": 0}

        ltp_logs = [log for log in self.adjustment_log if log["type"] == "ltp"]

        if not ltp_logs:
            return {"total_adjustments": len(self.adjustment_log)}

        strengths = [log["final_strength"] for log in ltp_logs]

        return {
            "total_adjustments": len(self.adjustment_log),
            "ltp_count": len(ltp_logs),
            "avg_ltp_strength": sum(strengths) / len(strengths),
            "max_ltp_strength": max(strengths),
            "min_ltp_strength": min(strengths)
        }


# 便捷函数
def apply_adaptive_ltp(synapse: SynapseState) -> SynapseState:
    """应用自适应 LTP（便捷函数）"""
    adapter = AdaptiveLTP_LTD()
    return adapter.apply_ltp(synapse)


def apply_adaptive_ltd(synapse: SynapseState, days_unused: int = None) -> SynapseState:
    """应用自适应 LTD（便捷函数）"""
    adapter = AdaptiveLTP_LTD()
    return adapter.apply_ltd(synapse, days_unused)


if __name__ == "__main__":
    # 测试
    adapter = AdaptiveLTP_LTD()

    # 创建测试突触
    test_synapses = [
        SynapseState(
            weight=0.5,
            reinforcement_count=5,
            last_reinforced=datetime.now() - timedelta(hours=0.5),
            importance=0.3,
            created_at=datetime.now() - timedelta(days=30)
        ),
        SynapseState(
            weight=0.9,
            reinforcement_count=20,
            last_reinforced=datetime.now() - timedelta(hours=2),
            importance=0.8,
            created_at=datetime.now() - timedelta(days=60)
        ),
        SynapseState(
            weight=0.3,
            reinforcement_count=2,
            last_reinforced=datetime.now() - timedelta(days=10),
            importance=0.1,
            created_at=datetime.now() - timedelta(days=15)
        ),
    ]

    print("=" * 70)
    print("自适应 LTP/LTD 测试")
    print("=" * 70)

    for i, synapse in enumerate(test_synapses):
        print(f"\n【突触 {i+1}】")
        print(f"  初始权重: {synapse.weight:.2f}")
        print(f"  激活次数: {synapse.reinforcement_count}")
        print(f"  重要性: {synapse.importance:.2f}")

        # 计算 LTP
        ltp_strength = adapter.calculate_ltp_strength(synapse)
        print(f"  LTP 强度: {ltp_strength:.4f}")

        # 计算 LTD
        days_unused = (datetime.now() - synapse.last_reinforced).days
        ltd_rate = adapter.calculate_ltd_rate(synapse, days_unused)
        print(f"  LTD 衰减率: {ltd_rate:.4f} (未使用 {days_unused} 天)")

        # 应用 LTP 后的权重
        new_synapse = adapter.apply_ltp(synapse)
        print(f"  LTP 后权重: {new_synapse.weight:.2f}")

    print("\n" + "=" * 70)
    print("调整统计")
    print("=" * 70)
    stats = adapter.get_adjustment_stats()
    print(f"  总调整次数: {stats['total_adjustments']}")
    print(f"  平均 LTP 强度: {stats.get('avg_ltp_strength', 0):.4f}")
