#!/usr/bin/env python3
"""
Proposition 检索粒度优化模块

论文参考:
- Dense X Retrieval: Learning Retrieval-Oriented Verbal Representations for Zero-Shot Dense Retrieval (2024)
  核心发现: 将文档拆分为原子命题 (proposition) 级别检索，比段落级检索准确率 +15-25%

核心思想:
- 传统 RAG: 文档 → 段落 (chunk) → 检索
- Proposition RAG: 文档 → 命题 (proposition) → 检索

命题 = 不可再分的最小知识单元
- "机器学习是AI的分支" → 一个命题
- "深度学习使用神经网络" → 一个命题
- "机器学习是AI的分支，深度学习使用神经网络" → 两个命题

优势:
1. 更精准的语义匹配: 命题级粒度避免了段落中无关内容的干扰
2. 更高的召回率: 细粒度索引覆盖更多知识
3. 更好的重排: 命题级打分更准确
"""

import logging
import re
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Proposition:
    """命题: 不可再分的最小知识单元"""
    content: str                      # 命题内容
    source_doc_id: str                # 来源文档 ID
    source_doc_content: str           # 来源文档内容（用于上下文）
    index_in_doc: int = 0            # 在文档中的序号
    metadata: Dict = field(default_factory=dict)


class PropositionExtractor:
    """
    命题提取器

    将文档拆分为原子命题级别。

    支持两种模式:
    1. 规则模式: 基于标点和语法规则拆分（无需 API）
    2. LLM 模式: 用 LLM 精确提取命题（需要 API）
    """

    # 命题分隔符模式
    _SPLIT_PATTERNS = [
        r'[。！？；]',       # 中文句末
        r'[.!?;]\s',        # 英文句末
        r'[，、]\s*',       # 中文逗号（较短的停顿）
        r'[:：]\s*',        # 冒号
    ]

    # 命题过滤: 过短或无意义的文本
    _MIN_PROPOSITION_LENGTH = 5  # 最短命题字符数

    def __init__(self, llm_client: Any = None, min_length: int = 5):
        self.llm_client = llm_client
        self.min_length = min_length
        self.stats = {
            'total_extractions': 0,
            'rule_based': 0,
            'llm_based': 0,
            'total_propositions': 0,
        }

    def extract(
        self,
        document: str,
        doc_id: str = "",
        method: str = "auto",  # "rule" / "llm" / "auto"
    ) -> List[Proposition]:
        """
        提取文档中的命题

        Args:
            document: 文档文本
            doc_id: 文档 ID
            method: 提取方法 ("rule" / "llm" / "auto")

        Returns:
            命题列表
        """
        self.stats['total_extractions'] += 1

        if method == "auto":
            method = "llm" if self.llm_client else "rule"

        if method == "llm" and self.llm_client:
            propositions = self._extract_with_llm(document, doc_id)
            self.stats['llm_based'] += 1
        else:
            propositions = self._extract_with_rules(document, doc_id)
            self.stats['rule_based'] += 1

        self.stats['total_propositions'] += len(propositions)
        return propositions

    def _extract_with_rules(self, document: str, doc_id: str) -> List[Proposition]:
        """基于规则的命题提取"""
        # Step 1: 按句子分割
        sentences = self._split_sentences(document)

        # Step 2: 对每个句子进一步拆分为命题
        propositions = []
        for idx, sentence in enumerate(sentences):
            # 尝试按逗号进一步拆分
            sub_props = self._split_to_propositions(sentence)

            for prop_text in sub_props:
                prop_text = prop_text.strip()
                if len(prop_text) >= self.min_length:
                    propositions.append(Proposition(
                        content=prop_text,
                        source_doc_id=doc_id,
                        source_doc_content=document,
                        index_in_doc=idx,
                    ))

        return propositions

    def _extract_with_llm(self, document: str, doc_id: str) -> List[Proposition]:
        """使用 LLM 提取命题"""
        try:
            prompt = [
                {"role": "system", "content": (
                    "你是一个知识提取专家。将以下文本拆分为原子命题（proposition）。\n"
                    "每个命题应该是一个不可再分的最小知识单元，包含一个完整的事实或陈述。\n"
                    "每行输出一个命题，不要编号，不要解释。"
                )},
                {"role": "user", "content": document}
            ]
            result = self.llm_client.chat(prompt, max_tokens=1000, temperature=0.1)

            if result is None:
                return self._extract_with_rules(document, doc_id)

            # 解析 LLM 输出
            lines = [line.strip() for line in result.strip().split('\n') if line.strip()]
            propositions = []
            for idx, line in enumerate(lines):
                # 过滤编号
                line = re.sub(r'^\d+[\.\)、]\s*', '', line)
                line = re.sub(r'^[-*]\s*', '', line)
                if len(line) >= self.min_length:
                    propositions.append(Proposition(
                        content=line,
                        source_doc_id=doc_id,
                        source_doc_content=document,
                        index_in_doc=idx,
                    ))

            return propositions if propositions else self._extract_with_rules(document, doc_id)

        except Exception as e:
            logger.error(f"LLM 命题提取失败，回退到规则: {e}")
            return self._extract_with_rules(document, doc_id)

    def _split_sentences(self, text: str) -> List[str]:
        """按句子分割"""
        # 统一分隔符
        text = text.replace('\n', '。')

        # 按句末标点分割
        parts = re.split(r'([。！？；.!?;])', text)

        sentences = []
        current = ""
        for part in parts:
            current += part
            if re.match(r'[。！？；.!?;]', part):
                if current.strip():
                    sentences.append(current.strip())
                current = ""

        if current.strip():
            sentences.append(current.strip())

        return sentences

    def _split_to_propositions(self, sentence: str) -> List[str]:
        """将句子拆分为命题"""
        # 按逗号、顿号分割
        parts = re.split(r'[，、,]\s*', sentence)

        if len(parts) <= 1:
            return [sentence]

        # 检查每个部分是否是完整的命题
        propositions = []
        current_prop = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # 如果部分有主语结构，它可能是一个独立命题
            if self._has_subject_predicate(part):
                if current_prop:
                    propositions.append(current_prop)
                current_prop = part
            else:
                # 补充到当前命题
                if current_prop:
                    current_prop += "，" + part
                else:
                    current_prop = part

        if current_prop:
            propositions.append(current_prop)

        return propositions if propositions else [sentence]

    @staticmethod
    def _has_subject_predicate(text: str) -> bool:
        """判断文本是否有主谓结构（简化版）"""
        # 中文常见主语标记
        subject_markers = ['是', '有', '在', '用', '可以', '能', '会', '为', '属于', '包含']
        return any(f" {m}" in text or text.startswith(m) for m in subject_markers)

    def get_stats(self) -> Dict:
        return dict(self.stats)


