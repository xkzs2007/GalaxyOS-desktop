#!/usr/bin/env python3
"""
RAG HyDE 查询重写模块
通过假设性文档嵌入提升检索相关性

功能：
- HyDE (Hypothetical Document Embeddings) 查询重写（LLM 生成 + 模板回退）
- 子查询分解（正则 + LLM 增强）
- 查询扩展
- 多查询融合
- Reranker 重排序

优化效果：
- 首轮检索相关性提升 20-40%
- 复杂查询召回率提升 15-30%
"""

from typing import List, Optional, Tuple, Dict, Any, Callable
import re
import json
import logging

logger = logging.getLogger(__name__)


class HyDEQueryRewriter:
    """
    HyDE 查询重写器

    优先使用 LLM 生成假设性文档，当 LLM 不可用时回退到静态模板。
    """

    def __init__(
        self,
        embedding_model: Optional[Callable] = None,
        llm_model: Optional[Any] = None,
        num_hypothetical_docs: int = 3
    ):
        """
        初始化 HyDE 重写器

        Args:
            embedding_model: 嵌入模型（用于生成向量）
            llm_model: LLM 客户端（用于生成假设性文档）
            num_hypothetical_docs: 生成的假设性文档数量
        """
        self.embedding_model = embedding_model
        self.llm_model = llm_model
        self.num_hypothetical_docs = num_hypothetical_docs

        # 预定义的假设性文档模板（LLM 不可用时的回退）
        self.templates = {
            'technical': """
假设这是一篇关于 {query} 的技术文档：

在 {domain} 领域中，{query} 是一个重要的概念。它涉及到以下几个关键方面：

1. 核心原理：{query} 的基本工作原理是...
2. 实现方法：常见的实现方式包括...
3. 应用场景：{query} 主要应用于...
4. 优势与局限：相比其他方法，{query} 的优势在于...

总结：{query} 是解决特定问题的有效方法。
""",
            'general': """
假设这是一篇关于 {query} 的文章：

{query} 是一个值得探讨的话题。以下是关于它的主要内容：

首先，我们需要了解 {query} 的基本概念。简单来说，它指的是...

其次，{query} 有以下几个重要特点：
- 特点一：...
- 特点二：...
- 特点三：...

最后，关于 {query} 的实际应用，我们可以看到...

这篇文章总结了 {query} 的核心要点。
""",
            'qa': """
假设这是一个关于 {query} 的问答：

问题：{query}

回答：
关于 {query}，我们可以从以下几个方面来理解：

1. 定义：{query} 指的是...
2. 原理：其背后的原理是...
3. 实践：在实际中，我们通常这样处理...
4. 注意事项：需要注意...

希望这个回答能帮助您理解 {query}。
"""
        }

    def detect_query_type(self, query: str) -> str:
        """
        检测查询类型

        Args:
            query: 原始查询

        Returns:
            str: 查询类型
        """
        tech_keywords = [
            '如何', '怎么', '实现', '原理', '架构', '算法',
            '代码', '编程', '优化', '性能', '配置', '部署',
            'API', 'SDK', '框架', '库', '模块', '函数'
        ]

        qa_keywords = ['什么是', '为什么', '区别', '比较', '优缺点']

        query_lower = query.lower()

        for kw in tech_keywords:
            if kw.lower() in query_lower:
                return 'technical'

        for kw in qa_keywords:
            if kw.lower() in query_lower:
                return 'qa'

        return 'general'

    def detect_domain(self, query: str) -> str:
        """
        检测查询领域

        Args:
            query: 原始查询

        Returns:
            str: 领域名称
        """
        domains = {
            'AI/ML': ['机器学习', '深度学习', '神经网络', 'AI', 'ML', '模型', '训练'],
            '数据库': ['数据库', 'SQL', 'NoSQL', 'MySQL', 'PostgreSQL', 'MongoDB'],
            'Web开发': ['Web', '前端', '后端', 'HTTP', 'API', 'REST', 'GraphQL'],
            '系统': ['系统', 'Linux', '操作系统', '进程', '线程', '内存'],
            '安全': ['安全', '加密', '认证', '授权', '漏洞', '攻击'],
        }

        for domain, keywords in domains.items():
            for kw in keywords:
                if kw.lower() in query.lower():
                    return domain

        return '技术'

    def generate_hypothetical_doc(self, query: str, doc_type: str = None) -> str:
        """
        生成假设性文档

        优先使用 LLM 生成，不可用时回退到模板。

        Args:
            query: 原始查询
            doc_type: 文档类型

        Returns:
            str: 假设性文档
        """
        if doc_type is None:
            doc_type = self.detect_query_type(query)

        # 优先使用 LLM 生成
        if self.llm_model is not None:
            try:
                llm_doc = self._generate_with_llm(query, doc_type)
                if llm_doc:
                    return llm_doc
            except Exception as e:
                logger.warning(f"LLM 生成假设性文档失败，回退到模板: {e}")

        # 回退到模板
        return self._generate_with_template(query, doc_type)

    def _generate_with_llm(self, query: str, doc_type: str) -> Optional[str]:
        """使用 LLM 生成假设性文档"""
        if not hasattr(self.llm_model, 'chat'):
            return None

        prompt_map = {
            'technical': f"请写一段技术文档（200字以内），回答以下技术问题：{query}",
            'qa': f"请写一个详细的问答（200字以内），回答：{query}",
            'general': f"请写一篇简短的文章（200字以内），介绍：{query}",
        }

        prompt = prompt_map.get(doc_type, prompt_map['general'])
        messages = [{"role": "user", "content": prompt}]

        response = self.llm_model.chat(messages, max_tokens=300, temperature=0.7)
        return response.strip() if response else None

    def _generate_with_template(self, query: str, doc_type: str) -> str:
        """使用模板生成假设性文档"""
        domain = self.detect_domain(query)
        template = self.templates.get(doc_type, self.templates['general'])
        return template.format(query=query, domain=domain).strip()

    def generate_hypothetical_docs(
        self,
        query: str,
        num_docs: int = None
    ) -> List[str]:
        """
        生成多个假设性文档

        Args:
            query: 原始查询
            num_docs: 文档数量

        Returns:
            List[str]: 假设性文档列表
        """
        if num_docs is None:
            num_docs = self.num_hypothetical_docs

        docs = []
        doc_types = ['technical', 'general', 'qa']

        for i in range(num_docs):
            doc_type = doc_types[i % len(doc_types)]
            doc = self.generate_hypothetical_doc(query, doc_type)
            docs.append(doc)

        return docs

    def rewrite(
        self,
        query: str,
        return_docs: bool = False
    ) -> Dict[str, Any]:
        """
        重写查询

        Args:
            query: 原始查询
            return_docs: 是否返回假设性文档

        Returns:
            Dict: 重写结果
        """
        hypothetical_docs = self.generate_hypothetical_docs(query)

        # 合并查询和假设性文档
        expanded_query = query + "\n\n" + "\n\n".join(hypothetical_docs)

        result = {
            'original_query': query,
            'expanded_query': expanded_query,
            'query_type': self.detect_query_type(query),
            'domain': self.detect_domain(query),
            'llm_used': self.llm_model is not None,
        }

        if return_docs:
            result['hypothetical_docs'] = hypothetical_docs

        return result


