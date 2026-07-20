#!/usr/bin/env python3
"""
RAG 失败点检测模块

论文参考:
- Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection (arXiv:2310.11511)
- Seven Failure Points of RAG Systems (2024)
  识别 RAG 系统的 7 个常见失败点，并提供自动检测和纠正策略

7 个 RAG 失败点:
1. 缺失内容 (Missing Content): 检索的文档不包含回答问题所需的信息
2. 错误检索 (Wrong Retrieval): 检索到了不相关的文档
3. 排序失败 (Ranking Failure): 相关文档被排在后面，无关文档排在前面
4. 上下文遗漏 (Context Missing): 相关文档存在但未被检索到
5. 格式不匹配 (Format Mismatch): 文档格式与查询需求不匹配
6. 提取失败 (Extraction Failure): 模型无法从检索到的文档中提取正确信息
7. 生成失败 (Generation Failure): 模型即使有正确的上下文也无法生成正确答案

功能:
- 自动检测 RAG 流程中的失败点
- 提供纠正建议和自动纠正策略
- 统计失败频率，指导系统优化
"""

import logging
import re
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class FailurePoint(Enum):
    """RAG 失败点"""
    MISSING_CONTENT = "missing_content"          # 缺失内容
    WRONG_RETRIEVAL = "wrong_retrieval"          # 错误检索
    RANKING_FAILURE = "ranking_failure"          # 排序失败
    CONTEXT_MISSING = "context_missing"          # 上下文遗漏
    FORMAT_MISMATCH = "format_mismatch"          # 格式不匹配
    EXTRACTION_FAILURE = "extraction_failure"    # 提取失败
    GENERATION_FAILURE = "generation_failure"    # 生成失败


@dataclass
class FailureDetection:
    """失败检测结果"""
    failure_point: FailurePoint
    detected: bool
    confidence: float               # 检测置信度 (0-1)
    description: str                # 描述
    suggestion: str                 # 纠正建议
    auto_correctable: bool = False  # 是否可自动纠正
    correction: Optional[str] = None  # 自动纠正方案


@dataclass
class RAGFailureReport:
    """RAG 失败检测报告"""
    query: str
    detections: List[FailureDetection]
    has_failure: bool
    failure_count: int
    auto_correctable_count: int
    overall_health: float           # 0-1, 1 = 健康
    metadata: Dict = field(default_factory=dict)


