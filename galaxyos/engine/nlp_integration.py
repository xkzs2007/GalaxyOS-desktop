#!/usr/bin/env python3
"""
NLP 模块整合器
==============

将 NLP 处理器整合到GalaxyOS：
- 为记忆模块提供关键词提取、实体识别
- 为检索模块提供分词、TF-IDF
- 为防幻觉模块提供情感分析、语义相似度
- 为知识图谱提供实体抽取

Author: GalaxyOS
版本: v1.0.0
日期: 2026-04-23
"""

import os
import sys
import json
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from functools import lru_cache
import threading

# 添加路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nlp_processor import (
    NLPProcessor,
    NLPResult,
    Token,
    Entity,
    SentimentResult,
    SummaryResult,
)

logger = logging.getLogger(__name__)


@dataclass
class NLPConfig:
    """NLP 配置"""
    enable_cache: bool = True
    cache_size: int = 1000
    default_tasks: List[str] = None

    def __post_init__(self):
        if self.default_tasks is None:
            self.default_tasks = ['tokenize', 'ner', 'keyword']


class NLPIntegration:
    """
    NLP 整合器

    作为 NLP 模块与系统其他模块的桥梁。
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """单例模式"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        config: Optional[NLPConfig] = None,
        embedding_func: Optional[Callable] = None
    ):
        """
        初始化整合器

        Args:
            config: NLP 配置
            embedding_func: 向量化函数（来自系统的 embedding 模块）
        """
        if hasattr(self, '_initialized') and self._initialized:
            return

        self.config = config or NLPConfig()
        self.processor = NLPProcessor(embedding_func=embedding_func)

        # 缓存
        self._cache = {}
        self._cache_lock = threading.Lock()

        # 回调函数（其他模块注册）
        self._callbacks: Dict[str, List[Callable]] = {
            'on_entity_found': [],
            'on_keyword_extracted': [],
            'on_sentiment_analyzed': [],
        }

        self._initialized = True
        logger.info("NLP 整合器初始化完成")

    def process(
        self,
        text: str,
        tasks: Optional[List[str]] = None,
        use_cache: bool = True
    ) -> NLPResult:
        """
        处理文本

        Args:
            text: 输入文本
            tasks: 任务列表
            use_cache: 是否使用缓存

        Returns:
            NLP 结果
        """
        if not text:
            return NLPResult(text='', tokens=[], entities=[], keywords=[])

        # 检查缓存
        cache_key = self._make_cache_key(text, tasks)
        if use_cache and self.config.enable_cache:
            with self._cache_lock:
                if cache_key in self._cache:
                    return self._cache[cache_key]

        # 处理
        tasks = tasks or self.config.default_tasks
        result = self.processor.process(text, tasks)

        # 触发回调
        self._trigger_callbacks(result)

        # 缓存结果
        if use_cache and self.config.enable_cache:
            with self._cache_lock:
                self._cache[cache_key] = result
                # 限制缓存大小
                if len(self._cache) > self.config.cache_size:
                    # 删除最早的 10%
                    keys_to_remove = list(self._cache.keys())[:self.config.cache_size // 10]
                    for k in keys_to_remove:
                        del self._cache[k]

        return result

    def _make_cache_key(self, text: str, tasks: Optional[List[str]]) -> str:
        """生成缓存键"""
        import hashlib
        task_str = ','.join(sorted(tasks)) if tasks else ''
        key_str = f"{text}:{task_str}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def _trigger_callbacks(self, result: NLPResult):
        """触发回调函数"""
        # 实体发现回调
        if result.entities:
            for callback in self._callbacks['on_entity_found']:
                try:
                    callback(result.entities)
                except Exception as e:
                    logger.error(f"回调执行失败: {e}")

        # 关键词提取回调
        if result.keywords:
            for callback in self._callbacks['on_keyword_extracted']:
                try:
                    callback(result.keywords)
                except Exception as e:
                    logger.error(f"回调执行失败: {e}")

        # 情感分析回调
        if result.sentiment:
            for callback in self._callbacks['on_sentiment_analyzed']:
                try:
                    callback(result.sentiment)
                except Exception as e:
                    logger.error(f"回调执行失败: {e}")

    # ========== 记忆模块接口 ==========

    def extract_memory_keywords(self, text: str, top_k: int = 10) -> List[str]:
        """
        为记忆模块提取关键词

        Args:
            text: 记忆文本
            top_k: 返回数量

        Returns:
            关键词列表
        """
        keywords = self.processor.extract_keywords(text, top_k)
        return [kw for kw, _ in keywords]

    def extract_memory_entities(self, text: str) -> Dict[str, List[str]]:
        """
        为记忆模块提取实体

        Args:
            text: 记忆文本

        Returns:
            {实体类型: [实体列表]}
        """
        entities = self.processor.extract_entities(text)

        result = {}
        for entity in entities:
            if entity.type not in result:
                result[entity.type] = []
            result[entity.type].append(entity.text)

        return result

    def calculate_memory_importance(self, text: str) -> float:
        """
        计算记忆重要性

        基于情感强度、实体数量、关键词密度

        Args:
            text: 记忆文本

        Returns:
            重要性分数 (0-1)
        """
        result = self.process(text, ['sentiment', 'ner', 'keyword'])

        # 情感强度
        sentiment_weight = 0.0
        if result.sentiment:
            sentiment_weight = result.sentiment.confidence

        # 实体数量
        entity_weight = min(len(result.entities) / 5, 1.0)

        # 关键词密度
        keyword_weight = 0.0
        if result.keywords:
            keyword_weight = min(len(result.keywords) / 10, 1.0)

        # 综合分数
        importance = (
            sentiment_weight * 0.4 +
            entity_weight * 0.3 +
            keyword_weight * 0.3
        )

        return importance

    # ========== 检索模块接口 ==========

    def tokenize_for_search(self, text: str) -> List[str]:
        """
        为检索模块分词

        Args:
            text: 查询文本

        Returns:
            分词列表
        """
        tokens = self.processor.tokenize(text, with_pos=False)
        return [t.text for t in tokens]

    def get_search_terms(self, text: str) -> Dict[str, Any]:
        """
        获取检索术语

        Args:
            text: 查询文本

        Returns:
            {
                'tokens': 分词列表,
                'entities': 实体列表,
                'keywords': 关键词列表,
            }
        """
        result = self.process(text, ['tokenize', 'ner', 'keyword'])

        return {
            'tokens': [t.text for t in result.tokens],
            'entities': [(e.text, e.type) for e in result.entities],
            'keywords': [kw for kw, _ in result.keywords],
        }

    def expand_query(self, query: str) -> List[str]:
        """
        查询扩展

        Args:
            query: 原始查询

        Returns:
            扩展后的查询列表
        """
        result = self.process(query, ['tokenize', 'ner', 'keyword'])

        expanded = [query]

        # 添加关键词组合
        if result.keywords:
            keyword_query = ' '.join([kw for kw, _ in result.keywords[:3]])
            expanded.append(keyword_query)

        # 添加实体
        for entity in result.entities:
            if entity.text not in query:
                expanded.append(f"{query} {entity.text}")

        return list(set(expanded))

    # ========== 防幻觉模块接口 ==========

    def check_claim_sentiment(self, claim: str) -> Dict[str, Any]:
        """
        检查声明的情感倾向

        Args:
            claim: 声明文本

        Returns:
            情感分析结果
        """
        sentiment = self.processor.analyze_sentiment(claim)

        return {
            'label': sentiment.label,
            'score': sentiment.score,
            'confidence': sentiment.confidence,
            'keywords': sentiment.keywords,
        }

    def compare_texts(self, text1: str, text2: str) -> Dict[str, Any]:
        """
        比较两段文本

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            {
                'similarity': 相似度,
                'common_entities': 共同实体,
                'common_keywords': 共同关键词,
            }
        """
        result1 = self.process(text1)
        result2 = self.process(text2)

        # 语义相似度
        similarity = self.processor.similarity(text1, text2)

        # 共同实体
        entities1 = set(e.text for e in result1.entities)
        entities2 = set(e.text for e in result2.entities)
        common_entities = list(entities1 & entities2)

        # 共同关键词
        keywords1 = set(kw for kw, _ in result1.keywords)
        keywords2 = set(kw for kw, _ in result2.keywords)
        common_keywords = list(keywords1 & keywords2)

        return {
            'similarity': similarity,
            'common_entities': common_entities,
            'common_keywords': common_keywords,
        }

    def detect_contradiction(self, text1: str, text2: str) -> Dict[str, Any]:
        """
        检测矛盾

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            {
                'has_contradiction': 是否矛盾,
                'sentiment_diff': 情感差异,
                'entity_conflict': 实体冲突,
            }
        """
        sentiment1 = self.processor.analyze_sentiment(text1)
        sentiment2 = self.processor.analyze_sentiment(text2)

        # 情感差异
        sentiment_diff = abs(sentiment1.score - sentiment2.score)

        # 实体冲突（同名实体但不同类型）
        entities1 = {e.text: e.type for e in self.processor.extract_entities(text1)}
        entities2 = {e.text: e.type for e in self.processor.extract_entities(text2)}

        entity_conflict = []
        for text, type1 in entities1.items():
            if text in entities2 and entities2[text] != type1:
                entity_conflict.append({
                    'text': text,
                    'type1': type1,
                    'type2': entities2[text]
                })

        # 判断是否矛盾
        has_contradiction = (
            sentiment_diff > 0.5 or  # 情感差异大
            len(entity_conflict) > 0  # 实体冲突
        )

        return {
            'has_contradiction': has_contradiction,
            'sentiment_diff': sentiment_diff,
            'entity_conflict': entity_conflict,
        }

    # ========== 知识图谱接口 ==========

    def extract_triples(self, text: str) -> List[Dict[str, str]]:
        """
        提取三元组（简化版）

        Args:
            text: 输入文本

        Returns:
            [{'subject': ..., 'predicate': ..., 'object': ...}, ...]
        """
        result = self.process(text, ['tokenize', 'ner'])

        triples = []

        # 简单规则：实体 + 动词 + 实体
        entities = result.entities
        tokens = result.tokens

        for i, token in enumerate(tokens):
            if token.pos == 'VERB':
                # 找前面的实体作为主语
                subject = None
                for entity in entities:
                    if entity.end <= token.start:
                        subject = entity.text

                # 找后面的实体作为宾语
                obj = None
                for entity in entities:
                    if entity.start >= token.end:
                        obj = entity.text
                        break

                if subject and obj:
                    triples.append({
                        'subject': subject,
                        'predicate': token.text,
                        'object': obj
                    })

        return triples

    # ========== 回调注册 ==========

    def register_callback(self, event: str, callback: Callable):
        """
        注册回调函数

        Args:
            event: 事件名称
            callback: 回调函数
        """
        if event in self._callbacks:
            self._callbacks[event].append(callback)
            logger.info(f"注册回调: {event}")
        else:
            logger.warning(f"未知事件: {event}")

    def unregister_callback(self, event: str, callback: Callable):
        """注销回调函数"""
        if event in self._callbacks and callback in self._callbacks[event]:
            self._callbacks[event].remove(callback)

    # ========== 统计与维护 ==========

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = self.processor.get_stats()
        stats.update({
            'cache_size': len(self._cache),
            'callbacks_registered': sum(len(cbs) for cbs in self._callbacks.values()),
        })
        return stats

    def clear_cache(self):
        """清空缓存"""
        with self._cache_lock:
            self._cache.clear()
        logger.info("NLP 缓存已清空")

    def update_doc_freq(self, text: str):
        """更新文档频率"""
        self.processor.update_doc_freq(text)


# ========== 全局实例 ==========

_nlp_integration: Optional[NLPIntegration] = None
_nlp_lock = threading.Lock()


def get_nlp_integration(
    config: Optional[NLPConfig] = None,
    embedding_func: Optional[Callable] = None
) -> NLPIntegration:
    """
    获取全局 NLP 整合器实例

    Args:
        config: NLP 配置
        embedding_func: 向量化函数

    Returns:
        NLP 整合器实例
    """
    global _nlp_integration

    if _nlp_integration is None:
        with _nlp_lock:
            if _nlp_integration is None:
                _nlp_integration = NLPIntegration(config, embedding_func)

    return _nlp_integration


def init_nlp(embedding_func: Optional[Callable] = None) -> NLPIntegration:
    """
    初始化 NLP 模块

    Args:
        embedding_func: 向量化函数

    Returns:
        NLP 整合器实例
    """
    return get_nlp_integration(embedding_func=embedding_func)


# ========== 便捷函数 ==========

def process_text(text: str, tasks: Optional[List[str]] = None) -> NLPResult:
    """处理文本"""
    return get_nlp_integration().process(text, tasks)


def extract_keywords(text: str, top_k: int = 10) -> List[str]:
    """提取关键词"""
    return get_nlp_integration().extract_memory_keywords(text, top_k)


def extract_entities(text: str) -> Dict[str, List[str]]:
    """提取实体"""
    return get_nlp_integration().extract_memory_entities(text)


def analyze_sentiment(text: str) -> Dict[str, Any]:
    """情感分析"""
    return get_nlp_integration().check_claim_sentiment(text)


def tokenize(text: str) -> List[str]:
    """分词"""
    return get_nlp_integration().tokenize_for_search(text)


if __name__ == '__main__':
    # 测试整合器
    print("=" * 60)
    print("NLP 整合器测试")
    print("=" * 60)

    nlp = get_nlp_integration()

    # 测试文本
    test_texts = [
        "GalaxyOS 是一个非常优秀的 AI 助手，我很喜欢用它。",
        "2026年4月23日，GalaxyOS 完成了 NLP 模块的开发。",
        "这个产品太差了，完全不值这个价格，非常失望。",
    ]

    for text in test_texts:
        print(f"\n文本: {text}")
        print("-" * 40)

        # 记忆模块接口
        print(f"关键词: {nlp.extract_memory_keywords(text)}")
        print(f"实体: {nlp.extract_memory_entities(text)}")
        print(f"重要性: {nlp.calculate_memory_importance(text):.2f}")

        # 检索模块接口
        print(f"分词: {nlp.tokenize_for_search(text)}")
        print(f"查询扩展: {nlp.expand_query(text)}")

        # 防幻觉模块接口
        print(f"情感: {nlp.check_claim_sentiment(text)}")

    # 矛盾检测
    print("\n" + "=" * 60)
    print("矛盾检测测试")
    print("=" * 60)

    text1 = "这个产品非常好，我很满意。"
    text2 = "这个产品太差了，完全不值这个价格。"

    result = nlp.detect_contradiction(text1, text2)
    print(f"文本1: {text1}")
    print(f"文本2: {text2}")
    print(f"矛盾检测结果: {result}")

    # 统计信息
    print("\n" + "=" * 60)
    print("统计信息:")
    print(json.dumps(nlp.get_stats(), indent=2, ensure_ascii=False))
