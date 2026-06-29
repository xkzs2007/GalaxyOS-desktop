"""DeepAgent — The GalaxyOS Agent class (the "brains").

This is the **public entry point** for any Agent in GalaxyOS. Users
should NOT construct DeepAgent directly; use ``create_galaxy_agent()``
instead (the factory handles lazy loading and config defaults).

Responsibilities:
  - Run a think → act → observe loop on the user's input
  - Call the LLM with the current context + tools
  - Invoke tools through the Workspace's registry
  - Update memory after each meaningful step
  - Emit TaskLoopEvents for observability

Mirrors openJiuwen's DeepAgent but is async-first and integrates
GalaxyOS-specific components (MeMo, liquid state, SkillGraph).
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from .config import DeepAgentConfig
from .task_loop import (
    TaskLoopEvent, TaskLoopEventType, TaskLoopEventHandler,
    TaskLoopEventExecutor,
)
from .workspace import Workspace

log = logging.getLogger("galaxyos.harness.deep_agent")

# Lazy import paths for GalaxyOS internals (loaded by create_galaxy_agent)
_AGENT_LOOP_PATH = "galaxyos.harness.desktop_shell_compat.agent_loop"
_TOOLS_PATH = "galaxyos.harness.desktop_shell_compat.tools"
_AC_ROUTER_PATH = "galaxyos.harness.desktop_shell_compat.ac_router"


class DeepAgent:
    """A GalaxyOS Deep Agent — the user's primary interface.

    Lifecycle:
        agent = create_galaxy_agent(name="assistant")
        result = await agent.run("List my skills")
        # Or with streaming:
        async for event in agent.stream("Hello"):
            print(event.to_dict())
    """
    def __init__(self, config: DeepAgentConfig, workspace: Workspace) -> None:
        self.config = config
        self.workspace = workspace
        self.handler = TaskLoopEventHandler()
        self.executor = TaskLoopEventExecutor(
            handler=self.handler,
            max_iterations=config.max_iterations,
        )
        # Internal conversation history (per session)
        self._history: List[Dict[str, str]] = []

    # ── Public API ──────────────────────────────────────────────

    async def run(self, user_input: str) -> Dict[str, Any]:
        """Run the Agent on a user input. Returns the final result dict."""
        self._history.append({"role": "user", "content": user_input})

        async def step_fn(iteration: int) -> Dict[str, Any]:
            # Emit THINKING for observability
            await self.handler.emit(TaskLoopEvent(
                type=TaskLoopEventType.THINKING,
                session_id=self.workspace.session_id,
                iteration=iteration,
                payload={"input_preview": user_input[:60]},
            ))

            # 1. Try tool dispatch via heuristic agent_loop
            tool_call = self._select_tool(user_input)
            if tool_call:
                name, fn = tool_call
                await self.handler.emit(TaskLoopEvent(
                    type=TaskLoopEventType.TOOL_CALL,
                    session_id=self.workspace.session_id,
                    iteration=iteration,
                    payload={"name": name},
                ))
                try:
                    if asyncio.iscoroutinefunction(fn):
                        result = await fn(user_input)
                    else:
                        result = fn(user_input)
                except Exception as e:
                    log.warning("tool %r failed: %s", name, e)
                    result = {"ok": False, "error": str(e)}
                await self.handler.emit(TaskLoopEvent(
                    type=TaskLoopEventType.TOOL_RESULT,
                    session_id=self.workspace.session_id,
                    iteration=iteration,
                    payload={"result": self._safe_preview(result)},
                ))
                return {
                    "status": "finished",
                    "result": result,
                    "thought": f"Used tool {name}",
                }

            # 2. No tool → call LLM (if available) else canned response
            llm_response = await self._ask_llm(user_input)
            self._history.append({"role": "assistant", "content": llm_response})
            return {
                "status": "finished",
                "result": {"text": llm_response, "ok": True},
                "thought": "Direct LLM response",
            }

        results = await self.executor.run(self.workspace.session_id, step_fn)
        final = results[-1] if results else {"result": None}
        return {
            "session_id": self.workspace.session_id,
            "agent": self.config.name,
            "iterations": len(results),
            "result": final.get("result"),
        }

    async def stream(self, user_input: str):
        """Async generator of TaskLoopEvents. For SSE/TokUI rendering.

        v1: yields STARTED + FINAL events. A future version can stream
        intermediate THINKING / TOOL_CALL events from the handler.
        """
        await self.handler.emit(TaskLoopEvent(
            type=TaskLoopEventType.STARTED,
            session_id=self.workspace.session_id,
            payload={"input": user_input[:200]},
        ))
        result = await self.run(user_input)
        await self.handler.emit(TaskLoopEvent(
            type=TaskLoopEventType.FINISHED,
            session_id=self.workspace.session_id,
            payload={"result": self._safe_preview(result.get("result"))},
        ))
        yield result

    def on(self, event_type: TaskLoopEventType):
        """Subscribe to events. Returns a decorator for handler registration."""
        return self.handler.on(event_type)

    def info(self) -> Dict[str, Any]:
        return {
            "agent": self.config.name,
            "model": self.config.model,
            "workspace": self.workspace.info(),
            "history_len": len(self._history),
        }

    # ── Internal helpers ───────────────────────────────────────

    def _select_tool(self, user_input: str):
        """Heuristic v1 tool selection. v2 will be LLM-driven.

        Returns (name, fn) or None. Reads from the workspace's tool
        registry if available, else falls back to desktop_shell_compat.
        """
        text = user_input.lower()
        # Try the real GalaxyOS tools first (registered in workspace)
        if self.workspace.tools:
            for name, fn in self.workspace.tools.items():
                if self._matches_tool(text, name):
                    return name, fn

        # Fallback: try to import desktop_shell_compat.tools
        try:
            from . import desktop_shell_compat
            tools_mod = desktop_shell_compat.tools
            for name in self.config.tools:
                fn = getattr(tools_mod, name, None)
                if fn and self._matches_tool(text, name):
                    return name, fn
        except ImportError:
            pass

        return None

    @staticmethod
    def _matches_tool(text: str, tool_name: str) -> bool:
        """Keyword match: each tool has a small set of trigger words."""
        triggers = {
            "shell_run": ["shell", "run", "execute", "!", "$"],
            "read_file": ["read", "cat", "view", "show", "open"],
            "write_file": ["write", "save", "create", "edit", "echo"],
            "list_dir": ["list", "ls", "dir", "directory"],
            "grep": ["grep", "search", "find", "regex"],
            "apply_diff": ["diff", "patch", "apply"],
        }
        for kw in triggers.get(tool_name, []):
            if kw in text:
                return True
        return False

    async def _ask_llm(self, user_input: str) -> str:
        """Call the LLM if available, else return a canned response."""
        if self.workspace.llm is None:
            return f"[{self.config.name}] (no LLM configured) You said: {user_input}"
        try:
            # workspace.llm is expected to have a .chat(messages) method
            if asyncio.iscoroutinefunction(self.workspace.llm.chat):
                return await self.workspace.llm.chat(
                    messages=self._history + [{"role": "user", "content": user_input}],
                    temperature=self.config.temperature,
                )
            return self.workspace.llm.chat(
                messages=self._history + [{"role": "user", "content": user_input}],
                temperature=self.config.temperature,
            )
        except Exception as e:
            log.warning("LLM call failed: %s", e)
            return f"[{self.config.name}] LLM error: {e}"

    @staticmethod
    def _safe_preview(value: Any, max_len: int = 200) -> Any:
        """Truncate large values for event payloads (avoid log bloat)."""
        if isinstance(value, str) and len(value) > max_len:
            return value[:max_len] + "..."
        if isinstance(value, dict):
            return {k: DeepAgent._safe_preview(v, max_len)
                    for k, v in value.items()}
        if isinstance(value, list) and len(value) > 20:
            return [DeepAgent._safe_preview(v, max_len) for v in value[:20]] + ["..."]
        return value


__all__ = ["DeepAgent"]
