#!/usr/bin/env python3
"""
ANN 索引自动选择模块 [v2 — ContextEngine quantized index]
根据数据规模和精度要求自动选择最优索引+量化策略。

[v2] 新增：
- IVFPQ（Product Quantization）：真实压缩空间距离计算，4-16x 压缩
- PQ 子向量数自适应：dim / 4 或 dim / 8
- SQ（Scalar Quantizer）：FAISS IndexIVFScalarQuantizer
- 可配置精度偏好（speed / balanced / precision）

算法选择策略：
- <5000:      HNSW（最大精度）
- 5000-50K:  IVFSQ（标量量化，4x 压缩，>99% recall）
- 50K-500K:  IVFPQ（乘积量化，8-16x 压缩，>97% recall）
- >500K:     IVF + PQ 高压缩（16-32x）
"""

import numpy as np
import faiss
import os, json, logging, time
from typing import Tuple, Dict, Any, Optional

logger = logging.getLogger(__name__)


class ANNSelector:
    """
    ANN 索引自动选择器 [v2]
    """

    def __init__(
        self,
        n_vectors: int,
        dim: int = 4096,
        metric: str = 'cosine',
        precision: str = 'balanced',  # speed / balanced / precision
        index_path: Optional[str] = None,
    ):
        """
        Args:
            n_vectors: 向量数量
            dim: 向量维度
            metric: 距离度量 ('cosine' | 'ip' | 'l2')
            precision: 精度偏好
            index_path: 索引持久化路径（可选）
        """
        self.n_vectors = n_vectors
        self.dim = dim
        self.metric = metric
        self.precision = precision
        self.index_path = index_path
        self.faiss_index = None
        self.vectors = None

        self.algorithm = self._select_algorithm()
        self.quant_config = self._quant_config()

        logger.info(f"ANNSelector v2: {n_vectors} vecs, {dim}d, "
                    f"algo={self.algorithm}, quant={self.quant_config['type']}, "
                    f"precision={precision}")

    def _select_algorithm(self) -> str:
        if self.n_vectors < 5000:
            return 'hnsw'
        elif self.n_vectors < 50000:
            return 'ivf_sq'
        elif self.n_vectors < 500000:
            return 'ivfpq'
        else:
            return 'ivfpq_high'

    def _quant_config(self) -> Dict[str, Any]:
        cfg = {'type': 'none'}
        algo = self.algorithm
        p = self.precision

        if algo == 'hnsw':
            M = 48 if p == 'precision' else 32 if p == 'balanced' else 16
            cfg = {'type': 'flat', 'M': M}
        elif algo == 'ivf_sq':
            # FAISS IndexIVFScalarQuantizer — INT8 对称量化
            sq_type = faiss.ScalarQuantizer.QT_8bit
            cfg = {
                'type': 'sq8',
                'sq_type': sq_type,
                'nprobe': 20 if p == 'precision' else 10 if p == 'balanced' else 5,
                'compression': 4.0,
            }
        elif algo == 'ivfpq':
            # Product Quantization — 子向量编码
            m = max(1, self.dim // 4)  # 默认 4 子向量
            nbits = 8  # 每个子向量 8 bit
            cfg = {
                'type': 'pq8',
                'm': m,
                'nbits': nbits,
                'nprobe': 15 if p == 'precision' else 8 if p == 'balanced' else 4,
                'compression': (m * nbits) / (self.dim * 32) * 32,
            }
        elif algo == 'ivfpq_high':
            m = max(1, self.dim // 8)
            nbits = 8
            cfg = {
                'type': 'pq8',
                'm': m,
                'nbits': nbits,
                'nprobe': 10 if p == 'precision' else 5 if p == 'balanced' else 3,
                'compression': (m * nbits) / (self.dim * 32) * 32,
            }
        return cfg

    def build_index(self, vectors: np.ndarray):
        self.vectors = vectors.astype(np.float32)
        if self.metric == 'cosine':
            faiss.normalize_L2(self.vectors)

        algo = self.algorithm
        cfg = self.quant_config
        nlist = min(int(np.sqrt(self.n_vectors)), max(1, self.n_vectors))
        nlist = max(1, nlist)

        # 优先从持久化加载
        if self.index_path and os.path.exists(self.index_path):
            try:
                self.faiss_index = faiss.read_index(self.index_path)
                logger.info(f"FAISS index loaded: {self.index_path}")
                return
            except Exception as e:
                logger.warning(f"FAISS load failed, rebuilding: {e}")

        metric = faiss.METRIC_INNER_PRODUCT if self.metric != 'l2' else faiss.METRIC_L2

        if algo == 'hnsw':
            M = cfg['M']
            self.faiss_index = faiss.IndexHNSWFlat(self.dim, M)
            self.faiss_index.hnsw.metric = metric
            self.faiss_index.add(self.vectors)

        elif algo == 'ivf_sq':
            quantizer = faiss.IndexFlatIP(self.dim) if self.metric != 'l2' else faiss.IndexFlatL2(self.dim)
            self.faiss_index = faiss.IndexIVFScalarQuantizer(
                quantizer, self.dim, nlist, cfg['sq_type'], metric
            )
            self.faiss_index.train(self.vectors)
            self.faiss_index.add(self.vectors)
            self.faiss_index.nprobe = cfg['nprobe']

        elif algo in ('ivfpq', 'ivfpq_high'):
            # IndexIVFPQ — 乘积量化
            quantizer = faiss.IndexFlatIP(self.dim) if self.metric != 'l2' else faiss.IndexFlatL2(self.dim)
            self.faiss_index = faiss.IndexIVFPQ(
                quantizer, self.dim, nlist, cfg['m'], cfg['nbits'], metric
            )
            # IVFPQ 的 train 要求：先用 flat 编码器
            self.faiss_index.train(self.vectors)
            self.faiss_index.add(self.vectors)
            self.faiss_index.nprobe = cfg['nprobe']

        if self.index_path:
            os.makedirs(os.path.dirname(self.index_path) or '.', exist_ok=True)
            faiss.write_index(self.faiss_index, self.index_path)
            logger.info(f"FAISS index saved: {self.index_path}")

        logger.info(f"FAISS {algo} index built: {self.n_vectors} vecs, "
                    f"quant={cfg['type']}")

    def search(
        self,
        query: np.ndarray,
        top_k: int = 10
    ) -> Tuple[np.ndarray, np.ndarray]:
        if self.faiss_index is None or self.vectors is None:
            raise RuntimeError("ANNSelector: call build_index() first")

        q = query.astype(np.float32).reshape(1, -1)
        if self.metric == 'cosine':
            faiss.normalize_L2(q)

        scores, indices = self.faiss_index.search(q, top_k)
        return indices[0], scores[0]

    def add(self, vectors: np.ndarray):
        """增量添加向量（不重建索引，简单追加）"""
        v = vectors.astype(np.float32)
        if self.metric == 'cosine':
            faiss.normalize_L2(v)
        if self.faiss_index:
            self.faiss_index.add(v)
            self.vectors = np.vstack([self.vectors, v]) if self.vectors is not None else v
            self.n_vectors = len(self.vectors) if self.vectors is not None else 0
            if self.index_path:
                faiss.write_index(self.faiss_index, self.index_path)

    def get_stats(self) -> Dict[str, Any]:
        return {
            'algorithm': self.algorithm,
            'n_vectors': self.n_vectors,
            'dim': self.dim,
            'metric': self.metric,
            'precision': self.precision,
            'quant_config': self.quant_config,
        }

    def get_compression_ratio(self) -> float:
        """返回内存压缩比（相对 FP32 Flat）"""
        if self.algorithm == 'hnsw':
            return 1.0
        return self.quant_config.get('compression', 1.0)


# ── 快捷入口 ──

def auto_select_index(vectors: np.ndarray, dim: int = 4096, metric: str = 'cosine',
                      precision: str = 'balanced',
                      index_path: Optional[str] = None) -> ANNSelector:
    """一键创建并构建自动选择索引"""
    sel = ANNSelector(len(vectors), dim, metric, precision, index_path)
    sel.build_index(vectors)
    return sel


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== ANNSelector v2 测试 ===")

    for n in [100, 5000, 50000, 200000]:
        dim = 1024
        vecs = np.random.randn(n, dim).astype(np.float32)
        faiss.normalize_L2(vecs)
        q = np.random.randn(dim).astype(np.float32)
        faiss.normalize_L2(q)

        sel = ANNSelector(n, dim, 'cosine', 'balanced')
        t0 = time.time()
        sel.build_index(vecs)
        build_t = time.time() - t0

        t0 = time.time()
        idx, scores = sel.search(q, 10)
        search_t = time.time() - t0

        stats = sel.get_stats()
        print(f"  n={n:>7d}  algo={stats['algorithm']:<12s}  "
              f"quant={stats['quant_config']['type']:<5s}  "
              f"build={build_t*1000:.1f}ms  search={search_t*1000:.3f}ms  "
              f"compress={sel.get_compression_ratio():.1f}x")