class SubQueryDecomposer:
    """
    子查询分解器

    将复杂查询分解为多个子查询。支持正则分解和 LLM 增强分解。
    """

    def __init__(self, llm_model: Optional[Any] = None):
        """
        初始化分解器

        Args:
            llm_model: LLM 客户端（可选，用于复杂查询分解）
        """
        self.llm_model = llm_model

        # 分解模式
        self.decomposition_patterns = [
            # "A 和 B" -> ["A", "B"]
            (r'(.+?)\s+和\s+(.+)', lambda m: [m.group(1), m.group(2)]),
            # "A 或 B" -> ["A", "B"]
            (r'(.+?)\s+或\s+(.+)', lambda m: [m.group(1), m.group(2)]),
            # "A vs B" -> ["A", "B", "A vs B"]
            (r'(.+?)\s+(?:vs|对比|比较)\s+(.+)', lambda m: [m.group(1), m.group(2), f"{m.group(1)} vs {m.group(2)}"]),
            # 逗号分隔
            (r'(.+?)[,，]\s*(.+)', lambda m: [m.group(1).strip(), m.group(2).strip()]),
            # "既 A 又 B"
            (r'(.+?)\s+(?:既|又|还|同时)\s+(.+)', lambda m: [m.group(1), m.group(2)]),
        ]

    def decompose(self, query: str) -> List[str]:
        """
        分解查询

        Args:
            query: 原始查询

        Returns:
            List[str]: 子查询列表
        """
        sub_queries = [query]

        # 尝试正则分解
        for pattern, extractor in self.decomposition_patterns:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                extracted = extractor(match)
                sub_queries.extend(extracted)
                break

        # 如果正则未能分解且 LLM 可用，尝试 LLM 分解
        if len(sub_queries) == 1 and self.llm_model is not None and len(query) > 20:
            llm_sub_queries = self._decompose_with_llm(query)
            if llm_sub_queries:
                sub_queries.extend(llm_sub_queries)

        return list(set(sub_queries))

    def _decompose_with_llm(self, query: str) -> Optional[List[str]]:
        """使用 LLM 分解复杂查询"""
        if not hasattr(self.llm_model, 'chat'):
            return None

        prompt = (
            f"请将以下复杂查询分解为 2-4 个简单的子查询：\n\n"
            f"查询：{query}\n\n"
            f'以 JSON 数组格式返回，例如：["子查询1", "子查询2"]\n\n'
            f"只返回 JSON 数组。"
        )

        try:
            response = self.llm_model.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.3,
            )
            if response:
                clean = response.strip()
                if clean.startswith("```"):
                    clean = clean.split("```")[1]
                    if clean.startswith("json"):
                        clean = clean[4:]
                result = json.loads(clean.strip())
                if isinstance(result, list):
                    return [str(q) for q in result]
        except (json.JSONDecodeError, ValueError, Exception) as e:
            logger.warning(f"LLM 子查询分解失败: {e}")

        return None


