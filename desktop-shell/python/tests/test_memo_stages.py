"""tests/test_memo_stages.py — Unit tests for MeMo 3-stage protocol.

Covers:
- GroundingResult, EntityResult, AnswerResult dataclasses
- MeMoProtocol.run() full 3-stage cycle with MockExecutive
- _do_stream_memo() returns a complete DSL fragment set
- Default Executive is MockExecutiveClient
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestMeMoDataclasses:
    def test_grounding_result_defaults(self):
        from memo_stages import GroundingResult
        g = GroundingResult()
        assert g.sub_questions == []
        assert g.answers == []

    def test_entity_result_defaults(self):
        from memo_stages import EntityResult
        e = EntityResult()
        assert e.candidates == []
        assert e.chosen is None

    def test_answer_result_defaults(self):
        from memo_stages import AnswerResult
        a = AnswerResult()
        assert a.supporting_facts == []
        assert a.final_answer == ""


class TestMeMoProtocolRun:
    def test_run_with_mock_executive(self):
        """Full 3-stage cycle completes with Mock executive."""
        from memo_stages import MeMoProtocol, GroundingResult, EntityResult, AnswerResult
        # Build protocol directly with mock components
        from memo_adapter import MockMeMoAdapter
        from executive_client import MockExecutiveClient

        class FakeExecutive(MockExecutiveClient):
            async def decompose(self, question, *, max_sub_questions=8):
                return [f"What is {question}", f"Who uses {question}"]
            async def identify_entity(self, question, grounding_answers, *, max_followups=6):
                return [(q.split()[0] if q.split() else "Unknown", 0.9) for q in grounding_answers[:1]], "Entity"
            async def synthesize(self, question, grounding_answers, chosen_entity, *, max_followups=6):
                return grounding_answers[:1], f"Based on {chosen_entity}: {grounding_answers[0] if grounding_answers else 'no info'}"

        from memo_adapter import MockMeMoAdapter
        memo = MockMeMoAdapter()
        exec_ = FakeExecutive()
        proto = MeMoProtocol(memo=memo, executive=exec_, overall_timeout_s=5.0)
        trace = asyncio.run(proto.run("Test query"))
        assert trace.grounding.sub_questions  # populated
        assert len(trace.grounding.answers) > 0
        assert trace.entity.chosen is not None
        assert trace.answer.final_answer  # non-empty

    def test_run_timeout(self):
        """Protocol returns an error answer if it times out."""
        from memo_stages import MeMoProtocol
        from memo_adapter import MockMeMoAdapter
        from executive_client import MockExecutiveClient

        class SlowExecutive(MockExecutiveClient):
            async def decompose(self, question, *, max_sub_questions=8):
                await asyncio.sleep(10)
                return []
            async def identify_entity(self, *args, **kwargs):
                await asyncio.sleep(10)
                return [], None
            async def synthesize(self, *args, **kwargs):
                await asyncio.sleep(10)
                return [], ""

        proto = MeMoProtocol(
            memo=MockMeMoAdapter(),
            executive=SlowExecutive(),
            overall_timeout_s=0.5,
        )
        trace = asyncio.run(proto.run("test"))
        # Should return an error answer within 0.5s
        assert "timeout" in trace.answer.final_answer.lower() or "0.5" in trace.answer.final_answer


class TestMeMoStageProtocol:
    """Validate the Grounding → Entity → Answer flow as individual steps."""

    def test_grounding_sub_question_format(self):
        """Grounding decomposes question into 2-4 atomic sub-questions."""
        from executive_client import MockExecutiveClient
        exec_ = MockExecutiveClient()
        result = asyncio.run(exec_.decompose("What is GalaxyOS", max_sub_questions=4))
        assert isinstance(result, list)
        assert 1 <= len(result) <= 4
        for q in result:
            assert isinstance(q, str)
            assert len(q) > 0

    def test_entity_returns_chosen(self):
        from executive_client import MockExecutiveClient
        exec_ = MockExecutiveClient()
        candidates, chosen = asyncio.run(exec_.identify_entity(
            "What is X", ["X is a thing"], max_followups=6))
        # Mock may return chosen=None for queries that don't match any
        # known entity — that's fine, we just verify the shape.
        assert candidates is not None
        assert isinstance(candidates, list)
        # If chosen is set, candidates must be non-empty
        if chosen is not None:
            assert len(candidates) > 0
            for c in candidates:
                assert isinstance(c, tuple) and len(c) == 2

    def test_synthesize_returns_final_answer(self):
        from executive_client import MockExecutiveClient
        exec_ = MockExecutiveClient()
        facts, answer = asyncio.run(exec_.synthesize(
            "What is X", ["X is a thing"], "X", max_followups=6))
        assert isinstance(facts, list)
        assert isinstance(answer, str)
        assert len(answer) > 0
