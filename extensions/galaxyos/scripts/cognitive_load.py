#!/usr/bin/env python3
"""
认知负荷上下文管理 (论文级升级版) — Cognitive Load Based Context Assembly

Sweller (1988) — 认知负荷理论 (被引 25000+)
扩展: 将"内在/外在/关联"三维负荷应用于 DAG 上下文压缩决策

核心思想:
  DAG _compactInner 不再只靠 token budget 做硬切.
  而是计算三组量, 输出动态压缩力度:
  - 内在负荷: 不同主题数 / 关系密度 (越高越要保留上下文)
  - 外在负荷: 上下文长度的平方 / 窗口大小 (越长越要压)
  - 关联负荷: 记忆与当前 query 的语义距离分布 (越分散越要保留线索)

升级 A: 用 PyTorch 嵌入替代词袋 (随机投影)
升级 B: 新增 get_compression_advice() — DAG 压缩建议

用法:
    cl = CognitiveLoad()
    result = cl.assess(query, dag_nodes, session_history)
    advice = cl.get_compression_advice(query, dag_nodes, history)
"""

import json
import math
import logging
import re
from typing import List, Dict, Optional, Any, Tuple
from collections import Counter

import torch

logger = logging.getLogger(__name__)

# ──────────── 嵌入基础设施 (与 semantic_entropy 共享) ────────────

_EMBED_MAT_CL = None
_EMBED_DIM_CL = 64
_VOCAB_SIZE_CL = 65536


def _get_embed_mat_cl():
    """获取全局随机投影嵌入矩阵 (固定种子, 与 SE 独立种子)"""
    global _EMBED_MAT_CL
    if _EMBED_MAT_CL is None:
        gen = torch.Generator()
        gen.manual_seed(12345)
        _EMBED_MAT_CL = torch.randn(_VOCAB_SIZE_CL, _EMBED_DIM_CL, generator=gen)
    return _EMBED_MAT_CL


def _build_embedding_cl(text: str) -> torch.Tensor:
    """将文本转换为随机投影嵌入向量"""
    if not text:
        return torch.zeros(_EMBED_DIM_CL)
    mat = _get_embed_mat_cl()
    chars = text[:500]
    indices = torch.tensor([ord(c) % _VOCAB_SIZE_CL for c in chars], dtype=torch.long)
    with torch.no_grad():
        embeds = mat[indices]
        vec = embeds.mean(dim=0)
    return vec


def _cosine_sim_cl(v1: torch.Tensor, v2: torch.Tensor) -> float:
    """余弦相似度"""
    with torch.no_grad():
        cos = torch.nn.functional.cosine_similarity(v1.unsqueeze(0), v2.unsqueeze(0))
    return float(cos.item())