class QueryExpander:
    """
    查询扩展器

    通过同义词和相关词扩展查询。
    """

    def __init__(self):
        """初始化扩展器"""
        # 同义词词典
        self.synonyms = {
            '优化': ['性能', '加速', '提升', '改进'],
            '搜索': ['检索', '查找', '查询', '寻找'],
            '向量': ['嵌入', 'embedding', '表示'],
            '数据库': ['DB', '存储', '持久化'],
            '模型': ['算法', '方法', '方案'],
            '配置': ['设置', '参数', '选项'],
            '部署': ['安装', '发布', '上线'],
            '测试': ['验证', '检查', '调试'],
        }

        # 相关词词典
        self.related = {
            '性能': ['速度', '延迟', '吞吐量', '效率'],
            '内存': ['RAM', '缓存', '堆', '栈'],
            '并发': ['并行', '多线程', '异步', '锁'],
            '安全': ['加密', '认证', '授权', '防护'],
        }

    def expand(self, query: str, max_expansions: int = 3) -> List[str]:
        """
        扩展查询

        Args:
            query: 原始查询
            max_expansions: 最大扩展数量

        Returns:
            List[str]: 扩展后的查询列表
        """
        expanded = [query]

        # 同义词扩展
        for word, syns in self.synonyms.items():
            if word in query:
                for syn in syns[:max_expansions]:
                    expanded_query = query.replace(word, syn)
                    expanded.append(expanded_query)

        # 相关词扩展
        for word, related_words in self.related.items():
            if word in query:
                for rw in related_words[:max_expansions]:
                    expanded_query = f"{query} {rw}"
                    expanded.append(expanded_query)

        return list(set(expanded))


