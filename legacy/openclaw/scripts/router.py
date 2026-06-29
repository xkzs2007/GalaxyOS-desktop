"""智能路由 - 根据查询复杂度选择模式

v2.1: 增加可插拔搜索后端抽象，替代硬编码 mock 数据。
"""

from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class SearchBackend:
    """搜索后端抽象基类

    子类需实现 search_vector 和 search_fts 方法。
    """

    def search_vector(self, query: str, top_k: int = 10) -> List[Dict]:
        """向量搜索

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            List[Dict]: 搜索结果列表，每个结果至少包含 record_id, content, type, scene
        """
        raise NotImplementedError("子类需实现 search_vector")

    def search_fts(self, query: str, top_k: int = 10) -> List[Dict]:
        """全文搜索

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            List[Dict]: 搜索结果列表
        """
        raise NotImplementedError("子类需实现 search_fts")


class MockSearchBackend(SearchBackend):
    """Mock 搜索后端（默认，仅用于开发和测试）"""

    def search_vector(self, query: str, top_k: int = 10) -> List[Dict]:
        return [{"record_id": "mock", "content": query, "type": "memory", "scene": "default"}]

    def search_fts(self, query: str, top_k: int = 10) -> List[Dict]:
        return [{"record_id": "mock", "content": query, "type": "memory", "scene": "default"}]


class FAISSSearchBackend(SearchBackend):
    """FAISS 向量搜索后端（需安装 faiss-cpu 或 faiss-gpu）

    使用方式:
        from scripts_core.router import QueryRouter, FAISSSearchBackend
        router = QueryRouter()
        router.set_backend(FAISSSearchBackend(embedding_dim=768))
    """

    def __init__(self, embedding_dim: int = 768, index_path: Optional[str] = None):
        self.embedding_dim = embedding_dim
        self.index_path = index_path
        self._index = None
        self._id_map = {}
        self._faiss = None

        try:
            import faiss
            self._faiss = faiss
            if index_path:
                self._index = faiss.read_index(index_path)
            else:
                self._index = faiss.IndexFlatIP(embedding_dim)
        except ImportError:
            logger.warning("faiss 未安装，请执行: pip install faiss-cpu 或 faiss-gpu")

    def search_vector(self, query: str, top_k: int = 10) -> List[Dict]:
        if self._faiss is None or self._index is None:
            return []

        # 需要 embedding 引擎来将查询转为向量
        try:
            from .embedding import EmbeddingEngine
            engine = EmbeddingEngine()
            query_vec = engine.encode(query)
            if query_vec is None:
                return []

            import numpy as np
            vec = np.array([query_vec], dtype=np.float32)
            scores, indices = self._index.search(vec, top_k)

            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx >= 0 and idx in self._id_map:
                    results.append(self._id_map[idx])
            return results
        except Exception as e:
            logger.error(f"FAISS 搜索失败: {e}")
            return []

    def search_fts(self, query: str, top_k: int = 10) -> List[Dict]:
        # FAISS 不支持 FTS，返回空
        return []


class QueryRouter:
    """智能查询路由器"""

    # 全局默认搜索后端
    _default_backend: Optional[SearchBackend] = None

    def __init__(self, backend: Optional[SearchBackend] = None):
        """
        Args:
            backend: 搜索后端实例。None 则使用全局默认后端（或 MockBackend）。
        """
        self._backend = backend or self._default_backend or MockSearchBackend()

    @classmethod
    def set_default_backend(cls, backend: SearchBackend):
        """设置全局默认搜索后端"""
        cls._default_backend = backend

    def set_backend(self, backend: SearchBackend):
        """设置实例搜索后端"""
        self._backend = backend

    @staticmethod
    def analyze(query: str) -> str:
        """分析查询复杂度"""
        # 简单特征
        simple_score = sum([
            len(query) < 10,
            len(query.split()) <= 2,
            any(kw in query for kw in ["推送", "配置", "设置", "状态", "规则"]),
        ])

        # 复杂特征
        complex_score = sum([
            len(query) > 30,
            "或者" in query or "和" in query,
            "?" in query or "？" in query,
            any(kw in query for kw in ["比较", "分析", "为什么", "如何", "区别"]),
        ])

        if complex_score > simple_score:
            return "full"
        elif simple_score >= 2:
            return "fast"
        else:
            return "balanced"

    @staticmethod
    def select_mode(query: str, use_llm: bool) -> str:
        """选择搜索模式"""
        if not use_llm:
            return "fast"

        complexity = QueryRouter.analyze(query)

        if complexity == "full":
            return "full"
        elif complexity == "fast":
            return "fast"
        else:
            return "balanced"

    def route(self, query: str, mode: str = "hybrid", top_k: int = 10) -> Dict[str, List[Dict]]:
        """
        路由查询到不同的搜索模式

        Args:
            query: 查询文本
            mode: 搜索模式 ("vector", "fts", "hybrid")
            top_k: 每个搜索模式返回的结果数

        Returns:
            Dict: 路由结果
        """
        complexity = self.analyze(query)

        vector_results = []
        fts_results = []

        try:
            if mode in ("vector", "hybrid"):
                vector_results = self._backend.search_vector(query, top_k=top_k)
        except Exception as e:
            logger.error(f"向量搜索失败: {e}")

        try:
            if mode in ("fts", "hybrid"):
                fts_results = self._backend.search_fts(query, top_k=top_k)
        except Exception as e:
            logger.error(f"FTS 搜索失败: {e}")

        return {
            "vector": vector_results,
            "fts": fts_results,
            "mode": mode,
            "complexity": complexity
        }
