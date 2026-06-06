#!/usr/bin/env python3
"""
Yaoyao Bridge — Python 侧读写 yaoyao-plugin 的 .yaoyao.db（SQLite + FTS5 + sqlite-vec）

功能：
  1. store_to_yaoyao(content, source) — 写一条到 yaoyao 的 FTS5 + vec
  2. search_yaoyao_fts(query, limit) — FTS5 全文检索
  3. search_yaoyao_vec(query, limit) — 向量检索（走 Gitee Qwen3）
  4. search_yaoyao(query, limit) — 混合搜索（FTS5 + vec + RRF）

设计目标：最小依赖，只用 sqlite3 + requests + embedding_client。
yaoyao DB 结构（Node sqlite-vec 创建）：
  - memory_meta(id, date, user_text, asst_text, created_at, source_session)
  - memory_fts(VIRTUAL, FTS5: date, user_text, asst_text)
  - memory_vec(VIRTUAL, vec0: embedding float[D])
  - memory_vec_meta(id, meta_id, model, dimensions, created_at)
"""

import json
import os
import re
import sqlite3
import struct
import time
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import hashlib
import logging

import numpy as np
import hnswlib

logger = logging.getLogger(__name__)

# ── 路径 ──────────────────────────────────────────────────────────
OPENCLAW_HOME = Path(os.environ.get("OPENCLAW_HOME", os.path.expanduser("~/.openclaw")))
WORKSPACE_DIR = OPENCLAW_HOME / "workspace"
YAOYAO_DB_PATH = WORKSPACE_DIR / "memory" / ".yaoyao.db"
YAOYAO_HNDATA_PATH = Path(os.environ.get(
    "YAOYAO_HNDATA_PATH",
    str(WORKSPACE_DIR / "memory" / ".yaoyao.hndata"),
))
MEMORY_DIR = WORKSPACE_DIR / "memory"

# ── hnswlib 索引（替换 sqlite-vec0）───────────────────────────────
_HNSW_INDEX = None
_HNSW_ID_MAP: Dict[int, int] = {}  # sequence_id -> meta_id

def _ensure_hnsw_index():
    """Lazy-load hnswlib 持久化索引"""
    global _HNSW_INDEX, _HNSW_ID_MAP
    if _HNSW_INDEX is not None:
        return _HNSW_INDEX
    try:
        idx = hnswlib.Index(space='cosine', dim=EMBEDDING_DIM)
        hndata = str(YAOYAO_HNDATA_PATH)
        if os.path.exists(hndata):
            idx.load_index(hndata)
            logger.info(f"hnswlib 索引已加载: {hndata}")
        else:
            idx.init_index(max_elements=10000, ef_construction=200, M=16)
            logger.info(f"hnswlib 索引初始化 (max=10000, dim={EMBEDDING_DIM})")
        _HNSW_INDEX = idx
        
        # 从 memory_meta 重建 id 映射（如果索引是空的但 DB 有数据）
        if idx.element_count == 0 and os.path.exists(str(YAOYAO_DB_PATH)):
            try:
                c = sqlite3.connect(str(YAOYAO_DB_PATH))
                rows = c.execute(
                    "SELECT id FROM memory_meta ORDER BY id"
                ).fetchall()
                for seq, (mid,) in enumerate(rows):
                    _HNSW_ID_MAP[seq] = mid
                c.close()
                if rows:
                    logger.info(f"重建 id 映射: {len(rows)} 条")
            except Exception as e:
                logger.warning(f"重建 id 映射失败: {e}")
        else:
            _HNSW_ID_MAP = {}
        return idx
    except Exception as e:
        logger.error(f"hnswlib 索引初始化失败: {e}")
        return None

def _save_hnsw_index():
    """保存 hnswlib 索引到文件"""
    global _HNSW_INDEX
    if _HNSW_INDEX is None:
        return
    try:
        hndata = str(YAOYAO_HNDATA_PATH)
        _HNSW_INDEX.save_index(hndata)
        logger.info(f"hnswlib 索引已保存 ({_HNSW_INDEX.element_count} 个向量)")
    except Exception as e:
        logger.warning(f"hnswlib 保存失败: {e}")

# ── Embedding 配置 ────────────────────────────────────────────────
EMBEDDING_URL = os.environ.get(
    "YAOYAO_EMBEDDING_URL",
    "https://cloud.infini-ai.com/maas/v1/embeddings",
)
EMBEDDING_API_KEY = os.environ.get(
    "YAOYAO_EMBEDDING_KEY",
    os.environ.get("INFINI_API_KEY", ""),
)
EMBEDDING_MODEL = "bge-m3"
EMBEDDING_DIM = 1024

