"""memo_stages.py — MeMo 3-stage protocol orchestrator.

This implements the protocol from arXiv:2605.15156 Section 4.4:

  Stage 1 — Grounding
    Executive decomposes the user question into J atomic
    clue-probing sub-questions. The Memory model answers each
    independently (no shared context). Result: J compact NL
    snippets {m_1, ..., m_J}.

  Stage 2 — Entity identification
    Executive iteratively issues targeted follow-ups to the
    Memory model to narrow a candidate entity set. Loop exits
    when (a) Executive converges on a single entity e*, or (b)
    the stage budget is exhausted.

  Stage 3 — Answer seeking & synthesis
    Conditioned on q, {m_j}, and e*, Executive issues further
    follow-ups to gather supporting facts m_seek, then
    composes the final answer â.

    â = Executive(q, {m_j}, e*, m_seek)

For the Stage 3 demo:
  - Memory model: MockMeMoAdapter (in memo_adapter.py)
  - Executive model: MockExecutiveClient (in executive_client.py)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from memo_adapter import MeMoAdapter, MockMeMoAdapter
from executive_client import ExecutiveClient, MockExecutiveClient

log = logging.getLogger("galaxyos-memo-stages")


# ── Stage results ──────────────────────────────────────────────────

@dataclass
class GroundingResult:
    sub_questions: List[str] = field(default_factory=list)
    answers: List[str] = field(default_factory=list)
    """Parallel lists. answers[i] is the Memory-model response to sub_questions[i]."""


@dataclass
class EntityResult:
    candidates: List[tuple] = field(default_factory=list)
    """List of (name, confidence) tuples, sorted by confidence desc."""
    chosen: Optional[str] = None
    """Final entity name (None = no convergence)."""


@dataclass
class AnswerResult:
    supporting_facts: List[str] = field(default_factory=list)
    final_answer: str = ""


@dataclass
class MeMoTrace:
    """Full trace of a 3-stage MeMo protocol execution.

    Streamed to the renderer as DSL fragments so the user sees
    Grounding → Entity → Answer in real time.
    """
    grounding: GroundingResult
    entity: EntityResult
    answer: AnswerResult


# ── Protocol orchestrator ──────────────────────────────────────────

class MeMoProtocol:
    """Runs the 3-stage MeMo protocol end-to-end.

    Each stage is bounded by a per-stage interaction budget
    (default 8 sub-questions for Grounding, 6 for Entity
    follow-ups, 6 for Answer follow-ups). Each is also bounded
    by an overall timeout (default 30s) so a single stuck
    Executive call doesn't hang the UI.
    """

    def __init__(
        self,
        memo: MeMoAdapter,
        executive: ExecutiveClient,
        *,
        grounding_budget: int = 8,
        entity_budget: int = 6,
        answer_budget: int = 6,
        overall_timeout_s: float = 30.0,
    ):
        self.memo = memo
        self.executive = executive
        self.gb = grounding_budget
        self.eb = entity_budget
        self.ab = answer_budget
        self.timeout_s = overall_timeout_s

    async def run(self, question: str) -> MeMoTrace:
        """Execute the full 3-stage protocol with an overall timeout."""
        try:
            return await asyncio.wait_for(
                self._run_inner(question),
                timeout=self.timeout_s,
            )
        except asyncio.TimeoutError:
            log.error(f"3-stage protocol timed out after {self.timeout_s}s")
            return MeMoTrace(
                grounding=GroundingResult(),
                entity=EntityResult(),
                answer=AnswerResult(
                    final_answer=f"[MeMo 3-stage timeout after {self.timeout_s}s] "
                                 f"Memory model: {self.memo.backend_name()}"
                ),
            )

    async def _run_inner(self, question: str) -> MeMoTrace:
        # ── Stage 1: Grounding ─────────────────────────────────
        sub_qs = await self.executive.decompose(
            question=question,
            max_sub_questions=self.gb,
        )
        # Call Memory model in parallel for each sub-question
        answers = await asyncio.gather(*[
            self.memo.answer(q) for q in sub_qs
        ])
        grounding = GroundingResult(sub_questions=sub_qs, answers=answers)
        log.info(f"Grounding: {len(sub_qs)} sub-qs, "
                 f"answers joined: {' '.join(answers)[:100]}...")

        # ── Stage 2: Entity identification ─────────────────────
        candidates, chosen = await self.executive.identify_entity(
            question=question,
            grounding_answers=answers,
            max_followups=self.eb,
        )
        entity = EntityResult(candidates=candidates, chosen=chosen)
        log.info(f"Entity: chosen={chosen}, candidates={candidates[:3]}")

        # ── Stage 3: Answer seeking & synthesis ─────────────────
        supporting, final = await self.executive.synthesize(
            question=question,
            grounding_answers=answers,
            chosen_entity=chosen,
            max_followups=self.ab,
        )
        answer = AnswerResult(supporting_facts=supporting, final_answer=final)
        log.info(f"Answer: {len(supporting)} supporting facts, "
                 f"final answer length: {len(final)}")

        return MeMoTrace(grounding=grounding, entity=entity, answer=answer)


# ── Convenience: default constructor ──────────────────────────────

def default_protocol() -> MeMoProtocol:
    """Construct the default protocol with Mock backends.

    Swap the adapters for real ONNX (memo) and DeepSeek (exec) to
    get the production system.
    """
    return MeMoProtocol(
        memo=MockMeMoAdapter(),
        executive=MockExecutiveClient(),
    )
