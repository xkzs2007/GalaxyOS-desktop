#!/usr/bin/env python3
"""
AriGraph: 空间拓扑记忆模块

基于 AriGraph (arXiv:2407.04363) 的核心思想：
Agent 在探索环境时从头构建 KG 世界模型，语义记忆和情景记忆融合到同一张图里。
"场景"不是文本标签字段，而是图拓扑——两个节点的图距离=空间关系，
近邻=在同一场景，远邻=在不同场景。

This module implements SpatialTopologyGraph:
- 场景管理：注册、查找、关联场景节点
- 导航记忆：记录和检索用户在场景间的移动
- 空间检索：在当前场景附近找关联记忆
- 拓扑推断：根据实体推断用户所在场景
- 别名消歧：xiaoyi → 小艺
- SQLite 持久化，线程安全
"""

import os
import json
import time
import logging
import sqlite3
import threading
import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


# ============================================================================
# 数据模型
# ============================================================================

@dataclass
class SpatialNode:
    """空间拓扑节点"""
    node_id: str                # 场景/实体的唯一ID
    label: str                  # 场景名称（如 "A项目"、"B文件夹"）
    node_type: str              # "scene", "entity", "context"
    embedding: List[float] = field(default_factory=list)  # 语义嵌入
    center_position: List[float] = field(default_factory=list)  # 拓扑坐标
    parent_id: Optional[str] = None     # 父场景（层级关系）
    children_ids: List[str] = field(default_factory=list)  # 子场景
    aliases: List[str] = field(default_factory=list)  # 别名
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SpatialNode":
        valid_keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


@dataclass
class SpatialEdge:
    """空间拓扑边"""
    edge_id: str
    src_id: str
    dst_id: str
    relation: str               # "inside", "connected_to", "near", "far_from"
    weight: float = 1.0         # 关系强度
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SpatialEdge":
        valid_keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


@dataclass
class NavigationRecord:
    """导航记录 — 用户从A到B的过程"""
    record_id: str
    from_node: str
    to_node: str
    path: List[str] = field(default_factory=list)  # 经过的节点
    context: str = ""             # 导航背景（为什么去）
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "NavigationRecord":
        valid_keys = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


# ============================================================================
# 关系类型常量 & 别名映射
# ============================================================================

RELATION_INSIDE = "inside"
RELATION_CONNECTED = "connected_to"
RELATION_NEAR = "near"
RELATION_FAR = "far_from"

# 默认别名映射（用户可在 register_scene 时指定更多）
DEFAULT_ALIAS_MAP = {
    "xiaoyi": "小艺",
    "claw": "小艺 Claw",
    "agent": "小艺 Claw",
    "ai": "小艺 Claw",
}


# ============================================================================
# 默认数据库路径
# ============================================================================

DEFAULT_DB_DIR = os.path.expanduser("~/.openclaw/workspace")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "spatial_topology.db")


# ============================================================================
# SpatialTopologyGraph 核心
# ============================================================================

