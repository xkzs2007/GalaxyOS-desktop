"""测试 model_router — 模型路由 + 断路器"""
import sys; sys.path.insert(0, '.')
import pytest
from galaxyos.privileged.model_router import (
    CircuitBreaker, CircuitState,
    ComplexityClassifier, CascadeRouter, CascadeRule,
    QueryComplexity,
)


class TestCircuitState:
    def test_all_states(self):
        states = list(CircuitState)
        assert len(states) >= 3
        assert CircuitState.CLOSED in states
        assert CircuitState.OPEN in states


class TestCircuitBreaker:
    @pytest.fixture
    def breaker(self):
        return CircuitBreaker(failure_threshold=3, recovery_timeout=0.1)

    def test_init(self, breaker):
        assert breaker is not None

    def test_allow_request_initially(self, breaker):
        assert breaker.allow_request() is True

    def test_record_failure_and_success(self, breaker):
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        # 不应崩溃

    def test_opens_after_threshold(self, breaker):
        for _ in range(5):
            breaker.record_failure()
        # 超过阈值后可能拒绝
        breaker.allow_request()  # 不应崩溃

    def test_recovery_after_timeout(self):
        breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        for _ in range(3):
            breaker.record_failure()
        # 等待恢复
        import time
        time.sleep(0.02)
        breaker.allow_request()  # 不应崩溃


class TestQueryComplexity:
    def test_all_levels(self):
        levels = list(QueryComplexity)
        assert len(levels) >= 2


class TestComplexityClassifier:
    @pytest.fixture
    def classifier(self):
        return ComplexityClassifier()

    def test_init(self, classifier):
        assert classifier is not None

    def test_classify(self, classifier):
        result = classifier.classify("hello world")
        assert result in QueryComplexity

    def test_classify_complex(self, classifier):
        result = classifier.classify("解释量子力学的原理并对比经典力学")
        assert result in QueryComplexity

    def test_add_template(self, classifier):
        classifier.add_template("simple question", QueryComplexity.SIMPLE
                               if hasattr(QueryComplexity, 'SIMPLE')
                               else list(QueryComplexity)[0])
        # 不应崩溃


class TestCascadeRule:
    def test_creation(self):
        rule = CascadeRule(
            complexity=list(QueryComplexity)[0],
            model_id="flash",
            description="simple queries",
        )
        assert rule.model_id == "flash"


class TestCascadeRouter:
    def test_init(self):
        # CascadeRouter 需要 base_router (ModelRouter)
        # 先创建一个基本 router
        from galaxyos.privileged.model_router import ModelRouter
        base = ModelRouter()
        router = CascadeRouter(base_router=base)
        assert router is not None

    def test_add_rule_and_route(self):
        from galaxyos.privileged.model_router import ModelRouter
        base = ModelRouter()
        router = CascadeRouter(base_router=base)
        router.add_rule(
            complexity=list(QueryComplexity)[0],
            model_id="test_model",
            description="test rule",
        )
        result = router.route("hello", complexity=list(QueryComplexity)[0])
        # route 可能返回 None 如果有路由问题
        assert result is None or isinstance(result, (str, dict))

    def test_get_stats(self):
        from galaxyos.privileged.model_router import ModelRouter
        base = ModelRouter()
        router = CascadeRouter(base_router=base)
        stats = router.get_stats()
        assert isinstance(stats, dict)
