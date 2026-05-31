#!/usr/bin/env python3
"""
持久化向量存储模块 (Persistent Vector Store)

基于 SQLite 的持久化向量数据库，支持：
- CRUD 操作（add / get / update / delete）
- 向量相似度搜索（sqlite-vec / 内置 HNSW / numpy 暴力搜索 三级加速）
- 增量索引（无需全量重建）
- 元数据过滤
- 多集合管理
- 线程安全

设计原则：
- 优先使用 sqlite-vec 扩展加速搜索
- 次选内置纯 Python HNSW 索引（无需编译，沙箱环境可用）
- 回退到 numpy 暴力搜索（无依赖降级）
- 向量数据持久化到 SQLite BLOB，进程退出不丢失
"""

import heapq
import json
import logging
import random
import sqlite3
import struct
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _try_load_prebuilt_hnswlib():
    """
    尝试加载预编译的 hnswlib C 扩展

    优先级:
    1. 已安装的 hnswlib (pip install)
    2. 预编译嵌入产物 (prebuilt/extensions/hnswlib/)

    Returns:
        模块对象或 None
    """
    # 先尝试已安装的
    try:
        import hnswlib
        return hnswlib
    except ImportError:
        pass

    # 尝试预编译嵌入产物
    try:
        from sandbox_manager import PrebuiltManager
        pm = PrebuiltManager()
        if pm.has_prebuilt_extension('hnswlib'):
            mod = pm.load_prebuilt_extension('hnswlib')
            if mod is not None:
                logger.info("从预编译嵌入产物加载 hnswlib 成功")
                return mod
    except Exception as e:
        logger.debug(f"预编译 hnswlib 加载失败: {e}")

    return None


def _pack_vector(vec: np.ndarray) -> bytes:
    """将 float32 向量打包为二进制"""
    return vec.astype(np.float32).tobytes()


