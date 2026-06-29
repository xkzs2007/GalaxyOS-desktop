"""ac_router.py — Agent-as-a-Router (arXiv:2606.22902) C-A-F loop.

Implements the 3 components + the C-A-F closed loop from the paper:

  C → A → F → (memorize) → C' → A' → F' → ...

  Context:  query + task metadata + kNN neighbours from Memory
  Action:   Orchestrator picks 1 of N expert strategies
            (fast_path / liquid_only / memo_3stage / process_5_stage)
  Feedback: Verifier scores the result with 4 signals
  Memorize: commit the (emb, action, score, cost, trace) to Memory

The default Orchestrator in this build is a heuristic policy
(rule-based scoring) — when Qwen3.5-0.8B LoRA weights are
available, swap in OrchestratorLoRA. Same for the Verifier
signals: we use deterministic placeholders that approximate
the paper's 4 signals.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("galaxyos-acrouter")


# ── Action space ───────────────────────────────────────────────────

# The 4 expert strategies our router can pick from. Each is an
# existing piece of the desktop shell — ACRouter just chooses
# between them based on the question's shape.
ACTIONS = (
    "fast_path",      # ask() — single recall + answer
    "liquid_only",    # liquid layer only (memory + DAG + synapse)
    "memo_3stage",    # MeMo 3-stage (Grounding → Entity → Answer)
    "process_5_stage" # full R-CCAM (Retrieval → Cognition → ...)
)
ACTION_DEFAULT = "fast_path"


# ── Verifier (4-signal multi-signal aggregator) ──────────────────

@dataclass
class VerifierSignals:
    """Per the paper Section 4 (Eq. 8):

        u_i = Σ_k w_{d(t),k} · s_k(a_i, t_i)

    We compute 4 signals per action result and combine with
    default weights. For Stage 3, the weights are hard-coded
    per task type ("d" is the dimension).
    """
    # Signal 1: AST / structural validity (1.0 if well-formed DSL, else 0.0)
    s_structural: float = 0.0
    # Signal 2: Sandbox execution (1.0 if tool ran without error)
    s_sandbox: float = 0.0
    # Signal 3: Self-consistency (1.0 if multi-sample answer agrees)
    s_consistency: float = 0.0
    # Signal 4: LLM-as-judge (1.0 if the judge LLM rates it correct)
    s_judge: float = 0.0

    def score(self, task_type: str = "factual") -> float:
        """Combine signals with default weights."""
        # Default weights (from paper Appendix C, simplified)
        w_struct = 0.1
        w_sandbox = 0.2
        w_consist = 0.3
        w_judge = 0.4
        return (
            w_struct * self.s_structural
            + w_sandbox * self.s_sandbox
            + w_consist * self.s_consistency
            + w_judge * self.s_judge
        )


# ── Orchestrator ──────────────────────────────────────────────────

class Orchestrator(ABC):
    """Decides which action to take for a given context.

    The paper's Qwen3.5-0.8B LoRA policy is the production target.
    For Stage 3 we use a heuristic that scores each action by
    cheap signals and picks the highest.
    """

    @abstractmethod
    async def decide(
        self,
        query: str,
        task_metadata: Dict[str, Any],
        k_neighbors: List[Dict[str, Any]],
    ) -> str:
        """Return one of ACTIONS."""
        raise NotImplementedError

    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError


class HeuristicOrchestrator(Orchestrator):
    """Rule-based orchestrator (Stage 3 default).

    Heuristic:
      - If the query is a factual lookup that matches a known
        entity (GalaxyOS / R-CCAM / MeMo / etc.), prefer
        memo_3stage (parametric retrieval is faster and more
        accurate than RAG for these).
      - If the query is "do X with Y" or starts with ! or mentions
        shell / file / grep keywords, prefer process_5_stage
        (the existing R-CCAM RAG path; tool-capable via Agent).
      - If the query is conversational / vague, prefer
        fast_path (cheap, single recall).
      - Else default: liquid_only (use the liquid network only).
    """

    def __init__(self):
        # Lightweight classifier signals
        self.memo_keywords = {
            "galaxyos", "r-ccam", "memo", "tokui",
            "agent-as-a-router", "openclaw", "xiao yi", "xiaoyi",
        }
        self.tool_keywords = {
            "shell", "sh", "$", "!", "run:", "执行",
            "read", "cat", "看", "read_file",
            "write", "保存", "edit", "create",
            "grep", "搜索", "find", "找",
            "list", "ls", "列出",
            "diff", "patch", "edit_file",
        }

    async def decide(
        self,
        query: str,
        task_metadata: Dict[str, Any],
        k_neighbors: List[Dict[str, Any]],
    ) -> str:
        lower = query.lower()

        # 1) Strong tool signal
        if any(k in lower for k in self.tool_keywords):
            # The user clearly wants tool execution. The existing
            # /sse/agent endpoint is best; ACRouter just routes
            # the orchestrator's call to it.
            return "process_5_stage"

        # 2) If a strong memory match exists in the kNN neighbors
        #    (a previous similar question with high score), use it
        if k_neighbors and k_neighbors[0].get("score", 0) > 0.85:
            return k_neighbors[0].get("action", ACTION_DEFAULT)

        # 3) Factual entity lookup → memo_3stage
        if any(k in lower for k in self.memo_keywords):
            return "memo_3stage"

        # 4) Very short / vague query → fast_path
        if len(query.strip()) <= 4 or query.endswith("?"):
            return "fast_path"

        # 5) Default
        return "liquid_only"

    def name(self) -> str:
        return "HeuristicOrchestrator"


# ── Memory (online vector store) ─────────────────────────────────

class Memory:
    """Online vector store per paper Section 4.2.

    BGE-large embeddings, FIFO at 20K, cosine kNN k=10. We ship a
    small in-process implementation backed by a JSONL file. For
    Stage 3 we compute embeddings via a simple Bag-of-Words
    fallback (no GPU model needed); swap in the real BGE-large
    encoder when GPU is available.
    """

    def __init__(self, max_size: int = 20_000, dim: int = 128,
                 store_path: Optional[Path] = None):
        self.max_size = max_size
        self.dim = dim
        self.store_path = store_path or (Path.home() / ".galaxyos" /
                                         "workspace" / "router_memory.jsonl")
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: List[Dict[str, Any]] = []  # [{key, emb, action, score, cost, trace, ts}]
        self._load()

    def _load(self) -> None:
        if not self.store_path.exists():
            return
        try:
            with self.store_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    self.entries.append(json.loads(line))
            log.info(f"Memory: loaded {len(self.entries)} entries")
        except Exception as e:
            log.warning(f"Memory: load failed: {e}")

    def _save(self) -> None:
        try:
            with self.store_path.open("w", encoding="utf-8") as f:
                for e in self.entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning(f"Memory: save failed: {e}")

    def _embed(self, text: str) -> List[float]:
        """Bag-of-Words embedding (Stage 3 placeholder).

        Tokenize, hash each token to a position in [0, dim), and
        produce a unit-norm sparse vector. Same shape as a real
        BGE-large embedding (1-D float vector of dim), but
        without semantic content. Real BGE-large drops in by
        replacing this method.
        """
        import math
        import re
        vec = [0.0] * self.dim
        for tok in re.findall(r"\w+", text.lower()):
            h = hash(tok) % self.dim
            vec[h] += 1.0
        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _cosine(self, a: List[float], b: List[float]) -> float:
        return sum(x * y for x, y in zip(a, b))  # already unit-norm

    def k_nearest(self, query: str, k: int = 10, sim_threshold: float = 0.3) -> List[Dict[str, Any]]:
        """Top-k nearest neighbours by cosine similarity.

        Default sim_threshold=0.3 (lower than the paper's 0.5) to
        compensate for our BoW embedder being less discriminative
        than BGE-large. Real BGE-large can use the paper's 0.5.
        """
        if not self.entries:
            return []
        qv = self._embed(query)
        scored = []
        for e in self.entries:
            sim = self._cosine(qv, e.get("emb", []))
            if sim >= sim_threshold:
                scored.append({**e, "score": sim})
        scored.sort(key=lambda x: -x["score"])
        return scored[:k]

    def commit(self, key: str, value: Dict[str, Any]) -> None:
        """Commit a new entry; FIFO if over max_size."""
        emb = self._embed(key)
        entry = {
            "key": key,
            "emb": emb,
            **value,
            "ts": time.time(),
        }
        self.entries.append(entry)
        if len(self.entries) > self.max_size:
            # FIFO eviction
            self.entries = self.entries[-self.max_size:]
        self._save()

    def size(self) -> int:
        return len(self.entries)


# ── C-A-F loop ───────────────────────────────────────────────────

@dataclass
class CAFResult:
    """Result of a single C-A-F iteration."""
    query: str
    chosen_action: str
    answer: str
    confidence: float
    cost: float  # USD-equivalent (paper reward = score - 0.1*cost)
    verifier_signals: VerifierSignals
    k_neighbors: List[Dict[str, Any]] = field(default_factory=list)
    trace: Dict[str, Any] = field(default_factory=dict)


class CAFRouter:
    """Context → Action → Feedback → (memorize) closed loop.

    For Stage 3 we run exactly one iteration per query (the paper
    streams over task streams; for a single-turn desktop app, one
    loop is enough). Multi-iteration mode is opt-in via
    `max_iterations > 1`.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        memory: Memory,
        executor: Callable[[str, str], Awaitable[Any]],
    ):
        """executor(action, query) -> answer string + VerifierSignals.

        The executor wraps the actual expert (fast_path = ask(),
        memo_3stage = memo_stages, process_5_stage = process() / agent()).
        """
        self.orch = orchestrator
        self.memory = memory
        self.executor = executor

    async def route(
        self,
        query: str,
        task_metadata: Optional[Dict[str, Any]] = None,
    ) -> CAFResult:
        """Single C-A-F iteration."""
        task_metadata = task_metadata or {}

        # ── Context ─────────────────────────────────────────
        k_neighbors = self.memory.k_nearest(query, k=10, sim_threshold=0.5)
        log.info(f"C: k={len(k_neighbors)} neighbours for q='{query[:30]}...'")

        # ── Action ──────────────────────────────────────────
        action = await self.orch.decide(query, task_metadata, k_neighbors)
        log.info(f"A: {action}")

        # ── Execute + Feedback ──────────────────────────────
        t0 = time.time()
        try:
            result = await self.executor(action, query)
        except Exception as e:
            log.error(f"executor failed: {e}")
            result = {"answer": f"[ACRouter executor error: {e}]",
                      "signals": VerifierSignals(),
                      "cost": 0.0}
        cost = time.time() - t0
        if isinstance(result, dict):
            answer = str(result.get("answer", ""))
            signals = result.get("signals") or VerifierSignals()
            cost_usd = float(result.get("cost", 0.0))
        else:
            # Backward compat: executor returned a string
            answer = str(result)
            signals = VerifierSignals(s_structural=0.7, s_sandbox=0.5,
                                      s_consistency=0.5, s_judge=0.5)
            cost_usd = cost * 0.001  # 1ms = 0.001 USD
        score = signals.score()
        log.info(f"F: score={score:.2f} cost={cost:.2f}s")

        # ── Memorize ────────────────────────────────────────
        self.memory.commit(
            key=query,
            value={
                "action": action,
                "score": score,
                "cost": cost_usd,
                "trace": {"task_type": task_metadata.get("type", "factual")},
            },
        )

        return CAFResult(
            query=query,
            chosen_action=action,
            answer=answer,
            confidence=score,
            cost=cost_usd,
            verifier_signals=signals,
            k_neighbors=k_neighbors,
            trace={"iteration": 1, "task_type": task_metadata.get("type", "factual")},
        )


# ── Default factory ──────────────────────────────────────────────

from typing import Awaitable

def default_router(executor: Callable[[str, str], Awaitable[Any]]) -> CAFRouter:
    return CAFRouter(
        orchestrator=HeuristicOrchestrator(),
        memory=Memory(),
        executor=executor,
    )
