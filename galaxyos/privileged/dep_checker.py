"""
Dependency Checker - 技能依赖扩展自检测

自动检测所有模块的依赖安装状态，提供安装建议，
方便用户按需安装缺失依赖后启用对应功能。
"""

import importlib
import platform
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class DependencyInfo:
    """依赖项信息"""
    name: str                    # 包名
    category: str                # 分类: core/recommended/optional/hardware
    installed: bool              # 是否已安装
    version: Optional[str]       # 已安装版本
    pip_name: Optional[str]      # pip 安装名
    apt_name: Optional[str]      # apt 安装名（Linux）
    affected_modules: List[str]  # 依赖此包的模块列表
    description: str             # 说明
    install_hint: str            # 安装提示


@dataclass
class ModuleStatus:
    """模块状态"""
    name: str                    # 模块名
    available: bool              # 是否可用
    missing_deps: List[str]      # 缺失的依赖
    category: str                # 分类


# ============ 依赖注册表 ============

# 第三方 Python 包定义
_PIP_DEPS = {
    'numpy': {
        'category': 'core',
        'pip_name': 'numpy',
        'apt_name': 'python3-numpy',
        'description': '向量计算核心库',
        'install_hint': 'pip install numpy',
    },
    'scipy': {
        'category': 'optional',
        'pip_name': 'scipy',
        'apt_name': 'python3-scipy',
        'description': '稀疏向量搜索',
        'install_hint': 'pip install scipy',
    },
    'sklearn': {
        'category': 'optional',
        'pip_name': 'scikit-learn',
        'apt_name': 'python3-sklearn',
        'description': 'OPQ 量化（PCA 降维）',
        'install_hint': 'pip install scikit-learn',
    },
    'aiohttp': {
        'category': 'optional',
        'pip_name': 'aiohttp',
        'apt_name': None,
        'description': '分布式远程搜索 / 异步 HTTP',
        'install_hint': 'pip install aiohttp',
    },
    'pyopencl': {
        'category': 'optional',
        'pip_name': 'pyopencl',
        'apt_name': 'python3-pyopencl',
        'description': 'OpenCL GPU 检测（AMD/Intel GPU 支持）',
        'install_hint': 'pip install pyopencl',
    },
    'requests': {
        'category': 'optional',
        'pip_name': 'requests',
        'apt_name': 'python3-requests',
        'description': '模型性能测试 HTTP 调用',
        'install_hint': 'pip install requests',
    },
    'pysqlite3': {
        'category': 'recommended',
        'pip_name': 'pysqlite3-binary',
        'apt_name': None,
        'description': 'SQLite 扩展加载（向量搜索必需）',
        'install_hint': 'pip install pysqlite3-binary',
    },
}

# 模块 → 依赖映射
_MODULE_DEPS = {
    # 核心 LLM/Embedding
    'conversation': ['numpy'],
    'quantization': ['numpy'],
    'opq_quantization': ['numpy', 'sklearn'],
    'sparse_anns': ['numpy', 'scipy'],
    'ann_selector': ['numpy'],  # sklearn 仅 IVF 模式需要，延迟导入
    'approximate_cache': ['numpy'],
    'auto_tuner': ['numpy'],
    'cross_lingual': ['numpy'],
    'failover': [],

    # 网络 API
    'async_ops': ['numpy', 'aiohttp'],
    'model_router': ['numpy'],
    'llm_streaming': [],
    'rag_cache': ['numpy'],
    'rag_optimizer': ['numpy'],
    'distributed_search': ['numpy', 'aiohttp'],
    'multimodal_search': ['numpy'],
    'multiresolution_search': ['numpy'],
    'model_performance': ['requests'],

    # 原生扩展
    'sqlite_ext': [],
    'sqlite_vec': ['pysqlite3'],

    # 系统优化
    'numa_optimizer': ['numpy'],
    'kunpeng_optimizer': ['numpy'],
    'mkl_accelerator': ['numpy'],
    'fma_accelerator': ['numpy'],
    'cache_aware_scheduler': [],
    'cxl_optimizer': ['numpy'],
    'hugepage_manager': ['numpy'],
    'irq_isolator': [],
    'realtime_scheduler': [],
    'computational_storage': ['numpy'],
    'zram_detector': [],
    'hardware_optimize': ['numpy'],
    'gpu_optimizer': [],  # pyopencl/torch 为可选依赖，运行时按需导入
    'safety_alignment': [],
    'retrieval_eval': [],

    # 横向能力
    '__init__': [],
    'tools_registry': [],
    'acp_server': [],
    'platform_adapter': [],
    'sandbox_manager': [],
}