class RAGFailureDetector:
    """
    RAG 失败点检测器

    检测 RAG 流程中的 7 个失败点，并提供纠正建议。

    使用示例:
    >>> detector = RAGFailureDetector(llm_client=client)
    >>> report = detector.detect(
    ...     query="什么是机器学习？",
    ...     documents=[...],
    ...     generated_answer="机器学习是..."
    ... )
    >>> for d in report.detections:
    ...     if d.detected:
    ...         print(f"{d.failure_point.value}: {d.description}")
    """

    # 每个失败点的检测权重
    _FAILURE_WEIGHTS = {
        FailurePoint.MISSING_CONTENT: 1.0,
        FailurePoint.WRONG_RETRIEVAL: 0.8,
        FailurePoint.RANKING_FAILURE: 0.7,
        FailurePoint.CONTEXT_MISSING: 0.9,
        FailurePoint.FORMAT_MISMATCH: 0.5,
        FailurePoint.EXTRACTION_FAILURE: 0.8,
        FailurePoint.GENERATION_FAILURE: 0.9,
    }

    def __init__(
        self,
        llm_client: Any = None,
        embedding_client: Any = None,
        relevance_threshold: float = 0.3,
    ):
        self.llm_client = llm_client
        self.embedding_client = embedding_client
        self.relevance_threshold = relevance_threshold

        # 失败统计
        self.stats = {
            'total_checks': 0,
            'failures_detected': {fp.value: 0 for fp in FailurePoint},
            'auto_corrections': 0,
        }

    def detect(
        self,
        query: str,
        documents: List[Dict],
        generated_answer: Optional[str] = None,
        context: Optional[str] = None,
    ) -> RAGFailureReport:
        """
        检测 RAG 失败点

        Args:
            query: 用户查询
            documents: 检索到的文档列表 [{"content": ..., "score": ...}, ...]
            generated_answer: 生成的答案
            context: 使用的上下文

        Returns:
            RAGFailureReport
        """
        self.stats['total_checks'] += 1
        detections = []

        # 1. 缺失内容检测
        d = self._detect_missing_content(query, documents)
        detections.append(d)

        # 2. 错误检索检测
        d = self._detect_wrong_retrieval(query, documents)
        detections.append(d)

        # 3. 排序失败检测
        d = self._detect_ranking_failure(query, documents)
        detections.append(d)

        # 4. 上下文遗漏检测
        d = self._detect_context_missing(query, documents)
        detections.append(d)

        # 5. 格式不匹配检测
        d = self._detect_format_mismatch(query, documents)
        detections.append(d)

        # 6. 提取失败检测
        if generated_answer:
            d = self._detect_extraction_failure(query, documents, generated_answer)
            detections.append(d)
        else:
            detections.append(FailureDetection(
                failure_point=FailurePoint.EXTRACTION_FAILURE,
                detected=False,
                confidence=0.0,
                description="未提供生成答案，跳过提取失败检测",
                suggestion="",
            ))

        # 7. 生成失败检测
        if generated_answer:
            d = self._detect_generation_failure(query, generated_answer, context)
            detections.append(d)
        else:
            detections.append(FailureDetection(
                failure_point=FailurePoint.GENERATION_FAILURE,
                detected=False,
                confidence=0.0,
                description="未提供生成答案，跳过生成失败检测",
                suggestion="",
            ))

        # 汇总
        detected_failures = [d for d in detections if d.detected]
        has_failure = len(detected_failures) > 0
        auto_correctable = [d for d in detected_failures if d.auto_correctable]

        # 计算健康度
        health = 1.0
        for d in detected_failures:
            health -= self._FAILURE_WEIGHTS.get(d.failure_point, 0.5) * d.confidence
        health = max(0.0, min(1.0, health))

        # 更新统计
        for d in detected_failures:
            self.stats['failures_detected'][d.failure_point.value] += 1

        return RAGFailureReport(
            query=query,
            detections=detections,
            has_failure=has_failure,
            failure_count=len(detected_failures),
            auto_correctable_count=len(auto_correctable),
            overall_health=round(health, 3),
            metadata={
                'num_documents': len(documents),
                'has_answer': generated_answer is not None,
            },
        )

    def _detect_missing_content(
        self,
        query: str,
        documents: List[Dict],
    ) -> FailureDetection:
        """
        检测缺失内容

        判断: 检索到的文档是否包含回答问题所需的信息。
        启发式: 检查文档内容与查询的关键词重叠度。
        """
        if not documents:
            return FailureDetection(
                failure_point=FailurePoint.MISSING_CONTENT,
                detected=True,
                confidence=1.0,
                description="没有检索到任何文档",
                suggestion="扩大检索范围、降低相似度阈值、或添加更多数据源",
                auto_correctable=True,
                correction="web_search: 触发 Web 搜索补充",
            )

        # 检查关键词覆盖度
        query_keywords = self._extract_keywords(query)
        if not query_keywords:
            return FailureDetection(
                failure_point=FailurePoint.MISSING_CONTENT,
                detected=False,
                confidence=0.3,
                description="查询过于简短，无法判断内容覆盖度",
                suggestion="",
            )

        covered = 0
        for kw in query_keywords:
            for doc in documents:
                content = doc.get('content', doc.get('text', ''))
                if kw.lower() in content.lower():
                    covered += 1
                    break

        coverage = covered / len(query_keywords) if query_keywords else 0

        if coverage < self.relevance_threshold:
            return FailureDetection(
                failure_point=FailurePoint.MISSING_CONTENT,
                detected=True,
                confidence=1.0 - coverage,
                description=f"关键词覆盖率低 ({coverage:.0%})，文档可能缺少关键信息",
                suggestion="扩大检索范围或使用多查询扩展",
                auto_correctable=True,
                correction="query_expansion: 使用同义词和多角度查询扩展",
            )

        return FailureDetection(
            failure_point=FailurePoint.MISSING_CONTENT,
            detected=False,
            confidence=1.0 - coverage,
            description=f"关键词覆盖率 {coverage:.0%}，内容基本完整",
            suggestion="",
        )

    def _detect_wrong_retrieval(
        self,
        query: str,
        documents: List[Dict],
    ) -> FailureDetection:
        """检测错误检索"""
        if not documents:
            return FailureDetection(
                failure_point=FailurePoint.WRONG_RETRIEVAL,
                detected=False,
                confidence=0.0,
                description="无文档，跳过错误检索检测",
                suggestion="",
            )

        # 检查文档相关性分数分布
        scores = [doc.get('score', 0.0) for doc in documents]
        if not scores:
            return FailureDetection(
                failure_point=FailurePoint.WRONG_RETRIEVAL,
                detected=False,
                confidence=0.0,
                description="无分数信息",
                suggestion="",
            )

        avg_score = sum(scores) / len(scores)
        max_score = max(scores)

        # 如果最高分都很低，说明检索可能有误
        if max_score < 0.3:
            return FailureDetection(
                failure_point=FailurePoint.WRONG_RETRIEVAL,
                detected=True,
                confidence=0.8,
                description=f"最高相关性分数仅 {max_score:.2f}，检索结果可能不相关",
                suggestion="调整检索策略：使用混合搜索、降低阈值、或改写查询",
                auto_correctable=True,
                correction="hybrid_search: 切换到 Dense+Sparse 混合搜索",
            )

        # 如果分数方差大，可能有噪声
        if len(scores) > 1:
            variance = sum((s - avg_score) ** 2 for s in scores) / len(scores)
            if variance > 0.1 and avg_score < 0.5:
                return FailureDetection(
                    failure_point=FailurePoint.WRONG_RETRIEVAL,
                    detected=True,
                    confidence=0.5,
                    description="检索结果相关性差异大，可能有噪声",
                    suggestion="使用重排器精排，过滤低分文档",
                    auto_correctable=True,
                    correction="rerank: 对检索结果进行重排",
                )

        return FailureDetection(
            failure_point=FailurePoint.WRONG_RETRIEVAL,
            detected=False,
            confidence=0.2,
            description="检索结果相关性正常",
            suggestion="",
        )

    def _detect_ranking_failure(
        self,
        query: str,
        documents: List[Dict],
    ) -> FailureDetection:
        """检测排序失败"""
        if len(documents) < 2:
            return FailureDetection(
                failure_point=FailurePoint.RANKING_FAILURE,
                detected=False,
                confidence=0.0,
                description="文档数量不足，无法检测排序",
                suggestion="",
            )

        # 检查分数是否单调递减
        scores = [doc.get('score', 0.0) for doc in documents]
        is_monotone = all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))

        if not is_monotone:
            return FailureDetection(
                failure_point=FailurePoint.RANKING_FAILURE,
                detected=True,
                confidence=0.4,
                description="文档排序不符合分数递减，可能存在排序问题",
                suggestion="检查排序逻辑，使用重排器",
                auto_correctable=True,
                correction="rerank: 重新按相关性分数排序",
            )

        return FailureDetection(
            failure_point=FailurePoint.RANKING_FAILURE,
            detected=False,
            confidence=0.1,
            description="文档排序正常",
            suggestion="",
        )

    def _detect_context_missing(
        self,
        query: str,
        documents: List[Dict],
    ) -> FailureDetection:
        """检测上下文遗漏"""
        if not documents:
            return FailureDetection(
                failure_point=FailurePoint.CONTEXT_MISSING,
                detected=True,
                confidence=0.9,
                description="无检索结果，可能遗漏了相关上下文",
                suggestion="增加检索数量、使用多查询扩展、或添加数据源",
                auto_correctable=True,
                correction="multi_query: 使用多查询扩展增加召回",
            )

        # 检查文档数量是否过少
        if len(documents) < 3:
            return FailureDetection(
                failure_point=FailurePoint.CONTEXT_MISSING,
                detected=True,
                confidence=0.5,
                description=f"仅检索到 {len(documents)} 个文档，可能遗漏相关上下文",
                suggestion="增加 top_k 参数，使用多路检索",
                auto_correctable=True,
                correction="increase_topk: 增加检索数量",
            )

        return FailureDetection(
            failure_point=FailurePoint.CONTEXT_MISSING,
            detected=False,
            confidence=0.2,
            description="检索结果数量合理",
            suggestion="",
        )

    def _detect_format_mismatch(
        self,
        query: str,
        documents: List[Dict],
    ) -> FailureDetection:
        """检测格式不匹配"""
        # 检查查询是否需要特定格式（列表、表格、代码等）
        format_keywords = {
            '列表': ['列表', '列举', '列出', 'list'],
            '表格': ['表格', '表', 'table'],
            '代码': ['代码', '编程', 'code', '编程实现'],
            '步骤': ['步骤', '流程', '方法', 'how to'],
            '数字': ['多少', '数量', '数字', '统计'],
        }

        required_format = None
        for fmt, keywords in format_keywords.items():
            if any(kw in query.lower() for kw in keywords):
                required_format = fmt
                break

        if required_format is None:
            return FailureDetection(
                failure_point=FailurePoint.FORMAT_MISMATCH,
                detected=False,
                confidence=0.1,
                description="查询无特定格式要求",
                suggestion="",
            )

        # 检查文档是否包含所需格式
        format_indicators = {
            '列表': ['1.', '2.', '-', '*', '•'],
            '表格': ['|', '---', '表'],
            '代码': ['```', 'def ', 'class ', 'import '],
            '步骤': ['步骤', 'Step', '1.', '首先'],
            '数字': [r'\d+', r'\d+%'],
        }

        indicators = format_indicators.get(required_format, [])
        has_format = False
        for doc in documents[:5]:
            content = doc.get('content', doc.get('text', ''))
            for indicator in indicators:
                if indicator.startswith(r'\d'):
                    # 正则
                    if re.search(indicator, content):
                        has_format = True
                        break
                elif indicator in content:
                    has_format = True
                    break
            if has_format:
                break

        if not has_format:
            return FailureDetection(
                failure_point=FailurePoint.FORMAT_MISMATCH,
                detected=True,
                confidence=0.6,
                description=f"查询需要{required_format}格式，但检索结果中未找到",
                suggestion=f"在 prompt 中明确要求以{required_format}格式输出",
                auto_correctable=True,
                correction=f"format_prompt: 在生成 prompt 中加入{required_format}格式要求",
            )

        return FailureDetection(
            failure_point=FailurePoint.FORMAT_MISMATCH,
            detected=False,
            confidence=0.2,
            description="文档格式与查询需求匹配",
            suggestion="",
        )

    def _detect_extraction_failure(
        self,
        query: str,
        documents: List[Dict],
        answer: str,
    ) -> FailureDetection:
        """检测提取失败"""
        if not answer or not documents:
            return FailureDetection(
                failure_point=FailurePoint.EXTRACTION_FAILURE,
                detected=False,
                confidence=0.0,
                description="无答案或无文档",
                suggestion="",
            )

        # 检查答案是否引用了文档内容
        doc_contents = [doc.get('content', doc.get('text', '')) for doc in documents[:5]]
        combined_docs = ' '.join(doc_contents)

        # 计算答案与文档的词汇重叠
        answer_words = set(answer.lower().split())
        doc_words = set(combined_docs.lower().split())

        # 中文: 按字符
        answer_chars = set(answer) - {' ', '的', '了', '是', '在', '和'}
        doc_chars = set(combined_docs) - {' ', '的', '了', '是', '在', '和'}

        char_overlap = len(answer_chars & doc_chars) / len(answer_chars) if answer_chars else 0
        word_overlap = len(answer_words & doc_words) / len(answer_words) if answer_words else 0

        overlap = max(char_overlap, word_overlap)

        if overlap < 0.2:
            return FailureDetection(
                failure_point=FailurePoint.EXTRACTION_FAILURE,
                detected=True,
                confidence=0.7,
                description=f"答案与文档内容重叠度低 ({overlap:.0%})，可能存在幻觉",
                suggestion="使用忠实度检查，要求模型严格基于文档回答",
                auto_correctable=True,
                correction="faithfulness_check: 启用忠实度验证",
            )

        return FailureDetection(
            failure_point=FailurePoint.EXTRACTION_FAILURE,
            detected=False,
            confidence=0.2,
            description="答案与文档内容重叠度正常",
            suggestion="",
        )

    def _detect_generation_failure(
        self,
        query: str,
        answer: str,
        context: Optional[str] = None,
    ) -> FailureDetection:
        """检测生成失败"""
        if not answer:
            return FailureDetection(
                failure_point=FailurePoint.GENERATION_FAILURE,
                detected=True,
                confidence=1.0,
                description="生成答案为空",
                suggestion="重试生成，调整参数或换用更大的模型",
            )

        # 检查答案是否过于简短
        if len(answer) < 10:
            return FailureDetection(
                failure_point=FailurePoint.GENERATION_FAILURE,
                detected=True,
                confidence=0.6,
                description="生成答案过于简短",
                suggestion="增加 max_tokens 参数，调整 temperature",
            )

        # 检查是否包含"无法回答"类模式
        failure_patterns = [
            "我无法", "我不知道", "无法回答", "没有足够信息",
            "I cannot", "I don't know", "unable to answer",
        ]
        for pattern in failure_patterns:
            if pattern in answer:
                return FailureDetection(
                    failure_point=FailurePoint.GENERATION_FAILURE,
                    detected=True,
                    confidence=0.5,
                    description=f"答案包含'{pattern}'，可能生成失败",
                    suggestion="增强检索上下文，或换用更大模型",
                )

        return FailureDetection(
            failure_point=FailurePoint.GENERATION_FAILURE,
            detected=False,
            confidence=0.1,
            description="生成答案正常",
            suggestion="",
        )

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """提取关键词"""
        # 简单关键词提取: 去停用词后分词
        stopwords = {'的', '了', '在', '是', '我', '有', '和', '就', '不',
                     '都', '一', '上', '也', '很', '到', '说', '要', '去',
                     '你', '会', '着', '好', '这', '那', '什么', '怎么',
                     '如何', '为什么', '哪', '哪个', '哪些', '多少',
                     'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be',
                     'have', 'has', 'had', 'do', 'does', 'did',
                     'what', 'how', 'why', 'which', 'where', 'when'}

        # 中文分词（简化：按字+词混合）
        keywords = []
        # 英文词
        words = text.lower().split()
        for w in words:
            if w not in stopwords and len(w) > 1:
                keywords.append(w)

        # 中文关键词
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff' and ch not in stopwords:
                keywords.append(ch)

        return keywords

    def get_stats(self) -> Dict:
        return dict(self.stats)

    def get_failure_report(self) -> str:
        """获取失败统计报告"""
        report_lines = ["=== RAG 失败点统计 ==="]
        for fp_name, count in self.stats['failures_detected'].items():
            report_lines.append(f"  {fp_name}: {count}")
        report_lines.append(f"  总检测次数: {self.stats['total_checks']}")
        report_lines.append(f"  自动纠正次数: {self.stats['auto_corrections']}")
        return '\n'.join(report_lines)


