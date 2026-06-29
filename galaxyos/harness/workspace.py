"""Workspace — Agent's execution context.

A Workspace bundles everything an Agent needs at runtime:
  - file sandbox (workspace_dir)
  - tool registry (registry)
  - memory backend (memory)
  - skill graph (skills)
  - LLM client (llm)
  - session (id, created_at, metadata)

Mirrors openJiuwen's Workspace abstraction but simpler — GalaxyOS
runs single-agent in single-process for now.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("galaxyos.harness.workspace")


@dataclass
class Workspace:
    """The execution context for a GalaxyOS DeepAgent.

    Constructed automatically by ``create_galaxy_agent()``. Most
    users won't instantiate this directly.
    """
    workspace_dir: Path
    tools: Dict[str, Any] = field(default_factory=dict)
    memory: Any = None
    skills: Any = None
    llm: Any = None
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def info(self) -> Dict[str, Any]:
        """Snapshot of workspace state (for /sse/health or debugging)."""
        return {
            "workspace_dir": str(self.workspace_dir),
            "session_id": self.session_id,
            "tools": list(self.tools.keys()),
            "memory_backend": type(self.memory).__name__ if self.memory else None,
            "skill_count": (
                len(self.skills.nodes) if self.skills
                and hasattr(self.skills, "nodes") else 0
            ),
            "llm": type(self.llm).__name__ if self.llm else None,
            "created_at": self.created_at,
            "age_seconds": time.time() - self.created_at,
        }

    def ensure_dirs(self) -> None:
        """Create workspace skeleton (executed + skills + memory)."""
        wd = Path(self.workspace_dir)
        for sub in ("executions", "skills", "memory"):
            (wd / sub).mkdir(parents=True, exist_ok=True)


__all__ = ["Workspace"]
