"""memo_adapter.py — MeMo (Memory as a Model) adapter interface.

This module implements the **abstract layer** for the MeMo paper
(arXiv:2605.15156, "Memory as a Model"). It defines:

  - `MeMoAdapter`           : the interface every backend must satisfy
  - `MockMeMoAdapter`       : deterministic in-process backend
  - `OnnxMeMoAdapter`       : ONNX runtime backend (Stage 3, optional)

The actual SFT weights (~900 MB INT4 ONNX) are out of scope for this
in-process build; the Mock backend returns canned answers that
exercise the 3-stage protocol end-to-end so the rest of the system
behaves exactly as it will once the real weights are loaded.

Real MeMo inference is `constant-time` and `independent of corpus
size` — the Memory model has parametrically internalized the corpus
during SFT, so at inference we never touch the documents again. The
Mock backend emulates this by computing answers from a fixed lookup
table — same O(1) characteristic.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("galaxyos-memo")


# ── Data shapes ────────────────────────────────────────────────────

@dataclass
class GroundingQuestion:
    """One atomic sub-question produced by Stage 1 (Grounding)."""
    id: int
    text: str
    """Natural language question targeting a single clue."""


@dataclass
class GroundingAnswer:
    """A Memory-model response to one Grounding sub-question."""
    text: str
    """Compact natural-language snippet, independent of corpus size."""


@dataclass
class EntityCandidate:
    """One candidate entity the Executive model is iterating over."""
    name: str
    """Entity name (e.g. "Qwen-2.5", "GalaxyOS")."""
    confidence: float
    """0..1, how strongly the grounding evidence points to this entity."""


# ── Adapter interface ─────────────────────────────────────────────

class MeMoAdapter(ABC):
    """The Memory-model half of the MeMo architecture.

    Per arXiv:2605.15156 Section 4.1, the Memory model is a *frozen*
    SFT'd model that has parametrically internalized the corpus. At
    inference it produces short natural-language snippets in
    response to short questions — constant-time, independent of
    |corpus|.

    Implementations must be safe to call from a running event loop
    (i.e. NOT `asyncio.run` internally).
    """

    @abstractmethod
    async def answer(self, question: str, *, max_tokens: int = 96) -> str:
        """Answer a single atomic Grounding sub-question.

        Returns a compact natural-language snippet (NOT a full
        answer). The Executive model uses these snippets to build
        the final response.
        """
        raise NotImplementedError

    @abstractmethod
    async def is_loaded(self) -> bool:
        """Whether the SFT weights are actually present."""
        raise NotImplementedError

    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable identifier (for UI display + logs)."""
        raise NotImplementedError


# ── Mock backend ──────────────────────────────────────────────────

# A small corpus the Mock "knows" by heart. Stage 3 unit tests use
# these exact facts to verify the 3-stage protocol returns them
# through Grounding → Entity → Answer.
MOCK_CORPUS: Dict[str, List[Tuple[str, str]]] = {
    "GalaxyOS": [
        ("What is GalaxyOS", "GalaxyOS is an open-source cognitive enhancement engine for OpenClaw, v8.6.0"),
        ("Who maintains GalaxyOS", "GalaxyOS is maintained by the llm-memory-integrat team on cnb.cool"),
        ("What does GalaxyOS do", "GalaxyOS provides memory, retrieval, reasoning, and self-evolution capabilities"),
        ("When was GalaxyOS released", "GalaxyOS v8.6.0 was released on 2026-06-28"),
    ],
    "R-CCAM": [
        ("What is R-CCAM", "R-CCAM is the five-stage cognitive loop in GalaxyOS: Retrieval, Cognition, Control, Action, Memory"),
        ("How many stages does R-CCAM have", "R-CCAM has 5 stages"),
        ("What does R-CCAM stand for", "R-CCAM stands for Retrieval Cognition Control Action Memory"),
    ],
    "MeMo": [
        ("What is MeMo", "MeMo is a memory-as-a-model architecture that trains a small SFT model to encode knowledge"),
        ("What does MeMo stand for", "MeMo stands for Memory as a Model"),
        ("Who proposed MeMo", "MeMo was proposed by Quek et al. in arXiv:2605.15156"),
    ],
    "TokUI": [
        ("What is TokUI", "TokUI is a streaming UI framework for AI agents by JBoltAI"),
        ("What is TokUI used for", "TokUI renders streaming DSL tokens as proper chat bubbles, tool calls, and code blocks"),
        ("Who makes TokUI", "TokUI is made by the JBoltAI team"),
    ],
    "Agent-as-a-Router": [
        ("What is Agent-as-a-Router", "Agent-as-a-Router is a routing framework that uses a C-A-F closed loop to pick the best LLM per task"),
        ("What is C-A-F", "C-A-F stands for Context Action Feedback, the closed loop in Agent-as-a-Router"),
    ],
}


