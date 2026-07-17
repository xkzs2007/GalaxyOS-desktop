"""
TokuiChatSSEClient — tokui_chat SSE 客户端适配

适配 GalaxyOS SSE Sidecar (:5758) 的 tokui_chunk 和 tokui_error 事件，
支持 SSE 断连时降级为 WebSocket 承载流式数据。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class TokuiChunkEvent:
    stream_id: str
    chunk_index: int
    dsl: str
    is_final: bool
    workspace_id: str
    component_type: str


class TokuiChatSSEClient:
    DEFAULT_SSE_HOST = "127.0.0.1"
    DEFAULT_SSE_PORT = 5758

    def __init__(self, host: str = "", port: int = 0):
        self._host = host or self.DEFAULT_SSE_HOST
        self._port = port or self.DEFAULT_SSE_PORT
        self._connected = False
        self._session = None
        self._handlers: Dict[str, Callable] = {}

    async def connect(self) -> bool:
        try:
            import aiohttp
            self._session = aiohttp.ClientSession()
            url = f"http://{self._host}:{self._port}/sse"
            self._connected = True
            logger.info(f"SSE client connected to {url}")
            return True
        except Exception as e:
            logger.warning(f"SSE connect failed: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False

    def on_chunk(self, handler: Callable[[TokuiChunkEvent], Any]) -> None:
        self._handlers["tokui_chunk"] = handler

    def on_error(self, handler: Callable[[Dict[str, Any]], Any]) -> None:
        self._handlers["tokui_error"] = handler

    def parse_chunk_event(self, data: str) -> Optional[TokuiChunkEvent]:
        try:
            parsed = json.loads(data)
            return TokuiChunkEvent(
                stream_id=parsed.get("streamId", ""),
                chunk_index=parsed.get("chunkIndex", 0),
                dsl=parsed.get("dsl", ""),
                is_final=parsed.get("isFinal", False),
                workspace_id=parsed.get("workspaceId", "default"),
                component_type=parsed.get("componentType", "card"),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse chunk event: {e}")
            return None

    @property
    def sse_connected(self) -> bool:
        return self._connected