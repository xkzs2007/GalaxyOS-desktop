#!/usr/bin/env python3
"""
自然语言处理模块 (NLP Processor)
================================

独立的 NLP 处理管道，提供：
- 中文分词 + 词性标注
- 命名实体识别 (NER)
- 关键词提取
- 情感分析
- 文本摘要
- 语义相似度计算

整合到小艺 Claw 系统，为记忆、检索、防幻觉等模块提供底层 NLP 能力。

作者: 小艺 Claw
版本: v1.0.0
日期: 2026-04-23
"""

import re
import json
import hashlib
import logging
from typing import Dict, List, Tuple, Optional, Any, Union, Set, Callable
from dataclasses import dataclass, field
from collections import Counter, defaultdict
from functools import lru_cache
import threading

# 尝试导入可选依赖
try:
    import jieba
    import jieba.posseg as pseg
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False

try:
    from snownlp import SnowNLP
    SNOWNLP_AVAILABLE = True
except ImportError:
    SNOWNLP_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class Token:
    """分词结果"""
    text: str
    pos: str  # 词性标签
    start: int  # 起始位置
    end: int  # 结束位置
    is_entity: bool = False
    entity_type: Optional[str] = None


@dataclass
class Entity:
    """命名实体"""
    text: str
    type: str  # PER/LOC/ORG/TIME/NUM/MISC
    start: int
    end: int
    confidence: float = 1.0


@dataclass
class SentimentResult:
    """情感分析结果"""
    score: float  # 0-1, 0.5 为中性
    label: str  # positive/negative/neutral
    confidence: float
    keywords: List[str] = field(default_factory=list)


@dataclass
class SummaryResult:
    """摘要结果"""
    summary: str
    key_sentences: List[str]
    keywords: List[str]
    compression_ratio: float  # 压缩比


@dataclass
class NLPResult:
    """NLP 处理结果"""
    text: str
    tokens: List[Token]
    entities: List[Entity]
    keywords: List[Tuple[str, float]]  # (keyword, weight)
    sentiment: Optional[SentimentResult] = None
    summary: Optional[SummaryResult] = None
    language: str = "zh"
    processing_time_ms: float = 0.0


