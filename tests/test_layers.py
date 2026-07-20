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
        from galaxyos.privileged.exceptions import (
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
        from galaxyos.privileged.unified_cache import UnifiedCache
        cache = UnifiedCache(backend="memory", max_size=10)
        cache.set("k1", {"data": [1, 2, 3]})
        cache.set("k2", "string_value")
        assert cache.get("k1") == {"data": [1, 2, 3]}
        assert cache.get("k2") == "string_value"
        cache.delete("k1")
        assert cache.get("k1") is None
        stats = cache.stats()
        assert stats["count"] == 1


class TestThinkingIntegration:
    """思考引擎集成测试"""

    def test_thinking_enhanced_flow(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import importlib
        import galaxyos.engine.thinking_enhanced as te
        importlib.reload(te)

        engine = te.ReflexionEngine()
        results = engine.retrieve("test")
        assert isinstance(results, list)

        loop = te.SelfRefineLoop()
        refined, history = loop.refine("q", "a")
        assert isinstance(refined, str)

        explorer = te.MultiPathExplorer()
        paths = explorer.explore("q")
        assert isinstance(paths, dict)