# ── Reranker 配置（无问芯穹 bge-reranker-v2-m3，免费）──────────
RERANKER_URL = os.environ.get(
    "YAOYAO_RERANKER_URL",
    "https://cloud.infini-ai.com/maas/v1/rerank",
)
RERANKER_API_KEY = os.environ.get(
    "YAOYAO_RERANKER_KEY",
    os.environ.get("INFINI_API_KEY", ""),
)
RERANKER_MODEL = "bge-reranker-v2-m3"

# ── RRF 融合 ──────────────────────────────────────────────────────
RRF_K = 60
VEC_WEIGHT = 0.6
FTS_WEIGHT = 0.4


# ====================================================================
#  DB 连接管理
# ====================================================================

class YaoyaoDB:
    """yaoyao .yaoyao.db 连接封装 — 写/查分离"""

    def __init__(self, db_path: str = None):
        self.db_path = str(db_path or YAOYAO_DB_PATH)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


# ====================================================================
#  Embedding
# ====================================================================

class EmbeddingClient:
    """轻量 Embedding 客户端 — 直接 httpx/requests"""

    def __init__(self):
        self.api_key = EMBEDDING_API_KEY
        self.url = EMBEDDING_URL
        self.model = EMBEDDING_MODEL
        self.dimensions = EMBEDDING_DIM

    def embed(self, text: str) -> Optional[List[float]]:
        """获取单条文本的 embedding"""
        if not text:
            return None
        import requests
        try:
            resp = requests.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": text[:2000],
                    "dimensions": self.dimensions,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["data"][0]["embedding"]
            else:
                logger.warning(f"Embedding API error: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            logger.warning(f"Embedding request failed: {e}")
            return None


# ====================================================================
#  写操作
# ====================================================================

_EMBEDDING_CLIENT = None
def _get_embedding_client():
    global _EMBEDDING_CLIENT
    if _EMBEDDING_CLIENT is None:
        _EMBEDDING_CLIENT = EmbeddingClient()
    return _EMBEDDING_CLIENT


def _vector_to_blob(vec: List[float]) -> bytes:
    """List[float] → binary blob (float32 array)"""
    return struct.pack(f"{len(vec)}f", *vec)


def store_to_yaoyao(
    content: str,
    source: str = "user",
    source_session: str = "",
) -> int:
    """
    写入一条记忆到 .yaoyao.db（FTS5 + vec + meta）
    
    Args:
        content: 记忆内容
        source: 来源（user/ai/observation）
        source_session: 来源会话 ID
        
    Returns:
        memory_meta.id，失败返回 -1
    """
    today = date.today().isoformat()
    content_clean = content.strip()[:2000]  # 截断过长的内容
    
    # 用户文本 vs AI 文本
    if source == "user":
        user_text = content_clean
        asst_text = ""
    elif source in ("ai", "observation"):
        user_text = ""
        asst_text = content_clean
    else:
        user_text = content_clean
        asst_text = ""
    
    try:
        db = YaoyaoDB()
        conn = db.connect()
        
        # 1. 写 memory_meta
        cur = conn.execute(
            "INSERT INTO memory_meta (date, user_text, asst_text, source_session) "
            "VALUES (?, ?, ?, ?)",
            (today, user_text, asst_text, source_session),
        )
        meta_id = cur.lastrowid
        
        # 2. 写 memory_fts (FTS5)
        conn.execute(
            "INSERT INTO memory_fts (date, user_text, asst_text) "
            "VALUES (?, ?, ?)",
            (today, user_text, asst_text),
        )
        
        # 3. 写向量到 hnswlib 索引（替换 sqlite-vec0，用 C++ 后端）
        try:
            emb = _get_embedding_client().embed(content_clean)
            if emb and len(emb) == EMBEDDING_DIM:
                idx = _ensure_hnsw_index()
                if idx:
                    item_id = idx.element_count
                    idx.add_items([np.array(emb, dtype=np.float32)], [item_id])
                    _HNSW_ID_MAP[item_id] = meta_id
                    _save_hnsw_index()
        except Exception as e:
            logger.warning(f"Vector storage failed (non-fatal): {e}")
        
        conn.commit()
        return meta_id
        
    except Exception as e:
        logger.error(f"store_to_yaoyao failed: {e}")
        return -1


# ====================================================================
#  读操作 — FTS5 + Vector 混合搜索
# ====================================================================

def _fts5_query(conn: sqlite3.Connection, query: str, limit: int = 10) -> List[Dict]:
    """FTS5 全文检索（支持中文 unicode61 分词）"""
    # FTS5 特殊字符清理
    q = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', query)
    q = ' OR '.join(q.split()[:20])  # 最多 20 个词
    if not q:
        return []
    
    sql = """
        SELECT m.id, m.date, m.user_text, m.asst_text, m.created_at, m.source_session,
               rank
        FROM memory_fts f
        JOIN memory_meta m ON m.rowid = f.rowid
        WHERE memory_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, (q, limit)).fetchall()
        results = []
        for row in rows:
            content = row["user_text"] or row["asst_text"]
            if not content:
                continue
            # rank 是负数，越小越匹配，归一化到 0-1
            rank = row["rank"]
            score = min(1.0, max(0.1, -rank / 15)) if rank < 0 else 0.3
            results.append({
                "id": f"yaoyao_fts_{row['id']}",
                "content": content[:500],
                "source": "yaoyao_fts",
                "score": round(score, 4),
                "metadata": {
                    "date": row["date"],
                    "created_at": row["created_at"],
                    "type": "fts",
                }
            })
        return results
    except Exception as e:
        logger.warning(f"FTS5 search failed: {e}")
        return []


def _vec_query(query: str, limit: int = 10) -> List[Dict]:
    """向量检索 — hnswlib（C++ 后端，替换 sqlite-vec0）"""
    emb = _get_embedding_client().embed(query[:500])
    if not emb or len(emb) != EMBEDDING_DIM:
        return []
    
    try:
        idx = _ensure_hnsw_index()
        if not idx or idx.element_count == 0:
            return []
        
        labels, distances = idx.knn_query([np.array(emb, dtype=np.float32)], k=min(limit, idx.element_count))
        
        results = []
        db = YaoyaoDB()
        conn = db.connect()
        
        for seq_id, dist in zip(labels[0], distances[0]):
            meta_id = _HNSW_ID_MAP.get(int(seq_id))
            if meta_id is None:
                continue
            row = conn.execute(
                "SELECT id, date, user_text, asst_text, created_at FROM memory_meta WHERE id = ?",
                (meta_id,)
            ).fetchone()
            if not row:
                continue
            content_text = row["user_text"] or row["asst_text"]
            if not content_text:
                continue
            score = max(0.1, 1.0 - dist) if dist else 0.5
            results.append({
                "id": f"yaoyao_vec_{meta_id}",
                "content": content_text[:500],
                "source": "yaoyao_vec",
                "score": round(score, 4),
                "metadata": {
                    "date": row["date"],
                    "type": "vector",
                }
            })
        
        return results
        
    except Exception as e:
        logger.warning(f"Vector search failed (hnswlib): {e}")
        return []


def _rrf_fuse(list_a: List[Dict], list_b: List[Dict], k: int = 60) -> List[Dict]:
    """RRF 融合两个检索结果列表"""
    scores = {}
    for rank, item in enumerate(list_a):
        item_id = item.get("id", str(rank))
        scores[item_id] = scores.get(item_id, 0) + 1.0 / (k + rank + 1)
    for rank, item in enumerate(list_b):
        item_id = item.get("id", str(rank))
        scores[item_id] = scores.get(item_id, 0) + 1.0 / (k + rank + 1)
    seen = set()
    fused = []
    for item in list_a + list_b:
        item_id = item.get("id", "")
        if item_id not in seen:
            seen.add(item_id)
            item["score"] = scores.get(item_id, 0)
            fused.append(item)
    fused.sort(key=lambda x: x.get("score", 0), reverse=True)
    return fused


def rerank_results(query: str, candidates: List[Dict], top_k: int = 10) -> List[Dict]:
    """
    用 bge-reranker-v2-m3 对候选结果重排序（公开接口）
    
    与 _rerank 相同，为外部模块提供独立调用入口。
    """
    return _rerank(query, candidates, top_k=top_k)

def _rerank(query: str, candidates: List[Dict], top_k: int = 10) -> List[Dict]:
    """
    用 bge-reranker-v2-m3 对候选结果重排序
    
    无问芯穹免费，按 token 计费。query + documents 一起传，
    reranker 做 cross-encoder 重新打分，比 embedding cosine 准得多。
    
    Args:
        query: 原始查询
        candidates: 候选结果列表（建议传 top_k * 3）
        top_k: 返回结果数
    
    Returns:
        重排序后的结果列表
    """
    if not candidates:
        return []
    
    import requests as httpreq
    
    # 提取要排序的文本（去重，保留 50 个以内）
    seen_texts = set()
    docs = []
    doc_map = []  # index → candidate
    for c in candidates:
        text = c.get("content", "").strip()
        if text and text not in seen_texts:
            seen_texts.add(text)
            docs.append(text)
            doc_map.append(c)
        if len(docs) >= 50:  # token 限制保护
            break
    
    if not docs:
        return candidates[:top_k]
    
    try:
        resp = httpreq.post(
            RERANKER_URL,
            headers={
                "Authorization": f"Bearer {RERANKER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": RERANKER_MODEL,
                "query": query[:500],
                "documents": docs,
            },
            timeout=30,
        )
        
        if resp.status_code != 200:
            logger.warning(f"Reranker API error: {resp.status_code}")
            return candidates[:top_k]
        
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return candidates[:top_k]
        
        # 按 relevance_score 排序
        ranked = sorted(results, key=lambda x: -x["relevance_score"])
        
        reranked = []
        seen_ids = set()
        for r in ranked:
            idx = r["index"]
            if idx < len(doc_map):
                item = doc_map[idx]
                if item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    item["score"] = r["relevance_score"]
                    item["source"] = "yaoyao_reranked"
                    reranked.append(item)
        
        return reranked[:top_k]
        
    except Exception as e:
        logger.warning(f"Reranker call failed (non-fatal): {e}")
        return candidates[:top_k]


def search_yaoyao(query: str, limit: int = 10) -> List[Dict]:
    """
    混合搜索：FTS5 + Vector + RRF 融合 → bge-reranker-v2-m3 重排序
    
    先 FTS5 和 vector 各自召回 → RRF 融合出候选池（limit * 3）
    → reranker 逐个 cross-encoder 打分 → 取 top_k
    
    Args:
        query: 查询文本
        limit: 返回结果数
        
    Returns:
        统一格式的结果列表
    """
    if not query:
        return []
    
    try:
        db = YaoyaoDB()
        conn = db.connect()
        
        # Step 1: FTS5 + Vector 双路召回（候选池取 limit * 3）
        candidate_limit = limit * 3
        fts_results = _fts5_query(conn, query, limit=candidate_limit)
        vec_results = _vec_query(query, limit=candidate_limit)
        
        # Step 2: RRF 融合
        if fts_results and vec_results:
            fused = _rrf_fuse(fts_results, vec_results, k=RRF_K)
            results = fused[:candidate_limit]
        elif vec_results:
            results = vec_results[:candidate_limit]
        else:
            results = fts_results[:candidate_limit]
        
        # Step 3: bge-reranker-v2-m3 重排序
        if results:
            results = _rerank(query, results, top_k=limit)
        
        return results
        
    except Exception as e:
        logger.error(f"search_yaoyao failed: {e}")
        return []


# ====================================================================
#  工具函数
# ====================================================================

def get_yaoyao_db_size() -> int:
    """获取 yaoyao.db 文件大小"""
    try:
        return YAOYAO_DB_PATH.stat().st_size
    except Exception:
        return 0


def get_yaoyao_stats() -> Dict:
    """获取 yaoyao 记忆库统计"""
    try:
        db = YaoyaoDB()
        conn = db.connect()
        meta_count = conn.execute("SELECT COUNT(*) as c FROM memory_meta").fetchone()["c"]
        fts_count = conn.execute("SELECT COUNT(*) as c FROM memory_fts").fetchone()["c"]
        vec_count = conn.execute("SELECT COUNT(*) as c FROM memory_vec").fetchone()["c"]
        return {
            "meta_count": meta_count,
            "fts_count": fts_count,
            "vec_count": vec_count,
            "db_size": get_yaoyao_db_size(),
        }
    except Exception as e:
        return {"error": str(e)}


# ====================================================================
#  CLI 测试
# ====================================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python3 yaoyao_bridge.py store|search|stats [args...]")
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == "store":
        content = sys.argv[2] if len(sys.argv) > 2 else "测试记忆内容"
        source = sys.argv[3] if len(sys.argv) > 3 else "user"
        mid = store_to_yaoyao(content, source)
        print(f"Stored: id={mid}")
    
    elif action == "search":
        query = sys.argv[2] if len(sys.argv) > 2 else "测试"
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        results = search_yaoyao(query, limit=limit)
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    elif action == "stats":
        stats = get_yaoyao_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    
    else:
        print(f"Unknown action: {action}")
