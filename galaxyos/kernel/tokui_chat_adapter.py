"""
TokuiChatAdapter — tokui_chat 聊天前端加载器

根据 IntegrationConfig.chat_frontend 决定加载 tokui_chat 还是 JiuwenSwarm ChatPanel。
加载超时自动回退到 JiuwenSwarm ChatPanel。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class TokuiChatLoadResult:
    status: str  # active / fallback / unavailable
    chat_frontend: str
    sse_connected: bool = False
    cognitive_panel_visible: bool = False
    error: Optional[str] = None


class TokuiChatAdapter:
    LOAD_TIMEOUT = 5.0

    def __init__(self, config_manager=None, sse_client=None, cognitive_injector=None):
        self._config_manager = config_manager
        self._sse_client = sse_client
        self._cognitive_injector = cognitive_injector
        self._loaded = False
        self._active_frontend = "swarm"
        self._load_result: Optional[TokuiChatLoadResult] = None

    async def load(self) -> TokuiChatLoadResult:
        config = self._config_manager.get() if self._config_manager else None
        chat_frontend = config.chat_frontend if config else "swarm"

        if chat_frontend != "tokui_chat":
            logger.info(f"Chat frontend config: {chat_frontend}, using JiuwenSwarm ChatPanel")
            self._load_result = TokuiChatLoadResult(
                status="active" if chat_frontend == "swarm" else "fallback",
                chat_frontend="swarm",
            )
            self._active_frontend = "swarm"
            self._loaded = True
            return self._load_result

        try:
            result = await asyncio.wait_for(self._load_tokui_chat(), timeout=self.LOAD_TIMEOUT)
            self._load_result = result
            self._loaded = True
            return result
        except asyncio.TimeoutError:
            logger.warning(f"tokui_chat load timed out after {self.LOAD_TIMEOUT}s, falling back")
            return self._fallback("Load timeout")
        except Exception as e:
            logger.warning(f"tokui_chat load failed: {e}, falling back")
            return self._fallback(str(e))

    async def _load_tokui_chat(self) -> TokuiChatLoadResult:
        sse_connected = False
        if self._sse_client:
            try:
                await self._sse_client.connect()
                sse_connected = True
            except Exception as e:
                logger.warning(f"SSE client connect failed: {e}")

        cognitive_visible = False
        if self._cognitive_injector:
            try:
                await self._cognitive_injector.inject()
                cognitive_visible = True
            except Exception as e:
                logger.warning(f"Cognitive panel inject failed: {e}")

        self._active_frontend = "tokui_chat"
        return TokuiChatLoadResult(
            status="active",
            chat_frontend="tokui_chat",
            sse_connected=sse_connected,
            cognitive_panel_visible=cognitive_visible,
        )

    def _fallback(self, error: str) -> TokuiChatLoadResult:
        self._active_frontend = "swarm"
        return TokuiChatLoadResult(
            status="fallback",
            chat_frontend="swarm",
            error=error,
        )

    async def unload(self) -> None:
        if self._sse_client:
            await self._sse_client.disconnect()
        self._loaded = False

    def get_integration_status(self) -> Dict[str, Any]:
        return {
            "loaded": self._loaded,
            "active_frontend": self._active_frontend,
            "status": self._load_result.status if self._load_result else "unavailable",
            "sse_connected": self._load_result.sse_connected if self._load_result else False,
            "cognitive_panel_visible": self._load_result.cognitive_panel_visible if self._load_result else False,
        }

    def get_fallback_chat_panel(self) -> str:
        return "swarm"