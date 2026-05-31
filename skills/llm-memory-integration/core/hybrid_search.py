#!/usr/bin/env python3
"""
混合搜索模块 (Hybrid Search + Query Rewriting)

论文参考:
- Dense X Retrieval (2024): 检索粒度优化
- RAG Survey (2024): Hybrid Search (Dense + Sparse) + RRF 融合，召回率 +20-30%
- Query Rewriting for RAG (2024): 查询改写提升检索准确率
- Multi-Query Expansion: 从单一查询扩展为多角度查询

功能:
1. Hybrid Search: Dense (向量) + Sparse (BM25/TF-IDF) 混合检索 + RRF 融合
2. Query Rewriting: 查询改写（扩展、简化、分解）
3. Multi-Query Expansion: 多查询扩展
4. RRF (Reciprocal Rank Fusion): 多路检索结果融合
"""

import logging
import math
import re
import time
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

import numpy as np

logger = logging.getLogger(__name__)


# ==================== Sparse 检索 (BM25) ====================

class BM25Index:
    """
    BM25 稀疏检索索引

    经典 BM25 算法，用于关键词级别的精确匹配。
    与向量检索 (Dense) 互补，提高召回率。

    公式: BM25(D, Q) = sum(IDF(qi) * (f(qi, D) * (k1 + 1)) / (f(qi, D) + k1 * (1 - b + b * |D| / avgdl)))
    """

    def __init__(
        self,
        k1: float = 1.5,
        b: float = 0.75,
        language: str = "zh",
    ):
        self.k1 = k1
        self.b = b
        self.language = language

        # 文档存储
        self._docs: List[str] = []
        self._doc_ids: List[str] = []
        self._doc_metadata: List[Dict] = []

        # BM25 统计
        self._doc_freqs: List[Dict[str, int]] = []  # 每个文档的词频
        self._df: Dict[str, int] = defaultdict(int)  # 文档频率
        self._avgdl: float = 0.0  # 平均文档长度
        self._N: int = 0  # 文档总数
        self._total_doc_len: int = 0  # 总文档长度（增量维护，避免每次重算）

    def add_documents(
        self,
        documents: List[str],
        doc_ids: Optional[List[str]] = None,
        metadata: Optional[List[Dict]] = None,
    ):
        """
        添加文档到索引

        Args:
            documents: 文档文本列表
            doc_ids: 文档 ID 列表
            metadata: 元数据列表
        """
        doc_ids = doc_ids or [str(i) for i in range(len(documents))]
        metadata = metadata or [{} for _ in documents]

        for doc, doc_id, meta in zip(documents, doc_ids, metadata):
            self._docs.append(doc)
            self._doc_ids.append(doc_id)
            self._doc_metadata.append(meta)

            # 分词
            tokens = self._tokenize(doc)
            token_freqs: Dict[str, int] = defaultdict(int)
            for token in tokens:
                token_freqs[token] += 1
            self._doc_freqs.append(dict(token_freqs))

            # 更新文档频率
            for token in set(tokens):
                self._df[token] += 1

            self._N += 1
            # 增量更新总文档长度和平均长度
            self._total_doc_len += len(tokens)
            self._avgdl = self._total_doc_len / self._N if self._N > 0 else 0

    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> List[Tuple[str, float, Dict]]:
        """
        BM25 搜索

        Args:
            query: 查询文本
            top_k: 返回数量

        Returns:
            List[Tuple[doc_id, score, metadata]]
        """
        if not self._docs:
            return []

        query_tokens = self._tokenize(query)
        scores = []

        for idx in range(self._N):
            score = self._bm25_score(query_tokens, idx)
            scores.append((self._doc_ids[idx], score, self._doc_metadata[idx]))

        # 排序
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def _bm25_score(self, query_tokens: List[str], doc_idx: int) -> float:
        """计算 BM25 分数"""
        doc_freqs = self._doc_freqs[doc_idx]
        doc_len = sum(doc_freqs.values())

        score = 0.0
        for token in query_tokens:
            if token not in doc_freqs:
                continue

            # IDF
            df = self._df.get(token, 0)
            idf = math.log((self._N - df + 0.5) / (df + 0.5) + 1)

            # TF 部分
            tf = doc_freqs[token]
            tf_norm = (tf * (self.k1 + 1)) / (
                tf + self.k1 * (1 - self.b + self.b * doc_len / (self._avgdl + 1e-10))
            )

            score += idf * tf_norm

        return score

    def _tokenize(self, text: str) -> List[str]:
        """分词"""
        if self.language == "zh":
            # 中文: 按字符 + 标点分割
            tokens = []
            current = ""
            for ch in text:
                if '\u4e00' <= ch <= '\u9fff':
                    if current:
                        tokens.extend(current.lower().split())
                        current = ""
                    tokens.append(ch)
                elif ch in (' ', '\n', '\t', '。', '，', '！', '？', '、', '；', '：', '"', '"', '（', '）'):
                    if current:
                        tokens.extend(current.lower().split())
                        current = ""
                else:
                    current += ch
            if current:
                tokens.extend(current.lower().split())
            return tokens
        else:
            # 英文: 简单空格分割 + 小写
            return text.lower().split()

    def get_stats(self) -> Dict:
        return {
            'num_docs': self._N,
            'avg_doc_length': round(self._avgdl, 2),
            'vocabulary_size': len(self._df),
            'k1': self.k1,
            'b': self.b,
        }


