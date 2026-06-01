#!/usr/bin/env python3
"""
DAG (有向无环图) 上下文管理器

基于 LCM 论文思想的上下文管理方案：
1. 每条消息作为独立节点，保留依赖关系（DAG）
2. 当上下文超 threshold 时，增量压缩旧消息为摘要节点
3. 原始消息永久保留在 SQLite，支持回溯还原
4. 投机解码生成摘要，降成本提速度

论文参考:
- Lossless Context Management (LCM) - Voltropy
- REST: Retrieval-Based Speculative Decoding (1.62-2.36x)
- LLMLingua: Compressing Prompts for Accelerated Inference

Layer: L9 (会话管理层)

Adapted for claw-core: removed numpy dependency, standalone SQLite-based DAG.
"""

import os
import json
import time
import logging
import sqlite3
import hashlib
import threading
import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict, Counter
from pathlib import Path

import faiss
import numpy as np
from numpy.linalg import norm

logger = logging.getLogger(__name__)

# ============================================================================
# 数据库路径
# ============================================================================
DAG_DB_DIR = Path(os.path.expanduser("~/.openclaw"))
DAG_DB_PATH = DAG_DB_DIR / "dag_context.db"
DAG_FAISS_PATH = DAG_DB_DIR / "dag_faiss.idx"
DAG_FAISS_ID_PATH = DAG_DB_DIR / "dag_faiss_ids.json"  # node_id → idx mapping

# FAISS 向量索引（单例，线程安全通过 _lock 保证）
_FAISS_INDEX = None
_FAISS_NODE_IDS = []  # index[int] -> str(node_id)


# ============================================================================
# 节点类型与数据模型
# ============================================================================

class DAGNodeType:
    """节点类型"""
    MESSAGE = "message"          # 原始消息
    SUMMARY = "summary"          # 摘要节点
    PERSONA = "persona"          # 人格注入（永不压缩）
    SNAPSHOT = "snapshot"        # 会话快照
    SYSTEM = "system"            # 系统消息


class PhaseNodeType:
    """R-CCAM 阶段节点类型"""
    USER_INPUT = "rccam_user_input"
    RETRIEVAL = "rccam_retrieval"
    COGNITION = "rccam_cognition"
    CONTROL = "rccam_control"
    ACTION = "rccam_action"
    MEMORY = "rccam_memory"
    CYCLE_SUMMARY = "rccam_cycle_summary"


class PriorityLevel:
    """优先级"""
    CRITICAL = 0     # 永不压缩、永不裁减（人格）
    HIGH = 1         # 最后被裁（重要决策、会话快照）
    NORMAL = 2       # 普通消息，可被摘要
    LOW = 3          # 优先被摘要（系统日志等）


class CognitionForestType:
    """Cognition Forest 子树类型"""
    USER = "user"     # 用户画像：人格文件、偏好、记忆快照
    SELF = "self"     # 系统能力：可用技能、模块状态、配置
    ENV  = "env"      # 运行环境：时间、位置、设备、网络
    META = "meta"     # 元认知：自进化建议、反思记录

    ALL_TYPES = [USER, SELF, ENV, META]

_COG_SUBTREE_SESSION_PREFIX = "_cog_subtree_"


@dataclass
class DAGNode:
    """DAG 节点"""
    node_id: str                     # 唯一 ID
    node_type: str                   # 节点类型
    session_key: str                 # 所属会话
    content: str                     # 节点内容
    tokens: int = 0                  # token 数
    priority: int = PriorityLevel.NORMAL
    parent_ids: List[str] = field(default_factory=list)     # 父节点 ID
    children_ids: List[str] = field(default_factory=list)   # 子节点 ID
    is_summary: bool = False         # 是否为摘要节点
    summary_of_ids: List[str] = field(default_factory=list) # 摘要覆盖的节点 ID
    importance_score: float = 0.5    # 重要性 [0, 1]
    emotion_score: float = 0.0       # 情感分数 [-1, 1]
    keywords: List[str] = field(default_factory=list)       # 关键词
    entities: List[str] = field(default_factory=list)       # 实体
    timestamp: float = 0.0           # 时间戳
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DAGNode":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================================
# DAG 上下文管理器核心
# ============================================================================

