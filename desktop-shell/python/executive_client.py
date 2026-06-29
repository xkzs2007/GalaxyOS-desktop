"""executive_client.py — Executive model client for MeMo 3-stage protocol.

The "Executive" is the black-box large LLM that orchestrates the
3-stage MeMo protocol (Grounding → Entity → Answer). Per the
paper, this can be Qwen2.5-32B, Gemini-3-Flash, or any
reasonably-capable LLM that can do tool-use.

In production this would call DeepSeek's API (OpenAI-compatible).
For Stage 3 development we ship a deterministic Mock that does
the protocol orchestration with hard-coded heuristics — same
external behavior, no API key needed.
"""
from __future__ import annotations

import asyncio
import logging
import re
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

log = logging.getLogger("galaxyos-exec")


# ── Interface ─────────────────────────────────────────────────────

class ExecutiveClient(ABC):
    """The Executive model half of MeMo.

    Three methods, one per stage. All are async because real LLM
    APIs are async; the Mock uses sleep(0) to keep the same shape.
    """

    @abstractmethod
    async def decompose(self, question: str, *, max_sub_questions: int) -> List[str]:
        """Stage 1: produce atomic clue-probing sub-questions."""
        raise NotImplementedError

    @abstractmethod
    async def identify_entity(
        self,
        question: str,
        grounding_answers: List[str],
        *,
        max_followups: int,
    ) -> Tuple[List[Tuple[str, float]], Optional[str]]:
        """Stage 2: iterate to narrow entity set; return (candidates, chosen)."""
        raise NotImplementedError

    @abstractmethod
    async def synthesize(
        self,
        question: str,
        grounding_answers: List[str],
        chosen_entity: Optional[str],
        *,
        max_followups: int,
    ) -> Tuple[List[str], str]:
        """Stage 3: gather supporting facts + compose final answer."""
        raise NotImplementedError

    @abstractmethod
    def backend_name(self) -> str:
        raise NotImplementedError


# ── Mock implementation (deterministic, no API key) ─────────────

# Reuse the MeMo mock corpus so the Executive's "reasoning" is
# consistent with what the Memory model returns.
from memo_adapter import MOCK_CORPUS


def _find_entity(question: str, grounding_answers: List[str]) -> Optional[str]:
    """Decide which entity (if any) the question is about.

    Heuristic: scan the question + grounding answers for any
    entity name from MOCK_CORPUS. Return the most-mentioned one.
    """
    text = (question + " " + " ".join(grounding_answers)).lower()
    scores = {}
    for entity in MOCK_CORPUS:
        # Count entity mentions
        scores[entity] = text.count(entity.lower())
    if not scores or max(scores.values()) == 0:
        return None
    return max(scores, key=scores.get)


class MockExecutiveClient(ExecutiveClient):
    """Deterministic, in-process Executive for the 3-stage protocol.

    Decomposes questions into atomic sub-questions using regex +
    template, identifies entities by string matching, and
    synthesizes answers by templating the grounding snippets.
    """

    def __init__(self, latency_ms: int = 12):
        self._latency_ms = latency_ms
        self._call_count = 0

    async def _sim(self) -> None:
        self._call_count += 1
        await asyncio.sleep(self._latency_ms / 1000.0)

    async def decompose(self, question: str, *, max_sub_questions: int) -> List[str]:
        """Stage 1: split the question into atomic sub-questions.

        Heuristic: produce 3-5 sub-questions that probe for the
        key fact, source, time, definition, and example.
        """
        await self._sim()
        entity = _find_entity(question, [])
        # Standard probe set
        sub_qs = [
            f"What is {entity}" if entity else question,
            f"Who {entity or 'this'} is associated with" if entity else "Who is the source",
            "When was this established",
            "What is the canonical definition",
        ]
        return sub_qs[:max_sub_questions]

    async def identify_entity(
        self,
        question: str,
        grounding_answers: List[str],
        *,
        max_followups: int,
    ) -> Tuple[List[Tuple[str, float]], Optional[str]]:
        """Stage 2: pick the entity most strongly supported by
        the grounding evidence.
        """
        await self._sim()
        # Count how many grounding answers mention each entity
        joined = " ".join(grounding_answers).lower()
        candidates: List[Tuple[str, float]] = []
        for entity in MOCK_CORPUS:
            count = joined.count(entity.lower())
            if count > 0:
                # Confidence = sigmoid-like scaling
                conf = min(1.0, 0.4 + count * 0.2)
                candidates.append((entity, conf))
        candidates.sort(key=lambda x: -x[1])
        chosen = candidates[0][0] if candidates else _find_entity(question, grounding_answers)
        return candidates[:5], chosen

    async def synthesize(
        self,
        question: str,
        grounding_answers: List[str],
        chosen_entity: Optional[str],
        *,
        max_followups: int,
    ) -> Tuple[List[str], str]:
        """Stage 3: gather 1-2 supporting facts + compose answer."""
        await self._sim()
        # Take the first 2 grounding answers as supporting facts
        supporting = [a for a in grounding_answers if a and "No specific" not in a][:2]
        # Synthesize
        if not supporting:
            final = (
                f"我不知道「{question}」的准确答案。"
                f"（Mock MeMo 3-stage：未找到 grounding 证据）"
            )
        else:
            best = supporting[0]
            extras = " " + " ".join(supporting[1:]) if len(supporting) > 1 else ""
            final = (
                f"**{chosen_entity or '相关概念'}**\n\n"
                f"{best}{extras}\n\n"
                f"---\n*（通过 MeMo 3-stage 协议生成：Grounding → Entity({chosen_entity}) → Answer，"
                f"Memory model: Mock parametric corpus，"
                f"{len(grounding_answers)} 个 grounding 证据 / {len(supporting)} 个 supporting fact）*"
            )
        return supporting, final

    def backend_name(self) -> str:
        return "Executive (Mock, deterministic)"

    @property
    def call_count(self) -> int:
        return self._call_count


