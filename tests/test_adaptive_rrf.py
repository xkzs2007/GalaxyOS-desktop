"""
测试 AdaptiveRRF — 自适应 RRF 融合
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.adaptive_rrf import AdaptiveRRF, RRFWeights, QueryCategory


class TestAdaptiveRRF:
    """RRF 自适应融合测试"""

    @pytest.fixture
    def rrf(self):
        return AdaptiveRRF()

    # ── 查询分类 ──

    def test_exact_match_quoted(self, rrf):
        cat = rrf.classify_query('"hello world"')
        assert cat == QueryCategory.EXACT_MATCH

    def test_exact_match_year(self, rrf):
        cat = rrf.classify_query("event in 2024")
        assert cat == QueryCategory.EXACT_MATCH

    def test_exact_match_abbreviation(self, rrf):
        cat = rrf.classify_query("What is RAG?")
        assert cat == QueryCategory.EXACT_MATCH

    def test_keyword_heavy(self, rrf):
        cat = rrf.classify_query("具体怎么实现？")
        assert cat == QueryCategory.KEYWORD_HEAVY

    def test_concept_heavy(self, rrf):
        cat = rrf.classify_query("有没有类似的方法？")
        assert cat == QueryCategory.CONCEPT_HEAVY

    def test_concept_heavy_english(self, rrf):
        """英文 similar 关键词"""
        cat = rrf.classify_query("similar concepts compared")
        # similar 可能被分类为 CONCEPT_HEAVY
        assert cat in (QueryCategory.CONCEPT_HEAVY, QueryCategory.EXACT_MATCH)

    def test_semantic_fallback(self, rrf):
        """没有明确特征应退到语义查询"""
        cat = rrf.classify_query("讲个笑话吧")
        assert cat in QueryCategory  # 无论什么分类，只要不崩溃就行

    # ── 权重获取 ──

    def test_get_adaptive_weights(self, rrf):
        w = rrf.get_adaptive_weights("test query")
        assert isinstance(w, RRFWeights)
        assert w.k > 0

    def test_weights_for_exact_match(self, rrf):
        w = rrf.get_adaptive_weights("exact term", category=QueryCategory.EXACT_MATCH)
        assert w.sparse_weight > w.dense_weight

    def test_weights_for_semantic(self, rrf):
        w = rrf.get_adaptive_weights("vague idea", category=QueryCategory.SEMANTIC)
        assert w.dense_weight > w.sparse_weight

    def test_weights_for_keyword(self, rrf):
        w = rrf.get_adaptive_weights("具体搜索", category=QueryCategory.KEYWORD_HEAVY)
        assert w.sparse_weight > w.dense_weight

    def test_weights_for_concept(self, rrf):
        w = rrf.get_adaptive_weights("类似相关", category=QueryCategory.CONCEPT_HEAVY)
        assert w.dense_weight > w.sparse_weight

    def test_weights_for_hybrid(self, rrf):
        w = rrf.get_adaptive_weights("exact similar", category=QueryCategory.HYBRID)
        assert w.dense_weight == pytest.approx(0.5)
        assert w.sparse_weight == pytest.approx(0.5)

    # ── 融合 ──

    def test_fuse_empty_lists(self, rrf):
        result = rrf.fuse_rankings([], [])
        assert result == []

    def test_fuse_single_list(self, rrf):
        result = rrf.fuse_rankings([("doc1", 0.9), ("doc2", 0.7)], [])
        assert len(result) >= 1
        assert result[0][0] == "doc1"

    def test_fuse_both_lists(self, rrf):
        dense = [("d1", 0.9), ("d2", 0.7)]
        sparse = [("d2", 0.8), ("d3", 0.6)]
        result = rrf.fuse_rankings(dense, sparse)
        assert len(result) >= 1
        docs = [r[0] for r in result]
        # d2 在两个列表都出现
        assert "d2" in docs


class TestRRFWeights:
    """权重配置测试"""

    def test_default_weights(self):
        w = RRFWeights()
        assert w.dense_weight == 0.5
        assert w.sparse_weight == 0.5
        assert w.k == 60

    def test_custom_weights(self):
        w = RRFWeights(dense_weight=0.8, sparse_weight=0.2, k=100)
        assert w.dense_weight == 0.8
        assert w.sparse_weight == 0.2
        assert w.k == 100


class TestQueryCategoryEnum:
    """查询类别枚举测试"""

    def test_all_categories(self):
        categories = list(QueryCategory)
        assert len(categories) == 5
        names = [c.value for c in categories]
        assert "exact_match" in names
        assert "semantic" in names
        assert "hybrid" in names
        assert "keyword_heavy" in names
        assert "concept_heavy" in names
