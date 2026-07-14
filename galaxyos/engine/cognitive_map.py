#!/usr/bin/env python3
"""
LASAR: Latent Cognitive Map for Spatial-Temporal Reasoning (Torch 实现)

论文: arXiv:2605.16899 (中山大学, 2026-05-16)

核心思路：
- 双记忆系统: episodic 记忆(时序特征序列) + semantic cognitive map(latent codebook)
- ST-CRL 三类认知 query: Retrospective(回顾) / Introspective(内省) / Prospective(预测)
- 用 Flash 推理替代训练: 对话中动态生成三类认知 query 的答案
- 锚点哈希表替代可训练 codebook: semantic hash + 语义聚类

设计原则：
1. 基于 PyTorch 2.12.0 张量运算（利用 torch 向量化加速）
2. 轻量化：无训练、无 embedding 模型依赖
3. 可持久化：SQLite 存储锚点和 query 记录
4. 与 DAG Context Manager 自然融合
"""

import json
import os
import time
import logging
import sqlite3
import hashlib
import math
import re
import threading
import numpy as np
from typing import Dict, List, Optional, Any, Tuple, Callable, Union
from dataclasses import dataclass, field, asdict

import torch

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class SpatialAnchor:
    """空间锚点 — 记忆在认知空间中的位置"""
    anchor_id: str
    node_id: str                  # 关联的 DAG 节点 ID
    context: str                  # 锚点的上下文描述
    anchor_vector: Union[str, List[float]]  # JSON 序列化或原始列表（256 维，不依赖 numpy）
    dimension: int = 256          # 向量维度
    timestamp: float = 0.0        # 创建时间
    session_key: str = ""         # 会话 Key
    importance: float = 0.5       # 重要性 [0, 1]
    access_count: int = 0         # 访问次数
    cluster_id: Optional[str] = None  # 聚类 ID
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(d.get('anchor_vector'), list):
            d['anchor_vector'] = json.dumps(d['anchor_vector'])
        if isinstance(d.get('metadata'), dict):
            d['metadata'] = json.dumps(d['metadata'])
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "SpatialAnchor":
        d = dict(data)
        if isinstance(d.get('anchor_vector'), str):
            try:
                d['anchor_vector'] = json.loads(d['anchor_vector'])
            except (json.JSONDecodeError, TypeError):
                d['anchor_vector'] = [0.0] * 256
        if isinstance(d.get('metadata'), str):
            try:
                d['metadata'] = json.loads(d['metadata'])
            except (json.JSONDecodeError, TypeError):
                d['metadata'] = {}
        for f in ('importance', 'access_count'):
            if f in d and not isinstance(d.get(f), (int, float)):
                d[f] = float(d[f]) if str(d.get(f, '0')).replace('.','',1).isdigit() else 0.0
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class CognitiveQuery:
    """认知 query — LASAR 的三类 query"""
    query_id: str
    query_type: str               # "retrospective", "introspective", "prospective"
    query_text: str               # query 文本
    result: str                   # query 结果
    timestamp: float = 0.0        # 执行时间
    anchor_id: Optional[str] = None  # 关联锚点
    session_key: str = ""         # 会话
    confidence: float = 0.5       # 置信度

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CognitiveQuery":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================================
# 向量计算（零 numpy 依赖）
# ============================================================================