class ChineseTokenizer:
    """中文分词器"""

    # 词性标签映射（jieba -> 通用标签）
    POS_MAP = {
        'a': 'ADJ',      # 形容词
        'ad': 'ADJ',     # 形容词
        'an': 'ADJ',     # 名形词
        'c': 'CONJ',     # 连词
        'd': 'ADV',      # 副词
        'e': 'INTJ',     # 叹词
        'f': 'LOC',      # 方位词
        'i': 'IDIOM',    # 成语
        'j': 'NOUN',     # 简称
        'l': 'IDIOM',    # 习用语
        'm': 'NUM',      # 数词
        'n': 'NOUN',     # 名词
        'nr': 'PER',     # 人名
        'ns': 'LOC',     # 地名
        'nt': 'ORG',     # 机构名
        'nz': 'NOUN',    # 其他专名
        'o': 'ONO',      # 拟声词
        'p': 'PREP',     # 介词
        'q': 'QUANT',    # 量词
        'r': 'PRON',     # 代词
        's': 'LOC',      # 处所词
        't': 'TIME',     # 时间词
        'u': 'PART',     # 助词
        'v': 'VERB',     # 动词
        'vd': 'VERB',    # 动副词
        'vn': 'VERB',    # 名动词
        'w': 'PUNCT',    # 标点
        'x': 'X',        # 非语素字
        'y': 'MOD',      # 语气词
        'z': 'STATE',    # 状态词
        'eng': 'X',      # 英文
    }

    def __init__(self, user_dict: Optional[str] = None):
        """
        初始化分词器
        
        Args:
            user_dict: 用户自定义词典路径
        """
        self.initialized = False
        self.user_dict = user_dict

        if JIEBA_AVAILABLE:
            if user_dict:
                jieba.load_userdict(user_dict)
            self.initialized = True
            logger.info("jieba 分词器初始化成功")
        else:
            logger.warning("jieba 未安装，使用简单分词器")

    def tokenize(self, text: str, with_pos: bool = True) -> List[Token]:
        """
        分词
        
        Args:
            text: 输入文本
            with_pos: 是否包含词性标注
            
        Returns:
            分词结果列表
        """
        if not text:
            return []

        if self.initialized and JIEBA_AVAILABLE:
            return self._jieba_tokenize(text, with_pos)
        else:
            return self._simple_tokenize(text)

    def _jieba_tokenize(self, text: str, with_pos: bool) -> List[Token]:
        """使用 jieba 分词"""
        tokens = []

        if with_pos:
            words = pseg.cut(text)
            offset = 0
            for word, flag in words:
                token = Token(
                    text=word,
                    pos=self.POS_MAP.get(flag, 'X'),
                    start=offset,
                    end=offset + len(word)
                )
                tokens.append(token)
                offset += len(word)
        else:
            words = jieba.cut(text)
            offset = 0
            for word in words:
                token = Token(
                    text=word,
                    pos='X',
                    start=offset,
                    end=offset + len(word)
                )
                tokens.append(token)
                offset += len(word)

        return tokens

    def _simple_tokenize(self, text: str) -> List[Token]:
        """简单分词器（字符级）"""
        tokens = []
        # 按字符分割，保留标点
        pattern = r'[\u4e00-\u9fff]|[a-zA-Z]+|[0-9]+|[^\w\s]'

        for match in re.finditer(pattern, text):
            char = match.group()
            pos = self._guess_pos(char)
            token = Token(
                text=char,
                pos=pos,
                start=match.start(),
                end=match.end()
            )
            tokens.append(token)

        return tokens

    def _guess_pos(self, char: str) -> str:
        """猜测词性"""
        if re.match(r'[\u4e00-\u9fff]', char):
            return 'CHAR'
        elif re.match(r'[a-zA-Z]', char):
            return 'LATIN'
        elif re.match(r'[0-9]', char):
            return 'NUM'
        elif re.match(r'[^\w\s]', char):
            return 'PUNCT'
        return 'X'


class NamedEntityRecognizer:
    """命名实体识别器"""

    # 实体类型
    ENTITY_TYPES = {
        'PER': ['nr', 'nrt', 'nrfg'],  # 人名
        'LOC': ['ns', 'f', 's'],        # 地名
        'ORG': ['nt', 'nz'],            # 机构名
        'TIME': ['t', 'tg'],            # 时间
        'NUM': ['m', 'mq'],             # 数字
    }

    # 正则表达式模式
    TIME_PATTERNS = [
        r'\d{4}年\d{1,2}月\d{1,2}日',
        r'\d{4}-\d{1,2}-\d{1,2}',
        r'\d{1,2}月\d{1,2}日',
        r'昨天|今天|明天|后天|大后天',
        r'上周|这周|下周',
        r'上个?月|这个?月|下个?月',
        r'\d{1,2}[点时]\d{1,2}分?',
        r'上午|下午|晚上|中午|早上|傍晚',
    ]

    NUM_PATTERNS = [
        r'\d+(?:\.\d+)?(?:万|亿|千|百)?(?:元|块|美元|欧元)?',
        r'[一二三四五六七八九十百千万亿]+(?:元|块)?',
        r'\d+(?:\.\d+)?%',
    ]

    def __init__(self):
        self.time_regex = re.compile('|'.join(self.TIME_PATTERNS))
        self.num_regex = re.compile('|'.join(self.NUM_PATTERNS))

    def recognize(self, text: str, tokens: List[Token]) -> List[Entity]:
        """
        识别命名实体
        
        Args:
            text: 输入文本
            tokens: 分词结果
            
        Returns:
            实体列表
        """
        entities = []

        # 1. 从分词结果中提取实体
        for token in tokens:
            entity_type = self._get_entity_type(token.pos)
            if entity_type:
                entity = Entity(
                    text=token.text,
                    type=entity_type,
                    start=token.start,
                    end=token.end
                )
                entities.append(entity)
                token.is_entity = True
                token.entity_type = entity_type

        # 2. 用正则表达式补充时间和数字实体
        entities.extend(self._regex_recognize(text))

        # 3. 合并重叠实体
        entities = self._merge_entities(entities)

        return entities

    def _get_entity_type(self, pos: str) -> Optional[str]:
        """根据词性判断实体类型"""
        for entity_type, pos_list in self.ENTITY_TYPES.items():
            if pos.lower() in pos_list:
                return entity_type
        return None

    def _regex_recognize(self, text: str) -> List[Entity]:
        """正则表达式识别"""
        entities = []

        # 时间实体
        for match in self.time_regex.finditer(text):
            entity = Entity(
                text=match.group(),
                type='TIME',
                start=match.start(),
                end=match.end()
            )
            entities.append(entity)

        # 数字实体
        for match in self.num_regex.finditer(text):
            entity = Entity(
                text=match.group(),
                type='NUM',
                start=match.start(),
                end=match.end()
            )
            entities.append(entity)

        return entities

    def _merge_entities(self, entities: List[Entity]) -> List[Entity]:
        """合并重叠实体"""
        if not entities:
            return []

        # 按起始位置排序
        entities.sort(key=lambda e: e.start)

        merged = [entities[0]]
        for entity in entities[1:]:
            last = merged[-1]
            # 如果重叠，保留更长的
            if entity.start < last.end:
                if len(entity.text) > len(last.text):
                    merged[-1] = entity
            else:
                merged.append(entity)

        return merged


