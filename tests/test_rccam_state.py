"""
测试 rccam_state — R-CCAM PhaseState 状态对象
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import time
from services.rccam_state import PhaseState


class TestPhaseState:
    """PhaseState 创建与字段测试"""

    @pytest.fixture
    def state(self):
        return PhaseState("test user input")

    def test_creation(self, state):
        assert state.user_input == "test user input"
        assert state._start_time > 0

    def test_session_key_unique(self):
        s1 = PhaseState("a")
        s2 = PhaseState("b")
        assert s1.session_key != s2.session_key

    def test_session_key_format(self, state):
        assert state.session_key.startswith("rccam_")
        assert len(state.session_key) > 20

    # ── 默认值 ──

    def test_image_defaults(self, state):
        assert state.has_image is False
        assert state.image_source is None

    def test_retrieval_defaults(self, state):
        assert state.retrieved_memories == []
        assert state.dag_summaries == []
        assert state.kg_entities == []
        assert state.web_results == []
        assert state.retrieval_confidence == 0.0
        assert state.needs_more_info is False
        assert state.paper_engine_results == []
        assert state.suggested_tool is None

    def test_cognition_defaults(self, state):
        assert state.knowledge_type == "info"
        assert state.type_confidence == 0.5
        assert state.analysis == {}
        assert state.intent == "query"
        assert state.thinking_skills_used == []

    def test_control_defaults(self, state):
        assert state.strategy == "answer"
        assert state.boundaries == []
        assert state.fallback == "polite_refuse"
        assert state.reasoning == ""
        assert state.control_decision == {}

    def test_action_defaults(self, state):
        assert state.action_result is None
        assert state.action_success is False
        assert state.action_error is None
        assert state.generated_answer == ""
        assert state.answer_confidence == 0.0

    def test_rci_defaults(self, state):
        assert state.consistency_action == ""
        assert state.critic_scores == {}

    def test_memory_defaults(self, state):
        assert state.memory_ids == []
        assert state.dag_nodes_created == 0
        assert state.synapse_updated is False
        assert state.emotion_marked is False
        assert state.evolution_triggered is False

    def test_cycle_control_defaults(self, state):
        assert state.cycle_count == 0
        assert state.max_cycles == 3
        assert state.should_stop is False
        assert state.stop_reason == ""

    # ── 字段修改 ──

    def test_mutable_fields(self, state):
        """所有可变字段应可修改"""
        state.retrieved_memories.append({"id": "m1", "content": "test"})
        assert len(state.retrieved_memories) == 1

        state.knowledge_type = "code"
        assert state.knowledge_type == "code"

        state.should_stop = True
        state.stop_reason = "done"
        assert state.should_stop is True

    def test_full_cycle_simulation(self, state):
        """模拟完整的 R-CCAM 五阶段流程"""
        # Retrieval
        state.retrieved_memories = [{"id": "m1"}]
        state.retrieval_confidence = 0.8

        # Cognition
        state.knowledge_type = "factual"
        state.intent = "query"
        state.thinking_skills_used = ["contradiction_analysis"]

        # Control
        state.strategy = "answer"
        state.boundaries = ["safe"]

        # Action
        state.generated_answer = "The answer is 42."
        state.answer_confidence = 0.9
        state.action_success = True

        # Memory
        state.memory_ids = ["mem_001"]
        state.dag_nodes_created = 2
        state.synapse_updated = True

        # 验证最终状态
        assert state.retrieval_confidence == 0.8
        assert state.knowledge_type == "factual"
        assert state.generated_answer == "The answer is 42."
        assert state.action_success is True
        assert len(state.memory_ids) == 1

    def test_different_inputs(self):
        """不同输入创建独立状态"""
        s1 = PhaseState("hello")
        s2 = PhaseState("world")

        s1.retrieved_memories.append({"id": "x"})
        s2.retrieval_confidence = 1.0

        assert len(s1.retrieved_memories) == 1
        assert s2.retrieved_memories == []
        assert s2.retrieval_confidence == 1.0
