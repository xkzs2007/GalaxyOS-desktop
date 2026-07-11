"""
GalaxyOS 生命周期钩子映射 — 将 GalaxyOS 的 9 个 OpenClaw 生命周期钩子映射到 Agent Studio 生命周期事件。

映射关系：
  gateway_start        -> on_plugin_load       (插件加载初始化)
  gateway_stop         -> on_plugin_unload     (插件卸载清理)
  before_tool_call     -> on_pre_tool_use      (工具调用前)
  after_tool_call      -> on_post_tool_use     (工具调用后)
  before_compaction    -> on_pre_compaction    (压缩前，降级钩子)
  after_compaction     -> on_post_compaction   (压缩后，降级钩子)
  before_agent_reply   -> on_pre_agent_reply   (Agent 回复前)
  agent_end            -> on_post_agent_reply  (Agent 回复后)
  before_prompt_build  -> on_user_prompt_submit(提示构建前)

降级策略：
  - before_compaction / after_compaction: Agent Studio 无直接对应，降级为定时轮询
  - Python 内核崩溃时: 钩子调用返回 allowed=True + warning，不阻塞主流程
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class OpenClawHook(str, Enum):
    GATEWAY_START = "gateway_start"
    GATEWAY_STOP = "gateway_stop"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_COMPACTION = "before_compaction"
    AFTER_COMPACTION = "after_compaction"
    BEFORE_AGENT_REPLY = "before_agent_reply"
    AGENT_END = "agent_end"
    BEFORE_PROMPT_BUILD = "before_prompt_build"


class AgentStudioEvent(str, Enum):
    ON_PLUGIN_LOAD = "on_plugin_load"
    ON_PLUGIN_UNLOAD = "on_plugin_unload"
    ON_PRE_TOOL_USE = "on_pre_tool_use"
    ON_POST_TOOL_USE = "on_post_tool_use"
    ON_PRE_COMPACTION = "on_pre_compaction"
    ON_POST_COMPACTION = "on_post_compaction"
    ON_PRE_AGENT_REPLY = "on_pre_agent_reply"
    ON_POST_AGENT_REPLY = "on_post_agent_reply"
    ON_USER_PROMPT_SUBMIT = "on_user_prompt_submit"


HOOK_MAPPING: dict[OpenClawHook, AgentStudioEvent] = {
    OpenClawHook.GATEWAY_START: AgentStudioEvent.ON_PLUGIN_LOAD,
    OpenClawHook.GATEWAY_STOP: AgentStudioEvent.ON_PLUGIN_UNLOAD,
    OpenClawHook.BEFORE_TOOL_CALL: AgentStudioEvent.ON_PRE_TOOL_USE,
    OpenClawHook.AFTER_TOOL_CALL: AgentStudioEvent.ON_POST_TOOL_USE,
    OpenClawHook.BEFORE_COMPACTION: AgentStudioEvent.ON_PRE_COMPACTION,
    OpenClawHook.AFTER_COMPACTION: AgentStudioEvent.ON_POST_COMPACTION,
    OpenClawHook.BEFORE_AGENT_REPLY: AgentStudioEvent.ON_PRE_AGENT_REPLY,
    OpenClawHook.AGENT_END: AgentStudioEvent.ON_POST_AGENT_REPLY,
    OpenClawHook.BEFORE_PROMPT_BUILD: AgentStudioEvent.ON_USER_PROMPT_SUBMIT,
}

REVERSE_MAPPING: dict[AgentStudioEvent, OpenClawHook] = {v: k for k, v in HOOK_MAPPING.items()}

DEGRADED_HOOKS = {OpenClawHook.BEFORE_COMPACTION, OpenClawHook.AFTER_COMPACTION}


@dataclass
class HookResult:
    allowed: bool = True
    warning: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


HookHandler = Callable[[dict[str, Any]], HookResult]


class LifecycleHookManager:
    """
    管理 GalaxyOS 9 钩子与 Agent Studio 生命周期事件的映射和分发。

    用法：
        manager = LifecycleHookManager()
        manager.register_handler(OpenClawHook.BEFORE_AGENT_REPLY, my_rccam_handler)
        result = manager.dispatch(AgentStudioEvent.ON_PRE_AGENT_REPLY, {"query": "..."})
    """

    def __init__(self):
        self._handlers: dict[OpenClawHook, list[HookHandler]] = {hook: [] for hook in OpenClawHook}
        self._kernel_available = True
        self._last_degraded_poll = 0.0
        self._degraded_poll_interval = 300.0

    def register_handler(self, hook: OpenClawHook, handler: HookHandler) -> None:
        self._handlers[hook].append(handler)

    def set_kernel_available(self, available: bool) -> None:
        self._kernel_available = available
        if not available:
            logger.warning("GalaxyOS Python kernel unavailable — hooks will degrade to pass-through")

    def dispatch(self, event: AgentStudioEvent, context: dict[str, Any] | None = None) -> HookResult:
        context = context or {}
        hook = REVERSE_MAPPING.get(event)
        if hook is None:
            return HookResult(allowed=True, warning=f"Unknown Agent Studio event: {event}")

        if hook in DEGRADED_HOOKS:
            return self._dispatch_degraded(hook, context)

        if not self._kernel_available:
            return HookResult(allowed=True, warning=f"GalaxyOS kernel unavailable — hook {hook.value} skipped")

        return self._dispatch_normal(hook, context)

    def _dispatch_normal(self, hook: OpenClawHook, context: dict[str, Any]) -> HookResult:
        handlers = self._handlers.get(hook, [])
        if not handlers:
            return HookResult(allowed=True)

        combined_data = {}
        for handler in handlers:
            try:
                result = handler(context)
                if not result.allowed:
                    return result
                combined_data.update(result.data)
            except Exception as e:
                logger.error(f"Hook handler error for {hook.value}: {e}", exc_info=True)
                return HookResult(allowed=True, warning=f"Hook {hook.value} handler error: {e}")

        return HookResult(allowed=True, data=combined_data)

    def _dispatch_degraded(self, hook: OpenClawHook, context: dict[str, Any]) -> HookResult:
        now = time.monotonic()
        if now - self._last_degraded_poll < self._degraded_poll_interval:
            return HookResult(allowed=True, warning=f"Degraded hook {hook.value} — polling interval not reached")

        self._last_degraded_poll = now
        if not self._kernel_available:
            return HookResult(allowed=True, warning=f"Degraded hook {hook.value} — kernel unavailable")

        return self._dispatch_normal(hook, context)

    def dispatch_agent_studio_event(self, event_name: str, context: dict[str, Any] | None = None) -> HookResult:
        try:
            event = AgentStudioEvent(event_name)
        except ValueError:
            return HookResult(allowed=True, warning=f"Unknown event: {event_name}")
        return self.dispatch(event, context)

    def get_hook_status(self) -> dict[str, Any]:
        return {
            "kernel_available": self._kernel_available,
            "hooks": {
                hook.value: {
                    "agent_studio_event": HOOK_MAPPING[hook].value,
                    "degraded": hook in DEGRADED_HOOKS,
                    "handler_count": len(self._handlers[hook]),
                }
                for hook in OpenClawHook
            },
        }