class PropositionRetriever:
    """
    命题级检索器

    将文档拆分为命题，然后在命题级别进行检索。

    使用示例:
    >>> retriever = PropositionRetriever(embedding_client=emb_client)
    >>> retriever.add_documents(["文档1", "文档2"])
    >>> results = retriever.search("查询", top_k=5)
    """

    def __init__(
        self,
        embedding_client: Any = None,
        llm_client: Any = None,
        min_proposition_length: int = 5,
        max_propositions: int = 100000,  # 命题索引上限，防止内存泄漏
    ):
        self.extractor = PropositionExtractor(
            llm_client=llm_client,
            min_length=min_proposition_length,
        )
        self.embedding_client = embedding_client
        self.max_propositions = max_propositions

        # 命题索引
        self._propositions: List[Proposition] = []
        self._prop_embeddings: List[Any] = []  # 命题嵌入

        # 统计
        self.stats = {
            'total_docs': 0,
            'total_propositions': 0,
            'total_searches': 0,
        }

    def add_documents(
        self,
        documents: List[str],
        doc_ids: Optional[List[str]] = None,
    ):
        """
        添加文档（自动提取命题）

        Args:
            documents: 文档文本列表
            doc_ids: 文档 ID 列表
        """
        doc_ids = doc_ids or [str(i) for i in range(len(documents))]

        for doc, doc_id in zip(documents, doc_ids):
            # 提取命题
            propositions = self.extractor.extract(doc, doc_id=doc_id)
            
            # 内存保护: 如果超过上限，丢弃最旧的命题
            if len(self._propositions) + len(propositions) > self.max_propositions:
                excess = len(self._propositions) + len(propositions) - self.max_propositions
                self._propositions = self._propositions[excess:]
                self._prop_embeddings = self._prop_embeddings[excess:]
                logger.warning(f"命题索引超过上限 {self.max_propositions}，已淘汰 {excess} 条旧命题")
            
            self._propositions.extend(propositions)
            self.stats['total_docs'] += 1
            self.stats['total_propositions'] += len(propositions)

            # 获取命题嵌入
            if self.embedding_client is not None:
                for prop in propositions:
                    emb = self.embedding_client.embed(prop.content)
                    self._prop_embeddings.append(emb)

    def search(
        self,
        query: str,
        top_k: int = 10,
        return_context: bool = True,
    ) -> List[Dict]:
        """
        命题级检索

        Args:
            query: 查询文本
            top_k: 返回数量
            return_context: 是否返回来源文档上下文

        Returns:
            检索结果列表
        """
        self.stats['total_searches'] += 1

        if not self._propositions:
            return []

        results = []

        if self.embedding_client is not None and self._prop_embeddings:
            # 向量检索
            import numpy as np
            query_emb = self.embedding_client.embed(query)
            if query_emb is not None:
                q_norm = query_emb / (np.linalg.norm(query_emb) + 1e-10)
                scores = []
                for i, prop_emb in enumerate(self._prop_embeddings):
                    if prop_emb is None:
                        scores.append(0.0)
                        continue
                    p_norm = prop_emb / (np.linalg.norm(prop_emb) + 1e-10)
                    sim = float(np.dot(q_norm, p_norm))
                    scores.append(sim)

                # 排序
                indexed = list(enumerate(scores))
                indexed.sort(key=lambda x: x[1], reverse=True)

                for idx, score in indexed[:top_k]:
                    prop = self._propositions[idx]
                    result = {
                        'content': prop.source_doc_content if return_context else prop.content,
                        'proposition': prop.content,
                        'score': score,
                        'doc_id': prop.source_doc_id,
                        'index_in_doc': prop.index_in_doc,
                    }
                    results.append(result)
        else:
            # 关键词匹配（回退）
            query_lower = query.lower()
            scored = []
            for i, prop in enumerate(self._propositions):
                # 简单关键词重叠度
                prop_lower = prop.content.lower()
                overlap = sum(1 for w in query_lower.split() if w in prop_lower)
                scored.append((i, overlap))

            scored.sort(key=lambda x: x[1], reverse=True)

            for idx, score in scored[:top_k]:
                prop = self._propositions[idx]
                result = {
                    'content': prop.source_doc_content if return_context else prop.content,
                    'proposition': prop.content,
                    'score': float(score),
                    'doc_id': prop.source_doc_id,
                    'index_in_doc': prop.index_in_doc,
                }
                results.append(result)

        return results

    def get_stats(self) -> Dict:
        return dict(self.stats)