# 导出
__all__ = [
    'RAGFailureDetector',
    'FailurePoint',
    'FailureDetection',
    'RAGFailureReport',
]


if __name__ == "__main__":
    print("=== RAG 失败点检测测试 ===\n")

    detector = RAGFailureDetector()

    # 测试1: 正常情况
    print("1. 正常 RAG:")
    report = detector.detect(
        query="什么是机器学习？",
        documents=[
            {"content": "机器学习是人工智能的一个分支，使用算法从数据中学习模式。", "score": 0.9},
            {"content": "监督学习使用标注数据进行训练。", "score": 0.7},
        ],
        generated_answer="机器学习是人工智能的一个分支，使用算法从数据中学习模式。",
    )
    print(f"   健康度: {report.overall_health}")
    print(f"   失败数: {report.failure_count}")
    for d in report.detections:
        if d.detected:
            print(f"   ❌ {d.failure_point.value}: {d.description}")

    # 测试2: 缺失内容
    print("\n2. 缺失内容:")
    report = detector.detect(
        query="什么是量子计算？",
        documents=[
            {"content": "机器学习是人工智能的一个分支。", "score": 0.3},
        ],
        generated_answer="我无法回答关于量子计算的问题。",
    )
    print(f"   健康度: {report.overall_health}")
    print(f"   失败数: {report.failure_count}")
    for d in report.detections:
        if d.detected:
            print(f"   ❌ {d.failure_point.value}: {d.description}")
            print(f"      建议: {d.suggestion}")

    # 测试3: 无文档
    print("\n3. 无文档:")
    report = detector.detect(
        query="什么是深度学习？",
        documents=[],
    )
    print(f"   健康度: {report.overall_health}")
    for d in report.detections:
        if d.detected:
            print(f"   ❌ {d.failure_point.value}: {d.description}")

    # 统计
    print(f"\n4. 统计: {detector.get_stats()}")
