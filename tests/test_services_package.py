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
        """services/__init__.py 应能正常导入"""
        import services
        assert services.__version__ is not None
        assert services.__author__ == "xkzs2007"

    def test_initialize_runs(self, monkeypatch):
        """initialize() 应能运行不崩溃"""
        monkeypatch.setattr("builtins.print", lambda *a, **k: None)
        import services
        result = services.initialize()
        assert isinstance(result, dict)
        assert "platform" in result
        assert "dependencies" in result


class TestClawAPIImport:
    """xiaoyi_claw_api 完整导入链"""

    def test_xiaoyi_claw_llm_import(self):
        from services.xiaoyi_claw_api import XiaoYiClawLLM
        assert XiaoYiClawLLM is not None

    def test_phase_state_from_new_module(self):
        """PhaseState 应从 rccam_state 模块导入"""
        from services.rccam_state import PhaseState
        assert PhaseState is not None
        s = PhaseState("test")
        assert s.user_input == "test"

    def test_helpers_from_new_module(self):
        """便捷函数应从 claw_helpers 模块导入"""
        from services.claw_helpers import get_xiaoyi_claw
        assert callable(get_xiaoyi_claw)

    def test_imports_module_available(self):
        """_imports 模块应可用"""
        from services._imports import HAS_NEURAL
        assert isinstance(HAS_NEURAL, bool)

    def test_api_re_exports_helpers(self):
        """xiaoyi_claw_api 应重新导出便捷函数"""
        from services.xiaoyi_claw_api import get_xiaoyi_claw
        assert callable(get_xiaoyi_claw)

    def test_claw_instantiation(self):
        """XiaoYiClawLLM 实例化不崩溃"""
        from services.xiaoyi_claw_api import XiaoYiClawLLM
        claw = XiaoYiClawLLM()
        assert claw is not None
        assert hasattr(claw, 'process')
        assert hasattr(claw, 'health_check')


class TestNewModulesIndependent:
    """新提取模块可独立使用"""

    def test_phase_state_standalone(self):
        """PhaseState 可不依赖 claw_api 使用"""
        from services.rccam_state import PhaseState
        s = PhaseState("hello world")
        assert s.user_input == "hello world"
        # 不依赖任何 claw 实例

    def test_imports_standalone(self):
        """_imports 可独立查询"""
        from services._imports import (
            HAS_RETRIEVAL_HUB, HAS_NEURAL,
            get_dynamic_confidence,
        )
        assert isinstance(HAS_RETRIEVAL_HUB, bool)
        assert callable(get_dynamic_confidence)
