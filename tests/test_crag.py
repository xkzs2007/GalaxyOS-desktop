"""
测试 CRAG — 纠错检索增强生成
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.crag import (
    CRAG, CRAGResult, CRAGStep, CRAGState,
    RetrievalAction, EvaluationResult,
)


class TestCRAGState:
    """CRAG 状态机测试"""

    def test_all_states(self):
        states = list(CRAGState)
        assert len(states) >= 6
        values = {s.value for s in states}
        assert "init" in values
        assert "retrieving" in values
        assert "evaluating" in values
        assert "completed" in values
        assert "failed" in values


class TestCRAGStep:
    """CRAG 步骤记录测试"""

    def test_creation(self):
        step = CRAGStep(
            state=CRAGState.RETRIEVING,
            action="search",
            input_data="query",
            output_data=["doc1"],
            confidence=0.85,
        )
        assert step.state == CRAGState.RETRIEVING
        assert step.action == "search"
        assert step.output_data == ["doc1"]
        assert step.confidence == 0.85

    def test_metadata_default(self):
        step = CRAGStep(
            state=CRAGState.INIT,
            action="start",
            input_data=None,
            output_data=None,
        )
        assert step.metadata == {}


class TestCRAGResult:
    """CRAG 结果测试"""

    def test_creation(self):
        result = CRAGResult(
            query="test query",
            answer="test answer",
            confidence=0.9,
            action_taken=RetrievalAction.USE,
            refined_knowledge=None,
            augmented=False,
            sources=["src1"],
            steps=[],
        )
        assert result.query == "test query"
        assert result.answer == "test answer"
        assert result.confidence == 0.9
        assert result.sources == ["src1"]

    def test_with_steps(self):
        steps = [
            CRAGStep(CRAGState.RETRIEVING, "search", "q", ["d"], 0.8),
            CRAGStep(CRAGState.EVALUATING, "evaluate", ["d"], None, 0.7),
        ]
        result = CRAGResult(
            query="q", answer="a", confidence=0.8,
            action_taken=RetrievalAction.USE,
            refined_knowledge=None, augmented=False,
            sources=["d"], steps=steps,
        )
        assert len(result.steps) == 2


class TestCRAG:
    """CRAG 控制器测试"""

    def test_init_default(self):
        crag = CRAG()
        assert crag is not None
        assert crag.current_state == CRAGState.INIT

    def test_process_returns_result(self):
        crag = CRAG()
        result = crag.process("test query")
        assert isinstance(result, CRAGResult)
        assert result.query == "test query"
        assert isinstance(result.confidence, float)

    def test_process_tracks_steps(self):
        crag = CRAG()
        result = crag.process("test")
        assert len(result.steps) > 0
        for step in result.steps:
            assert isinstance(step, CRAGStep)
            assert isinstance(step.state, CRAGState)

    def test_process_empty_query(self):
        crag = CRAG()
        result = crag.process("")
        assert isinstance(result, CRAGResult)

    def test_action_taken_in_result(self):
        crag = CRAG()
        result = crag.process("what is Python")
        assert result.action_taken in RetrievalAction
