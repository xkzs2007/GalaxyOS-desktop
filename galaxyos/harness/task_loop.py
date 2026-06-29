"""task_loop — Event-driven Agent control flow.

Three components, mirroring openJiuwen's TaskLoopEvent* trio:

  TaskLoopEvent         : A typed event (started/tool_call/finished/etc)
  TaskLoopEventHandler  : Observer / listener (callbacks for events)
  TaskLoopEventExecutor : The actual loop driver (think → act → observe)

GalaxyOS extensions:
  - Each event carries a ``payload`` dict with rich metadata
  - Events can be streamed over SSE (via tokui_dsl on the sidecar)
  - Handlers are async-safe (can yield to other tasks)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger("galaxyos.harness.task_loop")


class TaskLoopEventType(str, Enum):
    """Standard Agent event types."""
    STARTED = "started"               # Agent.run() invoked
    THINKING = "thinking"             # LLM call in progress
    TOOL_CALL = "tool_call"           # About to invoke a tool
    TOOL_RESULT = "tool_result"       # Tool returned
    OBSERVATION = "observation"       # Post-tool reflection
    MEMORY_READ = "memory_read"       # Memory lookup
    MEMORY_WRITE = "memory_write"     # Memory store
    FINISHED = "finished"             # Agent.run() completed
    ERROR = "error"                   # Something failed
    CANCELLED = "cancelled"           # User/system cancellation


@dataclass
class TaskLoopEvent:
    """A typed event in the Agent's lifecycle."""
    type: TaskLoopEventType
    session_id: str
    iteration: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type.value,
            "session_id": self.session_id,
            "iteration": self.iteration,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


# Handler signature: async (event: TaskLoopEvent) -> None
EventHandler = Callable[[TaskLoopEvent], Awaitable[None]]


class TaskLoopEventHandler:
    """Observer for Agent events. Register callbacks for specific types.

    Usage:
        handler = TaskLoopEventHandler()
        @handler.on(TaskLoopEventType.TOOL_CALL)
        async def log_tool(event):
            print(f"→ {event.payload['name']}")

        # Or subscribe all:
        handler.subscribe_all(my_async_logger)
    """
    def __init__(self) -> None:
        self._callbacks: Dict[TaskLoopEventType, List[EventHandler]] = {
            t: [] for t in TaskLoopEventType
        }
        self._all_callbacks: List[EventHandler] = []

    def on(self, event_type: TaskLoopEventType) -> Callable:
        """Decorator: register a coroutine for a specific event type."""
        def decorator(fn: EventHandler) -> EventHandler:
            self._callbacks[event_type].append(fn)
            return fn
        return decorator

    def subscribe_all(self, fn: EventHandler) -> None:
        """Register a callback for ALL event types."""
        self._all_callbacks.append(fn)

    async def emit(self, event: TaskLoopEvent) -> None:
        """Fire an event to all matching handlers (errors are isolated)."""
        handlers = list(self._callbacks.get(event.type, []))
        handlers.extend(self._all_callbacks)
        for h in handlers:
            try:
                await h(event)
            except Exception as e:
                # Handlers must NEVER break the Agent loop
                log.warning("handler %r failed: %s", h, e)


class TaskLoopEventExecutor:
    """Drives the think → act → observe loop.

    This is intentionally minimal — the actual decision logic lives in
    the LLM client + tool registry. The executor just orchestrates
    the iteration count, event emission, and termination conditions.
    """
    def __init__(self, handler: TaskLoopEventHandler,
                 max_iterations: int = 20) -> None:
        self.handler = handler
        self.max_iterations = max_iterations

    async def run(
        self,
        session_id: str,
        step_fn: Callable[[int], Awaitable[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Run the loop. ``step_fn`` returns a dict like::

            {"status": "continue" | "finished" | "error",
             "result": <final_answer>,
             "tool_call": {...}  # if status == "continue"
             "thought": "..."    # optional
            }

        Returns the list of step results.
        """
        results: List[Dict[str, Any]] = []
        await self.handler.emit(TaskLoopEvent(
            type=TaskLoopEventType.STARTED,
            session_id=session_id,
        ))

        for i in range(self.max_iterations):
            try:
                step = await step_fn(i)
            except asyncio.CancelledError:
                await self.handler.emit(TaskLoopEvent(
                    type=TaskLoopEventType.CANCELLED,
                    session_id=session_id,
                    iteration=i,
                ))
                raise
            except Exception as e:
                await self.handler.emit(TaskLoopEvent(
                    type=TaskLoopEventType.ERROR,
                    session_id=session_id,
                    iteration=i,
                    payload={"error": str(e), "type": type(e).__name__},
                ))
                raise

            results.append(step)
            status = step.get("status", "continue")

            if status == "finished":
                await self.handler.emit(TaskLoopEvent(
                    type=TaskLoopEventType.FINISHED,
                    session_id=session_id,
                    iteration=i,
                    payload={"result": step.get("result")},
                ))
                return results
            if status == "error":
                await self.handler.emit(TaskLoopEvent(
                    type=TaskLoopEventType.ERROR,
                    session_id=session_id,
                    iteration=i,
                    payload=step,
                ))
                return results
            # else "continue" — keep looping

        # Max iterations reached
        await self.handler.emit(TaskLoopEvent(
            type=TaskLoopEventType.FINISHED,
            session_id=session_id,
            iteration=self.max_iterations,
            payload={"result": None, "reason": "max_iterations_exceeded"},
        ))
        return results


__all__ = [
    "TaskLoopEvent",
    "TaskLoopEventHandler",
    "TaskLoopEventExecutor",
    "TaskLoopEventType",
    "EventHandler",
]
