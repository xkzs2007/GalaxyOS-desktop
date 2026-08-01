"""
TokUIStreamer — TokUI DSL SSE 推送桥接器

将 TokUI DSL 事件通过 SSE 通道推送到 C++ 桌面壳。
push() 内部调用 mcp_server.send_sse_event() 推送 tokui_dsl 事件。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TokUIStreamer:
    def __init__(self, mcp_server=None):
        self._mcp_server = mcp_server
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._push_count: int = 0

    def set_mcp_server(self, mcp_server) -> None:
        self._mcp_server = mcp_server

    def subscribe(self, workspace_id: str = "") -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        key = workspace_id or "default"
        self._subscribers[key] = queue
        return queue

    def unsubscribe(self, workspace_id: str = "") -> None:
        key = workspace_id or "default"
        self._subscribers.pop(key, None)

    async def push(
        self,
        dsl: str,
        stream_id: str = "",
        workspace_id: str = "",
        component_type: str = "",
    ) -> Dict[str, Any]:
        self._push_count += 1
        result_stream_id = stream_id or str(uuid.uuid4())

        event_data = {
            "type": "tokui_dsl",
            "dsl": dsl,
            "stream_id": result_stream_id,
            "workspace_id": workspace_id,
            "component_type": component_type,
            "timestamp": time.time(),
            "push_index": self._push_count,
        }

        if self._mcp_server and hasattr(self._mcp_server, "send_sse_event"):
            try:
                await self._mcp_server.send_sse_event("tokui_dsl", event_data)
            except Exception as e:
                logger.warning(f"TokUI SSE push via mcp_server failed: {e}")

        for key, queue in self._subscribers.items():
            if not workspace_id or key == workspace_id or key == "default":
                try:
                    queue.put_nowait(event_data)
                except Exception as e:
                    logger.warning(f"TokUI queue push failed for {key}: {e}")

        return {
            "status": "pushed",
            "stream_id": result_stream_id,
            "total_chunks": 1,
            "push_index": self._push_count,
        }

    def get_stats(self) -> Dict[str, Any]:
        return {
            "push_count": self._push_count,
            "subscribers": len(self._subscribers),
            "subscriber_keys": list(self._subscribers.keys()),
        }
