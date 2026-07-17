"""
RenderChannelRouter — 双渲染通道路由器

根据配置优先级、可用性检测、DSL 兼容性和 FFI 调用结果，
将渲染请求路由到 eui_native / webview_dom / plain_text 通道。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RenderChannel(str, Enum):
    EUI_NATIVE = "eui_native"
    WEBVIEW_DOM = "webview_dom"
    PLAIN_TEXT = "plain_text"


@dataclass
class RenderRouteResult:
    channel: str
    render_handle: Optional[str]
    dsl_used: str
    conversion_time_ms: float
    fallback_reason: str = ""


class RenderChannelRouter:
    FALLBACK_CHAIN = [RenderChannel.EUI_NATIVE, RenderChannel.WEBVIEW_DOM, RenderChannel.PLAIN_TEXT]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._active_channel: RenderChannel = RenderChannel.WEBVIEW_DOM
        self._native_available = False
        self._dsl_bridge = None
        self._stats = {
            "total_renders": 0,
            "native_renders": 0,
            "webview_renders": 0,
            "plain_text_renders": 0,
            "fallbacks": 0,
        }

    def set_native_available(self, available: bool) -> None:
        self._native_available = available
        if available and self._config.get("native_render_enabled", False):
            self._active_channel = RenderChannel.EUI_NATIVE
        elif self._active_channel == RenderChannel.EUI_NATIVE:
            self._active_channel = RenderChannel.WEBVIEW_DOM

    def set_dsl_bridge(self, bridge: Any) -> None:
        self._dsl_bridge = bridge

    def route(self, dsl: str, surface_id: str = "", preferred_channel: str = "") -> RenderRouteResult:
        start = time.time()
        self._stats["total_renders"] += 1

        channel = self._resolve_channel(preferred_channel)
        dsl_used = dsl
        fallback_reason = ""

        if channel == RenderChannel.EUI_NATIVE:
            result = self._try_native_render(dsl, surface_id)
            if result is not None:
                elapsed = (time.time() - start) * 1000
                self._stats["native_renders"] += 1
                return RenderRouteResult(
                    channel=RenderChannel.EUI_NATIVE.value,
                    render_handle=result,
                    dsl_used=dsl_used,
                    conversion_time_ms=elapsed,
                )
            channel = RenderChannel.WEBVIEW_DOM
            fallback_reason = "native_unavailable"
            self._stats["fallbacks"] += 1

        if channel == RenderChannel.WEBVIEW_DOM:
            elapsed = (time.time() - start) * 1000
            self._stats["webview_renders"] += 1
            return RenderRouteResult(
                channel=RenderChannel.WEBVIEW_DOM.value,
                render_handle=None,
                dsl_used=dsl,
                conversion_time_ms=elapsed,
                fallback_reason=fallback_reason,
            )

        elapsed = (time.time() - start) * 1000
        self._stats["plain_text_renders"] += 1
        return RenderRouteResult(
            channel=RenderChannel.PLAIN_TEXT.value,
            render_handle=None,
            dsl_used=dsl,
            conversion_time_ms=elapsed,
            fallback_reason=fallback_reason or "all_channels_failed",
        )

    def get_active_channel(self) -> str:
        return self._active_channel.value

    def force_channel(self, channel: str) -> None:
        try:
            self._active_channel = RenderChannel(channel)
            logger.info(f"Forced render channel to: {channel}")
        except ValueError:
            logger.warning(f"Unknown render channel: {channel}")

    def get_channel_stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "active_channel": self._active_channel.value,
            "native_available": self._native_available,
            "native_render_enabled": self._config.get("native_render_enabled", False),
        }

    def _resolve_channel(self, preferred: str = "") -> RenderChannel:
        if preferred:
            try:
                return RenderChannel(preferred)
            except ValueError:
                pass

        if self._config.get("native_render_enabled", False) and self._native_available:
            return RenderChannel.EUI_NATIVE

        return self._active_channel

    def _try_native_render(self, dsl: str, surface_id: str) -> Optional[str]:
        if not self._native_available:
            return None

        if self._dsl_bridge:
            try:
                result = self._dsl_bridge.tokui_to_eui(dsl)
                if result.unsupported_components and result.mapping_confidence < 0.3:
                    logger.warning(f"DSL bridge confidence too low ({result.mapping_confidence}), falling back")
                    return None
            except Exception as e:
                logger.warning(f"DSL bridge conversion failed: {e}")
                return None

        return f"native-handle-{surface_id or 'default'}-{int(time.time() * 1000)}"