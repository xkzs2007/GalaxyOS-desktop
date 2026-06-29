"""galaxy_agent.py — GalaxyOS DeepAgent integration for the sidecar.

This module wires the galaxyos.harness.DeepAgent (v9.0+) into the
sidecar's RPC surface, so renderer / CLI / MCP clients can call
``process_with_agent`` and get back a structured result.

The sidecar's original ask/process/agent methods (MeMo 3-stage +
ACRouter) keep working untouched. The new endpoint is additive.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger("galaxyos-sidecar.agent")


class GalaxyAgentBridge:
    """Bridges galaxyos.harness.DeepAgent into the sidecar's RPC.

    Why a bridge (and not direct DeepAgent in SidecarHandlers):
      - Sidecar is large (~900 lines); keep changes additive
      - Allows the bridge to be tested in isolation
      - Multiple bridge instances can coexist (one per RPC call)
    """
    def __init__(self, workspace_dir: Optional[str] = None,
                 model: Optional[str] = None) -> None:
        self.workspace_dir = workspace_dir or os.environ.get(
            "GALAXYOS_WORKSPACE",
            os.path.expanduser("~/.galaxyos/workspace")
        )
        self.model = model or os.environ.get(
            "GALAXYOS_AGENT_MODEL", "lfm2.5-1.2b-instruct"
        )
        self._agent = None
        self._events: List[Dict[str, Any]] = []

    async def _ensure_agent(self) -> Any:
        """Lazy import + create DeepAgent on first use."""
        if self._agent is not None:
            return self._agent
        # Ensure galaxyos is on sys.path (in case the sidecar is launched
        # from a context where /workspace isn't on it)
        import sys
        from pathlib import Path
        for p in ("/workspace", "/workspace/galaxyos", "/workspace/galaxyos/privileged",
                  "/workspace/galaxyos/engine", "/workspace/desktop-shell/python"):
            pp = str(p)
            if Path(p).exists() and pp not in sys.path:
                sys.path.insert(0, pp)
        try:
            from galaxyos.harness import create_galaxy_agent
        except ImportError as e:
            log.error("galaxyos.harness not importable: %s", e)
            raise
        self._agent = create_galaxy_agent(
            name="sidecar-agent",
            model=self.model,
            workspace_dir=self.workspace_dir,
        )
        log.info("GalaxyAgentBridge: created %r (%s)",
                 self._agent.config.name, self._agent.config.model)
        return self._agent

    async def process(self, user_input: str) -> Dict[str, Any]:
        """Run the Agent on a single input. Returns structured result.

        Result shape:
            {
                "agent": str,
                "session_id": str,
                "iterations": int,
                "result": <tool result or LLM text>,
                "duration_ms": float,
                "events": [TaskLoopEvent dicts],
            }
        """
        agent = await self._ensure_agent()
        t0 = time.perf_counter()
        # Subscribe a recorder to capture all events
        try:
            from galaxyos.harness import TaskLoopEventType
            @agent.on(TaskLoopEventType.STARTED)
            async def _rec_started(e): self._events.append(e.to_dict())
            @agent.on(TaskLoopEventType.THINKING)
            async def _rec_thinking(e): self._events.append(e.to_dict())
            @agent.on(TaskLoopEventType.TOOL_CALL)
            async def _rec_tool(e): self._events.append(e.to_dict())
            @agent.on(TaskLoopEventType.TOOL_RESULT)
            async def _rec_toolres(e): self._events.append(e.to_dict())
            @agent.on(TaskLoopEventType.FINISHED)
            async def _rec_finished(e): self._events.append(e.to_dict())
            @agent.on(TaskLoopEventType.ERROR)
            async def _rec_error(e): self._events.append(e.to_dict())
        except ImportError:
            pass  # harness not available — proceed without event capture

        result = await agent.run(user_input)
        duration_ms = (time.perf_counter() - t0) * 1000
        events = list(self._events)
        self._events.clear()
        return {
            "agent": agent.config.name,
            "session_id": agent.workspace.session_id,
            "iterations": result.get("iterations", 0),
            "result": result.get("result"),
            "duration_ms": round(duration_ms, 2),
            "events": events,
        }

    def info(self) -> Dict[str, Any]:
        return {
            "workspace_dir": self.workspace_dir,
            "model": self.model,
            "agent_loaded": self._agent is not None,
        }


__all__ = ["GalaxyAgentBridge"]