def _extract_keywords(text: str) -> List[str]:
    """Pull probable entity names from a question.

    Heuristic: scan for capitalized words and known terms.
    """
    import re
    # Match capitalized words (English) and known terms
    candidates = set()
    for word in re.findall(r"\b[A-Z][a-zA-Z0-9-]{1,20}\b", text):
        candidates.add(word)
    # Match hyphenated terms
    for word in re.findall(r"\b[a-z]+-[a-z]+\b", text):
        candidates.add(word)
    # Lowercase lookup for "mem o" -> "memo" matches
    for known in MOCK_CORPUS:
        if known.lower() in text.lower():
            candidates.add(known)
    return list(candidates)


class MockMeMoAdapter(MeMoAdapter):
    """Deterministic, corpus-lookup MeMo backend.

    Used in Stage 3 development while we don't have actual SFT
    weights. Returns compact natural-language snippets that match
    the *form* of real MeMo output (short, factual, parametric).
    """

    def __init__(self, latency_ms: int = 8):
        self._latency_ms = latency_ms
        self._call_count = 0
        # Pre-build a single best-answer for each entity
        self._best_answer = {
            e: corpus[0][1] if corpus else f"No information about {e}."
            for e, corpus in MOCK_CORPUS.items()
        }

    async def answer(self, question: str, *, max_tokens: int = 96) -> str:
        import asyncio
        self._call_count += 1
        # Simulate inference latency
        await asyncio.sleep(self._latency_ms / 1000.0)

        keywords = _extract_keywords(question)
        # Try to find the most relevant entity
        for kw in keywords:
            for entity, facts in MOCK_CORPUS.items():
                if entity == kw or kw in entity:
                    # Find the best-matching fact for the question
                    question_lower = question.lower()
                    best = None
                    best_score = 0
                    for q, a in facts:
                        # Score by shared words
                        words = set(w for w in q.lower().split() if len(w) > 2)
                        q_words = set(w for w in question_lower.split() if len(w) > 2)
                        score = len(words & q_words)
                        if score > best_score:
                            best = a
                            best_score = score
                    if best:
                        return best[:max_tokens * 4]  # ~4 chars/token
                    return self._best_answer[entity][:max_tokens * 4]
        # Default: return a generic "I don't have that"
        return "No specific information found in the knowledge base."

    async def is_loaded(self) -> bool:
        return True

    def backend_name(self) -> str:
        return "MeMo (Mock backend, parametric corpus lookup)"

    @property
    def call_count(self) -> int:
        return self._call_count


# ── Optional: real ONNX backend (placeholder) ─────────────────────

class OnnxMeMoAdapter(MeMoAdapter):
    """Stub for the real ONNX Runtime backend.

    When the SFT weights are available at
    ``models/memo/qwen-1.5b-int4.onnx`` + ``tokenizer.json``, this
    adapter runs the real model. For now it raises NotImplementedError
    if .is_loaded() is called and no weights are present, so callers
    can fall back to Mock gracefully.
    """

    def __init__(self, model_path: str, tokenizer_path: str):
        self.model_path = model_path
        self.tokenizer_path = tokenizer_path
        self._session = None  # onnxruntime.InferenceSession, lazy
        self._tokenizer = None  # tokenizers.Tokenizer, lazy

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as e:
            raise RuntimeError(
                "onnxruntime + tokenizers not installed. "
                "Install via: pip install onnxruntime tokenizers"
            ) from e
        import os
        if not os.path.exists(self.model_path):
            raise FileNotFoundError(f"MeMo ONNX weights not found at {self.model_path}")
        self._session = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(self.tokenizer_path)

    async def answer(self, question: str, *, max_tokens: int = 96) -> str:
        # The real implementation: tokenize, run ONNX, decode
        # `max_tokens` worth of generated tokens. We keep it simple
        # here because the actual ONNX execution depends on the
        # SFT model's input/output names (which differ between
        # Qwen, Gemma, LFM families).
        import asyncio
        await asyncio.sleep(0)  # placeholder
        raise NotImplementedError(
            "OnnxMeMoAdapter is a placeholder. Use MockMeMoAdapter "
            "until the SFT weights are available, or wire up a real "
            "Qwen2.5 / Gemma3 / LFM2.5 generation loop here."
        )

    async def is_loaded(self) -> bool:
        import os
        return os.path.exists(self.model_path) and os.path.exists(self.tokenizer_path)

    def backend_name(self) -> str:
        return f"MeMo (ONNX Runtime @ {self.model_path})"


# ── Loader ─────────────────────────────────────────────────────────

def load_default_adapter() -> MeMoAdapter:
    """Pick the best available adapter.

    Order of preference:
      1. OnnxMeMoAdapter if models/memo/*.onnx exists
      2. MockMeMoAdapter (always available; deterministic)

    For the Stage 3 demo, we always return the Mock.
    """
    # Future: probe for ONNX weights and return OnnxMeMoAdapter
    # For now, return Mock
    log.info("Using MockMeMoAdapter (real SFT weights not yet available)")
    return MockMeMoAdapter()
