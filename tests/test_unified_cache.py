"""
测试 UnifiedCache — 统一缓存接口（memory 后端）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.unified_cache import UnifiedCache


class TestUnifiedCacheMemoryBackend:
    """内存后端测试"""

    @pytest.fixture
    def cache(self):
        return UnifiedCache(backend="memory", max_size=10)

    def test_set_and_get(self, cache):
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_key(self, cache):
        assert cache.get("nonexistent") is None

    def test_delete(self, cache):
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
        cache.delete("key1")
        assert cache.get("key1") is None

    def test_delete_nonexistent(self, cache):
        # 不应抛异常
        cache.delete("nonexistent")

    def test_overwrite(self, cache):
        cache.set("key1", "v1")
        cache.set("key1", "v2")
        assert cache.get("key1") == "v2"

    def test_multiple_keys(self, cache):
        for i in range(5):
            cache.set(f"key{i}", f"value{i}")
        for i in range(5):
            assert cache.get(f"key{i}") == f"value{i}"

    def test_stats(self, cache):
        cache.set("a", 1)
        cache.set("b", 2)
        stats = cache.stats()
        assert stats["backend"] == "memory"
        assert stats["count"] == 2
        assert stats["max_size"] == 10

    def test_backend_name_property(self, cache):
        assert cache.backend_name == "memory"

    def test_backend_property(self, cache):
        from services.unified_cache import _MemoryCacheBackend
        assert isinstance(cache.backend, _MemoryCacheBackend)

    def test_fifo_eviction(self, cache):
        """FIFO 驱逐：超过 max_size 时最旧的被驱逐"""
        small = UnifiedCache(backend="memory", max_size=3)
        small.set("a", 1)
        small.set("b", 2)
        small.set("c", 3)
        small.set("d", 4)  # 驱逐 "a"
        assert small.get("a") is None
        assert small.get("b") == 2
        assert small.get("c") == 3
        assert small.get("d") == 4

    def test_fifo_eviction_does_not_evict_existing(self, cache):
        """已存在的 key 即使满了也不驱逐"""
        small = UnifiedCache(backend="memory", max_size=3)
        small.set("a", 1)
        small.set("b", 2)
        small.set("c", 3)
        small.set("a", 999)  # 更新，不驱逐
        assert small.get("a") == 999
        assert small.get("b") == 2
        assert small.get("c") == 3
        assert len(small.backend._cache) == 3

    def test_complex_values(self, cache):
        cache.set("list", [1, 2, 3])
        cache.set("dict", {"x": 1})
        cache.set("nested", {"a": [1, {"b": 2}]})
        assert cache.get("list") == [1, 2, 3]
        assert cache.get("dict") == {"x": 1}
        assert cache.get("nested") == {"a": [1, {"b": 2}]}

    def test_set_returns_true(self, cache):
        assert cache.set("x", 1) is True

    def test_delete_returns_true(self, cache):
        cache.set("x", 1)
        assert cache.delete("x") is True

    def test_delete_nonexistent_does_not_raise(self, cache):
        # 删除不存在的 key 不应抛异常
        result = cache.delete("ghost")
        assert result is not None  # 可能返回 True 也可能 False，视实现而定

    def test_set_after_delete(self, cache):
        cache.set("x", "old")
        cache.delete("x")
        cache.set("x", "new")
        assert cache.get("x") == "new"


class TestUnifiedCacheInvalidBackend:
    """非法后端测试"""

    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="未知的缓存后端"):
            UnifiedCache(backend="invalid_xyz")

    def test_valid_backends_do_not_raise(self):
        # memory 后端应该正常工作
        c = UnifiedCache(backend="memory", max_size=5)
        assert c.backend_name == "memory"
