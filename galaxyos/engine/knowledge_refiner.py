#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Knowledge Refiner - 知识精炼器
基于 CRAG 论文实现，从检索结果中提取关键信息

CRAG: https://arxiv.org/abs/2401.15884

核心功能：
1. 分割长文档为知识片段
2. 提取关键信息
3. 去重和整合
4. 生成精炼的知识摘要
"""

import re
import json
import logging
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass, field
from collections import Counter

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeSegment:
    """知识片段"""
    content: str
    segment_type: str  # definition, fact, procedure, comparison, example
    importance: float
    keywords: List[str]
    source_index: int
    position: Tuple[int, int]  # (start, end) in original text


@dataclass
class RefinedKnowledge:
    """精炼后的知识"""
    summary: str
    key_points: List[str]
    segments: List[KnowledgeSegment]
    deduplicated: bool
    original_length: int
    refined_length: int
    compression_ratio: float


class KnowledgeRefiner:
    """
    知识精炼器

    基于 CRAG 论文的知识精炼模块，从检索到的文档中提取
    关键信息，去除冗余，生成精炼的知识摘要。

    处理流程：
    1. 文档分割：将长文档分割为语义片段
    2. 片段分类：识别片段类型（定义、事实、步骤等）
    3. 重要性评估：评估每个片段的重要性
    4. 去重：移除重复或高度相似的片段
    5. 整合：生成最终的知识摘要
    """

    # 片段类型识别模式
    SEGMENT_PATTERNS = {
        'definition': [
            r'(是指|定义为|是一种|意思是|所谓)',
            r'(定义|概念|含义)'
        ],
        'fact': [
            r'(是|有|包含|包括|存在)',
            r'(成立于|发明于|发现于|创建于)'
        ],
        'procedure': [
            r'(首先|然后|接着|最后|步骤)',
            r'(方法|过程|流程|操作)'
        ],
        'comparison': [
            r'(与.*相比|不同于|区别在于|差异)',
            r'(优点|缺点|优势|劣势)'
        ],
        'example': [
            r'(例如|比如|譬如|举例)',
            r'(案例|实例|样本)'
        ],
        'statistic': [
            r'(\d+%|\d+亿|\d+万|\d+千)',
            r'(统计|数据|调查|研究显示)'
        ]
    }

    # 停用词
    STOPWORDS = {
        '的', '是', '在', '了', '和', '与', '或', '有', '我', '你', '他', '她', '它',
        '这', '那', '一个', '一些', '这种', '那种', '可以', '能够', '应该', '需要',
        '进行', '通过', '使用', '根据', '按照', '为了', '由于', '因为', '所以'
    }

    def __init__(self, config: Optional[Dict] = None):
        """
        初始化知识精炼器

        Args:
            config: 配置字典
        """
        self.config = config or {}

        # 最小片段长度
        self.min_segment_length = self.config.get('min_segment_length', 20)

        # 最大片段数量
        self.max_segments = self.config.get('max_segments', 10)

        # 相似度阈值（用于去重）
        self.similarity_threshold = self.config.get('similarity_threshold', 0.8)

        # 编译正则表达式
        self._compiled_patterns = {
            seg_type: [re.compile(p) for p in patterns]
            for seg_type, patterns in self.SEGMENT_PATTERNS.items()
        }

        logger.info("Knowledge Refiner initialized")

    def refine(
        self,
        query: str,
        documents: List[str],
        max_length: Optional[int] = None
    ) -> RefinedKnowledge:
        """
        精炼知识

        Args:
            query: 用户查询
            documents: 检索到的文档列表
            max_length: 最大输出长度（可选）

        Returns:
            RefinedKnowledge: 精炼后的知识
        """
        if not documents:
            return self._empty_result(query)

        # 1. 分割文档
        all_segments = []
        for idx, doc in enumerate(documents):
            segments = self._segment_document(doc, idx)
            all_segments.extend(segments)

        # 2. 分类片段
        classified_segments = self._classify_segments(all_segments)

        # 3. 评估重要性
        scored_segments = self._score_segments(query, classified_segments)

        # 4. 去重
        deduplicated_segments = self._deduplicate_segments(scored_segments)

        # 5. 选择最重要的片段
        selected_segments = sorted(
            deduplicated_segments,
            key=lambda s: s.importance,
            reverse=True
        )[:self.max_segments]

        # 6. 生成摘要
        summary, key_points = self._generate_summary(
            query, selected_segments, max_length
        )

        # 计算压缩比
        original_length = sum(len(doc) for doc in documents)
        refined_length = len(summary)
        compression_ratio = refined_length / original_length if original_length > 0 else 0

        return RefinedKnowledge(
            summary=summary,
            key_points=key_points,
            segments=selected_segments,
            deduplicated=len(deduplicated_segments) < len(scored_segments),
            original_length=original_length,
            refined_length=refined_length,
            compression_ratio=round(compression_ratio, 2)
        )

    def _segment_document(
        self,
        document: str,
        source_index: int
    ) -> List[KnowledgeSegment]:
        """分割文档为片段"""
        segments = []

        # 按句子分割
        sentences = re.split(r'([。！？\n]+)', document)

        # 合并句子为片段
        current_segment = ""
        segment_start = 0

        for i, part in enumerate(sentences):
            if re.match(r'^[。！？\n]+$', part):
                # 分隔符，检查是否应该结束当前片段
                if len(current_segment) >= self.min_segment_length:
                    segment_end = segment_start + len(current_segment)

                    # 提取关键词
                    keywords = self._extract_keywords(current_segment)

                    segments.append(KnowledgeSegment(
                        content=current_segment.strip(),
                        segment_type='unknown',
                        importance=0.0,
                        keywords=keywords,
                        source_index=source_index,
                        position=(segment_start, segment_end)
                    ))

                    segment_start = segment_end + len(part)
                    current_segment = ""
                else:
                    current_segment += part
            else:
                current_segment += part

        # 处理最后一个片段
        if len(current_segment) >= self.min_segment_length:
            keywords = self._extract_keywords(current_segment)
            segments.append(KnowledgeSegment(
                content=current_segment.strip(),
                segment_type='unknown',
                importance=0.0,
                keywords=keywords,
                source_index=source_index,
                position=(segment_start, segment_start + len(current_segment))
            ))

        return segments

    def _classify_segments(
        self,
        segments: List[KnowledgeSegment]
    ) -> List[KnowledgeSegment]:
        """分类片段"""
        for segment in segments:
            segment.segment_type = self._identify_segment_type(segment.content)

        return segments

    def _identify_segment_type(self, content: str) -> str:
        """识别片段类型"""
        type_scores = {}

        for seg_type, patterns in self._compiled_patterns.items():
            score = sum(1 for p in patterns if p.search(content))
            if score > 0:
                type_scores[seg_type] = score

        if not type_scores:
            return 'fact'  # 默认为事实类型

        # 返回得分最高的类型
        return max(type_scores, key=type_scores.get)

    def _score_segments(
        self,
        query: str,
        segments: List[KnowledgeSegment]
    ) -> List[KnowledgeSegment]:
        """评估片段重要性"""
        query_keywords = set(self._extract_keywords(query))

        for segment in segments:
            # 关键词匹配得分
            segment_keywords = set(segment.keywords)
            keyword_overlap = len(query_keywords & segment_keywords)
            keyword_score = keyword_overlap / max(len(query_keywords), 1)

            # 类型权重
            type_weights = {
                'definition': 1.0,
                'fact': 0.8,
                'procedure': 0.9,
                'comparison': 0.7,
                'example': 0.6,
                'statistic': 0.8,
                'unknown': 0.5
            }
            type_score = type_weights.get(segment.segment_type, 0.5)

            # 长度得分（适中的长度更好）
            length = len(segment.content)
            length_score = 1.0 if 50 <= length <= 200 else 0.7

            # 综合得分
            segment.importance = (
                0.5 * keyword_score +
                0.3 * type_score +
                0.2 * length_score
            )

        return segments

    def _deduplicate_segments(
        self,
        segments: List[KnowledgeSegment]
    ) -> List[KnowledgeSegment]:
        """去重"""
        if len(segments) <= 1:
            return segments

        unique_segments = []

        for segment in segments:
            is_duplicate = False

            for existing in unique_segments:
                similarity = self._calculate_similarity(
                    segment.content, existing.content
                )

                if similarity >= self.similarity_threshold:
                    is_duplicate = True
                    # 保留重要性更高的
                    if segment.importance > existing.importance:
                        unique_segments.remove(existing)
                        unique_segments.append(segment)
                    break

            if not is_duplicate:
                unique_segments.append(segment)

        return unique_segments

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """计算文本相似度（Jaccard相似度）"""
        words1 = set(self._extract_keywords(text1))
        words2 = set(self._extract_keywords(text2))

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        words = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+', text.lower())
        return [w for w in words if w not in self.STOPWORDS and len(w) > 1]

    def _generate_summary(
        self,
        query: str,
        segments: List[KnowledgeSegment],
        max_length: Optional[int]
    ) -> Tuple[str, List[str]]:
        """生成摘要"""
        if not segments:
            return "无相关信息", []

        # 按类型分组
        grouped = {}
        for segment in segments:
            seg_type = segment.segment_type
            if seg_type not in grouped:
                grouped[seg_type] = []
            grouped[seg_type].append(segment)

        # 生成关键点
        key_points = []

        # 定义类信息优先
        if 'definition' in grouped:
            for seg in grouped['definition'][:2]:
                key_points.append(seg.content)

        # 事实信息
        if 'fact' in grouped:
            for seg in grouped['fact'][:3]:
                key_points.append(seg.content)

        # 步骤信息
        if 'procedure' in grouped:
            for seg in grouped['procedure'][:2]:
                key_points.append(seg.content)

        # 其他信息
        for seg_type, segs in grouped.items():
            if seg_type not in ['definition', 'fact', 'procedure']:
                for seg in segs[:1]:
                    key_points.append(seg.content)

        # 生成摘要
        summary = "\n".join(key_points)

        # 截断到最大长度
        if max_length and len(summary) > max_length:
            summary = summary[:max_length] + "..."

        return summary, key_points[:10]

    def _empty_result(self, query: str) -> RefinedKnowledge:
        """返回空结果"""
        return RefinedKnowledge(
            summary="无相关检索结果",
            key_points=[],
            segments=[],
            deduplicated=False,
            original_length=0,
            refined_length=0,
            compression_ratio=0.0
        )

    def extract_key_entities(self, text: str) -> List[str]:
        """提取关键实体"""
        # 提取可能的实体（大写字母开头的英文、专有名词等）
        entities = []

        # 英文实体
        english_entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        entities.extend(english_entities)

        # 数字实体（年份、数量等）
        number_entities = re.findall(r'\d{4}年|\d+%', text)
        entities.extend(number_entities)

        return list(set(entities))

    def extract_relations(self, text: str) -> List[Tuple[str, str, str]]:
        """提取关系三元组（简化版）"""
        relations = []

        # 简单的关系模式
        patterns = [
            (r'(.+?)是(.+?)的(.+)', '是'),
            (r'(.+?)包括(.+)', '包括'),
            (r'(.+?)属于(.+)', '属于'),
        ]

        for pattern, relation in patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if len(match) >= 2:
                    relations.append((match[0], relation, match[1]))

        return relations[:5]


# 便捷函数
def refine_knowledge(
    query: str,
    documents: List[str],
    config: Optional[Dict] = None
) -> RefinedKnowledge:
    """
    便捷函数：精炼知识

    Args:
        query: 用户查询
        documents: 检索到的文档
        config: 可选配置

    Returns:
        RefinedKnowledge: 精炼后的知识
    """
    refiner = KnowledgeRefiner(config)
    return refiner.refine(query, documents)


if __name__ == "__main__":
    # 测试示例
    test_docs = [
        "机器学习是人工智能的一个分支，它使计算机能够从数据中学习并做出决策。机器学习算法通过训练数据构建模型，然后使用该模型对新数据进行预测。",
        "深度学习是机器学习的子领域，使用多层神经网络进行学习。深度学习在图像识别、自然语言处理等领域取得了重大突破。",
        "机器学习的主要类型包括监督学习、无监督学习和强化学习。监督学习使用标注数据进行训练，无监督学习从未标注数据中发现模式。"
    ]

    refiner = KnowledgeRefiner()

    print("=" * 60)
    print("Knowledge Refiner 测试")
    print("=" * 60)

    result = refiner.refine("什么是机器学习？", test_docs)

    print(f"\n摘要:\n{result.summary}")
    print("\n关键点:")
    for i, point in enumerate(result.key_points, 1):
        print(f"  {i}. {point[:50]}...")

    print(f"\n片段数: {len(result.segments)}")
    print(f"原始长度: {result.original_length}")
    print(f"精炼长度: {result.refined_length}")
    print(f"压缩比: {result.compression_ratio:.2%}")

    print("\n片段详情:")
    for seg in result.segments:
        print(f"  - [{seg.segment_type}] 重要性:{seg.importance:.2f}")
        print(f"    {seg.content[:60]}...")
