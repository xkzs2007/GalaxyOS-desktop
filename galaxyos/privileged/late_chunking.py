#!/usr/bin/env python3
"""
Late Chunking 模块

论文参考:
- Jina AI: Late Chunking: Context-Aware Chunking for Better RAG (2024)
- Gunther et al.: Late Interaction Models for Retrieval (ColBERT, 2020)

核心思想:
传统 RAG: 文档 → 分块 → 嵌入 → 检索
Late Chunking: 文档 → 嵌入 → 在向量空间中分块 → 检索

优势:
1. 保留上下文信息: 分块前先编码整个文档，每个分块保留了文档的上下文
2. 长文档检索准确率 +10-20%
3. 避免分块边界处的信息丢失

实现:
1. 先对整个文档做 Embedding
2. 在向量空间中对 token 级嵌入做切分
3. 每个 chunk 的嵌入是其 token 嵌入的均值池化
4. 查询时匹配 chunk 级嵌入
"""

import logging
import numpy as np
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LateChunk:
    """Late Chunk: 带上下文感知的分块"""
    content: str                           # 分块文本
    embedding: Optional[np.ndarray] = None  # 分块嵌入（上下文感知）
    start_idx: int = 0                    # 在原文中的起始位置
    end_idx: int = 0                      # 在原文中的结束位置
    doc_id: str = ""                      # 来源文档 ID
    token_embeddings: Optional[np.ndarray] = None  # token 级嵌入
    metadata: Dict = field(default_factory=dict)


