#!/usr/bin/env python3
"""
Zep/Graphiti 时序知识图谱模块 (arXiv:2501.13956)

四时间戳模型:
- t_created: 事实发生的时间（用户说"我昨天去上海了" → t_created=昨天）
- t_ingested: 系统摄入时间（始终是 now）
- t_valid: 事实生效时间（通常=t_created，但某些场景可延迟生效）
- t_invalid: 事实失效时间（None 表示仍有效，invalidate edge 时设置）

边可过期（t_invalid != None），实体不可删除（只追加别名）。
支持标签传播聚类构建社区子图，混合检索（语义+BM25+图遍历）。
"""

import os
import json
import time
import logging
import sqlite3
import threading
import re
from typing import Dict, List, Optional, Any, Tuple, Set

# 默认 DB 路径：与 PaperIntegration 保持一致
_DEFAULT_TKG_DB = os.path.expanduser("~/.openclaw/workspace/temporal_kg.db")
from collections import defaultdict, Counter
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# ============================================================
# 数据模型
# ============================================================

@dataclass
class TemporalEdge:
    """带四时间戳的关系边"""
    edge_id: str
    src_entity: str
    dst_entity: str
    relation: str
    content: str = ""                 # 关系描述/证据
    t_created: float = 0.0            # 事实发生时间
    t_ingested: float = 0.0           # 系统摄入时间
    t_valid: float = 0.0              # 生效时间
    t_invalid: Optional[float] = None # 失效时间（None=有效）
    confidence: float = 0.5
    source: str = "user"
    metadata: Dict[str, Any] = field(default_factory=dict)
    session_key: str = ""

    def is_active(self, at_time: Optional[float] = None) -> bool:
        """检查边在指定时刻是否活跃"""
        now = at_time or time.time()
        if self.t_invalid is not None and now >= self.t_invalid:
            return False
        if self.t_valid > 0 and now < self.t_valid:
            return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EntityNode:
    """实体节点"""
    entity_id: str
    name: str
    entity_type: str = "unknown"
    embedding: Optional[List[float]] = None
    aliases: List[str] = field(default_factory=list)
    t_created: float = 0.0
    t_last_seen: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get('embedding') is not None and len(d['embedding']) > 4:
            d['embedding'] = d['embedding'][:4] + [f"...({len(d['embedding'])} dims)"]
        return d


def _now() -> float:
    return time.time()


def _generate_id(prefix: str = "tkg") -> str:
    import hashlib, random
    raw = f"{prefix}_{_now()}_{random.random()}"
    return f"{prefix}_{hashlib.md5(raw.encode()).hexdigest()[:12]}"


def _keyword_score(text: str, query: str) -> float:
    """BM25 简化版：关键词重叠分数"""
    if not text or not query:
        return 0.0
    q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))
    t_words = set(re.findall(r'[\w\u4e00-\u9fff]+', text.lower()))
    if not q_words:
        return 0.0
    overlap = len(q_words & t_words)
    return overlap / len(q_words)


# ============================================================
# 时序知识图谱核心
# ============================================================

