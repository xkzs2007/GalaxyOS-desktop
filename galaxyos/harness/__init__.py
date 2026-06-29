"""galaxyos.harness — Deep Agent runtime layer (GalaxyOS standalone).

Architecture reference: openJiuwen harness (Huawei, 2025-2026)
  https://github.com/OPENJIUWEN-AI/AGENT-CORE/tree/main/openjiuwen/harness

The harness layer is the **microkernel** that orchestrates an Agent's
lifecycle:
  - DeepAgent            : the Agent class (brains)
  - Workspace            : the execution context (workspace, tools, memory)
  - TaskLoopEvent*       : the event-driven control flow
  - create_galaxy_agent  : the factory function (lazy loading)

GalaxyOS extends this with:
  - LiquidStateBackend   : LFM2.5-1.2B conv state for liquid memory
  - MeMoProtocol         : 3-stage Grounding → Entity → Answer (arXiv:2605.15156)
  - DSparkDecoder        : confidence-scheduled speculative decoding
  - SkillGraph           : 76-skill graph with graph-aware search

This module is **the single entry point** for any Agent in GalaxyOS:

    from galaxyos.harness import create_galaxy_agent
    agent = create_galaxy_agent(name="assistant")
    result = await agent.run("List my skills")
"""
from __future__ import annotations

# Lazy loading pattern (mirrors OpenJiuwen's __getattr__ trick)
# Public API is exposed via __getattr__ to avoid loading heavy deps
# (e.g. onnxruntime, sentence-transformers) until the user actually
# instantiates an Agent.

__version__ = "9.0.0"
__all__ = [
    "DeepAgent",
    "Workspace",
    "create_galaxy_agent",
    "DeepAgentConfig",
    "TaskLoopEvent",
    "TaskLoopEventHandler",
    "TaskLoopEventExecutor",
    "LiquidStateBackend",
]


def __getattr__(name):
    """Lazy attribute loader for heavy modules.

    The first time someone does `from galaxyos.harness import DeepAgent`,
    we import the implementation module. Subsequent imports hit the
    cached attribute (no re-import).
    """
    if name == "DeepAgent":
        from .deep_agent import DeepAgent
        return DeepAgent
    if name == "Workspace":
        from .workspace import Workspace
        return Workspace
    if name == "create_galaxy_agent":
        from .factory import create_galaxy_agent
        return create_galaxy_agent
    if name == "DeepAgentConfig":
        from .config import DeepAgentConfig
        return DeepAgentConfig
    if name in ("TaskLoopEvent", "TaskLoopEventHandler", "TaskLoopEventExecutor"):
        from . import task_loop
        return getattr(task_loop, name)
    if name == "LiquidStateBackend":
        from .liquid import LiquidStateBackend
        return LiquidStateBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
