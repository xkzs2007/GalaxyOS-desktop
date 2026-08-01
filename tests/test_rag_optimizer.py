"""测试 rag_optimizer — RAG 查询优化"""
import sys; sys.path.insert(0, '.')
import pytest
from galaxyos.privileged.rag_optimizer import (
    QueryExpander, Reranker, MultiQueryFusion,
    RAGQueryOptimizer, HyDEQueryRewriter,
)


class TestQueryExpander:
    @pytest.fixture
    def expander(self):
        return QueryExpander()

    def test_init(self, expander):
        assert expander is not None

    def test_expand_basic(self, expander):
        result = expander.expand("machine learning")
        assert isinstance(result, (str, list))
        if isinstance(result, list):
            assert len(result) >= 1

    def test_expand_chinese(self, expander):
        result = expander.expand("机器学习")
        assert isinstance(result, (str, list))


class TestReranker:
    @pytest.fixture
    def reranker(self):
        return Reranker()

    def test_init(self, reranker):
        assert reranker is not None

    def test_rerank_empty(self, reranker):
        result = reranker.rerank(query="test", documents=[])
        assert isinstance(result, list)

    def test_rerank_basic(self, reranker):
        docs = [
            {"id": "d1", "content": "machine learning basics", "score": 0.8},
            {"id": "d2", "content": "deep learning advanced", "score": 0.6},
        ]
        result = reranker.rerank(query="ml basics", documents=docs)
        assert isinstance(result, list)


class TestMultiQueryFusion:
    @pytest.fixture
    def fusion(self):
        return MultiQueryFusion(fusion_method="rrf")

    def test_init(self, fusion):
        assert fusion is not None

    def test_fuse_empty(self, fusion):
        result = fusion.fuse([])
        assert isinstance(result, list)

    def test_fuse_basic(self, fusion):
        r1 = [("d1", 0.9), ("d2", 0.5)]
        r2 = [("d2", 0.8), ("d3", 0.4)]
        result = fusion.fuse([r1, r2])
        assert isinstance(result, list)

    def test_reciprocal_rank_fusion(self, fusion):
        result = fusion.reciprocal_rank_fusion(
            [[("a", 1), ("b", 2)], [("b", 1), ("c", 2)]],
        )
        assert isinstance(result, list)

    def test_weighted_fusion(self, fusion):
        result = fusion.weighted_fusion(
            [[("a", 0.9)], [("b", 0.8)]],
        )
        assert isinstance(result, list)


class TestHyDEQueryRewriter:
    @pytest.fixture
    def rewriter(self):
        return HyDEQueryRewriter()

    def test_init(self, rewriter):
        assert rewriter is not None

    def test_rewrite_basic(self, rewriter):
        result = rewriter.rewrite("test query")
        assert isinstance(result, dict)

    def test_detect_domain(self, rewriter):
        domain = rewriter.detect_domain("Python programming")
        assert isinstance(domain, str)

    def test_detect_query_type(self, rewriter):
        qtype = rewriter.detect_query_type("what is Python")
        assert isinstance(qtype, str)


class TestRAGQueryOptimizer:
    def test_init(self):
        optimizer = RAGQueryOptimizer()
        assert optimizer is not None

    def test_optimize_basic(self):
        optimizer = RAGQueryOptimizer()
        result = optimizer.optimize("test query")
        assert isinstance(result, (str, list, dict))

    def test_fuse_results(self):
        optimizer = RAGQueryOptimizer()
        result = optimizer.fuse_results(
            [[("d1", 0.9)], [("d1", 0.8)]]
        )
        assert isinstance(result, list)