# 模块分类
_MODULE_CATEGORIES = {
    '__init__': '横向能力',
    'tools_registry': '横向能力',
    'acp_server': '横向能力',
    'platform_adapter': '横向能力',
    'sandbox_manager': '横向能力',
    'conversation': '核心 LLM',
    'quantization': '核心 LLM',
    'opq_quantization': '核心 LLM',
    'sparse_anns': '核心 LLM',
    'ann_selector': '核心 LLM',
    'approximate_cache': '核心 LLM',
    'auto_tuner': '核心 LLM',
    'cross_lingual': '核心 LLM',
    'failover': '核心 LLM',
    'async_ops': '网络 API',
    'model_router': '网络 API',
    'llm_streaming': '网络 API',
    'rag_cache': '网络 API',
    'rag_optimizer': '网络 API',
    'distributed_search': '网络 API',
    'multimodal_search': '网络 API',
    'multiresolution_search': '网络 API',
    'model_performance': '网络 API',
    'sqlite_ext': '原生扩展',
    'sqlite_vec': '原生扩展',
    'numa_optimizer': '系统优化',
    'kunpeng_optimizer': '系统优化',
    'mkl_accelerator': '系统优化',
    'fma_accelerator': '系统优化',
    'cache_aware_scheduler': '系统优化',
    'cxl_optimizer': '系统优化',
    'hugepage_manager': '系统优化',
    'irq_isolator': '系统优化',
    'realtime_scheduler': '系统优化',
    'computational_storage': '系统优化',
    'zram_detector': '系统优化',
    'hardware_optimize': '系统优化',
    'gpu_optimizer': '系统优化',
    'safety_alignment': '安全对齐',
    'retrieval_eval': '检索评估',
}