# ── Optional: real DeepSeek / OpenAI client (placeholder) ─────────

class DeepSeekExecutiveClient(ExecutiveClient):
    """Production client for DeepSeek (OpenAI-compatible).

    Inherits the same 3-stage shape but calls DeepSeek's API
    using a system prompt that encodes the protocol. Requires
    ``DEEPSEEK_API_KEY`` in the environment.
    """

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        self.api_key = api_key
        self.model = model
        self._client = None  # openai.OpenAI, lazy

    def _ensure(self) -> None:
        if self._client is not None:
            return
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("openai package not installed") from e
        self._client = OpenAI(api_key=self.api_key)

    async def _chat(self, system: str, user: str) -> str:
        # Synchronous call wrapped in thread; real apps should use
        # the AsyncOpenAI client.
        import asyncio
        self._ensure()
        def _call() -> str:
            r = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=512,
                temperature=0.0,
            )
            return r.choices[0].message.content or ""
        return await asyncio.to_thread(_call)

    async def decompose(self, question, *, max_sub_questions):
        out = await self._chat(
            "你是一个 MeMo 3-stage 协议的执行器。"
            "把用户问题分解成最多 N 个原子子问题，每个子问题针对一个独立线索。",
            f"问题: {question}\n最多 {max_sub_questions} 个子问题。",
        )
        # Parse: one per line
        sub_qs = [line.strip(" -1234567890.").strip()
                  for line in out.splitlines() if line.strip()]
        return sub_qs[:max_sub_questions]

    async def identify_entity(self, question, grounding_answers, *, max_followups):
        joined = "\n".join(f"- {a}" for a in grounding_answers)
        out = await self._chat(
            "你是一个 MeMo 3-stage 协议的执行器。"
            "根据用户问题和 grounding 证据，识别最可能的实体。"
            "按 JSON 数组返回候选: [[name, confidence], ...]，并给出 chosen 字段。",
            f"问题: {question}\nGrounding 证据:\n{joined}",
        )
        import json
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = {"candidates": [], "chosen": None}
        cands = [tuple(c) for c in data.get("candidates", [])][:5]
        return cands, data.get("chosen")

    async def synthesize(self, question, grounding_answers, chosen_entity, *, max_followups):
        joined = "\n".join(f"- {a}" for a in grounding_answers)
        out = await self._chat(
            "你是一个 MeMo 3-stage 协议的执行器。"
            f"基于用户问题 + grounding 证据 + 识别实体 ({chosen_entity})，"
            "用中文合成最终答案。",
            f"问题: {question}\n实体: {chosen_entity}\n证据:\n{joined}",
        )
        # Use the grounding answers themselves as supporting facts
        supporting = [a for a in grounding_answers if a and "No specific" not in a][:2]
        return supporting, out

    def backend_name(self) -> str:
        return f"Executive (DeepSeek {self.model})"