class VectorOps:
    """基于 PyTorch 的向量运算（利用 torch 张量操作）"""

    _DEVICE = torch.device('cpu')

    @staticmethod
    def tensor(vec) -> torch.Tensor:
        if isinstance(vec, torch.Tensor):
            return vec.detach().clone().to(dtype=torch.float32, device=VectorOps._DEVICE)
        return torch.tensor(vec, dtype=torch.float32, device=VectorOps._DEVICE)

    @staticmethod
    def tensors(vectors) -> torch.Tensor:
        if isinstance(vectors, torch.Tensor):
            return vectors.detach().clone().to(dtype=torch.float32, device=VectorOps._DEVICE)
        return torch.tensor(vectors, dtype=torch.float32, device=VectorOps._DEVICE)

    @staticmethod
    def cosine_similarity(a, b) -> torch.Tensor:
        """余弦相似度 [0, 1]，支持 batch 计算"""
        a_t = VectorOps.tensor(a)
        b_t = VectorOps.tensor(b)
        # 单向量 vs 单向量
        if a_t.dim() == 1 and b_t.dim() == 1:
            return torch.nn.functional.cosine_similarity(
                a_t.unsqueeze(0), b_t.unsqueeze(0)).item()
        # 单向量 vs 批量: unsqueeze 广播
        if a_t.dim() == 1 and b_t.dim() == 2:
            a_t = a_t.unsqueeze(0)
        elif a_t.dim() == 2 and b_t.dim() == 1:
            b_t = b_t.unsqueeze(0)
        return torch.nn.functional.cosine_similarity(a_t, b_t)

    @staticmethod
    def gaussian_decay(dist: float, sigma: float = 1.0) -> float:
        """高斯距离衰减"""
        return math.exp(- (dist * dist) / (2 * sigma * sigma))

    @staticmethod
    def euclidean_distance(a, b) -> torch.Tensor:
        """欧氏距离，支持 batch"""
        a_t = VectorOps.tensor(a)
        b_t = VectorOps.tensor(b)
        if a_t.dim() == 1 and b_t.dim() == 1:
            return torch.norm(a_t - b_t).item()
        # batch 计算
        if a_t.dim() == 1 and b_t.dim() == 2:
            a_t = a_t.unsqueeze(0)
        elif a_t.dim() == 2 and b_t.dim() == 1:
            b_t = b_t.unsqueeze(0)
        return torch.norm(a_t - b_t, dim=1)

    @staticmethod
    def mean(vectors) -> torch.Tensor:
        """向量均值"""
        if not vectors:
            return torch.empty(0, device=VectorOps._DEVICE)
        vec_t = VectorOps.tensors(vectors)
        return vec_t.mean(dim=0)

    @staticmethod
    def spatial_similarity(a, b) -> torch.Tensor:
        """空间相似度 = 余弦相似度 × 高斯距离衰减"""
        a_t = VectorOps.tensor(a)
        b_t = VectorOps.tensor(b)

        cos_sim = VectorOps.cosine_similarity(a_t, b_t)
        dist = VectorOps.euclidean_distance(a_t, b_t)
        decay = math.exp(- (dist * dist) / (2 * 0.5 * 0.5))
        return cos_sim * decay

    @staticmethod
    def batch_spatial_similarity(query_vec, anchors_mat) -> torch.Tensor:
        """批量计算 query 与所有锚点的空间相似度

        Args:
            query_vec: (dim,) 或 (1, dim)
            anchors_mat: (N, dim)
        Returns:
            (N,) 张量
        """
        q = VectorOps.tensor(query_vec).flatten().unsqueeze(0)  # (1, dim)
        a = VectorOps.tensors(anchors_mat)                      # (N, dim)

        cos_sim = torch.nn.functional.cosine_similarity(q, a)  # (N,)
        dist = torch.norm(q - a, dim=1)                         # (N,)
        decay = torch.exp(- (dist * dist) / (2 * 0.5 * 0.5))    # (N,)
        return cos_sim * decay

    @staticmethod
    def randomized_projection(text: str, dim: int = 256, seed: int = 42) -> List[float]:
        """字符级特征哈希投影：文本 → 固定维度向量

        特征提取部分仍用 Python dict（纯字符串操作，不适合张量化），
        但投影计算改用 torch 向量化操作。
        """
        chars = text.lower().strip()
        features: Dict[str, Any] = {}

        clean_chars = ''.join(c for c in chars if c.isalnum() or '\u4e00' <= c <= '\u9fff')
        if not clean_chars:
            clean_chars = chars

        for c in clean_chars:
            features[f'1_{c}'] = features.get(f'1_{c}', 0.0) + 1.0
        for i in range(len(clean_chars) - 1):
            features[f'2_{clean_chars[i:i+2]}'] = \
                features.get(f'2_{clean_chars[i:i+2]}', 0.0) + 1.0
        for i in range(len(clean_chars) - 2):
            features[f'3_{clean_chars[i:i+3]}'] = \
                features.get(f'3_{clean_chars[i:i+3]}', 0.0) + 1.0

        if not features:
            return [0.0] * dim

        n_probes = 3
        vec = torch.zeros(dim, dtype=torch.float32)
        for feat, tf in features.items():
            weight = math.log1p(tf)
            for probe in range(n_probes):
                h = hashlib.md5(f"{seed}_{feat}_{probe}".encode()).digest()
                pos = int.from_bytes(h[:4], 'little') % dim
                sign = -1 if (h[4] if len(h) > 4 else 0) < 128 else 1
                vec[pos] += weight * sign

        norm = vec.norm().item()
        if norm > 0:
            vec = vec / norm
        return vec.tolist()


# ============================================================================
# 锚点哈希表（Codebook 替代品）
# ============================================================================

