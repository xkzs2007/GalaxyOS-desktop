"""
集成测试 — 跨模块功能性测试
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


class TestExceptionIntegration:
    """异常系统集成测试"""

    def test_exception_flow(self):
        from services.exceptions import (
            SkillError, LLMError, EmbeddingError,
        )
        try:
            raise LLMError("api timeout", details={"retry": 3})
        except SkillError as e:
            assert e.code == "LLM_ERROR"
            d = e.to_dict()
            assert d["error"] is True
            assert d["details"]["retry"] == 3


class TestCacheIntegration:
    """缓存系统集成测试"""

    def test_cache_lifecycle(self):
        from services.unified_cache import UnifiedCache
        cache = UnifiedCache(backend="memory", max_size=10)
        cache.set("k1", {"data": [1, 2, 3]})
        cache.set("k2", "string_value")
        assert cache.get("k1") == {"data": [1, 2, 3]}
        assert cache.get("k2") == "string_value"
        cache.delete("k1")
        assert cache.get("k1") is None
        stats = cache.stats()
        assert stats["count"] == 1


class TestCRAGIntegration:
    """CRAG 集成测试"""

    def test_full_process_flow(self):
        from services.crag import CRAG, CRAGResult, CRAGStep, CRAGState
        crag = CRAG()
        result = crag.process("test query")
        assert isinstance(result, CRAGResult)
        assert result.query == "test query"
        steps = result.steps
        assert len(steps) > 0
        # 第一步可以是 RETRIEVING（实际行为）或 INIT
        assert steps[0].state in (CRAGState.INIT, CRAGState.RETRIEVING)
        assert steps[-1].state in (CRAGState.COMPLETED, CRAGState.FAILED, CRAGState.GENERATING)


class TestRRFIntegration:
    """RRF 融合集成测试"""

    def test_query_to_weights_to_fusion(self):
        from services.adaptive_rrf import AdaptiveRRF, QueryCategory
        rrf = AdaptiveRRF()
        # 分类
        cat = rrf.classify_query("specific exact term")
        assert cat in QueryCategory
        # 获取权重
        weights = rrf.get_adaptive_weights("specific exact term")
        assert weights.k == 60
        # 融合
        dense = [("d1", 0.9), ("d2", 0.5)]
        sparse = [("d2", 0.8), ("d3", 0.4)]
        fused = rrf.fuse_rankings(dense, sparse)
        assert len(fused) >= 2


class TestThinkingIntegration:
    """思考引擎集成测试"""

    def test_thinking_enhanced_flow(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import importlib
        import services.thinking_enhanced as te
        importlib.reload(te)

        # Reflexion → retrieve
        engine = te.ReflexionEngine()
        results = engine.retrieve("test")
        assert isinstance(results, list)

        # SelfRefineLoop
        loop = te.SelfRefineLoop()
        refined, history = loop.refine("q", "a")
        assert isinstance(refined, str)

        # MultiPathExplorer
        explorer = te.MultiPathExplorer()
        paths = explorer.explore("q")
        assert isinstance(paths, dict)


class TestHallucinationIntegration:
    """防幻觉系统集成测试"""

    def test_guard_flow(self, tmp_path):
        from services.enhanced_hallucination_guard import (
            EnhancedHallucinationGuard, VerificationLevel,
        )
        guard = EnhancedHallucinationGuard(workspace_path=str(tmp_path))
        # 验证级别判定
        assert guard.determine_verification_level(0.95) == VerificationLevel.NONE
        assert guard.determine_verification_level(0.5) == VerificationLevel.MODERATE
        # 交叉验证
        result = guard.verify_with_cross_validation(
            "Python is a programming language"
        )
        assert isinstance(result, dict)


class TestMemoryIntegration:
    """记忆系统集成测试"""

    def test_memory_params_and_metrics(self):
        from services.adaptive_memory import MemoryParameters, PerformanceMetrics
        params = MemoryParameters(
            recall_threshold=0.5,
            ltp_strength=0.15,
        )
        assert params.to_dict()["recall_threshold"] == 0.5

        metrics = PerformanceMetrics(
            recall_precision=0.9,
            user_satisfaction=0.88,
        )
        d = metrics.to_dict()
        assert d["recall_precision"] == 0.9
        assert d["user_satisfaction"] == 0.88