# ==================== RRF 融合 ====================

class RRFFusion:
    """
    Reciprocal Rank Fusion (RRF)

    将多路检索结果融合为统一排序列表。

    公式: RRF_score(d) = sum(1 / (k + rank_i(d)))
    其中 k 通常取 60

    论文: Cormack et al. (2009) - Reciprocal Rank Fusion outperforms Condorcet
    """

    def __init__(self, k: int = 60):
        """
        Args:
            k: RRF 参数 (默认 60)
        """
        self.k = k

    def fuse(
        self,
        result_lists: List[List[Tuple[str, float, Dict]]],
        weights: Optional[List[float]] = None,
    ) -> List[Tuple[str, float, Dict]]:
        """
        融合多路检索结果

        Args:
            result_lists: 多路检索结果列表，每路格式为 [(doc_id, score, metadata), ...]
            weights: 每路结果的权重

        Returns:
            融合后的排序列表 [(doc_id, rrf_score, metadata), ...]
        """
        if not result_lists:
            return []

        weights = weights or [1.0] * len(result_lists)

        # 计算 RRF 分数
        rrf_scores: Dict[str, float] = defaultdict(float)
        doc_metadata: Dict[str, Dict] = {}

        for result_list, weight in zip(result_lists, weights):
            for rank, (doc_id, score, metadata) in enumerate(result_list, 1):
                rrf_scores[doc_id] += weight / (self.k + rank)
                if doc_id not in doc_metadata:
                    doc_metadata[doc_id] = metadata

        # 排序
        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        return [
            (doc_id, score, doc_metadata.get(doc_id, {}))
            for doc_id, score in sorted_results
        ]


# ==================== 查询改写 ====================

