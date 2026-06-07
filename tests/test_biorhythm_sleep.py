"""测试 biorhythm_sleep_consolidation — 仿生睡眠巩固"""
import sys; sys.path.insert(0, '.')
import pytest
from services.biorhythm_sleep_consolidation import (
    DreamSleepConfig, DreamFragment, SleepPhase,
    BioRhythmSleepConsolidator,
)


class TestDreamSleepConfig:
    def test_default_values(self):
        c = DreamSleepConfig()
        assert c.nrem_duration_s > 0
        assert c.rem_duration_s > 0
        assert c.deep_sleep_duration_s > 0

    def test_custom_values(self):
        c = DreamSleepConfig(
            nrem_duration_s=300,
            rem_duration_s=200,
            deep_sleep_duration_s=600,
        )
        assert c.nrem_duration_s == 300
        assert c.rem_duration_s == 200


class TestSleepPhase:
    def test_has_phases(self):
        assert SleepPhase is not None


class TestDreamFragment:
    def test_creation(self):
        f = DreamFragment(
            id="d1", phase="rem_generative",
            content="dream of code", source_ids=["m1"],
        )
        assert f.phase == "rem_generative"
        assert "dream" in f.content

    def test_default_values(self):
        f = DreamFragment(id="d2", phase="nrem_swr", content="test")
        assert f.source_ids == []


class TestBioRhythmSleepConsolidator:
    def test_init(self, tmp_path):
        c = BioRhythmSleepConsolidator(workspace_path=str(tmp_path))
        assert c is not None

    def test_init_with_config(self, tmp_path):
        config = DreamSleepConfig(nrem_duration_s=100)
        c = BioRhythmSleepConsolidator(
            workspace_path=str(tmp_path), config=config
        )
        assert c is not None

    def test_run_full_sleep_cycle(self, tmp_path):
        c = BioRhythmSleepConsolidator(workspace_path=str(tmp_path))
        result = c.run_full_sleep_cycle()
        assert isinstance(result, dict)

    def test_run_manual_cycle(self, tmp_path):
        c = BioRhythmSleepConsolidator(workspace_path=str(tmp_path))
        result = c.run_manual_cycle()
        assert isinstance(result, dict)

    def test_get_dream_logs(self, tmp_path):
        c = BioRhythmSleepConsolidator(workspace_path=str(tmp_path))
        logs = c.get_dream_logs(limit=5)
        assert isinstance(logs, list)

    def test_get_stats(self, tmp_path):
        c = BioRhythmSleepConsolidator(workspace_path=str(tmp_path))
        stats = c.get_stats()
        assert isinstance(stats, dict)

    def test_mark_active(self, tmp_path):
        c = BioRhythmSleepConsolidator(workspace_path=str(tmp_path))
        c.mark_active()
        # 不应崩溃
