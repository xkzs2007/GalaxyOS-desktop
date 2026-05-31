#!/usr/bin/env python3
"""
ANN 索引自动选择模块
根据数据规模自动选择最优算法

算法选择策略：
- 小规模 (<10K): HNSW（高精度）
- 中等规模 (10K-1M): IVF（平衡）
- 大规模 (>1M): LSH（快速）
"""

import numpy as np
import faiss
from typing import Tuple, Dict, Any


class ANNSelector:
    """
    ANN 索引自动选择器
    """

    def __init__(
        self,
        n_vectors: int,
        dim: int = 4096,
        metric: str = 'cosine'
    ):
        """
        初始化选择器

        Args:
            n_vectors: 向量数量
            dim: 向量维度
            metric: 距离度量
        """
        self.n_vectors = n_vectors
        self.dim = dim
        self.metric = metric

        # 选择算法
        self.algorithm = self._select_algorithm()
        self.index = None

        print("ANN 选择器初始化:")
        print(f"  向量数: {n_vectors}")
        print(f"  算法: {self.algorithm}")

    def _select_algorithm(self) -> str:
        """
        根据数据规模选择算法

        Returns:
            str: 算法名称
        """
        if self.n_vectors < 10000:
            return 'hnsw'
        elif self.n_vectors < 1000000:
            return 'ivf'
        else:
            return 'lsh'

    def build_index(self, vectors: np.ndarray):
        """
        构建索引

        Args:
            vectors: 向量矩阵 (n, dim)
        """
        if self.algorithm == 'hnsw':
            self._build_hnsw(vectors)
        elif self.algorithm == 'ivf':
            self._build_ivf(vectors)
        else:
            self._build_lsh(vectors)

    def _build_hnsw(self, vectors: np.ndarray):
        """构建 HNSW 索引（FAISS HNSWFlat）"""
        vectors = vectors.astype(np.float32)
        faiss.normalize_L2(vectors)
        M = 32
        self.faiss_index = faiss.IndexHNSWFlat(self.dim, M)
        self.faiss_index.hnsw.metric = faiss.METRIC_INNER_PRODUCT
        self.faiss_index.add(vectors)
        self.vectors = vectors
        print(f"✅ HNSW 索引构建完成（FAISS, M={M}）")

    def _build_ivf(self, vectors: np.ndarray):
        """构建 IVF 索引（FAISS IVFFlat）"""
        vectors = vectors.astype(np.float32)
        faiss.normalize_L2(vectors)
        n_clusters = min(int(np.sqrt(self.n_vectors)), max(self.n_vectors, 1))
        quantizer = faiss.IndexFlatIP(self.dim)
        self.faiss_index = faiss.IndexIVFFlat(quantizer, self.dim, n_clusters, faiss.METRIC_INNER_PRODUCT)
        self.faiss_index.train(vectors)
        self.faiss_index.add(vectors)
        self.faiss_index.nprobe = min(10, n_clusters)
        self.vectors = vectors
        print(f"✅ IVF 索引构建完成（FAISS, {n_clusters} 聚类）")

    def _build_lsh(self, vectors: np.ndarray):
        """构建 LSH 索引（FAISS IndexLSH）"""
        vectors = vectors.astype(np.float32)
        faiss.normalize_L2(vectors)
        n_bits = 16
        self.faiss_index = faiss.IndexLSH(self.dim, n_bits)
        self.faiss_index.train(vectors)
        self.faiss_index.add(vectors)
        self.vectors = vectors
        print(f"✅ LSH 索引构建完成（FAISS, {n_bits} bits）")

    def search(
        self,
        query: np.ndarray,
        top_k: int = 10
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        搜索

        Args:
            query: 查询向量
            top_k: 返回数量

        Returns:
            Tuple[np.ndarray, np.ndarray]: (索引, 得分)
        """
        if self.vectors is None:
            raise RuntimeError("ANNSelector: 必须先调用 build_index() 再调用 search()")
        if self.algorithm == 'hnsw':
            return self._search_hnsw(query, top_k)
        elif self.algorithm == 'ivf':
            return self._search_ivf(query, top_k)
        else:
            return self._search_lsh(query, top_k)

    def _search_hnsw(
        self,
        query: np.ndarray,
        top_k: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        """HNSW 搜索（FAISS）"""
        query = query.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(query)
        scores, indices = self.faiss_index.search(query, top_k)
        return indices[0], scores[0]

    def _search_ivf(
        self,
        query: np.ndarray,
        top_k: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        """IVF 搜索（FAISS）"""
        query = query.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(query)
        scores, indices = self.faiss_index.search(query, top_k)
        return indices[0], scores[0]

    def _search_lsh(
        self,
        query: np.ndarray,
        top_k: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        """LSH 搜索（FAISS）"""
        query = query.astype(np.float32).reshape(1, -1)
        faiss.normalize_L2(query)
        scores, indices = self.faiss_index.search(query, top_k)
        return indices[0], scores[0]

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'algorithm': self.algorithm,
            'n_vectors': self.n_vectors,
            'dim': self.dim,
            'metric': self.metric
        }


if __name__ == "__main__":
    # 测试
    print("=== ANN 选择器测试 ===")

    dim = 4096
    n_vectors = 50000

    vectors = np.random.randn(n_vectors, dim).astype(np.float32)
    query = np.random.randn(dim).astype(np.float32)

    selector = ANNSelector(n_vectors, dim)
    selector.build_index(vectors)

    import time
    start = time.time()
    indices, scores = selector.search(query, top_k=20)
    elapsed = time.time() - start
    print(f"搜索耗时: {elapsed*1000:.2f}ms")