class QueryRewriter:
    """
    查询改写器

    支持:
    1. 查询扩展: 添加同义词、相关词
    2. 查询简化: 去除冗余词
    3. 查询分解: 将复合查询拆分为子查询
    4. LLM 改写: 用 LLM 生成更好的查询表述
    """

    # 同义词扩展词典（基础版，可扩展）
    _SYNONYM_DICT: Dict[str, List[str]] = {
        "机器学习": ["ML", "machine learning", "统计学习"],
        "深度学习": ["DL", "deep learning", "神经网络"],
        "人工智能": ["AI", "artificial intelligence"],
        "自然语言处理": ["NLP", "natural language processing"],
        "向量搜索": ["向量检索", "vector search", "近似最近邻", "ANN"],
        "数据库": ["DB", "database", "存储系统"],
        "优化": ["优化器", "optimizer", "调优"],
        "缓存": ["cache", "缓冲", "缓存系统"],
        "推理": ["inference", "推断", "预测"],
        "训练": ["training", "学习", "拟合"],
    }

    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client

    def expand(self, query: str) -> List[str]:
        """
        查询扩展: 添加同义词和相关词

        Args:
            query: 原始查询

        Returns:
            扩展后的查询列表 [原查询, 扩展1, 扩展2, ...]
        """
        queries = [query]

        # 基于同义词词典扩展
        for term, synonyms in self._SYNONYM_DICT.items():
            if term in query:
                for syn in synonyms:
                    expanded = query.replace(term, syn)
                    if expanded != query:
                        queries.append(expanded)

        return queries

    def simplify(self, query: str) -> str:
        """
        查询简化: 去除停用词和冗余

        Args:
            query: 原始查询

        Returns:
            简化后的查询
        """
        # 中文停用词
        stopwords = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
            "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
            "你", "会", "着", "没有", "看", "好", "自己", "这",
        }

        tokens = []
        current = ""
        for ch in query:
            if '\u4e00' <= ch <= '\u9fff':
                if current:
                    tokens.append(current)
                    current = ""
                if ch not in stopwords:
                    tokens.append(ch)
            elif ch in (' ', '\n'):
                if current:
                    tokens.append(current)
                    current = ""
            else:
                current += ch
        if current:
            tokens.append(current)

        return " ".join(tokens)

    def decompose(self, query: str) -> List[str]:
        """
        查询分解: 将复合查询拆分为子查询

        Args:
            query: 原始查询

        Returns:
            子查询列表
        """
        # 基于分隔符拆分
        separators = ['，', '、', '；', '和', '与', '以及', '还有', '另外', '同时', ',', ';', 'and', 'or']

        subqueries = [query]
        for sep in separators:
            new_subqueries = []
            for sq in subqueries:
                parts = sq.split(sep)
                new_subqueries.extend([p.strip() for p in parts if p.strip()])
            subqueries = new_subqueries

        # 如果只有一个结果，尝试基于问号拆分
        if len(subqueries) == 1:
            parts = re.split(r'[？?]', subqueries[0])
            subqueries = [p.strip() for p in parts if p.strip()]

        return subqueries if subqueries else [query]

    def rewrite_with_llm(self, query: str, context: str = "") -> Optional[str]:
        """
        使用 LLM 改写查询

        Args:
            query: 原始查询
            context: 上下文

        Returns:
            改写后的查询
        """
        if self.llm_client is None:
            return None

        try:
            prompt = [
                {"role": "system", "content": "你是一个查询优化专家。将用户的查询改写为更适合检索的表述。"},
                {"role": "user", "content": (
                    f"原始查询: {query}\n"
                    f"{'上下文: ' + context if context else ''}\n\n"
                    "请改写这个查询，使其更精确、更适合信息检索。\n"
                    "只输出改写后的查询，不要解释。"
                )}
            ]
            result = self.llm_client.chat(prompt, max_tokens=100, temperature=0.3)
            return result.strip() if result else None
        except Exception as e:
            logger.error(f"LLM 查询改写失败: {e}")
            return None

    def multi_query_expand(self, query: str, n: int = 3) -> List[str]:
        """
        多查询扩展: 从不同角度生成多个查询

        Args:
            query: 原始查询
            n: 生成数量

        Returns:
            扩展的查询列表
        """
        if self.llm_client is None:
            # 回退: 用同义词扩展
            return self.expand(query)[:n]

        try:
            prompt = [
                {"role": "system", "content": "你是一个查询扩展专家。"},
                {"role": "user", "content": (
                    f"原始查询: {query}\n\n"
                    f"请生成 {n} 个不同角度的查询变体，每个一行，"
                    "用于多路检索以提高召回率。\n"
                    "只输出查询，每行一个，不要编号。"
                )}
            ]
            result = self.llm_client.chat(prompt, max_tokens=200, temperature=0.5)

            if result:
                variants = [q.strip() for q in result.strip().split('\n') if q.strip()]
                return [query] + variants[:n]  # 包含原查询
            return [query]
        except Exception as e:
            logger.error(f"多查询扩展失败: {e}")
            return [query]


# ==================== Hybrid Search ====================

@dataclass
class HybridSearchResult:
    """混合搜索结果"""
    doc_id: str
    score: float
    content: str
    metadata: Dict = field(default_factory=dict)
    source: str = ""              # "dense" / "sparse" / "hybrid"
    dense_score: float = 0.0
    sparse_score: float = 0.0


