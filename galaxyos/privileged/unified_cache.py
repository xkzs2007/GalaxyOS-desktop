"""
统一缓存接口

抽象出一致的缓存 API，替代项目中分散的多套缓存实现。
- scripts_core/cache.py: 磁盘 + TTL
- rag_cache.py: 多级 LRU-K（内部使用）
- scripts_core/embedding.py: 内存 + 预计算 JSON（内部使用）

统一接口供外部调用，内部实现仍可独立运作。
"""

from typing import Any, Optional, Dict
import logging

logger = logging.getLogger(__name__)


class UnifiedCache:
    """
    统一缓存接口

    提供一致的 get/set/delete/stats API，
    底层可委托给不同的缓存实现。
    """

    def __init__(self, backend: str = "disk", **kwargs):
        """
        Args:
            backend: 缓存后端类型
                - "disk": 磁盘缓存（CacheManager，支持 TTL + gzip）
                - "memory": 内存缓存（dict，适合临时数据）
                - "rag": RAG 多级缓存（RAGCache，适合向量+知识）
            **kwargs: 传递给后端的参数
        """
        self._backend_name = backend
        self._backend = self._create_backend(backend, **kwargs)

    def _create_backend(self, backend: str, **kwargs):
        """创建缓存后端实例"""
        if backend == "disk":
            from .scripts_core.cache import CacheManager
            return CacheManager(
                cache_dir=kwargs.get('cache_dir'),
                ttl=kwargs.get('ttl', 3600),
            )
        elif backend == "memory":
            return _MemoryCacheBackend(
                max_size=kwargs.get('max_size', 1000),
            )
        elif backend == "rag":
            from .rag_cache import RAGCache
            return RAGCache(
                gpu_cache_size=kwargs.get('gpu_cache_size', 1000),
                host_cache_size=kwargs.get('host_cache_size', 10000),
                persist_path=kwargs.get('persist_path'),
            )
        else:
            raise ValueError(f"未知的缓存后端: {backend}，可选: disk, memory, rag")

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值"""
        return self._backend.get(key) if hasattr(self._backend, 'get') else None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """设置缓存值"""
        try:
            if hasattr(self._backend, 'set'):
                self._backend.set(key, value)
                return True
            return False
        except Exception as e:
            logger.error(f"缓存设置失败: {e}")
            return False

    def delete(self, key: str) -> bool:
        """删除缓存"""
        try:
            if hasattr(self._backend, 'delete'):
                self._backend.delete(key)
                return True
            return False
        except Exception as e:
            logger.error(f"缓存删除失败: {e}")
            return False

    def stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        if hasattr(self._backend, 'stats'):
            return self._backend.stats()
        if hasattr(self._backend, 'get_stats'):
            return self._backend.get_stats()
        return {'backend': self._backend_name}

    @property
    def backend_name(self) -> str:
        """获取后端名称"""
        return self._backend_name

    @property
    def backend(self) -> Any:
        """获取原始后端实例（高级用法）"""
        return self._backend


class _MemoryCacheBackend:
    """简单内存缓存后端"""

    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._cache: Dict[str, Any] = {}

    def get(self, key: str) -> Optional[Any]:
        return self._cache.get(key)

    def set(self, key: str, value: Any):
        if len(self._cache) >= self.max_size and key not in self._cache:
            # 简单 FIFO 驱逐
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = value

    def delete(self, key: str):
        self._cache.pop(key, None)

    def stats(self) -> Dict[str, Any]:
        return {
            'backend': 'memory',
            'count': len(self._cache),
            'max_size': self.max_size,
        }


__all__ = ['UnifiedCache']
