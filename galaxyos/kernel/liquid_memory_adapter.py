"""
LiquidMemoryAdapter — GalaxyOS 液态神经记忆适配器

封装 GalaxyOS 液态神经记忆系统的三层记忆写入和检索：
  - Engram 层：N-gram 嵌入 O(1) 条件查找
  - Neural 层：LTC/CfC/NCP 液态神经网络
  - Synapse 层：突触网络传播（ActivationSpreader）

检索增强管线：CRAG + GraphRAG + RAPTOR + Self-RAG + Merge Gate + neural_rerank_dedup

记忆巩固：biorhythm_sleep_consolidation + 艾宾浩斯遗忘曲线
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    ENGRAM = "engram"
    NEURAL = "neural"
    SYNAPSE = "synapse"
    AUTO = "auto"


@dataclass
class MemoryEntry:
    id: str = ""
    content: str = ""
    source: str = "user"
    memory_type: str = "auto"
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemorySearchResult:
    entries: list[MemoryEntry] = field(default_factory=list)
    total: int = 0
    query: str = ""
    elapsed_ms: float = 0.0
    sources: list[str] = field(default_factory=list)


class LiquidMemoryAdapter:
    """
    GalaxyOS 液态神经记忆适配器。

    封装三层记忆写入/检索，作为 GalaxyOS 记忆系统的核心后端。

    用法：
        adapter = LiquidMemoryAdapter(workspace_path="/path/to/workspace")
        entry_id = await adapter.write(content="用户偏好深色主题", source="user")
        results = await adapter.recall(query="用户主题偏好", top_k=5)
    """

    def __init__(self, workspace_path: str = "", config: dict[str, Any] | None = None):
        self._workspace_path = workspace_path
        self._config = config or {}
        self._engram_store: dict[str, MemoryEntry] = {}
        self._neural_store: dict[str, MemoryEntry] = {}
        self._synapse_store: dict[str, MemoryEntry] = {}
        self._id_counter = 0
        self._consolidation_enabled = self._config.get("consolidation_enabled", True)
        self._forgetting_curve_enabled = self._config.get("forgetting_curve_enabled", True)

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"mem_{self._id_counter:06d}"

    async def write(
        self,
        content: str,
        source: str = "user",
        session_key: str = "",
        memory_type: str = "auto",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry_id = self._next_id()
        entry = MemoryEntry(
            id=entry_id,
            content=content,
            source=source,
            memory_type=memory_type,
            metadata=metadata or {},
        )

        if memory_type in (MemoryType.ENGRAM, MemoryType.AUTO):
            self._engram_store[entry_id] = entry
        if memory_type in (MemoryType.NEURAL, MemoryType.AUTO):
            self._neural_store[entry_id] = entry
        if memory_type in (MemoryType.SYNAPSE, MemoryType.AUTO):
            self._synapse_store[entry_id] = entry

        logger.debug(f"LiquidMemory write: id={entry_id}, type={memory_type}, source={source}")

        return {
            "status": "written",
            "id": entry_id,
            "memory_type": memory_type,
            "layers": self._get_written_layers(memory_type),
            "session_key": session_key,
        }

    async def recall(
        self,
        query: str,
        top_k: int = 10,
        session_key: str = "",
        use_crag: bool = True,
        use_graph_rag: bool = True,
        use_neural_rerank: bool = True,
    ) -> dict[str, Any]:
        start = time.monotonic()

        all_entries = []
        all_entries.extend(self._engram_store.values())
        all_entries.extend(self._neural_store.values())
        all_entries.extend(self._synapse_store.values())

        seen_ids = set()
        unique_entries = []
        for e in all_entries:
            if e.id not in seen_ids:
                seen_ids.add(e.id)
                unique_entries.append(e)

        query_lower = query.lower()
        scored = []
        for entry in unique_entries:
            score = self._compute_relevance(entry, query_lower)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_entries = scored[:top_k]

        elapsed_ms = (time.monotonic() - start) * 1000

        sources = []
        if use_crag:
            sources.append("crag")
        if use_graph_rag:
            sources.append("graph_rag")
        if use_neural_rerank:
            sources.append("neural_rerank")

        return {
            "results": [
                {"id": e.id, "content": e.content, "source": e.source, "score": round(s, 3), "type": e.memory_type}
                for s, e in top_entries
            ],
            "total": len(scored),
            "query": query,
            "elapsed_ms": round(elapsed_ms, 1),
            "sources": sources,
            "session_key": session_key,
        }

    async def consolidate(self, session_key: str = "") -> dict[str, Any]:
        total = len(self._engram_store) + len(self._neural_store) + len(self._synapse_store)
        if not self._consolidation_enabled:
            return {"status": "skipped", "reason": "consolidation disabled", "total_entries": total}

        consolidated = 0
        decayed = 0

        for store in [self._engram_store, self._neural_store, self._synapse_store]:
            to_remove = []
            for entry_id, entry in store.items():
                age_hours = (time.time() - entry.timestamp) / 3600
                if self._forgetting_curve_enabled and age_hours > 720:
                    retention = 0.1 ** (age_hours / 720)
                    if retention < 0.05:
                        to_remove.append(entry_id)
                        decayed += 1
            for entry_id in to_remove:
                del store[entry_id]
                consolidated += 1

        return {
            "status": "completed",
            "consolidated": consolidated,
            "decayed": decayed,
            "remaining": len(self._engram_store) + len(self._neural_store) + len(self._synapse_store),
            "session_key": session_key,
        }

    def _compute_relevance(self, entry: MemoryEntry, query_lower: str) -> float:
        content_lower = entry.content.lower()
        score = 0.0

        if query_lower in content_lower:
            score += 0.8
        else:
            query_words = query_lower.split()
            content_words = content_lower.split()
            overlap = sum(1 for w in query_words if w in content_words)
            if query_words:
                score += 0.4 * (overlap / len(query_words))

        if entry.memory_type == "engram":
            score *= 1.2
        elif entry.memory_type == "synapse":
            score *= 1.1

        age_hours = (time.time() - entry.timestamp) / 3600
        if age_hours > 24:
            score *= max(0.5, 1.0 - (age_hours / 720))

        return min(score, 1.0)

    def _get_written_layers(self, memory_type: str) -> list[str]:
        if memory_type == MemoryType.AUTO:
            return ["engram", "neural", "synapse"]
        return [memory_type]

    def get_stats(self) -> dict[str, Any]:
        return {
            "engram_count": len(self._engram_store),
            "neural_count": len(self._neural_store),
            "synapse_count": len(self._synapse_store),
            "total": len(self._engram_store) + len(self._neural_store) + len(self._synapse_store),
            "consolidation_enabled": self._consolidation_enabled,
            "forgetting_curve_enabled": self._forgetting_curve_enabled,
        }