class AnchorHashMap:
    """锚点哈希表 — LASAR codebook 的轻量化替代
    
    LASAR 用可训练的 codebook（向量量化 + cross-attention）。
    我们轻量化: 用确定性哈希 + 语义聚类做锚点查找。
    """

    def __init__(self, dim: int = 256, bucket_count: int = 128):
        self.dim = dim
        self.bucket_count = bucket_count
        # 每个 bucket 存储空间锚点
        self.buckets: Dict[int, List[str]] = {}
        # 锚点缓存 {anchor_id: SpatialAnchor}
        self._anchors: Dict[str, SpatialAnchor] = {}

    def _hash_vector(self, vec) -> int:
        """向量确定性哈希 → bucket 索引"""
        if isinstance(vec, torch.Tensor):
            if vec.numel() == 0:
                return 0
            vec_list = vec.flatten().tolist()
        else:
            if not vec:
                return 0
            vec_list = list(vec) if not isinstance(vec, list) else vec
        # 用所有维度的均值 + 前几维的符号做复合哈希
        components = []
        for i in range(min(8, len(vec_list))):
            components.append(str(int(vec_list[i] * 1000)))
        hash_str = "_".join(components)
        h = int(hashlib.md5(hash_str.encode()).hexdigest()[:8], 16)
        return h % self.bucket_count

    def insert(self, anchor: SpatialAnchor):
        """插入锚点到哈希表"""
        idx = self._hash_vector(anchor.anchor_vector)
        if idx not in self.buckets:
            self.buckets[idx] = []
        if anchor.anchor_id not in self.buckets[idx]:
            self.buckets[idx].append(anchor.anchor_id)
        self._anchors[anchor.anchor_id] = anchor

    def remove(self, anchor_id: str):
        """移除锚点"""
        if anchor_id in self._anchors:
            anchor = self._anchors.pop(anchor_id)
            idx = self._hash_vector(anchor.anchor_vector)
            if idx in self.buckets and anchor_id in self.buckets[idx]:
                self.buckets[idx].remove(anchor_id)

    def _build_anchor_tensor(self) -> Tuple[torch.Tensor, List[SpatialAnchor]]:
        """将所有锚点向量组装为 (N, dim) 张量，用于 torch batch 计算"""
        anchors = list(self._anchors.values())
        if not anchors:
            return torch.empty(0, self.dim, device=VectorOps._DEVICE), []
        mat = torch.tensor(
            [a.anchor_vector if isinstance(a.anchor_vector, (list, tuple))
             else json.loads(a.anchor_vector) if isinstance(a.anchor_vector, str)
             else list(a.anchor_vector)
             for a in anchors],
            dtype=torch.float32, device=VectorOps._DEVICE)
        return mat, anchors

    def find_nearby(self, vec: Union[str, List[float]], k: int = 5,
                    radius: float = 0.3) -> List[SpatialAnchor]:
        """在哈希表中找最近的 k 个锚点

        使用 torch batch 相似度计算替代逐元素循环。
        先通过 LSH bucket 过滤候选集，再批量算空间相似度。
        """
        if not self._anchors:
            return []

        # 第一步：LSH bucket 过滤候选
        target_bucket = self._hash_vector(vec)
        candidate_ids = set()

        for offset in range(self.bucket_count):
            idx = (target_bucket + offset) % self.bucket_count
            if idx in self.buckets:
                for aid in self.buckets[idx]:
                    if aid in self._anchors:
                        candidate_ids.add(aid)
            if len(candidate_ids) >= k * 5:
                break
            idx2 = (target_bucket - offset) % self.bucket_count
            if idx2 in self.buckets and idx2 != idx:
                for aid in self.buckets[idx2]:
                    if aid in self._anchors:
                        candidate_ids.add(aid)
            if len(candidate_ids) >= k * 5:
                break

        if not candidate_ids:
            # fallback: 全量搜
            candidate_ids = set(self._anchors.keys())

        # 第二步：组装候选张量，torch batch 一次算完
        candidates = [self._anchors[aid] for aid in candidate_ids if aid in self._anchors]
        if not candidates:
            return []

        # 统一 vec 为列表（可能传入 tensor）
        if isinstance(vec, torch.Tensor):
            vec_list = vec.detach().clone().flatten().tolist()
            vec_t = vec.detach().clone().to(dtype=torch.float32, device=VectorOps._DEVICE).unsqueeze(0)
        else:
            vec_list = vec
            vec_t = torch.tensor(vec, dtype=torch.float32, device=VectorOps._DEVICE).unsqueeze(0)

        anchor_mat = torch.tensor(
            [a.anchor_vector if isinstance(a.anchor_vector, (list, tuple))
             else json.loads(a.anchor_vector) if isinstance(a.anchor_vector, str)
             else list(a.anchor_vector)
             for a in candidates],
            dtype=torch.float32, device=VectorOps._DEVICE)

        # batch 空间相似度 (N,)
        cos_sim = torch.nn.functional.cosine_similarity(vec_t, anchor_mat)
        dist = torch.norm(vec_t - anchor_mat, dim=1)
        decay = torch.exp(- (dist * dist) / (2 * 0.5 * 0.5))
        spatial_sim = cos_sim * decay

        # 综合得分
        max_dist = math.sqrt(len(vec_list)) * 0.5
        if max_dist <= 0:
            max_dist = 10.0

        # 距离滤波: 超过 max_dist 的得分为 0
        dist_ratio = torch.clamp(dist / max_dist, 0.0, 1.0)
        scores = spatial_sim * (1.0 - dist_ratio * 0.5)
        scores[dist > max_dist] = 0.0

        # top-k
        topk = min(k, len(candidates))
        if topk == 0:
            return []
        _, top_indices = torch.topk(scores, topk)

        result = []
        for i in range(topk):
            idx = top_indices[i].item()
            if scores[idx].item() > 0:
                result.append(candidates[idx])
        return result

    def get_all(self) -> List[SpatialAnchor]:
        return list(self._anchors.values())

    def size(self) -> int:
        return len(self._anchors)

    def clear(self):
        self.buckets.clear()
        self._anchors.clear()


# ============================================================================
# 认知地图核心
# ============================================================================

