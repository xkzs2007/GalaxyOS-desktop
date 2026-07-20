"""
WorkflowHookDispatcher — agent-core 工作流事件 → GalaxyOS 钩子分发器

将 agent-core 工作流事件（on_workflow_start, on_workflow_step, on_workflow_end, on_tool_call, on_agent_reply）
分发到 GalaxyOS 钩子系统，替代原 Agent Studio 9 钩子映射。

事件映射：
  on_workflow_start → before_agent_reply (R-CCAM 注入)
  on_workflow_step  → before_tool_call (工具调用前校验)
  on_workflow_end   → after_agent_reply (记忆双写)
  on_tool_call      → after_tool_call (工具调用后处理)
  on_agent_reply    → before_agent_reply (认知上下文注入)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    allowed: bool = True
    warning: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


WORKFLOW_HOOK_MAP = {
    "on_workflow_start": "before_agent_reply",
    "on_workflow_step": "before_tool_call",
    "on_workflow_end": "after_agent_reply",
    "on_tool_call": "after_tool_call",
    "on_agent_reply": "before_agent_reply",
}


class WorkflowHookDispatcher:
    def __init__(self, hook_manager=None):
        self._hook_manager = hook_manager
        self._handlers: Dict[str, List[Callable]] = {}
        self._kernel_available = True

    def set_kernel_available(self, available: bool) -> None:
        self._kernel_available = available

    def register_handler(self, event_name: str, handler: Callable) -> None:
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        self._handlers[event_name].append(handler)

    async def on_workflow_start(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("on_workflow_start", context)

    async def on_workflow_step(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("on_workflow_step", context)

    async def on_workflow_end(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("on_workflow_end", context)

    async def on_tool_call(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("on_tool_call", context)

    async def on_agent_reply(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("on_agent_reply", context)

    async def _dispatch(self, event_name: str, context: Dict[str, Any]) -> HookResult:
        if not self._kernel_available:
            logger.warning(f"Kernel unavailable, skipping hook dispatch for {event_name}")
            return HookResult(allowed=True, warning="kernel unavailable")

        galaxyos_hook = WORKFLOW_HOOK_MAP.get(event_name)
        if not galaxyos_hook:
            logger.warning(f"Unknown workflow event: {event_name}")
            return HookResult(allowed=True, warning=f"Unknown event: {event_name}")

        results = []

        if self._hook_manager and hasattr(self._hook_manager, "dispatch"):
            try:
                result = await self._hook_manager.dispatch(galaxyos_hook, context)
                if isinstance(result, HookResult):
                    results.append(result)
                elif isinstance(result, dict):
                    results.append(HookResult(
                        allowed=result.get("allowed", True),
                        warning=result.get("warning", ""),
                        data=result.get("data", {}),
                    ))
            except Exception as e:
                logger.warning(f"Hook manager dispatch error for {event_name}: {e}")
                return HookResult(allowed=True, warning=str(e))

        handlers = self._handlers.get(event_name, [])
        for handler in handlers:
            try:
                result = await handler(context)
                if isinstance(result, HookResult):
                    results.append(result)
                elif isinstance(result, dict):
                    results.append(HookResult(
                        allowed=result.get("allowed", True),
                        warning=result.get("warning", ""),
                        data=result.get("data", {}),
                    ))
            except Exception as e:
                logger.warning(f"Handler error for {event_name}: {e}")
                results.append(HookResult(allowed=True, warning=str(e)))

        if not results:
            return HookResult(allowed=True)

        final_allowed = all(r.allowed for r in results)
        warnings = [r.warning for r in results if r.warning]
        merged_data = {}
        for r in results:
            merged_data.update(r.data)

        return HookResult(
            allowed=final_allowed,
            warning="; ".join(warnings) if warnings else "",
            data=merged_data,
        )

    def get_hook_status(self) -> Dict[str, Any]:
        return {
            "kernel_available": self._kernel_available,
            "registered_events": list(self._handlers.keys()),
            "handler_counts": {k: len(v) for k, v in self._handlers.items()},
            "hook_map": WORKFLOW_HOOK_MAP,
        }