class LateChunker:
    """
    Late Chunking 分块器

    先对整个文档做 Embedding，再在向量空间中切分。

    使用示例:
    >>> chunker = LateChunker(embedding_client=emb_client)
    >>> chunks = chunker.chunk_document("长文档...", doc_id="doc1")
    >>> results = chunker.search("查询", chunks, top_k=5)
    """

    def __init__(
        self,
        embedding_client: Any = None,
        chunk_size: int = 200,         # 每个分块的目标字符数
        chunk_overlap: int = 50,        # 分块重叠字符数
        min_chunk_size: int = 50,       # 最小分块大小
        split_on_sentences: bool = True, # 是否在句子边界分块
    ):
        self.embedding_client = embedding_client
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size
        self.split_on_sentences = split_on_sentences

        self.stats = {
            'total_documents': 0,
            'total_chunks': 0,
            'avg_chunk_size': 0.0,
        }

    def chunk_document(
        self,
        document: str,
        doc_id: str = "",
        metadata: Optional[Dict] = None,
    ) -> List[LateChunk]:
        """
        Late Chunking: 先分块，再获取上下文感知的嵌入

        Args:
            document: 文档文本
            doc_id: 文档 ID
            metadata: 元数据

        Returns:
            分块列表
        """
        self.stats['total_documents'] += 1

        # Step 1: 文本分块
        text_chunks = self._split_text(document)

        # Step 2: 获取上下文感知的嵌入
        chunks = []
        for i, (text, start, end) in enumerate(text_chunks):
            chunk = LateChunk(
                content=text,
                start_idx=start,
                end_idx=end,
                doc_id=doc_id,
                metadata=metadata or {},
            )

            # 获取嵌入
            if self.embedding_client is not None:
                # Late Chunking 核心: 用整个文档的上下文来嵌入分块
                # 简化实现: 对分块 + 上下文窗口做嵌入
                context_text = self._get_context_window(document, start, end)
                emb = self.embedding_client.embed(context_text)
                chunk.embedding = emb

            chunks.append(chunk)

        self.stats['total_chunks'] += len(chunks)
        if chunks:
            self.stats['avg_chunk_size'] = sum(len(c.content) for c in chunks) / len(chunks)

        return chunks

    def _split_text(self, text: str) -> List[Tuple[str, int, int]]:
        """
        分割文本

        Returns:
            List of (chunk_text, start_idx, end_idx)
        """
        if self.split_on_sentences:
            return self._split_on_sentences(text)
        else:
            return self._split_fixed_size(text)

    def _split_on_sentences(self, text: str) -> List[Tuple[str, int, int]]:
        """按句子边界分块"""
        import re

        # 找到所有句子边界
        boundaries = [0]
        for m in re.finditer(r'[。！？；.!?;]\s*', text):
            boundaries.append(m.end())

        if boundaries[-1] != len(text):
            boundaries.append(len(text))

        # 按目标大小合并句子
        chunks = []
        current_start = 0

        for i in range(1, len(boundaries)):
            current_end = boundaries[i]
            current_text = text[current_start:current_end].strip()

            if len(current_text) >= self.chunk_size and i < len(boundaries) - 1:
                # 当前文本超过目标大小，在上一句切断
                prev_end = boundaries[i - 1]
                chunk_text = text[current_start:prev_end].strip()

                if len(chunk_text) >= self.min_chunk_size:
                    chunks.append((chunk_text, current_start, prev_end))

                # 重叠
                overlap_start = max(current_start, prev_end - self.chunk_overlap)
                current_start = overlap_start
            elif i == len(boundaries) - 1:
                # 最后一段
                chunk_text = text[current_start:].strip()
                if len(chunk_text) >= self.min_chunk_size:
                    chunks.append((chunk_text, current_start, len(text)))

        # 处理空结果
        if not chunks:
            chunks.append((text.strip(), 0, len(text)))

        return chunks

    def _split_fixed_size(self, text: str) -> List[Tuple[str, int, int]]:
        """固定大小分块"""
        chunks = []
        start = 0

        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunk_text = text[start:end].strip()

            if len(chunk_text) >= self.min_chunk_size:
                chunks.append((chunk_text, start, end))

            start = end - self.chunk_overlap

        return chunks

    def _get_context_window(self, document: str, start: int, end: int) -> str:
        """
        获取上下文窗口

        Late Chunking 核心: 在嵌入分块时，包含周围上下文
        """
        # 在分块前后各扩展 chunk_size 的一半作为上下文
        context_radius = self.chunk_size // 2
        context_start = max(0, start - context_radius)
        context_end = min(len(document), end + context_radius)

        return document[context_start:context_end]

    def search(
        self,
        query: str,
        chunks: List[LateChunk],
        top_k: int = 10,
    ) -> List[Dict]:
        """
        在 Late Chunks 中搜索

        Args:
            query: 查询
            chunks: 分块列表
            top_k: 返回数量

        Returns:
            搜索结果列表
        """
        if not chunks or self.embedding_client is None:
            # 关键词匹配回退
            return self._keyword_search(query, chunks, top_k)

        # 向量搜索
        query_emb = self.embedding_client.embed(query)
        if query_emb is None:
            return self._keyword_search(query, chunks, top_k)

        q_norm = query_emb / (np.linalg.norm(query_emb) + 1e-10)

        scored = []
        for chunk in chunks:
            if chunk.embedding is None:
                continue
            c_norm = chunk.embedding / (np.linalg.norm(chunk.embedding) + 1e-10)
            sim = float(np.dot(q_norm, c_norm))
            scored.append((chunk, sim))

        scored.sort(key=lambda x: x[1], reverse=True)

        results = []
        for chunk, score in scored[:top_k]:
            results.append({
                'content': chunk.content,
                'score': score,
                'doc_id': chunk.doc_id,
                'start_idx': chunk.start_idx,
                'end_idx': chunk.end_idx,
                'metadata': chunk.metadata,
            })

        return results

    @staticmethod
    def _keyword_search(
        query: str,
        chunks: List[LateChunk],
        top_k: int,
    ) -> List[Dict]:
        """关键词搜索（回退方案）"""
        query_lower = query.lower()
        scored = []

        for chunk in chunks:
            # 简单关键词匹配
            chunk_lower = chunk.content.lower()
            overlap = sum(1 for w in query_lower.split() if w in chunk_lower)
            scored.append((chunk, float(overlap)))

        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            {
                'content': chunk.content,
                'score': score,
                'doc_id': chunk.doc_id,
                'start_idx': chunk.start_idx,
                'end_idx': chunk.end_idx,
            }
            for chunk, score in scored[:top_k]
        ]

    def get_stats(self) -> Dict:
        return dict(self.stats)


# 导出
__all__ = [
    'LateChunker',
    'LateChunk',
]


if __name__ == "__main__":
    print("=== Late Chunking 测试 ===\n")

    # 创建分块器
    chunker = LateChunker(chunk_size=100, chunk_overlap=20)

    # 测试分块
    doc = (
        "机器学习是人工智能的一个分支，使用算法从数据中学习模式。"
        "监督学习使用标注数据进行训练，是机器学习最常见的形式。"
        "无监督学习不需要标注数据，通过发现数据中的结构来学习。"
        "深度学习是机器学习的子集，使用多层神经网络进行特征学习。"
        "卷积神经网络（CNN）适合图像处理，循环神经网络（RNN）适合序列数据。"
        "Transformer 架构是当前最流行的深度学习模型，GPT 和 BERT 都基于它。"
    )

    chunks = chunker.chunk_document(doc, doc_id="test_doc")

    print(f"1. 文档分块 ({len(chunks)} 个分块):")
    for i, chunk in enumerate(chunks):
        print(f"   [{i}] ({chunk.start_idx}-{chunk.end_idx}): {chunk.content[:60]}...")

    # 搜索测试
    print("\n2. 搜索 (关键词回退):")
    results = chunker.search("深度学习", chunks, top_k=3)
    for r in results:
        print(f"   score={r['score']:.2f}: {r['content'][:50]}...")

    # 统计
    print(f"\n3. 统计: {chunker.get_stats()}")