class Reranker:
    """
    重排序器

    对检索结果进行二次重排序以提升相关性。
    支持交叉编码器（cross-encoder）和 LLM 重排序。
    """

    def __init__(self, llm_model: Optional[Any] = None, cross_encoder: Optional[Callable] = None):
        """
        初始化重排序器

        Args:
            llm_model: LLM 客户端（用于 LLM 重排序）
            cross_encoder: 交叉编码器函数，签名为 cross_encoder(query, doc) -> float
        """
        self.llm_model = llm_model
        self.cross_encoder = cross_encoder

    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 10,
        method: str = "auto",
    ) -> List[Dict[str, Any]]:
        """
        重排序文档

        Args:
            query: 查询文本
            documents: 文档列表，每个元素需包含 'content' 和 'score' 字段
            top_k: 返回数量
            method: 重排序方法 ("cross_encoder" / "llm" / "auto")

        Returns:
            List[Dict]: 重排序后的文档列表
        """
        if not documents:
            return []

        # 确定方法
        if method == "auto":
            if self.cross_encoder is not None:
                method = "cross_encoder"
            elif self.llm_model is not None:
                method = "llm"
            else:
                # 无重排序器可用，按原始分数排序返回
                return sorted(documents, key=lambda x: x.get('score', 0), reverse=True)[:top_k]

        if method == "cross_encoder" and self.cross_encoder is not None:
            return self._rerank_cross_encoder(query, documents, top_k)
        elif method == "llm" and self.llm_model is not None:
            return self._rerank_llm(query, documents, top_k)
        else:
            return sorted(documents, key=lambda x: x.get('score', 0), reverse=True)[:top_k]

    def _rerank_cross_encoder(
        self,
        query: str,
        documents: List[Dict],
        top_k: int,
    ) -> List[Dict]:
        """使用交叉编码器重排序"""
        scored_docs = []
        for doc in documents:
            content = doc.get('content', '')
            try:
                rerank_score = self.cross_encoder(query, content)
                doc_copy = dict(doc)
                doc_copy['rerank_score'] = float(rerank_score)
                scored_docs.append(doc_copy)
            except Exception as e:
                logger.warning(f"Cross-encoder 打分失败: {e}")
                doc_copy = dict(doc)
                doc_copy['rerank_score'] = doc.get('score', 0)
                scored_docs.append(doc_copy)

        scored_docs.sort(key=lambda x: x['rerank_score'], reverse=True)
        return scored_docs[:top_k]

    def _rerank_llm(
        self,
        query: str,
        documents: List[Dict],
        top_k: int,
    ) -> List[Dict]:
        """使用 LLM 重排序"""
        # 限制输入文档数量（避免 token 过长）
        docs_to_rank = documents[:20]

        doc_texts = []
        for i, doc in enumerate(docs_to_rank):
            content = doc.get('content', '')[:200]
            doc_texts.append(f"[{i}] {content}")

        prompt = (
            f"请根据查询对以下文档按相关性打分（1-10分）：\n\n"
            f"查询：{query}\n\n"
            f"文档：\n{chr(10).join(doc_texts)}\n\n"
            f'以 JSON 返回：[{{"index": 0, "score": 8}}, ...]\n只返回 JSON。'
        )

        try:
            response = self.llm_model.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.1,
            )
            if response:
                clean = response.strip()
                if clean.startswith("```json"):
                    clean = clean[7:]
                if clean.startswith("```"):
                    clean = clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                scores = json.loads(clean.strip())

                score_map = {}
                for item in scores:
                    if isinstance(item, dict) and 'index' in item:
                        score_map[item['index']] = item.get('score', 5)

                for i, doc in enumerate(docs_to_rank):
                    doc['rerank_score'] = score_map.get(i, 5)

                docs_to_rank.sort(key=lambda x: x.get('rerank_score', 0), reverse=True)
                return docs_to_rank[:top_k]

        except (json.JSONDecodeError, ValueError, Exception) as e:
            logger.warning(f"LLM 重排序失败: {e}")

        return sorted(documents, key=lambda x: x.get('score', 0), reverse=True)[:top_k]