class KeywordExtractor:
    """关键词提取器"""

    # 停用词
    STOPWORDS = {
        '的', '了', '是', '在', '我', '有', '和', '就', '不', '人', '都', '一', '一个',
        '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好',
        '自己', '这', '那', '他', '她', '它', '们', '这个', '那个', '什么', '怎么',
        '为什么', '哪', '哪里', '哪个', '如何', '可以', '能', '应该', '需要', '必须',
        '但是', '不过', '而且', '或者', '因为', '所以', '如果', '虽然', '即使',
        '然后', '接着', '于是', '因此', '否则', '然而', '可是', '尽管', '无论',
    }

    def __init__(self, stopwords: Optional[Set[str]] = None):
        """
        初始化关键词提取器
        
        Args:
            stopwords: 自定义停用词集合
        """
        self.stopwords = stopwords or self.STOPWORDS

    def extract(self, tokens: List[Token], top_k: int = 10) -> List[Tuple[str, float]]:
        """
        提取关键词
        
        Args:
            tokens: 分词结果
            top_k: 返回前 K 个关键词
            
        Returns:
            [(keyword, weight), ...]
        """
        # 过滤停用词和标点
        valid_tokens = [
            t for t in tokens
            if t.text not in self.stopwords
            and t.pos not in ('PUNCT', 'PART', 'CONJ', 'PRON', 'NUM')
            and len(t.text) > 1
        ]

        if not valid_tokens:
            return []

        # TF 计算
        word_freq = Counter(t.text for t in valid_tokens)
        total = sum(word_freq.values())

        # 加权：名词权重更高
        weighted_freq = {}
        for word, freq in word_freq.items():
            # 找到这个词的词性
            pos = next((t.pos for t in valid_tokens if t.text == word), 'X')

            # 词性权重
            pos_weight = {
                'NOUN': 2.0,
                'PER': 3.0,
                'LOC': 2.5,
                'ORG': 2.5,
                'TIME': 1.5,
                'VERB': 1.2,
                'ADJ': 1.0,
            }.get(pos, 1.0)

            weighted_freq[word] = (freq / total) * pos_weight

        # 排序返回
        sorted_keywords = sorted(
            weighted_freq.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return sorted_keywords[:top_k]

    def extract_tfidf(
        self,
        text: str,
        tokens: List[Token],
        doc_freq: Dict[str, int],
        total_docs: int,
        top_k: int = 10
    ) -> List[Tuple[str, float]]:
        """
        TF-IDF 关键词提取
        
        Args:
            text: 输入文本
            tokens: 分词结果
            doc_freq: 文档频率字典
            total_docs: 总文档数
            top_k: 返回前 K 个
            
        Returns:
            [(keyword, tfidf_score), ...]
        """
        import math

        # TF
        valid_tokens = [
            t for t in tokens
            if t.text not in self.stopwords
            and t.pos not in ('PUNCT', 'PART', 'CONJ', 'PRON')
            and len(t.text) > 1
        ]

        if not valid_tokens:
            return []

        word_freq = Counter(t.text for t in valid_tokens)
        total = sum(word_freq.values())

        # TF-IDF
        tfidf_scores = {}
        for word, freq in word_freq.items():
            tf = freq / total
            df = doc_freq.get(word, 1)
            idf = math.log(total_docs / df)
            tfidf_scores[word] = tf * idf

        sorted_keywords = sorted(
            tfidf_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return sorted_keywords[:top_k]


class SentimentAnalyzer:
    """情感分析器"""

    # 情感词典（简化版）
    POSITIVE_WORDS = {
        '好', '棒', '优秀', '出色', '精彩', '完美', '满意', '喜欢', '爱', '赞',
        '厉害', '牛', '强', '赞', '不错', '挺好', '很好', '非常好', '特别',
        '开心', '高兴', '快乐', '幸福', '舒服', '舒适', '方便', '简单',
        '成功', '胜利', '赢', '赚', '省钱', '便宜', '划算', '值得',
    }

    NEGATIVE_WORDS = {
        '差', '烂', '糟糕', '垃圾', '废物', '讨厌', '恨', '烦', '恶心',
        '失望', '后悔', '遗憾', '可惜', '倒霉', '不幸', '悲剧', '惨',
        '难', '困难', '麻烦', '复杂', '累', '辛苦', '痛苦', '难受',
        '失败', '输', '亏', '贵', '浪费', '坑', '骗', '假',
    }

    # 程度副词
    INTENSIFIERS = {
        '非常': 2.0, '特别': 2.0, '极其': 2.5, '相当': 1.5,
        '很': 1.5, '挺': 1.3, '比较': 1.2, '稍微': 0.8,
        '有点': 0.7, '略微': 0.6, '不太': 0.5,
    }

    # 否定词
    NEGATORS = {'不', '没', '无', '非', '未', '别', '莫', '勿'}

    def __init__(self):
        if SNOWNLP_AVAILABLE:
            self.use_snownlp = True
        else:
            self.use_snownlp = False
            logger.info("SnowNLP 未安装，使用词典方法")

    def analyze(self, text: str, tokens: Optional[List[Token]] = None) -> SentimentResult:
        """
        情感分析
        
        Args:
            text: 输入文本
            tokens: 分词结果（可选）
            
        Returns:
            情感分析结果
        """
        if self.use_snownlp:
            return self._snownlp_analyze(text)
        else:
            return self._dict_analyze(text, tokens)

    def _snownlp_analyze(self, text: str) -> SentimentResult:
        """使用 SnowNLP 分析"""
        s = SnowNLP(text)
        score = s.sentiments

        if score > 0.6:
            label = 'positive'
        elif score < 0.4:
            label = 'negative'
        else:
            label = 'neutral'

        return SentimentResult(
            score=score,
            label=label,
            confidence=abs(score - 0.5) * 2,
            keywords=[]
        )

    def _dict_analyze(self, text: str, tokens: Optional[List[Token]]) -> SentimentResult:
        """词典方法分析"""
        if not tokens:
            tokens = []

        # 简单分词
        words = [t.text for t in tokens] if tokens else list(text)

        positive_count = 0
        negative_count = 0
        keywords = []

        i = 0
        while i < len(words):
            word = words[i]

            # 检查否定词
            negated = False
            if word in self.NEGATORS:
                negated = True
                i += 1
                if i >= len(words):
                    break
                word = words[i]

            # 检查程度副词
            intensity = 1.0
            if word in self.INTENSIFIERS:
                intensity = self.INTENSIFIERS[word]
                i += 1
                if i >= len(words):
                    break
                word = words[i]

            # 情感词
            if word in self.POSITIVE_WORDS:
                score = intensity * (1 if not negated else -1)
                if score > 0:
                    positive_count += score
                    keywords.append(word)
                else:
                    negative_count += abs(score)
            elif word in self.NEGATIVE_WORDS:
                score = intensity * (-1 if not negated else 1)
                if score > 0:
                    positive_count += score
                else:
                    negative_count += abs(score)
                    keywords.append(word)

            i += 1

        # 计算最终分数
        total = positive_count + negative_count
        if total == 0:
            score = 0.5
            label = 'neutral'
            confidence = 0.0
        else:
            score = positive_count / total
            if score > 0.6:
                label = 'positive'
            elif score < 0.4:
                label = 'negative'
            else:
                label = 'neutral'
            confidence = abs(score - 0.5) * 2

        return SentimentResult(
            score=score,
            label=label,
            confidence=confidence,
            keywords=list(set(keywords))
        )


class TextSummarizer:
    """文本摘要器"""

    def __init__(self, min_sentences: int = 2, max_sentences: int = 5):
        """
        初始化摘要器
        
        Args:
            min_sentences: 最少句子数
            max_sentences: 最多句子数
        """
        self.min_sentences = min_sentences
        self.max_sentences = max_sentences

    def summarize(self, text: str, ratio: float = 0.3) -> SummaryResult:
        """
        生成摘要
        
        Args:
            text: 输入文本
            ratio: 压缩比例
            
        Returns:
            摘要结果
        """
        # 分句
        sentences = self._split_sentences(text)

        if len(sentences) <= self.min_sentences:
            return SummaryResult(
                summary=text,
                key_sentences=sentences,
                keywords=[],
                compression_ratio=1.0
            )

        # 计算句子重要性
        sentence_scores = self._score_sentences(sentences)

        # 选择重要句子
        num_summary_sentences = max(
            self.min_sentences,
            min(self.max_sentences, int(len(sentences) * ratio))
        )

        top_indices = sorted(
            range(len(sentence_scores)),
            key=lambda i: sentence_scores[i],
            reverse=True
        )[:num_summary_sentences]

        # 按原文顺序排列
        top_indices.sort()
        key_sentences = [sentences[i] for i in top_indices]

        summary = ''.join(key_sentences)

        return SummaryResult(
            summary=summary,
            key_sentences=key_sentences,
            keywords=[],
            compression_ratio=len(summary) / len(text) if text else 1.0
        )

    def _split_sentences(self, text: str) -> List[str]:
        """分句"""
        # 中文分句
        pattern = r'(?<=[。！？\n])'
        sentences = re.split(pattern, text)
        sentences = [s.strip() for s in sentences if s.strip()]
        return sentences

    def _score_sentences(self, sentences: List[str]) -> List[float]:
        """计算句子重要性分数"""
        if not sentences:
            return []

        # 词频
        words = []
        for sent in sentences:
            words.extend(list(sent))

        word_freq = Counter(words)
        total_words = sum(word_freq.values())

        # 句子分数 = 词频之和 / 句子长度
        scores = []
        for sent in sentences:
            sent_words = list(sent)
            score = sum(word_freq.get(w, 0) for w in sent_words)
            # 归一化
            score = score / (len(sent_words) + 1) if sent_words else 0
            scores.append(score)

        return scores


class SemanticSimilarity:
    """语义相似度计算"""

    def __init__(self, embedding_func=None):
        """
        初始化
        
        Args:
            embedding_func: 向量化函数 (text -> vector)
        """
        self.embedding_func = embedding_func
        self._cache = {}
        self._lock = threading.Lock()

    def similarity(self, text1: str, text2: str) -> float:
        """
        计算两段文本的相似度
        
        Args:
            text1: 文本1
            text2: 文本2
            
        Returns:
            相似度分数 (0-1)
        """
        if self.embedding_func:
            return self._embedding_similarity(text1, text2)
        else:
            return self._jaccard_similarity(text1, text2)

    def _embedding_similarity(self, text1: str, text2: str) -> float:
        """基于向量的相似度"""
        vec1 = self._get_embedding(text1)
        vec2 = self._get_embedding(text2)

        if vec1 is None or vec2 is None:
            return self._jaccard_similarity(text1, text2)

        if NUMPY_AVAILABLE:
            vec1 = np.array(vec1)
            vec2 = np.array(vec2)
            cos_sim = np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))
            return float(cos_sim)
        else:
            # 手动计算余弦相似度
            dot = sum(a * b for a, b in zip(vec1, vec2))
            norm1 = sum(a * a for a in vec1) ** 0.5
            norm2 = sum(b * b for b in vec2) ** 0.5
            return dot / (norm1 * norm2) if norm1 and norm2 else 0.0

    def _get_embedding(self, text: str):
        """获取向量（带缓存）"""
        with self._lock:
            if text in self._cache:
                return self._cache[text]

            if self.embedding_func:
                vec = self.embedding_func(text)
                self._cache[text] = vec
                return vec

        return None

    def _jaccard_similarity(self, text1: str, text2: str) -> float:
        """Jaccard 相似度"""
        set1 = set(text1)
        set2 = set(text2)

        intersection = len(set1 & set2)
        union = len(set1 | set2)

        return intersection / union if union else 0.0

    def batch_similarity(
        self,
        query: str,
        candidates: List[str]
    ) -> List[Tuple[str, float]]:
        """
        批量计算相似度
        
        Args:
            query: 查询文本
            candidates: 候选文本列表
            
        Returns:
            [(candidate, score), ...]
        """
        results = []
        for candidate in candidates:
            score = self.similarity(query, candidate)
            results.append((candidate, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results


class NLPProcessor:
    """
    NLP 处理器主类
    
    整合所有 NLP 能力，提供统一的处理接口。
    """

    def __init__(
        self,
        user_dict: Optional[str] = None,
        stopwords: Optional[set] = None,
        embedding_func=None
    ):
        """
        初始化 NLP 处理器
        
        Args:
            user_dict: 用户自定义词典
            stopwords: 自定义停用词
            embedding_func: 向量化函数
        """
        self.tokenizer = ChineseTokenizer(user_dict)
        self.ner = NamedEntityRecognizer()
        self.keyword_extractor = KeywordExtractor(stopwords)
        self.sentiment_analyzer = SentimentAnalyzer()
        self.summarizer = TextSummarizer()
        self.semantic_sim = SemanticSimilarity(embedding_func)

        # 文档频率（用于 TF-IDF）
        self.doc_freq: Dict[str, int] = defaultdict(int)
        self.total_docs = 0

        logger.info("NLP 处理器初始化完成")

    def process(
        self,
        text: str,
        tasks: Optional[List[str]] = None
    ) -> NLPResult:
        """
        处理文本
        
        Args:
            text: 输入文本
            tasks: 要执行的任务列表
                - 'tokenize': 分词
                - 'ner': 命名实体识别
                - 'keyword': 关键词提取
                - 'sentiment': 情感分析
                - 'summary': 摘要
                默认执行所有任务
        
        Returns:
            NLP 处理结果
        """
        import time
        start_time = time.time()

        if not text:
            return NLPResult(
                text='',
                tokens=[],
                entities=[],
                keywords=[]
            )

        # 默认执行所有任务
        if tasks is None:
            tasks = ['tokenize', 'ner', 'keyword', 'sentiment', 'summary']

        # 1. 分词
        tokens = []
        if 'tokenize' in tasks:
            tokens = self.tokenizer.tokenize(text, with_pos=True)

        # 2. 命名实体识别
        entities = []
        if 'ner' in tasks:
            entities = self.ner.recognize(text, tokens)

        # 3. 关键词提取
        keywords = []
        if 'keyword' in tasks:
            if self.total_docs > 0:
                keywords = self.keyword_extractor.extract_tfidf(
                    text, tokens, self.doc_freq, self.total_docs
                )
            else:
                keywords = self.keyword_extractor.extract(tokens)

        # 4. 情感分析
        sentiment = None
        if 'sentiment' in tasks:
            sentiment = self.sentiment_analyzer.analyze(text, tokens)

        # 5. 摘要
        summary = None
        if 'summary' in tasks and len(text) > 200:
            summary = self.summarizer.summarize(text)

        processing_time = (time.time() - start_time) * 1000

        return NLPResult(
            text=text,
            tokens=tokens,
            entities=entities,
            keywords=keywords,
            sentiment=sentiment,
            summary=summary,
            language='zh',
            processing_time_ms=processing_time
        )

    def tokenize(self, text: str, with_pos: bool = True) -> List[Token]:
        """快速分词接口"""
        return self.tokenizer.tokenize(text, with_pos)

    def extract_keywords(self, text: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """快速关键词提取接口"""
        tokens = self.tokenizer.tokenize(text)
        return self.keyword_extractor.extract(tokens, top_k)

    def analyze_sentiment(self, text: str) -> SentimentResult:
        """快速情感分析接口"""
        return self.sentiment_analyzer.analyze(text)

    def summarize(self, text: str, ratio: float = 0.3) -> SummaryResult:
        """快速摘要接口"""
        return self.summarizer.summarize(text, ratio)

    def similarity(self, text1: str, text2: str) -> float:
        """快速相似度计算接口"""
        return self.semantic_sim.similarity(text1, text2)

    def extract_entities(self, text: str) -> List[Entity]:
        """快速实体提取接口"""
        tokens = self.tokenizer.tokenize(text)
        return self.ner.recognize(text, tokens)

    def update_doc_freq(self, text: str):
        """
        更新文档频率（用于 TF-IDF）
        
        Args:
            text: 文档文本
        """
        tokens = self.tokenizer.tokenize(text)
        words = set(t.text for t in tokens if len(t.text) > 1)

        for word in words:
            self.doc_freq[word] += 1

        self.total_docs += 1

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'total_docs': self.total_docs,
            'vocab_size': len(self.doc_freq),
            'tokenizer_initialized': self.tokenizer.initialized,
            'jieba_available': JIEBA_AVAILABLE,
            'snownlp_available': SNOWNLP_AVAILABLE,
            'numpy_available': NUMPY_AVAILABLE,
        }


# 便捷函数
def process_text(text: str, tasks: Optional[List[str]] = None) -> NLPResult:
    """
    便捷函数：处理文本
    
    Args:
        text: 输入文本
        tasks: 要执行的任务列表
        
    Returns:
        NLP 处理结果
    """
    processor = NLPProcessor()
    return processor.process(text, tasks)


def tokenize(text: str) -> List[str]:
    """便捷函数：分词"""
    processor = NLPProcessor()
    tokens = processor.tokenize(text, with_pos=False)
    return [t.text for t in tokens]


def extract_keywords(text: str, top_k: int = 10) -> List[str]:
    """便捷函数：提取关键词"""
    processor = NLPProcessor()
    keywords = processor.extract_keywords(text, top_k)
    return [kw for kw, _ in keywords]


def analyze_sentiment(text: str) -> Tuple[str, float]:
    """便捷函数：情感分析"""
    processor = NLPProcessor()
    result = processor.analyze_sentiment(text)
    return result.label, result.score


if __name__ == '__main__':
    # 测试
    print("=" * 60)
    print("NLP 处理器测试")
    print("=" * 60)

    processor = NLPProcessor()

    # 测试文本
    test_texts = [
        "小艺 Claw 是一个非常优秀的 AI 助手，我很喜欢用它。",
        "今天天气真差，烦死了，不想出门。",
        "2026年4月23日，小艺 Claw 完成了 NLP 模块的开发。",
    ]

    for text in test_texts:
        print(f"\n文本: {text}")
        print("-" * 40)

        result = processor.process(text)

        print(f"分词: {[t.text for t in result.tokens[:10]]}")
        print(f"实体: {[(e.text, e.type) for e in result.entities]}")
        print(f"关键词: {[kw for kw, _ in result.keywords[:5]]}")

        if result.sentiment:
            print(f"情感: {result.sentiment.label} ({result.sentiment.score:.2f})")

        print(f"处理时间: {result.processing_time_ms:.2f}ms")

    print("\n" + "=" * 60)
    print("统计信息:")
    print(json.dumps(processor.get_stats(), indent=2, ensure_ascii=False))
