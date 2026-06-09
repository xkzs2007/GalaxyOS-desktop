"""
Platform Adapter - 跨平台兼容层

实现"一次编写，处处运行"的跨平台能力。
"""

import os
import platform
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import subprocess


@dataclass
class PlatformInfo:
    """平台信息"""
    system: str          # Linux, Darwin, Windows
    machine: str         # x86_64, aarch64, arm64
    python_version: str
    is_arm: bool
    is_x64: bool
    is_linux: bool
    is_macos: bool
    is_windows: bool
    home_dir: Path
    config_dir: Path
    cache_dir: Path


class PlatformAdapter:
    """平台适配器"""

    def __init__(self):
        self.info = self._detect_platform()
        self._binaries: Dict[str, Path] = {}
        self._libraries: Dict[str, Path] = {}
        self._setup_paths()

    def _detect_platform(self) -> PlatformInfo:
        """检测平台信息"""
        system = platform.system()
        machine = platform.machine().lower()

        # 标准化架构名称
        if machine in ['arm64', 'aarch64']:
            machine = 'arm64'
            is_arm = True
            is_x64 = False
        elif machine in ['x86_64', 'amd64']:
            machine = 'x64'
            is_arm = False
            is_x64 = True
        else:
            is_arm = False
            is_x64 = False

        # 平台判断
        is_linux = system == 'Linux'
        is_macos = system == 'Darwin'
        is_windows = system == 'Windows'

        # 目录设置
        home_dir = Path.home()

        if is_linux or is_macos:
            config_dir = home_dir / '.config'
            cache_dir = home_dir / '.cache'
        elif is_windows:
            config_dir = Path(os.environ.get('APPDATA', home_dir / 'AppData' / 'Roaming'))
            cache_dir = Path(os.environ.get('LOCALAPPDATA', home_dir / 'AppData' / 'Local'))
        else:
            config_dir = home_dir / '.config'
            cache_dir = home_dir / '.cache'

        return PlatformInfo(
            system=system,
            machine=machine,
            python_version=platform.python_version(),
            is_arm=is_arm,
            is_x64=is_x64,
            is_linux=is_linux,
            is_macos=is_macos,
            is_windows=is_windows,
            home_dir=home_dir,
            config_dir=config_dir,
            cache_dir=cache_dir
        )

    def _setup_paths(self):
        """设置路径"""
        # 技能根目录
        skill_root = Path(__file__).parent.parent

        # 二进制文件目录
        bin_dir = skill_root / 'bin' / f'{self.info.system.lower()}-{self.info.machine}'

        if bin_dir.exists():
            for binary in bin_dir.iterdir():
                if binary.is_file():
                    self._binaries[binary.name] = binary

        # 库文件目录
        lib_dir = skill_root / 'lib' / f'{self.info.system.lower()}-{self.info.machine}'

        if lib_dir.exists():
            for lib in lib_dir.iterdir():
                if lib.is_file():
                    self._libraries[lib.name] = lib

    def get_binary(self, name: str) -> Optional[Path]:
        """获取二进制文件路径"""
        # 优先使用平台特定版本
        if name in self._binaries:
            return self._binaries[name]

        # 回退到系统 PATH
        binary = shutil.which(name)
        if binary:
            return Path(binary)

        return None

    def get_library(self, name: str) -> Optional[Path]:
        """获取库文件路径"""
        return self._libraries.get(name)

    def get_platform_config(self) -> Dict[str, Any]:
        """获取平台配置"""
        config = {
            'platform': {
                'system': self.info.system,
                'machine': self.info.machine,
                'python_version': self.info.python_version,
                'is_arm': self.info.is_arm,
                'is_x64': self.info.is_x64
            },
            'paths': {
                'home': str(self.info.home_dir),
                'config': str(self.info.config_dir),
                'cache': str(self.info.cache_dir)
            },
            'optimizations': self._get_optimizations()
        }

        return config

    def _get_optimizations(self) -> Dict[str, bool]:
        """获取可用的优化选项"""
        optimizations = {
            'numa': False,
            'avx512': False,
            'avx2': False,
            'neon': False,
            'cuda': False,
            'mkl': False,
            'fma': False
        }

        # Linux 特定检测
        if self.info.is_linux:
            # NUMA 检测
            if Path('/sys/devices/system/node').exists():
                optimizations['numa'] = True

            # CPU 特性检测
            cpuinfo = Path('/proc/cpuinfo')
            if cpuinfo.exists():
                content = cpuinfo.read_text()
                if 'avx512' in content:
                    optimizations['avx512'] = True
                if 'avx2' in content:
                    optimizations['avx2'] = True
                if 'fma' in content:
                    optimizations['fma'] = True

        # macOS 特定检测
        elif self.info.is_macos:
            # ARM Mac 支持 NEON
            if self.info.is_arm:
                optimizations['neon'] = True

            # 检测 CPU 特性
            try:
                result = subprocess.run(
                    ['sysctl', '-n', 'hw.optional.arm.FEAT_FP16'],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0 and result.stdout.strip() == '1':
                    optimizations['neon'] = True
            except Exception:
                pass

        # CUDA 检测
        if shutil.which('nvidia-smi'):
            try:
                result = subprocess.run(
                    ['nvidia-smi'],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    optimizations['cuda'] = True
            except Exception:
                pass

        # MKL 检测
        try:
            import numpy
            if 'mkl' in numpy.__config__.show().lower():
                optimizations['mkl'] = True
        except Exception:
            pass

        return optimizations

    def get_optimized_module(self, module_name: str) -> Optional[str]:
        """获取优化后的模块名"""
        optimizations = self._get_optimizations()

        # 根据平台和优化选择最佳模块
        if self.info.is_arm:
            if optimizations['neon']:
                return f'{module_name}_neon'
            return f'{module_name}_arm'

        elif self.info.is_x64:
            if optimizations['avx512']:
                return f'{module_name}_avx512'
            elif optimizations['avx2']:
                return f'{module_name}_avx2'
            return f'{module_name}_x64'

        return module_name

    def run_command(self, cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
        """运行命令（跨平台）"""
        # 在 Windows 上处理路径
        if self.info.is_windows:
            cmd = [str(c) for c in cmd]

        return subprocess.run(cmd, **kwargs)

    def get_env(self) -> Dict[str, str]:
        """获取环境变量"""
        env = os.environ.copy()

        # 添加库路径
        if self._libraries:
            lib_paths = [str(lib.parent) for lib in self._libraries.values()]

            if self.info.is_linux:
                existing = env.get('LD_LIBRARY_PATH', '')
                env['LD_LIBRARY_PATH'] = ':'.join(lib_paths + [existing] if existing else lib_paths)

            elif self.info.is_macos:
                existing = env.get('DYLD_LIBRARY_PATH', '')
                env['DYLD_LIBRARY_PATH'] = ':'.join(lib_paths + [existing] if existing else lib_paths)

        return env


# 全局适配器实例
adapter = PlatformAdapter()


def get_platform_info() -> PlatformInfo:
    """获取平台信息"""
    return adapter.info


def get_binary(name: str) -> Optional[Path]:
    """获取二进制文件"""
    return adapter.get_binary(name)


def get_platform_config() -> Dict[str, Any]:
    """获取平台配置"""
    return adapter.get_platform_config()


# ============ 导出 ============

__all__ = [
    'PlatformAdapter',
    'PlatformInfo',
    'adapter',
    'get_platform_info',
    'get_binary',
    'get_platform_config'
]
