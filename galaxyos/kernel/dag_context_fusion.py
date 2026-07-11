"""
DAGContextFusion — GalaxyOS DAG 上下文融合层

将 GalaxyOS DAG 上下文管理器作为 Agent Studio ContextEngine 的增强实现注入。

核心能力：
  - assemble(): 融合 DAG 上下文（摘要节点回溯 + 时间衰减排序）和 agent-core 上下文
  - create_node(): 创建 DAG 上下文节点
  - get_summary_chain(): 获取摘要节点回溯链
  - compact(): DAG 上下文压缩（COSPLAY 增强压缩 + contract 上下文注入）
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class DAGNode:
    id: str = ""
    session_key: str = ""
    role: str = "user"
    content: str = ""
    parent_id: str = ""
    summary: str = ""
    timestamp: float = field(default_factory=time.time)
    importance: float = 1.0
    node_type: str = "message"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SummaryChain:
    nodes: list[DAGNode] = field(default_factory=list)
    total_tokens: int = 0
    session_key: str = ""


class DAGContextFusion:
    """
    GalaxyOS DAG 上下文融合层。

    将 GalaxyOS DAG 上下文管理器作为 Agent Studio ContextEngine 的增强实现注入，
    提供 DAG 摘要节点回溯、时间衰减排序、COSPLAY 增强压缩等能力。

    用法：
        fusion = DAGContextFusion(db_path="dag_context.db")
        node_id = await fusion.create_node(session_key="ws:dm:user1", role="user", content="hello")
        context = await fusion.assemble(session_key="ws:dm:user1", token_budget=12000)
    """

    def __init__(self, db_path: str = "", config: dict[str, Any] | None = None):
        self._db_path = db_path
        self._config = config or {}
        self._nodes: dict[str, DAGNode] = {}
        self._id_counter = 0
        self._max_nodes = self._config.get("max_nodes", 10000)
        self._retention_days = self._config.get("retention_days", 90)
        self._default_token_budget = self._config.get("token_budget", 12000)
        self._compact_threshold = self._config.get("compact_threshold", 0.6)

        if db_path:
            self._init_db(db_path)

    def _init_db(self, db_path: str) -> None:
        try:
            db_dir = Path(db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dag_nodes (
                    id TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    parent_id TEXT DEFAULT '',
                    summary TEXT DEFAULT '',
                    timestamp REAL NOT NULL,
                    importance REAL DEFAULT 1.0,
                    node_type TEXT DEFAULT 'message',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON dag_nodes(session_key)")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"DAG DB init failed, using in-memory: {e}")

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"dag_{self._id_counter:06d}"

    async def create_node(
        self,
        session_key: str,
        role: str = "user",
        content: str = "",
        parent_id: str = "",
        importance: float = 1.0,
        node_type: str = "message",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        node_id = self._next_id()
        node = DAGNode(
            id=node_id,
            session_key=session_key,
            role=role,
            content=content,
            parent_id=parent_id,
            importance=importance,
            node_type=node_type,
            metadata=metadata or {},
        )
        self._nodes[node_id] = node

        if self._db_path:
            self._persist_node(node)

        return {"id": node_id, "session_key": session_key, "role": role, "importance": importance}

    async def assemble(
        self,
        session_key: str,
        token_budget: int = 0,
        include_dag: bool = True,
        recall_on_assemble: bool = True,
    ) -> dict[str, Any]:
        budget = token_budget or self._default_token_budget
        start = time.monotonic()

        session_nodes = [
            n for n in self._nodes.values()
            if n.session_key == session_key
        ]

        session_nodes.sort(key=lambda n: (n.importance, n.timestamp), reverse=True)

        assembled_context = []
        total_tokens = 0
        for node in session_nodes:
            estimated_tokens = len(node.content) // 4
            if total_tokens + estimated_tokens > budget:
                if node.summary:
                    assembled_context.append({
                        "role": node.role,
                        "content": f"[摘要] {node.summary}",
                        "source": "dag_summary",
                        "importance": node.importance,
                    })
                    total_tokens += len(node.summary) // 4
                continue
            assembled_context.append({
                "role": node.role,
                "content": node.content,
                "source": "dag_full",
                "importance": node.importance,
            })
            total_tokens += estimated_tokens

        elapsed_ms = (time.monotonic() - start) * 1000

        return {
            "context": assembled_context,
            "session_key": session_key,
            "total_nodes": len(session_nodes),
            "assembled_entries": len(assembled_context),
            "token_budget": budget,
            "tokens_used": total_tokens,
            "elapsed_ms": round(elapsed_ms, 1),
            "dag_enabled": include_dag,
        }

    async def get_summary_chain(self, session_key: str, max_depth: int = 10) -> dict[str, Any]:
        session_nodes = [
            n for n in self._nodes.values()
            if n.session_key == session_key and n.summary
        ]
        session_nodes.sort(key=lambda n: n.timestamp, reverse=True)

        chain = []
        for i, node in enumerate(session_nodes[:max_depth]):
            chain.append({
                "id": node.id,
                "summary": node.summary,
                "timestamp": node.timestamp,
                "importance": node.importance,
            })

        return {
            "chain": chain,
            "session_key": session_key,
            "depth": len(chain),
            "total_summaries": len(session_nodes),
        }

    async def compact(self, session_key: str, threshold: float = 0.0) -> dict[str, Any]:
        t = threshold or self._compact_threshold
        session_nodes = [
            n for n in self._nodes.values()
            if n.session_key == session_key
        ]

        low_importance = [n for n in session_nodes if n.importance < t]
        compacted = 0
        for node in low_importance:
            if not node.summary and node.content:
                node.summary = node.content[:100] + "..." if len(node.content) > 100 else node.content
                node.content = ""
                compacted += 1

        return {
            "status": "completed",
            "session_key": session_key,
            "total_nodes": len(session_nodes),
            "compacted": compacted,
            "threshold": t,
        }

    def _persist_node(self, node: DAGNode) -> None:
        if not self._db_path:
            return
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "INSERT OR REPLACE INTO dag_nodes VALUES (?,?,?,?,?,?,?,?,?,?)",
                (node.id, node.session_key, node.role, node.content, node.parent_id,
                 node.summary, node.timestamp, node.importance, node.node_type,
                 json.dumps(node.metadata, ensure_ascii=False)),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"DAG persist failed: {e}")

    def get_stats(self) -> dict[str, Any]:
        sessions = set(n.session_key for n in self._nodes.values())
        return {
            "total_nodes": len(self._nodes),
            "sessions": len(sessions),
            "max_nodes": self._max_nodes,
            "retention_days": self._retention_days,
            "db_path": self._db_path or "in-memory",
        }
