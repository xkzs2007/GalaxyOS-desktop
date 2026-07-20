"""
测试 services 包完整性 — 验证所有模块导入链
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


class TestPackageExports:
    """包 __init__.py 导出完整性"""

    def test_init_imports(self):
        import galaxyos.engine as services
        assert services is not None


class TestClawAPIImport:
    """xiaoyi_claw_api 完整导入链"""

    def test_xiaoyi_claw_llm_import(self):
        from galaxyos.engine.claw_worker import ClawWorker as XiaoYiClawLLM
        assert XiaoYiClawLLM is not None

    def test_phase_state_from_new_module(self):
        """PhaseState 应从 rccam_state 模块导入"""
        from galaxyos.engine.rccam_state import PhaseState
        assert PhaseState is not None
        s = PhaseState("test")
        assert s.user_input == "test"

    def test_helpers_from_new_module(self):
        """便捷函数应从 claw_helpers 模块导入"""
        from galaxyos.engine.claw_helpers import get_xiaoyi_claw
        assert callable(get_xiaoyi_claw)

    def test_imports_module_available(self):
        """_imports 模块应可用"""
        from galaxyos.engine._imports import HAS_NEURAL
        assert isinstance(HAS_NEURAL, bool)

    def test_api_re_exports_helpers(self):
        """xiaoyi_claw_api 应重新导出便捷函数"""
        from galaxyos.engine.claw_helpers import get_xiaoyi_claw
        assert callable(get_xiaoyi_claw)

    def test_claw_instantiation(self):
        from galaxyos.engine.claw_worker import ClawWorker as XiaoYiClawLLM
        claw = XiaoYiClawLLM()
        assert claw is not None
        assert hasattr(claw, 'remember')
        assert hasattr(claw, 'recall')


class TestNewModulesIndependent:
    """新提取模块可独立使用"""

    def test_phase_state_standalone(self):
        """PhaseState 可不依赖 claw_api 使用"""
        from galaxyos.engine.rccam_state import PhaseState
        s = PhaseState("hello world")
        assert s.user_input == "hello world"
        # 不依赖任何 claw 实例

    def test_imports_standalone(self):
        """_imports 可独立查询"""
        from galaxyos.engine._imports import (
            HAS_RETRIEVAL_HUB, HAS_NEURAL,
            get_dynamic_confidence,
        )
        assert isinstance(HAS_RETRIEVAL_HUB, bool)
        assert callable(get_dynamic_confidence)
