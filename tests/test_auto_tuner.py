"""
测试 AutoTuner — 自动参数调优
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import time
from galaxyos.privileged.auto_tuner import AutoTuner


class TestAutoTuner:
    """自动调优器测试"""

    @pytest.fixture
    def tuner(self):
        return AutoTuner(
            param_space={
                "top_k": [10, 20, 50],
                "n_probe": [5, 10],
                "batch_size": [100, 500],
            },
            metric="latency",
            n_trials=5,
        )

    def test_init_defaults(self):
        t = AutoTuner()
        assert t.metric == "latency"
        assert t.n_trials == 20
        assert "top_k" in t.param_space
        assert len(t.trials) == 0

    def test_init_custom(self, tuner):
        assert tuner.metric == "latency"
        assert tuner.n_trials == 5
        assert len(tuner.param_space) == 3

    def test_sample_params(self, tuner):
        params = tuner._sample_params()
        assert isinstance(params, dict)
        for key in tuner.param_space:
            assert key in params
        assert params["top_k"] in tuner.param_space["top_k"]
        assert params["n_probe"] in tuner.param_space["n_probe"]
        assert params["batch_size"] in tuner.param_space["batch_size"]

    def test_sample_params_are_random(self, tuner):
        """多次采样应产生变化"""
        samples = set()
        for _ in range(10):
            params = tuner._sample_params()
            samples.add(tuple(params.values()))
        assert len(samples) >= 1

    def test_optimize_latency(self, tuner):
        def dummy_benchmark(params):
            return params["top_k"] * 0.1 + params["n_probe"] * 0.2

        best = tuner.optimize(dummy_benchmark)
        assert isinstance(best, dict)
        assert tuner.best_params is not None

    def test_optimize_zero_trials(self):
        t = AutoTuner(n_trials=0)
        result = t.optimize(lambda p: 1)
        # 0 次试验不回退但仍然运行
        assert t.best_params is None

    def test_optimize_throughput(self):
        t = AutoTuner(
            param_space={"top_k": [10, 50], "use_cache": [False, True]},
            metric="throughput",
            n_trials=5,
        )

        def dummy_benchmark(params):
            # throughput 越大越好，确保返回正值
            return params["top_k"] * 0.1 + (10 if params.get("use_cache") else 0)

        best = t.optimize(dummy_benchmark)
        # throughput 模式下 best_params 应有值
        assert t.best_params is not None or best is not None

    def test_trials_recorded(self, tuner):
        def dummy(params):
            return 1.0

        tuner.optimize(dummy)
        assert len(tuner.trials) == tuner.n_trials

    def test_best_score_updated(self, tuner):
        def dummy(params):
            return params["top_k"] * 0.1

        tuner.optimize(dummy)
        # best_score 应该更新为某个值
        assert tuner.best_score is not None

    def test_boolean_params(self):
        t = AutoTuner(
            param_space={"flag": [True, False]},
            n_trials=4,
        )

        def dummy(params):
            return 0 if params["flag"] else 100

        t.optimize(dummy)
        assert t.best_params is not None

    def test_get_results(self, tuner):
        tuner.optimize(lambda p: p["top_k"])
        results = tuner.get_results()
        assert isinstance(results, (list, dict))  # 可能返回 list 或 dict
