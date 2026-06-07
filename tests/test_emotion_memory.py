"""测试 emotion_memory — 情感记忆管理"""
import sys; sys.path.insert(0, '.')
import pytest
from services.emotion_memory import (
    EmotionType, EmotionScore, EmotionDetector,
    EmotionMemoryManager, EmotionWeightCalculator,
)


class TestEmotionType:
    def test_all_types(self):
        types = list(EmotionType)
        assert len(types) >= 4

    def test_values(self):
        values = {t.value for t in EmotionType}
        # 基本情绪应该在
        assert len(values) >= 3


class TestEmotionScore:
    def test_creation(self):
        s = EmotionScore(type=EmotionType.JOY, intensity=0.8, confidence=0.9)
        assert s.intensity == 0.8
        assert s.confidence == 0.9

    def test_to_dict(self):
        s = EmotionScore(type=EmotionType.SADNESS, intensity=0.3, confidence=0.7)
        d = s.to_dict()
        assert "intensity" in d
        assert "confidence" in d


class TestEmotionDetector:
    @pytest.fixture
    def detector(self):
        return EmotionDetector()

    def test_init(self, detector):
        assert detector is not None

    def test_detect_basic(self, detector):
        result = detector.detect("I am very happy today!")
        assert isinstance(result, (list, dict, EmotionScore))

    def test_detect_empty(self, detector):
        result = detector.detect("")
        assert result is not None


class TestEmotionWeightCalculator:
    @pytest.fixture
    def calculator(self):
        return EmotionWeightCalculator()

    def test_calculate(self, calculator):
        score = EmotionScore(type=EmotionType.JOY, intensity=0.9, confidence=0.8)
        weight = calculator.calculate(score)
        assert isinstance(weight, float)
        assert 0 <= weight <= 1

    def test_get_memory_priority(self, calculator):
        score = EmotionScore(type=EmotionType.JOY, intensity=0.9, confidence=0.8)
        priority = calculator.get_memory_priority(score)
        # 可能返回 str 或 float
        assert priority is not None


class TestEmotionMemoryManager:
    def test_init(self, tmp_path):
        mgr = EmotionMemoryManager(workspace_path=str(tmp_path))
        assert mgr is not None

    def test_process_message(self, tmp_path):
        mgr = EmotionMemoryManager(workspace_path=str(tmp_path))
        result = mgr.process_message("I'm feeling great!")
        assert isinstance(result, dict)

    def test_get_emotion_stats(self, tmp_path):
        mgr = EmotionMemoryManager(workspace_path=str(tmp_path))
        mgr.process_message("happy")
        stats = mgr.get_emotion_stats()
        assert isinstance(stats, dict)

    def test_get_high_priority_memories(self, tmp_path):
        mgr = EmotionMemoryManager(workspace_path=str(tmp_path))
        mems = mgr.get_high_priority_memories(limit=5)
        assert isinstance(mems, list)