def _unpack_vector(data: bytes, dim: int) -> np.ndarray:
    """将二进制解包为 float32 向量"""
    return np.frombuffer(data, dtype=np.float32)[:dim].copy()


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度"""
    a_norm = np.linalg.norm(a) + 1e-10
    b_norm = np.linalg.norm(b) + 1e-10
    return float(np.dot(a, b) / (a_norm * b_norm))


# ==================== 内置纯 Python HNSW 索引 ====================
# 无需 hnswlib C 扩展，沙箱环境直接可用


class HNSWIndex:
    """
    纯 Python 实现的 HNSW (Hierarchical Navigable Small World) 索引

    无需编译 C 扩展，沙箱环境直接可用。
    基于 Malkov & Yashunin (2018) 论文算法实现。

    特性：
    - 增量构建：支持逐条插入，无需全量重建
    - 近似最近邻搜索：O(log n) 复杂度
    - 纯 Python + numpy：零 C 依赖

    参数说明：
    - M: 每个节点的最大邻居数（层0为 2*M），影响索引大小和召回率
    - ef_construction: 构建时的搜索宽度，越大越精确但越慢
    - ef_search: 搜索时的搜索宽度，越大越精确但越慢
    """

    def __init__(
        self,
        dim: int,
        M: int = 16,
        ef_construction: int = 200,
        ef_search: int = 50,
        metric: str = "cosine",
    ):
        self.dim = dim
        self.M = M
        self.M_max0 = 2 * M  # 层0的最大邻居数
        self.ef_construction = ef_construction
        self.ef_search = ef_search
        self.metric = metric

        # 数据存储：id -> (vector, level)
        self._vectors: Dict[str, np.ndarray] = {}
        # 图结构：id -> {level: [neighbor_ids]}
        self._graph: Dict[str, Dict[int, List[str]]] = {}
        # 每层入口节点
        self._entry_point: Optional[str] = None
        self._max_level: int = -1
        # 预计算归一化向量（cosine 时使用）
        self._norms: Dict[str, np.ndarray] = {}

        # 随机数生成器（节点层级分配）
        self._ml = 1.0 / np.log(M) if M > 1 else 1.0
        self._rng = random.Random(42)

    def _distance(self, a: np.ndarray, b: np.ndarray) -> float:
        """计算距离（cosine 用 1-sim，l2 用欧氏距离）"""
        if self.metric == "cosine":
            dot = float(np.dot(a, b))
            na = float(np.dot(a, a))
            nb = float(np.dot(b, b))
            if na < 1e-20 or nb < 1e-20:
                return 1.0
            return 1.0 - dot / (np.sqrt(na) * np.sqrt(nb))
        else:
            diff = a - b
            return float(np.dot(diff, diff))

    def _random_level(self) -> int:
        """分配随机层级"""
        level = 0
        while self._rng.random() < np.exp(-1.0 / self._ml) and level < 16:
            level += 1
        return level

    def insert(self, id: str, vector: np.ndarray):
        """
        插入一个向量

        Args:
            id: 向量标识
            vector: 向量数据
        """
        vec = vector.astype(np.float32)
        level = self._random_level()

        self._vectors[id] = vec
        self._graph[id] = {l: [] for l in range(level + 1)}

        if self._entry_point is None:
            self._entry_point = id
            self._max_level = level
            return

        # 从最高层向下搜索最近邻入口
        curr = self._entry_point
        curr_dist = self._distance(vec, self._vectors[curr])

        for lc in range(self._max_level, level, -1):
            changed = True
            while changed:
                changed = False
                neighbors = self._graph[curr].get(lc, [])
                for n_id in neighbors:
                    n_dist = self._distance(vec, self._vectors[n_id])
                    if n_dist < curr_dist:
                        curr = n_id
                        curr_dist = n_dist
                        changed = True

        # 在 level 及以下各层插入
        for lc in range(min(level, self._max_level), -1, -1):
            neighbors = self._search_layer(vec, curr, self.ef_construction, lc)
            M_max = self.M_max0 if lc == 0 else self.M
            selected = self._select_neighbors(vec, neighbors, M_max)

            # 添加双向连接
            self._graph[id][lc] = [n_id for n_id, _ in selected]

            for n_id, _ in selected:
                n_neighbors = self._graph[n_id].setdefault(lc, [])
                n_neighbors.append(id)
                # 裁剪：如果邻居数超过 M_max，保留最近的 M_max 个
                if len(n_neighbors) > M_max:
                    n_vec = self._vectors[n_id]
                    scored = [(nid, self._distance(n_vec, self._vectors[nid])) for nid in n_neighbors]
                    scored.sort(key=lambda x: x[1])
                    self._graph[n_id][lc] = [nid for nid, _ in scored[:M_max]]

            # 更新搜索起点
            if selected:
                curr = selected[0][0]

        # 如果新节点层级更高，更新入口
        if level > self._max_level:
            self._entry_point = id
            self._max_level = level

    def _search_layer(
        self, query: np.ndarray, entry: str, ef: int, level: int
    ) -> List[Tuple[str, float]]:
        """在指定层搜索最近邻（贪心搜索 + 优先队列）"""
        visited: Set[str] = {entry}
        entry_dist = self._distance(query, self._vectors[entry])

        # 候选集（最小堆：距离最近的优先弹出）
        candidates = [(entry_dist, entry)]
        # 结果集（最大堆：距离最远的优先弹出用于裁剪）
        results = [(-entry_dist, entry)]

        while candidates:
            c_dist, c_id = heapq.heappop(candidates)
            f_dist = -results[0][0]  # 结果集中最远距离

            if c_dist > f_dist:
                break

            for n_id in self._graph[c_id].get(level, []):
                if n_id in visited:
                    continue
                visited.add(n_id)

                n_dist = self._distance(query, self._vectors[n_id])
                f_dist = -results[0][0]

                if n_dist < f_dist or len(results) < ef:
                    heapq.heappush(candidates, (n_dist, n_id))
                    heapq.heappush(results, (-n_dist, n_id))
                    if len(results) > ef:
                        heapq.heappop(results)

        return [(id_, -dist) for dist, id_ in results]

    def _select_neighbors(
        self, query: np.ndarray, candidates: List[Tuple[str, float]], M: int
    ) -> List[Tuple[str, float]]:
        """选择最近的 M 个邻居（简单策略）"""
        candidates.sort(key=lambda x: x[1])
        return candidates[:M]

    def search(self, query: np.ndarray, top_k: int = 10, ef: Optional[int] = None) -> List[Tuple[str, float]]:
        """
        搜索最近邻

        Args:
            query: 查询向量
            top_k: 返回数量
            ef: 搜索宽度（默认使用 ef_search）

        Returns:
            List[Tuple[id, distance]]: 按距离升序排列的结果
        """
        if not self._vectors or self._entry_point is None:
            return []

        ef = ef or self.ef_search
        ef = max(ef, top_k)

        vec = query.astype(np.float32)
        curr = self._entry_point
        curr_dist = self._distance(vec, self._vectors[curr])

        # 从最高层向下，每层贪心搜索最近入口
        for lc in range(self._max_level, 0, -1):
            changed = True
            while changed:
                changed = False
                for n_id in self._graph[curr].get(lc, []):
                    n_dist = self._distance(vec, self._vectors[n_id])
                    if n_dist < curr_dist:
                        curr = n_id
                        curr_dist = n_dist
                        changed = True

        # 在层0做完整搜索
        results = self._search_layer(vec, curr, ef, 0)
        results.sort(key=lambda x: x[1])

        # 距离转相似度（cosine 时 1-dist = similarity）
        return results[:top_k]

    def remove(self, id: str):
        """
        删除一个向量（软删除：从图中移除连接，保留数据位）

        Args:
            id: 要删除的向量标识
        """
        if id not in self._graph:
            return

        # 从所有邻居的连接中移除该节点
        for level, neighbors in self._graph[id].items():
            for n_id in neighbors:
                if n_id in self._graph:
                    n_level = self._graph[n_id].get(level, [])
                    if id in n_level:
                        n_level.remove(id)

        del self._graph[id]
        self._vectors.pop(id, None)
        self._norms.pop(id, None)

        # 如果删除的是入口节点，重新选择
        if id == self._entry_point:
            if self._graph:
                self._entry_point = next(iter(self._graph))
                self._max_level = max(
                    (max(levels.keys()) for levels in self._graph.values() if levels),
                    default=0,
                )
            else:
                self._entry_point = None
                self._max_level = -1

    def __len__(self) -> int:
        return len(self._vectors)

    @property
    def size(self) -> int:
        return len(self._vectors)


class VectorStore:
    """
    持久化向量存储

    使用示例:
    >>> store = VectorStore(db_path="~/.openclaw/memory/vectors.db")
    >>> store.add("mem_1", "什么是机器学习", embedding, metadata={"topic": "AI"})
    >>> results = store.search(query_embedding, top_k=5)
    >>> store.delete("mem_1")
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        dimension: int = 4096,
        collection: str = "default",
        enable_hnsw: bool = True,
        hnsw_m: int = 16,
        hnsw_ef_construction: int = 200,
        hnsw_ef_search: int = 50,
    ):
        """
        初始化向量存储

        Args:
            db_path: SQLite 数据库路径，':memory:' 为内存模式
            dimension: 向量维度
            collection: 集合名称（支持多集合）
            enable_hnsw: 是否启用内置 HNSW 索引（沙箱环境推荐启用）
            hnsw_m: HNSW M 参数（每个节点邻居数）
            hnsw_ef_construction: HNSW 构建时搜索宽度
            hnsw_ef_search: HNSW 搜索时搜索宽度
        """
        self.db_path = str(Path(db_path).expanduser().absolute()) if db_path != ":memory:" else db_path
        self.dimension = dimension
        self.collection = collection
        self._lock = threading.Lock()

        # 初始化数据库
        self._conn = self._connect()
        self._init_tables()
        self._vec_available = self._check_vec_extension()

        # 内存缓存（加速频繁搜索）
        self._cache: Dict[str, Tuple[np.ndarray, Dict]] = {}
        self._cache_dirty = True

        # 搜索引擎选择：
        # 1. sqlite-vec 扩展（C 扩展，最快）
        # 2. hnswlib C 扩展（预编译嵌入或 pip 安装，近似搜索）
        # 3. 内置 HNSWIndex（纯 Python，沙箱环境可用，近似搜索）
        # 4. numpy 暴力搜索（精确搜索 O(n)，兜底）
        self._hnswlib_mod = _try_load_prebuilt_hnswlib()
        self._hnswlib_index = None  # hnswlib C 扩展索引实例
        self._hnswlib_dim = dimension
        self._hnswlib_elements = 0  # 当前索引中的元素数

        # 内置 HNSW 索引（纯 Python，无需编译，作为 hnswlib C 扩展的回退）
        self._enable_hnsw = enable_hnsw
        self._hnsw: Optional[HNSWIndex] = None
        self._hnsw_dirty = True  # 是否需要重建
        if enable_hnsw:
            self._hnsw = HNSWIndex(
                dim=dimension,
                M=hnsw_m,
                ef_construction=hnsw_ef_construction,
                ef_search=hnsw_ef_search,
            )

        # 确定搜索策略描述
        if self._vec_available:
            search_desc = "sqlite-vec"
        elif self._hnswlib_mod is not None:
            search_desc = "hnswlib(C扩展)"
        elif enable_hnsw:
            search_desc = "HNSW(内置纯Python)"
        else:
            search_desc = "numpy暴力"

        logger.info(
            f"VectorStore 初始化: db={self.db_path}, dim={dimension}, "
            f"collection={collection}, 搜索策略={search_desc}"
        )

    def _connect(self) -> sqlite3.Connection:
        """连接数据库"""
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB 缓存
        return conn

    def _init_tables(self):
        """初始化数据库表"""
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS vectors (
                    id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    vector BLOB NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    importance REAL DEFAULT 0.5,
                    created_at REAL,
                    updated_at REAL,
                    access_count INTEGER DEFAULT 0,
                    last_access REAL
                );

                CREATE INDEX IF NOT EXISTS idx_vectors_collection
                    ON vectors(collection);

                CREATE INDEX IF NOT EXISTS idx_vectors_importance
                    ON vectors(collection, importance DESC);

                CREATE INDEX IF NOT EXISTS idx_vectors_updated
                    ON vectors(collection, updated_at DESC);
            """)
            self._conn.commit()

    def _check_vec_extension(self) -> bool:
        """检查 sqlite-vec 扩展是否可用"""
        try:
            from .sqlite_vec import is_vec_available, connect
            if is_vec_available(self._conn):
                return True
        except Exception:
            pass

        # 尝试直接加载
        try:
            self._conn.enable_load_extension(True)
            from .sqlite_vec import find_vec0_extension
            vec0_path = find_vec0_extension()
            if vec0_path:
                self._conn.load_extension(vec0_path)
                self._conn.enable_load_extension(False)
                return True
        except Exception:
            pass

        try:
            self._conn.enable_load_extension(False)
        except Exception:
            pass

        return False

    # ==================== CRUD ====================

    def add(
        self,
        id: str,
        content: str,
        vector: np.ndarray,
        metadata: Optional[Dict] = None,
        importance: float = 0.5,
    ) -> bool:
        """
        添加向量记录

        Args:
            id: 记录 ID
            content: 文本内容
            vector: 嵌入向量
            metadata: 元数据
            importance: 重要性评分 (0-1)

        Returns:
            bool: 是否成功
        """
        if vector.shape[0] != self.dimension:
            logger.warning(f"向量维度不匹配: 期望 {self.dimension}, 实际 {vector.shape[0]}")
            # 自适应维度
            if vector.shape[0] > self.dimension:
                vector = vector[:self.dimension]
            else:
                padded = np.zeros(self.dimension, dtype=np.float32)
                padded[:vector.shape[0]] = vector
                vector = padded

        now = time.time()
        metadata = metadata or {}
        vec_blob = _pack_vector(vector)

        with self._lock:
            try:
                self._conn.execute(
                    """INSERT OR REPLACE INTO vectors
                       (id, collection, content, vector, metadata, importance,
                        created_at, updated_at, access_count, last_access)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                    (id, self.collection, content, vec_blob,
                     json.dumps(metadata, ensure_ascii=False), importance,
                     now, now, now)
                )
                self._conn.commit()
                self._cache_dirty = True
                # 更新内存缓存
                self._cache[id] = (vector, {"content": content, "metadata": metadata, "importance": importance})
                # 增量更新 HNSW 索引
                if self._enable_hnsw and self._hnsw is not None:
                    try:
                        self._hnsw.insert(id, vector)
                    except Exception as e:
                        logger.debug(f"HNSW 增量插入失败(将标记重建): {e}")
                        self._hnsw_dirty = True
                # hnswlib C 扩展索引 - 标记需要重建（hnswlib 不支持增量删除后重建，统一标记）
                if self._hnswlib_mod is not None and self._hnswlib_index is not None:
                    self._hnsw_dirty = True
                return True
            except Exception as e:
                logger.error(f"添加向量记录失败: {e}")
                return False

    def batch_add(
        self,
        ids: List[str],
        contents: List[str],
        vectors: np.ndarray,
        metadatas: Optional[List[Dict]] = None,
        importances: Optional[List[float]] = None,
    ) -> int:
        """
        批量添加向量记录

        Args:
            ids: ID 列表
            contents: 内容列表
            vectors: 向量矩阵 (n, dim)
            metadatas: 元数据列表
            importances: 重要性列表

        Returns:
            int: 成功添加的数量
        """
        metadatas = metadatas or [{}] * len(ids)
        importances = importances or [0.5] * len(ids)
        now = time.time()
        count = 0

        with self._lock:
            for i, (id_, content, vec, meta, imp) in enumerate(
                zip(ids, contents, vectors, metadatas, importances)
            ):
                if vec.shape[0] != self.dimension:
                    if vec.shape[0] > self.dimension:
                        vec = vec[:self.dimension]
                    else:
                        padded = np.zeros(self.dimension, dtype=np.float32)
                        padded[:vec.shape[0]] = vec
                        vec = padded

                try:
                    self._conn.execute(
                        """INSERT OR REPLACE INTO vectors
                           (id, collection, content, vector, metadata, importance,
                            created_at, updated_at, access_count, last_access)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                        (id_, self.collection, content, _pack_vector(vec),
                         json.dumps(meta, ensure_ascii=False), imp,
                         now, now, now)
                    )
                    count += 1
                except Exception as e:
                    logger.error(f"批量添加第 {i} 条失败: {e}")

            self._conn.commit()
            self._cache_dirty = True

        # 批量更新 HNSW 索引
        if self._enable_hnsw and self._hnsw is not None and count > 0:
            try:
                for i, (id_, content, vec, meta, imp) in enumerate(
                    zip(ids, contents, vectors, metadatas, importances)
                ):
                    if vec.shape[0] != self.dimension:
                        if vec.shape[0] > self.dimension:
                            vec = vec[:self.dimension]
                        else:
                            padded = np.zeros(self.dimension, dtype=np.float32)
                            padded[:vec.shape[0]] = vec
                            vec = padded
                    self._hnsw.insert(id_, vec)
            except Exception as e:
                logger.debug(f"HNSW 批量插入失败(将标记重建): {e}")
                self._hnsw_dirty = True

        return count

    def get(self, id: str) -> Optional[Dict]:
        """
        获取单条记录

        Args:
            id: 记录 ID

        Returns:
            Optional[Dict]: 记录数据，包含 id, content, vector, metadata 等
        """
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, content, vector, metadata, importance, "
                "created_at, updated_at, access_count, last_access "
                "FROM vectors WHERE id = ? AND collection = ?",
                (id, self.collection)
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_dict(row)

    def update(
        self,
        id: str,
        content: Optional[str] = None,
        vector: Optional[np.ndarray] = None,
        metadata: Optional[Dict] = None,
        importance: Optional[float] = None,
    ) -> bool:
        """
        更新记录

        Args:
            id: 记录 ID
            content: 新内容（None 不更新）
            vector: 新向量（None 不更新）
            metadata: 新元数据（None 不更新，会合并已有元数据）
            importance: 新重要性（None 不更新）

        Returns:
            bool: 是否成功
        """
        existing = self.get(id)
        if existing is None:
            return False

        now = time.time()
        updates = ["updated_at = ?"]
        params = [now]

        if content is not None:
            updates.append("content = ?")
            params.append(content)

        if vector is not None:
            if vector.shape[0] != self.dimension:
                if vector.shape[0] > self.dimension:
                    vector = vector[:self.dimension]
                else:
                    padded = np.zeros(self.dimension, dtype=np.float32)
                    padded[:vector.shape[0]] = vector
                    vector = padded
            updates.append("vector = ?")
            params.append(_pack_vector(vector))
            # 向量变更需要重建 HNSW
            self._hnsw_dirty = True

        if metadata is not None:
            # 合并元数据
            merged = {**existing.get("metadata", {}), **metadata}
            updates.append("metadata = ?")
            params.append(json.dumps(merged, ensure_ascii=False))

        if importance is not None:
            updates.append("importance = ?")
            params.append(importance)

        params.append(id)
        params.append(self.collection)

        with self._lock:
            try:
                self._conn.execute(
                    f"UPDATE vectors SET {', '.join(updates)} WHERE id = ? AND collection = ?",
                    params
                )
                self._conn.commit()
                self._cache_dirty = True
                return True
            except Exception as e:
                logger.error(f"更新记录失败: {e}")
                return False

    def delete(self, id: str) -> bool:
        """
        删除记录

        Args:
            id: 记录 ID

        Returns:
            bool: 是否成功
        """
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "DELETE FROM vectors WHERE id = ? AND collection = ?",
                    (id, self.collection)
                )
                self._conn.commit()
                self._cache_dirty = True
                self._cache.pop(id, None)
                # 从 HNSW 索引中移除
                if self._enable_hnsw and self._hnsw is not None:
                    try:
                        self._hnsw.remove(id)
                    except Exception:
                        self._hnsw_dirty = True
                return cursor.rowcount > 0
            except Exception as e:
                logger.error(f"删除记录失败: {e}")
                return False

    def delete_by_metadata(self, key: str, value: Any) -> int:
        """
        按元数据条件删除记录

        Args:
            key: 元数据键
            value: 元数据值

        Returns:
            int: 删除数量
        """
        with self._lock:
            try:
                # 使用 JSON 函数匹配（SQLite 3.38+）
                cursor = self._conn.execute(
                    "DELETE FROM vectors WHERE collection = ? AND json_extract(metadata, ?) = ?",
                    (self.collection, f"$.{key}", json.dumps(value) if isinstance(value, (dict, list)) else str(value))
                )
                self._conn.commit()
                self._cache_dirty = True
                return cursor.rowcount
            except Exception:
                # 回退：扫描删除
                all_records = self._scan_all()
                to_delete = [
                    rid for rid, (_, meta) in all_records.items()
                    if meta.get(key) == value
                ]
                for rid in to_delete:
                    self._conn.execute(
                        "DELETE FROM vectors WHERE id = ? AND collection = ?",
                        (rid, self.collection)
                    )
                self._conn.commit()
                self._cache_dirty = True
                return len(to_delete)

    # ==================== 搜索 ====================

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 10,
        filter_metadata: Optional[Dict] = None,
        min_importance: float = 0.0,
        metric: str = "cosine",
    ) -> List[Dict]:
        """
        向量相似度搜索

        搜索优先级：
        1. sqlite-vec 扩展（最快，需要 .so 扩展）
        2. hnswlib C 扩展（预编译嵌入或 pip 安装，近似搜索 O(log n)）
        3. 内置 HNSW 索引（纯 Python，沙箱环境可用，近似搜索 O(log n)）
        4. numpy 暴力搜索（精确搜索 O(n)，兜底方案）

        Args:
            query_vector: 查询向量
            top_k: 返回数量
            filter_metadata: 元数据过滤条件
            min_importance: 最低重要性阈值
            metric: 距离度量 ('cosine' 或 'l2')

        Returns:
            List[Dict]: 搜索结果，每条包含 id, content, score, metadata 等
        """
        if query_vector.shape[0] != self.dimension:
            if query_vector.shape[0] > self.dimension:
                query_vector = query_vector[:self.dimension]
            else:
                padded = np.zeros(self.dimension, dtype=np.float32)
                padded[:query_vector.shape[0]] = query_vector
                query_vector = padded

        # 1. 尝试 sqlite-vec 搜索（最快）
        if self._vec_available and filter_metadata is None and min_importance <= 0:
            results = self._search_with_vec(query_vector, top_k, metric)
            if results is not None:
                return results

        # 2. 尝试 hnswlib C 扩展搜索（预编译嵌入或 pip 安装）
        if self._hnswlib_mod is not None and filter_metadata is None and min_importance <= 0:
            results = self._search_with_hnswlib(query_vector, top_k, metric)
            if results is not None:
                return results

        # 3. 尝试内置 HNSW 索引搜索（纯 Python，沙箱环境可用）
        if self._enable_hnsw and filter_metadata is None and min_importance <= 0:
            results = self._search_with_hnsw(query_vector, top_k, metric)
            if results is not None:
                return results

        # 4. 回退到 numpy 暴力搜索
        return self._search_with_numpy(query_vector, top_k, filter_metadata, min_importance, metric)

    def _search_with_vec(
        self,
        query_vector: np.ndarray,
        top_k: int,
        metric: str,
    ) -> Optional[List[Dict]]:
        """使用 sqlite-vec 扩展搜索"""
        try:
            # 创建临时 vec 表
            self._conn.execute("DROP TABLE IF EXISTS _tmp_query_vec")
            self._conn.execute(
                f"CREATE VIRTUAL TABLE _tmp_query_vec USING vec0("
                f"query_vector float[{self.dimension}])"
            )
            self._conn.execute(
                "INSERT INTO _tmp_query_vec(rowid, query_vector) VALUES (1, ?)",
                (_pack_vector(query_vector),)
            )

            distance_col = "distance"
            self._conn.execute("DROP TABLE IF EXISTS _tmp_search_results")
            self._conn.execute(f"""
                CREATE TEMPORARY TABLE _tmp_search_results AS
                SELECT v.id, v.content, v.metadata, v.importance,
                       v.access_count, v.last_access, v.created_at, v.updated_at,
                       s.{distance_col} AS score
                FROM _tmp_query_vec q
                JOIN vectors v ON v.collection = ?
                JOIN (
                    SELECT rowid, {distance_col}
                    FROM vec_top_k(
                        (SELECT query_vector FROM _tmp_query_vec WHERE rowid = 1),
                        ?
                    )
                ) s ON v.rowid = s.rowid
                ORDER BY s.{distance_col}
            """, (self.collection, top_k))

            cursor = self._conn.execute(
                "SELECT id, content, metadata, importance, score, "
                "access_count, last_access, created_at, updated_at "
                "FROM _tmp_search_results"
            )

            results = []
            for row in cursor.fetchall():
                d = self._row_to_dict(row[:7])
                d["score"] = row[4] if metric == "l2" else 1.0 - row[4]
                results.append(d)

            self._conn.execute("DROP TABLE IF EXISTS _tmp_query_vec")
            self._conn.execute("DROP TABLE IF EXISTS _tmp_search_results")
            self._conn.commit()

            return results

        except Exception as e:
            logger.debug(f"sqlite-vec 搜索失败，回退 numpy: {e}")
            # 清理临时表
            try:
                self._conn.execute("DROP TABLE IF EXISTS _tmp_query_vec")
                self._conn.execute("DROP TABLE IF EXISTS _tmp_search_results")
                self._conn.commit()
            except Exception:
                pass
            return None

    def _ensure_hnswlib_index(self) -> bool:
        """
        确保 hnswlib C 扩展索引已构建且与数据库同步

        Returns:
            bool: 索引是否可用
        """
        if self._hnswlib_mod is None:
            return False

        all_records = self._load_all_vectors()
        total = len(all_records)

        # 如果索引已构建且元素数匹配，无需重建
        if (self._hnswlib_index is not None
                and self._hnswlib_elements == total
                and not self._hnsw_dirty):
            return True

        # 需要重建索引
        try:
            max_elements = max(total * 2, 1024)  # 预留空间
            space = 'cosine' if self._hnswlib_index is None or self._hnswlib_dim == self.dimension else 'l2'

            # 重新创建索引
            index = self._hnswlib_mod.Index(space=space, dim=self.dimension)
            index.init_index(max_elements=max_elements, ef_construction=200, M=16)

            # 批量插入
            if all_records:
                ids = list(all_records.keys())
                vectors = np.stack([all_records[k][0] for k in ids]).astype(np.float32)
                # hnswlib 需要连续的整数 label
                self._hnswlib_id_map = {i: id_ for i, id_ in enumerate(ids)}
                self._hnswlib_id_reverse = {id_: i for i, id_ in enumerate(ids)}
                index.add_items(vectors, np.arange(len(ids)))
                index.set_ef(50)

            self._hnswlib_index = index
            self._hnswlib_elements = total
            self._hnsw_dirty = False
            logger.info(f"hnswlib C 扩展索引重建完成: {total} 条记录")
            return True
        except Exception as e:
            logger.warning(f"hnswlib C 扩展索引构建失败: {e}")
            self._hnswlib_index = None
            return False

    def _search_with_hnswlib(
        self,
        query_vector: np.ndarray,
        top_k: int,
        metric: str,
    ) -> Optional[List[Dict]]:
        """使用 hnswlib C 扩展搜索"""
        if not self._ensure_hnswlib_index():
            return None

        try:
            labels, distances = self._hnswlib_index.knn_query(
                query_vector.astype(np.float32).reshape(1, -1), k=top_k
            )

            all_records = self._load_all_vectors()
            results = []

            for label, dist in zip(labels[0], distances[0]):
                label = int(label)
                id_ = self._hnswlib_id_map.get(label)
                if id_ is None or id_ not in all_records:
                    continue
                _, meta = all_records[id_]
                if metric == "cosine":
                    score = max(0.0, 1.0 - float(dist))
                else:
                    score = -float(dist)
                results.append({
                    "id": id_,
                    "content": meta.get("content", ""),
                    "score": score,
                    "metadata": meta.get("metadata", {}),
                    "importance": meta.get("importance", 0.5),
                })

            results.sort(key=lambda x: x["score"], reverse=True)

            for r in results[:top_k]:
                self._increment_access(r["id"])

            return results[:top_k]
        except Exception as e:
            logger.debug(f"hnswlib C 扩展搜索失败，回退内置 HNSW: {e}")
            return None

    def _ensure_hnsw_index(self) -> bool:
        """
        确保 HNSW 索引已构建且与数据库同步

        Returns:
            bool: 索引是否可用
        """
        if not self._enable_hnsw or self._hnsw is None:
            return False

        if not self._hnsw_dirty and len(self._hnsw) > 0:
            return True

        # 需要重建索引
        try:
            self._hnsw = HNSWIndex(
                dim=self.dimension,
                M=self._hnsw.M,
                ef_construction=self._hnsw.ef_construction,
                ef_search=self._hnsw.ef_search,
                metric=self._hnsw.metric,
            )

            all_records = self._load_all_vectors()
            for id_, (vec, meta) in all_records.items():
                self._hnsw.insert(id_, vec)

            self._hnsw_dirty = False
            logger.info(f"HNSW 索引重建完成: {len(self._hnsw)} 条记录")
            return len(self._hnsw) > 0
        except Exception as e:
            logger.warning(f"HNSW 索引构建失败: {e}")
            return False

    def _search_with_hnsw(
        self,
        query_vector: np.ndarray,
        top_k: int,
        metric: str,
    ) -> Optional[List[Dict]]:
        """使用内置 HNSW 索引搜索"""
        if not self._ensure_hnsw_index():
            return None

        try:
            hnsw_results = self._hnsw.search(query_vector, top_k=top_k * 2)
            if not hnsw_results:
                return None

            # HNSW 返回近似结果，用元数据补充
            all_records = self._load_all_vectors()
            results = []
            for id_, dist in hnsw_results:
                if id_ not in all_records:
                    continue
                _, meta = all_records[id_]
                # 距离转相似度
                if metric == "cosine":
                    score = max(0.0, 1.0 - dist)
                else:
                    score = -dist
                results.append({
                    "id": id_,
                    "content": meta.get("content", ""),
                    "score": score,
                    "metadata": meta.get("metadata", {}),
                    "importance": meta.get("importance", 0.5),
                })

            results.sort(key=lambda x: x["score"], reverse=True)

            for r in results[:top_k]:
                self._increment_access(r["id"])

            return results[:top_k]
        except Exception as e:
            logger.debug(f"HNSW 搜索失败，回退 numpy: {e}")
            return None

    def _search_with_numpy(
        self,
        query_vector: np.ndarray,
        top_k: int,
        filter_metadata: Optional[Dict],
        min_importance: float,
        metric: str,
    ) -> List[Dict]:
        """numpy 暴力搜索"""
        all_records = self._load_all_vectors()

        if not all_records:
            return []

        # 过滤
        filtered = []
        for id_, (vec, meta) in all_records.items():
            if min_importance > 0 and meta.get("importance", 0) < min_importance:
                continue
            if filter_metadata:
                match = all(meta.get(k) == v for k, v in filter_metadata.items())
                if not match:
                    continue
            filtered.append((id_, vec, meta))

        if not filtered:
            return []

        # 计算相似度
        query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-10)
        results = []

        for id_, vec, meta in filtered:
            if metric == "cosine":
                score = _cosine_similarity(query_vector, vec)
            else:  # l2
                score = -float(np.linalg.norm(query_vector - vec))

            results.append({
                "id": id_,
                "content": meta.get("content", ""),
                "score": score,
                "metadata": meta.get("metadata", {}),
                "importance": meta.get("importance", 0.5),
            })

        # 排序
        results.sort(key=lambda x: x["score"], reverse=True)

        # 更新访问计数
        for r in results[:top_k]:
            self._increment_access(r["id"])

        return results[:top_k]

    def _load_all_vectors(self) -> Dict[str, Tuple[np.ndarray, Dict]]:
        """加载当前集合所有向量到内存"""
        if not self._cache_dirty and self._cache:
            return self._cache

        cache = {}
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id, content, vector, metadata, importance "
                "FROM vectors WHERE collection = ?",
                (self.collection,)
            )
            for row in cursor.fetchall():
                id_, content, vec_blob, meta_str, importance = row
                vec = _unpack_vector(vec_blob, self.dimension)
                try:
                    metadata = json.loads(meta_str) if meta_str else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
                cache[id_] = (vec, {
                    "content": content,
                    "metadata": metadata,
                    "importance": importance,
                })

        self._cache = cache
        self._cache_dirty = False
        return cache

    def _scan_all(self) -> Dict[str, Tuple[np.ndarray, Dict]]:
        """扫描所有记录（同 _load_all_vectors）"""
        return self._load_all_vectors()

    def _increment_access(self, id: str):
        """增加访问计数"""
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE vectors SET access_count = access_count + 1, "
                    "last_access = ? WHERE id = ? AND collection = ?",
                    (time.time(), id, self.collection)
                )
                self._conn.commit()
        except Exception:
            pass

    # ==================== 辅助方法 ====================

    def _row_to_dict(self, row) -> Dict:
        """将数据库行转为字典"""
        id_, content, vec_blob, meta_str, importance = row[:5]
        created_at = row[5] if len(row) > 5 else None
        updated_at = row[6] if len(row) > 6 else None
        access_count = row[7] if len(row) > 7 else 0
        last_access = row[8] if len(row) > 8 else None

        try:
            metadata = json.loads(meta_str) if meta_str else {}
        except (json.JSONDecodeError, TypeError):
            metadata = {}

        return {
            "id": id_,
            "content": content,
            "vector": _unpack_vector(vec_blob, self.dimension) if vec_blob else None,
            "metadata": metadata,
            "importance": importance,
            "created_at": created_at,
            "updated_at": updated_at,
            "access_count": access_count,
            "last_access": last_access,
        }

    def count(self) -> int:
        """获取记录总数"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT COUNT(*) FROM vectors WHERE collection = ?",
                (self.collection,)
            )
            return cursor.fetchone()[0]

    def list_ids(self, limit: int = 1000, offset: int = 0) -> List[str]:
        """列出所有 ID"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT id FROM vectors WHERE collection = ? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (self.collection, limit, offset)
            )
            return [row[0] for row in cursor.fetchall()]

    def get_stats(self) -> Dict:
        """获取统计信息"""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT COUNT(*), AVG(importance), SUM(access_count) "
                "FROM vectors WHERE collection = ?",
                (self.collection,)
            )
            row = cursor.fetchone()
            count = row[0] or 0
            avg_importance = row[1] or 0.0
            total_access = row[2] or 0

        return {
            "collection": self.collection,
            "count": count,
            "dimension": self.dimension,
            "avg_importance": round(avg_importance, 3),
            "total_access": total_access,
            "vec_extension": self._vec_available,
            "hnswlib_available": self._hnswlib_mod is not None,
            "hnsw_enabled": self._enable_hnsw,
            "hnsw_size": len(self._hnsw) if self._hnsw else 0,
            "db_path": self.db_path,
        }

    def close(self):
        """关闭数据库连接"""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class VectorStoreManager:
    """
    向量存储管理器

    管理多个集合的 VectorStore 实例。
    """

    def __init__(
        self,
        db_path: str = "~/.openclaw/memory/vectors.db",
        dimension: int = 4096,
        enable_hnsw: bool = True,
    ):
        self.db_path = db_path
        self.dimension = dimension
        self.enable_hnsw = enable_hnsw
        self._stores: Dict[str, VectorStore] = {}
        self._lock = threading.Lock()

    def get_store(self, collection: str = "default") -> VectorStore:
        """获取指定集合的 VectorStore"""
        with self._lock:
            if collection not in self._stores:
                self._stores[collection] = VectorStore(
                    db_path=self.db_path,
                    dimension=self.dimension,
                    collection=collection,
                    enable_hnsw=self.enable_hnsw,
                )
            return self._stores[collection]

    def list_collections(self) -> List[str]:
        """列出所有集合"""
        with self._lock:
            if not self._stores:
                return []
            store = next(iter(self._stores.values()))
            with store._lock:
                cursor = store._conn.execute(
                    "SELECT DISTINCT collection FROM vectors"
                )
                return [row[0] for row in cursor.fetchall()]

    def close_all(self):
        """关闭所有存储"""
        with self._lock:
            for store in self._stores.values():
                store.close()
            self._stores.clear()


__all__ = ["VectorStore", "VectorStoreManager", "HNSWIndex"]
