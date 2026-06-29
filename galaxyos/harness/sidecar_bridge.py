"""sidecar_bridge.py — Bridge GalaxyOS harness ↔ desktop-shell SidecarHandlers.

The harness's DeepAgent expects ``workspace.llm`` to be a duck-typed object
with an async ``.chat(messages)`` method. This module wraps the
in-process ``SidecarHandlers`` (the 57KB, 30+ method backbone of the
desktop sidecar) so that harness agents can drive the *real* engine
without going through zmq or HTTP.

Why in-process (not HTTP/zmq):
  - Zero latency (no TCP/serialization)
  - Shared memory (one engine load, no double-spawn)
  - Same code path as production renderer (SidecarHandlers is the
    single source of truth)

Usage (from harness/factory.py):

    from galaxyos.harness.sidecar_bridge import build_sidecar_backend
    workspace.llm = build_sidecar_backend(model="qwen2.5-7b")

The returned object implements::

    async chat(messages, **kwargs) -> str
    async stream(messages, **kwargs) -> AsyncIterator[str]
    backend_name() -> str
    is_sidecar() -> bool
"""
from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

log = logging.getLogger("galaxyos.harness.sidecar_bridge")


# ── Lazy import of SidecarHandlers ─────────────────────────────────────

def _load_sidecar_handlers():
    """Import SidecarHandlers from desktop-shell/python.

    Adds the directory to sys.path the first time. Cached afterwards.
    """
    from pathlib import Path
    import sys
    # desktop-shell/python is two levels up from galaxyos/harness
    here = Path(__file__).resolve()
    sidecar_dir = here.parent.parent.parent / "desktop-shell" / "python"
    sidecar_dir_str = str(sidecar_dir)
    if sidecar_dir_str not in sys.path:
        sys.path.insert(0, sidecar_dir_str)
    try:
        from galaxyos_sidecar import SidecarHandlers  # type: ignore
        return SidecarHandlers
    except Exception as e:
        log.warning("failed to import SidecarHandlers: %s", e)
        return None


# ── Mapping from harness model name → sidecar stream_*_frag method ────

# These model names mirror the model-picker in the renderer
# (renderer/index.html lines 90-114). When the user picks one in the
# Settings modal, it round-trips through llm_config.json → set_config
# → executive_client. Our stream_*_frag methods are model-agnostic;
# they call the active engine which uses whichever model is configured.
_MODEL_TO_STREAM = {
    # Qwen family → ask stream (single-shot)
    "qwen-2.5": "ask",
    "qwen2.5": "ask",
    "qwen-2.5-7b": "ask",
    "qwen2.5-7b": "ask",
    "qwen-3": "process",
    "qwen3": "process",
    # Reasoning-deep models → process stream (R-CCAM 5-stage)
    "deepseek-v4": "process",
    "deepseek-chat": "process",
    "deepseek-v3": "process",
    # Long-context → process stream
    "gemini-3-flash": "process",
    "gemini-2.0-flash": "process",
    # Local / MeMo flows
    "lfm-2.5-1.2b": "memo",
    "lfm2.5-1.2b": "memo",
    "lfm2.5-1.2b-instruct": "memo",
    "lfm2.5-1.2b-thinking": "memo",
    # Default fallback
    "default": "ask",
}


def _pick_stream_kind(model: str) -> str:
    """Resolve model name → stream kind ('ask' | 'process' | 'memo' | 'plan')."""
    if not model:
        return "ask"
    m = model.lower().strip()
    return _MODEL_TO_STREAM.get(m, "ask")


# ── SidecarBackend ─────────────────────────────────────────────────────

