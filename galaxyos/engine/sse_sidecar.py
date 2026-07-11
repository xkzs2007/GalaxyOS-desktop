"""
SSE Sidecar — GalaxyOS 事件推送服务

支持事件类型：
  - cognitive_result: 认知增强结果推送
  - tokui_chunk: TokUI DSL 分片推送
  - tokui_error: TokUI 渲染错误通知
  - heartbeat: 心跳事件
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5758


@dataclass
class SSEClient:
    client_id: str
    workspace_id: str
    queue: asyncio.Queue
    connected_at: float = field(default_factory=time.time)


class SSESidecar:
    def __init__(self, host: str = "", port: int = 0, auth_token: str = ""):
        self._host = host or DEFAULT_HOST
        self._port = port or DEFAULT_PORT
        self._auth_token = auth_token
        self._clients: Dict[str, SSEClient] = {}
        self._event_count = 0
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info(f"SSE Sidecar starting on {self._host}:{self._port}")

    async def stop(self) -> None:
        self._running = False
        for client in self._clients.values():
            await client.queue.put(None)
        self._clients.clear()
        logger.info("SSE Sidecar stopped")

    def add_client(self, client_id: str, workspace_id: str = "default") -> SSEClient:
        client = SSEClient(
            client_id=client_id,
            workspace_id=workspace_id,
            queue=asyncio.Queue(maxsize=1000),
        )
        self._clients[client_id] = client
        return client

    def remove_client(self, client_id: str) -> None:
        self._clients.pop(client_id, None)

    async def push_event(self, event_type: str, data: Dict[str, Any], workspace_id: str = "") -> int:
        self._event_count += 1
        pushed = 0

        event_payload = json.dumps({
            "event": event_type,
            "data": data,
            "timestamp": time.time(),
        }, ensure_ascii=False)

        for client in list(self._clients.values()):
            if workspace_id and client.workspace_id != workspace_id:
                continue
            try:
                client.queue.put_nowait(event_payload)
                pushed += 1
            except asyncio.QueueFull:
                logger.warning(f"SSE client {client.client_id} queue full, dropping event")

        return pushed

    async def push_tokui_chunk(
        self,
        stream_id: str,
        chunk_index: int,
        dsl: str,
        is_final: bool,
        workspace_id: str = "",
        component_type: str = "",
    ) -> int:
        return await self.push_event("tokui_chunk", {
            "streamId": stream_id,
            "chunkIndex": chunk_index,
            "dsl": dsl,
            "isFinal": is_final,
            "workspaceId": workspace_id,
            "componentType": component_type,
        }, workspace_id=workspace_id)

    async def push_tokui_error(
        self,
        stream_id: str,
        error: str,
        code: str = "TOKUI_RENDER_ERROR",
        workspace_id: str = "",
    ) -> int:
        return await self.push_event("tokui_error", {
            "streamId": stream_id,
            "error": error,
            "code": code,
            "workspaceId": workspace_id,
        }, workspace_id=workspace_id)

    async def push_cognitive_result(self, data: Dict[str, Any], workspace_id: str = "") -> int:
        return await self.push_event("cognitive_result", data, workspace_id=workspace_id)

    async def push_heartbeat(self) -> int:
        return await self.push_event("heartbeat", {
            "status": "alive",
            "clients": len(self._clients),
            "uptime_s": time.time() - self._start_time if hasattr(self, '_start_time') else 0,
        })

    def get_stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "host": self._host,
            "port": self._port,
            "clients": len(self._clients),
            "total_events": self._event_count,
        }