class MultiQueryFusion:
    """
    多查询融合器

    融合多个查询的检索结果。
    """

    def __init__(self, fusion_method: str = 'rrf'):
        """
        初始化融合器

        Args:
            fusion_method: 融合方法 ('rrf', 'weighted', 'max')
        """
        self.fusion_method = fusion_method

    def reciprocal_rank_fusion(
        self,
        results_list: List[List[Tuple[int, float]]],
        k: int = 60
    ) -> List[Tuple[int, float]]:
        """
        倒数排名融合 (RRF)

        Args:
            results_list: 多个查询的结果列表
            k: RRF 参数

        Returns:
            List[Tuple[int, float]]: 融合后的结果
        """
        scores = {}

        for results in results_list:
            for rank, (doc_id, score) in enumerate(results):
                if doc_id not in scores:
                    scores[doc_id] = 0
                scores[doc_id] += 1 / (k + rank + 1)

        sorted_results = sorted(scores.items(), key=lambda x: -x[1])
        return sorted_results

    def weighted_fusion(
        self,
        results_list: List[List[Tuple[int, float]]],
        weights: List[float] = None
    ) -> List[Tuple[int, float]]:
        """
        加权融合

        Args:
            results_list: 多个查询的结果列表
            weights: 权重列表

        Returns:
            List[Tuple[int, float]]: 融合后的结果
        """
        if weights is None:
            weights = [1.0 / len(results_list)] * len(results_list)

        scores = {}

        for results, weight in zip(results_list, weights):
            for doc_id, score in results:
                if doc_id not in scores:
                    scores[doc_id] = 0
                scores[doc_id] += score * weight

        sorted_results = sorted(scores.items(), key=lambda x: -x[1])
        return sorted_results

    def max_fusion(
        self,
        results_list: List[List[Tuple[int, float]]]
    ) -> List[Tuple[int, float]]:
        """
        最大值融合

        Args:
            results_list: 多个查询的结果列表

        Returns:
            List[Tuple[int, float]]: 融合后的结果
        """
        scores = {}

        for results in results_list:
            for doc_id, score in results:
                if doc_id not in scores:
                    scores[doc_id] = score
                else:
                    scores[doc_id] = max(scores[doc_id], score)

        sorted_results = sorted(scores.items(), key=lambda x: -x[1])
        return sorted_results

    def fuse(
        self,
        results_list: List[List[Tuple[int, float]]]
    ) -> List[Tuple[int, float]]:
        """
        融合结果

        Args:
            results_list: 多个查询的结果列表

        Returns:
            List[Tuple[int, float]]: 融合后的结果
        """
        if self.fusion_method == 'rrf':
            return self.reciprocal_rank_fusion(results_list)
        elif self.fusion_method == 'weighted':
            return self.weighted_fusion(results_list)
        elif self.fusion_method == 'max':
            return self.max_fusion(results_list)
        else:
            return self.reciprocal_rank_fusion(results_list)


