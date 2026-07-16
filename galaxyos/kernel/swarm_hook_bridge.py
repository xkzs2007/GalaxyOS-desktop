"""
SwarmHookBridge — JiuwenSwarm AsyncCallbackFramework → GalaxyOS 生命周期钩子桥接

将 JiuwenSwarm 的 hook 事件（gateway_started, before_chat_request, memory_before_chat 等）
桥接到 GalaxyOS 的生命周期钩子系统，实现双源事件共存。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class HookResult:
    allowed: bool = True
    warning: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


SWARM_HOOK_TO_GALAXYOS = {
    "gateway_started": "gateway_start",
    "gateway_stopped": "gateway_stop",
    "before_chat_request": "before_agent_reply",
    "after_chat_request": "after_agent_reply",
    "memory_before_chat": "before_tool_call",
    "memory_after_chat": "after_tool_call",
    "before_system_prompt_build": "before_agent_reply",
    "agent_server_started": "gateway_start",
    "agent_server_stopped": "gateway_stop",
}


class SwarmHookBridge:
    def __init__(self, hook_manager=None, registry=None):
        self._hook_manager = hook_manager
        self._registry = registry
        self._handlers: Dict[str, Callable] = {}

    async def on_gateway_started(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("gateway_started", context)

    async def on_gateway_stopped(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("gateway_stopped", context)

    async def on_before_chat_request(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("before_chat_request", context)

    async def on_after_chat_request(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("after_chat_request", context)

    async def on_memory_before_chat(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("memory_before_chat", context)

    async def on_memory_after_chat(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("memory_after_chat", context)

    async def on_before_system_prompt_build(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("before_system_prompt_build", context)

    async def on_agent_server_started(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("agent_server_started", context)

    async def on_agent_server_stopped(self, context: Dict[str, Any]) -> HookResult:
        return await self._dispatch("agent_server_stopped", context)

    async def _dispatch(self, event_name: str, context: Dict[str, Any]) -> HookResult:
        galaxyos_hook = SWARM_HOOK_TO_GALAXYOS.get(event_name)
        if not galaxyos_hook:
            logger.warning(f"Unknown JiuwenSwarm hook event: {event_name}")
            return HookResult(allowed=True, warning=f"Unknown event: {event_name}")

        try:
            if self._hook_manager and hasattr(self._hook_manager, "dispatch"):
                result = await self._hook_manager.dispatch(galaxyos_hook, context)
                if isinstance(result, HookResult):
                    return result
                return HookResult(allowed=True, data=result if isinstance(result, dict) else {})
        except Exception as e:
            logger.warning(f"Hook dispatch error for {event_name} -> {galaxyos_hook}: {e}")
            return HookResult(allowed=True, warning=str(e))

        handler = self._handlers.get(galaxyos_hook)
        if handler:
            try:
                result = await handler(context)
                if isinstance(result, HookResult):
                    return result
                return HookResult(allowed=True, data=result if isinstance(result, dict) else {})
            except Exception as e:
                logger.warning(f"Handler error for {galaxyos_hook}: {e}")
                return HookResult(allowed=True, warning=str(e))

        return HookResult(allowed=True)

    def register_handler(self, hook_name: str, handler: Callable) -> None:
        self._handlers[hook_name] = handler