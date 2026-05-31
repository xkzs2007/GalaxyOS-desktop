#!/usr/bin/env python3
"""
统一向量存储接口

"""

import os
import json
import sqlite3
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from abc import ABC, abstractmethod

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
    
    def __init__(self, db_path: str, dim: int = 4096):
        self.db_path = db_path
        self.dim = dim
        self._init_db()
    
    def _init_db(self):
        """初始化数据库"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建向量表
        cursor.execute(f'''
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
                vector_bytes = np.array(record.vector, dtype=np.float32).tobytes()
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
        
        # 计算相似度
        query_vec = np.array(query_vector, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        
        results = []
        for row in rows:
            id_, content, metadata, source, embedding_bytes = row
            vec = np.frombuffer(embedding_bytes, dtype=np.float32) if (embedding_bytes and len(embedding_bytes) > 0) else None
            
            if vec is None or len(vec) != len(query_vector):
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

    def __init__(self, index_path: str, dim: int = 4096, ef_construction: int = 200, M: int = 16):
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
                            if sid in self.id_map_repr:
                                pass  # rev_map 从 id_map 重建
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
        with open(meta_path, 'w') as f:
            json.dump({
                'next_id': self._next_id,
                'records': {sid: {
                    'id': r.id, 'vector': r.vector, 'metadata': r.metadata,
                    'content': r.content, 'source': r.source
                } for sid, r in self.records.items()}
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

        q = np.array([query_vector], dtype=np.float32)
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
    """FAISS 后端"""
    
    def __init__(self, index_path: str, dim: int = 4096):
        self.index_path = index_path
        self.dim = dim
        self.index = None
        self.id_map = {}  # id -> index
        self.records = []  # 存储记录元数据
        self._init_index()
    
    def _init_index(self):
        """初始化 FAISS 索引"""
        try:
            import faiss
            
            if Path(self.index_path).exists():
                self.index = faiss.read_index(self.index_path)
                # 加载 id_map 和 records
                meta_path = self.index_path + '.meta'
                if Path(meta_path).exists():
                    with open(meta_path, 'r') as f:
                        meta = json.load(f)
                        self.id_map = meta.get('id_map', {})
                        self.records = [VectorRecord(**r) for r in meta.get('records', [])]
            else:
                self.index = faiss.IndexFlatIP(self.dim)
        except ImportError:
            logger.warning("FAISS 未安装，使用 SQLite 后端")
            raise
    
    def add(self, records: List[VectorRecord]) -> int:
        """添加向量记录"""
        import numpy as np
        
        vectors = []
        for record in records:
            vec = np.array(record.vector, dtype=np.float32)
            vectors.append(vec)
            self.id_map[record.id] = len(self.records)
            self.records.append(record)
        
        if vectors:
            vectors_np = np.vstack(vectors)
            self.index.add(vectors_np)
            self._save_index()
        
        return len(records)
    
    def search(self, query_vector: List[float], top_k: int = 10,
               filters: Optional[Dict] = None) -> List[Tuple[VectorRecord, float]]:
        """搜索相似向量"""
        import numpy as np
        
        query_vec = np.array([query_vector], dtype=np.float32)
        distances, indices = self.index.search(query_vec, min(top_k, len(self.records)))
        
        results = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.records):
                record = self.records[idx]
                # 应用过滤器
                if filters and 'source' in filters:
                    if record.source != filters['source']:
                        continue
                results.append((record, float(distances[0][i])))
        
        return results
    
    def delete(self, ids: List[str]) -> int:
        """删除向量记录（FAISS 不支持直接删除，需要重建索引）"""
        # 标记删除
        to_delete = set(ids)
        new_records = [r for r in self.records if r.id not in to_delete]
        
        if len(new_records) < len(self.records):
            # 重建索引
            import faiss
            import numpy as np
            
            self.index = faiss.IndexFlatIP(self.dim)
            self.id_map = {}
            self.records = []
            
            for record in new_records:
                self.add([record])
            
            return len(to_delete)
        return 0
    
    def _save_index(self):
        """保存索引"""
        import faiss
        
        Path(self.index_path).parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, self.index_path)
        
        # 保存元数据
        meta_path = self.index_path + '.meta'
        with open(meta_path, 'w') as f:
            json.dump({
                'id_map': self.id_map,
                'records': [{'id': r.id, 'vector': r.vector, 'metadata': r.metadata, 
                            'content': r.content, 'source': r.source} for r in self.records]
            }, f)
    
    def count(self) -> int:
        """获取记录总数"""
        return len(self.records)


class UnifiedVectorStore:
    """
    统一向量存储接口
    
    整合多个后端，提供统一的向量存储和检索能力。
    """
    
    def __init__(self, 
                 backend: str = 'sqlite',
                 db_path: Optional[str] = None,
                 index_path: Optional[str] = None,
                 dim: int = 4096):
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
        openclaw_home = os.environ.get('OPENCLAW_HOME', Path.home() / '.openclaw')
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
