#!/usr/bin/env python3
"""
统一向量存储接口

整合 memory-tencentdb 和 llm-memory-integration 的向量检索能力。
"""

import os
import json
import sqlite3
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from abc import ABC, abstractmethod
from galaxyos.shared.paths import galaxyos_home

logger = logging.getLogger(__name__)


@dataclass
class VectorRecord:
    """向量记录"""
    id: str
    vector: List[float]
    metadata: Dict[str, Any]
    content: str
    source: str  # 来源: 'memory-tdai', 'llm-memory', 'brain', 'ontology'


class VectorStoreBackend(ABC):
    """向量存储后端抽象类"""

    @abstractmethod
    def add(self, records: List[VectorRecord]) -> int:
        """添加向量记录"""
        pass

    @abstractmethod
    def search(self, query_vector: List[float], top_k: int = 10,
               filters: Optional[Dict] = None) -> List[Tuple[VectorRecord, float]]:
        """搜索相似向量"""
        pass

    @abstractmethod
    def delete(self, ids: List[str]) -> int:
        """删除向量记录"""
        pass

    @abstractmethod
    def count(self) -> int:
        """获取记录总数"""
        pass


class SQLiteVecBackend(VectorStoreBackend):
    """sqlite-vec 后端"""

    def __init__(self, db_path: str, dim: int = 1024):
        self.db_path = db_path
        self.dim = dim
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 创建向量表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS vectors (
                id TEXT PRIMARY KEY,
                content TEXT,
                metadata TEXT,
                source TEXT,
                embedding BLOB
            )
        ''')

        conn.commit()
        conn.close()

    def add(self, records: List[VectorRecord]) -> int:
        """添加向量记录"""
        import numpy as np

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        count = 0
        for record in records:
            try:
                vec = np.array(record.vector, dtype=np.float32)
                # 自动补齐到目标维度（不足的补零，超出截断）
                if len(vec) < self.dim:
                    padded = np.zeros(self.dim, dtype=np.float32)
                    padded[:len(vec)] = vec
                    vec = padded
                elif len(vec) > self.dim:
                    vec = vec[:self.dim]
                vector_bytes = vec.tobytes()
                cursor.execute('''
                    INSERT OR REPLACE INTO vectors (id, content, metadata, source, embedding)
                    VALUES (?, ?, ?, ?, ?)
                ''', (record.id, record.content, json.dumps(record.metadata),
                      record.source, vector_bytes))
                count += 1
            except Exception as e:
                logger.error(f"添加向量失败: {record.id}, {e}")

        conn.commit()
        conn.close()
        return count

    def search(self, query_vector: List[float], top_k: int = 10,
               filters: Optional[Dict] = None) -> List[Tuple[VectorRecord, float]]:
        """搜索相似向量（暴力搜索，适合小规模数据）"""
        import numpy as np

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 构建查询
        query = "SELECT id, content, metadata, source, embedding FROM vectors"
        params = []

        if filters and 'source' in filters:
            query += " WHERE source = ?"
            params.append(filters['source'])

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        # 如果查询向量维度小于存储维度，补零对齐
        query_vec = np.array(query_vector, dtype=np.float32)
        if len(query_vec) < self.dim:
            padded = np.zeros(self.dim, dtype=np.float32)
            padded[:len(query_vec)] = query_vec
            query_vec = padded
        query_norm = np.linalg.norm(query_vec)

        results = []
        for row in rows:
            id_, content, metadata, source, embedding_bytes = row
            vec = np.frombuffer(embedding_bytes, dtype=np.float32) if (embedding_bytes and len(embedding_bytes) > 0) else None

            if vec is None or len(vec) != self.dim:
                continue

            vec_norm = np.linalg.norm(vec)
            if query_norm > 0 and vec_norm > 0:
                similarity = np.dot(query_vec, vec) / (query_norm * vec_norm)
            else:
                similarity = 0.0

            record = VectorRecord(
                id=id_,
                vector=vec.tolist(),
                metadata=json.loads(metadata) if metadata else {},
                content=content,
                source=source
            )
            results.append((record, float(similarity)))

        # 排序并返回 top_k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def delete(self, ids: List[str]) -> int:
        """删除向量记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        placeholders = ','.join('?' * len(ids))
        cursor.execute(f"DELETE FROM vectors WHERE id IN ({placeholders})", ids)
        count = cursor.rowcount

        conn.commit()
        conn.close()
        return count

    def count(self) -> int:
        """获取记录总数"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM vectors")
        count = cursor.fetchone()[0]
        conn.close()
        return count


class HNSWLibBackend(VectorStoreBackend):
    """HNSWLib 后端 — 基于 hnswlib"""

    def __init__(self, index_path: str, dim: int = 1024, ef_construction: int = 200, M: int = 16):
        self.index_path = index_path
        self.dim = dim
        self.ef_construction = ef_construction
        self.M = M
        self.index = None
        self.id_map: Dict[int, str] = {}   # hnsw internal_id -> str_id
        self.rev_map: Dict[str, int] = {}  # str_id -> hnsw internal_id
        self.records: Dict[str, VectorRecord] = {}
        self._next_id = 0
        self._init_index()

    def _init_index(self):
        import hnswlib
        if os.path.exists(self.index_path):
            try:
                self.index = hnswlib.Index(space='ip', dim=self.dim)
                self.index.load_index(self.index_path)
                # 加载元数据
                meta_path = self.index_path + '.meta'
                if os.path.exists(meta_path):
                    with open(meta_path, 'r') as f:
                        meta = json.load(f)
                        self._next_id = meta.get('next_id', 0)
                        for sid, rec_dict in meta.get('records', {}).items():
                            self.records[sid] = VectorRecord(**rec_dict)
                        # 重建 id_map 和 rev_map
                        id_map_raw = meta.get('id_map', {})
                        for int_id_str, str_id in id_map_raw.items():
                            self.id_map[int(int_id_str)] = str_id
                            self.rev_map[str_id] = int(int_id_str)
                return
            except Exception:
                pass  # 无法加载，重建

        self.index = hnswlib.Index(space='ip', dim=self.dim)
        self.index.init_index(max_elements=10000, ef_construction=self.ef_construction, M=self.M)

    def _save_index(self):
        import hnswlib
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        self.index.save_index(self.index_path)
        meta_path = self.index_path + '.meta'
        _records_meta = {}
        for sid, r in self.records.items():
            _records_meta[sid] = {
                'id': r.id, 'vector': r.vector,
                'metadata': r.metadata, 'content': r.content, 'source': r.source
            }
        with open(meta_path, 'w') as f:
            json.dump({
                'next_id': self._next_id,
                'id_map': {str(k): v for k, v in self.id_map.items()},
                'records': _records_meta,
            }, f)

    def add(self, records: List[VectorRecord]) -> int:
        import numpy as np
        old_count = self.index.element_count
        need = len(records)
        if old_count + need > self.index.max_elements:
            self.index.resize_index(old_count + need + 10000)

        for record in records:
            vec = np.array(record.vector, dtype=np.float32).reshape(1, -1)
            internal_id = self._next_id
            self.index.add_items(vec, [internal_id])
            self.id_map[internal_id] = record.id
            self.rev_map[record.id] = internal_id
            self.records[record.id] = record
            self._next_id += 1

        self._save_index()
        return len(records)

    def search(self, query_vector: List[float], top_k: int = 10,
               filters: Optional[Dict] = None) -> List[Tuple[VectorRecord, float]]:
        import numpy as np
        if self.index.element_count == 0:
            return []

        q = np.array(query_vector, dtype=np.float32)
        # 自动补齐到索引维度
        if len(q) < self.dim:
            padded = np.zeros(self.dim, dtype=np.float32)
            padded[:len(q)] = q
            q = padded
        q = q.reshape(1, -1)

        k = min(top_k, self.index.element_count)
        labels, distances = self.index.knn_query(q, k=k)

        results = []
        for i in range(len(labels[0])):
            internal_id = labels[0][i]
            sid = self.id_map.get(internal_id)
            if sid is None:
                continue
            record = self.records.get(sid)
            if record is None:
                continue
            if filters and 'source' in filters:
                if record.source != filters['source']:
                    continue
            results.append((record, float(distances[0][i])))

        return results

    def delete(self, ids: List[str]) -> int:
        """HNSWLib 不支持直接删除，标记删除后重建"""
        count = 0
        to_keep = []
        for sid, rec in self.records.items():
            if sid in ids:
                count += 1
            else:
                to_keep.append(rec)

        if count == 0:
            return 0

        import hnswlib
        self.index = hnswlib.Index(space='ip', dim=self.dim)
        self.index.init_index(max_elements=max(10000, len(to_keep) + 1000),
                              ef_construction=self.ef_construction, M=self.M)
        self.id_map.clear()
        self.rev_map.clear()
        self.records.clear()
        self._next_id = 0
        if to_keep:
            self.add(to_keep)
        return count

    def count(self) -> int:
        return self.index.element_count if self.index else 0


class FAISSBackend(VectorStoreBackend):
    """
    FAISS 后端 [v2: ANNSelector 自动选择索引+量化策略]

    根据向量数量自动选择:
    - < 5000:  HNSWFlat（最大精度）
    - < 50K:   IVFScalarQuantizer（INT8 标量量化, 4x 压缩）
    - < 500K:  IVFPQ（乘积量化, 8x 压缩）
    - > 500K:  IVFPQ 高压缩（16x 压缩）
    """

    def __init__(self, index_path: str, dim: int = 1024,
                 precision: str = 'balanced'):
        self.index_path = index_path
        self.dim = dim
        self.precision = precision
        self.selector = None  # ANNSelector 实例
        self.id_map = {}  # id -> index
        self.records = []  # 存储记录元数据
        self._loaded_from_disk = False
        self._init_index()

    def _init_index(self):
        """初始化 FAISS 索引（延迟：到 add 时根据数量选择）"""
        try:
            import faiss

            if Path(self.index_path).exists() and os.path.getsize(self.index_path) > 0:
                self.selector = ANNSelector.__new__(ANNSelector)
                self.selector.dim = self.dim
                self.selector.precision = self.precision
                self.selector.faiss_index = faiss.read_index(self.index_path)
                self.selector.algorithm = self._detect_algo(self.selector.faiss_index)
                self.selector.n_vectors = self.selector.faiss_index.ntotal
                self.selector.vectors = None

                meta_path = self.index_path + '.meta'
                if Path(meta_path).exists():
                    with open(meta_path, 'r') as f:
                        meta = json.load(f)
                        self.id_map = meta.get('id_map', {})
                        self.records = [VectorRecord(**r) for r in meta.get('records', [])]
                self._loaded_from_disk = True
        except ImportError:
            logger.warning("FAISS 未安装")
            raise

    def _detect_algo(self, idx) -> str:
        """从 FAISS 索引类型推断算法名"""
        t = idx.__class__.__name__
        if 'PQ' in t:
            return 'ivfpq'
        if 'SQ' in t or 'Scalar' in t:
            return 'ivf_sq'
        if 'HNSW' in t:
            return 'hnsw'
        if 'IVF' in t:
            return 'ivf'
        return 'flat'

    def _ensure_selector(self, n: int):
        """确保 selector 已初始化（按当前向量数量自动选择索引策略）"""
        if self.selector is not None and self._loaded_from_disk:
            return
        if self.selector is not None:
            # 数量变化超过阈值时重建
            _delta = abs(self.selector.n_vectors - (len(self.records) + n))
            if _delta < 5000:
                return

        n_total = len(self.records) + n
        from ann_selector import ANNSelector

        try:
            import faiss
            sel = ANNSelector(
                n_total, self.dim, metric='cosine',
                precision=self.precision,
                index_path=self.index_path
            )
            if self.records:
                import numpy as np
                vecs = np.array([r.vector for r in self.records], dtype=np.float32)
                sel.build_index(vecs)
            self.selector = sel
        except Exception as e:
            logger.warning(f"ANNSelector init failed, fallback to FlatIP: {e}")
            self.selector = ANNSelector.__new__(ANNSelector)
            self.selector.dim = self.dim
            self.selector.n_vectors = len(self.records) + n
            self.selector.faiss_index = faiss.IndexFlatIP(self.dim)
            if self.records:
                import numpy as np
                self.selector.faiss_index.add(
                    np.array([r.vector for r in self.records], dtype=np.float32))
            self.selector.algorithm = 'flat'

    def add(self, records: List[VectorRecord]) -> int:
        """添加向量记录"""
        import numpy as np

        self._ensure_selector(len(records))

        vectors = []
        for record in records:
            vec = np.array(record.vector, dtype=np.float32)
            vectors.append(vec)
            self.id_map[record.id] = len(self.records)
            self.records.append(record)

        if vectors:
            vectors_np = np.vstack(vectors)
            if self.selector.faiss_index:
                if self.selector.algorithm in ('ivfpq', 'ivfpq_high', 'ivf_sq'):
                    # IVF 系列不支持增量 add（需要 train），走 selector.add
                    self.selector.add(vectors_np)
                else:
                    self.selector.faiss_index.add(vectors_np)
            self.selector.n_vectors = len(self.records)
            self._save_index()

        return len(records)

    def search(self, query_vector: List[float], top_k: int = 10,
               filters: Optional[Dict] = None) -> List[Tuple[VectorRecord, float]]:
        import numpy as np

        if self.selector is None or self.selector.faiss_index is None:
            self._ensure_selector(0)
        if self.selector is None or self.selector.faiss_index is None:
            return []

        query_vec = np.array([query_vector], dtype=np.float32)
        k = min(top_k, len(self.records) or 1)
        if k <= 0:
            return []

        indices_arr, distances_arr = self.selector.search(query_vec, k)

        results = []
        for i in range(len(indices_arr)):
            idx = int(indices_arr[i])
            if idx < 0 or idx >= len(self.records):
                continue
            record = self.records[idx]
            if filters and 'source' in filters:
                if record.source != filters['source']:
                    continue
            results.append((record, float(distances_arr[i])))

        return results

    def delete(self, ids: List[str]) -> int:
        """删除向量记录（FAISS 不支持直接删除，需要重建索引）"""
        to_delete = set(ids)
        new_records = [r for r in self.records if r.id not in to_delete]

        if len(new_records) < len(self.records):
            import faiss

            self.selector = None
            self.id_map = {}
            self.records = []

            if new_records:
                self.add(new_records)

            return len(to_delete)
        return 0

    def _save_index(self):
        """保存索引"""
        if self.selector and self.selector.faiss_index:
            try:
                import faiss
                Path(self.index_path).parent.mkdir(parents=True, exist_ok=True)
                faiss.write_index(self.selector.faiss_index, self.index_path)

                meta_path = self.index_path + '.meta'
                with open(meta_path, 'w') as f:
                    json.dump({
                        'id_map': self.id_map,
                        'records': [{'id': r.id, 'vector': r.vector, 'metadata': r.metadata,
                                    'content': r.content, 'source': r.source} for r in self.records]
                    }, f)
            except Exception as e:
                logger.warning(f"FAISS save failed: {e}")

    def count(self) -> int:
        return len(self.records)


class UnifiedVectorStore:
    """
    统一向量存储接口
    
    整合多个后端，提供统一的向量存储和检索能力。
    """

    def __init__(self,
                 backend: str = 'hnswlib',
                 db_path: Optional[str] = None,
                 index_path: Optional[str] = None,
                 dim: int = 1024):
        """
        初始化统一向量存储
        
        Args:
            backend: 后端类型 ('sqlite' 或 'faiss')
            db_path: SQLite 数据库路径
            index_path: FAISS 索引路径
            dim: 向量维度
        """
        self.dim = dim

        # 默认路径
        openclaw_home = os.environ.get('OPENCLAW_HOME', Path(galaxyos_home()))
        if db_path is None:
            db_path = str(Path(openclaw_home) / 'memory-tdai' / 'unified_vectors.db')
        if index_path is None:
            index_path = str(Path(openclaw_home) / 'memory-tdai' / 'unified_vectors.faiss')

        # 初始化后端
        if backend == 'hnswlib':
            try:
                import hnswlib
                self.backend = HNSWLibBackend(index_path, dim)
            except ImportError:
                logger.warning("HNSWLib 不可用，回退到 SQLite")
                self.backend = SQLiteVecBackend(db_path, dim)
        elif backend == 'faiss':
            try:
                import faiss
                self.backend = FAISSBackend(index_path, dim)
            except ImportError:
                logger.warning("FAISS 不可用，回退到 SQLite")
                self.backend = SQLiteVecBackend(db_path, dim)
        else:
            self.backend = SQLiteVecBackend(db_path, dim)

        logger.info(f"统一向量存储初始化完成: backend={type(self.backend).__name__}, dim={dim}")

    def add_vectors(self,
                    vectors: List[List[float]],
                    contents: List[str],
                    metadatas: Optional[List[Dict]] = None,
                    ids: Optional[List[str]] = None,
                    source: str = 'unknown') -> int:
        """
        添加向量
        
        Args:
            vectors: 向量列表
            contents: 内容列表
            metadatas: 元数据列表
            ids: ID 列表
            source: 数据来源
        
        Returns:
            添加的记录数
        """
        import uuid

        if metadatas is None:
            metadatas = [{}] * len(vectors)
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in vectors]

        records = []
        for i, (vec, content, metadata, id_) in enumerate(zip(vectors, contents, metadatas, ids)):
            records.append(VectorRecord(
                id=id_,
                vector=vec,
                metadata=metadata,
                content=content,
                source=source
            ))

        return self.backend.add(records)

    def search(self,
               query_vector: List[float],
               top_k: int = 10,
               source_filter: Optional[str] = None) -> List[Dict]:
        """
        搜索相似向量
        
        Args:
            query_vector: 查询向量
            top_k: 返回数量
            source_filter: 来源过滤
        
        Returns:
            搜索结果列表
        """
        filters = {'source': source_filter} if source_filter else None
        results = self.backend.search(query_vector, top_k, filters)

        return [
            {
                'id': record.id,
                'content': record.content,
                'metadata': record.metadata,
                'source': record.source,
                'score': score
            }
            for record, score in results
        ]

    def delete(self, ids: List[str]) -> int:
        """删除向量"""
        return self.backend.delete(ids)

    def count(self) -> int:
        """获取记录总数"""
        return self.backend.count()

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'backend': type(self.backend).__name__,
            'dim': self.dim,
            'total_records': self.count()
        }


# 便捷函数
_default_store = None

def get_vector_store() -> UnifiedVectorStore:
    """获取默认向量存储实例"""
    global _default_store
    if _default_store is None:
        _default_store = UnifiedVectorStore()
    return _default_store


def add_memory_vectors(vectors: List[List[float]],
                       contents: List[str],
                       metadatas: Optional[List[Dict]] = None,
                       source: str = 'memory-tdai') -> int:
    """添加记忆向量"""
    return get_vector_store().add_vectors(vectors, contents, metadatas, source=source)


def search_memory(query_vector: List[float], top_k: int = 10) -> List[Dict]:
    """搜索记忆"""
    return get_vector_store().search(query_vector, top_k)
