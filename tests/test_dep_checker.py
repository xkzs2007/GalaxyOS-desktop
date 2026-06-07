"""测试 dep_checker — 依赖检测"""
import sys; sys.path.insert(0, '.')
import pytest
from services.dep_checker import DependencyChecker, DependencyInfo, ModuleStatus


class TestDependencyInfo:
    def test_creation(self):
        info = DependencyInfo(
            name="numpy", category="core", installed=True,
            version="1.24", pip_name="numpy", apt_name=None,
            affected_modules=[], description="test",
            install_hint="pip install numpy",
        )
        assert info.name == "numpy"
        assert info.installed is True

    def test_not_installed(self):
        info = DependencyInfo(
            name="fake", category="optional", installed=False,
            version=None, pip_name="fake", apt_name=None,
            affected_modules=[], description="fake",
            install_hint="pip install fake",
        )
        assert info.installed is False
        assert info.version is None


class TestModuleStatus:
    def test_available(self):
        s = ModuleStatus(name="test", available=True, missing_deps=[], category="core")
        assert s.available is True
        assert s.missing_deps == []

    def test_unavailable(self):
        s = ModuleStatus(name="test2", available=False, missing_deps=["dep_a", "dep_b"], category="optional")
        assert s.available is False
        assert len(s.missing_deps) == 2


class TestDependencyChecker:
    @pytest.fixture
    def checker(self):
        return DependencyChecker()

    def test_init(self, checker):
        assert checker is not None

    def test_check(self, checker):
        result = checker.check()
        assert isinstance(result, dict)

    def test_get_available_modules(self, checker):
        modules = checker.get_available_modules()
        assert isinstance(modules, list)

    def test_get_missing_deps(self, checker):
        missing = checker.get_missing_deps()
        assert isinstance(missing, list)

    def test_get_install_plan(self, checker):
        plan = checker.get_install_plan()
        # 可能返回 dict 或 list
        assert isinstance(plan, (list, dict))

    def test_get_unavailable_modules(self, checker):
        modules = checker.get_unavailable_modules()
        assert isinstance(modules, list)

    def test_print_report(self, checker):
        checker.print_report()
