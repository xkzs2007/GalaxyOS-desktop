"""DeepAgentConfig — Top-level Agent configuration.

Mirrors openJiuwen's DeepAgentConfig schema. Multi-modal is **not**
first-class in GalaxyOS yet (text-only), but the schema is forward-
compatible so adding AudioConfig/VisionConfig later is non-breaking.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class DeepAgentConfig:
    """Configuration for a GalaxyOS DeepAgent.

    Required:
        name           : Human-readable agent name (used in logs/UI)
        model          : LLM model identifier (e.g. "lfm2.5-1.2b-instruct",
                         "deepseek-chat", "gpt-4o")

    Optional (with sensible defaults):
        workspace_dir  : Where the agent reads/writes files
                         (default: ~/.galaxyos/workspace)
        tools          : List of tool names to register
                         (default: ["shell_run", "read_file", "write_file",
                          "list_dir", "grep", "apply_diff"])
        memory         : Memory backend (default: "vector")
                         Options: "vector" (PersistentVectorStore),
                                  "liquid" (LFM conv state),
                                  "mock" (in-process dict)
        skill_graph    : Whether to enable SkillGraph (default: True)
        max_iterations : Max tool-call iterations per run (default: 20)
        temperature    : LLM sampling temperature (default: 0.7)
        streaming      : Enable SSE streaming output (default: True)
        system_prompt  : Custom system prompt (default: built-in)

    Environment overrides:
        GALAXYOS_AGENT_NAME, GALAXYOS_AGENT_MODEL, etc.
    """
    name: str = "galaxy-agent"
    model: str = "lfm2.5-1.2b-instruct"

    workspace_dir: Path = field(
        default_factory=lambda: Path.home() / ".galaxyos" / "workspace"
    )
    tools: List[str] = field(
        default_factory=lambda: [
            "shell_run", "read_file", "write_file",
            "list_dir", "grep", "apply_diff",
        ]
    )
    memory: str = "vector"            # vector | liquid | mock
    skill_graph: bool = True
    max_iterations: int = 20
    temperature: float = 0.7
    streaming: bool = True
    system_prompt: Optional[str] = None

    # Optional metadata (for UI / monitoring)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, **overrides) -> "DeepAgentConfig":
        """Build a config from env vars (with optional overrides)."""
        import os
        defaults = {
            "name": os.environ.get("GALAXYOS_AGENT_NAME", "galaxy-agent"),
            "model": os.environ.get("GALAXYOS_AGENT_MODEL",
                                    "lfm2.5-1.2b-instruct"),
        }
        defaults.update(overrides)
        return cls(**defaults)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": self.name,
            "model": self.model,
            "workspace_dir": str(self.workspace_dir),
            "tools": list(self.tools),
            "memory": self.memory,
            "skill_graph": self.skill_graph,
            "max_iterations": self.max_iterations,
            "temperature": self.temperature,
            "streaming": self.streaming,
        }
        if self.system_prompt:
            d["system_prompt"] = self.system_prompt
        if self.metadata:
            d["metadata"] = self.metadata
        return d


__all__ = ["DeepAgentConfig"]
