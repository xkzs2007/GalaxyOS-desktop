"""
MemorySyncBridge — 液态神经记忆与 agent-core 上下文双写桥接层

职责：
1. dual_write() — 同步写液态神经记忆（engram/neural/synapse + DAG 节点），异步写 agent-core 上下文
2. recall() — 优先液态神经记忆（CRAG + GraphRAG + DAG 上下文增强），语义补充 agent-core 上下文
3. rollback_sync() — 液态神经记忆回滚时同步清理 agent-core 上下文
4. dream_mode_sync() — Dream Mode 协同（记忆巩固 + 艾宾浩斯遗忘曲线）
5. WorkSpace 隔离验证
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MemoryScope(str, Enum):
    LIQUID_NEURAL = "liquid_neural"
    AGENT_CORE = "agent_core"
    BOTH = "both"


@dataclass
class MemoryEntry:
    id: str
    workspace_id: str
    content: str
    source: str
    scope: MemoryScope
    memory_type: str = "auto"
    skill_name: Optional[str] = None
    pinned: bool = False
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryRecallResult:
    entries: List[MemoryEntry]
    total: int
    source: str
    latency_ms: float = 0
    dag_enhanced: bool = False


class MemorySyncBridge:
    CONSISTENCY_WINDOW_S = 30.0
    SEMANTIC_ENHANCEMENT_TIMEOUT_S = 2.0
    MEMORY_ISOLATION_VIOLATION = "MEMORY_ISOLATION_VIOLATION"
    DAG_CONTEXT_TIMEOUT_S = 1.5
    ASYNC_RETRY_MAX = 3
    ASYNC_RETRY_DELAY_S = 1.0

    def __init__(
        self,
        liquid_memory_adapter=None,
        dag_context_fusion=None,
        agent_core_context: Optional[Any] = None,
        persist_dir: Optional[Path] = None,
    ):
        self._liquid_memory = liquid_memory_adapter
        self._dag_fusion = dag_context_fusion
        self._agent_core_context = agent_core_context
        self._persist_dir = persist_dir or Path.home() / ".galaxyos" / "memory"
        self._agent_core_store: Dict[str, List[MemoryEntry]] = {}
        self._pending_async_writes: List[Dict[str, Any]] = []
        self._audit_log: List[Dict[str, Any]] = []
        self._workspace_sessions: Dict[str, str] = {}

    def _session_key(self, workspace_id: str) -> str:
        if workspace_id not in self._workspace_sessions:
            self._workspace_sessions[workspace_id] = f"ws:dm:{workspace_id}"
        return self._workspace_sessions[workspace_id]

    async def dual_write(
        self,
        workspace_id: str,
        content: str,
        source: str = "skill",
        memory_type: str = "auto",
        skill_name: Optional[str] = None,
        pinned: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryEntry:
        session_key = self._session_key(workspace_id)
        entry_id = f"mem-{int(time.time() * 1000)}"

        ln_result = await self._write_liquid_neural(
            session_key=session_key,
            content=content,
            source=source,
            memory_type=memory_type,
            metadata=metadata,
        )

        if not ln_result.get("status") == "written":
            self._audit("dual_write", workspace_id, entry_id, "liquid_neural", f"failed: {ln_result}")
            raise RuntimeError(f"Liquid neural memory write failed: {ln_result}")

        self._audit("dual_write", workspace_id, entry_id, "liquid_neural", "success")

        dag_node_id = ""
        if self._dag_fusion:
            try:
                dag_result = await asyncio.wait_for(
                    self._dag_fusion.create_node(
                        session_key=session_key,
                        role=source,
                        content=content,
                        importance=1.2 if pinned else 1.0,
                        node_type="memory_write",
                        metadata={"entry_id": entry_id, "skill_name": skill_name, **(metadata or {})},
                    ),
                    timeout=self.DAG_CONTEXT_TIMEOUT_S,
                )
                dag_node_id = dag_result.get("id", "")
                self._audit("dual_write", workspace_id, entry_id, "dag_context", "success")
            except asyncio.TimeoutError:
                self._audit("dual_write", workspace_id, entry_id, "dag_context", "timeout")
            except Exception as e:
                self._audit("dual_write", workspace_id, entry_id, "dag_context", f"failed: {e}")

        asyncio.ensure_future(self._async_write_agent_core(
            workspace_id=workspace_id,
            entry_id=entry_id,
            content=content,
            source=source,
            skill_name=skill_name,
            pinned=pinned,
            metadata=metadata,
        ))

        wb_entry = MemoryEntry(
            id=entry_id,
            workspace_id=workspace_id,
            content=content,
            source=source,
            scope=MemoryScope.LIQUID_NEURAL,
            memory_type=memory_type,
            skill_name=skill_name,
            pinned=pinned,
            timestamp=time.time(),
            metadata={
                **(metadata or {}),
                "liquid_neural_id": ln_result.get("id", ""),
                "dag_node_id": dag_node_id,
            },
        )

        self._persist_entry(workspace_id, wb_entry)
        return wb_entry

    async def recall(
        self,
        workspace_id: str,
        query: str = "",
        top_k: int = 10,
        semantic_enhancement: bool = True,
        dag_context: bool = True,
    ) -> MemoryRecallResult:
        start = time.time()
        session_key = self._session_key(workspace_id)

        ln_entries: List[MemoryEntry] = []
        dag_enhanced = False

        if self._liquid_memory:
            try:
                ln_result = await self._liquid_memory.recall(
                    query=query,
                    top_k=top_k,
                    session_key=session_key,
                )
                for r in ln_result.get("results", []):
                    ln_entries.append(MemoryEntry(
                        id=r.get("id", ""),
                        workspace_id=workspace_id,
                        content=r.get("content", ""),
                        source=r.get("source", ""),
                        scope=MemoryScope.LIQUID_NEURAL,
                        memory_type=r.get("type", "auto"),
                        metadata={"score": r.get("score", 0), "source_layer": "liquid_neural"},
                    ))
            except Exception as e:
                logger.warning(f"Liquid neural recall failed: {e}")

        dag_entries: List[MemoryEntry] = []
        if dag_context and self._dag_fusion:
            try:
                dag_result = await asyncio.wait_for(
                    self._dag_fusion.assemble(
                        session_key=session_key,
                        token_budget=4000,
                    ),
                    timeout=self.DAG_CONTEXT_TIMEOUT_S,
                )
                for ctx in dag_result.get("context", []):
                    dag_entries.append(MemoryEntry(
                        id=f"dag-{int(time.time() * 1000)}",
                        workspace_id=workspace_id,
                        content=ctx.get("content", ""),
                        source=ctx.get("source", "dag"),
                        scope=MemoryScope.LIQUID_NEURAL,
                        memory_type="dag_context",
                        metadata={"importance": ctx.get("importance", 1.0), "source_layer": "dag"},
                    ))
                dag_enhanced = True
            except asyncio.TimeoutError:
                logger.warning("DAG context assembly timed out")
            except Exception as e:
                logger.warning(f"DAG context assembly failed: {e}")

        ac_entries: List[MemoryEntry] = []
        if semantic_enhancement and self._agent_core_context:
            try:
                ac_all = self._agent_core_store.get(workspace_id, [])
                if query:
                    ac_entries = [e for e in ac_all if query.lower() in e.content.lower()]
                ac_entries = ac_entries[:max(0, top_k - len(ln_entries))]
            except Exception:
                ac_entries = []

        all_entries = ln_entries + dag_entries + ac_entries
        seen_ids = set()
        deduped = []
        for e in all_entries:
            key = (e.workspace_id, e.content[:100])
            if key not in seen_ids:
                seen_ids.add(key)
                deduped.append(e)

        deduped.sort(key=lambda e: (
            0 if e.scope == MemoryScope.LIQUID_NEURAL else 1,
            -e.metadata.get("score", 0),
            -e.metadata.get("importance", 0),
            -e.timestamp,
        ))

        latency = (time.time() - start) * 1000

        return MemoryRecallResult(
            entries=deduped[:top_k],
            total=len(deduped),
            source="liquid_neural+dag+agent_core",
            latency_ms=latency,
            dag_enhanced=dag_enhanced,
        )

    async def rollback_sync(self, workspace_id: str, entry_id: str) -> bool:
        session_key = self._session_key(workspace_id)

        ac_entries = self._agent_core_store.get(workspace_id, [])
        self._agent_core_store[workspace_id] = [e for e in ac_entries if e.id != entry_id]

        self._audit("rollback_sync", workspace_id, entry_id, "agent_core", "cleaned")

        if self._dag_fusion:
            try:
                session_nodes = [
                    n for n in self._dag_fusion._nodes.values()
                    if n.session_key == session_key
                    and n.metadata.get("entry_id") == entry_id
                ]
                for node in session_nodes:
                    del self._dag_fusion._nodes[node.id]
                self._audit("rollback_sync", workspace_id, entry_id, "dag_context", f"cleaned {len(session_nodes)} nodes")
            except Exception as e:
                self._audit("rollback_sync", workspace_id, entry_id, "dag_context", f"failed: {e}")

        self._audit("rollback_sync", workspace_id, entry_id, "liquid_neural", "rolled_back")
        return True

    async def dream_mode_sync(self, workspace_id: str) -> Dict[str, Any]:
        session_key = self._session_key(workspace_id)

        consolidation_result = {"consolidated": 0, "decayed": 0}
        if self._liquid_memory:
            try:
                consolidation_result = await self._liquid_memory.consolidate(session_key=session_key)
            except Exception as e:
                logger.warning(f"Dream mode consolidation failed: {e}")

        compact_result = {"compacted": 0}
        if self._dag_fusion:
            try:
                compact_result = await self._dag_fusion.compact(session_key=session_key)
            except Exception as e:
                logger.warning(f"Dream mode DAG compact failed: {e}")

        self._audit("dream_mode_sync", workspace_id, "", "both", "completed")

        return {
            "workspace_id": workspace_id,
            "consolidation": consolidation_result,
            "dag_compact": compact_result,
            "agent_core_total": len(self._agent_core_store.get(workspace_id, [])),
        }

    def verify_workspace_isolation(self, workspace_a: str, workspace_b: str) -> bool:
        entries_a = set(e.id for e in self._agent_core_store.get(workspace_a, []))
        entries_b = set(e.id for e in self._agent_core_store.get(workspace_b, []))

        if len(entries_a & entries_b) > 0:
            logger.error(f"{self.MEMORY_ISOLATION_VIOLATION}: shared entries between {workspace_a} and {workspace_b}")
            return False

        if self._dag_fusion:
            sk_a = self._session_key(workspace_a)
            sk_b = self._session_key(workspace_b)
            nodes_a = {n.id for n in self._dag_fusion._nodes.values() if n.session_key == sk_a}
            nodes_b = {n.id for n in self._dag_fusion._nodes.values() if n.session_key == sk_b}
            if len(nodes_a & nodes_b) > 0:
                logger.error(f"{self.MEMORY_ISOLATION_VIOLATION}: shared DAG nodes between {workspace_a} and {workspace_b}")
                return False

        return True

    async def _write_liquid_neural(
        self,
        session_key: str,
        content: str,
        source: str,
        memory_type: str,
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not self._liquid_memory:
            return {"status": "skipped", "reason": "no liquid memory adapter"}

        return await self._liquid_memory.write(
            content=content,
            source=source,
            session_key=session_key,
            memory_type=memory_type,
            metadata=metadata,
        )

    async def _async_write_agent_core(
        self,
        workspace_id: str,
        entry_id: str,
        content: str,
        source: str,
        skill_name: Optional[str],
        pinned: bool,
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        for attempt in range(self.ASYNC_RETRY_MAX):
            try:
                ac_entry = MemoryEntry(
                    id=entry_id,
                    workspace_id=workspace_id,
                    content=content,
                    source=source,
                    scope=MemoryScope.AGENT_CORE,
                    skill_name=skill_name,
                    pinned=pinned,
                    timestamp=time.time(),
                    metadata=metadata or {},
                )
                self._agent_core_store.setdefault(workspace_id, []).append(ac_entry)
                self._audit("dual_write", workspace_id, entry_id, "agent_core", "success")
                return
            except Exception as e:
                self._audit("dual_write", workspace_id, entry_id, "agent_core", f"attempt {attempt + 1} failed: {e}")
                if attempt < self.ASYNC_RETRY_MAX - 1:
                    await asyncio.sleep(self.ASYNC_RETRY_DELAY_S)

        self._pending_async_writes.append({
            "workspace_id": workspace_id,
            "entry_id": entry_id,
            "content": content,
            "source": source,
            "skill_name": skill_name,
            "pinned": pinned,
            "metadata": metadata,
        })

    def _persist_entry(self, workspace_id: str, entry: MemoryEntry) -> None:
        ws_dir = self._persist_dir / workspace_id
        ws_dir.mkdir(parents=True, exist_ok=True)
        entry_file = ws_dir / f"{entry.id}.json"
        entry_file.write_text(json.dumps({
            "id": entry.id,
            "workspace_id": entry.workspace_id,
            "content": entry.content,
            "source": entry.source,
            "scope": entry.scope.value,
            "memory_type": entry.memory_type,
            "skill_name": entry.skill_name,
            "pinned": entry.pinned,
            "timestamp": entry.timestamp,
            "metadata": entry.metadata,
        }, ensure_ascii=False, indent=2))

    def _audit(self, operation: str, workspace_id: str, entry_id: str, scope: str, result: str) -> None:
        self._audit_log.append({
            "timestamp": time.time(),
            "operation": operation,
            "workspace_id": workspace_id,
            "entry_id": entry_id,
            "scope": scope,
            "result": result,
        })

    def get_stats(self) -> Dict[str, Any]:
        return {
            "agent_core_workspaces": len(self._agent_core_store),
            "agent_core_total_entries": sum(len(v) for v in self._agent_core_store.values()),
            "pending_async_writes": len(self._pending_async_writes),
            "audit_log_size": len(self._audit_log),
            "liquid_memory": self._liquid_memory.get_stats() if self._liquid_memory else None,
            "dag_fusion": self._dag_fusion.get_stats() if self._dag_fusion else None,
        }
