"""
GalaxyOS 容错降级策略 — TokUI 全链路容错

10 种异常场景的降级矩阵：
  1. DSL 解析失败 → div.tokui-unknown 显示原始 DSL
  2. 未注册组件类型 → div.tokui-unknown + 灰色占位框
  3. SSE 连接中断 → 自动重连（指数退避，最多 3 次）
  4. TokUIBuilder 生成超时 → 纯文本回复
  5. tokui_render MCP 工具调用失败 → 纯文本回复
  6. 主题同步失败 → 使用上一次同步的主题
  7. 事件处理器未注册 → 组件渲染但点击无响应
  8. DAG 上下文树数据量过大 → 懒加载
  9. 认知面板布局冲突 → 自动调整为浮动窗口
  10. Python 内核崩溃 → 钩子返回 allowed=True + warning
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DegradationLevel(str, Enum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


@dataclass
class DegradationEvent:
    scenario: str
    level: DegradationLevel
    fallback: str
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)


class TokUIDegradationManager:
    MAX_DSL_SIZE = 100_000
    MAX_DAG_NODES = 500
    SSE_RECONNECT_MAX = 3
    SSE_RECONNECT_DELAY_BASE = 2.0
    BUILD_TIMEOUT_S = 5.0
    MCP_CALL_TIMEOUT_S = 30.0

    def __init__(self):
        self._events: List[DegradationEvent] = []
        self._reconnect_counts: Dict[str, int] = {}

    def handle_dsl_parse_failure(self, dsl: str, error: str) -> Dict[str, Any]:
        self._record("dsl_parse_failure", DegradationLevel.PARTIAL, "div.tokui-unknown", {"error": error})
        return {
            "status": "degraded",
            "fallback": "raw_dsl",
            "display": dsl[:500],
            "error": error,
        }

    def handle_unknown_component(self, component_type: str) -> Dict[str, Any]:
        self._record("unknown_component", DegradationLevel.PARTIAL, "div.tokui-unknown", {"type": component_type})
        return {
            "status": "degraded",
            "fallback": "unknown_component",
            "component_type": component_type,
            "display": f"未知组件: [{component_type}]",
        }

    def handle_sse_disconnect(self, stream_id: str) -> Dict[str, Any]:
        count = self._reconnect_counts.get(stream_id, 0) + 1
        self._reconnect_counts[stream_id] = count

        if count > self.SSE_RECONNECT_MAX:
            self._record("sse_disconnect", DegradationLevel.FULL, "stop_reconnect", {"stream_id": stream_id, "attempts": count})
            return {"status": "degraded", "fallback": "stop_reconnect", "attempts": count}

        backoff = min(self.SSE_RECONNECT_DELAY_BASE ** count, 10)
        self._record("sse_disconnect", DegradationLevel.PARTIAL, "auto_reconnect", {"stream_id": stream_id, "attempts": count, "backoff_s": backoff})
        return {"status": "reconnecting", "fallback": "auto_reconnect", "attempts": count, "backoff_s": backoff}

    def handle_build_timeout(self, content: str = "") -> Dict[str, Any]:
        self._record("build_timeout", DegradationLevel.FULL, "plain_text", {"content_length": len(content)})
        return {
            "status": "degraded",
            "fallback": "plain_text",
            "content": content or "[p 回复生成超时]",
        }

    def handle_mcp_call_failure(self, tool_name: str, error: str) -> Dict[str, Any]:
        self._record("mcp_call_failure", DegradationLevel.FULL, "plain_text", {"tool": tool_name, "error": error})
        return {
            "status": "degraded",
            "fallback": "plain_text",
            "tool_name": tool_name,
            "error": error,
        }

    def handle_theme_sync_failure(self, error: str) -> Dict[str, Any]:
        self._record("theme_sync_failure", DegradationLevel.PARTIAL, "last_synced_theme", {"error": error})
        return {"status": "degraded", "fallback": "last_synced_theme", "error": error}

    def handle_handler_not_registered(self, handler_name: str) -> Dict[str, Any]:
        self._record("handler_not_registered", DegradationLevel.PARTIAL, "no_response", {"handler": handler_name})
        return {
            "status": "degraded",
            "fallback": "no_response",
            "handler_name": handler_name,
            "warning": f"事件处理器未注册: {handler_name}",
        }

    def handle_dag_oversized(self, node_count: int, dsl_size: int) -> Dict[str, Any]:
        self._record("dag_oversized", DegradationLevel.PARTIAL, "lazy_load", {"nodes": node_count, "dsl_size": dsl_size})
        return {
            "status": "degraded",
            "fallback": "lazy_load",
            "visible_levels": 1,
            "total_nodes": node_count,
        }

    def handle_panel_layout_conflict(self) -> Dict[str, Any]:
        self._record("panel_layout_conflict", DegradationLevel.PARTIAL, "floating_window", {})
        return {"status": "degraded", "fallback": "floating_window"}

    def handle_kernel_crash(self, hook_name: str) -> Dict[str, Any]:
        self._record("kernel_crash", DegradationLevel.PARTIAL, "allowed_with_warning", {"hook": hook_name})
        return {"allowed": True, "warning": "GalaxyOS kernel not running"}

    def handle_eui_init_failure(self, error: str) -> Dict[str, Any]:
        self._record("eui_init_failure", DegradationLevel.FULL, "webview_dom", {"error": error})
        return {
            "status": "degraded",
            "fallback": "webview_dom",
            "native_render_available": False,
            "error": error,
        }

    def handle_dsl_bridge_failure(self, unsupported: list, error: str = "") -> Dict[str, Any]:
        self._record("dsl_bridge_failure", DegradationLevel.PARTIAL, "skip_unsupported", {"unsupported": unsupported, "error": error})
        return {
            "status": "degraded",
            "fallback": "skip_unsupported",
            "unsupported_components": unsupported,
            "error": error,
        }

    def handle_surface_crash(self, surface_id: str, error: str = "") -> Dict[str, Any]:
        self._record("surface_crash", DegradationLevel.PARTIAL, "rebuild_surface", {"surface_id": surface_id, "error": error})
        return {
            "status": "degraded",
            "fallback": "rebuild_surface",
            "surface_id": surface_id,
            "error": error,
        }

    def handle_ffi_timeout(self, surface_id: str, timeout_ms: float) -> Dict[str, Any]:
        self._record("ffi_timeout", DegradationLevel.PARTIAL, "webview_dom", {"surface_id": surface_id, "timeout_ms": timeout_ms})
        return {
            "status": "degraded",
            "fallback": "webview_dom",
            "surface_id": surface_id,
            "timeout_ms": timeout_ms,
        }

    def check_dsl_size(self, dsl: str) -> bool:
        return len(dsl) <= self.MAX_DSL_SIZE

    def check_dag_size(self, node_count: int) -> bool:
        return node_count <= self.MAX_DAG_NODES

    def _record(self, scenario: str, level: DegradationLevel, fallback: str, details: Dict[str, Any]) -> None:
        event = DegradationEvent(scenario=scenario, level=level, fallback=fallback, details=details)
        self._events.append(event)
        if len(self._events) > 1000:
            self._events = self._events[-500:]
        logger.warning(f"TokUI degradation: {scenario} → {fallback} (level={level.value})")

    def get_stats(self) -> Dict[str, Any]:
        level_counts = {}
        for e in self._events:
            level_counts[e.level.value] = level_counts.get(e.level.value, 0) + 1
        return {
            "total_events": len(self._events),
            "level_counts": level_counts,
            "recent_events": [
                {"scenario": e.scenario, "level": e.level.value, "fallback": e.fallback}
                for e in self._events[-10:]
            ],
        }
