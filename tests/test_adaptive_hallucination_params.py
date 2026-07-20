"""测试 adaptive_hallucination_params — 自适应防幻觉参数"""
import sys; sys.path.insert(0, '.')
import pytest
from galaxyos.engine.adaptive_hallucination_params import (
    AdaptiveHallucinationParams, AdaptiveThresholds,
    DomainType, QueryType,
)


class TestDomainType:
    def test_all_types(self):
        types = list(DomainType)
        assert len(types) >= 3


class TestQueryType:
    def test_all_types(self):
        types = list(QueryType)
        assert len(types) >= 3


class TestAdaptiveThresholds:
    def test_default_values(self):
        t = AdaptiveThresholds()
        assert t.familiarity_threshold >= 0
        assert t.source_weight_internal >= 0

    def test_custom_values(self):
        t = AdaptiveThresholds(
            familiarity_threshold=0.6,
            source_weight_internal=0.9,
        )
        assert t.familiarity_threshold == 0.6
        assert t.source_weight_internal == 0.9


class TestAdaptiveHallucinationParams:
    @pytest.fixture
    def params(self):
        return AdaptiveHallucinationParams()

    def test_init(self, params):
        assert params is not None

    def test_classify_query(self, params):
        qtype = params.classify_query("what is the capital of France")
        assert qtype in QueryType

    def test_classify_query_chinese(self, params):
        qtype = params.classify_query("Python是什么")
        assert qtype in QueryType

    def test_detect_domain(self, params):
        domain = params.detect_domain("how to fix a bug in Python")
        assert domain in DomainType

    def test_get_verification_level(self, params):
        from galaxyos.engine.adaptive_hallucination_params import AdaptiveThresholds
        t = AdaptiveThresholds()
        level = params.get_verification_level(confidence=0.3, thresholds=t)
        assert level is not None

    def test_adjust_thresholds(self, params):
        params.adjust_thresholds(query="test query")
        # 不应崩溃

    def test_get_adjustment_stats(self, params):
        stats = params.get_adjustment_stats()
        assert isinstance(stats, dict)
