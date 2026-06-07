"""
测试 MemoryConsolidation — 记忆巩固引擎
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.memory_consolidation import (
    ConsolidationConfig, ConsolidationEngine,
)


class TestConsolidationConfig:
    """巩固配置测试"""

    def test_default_values(self):
        c = ConsolidationConfig()
        assert c.consolidation_interval_s == 300
        assert c.dag_importance_threshold == 0.4
        assert c.max_consolidate_per_cycle == 10
        assert c.replay_interval_s == 900
        assert c.replay_top_k_paths == 5
        assert c.ltp_replay_strength == 0.05
        assert c.ltd_unused_days == 14
        assert c.ltd_prune_weight == 0.15
        assert c.merge_similarity_threshold == 0.85
        assert c.max_merge_candidates == 3
        assert c.prediction_error_decay == 0.2
        assert c.max_conflict_age_days == 90

    def test_custom_config(self):
        c = ConsolidationConfig(
            consolidation_interval_s=600,
            dag_importance_threshold=0.6,
            replay_top_k_paths=10,
        )
        assert c.consolidation_interval_s == 600
        assert c.dag_importance_threshold == 0.6
        assert c.replay_top_k_paths == 10
        assert c.max_consolidate_per_cycle == 10


class TestConsolidationEngine:
    """巩固引擎测试"""

    def test_init_default(self, tmp_path):
        engine = ConsolidationEngine(workspace_path=str(tmp_path))
        assert engine.config is not None
        assert isinstance(engine.config, ConsolidationConfig)

    def test_init_custom(self, tmp_path):
        config = ConsolidationConfig(dag_importance_threshold=0.8)
        engine = ConsolidationEngine(
            workspace_path=str(tmp_path), config=config
        )
        assert engine.config.dag_importance_threshold == 0.8

    def test_consolidate_from_dag(self, tmp_path):
        engine = ConsolidationEngine(workspace_path=str(tmp_path))
        result = engine.consolidate_from_dag()
        assert isinstance(result, dict)

    def test_detect_and_manage_interference(self, tmp_path):
        engine = ConsolidationEngine(workspace_path=str(tmp_path))
        result = engine.detect_and_manage_interference("test content")
        assert isinstance(result, dict)

    def test_detect_prediction_error(self, tmp_path):
        engine = ConsolidationEngine(workspace_path=str(tmp_path))
        result = engine.detect_prediction_error(
            query="test", retrieved_memories=[]
        )
        assert isinstance(result, list)

    def test_replay_and_consolidate(self, tmp_path):
        engine = ConsolidationEngine(workspace_path=str(tmp_path))
        result = engine.replay_and_consolidate()
        assert isinstance(result, dict)

    def test_get_stats(self, tmp_path):
        engine = ConsolidationEngine(workspace_path=str(tmp_path))
        stats = engine.get_stats()
        assert isinstance(stats, dict)

    def test_mark_active(self, tmp_path):
        engine = ConsolidationEngine(workspace_path=str(tmp_path))
        engine.mark_active()
        # 不应崩溃