class DAGContextManager:
    """
    DAG 上下文管理器

    功能:
    1. 消息节点存储（SQLite）
    2. 增量摘要触发与生成
    3. 上下文组装（原始消息 + 摘要）
    4. 回溯检索（摘要 → 原始消息）
    5. 人格节点保护（永不压缩）
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_context_tokens: int = 240000,
        fresh_tail_count: int = 20,
        leaf_chunk_tokens: int = 8000,
        summary_target_tokens: int = 500,
        context_threshold: float = 0.75,
    ):
        self.db_path = db_path or str(DAG_DB_PATH)
        self.max_context_tokens = max_context_tokens
        self.fresh_tail_count = fresh_tail_count
        self.leaf_chunk_tokens = leaf_chunk_tokens
        self.summary_target_tokens = summary_target_tokens
        self.context_threshold = context_threshold

        # SQLite 锁（线程安全）
        self._lock = threading.Lock()

        # 确保数据库目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # 初始化数据库
        self._init_db()

        logger.info(f"DAG Context Manager 初始化: db={self.db_path}, "
                    f"max_tokens={max_context_tokens}, threshold={context_threshold}")

    def _init_db(self):
        """初始化 SQLite 数据库"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS dag_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tokens INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 2,
                    parent_ids TEXT DEFAULT '[]',
                    children_ids TEXT DEFAULT '[]',
                    is_summary INTEGER DEFAULT 0,
                    summary_of_ids TEXT DEFAULT '[]',
                    importance_score REAL DEFAULT 0.5,
                    emotion_score REAL DEFAULT 0.0,
                    keywords TEXT DEFAULT '[]',
                    entities TEXT DEFAULT '[]',
                    timestamp REAL DEFAULT 0.0,
                    metadata TEXT DEFAULT '{}'
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS rccam_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tokens INTEGER DEFAULT 0,
                    parent_ids TEXT DEFAULT '[]',
                    cycle_id TEXT DEFAULT '',
                    previous_cycle_id TEXT DEFAULT '',
                    phase_name TEXT DEFAULT '',
                    cycle_index INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 2,
                    is_summary INTEGER DEFAULT 0,
                    is_compressed INTEGER DEFAULT 0,
                    importance_score REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 0.5,
                    validation TEXT DEFAULT 'unknown',
                    keywords TEXT DEFAULT '[]',
                    strategy TEXT DEFAULT '',
                    timestamp REAL DEFAULT 0.0,
                    metadata TEXT DEFAULT '{}'
                )
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_key ON dag_nodes(session_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_priority ON dag_nodes(priority)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_node_type ON dag_nodes(node_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON dag_nodes(timestamp)")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_cycle ON rccam_nodes(session_key, cycle_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_phase ON rccam_nodes(session_key, phase_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cycle_index ON rccam_nodes(session_key, cycle_index)")

            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS dag_fts
                    USING fts5(content, keywords, entities, content=dag_nodes, content_rowid=rowid)
                """)
            except Exception:
                logger.warning("FTS5 不可用，跳过全文搜索索引")

            conn.commit()
            conn.close()

    def add_node(self, node: DAGNode) -> bool:
        """添加一个节点到 DAG"""
        data = node.to_dict()

        for field_name in ['parent_ids', 'children_ids', 'summary_of_ids', 'keywords', 'entities']:
            if isinstance(data.get(field_name), list):
                data[field_name] = json.dumps(data[field_name])
        if isinstance(data.get('metadata'), dict):
            data['metadata'] = json.dumps(data['metadata'])

        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("""
                    INSERT OR REPLACE INTO dag_nodes
                    (node_id, node_type, session_key, content, tokens, priority,
                     parent_ids, children_ids, is_summary, summary_of_ids,
                     importance_score, emotion_score, keywords, entities,
                     timestamp, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data['node_id'], data['node_type'], data['session_key'],
                    data['content'], data['tokens'], data['priority'],
                    data['parent_ids'], data['children_ids'],
                    1 if data['is_summary'] else 0,
                    data['summary_of_ids'],
                    data['importance_score'], data['emotion_score'],
                    data['keywords'], data['entities'],
                    data['timestamp'] or time.time(),
                    data['metadata']
                ))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                logger.error(f"添加节点失败: {e}")
                return False

    def add_message(
        self,
        session_key: str,
        role: str,
        content: str,
        tokens: int = 0,
        importance: float = 0.5,
        emotion: float = 0.0,
        priority: int = PriorityLevel.NORMAL,
        keywords: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        parent_ids: Optional[List[str]] = None,
    ) -> str:
        """添加一条消息节点，返回节点 ID"""
        node_id = f"{session_key}_{role}_{int(time.time()*1000)}_{hashlib.md5(content.encode()[:32]).hexdigest()[:8]}"

        node = DAGNode(
            node_id=node_id,
            node_type=DAGNodeType.MESSAGE,
            session_key=session_key,
            content=content,
            tokens=tokens or len(content) // 4,
            priority=priority,
            parent_ids=parent_ids or [],
            importance_score=importance,
            emotion_score=emotion,
            keywords=keywords or [],
            entities=entities or [],
            timestamp=time.time(),
            metadata={"role": role}
        )

        self.add_node(node)
        return node_id

    def add_persona_node(
        self,
        session_key: str,
        content: str,
        tokens: int = 0,
        source: str = "claw-bootstrap_hook",
    ) -> str:
        """添加人格节点（priority: CRITICAL，永不压缩）"""
        node_id = f"persona_{session_key}_{int(time.time())}"

        node = DAGNode(
            node_id=node_id,
            node_type=DAGNodeType.PERSONA,
            session_key=session_key,
            content=content,
            tokens=tokens or len(content) // 4,
            priority=PriorityLevel.CRITICAL,
            timestamp=time.time(),
            metadata={"source": source, "persist_across_sessions": True}
        )

        self.add_node(node)
        return node_id

    def get_session_nodes(
        self,
        session_key: str,
        priority_max: Optional[int] = None,
        node_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[DAGNode]:
        """获取会话的所有节点"""
        conditions = ["session_key = ?"]
        params = [session_key]

        if priority_max is not None:
            conditions.append("priority <= ?")
            params.append(priority_max)

        if node_type:
            conditions.append("node_type = ?")
            params.append(node_type)

        query = f"SELECT * FROM dag_nodes WHERE {' AND '.join(conditions)} ORDER BY timestamp DESC"
        if limit:
            query += f" LIMIT {limit}"

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

        return [self._row_to_node(dict(row)) for row in rows]

    def _row_to_node(self, row: dict) -> DAGNode:
        """SQLite 行转 DAGNode"""
        for field_name in ['parent_ids', 'children_ids', 'summary_of_ids', 'keywords', 'entities']:
            if isinstance(row.get(field_name), str):
                try:
                    row[field_name] = json.loads(row[field_name])
                except (json.JSONDecodeError, TypeError):
                    row[field_name] = []
        if isinstance(row.get('metadata'), str):
            try:
                row['metadata'] = json.loads(row['metadata'])
            except (json.JSONDecodeError, TypeError):
                row['metadata'] = {}

        row['is_summary'] = bool(row.get('is_summary', False))
        return DAGNode.from_dict(row)

    def _query_nodes(self, session_key: str, priority_eq: Optional[int] = None,
                     priority_min: Optional[int] = None,
                     is_summary: Optional[bool] = None,
                     node_type: Optional[str] = None,
                     order_asc: bool = True,
                     limit: Optional[int] = None) -> List[DAGNode]:
        """SQL 层面过滤的节点查询，不拉全量"""
        conditions = ["session_key = ?"]
        params = [session_key]

        if priority_eq is not None:
            conditions.append("priority = ?")
            params.append(priority_eq)
        if priority_min is not None:
            conditions.append("priority >= ?")
            params.append(priority_min)
        if is_summary is not None:
            conditions.append("is_summary = ?")
            params.append(1 if is_summary else 0)
        if node_type is not None:
            conditions.append("node_type = ?")
            params.append(node_type)

        order = "ASC" if order_asc else "DESC"
        query = f"SELECT * FROM dag_nodes WHERE {' AND '.join(conditions)} ORDER BY timestamp {order}"
        if limit:
            query += f" LIMIT {limit}"

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(query, params).fetchall()]
            conn.close()

        return [self._row_to_node(r) for r in rows]

    def assemble_context(
        self,
        session_key: str,
        fresh_tail_count: Optional[int] = None,
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, Dict]:
        """
        组装最终上下文

        策略:
        1. priority: CRITICAL 的节点强制包含（人格）
        2. 最近 fresh_tail_count 条原始消息保留
        3. 从旧到新，用摘要节点填充剩余空间
        4. priority: HIGH 节点最后被裁
        """
        fresh_tail_count = fresh_tail_count or self.fresh_tail_count
        max_tokens = max_tokens or self.max_context_tokens

        # 用四条定向 SQL 代替一次全表扫 + Python 分拣
        critical_nodes = self._query_nodes(session_key, priority_eq=PriorityLevel.CRITICAL, order_asc=True)
        summary_nodes = self._query_nodes(session_key, priority_min=PriorityLevel.NORMAL,
                                           is_summary=True, order_asc=True)
        message_nodes = self._query_nodes(session_key, priority_min=PriorityLevel.NORMAL,
                                           is_summary=False, order_asc=True)
        high_nodes = self._query_nodes(session_key, priority_eq=PriorityLevel.HIGH, order_asc=True)

        # 第一步：关键节点（人格等）强制包含
        result_parts = []
        used_tokens = 0

        for node in critical_nodes:
            result_parts.append(("critical", node))
            used_tokens += node.tokens

        # 第二步：最近原始消息（取最后 fresh_tail_count 条）
        recent_messages = message_nodes[-fresh_tail_count:] if message_nodes else []

        for node in recent_messages:
            if used_tokens + node.tokens > max_tokens:
                break
            result_parts.append(("recent", node))
            used_tokens += node.tokens

        # 第三步：摘要节点（从旧到新）
        allowed_summary_tokens = max_tokens - used_tokens
        summary_tokens_used = 0

        for node in summary_nodes:
            if summary_tokens_used + node.tokens > allowed_summary_tokens:
                break
            result_parts.append(("summary", node))
            summary_tokens_used += node.tokens
            used_tokens += node.tokens

        # 第四步：如果还有空间，放 priority: HIGH 节点
        for node in high_nodes:
            if used_tokens + node.tokens > max_tokens:
                break
            result_parts.append(("high", node))
            used_tokens += node.tokens

        assembled_text = "\n\n".join([node.content for _, node in result_parts])

        stats = {
            "total_tokens": used_tokens,
            "max_tokens": max_tokens,
            "critical_nodes": len(critical_nodes),
            "recent_messages": len(recent_messages),
            "summary_nodes_used": len([p for p in result_parts if p[0] == "summary"]),
            "summary_nodes_total": len(summary_nodes),
            "high_nodes_used": len([p for p in result_parts if p[0] == "high"]),
            "total_queried_critical": len(critical_nodes),
            "total_queried_summary": len(summary_nodes),
            "total_queried_message": len(message_nodes),
        }

        return assembled_text, stats

    def should_compact(self, session_key: str) -> Tuple[bool, Dict]:
        """检查是否需要触发增量压缩

        用 SQL 聚合代替全表拉到 Python 再 sum。
        """
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            raw_tokens = conn.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM dag_nodes "
                "WHERE session_key=? AND is_summary=0 AND priority>=?",
                (session_key, PriorityLevel.NORMAL)
            ).fetchone()[0]
            raw_count = conn.execute(
                "SELECT COUNT(*) FROM dag_nodes "
                "WHERE session_key=? AND is_summary=0 AND priority>=?",
                (session_key, PriorityLevel.NORMAL)
            ).fetchone()[0]
            summary_count = conn.execute(
                "SELECT COUNT(*) FROM dag_nodes "
                "WHERE session_key=? AND is_summary=1",
                (session_key,)
            ).fetchone()[0]
            conn.close()

        threshold_tokens = int(self.max_context_tokens * self.context_threshold)
        needs_compact = raw_tokens > self.leaf_chunk_tokens

        stats = {
            "raw_nodes": raw_count,
            "raw_tokens": raw_tokens,
            "summary_nodes": summary_count,
            "threshold_tokens": threshold_tokens,
            "leaf_chunk_tokens": self.leaf_chunk_tokens,
            "needs_compact": needs_compact,
            "context_usage_ratio": raw_tokens / self.max_context_tokens if self.max_context_tokens else 0,
        }

        return needs_compact, stats

    def auto_summarize(
        self,
        session_key: str,
        batch_size: int = 10,
        summary_text: Optional[str] = None,
    ) -> Dict:
        """
        自动为旧消息生成摘要节点

        Args:
            session_key: 会话 key
            batch_size: 一批摘要的消息数
            summary_text: 外部提供的摘要文本（如由 Worker/LLM 生成），None 则截断降级
        """
        needs_compact, _ = self.should_compact(session_key)
        if not needs_compact:
            return {"summarized": 0, "reason": "leaf_chunk_tokens 未达到阈值"}

        # 用 _query_nodes 定向查原始消息节点，代替全表扫再分拣
        raw_nodes = self._query_nodes(session_key, priority_min=PriorityLevel.NORMAL,
                                       is_summary=False, order_asc=True)

        if len(raw_nodes) <= self.fresh_tail_count + batch_size:
            return {"summarized": 0, "reason": "消息数不够，保留最近上下文"}

        to_summarize = raw_nodes[:-self.fresh_tail_count][:batch_size]
        if not to_summarize:
            return {"summarized": 0, "reason": "没有可摘要的节点"}

        # 生成摘要
        if not summary_text:
            combined_text = "\n".join([n.content for n in to_summarize])
            summary_text = combined_text[:500] + "..." if len(combined_text) > 500 else combined_text

        # 提取关键词
        keywords = _extract_keywords(summary_text)

        # 存储摘要节点
        summary_node_id = f"summ_{session_key}_{int(time.time())}_{hashlib.md5(summary_text.encode()[:16]).hexdigest()[:8]}"
        summary_node = DAGNode(
            node_id=summary_node_id,
            node_type=DAGNodeType.SUMMARY,
            session_key=session_key,
            content=f"[摘要] {summary_text}",
            tokens=len(summary_text) // 4,
            priority=PriorityLevel.NORMAL,
            is_summary=True,
            summary_of_ids=[n.node_id for n in to_summarize],
            timestamp=time.time(),
            keywords=keywords,
        )
        self.add_node(summary_node)

        return {
            "summarized": len(to_summarize),
            "summary_node_id": summary_node_id,
            "summary_length": len(summary_text),
            "method": "provided" if summary_text else "rule",
        }

    def expand_summary(self, summary_node_id: str) -> List[DAGNode]:
        """展开摘要，找回原始消息"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT summary_of_ids FROM dag_nodes WHERE node_id = ?",
                (summary_node_id,)
            )
            row = cursor.fetchone()
            if not row:
                conn.close()
                return []

            summary_of_ids = json.loads(row['summary_of_ids'])
            if not summary_of_ids:
                conn.close()
                return []

            placeholders = ','.join(['?' for _ in summary_of_ids])
            cursor = conn.execute(
                f"SELECT * FROM dag_nodes WHERE node_id IN ({placeholders})",
                summary_of_ids
            )
            rows = cursor.fetchall()
            conn.close()

        nodes = [self._row_to_node(dict(r)) for r in rows]
        nodes.sort(key=lambda n: n.timestamp)
        return nodes

    def get_node_count(self) -> Dict[str, Dict[str, int]]:
        """获取节点统计"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT node_type, priority, COUNT(*) as count
                FROM dag_nodes
                GROUP BY node_type, priority
            """)
            rows = cursor.fetchall()
            conn.close()

        stats = defaultdict(lambda: defaultdict(int))
        for row in rows:
            stats[row[0]][row[1]] = row[2]

        return dict(stats)

    # ========================================================================
    # R-CCAM 阶段节点（新式节点，写入 rccam_nodes 表）
    # ========================================================================

    PHASE_PARENT_MAP = {
        "user_input":  [],
        "retrieval":   ["user_input"],
        "cognition":   ["retrieval"],
        "control":     ["cognition"],
        "action":      ["control"],
        "memory":      ["action"],
    }

    PHASE_ORDER = [
        "user_input", "retrieval", "cognition",
        "control", "action", "memory", "cycle_summary",
    ]

    def add_rccam_node(self, session_key, cycle_id, cycle_index, phase_name,
                        content, strategy="", confidence=0.5, validation="unknown",
                        importance=0.5, parent_ids=None, priority=2,
                        node_type=None, metadata=None,
                        previous_cycle_id=""):
        """写入一个 R-CCAM 阶段节点到 rccam_nodes 表"""
        node_type = node_type or f"rccam_{phase_name}"
        node_id = f"rccam_{phase_name}_{session_key}_{cycle_index}_{int(time.time()*1000)}"

        if parent_ids is None:
            parent_ids = []
            expected = self.PHASE_PARENT_MAP.get(phase_name, [])
            if expected:
                with self._lock:
                    conn = sqlite3.connect(self.db_path)
                    cur = conn.execute(
                        "SELECT node_id FROM rccam_nodes WHERE session_key=? AND cycle_id=? AND phase_name=?",
                        (session_key, cycle_id, expected[-1])
                    )
                    row = cur.fetchone()
                    if row:
                        parent_ids.append(row[0])
                    conn.close()

        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)

        tokens = len(str(content)) // 2 or 1
        ts = time.time()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT OR REPLACE INTO rccam_nodes
                (node_id, node_type, session_key, content, tokens,
                 parent_ids, cycle_id, previous_cycle_id, phase_name, cycle_index,
                 priority, is_summary, is_compressed,
                 importance_score, confidence, validation,
                 keywords, strategy, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node_id, node_type, session_key, str(content), tokens,
                json.dumps(parent_ids), cycle_id, previous_cycle_id, phase_name, cycle_index,
                priority, 0, 0,
                importance, confidence, validation,
                json.dumps(self._extract_keywords(content)),
                strategy, ts,
                json.dumps(metadata or {}),
            ))
            conn.commit()
            conn.close()

        return node_id

    def get_rccam_cycle_nodes(self, session_key, cycle_id):
        """获取指定 cycle 内的所有阶段节点，按 phase 顺序排列"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM rccam_nodes WHERE session_key=? AND cycle_id=? ORDER BY timestamp ASC",
                (session_key, cycle_id)
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()

        def _phase_key(n):
            p = n.get("phase_name", "")
            try:
                return self.PHASE_ORDER.index(p)
            except ValueError:
                return 99
        rows.sort(key=_phase_key)
        return rows

    def get_rccam_session_cycles(self, session_key):
        """获取一个 session 的所有 cycle_id 及其元信息"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                """SELECT DISTINCT cycle_id, MIN(timestamp) as first_ts,
                          MAX(cycle_index) as max_idx,
                          MAX(is_summary) as has_summary
                 FROM rccam_nodes WHERE session_key=? AND cycle_id != ''
                 GROUP BY cycle_id ORDER BY first_ts ASC""",
                (session_key,)
            )
            cycles = [dict(r) for r in cur.fetchall()]
            conn.close()
        return cycles

    def assemble_from_cycles(self, session_key, fresh_cycles=3, max_tokens=240000):
        """
        按 cycle 为单元组装上下文。
        """
        persona_nodes = self.get_session_nodes(session_key, priority_max=0, limit=10)
        cycles = self.get_rccam_session_cycles(session_key)

        result_parts = []
        used_tokens = 0

        for n in persona_nodes:
            result_parts.append(("persona", n.content))
            used_tokens += max(n.tokens, len(n.content) // 2)

        if cycles:
            recent = cycles[-fresh_cycles:] if len(cycles) > fresh_cycles else cycles
            older = cycles[:-fresh_cycles] if len(cycles) > fresh_cycles else []

            for c in recent:
                nodes = self.get_rccam_cycle_nodes(session_key, c["cycle_id"])
                for node in nodes:
                    if node["is_compressed"]:
                        continue
                    t = node["tokens"] or len(node["content"]) // 2
                    if used_tokens + t > max_tokens * 0.95:
                        break
                    label = "[{}] ".format(node["phase_name"].upper())
                    result_parts.append((f"cycle_{c['cycle_id']}_{node['phase_name']}",
                                         label + node["content"]))
                    used_tokens += t

            for c in reversed(older):
                summary = self.get_cycle_summary(session_key, c["cycle_id"])
                if summary:
                    st = summary.get("tokens", len(summary["content"]) // 2)
                    if used_tokens + st <= max_tokens:
                        result_parts.append((f"summ_{c['cycle_id']}",
                                             f"[Cycle {c.get('cycle_index','?')} 摘要] {summary['content']}"))
                        used_tokens += st
                    else:
                        break

        has_rccam = bool(cycles)
        if not has_rccam:
            text, _ = self.assemble_context(session_key, fresh_tail_count=20, max_tokens=max_tokens)
            if text:
                result_parts.append(("old_dag", text))

        assembled = "\n\n".join([c for _, c in result_parts])
        stats = {
            "total_tokens": used_tokens,
            "max_tokens": max_tokens,
            "persona_nodes": len(persona_nodes),
            "total_cycles": len(cycles),
            "total_parts": len(result_parts),
        }
        return assembled, stats

    def get_cycle_summary(self, session_key, cycle_id):
        """获取某个 cycle 的摘要节点"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM rccam_nodes WHERE session_key=? AND cycle_id=? AND node_type='rccam_cycle_summary' LIMIT 1",
                (session_key, cycle_id)
            )
            row = cur.fetchone()
            conn.close()
        if row:
            d = dict(row)
            try:
                d["metadata"] = json.loads(d["metadata"]) if isinstance(d["metadata"], str) else d["metadata"]
            except Exception:
                d["metadata"] = {}
            return d
        return None

    def write_cycle_summary(self, session_key, cycle_id, cycle_index,
                             user_intent, key_findings, conclusion,
                             confidence=0.5, source_phases=None):
        """写入 cycle_summary 节点，标记原始节点为 is_compressed"""
        content_obj = {
            "cycle": cycle_index,
            "user_intent": user_intent[:200],
            "key_findings": (key_findings or [])[:5],
            "conclusion": conclusion[:200] if conclusion else "",
            "confidence": confidence,
            "source_phases": source_phases or {},
        }
        content = json.dumps(content_obj, ensure_ascii=False)

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE rccam_nodes SET is_compressed=1 WHERE session_key=? AND cycle_id=? AND node_type != 'rccam_cycle_summary'",
                (session_key, cycle_id)
            )
            conn.commit()
            conn.close()

        previous_cycle_id = cycle_id.replace(f"_{cycle_index}", f"_{cycle_index - 1}") if cycle_index and cycle_index > 1 else ""

        return self.add_rccam_node(
            session_key=session_key, cycle_id=cycle_id,
            cycle_index=cycle_index, phase_name="cycle_summary",
            content=content, strategy="cycle_summary",
            confidence=confidence, validation="passed",
            importance=0.8, node_type="rccam_cycle_summary",
            previous_cycle_id=previous_cycle_id,
        )

    def expand_rccam_cycle(self, session_key, cycle_id):
        """展开 cycle_summary，从 rccam_nodes 恢复原始阶段节点"""
        nodes = self.get_rccam_cycle_nodes(session_key, cycle_id)
        return [n for n in nodes if n["node_type"] != "rccam_cycle_summary"]

    def rccam_compact_needed(self, session_key):
        """检查是否需要触发压缩（τ_soft: 6K / τ_hard: 12K tokens）"""
        TAU_SOFT, TAU_HARD = 6000, 12000
        cycles = self.get_rccam_session_cycles(session_key)

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM rccam_nodes "
                "WHERE session_key=? AND is_compressed=0 AND node_type != 'rccam_cycle_summary'",
                (session_key,)
            )
            raw_tokens = cur.fetchone()[0] or 0
            conn.close()

        needs_soft = raw_tokens > TAU_SOFT
        needs_hard = raw_tokens > TAU_HARD

        compressible_cycles = []
        if needs_soft:
            for c in cycles:
                if not c.get("has_summary", 0):
                    if len(cycles) - cycles.index(c) > 2:
                        compressible_cycles.append(c["cycle_id"])

        stats = {"raw_tokens": raw_tokens, "tau_soft": TAU_SOFT, "tau_hard": TAU_HARD,
                 "total_cycles": len(cycles), "compressible_cycles": len(compressible_cycles)}
        return needs_soft, needs_hard, compressible_cycles, stats

    def compact_rccam_cycle(self, session_key, cycle_id):
        """对一个旧 cycle 执行压缩（LCM 三级升级协议）

        Level 1: 完整结构化摘要 — 保留所有阶段内容（最适合 QA）
        Level 2: 要点式压缩 — 只留关键发现 + 结论（节省 ~50% tokens）
        Level 3: 确定性 512 字符硬截断 — 绝对不溢出窗口

        三级协议保证：摘要质量随压缩强度梯度降级，但永远不会爆窗口。
        """
        nodes = self.get_rccam_cycle_nodes(session_key, cycle_id)
        if not nodes:
            return {"summarized": 0, "reason": "no nodes"}

        cycle_index = nodes[0]["cycle_index"]
        phase_map = {n["phase_name"]: n["content"] for n in nodes}

        user_intent = phase_map.get("user_input") or ""
        conclusions = phase_map.get("action") or phase_map.get("control") or ""

        # 按 PHASE_ORDER 顺序收集各阶段前 200 字符
        candidate_findings = []
        source_phases = {}
        for p in self.PHASE_ORDER:
            c = phase_map.get(p, "")
            if c:
                candidate_findings.append(c[:200])
                source_phases[p] = c[:100]

        # ── Level 1: 完整结构化摘要 ──
        l1_content = json.dumps({
            "cycle": cycle_index,
            "user_intent": user_intent[:200],
            "key_findings": [f[:120] for f in candidate_findings[:5]],
            "conclusion": conclusions[:200],
            "compression_level": 1,
        }, ensure_ascii=False)

        L3_HARD_LIMIT = 512  # Level 3 绝对上限

        if len(l1_content) > L3_HARD_LIMIT:
            # ── Level 2: 要点式压缩（删阶段细节，只留关键发现 + 结论） ──
            l2_content = json.dumps({
                "cycle": cycle_index,
                "key_findings": [f[:80] for f in candidate_findings[:3]],
                "conclusion": conclusions[:150],
                "compression_level": 2,
            }, ensure_ascii=False)

            if len(l2_content) > L3_HARD_LIMIT:
                # ── Level 3: 确定性硬截断 ──
                # 取结论前 150 + 意图前 60 写入 cycle_summary
                # 同时标记所有节点 is_compressed=1
                with self._lock:
                    conn = sqlite3.connect(self.db_path)
                    conn.execute(
                        "UPDATE rccam_nodes SET is_compressed=1 WHERE session_key=? AND cycle_id=? AND node_type != 'rccam_cycle_summary'",
                        (session_key, cycle_id)
                    )
                    conn.commit()
                    conn.close()

                l3_text = json.dumps({
                    "cycle": cycle_index,
                    "user_intent": user_intent[:60],
                    "conclusion": conclusions[:100],
                    "compression_level": 3,
                }, ensure_ascii=False)
                if len(l3_text) > L3_HARD_LIMIT:
                    l3_text = l3_text[:L3_HARD_LIMIT]

                previous_cycle_id = cycle_id.replace(f"_{cycle_index}", f"_{cycle_index - 1}") if cycle_index and cycle_index > 1 else ""
                return self.add_rccam_node(
                    session_key=session_key, cycle_id=cycle_id,
                    cycle_index=cycle_index, phase_name="cycle_summary",
                    content=l3_text, strategy="cycle_summary",
                    confidence=0.2, validation="truncated",
                    importance=0.3, node_type="rccam_cycle_summary",
                    previous_cycle_id=previous_cycle_id,
                )

            # Level 2: 要点式
            return self.write_cycle_summary(
                session_key=session_key, cycle_id=cycle_id,
                cycle_index=cycle_index, user_intent=user_intent[:80],
                key_findings=[f[:80] for f in candidate_findings[:3]],
                conclusion=conclusions[:150], confidence=0.4,
                source_phases={},
            )

        # Level 1: 完整结构
        return self.write_cycle_summary(
            session_key=session_key, cycle_id=cycle_id,
            cycle_index=cycle_index, user_intent=user_intent[:200],
            key_findings=[f[:120] for f in candidate_findings[:5]],
            conclusion=conclusions[:200], confidence=0.5,
            source_phases=source_phases,
        )

    def get_rccam_stats(self, session_key):
        """获取 R-CCAM DAG 全景统计"""
        cycles = self.get_rccam_session_cycles(session_key)
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            total_nodes = conn.execute(
                "SELECT COUNT(*) FROM rccam_nodes WHERE session_key=?",
                (session_key,)).fetchone()[0] or 0
            total_bytes = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(content)), 0) FROM rccam_nodes WHERE session_key=?",
                (session_key,)).fetchone()[0] or 0
            compressed = conn.execute(
                "SELECT COUNT(*) FROM rccam_nodes WHERE session_key=? AND is_compressed=1",
                (session_key,)).fetchone()[0] or 0
            conn.close()
        return {
            "rccam_nodes": total_nodes, "total_bytes": total_bytes,
            "compressed_nodes": compressed, "total_cycles": len(cycles),
        }

    def _extract_keywords(self, text):
        """简易关键词提取"""
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', str(text)[:2000])
        word_counts = Counter(w for w in words if len(w) > 1)
        return [w for w, _ in word_counts.most_common(10)]

    def get_db_size(self) -> int:
        """获取数据库大小（字节）"""
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0

    # ========================================================================
    # Cognition Forest 子树（四类独立子树，CRITICAL 优先永不压缩）
    # ========================================================================

    def _cog_subtree_key(self, forest_type: str) -> str:
        """生成 Cognition Forest 子树 session_key"""
        return f"{_COG_SUBTREE_SESSION_PREFIX}{forest_type}"

    def add_cognition_subtree(
        self,
        forest_type: str,
        content: str,
        tokens: int = 0,
        source: str = "",
        metadata: Optional[Dict] = None,
    ) -> str:
        """写入 Cognition Forest 子树数据（_memory_phase 调用入口）"""
        if forest_type not in CognitionForestType.ALL_TYPES:
            logger.warning(f"未知子树类型: {forest_type}, 跳过")
            return ""

        session_key = self._cog_subtree_key(forest_type)
        node_id = f"cog_{forest_type}_{source}_{int(time.time()*1000)}_{hashlib.md5(content.encode()[:16]).hexdigest()[:8]}"

        _meta = {"source": source, "forest_type": forest_type}
        if metadata:
            _meta.update(metadata)

        node = DAGNode(
            node_id=node_id,
            node_type=DAGNodeType.PERSONA,
            session_key=session_key,
            content=content,
            tokens=tokens or len(content) // 4,
            priority=PriorityLevel.CRITICAL,
            parent_ids=[],
            importance_score=0.9 if forest_type in (CognitionForestType.USER, CognitionForestType.SELF) else 0.7,
            timestamp=time.time(),
            metadata=_meta,
        )

        self.add_node(node)
        return node_id

    def get_all_session_keys(self) -> List[str]:
        """获取 DAG 中所有活跃的 session_key"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT DISTINCT session_key FROM dag_nodes ORDER BY session_key"
            ).fetchall()
            rows2 = conn.execute(
                "SELECT DISTINCT session_key FROM rccam_nodes ORDER BY session_key"
            ).fetchall()
            conn.close()
        keys = set(r[0] for r in rows if r[0])
        keys.update(r[0] for r in rows2 if r[0])
        keys.discard('_cog_subtree_user')
        keys.discard('_cog_subtree_self')
        return sorted(keys)

    def close(self):
        """关闭资源"""
        pass


# ============================================================================
# 辅助函数
# ============================================================================

def _extract_keywords(text: str) -> List[str]:
    """简易关键词提取（无需 numpy / jieba）"""
    words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text)
    word_counts = Counter(w for w in words if len(w) > 1)
    return [w for w, _ in word_counts.most_common(15)]


_dag_instances: Dict[str, DAGContextManager] = {}


def get_dag_manager(
    session_key: str = "default",
    **kwargs,
) -> DAGContextManager:
    """获取 DAG 上下文管理器实例（单例缓存）"""
    global _dag_instances
    instance = _dag_instances.get(session_key)
    if instance is None:
        instance = DAGContextManager(**kwargs)
        _dag_instances[session_key] = instance
    return instance


__all__ = [
    'DAGContextManager',
    'DAGNode',
    'DAGNodeType',
    'PhaseNodeType',
    'PriorityLevel',
    'CognitionForestType',
    'get_dag_manager',
]
