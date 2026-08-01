"""
HookAdapter — 将 GalaxyOS OpenClaw 钩子逻辑映射到 PilotDeck Hook 协议

钩子映射表：
- gateway_start → Setup
- gateway_stop → SessionEnd
- before_tool_call → PreToolUse
- after_tool_call → PostToolUse
- before_compaction → PreCompact（降级为定时轮询）
- after_compaction → PostCompact（降级为定时轮询）
- before_agent_reply → PreModelRequest（近似）
- agent_end → SessionEnd（近似）
- before_prompt_build → UserPromptSubmit
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class PilotDeckHook(str, Enum):
    SETUP = "Setup"
    SESSION_END = "SessionEnd"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    PRE_MODEL_REQUEST = "PreModelRequest"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"


class GalaxyOSHook(str, Enum):
    GATEWAY_START = "gateway_start"
    GATEWAY_STOP = "gateway_stop"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_COMPACTION = "before_compaction"
    AFTER_COMPACTION = "after_compaction"
    BEFORE_AGENT_REPLY = "before_agent_reply"
    AGENT_END = "agent_end"
    BEFORE_PROMPT_BUILD = "before_prompt_build"


HOOK_MAPPING: Dict[GalaxyOSHook, PilotDeckHook] = {
    GalaxyOSHook.GATEWAY_START: PilotDeckHook.SETUP,
    GalaxyOSHook.GATEWAY_STOP: PilotDeckHook.SESSION_END,
    GalaxyOSHook.BEFORE_TOOL_CALL: PilotDeckHook.PRE_TOOL_USE,
    GalaxyOSHook.AFTER_TOOL_CALL: PilotDeckHook.POST_TOOL_USE,
    GalaxyOSHook.BEFORE_COMPACTION: PilotDeckHook.PRE_COMPACT,
    GalaxyOSHook.AFTER_COMPACTION: PilotDeckHook.POST_COMPACT,
    GalaxyOSHook.BEFORE_AGENT_REPLY: PilotDeckHook.PRE_MODEL_REQUEST,
    GalaxyOSHook.AGENT_END: PilotDeckHook.SESSION_END,
    GalaxyOSHook.BEFORE_PROMPT_BUILD: PilotDeckHook.USER_PROMPT_SUBMIT,
}

DEGRADED_HOOKS = {
    GalaxyOSHook.BEFORE_COMPACTION,
    GalaxyOSHook.AFTER_COMPACTION,
}


@dataclass
class HookResult:
    allowed: bool = True
    modified_input: Optional[Dict[str, Any]] = None
    warning: Optional[str] = None


class HookAdapter:
    def __init__(self, python_kernel_available: bool = True):
        self._handlers: Dict[PilotDeckHook, List[Callable]] = {}
        self._python_kernel_available = python_kernel_available

    def register_handler(self, pilotdeck_hook: PilotDeckHook, handler: Callable) -> None:
        self._handlers.setdefault(pilotdeck_hook, []).append(handler)

    def register_galaxyos_handler(self, galaxyos_hook: GalaxyOSHook, handler: Callable) -> None:
        pd_hook = HOOK_MAPPING.get(galaxyos_hook)
        if pd_hook is None:
            return
        self.register_handler(pd_hook, handler)

    async def trigger(self, pilotdeck_hook: PilotDeckHook, context: Dict[str, Any]) -> HookResult:
        if not self._python_kernel_available:
            return HookResult(
                allowed=True,
                warning="Python kernel unavailable, hook skipped",
            )

        handlers = self._handlers.get(pilotdeck_hook, [])
        for handler in handlers:
            try:
                result = await handler(context) if asyncio.iscoroutinefunction(handler) else handler(context)
                if isinstance(result, HookResult) and not result.allowed:
                    return result
            except Exception:
                continue

        return HookResult(allowed=True)

    async def trigger_galaxyos(self, galaxyos_hook: GalaxyOSHook, context: Dict[str, Any]) -> HookResult:
        pd_hook = HOOK_MAPPING.get(galaxyos_hook)
        if pd_hook is None:
            return HookResult(allowed=True, warning=f"No mapping for {galaxyos_hook}")

        if galaxyos_hook in DEGRADED_HOOKS:
            return HookResult(
                allowed=True,
                warning=f"Hook {galaxyos_hook} runs in degraded mode (polling)",
            )

        return await self.trigger(pd_hook, context)

    def get_mapping_table(self) -> Dict[str, str]:
        return {gh.value: ph.value for gh, ph in HOOK_MAPPING.items()}


import asyncio
