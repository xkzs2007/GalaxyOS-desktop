"""
测试 ContextCompressor — 上下文压缩
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from galaxyos.privileged.context_compressor import (
    RuleBasedCompressor, ContextCompressor,
    LLMCompressor, CompressionResult,
)


class TestCompressionResult:
    """压缩结果数据类测试"""

    def test_creation(self):
        r = CompressionResult(
            original_text="hello world",
            compressed_text="hello",
            compression_ratio=0.5,
            original_length=11,
            compressed_length=5,
            method="rule",
            preserved_keywords=["world"],
            metadata={"strategy": "truncate"},
        )
        assert r.original_text == "hello world"
        assert r.compressed_text == "hello"
        assert r.compression_ratio == 0.5
        assert r.compressed_length == 5
        assert r.method == "rule"
        assert r.preserved_keywords == ["world"]


class TestRuleBasedCompressor:
    """规则压缩器测试"""

    @pytest.fixture
    def compressor(self):
        return RuleBasedCompressor()

    def test_compress_empty(self, compressor):
        result = compressor.compress("")
        assert isinstance(result, CompressionResult)
        assert len(result.compressed_text) >= 0

    def test_compress_short(self, compressor):
        text = "hello"
        result = compressor.compress(text)
        assert result.original_text == text
        assert len(result.compressed_text) >= len(text)

    def test_compress_template_removal(self, compressor):
        text = "请基于以下参考信息回答问题：答案是42。"
        result = compressor.compress(text)
        assert "答案是42" in result.compressed_text

    def test_compress_reference_prefix(self, compressor):
        text = "参考信息: 今天天气很好。"
        result = compressor.compress(text)
        assert "今天天气很好" in result.compressed_text

    def test_compression_ratio(self, compressor):
        text = "hello " * 100
        result = compressor.compress(text)
        assert 0 < result.compression_ratio <= 1.0

    def test_method_name(self, compressor):
        result = compressor.compress("test")
        assert "rule" in result.method.lower()

    def test_compress_preserves_content(self, compressor):
        text = "关键信息：Python是很好的编程语言。"
        result = compressor.compress(text)
        assert "Python" in result.compressed_text

    def test_compress_redundant_whitespace(self, compressor):
        text = "hello    world     test"
        result = compressor.compress(text)
        assert "    " not in result.compressed_text


class TestContextCompressor:
    """高层上下文压缩器测试"""

    def test_init(self):
        c = ContextCompressor()
        assert c is not None

    def test_compress_basic(self):
        c = ContextCompressor()
        result = c.compress("test text content")
        assert isinstance(result, CompressionResult)
        assert len(result.compressed_text) > 0

    def test_compress_documents(self):
        c = ContextCompressor()
        docs = ["doc 1 content here is long enough", "doc 2 content here too"]
        result = c.compress_documents(docs)
        assert isinstance(result, list)

    def test_get_stats(self):
        c = ContextCompressor()
        c.compress("test")
        stats = c.get_stats()
        assert isinstance(stats, dict)


class TestLLMCompressor:
    """LLM 压缩器测试"""

    def test_init(self):
        c = LLMCompressor()
        assert c is not None

    def test_compress_without_llm(self):
        """没有 LLM 客户端时应降级"""
        c = LLMCompressor()
        result = c.compress("test content")
        assert isinstance(result, CompressionResult)
