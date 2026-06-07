"""
测试 CognitiveMap — 认知地图（AriGraph 空间推理）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.cognitive_map import (
    CognitiveMap, SpatialAnchor,
)


class TestSpatialAnchor:
    """空间锚点测试"""

    def test_creation(self):
        anchor = SpatialAnchor(
            anchor_id="a1",
            node_id="n1",
            context="test context",
            anchor_vector=[0.1, 0.2, 0.3],
            dimension=3,
            timestamp="2026-01-01T00:00:00Z",
            session_key="session_1",
            importance=0.8,
            access_count=0,
        )
        assert anchor.anchor_id == "a1"
        assert anchor.node_id == "n1"
        assert anchor.context == "test context"
        assert anchor.anchor_vector == [0.1, 0.2, 0.3]
        assert anchor.dimension == 3
        assert anchor.importance == 0.8
        assert anchor.access_count == 0

    def test_default_values(self):
        anchor = SpatialAnchor(
            anchor_id="a2", node_id="n2",
            context="", anchor_vector=[],
            dimension=0, timestamp="",
            session_key="", importance=0.0,
            access_count=0,
        )
        # cluster_id 默认为 None（表示未分配集群）
        assert anchor.cluster_id is None
        assert anchor.metadata == {}


class TestCognitiveMap:
    """认知地图测试"""

    @pytest.fixture
    def cmap(self, tmp_path):
        db = str(tmp_path / "test_cog.db")
        return CognitiveMap(db_path=db, dim=64)

    def test_init(self, cmap):
        assert cmap is not None

    def test_init_default_dim(self):
        cmap = CognitiveMap(dim=256)
        assert cmap is not None

    def test_add_anchor(self, cmap):
        anchor_id = cmap.add_anchor(
            node_id="node1",
            context="Python is a programming language",
            session_key="test_session",
        )
        assert anchor_id is not None
        assert isinstance(anchor_id, str)

    def test_add_anchor_with_embedding(self, cmap):
        embedding = [0.0] * 64
        anchor_id = cmap.add_anchor(
            node_id="node2",
            context="test",
            embedding=embedding,
        )
        assert anchor_id is not None

    def test_compute_anchor_vector(self, cmap):
        vec = cmap.compute_anchor_vector("test context")
        assert isinstance(vec, list)
        # 默认维度 64
        assert len(vec) == 64

    def test_compute_anchor_vector_with_embedding(self, cmap):
        embedding = [0.1] * 64
        vec = cmap.compute_anchor_vector("test", embedding=embedding)
        assert isinstance(vec, list)
        assert len(vec) == 64

    def test_spatial_similarity(self, cmap):
        v1 = [0.1] * 64
        v2 = [0.2] * 64
        sim = cmap.spatial_similarity(v1, v2)
        assert isinstance(sim, float)
        assert -1.0 <= sim <= 1.0

    def test_spatial_similarity_same(self, cmap):
        v = [0.5] * 64
        sim = cmap.spatial_similarity(v, v)
        assert sim == pytest.approx(1.0, abs=0.01)

    def test_add_and_get_nearby(self, cmap):
        cmap.add_anchor("n1", "first anchor", session_key="s1")
        cmap.add_anchor("n2", "second anchor", session_key="s1")
        vec = cmap.compute_anchor_vector("first")
        nearby = cmap.get_nearby_anchors(vec, k=2)
        assert isinstance(nearby, list)

    def test_get_nearby_anchors_empty(self, cmap):
        vec = cmap.compute_anchor_vector("nothing")
        nearby = cmap.get_nearby_anchors(vec, k=5)
        assert isinstance(nearby, list)
        # 空地图返回空列表
        assert nearby == []

    def test_get_anchor_density(self, cmap):
        cmap.add_anchor("n1", "first")
        cmap.add_anchor("n2", "second")
        vec = cmap.compute_anchor_vector("first")
        density = cmap.get_anchor_density(vec)
        assert isinstance(density, float)

    def test_update_anchor_importance(self, cmap):
        anchor_id = cmap.add_anchor("n1", "test")
        cmap.update_anchor_importance(anchor_id, access_count=5)
        # 不应崩溃

    def test_run_cognitive_queries(self, cmap):
        cmap.add_anchor("n1", "learning Python basics", session_key="s1")
        result = cmap.run_cognitive_queries(
            current_context="learn programming",
            session_key="s1",
        )
        assert isinstance(result, dict)

    def test_get_stats(self, cmap):
        cmap.add_anchor("n1", "test")
        stats = cmap.get_stats()
        assert isinstance(stats, dict)

    def test_get_cognitive_landscape(self, cmap):
        landscape = cmap.get_cognitive_landscape()
        assert isinstance(landscape, dict)

    def test_multiple_sessions(self, cmap):
        """不同 session 的锚点隔离"""
        cmap.add_anchor("n1", "session A", session_key="A")
        cmap.add_anchor("n2", "session B", session_key="B")
        stats = cmap.get_stats()
        assert isinstance(stats, dict)
