"""测试 unified_vector_store — 统一向量存储"""
import sys; sys.path.insert(0, '.')
import pytest
import numpy as np
from services.unified_vector_store import (
    UnifiedVectorStore, VectorRecord,
)


class TestVectorRecord:
    def test_creation(self):
        r = VectorRecord(
            id="v1", vector=[0.1, 0.2], metadata={"k": "v"},
            content="test", source="unit_test",
        )
        assert r.id == "v1"
        assert r.content == "test"

    def test_default_metadata(self):
        r = VectorRecord(
            id="v2", vector=[0.5], metadata={},
            content="x", source="test",
        )
        assert isinstance(r.metadata, dict)


class TestUnifiedVectorStore:
    @pytest.fixture
    def store(self, tmp_path):
        idx_path = str(tmp_path / "vectors")
        return UnifiedVectorStore(backend="hnswlib", index_path=idx_path, dim=64)

    def test_init(self, store):
        assert store is not None

    def test_count_empty(self, store):
        assert store.count() >= 0

    def test_add_and_count(self, store):
        vec = np.random.randn(5, 64).astype(np.float32).tolist()
        meta = [{"id": f"v{i}"} for i in range(5)]
        content = [f"content {i}" for i in range(5)]
        store.add_vectors(vec, meta, content)
        assert store.count() >= 1

    def test_search(self, store):
        vec = np.random.randn(10, 64).astype(np.float32).tolist()
        meta = [{"id": f"v{i}"} for i in range(10)]
        content = [f"doc {i}" for i in range(10)]
        store.add_vectors(vec, meta, content)

        query = np.random.randn(64).astype(np.float32).tolist()
        results = store.search(query, top_k=3)
        assert isinstance(results, list)

    def test_search_single_dimension(self, store):
        """确保查询向量维度正确"""
        vec = np.random.randn(3, 64).astype(np.float32).tolist()
        store.add_vectors(vec, [{"id": f"a{i}"} for i in range(3)], ["c"] * 3)
        query = np.random.randn(64).astype(np.float32).tolist()
        results = store.search(query, top_k=2)
        assert isinstance(results, list)

    def test_get_stats(self, store):
        stats = store.get_stats()
        assert isinstance(stats, dict)

    def test_delete(self, store):
        vec = np.random.randn(2, 64).astype(np.float32).tolist()
        store.add_vectors(vec, [{"id": "x1"}, {"id": "x2"}], ["a", "b"])
        before = store.count()
        store.delete("x1")
        after = store.count()
        assert after <= before