class SidecarBackend:
    """Duck-typed LLM backend backed by SidecarHandlers.

    Implements the protocol that ``galaxyos.harness.deep_agent.DeepAgent``
    expects: an async ``.chat(messages) -> str`` and a ``.backend_name()``
    method. Internally it routes to the appropriate ``stream_*_frag``
    method of SidecarHandlers and concatenates the resulting TokUI DSL
    fragments into a plain string for the agent.
    """
    def __init__(self, handlers: Any, model: str = "default") -> None:
        self._handlers = handlers
        self._model = model
        self._stream_kind = _pick_stream_kind(model)
        log.info("SidecarBackend initialised: model=%s kind=%s", model, self._stream_kind)

    # ── Public API (consumed by DeepAgent._ask_llm) ───────────────────

    async def chat(self, messages: List[Dict[str, str]],
                   temperature: float = 0.7,
                   session_id: str = "default",
                   **kwargs) -> str:
        """One-shot chat. Returns a plain string (last meaningful fragment).

        Joins all DSL fragments, strips the TokUI tags, and returns the
        de-tagged text. The agent doesn't need the DSL structure — it
        just needs the answer text.
        """
        fragments = await self._fragments(messages, temperature, session_id)
        return _fragments_to_text(fragments)

    async def stream(self, messages: List[Dict[str, str]],
                     temperature: float = 0.7,
                     session_id: str = "default",
                     **kwargs) -> AsyncIterator[str]:
        """Async generator yielding one DSL fragment at a time.

        This is the **preferred** path for the harness — it preserves
        the streaming UX (think-step pending→running→done visible
        in real time). DeepAgent._ask_llm currently uses .chat();
        a future v9.2 will switch to .stream().
        """
        fragments = await self._fragments(messages, temperature, session_id)
        for f in fragments:
            yield f

    def backend_name(self) -> str:
        engine_name = "unknown"
        try:
            health = self._handlers.health({})
            engine_name = health.get("memo_backend", "unknown")
        except Exception:
            pass
        return f"SidecarBackend(model={self._model}, engine={engine_name})"

    def is_sidecar(self) -> bool:
        return True

    # ── Internal ──────────────────────────────────────────────────────

    async def _fragments(self, messages: List[Dict[str, str]],
                         temperature: float,
                         session_id: str) -> List[str]:
        """Dispatch to the right SidecarHandlers.stream_*_frag method."""
        # Extract the last user message as the prompt
        prompt = _last_user_message(messages)
        if not prompt:
            prompt = ""
        # Run the synchronous SidecarHandlers call in a thread
        # (it has its own asyncio.run() inside for some paths)
        import asyncio
        kind = self._stream_kind
        loop = asyncio.get_event_loop()
        try:
            if kind == "ask":
                fragments = await loop.run_in_executor(
                    None, self._handlers.stream_ask_frag, prompt, session_id
                )
            elif kind == "process":
                fragments = await loop.run_in_executor(
                    None, self._handlers.stream_process_frag, prompt, session_id
                )
            elif kind == "memo":
                fragments = await loop.run_in_executor(
                    None, self._handlers.stream_memo_frag, prompt
                )
            elif kind == "plan":
                # plan doesn't take a session_id
                fragments = await loop.run_in_executor(
                    None, self._handlers._do_stream_plan, prompt
                )
            else:
                log.warning("unknown stream kind %r; falling back to ask", kind)
                fragments = await loop.run_in_executor(
                    None, self._handlers.stream_ask_frag, prompt, session_id
                )
        except Exception as e:
            log.error("SidecarBackend._fragments failed (kind=%s): %s", kind, e)
            # Fall back to ask stream
            fragments = await loop.run_in_executor(
                None, self._handlers.stream_ask_frag, prompt, session_id
            )
        return fragments or []


# ── Public factory ─────────────────────────────────────────────────────

_cached_handlers: Optional[Any] = None


def build_sidecar_backend(model: str = "default") -> Optional[SidecarBackend]:
    """Build a SidecarBackend if SidecarHandlers is importable, else None.

    Caches the handlers instance across calls (same engine load).
    """
    global _cached_handlers
    HandlersCls = _load_sidecar_handlers()
    if HandlersCls is None:
        return None
    if _cached_handlers is None:
        try:
            _cached_handlers = HandlersCls()
        except Exception as e:
            log.warning("SidecarHandlers() ctor failed: %s", e)
            return None
    return SidecarBackend(_cached_handlers, model=model)


# ── Helpers ────────────────────────────────────────────────────────────

def _last_user_message(messages: List[Dict[str, str]]) -> str:
    """Return the last user-role message text from a chat-history list."""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
            return str(content) if content else ""
    return ""


def _fragments_to_text(fragments: List[str]) -> str:
    """Concatenate TokUI DSL fragments and strip the bracket tags.

    The agent doesn't render DSL; it just needs the answer text. This
    is a best-effort strip — removes [tag ...] and [/tag] markers,
    keeps the inner text. We use the same _esc rule from tokui_dsl.
    """
    import re
    out: List[str] = []
    for f in fragments or []:
        if not isinstance(f, str):
            continue
        s = f
        # 1. Replace container pairs: [tag attrs]...[/tag]  → inner text
        s = re.sub(r"\[\w[\w\-]*(?:\s+[^\]]*)?\](.*?)\[/\w[\w\-]*\]",
                   r"\1", s, flags=re.DOTALL)
        # 2. Strip self-closing / leaf tags: [tag attrs]
        s = re.sub(r"\[\w[\w\-]*(?:\s+[^\]]*)?\]", "", s)
        # 3. Strip any orphan closing tags still in the string
        s = re.sub(r"\[/\w[\w\-]*\]", "", s)
        s = s.strip()
        if s:
            out.append(s)
    text = "\n".join(out).strip()
    return text or "(no response from sidecar)"


__all__ = [
    "SidecarBackend",
    "build_sidecar_backend",
    "_MODEL_TO_STREAM",
    "_pick_stream_kind",
]