class HybridSearcher:
    """
    混合搜索器

    Dense (向量) + Sparse (BM25) 混合检索 + RRF 融合。

    使用示例:
    >>> searcher = HybridSearcher(embedding_client=emb_client)
    >>> searcher.add_documents(["文档1", "文档2"], doc_ids=["1", "2"])
    >>> results = searcher.search("查询", top_k=10)
    """

    def __init__(
        self,
        embedding_client: Any = None,
        bm25_k1: float = 1.5,
        bm25_b: float = 0.75,
        rrf_k: int = 60,
        dense_weight: float = 0.6,
        sparse_weight: float = 0.4,
        enable_query_rewrite: bool = True,
        llm_client: Any = None,
    ):
        self.embedding_client = embedding_client
        self.bm25 = BM25Index(k1=bm25_k1, b=bm25_b)
        self.rrf = RRFFusion(k=rrf_k)
        self.dense_weight = dense_weight
        self.sparse_weight = sparse_weight
        self.query_rewriter = QueryRewriter(llm_client=llm_client)
        self.enable_query_rewrite = enable_query_rewrite

        # Dense 索引
        self._vectors: List[np.ndarray] = []
        self._doc_ids: List[str] = []
        self._doc_contents: List[str] = []
        self._doc_metadata: List[Dict] = []
        self._doc_id_to_index: Dict[str, int] = {}  # 快速查找 doc_id → 索引

        # 统计
        self.stats = {
            'total_searches': 0,
            'total_rewrites': 0,
            'dense_only_hits': 0,
            'sparse_only_hits': 0,
            'hybrid_hits': 0,
        }

    def add_documents(
        self,
        documents: List[str],
        doc_ids: Optional[List[str]] = None,
        metadata: Optional[List[Dict]] = None,
    ):
        """
        添加文档到索引

        Args:
            documents: 文档文本列表
            doc_ids: 文档 ID 列表
            metadata: 元数据列表
        """
        doc_ids = doc_ids or [str(i) for i in range(len(documents))]
        metadata = metadata or [{} for _ in documents]

        # 添加到 BM25 索引
        self.bm25.add_documents(documents, doc_ids, metadata)

        # 添加到 Dense 索引
        for doc, doc_id, meta in zip(documents, doc_ids, metadata):
            self._doc_id_to_index[doc_id] = len(self._doc_ids)
            self._doc_ids.append(doc_id)
            self._doc_contents.append(doc)
            self._doc_metadata.append(meta)

            # 获取向量
            if self.embedding_client is not None:
                emb = self.embedding_client.embed(doc[:500])
                if emb is not None:
                    self._vectors.append(emb)
                else:
                    self._vectors.append(np.zeros(1, dtype=np.float32))
            else:
                self._vectors.append(np.zeros(1, dtype=np.float32))

    def search(
        self,
        query: str,
        top_k: int = 10,
        mode: str = "hybrid",  # "dense" / "sparse" / "hybrid"
        enable_rewrite: Optional[bool] = None,
    ) -> List[HybridSearchResult]:
        """
        混合搜索

        Args:
            query: 查询文本
            top_k: 返回数量
            mode: 搜索模式
            enable_rewrite: 是否启用查询改写

        Returns:
            搜索结果列表
        """
        start = time.time()
        self.stats['total_searches'] += 1

        enable_rewrite = enable_rewrite if enable_rewrite is not None else self.enable_query_rewrite

        # 查询改写
        expanded_queries = [query]
        if enable_rewrite:
            expanded_queries = self.query_rewriter.expand(query)
            self.stats['total_rewrites'] += 1

        # Dense 检索
        dense_results = []
        if mode in ("dense", "hybrid"):
            dense_results = self._dense_search(expanded_queries, top_k)

        # Sparse 检索
        sparse_results = []
        if mode in ("sparse", "hybrid"):
            sparse_results = self._sparse_search(expanded_queries, top_k)

        # 融合
        if mode == "hybrid" and dense_results and sparse_results:
            fused = self.rrf.fuse(
                [dense_results, sparse_results],
                weights=[self.dense_weight, self.sparse_weight],
            )
            self.stats['hybrid_hits'] += 1

            # 构建结果
            results = []
            for doc_id, score, meta in fused[:top_k]:
                idx = self._doc_id_to_index.get(doc_id, -1)
                content = self._doc_contents[idx] if idx >= 0 else ""
                dense_score = next((s for d, s, m in dense_results if d == doc_id), 0.0)
                sparse_score = next((s for d, s, m in sparse_results if d == doc_id), 0.0)
                results.append(HybridSearchResult(
                    doc_id=doc_id,
                    score=score,
                    content=content,
                    metadata=meta,
                    source="hybrid",
                    dense_score=dense_score,
                    sparse_score=sparse_score,
                ))
            return results

        elif mode == "dense" and dense_results:
            self.stats['dense_only_hits'] += 1
            results = []
            for doc_id, score, meta in dense_results[:top_k]:
                idx = self._doc_id_to_index.get(doc_id, -1)
                content = self._doc_contents[idx] if idx >= 0 else ""
                results.append(HybridSearchResult(
                    doc_id=doc_id, score=score, content=content,
                    metadata=meta, source="dense", dense_score=score,
                ))
            return results

        elif mode == "sparse" and sparse_results:
            self.stats['sparse_only_hits'] += 1
            results = []
            for doc_id, score, meta in sparse_results[:top_k]:
                idx = self._doc_id_to_index.get(doc_id, -1)
                content = self._doc_contents[idx] if idx >= 0 else ""
                results.append(HybridSearchResult(
                    doc_id=doc_id, score=score, content=content,
                    metadata=meta, source="sparse", sparse_score=score,
                ))
            return results

        return []

    def _dense_search(
        self,
        queries: List[str],
        top_k: int,
    ) -> List[Tuple[str, float, Dict]]:
        """Dense 向量检索"""
        if not self._vectors or self.embedding_client is None:
            return []

        all_results = []
        for query in queries:
            query_emb = self.embedding_client.embed(query)
            if query_emb is None:
                continue

            q_norm = query_emb / (np.linalg.norm(query_emb) + 1e-10)
            scores = []
            for i, vec in enumerate(self._vectors):
                if vec.shape[0] <= 1:
                    continue
                v_norm = vec / (np.linalg.norm(vec) + 1e-10)
                sim = float(np.dot(q_norm, v_norm))
                scores.append((self._doc_ids[i], sim, self._doc_metadata[i]))

            scores.sort(key=lambda x: x[1], reverse=True)
            all_results.extend(scores[:top_k])

        # 去重（保留最高分）
        seen = {}
        for doc_id, score, meta in all_results:
            if doc_id not in seen or score > seen[doc_id][1]:
                seen[doc_id] = (doc_id, score, meta)

        return sorted(seen.values(), key=lambda x: x[1], reverse=True)

    def _sparse_search(
        self,
        queries: List[str],
        top_k: int,
    ) -> List[Tuple[str, float, Dict]]:
        """Sparse BM25 检索"""
        all_results = []
        for query in queries:
            results = self.bm25.search(query, top_k=top_k)
            all_results.extend(results)

        # 去重（保留最高分）
        seen = {}
        for doc_id, score, meta in all_results:
            if doc_id not in seen or score > seen[doc_id][1]:
                seen[doc_id] = (doc_id, score, meta)

        return sorted(seen.values(), key=lambda x: x[1], reverse=True)

    def get_stats(self) -> Dict:
        return dict(self.stats)