# 导出
__all__ = [
    'PropositionRetriever',
    'PropositionExtractor',
    'Proposition',
]


if __name__ == "__main__":
    print("=== Proposition 检索测试 ===\n")

    # 创建检索器
    retriever = PropositionRetriever()

    # 添加文档
    docs = [
        "机器学习是人工智能的一个分支。它使用算法从数据中学习模式。监督学习和无监督学习是两种主要类型。",
        "深度学习是机器学习的子集，使用多层神经网络。卷积神经网络适合图像处理，循环神经网络适合序列数据。",
    ]
    retriever.add_documents(docs)

    # 搜索
    print("1. 命题级搜索:")
    results = retriever.search("什么是机器学习", top_k=5)
    for r in results:
        print(f"   命题: {r['proposition'][:50]}...")
        print(f"   分数: {r['score']:.4f}")

    # 命题提取测试
    print("\n2. 命题提取:")
    extractor = PropositionExtractor()
    doc = "机器学习是AI的分支，使用算法学习。深度学习使用神经网络，是机器学习的子集。"
    propositions = extractor.extract(doc, doc_id="test")
    for p in propositions:
        print(f"   [{p.index_in_doc}] {p.content}")

    # 统计
    print(f"\n3. 统计: {retriever.get_stats()}")
    print(f"   提取器: {extractor.get_stats()}")