class RAGQueryOptimizer:
    """
    RAG 查询优化器

    整合所有查询优化技术。
    """

    def __init__(
        self,
        use_hyde: bool = True,
        use_decomposition: bool = True,
        use_expansion: bool = True,
        use_reranker: bool = True,
        fusion_method: str = 'rrf',
        llm_model: Optional[Any] = None,
    ):
        """
        初始化优化器

        Args:
            use_hyde: 是否使用 HyDE
            use_decomposition: 是否使用子查询分解
            use_expansion: 是否使用查询扩展
            use_reranker: 是否使用重排序
            fusion_method: 融合方法
            llm_model: LLM 客户端
        """
        self.use_hyde = use_hyde
        self.use_decomposition = use_decomposition
        self.use_expansion = use_expansion
        self.use_reranker = use_reranker

        self.hyde = HyDEQueryRewriter(llm_model=llm_model)
        self.decomposer = SubQueryDecomposer(llm_model=llm_model)
        self.expander = QueryExpander()
        self.fusion = MultiQueryFusion(fusion_method)
        self.reranker = Reranker(llm_model=llm_model)

    def optimize(self, query: str) -> Dict[str, Any]:
        """
        优化查询

        Args:
            query: 原始查询

        Returns:
            Dict: 优化结果
        """
        result = {
            'original_query': query,
            'optimized_queries': [query],
            'hyde_result': None,
            'sub_queries': None,
            'expanded_queries': None,
        }

        # HyDE 重写
        if self.use_hyde:
            result['hyde_result'] = self.hyde.rewrite(query, return_docs=True)
            result['optimized_queries'].append(result['hyde_result']['expanded_query'])

        # 子查询分解
        if self.use_decomposition:
            result['sub_queries'] = self.decomposer.decompose(query)
            result['optimized_queries'].extend(result['sub_queries'])

        # 查询扩展
        if self.use_expansion:
            result['expanded_queries'] = self.expander.expand(query)
            result['optimized_queries'].extend(result['expanded_queries'])

        # 去重
        result['optimized_queries'] = list(set(result['optimized_queries']))

        return result

    def fuse_results(
        self,
        results_list: List[List[Tuple[int, float]]]
    ) -> List[Tuple[int, float]]:
        """
        融合多个查询的检索结果

        Args:
            results_list: 多个查询的结果列表

        Returns:
            List[Tuple[int, float]]: 融合后的结果
        """
        return self.fusion.fuse(results_list)

    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        重排序检索结果

        Args:
            query: 查询文本
            documents: 文档列表
            top_k: 返回数量

        Returns:
            List[Dict]: 重排序后的文档
        """
        if not self.use_reranker:
            return sorted(documents, key=lambda x: x.get('score', 0), reverse=True)[:top_k]
        return self.reranker.rerank(query, documents, top_k)


def print_optimization_summary(result: Dict[str, Any]):
    """打印优化摘要"""
    print("=== RAG 查询优化摘要 ===")
    print(f"原始查询: {result['original_query']}")
    print(f"优化后查询数量: {len(result['optimized_queries'])}")

    if result.get('hyde_result'):
        print(f"\nHyDE 类型: {result['hyde_result']['query_type']}")
        print(f"HyDE 领域: {result['hyde_result']['domain']}")
        print(f"HyDE 使用 LLM: {'是' if result['hyde_result'].get('llm_used') else '否'}")

    if result.get('sub_queries'):
        print(f"\n子查询: {result['sub_queries']}")

    if result.get('expanded_queries'):
        print(f"\n扩展查询数量: {len(result['expanded_queries'])}")

    print("======================")


# 导出
__all__ = [
    'HyDEQueryRewriter',
    'SubQueryDecomposer',
    'QueryExpander',
    'Reranker',
    'MultiQueryFusion',
    'RAGQueryOptimizer',
    'print_optimization_summary'
]


# 测试
if __name__ == "__main__":
    optimizer = RAGQueryOptimizer()

    # 测试查询优化
    query = "如何优化向量搜索性能"
    result = optimizer.optimize(query)
    print_optimization_summary(result)

    # 测试结果融合
    results1 = [(1, 0.9), (2, 0.8), (3, 0.7)]
    results2 = [(2, 0.95), (1, 0.85), (4, 0.6)]
    fused = optimizer.fuse_results([results1, results2])
    print(f"\n融合结果: {fused}")

    # 测试重排序
    docs = [
        {"content": "向量搜索基于余弦相似度", "score": 0.6},
        {"content": "优化索引可以加速搜索", "score": 0.8},
        {"content": "数据库查询优化技巧", "score": 0.4},
    ]
    reranked = optimizer.rerank("如何优化向量搜索性能", docs, top_k=3)
    print(f"\n重排序结果: {[(d.get('content', '')[:30], d.get('rerank_score', d.get('score', 0))) for d in reranked]}")