class CognitiveMap:
    """LASAR 轻量认知地图
    
    模拟 LASAR 论文的 Latent Cognitive Map 能力：
    1. 空间锚点管理（记忆在认知空间中的位置）
    2. 三类认知 query（回顾/内省/预测）
    3. 空间接近性检索
    4. 认知地图演化
    """

    def __init__(self, db_path: Optional[str] = None, dim: int = 256):
        self.dim = dim
        self.db_path = db_path or os.path.expanduser(
            "~/.openclaw/workspace/cognitive_map.db")
        self._lock = threading.Lock()

        # 内存缓存
        self.hash_map = AnchorHashMap(dim=dim, bucket_count=128)
        self._anchor_cache: Dict[str, SpatialAnchor] = {}
        self._transition_graph: Dict[str, Dict[str, int]] = {}
        # 会话 -> [anchor_id] 序列
        self._session_sequences: Dict[str, List[str]] = {}
        # 聚类缓存
        self._cluster_cache: Dict[str, List[SpatialAnchor]] = {}

        # 初始化数据库
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        # 加载已有数据
        self._load_from_db()

        logger.info(f"CognitiveMap 初始化: db={self.db_path}, dim={dim}")

    def _init_db(self):
        """初始化 SQLite 数据库"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spatial_anchors (
                    anchor_id TEXT PRIMARY KEY,
                    node_id TEXT NOT NULL,
                    context TEXT NOT NULL,
                    anchor_vector TEXT NOT NULL,
                    dimension INTEGER DEFAULT 256,
                    timestamp REAL DEFAULT 0.0,
                    session_key TEXT DEFAULT '',
                    importance REAL DEFAULT 0.5,
                    access_count INTEGER DEFAULT 0,
                    cluster_id TEXT DEFAULT '',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cognitive_queries (
                    query_id TEXT PRIMARY KEY,
                    query_type TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    result TEXT NOT NULL,
                    timestamp REAL DEFAULT 0.0,
                    anchor_id TEXT DEFAULT '',
                    session_key TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.5
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transition_graph (
                    from_anchor_id TEXT NOT NULL,
                    to_anchor_id TEXT NOT NULL,
                    count INTEGER DEFAULT 1,
                    session_key TEXT DEFAULT '',
                    PRIMARY KEY (from_anchor_id, to_anchor_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS anchor_sequences (
                    session_key TEXT NOT NULL,
                    sequence_order INTEGER NOT NULL,
                    anchor_id TEXT NOT NULL,
                    PRIMARY KEY (session_key, sequence_order)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_anchor_session
                ON spatial_anchors(session_key)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_anchor_node
                ON spatial_anchors(node_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_type
                ON cognitive_queries(query_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_query_session
                ON cognitive_queries(session_key)
            """)
            conn.commit()
            conn.close()

    def _load_from_db(self):
        """从数据库加载已有锚点到内存"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM spatial_anchors ORDER BY timestamp ASC")
            rows = cursor.fetchall()

            for row in rows:
                try:
                    anchor = SpatialAnchor.from_dict(dict(row))
                    self._anchor_cache[anchor.anchor_id] = anchor
                    self.hash_map.insert(anchor)
                except Exception as e:
                    logger.warning(f"加载锚点失败 {row.get('anchor_id', '?')}: {e}")

            # 加载转移图
            cursor = conn.execute("SELECT * FROM transition_graph")
            for row in cursor.fetchall():
                d = dict(row)
                src = d['from_anchor_id']
                dst = d['to_anchor_id']
                cnt = d['count']
                if src not in self._transition_graph:
                    self._transition_graph[src] = {}
                self._transition_graph[src][dst] = cnt

            # 加载序列
            cursor = conn.execute(
                "SELECT * FROM anchor_sequences ORDER BY session_key, sequence_order")
            for row in cursor.fetchall():
                d = dict(row)
                sk = d['session_key']
                aid = d['anchor_id']
                if sk not in self._session_sequences:
                    self._session_sequences[sk] = []
                self._session_sequences[sk].append(aid)

            conn.close()
            logger.info(
                f"从数据库加载: {len(self._anchor_cache)} 锚点, "
                f"{len(self._transition_graph)} 转移记录, "
                f"{len(self._session_sequences)} 会话序列")

    # ── 锚点管理 ──

    def add_anchor(self, node_id: str, context: str,
                   session_key: str = "",
                   embedding: Optional[List[float]] = None) -> str:
        """为记忆创建空间锚点"""
        anchor_id = f"anc_{session_key}_{time.time()}_{hashlib.md5(context.encode()[:16]).hexdigest()[:8]}"
        anchor_vector = self.compute_anchor_vector(context, embedding)

        anchor = SpatialAnchor(
            anchor_id=anchor_id,
            node_id=node_id,
            context=context[:500],
            anchor_vector=anchor_vector,
            dimension=self.dim,
            timestamp=time.time(),
            session_key=session_key,
            importance=0.5,
            access_count=0,
        )

        # 写入内存
        self._anchor_cache[anchor_id] = anchor
        self.hash_map.insert(anchor)

        # 更新会话序列
        if session_key:
            if session_key not in self._session_sequences:
                self._session_sequences[session_key] = []
            seq = self._session_sequences[session_key]

            # 记录转移（从上一个锚点到新锚点）
            if seq:
                prev_aid = seq[-1]
                if prev_aid not in self._transition_graph:
                    self._transition_graph[prev_aid] = {}
                self._transition_graph[prev_aid][anchor_id] = \
                    self._transition_graph[prev_aid].get(anchor_id, 0) + 1

            seq.append(anchor_id)

        # 持久化
        self._save_anchor(anchor)
        if session_key:
            self._save_sequence_entry(session_key, anchor_id,
                                      len(self._session_sequences.get(session_key, [])))
            self._save_transition(anchor_id, session_key)

        return anchor_id

    def compute_anchor_vector(self, context: str,
                               embedding: Optional[List[float]] = None) -> List[float]:
        """压缩上下文为锚点向量（256 维确定性投影）

        优先级:
        1. 如果传入了 embedding，直接使用（截断/填充到 dim）
        2. 否则用随机投影
        """
        if embedding:
            if len(embedding) >= self.dim:
                return embedding[:self.dim]
            else:
                return embedding + [0.0] * (self.dim - len(embedding))
        return VectorOps.randomized_projection(context, self.dim)

    def get_nearby_anchors(self, anchor_vector: Union[str, List[float]],
                            k: int = 5, radius: float = 0.3) -> List[SpatialAnchor]:
        """在认知空间中找最近的锚点（torch batch 实现）"""
        nearby = self.hash_map.find_nearby(anchor_vector, k=k, radius=radius)
        if nearby:
            return nearby

        # 降级: 全量扫描（torch batch）
        if not self._anchor_cache:
            return []

        anchors = list(self._anchor_cache.values())
        if isinstance(anchor_vector, torch.Tensor):
            q = anchor_vector.detach().clone().to(dtype=torch.float32).unsqueeze(0)
        else:
            q = torch.tensor(anchor_vector, dtype=torch.float32).unsqueeze(0)
        mat = torch.tensor(
            [a.anchor_vector if isinstance(a.anchor_vector, (list, tuple))
             else json.loads(a.anchor_vector) if isinstance(a.anchor_vector, str)
             else list(a.anchor_vector)
             for a in anchors],
            dtype=torch.float32)

        max_dist = math.sqrt(len(anchor_vector)) * 0.5
        if max_dist <= 0:
            max_dist = 10.0

        cos_sim = torch.nn.functional.cosine_similarity(q, mat)
        dist = torch.norm(q - mat, dim=1)
        decay = torch.exp(- (dist * dist) / (2 * 0.5 * 0.5))
        spatial_sim = cos_sim * decay

        dist_ratio = torch.clamp(dist / max_dist, 0.0, 1.0)
        scores = spatial_sim * (1.0 - dist_ratio * 0.5)
        scores[dist > max_dist] = 0.0

        topk = min(k, len(anchors))
        if topk == 0:
            return []
        _, top_indices = torch.topk(scores, topk)
        return [anchors[i.item()] for i in top_indices if scores[i.item()] > 0]

    def get_anchor_density(self, region_vector: List[float],
                            radius: float = 0.5) -> float:
        """计算某区域的锚点密度（认知熟悉度）

        返回 [0, 1] 范围，越密集越熟悉
        """
        if not self._anchor_cache:
            return 0.0

        nearby = self.get_nearby_anchors(region_vector, k=20, radius=radius)
        if not nearby:
            return 0.0

        density = len(nearby) / 20.0
        # 批量距离
        if isinstance(region_vector, torch.Tensor):
            q = region_vector.detach().clone().to(dtype=torch.float32)
        else:
            q = torch.tensor(region_vector, dtype=torch.float32)
        nearby_mat = torch.tensor(
            [a.anchor_vector if isinstance(a.anchor_vector, (list, tuple))
             else json.loads(a.anchor_vector) if isinstance(a.anchor_vector, str)
             else list(a.anchor_vector)
             for a in nearby],
            dtype=torch.float32)
        distances = torch.norm(q - nearby_mat, dim=1)
        avg_dist = distances.mean().item() if distances.numel() > 0 else 1.0
        return density * (1.0 - avg_dist * 0.3)

    # ── 三类认知 query（轻量化核心）──

    def retrospective_query(self, target_anchor: str,
                            lookback: int = 3) -> str:
        """回顾 query：在这个位置之前发生了什么？

        LASAR 原文：Agent 回顾之前经过的场景和事件。
        我们：给定一个锚点，检索这个节点之前相邻的 N 个节点的摘要。

        Args:
            target_anchor: 目标锚点 ID
            lookback: 往前看多少步

        Returns:
            回顾结果文本
        """
        anchor = self._anchor_cache.get(target_anchor)
        if not anchor:
            return "没有找到目标锚点"

        session = anchor.session_key
        seq = self._session_sequences.get(session, [])

        # 找目标锚点在序列中的位置
        try:
            idx = seq.index(target_anchor)
        except ValueError:
            return "目标锚点不在序列中"

        # 取前 lookback 个锚点
        start = max(0, idx - lookback)
        prev_anchors = seq[start:idx]

        if not prev_anchors:
            return "这是序列的起点，之前没有记忆。"

        # 拼接上下文
        parts = []
        for aid in reversed(prev_anchors):
            a = self._anchor_cache.get(aid)
            if a:
                parts.append(f"[{a.context[:100]}]")

        result = "之前发生了什么：\n" + "\n".join(parts)
        # 写 query 记录
        self._save_query("retrospective", f"回顾锚点 {target_anchor}",
                        result, anchor_id=target_anchor, session_key=session)
        return result

    def introspective_query(self, current_context: str,
                            current_anchor: Optional[str] = None) -> str:
        """内省 query：我现在在认知空间的什么位置？

        LASAR 原文：Agent 判断自己在厨房还是客厅。
        我们：给定当前上下文，找到最近的锚点 + 锚点所在聚类。

        Args:
            current_context: 当前上下文文本
            current_anchor: 当前锚点 ID（可选）

        Returns:
            内省结果文本（你在一个什么类型的认知区域）
        """
        vec: Union[str, List[float]] = self.compute_anchor_vector(current_context)

        if current_anchor and current_anchor in self._anchor_cache:
            vec = self._anchor_cache[current_anchor].anchor_vector

        nearby = self.get_nearby_anchors(vec, k=5)

        if not nearby:
            return "这是一个新的认知区域，还没有建立空间锚点。"

        # 计算中心位置
        centroid = VectorOps.mean([a.anchor_vector for a in nearby])
        density = self.get_anchor_density(centroid)

        # 分析附近锚点的主题分布
        topics: Dict[str, Any] = {}
        for a in nearby:
            # 从 context 中提取主题词（前几个非停用词字符）
            words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', a.context[:50])
            for w in words[:3]:
                topics[w] = topics.get(w, 0) + 1

        top_topics = [w for w, _ in sorted(
            topics.items(), key=lambda x: x[1], reverse=True)[:5]]

        # 计算新锚点位置与中心点的偏移
        dist_to_centroid = VectorOps.euclidean_distance(vec, centroid)
        is_central = dist_to_centroid < 0.3

        # 构建描述
        familiarity = "熟悉" if density > 0.5 else "较熟悉" if density > 0.2 else "陌生"
        location_desc = (
            f"你在一个{familiarity}的认知区域，"
            f"附近有 {len(nearby)} 个相关记忆。"
        )
        if top_topics:
            location_desc += f"主要主题: {'、'.join(top_topics[:3])}。"

        if is_central:
            location_desc += "你处在这个区域的中心位置。"
        else:
            location_desc += f"你离区域中心还有一定距离（偏移 {dist_to_centroid:.2f}）。"

        result = location_desc
        self._save_query("introspective", current_context[:100],
                        result, session_key=nearby[0].session_key if nearby else "")
        return result

    def prospective_query(self, current_anchor: str,
                          direction: str = "forward") -> str:
        """预测 query：向前走可能遇到什么？

        LASAR 原文：Agent 预测走廊出去是什么。
        我们：给定当前锚点，从历史导航模式推断下一步。

        Args:
            current_anchor: 当前锚点 ID
            direction: "forward"（预测下一步）或 "explore"（探索附近）

        Returns:
            预测结果
        """
        if current_anchor not in self._anchor_cache:
            return "没有找到当前锚点"

        anchor = self._anchor_cache[current_anchor]
        session = anchor.session_key

        # 方法 1: 从转移图看常见下一步
        transitions = self._transition_graph.get(current_anchor, {})
        if transitions:
            sorted_transitions = sorted(
                transitions.items(), key=lambda x: x[1], reverse=True)

            predictions = []
            for next_aid, cnt in sorted_transitions[:3]:
                next_anchor = self._anchor_cache.get(next_aid)
                if next_anchor:
                    predictions.append(f"→ {next_anchor.context[:80]} (出现 {cnt} 次)")

            if predictions:
                result = "基于历史导航模式，下一步可能遇到:\n" + "\n".join(predictions)
                self._save_query("prospective", f"预测方向 {direction}",
                                result, anchor_id=current_anchor,
                                session_key=session)
                return result

        # 方法 2: 用空间接近性探索附近
        nearby = self.get_nearby_anchors(anchor.anchor_vector, k=5)
        nearby = [a for a in nearby if a.anchor_id != current_anchor]

        if nearby:
            parts = []
            for a in nearby[:3]:
                sim = VectorOps.spatial_similarity(
                    anchor.anchor_vector, a.anchor_vector)
                parts.append(f"附近: {a.context[:80]} (相似度 {sim:.2f})")

            result = "附近可能相关的记忆:\n" + "\n".join(parts)
        else:
            result = "暂无足够导航数据来预测下一步。"

        self._save_query("prospective", f"预测方向 {direction}",
                        result, anchor_id=current_anchor,
                        session_key=session)
        return result

    def run_cognitive_queries(self, current_context: str,
                               session_key: str = "") -> Dict:
        """一次性执行三类认知 query，返回结构化结果

        Args:
            current_context: 当前上下文
            session_key: 会话 Key

        Returns:
            {
                "retrospective": "回顾结果",
                "introspective": "内省结果",
                "prospective": "预测结果",
                "density": 0.5,
                "nearby_count": 5
            }
        """
        vec = self.compute_anchor_vector(current_context)
        nearby = self.get_nearby_anchors(vec, k=3)

        ret = "暂无回顾数据"
        intros = self.introspective_query(current_context)
        pros = "暂无预测数据"

        # 取最近锚点做回顾和预测
        if nearby:
            target = nearby[0]
            ret = self.retrospective_query(target.anchor_id, lookback=3)
            pros = self.prospective_query(target.anchor_id)

        density = self.get_anchor_density(vec)

        return {
            "retrospective": ret,
            "introspective": intros,
            "prospective": pros,
            "density": round(density, 3),
            "nearby_count": len(nearby),
        }

    # ── 空间接近性检索 ──

    def proximity_retrieve(self, current_context: str,
                            query_embedding: Optional[List[float]] = None,
                            top_k: int = 5) -> List[Dict]:
        """空间接近性检索：torch batch 实现"""
        vec = query_embedding or self.compute_anchor_vector(current_context)
        nearby = self.get_nearby_anchors(vec, k=top_k)

        if not nearby:
            return []

        if isinstance(vec, torch.Tensor):
            vec_t = vec.detach().clone().to(dtype=torch.float32).unsqueeze(0)
        else:
            vec_t = torch.tensor(vec, dtype=torch.float32).unsqueeze(0)
        nearby_t = torch.tensor(
            [a.anchor_vector if isinstance(a.anchor_vector, (list, tuple))
             else json.loads(a.anchor_vector) if isinstance(a.anchor_vector, str)
             else list(a.anchor_vector)
             for a in nearby],
            dtype=torch.float32)

        # batch 空间相似度
        cos_sim = torch.nn.functional.cosine_similarity(vec_t, nearby_t)
        d = torch.norm(vec_t - nearby_t, dim=1)
        decay = torch.exp(- (d * d) / (2 * 0.5 * 0.5))
        spatial_sims = cos_sim * decay

        results = []
        for i, anchor in enumerate(nearby):
            proximity = self.compute_proximity_score(
                anchor, SpatialAnchor(
                    anchor_id="query",
                    node_id="",
                    context=current_context,
                    anchor_vector=vec,
                    session_key="",
                ),
                batch_spatial=spatial_sims[i].item(),
            )
            results.append({
                "anchor_id": anchor.anchor_id,
                "node_id": anchor.node_id,
                "context": anchor.context[:200],
                "similarity": round(cos_sim[i].item(), 4),
                "distance": round(d[i].item(), 4),
                "proximity_score": round(proximity, 4),
            })

        results.sort(key=lambda x: x["proximity_score"], reverse=True)
        return results

    def spatial_similarity(self, vec1: List[float],
                           vec2: List[float]) -> float:
        """空间相似度 = 余弦相似度 × 高斯距离衰减（torch 实现）"""
        return VectorOps.spatial_similarity(vec1, vec2)

    def compute_proximity_score(self, anchor_a: SpatialAnchor,
                                 anchor_b: SpatialAnchor,
                                 batch_spatial: Optional[float] = None) -> float:
        """计算两个锚点的接近度（空间 + 时间 + 语义加权）

        权重: 空间 0.5 + 时间 0.2 + 语义 0.3
        """
        if batch_spatial is not None:
            spatial = batch_spatial
        else:
            spatial = VectorOps.spatial_similarity(
                anchor_a.anchor_vector, anchor_b.anchor_vector)

        # 时间接近性（越近越高）
        time_diff = abs(anchor_a.timestamp - anchor_b.timestamp)
        temporal = math.exp(-time_diff / 3600)  # 1小时衰减

        # 语义关键词重叠
        words_a = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}',
                                 anchor_a.context[:100]))
        words_b = set(re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}',
                                 anchor_b.context[:100]))
        overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)

        proximity = 0.5 * spatial + 0.2 * temporal + 0.3 * overlap
        return proximity

    # ── 认知地图演化 ──

    def merge_close_anchors(self, threshold: float = 0.1):
        """合并相近的锚点（空间聚类，torch 距离矩阵实现）

        当两个锚点的空间距离 < threshold 时，合并它们。
        """
        anchors = list(self._anchor_cache.values())
        if len(anchors) < 2:
            return

        # 用 torch 一次算完整距离矩阵
        mat = torch.tensor(
            [a.anchor_vector if isinstance(a.anchor_vector, (list, tuple))
             else json.loads(a.anchor_vector) if isinstance(a.anchor_vector, str)
             else list(a.anchor_vector)
             for a in anchors],
            dtype=torch.float32)
        # (N, 1, dim) - (1, N, dim) -> (N, N, dim) -> norm -> (N, N) 距离矩阵
        dist_mat = torch.cdist(mat, mat, p=2.0)  # (N, N)

        to_merge = []
        for i in range(len(anchors)):
            for j in range(i + 1, len(anchors)):
                if dist_mat[i, j].item() < threshold:
                    to_merge.append((anchors[i], anchors[j]))

        merged_count = 0
        for a, b in to_merge:
            if a.anchor_id in self._anchor_cache and b.anchor_id in self._anchor_cache:
                # 保留重要性高的
                keep = a if a.importance >= b.importance else b
                remove = b if keep == a else a
                # 保留较长的 context
                if len(remove.context) > len(keep.context):
                    keep.context = remove.context
                keep.access_count += remove.access_count
                # 删除被合并的
                self._remove_anchor(remove.anchor_id)
                merged_count += 1

        if merged_count:
            logger.info(f"合并了 {merged_count} 对相近锚点（阈值 {threshold}）")

    def update_anchor_importance(self, anchor_id: str, access_count: int = 1):
        """更新锚点活跃度（频繁访问的锚点不动）"""
        if anchor_id not in self._anchor_cache:
            return

        anchor = self._anchor_cache[anchor_id]
        anchor.access_count += access_count
        # 活跃度越高，重要性逐渐衰减（但不会低于 0.1）
        anchor.importance = max(0.1, 0.5 * math.exp(-anchor.access_count * 0.01))
        self._save_anchor(anchor)

    def get_cognitive_landscape(self,
                                 region: Optional[List[float]] = None) -> Dict:
        """生成当前认知地图的全景（锚点分布图）"""
        if region:
            nearby = self.get_nearby_anchors(region, k=50)
            anchors = nearby
        else:
            anchors = list(self._anchor_cache.values())

        total = len(self._anchor_cache)
        active = sum(1 for a in self._anchor_cache.values() if a.importance > 0.3)
        avg_importance = sum(a.importance for a in self._anchor_cache.values()) / max(total, 1)

        # 会话分布
        session_dist: Dict[str, Any] = {}
        for a in self._anchor_cache.values():
            sk = a.session_key or "unknown"
            session_dist[sk] = session_dist.get(sk, 0) + 1

        # 聚类统计
        cluster_dist: Dict[str, Any] = {}
        for a in self._anchor_cache.values():
            cid = a.cluster_id or "unclustered"
            cluster_dist[cid] = cluster_dist.get(cid, 0) + 1

        return {
            "total_anchors": total,
            "active_anchors": active,
            "avg_importance": round(avg_importance, 3),
            "in_view": len(anchors),
            "session_distribution": session_dist,
            "cluster_distribution": cluster_dist,
            "transition_graph_edges": sum(
                len(v) for v in self._transition_graph.values()),
            "session_sequences": len(self._session_sequences),
        }

    # ── 持久化 ──

    def save(self):
        """全量持久化"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            # 保存所有锚点
            for anchor in self._anchor_cache.values():
                self._upsert_anchor_db(conn, anchor)
            # 保存转移图
            for src, targets in self._transition_graph.items():
                for dst, cnt in targets.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO transition_graph "
                        "(from_anchor_id, to_anchor_id, count) VALUES (?, ?, ?)",
                        (src, dst, cnt))
            # 保存序列
            for sk, seq in self._session_sequences.items():
                for i, aid in enumerate(seq):
                    conn.execute(
                        "INSERT OR REPLACE INTO anchor_sequences "
                        "(session_key, sequence_order, anchor_id) VALUES (?, ?, ?)",
                        (sk, i, aid))
            conn.commit()
            conn.close()
        logger.info(f"CognitiveMap 持久化: {len(self._anchor_cache)} 锚点")

    def load(self):
        """重新加载"""
        self._anchor_cache.clear()
        self.hash_map.clear()
        self._transition_graph.clear()
        self._session_sequences.clear()
        self._load_from_db()

    # ── 内部保存方法 ──

    def _save_anchor(self, anchor: SpatialAnchor):
        """保存单个锚点到数据库"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            self._upsert_anchor_db(conn, anchor)
            conn.commit()
            conn.close()

    def _upsert_anchor_db(self, conn, anchor: SpatialAnchor):
        conn.execute("""
            INSERT OR REPLACE INTO spatial_anchors
            (anchor_id, node_id, context, anchor_vector, dimension,
             timestamp, session_key, importance, access_count,
             cluster_id, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            anchor.anchor_id,
            anchor.node_id,
            anchor.context,
            json.dumps(anchor.anchor_vector, ensure_ascii=False),
            anchor.dimension,
            anchor.timestamp,
            anchor.session_key,
            anchor.importance,
            anchor.access_count,
            anchor.cluster_id or "",
            json.dumps(anchor.metadata, ensure_ascii=False),
        ))

    def _save_query(self, qtype: str, qtext: str, result: str,
                    anchor_id: str = "", session_key: str = ""):
        """保存 query 记录"""
        query_id = f"q_{qtype}_{time.time()}_{hashlib.md5(qtext.encode()[:8]).hexdigest()[:8]}"
        query = CognitiveQuery(
            query_id=query_id,
            query_type=qtype,
            query_text=qtext[:200],
            result=result[:500],
            timestamp=time.time(),
            anchor_id=anchor_id or "",
            session_key=session_key or "",
        )
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("""
                    INSERT INTO cognitive_queries
                    (query_id, query_type, query_text, result, timestamp,
                     anchor_id, session_key, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    query.query_id, query.query_type, query.query_text,
                    query.result, query.timestamp, query.anchor_id,
                    query.session_key, query.confidence,
                ))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"保存 query 失败: {e}")

    def _save_sequence_entry(self, session_key: str,
                              anchor_id: str, order: int):
        """保存序列条目"""
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "INSERT OR REPLACE INTO anchor_sequences "
                    "(session_key, sequence_order, anchor_id) VALUES (?, ?, ?)",
                    (session_key, order, anchor_id))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"保存序列条目失败: {e}")

    def _save_transition(self, anchor_id: str, session_key: str):
        """保存转移信息"""
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "INSERT OR REPLACE INTO transition_graph "
                    "(from_anchor_id, to_anchor_id, count, session_key) "
                    "VALUES (?, ?, COALESCE((SELECT count + 1 FROM transition_graph "
                    "WHERE from_anchor_id=? AND to_anchor_id=?), 1), ?)",
                    (anchor_id, anchor_id, anchor_id, anchor_id, session_key))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"保存转移信息失败: {e}")

    def _remove_anchor(self, anchor_id: str):
        """从内存和数据库删除锚点"""
        if anchor_id in self._anchor_cache:
            del self._anchor_cache[anchor_id]
        self.hash_map.remove(anchor_id)
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("DELETE FROM spatial_anchors WHERE anchor_id=?",
                             (anchor_id,))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"删除锚点失败: {e}")

    def _delete_anchor(self, anchor_id: str):
        """公开删除接口"""
        self._remove_anchor(anchor_id)

    # ── 统计数据 ──

    def get_stats(self) -> Dict:
        """获取认知地图统计"""
        return {
            "total_anchors": len(self._anchor_cache),
            "total_queries": 0,
            "hash_map_buckets": len(self.hash_map.buckets),
            "transition_edges": sum(len(v) for v in self._transition_graph.values()),
            "session_count": len(self._session_sequences),
            "dimension": self.dim,
        }


# ============================================================================
# 全局单例
# ============================================================================

_cognitive_map_instance = None


def get_cognitive_map(db_path: Optional[str] = None,
                      dim: int = 256) -> CognitiveMap:
    """获取认知地图实例（单例）"""
    global _cognitive_map_instance
    if _cognitive_map_instance is None:
        _cognitive_map_instance = CognitiveMap(db_path=db_path, dim=dim)
    return _cognitive_map_instance


__all__ = [
    'CognitiveMap',
    'SpatialAnchor',
    'CognitiveQuery',
    'AnchorHashMap',
    'VectorOps',
    'get_cognitive_map',
]
