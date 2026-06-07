"""
测试 AdaptiveLTP_LTD — 自适应 LTP/LTD 突触可塑性
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import pytest
from datetime import datetime, timedelta
from services.adaptive_ltp_ltd import (
    AdaptiveLTP_LTD, SynapseState, LTP_LTDParams,
)


class TestSynapseState:
    """突触状态测试"""

    def test_default_creation(self):
        now = datetime.now()
        s = SynapseState(
            weight=0.5, reinforcement_count=3,
            last_reinforced=now, importance=0.7,
            created_at=now,
        )
        assert s.weight == 0.5
        assert s.reinforcement_count == 3
        assert s.importance == 0.7

    def test_minimum_weight(self):
        s = SynapseState(
            weight=0.0, reinforcement_count=0,
            last_reinforced=datetime.now(), importance=0.0,
            created_at=datetime.now(),
        )
        assert s.weight == 0.0


class TestLTP_LTDParams:
    """参数配置测试"""

    def test_default_params(self):
        p = LTP_LTDParams()
        assert p.base_ltp_strength == 0.1
        assert p.base_ltd_rate == 0.01
        assert p.decay_threshold_days == 7
        assert p.max_weight == 1.0
        assert p.min_weight == 0.0
        assert p.importance_preservation == 0.5
        assert p.ebbinghaus_base_s == 3.0

    def test_custom_params(self):
        p = LTP_LTDParams(
            base_ltp_strength=0.2,
            base_ltd_rate=0.05,
            max_weight=2.0,
        )
        assert p.base_ltp_strength == 0.2
        assert p.base_ltd_rate == 0.05
        assert p.max_weight == 2.0
        assert p.decay_threshold_days == 7


class TestAdaptiveLTP_LTD:
    """LTP/LTD 核心机制测试"""

    @pytest.fixture
    def engine(self):
        return AdaptiveLTP_LTD()

    def _make_synapse(self, weight=0.5, days_ago=0, reinforcement=1, importance=0.5):
        ago = timedelta(days=days_ago)
        return SynapseState(
            weight=weight,
            reinforcement_count=reinforcement,
            last_reinforced=datetime.now() - ago,
            importance=importance,
            created_at=datetime.now() - ago * 2,
        )

    # ── LTP 计算 ──

    def test_ltp_increases_weight(self, engine):
        s = self._make_synapse(weight=0.3, days_ago=0)
        ltp = engine.calculate_ltp_strength(s)
        assert ltp > 0

    def test_ltp_diminishing_returns(self, engine):
        """接近 max_weight 时应减少增量"""
        s_low = self._make_synapse(weight=0.2)
        s_high = self._make_synapse(weight=0.95)
        ltp_low = engine.calculate_ltp_strength(s_low)
        ltp_high = engine.calculate_ltp_strength(s_high)
        assert ltp_low >= ltp_high

    def test_ltp_bounded_by_max_weight(self, engine):
        s = self._make_synapse(weight=0.99, reinforcement=10)
        ltp = engine.calculate_ltp_strength(s)
        assert s.weight + ltp <= engine.params.max_weight + 0.001

    # ── LTD (遗忘率) 计算 ──

    def test_ltd_rate_positive(self, engine):
        s = self._make_synapse(weight=0.5, days_ago=30)
        rate = engine.calculate_ltd_rate(s)
        assert rate > 0

    def test_ltd_rate_increases_with_unused_days(self, engine):
        """未使用天数越多衰减率越大"""
        s_recent = self._make_synapse(weight=0.5, days_ago=1)
        s_old = self._make_synapse(weight=0.5, days_ago=30)
        rate_recent = engine.calculate_ltd_rate(s_recent, days_unused=1)
        rate_old = engine.calculate_ltd_rate(s_old, days_unused=30)
        assert rate_old > rate_recent

    def test_importance_preservation(self, engine):
        """高重要性记忆衰减更慢"""
        s_low = self._make_synapse(weight=0.5, days_ago=10, importance=0.1)
        s_high = self._make_synapse(weight=0.5, days_ago=10, importance=0.9)
        rate_low = engine.calculate_ltd_rate(s_low, days_unused=10)
        rate_high = engine.calculate_ltd_rate(s_high, days_unused=10)
        assert rate_low >= rate_high

    # ── 应用调整 ──

    def test_apply_ltp(self, engine):
        s = self._make_synapse(weight=0.4)
        result = engine.apply_ltp(s)
        assert isinstance(result, SynapseState)
        # 强化后权重增加
        assert result.weight > 0.4
        assert result.reinforcement_count == s.reinforcement_count + 1

    def test_apply_ltd(self, engine):
        s = self._make_synapse(weight=0.4, days_ago=100, reinforcement=0)
        result = engine.apply_ltd(s, days_unused=30)
        assert isinstance(result, SynapseState)
        # 衰减后权重不增加
        assert result.weight <= 0.4

    def test_full_cycle(self, engine):
        """完整 LTP→LTD 周期"""
        s = self._make_synapse(weight=0.3)
        # 强化
        s = engine.apply_ltp(s)
        assert s.weight > 0.3
        # 衰减（模拟时间流逝）
        s = engine.apply_ltd(s, days_unused=30)
        # 权重至少有效
        assert s.weight >= 0.0

    # ── 调整日志 ──

    def test_adjustment_log(self, engine):
        s = self._make_synapse(weight=0.3)
        engine.apply_ltp(s)
        engine.apply_ltd(s, days_unused=5)
        assert len(engine.adjustment_log) >= 1