class SpatialTopologyGraph:
    """
    空间拓扑图

    核心思路（AriGraph）：
    场景不是字符串字段，是图上的一个子图。
    "你在A项目的B文件夹下问的问题" = 从"你"→"A项目"→"B文件夹"的一条图路径。
    下次问类似问题时，图拓扑告诉你"这个记忆在空间上离你当前语境最近"。

    特性：
    - SQLite 持久化
    - 线程安全（_lock 模式，参考 DAGContextManager）
    - 别名消歧
    - 拓扑坐标通过邻居关系推断
    - 场景层级：用户 → 项目 → 文件夹 → 文件
    - 关系类型：inside, connected_to, near, far_from
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._lock = threading.Lock()

        # 确保目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # 初始化数据库
        self._init_db()

        # 别名缓存（node_id → aliases, 反查用）
        self._alias_cache: Dict[str, List[str]] = {}
        self._alias_reverse: Dict[str, str] = {}  # alias → label (case-insensitive)
        self._load_alias_cache()

        logger.info(f"SpatialTopologyGraph initialized: db={self.db_path}")

    # ========================================================================
    # 数据库初始化
    # ========================================================================

    def _init_db(self):
        """初始化 SQLite 数据库表"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            # 节点表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spatial_nodes (
                    node_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    node_type TEXT NOT NULL DEFAULT 'scene',
                    embedding TEXT DEFAULT '[]',
                    center_position TEXT DEFAULT '[]',
                    parent_id TEXT,
                    children_ids TEXT DEFAULT '[]',
                    aliases TEXT DEFAULT '[]',
                    metadata TEXT DEFAULT '{}',
                    created_at REAL DEFAULT (strftime('%s','now')),
                    updated_at REAL DEFAULT (strftime('%s','now'))
                )
            """)

            # 边表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS spatial_edges (
                    edge_id TEXT PRIMARY KEY,
                    src_id TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    relation TEXT NOT NULL DEFAULT 'connected_to',
                    weight REAL DEFAULT 1.0,
                    timestamp REAL DEFAULT (strftime('%s','now')),
                    FOREIGN KEY (src_id) REFERENCES spatial_nodes(node_id),
                    FOREIGN KEY (dst_id) REFERENCES spatial_nodes(node_id)
                )
            """)

            # 导航记录表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS navigation_records (
                    record_id TEXT PRIMARY KEY,
                    from_node TEXT NOT NULL,
                    to_node TEXT NOT NULL,
                    path TEXT DEFAULT '[]',
                    context TEXT DEFAULT '',
                    timestamp REAL DEFAULT (strftime('%s','now')),
                    FOREIGN KEY (from_node) REFERENCES spatial_nodes(node_id),
                    FOREIGN KEY (to_node) REFERENCES spatial_nodes(node_id)
                )
            """)

            # 索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sn_label ON spatial_nodes(label)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sn_type ON spatial_nodes(node_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sn_parent ON spatial_nodes(parent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_se_src ON spatial_edges(src_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_se_dst ON spatial_edges(dst_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_se_rel ON spatial_edges(relation)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nav_from ON navigation_records(from_node)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_nav_to ON navigation_records(to_node)")

            conn.commit()
            conn.close()

    def _load_alias_cache(self):
        """从数据库加载别名缓存"""
        self._alias_cache = {}
        self._alias_reverse = {}
        try:
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.execute(
                    "SELECT node_id, label, aliases FROM spatial_nodes"
                )
                for row in cursor.fetchall():
                    node_id, label, aliases_json = row
                    try:
                        aliases = json.loads(aliases_json) if aliases_json else []
                    except (json.JSONDecodeError, TypeError):
                        aliases = []
                    self._alias_cache[node_id] = aliases
                    self._alias_reverse[label.lower()] = label
                    for alias in aliases:
                        self._alias_reverse[alias.lower()] = alias
                conn.close()
        except Exception as e:
            logger.warning(f"加载别名缓存失败: {e}")

    # ========================================================================
    # 场景管理
    # ========================================================================

    def register_scene(
        self,
        label: str,
        scene_type: str = "context",
        parent_label: Optional[str] = None,
        embedding: Optional[List[float]] = None,
        aliases: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """
        注册一个场景节点。

        Args:
            label: 场景名称（如 "A项目", "B文件夹"）
            scene_type: 节点类型 ("scene", "entity", "context")
            parent_label: 父场景名称（自动建立层级关系）
            embedding: 语义嵌入（可选）
            aliases: 别名列表（可选）
            metadata: 附加元数据

        Returns:
            node_id
        """
        node_id = f"spatial_{scene_type}_{int(time.time()*1000)}_{_hash_str(label)[:8]}"

        # 如果已存在同名节点，返回现有 ID
        existing = self.get_scene(label)
        if existing:
            logger.info(f"场景 '{label}' 已存在, node_id={existing.node_id}")
            return existing.node_id

        _aliases = aliases or []
        # 添加默认别名
        for key, val in DEFAULT_ALIAS_MAP.items():
            if label.lower() == val.lower() and key not in [a.lower() for a in _aliases]:
                _aliases.append(key)

        # 父场景
        parent_id = None
        if parent_label:
            parent = self.get_scene(parent_label)
            if parent:
                parent_id = parent.node_id

        node = SpatialNode(
            node_id=node_id,
            label=label,
            node_type=scene_type,
            embedding=embedding or [],
            parent_id=parent_id,
            aliases=_aliases,
            metadata=metadata or {},
        )

        self._save_node(node)

        # 更新别名字典
        self._alias_cache[node_id] = _aliases
        self._alias_reverse[label.lower()] = label
        for alias in _aliases:
            self._alias_reverse[alias.lower()] = alias

        # 如果指定了父场景，建立 inside 关系
        if parent_id:
            self._ensure_edge(
                src_id=node_id,
                dst_id=parent_id,
                relation=RELATION_INSIDE,
                weight=1.0,
            )
            # 更新父节点的 children_ids
            self._add_child_to_parent(node_id, parent_id)
            logger.info(f"场景 '{label}' 注册于 '{parent_label or 'root'}' 之下")

        logger.info(f"场景注册: {label} (type={scene_type}, id={node_id})")
        return node_id

    def get_scene(self, label_or_alias: str) -> Optional[SpatialNode]:
        """
        通过名称或别名查找场景。

        支持模糊匹配和消歧：
        1. 精确匹配 label
        2. 精确匹配 alias（不区分大小写）
        3. 模糊匹配（包含关系）
        """
        query = label_or_alias.strip()

        # 1. 精确匹配 label
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM spatial_nodes WHERE label = ?", (query,)
            )
            row = cursor.fetchone()
            if row:
                conn.close()
                return self._row_to_node(dict(row))

            # 2. 大小写不敏感 label 匹配
            cursor = conn.execute(
                "SELECT * FROM spatial_nodes WHERE LOWER(label) = ?",
                (query.lower(),)
            )
            row = cursor.fetchone()
            if row:
                conn.close()
                return self._row_to_node(dict(row))

            # 3. 别名匹配
            # 遍历所有节点，检查别名列表
            cursor = conn.execute("SELECT node_id, label, aliases FROM spatial_nodes")
            for row in cursor.fetchall():
                try:
                    aliases = json.loads(row["aliases"]) if row["aliases"] else []
                except (json.JSONDecodeError, TypeError):
                    aliases = []
                if any(a.lower() == query.lower() for a in aliases):
                    # 找到别名的全量数据
                    cursor2 = conn.execute(
                        "SELECT * FROM spatial_nodes WHERE node_id = ?", (row["node_id"],)
                    )
                    node_row = cursor2.fetchone()
                    conn.close()
                    return self._row_to_node(dict(node_row)) if node_row else None

            # 4. 模糊匹配（子串包含）
            cursor = conn.execute(
                "SELECT * FROM spatial_nodes WHERE label LIKE ?",
                (f"%{query}%",)
            )
            rows = cursor.fetchall()
            conn.close()

            if len(rows) == 1:
                return self._row_to_node(dict(rows[0]))
            elif len(rows) > 1:
                # 多个匹配，返回首字匹配
                for r in rows:
                    dr = dict(r)
                    if dr["label"].startswith(query):
                        return self._row_to_node(dr)
                # 仍然返回第一个
                return self._row_to_node(dict(rows[0]))

            return None

    def relate_scenes(
        self,
        src_label: str,
        dst_label: str,
        relation: str = "connected_to",
        weight: float = 1.0,
    ) -> bool:
        """建立两个场景之间的拓扑关系"""
        src = self.get_scene(src_label)
        dst = self.get_scene(dst_label)
        if not src:
            logger.warning(f"relate_scenes: 源场景 '{src_label}' 未找到")
            return False
        if not dst:
            logger.warning(f"relate_scenes: 目标场景 '{dst_label}' 未找到")
            return False

        edge_id = f"edge_{src.node_id}_{dst.node_id}_{int(time.time())}"
        edge = SpatialEdge(
            edge_id=edge_id,
            src_id=src.node_id,
            dst_id=dst.node_id,
            relation=relation,
            weight=weight,
            timestamp=time.time(),
        )
        self._save_edge(edge)

        # 更新拓扑坐标
        self._infer_position(src.node_id)
        self._infer_position(dst.node_id)

        return True

    def get_scene_neighbors(
        self,
        label: str,
        depth: int = 1,
    ) -> List[SpatialNode]:
        """
        获取场景的拓扑邻居（图遍历）。

        Args:
            label: 场景名称
            depth: 遍历深度

        Returns:
            邻居节点列表
        """
        node = self.get_scene(label)
        if not node:
            return []

        visited = {node.node_id}
        queue = [(node.node_id, 0)]
        neighbors = []

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            while queue:
                current_id, current_depth = queue.pop(0)
                if current_depth > 0 and current_depth <= depth:
                    cursor = conn.execute(
                        "SELECT * FROM spatial_nodes WHERE node_id = ?", (current_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        neighbors.append(self._row_to_node(dict(row)))

                if current_depth < depth:
                    # 出边邻居
                    cursor = conn.execute(
                        "SELECT dst_id FROM spatial_edges WHERE src_id = ?",
                        (current_id,)
                    )
                    for row in cursor.fetchall():
                        if row["dst_id"] not in visited:
                            visited.add(row["dst_id"])
                            queue.append((row["dst_id"], current_depth + 1))

                    # 入边邻居
                    cursor = conn.execute(
                        "SELECT src_id FROM spatial_edges WHERE dst_id = ?",
                        (current_id,)
                    )
                    for row in cursor.fetchall():
                        if row["src_id"] not in visited:
                            visited.add(row["src_id"])
                            queue.append((row["src_id"], current_depth + 1))

            conn.close()

        return neighbors

    # ========================================================================
    # 导航记忆
    # ========================================================================

    def record_navigation(
        self,
        from_label: str,
        to_label: str,
        context: str = "",
    ) -> Optional[NavigationRecord]:
        """
        记录用户从A到B的导航过程。

        自动：
        1. BFS 找到 A→B 的最短路径
        2. 更新路径上所有边的权重
        3. 保存导航记录
        """
        src = self.get_scene(from_label)
        dst = self.get_scene(to_label)
        if not src or not dst:
            logger.warning(f"record_navigation: 场景未找到 ({from_label} → {to_label})")
            return None

        # BFS 找路径
        path = self._bfs_path(src.node_id, dst.node_id)
        if path:
            # 更新路径上边的权重
            for i in range(len(path) - 1):
                self._strengthen_edge(path[i], path[i + 1])
        else:
            path = [src.node_id, dst.node_id]
            logger.info(f"导航路径未找到，创建直接连接: {from_label} → {to_label}")

            # 创建 connected_to 边
            edge_id = f"edge_{src.node_id}_{dst.node_id}_nav_{int(time.time())}"
            edge = SpatialEdge(
                edge_id=edge_id,
                src_id=src.node_id,
                dst_id=dst.node_id,
                relation=RELATION_CONNECTED,
                weight=0.5,
                timestamp=time.time(),
            )
            self._save_edge(edge)

        record_id = f"nav_{int(time.time()*1000)}_{_hash_str(from_label+to_label)[:8]}"
        record = NavigationRecord(
            record_id=record_id,
            from_node=src.node_id,
            to_node=dst.node_id,
            path=path,
            context=context,
            timestamp=time.time(),
        )
        self._save_navigation(record)

        logger.info(f"导航记录: {from_label} → {to_label} ({len(path)} hops)")
        return record

    def get_navigation_path(
        self,
        from_label: str,
        to_label: str,
        max_depth: int = 5,
    ) -> List[str]:
        """BFS 找到从 A 到 B 的最短导航路径"""
        src = self.get_scene(from_label)
        dst = self.get_scene(to_label)
        if not src or not dst:
            return []

        path = self._bfs_path(src.node_id, dst.node_id, max_depth=max_depth)
        if not path:
            return []

        # 转换 node_id → label
        labels = []
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            for nid in path:
                cursor = conn.execute(
                    "SELECT label FROM spatial_nodes WHERE node_id = ?", (nid,)
                )
                row = cursor.fetchone()
                labels.append(row["label"] if row else nid)
            conn.close()
        return labels

    def get_common_scenes(
        self,
        contexts: List[str],
        top_k: int = 3,
    ) -> List[str]:
        """
        给定多个上下文，找出它们在拓扑空间中最"近"的场景。

        策略：
        1. 每个上下文分别查找最近的场景
        2. 所有上下文共同邻居取交集
        3. 按图距离之和排序（距离越近 = 共同场景）
        """
        if not contexts:
            return []

        # 每个上下文找其场景节点
        scene_sets = []
        for ctx in contexts:
            node = self.get_scene(ctx)
            if node:
                scene_sets.append({node.node_id})
                # 加一阶邻居
                neighbors = self.get_scene_neighbors(ctx, depth=1)
                scene_sets[-1].update(n.node_id for n in neighbors)

        if not scene_sets:
            return []

        # 交集
        common = set.intersection(*scene_sets) if len(scene_sets) > 1 else scene_sets[0]
        if not common:
            return []

        # 按度排序（连接越多的场景越重要）
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            scored = []
            for nid in common:
                cursor = conn.execute(
                    "SELECT COUNT(*) as cnt FROM spatial_edges WHERE src_id = ? OR dst_id = ?",
                    (nid, nid)
                )
                row = cursor.fetchone()
                degree = row["cnt"] if row else 0
                cursor = conn.execute(
                    "SELECT label FROM spatial_nodes WHERE node_id = ?", (nid,)
                )
                row = cursor.fetchone()
                if row:
                    scored.append((degree, row["label"]))
            conn.close()

        scored.sort(key=lambda x: -x[0])
        return [label for _, label in scored[:top_k]]

    # ========================================================================
    # 空间检索
    # ========================================================================

    def spatial_retrieve(
        self,
        query: str,
        current_context: Optional[str] = None,
        embedding: Optional[List[float]] = None,
        top_k: int = 5,
    ) -> List[dict]:
        """
        空间感知检索：在当前场景附近找关联记忆。

        如果指定了 current_context，首先定位到该场景，
        然后在其邻居中搜索匹配 query 的节点。

        Args:
            query: 查询文本
            current_context: 当前场景（可选）
            embedding: 查询向量（可选）
            top_k: 返回数量

        Returns:
            检索结果列表
        """
        results = []

        if current_context:
            node = self.get_scene(current_context)
            if node:
                # 获取邻居（包括自身）
                neighbors = self.get_scene_neighbors(current_context, depth=2)
                nearby_ids = {n.node_id for n in neighbors}
                nearby_ids.add(node.node_id)

                # 在这些节点中搜索
                with self._lock:
                    conn = sqlite3.connect(self.db_path)
                    conn.row_factory = sqlite3.Row

                    # 关键词匹配
                    keywords = _extract_keywords_from_query(query)
                    for nid in nearby_ids:
                        cursor = conn.execute(
                            "SELECT * FROM spatial_nodes WHERE node_id = ?", (nid,)
                        )
                        row = cursor.fetchone()
                        if row:
                            sn = self._row_to_node(dict(row))
                            # 计算关键词匹配分
                            score = self._keyword_match_score(query, sn.label)
                            for alias in sn.aliases:
                                score = max(score, self._keyword_match_score(query, alias))

                            # 元数据中也搜
                            if isinstance(sn.metadata, dict):
                                for val in sn.metadata.values():
                                    if isinstance(val, str):
                                        score = max(score, self._keyword_match_score(query, val) * 0.5)

                            if score > 0:
                                # 获取邻居标签
                                neighbor_labels = []
                                for nn in neighbors:
                                    if nn.node_id == nid:
                                        continue
                                    neighbor_labels.append(nn.label)

                                results.append({
                                    "id": sn.node_id,
                                    "label": sn.label,
                                    "node_type": sn.node_type,
                                    "content": f"[空间拓扑] {sn.label} (类型: {sn.node_type})",
                                    "score": score,
                                    "source": "spatial_topology",
                                    "metadata": {
                                        "neighbors": neighbor_labels[:5],
                                        "alias": sn.aliases,
                                        "parent_id": sn.parent_id,
                                        "node_type": sn.node_type,
                                    }
                                })

                    conn.close()

                # 如果有 embedding，再算语义相似度
                if embedding and len(embedding) > 0:
                    for r in results:
                        nid = r["id"]
                        node = self._get_node_by_id(nid)
                        if node and node.embedding and len(node.embedding) > 0:
                            sem_score = _cosine_similarity(embedding, node.embedding)
                            # 混合：关键词 60% + 语义 40%
                            r["score"] = r["score"] * 0.6 + sem_score * 0.4

        if not results:
            # 没有指定场景，全局搜索
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM spatial_nodes ORDER BY updated_at DESC LIMIT ?",
                    (top_k * 2,)
                )
                for row in cursor.fetchall():
                    sn = self._row_to_node(dict(row))
                    score = self._keyword_match_score(query, sn.label)
                    for alias in sn.aliases:
                        score = max(score, self._keyword_match_score(query, alias) * 0.8)
                    if score > 0:
                        results.append({
                            "id": sn.node_id,
                            "label": sn.label,
                            "node_type": sn.node_type,
                            "content": f"[空间拓扑] {sn.label}",
                            "score": score * 0.8,
                            "source": "spatial_topology",
                            "metadata": {
                                "aliases": sn.aliases,
                                "node_type": sn.node_type,
                            }
                        })
                conn.close()

        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    def spatial_context_augment(
        self,
        query_context: str,
        entities: List[str],
    ) -> List[dict]:
        """
        给检索结果附加空间上下文。

        例如 "你在A项目的B文件夹下问这个问题"。
        返回当前场景的拓扑结构描述。
        """
        scene = self.get_scene(query_context)
        if not scene:
            return []

        # 获取场景的全貌
        scope = self.get_scene_scope(scene.label)

        # 标注实体是否在场景附近
        entity_locations = []
        for ent in entities:
            ent_node = self.get_scene(ent)
            if ent_node:
                # 计算图距离
                dist = self._graph_distance(scene.node_id, ent_node.node_id)
                entity_locations.append({
                    "entity": ent,
                    "distance": dist,
                    "relative_pos": "当前场景" if dist == 0 else (
                        "近邻" if dist <= 1 else ("同区域" if dist <= 2 else "远距离")
                    ),
                })

        context_info = {
            "current_scene": scene.label,
            "scene_type": scene.node_type,
            "parent": scope.get("parent"),
            "children": scope.get("children", []),
            "neighbors": scope.get("neighbors", []),
            "entity_locations": entity_locations,
            "aliases": scene.aliases,
        }

        return context_info

    # ========================================================================
    # 拓扑推断
    # ========================================================================

    def infer_scene_from_entities(self, entities: List[str]) -> Optional[str]:
        """
        根据当前提及的实体推断用户在哪个场景。

        策略：
        1. 每个实体找到它所属的场景（parent 链）
        2. 取最具体的公共父场景
        """
        if not entities:
            return None

        scenes = []
        for ent in entities:
            node = self.get_scene(ent)
            if node:
                # 如果是实体类型，沿着 parent 链找到 scene 类型
                if node.node_type == "entity":
                    scene = self._find_parent_scene(node.node_id)
                    if scene:
                        scenes.append(scene.label)
                else:
                    scenes.append(node.label)
            else:
                # 尝试模糊匹配
                node = self._fuzzy_find_scene(ent)
                if node:
                    scenes.append(node.label)

        if not scenes:
            return None

        # 取最频繁出现的场景
        from collections import Counter
        scene_counts = Counter(scenes)
        most_common = scene_counts.most_common(1)
        return most_common[0][0] if most_common else scenes[0]

    def get_scene_scope(self, label: str) -> dict:
        """获取场景的完整范围：父场景、子场景、近邻、活跃度"""
        node = self.get_scene(label)
        if not node:
            return {}

        parent_label = None
        if node.parent_id:
            parent_node = self._get_node_by_id(node.parent_id)
            if parent_node:
                parent_label = parent_node.label

        # 子场景
        children = []
        for cid in node.children_ids:
            child = self._get_node_by_id(cid)
            if child:
                children.append(child.label)

        # 近邻
        neighbors = self.get_scene_neighbors(label, depth=1)
        neighbor_labels = [n.label for n in neighbors if n.label != label]

        # 进出度（活跃度指标）
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            in_degree = conn.execute(
                "SELECT COUNT(*) FROM spatial_edges WHERE dst_id = ?", (node.node_id,)
            ).fetchone()[0]
            out_degree = conn.execute(
                "SELECT COUNT(*) FROM spatial_edges WHERE src_id = ?", (node.node_id,)
            ).fetchone()[0]
            nav_count = conn.execute(
                "SELECT COUNT(*) FROM navigation_records WHERE from_node = ? OR to_node = ?",
                (node.node_id, node.node_id)
            ).fetchone()[0]
            conn.close()

        return {
            "scene": label,
            "node_type": node.node_type,
            "parent": parent_label,
            "children": children,
            "neighbors": neighbor_labels,
            "in_degree": in_degree,
            "out_degree": out_degree,
            "navigation_count": nav_count,
            "aliases": node.aliases,
        }

    # ========================================================================
    # 持久化
    # ========================================================================

    def save(self):
        """持久化（实际上 SQLite 是实时写的，此方法只做验证）"""
        stats = self.get_stats()
        logger.info(f"SpatialTopologyGraph saved: {stats['nodes']} nodes, {stats['edges']} edges")
        return stats

    def load(self):
        """加载（SQLite 实时读，重建别名缓存）"""
        self._load_alias_cache()
        stats = self.get_stats()
        logger.info(f"SpatialTopologyGraph loaded: {stats['nodes']} nodes, {stats['edges']} edges")
        return stats

    def get_stats(self) -> dict:
        """获取统计信息"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            nodes = conn.execute("SELECT COUNT(*) FROM spatial_nodes").fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM spatial_edges").fetchone()[0]
            navigations = conn.execute("SELECT COUNT(*) FROM navigation_records").fetchone()[0]
            conn.close()
        return {
            "nodes": nodes,
            "edges": edges,
            "navigations": navigations,
        }

    # ========================================================================
    # 内部方法
    # ========================================================================

    def _save_node(self, node: SpatialNode):
        """保存节点到数据库"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT OR REPLACE INTO spatial_nodes
                (node_id, label, node_type, embedding, center_position,
                 parent_id, children_ids, aliases, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node.node_id,
                node.label,
                node.node_type,
                json.dumps(node.embedding),
                json.dumps(node.center_position),
                node.parent_id,
                json.dumps(node.children_ids),
                json.dumps(node.aliases),
                json.dumps(node.metadata),
                time.time(),
            ))
            conn.commit()
            conn.close()

    def _save_edge(self, edge: SpatialEdge):
        """保存边到数据库"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT OR REPLACE INTO spatial_edges
                (edge_id, src_id, dst_id, relation, weight, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                edge.edge_id,
                edge.src_id,
                edge.dst_id,
                edge.relation,
                edge.weight,
                edge.timestamp or time.time(),
            ))
            conn.commit()
            conn.close()

    def _save_navigation(self, record: NavigationRecord):
        """保存导航记录"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT OR REPLACE INTO navigation_records
                (record_id, from_node, to_node, path, context, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                record.record_id,
                record.from_node,
                record.to_node,
                json.dumps(record.path),
                record.context,
                record.timestamp or time.time(),
            ))
            conn.commit()
            conn.close()

    def _row_to_node(self, row: dict) -> SpatialNode:
        """数据库行转 SpatialNode"""
        for field in ['embedding', 'center_position', 'children_ids', 'aliases']:
            if isinstance(row.get(field), str):
                try:
                    row[field] = json.loads(row[field])
                except (json.JSONDecodeError, TypeError):
                    row[field] = []
        if isinstance(row.get('metadata'), str):
            try:
                row['metadata'] = json.loads(row['metadata'])
            except (json.JSONDecodeError, TypeError):
                row['metadata'] = {}
        return SpatialNode.from_dict(row)

    def _get_node_by_id(self, node_id: str) -> Optional[SpatialNode]:
        """通过 node_id 获取节点"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM spatial_nodes WHERE node_id = ?", (node_id,)
            )
            row = cursor.fetchone()
            conn.close()
            return self._row_to_node(dict(row)) if row else None

    def _ensure_edge(self, src_id: str, dst_id: str, relation: str, weight: float):
        """确保边存在（不重复创建）"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "SELECT edge_id FROM spatial_edges WHERE src_id=? AND dst_id=? AND relation=?",
                (src_id, dst_id, relation)
            )
            if cursor.fetchone():
                conn.close()
                return
            conn.close()

        edge_id = f"edge_{src_id}_{dst_id}_{int(time.time())}"
        edge = SpatialEdge(
            edge_id=edge_id,
            src_id=src_id,
            dst_id=dst_id,
            relation=relation,
            weight=weight,
            timestamp=time.time(),
        )
        self._save_edge(edge)

    def _add_child_to_parent(self, child_id: str, parent_id: str):
        """将子节点 ID 添加到父节点的 children_ids"""
        parent = self._get_node_by_id(parent_id)
        if parent and child_id not in parent.children_ids:
            parent.children_ids.append(child_id)
            self._save_node(parent)

    def _infer_position(self, node_id: str):
        """推断节点拓扑坐标"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            # 获取邻居
            neighbors = []
            cursor = conn.execute(
                "SELECT dst_id FROM spatial_edges WHERE src_id = ?", (node_id,)
            )
            for row in cursor.fetchall():
                neighbors.append(row["dst_id"])
            cursor = conn.execute(
                "SELECT src_id FROM spatial_edges WHERE dst_id = ?", (node_id,)
            )
            for row in cursor.fetchall():
                neighbors.append(row["src_id"])

            if not neighbors:
                conn.close()
                return

            # 累计邻居位置
            pos = [0.0, 0.0]
            count = 0
            for nid in set(neighbors):
                cursor = conn.execute(
                    "SELECT center_position FROM spatial_nodes WHERE node_id = ?", (nid,)
                )
                row = cursor.fetchone()
                if row:
                    try:
                        np = json.loads(row["center_position"])
                        if len(np) >= 2:
                            pos[0] += np[0]
                            pos[1] += np[1]
                            count += 1
                    except (json.JSONDecodeError, TypeError):
                        pass

            conn.close()

            if count > 0:
                pos[0] /= count
                pos[1] /= count
                # 更新位置
                with self._lock:
                    conn2 = sqlite3.connect(self.db_path)
                    conn2.execute(
                        "UPDATE spatial_nodes SET center_position = ? WHERE node_id = ?",
                        (json.dumps(pos), node_id)
                    )
                    conn2.commit()
                    conn2.close()

    def _bfs_path(
        self,
        src_id: str,
        dst_id: str,
        max_depth: int = 5,
    ) -> Optional[List[str]]:
        """BFS 找最短路径"""
        if src_id == dst_id:
            return [src_id]

        visited = {src_id}
        queue = [(src_id, [src_id])]

        with self._lock:
            conn = sqlite3.connect(self.db_path)

            while queue:
                current, path = queue.pop(0)
                if len(path) > max_depth:
                    continue

                # 出边
                cursor = conn.execute(
                    "SELECT dst_id FROM spatial_edges WHERE src_id = ?", (current,)
                )
                for row in cursor.fetchall():
                    nid = row[0]  # tuple: (dst_id,)
                    if nid == dst_id:
                        conn.close()
                        return path + [nid]
                    if nid not in visited:
                        visited.add(nid)
                        queue.append((nid, path + [nid]))

                # 入边
                cursor = conn.execute(
                    "SELECT src_id FROM spatial_edges WHERE dst_id = ?", (current,)
                )
                for row in cursor.fetchall():
                    nid = row[0]  # tuple: (src_id,)
                    if nid == dst_id:
                        conn.close()
                        return path + [nid]
                    if nid not in visited:
                        visited.add(nid)
                        queue.append((nid, path + [nid]))

            conn.close()

        return None

    def _graph_distance(self, src_id: str, dst_id: str) -> int:
        """计算两个节点之间的图距离（BFS 步数）"""
        if src_id == dst_id:
            return 0

        visited = {src_id}
        queue = [(src_id, 0)]

        with self._lock:
            conn = sqlite3.connect(self.db_path)

            while queue:
                current, dist = queue.pop(0)
                if dist > 5:
                    continue

                cursor = conn.execute(
                    "SELECT dst_id FROM spatial_edges WHERE src_id = ?", (current,)
                )
                for row in cursor.fetchall():
                    nid = row[0]  # tuple: (dst_id,)
                    if nid == dst_id:
                        conn.close()
                        return dist + 1
                    if nid not in visited:
                        visited.add(nid)
                        queue.append((nid, dist + 1))

                cursor = conn.execute(
                    "SELECT src_id FROM spatial_edges WHERE dst_id = ?", (current,)
                )
                for row in cursor.fetchall():
                    nid = row[0]  # tuple: (src_id,)
                    if nid == dst_id:
                        conn.close()
                        return dist + 1
                    if nid not in visited:
                        visited.add(nid)
                        queue.append((nid, dist + 1))

            conn.close()

        return 999  # 不可达

    def _strengthen_edge(self, src_id: str, dst_id: str, delta: float = 0.2):
        """增加边权重"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                UPDATE spatial_edges SET weight = MIN(weight + ?, 2.0), timestamp = ?
                WHERE (src_id = ? AND dst_id = ?) OR (src_id = ? AND dst_id = ?)
            """, (delta, time.time(), src_id, dst_id, dst_id, src_id))
            conn.commit()
            conn.close()

    def _find_parent_scene(self, node_id: str) -> Optional[SpatialNode]:
        """沿着 parent 链找到 scene 类型的节点"""
        node = self._get_node_by_id(node_id)
        if not node:
            return None
        if node.node_type == "scene":
            return node
        if node.parent_id:
            return self._find_parent_scene(node.parent_id)
        return None

    def _fuzzy_find_scene(self, text: str) -> Optional[SpatialNode]:
        """模糊查找场景"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM spatial_nodes WHERE label LIKE ? OR label LIKE ?",
                (f"%{text}%", f"{text}%")
            )
            rows = cursor.fetchall()
            conn.close()
            if rows:
                return self._row_to_node(dict(rows[0]))
            return None

    def _keyword_match_score(self, query: str, text: str) -> float:
        """计算关键词匹配分数"""
        if not query or not text:
            return 0.0

        query_lower = query.lower()
        text_lower = text.lower()

        # 精确匹配
        if query_lower == text_lower:
            return 1.0

        # 包含
        if query_lower in text_lower or text_lower in query_lower:
            return 0.8

        # 词重叠
        q_words = set(_extract_keywords_from_query(query_lower))
        t_words = set(_extract_keywords_from_query(text_lower))
        if q_words and t_words:
            overlap = len(q_words & t_words)
            max_len = max(len(q_words), len(t_words))
            return overlap / max_len * 0.6 if max_len > 0 else 0.0

        return 0.0


# ============================================================================
# 辅助函数
# ============================================================================

def _hash_str(text: str) -> str:
    """简易哈希（不依赖 hashlib 外部函数名）"""
    h = 0
    for c in text:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return format(h, 'x')


def _extract_keywords_from_query(text: str) -> List[str]:
    """从查询文本提取关键词"""
    # 中文词组（2字以上）
    chinese = re.findall(r'[\u4e00-\u9fff]{2,}', text)
    # 英文单词（3字母以上）
    english = re.findall(r'[a-zA-Z]{3,}', text)
    return chinese + english


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ============================================================================
# 单例缓存
# ============================================================================

_spatial_instances: Dict[str, SpatialTopologyGraph] = {}


def get_spatial_graph(db_path: Optional[str] = None) -> SpatialTopologyGraph:
    """获取 SpatialTopologyGraph 实例（单例）"""
    key = db_path or DEFAULT_DB_PATH
    if key not in _spatial_instances:
        _spatial_instances[key] = SpatialTopologyGraph(db_path=db_path)
    return _spatial_instances[key]


__all__ = [
    'SpatialTopologyGraph',
    'SpatialNode',
    'SpatialEdge',
    'NavigationRecord',
    'get_spatial_graph',
    'RELATION_INSIDE',
    'RELATION_CONNECTED',
    'RELATION_NEAR',
    'RELATION_FAR',
]
