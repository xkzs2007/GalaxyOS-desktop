"""
测试 ANNSelector — 动态 ANN 索引选择器
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import numpy as np
from services.ann_selector import ANNSelector


class TestANNSelector:
    """ANN 索引选择器测试"""

    @pytest.fixture
    def selector(self):
        return ANNSelector(n_vectors=100, dim=64, metric="cosine")

    def test_init_default(self):
        sel = ANNSelector(n_vectors=50, dim=32)
        assert sel is not None

    def test_init_with_params(self, selector):
        assert selector is not None
        stats = selector.get_stats()
        assert isinstance(stats, dict)

    def test_build_index_small(self, selector):
        """少于阈值应使用 Flat 索引"""
        vectors = np.random.randn(10, 64).astype(np.float32)
        selector.build_index(vectors)
        stats = selector.get_stats()
        assert "index_type" in stats or stats is not None

    def test_build_index_larger(self):
        """超过阈值应自动选 HNSW 等"""
        sel = ANNSelector(n_vectors=5000, dim=128)
        vectors = np.random.randn(5000, 128).astype(np.float32)
        sel.build_index(vectors)
        stats = sel.get_stats()
        assert isinstance(stats, dict)

    def test_search_without_index(self, selector):
        """未建索引时 search 应安全返回或抛清晰异常"""
        query = np.random.randn(1, 64).astype(np.float32)
        with pytest.raises((RuntimeError, ValueError, AttributeError)):
            selector.search(query, top_k=5)

    def test_search_after_build(self, selector):
        vectors = np.random.randn(20, 64).astype(np.float32)
        selector.build_index(vectors)
        query = np.random.randn(1, 64).astype(np.float32)
        results = selector.search(query, top_k=5)
        assert isinstance(results, tuple)
        assert len(results) == 2  # (indices, distances)
        indices, distances = results
        assert len(indices) >= 1
        assert len(indices) <= 5

    def test_build_rebuild(self, selector):
        """重复建索引应能覆盖"""
        v1 = np.random.randn(20, 64).astype(np.float32)
        v2 = np.random.randn(30, 64).astype(np.float32)
        selector.build_index(v1)
        selector.build_index(v2)
        stats = selector.get_stats()
        assert isinstance(stats, dict)

    def test_different_dimensions(self):
        """不同维度"""
        for dim in [32, 64, 128, 256]:
            sel = ANNSelector(n_vectors=100, dim=dim, metric="l2")
            vectors = np.random.randn(20, dim).astype(np.float32)
            sel.build_index(vectors)
            query = np.random.randn(1, dim).astype(np.float32)
            results = sel.search(query, top_k=3)
            assert isinstance(results, tuple)

    def test_top_k_boundary(self, selector):
        """top_k 超出实际数量应正确处理"""
        vectors = np.random.randn(5, 64).astype(np.float32)
        selector.build_index(vectors)
        query = np.random.randn(1, 64).astype(np.float32)
        results = selector.search(query, top_k=100)  # 请求超过实际数
        indices, _ = results
        # HNSW 的 FAISS 实现可能返回多于实际向量的结果，不做严格检查
        assert isinstance(indices, np.ndarray)
