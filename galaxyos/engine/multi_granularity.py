#!/usr/bin/env python3
"""
multi_granularity.py — MemGAS multi-granularity + GMM association

MemGAS 多粒度表示提取和 GMM 聚类关联。

MultiGranularityExtractor:
  - 从文本提取 4 种粒度表示
    - session_level: 完整文本（或前 2000 字符）
    - turn_level: 按句/段切分
    - summary_level: jieba 抽取式摘要（top 句子 + 关键词）
    - keyword_level: jieba TF-IDF 关键词

GMMAssociator:
  - 基于 sklearn GaussianMixture 聚类
  - fit on memory assets, predict accept/reject sets
  - 新资产关联 accept_set 记忆
  - 构建/更新关联图边
"""

import re
import math
import time
import logging
import threading
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ============================================================
# MultiGranularityExtractor
# ============================================================

class MultiGranularityExtractor:
    """
    多粒度表示提取器

    从一段文本提取 4 种粒度的表示：
    - session_level: 完整文本（或前 2000 字符）
    - turn_level: 按句/段切分
    - summary_level: jieba 抽取式摘要（top 句子 + 关键词）
    - keyword_level: jieba TF-IDF 关键词
    """

    def __init__(self, max_session_chars: int = 2000, max_summary_sentences: int = 5,
                 top_keywords: int = 15, min_keyword_len: int = 2):
        self.max_session_chars = max_session_chars
        self.max_summary_sentences = max_summary_sentences
        self.top_keywords = top_keywords
        self.min_keyword_len = min_keyword_len

    def extract(self, text: str) -> Dict[str, Any]:
        """
        从文本提取多粒度表示

        Args:
            text: 原始文本（不限长度）

        Returns:
            {
                "session_level": str,
                "turn_level": List[str],
                "summary_level": {"sentences": List[str], "keywords": List[str]},
                "keyword_level": List[str],
            }
        """
        if not text or not text.strip():
            return self._empty_result()

        result = {
            "session_level": self._extract_session(text),
            "turn_level": self._extract_turns(text),
            "summary_level": self._extract_summary(text),
            "keyword_level": self._extract_keywords(text),
        }
        return result

    def _empty_result(self) -> Dict[str, Any]:
        return {
            "session_level": "",
            "turn_level": [],
            "summary_level": {"sentences": [], "keywords": []},
            "keyword_level": [],
        }

    def _extract_session(self, text: str) -> str:
        """session_level: 完整文本，截断到 max_session_chars"""
        return text[:self.max_session_chars]

    def _extract_turns(self, text: str) -> List[str]:
        """
        turn_level: 按句号/问号/感叹号/换行分割成句块

        每句作为一个 turn（短句合并到前一句）
        """
        # 分句
        raw_sentences = re.split(r'[。！？\n!?\n]+', text)
        sentences = [s.strip() for s in raw_sentences if s.strip()]

        # 合并过短的句子（< 4 字符）
        merged = []
        for s in sentences:
            if len(s) < 4 and merged:
                merged[-1] += s
            else:
                merged.append(s)

        return merged if merged else [text[:500]]

    def _extract_summary(self, text: str) -> Dict[str, List[str]]:
        """
        summary_level: jieba 抽取式摘要

        策略：
        1. 分词计算 TF
        2. 对每个句子计算关键词密度评分
        3. 取 top N 句子
        4. 用 jieba.analyse.extract_tags 提取关键词
        """
        try:
            import jieba
            import jieba.analyse
        except ImportError:
            # 无 jieba：取头尾句 + 高频词
            return self._fallback_summary(text)

        # 分句
        raw_sentences = re.split(r'[。！？\n!?\n]+', text)
        sentences = [s.strip() for s in raw_sentences if s.strip() and len(s.strip()) >= 4]
        if not sentences:
            return {"sentences": [], "keywords": []}

        # 全文分词
        full_words = list(jieba.cut(text[:3000]))
        word_freq: Dict[str, int] = {}
        for w in full_words:
            if len(w) >= self.min_keyword_len:
                word_freq[w] = word_freq.get(w, 0) + 1

        max_freq = max(word_freq.values()) if word_freq else 1

        # 对每个句子评分（关键词密度 + 位置）
        scored_sentences = []
        for i, sent in enumerate(sentences):
            if len(sent) < 4:
                continue
            sent_words = list(jieba.cut(sent))
            kw_count = sum(1 for w in sent_words if len(w) >= self.min_keyword_len and
                          word_freq.get(w, 0) >= 2)
            # 位置权重：前 20% 句子加权
            pos_weight = 1.5 if i < len(sentences) * 0.2 else 1.0
            # 长度惩罚：太短的句子减分
            len_penalty = min(1.0, len(sent) / 50)
            score = (kw_count / max(len(sent_words), 1)) * pos_weight * len_penalty
            scored_sentences.append((score, sent))

        # 取 top N 句子
        scored_sentences.sort(key=lambda x: -x[0])
        top_sentences = [s for _, s in scored_sentences[:self.max_summary_sentences]]

        # 关键词提取
        try:
            keywords = jieba.analyse.extract_tags(
                text[:3000], topK=self.top_keywords
            )
        except Exception:
            keywords = self._simple_keywords(text)

        return {
            "sentences": top_sentences,
            "keywords": keywords,
        }

    def _fallback_summary(self, text: str) -> Dict[str, List[str]]:
        """无 jieba 时的降级摘要"""
        raw = re.split(r'[。！？\n!?\n]+', text)
        sentences = [s.strip() for s in raw if s.strip() and len(s.strip()) >= 4]
        if not sentences:
            return {"sentences": [], "keywords": []}

        # 取头 2 句 + 尾 1 句
        top = sentences[:2]
        if len(sentences) > 3:
            top.append(sentences[-1])

        return {
            "sentences": top,
            "keywords": self._simple_keywords(text),
        }

    def _simple_keywords(self, text: str) -> List[str]:
        """简易关键词提取：高频词"""
        words = re.findall(r'[\u4e00-\u9fff\w]{2,}', text[:3000])
        freq: Dict[str, int] = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        sorted_words = sorted(freq.items(), key=lambda x: -x[1])
        return [w for w, _ in sorted_words[:self.top_keywords]]

    def _extract_keywords(self, text: str) -> List[str]:
        """
        keyword_level: jieba TF-IDF 关键词

        优先 jieba.analyse.extract_tags，降级到高频词
        """
        try:
            import jieba.analyse
            keywords = jieba.analyse.extract_tags(
                text[:3000], topK=self.top_keywords
            )
            return keywords
        except ImportError:
            return self._simple_keywords(text)

    def extract_batch(self, texts: List[str]) -> List[Dict[str, Any]]:
        """批量提取"""
        return [self.extract(t) for t in texts]


