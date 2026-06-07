"""
测试 Thinking Enhanced — 增强思考引擎
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.thinking_enhanced import (
    ReflexionEntry, ReflexionEngine,
    SelfRefineLoop, MultiPathExplorer,
    ThinkingEnhanced, FlashNLP, get_thinking_enhanced,
)


class TestReflexionEntry:
    """反思条目测试"""

    def test_creation(self):
        entry = ReflexionEntry(
            id="r1",
            question="什么是RAG？",
            answer_snippet="RAG is...",
            failure_pattern="幻觉",
            root_cause="模型幻觉",
            fix_strategy="多源验证",
            confidence_drop=0.3,
            created_at="2026-01-01T00:00:00Z",
            hit_count=2,
        )
        assert entry.id == "r1"
        assert entry.failure_pattern == "幻觉"
        assert entry.hit_count == 2

    def test_default_values(self):
        entry = ReflexionEntry(
            id="r2",
            question="?",
            answer_snippet="...",
            failure_pattern="unknown",
            root_cause="unknown",
            fix_strategy="none",
            confidence_drop=0.0,
        )
        assert entry.created_at == ""
        assert entry.hit_count == 0


class TestReflexionEngine:
    """反思引擎测试"""

    def test_init_with_tmp(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import importlib
        import services.thinking_enhanced as te
        importlib.reload(te)

        engine = te.ReflexionEngine()
        assert engine._cache == []

    def test_record(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import importlib
        import services.thinking_enhanced as te
        importlib.reload(te)

        engine = te.ReflexionEngine()
        engine.record(
            question="测试问题",
            answer="测试回答",
            scores={"accuracy": 0.3},
            flash_client=None,
        )
        assert len(engine._cache) > 0

    def test_retrieve(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import importlib
        import services.thinking_enhanced as te
        importlib.reload(te)

        engine = te.ReflexionEngine()
        results = engine.retrieve("test query")
        assert isinstance(results, list)

    def test_format_context(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import importlib
        import services.thinking_enhanced as te
        importlib.reload(te)

        engine = te.ReflexionEngine()
        ctx = engine.format_context(entries=[])
        assert isinstance(ctx, str)


class TestSelfRefineLoop:
    """自精炼循环测试"""

    def test_init(self):
        engine = SelfRefineLoop()
        assert engine is not None

    def test_refine_without_judge(self):
        engine = SelfRefineLoop()
        result = engine.refine(
            question="test query",
            initial_answer="test answer",
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        refined_answer, history = result
        assert isinstance(refined_answer, str)
        assert isinstance(history, list)


class TestMultiPathExplorer:
    """多路径探索器测试"""

    def test_init(self):
        engine = MultiPathExplorer()
        assert engine is not None

    def test_explore(self):
        engine = MultiPathExplorer()
        result = engine.explore(question="test question")
        assert isinstance(result, dict)


class TestThinkingEnhanced:
    """集成思考增强引擎测试"""

    def test_init(self):
        engine = ThinkingEnhanced()
        assert engine is not None

    def test_get_thinking_enhanced_singleton(self):
        e1 = get_thinking_enhanced()
        e2 = get_thinking_enhanced()
        assert e1 is e2


class TestFlashNLP:
    """Flash NLP 工具测试"""

    def test_init(self):
        nlp = FlashNLP()
        assert nlp is not None

    def test_analyze_intent(self):
        nlp = FlashNLP()
        result = nlp.analyze_intent("Python 是一种编程语言")
        assert isinstance(result, dict)

    def test_detect_comparison(self):
        nlp = FlashNLP()
        result = nlp.detect_comparison("A 比 B 更好")
        assert result is None or isinstance(result, dict)

    def test_resolve_coref(self):
        nlp = FlashNLP()
        result = nlp.resolve_coref("他说他今天会来", "Python")
        assert isinstance(result, dict)
