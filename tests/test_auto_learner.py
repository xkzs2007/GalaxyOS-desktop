"""
测试 AutoLearner — 自主学习器
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import json
from services.auto_learner import (
    AutoLearner, LearningEvent,
)


class TestLearningEvent:
    """学习事件测试"""

    def test_creation(self):
        event = LearningEvent(
            id="e1",
            event_type="preference",
            content="user prefers short answers",
            context={"topic": "coding"},
            learned_at="2026-01-01T00:00:00Z",
            applied=True,
        )
        assert event.id == "e1"
        assert event.event_type == "preference"
        assert event.content == "user prefers short answers"
        assert event.context["topic"] == "coding"
        assert event.applied is True

    def test_event_types(self):
        valid_types = ["preference", "correction", "feedback", "pattern"]
        for t in valid_types:
            event = LearningEvent(
                id="e", event_type=t, content="c",
                context={}, learned_at="", applied=False,
            )
            assert event.event_type == t


class TestAutoLearner:
    """自主学习器测试"""

    def test_init_empty(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))
        assert learner.events == []
        assert learner.preferences == {}
        assert learner.patterns == {}

    def test_init_with_existing_events(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        events = [
            {"id": "e1", "event_type": "preference",
             "content": "short", "context": {},
             "learned_at": "2026-01-01", "applied": False},
            {"id": "e2", "event_type": "correction",
             "content": "fix", "context": {},
             "learned_at": "2026-01-02", "applied": True},
        ]
        learn_path.write_text(
            "\n".join(json.dumps(e) for e in events)
        )

        learner = AutoLearner(learning_path=str(learn_path))
        assert len(learner.events) == 2
        assert learner.events[0].id == "e1"
        assert learner.events[1].id == "e2"

    def test_learn_preference(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))

        learner.learn_preference(
            key="answer_style",
            value="concise",
        )
        assert len(learner.events) == 1
        assert learner.events[0].event_type == "preference"

    def test_learn_feedback(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))

        learner.learn_feedback(
            feedback_type="rating",
            content="user liked this answer",
        )
        assert len(learner.events) == 1
        assert learner.events[0].event_type == "feedback"

    def test_learn_pattern(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))

        for _ in range(3):
            learner.learn_pattern(
                pattern="user asks coding questions in morning",
            )
        assert len(learner.events) == 3

    def test_get_all_preferences(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))
        prefs = learner.get_all_preferences()
        assert isinstance(prefs, dict)

    def test_get_all_preferences_with_data(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))
        learner.learn_preference("style", "short")
        learner.learn_preference("language", "python")
        prefs = learner.get_all_preferences()
        assert isinstance(prefs, dict)

    def test_get_patterns(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))
        for _ in range(3):
            learner.learn_pattern("code review")
        patterns = learner.get_patterns()
        assert isinstance(patterns, dict)

    def test_corrupted_json_line(self, tmp_path):
        """损坏的 JSON 行不应导致崩溃"""
        learn_path = tmp_path / "learning.jsonl"
        learn_path.write_text(
            '{"id": "e1", "event_type": "preference", "content": "ok", "context": {}, "learned_at": "", "applied": false}\n'
            'this is not valid json\n'
            '{"id": "e2", "event_type": "correction", "content": "ok2", "context": {}, "learned_at": "", "applied": true}\n'
        )
        learner = AutoLearner(learning_path=str(learn_path))
        assert len(learner.events) >= 1

    def test_auto_learn_from_interaction(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))
        learner.learn_preference("answer_style", "concise")
        result = learner.auto_learn_from_interaction(
            user_input="tell me about Python",
            assistant_response="Python is a programming language...",
        )
        # 可能返回 dict 或 None（取决于实现）
        assert result is None or isinstance(result, dict)

    def test_events_persisted_to_disk(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))
        learner.learn_preference("key", "value")

        learner2 = AutoLearner(learning_path=str(learn_path))
        assert len(learner2.events) == 1

    def test_get_stats(self, tmp_path):
        learn_path = tmp_path / "learning.jsonl"
        learner = AutoLearner(learning_path=str(learn_path))
        learner.learn_preference("k", "v")
        learner.learn_feedback("like", "good")
        stats = learner.get_stats()
        assert isinstance(stats, dict)
