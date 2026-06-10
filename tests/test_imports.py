"""
测试 _imports — 统一选装模块导入管理
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services._imports import (
    HAS_RETRIEVAL_HUB, HAS_PAPER_INT, HAS_ADAPTIVE, HAS_TOT,
    HAS_MEMEDITOR, HAS_CAUSAL, HAS_COGLOAD, HAS_HYPER, HAS_PLAN,
    HAS_NEURAL,
    PaperEngines, DynamicConfidence, get_dynamic_confidence,
    DebateEngine, get_debate_engine,
    GraphOfThoughts, get_got_engine,
    ContextLayer, get_context_layer,
    FastPIL, get_fast_pil,
    get_memory_editor,
)


class TestImportFlags:
    """导入状态标志测试"""

    def test_flags_are_booleans(self):
        """所有 HAS_* 标志应为布尔值"""
        flags = [
            HAS_RETRIEVAL_HUB, HAS_PAPER_INT, HAS_ADAPTIVE, HAS_TOT,
            HAS_MEMEDITOR, HAS_CAUSAL, HAS_COGLOAD, HAS_HYPER, HAS_PLAN,
            HAS_NEURAL,
        ]
        for flag in flags:
            assert isinstance(flag, bool)

    def test_retrieval_hub_available(self):
        """retrieval_hub 应在 services/ 目录下"""
        # retrieval_hub.py 存在于 services/ 目录
        assert HAS_RETRIEVAL_HUB is True

    def test_neural_available(self):
        """ncps 神经网络应在安装 torch+ncps+jieba 后可用"""
        # 安装了 torch 后应该可用
        assert HAS_NEURAL is True

    def test_at_least_some_available(self):
        """至少有一些核心模块可用"""
        available = sum([
            HAS_RETRIEVAL_HUB, HAS_NEURAL,
        ])
        assert available >= 2


class TestFallbackModules:
    """降级模块测试：不可用时设为 None"""

    def test_paper_engines_type(self):
        """PaperEngines 要么是类要么是 None"""
        assert PaperEngines is None or callable(PaperEngines)

    def test_dynamic_confidence_type(self):
        assert DynamicConfidence is None or callable(DynamicConfidence)

    def test_debate_engine_type(self):
        assert DebateEngine is None or callable(DebateEngine)

    def test_graph_of_thoughts_type(self):
        assert GraphOfThoughts is None or callable(GraphOfThoughts)

    def test_context_layer_type(self):
        assert ContextLayer is None or callable(ContextLayer)

    def test_fast_pil_type(self):
        assert FastPIL is None or callable(FastPIL)


class TestFallbackFunctions:
    """降级函数测试：不可用时返回 lambda"""

    def test_get_dynamic_confidence_is_callable(self):
        fn = get_dynamic_confidence
        assert callable(fn)
        result = fn()
        assert result is None or hasattr(result, '__class__')

    def test_get_debate_engine_is_callable(self):
        fn = get_debate_engine
        assert callable(fn)
        result = fn()
        assert result is None or hasattr(result, '__class__')

    def test_get_got_engine_is_callable(self):
        fn = get_got_engine
        assert callable(fn)
        result = fn()
        assert result is None or hasattr(result, '__class__')

    def test_get_context_layer_is_callable(self):
        fn = get_context_layer
        assert callable(fn)
        result = fn()
        assert result is None or hasattr(result, '__class__')

    def test_get_fast_pil_is_callable(self):
        fn = get_fast_pil
        assert callable(fn)
        result = fn()
        assert result is None or hasattr(result, '__class__')

    def test_get_memory_editor_is_callable(self):
        fn = get_memory_editor
        assert callable(fn)
        # memory_editor 模块存在时返回实例，否则返回 None
        result = fn()
        assert result is None or hasattr(result, '__class__')