class CognitiveLoad:
    """认知负荷评估器 (论文级升级版)"""

    def __init__(self):
        self._embedding_cache = {}

    def assess(self, query: str = "", dag_nodes: List[Dict] = None,
               session_history: List[str] = None) -> Dict[str, Any]:
        """
        评估当前上下文的认知负荷

        Args:
            query: 用户当前 query
            dag_nodes: DAG 节点列表, 每个需有 content / node_type / importance 字段
            session_history: 会话历史文本列表

        Returns:
            intrinsic: float 内在负荷 0~1
            extraneous: float 外在负荷 0~1
            germane: float 关联负荷 0~1
            compression_strength: float 建议压缩力度 0~1 (1=全力压缩)
            retain_keys: List[str] 建议保留的关键节点 ID
        """
        nodes = dag_nodes or []
        history = session_history or []

        intrinsic = self._calc_intrinsic(nodes, history)
        extraneous = self._calc_extraneous(nodes, history)
        germane = self._calc_germane(query, nodes)

        # 综合压缩力度
        compression_strength = max(0.0, min(1.0,
            extraneous - (intrinsic + germane) / 2
        ))

        retain_keys = self._find_retain_keys(query, nodes, germane)

        return {
            "intrinsic": round(intrinsic, 3),
            "extraneous": round(extraneous, 3),
            "germane": round(germane, 3),
            "compression_strength": round(compression_strength, 3),
            "retain_keys": retain_keys,
            "recommendation": "aggressive" if compression_strength > 0.7 else (
                "moderate" if compression_strength > 0.3 else "minimal"
            ),
        }

    # ──────────── 升级 A: PyTorch 嵌入替代词袋 ────────────

    def _calc_intrinsic(self, nodes: List[Dict], history: List[str]) -> float:
        """
        内在负荷 (PyTorch 升级版)

        词袋模式 → 随机投影嵌入模式:
        - 主题多样性 = 嵌入空间的聚类数估计
        - 关系密度 = 嵌入向量间的平均余弦距离
        """
        if not nodes and not history:
            return 0.1

        # 收集所有文本
        texts = []
        for n in nodes:
            content = n.get("content", "") or ""
            if content:
                texts.append(content)
        for h in history:
            if h:
                texts.append(h)

        if not texts:
            return 0.1

        # 对每个文本生成嵌入向量
        embeddings = []
        for t in texts:
            cache_key = hash(t[:200])
            if cache_key in self._embedding_cache:
                emb = self._embedding_cache[cache_key]
            else:
                emb = _build_embedding_cl(t)
                self._embedding_cache[cache_key] = emb
            embeddings.append(emb)

        if len(embeddings) <= 1:
            return 0.1

        # ---- 主题多样性: 嵌入空间的聚类数估计 ----
        # 用贪心聚类: 余弦 > 0.7 认为同一主题
        n_emb = len(embeddings)
        topic_clusters = []
        for i in range(n_emb):
            assigned = False
            for cl in topic_clusters:
                rep_idx = cl[0]
                sim = _cosine_sim_cl(embeddings[i], embeddings[rep_idx])
                if sim > 0.7:
                    cl.append(i)
                    assigned = True
                    break
            if not assigned:
                topic_clusters.append([i])

        topic_diversity = min(len(topic_clusters) / max(n_emb * 0.3, 1), 1.0)

        # ---- 关系密度: 嵌入向量平均余弦距离 ----
        if len(embeddings) >= 2:
            pair_count = 0
            total_dist = 0.0
            for i in range(min(len(embeddings), 30)):
                for j in range(i + 1, min(len(embeddings), 30)):
                    sim = _cosine_sim_cl(embeddings[i], embeddings[j])
                    total_dist += (1.0 - sim)  # 余弦距离 = 1 - 相似度
                    pair_count += 1
            avg_dist = total_dist / max(pair_count, 1)
            # 距离越大 = 越分散 = 关系密度越低
            relationship_density = max(0.0, 1.0 - avg_dist)
        else:
            relationship_density = 0.5

        # ---- 节点类型多样性 (保留原始信息) ----
        type_diversity = 0.0
        if nodes:
            types = set(n.get("node_type", "") for n in nodes if n.get("node_type"))
            type_diversity = min(len(types) / 3, 1.0) if types else 0.0

        # 综合: 主题多样性 * 0.5 + 关系密度 * 0.3 + 类型多样性 * 0.2
        intrinsic = 0.5 * topic_diversity + 0.3 * relationship_density + 0.2 * type_diversity
        return min(max(intrinsic, 0.0), 1.0)

    def _calc_extraneous(self, nodes: List[Dict], history: List[str]) -> float:
        """外在负荷: 上下文规模"""
        total_chars = sum(len(n.get("content", "") or "") for n in nodes)
        total_chars += sum(len(h or "") for h in history)
        return min(total_chars / 20000, 1.0)

    def _calc_germane(self, query: str, nodes: List[Dict]) -> float:
        """
        关联负荷 (PyTorch 升级版)

        用嵌入相似度替代词袋重叠
        """
        if not query or not nodes:
            return 0.3

        q_emb = _build_embedding_cl(query)

        similarity_scores = []
        for n in nodes:
            content = (n.get("content", "") or "")
            if not content:
                continue
            cache_key = hash(content[:200])
            if cache_key in self._embedding_cache:
                n_emb = self._embedding_cache[cache_key]
            else:
                n_emb = _build_embedding_cl(content)
                self._embedding_cache[cache_key] = n_emb
            sim = _cosine_sim_cl(q_emb, n_emb)
            # 映射到 [0, 1]
            sim_mapped = max(0.0, (sim + 1.0) / 2.0)
            similarity_scores.append(sim_mapped)

        if not similarity_scores:
            return 0.3

        avg_sim = sum(similarity_scores) / len(similarity_scores)
        semantic_sim = min(avg_sim * 3, 1.0)

        if len(similarity_scores) > 1:
            variance = sum((s - avg_sim) ** 2 for s in similarity_scores) / len(similarity_scores)
            dispersion = min(variance * 5, 1.0)
        else:
            dispersion = 0

        return min(semantic_sim + dispersion, 1.0)

    def _find_retain_keys(self, query: str, nodes: List[Dict],
                          germane: float) -> List[str]:
        """找出建议保留的关键节点"""
        if not query or not nodes:
            return []

        q_emb = _build_embedding_cl(query)
        scored = []
        for n in nodes:
            content = (n.get("content", "") or "")
            if not content:
                continue
            cache_key = hash(content[:200])
            if cache_key in self._embedding_cache:
                n_emb = self._embedding_cache[cache_key]
            else:
                n_emb = _build_embedding_cl(content)
                self._embedding_cache[cache_key] = n_emb
            sim = _cosine_sim_cl(q_emb, n_emb)
            sim_mapped = max(0.0, (sim + 1.0) / 2.0)
            importance = n.get("importance", 0.5) if isinstance(n.get("importance"), (int, float)) else 0.5
            total = sim_mapped * 0.6 + importance * 0.4
            scored.append((total, n.get("node_id", str(id(n)))))

        scored.sort(key=lambda x: -x[0])
        top_n = max(1, min(int(germane * 10), len(scored)))
        return [s[1] for s in scored[:top_n]]

    # ──────────── 升级 B: DAG 压缩建议 ────────────

    def get_compression_advice(self, query: str, dag_nodes: List[Dict] = None,
                               history: List[str] = None) -> Dict[str, Any]:
        """
        为 DAG 上下文剪裁提供语义级压缩建议

        Args:
            query: 用户当前 query
            dag_nodes: DAG 节点列表 (content, node_id, importance, timestamp)
            history: 会话历史文本

        Returns:
            compression_strength: float 0~1 建议压缩力度
            retain_keys: List[str] 建议保留的节点 ID
            reasoning: str 压缩理由
            priority_groups: Dict 分组建议 (must_keep, can_summarize, can_drop)
        """
        nodes = dag_nodes or []
        history = history or []

        # 基础评估
        assessment = self.assess(query, nodes, history)

        compression_strength = assessment["compression_strength"]
        retain_keys = assessment["retain_keys"]

        # 构建优先级分组
        must_keep = []
        can_summarize = []
        can_drop = []

        if query and nodes:
            q_emb = _build_embedding_cl(query)
            for n in nodes:
                content = n.get("content", "") or ""
                node_id = n.get("node_id", str(id(n)))
                importance = n.get("importance", 0.5) if isinstance(n.get("importance"), (int, float)) else 0.5

                if not content and importance < 0.3:
                    can_drop.append(node_id)
                    continue

                cache_key = hash(content[:200])
                if cache_key in self._embedding_cache:
                    n_emb = self._embedding_cache[cache_key]
                else:
                    n_emb = _build_embedding_cl(content)
                    self._embedding_cache[cache_key] = n_emb
                sim = _cosine_sim_cl(q_emb, n_emb)
                sim_mapped = max(0.0, (sim + 1.0) / 2.0)

                relevance = sim_mapped * 0.5 + importance * 0.5

                if relevance > 0.6:
                    must_keep.append(node_id)
                elif relevance > 0.35:
                    can_summarize.append(node_id)
                else:
                    can_drop.append(node_id)
        else:
            can_drop = [n.get("node_id", str(id(n))) for n in nodes
                        if n.get("importance", 0.5) < 0.3]
            must_keep = [n.get("node_id", str(id(n))) for n in nodes
                         if n.get("importance", 0.5) >= 0.3]

        # 构建推理理由
        reasoning_parts = []
        if compression_strength > 0.7:
            reasoning_parts.append(f"压缩力度 {compression_strength:.2f}: 上下文过长 ({assessment['extraneous']:.2f} 外在负荷), 建议激进压缩")
        elif compression_strength > 0.3:
            reasoning_parts.append(f"压缩力度 {compression_strength:.2f}: 适度压缩, 保留关键语义节点")
        else:
            reasoning_parts.append(f"压缩力度 {compression_strength:.2f}: 维持现有上下文, 不压缩")

        reasoning_parts.append(f"内在负荷={assessment['intrinsic']:.2f}, 外在负荷={assessment['extraneous']:.2f}, 关联负荷={assessment['germane']:.2f}")
        reasoning_parts.append(f"必须保留 {len(must_keep)} 个, 可摘要 {len(can_summarize)} 个, 可丢弃 {len(can_drop)} 个")

        return {
            "compression_strength": round(compression_strength, 3),
            "retain_keys": list(set(retain_keys + must_keep)),
            "reasoning": " | ".join(reasoning_parts),
            "priority_groups": {
                "must_keep": must_keep,
                "can_summarize": can_summarize,
                "can_drop": can_drop,
            },
            "assessment": assessment,
        }


# 全局单例
_instance = None


def get_cognitive_load() -> CognitiveLoad:
    global _instance
    if _instance is None:
        _instance = CognitiveLoad()
    return _instance


if __name__ == "__main__":
    cl = CognitiveLoad()
    r = cl.assess("你好", [{"content": "用户喜欢编程", "node_id": "n1"}], ["昨天的对话"])
    print(f"CognitiveLoad test: {r}")

    advice = cl.get_compression_advice("Python编程问题",
        [{"content": "用户问过Python的装饰器", "node_id": "n1", "importance": 0.8},
         {"content": "昨天天气不错", "node_id": "n2", "importance": 0.1}],
        ["用户昨天问了很多Python问题"])
    print(f"Compression advice: {advice}")
