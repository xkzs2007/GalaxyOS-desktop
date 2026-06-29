"""factory — create_galaxy_agent() entry point.

Mirrors openJiuwen's ``create_deep_agent()`` factory. The factory
is the **only** sanctioned way to build a DeepAgent — direct
construction is allowed but discouraged.

Why a factory:
  - Lazy loading: heavy deps (onnxruntime, sentence-transformers) only
    load when an Agent is actually instantiated
  - Config defaults: env vars, file-based config, sensible fallbacks
  - Testability: easy to mock the entire agent with a single call
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .config import DeepAgentConfig
from .deep_agent import DeepAgent
from .workspace import Workspace

log = logging.getLogger("galaxyos.harness.factory")


def create_galaxy_agent(
    name: str = "galaxy-agent",
    model: str = "lfm2.5-1.2b-instruct",
    **overrides,
) -> DeepAgent:
    """Create a GalaxyOS Deep Agent with sensible defaults.

    Required:
        (none — all args have defaults)

    Common overrides:
        name           : Agent name (default: "galaxy-agent")
        model          : LLM model (default: "lfm2.5-1.2b-instruct")
        workspace_dir  : Override workspace directory
        tools          : List of tool names
        memory         : "vector" | "liquid" | "mock"
        max_iterations : Default 20
        temperature    : Default 0.7
        system_prompt  : Custom prompt

    Example:
        >>> agent = create_galaxy_agent(name="assistant")
        >>> import asyncio
        >>> result = asyncio.run(agent.run("Hello"))
        >>> print(result["result"])

    Environment variables (read if not overridden):
        GALAXYOS_AGENT_NAME  : Agent name
        GALAXYOS_AGENT_MODEL : LLM model
        GALAXYOS_HOME        : Override workspace root
    """
    config = DeepAgentConfig(
        name=overrides.pop("name", os.environ.get("GALAXYOS_AGENT_NAME", name)),
        model=overrides.pop("model", os.environ.get("GALAXYOS_AGENT_MODEL", model)),
        **overrides,
    )
    workspace = _build_workspace(config)
    return DeepAgent(config=config, workspace=workspace)


def _build_workspace(config: DeepAgentConfig) -> Workspace:
    """Wire up tools, memory, LLM, skills into a Workspace."""
    workspace = Workspace(workspace_dir=config.workspace_dir)
    workspace.ensure_dirs()

    # 1. Tools (try to register from desktop_shell_compat)
    try:
        from . import desktop_shell_compat
        tools_mod = desktop_shell_compat.tools
        for name in config.tools:
            fn = getattr(tools_mod, name, None)
            if fn is not None:
                workspace.tools[name] = fn
        log.info("registered %d tools", len(workspace.tools))
    except (ImportError, AttributeError) as e:
        log.warning("desktop_shell_compat.tools not available: %s", e)

    # 2. Memory backend
    if config.memory == "liquid":
        try:
            from .liquid import LiquidStateBackend
            workspace.memory = LiquidStateBackend()
        except Exception as e:
            log.warning("LiquidStateBackend failed: %s; falling back to mock", e)
            workspace.memory = None
    elif config.memory == "vector":
        try:
            from . import desktop_shell_compat
            vs = desktop_shell_compat.vector_store
            # The actual class name in galaxyos/privileged/vector_store.py
            for cls_name in ("VectorStore", "PersistentVectorStore",
                             "VectorStoreManager"):
                cls = getattr(vs, cls_name, None)
                if cls is not None:
                    workspace.memory = cls(
                        db_path=config.workspace_dir / "memory" / "vectors.db"
                    )
                    log.info("vector store: %s", cls_name)
                    break
        except Exception as e:
            log.warning("vector_store not available: %s", e)
    # else "mock" → workspace.memory stays None

    # 3. SkillGraph (if enabled)
    if config.skill_graph:
        try:
            from . import desktop_shell_compat
            sg_mod = desktop_shell_compat.skill_graph
            graph = sg_mod.SkillGraph(auto_load=True)
            workspace.skills = graph
            log.info("SkillGraph loaded: %d nodes",
                     len(graph.nodes) if hasattr(graph, "nodes") else 0)
        except Exception as e:
            log.warning("SkillGraph not available: %s", e)

    # 4. LLM backend — route by model name
    workspace.llm = _pick_llm_backend(config)
    if workspace.llm is not None:
        try:
            log.info("LLM backend: %s", workspace.llm.backend_name())
        except Exception:
            pass

    return workspace


def _pick_llm_backend(config: DeepAgentConfig):
    """Route config.model to the right LLM backend.

    Routing rules (v9.1):
      - "lfm2.5-*" / "lfm-*" / models with "local" hint
          → LiquidStateBackend (LFM ONNX, in-process)
      - any other model name OR if SidecarHandlers is importable
          → SidecarBackend (in-process bridge to galaxyos_sidecar.py)
      - if SidecarBackend fails to construct
          → fall back to a tiny canned-response backend (so the
            agent still runs in environments without the sidecar)

    The sidecar is the **primary** path because it has the real
    engine (XiaoYiClawLLM + MeMo + ACRouter + 76 Skills) and is
    shared with the desktop renderer.
    """
    model = (config.model or "").lower().strip()

    # Local LFM is the only case where we DON'T go through the sidecar
    # (the sidecar's stream_memo_frag path also covers it, but the
    # dedicated LiquidStateBackend gives finer control over conv state).
    if model.startswith("lfm") and "liquid" in (config.memory or "").lower():
        try:
            from .liquid import LiquidStateBackend
            return LiquidStateBackend()
        except Exception as e:
            log.warning("LiquidStateBackend failed: %s; trying sidecar", e)

    # Primary: sidecar bridge
    try:
        from .sidecar_bridge import build_sidecar_backend
        backend = build_sidecar_backend(model=config.model)
        if backend is not None:
            return backend
    except Exception as e:
        log.warning("SidecarBackend unavailable: %s; using canned fallback", e)

    # Final fallback: canned response (so the agent always "works"
    # in environments without the sidecar, e.g. CI without desktop-shell)
    return _CannedBackend(name=config.name, model=config.model)


class _CannedBackend:
    """Minimal LLM backend that returns a fixed response. Used when
    neither LiquidStateBackend nor SidecarBackend is available
    (e.g. headless CI, or a slim harness install).
    """
    def __init__(self, name: str = "galaxy-agent", model: str = "default") -> None:
        self._name = name
        self._model = model

    async def chat(self, messages, temperature: float = 0.7,
                   session_id: str = "default", **kwargs) -> str:
        # Pull the last user message for a slightly less canned reply
        last = ""
        for m in reversed(messages or []):
            if isinstance(m, dict) and m.get("role") == "user":
                last = str(m.get("content", "") or "")
                break
        return (
            f"[{self._name}/{self._model}] (canned) "
            f"received: {last[:120] or '(empty)'}"
        )

    def backend_name(self) -> str:
        return f"CannedBackend(model={self._model})"

    def is_sidecar(self) -> bool:
        return False


__all__ = ["create_galaxy_agent", "_CannedBackend"]
