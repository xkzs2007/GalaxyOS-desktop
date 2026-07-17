#!/usr/bin/env python3
"""
MemoryOS — 操作系统启发式记忆管理增强

借鉴 OS 三层记忆 + 分段页式 + 热度替换策略，
优化 GalaxyOS DAG 的压缩时机判断和存储层次。

参考: https://arxiv.org/abs/2506.06326 (MemoryOS)
"""

import os
import json
import time
import math
from typing import Dict, List, Any, Optional, Set
from pathlib import Path
from collections import defaultdict, OrderedDict
import logging

logger = logging.getLogger("memory_os")


def hybrid_score(text_a: str, text_b: str, emb_a: List[float] = None,
                 emb_b: List[float] = None) -> float:
    """
    MemoryOS 混合评分公式

    score = cos(emb_a, emb_b) + Jaccard(keywords_a, keywords_b)

    比纯向量相似度多一个关键词维度，更准确。
    """
    sim = 0.0

    # 1. 向量相似度
    if emb_a and emb_b and len(emb_a) == len(emb_b):
        dot = sum(a * b for a, b in zip(emb_a, emb_b))
        norm_a = math.sqrt(sum(a * a for a in emb_a)) or 1.0
        norm_b = math.sqrt(sum(b * b for b in emb_b)) or 1.0
        sim += dot / (norm_a * norm_b)
    else:
        # 无向量时用 TF 关键词代替
        kw_a = set(text_a.lower().split()[:50])
        kw_b = set(text_b.lower().split()[:50])
        if kw_a and kw_b:
            intersection = kw_a & kw_b
            union = kw_a | kw_b
            sim += len(intersection) / len(union)

    # 2. Jaccard 关键词相似度
    words_a = set(text_a.lower().split()[:50])
    words_b = set(text_b.lower().split()[:50])
    if words_a and words_b:
        inter = words_a & words_b
        un = words_a | words_b
        sim += len(inter) / len(un)

    return sim


class HeatTracker:
    """
    热度跟踪器 — 基于访问频率和时效性的权重计算

    用于 DAG 压缩时决定哪些节点该保留、哪些可以摘要。
    v7.1: 内部按 session_id 分区，不同会话的热度互不干扰。
    """

    def __init__(self, decay_hours: float = 24.0,
                 boost_on_access: float = 0.1,
                 max_heat: float = 10.0):
        self.decay_hours = decay_hours          # 半衰期（小时）
        self.boost_on_access = boost_on_access  # 每次访问的升温
        self.max_heat = max_heat
        self._nodes: Dict[str, Dict] = {}       # node_id → heat data

    def _key(self, node_id: str, session_id: str = ""):
        """v7.1: 复合键 node_id#session_id，不同会话热度独立"""
        return f"{node_id}#{session_id}" if session_id else node_id

    def record_access(self, node_id: str, now: float = None, session_id: str = ""):
        """记录一次访问 (v7.1: session_id 分区)"""
        if now is None:
            now = time.time()

        key = self._key(node_id, session_id)
        if key not in self._nodes:
            self._nodes[key] = {
                "heat": 0.0,
                "first_access": now,
                "last_access": now,
                "access_count": 0,
            }

        node = self._nodes[key]
        hours_since = (now - node["last_access"]) / 3600

        # 时效性衰减: 指数衰减
        if hours_since > 0:
            decay = math.exp(-hours_since / self.decay_hours)
            node["heat"] *= decay

        # 升温
        node["heat"] = min(self.max_heat, node["heat"] + self.boost_on_access)
        node["last_access"] = now
        node["access_count"] += 1

    def get_heat(self, node_id: str, now: float = None, session_id: str = "") -> float:
        """获取当前热度（含衰减）(v7.1: session_id 分区)"""
        node = self._nodes.get(self._key(node_id, session_id))
        if not node:
            return 0.0

        if now is None:
            now = time.time()

        hours_since = (now - node["last_access"]) / 3600
        decayed = node["heat"] * math.exp(-hours_since / self.decay_hours)
        return decayed

    def should_keep(self, node_id: str, threshold: float = 0.3, session_id: str = "") -> bool:
        """判断一个节点是否应保留（高于阈值）"""
        return self.get_heat(node_id, session_id=session_id) >= threshold

    def get_cold_nodes(self, threshold: float = 0.1, session_id: str = "") -> List[str]:
        """获取冷节点（低于阈值）(v7.1: session_id 分区)"""
        prefix = f"#{session_id}" if session_id else ""
        return [nid for nid in self._nodes
                if (not session_id or nid.endswith(prefix))
                and self.get_heat(nid) < threshold]

    def get_top_nodes(self, k: int = 10, session_id: str = "") -> List[str]:
        """获取热度最高的 k 个节点 (v7.1: session_id 分区)"""
        prefix = f"#{session_id}" if session_id else ""
        items = [(nid, self.get_heat(nid)) for nid in self._nodes
                 if not session_id or nid.endswith(prefix)]
        items.sort(key=lambda x: -x[1])
        return [nid for nid, _ in items[:k]]

    def get_status(self, session_id: str = "") -> dict:
        """v7.1: session_id 分区状态查询"""
        prefix = f"#{session_id}" if session_id else ""
        nodes = {k: v for k, v in self._nodes.items()
                 if not session_id or k.endswith(prefix)}
        total = len(nodes)
        hot = sum(1 for nid in nodes if self.get_heat(nid) >= 1.0)
        warm = sum(1 for nid in nodes if 0.3 <= self.get_heat(nid) < 1.0)
        cold = total - hot - warm
        return {
            "total_nodes": total,
            "hot": hot,
            "warm": warm,
            "cold": cold,
            "top_nodes": self.get_top_nodes(5, session_id=session_id),
        }


