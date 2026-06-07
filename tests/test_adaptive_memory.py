"""
测试 AdaptiveMemory — 自适应记忆架构
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.adaptive_memory import (
    MemoryParameters, PerformanceMetrics,
)


class TestMemoryParameters:
    """记忆参数测试"""

    def test_default_values(self):
        p = MemoryParameters()
        assert p.recall_threshold == 0.25
        assert p.max_recall_results == 10
        assert p.forget_threshold == 0.1
        assert p.decay_rate == 0.01
        assert p.ltp_strength == 0.1
        assert p.ltd_rate == 0.01
        assert p.synapse_threshold == 0.3
        assert p.emotion_weight_factor == 0.5
        assert p.reflection_frequency == 3
        assert p.auto_apply_threshold == 0.8

    def test_to_dict(self):
        p = MemoryParameters(recall_threshold=0.5, max_recall_results=5)
        d = p.to_dict()
        assert d["recall_threshold"] == 0.5
        assert d["max_recall_results"] == 5
        # 未修改的使用默认值
        assert d["forget_threshold"] == 0.1

    def test_from_dict(self):
        data = {"recall_threshold": 0.8, "max_recall_results": 20}
        p = MemoryParameters.from_dict(data)
        assert p.recall_threshold == 0.8
        assert p.max_recall_results == 20
        # 缺失的使用默认值
        assert p.forget_threshold == 0.1

    def test_from_dict_empty(self):
        p = MemoryParameters.from_dict({})
        assert p.recall_threshold == 0.25

    def test_roundtrip(self):
        original = MemoryParameters(
            recall_threshold=0.7,
            ltp_strength=0.2,
            reflection_frequency=5,
        )
        data = original.to_dict()
        restored = MemoryParameters.from_dict(data)
        assert restored.recall_threshold == 0.7
        assert restored.ltp_strength == 0.2
        assert restored.reflection_frequency == 5
        assert restored.forget_threshold == 0.1  # 默认值保持不变

    def test_all_fields_accessible(self):
        p = MemoryParameters()
        fields = [
            "recall_threshold", "max_recall_results", "forget_threshold",
            "decay_rate", "ltp_strength", "ltd_rate", "synapse_threshold",
            "emotion_weight_factor", "reflection_frequency", "auto_apply_threshold",
        ]
        for f in fields:
            assert hasattr(p, f)
            val = getattr(p, f)
            assert isinstance(val, (int, float))


class TestPerformanceMetrics:
    """性能指标测试"""

    def test_default_metrics(self):
        m = PerformanceMetrics()
        assert m.recall_precision == 0.0
        assert m.recall_recall == 0.0
        assert m.recall_f1 == 0.0
        assert m.forget_accuracy == 0.0
        assert m.false_forget_rate == 0.0
        assert m.user_satisfaction == 0.0

    def test_custom_metrics(self):
        m = PerformanceMetrics(
            recall_precision=0.9,
            recall_recall=0.85,
            user_satisfaction=0.92,
        )
        assert m.recall_precision == 0.9
        assert m.recall_recall == 0.85
        assert m.user_satisfaction == 0.92

    def test_f1_field(self):
        """F1 字段可独立设置"""
        m = PerformanceMetrics(recall_precision=0.8, recall_recall=0.6, recall_f1=0.7)
        assert m.recall_f1 == 0.7
        assert m.recall_precision == 0.8
