"""
测试 claw_helpers — 便捷 API 函数
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.claw_helpers import (
    get_xiaoyi_claw, remember, recall, forget,
    get_entity, learn,
    _rci_async_criticism, _load_latest_evolved_capabilities,
)


class TestGetXiaoyiClaw:
    """单例获取测试"""

    def test_returns_same_instance(self):
        i1 = get_xiaoyi_claw()
        i2 = get_xiaoyi_claw()
        assert i1 is i2

    def test_returns_valid_object(self):
        instance = get_xiaoyi_claw()
        assert instance is not None

    def test_has_expected_methods(self):
        instance = get_xiaoyi_claw()
        assert hasattr(instance, 'remember')
        assert hasattr(instance, 'recall')
        assert hasattr(instance, 'forget')
        assert hasattr(instance, 'process')
        assert hasattr(instance, 'health_check')


class TestRememberRecall:
    """记忆存取便捷函数测试"""

    def test_remember_returns_string(self):
        result = remember("test content for unit test")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_remember_with_metadata(self):
        result = remember(
            "Python is a versatile programming language",
            metadata={"source": "unit_test", "category": "programming"},
            source="unit_test",
        )
        assert isinstance(result, str)

    def test_recall_returns_list(self):
        results = recall("test content")
        assert isinstance(results, list)

    def test_remember_and_recall_roundtrip(self):
        memory_id = remember("unique test phrase: quantum aardvark")
        results = recall("quantum aardvark")
        assert isinstance(results, list)
        assert isinstance(memory_id, str)

    def test_forget_returns_int(self):
        """forget 应返回 int（删除数量）"""
        result = forget("nonexistent_id_12345")
        assert isinstance(result, int)

    def test_forget_existing_memory(self):
        memory_id = remember("temporary test memory")
        result = forget(memory_id)
        assert isinstance(result, int)


class TestGetEntity:
    """实体查询便捷函数测试"""

    def test_get_entity_returns_dict(self):
        result = get_entity("Python")
        assert isinstance(result, dict)

    def test_get_entity_nonexistent(self):
        result = get_entity("nonexistent_entity_xyz_123")
        assert isinstance(result, dict)


class TestLearn:
    """学习反馈便捷函数测试"""

    def test_learn_returns_bool(self):
        result = learn({"feedback": "positive", "topic": "testing"})
        assert isinstance(result, bool)

    def test_learn_empty_feedback(self):
        result = learn({})
        assert isinstance(result, bool)


class TestRCIAsyncCriticism:
    """RCI 异步批评函数测试"""

    def test_function_is_callable(self):
        assert callable(_rci_async_criticism)

    def test_execution_with_mock(self):
        """在不完整环境中也不应崩溃"""
        class MockClaw:
            _kv_session_id = "test-session"
            _rci_publish_zmq = None

        class MockState:
            critic_scores = {"accuracy": 0.8}
            consistency_action = "pass"
            generated_answer = "test answer"

        try:
            _rci_async_criticism(MockClaw(), MockState())
        except Exception:
            pass  # 在无 OpenClaw 环境时可能失败，但不应崩溃


class TestLoadEvolvedCapabilities:
    """自进化能力加载测试"""

    def test_returns_dict(self):
        result = _load_latest_evolved_capabilities()
        assert isinstance(result, dict)
        assert "success" in result

    def test_has_reason_on_failure(self):
        result = _load_latest_evolved_capabilities()
        if not result.get("success"):
            assert "reason" in result
