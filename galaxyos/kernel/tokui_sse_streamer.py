"""
TokUISSEStreamer — TokUI DSL 分片推送管理器

管理 DSL 分片推送和 streamId 生命周期，将 PyTokUIBuilder 生成的 DSL
按 max_chunk_size 分片，通过 SSE Sidecar 推送 tokui_chunk 事件到前端。

事件格式：
  tokui_chunk: {streamId, chunkIndex, dsl, isFinal, workspaceId, componentType}
  tokui_error: {streamId, error, code}
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHUNK_SIZE = 32768
SSE_SIDECAR_HOST = "127.0.0.1"
SSE_SIDECAR_PORT = 5758


@dataclass
class StreamSession:
    stream_id: str
    workspace_id: str
    component_type: str = ""
    chunk_index: int = 0
    total_chunks: int = 0
    created_at: float = field(default_factory=time.time)
    is_closed: bool = False


class TokUISSEStreamer:
    def __init__(
        self,
        sidecar_host: str = "",
        sidecar_port: int = 0,
        max_chunk_size: int = 0,
        auth_token: str = "",
    ):
        self._sidecar_host = sidecar_host or SSE_SIDECAR_HOST
        self._sidecar_port = sidecar_port or SSE_SIDECAR_PORT
        self._max_chunk_size = max_chunk_size or DEFAULT_MAX_CHUNK_SIZE
        self._auth_token = auth_token
        self._sessions: Dict[str, StreamSession] = {}
        self._push_count: int = 0
        self._error_count: int = 0

    def create_stream(self, workspace_id: str, component_type: str = "") -> str:
        stream_id = str(uuid.uuid4())
        self._sessions[stream_id] = StreamSession(
            stream_id=stream_id,
            workspace_id=workspace_id,
            component_type=component_type,
        )
        return stream_id

    async def push(
        self,
        dsl: str,
        stream_id: str,
        workspace_id: str,
        component_type: str = "",
    ) -> Dict[str, Any]:
        if not stream_id or stream_id not in self._sessions:
            stream_id = self.create_stream(workspace_id, component_type)

        session = self._sessions[stream_id]
        chunks = self._split_dsl(dsl)
        total_chunks = len(chunks)
        pushed = 0

        for i, chunk in enumerate(chunks):
            is_final = (i == total_chunks - 1)
            result = await self.push_chunk(
                chunk=chunk,
                stream_id=stream_id,
                chunk_index=session.chunk_index + i,
                is_final=is_final,
                workspace_id=workspace_id,
                component_type=component_type,
            )
            if result.get("status") == "pushed":
                pushed += 1

        session.chunk_index += total_chunks
        session.total_chunks += total_chunks

        return {
            "status": "pushed",
            "stream_id": stream_id,
            "total_chunks": total_chunks,
            "pushed": pushed,
            "workspace_id": workspace_id,
            "component_type": component_type,
        }

    async def push_chunk(
        self,
        chunk: str,
        stream_id: str,
        chunk_index: int,
        is_final: bool,
        workspace_id: str,
        component_type: str = "",
    ) -> Dict[str, Any]:
        event_data = {
            "streamId": stream_id,
            "chunkIndex": chunk_index,
            "dsl": chunk,
            "isFinal": is_final,
            "workspaceId": workspace_id,
            "componentType": component_type,
        }

        try:
            result = await self._post_to_sidecar("tokui_chunk", event_data)
            self._push_count += 1

            if is_final and stream_id in self._sessions:
                self._sessions[stream_id].is_closed = True

            return {"status": "pushed", "stream_id": stream_id, "chunk_index": chunk_index}

        except Exception as e:
            self._error_count += 1
            logger.warning(f"TokUI SSE push failed: stream_id={stream_id}, chunk={chunk_index}, error={e}")
            return {"status": "failed", "stream_id": stream_id, "chunk_index": chunk_index, "error": str(e)}

    async def push_error(
        self,
        stream_id: str,
        error: str,
        code: str = "TOKUI_RENDER_ERROR",
        workspace_id: str = "",
    ) -> Dict[str, Any]:
        event_data = {
            "streamId": stream_id,
            "error": error,
            "code": code,
            "workspaceId": workspace_id,
        }

        try:
            await self._post_to_sidecar("tokui_error", event_data)
            return {"status": "pushed", "stream_id": stream_id}
        except Exception as e:
            logger.warning(f"TokUI SSE error push failed: {e}")
            return {"status": "failed", "error": str(e)}

    async def close_stream(self, stream_id: str, workspace_id: str = "") -> Dict[str, Any]:
        if stream_id not in self._sessions:
            return {"status": "not_found", "stream_id": stream_id}

        session = self._sessions[stream_id]
        result = await self.push_chunk(
            chunk="",
            stream_id=stream_id,
            chunk_index=session.chunk_index,
            is_final=True,
            workspace_id=workspace_id or session.workspace_id,
            component_type=session.component_type,
        )
        session.is_closed = True
        return result

    def get_session(self, stream_id: str) -> Optional[Dict[str, Any]]:
        if stream_id not in self._sessions:
            return None
        s = self._sessions[stream_id]
        return {
            "stream_id": s.stream_id,
            "workspace_id": s.workspace_id,
            "component_type": s.component_type,
            "chunk_index": s.chunk_index,
            "total_chunks": s.total_chunks,
            "is_closed": s.is_closed,
            "age_s": round(time.time() - s.created_at, 1),
        }

    def cleanup_sessions(self, max_age_s: float = 3600) -> int:
        now = time.time()
        to_remove = [
            sid for sid, s in self._sessions.items()
            if (now - s.created_at > max_age_s) or s.is_closed
        ]
        for sid in to_remove:
            del self._sessions[sid]
        return len(to_remove)

    def _split_dsl(self, dsl: str) -> List[str]:
        if len(dsl) <= self._max_chunk_size:
            return [dsl]

        chunks = []
        pos = 0
        while pos < len(dsl):
            if pos + self._max_chunk_size >= len(dsl):
                chunks.append(dsl[pos:])
                break

            cut = pos + self._max_chunk_size
            boundary = dsl.rfind("][", pos, cut)
            if boundary > pos:
                cut = boundary + 1
            else:
                boundary = dsl.rfind("]", pos, cut)
                if boundary > pos:
                    cut = boundary + 1

            chunks.append(dsl[pos:cut])
            pos = cut

        return chunks

    async def _post_to_sidecar(self, event_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        import aiohttp

        url = f"http://{self._sidecar_host}:{self._sidecar_port}/events"
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        payload = {"event": event_type, "data": data}

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"status": "error", "http_status": resp.status}

    def get_stats(self) -> Dict[str, Any]:
        active = sum(1 for s in self._sessions.values() if not s.is_closed)
        return {
            "active_streams": active,
            "total_sessions": len(self._sessions),
            "total_pushes": self._push_count,
            "total_errors": self._error_count,
            "sidecar_host": self._sidecar_host,
            "sidecar_port": self._sidecar_port,
            "max_chunk_size": self._max_chunk_size,
        }