# 导出
__all__ = [
    'HybridSearcher',
    'BM25Index',
    'RRFFusion',
    'QueryRewriter',
    'HybridSearchResult',
]


if __name__ == "__main__":
    print("=== 混合搜索测试 ===\n")

    # 创建搜索器
    searcher = HybridSearcher(enable_query_rewrite=True)

    # 添加文档
    docs = [
        "机器学习是人工智能的一个分支，使用算法从数据中学习模式。",
        "深度学习是机器学习的子集，使用多层神经网络进行特征学习。",
        "自然语言处理是AI的重要应用领域，涉及文本理解和生成。",
        "向量搜索是信息检索的核心技术，通过向量相似度匹配文档。",
        "RAG（检索增强生成）结合了检索和生成，提升LLM的回答质量。",
    ]
    doc_ids = [f"doc_{i}" for i in range(len(docs))]
    searcher.add_documents(docs, doc_ids=doc_ids)

    # BM25 搜索测试
    print("1. BM25 搜索:")
    results = searcher.bm25.search("机器学习", top_k=3)
    for doc_id, score, meta in results:
        print(f"   {doc_id}: score={score:.4f}")

    # 混合搜索测试
    print("\n2. 混合搜索 (Sparse only, 无 Embedding API):")
    results = searcher.search("机器学习", top_k=3, mode="sparse")
    for r in results:
        print(f"   {r.doc_id}: score={r.score:.4f}, source={r.source}")

    # 查询改写测试
    print("\n3. 查询改写:")
    rewriter = QueryRewriter()
    expanded = rewriter.expand("什么是机器学习？")
    print(f"   扩展: {expanded}")

    decomposed = rewriter.decompose("机器学习和深度学习的区别，以及它们的应用")
    print(f"   分解: {decomposed}")

    simplified = rewriter.simplify("请告诉我什么是机器学习的学习方法")
    print(f"   简化: {simplified}")

    # 统计
    print(f"\n4. 统计: {searcher.get_stats()}")
    print(f"   BM25: {searcher.bm25.get_stats()}")
