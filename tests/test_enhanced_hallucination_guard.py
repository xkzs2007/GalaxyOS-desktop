"""
测试 EnhancedHallucinationGuard — 增强防幻觉守卫
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.enhanced_hallucination_guard import (
    MultiSourceCrossValidator, EnhancedHallucinationGuard,
    CrossValidationResult, VerificationSource,
    VerificationLevel, SourceType,
)


class TestSourceType:
    """信息来源类型测试"""

    def test_all_source_types(self):
        types = list(SourceType)
        assert len(types) == 8
        values = {t.value for t in types}
        assert "internal_memory" in values
        assert "user_statement" in values
        assert "web_search" in values
        assert "document" in values
        assert "knowledge_graph" in values
        assert "image_analysis" in values
        assert "inference" in values
        assert "unknown" in values

    def test_source_weights_between_0_and_1(self):
        weights = MultiSourceCrossValidator.SOURCE_WEIGHTS
        for t in SourceType:
            w = weights[t]
            assert 0 < w <= 1.0


class TestVerificationLevel:
    """验证级别测试"""

    def test_all_levels(self):
        levels = list(VerificationLevel)
        assert len(levels) == 5
        values = {l.value for l in levels}
        assert "none" in values
        assert "light" in values
        assert "moderate" in values
        assert "deep" in values
        assert "exhaustive" in values


class TestVerificationSource:
    """验证来源测试"""

    def test_creation(self):
        s = VerificationSource(
            source_type=SourceType.WEB_SEARCH,
            content="test content",
            confidence=0.85,
            timestamp="2026-01-01T00:00:00Z",
            metadata={"url": "http://example.com"},
        )
        assert s.source_type == SourceType.WEB_SEARCH
        assert s.content == "test content"
        assert s.confidence == 0.85

    def test_default_values(self):
        s = VerificationSource(
            source_type=SourceType.UNKNOWN,
            content="",
            confidence=0.0,
        )
        assert s.timestamp == ""
        assert s.metadata == {}


class TestCrossValidationResult:
    """交叉验证结果测试"""

    def test_default(self):
        r = CrossValidationResult(
            statement="test",
            is_verified=False,
            confidence=0.0,
            sources=[],
            agreements=0,
            disagreements=0,
            consensus="insufficient_data",
            analysis="",
        )
        assert r.statement == "test"

    def test_strong_agreement(self):
        sources = [
            VerificationSource(SourceType.INTERNAL_MEMORY, "same", 0.9),
            VerificationSource(SourceType.WEB_SEARCH, "same", 0.85),
        ]
        r = CrossValidationResult(
            statement="test", is_verified=True, confidence=0.9,
            sources=sources, agreements=2, disagreements=0,
            consensus="strong_agreement", analysis="verified",
        )
        assert r.is_verified is True
        assert r.agreements == 2


class TestEnhancedHallucinationGuard:
    """增强防幻觉守卫测试"""

    def test_init(self, tmp_path):
        guard = EnhancedHallucinationGuard(workspace_path=str(tmp_path))
        assert guard is not None
        assert guard.workspace_path == tmp_path

    def test_determine_verification_level(self, tmp_path):
        guard = EnhancedHallucinationGuard(workspace_path=str(tmp_path))
        assert guard.determine_verification_level(0.95) == VerificationLevel.NONE
        assert guard.determine_verification_level(0.8) == VerificationLevel.LIGHT
        assert guard.determine_verification_level(0.5) == VerificationLevel.MODERATE
        assert guard.determine_verification_level(0.3) == VerificationLevel.DEEP
        assert guard.determine_verification_level(0.0) == VerificationLevel.EXHAUSTIVE

    def test_verify_with_cross_validation(self, tmp_path):
        guard = EnhancedHallucinationGuard(workspace_path=str(tmp_path))
        result = guard.verify_with_cross_validation("Python is a language")
        assert isinstance(result, dict)


class TestMultiSourceCrossValidator:
    """多源交叉验证器测试"""

    def test_init_custom(self, tmp_path):
        validator = MultiSourceCrossValidator(workspace_path=str(tmp_path))
        assert validator.workspace_path == tmp_path
        assert validator._memories == []

    def test_search_internal_memory_empty(self, tmp_path):
        validator = MultiSourceCrossValidator(workspace_path=str(tmp_path))
        results = validator.search_internal_memory("test query")
        assert isinstance(results, list)
        assert results == []

    def test_cross_validate(self, tmp_path):
        validator = MultiSourceCrossValidator(workspace_path=str(tmp_path))
        sources = [
            VerificationSource(SourceType.WEB_SEARCH, "fact", 0.8),
        ]
        result = validator.cross_validate("test statement", sources)
        assert isinstance(result, CrossValidationResult)