class SegmentedPageOrganizer:
    """
    分段页式组织器 — 参考 MemoryOS 的 STM→MTM→LPM 升级

    将短期对话按主题分段，合并到中期存储，再提炼长期人格记忆。
    """

    def __init__(self, segment_threshold: float = 0.35,
                 max_short_term: int = 50):
        self.segment_threshold = segment_threshold  # 分段合并的相似度阈值
        self.max_short_term = max_short_term         # STM 最大容量

        # 三段存储
        self.short_term: List[Dict] = []    # 最近对话页面
        self.mid_term: List[Dict] = []      # 按主题聚合的段落
        self.long_term: Dict = {            # 长期人格记忆
            "user_profile": {},
            "agent_profile": {},
            "user_traits": {},
            "agent_traits": {},
        }

    def add_page(self, content: str, metadata: dict = None):
        """添加一个对话页面到 STM"""
        page = {
            "content": content,
            "metadata": metadata or {},
            "timestamp": time.time(),
            "keywords": set(content.lower().split()[:30]),
        }
        self.short_term.append(page)

        # STM 满 → 迁移到 MTM
        if len(self.short_term) >= self.max_short_term:
            self._flush_to_mid_term()

    def _flush_to_mid_term(self):
        """STM → MTM: FIFO + 主题合并"""
        if not self.short_term:
            return

        # 逐页面尝试合并到现有段落
        for page in self.short_term:
            best_segment = None
            best_score = 0.0

            for seg in self.mid_term:
                score = hybrid_score(
                    page["content"], seg.get("summary", "")
                )
                if score > best_score:
                    best_score = score
                    best_segment = seg

            if best_segment and best_score >= self.segment_threshold:
                # 合并到现有段落
                best_segment["pages"].append(page)
                best_segment["count"] += 1
                best_segment["last_updated"] = time.time()
            else:
                # 新建段落
                self.mid_term.append({
                    "summary": page["content"][:500],
                    "pages": [page],
                    "count": 1,
                    "created": time.time(),
                    "last_updated": time.time(),
                })

        self.short_term.clear()

    def promote_to_long_term(self, key: str, value: Any):
        """MTM → LPM: 将稳定的用户特征提升到长期人格"""
        if key in self.long_term:
            if isinstance(self.long_term[key], list):
                self.long_term[key].append(value)
            elif isinstance(self.long_term[key], dict) and isinstance(value, dict):
                self.long_term[key].update(value)
            else:
                self.long_term[key] = value

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """三段搜索"""
        results = []
        q_words = set(query.lower().split())

        # STM 搜索
        for p in self.short_term:
            score = hybrid_score(query, p["content"])
            results.append({"source": "stm", "content": p["content"], "score": score})

        # MTM 搜索
        for seg in self.mid_term:
            score = hybrid_score(query, seg.get("summary", ""))
            results.append({"source": "mtm", "content": seg.get("summary", ""), "score": score})

        # LPM 搜索 - 用户画像
        for k, v in self.long_term.items():
            if isinstance(v, str):
                score = hybrid_score(query, v)
                if score > 0.2:
                    results.append({"source": "lpm", "content": f"{k}: {v}", "score": score})

        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    def get_status(self) -> dict:
        return {
            "stm_pages": len(self.short_term),
            "mtm_segments": len(self.mid_term),
            "lpm_keys": list(self.long_term.keys()),
        }
