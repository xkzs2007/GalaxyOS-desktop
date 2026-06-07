"""
测试 CRAG Pipeline — 完整 RAG 管线
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.crag_pipeline import (
    RAGDocument, RetrievalConfidence, CRAGPipeline,
    RetrievalEvaluator, SelfRAG, Reranker,
)


class TestRetrievalConfidence:
    """检索置信度枚举测试"""

    def test_values(self):
        assert RetrievalConfidence.HIGH.value == "high"
        assert RetrievalConfidence.MEDIUM.value == "medium"
        assert RetrievalConfidence.LOW.value == "low"


class TestRAGDocument:
    """RAG 文档测试"""

    def test_creation(self):
        doc = RAGDocument(
            content="test content",
            score=0.85,
            source="web",
        )
        assert doc.content == "test content"
        assert doc.score == 0.85
        assert doc.source == "web"

    def test_default_values(self):
        doc = RAGDocument(content="c")
        assert doc.score == 0.0
        assert doc.source == ""
        assert doc.metadata == {}

    def test_with_metadata(self):
        doc = RAGDocument(
            content="c", score=0.5, source="local",
            metadata={"chunk_id": 5, "token_count": 100},
        )
        assert doc.metadata["chunk_id"] == 5


class TestRetrievalEvaluator:
    """检索评估器测试"""

    def test_init_default(self):
        evaluator = RetrievalEvaluator()
        assert evaluator.high_threshold == 0.85
        assert evaluator.low_threshold == 0.5

    def test_init_custom(self):
        evaluator = RetrievalEvaluator(high_threshold=0.9, low_threshold=0.3)
        assert evaluator.high_threshold == 0.9
        assert evaluator.low_threshold == 0.3

    def test_evaluate_empty(self):
        evaluator = RetrievalEvaluator()
        result = evaluator.evaluate("query", [])
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_evaluate_docs(self):
        evaluator = RetrievalEvaluator()
        docs = [
            RAGDocument(content="relevant", score=0.9, source="local"),
            RAGDocument(content="less relevant", score=0.4, source="web"),
        ]
        result = evaluator.evaluate("query", docs)
        assert isinstance(result, tuple)
        assert len(result) == 2


class TestSelfRAG:
    """Self-RAG 测试"""

    def test_init(self):
        srag = SelfRAG()
        assert srag is not None


class TestReranker:
    """重排序器测试"""

    def test_init(self):
        reranker = Reranker()
        assert reranker is not None


class TestCRAGPipeline:
    """RAG 管线集成测试"""

    def test_init(self):
        pipeline = CRAGPipeline()
        assert pipeline is not None

    def test_init_custom_options(self):
        pipeline = CRAGPipeline(
            enable_query_rewrite=False,
            enable_hybrid_search=True,
            enable_context_compression=False,
        )
        assert pipeline.enable_query_rewrite is False
        assert pipeline.enable_hybrid_search is True
        assert pipeline.enable_context_compression is False

    def test_run_empty_query(self):
        pipeline = CRAGPipeline()
        result = pipeline.run("")
        assert result is not None
        assert hasattr(result, "answer")

    def test_run_basic_query(self):
        pipeline = CRAGPipeline()
        result = pipeline.run("test query")
        assert result is not None
        assert hasattr(result, "answer")

    def test_pipeline_components_exist(self):
        pipeline = CRAGPipeline()
        assert pipeline.evaluator is not None
        assert pipeline.reranker is not None
        assert pipeline.self_rag is not None
