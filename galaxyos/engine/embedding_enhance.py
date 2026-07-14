#!/usr/bin/env python3
"""
Embedding 增强模块 — 利用 bge-m3 (1024维) 扩展能力

激活 6 个之前未使用的能力：
1. 记忆去重 — 检测语义重复的记忆，自动标记合并
2. 异常检测 — 找出置信度/情绪偏离正常范围的记忆
3. 主题聚类 — 把相似记忆自动归类成主题
4. 查询扩展 — embedding 语义扩展搜索关键词
5. 代表性抽取 — 一堆相似结果里挑最有代表性的
6. 质量评分 — 召回结果与 query 的语义匹配度打分

使用方法：
    from embedding_enhance import EmbeddingEnhancer
from galaxyos.shared.paths import workspace
    ee = EmbeddingEnhancer()
    duplicates = ee.find_duplicates(memories)
"""

import numpy as np
import logging
from typing import List, Dict, Optional, Tuple, Set, Any
from collections import defaultdict
from galaxyos.shared.paths import workspace

logger = logging.getLogger("embedding_enhance")


class EmbeddingEnhancer:
    """Embedding 增强能力"""

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化 Embedding 增强器

        Args:
            config_path: llm_config.json 路径，None 则自动查找
        """
        self._embedding_client = None
        self._vector_store = None
        self._load_config(config_path)

    def _load_config(self, config_path: Optional[str] = None):
        """加载配置并初始化客户端"""
        import json
        import os

        if config_path is None:
            # 自动查找
            candidates = [
                "skills/llm-memory-integration/config/llm_config.json",
                "config/llm_config.json",
                "config.json",
            ]
            ws = os.environ.get("OPENCLAW_WORKSPACE",
                                 workspace())
            for rel in candidates:
                p = os.path.join(ws, rel)
                if os.path.exists(p):
                    config_path = p
                    break

        if config_path and os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
            emb_cfg = cfg.get("embedding", {})
            self._embed_config = emb_cfg
            # 先尝试从已有的 EmbeddingClient 获取
            try:
                from semantic_cache import EmbeddingClient
                self._embedding_client = EmbeddingClient(
                    base_url=emb_cfg.get("base_url", "https://cloud.infini-ai.com/maas/v1"),
                    api_key=emb_cfg.get("api_key", ""),
                    model=emb_cfg.get("model", "bge-m3"),
                    dimensions=emb_cfg.get("dimensions", 4096),
                )
            except ImportError:
                self._embedding_client = None
        else:
            self._embed_config = {}

    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """获取 embedding 向量"""
        if not self._embedding_client:
            return None
        return self._embedding_client.embed(text)

    def _get_embeddings_batch(self, texts: List[str]) -> List[Optional[np.ndarray]]:
        """批量获取 embedding"""
        if not self._embedding_client:
            return [None] * len(texts)
        return self._embedding_client.batch_embed(texts)

    def available(self) -> bool:
        """检查是否可用"""
        return self._embedding_client is not None

    # ==================== 1. 记忆去重 ====================

    def find_duplicates(self, memories: List[Dict],
                        threshold: float = 0.92) -> List[Dict]:
        """
        检测语义重复的记忆

        Args:
            memories: 记忆列表，每项需包含 'content' 字段
            threshold: 相似度阈值，高于此值视为重复（默认 0.92）

        Returns:
            [{'source_idx': i, 'target_idx': j, 'similarity': s}, ...]
        """
        if not self.available() or len(memories) < 2:
            return []

        texts = [m.get("content", "") for m in memories]
        vectors = self._get_embeddings_batch(texts)
        vectors = [v for v in vectors if v is not None]

        if len(vectors) < 2:
            return []

        vec_arr = np.array(vectors)
        norm = vec_arr / (np.linalg.norm(vec_arr, axis=1, keepdims=True) + 1e-10)
        sim_matrix = np.dot(norm, norm.T)

        duplicates = []
        seen: Set[str] = set()
        for i in range(len(vectors)):
            for j in range(i + 1, len(vectors)):
                if sim_matrix[i][j] > threshold:
                    key = f"{min(i,j)}-{max(i,j)}"
                    if key not in seen:
                        seen.add(key)
                        duplicates.append({
                            "source_idx": i,
                            "target_idx": j,
                            "similarity": float(sim_matrix[i][j]),
                            "source_content": texts[i][:80],
                            "target_content": texts[j][:80],
                        })
        return duplicates

    def deduplicate_memories(self, memories: List[Dict],
                              threshold: float = 0.92,
                              strategy: str = "keep_first") -> List[Dict]:
        """
        去重并返回去重后的记忆列表

        Args:
            memories: 记忆列表
            threshold: 相似度阈值
            strategy: 去重策略，'keep_first' 保留第一条，'merge' 合并

        Returns:
            去重后的记忆列表
        """
        duplicates = self.find_duplicates(memories, threshold)
        if not duplicates:
            return memories

        to_remove: Set[int] = set()
        for dup in duplicates:
            if strategy == "keep_first":
                to_remove.add(dup["target_idx"])
            else:
                # merge: 标记源到目标
                source = dup["source_idx"]
                target = dup["target_idx"]
                if source < len(memories) and target < len(memories):
                    src_content = memories[source].get("content", "")
                    tgt_content = memories[target].get("content", "")
                    # 合并内容
                    if len(tgt_content) > len(src_content):
                        to_remove.add(source)
                    else:
                        to_remove.add(target)

        return [m for i, m in enumerate(memories) if i not in to_remove]

    # ==================== 2. 异常检测 ====================

    def detect_anomalies(self, memories: List[Dict]) -> List[Dict]:
        """
        检测异常记忆（置信度异常、情绪异常、内容嵌入离群）

        Args:
            memories: 记忆列表，每项需包含 'confidence' 字段

        Returns:
            [{'idx': i, 'type': 'confidence_low'|'embedding_outlier', ...}, ...]
        """
        anomalies = []

        # 置信度异常检测
        confidences = [m.get("confidence", 0.5) for m in memories]
        if confidences:
            conf_arr = np.array(confidences)
            mean = np.mean(conf_arr)
            std = np.std(conf_arr)
            for i, c in enumerate(confidences):
                reasons = []
                # 置信度极低
                if c < 0.1:
                    reasons.append("confidence_too_low")
                # 置信度超过均值-2.5σ
                if std > 0.01 and c < mean - 2.5 * std:
                    reasons.append("confidence_statistical_outlier")
                if reasons:
                    anomalies.append({
                        "idx": i,
                        "type": "confidence_anomaly",
                        "reasons": reasons,
                        "value": c,
                        "mean": float(mean),
                        "content_preview": str(memories[i].get("content", ""))[:60],
                    })

        # Embedding 离群检测
        texts = [m.get("content", "") for m in memories]
        vectors = self._get_embeddings_batch(texts)
        vectors = [v for v in vectors if v is not None]
        if len(vectors) >= 10:
            vec_arr = np.array(vectors)
            center = np.mean(vec_arr, axis=0)
            center_norm = center / (np.linalg.norm(center) + 1e-10)
            distances = []
            for idx, v in enumerate(vectors):
                # 只对没有置信度异常的记忆检测
                if any(a["idx"] == idx for a in anomalies):
                    continue
                assert v is not None
                v_norm = v / (np.linalg.norm(v) + 1e-10)
                cos_sim = float(np.dot(v_norm, center_norm))
                distances.append((idx, cos_sim))
            distances.sort(key=lambda x: x[1])
            # 底部 5% 视为离群
            outlier_count = max(1, len(distances) // 20)
            for idx, sim in distances[:outlier_count]:
                if sim < 0.5:
                    anomalies.append({
                        "idx": idx,
                        "type": "embedding_outlier",
                        "center_similarity": round(sim, 3),
                        "content_preview": str(memories[idx].get("content", ""))[:60],
                    })

        return anomalies

    # ==================== 3. 主题聚类 ====================

    def cluster_by_content(self, memories: List[Dict],
                           n_clusters: Optional[int] = None,
                           max_clusters: int = 10) -> List[Dict]:
        """
        基于 embedding 进行 KMeans 主题聚类

        Args:
            memories: 记忆列表
            n_clusters: 聚类数，None 则自动估算
            max_clusters: 最大聚类数

        Returns:
            [{'cluster_id': 0, 'label': '主题标签', 'items': [...], 'size': N}, ...]
        """
        if not self.available() or len(memories) < 3:
            return [{"cluster_id": 0, "label": "all", "items": memories, "size": len(memories)}]

        texts = [m.get("content", "") for m in memories]
        vectors = self._get_embeddings_batch(texts)
        vectors = [v for v in vectors if v is not None]
        valid_idxs = [i for i, v in enumerate(vectors) if v is not None]

        if len(vectors) < 3:
            return [{"cluster_id": 0, "label": "all", "items": memories, "size": len(memories)}]

        vec_arr = np.array(vectors)

        # 自动估算聚类数
        n = len(vectors)
        if n_clusters is None:
            n_clusters = min(max(2, int(np.sqrt(n / 2))), max_clusters, n - 1)

        try:
            from sklearn.cluster import KMeans
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=1)
            labels = kmeans.fit_predict(vec_arr)
        except ImportError:
            # 没有 sklearn，使用简单阈值聚类
            return self._simple_cluster(memories, vectors)  # type: ignore[arg-type]

        # 整理聚类结果
        clusters_dict: Dict[int, Dict] = {}
        for idx, label in enumerate(labels):
            if label not in clusters_dict:
                clusters_dict[label] = {"items": []}
            clusters_dict[label]["items"].append(memories[valid_idxs[idx]])

        # 生成主题标签
        clusters = []
        for cid, data in clusters_dict.items():
            # 标签：取该簇中与质心最近的前 3 条内容摘要
            centroid = kmeans.cluster_centers_[cid]
            centroid_norm = centroid / (np.linalg.norm(centroid) + 1e-10)
            items_texts = [(i, memories[valid_idxs[i]].get("content", ""))
                           for i in range(len(memories))
                           if labels[i] == cid]
            # 找与质心最接近的内容
            sorted_items = sorted(
                items_texts,
                key=lambda x: float(np.dot(
                    vectors[valid_idxs.index(x[0])] / (np.linalg.norm(vectors[valid_idxs.index(x[0])]) + 1e-10),  # type: ignore[operator]
                    centroid_norm
                )),
                reverse=True
            )
            top_keywords = [t[1][:30] for t in sorted_items[:min(3, len(sorted_items))]]

            clusters.append({
                "cluster_id": int(cid),
                "label": " | ".join(top_keywords),
                "items": data["items"],
                "size": len(data["items"]),
                "keyword_hints": top_keywords,
            })

        clusters.sort(key=lambda x: x["size"], reverse=True)
        return clusters

    def _simple_cluster(self, memories: List[Dict],
                        vectors: List[np.ndarray]) -> List[Dict]:
        """无 sklearn 时的简单聚类：基于距离阈值"""
        if len(vectors) < 2:
            return [{"cluster_id": 0, "label": "all", "items": memories, "size": len(memories)}]

        vec_arr = np.array(vectors)
        norm = vec_arr / (np.linalg.norm(vec_arr, axis=1, keepdims=True) + 1e-10)

        # 简单聚合：相似度 > 0.85 视为同一簇
        threshold = 0.85
        assigned: Set[int] = set()
        clusters: List[Any] = []
        valid_idxs = [i for i, _ in enumerate(vectors)]

        for i in range(len(vectors)):
            if i in assigned:
                continue
            group = [i]
            assigned.add(i)
            for j in range(i + 1, len(vectors)):
                if j in assigned:
                    continue
                sim = float(np.dot(norm[i], norm[j]))
                if sim > threshold:
                    group.append(j)
                    assigned.add(j)
            if group:
                items = [memories[valid_idxs[k]] for k in group]
                clusters.append({
                    "cluster_id": len(clusters),
                    "label": items[0].get("content", "")[:30],
                    "items": items,
                    "size": len(items),
                })

        # 未分组的独立项
        for i in range(len(vectors)):
            if i not in assigned:
                clusters.append({
                    "cluster_id": len(clusters),
                    "label": "singleton",
                    "items": [memories[valid_idxs[i]]],
                    "size": 1,
                })

        clusters.sort(key=lambda x: x["size"], reverse=True)
        return clusters

    # ==================== 4. 查询扩展 ====================

    def expand_query(self, query: str, top_n: int = 5) -> List[str]:
        """
        Embedding 语义扩展搜索关键词

        通过 embedding 找与 query 语义相近的记忆内容中的关键词

        Args:
            query: 原始查询
            top_n: 返回扩展词数量

        Returns:
            扩展后的查询词列表（包含原 query 本身）
        """
        if not self.available():
            return [query]

        q_vec = self._get_embedding(query)
        if q_vec is None:
            return [query]

        # 从向量库搜索近邻并提取关键词
        try:
            from unified_vector_store import get_vector_store
            store = get_vector_store()
            q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-10)
            results = store.search(q_norm.tolist(), top_k=10)
            if results:
                # 从结果内容中提取关键词
                from nlp_processor import NLPProcessor
                nlp = NLPProcessor()
                keywords = []
                for r in results:
                    kw = nlp.extract_keywords(r.get("content", ""))
                    keywords.extend(kw)
                # 去重并取 top_n
                seen: Set[str] = set()
                expanded = [query]
                for k in keywords:
                    if k.lower() not in seen and k.lower() != query.lower():
                        seen.add(k.lower())
                        expanded.append(k)
                    if len(expanded) >= top_n:
                        break
                return expanded if len(expanded) > 1 else [query]
        except Exception:
            pass

        return [query]

    # ==================== 5. 代表性抽取 ====================

    def extract_representative(self, results: List[Dict],
                               top_k: int = 1) -> List[Dict]:
        """
        从检索结果中抽取最有代表性的结果（基于 embedding 质心）

        当一堆结果相似时，只挑离质心最近的作为代表，避免信息冗余

        Args:
            results: 检索结果列表，每项需包含 'content' 字段
            top_k: 返回数量

        Returns:
            有代表性的结果列表
        """
        if not self.available() or not results:
            return results[:top_k] if results else []

        if len(results) <= top_k:
            return results

        texts = [r.get("content", "") for r in results]
        vectors = self._get_embeddings_batch(texts)
        vectors = [v for v in vectors if v is not None]

        if len(vectors) < 2:
            return results[:top_k]

        vec_arr = np.array(vectors)
        norm = vec_arr / (np.linalg.norm(vec_arr, axis=1, keepdims=True) + 1e-10)
        center = np.mean(norm, axis=0)
        center_norm = center / (np.linalg.norm(center) + 1e-10)
        scores = np.dot(norm, center_norm)

        # 按与质心的相似度排序
        valid_idxs = [i for i, v in enumerate(vectors) if v is not None]
        sorted_idxs = sorted(valid_idxs, key=lambda i: scores[i], reverse=True)

        # 取离质心最近的 top_k 个，同时确保它们彼此不重复
        selected = [sorted_idxs[0]]
        for idx in sorted_idxs[1:]:
            if len(selected) >= top_k:
                break
            # 检查是否与已选中的高度相似
            is_redundant = False
            for s in selected:
                sim = float(np.dot(norm[idx], norm[s]))
                if sim > 0.92:
                    is_redundant = True
                    break
            if not is_redundant:
                selected.append(idx)

        return [results[i] for i in selected[:top_k]]

    # ==================== 6. 质量评分 ====================

    def score_relevance(self, query: str, results: List[Dict]) -> List[Dict]:
        """
        为检索结果与 query 的语义匹配度打分

        给每条结果分配一个 relevance_score（0-1），并过滤低分项

        Args:
            query: 原始查询
            results: 检索结果列表，每项需包含 'content' 字段

        Returns:
            添加了 relevance_score 的结果列表
        """
        if not self.available() or not results:
            return [dict(r, **{"relevance_score": 0.5}) for r in results]

        q_vec = self._get_embedding(query)
        if q_vec is None:
            return [dict(r, **{"relevance_score": 0.5}) for r in results]

        texts = [r.get("content", "") for r in results]
        vectors = self._get_embeddings_batch(texts)
        q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-10)

        scored = []
        for i, vec in enumerate(vectors):
            if vec is not None:
                v_norm = vec / (np.linalg.norm(vec) + 1e-10)
                score = float(np.dot(v_norm, q_norm))
            else:
                score = 0.0
            scored.append(dict(results[i], **{"relevance_score": round(score, 4)}))

        return scored

    def filter_by_relevance(self, results: List[Dict],
                            threshold: float = 0.3) -> List[Dict]:
        """
        按相关性阈值过滤结果

        Args:
            results: 检索结果列表（建议先调用 score_relevance）
            threshold: 最低相关性阈值

        Returns:
            过滤后的结果
        """
        return [r for r in results if r.get("relevance_score", 0.5) >= threshold]


# ==================== 便捷入口 ====================

_default_enhancer: Optional[EmbeddingEnhancer] = None


def get_enhancer() -> EmbeddingEnhancer:
    """获取默认 Embedding 增强器单例"""
    global _default_enhancer
    if _default_enhancer is None:
        _default_enhancer = EmbeddingEnhancer()
    return _default_enhancer


def find_duplicates(memories: List[Dict], threshold: float = 0.92) -> List[Dict]:
    """便捷：检测重复"""
    return get_enhancer().find_duplicates(memories, threshold)


def deduplicate(memories: List[Dict], threshold: float = 0.92) -> List[Dict]:
    """便捷：去重"""
    return get_enhancer().deduplicate_memories(memories, threshold)


def detect_anomalies(memories: List[Dict]) -> List[Dict]:
    """便捷：异常检测"""
    return get_enhancer().detect_anomalies(memories)


def cluster(memories: List[Dict], n_clusters: Optional[int] = None) -> List[Dict]:
    """便捷：主题聚类"""
    return get_enhancer().cluster_by_content(memories, n_clusters)


def expand_query(query: str, top_n: int = 5) -> List[str]:
    """便捷：查询扩展"""
    return get_enhancer().expand_query(query, top_n)


def score_relevance(query: str, results: List[Dict]) -> List[Dict]:
    """便捷：质量评分"""
    return get_enhancer().score_relevance(query, results)


# ==================== 测试 ====================

if __name__ == "__main__":
    print("=" * 60)
    print("Embedding 增强模块测试")
    print("=" * 60)

    ee = EmbeddingEnhancer()
    print(f"Available: {ee.available()}")

    if ee.available():
        # 测试向量获取
        vec = ee._get_embedding("测试文本")
        print(f"Embedding 维度: {len(vec) if vec is not None else 'FAIL'}")
        print(f"Embedding 前5值: {vec[:5] if vec is not None else 'N/A'}")

        # 测试去重
        test_memories = [
            {"content": "用户偏好中文表达，不喜欢翻译腔", "confidence": 0.9},
            {"content": "用户喜欢中文表达，讨厌翻译风格", "confidence": 0.8},
            {"content": "系统使用OpenClaw框架", "confidence": 0.95},
        ]
        dups = ee.find_duplicates(test_memories)
        print(f"去重检测: {len(dups)} 对重复")
        if dups:
            for d in dups:
                print(f"  相似度 {d['similarity']:.3f}: {d['source_content'][:20]}... ↔ {d['target_content'][:20]}")

        # 测试异常检测
        anomalies = ee.detect_anomalies(test_memories)
        print(f"异常检测: {len(anomalies)} 个异常")

        # 测试主题聚类
        clusters = ee.cluster_by_content(test_memories, n_clusters=2)
        print(f"主题聚类: {len(clusters)} 个簇")

        # 测试评分
        scored = ee.score_relevance("中文表达偏好", test_memories)
        print(f"质量评分: {len(scored)} 条评分")
        for s in scored:
            print(f"  {s.get('score', s.get('relevance_score', '?')):.3f} | {s['content'][:20]}")

        print("\n✅ 测试完成")
    else:
        print("❌ Embedding API 不可用（检查 API Key 配置）")

    print("=" * 60)
