"""
CognitiveDataPusher — 通过 JiuwenSwarm Gateway WebSocket 推送认知面板数据

在 Agent 对话完成后，将记忆状态、R-CCAM 进度、DAG 上下文等认知数据
通过 Gateway 的 WebSocket 通道推送到前端，实现认知面板实时更新。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CognitiveDataPusher:
    GATEWAY_WS_HOST = "127.0.0.1"
    GATEWAY_WS_PORT = 19000
    GATEWAY_WS_PATH = "/ws"

    def __init__(self, host: str = "", port: int = 0, path: str = ""):
        self._host = host or self.GATEWAY_WS_HOST
        self._port = port or self.GATEWAY_WS_PORT
        self._path = path or self.GATEWAY_WS_PATH
        self._ws = None
        self._session = None
        self._connected = False
        self._push_count = 0

    async def connect(self) -> bool:
        try:
            import aiohttp
            if not self._session:
                self._session = aiohttp.ClientSession()
            ws_url = f"ws://{self._host}:{self._port}{self._path}"
            self._ws = await self._session.ws_connect(ws_url)
            self._connected = True
            logger.info(f"CognitiveDataPusher connected to {ws_url}")
            return True
        except Exception as e:
            logger.warning(f"CognitiveDataPusher connect failed: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False

    async def push_cognitive_update(self, context: Dict[str, Any]) -> bool:
        if not self._connected:
            connected = await self.connect()
            if not connected:
                return False

        event = self._build_cognitive_event(context)
        return await self._send(event)

    async def push_memory_update(self, memory_data: Dict[str, Any]) -> bool:
        event = {
            "type": "galaxyos-cognitive-update",
            "tab": "memory",
            "data": memory_data,
            "timestamp": int(time.time() * 1000),
        }
        return await self._send(event)

    async def push_rccam_update(self, rccam_data: Dict[str, Any]) -> bool:
        event = {
            "type": "galaxyos-cognitive-update",
            "tab": "rccam",
            "data": rccam_data,
            "timestamp": int(time.time() * 1000),
        }
        return await self._send(event)

    async def push_dag_update(self, dag_data: Dict[str, Any]) -> bool:
        event = {
            "type": "galaxyos-cognitive-update",
            "tab": "dag",
            "data": dag_data,
            "timestamp": int(time.time() * 1000),
        }
        return await self._send(event)

    def _build_cognitive_event(self, context: Dict[str, Any]) -> Dict[str, Any]:
        event_type = context.get("event", context.get("type", "unknown"))
        return {
            "type": "galaxyos-cognitive-update",
            "source": "galaxyos-extension",
            "trigger_event": event_type,
            "tabs": {
                "memory": {"updated": True},
                "rccam": {"updated": True},
                "dag": {"updated": True},
            },
            "session_id": context.get("session_id", ""),
            "timestamp": int(time.time() * 1000),
        }

    async def _send(self, event: Dict[str, Any]) -> bool:
        if not self._ws:
            return False
        try:
            await self._ws.send_json(event)
            self._push_count += 1
            logger.debug(f"Pushed cognitive update #{self._push_count}: {event.get('type', 'unknown')}")
            return True
        except Exception as e:
            logger.warning(f"WS send failed: {e}")
            self._connected = False
            return False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def push_count(self) -> int:
        return self._push_count