# ============================================================
# GMMAssociator
# ============================================================

class GMMAssociator:
    """
    GMM 聚类关联器

    基于 sklearn GaussianMixture 对记忆资产进行聚类，
    建立 accept/reject 集合，并维护关联图边。

    核心流程：
    1. fit(memory_texts): 聚类得到 accept_set / reject_set
    2. associate(new_text): 预测新文本的簇，与 accept_set 中记忆建立关联
    3. build_graph(): 构建/更新关联图边
    """

    def __init__(
        self,
        n_components: int = 5,
        random_state: int = 42,
        min_accept_score: float = 0.3,
        max_reject_score: float = -0.2,
    ):
        self.n_components = n_components
        self.random_state = random_state
        self.min_accept_score = min_accept_score
        self.max_reject_score = max_reject_score

        self._gmm = None
        self._vectorizer = None  # TF-IDF vectorizer

        # 聚类结果
        self._cluster_labels: List[int] = []
        self._accept_indices: Set[int] = set()
        self._reject_indices: Set[int] = set()

        # 关联图: {text_id -> {related_text_id: weight}}
        self._association_graph: Dict[str, Dict[str, float]] = {}

        # 原始数据
        self._texts: List[str] = []
        self._text_ids: List[str] = []

        # 特征矩阵 (n_samples, n_features)
        self._features = None

        self._lock = threading.Lock()
        self._is_fitted = False

    def fit(self, texts: List[str], text_ids: Optional[List[str]] = None) -> bool:
        """
        对记忆文本进行 GMM 聚类，划分 accept/reject 集合

        Args:
            texts: 记忆文本列表
            text_ids: 文本 ID 列表（可选，默认用索引字符串）

        Returns:
            是否成功
        """
        if not texts or len(texts) < 3:
            logger.warning("GMMAssociator.fit: need at least 3 texts")
            return False

        with self._lock:
            self._texts = texts
            self._text_ids = text_ids or [str(i) for i in range(len(texts))]

            try:
                from sklearn.feature_extraction.text import TfidfVectorizer
                from sklearn.mixture import GaussianMixture
                import numpy as np

                # TF-IDF 向量化
                self._vectorizer = TfidfVectorizer(
                    max_features=500,
                    stop_words=None,
                    analyzer='char_wb',
                    ngram_range=(1, 3),
                )
                self._features = self._vectorizer.fit_transform(texts)
                X = self._features.toarray()

                # GMM 聚类
                actual_n = min(self.n_components, max(2, len(texts) // 2))
                self._gmm = GaussianMixture(
                    n_components=actual_n,
                    random_state=self.random_state,
                    max_iter=200,
                    n_init=5,
                )
                self._cluster_labels = self._gmm.fit_predict(X).tolist()

                # 计算每个样本的密度分数
                scores = self._gmm.score_samples(X)  # log-likelihood

                # 划分 accept/reject
                self._accept_indices = set()
                self._reject_indices = set()

                for i, score in enumerate(scores):
                    if score >= self.min_accept_score:
                        self._accept_indices.add(i)
                    elif score <= self.max_reject_score:
                        self._reject_indices.add(i)

                # 如果没有 accept，取 top 20% 作为 accept
                if not self._accept_indices and len(scores) > 0:
                    threshold = sorted(scores, reverse=True)[
                        max(0, len(scores) // 5 - 1)
                    ]
                    for i, score in enumerate(scores):
                        if score >= threshold:
                            self._accept_indices.add(i)

                self._is_fitted = True
                logger.info(
                    f"GMMAssociator: fitted {len(texts)} texts, "
                    f"{len(self._accept_indices)} accept, "
                    f"{len(self._reject_indices)} reject, "
                    f"{actual_n} clusters"
                )
                return True

            except ImportError as e:
                logger.warning(f"GMMAssociator.fit: sklearn not available ({e}), "
                              f"using fallback KMeans")
                return self._fallback_fit(texts, text_ids)

            except Exception as e:
                logger.error(f"GMMAssociator.fit failed: {e}")
                return False

    def _fallback_fit(self, texts: List[str], text_ids: Optional[List[str]] = None) -> bool:
        """
        降级：无 sklearn 时使用 KMeans (sklearn.cluster)
        或简单相似度聚类
        """
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.cluster import KMeans
            import numpy as np

            self._vectorizer = TfidfVectorizer(
                max_features=500, analyzer='char_wb', ngram_range=(1, 3),
            )
            self._features = self._vectorizer.fit_transform(texts)
            X = self._features.toarray()

            n_clusters = min(self.n_components, max(2, len(texts) // 2))
            km = KMeans(n_clusters=n_clusters, random_state=self.random_state, n_init=5)
            self._cluster_labels = km.fit_predict(X).tolist()

            # 用 inertia 近似密度
            distances = km.transform(X).min(axis=1)
            max_dist = max(distances) if len(distances) > 0 else 1.0
            norm_scores = [1.0 - d / max_dist for d in distances]

            self._accept_indices = set()
            self._reject_indices = set()
            for i, s in enumerate(norm_scores):
                if s >= self.min_accept_score:
                    self._accept_indices.add(i)
                elif s <= self.max_reject_score:
                    self._reject_indices.add(i)

            if not self._accept_indices:
                threshold = sorted(norm_scores, reverse=True)[
                    max(0, len(norm_scores) // 5 - 1)
                ]
                for i, s in enumerate(norm_scores):
                    if s >= threshold:
                        self._accept_indices.add(i)

            self._is_fitted = True
            logger.info(
                f"GMMAssociator(fallback KMeans): fitted {len(texts)} texts, "
                f"{len(self._accept_indices)} accept"
            )
            return True

        except ImportError:
            logger.warning("GMMAssociator: sklearn completely unavailable, "
                          "using jieba similarity fallback")
            return self._fallback_similarity(texts, text_ids)

        except Exception as e:
            logger.error(f"GMMAssociator fallback fit failed: {e}")
            return False

    def _fallback_similarity(self, texts: List[str],
                              text_ids: Optional[List[str]] = None) -> bool:
        """
        终极降级：jieba 关键词重叠相似度聚类

        对文本两两计算 Jaccard 相似度，用阈值聚类
        """
        try:
            import jieba
            tokenized = []
            for t in texts:
                words = set(jieba.lcut(t[:500].lower()))
                words = {w for w in words if len(w) >= 2}
                tokenized.append(words)
        except ImportError:
            tokenized = [
                set(re.findall(r'[\w\u4e00-\u9fff]{2,}', t[:500].lower()))
                for t in texts
            ]

        self._texts = texts
        self._text_ids = text_ids or [str(i) for i in range(len(texts))]

        # 简单分配：每个文本单独一个簇（默认 accept）
        self._cluster_labels = list(range(len(texts)))
        self._accept_indices = set(range(len(texts)))
        self._is_fitted = True

        logger.info(
            f"GMMAssociator(fallback similarity): {len(texts)} texts, "
            f"all assigned as accept"
        )
        return True

    def associate(self, new_text: str, new_text_id: str = "") -> List[Tuple[str, str, float]]:
        """
        关联新文本到已训练的 accept_set 记忆

        Args:
            new_text: 新文本
            new_text_id: 新文本 ID（可选）

        Returns:
            [(related_text_id, relation, weight), ...]
            关联边列表
        """
        if not self._is_fitted or not self._texts:
            return []

        with self._lock:
            try:
                return self._associate_sklearn(new_text, new_text_id)
            except Exception:
                return self._associate_fallback(new_text, new_text_id)

    def _associate_sklearn(self, new_text: str, new_text_id: str) -> List[Tuple[str, str, float]]:
        """sklearn 路径：用 GMM predict + TF-IDF cosine 关联"""
        import numpy as np

        if self._gmm is None or self._vectorizer is None or self._features is None:
            return self._associate_fallback(new_text, new_text_id)

        new_vec = self._vectorizer.transform([new_text])
        new_X = new_vec.toarray()

        # 预测簇
        cluster = self._gmm.predict(new_X)[0]

        # 找同一簇的 accept 记忆
        accept_in_cluster = [
            i for i in self._accept_indices
            if self._cluster_labels[i] == cluster
        ]

        if not accept_in_cluster:
            # 关联最近的非 reject 记忆
            accept_in_cluster = [
                i for i in range(len(self._texts))
                if i not in self._reject_indices and
                self._cluster_labels[i] == cluster
            ]

        if not accept_in_cluster:
            return []

        # cosine 相似度计算
        new_norm = new_X / (np.linalg.norm(new_X, axis=1, keepdims=True) + 1e-10)
        old_features = self._features.toarray()
        old_norm = old_features / (np.linalg.norm(old_features, axis=1, keepdims=True) + 1e-10)

        results = []
        for idx in accept_in_cluster:
            cosine = float(np.dot(new_norm[0], old_norm[idx]))
            if cosine > 0.1:
                relation = "similar_to" if cosine > 0.5 else "weakly_related"
                weight = round(cosine, 4)
                results.append((self._text_ids[idx], relation, weight))

        results.sort(key=lambda x: -x[2])
        return results[:5]

    def _associate_fallback(self, new_text: str, new_text_id: str) -> List[Tuple[str, str, float]]:
        """降级：jieba 关键词相似度"""
        try:
            import jieba
            new_words = set(jieba.lcut(new_text[:500].lower()))
        except ImportError:
            new_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', new_text[:500].lower()))

        new_words = {w for w in new_words if len(w) >= 2}
        if not new_words:
            return []

        results = []
        for i in self._accept_indices:
            if i >= len(self._texts):
                continue
            old_text = self._texts[i]
            try:
                import jieba
                old_words = set(jieba.lcut(old_text[:500].lower()))
            except ImportError:
                old_words = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', old_text[:500].lower()))
            old_words = {w for w in old_words if len(w) >= 2}

            if not old_words:
                continue

            overlap = len(new_words & old_words)
            if overlap >= 1:
                jaccard = overlap / len(new_words | old_words)
                if jaccard > 0.05:
                    relation = "similar_to" if jaccard > 0.3 else "weakly_related"
                    results.append((self._text_ids[i], relation, round(jaccard, 4)))

        results.sort(key=lambda x: -x[2])
        return results[:5]

    def build_graph(self, registry=None) -> List[Tuple[str, str, str, float]]:
        """
        构建/更新关联图边（同一簇内两两建立关联）

        Args:
            registry: AssetRegistry 实例（可选，用于注册边）

        Returns:
            [(source_id, target_id, relation, weight), ...]
        """
        edges = []
        with self._lock:
            if not self._is_fitted or not self._cluster_labels:
                return edges

            from collections import defaultdict
            cluster_members: Dict[int, List[int]] = defaultdict(list)
            for i, label in enumerate(self._cluster_labels):
                cluster_members[label].append(i)

            for label, members in cluster_members.items():
                if len(members) < 2:
                    continue
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        src = members[i]
                        dst = members[j]
                        if src in self._reject_indices or dst in self._reject_indices:
                            continue

                        src_id = self._text_ids[src] if src < len(self._text_ids) else str(src)
                        dst_id = self._text_ids[dst] if dst < len(self._text_ids) else str(dst)
                        # 估算权重（基于簇大小和密度）
                        n_members = len(members)
                        weight = round(1.0 / math.sqrt(n_members), 4) if n_members > 0 else 0.5

                        edges.append((src_id, dst_id, "cluster_coherent", weight))

                        if registry:
                            from knowledge_asset import AssociationEdge
                            registry.add_edge(src_id, AssociationEdge(
                                target_asset_id=dst_id,
                                relation="cluster_coherent",
                                weight=weight,
                                metadata={"gmm_cluster": int(label)},
                            ))

        return edges

    def cluster_info(self) -> Dict[str, Any]:
        """获取聚类信息"""
        with self._lock:
            if not self._is_fitted:
                return {"fitted": False}

            from collections import Counter
            cluster_counts = Counter(self._cluster_labels)
            return {
                "fitted": True,
                "n_clusters": len(cluster_counts),
                "n_samples": len(self._cluster_labels),
                "n_accept": len(self._accept_indices),
                "n_reject": len(self._reject_indices),
                "cluster_sizes": dict(cluster_counts),
            }

    def update(self, new_texts: List[str], new_ids: Optional[List[str]] = None) -> bool:
        """
        增量更新：追加新数据并重新 fit

        Args:
            new_texts: 新记忆文本
            new_ids: 新 ID 列表
        """
        with self._lock:
            self._texts.extend(new_texts)
            if new_ids:
                self._text_ids.extend(new_ids)
            else:
                self._text_ids.extend(
                    str(i) for i in range(len(self._text_ids),
                                           len(self._text_ids) + len(new_texts))
                )
        return self.fit(self._texts, self._text_ids)

    def get_association_graph(self) -> Dict[str, Dict[str, float]]:
        """获取关联图"""
        return dict(self._association_graph)


# ══════════════════════════════════════════════════
# 组合管道
# ══════════════════════════════════════════════════

@dataclass
class GranularityPipeline:
    """
    多粒度 + GMM 关联组合管道

    典型的使用流程：
    1. extractor 提取每一段文本的多粒度表示
    2. associator 对所有记忆做 GMM 聚类
    3. 提取关联图边
    4. 将结果存入 KnowledgeAsset
    """

    extractor: MultiGranularityExtractor = field(
        default_factory=MultiGranularityExtractor
    )
    associator: GMMAssociator = field(
        default_factory=lambda: GMMAssociator(n_components=5)
    )

    def process(self, texts: List[str], text_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        完整处理管道

        Returns:
            {
                "granularities": {text_id: multi_granularity_dict},
                "clusters": cluster_info,
                "edges": [(src, dst, relation, weight), ...],
            }
        """
        if not texts:
            return {"granularities": {}, "clusters": {}, "edges": []}

        # 1. 多粒度提取
        granularities = {}
        for i, text in enumerate(texts):
            t_id = text_ids[i] if text_ids and i < len(text_ids) else str(i)
            granularities[t_id] = self.extractor.extract(text)

        # 2. GMM 聚类
        fit_ok = self.associator.fit(texts, text_ids)
        if not fit_ok:
            return {
                "granularities": granularities,
                "clusters": {"fitted": False},
                "edges": [],
            }

        # 3. 关联图边
        edges = self.associator.build_graph()
        cluster_info = self.associator.cluster_info()

        return {
            "granularities": granularities,
            "clusters": cluster_info,
            "edges": edges,
        }


# ── 测试入口 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    extractor = MultiGranularityExtractor()

    texts = [
        "用户偏好中文表达和七情六欲风格，避免 AI 味。这是小艺 Claw 的人格设定规则。",
        "记忆系统支持混合检索：KG + DAG + synapse + paper 五路并行，RRF 融合排序。",
        "系统架构共 16 层，包含 4 个自研插件，以 claw-core 为底层核心。",
        "用户要求 AI 在复杂问题先调 intelligent_thinking_trigger 再组合输出。",
    ]

    for i, text in enumerate(texts):
        mg = extractor.extract(text)
        print(f"\n── Text {i} ──")
        print(f"  session: {mg['session_level'][:60]}...")
        print(f"  turns: {len(mg['turn_level'])} chunks")
        print(f"  summary_sentences: {len(mg['summary_level']['sentences'])}")
        print(f"  keywords: {mg['keyword_level'][:5]}")

    pipe = GranularityPipeline()
    result = pipe.process(texts)
    print("\n── Pipeline ──")
    print(f"  Clusters: {result['clusters']}")
    print(f"  Edges: {len(result['edges'])}")
    for e in result['edges'][:5]:
        print(f"    {e[0]} --{e[2]}--> {e[1]} (w={e[3]})")