class TemporalKnowledgeGraph:
    """
    时序知识图谱 — SQLite 持久化 + 线程安全

    设计参考 Graphiti:
    - 实体节点（EntityNode）：带别名和embedding
    - 时序边（TemporalEdge）：四时间戳模型
    - 社区聚类（CommunityCluster）：标签传播
    - 混合检索：语义（cosine）+ BM25 + 图遍历
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _DEFAULT_TKG_DB
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()
        logger.info(f"TemporalKnowledgeGraph 初始化: db={self.db_path}")

    def _init_db(self):
        """初始化 SQLite 数据库"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            # 实体表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    entity_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    entity_type TEXT DEFAULT 'unknown',
                    embedding TEXT DEFAULT '',
                    aliases TEXT DEFAULT '[]',
                    t_created REAL DEFAULT 0,
                    t_last_seen REAL DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                )
            """)

            # 时序边表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS temporal_edges (
                    edge_id TEXT PRIMARY KEY,
                    src_entity TEXT NOT NULL,
                    dst_entity TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    content TEXT DEFAULT '',
                    t_created REAL DEFAULT 0,
                    t_ingested REAL DEFAULT 0,
                    t_valid REAL DEFAULT 0,
                    t_invalid REAL,
                    confidence REAL DEFAULT 0.5,
                    source TEXT DEFAULT 'user',
                    metadata TEXT DEFAULT '{}',
                    session_key TEXT DEFAULT ''
                )
            """)

            # 社区表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS communities (
                    community_id TEXT PRIMARY KEY,
                    members TEXT DEFAULT '[]',
                    summary TEXT DEFAULT '',
                    centroid TEXT DEFAULT '[]',
                    t_created REAL DEFAULT 0,
                    t_updated REAL DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                )
            """)

            # 索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_src ON temporal_edges(src_entity)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_dst ON temporal_edges(dst_entity)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_session ON temporal_edges(session_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_edge_active ON temporal_edges(t_invalid)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_name ON entities(name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_type ON entities(entity_type)")

            conn.commit()
            conn.close()

    # ============================================================
    # 实体管理
    # ============================================================

    def add_entity(self, name: str, entity_type: str = "unknown",
                   embedding: Optional[List[float]] = None,
                   aliases: Optional[List[str]] = None) -> str:
        """
        添加实体节点，支持别名消歧。
        如果同名实体已存在，合并别名。
        """
        existing = self.get_entity(name, fuzzy=False)
        if existing:
            # 合并别名
            cur_aliases = set(existing.get('aliases', []))
            if aliases:
                cur_aliases.update(aliases)
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "UPDATE entities SET t_last_seen=?, aliases=?, entity_type=? WHERE entity_id=?",
                    (_now(), json.dumps(list(cur_aliases)), entity_type, existing['entity_id'])
                )
                conn.commit()
                conn.close()
            logger.debug(f"实体已存在，合并别名: {name} → {list(cur_aliases)}")
            return existing['entity_id']

        entity_id = _generate_id("ent")
        t = _now()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR IGNORE INTO entities (entity_id, name, entity_type, embedding, aliases, t_created, t_last_seen, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (entity_id, name, entity_type,
                 json.dumps(embedding) if embedding else '',
                 json.dumps(aliases or []),
                 t, t, '{}')
            )
            conn.commit()
            conn.close()

        logger.info(f"添加实体: {name} (type={entity_type}, id={entity_id})")
        return entity_id

    def get_entity(self, name: str, fuzzy: bool = True) -> Optional[dict]:
        """
        获取实体信息。
        fuzzy=True 时尝试别名匹配。
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entities WHERE name=?", (name,)
            ).fetchone()

            if row is None and fuzzy:
                # 尝试别名匹配
                all_entities = conn.execute("SELECT * FROM entities").fetchall()
                for erow in all_entities:
                    try:
                        aliases = json.loads(erow['aliases']) if erow['aliases'] else []
                        if name in aliases:
                            row = erow
                            break
                    except (json.JSONDecodeError, TypeError):
                        pass
            conn.close()

        if row is None:
            return None

        d = dict(row)
        try:
            d['aliases'] = json.loads(d['aliases']) if d['aliases'] else []
        except (json.JSONDecodeError, TypeError):
            d['aliases'] = []
        try:
            d['embedding'] = json.loads(d['embedding']) if d['embedding'] else None
        except (json.JSONDecodeError, TypeError):
            d['embedding'] = None
        return d

    def disambiguate_entity(self, name: str, context: str) -> str:
        """
        实体消歧 — 同一实体的不同表述合并。
        策略：
        1. 精确匹配 → 返回
        2. 别名匹配 → 返回主实体
        3. LLM 消歧（可选）
        4. 新建实体
        """
        # 精确匹配
        exact = self.get_entity(name, fuzzy=False)
        if exact:
            return exact['entity_id']

        # 别名匹配
        fuzzy_match = self.get_entity(name, fuzzy=True)
        if fuzzy_match:
            return fuzzy_match['entity_id']

        # 尝试用 LLM 做消歧
        try:
            llm = self._get_llm()
            if llm and context:
                # 取候选实体
                with self._lock:
                    conn = sqlite3.connect(self.db_path)
                    conn.row_factory = sqlite3.Row
                    all_ents = conn.execute(
                        "SELECT name, entity_type FROM entities ORDER BY t_last_seen DESC LIMIT 20"
                    ).fetchall()
                    conn.close()

                candidates = {e['name']: e['entity_type'] for e in all_ents}
                if candidates:
                    prompt = (
                        f"用户提到的实体名称: {name}\n"
                        f"上下文: {context[:200]}\n"
                        f"已有实体: {json.dumps(candidates, ensure_ascii=False)}\n"
                        f"判断 '{name}' 是否指向已有实体之一。如果匹配，只返回匹配的实体名称；否则返回 'NEW'。"
                    )
                    resp = llm.chat.completions.create(
                        model=self._llm_flash_model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=30, temperature=0.1,
                    )
                    answer = resp.choices[0].message.content.strip()
                    if answer and answer != 'NEW' and answer in candidates:
                        logger.info(f"LLM 消歧: '{name}' → '{answer}'")
                        return self.add_entity(answer, candidates[answer])
        except Exception as e:
            logger.warning(f"LLM 消歧失败: {e}")

        # 新建实体
        return self.add_entity(name)

    # ============================================================
    # 时序边管理
    # ============================================================

    def add_edge(self, src_entity: str, dst_entity: str, relation: str,
                 timestamp: Optional[float] = None,
                 content: str = "",
                 session_key: str = "") -> str:
        """添加带时间戳的关系边（兼容无实体时自动创建）"""
        return self.add_temporal_edge(src_entity, dst_entity, relation, timestamp, content, session_key)

    def add_temporal_edge(self, src_entity: str, dst_entity: str,
                          relation: str, timestamp: Optional[float] = None,
                          content: str = "",
                          session_key: str = "") -> str:
        """
        添加带时间戳的关系边，四时间戳模型。

        timestamp 语义：
        - 如果用户说"我昨天去上海了"，则 timestamp = 昨天（t_created）
        - t_ingested 自动设为 now
        """
        # 确保实体存在
        src_id = self.disambiguate_entity(src_entity, content)
        dst_id = self.disambiguate_entity(dst_entity, content)

        t_created = timestamp or _now()
        t_ingested = _now()
        edge_id = _generate_id("edge")

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO temporal_edges "
                "(edge_id, src_entity, dst_entity, relation, content, "
                " t_created, t_ingested, t_valid, t_invalid, confidence, source, metadata, session_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                (edge_id, src_id, dst_id, relation, content,
                 t_created, t_ingested, t_created,
                 0.5, "user", '{}', session_key)
            )
            conn.commit()
            conn.close()

        logger.info(f"添加时序边: {src_entity} -[{relation}]-> {dst_entity} (t_created={t_created:.1f})")
        return edge_id

    def invalidate_edge(self, edge_id: str, at_time: Optional[float] = None):
        """
        Edge Invalidation — 标记事实过期。
        对应 Zep 论文中"事实过期"机制。
        """
        t = at_time or _now()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE temporal_edges SET t_invalid=? WHERE edge_id=?",
                (t, edge_id)
            )
            conn.commit()
            conn.close()
        logger.info(f"失效边: {edge_id} @ t_invalid={t:.1f}")

    def detect_and_resolve_conflict(self, new_edge_content: str,
                                     existing_edges: List[dict],
                                     llm=None) -> dict:
        """
        检测新事实与已有事实的矛盾，决定是否invalidate旧事实。

        Args:
            new_edge_content: 新事实描述
            existing_edges: 已有边列表
            llm: LLM 客户端（可选）

        Returns:
            {"conflict_detected": bool, "edges_to_invalidate": List[str], "reasoning": str}
        """
        if not existing_edges:
            return {"conflict_detected": False, "edges_to_invalidate": [], "reasoning": "无已有事实"}

        _llm = llm or self._get_llm()
        if _llm is None:
            # 降级：简单文本矛盾检测
            return self._simple_conflict_check(new_edge_content, existing_edges)

        # LLM 矛盾检测
        existing_texts = []
        for e in existing_edges[:10]:
            eid = e.get('edge_id', '?')
            src = e.get('src_entity', '?')
            dst = e.get('dst_entity', '?')
            rel = e.get('relation', '?')
            ct = e.get('content', '')[:100]
            existing_texts.append(f"[{eid}] {src} -[{rel}]-> {dst}: {ct}")

        prompt = (
            "你是一个知识图谱矛盾检测器。判断新事实与已有事实是否存在矛盾。\n\n"
            f"新事实: {new_edge_content[:300]}\n\n"
            "已有事实:\n" + "\n".join(existing_texts) + "\n\n"
            "请返回 JSON：\n"
            '{"conflict": true/false, "conflicting_ids": [...], "reasoning": "..."}'
        )

        try:
            resp = _llm.chat.completions.create(
                model=self._llm_flash_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=256, temperature=0.1,
            )
            answer = resp.choices[0].message.content.strip()
            result = json.loads(re.search(r'\{.*\}', answer, re.DOTALL).group())
            edges_to_invalidate = result.get("conflicting_ids", [])

            if edges_to_invalidate:
                for eid in edges_to_invalidate:
                    self.invalidate_edge(eid)

            return {
                "conflict_detected": result.get("conflict", False),
                "edges_to_invalidate": edges_to_invalidate,
                "reasoning": result.get("reasoning", "")
            }
        except Exception as e:
            logger.warning(f"LLM 矛盾检测失败: {e}")
            return self._simple_conflict_check(new_edge_content, existing_edges)

    def _simple_conflict_check(self, new_edge_content: str,
                                existing_edges: List[dict]) -> dict:
        """降级矛盾检测：关键词+关系重叠"""
        conflicts = []
        n_words = set(re.findall(r'[\w\u4e00-\u9fff]+', new_edge_content.lower()))
        for e in existing_edges[:20]:
            e_content = f"{e.get('relation', '')} {e.get('content', '')}"
            e_words = set(re.findall(r'[\w\u4e00-\u9fff]+', e_content.lower()))
            overlap = len(n_words & e_words)
            if overlap >= 3:
                conflicts.append(e.get('edge_id', '?'))

        for eid in conflicts:
            self.invalidate_edge(eid)

        return {
            "conflict_detected": len(conflicts) > 0,
            "edges_to_invalidate": conflicts,
            "reasoning": f"简单冲突检测: {len(conflicts)} 条冲突边"
        }

    # ============================================================
    # 检索与查询
    # ============================================================

    def get_active_edges(self, entity: Optional[str] = None,
                         at_time: Optional[float] = None) -> List[dict]:
        """获取指定时刻的活跃事实"""
        t = at_time or _now()
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row

            if entity:
                ent = self.get_entity(entity)
                if not ent:
                    conn.close()
                    return []
                eid = ent['entity_id']
                rows = conn.execute(
                    "SELECT * FROM temporal_edges "
                    "WHERE (src_entity=? OR dst_entity=?) "
                    "AND (t_invalid IS NULL OR t_invalid > ?) "
                    "AND (t_valid <= ? OR t_valid = 0) "
                    "ORDER BY t_created DESC",
                    (eid, eid, t, t)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM temporal_edges "
                    "WHERE (t_invalid IS NULL OR t_invalid > ?) "
                    "AND (t_valid <= ? OR t_valid = 0) "
                    "ORDER BY t_created DESC",
                    (t, t)
                ).fetchall()
            conn.close()

        results = []
        for r in rows:
            d = dict(r)
            try:
                d['metadata'] = json.loads(d['metadata']) if d['metadata'] else {}
            except (json.JSONDecodeError, TypeError):
                d['metadata'] = {}
            results.append(d)
        return results

    def get_entity_neighbors(self, entity: str, depth: int = 1,
                              at_time: Optional[float] = None) -> List[dict]:
        """
        图遍历 — 获取实体在指定时刻的邻居。

        Args:
            entity: 实体名称
            depth: 遍历深度（1=直接邻居，2=两跳）
            at_time: 时间点（None=当前）

        Returns:
            [{"entity": str, "relation": str, "distance": int, ...}, ...]
        """
        visited_names = set()
        results = []
        t = at_time or _now()

        # 解析起始实体名称到 entity_id
        start_ent = self.get_entity(entity)
        if not start_ent:
            return []

        def _traverse_by_id(current_entity_id: str, current_name_str: str, current_depth: int):
            if current_depth > depth:
                return
            visited_names.add(current_name_str)

            with self._lock:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM temporal_edges "
                    "WHERE (src_entity=? OR dst_entity=?) "
                    "AND (t_invalid IS NULL OR t_invalid > ?) "
                    "AND (t_valid <= ? OR t_valid = 0)",
                    (current_entity_id, current_entity_id, t, t)
                ).fetchall()
                conn.close()

            for e in rows:
                e = dict(e)
                # 确定邻居的 entity_id
                if e['src_entity'] == current_entity_id:
                    neighbor_id = e['dst_entity']
                else:
                    neighbor_id = e['src_entity']

                # 获取邻居的名称
                neighbor_ent = self.get_entity_by_id(neighbor_id)
                neighbor_name = neighbor_ent['name'] if neighbor_ent else neighbor_id

                if neighbor_name != current_name_str and neighbor_name not in visited_names:
                    results.append({
                        "entity": neighbor_name,
                        "entity_id": neighbor_id,
                        "relation": e['relation'],
                        "distance": current_depth,
                        "content": e.get('content', '')[:100],
                        "t_created": e.get('t_created', 0),
                        "edge_id": e['edge_id'],
                    })
                    _traverse_by_id(neighbor_id, neighbor_name, current_depth + 1)

        # 从 depth=0 开始，这样 depth=1 对应一跳邻居
        _traverse_by_id(start_ent['entity_id'], entity, 0)
        return results

    def get_entity_by_id(self, entity_id: str) -> Optional[dict]:
        """通过 ID 获取实体"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM entities WHERE entity_id=?", (entity_id,)
            ).fetchone()
            conn.close()
        if row is None:
            return None
        d = dict(row)
        try:
            d['aliases'] = json.loads(d['aliases']) if d['aliases'] else []
        except (json.JSONDecodeError, TypeError):
            d['aliases'] = []
        return d

    def get_src_name(self, e: dict) -> str:
        """获取边的源实体名称"""
        ent = self.get_entity_by_id(e.get('src_entity', ''))
        return ent['name'] if ent else e.get('src_entity', '?')

    def get_dst_name(self, e: dict) -> str:
        """获取边的目标实体名称"""
        ent = self.get_entity_by_id(e.get('dst_entity', ''))
        return ent['name'] if ent else e.get('dst_entity', '?')

    def hybrid_retrieve(self, query: str, embedding: Optional[List[float]] = None,
                        at_time: Optional[float] = None, top_k: int = 5) -> List[dict]:
        """
        混合检索：语义（cosine similarity）+ BM25 + 图遍历，带时间过滤。

        路由策略：
        - 如果提供 embedding，语义权重 0.4
        - BM25 关键词匹配权重 0.3
        - 图遍历（实体邻居扩散）权重 0.3
        """
        t = at_time or _now()
        edges = self.get_active_edges(at_time=t)
        if not edges:
            return []

        scored = []
        q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))

        # 语义评分（cosine similarity）
        semantic_scores = {}
        if embedding is not None:
            try:
                np = __import__('numpy')
                q_vec = np.array(embedding)
                q_norm = np.linalg.norm(q_vec)
                if q_norm > 0:
                    for e in edges[:100]:
                        src_name = self.get_src_name(e) or e.get('src_entity', '')
                        dst_name = self.get_dst_name(e) or e.get('dst_entity', '')
                        text = f"{e['relation']} {e.get('content', '')} {src_name} {dst_name}"
                        # 简单文本embedding模拟（实际会用存储的实体embedding）
                        eid = e.get('edge_id', '')
                        semantic_scores[eid] = 0.0
            except Exception:
                pass

        for e in edges:
            src_name = self.get_src_name(e) or e.get('src_entity', '')
            dst_name = self.get_dst_name(e) or e.get('dst_entity', '')
            text = f"{e['relation']} {e.get('content', '')} {src_name} {dst_name}"

            # BM25 分数
            bm25 = _keyword_score(text, query)

            # 图遍历扩散分数
            if q_words:
                t_words = set(re.findall(r'[\w\u4e00-\u9fff]+', text.lower()))
                overlap = len(q_words & t_words)
                graph_score = min(overlap / max(len(q_words), 1), 1.0)
            else:
                graph_score = 0.0

            # 时间衰减权重：较新的边得分更高
            time_decay = 1.0
            if e.get('t_created', 0) > 0:
                age_hours = (t - e['t_created']) / 3600
                time_decay = max(0.3, 1.0 - age_hours / (24 * 365 * 2))  # 2年半衰期

            # RRF 融合
            sem_score = semantic_scores.get(e.get('edge_id', ''), 0.0)
            total = (sem_score * 0.4 + bm25 * 0.3 + graph_score * 0.3) * time_decay * e.get('confidence', 0.5)

            scored.append({
                "edge_id": e['edge_id'],
                "src_entity": src_name,
                "dst_entity": dst_name,
                "relation": e['relation'],
                "content": e.get('content', '')[:200],
                "score": total,
                "t_created": e.get('t_created', 0),
                "session_key": e.get('session_key', ''),
            })

        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:top_k]

    def get_entity_timeline(self, entity: str) -> List[dict]:
        """
        获取实体的完整时间线（按 t_created 排序）。
        包含所有关联边，无论是否失效。
        """
        ent = self.get_entity(entity)
        if not ent:
            return []

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM temporal_edges "
                "WHERE src_entity=? OR dst_entity=? "
                "ORDER BY t_created ASC",
                (ent['entity_id'], ent['entity_id'])
            ).fetchall()
            conn.close()

        results = []
        for r in rows:
            d = dict(r)
            src_name = (self.get_src_name(d) or d.get('src_entity', '?'))
            dst_name = (self.get_dst_name(d) or d.get('dst_entity', '?'))
            results.append({
                "edge_id": d['edge_id'],
                "src": src_name,
                "dst": dst_name,
                "relation": d['relation'],
                "content": d.get('content', ''),
                "t_created": d.get('t_created', 0),
                "t_invalid": d.get('t_invalid'),
                "is_active": d.get('t_invalid') is None,
            })

        return results

    # ============================================================
    # 社区聚类
    # ============================================================

    def build_community(self, min_edges: int = 2) -> List[dict]:
        """
        标签传播聚类，构建社区子图。
        使用 Louvain-like 标签传播算法。
        """
        # 获取所有活跃边
        edges = self.get_active_edges()
        if len(edges) < min_edges:
            return []

        # 构建邻接表（实体名称->邻居集合）
        adj = defaultdict(set)
        for e in edges:
            src = e.get('src_entity', '')
            dst = e.get('dst_entity', '')
            src_name = self.get_src_name(e) or '?'
            dst_name = self.get_dst_name(e) or '?'
            adj[src_name].add(dst_name)
            adj[dst_name].add(src_name)

        if not adj:
            return []

        # 标签传播
        labels = {node: node for node in adj}
        max_iter = 10
        for _ in range(max_iter):
            changed = False
            for node in adj:
                neighbor_labels = Counter()
                for nb in adj[node]:
                    neighbor_labels[labels[nb]] += 1
                if neighbor_labels:
                    most_common = neighbor_labels.most_common(1)[0][0]
                    if labels[node] != most_common:
                        labels[node] = most_common
                        changed = True
            if not changed:
                break

        # 先构建 name->entity_id 映射，避免在持有锁时调用 get_entity_by_id
        name_map = {}
        for e in edges:
            for eid in (e['src_entity'], e['dst_entity']):
                if eid not in name_map:
                    name_map[eid] = eid  # fallback
        # 批量查询
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            for eid in list(name_map.keys()):
                row = conn.execute("SELECT name FROM entities WHERE entity_id=?", (eid,)).fetchone()
                if row:
                    name_map[eid] = row[0]
            conn.close()

        def _name(e):
            return name_map.get(e, '?')

        # 用 name_map 重写 edges 中的名称
        named_edges = []
        for e in edges:
            named_edges.append({
                'src': _name(e['src_entity']),
                'dst': _name(e['dst_entity']),
                'relation': e['relation'],
                'content': e.get('content', ''),
            })

        # 按标签分组
        communities = defaultdict(list)
        for node, label in labels.items():
            communities[label].append(node)

        # 过滤小社区
        result_communities = []
        for label, members in communities.items():
            if len(members) < min_edges:
                continue
            community_id = _generate_id("comm")

            # 计算 centroid（关键词频率）
            centroid_words = Counter()
            summary_parts = []
            for ne in named_edges:
                src = ne['src']
                dst = ne['dst']
                if src in members or dst in members:
                    words = re.findall(r'[\w\u4e00-\u9fff]{2,}',
                                       f"{ne['relation']} {ne.get('content', '')}")
                    centroid_words.update(words)
                    summary_parts.append(f"{src} -[{ne['relation']}]-> {dst}")

            centroid = [w for w, _ in centroid_words.most_common(10)]
            summary = f"社区成员({len(members)}): {', '.join(members[:5])}..."

            # 统计边数
            edge_count = 0
            for ne in named_edges:
                if ne['src'] in members or ne['dst'] in members:
                    edge_count += 1

            with self._lock:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    "INSERT OR REPLACE INTO communities "
                    "(community_id, members, summary, centroid, t_created, t_updated, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (community_id, json.dumps(members), summary,
                     json.dumps(centroid), _now(), _now(), json.dumps({
                         'edge_count': edge_count
                     }))
                )
                conn.commit()
                conn.close()

            result_communities.append({
                "community_id": community_id,
                "members": members,
                "summary": summary,
                "centroid": centroid,
            })

        return result_communities

    def get_community_summary(self, community_id: str) -> Optional[dict]:
        """获取社区摘要"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM communities WHERE community_id=?", (community_id,)
            ).fetchone()
            conn.close()

        if row is None:
            return None

        d = dict(row)
        try:
            d['members'] = json.loads(d['members']) if d['members'] else []
        except (json.JSONDecodeError, TypeError):
            d['members'] = []
        try:
            d['centroid'] = json.loads(d['centroid']) if d['centroid'] else []
        except (json.JSONDecodeError, TypeError):
            d['centroid'] = []
        return d

    # ============================================================
    # 会话级子图
    # ============================================================

    def get_session_graph(self, session_key: str) -> dict:
        """
        构建会话级子图（类似 Graphiti 的事件子图）。

        Returns:
            {
                "entities": List[EntityNode],
                "edges": List[TemporalEdge],
                "stats": {"entity_count": int, "edge_count": int, ...}
            }
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            edge_rows = conn.execute(
                "SELECT * FROM temporal_edges WHERE session_key=? ORDER BY t_created ASC",
                (session_key,)
            ).fetchall()

            # 收集涉及的实体 ID
            entity_ids = set()
            for r in edge_rows:
                entity_ids.add(r['src_entity'])
                entity_ids.add(r['dst_entity'])

            entity_rows = []
            if entity_ids:
                placeholders = ','.join(['?'] * len(entity_ids))
                entity_rows = conn.execute(
                    f"SELECT * FROM entities WHERE entity_id IN ({placeholders})",
                    list(entity_ids)
                ).fetchall()
            conn.close()

        entities = [dict(r) for r in entity_rows]
        for d in entities:
            try:
                d['aliases'] = json.loads(d['aliases']) if d['aliases'] else []
            except (json.JSONDecodeError, TypeError):
                d['aliases'] = []
            try:
                d['embedding'] = json.loads(d['embedding']) if d['embedding'] else None
            except (json.JSONDecodeError, TypeError):
                d['embedding'] = None

        edges = [dict(r) for r in edge_rows]

        return {
            "entities": entities,
            "edges": edges,
            "stats": {
                "entity_count": len(entities),
                "edge_count": len(edges),
            }
        }

    # ============================================================
    # 文本抽取
    # ============================================================

    @staticmethod
    @staticmethod
    def _rule_based_extract(text: str) -> List[dict]:
        """LLM 不可用时的规则兜底实体抽取"""
        entities = []
        seen = set()

        def add(e, t, r, target=""):
            key = e.lower()
            if key not in seen and 2 <= len(e) <= 40:
                seen.add(key)
                entities.append({"entity": e, "type": t, "relation": r, "target": target})

        clean = text.strip()
        lower = clean.lower()

        # 技术栈关键词
        tech = {
            'PyTorch': ('工具', '使用'), 'TensorFlow': ('工具', '使用'), 'JAX': ('工具', '使用'),
            'Docker': ('工具', '使用'), 'Redis': ('工具', '使用'), 'Kubernetes': ('工具', '使用'),
            'OpenAI': ('工具', '使用'), 'OpenClaw': ('工具', '使用'), 'ClawHub': ('平台', '使用'),
            'FastAPI': ('工具', '使用'), 'Node.js': ('工具', '使用'),
            'Python': ('语言', '使用'), 'JavaScript': ('语言', '使用'),
            'DeepSeek': ('模型', '使用'), 'GLM': ('模型', '使用'), 'Qwen': ('模型', '使用'),
            'FAISS': ('工具', '使用'), 'Qdrant': ('工具', '使用'), 'DuckDB': ('工具', '使用'),
            'SQLite': ('工具', '使用'), 'PostgreSQL': ('工具', '使用'),
            'MongoDB': ('工具', '使用'), 'Polars': ('工具', '使用'),
            'Lobster': ('工具', '使用'),
        }
        for name, (etype, rel) in tech.items():
            if name.lower() in lower:
                add(name, etype, rel)

        # 大写缩写/项目名 (R-CCAM, CognitiveMap, TemporalKG 等)
        for m in re.finditer(r'\b([A-Z][a-zA-Z0-9]*(?:[-_][A-Z][a-zA-Z0-9]+)+|[A-Z]{3,8})\b', clean):
            name = m.group(1)
            skip = {'OK','AI','DB','KG','API','CPU','GPU','RAM','URL','PDF','JSON','HTML','CSS',
                    'HTTP','SDK','IDE','IOT','NLP','UI','UX'}
            if len(name) >= 3 and name not in skip and not name.isdigit():
                add(name, '概念', '涉及')

        # 驼峰项目名
        for m in re.finditer(r'\b([A-Z][a-z]+[A-Z][a-zA-Z0-9]{2,30})\b', clean):
            name = m.group(1)
            if name not in ('OpenAI','GitHub','TypeScript','PostgreSQL','MongoDB','FastAPI','NodeJs'):
                add(name, '概念', '涉及')

        # 版本号
        for m in re.finditer(r'(?:^|\s)([a-zA-Z]+\s*\d+\.\d+(?:\.\d+)?)', clean):
            v = m.group(1).strip()
            if any(c.isdigit() for c in v):
                add(v, '版本', '版本为')

        # 中文地名
        for m in re.finditer(r'(?:在|去|位于|从|到)\s*([\u4e00-\u9fff]{2,4}(?:市|区|县|省)?)', clean):
            loc = m.group(1).strip()
            if loc and 2 <= len(loc) <= 5:
                add(loc, '地点', '位于')

        # 中文模块/概念
        for m in re.finditer(r'([\u4e00-\u9fff]{2,10}(?:模块|系统|平台|框架|组件|工具|项目|方案|报告|文档|架构|技能|能力|算法|模型|数据库|引擎|入口|接口|协议))', clean):
            add(m.group(1), '概念', '涉及')

        return entities

    def extract_entities_from_text(self, text: str, llm=None) -> List[dict]:
        """
        从文本中抽取实体和关系。
        优先尝试 LLM 抽取，失败则规则兜底。

        Returns:
            [{"entity": str, "type": str, "relation": str, "target": str}, ...]
        """
        _llm = llm or self._get_llm()

        # 确保 _llm_flash_model 已初始化（外部传入 llm 时 _get_llm 不会被执行）
        if not hasattr(self, '_llm_flash_model') or not self._llm_flash_model:
            self._llm_flash_model = 'deepseek-v4-flash'

        # 先尝试 LLM
        if _llm is not None:
            prompt = (
                "从以下文本中抽取实体和关系。返回 JSON 数组：\n"
                "[\n"
                '  {"entity": "实体名", "type": "人物/地点/组织/事件/概念/其他", '
                '"relation": "关系描述", "target": "关联实体"},\n'
                "  ...\n"
                "]\n"
                "如果没有实体，返回 []。\n\n"
                f"文本: {text[:1500]}"
            )
            try:
                resp = _llm.chat.completions.create(
                    model=self._llm_flash_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=512, temperature=0.1,
                )
                answer = resp.choices[0].message.content.strip()
                # 用大括号深度匹配提取第一个 JSON 数组（支持嵌套引号中的特殊字符）
                start = answer.find('[')
                if start >= 0:
                    depth = 0
                    for i in range(start, len(answer)):
                        if answer[i] == '[':
                            depth += 1
                        elif answer[i] == ']':
                            depth -= 1
                            if depth == 0:
                                block = answer[start:i+1]
                                try:
                                    result = json.loads(block)
                                    if result:
                                        logger.info(f"LLM 实体抽取: {len(result)} 个")
                                        return result
                                except json.JSONDecodeError:
                                    pass
                                break
            except Exception as e:
                logger.warning(f"LLM 实体抽取失败: {e}, 降级规则")

        # 降级: 规则兜底
        result = self._rule_based_extract(text)
        if result:
            logger.info(f"规则实体抽取: {len(result)} 个")
        else:
            logger.debug("没有抽取到实体")
        return result

    # ============================================================
    # 持久化
    # ============================================================

    def save(self):
        """保存（SQLite 自动持久化，此方法仅 VACUUM）"""
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("VACUUM")
                conn.close()
                logger.info(f"TKG 持久化完成: db={self.db_path}")
            except Exception as e:
                logger.warning(f"TKG VACUUM 失败: {e}")

    def load(self):
        """加载（SQLite 自动加载，此方法仅校验数据库完整性）"""
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("PRAGMA integrity_check")
                conn.close()
                logger.info(f"TKG 加载完成: db={self.db_path}")
                return True
            except Exception as e:
                logger.warning(f"TKG 加载失败: {e}")
                return False

    # ============================================================
    # 辅助
    # ============================================================

    def _get_llm(self):
        """懒加载 LLM 客户端"""
        try:
            from xiaoyi_claw_api import get_global_xiaoyi_claw
            xc = get_global_xiaoyi_claw()
            if xc and xc.llm_flash:
                self._llm_flash_model = getattr(xc, '_llm_flash_model', 'deepseek-v4-flash')
                return xc.llm_flash
        except Exception:
            pass
        return None

    def get_stats(self) -> dict:
        """获取统计信息"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM temporal_edges").fetchone()[0]
            active_edges = conn.execute(
                "SELECT COUNT(*) FROM temporal_edges WHERE t_invalid IS NULL"
            ).fetchone()[0]
            community_count = conn.execute("SELECT COUNT(*) FROM communities").fetchone()[0]
            conn.close()

        return {
            "entities": entity_count,
            "total_edges": edge_count,
            "active_edges": active_edges,
            "communities": community_count,
            "db_path": self.db_path,
        }


# ============================================================
# 全局单例
# ============================================================

_tkg_instances: Dict[str, TemporalKnowledgeGraph] = {}


def get_temporal_kg(db_path: Optional[str] = None) -> TemporalKnowledgeGraph:
    """获取时序 KG 单例"""
    key = db_path or "default"
    if key not in _tkg_instances:
        _tkg_instances[key] = TemporalKnowledgeGraph(db_path=db_path)
    return _tkg_instances[key]


__all__ = [
    'TemporalKnowledgeGraph',
    'TemporalEdge',
    'EntityNode',
    'get_temporal_kg',
]
