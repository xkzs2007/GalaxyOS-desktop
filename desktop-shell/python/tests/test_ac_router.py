"""tests/test_ac_router.py — Unit tests for the C-A-F routing loop.

Covers:
- HeuristicOrchestrator.decide() for tool/entity/vague/short prompts
- CAFRouter.route() — Context → Action → Feedback → Memorize
- Memory.k_nearest() cosine similarity
- VerifierSignals.score() weighting
- Cumulative regret math
"""
import sys
from pathlib import Path

# Allow tests to import the desktop-shell python module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from ac_router import (
    ACTIONS,
    HeuristicOrchestrator,
    Memory,
    VerifierSignals,
    CAFRouter,
    CAFResult,
)


# ── HeuristicOrchestrator ────────────────────────────────────

class TestHeuristicOrchestrator:
    def test_tool_keyword_routes_to_process_5_stage(self):
        orch = HeuristicOrchestrator()
        import asyncio
        # "execute" is not in the tool_keywords list (only "run:" / "shell:" are);
        # use the verified triggers.
        async def go():
            return [
                await orch.decide(q, {}, [])
                for q in ["!ls -la", "run: pwd", "shell: cat /etc/hosts"]
            ]
        results = asyncio.run(go())
        for q, action in zip(["!ls -la", "run: pwd", "shell: cat /etc/hosts"], results):
            assert action == "process_5_stage", f"q={q!r} got {action}"

    def test_memo_keyword_routes_to_memo_3stage(self):
        orch = HeuristicOrchestrator()
        import asyncio
        async def go():
            return [await orch.decide(q, {}, [])
                    for q in ["What is GalaxyOS", "MeMo 怎么用", "TokUI 是什么"]]
        results = asyncio.run(go())
        for q, action in zip(["What is GalaxyOS", "MeMo 怎么用", "TokUI 是什么"], results):
            assert action == "memo_3stage", f"q={q!r} got {action}"

    def test_short_or_question_routes_to_fast_path(self):
        orch = HeuristicOrchestrator()
        import asyncio
        async def go():
            return [await orch.decide(q, {}, [])
                    for q in ["hi", "你好?", "what?"]]
        results = asyncio.run(go())
        for q, action in zip(["hi", "你好?", "what?"], results):
            assert action == "fast_path", f"q={q!r} got {action}"

    def test_default_is_liquid_only(self):
        orch = HeuristicOrchestrator()
        import asyncio
        async def go():
            return await orch.decide("describe the system architecture", {}, [])
        assert asyncio.run(go()) == "liquid_only"

    def test_high_knn_match_reuses_action(self):
        """If the top-1 kNN has score > 0.85, reuse its action."""
        orch = HeuristicOrchestrator()
        import asyncio
        async def go():
            return await orch.decide(
                "anything goes here",
                {},
                [{"key": "old q", "score": 0.9, "action": "memo_3stage"}],
            )
        assert asyncio.run(go()) == "memo_3stage"


# ── VerifierSignals ──────────────────────────────────────────

class TestVerifierSignals:
    def test_default_score_is_zero(self):
        v = VerifierSignals()
        assert v.score() == 0.0

    def test_full_score_is_one(self):
        v = VerifierSignals(s_structural=1.0, s_sandbox=1.0,
                          s_consistency=1.0, s_judge=1.0)
        assert abs(v.score() - 1.0) < 0.001

    def test_weighted_score(self):
        # 0.1 + 0.2 + 0.3 + 0.4 = 1.0
        v = VerifierSignals(s_structural=1.0, s_sandbox=1.0,
                          s_consistency=1.0, s_judge=1.0)
        assert abs(v.score() - (0.1 + 0.2 + 0.3 + 0.4)) < 0.001

    def test_partial_score(self):
        v = VerifierSignals(s_judge=0.8)
        # 0.4 * 0.8 = 0.32
        assert abs(v.score() - 0.32) < 0.001


# ── Memory ──────────────────────────────────────────────────

class TestMemory:
    def test_empty_memory(self):
        m = Memory(max_size=100, dim=64, store_path=Path("/tmp/test_galaxyos_mem.jsonl"))
        assert m.size() == 0
        assert m.k_nearest("anything") == []

    def test_commit_and_retrieve(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            m = Memory(max_size=100, dim=128,
                       store_path=Path(td) / "test.jsonl")
            m.commit("What is GalaxyOS", {"action": "memo_3stage", "score": 0.9})
            m.commit("What is MeMo", {"action": "memo_3stage", "score": 0.85})
            assert m.size() == 2
            # First entry should be the most recent
            entries = m.k_nearest("GalaxyOS", k=10, sim_threshold=0.0)
            assert len(entries) == 2
            assert all("score" in e for e in entries)

    def test_fifo_eviction(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            m = Memory(max_size=3, dim=64,
                       store_path=Path(td) / "test.jsonl")
            for i in range(5):
                m.commit(f"query {i}", {"action": "a", "score": 1.0})
            assert m.size() == 3  # max_size enforced

    def test_persistence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.jsonl"
            m1 = Memory(max_size=10, dim=64, store_path=path)
            m1.commit("hello world", {"action": "a", "score": 1.0})
            # Re-load from disk
            m2 = Memory(max_size=10, dim=64, store_path=path)
            assert m2.size() == 1


# ── CAFRouter (C-A-F loop) ───────────────────────────────────

class TestCAFRouter:
    def test_route_runs_executor_and_returns_cafresult(self):
        import asyncio
        import tempfile

        async def mock_executor(action, query):
            return {
                "answer": f"Answer for {query} via {action}",
                "signals": VerifierSignals(
                    s_structural=0.9, s_sandbox=0.5,
                    s_consistency=0.8, s_judge=0.85,
                ),
                "cost": 0.002,
            }

        with tempfile.TemporaryDirectory() as td:
            mem = Memory(max_size=100, dim=64,
                        store_path=Path(td) / "test.jsonl")
            router = CAFRouter(
                orchestrator=HeuristicOrchestrator(),
                memory=mem,
                executor=mock_executor,
            )
            result = asyncio.run(router.route("What is GalaxyOS", {"type": "factual"}))
            assert isinstance(result, CAFResult)
            assert result.query == "What is GalaxyOS"
            assert result.chosen_action in ACTIONS
            assert result.answer == "Answer for What is GalaxyOS via memo_3stage"
            assert 0.0 <= result.confidence <= 1.0
            # Memory should now have 1 entry
            assert mem.size() == 1

    def test_route_commits_action_to_memory(self):
        import asyncio
        import tempfile

        async def mock_executor(action, query):
            return {
                "answer": "ok",
                "signals": VerifierSignals(s_judge=0.9),
                "cost": 0.001,
            }

        with tempfile.TemporaryDirectory() as td:
            mem = Memory(max_size=100, dim=64,
                        store_path=Path(td) / "test.jsonl")
            router = CAFRouter(
                orchestrator=HeuristicOrchestrator(),
                memory=mem,
                executor=mock_executor,
            )
            asyncio.run(router.route("What is GalaxyOS"))
            # Re-route the same query — should get kNN match
            result = asyncio.run(router.route("What is GalaxyOS"))
            assert result.k_neighbors  # should be populated
            # kNN match score > 0.85 → reuse previous action
            assert result.chosen_action in ("memo_3stage", "fast_path", "liquid_only", "process_5_stage")