class DependencyChecker:
    """依赖自检测器"""

    def __init__(self):
        self._results: Dict[str, DependencyInfo] = {}
        self._module_results: Dict[str, ModuleStatus] = {}
        self._checked = False

    def check(self) -> Dict[str, DependencyInfo]:
        """执行全部依赖检测"""
        if self._checked:
            return self._results

        for dep_name, dep_config in _PIP_DEPS.items():
            installed, version = self._check_package(dep_name)
            # 反查依赖此包的模块
            affected = [m for m, deps in _MODULE_DEPS.items() if dep_name in deps]

            self._results[dep_name] = DependencyInfo(
                name=dep_name,
                category=dep_config['category'],
                installed=installed,
                version=version,
                pip_name=dep_config['pip_name'],
                apt_name=dep_config.get('apt_name'),
                affected_modules=affected,
                description=dep_config['description'],
                install_hint=dep_config['install_hint'],
            )

        # 检测每个模块可用性
        for mod_name, deps in _MODULE_DEPS.items():
            missing = [
                d for d in deps if not self._results.get(
                    d,
                    DependencyInfo(
                        d,
                        '',
                        False,
                        None,
                        None,
                        None,
                        [],
                        '',
                        '')).installed]
            self._module_results[mod_name] = ModuleStatus(
                name=mod_name,
                available=len(missing) == 0,
                missing_deps=missing,
                category=_MODULE_CATEGORIES.get(mod_name, '其他'),
            )

        self._checked = True
        return self._results

    def _check_package(self, name: str) -> Tuple[bool, Optional[str]]:
        """检测单个包是否已安装"""
        # sklearn 的 import 名是 sklearn
        import_name = name
        try:
            mod = importlib.import_module(import_name)
            version = getattr(mod, '__version__', None)
            return True, version
        except ImportError:
            return False, None

    def get_missing_deps(self) -> List[DependencyInfo]:
        """获取缺失的依赖列表"""
        if not self._checked:
            self.check()
        return [d for d in self._results.values() if not d.installed]

    def get_available_modules(self) -> List[ModuleStatus]:
        """获取可用模块列表"""
        if not self._checked:
            self.check()
        return [m for m in self._module_results.values() if m.available]

    def get_unavailable_modules(self) -> List[ModuleStatus]:
        """获取不可用模块列表"""
        if not self._checked:
            self.check()
        return [m for m in self._module_results.values() if not m.available]

    def get_install_plan(self) -> Dict[str, List[str]]:
        """生成安装计划"""
        if not self._checked:
            self.check()

        plan = {
            'core': [],         # 必须安装
            'recommended': [],  # 推荐安装
            'optional': [],     # 可选安装
        }

        for dep in self._results.values():
            if dep.installed:
                continue
            if dep.category in plan:
                plan[dep.category].append(dep.install_hint)
            else:
                plan['optional'].append(dep.install_hint)

        return plan

    def print_report(self):
        """打印检测报告"""
        if not self._checked:
            self.check()

        print("=" * 60)
        print("   技能依赖扩展自检测报告")
        print("=" * 60)
        print(f"平台: {platform.system()} {platform.machine()}")
        print(f"Python: {platform.python_version()}")
        print()

        # 1. 依赖安装状态
        print("📦 依赖安装状态")
        print("-" * 40)

        category_labels = {
            'core': '必须',
            'recommended': '推荐',
            'optional': '可选',
        }

        for cat in ['core', 'recommended', 'optional']:
            deps = [d for d in self._results.values() if d.category == cat]
            if not deps:
                continue
            label = category_labels[cat]
            for dep in deps:
                status = "✅" if dep.installed else "❌"
                ver = f" ({dep.version})" if dep.version else ""
                print(f"  {status} [{label}] {dep.name}{ver} - {dep.description}")
                if not dep.installed:
                    print(f"      💡 {dep.install_hint}")

        print()

        # 2. 模块可用性
        print("🔧 模块可用性")
        print("-" * 40)

        available = self.get_available_modules()
        unavailable = self.get_unavailable_modules()

        print(f"  可用: {len(available)}/{len(self._module_results)}")

        if unavailable:
            print()
            print("  不可用模块:")
            for mod in unavailable:
                missing_str = ", ".join(mod.missing_deps)
                print(f"    ❌ {mod.name} ({mod.category}) - 缺: {missing_str}")

        print()

        # 3. 安装计划
        plan = self.get_install_plan()
        total_missing = sum(len(v) for v in plan.values())

        if total_missing > 0:
            print("📋 快速安装")
            print("-" * 40)

            for cat in ['core', 'recommended', 'optional']:
                if plan[cat]:
                    label = category_labels[cat]
                    cmd = " && ".join(plan[cat])
                    print(f"  [{label}] {cmd}")

            # 一键安装
            all_cmds = []
            for cat in ['core', 'recommended', 'optional']:
                all_cmds.extend(plan[cat])

            if all_cmds:
                print()
                print("  一键安装全部缺失依赖:")
                print(
                    f"    pip install {' '.join(d.pip_name for d in self._results.values() if not d.installed and d.pip_name)}")

            # Linux apt 提示
            if platform.system() == 'Linux':
                apt_pkgs = [d.apt_name for d in self._results.values()
                            if not d.installed and d.apt_name]
                if apt_pkgs:
                    print()
                    print("  或使用系统包管理器:")
                    print(f"    sudo apt install {' '.join(apt_pkgs)}")
        else:
            print("🎉 所有依赖已安装，全部功能可用！")

        print()
        print("=" * 60)

    def to_dict(self) -> Dict:
        """导出为字典"""
        if not self._checked:
            self.check()

        return {
            'dependencies': {
                name: {
                    'installed': d.installed,
                    'version': d.version,
                    'category': d.category,
                    'affected_modules': d.affected_modules,
                    'install_hint': d.install_hint,
                }
                for name, d in self._results.items()
            },
            'modules': {
                name: {
                    'available': m.available,
                    'missing_deps': m.missing_deps,
                    'category': m.category,
                }
                for name, m in self._module_results.items()
            },
            'install_plan': self.get_install_plan(),
        }


# 全局实例
checker = DependencyChecker()


def check_dependencies() -> Dict[str, DependencyInfo]:
    """检查所有依赖"""
    return checker.check()


def print_dependency_report():
    """打印依赖检测报告"""
    checker.print_report()


def get_missing_dependencies() -> List[DependencyInfo]:
    """获取缺失的依赖列表"""
    return checker.get_missing_deps()


def get_module_status(module_name: str) -> Optional[ModuleStatus]:
    """获取指定模块的状态"""
    if not checker._checked:
        checker.check()
    return checker._module_results.get(module_name)


# ============ 导出 ============

__all__ = [
    'DependencyChecker',
    'DependencyInfo',
    'ModuleStatus',
    'checker',
    'check_dependencies',
    'print_dependency_report',
    'get_missing_dependencies',
    'get_module_status',
]
