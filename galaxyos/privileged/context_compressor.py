#!/usr/bin/env python3
"""
上下文压缩模块 (Context Compression / LLMLingua)

论文参考:
- LLMLingua: Compressing Prompts for Accelerated Inference of Large Language Models (2024)
  核心: 用小模型计算 token 级困惑度，删除低信息量 token
- LongLLMLingua: Enhancing and Accelerating LLM-based Query-Focused Compression (2024)
  核心: 查询感知的上下文压缩，保留与查询相关的 token

效果:
- 上下文长度减少 50-70%
- TTFT (Time To First Token) 降低 30-50%
- 准确率损失 < 3%

实现:
1. 规则压缩: 基于关键词/句子的重要性评分，保留高信息量内容
2. LLM 压缩: 用 LLM 进行摘要式压缩
3. 查询感知压缩: 保留与查询相关的内容，删除无关内容
"""

import logging
import re
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass
class CompressionResult:
    """压缩结果"""
    original_text: str
    compressed_text: str
    compression_ratio: float       # 压缩比 (0-1, 越小压缩越多)
    original_length: int          # 原始字符数
    compressed_length: int        # 压缩后字符数
    method: str                   # 压缩方法
    preserved_keywords: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


class RuleBasedCompressor:
    """
    基于规则的上下文压缩器

    策略:
    1. 去除冗余重复内容
    2. 压缩模板化文本
    3. 保留与查询关键词相关的句子
    """

    # 模板化文本模式（可安全压缩）
    _TEMPLATE_PATTERNS = [
        (r'请基于以下参考信息回答问题[。：:]?', ''),
        (r'参考信息[：:]\s*', ''),
        (r'以下是相关文档[：:]\s*', ''),
        (r'根据上述信息[，,]?\s*', ''),
        (r'综上所述[，,]?\s*', ''),
        (r'如上所述[，,]?\s*', ''),
        (r'请注意[，,]?\s*', ''),
    ]

    # 停用词（中文高频低信息量词）
    _STOP_WORDS = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
        "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
        "你", "会", "着", "没有", "看", "好", "自己", "这", "那",
    }

    def compress(
        self,
        text: str,
        query: Optional[str] = None,
        target_ratio: float = 0.5,
    ) -> CompressionResult:
        """
        基于规则的压缩

        Args:
            text: 原始文本
            query: 查询（用于查询感知压缩）
            target_ratio: 目标压缩比

        Returns:
            CompressionResult
        """
        original_length = len(text)

        # Step 1: 去除模板化文本
        compressed = text
        for pattern, replacement in self._TEMPLATE_PATTERNS:
            compressed = re.sub(pattern, replacement, compressed)

        # Step 2: 去除连续空白
        compressed = re.sub(r'\n{3,}', '\n\n', compressed)
        compressed = re.sub(r' {2,}', ' ', compressed)

        # Step 3: 查询感知句子选择
        if query and len(compressed) > original_length * target_ratio:
            compressed = self._select_relevant_sentences(
                compressed, query, target_ratio
            )

        # Step 4: 去除重复句子
        compressed = self._deduplicate_sentences(compressed)

        compressed_length = len(compressed)
        compression_ratio = compressed_length / original_length if original_length > 0 else 1.0

        preserved = []
        if query:
            query_words = set(query.lower().split())
            for w in query_words:
                if w in compressed.lower():
                    preserved.append(w)

        return CompressionResult(
            original_text=text,
            compressed_text=compressed,
            compression_ratio=round(compression_ratio, 3),
            original_length=original_length,
            compressed_length=compressed_length,
            method="rule",
            preserved_keywords=preserved,
        )

    def _select_relevant_sentences(
        self,
        text: str,
        query: str,
        target_ratio: float,
    ) -> str:
        """选择与查询相关的句子"""
        # 分句
        sentences = re.split(r'[。！？；.!?;]', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return text

        # 计算每个句子与查询的相关性
        query_words = set(query.lower().split())
        # 中文: 也按字符匹配
        query_chars = set(query) - {' ', '的', '了', '是', '在', '和', '不', '有'}

        scored_sentences = []
        for sent in sentences:
            score = 0.0
            # 关键词重叠
            for word in query_words:
                if word.lower() in sent.lower():
                    score += 2.0
            # 字符重叠（中文）
            for ch in query_chars:
                if ch in sent:
                    score += 1.0
            # 长度奖励（保留完整句子）
            score += min(len(sent) / 100, 1.0)
            scored_sentences.append((sent, score))

        # 按分数排序
        scored_sentences.sort(key=lambda x: x[1], reverse=True)

        # 选择 top 句子直到达到目标长度
        target_length = int(len(text) * target_ratio)
        selected = []
        current_length = 0

        for sent, score in scored_sentences:
            if current_length + len(sent) <= target_length:
                selected.append(sent)
                current_length += len(sent)
            elif current_length == 0:
                # 至少保留第一个
                selected.append(sent)
                break

        return '。'.join(selected) + ('。' if selected else '')

    @staticmethod
    def _deduplicate_sentences(text: str) -> str:
        """去除重复句子"""
        sentences = re.split(r'([。！？；.!?;])', text)
        seen = set()
        result = []

        for i in range(0, len(sentences) - 1, 2):
            sent = sentences[i].strip()
            punct = sentences[i + 1] if i + 1 < len(sentences) else ''

            if sent and sent not in seen:
                seen.add(sent)
                result.append(sent + punct)

        # 最后一段（可能没有标点）
        if len(sentences) % 2 == 1:
            last = sentences[-1].strip()
            if last and last not in seen:
                result.append(last)

        return ''.join(result)


class LLMCompressor:
    """
    LLM 压缩器

    用 LLM 进行摘要式压缩。
    """

    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client
        self.stats = {
            'compressions': 0,
            'total_input_length': 0,
            'total_output_length': 0,
        }

    def compress(
        self,
        text: str,
        query: Optional[str] = None,
        target_ratio: float = 0.5,
    ) -> CompressionResult:
        """
        LLM 压缩

        Args:
            text: 原始文本
            query: 查询
            target_ratio: 目标压缩比

        Returns:
            CompressionResult
        """
        original_length = len(text)

        if self.llm_client is None:
            # 回退到规则压缩
            return RuleBasedCompressor().compress(text, query, target_ratio)

        try:
            target_len = int(original_length * target_ratio)

            if query:
                prompt = [
                    {"role": "system", "content": "你是一个文本压缩专家。"},
                    {"role": "user", "content": (
                        f"查询: {query}\n\n"
                        f"原始文本 ({original_length} 字符):\n{text}\n\n"
                        f"请压缩上述文本到约 {target_len} 字符，"
                        "保留与查询相关的所有关键信息，删除冗余内容。\n"
                        "只输出压缩后的文本，不要解释。"
                    )}
                ]
            else:
                prompt = [
                    {"role": "system", "content": "你是一个文本压缩专家。"},
                    {"role": "user", "content": (
                        f"原始文本 ({original_length} 字符):\n{text}\n\n"
                        f"请压缩上述文本到约 {target_len} 字符，"
                        "保留所有关键信息，删除冗余内容。\n"
                        "只输出压缩后的文本，不要解释。"
                    )}
                ]

            result = self.llm_client.chat(prompt, max_tokens=target_len * 2, temperature=0.3)

            compressed = result.strip() if result else text
            self.stats['compressions'] += 1
            self.stats['total_input_length'] += original_length
            self.stats['total_output_length'] += len(compressed)

            return CompressionResult(
                original_text=text,
                compressed_text=compressed,
                compression_ratio=round(len(compressed) / original_length, 3) if original_length > 0 else 1.0,
                original_length=original_length,
                compressed_length=len(compressed),
                method="llm",
            )

        except Exception as e:
            logger.error(f"LLM 压缩失败，回退到规则: {e}")
            return RuleBasedCompressor().compress(text, query, target_ratio)


class ContextCompressor:
    """
    上下文压缩器 (统一接口)

    整合规则压缩和 LLM 压缩。
    支持查询感知压缩。

    使用示例:
    >>> compressor = ContextCompressor()
    >>> result = compressor.compress("长文本...", query="查询", target_ratio=0.5)
    >>> print(result.compressed_text)
    >>> print(f"压缩比: {result.compression_ratio:.2%}")
    """

    def __init__(
        self,
        llm_client: Any = None,
        default_method: str = "auto",  # "rule" / "llm" / "auto"
        default_target_ratio: float = 0.5,
    ):
        self.rule_compressor = RuleBasedCompressor()
        self.llm_compressor = LLMCompressor(llm_client)
        self.default_method = default_method
        self.default_target_ratio = default_target_ratio

        self.stats = {
            'total_compressions': 0,
            'by_method': {'rule': 0, 'llm': 0},
            'total_chars_saved': 0,
            'avg_compression_ratio': 0.0,
            '_sum_ratios': 0.0,  # 累计压缩比之和（用于计算正确的平均值）
        }

    def compress(
        self,
        text: str,
        query: Optional[str] = None,
        target_ratio: Optional[float] = None,
        method: Optional[str] = None,
    ) -> CompressionResult:
        """
        压缩上下文

        Args:
            text: 原始文本
            query: 查询（用于查询感知压缩）
            target_ratio: 目标压缩比 (0-1)
            method: 压缩方法 ("rule" / "llm" / "auto")

        Returns:
            CompressionResult
        """
        target_ratio = target_ratio or self.default_target_ratio
        method = method or self.default_method

        if method == "auto":
            # 自动选择: 短文本用规则，长文本用 LLM
            method = "llm" if len(text) > 500 and self.llm_compressor.llm_client else "rule"

        if method == "llm":
            result = self.llm_compressor.compress(text, query, target_ratio)
        else:
            result = self.rule_compressor.compress(text, query, target_ratio)

        # 更新统计
        self.stats['total_compressions'] += 1
        self.stats['by_method'][result.method] = self.stats['by_method'].get(result.method, 0) + 1
        self.stats['total_chars_saved'] += result.original_length - result.compressed_length
        self.stats['_sum_ratios'] += result.compression_ratio
        self.stats['avg_compression_ratio'] = (
            self.stats['_sum_ratios'] / self.stats['total_compressions']
        )

        return result

    def compress_documents(
        self,
        documents: List[str],
        query: Optional[str] = None,
        target_ratio: Optional[float] = None,
    ) -> List[str]:
        """
        批量压缩文档

        Args:
            documents: 文档列表
            query: 查询
            target_ratio: 目标压缩比

        Returns:
            压缩后的文档列表
        """
        return [
            self.compress(doc, query, target_ratio).compressed_text
            for doc in documents
        ]

    def get_stats(self) -> Dict:
        stats = dict(self.stats)
        stats.pop('_sum_ratios', None)  # 内部字段，不暴露
        return stats


# 导出
__all__ = [
    'ContextCompressor',
    'RuleBasedCompressor',
    'LLMCompressor',
    'CompressionResult',
]


if __name__ == "__main__":
    print("=== 上下文压缩测试 ===\n")

    compressor = ContextCompressor()

    # 测试规则压缩
    long_text = (
        "机器学习是人工智能的一个分支，它使用算法从数据中学习模式。"
        "监督学习是机器学习的一种类型，使用标注数据进行训练。"
        "无监督学习是另一种类型，不需要标注数据。"
        "深度学习是机器学习的子集，使用多层神经网络进行特征学习。"
        "卷积神经网络适合图像处理任务。"
        "循环神经网络适合序列数据处理。"
        "请基于以上参考信息回答问题。"
        "综上所述，机器学习是AI的重要领域。"
    )

    print("1. 规则压缩:")
    result = compressor.compress(long_text, query="什么是机器学习", target_ratio=0.5, method="rule")
    print(f"   原始长度: {result.original_length}")
    print(f"   压缩后长度: {result.compressed_length}")
    print(f"   压缩比: {result.compression_ratio:.2%}")
    print(f"   压缩后: {result.compressed_text[:100]}...")
    print(f"   保留关键词: {result.preserved_keywords}")

    # 测试无查询压缩
    print("\n2. 无查询压缩:")
    result = compressor.compress(long_text, target_ratio=0.6, method="rule")
    print(f"   压缩比: {result.compression_ratio:.2%}")
    print(f"   压缩后: {result.compressed_text[:100]}...")

    # 统计
    print(f"\n3. 统计: {compressor.get_stats()}")
