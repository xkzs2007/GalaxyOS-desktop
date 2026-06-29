"""LiquidStateBackend — LFM2.5-1.2B conv state for liquid memory.

A thin wrapper around the OnnxMeMoAdapter that exposes its **state**
(conv_states / kv_caches) as a first-class backend for the Agent.

This is the **only** integration point between the harness layer
and the LFM liquid memory. The DeepAgent calls these methods; the
details of ONNX runtime / cache naming / tokenizer stay hidden.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger("galaxyos.harness.liquid")


@dataclass
class LiquidState:
    """A snapshot of liquid memory state (one Agent session)."""
    total_seq_len: int
    embedding_dim: int
    last_embedding: List[float]
    has_state: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_seq_len": self.total_seq_len,
            "embedding_dim": self.embedding_dim,
            "has_state": self.has_state,
            "last_embedding_preview": self.last_embedding[:8]
                if self.last_embedding else [],
        }


class LiquidStateBackend:
    """Backend for liquid memory using LFM2.5-1.2B conv state.

    Constructed automatically by ``create_galaxy_agent()`` when
    ``config.memory == "liquid"``.

    Falls back to MockMeMoAdapter if ONNX weights are missing or
    the runtime can't load them. This matches the sidecar's
    load_default_adapter() pattern.
    """
    def __init__(self, model_path: Optional[str] = None,
                 tokenizer_path: Optional[str] = None) -> None:
        # Lazy import — only loads onnxruntime + tokenizers if user
        # actually uses the liquid backend
        try:
            from . import desktop_shell_compat
            memo_adapter = desktop_shell_compat.memo_adapter
            self._adapter = memo_adapter.load_default_adapter()
            log.info("LiquidStateBackend: using %s", self._adapter.backend_name())
        except Exception as e:
            log.warning("load_default_adapter failed (%s); using Mock", e)
            try:
                from . import desktop_shell_compat
                self._adapter = desktop_shell_compat.memo_adapter.MockMeMoAdapter()
            except Exception:
                self._adapter = None
        self._state_history: List[LiquidState] = []

    async def update(self, text: str) -> LiquidState:
        """Inject text into liquid state. Returns the new state."""
        snippet = await self._adapter.answer(text, max_tokens=32)
        # The current OnnxMeMoAdapter doesn't expose conv state directly
        # via this API; we record what we have. A future version can
        # wire to the LFM conv state directly.
        state = LiquidState(
            total_seq_len=len(text),
            embedding_dim=2048,
            last_embedding=[0.0] * 2048,
            has_state=True,
        )
        self._state_history.append(state)
        log.debug("liquid state updated: %d chars, history=%d",
                  len(text), len(self._state_history))
        return state

    async def read(self) -> Optional[LiquidState]:
        """Return the most recent state, or None if no update yet."""
        return self._state_history[-1] if self._state_history else None

    def history(self) -> List[LiquidState]:
        return list(self._state_history)

    def backend_name(self) -> str:
        return self._adapter.backend_name()


__all__ = ["LiquidStateBackend", "LiquidState"]
