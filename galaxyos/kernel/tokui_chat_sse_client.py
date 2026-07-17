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
from enum import Enum

logger = logging.getLogger(__name__)


class TransportMode(str, Enum):
    SSE = "sse"
    WEBSOCKET = "websocket"


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
    DEFAULT_WS_PATH = "/ws"
    RECONNECT_MAX = 3
    RECONNECT_DELAY_BASE = 2.0

    def __init__(self, host: str = "", port: int = 0, ws_path: str = ""):
        self._host = host or self.DEFAULT_SSE_HOST
        self._port = port or self.DEFAULT_SSE_PORT
        self._ws_path = ws_path or self.DEFAULT_WS_PATH
        self._connected = False
        self._transport: TransportMode = TransportMode.SSE
        self._session = None
        self._ws = None
        self._listen_task: Optional[asyncio.Task] = None
        self._handlers: Dict[str, Callable] = {}
        self._reconnect_count = 0

    async def connect(self) -> bool:
        connected = await self._try_sse_connect()
        if connected:
            self._transport = TransportMode.SSE
            self._connected = True
            self._start_listen_loop()
            return True

        logger.info("SSE connect failed, trying WebSocket fallback")
        connected = await self._try_ws_connect()
        if connected:
            self._transport = TransportMode.WEBSOCKET
            self._connected = True
            self._start_listen_loop()
            return True

        self._connected = False
        return False

    async def _try_sse_connect(self) -> bool:
        try:
            import aiohttp
            self._session = aiohttp.ClientSession()
            url = f"http://{self._host}:{self._port}/sse"
            logger.info(f"SSE client connecting to {url}")
            return True
        except Exception as e:
            logger.warning(f"SSE connect failed: {e}")
            return False

    async def _try_ws_connect(self) -> bool:
        try:
            import aiohttp
            if not self._session:
                self._session = aiohttp.ClientSession()
            ws_url = f"ws://{self._host}:{self._port}{self._ws_path}"
            self._ws = await self._session.ws_connect(ws_url)
            logger.info(f"WebSocket client connected to {ws_url}")
            return True
        except Exception as e:
            logger.warning(f"WebSocket fallback connect failed: {e}")
            return False

    def _start_listen_loop(self) -> None:
        if self._listen_task and not self._listen_task.done():
            return
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        try:
            while self._connected:
                if self._transport == TransportMode.SSE:
                    await self._listen_sse()
                elif self._transport == TransportMode.WEBSOCKET:
                    await self._listen_ws()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Listen loop error: {e}")
            await self._handle_disconnect()

    async def _listen_sse(self) -> None:
        if not self._session:
            return
        try:
            url = f"http://{self._host}:{self._port}/sse"
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"SSE response status: {resp.status}")
                    return
                async for line in resp.content:
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if not decoded or decoded.startswith(":"):
                        continue
                    if decoded.startswith("data:"):
                        data = decoded[5:].strip()
                        await self._dispatch_event(data)
        except Exception as e:
            logger.debug(f"SSE stream ended: {e}")

    async def _listen_ws(self) -> None:
        if not self._ws:
            return
        try:
            msg = await self._ws.receive(timeout=30.0)
            if msg.type == 1:  # TEXT
                await self._dispatch_event(msg.data)
            elif msg.type in (8, 256):  # CLOSE
                logger.info("WebSocket closed by server")
                await self._handle_disconnect()
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            logger.debug(f"WS receive error: {e}")

    async def _dispatch_event(self, raw_data: str) -> None:
        try:
            parsed = json.loads(raw_data)
            event_type = parsed.get("event", parsed.get("type", ""))

            if event_type == "tokui_chunk":
                chunk = self.parse_chunk_event(raw_data)
                if chunk and "tokui_chunk" in self._handlers:
                    result = self._handlers["tokui_chunk"](chunk)
                    if asyncio.iscoroutine(result):
                        await result

            elif event_type == "tokui_error":
                if "tokui_error" in self._handlers:
                    result = self._handlers["tokui_error"](parsed)
                    if asyncio.iscoroutine(result):
                        await result

            elif event_type == "connection.ack":
                logger.info(f"Received connection.ack: {parsed}")

        except (json.JSONDecodeError, KeyError) as e:
            logger.debug(f"Failed to dispatch event: {e}")

    async def _handle_disconnect(self) -> None:
        self._connected = False
        self._reconnect_count += 1

        if self._reconnect_count > self.RECONNECT_MAX:
            logger.warning(f"Max reconnect attempts ({self.RECONNECT_MAX}) reached")
            return

        backoff = min(self.RECONNECT_DELAY_BASE ** self._reconnect_count, 10)
        logger.info(f"Reconnecting in {backoff:.1f}s (attempt {self._reconnect_count})")
        await asyncio.sleep(backoff)

        reconnected = await self.connect()
        if reconnected:
            self._reconnect_count = 0
            logger.info("Reconnected successfully")

    async def disconnect(self) -> None:
        self._connected = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None

    def on_chunk(self, handler: Callable[[TokuiChunkEvent], Any]) -> None:
        self._handlers["tokui_chunk"] = handler

    def on_error(self, handler: Callable[[Dict[str, Any]], Any]) -> None:
        self._handlers["tokui_error"] = handler

    def parse_chunk_event(self, data: str) -> Optional[TokuiChunkEvent]:
        try:
            parsed = json.loads(data) if isinstance(data, str) else data
            return TokuiChunkEvent(
                stream_id=parsed.get("streamId", parsed.get("stream_id", "")),
                chunk_index=parsed.get("chunkIndex", parsed.get("chunk_index", 0)),
                dsl=parsed.get("dsl", ""),
                is_final=parsed.get("isFinal", parsed.get("is_final", False)),
                workspace_id=parsed.get("workspaceId", parsed.get("workspace_id", "default")),
                component_type=parsed.get("componentType", parsed.get("component_type", "card")),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Failed to parse chunk event: {e}")
            return None

    @property
    def sse_connected(self) -> bool:
        return self._connected

    @property
    def transport_mode(self) -> str:
        return self._transport.value