"""
Sandbox Manager - 沙箱隔离与自动更新

实现安全部署和版本控制。

支持的技能标识格式：
- ClawHub 标识: @org/skill-name 或 skill-name
- Git URL: https://github.com/... 或 git@github.com:...
- 本地路径: /path/to/skill 或 ./relative/path

SudoTerminal 功能：
- 自动检测 sudo / root 权限可用性
- 无 sudo 时自动降级到用户空间方案
- 安全命令执行：白名单 + 路径校验
- 包管理器自动适配（apt/yum/brew/pip/conda）

CppToolchain 功能：
- 优先使用预编译嵌入产物（PrebuiltManager，零编译开销）
- 无 root 安装 C++ 编译工具链（gcc_impl/gxx_impl/binutils_impl/cmake/make）
- 从 PyPI 下载 manylinux 预编译 wheel（跳过编译）
- 预置常见 C++ 共享库（libstdc++, libgcc_s 等）
- 自动配置 CC/CXX/CMAKE_PREFIX_PATH/LD_LIBRARY_PATH
- **Meson 构建系统支持**（numpy 2.x 等包需要）：
  - pip --user 安装 meson/meson-python/ninja（无需 root）
  - gfortran 编译器支持（Fortran 绑定编译）
  - setup_meson_build_system() 一键配置完整 meson 构建环境
  - 自动注入 FC/MESON_BUILD_DIR/PKG_CONFIG_PATH 等环境变量

PrebuiltManager 功能：
- 管理预编译嵌入的二进制产物（扩展 + 工具链 + wheel + 可移植 Python）
- 预编译 hnswlib C 扩展直接加载（无需 pip install）
- 预打包工具链归档解压即用（无需 conda-forge 下载）
- cp314 wheel 自动使用可移植 Python 3.14 安装
- 运行 scripts/prebuild.py 构建预编译产物

PortablePythonManager 功能：
- 从预编译嵌入的 python-build-standalone 归档解压可移植 Python
- 沙箱环境无 sudo/root 也可使用 Python 3.14
- 自动解压到用户空间（~/.sandbox/python/python3.14/）
- 支持安装 cp314 预编译 wheel 和运行 Python 3.14 代码
- 提供环境变量配置，可与其他工具链配合使用
"""

import os
import json
import platform
import re
import subprocess
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass
from datetime import datetime
import hashlib
import logging

logger = logging.getLogger(__name__)

# ClawHub 默认仓库基础 URL
CLAWHUB_BASE_URL = "https://github.com/openclaw/skills"
# OpenClaw 默认 Git 组织
CLAWHUB_DEFAULT_ORG = "openclaw"


@dataclass
class VersionInfo:
    """版本信息"""
    version: str
    commit: str
    timestamp: str
    checksum: str


class SudoTerminal:
    """
    沙箱环境终端适配器

    在没有 sudo 权限的环境中，自动降级到用户空间方案：
    - apt-get install → pip install --user / conda install
    - 系统级安装 → 用户空间安装 (~/.local)
    - 全局写入 → 沙箱目录写入

    使用示例:
    >>> terminal = SudoTerminal()
    >>> result = terminal.run(["apt-get", "install", "-y", "libsqlite3-dev"])
    >>> # 自动降级为: pip install --user pysqlite3-binary
    >>> result = terminal.install_python_package("hnswlib")
    >>> # 如果 hnswlib 编译失败，自动降级为纯 Python 方案
    """

    # 允许在沙箱中执行的命令白名单
    SAFE_COMMANDS = {
        'pip', 'pip3', 'python', 'python3',
        'git', 'curl', 'wget', 'tar', 'unzip',
        'npm', 'node', 'npx',
        'conda', 'mamba',
        'make', 'cmake', 'meson', 'ninja',
        'cp', 'mv', 'mkdir', 'ln', 'chmod',
        'echo', 'cat', 'ls', 'find',
        'gcc', 'g++', 'cc', 'gfortran',
    }

    # 系统包到 pip 包的降级映射
    APT_TO_PIP_MAP = {
        'libsqlite3-dev': 'pysqlite3-binary',
        'sqlite3': 'pysqlite3-binary',
        'libpq-dev': 'psycopg2-binary',
        'libssl-dev': 'pyOpenSSL',
        'libffi-dev': 'cffi',
        'libxml2-dev': 'lxml',
        'libxslt1-dev': 'lxml',
        'zlib1g-dev': None,  # 无 pip 替代，需系统包
        'libbz2-dev': None,
        'libreadline-dev': None,
        'libncurses5-dev': None,
        'libncursesw5-dev': None,
        # meson 构建工具链（pip 安装无需 root）
        'meson': 'meson',
        'ninja-build': 'ninja',
    }

    def __init__(self, sandbox_dir: Optional[Path] = None):
        """
        初始化终端适配器

        Args:
            sandbox_dir: 沙箱目录（用于用户空间安装），默认 ~/.sandbox
        """
        self.sandbox_dir = sandbox_dir or Path.home() / '.sandbox'
        self._has_sudo: Optional[bool] = None
        self._is_root: Optional[bool] = None
        self._pip_user_base: Optional[Path] = None

        # 确保 sandbox_dir 存在
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)

    @property
    def has_sudo(self) -> bool:
        """检测当前环境是否有 sudo 权限"""
        if self._has_sudo is None:
            self._has_sudo = self._detect_sudo()
        return self._has_sudo

    @property
    def is_root(self) -> bool:
        """检测当前是否以 root 运行"""
        if self._is_root is None:
            self._is_root = os.getuid() == 0 if hasattr(os, 'getuid') else False
        return self._is_root

    @property
    def pip_user_base(self) -> Path:
        """获取 pip --user 安装的基础路径"""
        if self._pip_user_base is None:
            if sys.platform == 'win32':
                self._pip_user_base = Path(os.environ.get('APPDATA', '~')) / 'Python'
            else:
                self._pip_user_base = Path.home() / '.local'
        return self._pip_user_base

    def _detect_sudo(self) -> bool:
        """检测 sudo 是否可用"""
        if self.is_root:
            return True
        try:
            result = subprocess.run(
                ['sudo', '-n', 'true'],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _get_python_executable(self) -> str:
        """获取当前 Python 解释器路径"""
        return sys.executable or 'python3'

    def _get_pip_executable(self) -> str:
        """获取 pip 可执行路径"""
        # 优先使用与当前 Python 匹配的 pip
        python = self._get_python_executable()
        try:
            result = subprocess.run(
                [python, '-m', 'pip', '--version'],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return python
        except Exception:
            pass
        # 回退
        for cmd in ['pip3', 'pip']:
            if shutil.which(cmd):
                return cmd
        return 'pip'

    def run(
        self,
        cmd: List[str],
        capture: bool = True,
        timeout: int = 120,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        auto_adapt: bool = True,
    ) -> Dict[str, Any]:
        """
        安全执行命令，自动适配无 sudo 环境

        Args:
            cmd: 命令列表，如 ["apt-get", "install", "-y", "libsqlite3-dev"]
            capture: 是否捕获输出
            timeout: 超时秒数
            cwd: 工作目录
            env: 环境变量
            auto_adapt: 是否自动适配无 sudo 环境（降级为用户空间方案）

        Returns:
            Dict: {returncode, stdout, stderr, adapted, original_cmd}
        """
        original_cmd = list(cmd)
        adapted = False

        if auto_adapt:
            cmd, adapted = self._adapt_command(cmd)

        # 安全检查
        if not self._is_command_allowed(cmd):
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': f'命令不在白名单中: {cmd[0]}',
                'adapted': adapted,
                'original_cmd': original_cmd,
            }

        # 如果需要 sudo 且有 sudo 权限，添加 sudo 前缀
        if self._needs_sudo(cmd) and self.has_sudo and not self.is_root:
            cmd = ['sudo'] + cmd

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        # 确保 pip --user 安装的路径在 PATH 中
        user_bin = self.pip_user_base / 'bin'
        if str(user_bin) not in run_env.get('PATH', ''):
            run_env['PATH'] = f"{user_bin}:{run_env.get('PATH', '')}"

        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=run_env,
            )
            return {
                'returncode': result.returncode,
                'stdout': result.stdout if capture else '',
                'stderr': result.stderr if capture else '',
                'adapted': adapted,
                'original_cmd': original_cmd,
            }
        except FileNotFoundError:
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': f'命令不存在: {cmd[0]}',
                'adapted': adapted,
                'original_cmd': original_cmd,
            }
        except subprocess.TimeoutExpired:
            return {
                'returncode': -1,
                'stdout': '',
                'stderr': f'命令超时 ({timeout}s): {" ".join(cmd)}',
                'adapted': adapted,
                'original_cmd': original_cmd,
            }

    def _adapt_command(self, cmd: List[str]) -> Tuple[List[str], bool]:
        """
        适配命令：将需要 sudo 的系统命令降级为用户空间等价命令

        Returns:
            (adapted_cmd, was_adapted)
        """
        if not cmd:
            return cmd, False

        program = cmd[0]

        # apt-get install → pip install --user
        if program in ('apt-get', 'apt', 'yum', 'dnf', 'brew', 'pacman'):
            if 'install' in cmd:
                return self._adapt_package_install(cmd), True
            # 其他子命令（update, remove 等）在无 sudo 时跳过
            if not self.has_sudo:
                return ['echo', f'[sandbox] 跳过需要 root 的命令: {" ".join(cmd)}'], True
            return cmd, False

        # pip install → pip install --user
        if program in ('pip', 'pip3') and 'install' in cmd:
            if not self.has_sudo and '--user' not in cmd:
                return cmd + ['--user'], True

        # python -m pip install → python -m pip install --user
        if program in (self._get_python_executable(), 'python', 'python3'):
            if len(cmd) >= 3 and cmd[1] == '-m' and cmd[2] == 'pip' and 'install' in cmd:
                if not self.has_sudo and '--user' not in cmd:
                    return cmd + ['--user'], True

        return cmd, False

    def _adapt_package_install(self, cmd: List[str]) -> List[str]:
        """将系统包安装命令适配为 pip --user 或跳过"""
        # 提取包名（跳过 -y, install 等标志）
        packages = [arg for arg in cmd[2:] if not arg.startswith('-')]

        pip_packages = []
        skipped = []

        for pkg in packages:
            pip_equiv = self.APT_TO_PIP_MAP.get(pkg)
            if pip_equiv is not None:
                if pip_equiv:
                    pip_packages.append(pip_equiv)
                else:
                    skipped.append(pkg)
            else:
                # 未知包：尝试同名 pip 包
                pip_packages.append(pkg)

        result_cmd = []
        if pip_packages:
            python = self._get_python_executable()
            result_cmd = [python, '-m', 'pip', 'install', '--user'] + pip_packages

        if skipped:
            skip_msg = f"[sandbox] 跳过无 pip 替代的系统包: {', '.join(skipped)}"
            if result_cmd:
                # 先执行 pip 安装，再打印跳过信息
                return ['sh', '-c',
                        f'{" ".join(result_cmd)}; echo "{skip_msg}"']
            return ['echo', skip_msg]

        return result_cmd or ['echo', '[sandbox] 无可安装的包']

    def _is_command_allowed(self, cmd: List[str]) -> bool:
        """检查命令是否在白名单中"""
        if not cmd:
            return False
        program = Path(cmd[0]).name
        # 允许绝对路径命令（如 /usr/bin/python3）
        if '/' in cmd[0]:
            return program in self.SAFE_COMMANDS or program.startswith('python')
        return program in self.SAFE_COMMANDS or program in ('sudo', 'sh', 'bash', 'env')

    def _needs_sudo(self, cmd: List[str]) -> bool:
        """判断命令是否需要 sudo 权限"""
        if not cmd:
            return False
        program = cmd[0]
        return program in ('apt-get', 'apt', 'yum', 'dnf', 'systemctl', 'service')

    # ==================== 高级安装接口 ====================

    def install_python_package(
        self,
        package: str,
        version: Optional[str] = None,
        fallback: Optional[str] = None,
        pure_python_fallback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        安装 Python 包，自动处理编译失败和权限问题

        降级策略：
        1. pip install --user <package>        (用户空间安装)
        2. CppToolchain.install_cpp_package    (预编译 wheel / 便携编译器)
        3. pip install --user <fallback>       (预编译二进制替代)
        4. pip install --user <pure_python>    (纯 Python 替代，无需编译)

        Args:
            package: 包名（如 "hnswlib"）
            version: 版本约束（如 ">=0.8.0"）
            fallback: 编译失败时的二进制替代包（如 "hnswlib-bind"）
            pure_python_fallback: 纯 Python 替代包（无需编译）

        Returns:
            Dict: 安装结果
        """
        python = self._get_python_executable()
        pkg_spec = f"{package}{version}" if version else package
        errors = []

        # 策略1: 直接安装
        result = self.run(
            [python, '-m', 'pip', 'install', '--user', pkg_spec],
            timeout=300,
        )
        if result['returncode'] == 0:
            return {
                'status': 'success',
                'package': package,
                'strategy': 'pip_user',
                'output': result['stdout'],
            }
        errors.append(f"pip install {pkg_spec} 失败: {result['stderr'][:200]}")

        # 策略2: 用 CppToolchain 安装（预编译 wheel / 便携编译器 / conda）
        try:
            toolchain = CppToolchain(sandbox_dir=self.sandbox_dir)
            cpp_result = toolchain.install_cpp_package(
                package, version=version,
                fallback=fallback,
                pure_python_fallback=pure_python_fallback,
            )
            if cpp_result['status'] == 'success':
                return cpp_result
            errors.append(f"CppToolchain 安装失败: {cpp_result.get('errors', [])}")
        except Exception as e:
            errors.append(f"CppToolchain 异常: {e}")

        # 策略3: 尝试二进制替代包
        if fallback:
            result = self.run(
                [python, '-m', 'pip', 'install', '--user', fallback],
                timeout=300,
            )
            if result['returncode'] == 0:
                return {
                    'status': 'success',
                    'package': fallback,
                    'original_package': package,
                    'strategy': 'fallback_binary',
                    'output': result['stdout'],
                }
            errors.append(f"pip install {fallback} 失败: {result['stderr'][:200]}")

        # 策略4: 纯 Python 替代
        if pure_python_fallback:
            result = self.run(
                [python, '-m', 'pip', 'install', '--user', pure_python_fallback],
                timeout=300,
            )
            if result['returncode'] == 0:
                return {
                    'status': 'success',
                    'package': pure_python_fallback,
                    'original_package': package,
                    'strategy': 'pure_python_fallback',
                    'output': result['stdout'],
                }
            errors.append(f"pip install {pure_python_fallback} 失败: {result['stderr'][:200]}")

        return {
            'status': 'failed',
            'package': package,
            'errors': errors,
            'suggestion': (
                f"无法安装 {package}，建议：\n"
                f"  1. 运行 CppToolchain().setup_full_environment() 安装完整编译环境\n"
                f"  2. 使用内置纯 Python 替代方案（如 HNSWIndex，无需编译）\n"
                f"  3. 在有 sudo 权限的环境预编译后复制 .so 文件\n"
                f"  4. 使用 conda: conda install -c conda-forge {package}"
            ),
        }

    def install_system_package(self, package: str) -> Dict[str, Any]:
        """
        安装系统包，无 sudo 时自动降级

        Args:
            package: 系统包名（如 "libsqlite3-dev"）

        Returns:
            Dict: 安装结果
        """
        if self.has_sudo:
            # 有 sudo，直接安装
            result = self.run(['apt-get', 'install', '-y', package])
            return {
                'status': 'success' if result['returncode'] == 0 else 'failed',
                'strategy': 'apt_with_sudo',
                'output': result.get('stdout', ''),
                'error': result.get('stderr', ''),
            }

        # 无 sudo，尝试 pip 替代
        pip_equiv = self.APT_TO_PIP_MAP.get(package)
        if pip_equiv:
            if not pip_equiv:
                return {
                    'status': 'unavailable',
                    'package': package,
                    'message': f'系统包 {package} 无 pip 替代，需要 sudo 权限安装',
                }
            result = self.install_python_package(pip_equiv)
            result['original_system_package'] = package
            result['strategy'] = 'pip_fallback'
            return result

        # 尝试同名 pip 包
        return self.install_python_package(package)

    def get_environment_info(self) -> Dict[str, Any]:
        """获取当前环境信息"""
        return {
            'has_sudo': self.has_sudo,
            'is_root': self.is_root,
            'python_executable': self._get_python_executable(),
            'pip_user_base': str(self.pip_user_base),
            'sandbox_dir': str(self.sandbox_dir),
            'platform': sys.platform,
            'uid': os.getuid() if hasattr(os, 'getuid') else None,
            'user': os.environ.get('USER', 'unknown'),
            'home': str(Path.home()),
            'path_user_bin': str(self.pip_user_base / 'bin'),
            'in_path': str(self.pip_user_base / 'bin') in os.environ.get('PATH', ''),
        }


class PrebuiltManager:
    """
    预编译二进制管理器

    管理从源码预编译并嵌入的 C++ 扩展和工具链，避免沙箱环境运行时编译。

    预编译产物目录结构:
        prebuilt/
        ├── extensions/          # 预编译 Python C 扩展
        │   └── hnswlib/        # hnswlib 预编译 wheel 或 .so
        │       ├── *.whl
        │       └── METADATA.json
        ├── toolchain/           # 预编译 C++ 工具链归档
        │   ├── gcc_toolchain.tar.bz2
        │   ├── cmake_toolchain.tar.bz2
        │   └── METADATA.json
        └── wheels/              # 预缓存 wheel 文件
            └── *.whl

    使用示例:
    >>> pm = PrebuiltManager()
    >>> # 加载预编译的 hnswlib
    >>> hnswlib_mod = pm.load_prebuilt_extension('hnswlib')
    >>> # 安装预编译工具链
    >>> pm.setup_prebuilt_toolchain()
    """

    def __init__(self, prebuilt_dir: Optional[Path] = None):
        self.prebuilt_dir = prebuilt_dir or Path(__file__).parent / 'prebuilt'
        self.extensions_dir = self.prebuilt_dir / 'extensions'
        self.toolchain_dir = self.prebuilt_dir / 'toolchain'
        self.wheels_dir = self.prebuilt_dir / 'wheels'
        self.python_dir = self.prebuilt_dir / 'python'
        self.mkl_dir = self.prebuilt_dir / 'mkl'
        self._portable_python = None  # 延迟初始化 PortablePythonManager

    def has_prebuilt_extension(self, name: str) -> bool:
        """检查是否有预编译的 C 扩展"""
        ext_dir = self.extensions_dir / name
        if not ext_dir.exists():
            return False
        # 检查是否有 .whl 或 .so 文件
        return bool(list(ext_dir.glob('*.whl'))) or bool(list(ext_dir.glob('*.so')))

    def load_prebuilt_extension(self, name: str):
        """
        加载预编译的 C 扩展模块

        优先级: .whl 安装（当前 Python）> .whl 安装（可移植 Python）> .so 直接加载

        Args:
            name: 扩展名 (如 'hnswlib')

        Returns:
            模块对象或 None
        """
        ext_dir = self.extensions_dir / name
        if not ext_dir.exists():
            return None

        # 策略1: 从预编译 wheel 安装（当前 Python）
        wheels = sorted(ext_dir.glob('*.whl'))
        for wheel in wheels:
            if self._is_wheel_compatible(wheel):
                result = self._install_wheel(wheel)
                if result:
                    try:
                        return __import__(name)
                    except ImportError:
                        pass

        # 策略1.5: 通过可移植 Python 安装其他版本的 wheel
        ppm = self._get_portable_python_manager()
        if ppm:
            import re
            for wheel in wheels:
                cp_match = re.search(r'cp(\d+)', wheel.name)
                if not cp_match:
                    continue
                wheel_py_ver = cp_match.group(1)
                ppm_ver = f"{wheel_py_ver[0]}.{wheel_py_ver[1:]}"
                if ppm.has_portable_python(ppm_ver):
                    result = ppm.install_wheel(str(wheel), version=ppm_ver)
                    if result.get('status') == 'success':
                        logger.info(f"通过可移植 Python {ppm_ver} 安装了 {wheel.name}")
                        # 注意: 这里无法返回模块对象（运行在不同 Python 进程），
                        # 但已安装成功，调用方可通过 ppm.run_python 使用

        # 策略2: 直接加载 .so 文件
        so_files = sorted(ext_dir.glob('*.so'))
        for so_file in so_files:
            if self._is_so_compatible(so_file):
                return self._load_so_directly(name, so_file)

        return None

    def _is_wheel_compatible(self, wheel_path: Path) -> bool:
        """检查 wheel 是否与当前 Python/平台兼容"""
        fname = wheel_path.name
        # wheel 文件名: {name}-{ver}-{pytag}-{abi}-{platform}.whl
        py_ver = f"{sys.version_info.major}{sys.version_info.minor}"
        machine = platform.machine()

        # 纯 Python wheel
        if 'py3' in fname or 'py2.py3' in fname:
            return True

        # abi3 wheel (稳定 ABI，最低 Python 版本 <= 当前版本即可)
        if 'abi3' in fname:
            # 从文件名提取最低 cp 版本，如 cp38-abi3 表示 >= 3.8
            import re
            m = re.search(r'cp(\d+)', fname)
            if m:
                min_ver = int(m.group(1))
                cur_ver = sys.version_info.major * 10 + sys.version_info.minor
                if cur_ver >= min_ver and machine in fname:
                    return True

        # 精确匹配当前 Python 版本（cp312 只兼容 cp312）
        if f'cp{py_ver}' in fname and machine in fname:
            return True

        # 注意: 不再宽泛匹配 cp3* 或仅凭平台就认为兼容
        # 例如 cp314 的 wheel 不能在 cp312 上使用

        return False

    def _is_so_compatible(self, so_path: Path) -> bool:
        """检查 .so 是否与当前 Python 兼容"""
        fname = so_path.name
        py_ver = f"{sys.version_info.major}{sys.version_info.minor}"
        # cpython-310, cpython-311 等
        if f'cpython-{py_ver}' in fname:
            return True
        # abi3 (稳定 ABI)
        if 'abi3' in fname:
            return True
        return False

    def _install_wheel(self, wheel_path: Path) -> bool:
        """从本地 wheel 安装"""
        fname = wheel_path.name
        py_ver = f"{sys.version_info.major}{sys.version_info.minor}"

        # 判断 wheel 的目标 Python 版本
        import re
        cp_match = re.search(r'cp(\d+)', fname)
        wheel_py_ver = cp_match.group(1) if cp_match else None

        # 如果 wheel 的目标版本与当前 Python 不匹配，尝试使用可移植 Python
        if wheel_py_ver and wheel_py_ver != py_ver:
            ppm = self._get_portable_python_manager()
            if ppm:
                target_python = ppm.get_python_executable()
                if target_python:
                    try:
                        cmd = [target_python, '-m', 'pip', 'install', '--user',
                               '--no-deps', str(wheel_path)]
                        result = subprocess.run(
                            cmd, capture_output=True, text=True, timeout=60,
                        )
                        if result.returncode != 0 and 'externally-managed' in result.stderr:
                            cmd.append('--break-system-packages')
                            result = subprocess.run(
                                cmd, capture_output=True, text=True, timeout=60,
                            )
                        if result.returncode == 0:
                            logger.info(f"通过可移植 Python 安装 wheel: {fname}")
                            return True
                    except Exception as e:
                        logger.debug(f"通过可移植 Python 安装 wheel 失败: {e}")

        # 使用当前 Python 安装
        python = sys.executable or 'python3'
        try:
            cmd = [python, '-m', 'pip', 'install', '--user', '--no-deps', str(wheel_path)]
            # Python 3.14+ 需要 --break-system-packages 在外部管理的环境中
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 and 'externally-managed' in result.stderr:
                cmd.append('--break-system-packages')
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60,
                )
            return result.returncode == 0
        except Exception as e:
            logger.debug(f"安装预编译 wheel 失败: {e}")
            return False

    def _get_portable_python_manager(self):
        """获取 PortablePythonManager 实例（延迟初始化）"""
        if self._portable_python is None:
            try:
                ppm = PortablePythonManager(self.prebuilt_dir)
                if ppm.has_portable_python():
                    self._portable_python = ppm
            except Exception:
                pass
        return self._portable_python

    def _load_so_directly(self, name: str, so_path: Path):
        """直接通过 importlib 加载 .so 文件"""
        import importlib.util
        try:
            # 查找对应的 __init__.py 或直接加载 .so
            init_py = so_path.parent / '__init__.py'
            if init_py.exists():
                # 有包结构，添加到 sys.path
                parent = str(so_path.parent.parent)
                if parent not in sys.path:
                    sys.path.insert(0, parent)
                return __import__(name)
            else:
                # 单独的 .so 模块
                spec = importlib.util.spec_from_file_location(name, str(so_path))
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[name] = mod
                    spec.loader.exec_module(mod)
                    return mod
        except Exception as e:
            logger.debug(f"直接加载 .so 失败: {e}")
        return None

    def has_prebuilt_toolchain(self) -> bool:
        """检查是否有预打包的工具链"""
        if not self.toolchain_dir.exists():
            return False
        # 检查 gcc 工具链归档
        return bool(list(self.toolchain_dir.glob('*gcc*'))) or \
               bool(list(self.toolchain_dir.glob('*toolchain*')))

    def setup_prebuilt_toolchain(self, target_dir: Optional[Path] = None) -> Dict[str, Any]:
        """
        从预打包归档安装工具链

        Args:
            target_dir: 安装目标目录，默认 ~/.sandbox/cpp_toolchain

        Returns:
            Dict: 安装结果
        """
        if not self.has_prebuilt_toolchain():
            return {'status': 'unavailable', 'message': '无预打包工具链'}

        target = target_dir or Path.home() / '.sandbox' / 'cpp_toolchain'
        target.mkdir(parents=True, exist_ok=True)

        results = {}
        for archive in sorted(self.toolchain_dir.iterdir()):
            if archive.suffix in ('.bz2', '.gz', '.xz') or archive.name.endswith('.tar.bz2'):
                try:
                    import tarfile
                    open_mode = 'r:bz2' if archive.name.endswith('.bz2') else \
                                'r:gz' if archive.name.endswith('.gz') else 'r:xz'
                    with tarfile.open(str(archive), open_mode) as tar:
                        tar.extractall(path=str(target))
                    results[archive.name] = 'success'
                except Exception as e:
                    results[archive.name] = f'failed: {e}'
            elif archive.suffix == '.zip':
                import zipfile
                try:
                    with zipfile.ZipFile(str(archive)) as zf:
                        zf.extractall(str(target))
                    results[archive.name] = 'success'
                except Exception as e:
                    results[archive.name] = f'failed: {e}'

        # 修复可执行权限
        for bindir in target.rglob('bin'):
            if bindir.is_dir():
                for f in bindir.iterdir():
                    try:
                        if f.is_file() and not os.access(f, os.X_OK):
                            os.chmod(f, 0o755)
                    except Exception:
                        pass

        # 修复归档中可能存在的绝对路径符号链接
        self._fix_absolute_symlinks(target)

        # 确保常用短名符号链接存在（使用相对路径）
        self._ensure_toolchain_symlinks(target)

        return {
            'status': 'success' if any(v == 'success' for v in results.values()) else 'failed',
            'archives': results,
            'target': str(target),
        }

    def _fix_absolute_symlinks(self, root: Path):
        """修复绝对路径的符号链接为相对路径"""
        for item in root.rglob('*'):
            if item.is_symlink():
                try:
                    link_target = os.readlink(str(item))
                    if os.path.isabs(link_target):
                        # 绝对路径 → 相对路径
                        # 找到目标是否在 root 内
                        if link_target.startswith(str(root)):
                            rel = os.path.relpath(link_target, str(item.parent))
                            item.unlink()
                            os.symlink(rel, str(item))
                        else:
                            # 指向 root 外的绝对路径，尝试在 root 内查找
                            target_name = Path(link_target).name
                            # 在同目录或子目录中搜索
                            for candidate in item.parent.rglob(target_name):
                                if candidate.name == target_name and candidate.exists():
                                    rel = os.path.relpath(str(candidate), str(item.parent))
                                    item.unlink()
                                    os.symlink(rel, str(item))
                                    break
                except Exception:
                    pass

    def _ensure_toolchain_symlinks(self, target: Path):
        """确保工具链有正确的短名符号链接（使用相对路径）"""
        bin_dir = target / 'bin'
        if not bin_dir.exists():
            return

        short_links = {
            'gcc': 'x86_64-conda-linux-gnu-gcc',
            'g++': 'x86_64-conda-linux-gnu-g++',
            'gfortran': 'x86_64-conda-linux-gnu-gfortran',
            'cc': 'x86_64-conda-linux-gnu-gcc',
            'c++': 'x86_64-conda-linux-gnu-g++',
            'ld': 'x86_64-conda-linux-gnu-ld',
            'as': 'x86_64-conda-linux-gnu-as',
            'nm': 'x86_64-conda-linux-gnu-nm',
            'ar': 'x86_64-conda-linux-gnu-ar',
            'ranlib': 'x86_64-conda-linux-gnu-ranlib',
            'strip': 'x86_64-conda-linux-gnu-strip',
            'objcopy': 'x86_64-conda-linux-gnu-objcopy',
        }
        for link_name, target_name in short_links.items():
            # 查找目标可执行文件
            target_exe = None
            # 先在 bin_dir 本身查找
            candidate = bin_dir / target_name
            if candidate.exists() or candidate.is_symlink():
                target_exe = candidate
            else:
                # 递归搜索
                for bindir in target.rglob('bin'):
                    c = bindir / target_name
                    if c.exists():
                        target_exe = c
                        break

            if target_exe:
                link_path = bin_dir / link_name
                try:
                    if link_path.exists() or link_path.is_symlink():
                        link_path.unlink()
                    rel = os.path.relpath(str(target_exe), str(bin_dir))
                    os.symlink(rel, str(link_path))
                except Exception:
                    pass

    def list_prebuilt_wheels(self) -> List[Dict[str, str]]:
        """列出所有预缓存的 wheel 文件"""
        wheels = []
        if self.wheels_dir.exists():
            for whl in self.wheels_dir.glob('*.whl'):
                # 解析 wheel 文件名: {name}-{ver}-{pytag}-{abi}-{platform}.whl
                parts = whl.name.replace('.whl', '').split('-')
                name = parts[0] if parts else whl.name
                ver = parts[1] if len(parts) > 1 else 'unknown'
                wheels.append({
                    'name': name,
                    'version': ver,
                    'filename': whl.name,
                    'path': str(whl),
                })
        return wheels

    def install_prebuilt_wheel(self, package_name: str) -> Dict[str, Any]:
        """从预缓存目录安装 wheel

        优先安装与当前 Python 兼容的 wheel。
        如果当前 Python 无匹配 wheel 但有可移植 Python 匹配的 wheel，
        则通过可移植 Python 安装。
        """
        if not self.wheels_dir.exists():
            return {'status': 'unavailable', 'message': '无预缓存 wheel'}

        # 第一步：尝试安装与当前 Python 兼容的 wheel
        for whl in self.wheels_dir.glob(f'{package_name}*.whl'):
            if self._is_wheel_compatible(whl):
                if self._install_wheel(whl):
                    return {
                        'status': 'success',
                        'package': package_name,
                        'wheel': whl.name,
                        'strategy': 'prebuilt_wheel',
                    }
                return {'status': 'failed', 'error': f'安装 {whl.name} 失败'}

        # 第二步：尝试通过可移植 Python 安装其他版本的 wheel
        ppm = self._get_portable_python_manager()
        if ppm:
            import re
            for whl in self.wheels_dir.glob(f'{package_name}*.whl'):
                # 检查 wheel 是否与可移植 Python 兼容
                cp_match = re.search(r'cp(\d+)', whl.name)
                if not cp_match:
                    continue
                wheel_py_ver = cp_match.group(1)  # e.g. "314"
                ppm_ver = f"{wheel_py_ver[0]}.{wheel_py_ver[1:]}"  # "3.14"
                if ppm.has_portable_python(ppm_ver):
                    result = ppm.install_wheel(str(whl), version=ppm_ver)
                    if result.get('status') == 'success':
                        return {
                            'status': 'success',
                            'package': package_name,
                            'wheel': whl.name,
                            'strategy': 'portable_python_wheel',
                            'python_version': ppm_ver,
                        }

        return {'status': 'not_found', 'message': f'未找到 {package_name} 的预缓存 wheel'}

    def has_mkl_runtime(self) -> bool:
        """检查是否有预编译的 MKL 运行时库"""
        if not self.mkl_dir.exists():
            return False
        mkl_lib = self.mkl_dir / 'lib'
        return bool(list(mkl_lib.glob('libmkl_rt.so*'))) if mkl_lib.exists() else False

    def setup_mkl_runtime(self, target_dir: Optional[Path] = None) -> Dict[str, Any]:
        """
        安装预编译的 MKL 运行时库到用户空间

        将 prebuilt/mkl/lib 中的 MKL 共享库复制到用户空间，
        并设置 LD_LIBRARY_PATH 环境变量，使 MKL 链接的 numpy 等包能运行。

        Args:
            target_dir: 安装目标目录，默认 ~/.sandbox/mkl

        Returns:
            Dict: 安装结果
        """
        if not self.has_mkl_runtime():
            return {'status': 'unavailable', 'message': '无预编译 MKL 运行时'}

        target = target_dir or Path.home() / '.sandbox' / 'mkl'
        target_lib = target / 'lib'
        target_lib.mkdir(parents=True, exist_ok=True)

        # 复制 MKL 库文件
        mkl_lib = self.mkl_dir / 'lib'
        copied = 0
        for f in mkl_lib.iterdir():
            if f.is_file() and (f.suffix == '.so' or '.so.' in f.name):
                shutil.copy2(str(f), str(target_lib / f.name))
                copied += 1
            elif f.is_symlink():
                # 保留符号链接
                link_target = os.readlink(str(f))
                link_path = target_lib / f.name
                if link_path.exists() or link_path.is_symlink():
                    link_path.unlink()
                os.symlink(link_target, str(link_path))
                copied += 1

        # 创建符号链接（.so -> .so.3）
        metadata = self._load_mkl_metadata()
        if metadata and 'symlinks' in metadata:
            for link_name, link_target in metadata['symlinks'].items():
                link_path = target_lib / link_name
                if not link_path.exists():
                    try:
                        os.symlink(link_target, str(link_path))
                    except Exception:
                        pass

        # 复制头文件（供编译使用）
        mkl_include = self.mkl_dir / 'include'
        if mkl_include.exists():
            target_include = target / 'include'
            target_include.mkdir(parents=True, exist_ok=True)
            for f in mkl_include.iterdir():
                if f.is_file():
                    shutil.copy2(str(f), str(target_include / f.name))

        result = {
            'status': 'success' if copied > 0 else 'failed',
            'copied': copied,
            'target': str(target),
            'lib_dir': str(target_lib),
        }

        if copied > 0:
            logger.info(f"MKL 运行时安装完成: {copied} 个文件 -> {target}")

        return result

    def get_mkl_env(self, target_dir: Optional[Path] = None) -> Dict[str, str]:
        """
        获取 MKL 运行时所需的环境变量

        Args:
            target_dir: MKL 安装目录

        Returns:
            Dict: 环境变量
        """
        target = target_dir or Path.home() / '.sandbox' / 'mkl'
        target_lib = target / 'lib'

        env = os.environ.copy()

        if target_lib.exists():
            # LD_LIBRARY_PATH
            existing = env.get('LD_LIBRARY_PATH', '')
            lib_str = str(target_lib)
            if lib_str not in existing:
                env['LD_LIBRARY_PATH'] = f"{lib_str}:{existing}" if existing else lib_str

            # LIBRARY_PATH (link time)
            existing_lib = env.get('LIBRARY_PATH', '')
            if lib_str not in existing_lib:
                env['LIBRARY_PATH'] = f"{lib_str}:{existing_lib}" if existing_lib else lib_str

        target_include = target / 'include'
        if target_include.exists():
            # CPATH (compile time)
            existing_cpath = env.get('CPATH', '')
            inc_str = str(target_include)
            if inc_str not in existing_cpath:
                env['CPATH'] = f"{inc_str}:{existing_cpath}" if existing_cpath else inc_str

        # MKL 线程模型: 使用 GNU OpenMP (gomp) 与 GCC 编译的代码兼容
        env.setdefault('MKL_THREADING_LAYER', 'GNU')

        return env

    def _load_mkl_metadata(self) -> Optional[Dict]:
        """加载 mkl/METADATA.json"""
        metadata_path = self.mkl_dir / 'METADATA.json'
        if metadata_path.exists():
            try:
                return json.loads(metadata_path.read_text())
            except Exception:
                pass
        return None


class PortablePythonManager:
    """
    可移植 Python 管理器

    在沙箱环境（仅有 Python 3.12，无 root/sudo）中提供 Python 3.14 运行能力。

    从预编译嵌入的 python-build-standalone 归档解压到用户空间，
    无需系统安装即可使用。可用于：
    - 运行需要 Python 3.14 的代码
    - 安装 cp314 预编译 wheel
    - 编译需要 Python 3.14 的 C 扩展

    使用示例:
    >>> ppm = PortablePythonManager()
    >>> if ppm.has_portable_python():
    ...     python314 = ppm.get_python_executable()
    ...     subprocess.run([python314, '-c', 'import sys; print(sys.version)'])
    ...     ppm.install_package('numpy')
    """

    def __init__(self, prebuilt_dir: Optional[Path] = None):
        self.prebuilt_dir = prebuilt_dir or Path(__file__).parent / 'prebuilt'
        self.python_archive_dir = self.prebuilt_dir / 'python'
        self.install_base = Path.home() / '.sandbox' / 'python'
        self._metadata = None
        self._extracted_version = None

    def has_portable_python(self, version: str = None) -> bool:
        """
        检查是否有预编译的可移植 Python

        Args:
            version: Python 版本（如 "3.14"），为 None 则检查任意版本

        Returns:
            bool: 是否有可用的可移植 Python
        """
        if not self.python_archive_dir.exists():
            return False

        # 检查 METADATA.json
        metadata = self._load_metadata()
        if not metadata:
            # 回退：检查是否有 tar.gz 归档
            archives = list(self.python_archive_dir.glob('python-*.tar.gz'))
            if not archives:
                return False
            return True

        if version:
            ver_short = metadata.get('version_short', '')
            return ver_short == version

        return True

    def get_python_executable(self, version: str = None) -> Optional[str]:
        """
        获取可移植 Python 解释器路径

        首次调用时自动解压到用户空间（~/.sandbox/python/）。

        Args:
            version: Python 版本（如 "3.14"），为 None 则返回默认可移植 Python

        Returns:
            str: Python 解释器路径，不可用返回 None
        """
        if not self.has_portable_python(version):
            return None

        metadata = self._load_metadata()

        # 确定解压后的目标路径
        ver_short = version or (metadata.get('version_short', '') if metadata else '')
        if not ver_short:
            # 从归档文件名推断版本
            for archive in self.python_archive_dir.glob('python-*.tar.gz'):
                import re
                m = re.search(r'python-(\d+\.\d+)', archive.name)
                if m:
                    ver_short = m.group(1)
                    break

        if not ver_short:
            return None

        python_home = self.install_base / f'python{ver_short}'
        exe_relpath = (metadata.get('executable_relpath', f'bin/python{ver_short}')
                       if metadata else f'bin/python{ver_short}')
        python_exe = python_home / exe_relpath

        # 如果已解压且可执行，直接返回
        if python_exe.exists() and os.access(python_exe, os.X_OK):
            # 缓存版本号
            self._extracted_version = ver_short
            return str(python_exe)

        # 需要解压
        if self._extract_portable_python(ver_short, python_home):
            if python_exe.exists() and os.access(python_exe, os.X_OK):
                self._extracted_version = ver_short
                return str(python_exe)

        return None

    def get_pip_executable(self, version: str = None) -> Optional[str]:
        """
        获取可移植 Python 的 pip 路径

        Args:
            version: Python 版本

        Returns:
            str: pip 可执行路径，不可用返回 None
        """
        python = self.get_python_executable(version)
        if not python:
            return None
        # pip 通常通过 python -m pip 调用
        return python

    def get_python_home(self, version: str = None) -> Optional[Path]:
        """
        获取可移植 Python 的安装根目录

        Args:
            version: Python 版本

        Returns:
            Path: 安装根目录
        """
        metadata = self._load_metadata()
        ver_short = version or (metadata.get('version_short', '') if metadata else '')
        if not ver_short:
            for archive in self.python_archive_dir.glob('python-*.tar.gz'):
                import re
                m = re.search(r'python-(\d+\.\d+)', archive.name)
                if m:
                    ver_short = m.group(1)
                    break
        if not ver_short:
            return None
        return self.install_base / f'python{ver_short}'

    def install_package(self, package: str, version: str = None) -> Dict[str, Any]:
        """
        使用可移植 Python 安装包

        Args:
            package: 包名或包规格（如 'numpy', 'numpy==2.4.4'）
            version: Python 版本（如 "3.14"）

        Returns:
            Dict: 安装结果
        """
        python = self.get_python_executable(version)
        if not python:
            return {'status': 'unavailable', 'message': '无可移植 Python'}

        try:
            cmd = [python, '-m', 'pip', 'install', '--user', package]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0 and 'externally-managed' in result.stderr:
                cmd.append('--break-system-packages')
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300,
                )
            if result.returncode == 0:
                return {'status': 'success', 'package': package}
            return {'status': 'failed', 'error': result.stderr[:500]}
        except Exception as e:
            return {'status': 'failed', 'error': str(e)}

    def install_wheel(self, wheel_path: str, version: str = None) -> Dict[str, Any]:
        """
        使用可移植 Python 安装 wheel 文件

        Args:
            wheel_path: wheel 文件路径
            version: Python 版本

        Returns:
            Dict: 安装结果
        """
        python = self.get_python_executable(version)
        if not python:
            return {'status': 'unavailable', 'message': '无可移植 Python'}

        try:
            cmd = [python, '-m', 'pip', 'install', '--user', '--no-deps', str(wheel_path)]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 and 'externally-managed' in result.stderr:
                cmd.append('--break-system-packages')
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60,
                )
            if result.returncode == 0:
                return {'status': 'success', 'wheel': str(wheel_path)}
            return {'status': 'failed', 'error': result.stderr[:500]}
        except Exception as e:
            return {'status': 'failed', 'error': str(e)}

    def get_environment(self, version: str = None) -> Dict[str, str]:
        """
        获取使用可移植 Python 的环境变量

        Args:
            version: Python 版本

        Returns:
            Dict: 环境变量
        """
        python_home = self.get_python_home(version)
        if not python_home or not python_home.exists():
            return os.environ.copy()

        env = os.environ.copy()

        # PATH: 添加可移植 Python 的 bin 目录
        python_bin = python_home / 'bin'
        if python_bin.exists():
            env['PATH'] = f"{python_bin}:{env.get('PATH', '')}"

        # LD_LIBRARY_PATH: 添加可移植 Python 的 lib 目录
        python_lib = python_home / 'lib'
        if python_lib.exists():
            existing = env.get('LD_LIBRARY_PATH', '')
            env['LD_LIBRARY_PATH'] = f"{python_lib}:{existing}" if existing else str(python_lib)

        return env

    def run_python(self, args: list, version: str = None,
                   capture: bool = True, timeout: int = 120) -> Dict[str, Any]:
        """
        使用可移植 Python 执行命令

        Args:
            args: 命令参数（不含 python 本身，如 ['-c', 'print(1)']）
            version: Python 版本
            capture: 是否捕获输出
            timeout: 超时秒数

        Returns:
            Dict: 执行结果
        """
        python = self.get_python_executable(version)
        if not python:
            return {'status': 'unavailable', 'message': '无可移植 Python'}

        cmd = [python] + args
        env = self.get_environment(version)

        try:
            result = subprocess.run(
                cmd, capture_output=capture, text=True,
                timeout=timeout, env=env,
            )
            return {
                'status': 'success' if result.returncode == 0 else 'failed',
                'returncode': result.returncode,
                'stdout': result.stdout if capture else '',
                'stderr': result.stderr if capture else '',
            }
        except subprocess.TimeoutExpired:
            return {'status': 'timeout', 'message': f'执行超时 ({timeout}s)'}
        except Exception as e:
            return {'status': 'error', 'message': str(e)}

    def check_available_versions(self) -> List[Dict[str, Any]]:
        """
        检查可用的可移植 Python 版本

        Returns:
            List[Dict]: 可用版本列表
        """
        versions = []
        if not self.python_archive_dir.exists():
            return versions

        metadata = self._load_metadata()
        if metadata:
            ver_short = metadata.get('version_short', '')
            full_ver = metadata.get('python_version', '')
            archive_name = metadata.get('archive', '')

            # 检查归档是否存在
            archive_path = self.python_archive_dir / archive_name
            if archive_path.exists():
                # 检查是否已解压
                python_home = self.install_base / f'python{ver_short}'
                exe_relpath = metadata.get('executable_relpath', f'bin/python{ver_short}')
                extracted = (python_home / exe_relpath).exists()

                versions.append({
                    'version': full_ver,
                    'version_short': ver_short,
                    'archive': archive_name,
                    'archive_size': archive_path.stat().st_size,
                    'extracted': extracted,
                    'python_home': str(python_home) if extracted else None,
                })

        # 如果没有 METADATA，扫描归档文件
        if not versions:
            for archive in self.python_archive_dir.glob('python-*.tar.gz'):
                import re
                m = re.search(r'python-(\d+\.\d+\.\d+)', archive.name)
                if m:
                    full_ver = m.group(1)
                    ver_short = '.'.join(full_ver.split('.')[:2])
                    python_home = self.install_base / f'python{ver_short}'
                    exe_path = python_home / f'bin/python{ver_short}'
                    extracted = exe_path.exists()

                    versions.append({
                        'version': full_ver,
                        'version_short': ver_short,
                        'archive': archive.name,
                        'archive_size': archive.stat().st_size,
                        'extracted': extracted,
                        'python_home': str(python_home) if extracted else None,
                    })

        return versions

    def _load_metadata(self) -> Optional[Dict]:
        """加载 python/METADATA.json"""
        if self._metadata is not None:
            return self._metadata

        metadata_path = self.python_archive_dir / 'METADATA.json'
        if metadata_path.exists():
            try:
                self._metadata = json.loads(metadata_path.read_text())
                return self._metadata
            except Exception:
                pass
        return None

    def _extract_portable_python(self, version_short: str,
                                  target_dir: Path) -> bool:
        """
        解压可移植 Python 归档到用户空间

        python-build-standalone 的归档结构为:
            cpython-3.14.4+.../python/  (install_only 格式)
        解压后需要将内层 python/ 目录映射到 target_dir。

        Args:
            version_short: Python 短版本号（如 "3.14"）
            target_dir: 解压目标目录

        Returns:
            bool: 是否成功
        """
        import tarfile

        # 查找归档
        metadata = self._load_metadata()
        archive_name = metadata.get('archive', '') if metadata else ''

        archive_path = None
        if archive_name:
            candidate = self.python_archive_dir / archive_name
            if candidate.exists():
                archive_path = candidate

        if not archive_path:
            # 按版本号搜索
            for archive in self.python_archive_dir.glob('python-*.tar.gz'):
                if version_short in archive.name:
                    archive_path = archive
                    break

        if not archive_path:
            # 使用第一个找到的归档
            archives = list(self.python_archive_dir.glob('python-*.tar.gz'))
            if archives:
                archive_path = archives[0]

        if not archive_path:
            logger.error(f"未找到 Python {version_short} 的预编译归档")
            return False

        logger.info(f"解压可移植 Python: {archive_path.name} -> {target_dir}")

        try:
            # 创建临时目录用于解压
            tmp_extract = tempfile.mkdtemp(prefix='portable_python_')
            try:
                with tarfile.open(str(archive_path), 'r:gz') as tar:
                    tar.extractall(path=tmp_extract)

                # python-build-standalone 归档结构:
                #   cpython-3.14.4+20260414-x86_64-unknown-linux-gnu-install_only/python/
                # 需要找到内层的 python/ 目录
                extracted_root = Path(tmp_extract)
                python_src = None

                # 查找 python 子目录
                for candidate in extracted_root.iterdir():
                    sub_python = candidate / 'python'
                    if candidate.is_dir() and sub_python.is_dir():
                        python_src = sub_python
                        break

                if not python_src:
                    # 可能归档直接包含 bin/lib 等目录
                    for candidate in extracted_root.iterdir():
                        if candidate.is_dir() and (candidate / 'bin').exists():
                            python_src = candidate
                            break

                if not python_src:
                    logger.error(f"归档结构不符合预期: {archive_path.name}")
                    return False

                # 移动到目标目录
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                shutil.move(str(python_src), str(target_dir))

                # 修复可执行权限
                bin_dir = target_dir / 'bin'
                if bin_dir.exists():
                    for f in bin_dir.iterdir():
                        try:
                            if f.is_file():
                                os.chmod(f, 0o755)
                        except Exception:
                            pass

                # 修复符号链接（绝对路径 -> 相对路径）
                if bin_dir.exists():
                    for item in bin_dir.iterdir():
                        if item.is_symlink():
                            try:
                                link_target = os.readlink(str(item))
                                if os.path.isabs(link_target):
                                    # 尝试在同目录找目标
                                    target_name = Path(link_target).name
                                    candidate = bin_dir / target_name
                                    if candidate.exists():
                                        item.unlink()
                                        os.symlink(target_name, str(item))
                            except Exception:
                                pass

                logger.info(f"可移植 Python {version_short} 解压成功: {target_dir}")
                return True

            finally:
                shutil.rmtree(tmp_extract, ignore_errors=True)

        except Exception as e:
            logger.error(f"解压可移植 Python 失败: {e}")
            return False


class CppToolchain:
    """
    沙箱环境 C++ 编译工具链管理器

    解决沙箱无 root 权限无法安装 gcc/g++/cmake 的问题：
    - 优先使用预编译嵌入产物（PrebuiltManager）
    - 自动检测现有编译器
    - 下载预编译 wheel（manylinux，跳过 C++ 编译）
    - 安装便携版 gcc/g++/cmake 到用户空间（从 conda-forge）
    - 预置常见 C++ 共享库 (.so)，设好 LD_LIBRARY_PATH
    - 自动配置 CC / CXX / CMAKE_PREFIX_PATH 等环境变量

    使用示例:
    >>> toolchain = CppToolchain()
    >>> # 安装需要 C++ 编译的 Python 包（优先下载预编译 wheel）
    >>> toolchain.install_cpp_package("hnswlib")
    >>> # 安装便携编译器到用户空间
    >>> toolchain.setup_portable_compiler()
    >>> # 检查编译环境
    >>> info = toolchain.check_environment()
    """

    # conda-forge 便携编译器包（Linux x86_64）
    # 注意: gcc_linux-64 / gxx_linux-64 / binutils_linux-64 是元包(metapackage)，
    #       只含激活脚本，不含实际编译器。必须用 _impl 版本。
    CONDA_COMPILER_PACKAGES = {
        'gcc_impl_linux-64': {
            'url': 'https://conda.anaconda.org/conda-forge/linux-64/',
            'bin_subpath': 'bin',
            'executables': ['x86_64-conda-linux-gnu-gcc'],
            'symlinks': {'gcc': 'x86_64-conda-linux-gnu-gcc',
                         'cc': 'x86_64-conda-linux-gnu-gcc'},
            'provides_libs': True,  # 提供 libgcc 等
        },
        'gxx_impl_linux-64': {
            'url': 'https://conda.anaconda.org/conda-forge/linux-64/',
            'bin_subpath': 'bin',
            'executables': ['x86_64-conda-linux-gnu-g++'],
            'symlinks': {'g++': 'x86_64-conda-linux-gnu-g++',
                         'c++': 'x86_64-conda-linux-gnu-g++'},
            'provides_libs': True,  # 提供 libstdc++ 等
        },
        'gfortran_impl_linux-64': {
            'url': 'https://conda.anaconda.org/conda-forge/linux-64/',
            'bin_subpath': 'bin',
            'executables': ['x86_64-conda-linux-gnu-gfortran'],
            'symlinks': {'gfortran': 'x86_64-conda-linux-gnu-gfortran'},
            'provides_libs': True,  # 提供 libgfortran 等
        },
        'binutils_impl_linux-64': {
            'url': 'https://conda.anaconda.org/conda-forge/linux-64/',
            'bin_subpath': 'bin',
            'executables': ['x86_64-conda-linux-gnu-ld', 'x86_64-conda-linux-gnu-as',
                            'x86_64-conda-linux-gnu-nm', 'x86_64-conda-linux-gnu-objcopy'],
            'symlinks': {'ld': 'x86_64-conda-linux-gnu-ld',
                         'as': 'x86_64-conda-linux-gnu-as'},
            'provides_libs': False,
        },
        'cmake': {
            'url': 'https://conda.anaconda.org/conda-forge/linux-64/',
            'bin_subpath': 'bin',
            'executables': ['cmake'],
            'symlinks': {},
            'provides_libs': False,
        },
        'make': {
            'url': 'https://conda.anaconda.org/conda-forge/linux-64/',
            'bin_subpath': 'bin',
            'executables': ['make'],
            'symlinks': {},
            'provides_libs': False,
        },
        'meson': {
            'url': 'https://conda.anaconda.org/conda-forge/linux-64/',
            'bin_subpath': 'bin',
            'executables': ['meson'],
            'symlinks': {},
            'provides_libs': False,
        },
        'ninja': {
            'url': 'https://conda.anaconda.org/conda-forge/linux-64/',
            'bin_subpath': 'bin',
            'executables': ['ninja'],
            'symlinks': {},
            'provides_libs': False,
        },
    }

    # gcc 工具链完整依赖（安装顺序）
    GCC_TOOLCHAIN_DEPS = [
        'binutils_impl_linux-64',  # 链接器、汇编器（必须先装）
        'gcc_impl_linux-64',       # C 编译器
        'gxx_impl_linux-64',       # C++ 编译器
    ]

    # 额外需要的运行时库（含 C++ 头文件，编译 C++ 必须有）
    TOOLCHAIN_RUNTIME_DEPS = [
        'libstdcxx-devel_linux-64',  # C++ 标准库头文件
        'sysroot_linux-64',          # glibc 头文件（features.h 等）
    ]

    # 常见 C++ 编译依赖的预编译 wheel / binary 包映射
    # key: 包名, value: 替代方案列表（按优先级）
    CPP_PACKAGE_FALLBACKS = {
        'hnswlib': [
            # 1. 尝试 manylinux 预编译 wheel
            {'strategy': 'manylinux_wheel'},
            # 2. 尝试 conda 预编译包
            {'strategy': 'conda_install', 'channel': 'conda-forge'},
            # 3. 用便携编译器从源码编译
            {'strategy': 'compile_with_portable_gcc'},
        ],
        'faiss-cpu': [
            {'strategy': 'manylinux_wheel'},
            {'strategy': 'conda_install', 'channel': 'conda-forge'},
        ],
        'pysqlite3': [
            {'strategy': 'manylinux_wheel'},
            {'strategy': 'pip_package', 'package': 'pysqlite3-binary'},
        ],
        'lxml': [
            {'strategy': 'manylinux_wheel'},
        ],
        'psycopg2': [
            {'strategy': 'manylinux_wheel'},
            {'strategy': 'pip_package', 'package': 'psycopg2-binary'},
        ],
        'pyarrow': [
            {'strategy': 'manylinux_wheel'},
        ],
    }

    # 预置共享库映射（常见 C++ 依赖）
    # key: .so 文件名, value: 系统包名 + conda-forge 包名
    PREBUILT_LIBS = {
        'libstdc++.so': {
            'system_pkg': 'libstdc++6',
            'conda_pkg': 'libstdcxx-ng',
        },
        'libgcc_s.so': {
            'system_pkg': 'libgcc1',
            'conda_pkg': 'libgcc-ng',
        },
        'libgomp.so': {
            'system_pkg': 'libgomp1',
            'conda_pkg': 'libgcc-ng',  # 包含在 libgcc-ng 中
        },
        'libopenblas.so': {
            'system_pkg': 'libopenblas0',
            'conda_pkg': 'libopenblas',
        },
        'liblapack.so': {
            'system_pkg': 'liblapack3',
            'conda_pkg': 'liblapack',
        },
        'libsqlite3.so': {
            'system_pkg': 'libsqlite3-0',
            'conda_pkg': 'sqlite',
        },
        'libssl.so': {
            'system_pkg': 'libssl1.1',
            'conda_pkg': 'openssl',
        },
        'libcrypto.so': {
            'system_pkg': 'libcrypto1.1',
            'conda_pkg': 'openssl',
        },
        'libz.so': {
            'system_pkg': 'zlib1g',
            'conda_pkg': 'zlib',
        },
        'libffi.so': {
            'system_pkg': 'libffi7',
            'conda_pkg': 'libffi',
        },
    }

    def __init__(self, sandbox_dir: Optional[Path] = None):
        self.sandbox_dir = sandbox_dir or Path.home() / '.sandbox'
        self.toolchain_dir = self.sandbox_dir / 'cpp_toolchain'
        self.lib_dir = self.toolchain_dir / 'lib'
        self.bin_dir = self.toolchain_dir / 'bin'
        self.include_dir = self.toolchain_dir / 'include'
        self.wheel_cache = self.sandbox_dir / 'wheel_cache'

        # 预编译管理器（优先使用嵌入的预编译产物）
        self.prebuilt = PrebuiltManager()

        # 编译器检测结果缓存
        self._compiler_cache: Dict[str, Optional[str]] = {}

        # 确保目录存在
        for d in [self.toolchain_dir, self.lib_dir, self.bin_dir,
                   self.include_dir, self.wheel_cache]:
            d.mkdir(parents=True, exist_ok=True)

    # ==================== 编译器检测 ====================

    def find_compiler(self, name: str) -> Optional[str]:
        """
        查找编译器路径

        优先级: 用户空间工具链 > 系统编译器
        """
        if name in self._compiler_cache:
            return self._compiler_cache[name]

        # 1. 先查用户空间工具链
        user_bin = self.bin_dir / name
        if user_bin.exists() and os.access(user_bin, os.X_OK):
            self._compiler_cache[name] = str(user_bin)
            return str(user_bin)

        # 2. 再查系统 PATH
        system_path = shutil.which(name)
        if system_path:
            self._compiler_cache[name] = system_path
            return system_path

        self._compiler_cache[name] = None
        return None

    @property
    def has_gcc(self) -> bool:
        return self.find_compiler('gcc') is not None or self.find_compiler('cc') is not None

    @property
    def has_gxx(self) -> bool:
        return self.find_compiler('g++') is not None or self.find_compiler('c++') is not None

    @property
    def has_cmake(self) -> bool:
        return self.find_compiler('cmake') is not None

    @property
    def has_make(self) -> bool:
        return self.find_compiler('make') is not None

    @property
    def has_meson(self) -> bool:
        """检测 meson 构建系统是否可用"""
        return self.find_compiler('meson') is not None

    @property
    def has_ninja(self) -> bool:
        """检测 ninja 构建后端是否可用"""
        return self.find_compiler('ninja') is not None

    @property
    def has_gfortran(self) -> bool:
        """检测 gfortran Fortran 编译器是否可用"""
        return self.find_compiler('gfortran') is not None

    @property
    def has_compiler_toolchain(self) -> bool:
        """是否有完整的 C++ 编译工具链"""
        return self.has_gcc and self.has_gxx and self.has_cmake

    @property
    def has_meson_build_system(self) -> bool:
        """是否有完整的 meson 构建系统（meson + ninja + C/Fortran 编译器）"""
        return self.has_meson and self.has_ninja and self.has_gcc

    def check_environment(self) -> Dict[str, Any]:
        """
        检查 C++ 编译环境

        Returns:
            Dict: 编译环境信息
        """
        result = {
            'gcc': self.find_compiler('gcc'),
            'g++': self.find_compiler('g++'),
            'gfortran': self.find_compiler('gfortran'),
            'cc': self.find_compiler('cc'),
            'c++': self.find_compiler('c++'),
            'cmake': self.find_compiler('cmake'),
            'make': self.find_compiler('make'),
            'meson': self.find_compiler('meson'),
            'ninja': self.find_compiler('ninja'),
            'has_toolchain': self.has_compiler_toolchain,
            'has_meson_build': self.has_meson_build_system,
            'has_mkl': self._check_mkl_available(),
            'toolchain_dir': str(self.toolchain_dir),
            'lib_dir': str(self.lib_dir),
            'bin_dir': str(self.bin_dir),
            'prebuilt_libs': self._list_prebuilt_libs(),
            'ld_library_path': os.environ.get('LD_LIBRARY_PATH', ''),
            'platform': platform.machine(),
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}",
        }

        # 检查 gcc 版本
        gcc_path = self.find_compiler('gcc')
        if gcc_path:
            try:
                ver = subprocess.run(
                    [gcc_path, '--version'],
                    capture_output=True, text=True, timeout=5,
                )
                result['gcc_version'] = ver.stdout.split('\n')[0] if ver.stdout else 'unknown'
            except Exception:
                result['gcc_version'] = 'error'

        return result

    def _check_mkl_available(self) -> bool:
        """检查 MKL 运行时是否可用"""
        # 检查预编译 MKL
        if self.prebuilt and self.prebuilt.has_mkl_runtime():
            return True
        # 检查已安装的 MKL
        mkl_lib = Path.home() / '.sandbox' / 'mkl' / 'lib'
        if mkl_lib.exists() and list(mkl_lib.glob('libmkl_rt.so*')):
            return True
        # 检查系统 MKL
        return shutil.which('mkl_rt') is not None or \
               bool(list(Path('/usr/local/lib').glob('libmkl_rt.so*')))

    def _list_prebuilt_libs(self) -> List[str]:
        """列出已预置的共享库"""
        libs = []
        if self.lib_dir.exists():
            for f in self.lib_dir.iterdir():
                if f.suffix == '.so' or '.so.' in f.name:
                    libs.append(f.name)
        return sorted(libs)

    # ==================== 环境变量配置 ====================

    def get_build_env(self) -> Dict[str, str]:
        """
        获取编译所需的环境变量

        将用户空间工具链路径注入 PATH, LD_LIBRARY_PATH, CPATH 等，
        使 pip/编译脚本自动找到便携编译器和库。

        Returns:
            Dict: 需要设置的环境变量
        """
        env = os.environ.copy()

        # PATH: 用户空间 bin 优先
        env['PATH'] = f"{self.bin_dir}:{env.get('PATH', '')}"

        # LD_LIBRARY_PATH: 预置库路径
        existing_ld = env.get('LD_LIBRARY_PATH', '')
        if str(self.lib_dir) not in existing_ld:
            env['LD_LIBRARY_PATH'] = f"{self.lib_dir}:{existing_ld}" if existing_ld else str(self.lib_dir)

        # LIBRARY_PATH: 链接时搜索路径
        existing_lib = env.get('LIBRARY_PATH', '')
        if str(self.lib_dir) not in existing_lib:
            env['LIBRARY_PATH'] = f"{self.lib_dir}:{existing_lib}" if existing_lib else str(self.lib_dir)

        # CPATH / C_INCLUDE_PATH / CPLUS_INCLUDE_PATH: 头文件搜索路径
        # 收集所有 include 目录（含 C++ 标准库头文件）
        include_dirs = [str(self.include_dir)]
        for inc_dir in self.toolchain_dir.rglob('include'):
            if inc_dir != self.include_dir and inc_dir.is_dir():
                include_dirs.append(str(inc_dir))

        # 特别添加 C++ 标准库头文件路径
        # conda-forge 的路径结构: x86_64-conda-linux-gnu/include/c++/{version}/
        for cxx_dir in self.toolchain_dir.rglob('include/c++'):
            if cxx_dir.is_dir():
                # c++/ 目录下有版本子目录如 9.5.0
                for ver_dir in cxx_dir.iterdir():
                    if ver_dir.is_dir():
                        include_dirs.append(str(ver_dir))
                        # 版本目录下可能有 x86_64-conda-linux-gnu 子目录
                        for arch_dir in ver_dir.iterdir():
                            if arch_dir.is_dir() and 'linux' in arch_dir.name:
                                include_dirs.append(str(arch_dir))

        # 添加 x86_64-conda-linux-gnu/include（系统头文件如 stdio.h）
        for arch_inc in self.toolchain_dir.rglob('x86_64-conda-linux-gnu/include'):
            if arch_inc.is_dir() and arch_inc not in [Path(d) for d in include_dirs]:
                include_dirs.append(str(arch_inc))

        # sysroot 头文件路径（sysroot_linux-64 解压后的 glibc 头文件）
        # 路径结构: x86_64-conda-linux-gnu/sysroot/usr/include/
        for sysroot in self.toolchain_dir.rglob('sysroot'):
            if sysroot.is_dir():
                usr_include = sysroot / 'usr' / 'include'
                if usr_include.is_dir() and str(usr_include) not in include_dirs:
                    include_dirs.append(str(usr_include))
                # sysroot 本身也作为 --sysroot 参数传递给编译器
                # 这比手动添加 include 更可靠，因为编译器会自动搜索正确路径

        for var in ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH'):
            existing = env.get(var, '')
            for inc_dir in include_dirs:
                if inc_dir not in existing:
                    existing = f"{inc_dir}:{existing}" if existing else inc_dir
            env[var] = existing

        # CC / CXX: 指定编译器
        gcc = self.find_compiler('gcc')
        gxx = self.find_compiler('g++')
        if gcc:
            env.setdefault('CC', gcc)
        if gxx:
            env.setdefault('CXX', gxx)

        # --sysroot: 如果存在 sysroot，设置 CFLAGS/CXXFLAGS/LDFLAGS
        # 这对 conda-forge 工具链至关重要——没有 sysroot，g++ 找不到
        # stdlib.h/features.h 等系统头文件，导致 "Unsupported compiler" 错误
        for sysroot in self.toolchain_dir.rglob('sysroot'):
            if sysroot.is_dir():
                sysroot_str = str(sysroot)
                for var in ('CFLAGS', 'CXXFLAGS', 'LDFLAGS'):
                    existing = env.get(var, '')
                    if f'--sysroot={sysroot_str}' not in existing:
                        env[var] = f"--sysroot={sysroot_str} {existing}".strip()
                env['CONDA_BUILD_SYSROOT'] = sysroot_str
                break  # 只用第一个找到的 sysroot

        # CMAKE_PREFIX_PATH: cmake 搜索路径
        existing_cmake = env.get('CMAKE_PREFIX_PATH', '')
        if str(self.toolchain_dir) not in existing_cmake:
            env['CMAKE_PREFIX_PATH'] = f"{self.toolchain_dir}:{existing_cmake}" if existing_cmake else str(self.toolchain_dir)

        # Meson 构建系统环境变量
        # MESON_BUILD_DIR: 默认构建目录
        env.setdefault('MESON_BUILD_DIR', str(self.sandbox_dir / 'meson_build'))

        # FC: Fortran 编译器（numpy 2.x meson 构建需要）
        gfortran = self.find_compiler('gfortran')
        if gfortran:
            env.setdefault('FC', gfortran)

        # PKG_CONFIG_PATH: 让 meson 能找到用户空间安装的库
        pkg_config_paths = [
            str(self.lib_dir / 'pkgconfig'),
            str(self.toolchain_dir / 'lib' / 'pkgconfig'),
        ]
        # 也搜索所有子目录中的 pkgconfig
        for pc_dir in self.toolchain_dir.rglob('pkgconfig'):
            if pc_dir.is_dir() and str(pc_dir) not in pkg_config_paths:
                pkg_config_paths.append(str(pc_dir))

        existing_pkg = env.get('PKG_CONFIG_PATH', '')
        for pc_path in pkg_config_paths:
            if pc_path not in existing_pkg:
                existing_pkg = f"{pc_path}:{existing_pkg}" if existing_pkg else pc_path
        env['PKG_CONFIG_PATH'] = existing_pkg

        # meson-python 需要: 确保用户空间安装的 meson 和 ninja 在 PATH 中
        user_local_bin = str(Path.home() / '.local' / 'bin')
        if user_local_bin not in env.get('PATH', ''):
            env['PATH'] = f"{user_local_bin}:{env.get('PATH', '')}"

        # MKL 运行时库路径
        mkl_lib = Path.home() / '.sandbox' / 'mkl' / 'lib'
        if mkl_lib.exists():
            mkl_lib_str = str(mkl_lib)
            existing_ld = env.get('LD_LIBRARY_PATH', '')
            if mkl_lib_str not in existing_ld:
                env['LD_LIBRARY_PATH'] = f"{mkl_lib_str}:{existing_ld}" if existing_ld else mkl_lib_str
            existing_lib = env.get('LIBRARY_PATH', '')
            if mkl_lib_str not in existing_lib:
                env['LIBRARY_PATH'] = f"{mkl_lib_str}:{existing_lib}" if existing_lib else mkl_lib_str

        mkl_include = Path.home() / '.sandbox' / 'mkl' / 'include'
        if mkl_include.exists():
            mkl_inc_str = str(mkl_include)
            existing_cpath = env.get('CPATH', '')
            if mkl_inc_str not in existing_cpath:
                env['CPATH'] = f"{mkl_inc_str}:{existing_cpath}" if existing_cpath else mkl_inc_str

        # MKL 线程模型
        env.setdefault('MKL_THREADING_LAYER', 'GNU')

        return env

    def apply_build_env(self):
        """将编译环境变量应用到当前进程"""
        for key, value in self.get_build_env().items():
            os.environ[key] = value

    # ==================== 预编译 wheel 安装 ====================

    def find_manylinux_wheel(
        self,
        package: str,
        version: Optional[str] = None,
        python_version: Optional[str] = None,
        platform_tag: Optional[str] = None,
    ) -> Optional[str]:
        """
        从 PyPI 查找预编译 manylinux wheel 的下载 URL

        manylinux wheel 是预编译的二进制包，无需本地 C++ 编译器。

        Args:
            package: 包名
            version: 版本约束
            python_version: Python 版本 (如 "3.10")，默认当前版本
            platform_tag: 平台标签 (如 "manylinux_2_17_x86_64")，默认自动检测

        Returns:
            Optional[str]: wheel 下载 URL，无匹配则返回 None
        """
        import urllib.request
        import urllib.error

        py_ver = python_version or f"{sys.version_info.major}.{sys.version_info.minor}"
        py_tag = f"cp{py_ver.replace('.', '')}"

        # 确定平台标签
        if platform_tag is None:
            machine = platform.machine()
            if machine == 'x86_64':
                platform_tags = [
                    f'manylinux_2_17_{machine}',
                    f'manylinux2014_{machine}',
                    f'manylinux_2_5_{machine}',
                    f'manylinux1_{machine}',
                ]
            elif machine == 'aarch64':
                platform_tags = [
                    f'manylinux_2_17_{machine}',
                    f'manylinux2014_{machine}',
                ]
            else:
                platform_tags = [f'manylinux_{machine}']
        else:
            platform_tags = [platform_tag]

        # 从 PyPI JSON API 获取包信息
        url = f'https://pypi.org/pypi/{package}/json'
        try:
            req = urllib.request.Request(url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            logger.debug(f"PyPI API 查询 {package} 失败: {e}")
            return None

        # 获取所有版本或指定版本
        if version:
            target_version = version.lstrip('=<>!~')
            releases = data.get('releases', {}).get(target_version, [])
        else:
            releases = data.get('urls', [])

        if not releases and not version:
            # 尝试最新版本的 releases
            latest_ver = data.get('info', {}).get('version', '')
            releases = data.get('releases', {}).get(latest_ver, [])

        # 查找匹配的 manylinux wheel
        for plat in platform_tags:
            for release in releases:
                filename = release.get('filename', '')
                url = release.get('url', '')
                # 检查是否是 wheel 且匹配平台
                if not filename.endswith('.whl'):
                    continue
                # wheel 文件名格式: {name}-{ver}-{pytag}-{abi_tag}-{platform_tag}.whl
                parts = filename.replace('-', '_').split('_')
                # 简单匹配: 文件名包含 py_tag 和 platform_tag
                if py_tag in filename and plat in filename:
                    return url
                # 也匹配 cp3x (如 cp310 兼容 cp311 等)
                if f'cp{sys.version_info.major}' in filename and plat in filename:
                    # 检查 python 版本兼容性
                    for part in parts:
                        if part.startswith('cp3') and int(part[3:]) <= sys.version_info.minor:
                            return url

        logger.debug(f"未找到 {package} 的 manylinux wheel (py={py_tag}, plat={platform_tags})")
        return None

    def download_and_install_wheel(
        self,
        wheel_url: str,
        package_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        下载并安装预编译 wheel

        直接下载 .whl 文件，用 pip install 安装，跳过编译。

        Args:
            wheel_url: wheel 文件下载 URL
            package_name: 包名（用于日志）

        Returns:
            Dict: 安装结果
        """
        import urllib.request

        pkg_label = package_name or wheel_url.split('/')[-1]

        # 下载到缓存
        wheel_filename = wheel_url.split('/')[-1]
        cache_path = self.wheel_cache / wheel_filename

        if not cache_path.exists():
            logger.info(f"下载 wheel: {wheel_filename}")
            try:
                urllib.request.urlretrieve(wheel_url, str(cache_path))
            except Exception as e:
                return {
                    'status': 'failed',
                    'strategy': 'manylinux_wheel',
                    'error': f'下载 wheel 失败: {e}',
                }

        # pip install --user <wheel_file>
        python = sys.executable or 'python3'
        try:
            result = subprocess.run(
                [python, '-m', 'pip', 'install', '--user', str(cache_path)],
                capture_output=True, text=True, timeout=120,
                env=self.get_build_env(),
            )
            if result.returncode == 0:
                return {
                    'status': 'success',
                    'strategy': 'manylinux_wheel',
                    'package': pkg_label,
                    'wheel': wheel_filename,
                    'output': result.stdout,
                }
            return {
                'status': 'failed',
                'strategy': 'manylinux_wheel',
                'error': result.stderr[:300],
                'wheel': wheel_filename,
            }
        except Exception as e:
            return {
                'status': 'failed',
                'strategy': 'manylinux_wheel',
                'error': str(e),
            }

    # ==================== 便携编译器安装 ====================

    def setup_portable_compiler(
        self,
        components: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        安装便携版 C++ 编译工具链到用户空间

        优先级：
        1. 预编译嵌入工具链（prebuilt/toolchain/）
        2. 从 conda-forge 下载（gcc_impl_linux-64 等，非元包）

        从 conda-forge 下载预编译的 gcc/g++/cmake/make，
        无需 root 权限，安装到 ~/.sandbox/cpp_toolchain/

        Args:
            components: 要安装的组件列表，默认 ['gcc', 'cmake', 'make']
                        可选: 'gcc', 'gxx'(g++), 'cmake', 'make'

        Returns:
            Dict: 安装结果
        """
        components = components or ['gcc', 'cmake', 'make']
        results = {}
        errors = []

        # 优先使用预编译嵌入工具链
        if self.prebuilt.has_prebuilt_toolchain():
            logger.info("发现预编译工具链，直接解压安装")
            prebuilt_result = self.prebuilt.setup_prebuilt_toolchain(
                target_dir=self.toolchain_dir
            )
            if prebuilt_result['status'] == 'success':
                self._create_all_compiler_symlinks()
                self._compiler_cache.clear()
                self.apply_build_env()
                # 检查安装了哪些组件
                installed = []
                if self.find_compiler('gcc'):
                    installed.append('gcc')
                if self.find_compiler('g++'):
                    installed.append('gxx')
                if self.find_compiler('gfortran'):
                    installed.append('gfortran')
                if self.find_compiler('cmake'):
                    installed.append('cmake')
                if self.find_compiler('make'):
                    installed.append('make')
                if self.find_compiler('meson'):
                    installed.append('meson')
                if self.find_compiler('ninja'):
                    installed.append('ninja')
                return {
                    'status': 'success',
                    'strategy': 'prebuilt',
                    'components': {c: {'status': 'success'} for c in installed},
                    'errors': [],
                }
            logger.warning(f"预编译工具链安装失败: {prebuilt_result}，回退到 conda-forge 下载")

        # 回退: 从 conda-forge 下载
        for comp in components:
            if comp == 'gcc':
                # 安装 gcc 完整依赖链（避免元包问题）
                for pkg_name in self.GCC_TOOLCHAIN_DEPS:
                    if pkg_name.startswith('gcc_'):
                        res = self._install_conda_package(pkg_name)
                        results.setdefault(comp, {})['gcc_impl'] = res
                        if res['status'] != 'success':
                            errors.append(f"gcc 安装失败({pkg_name}): {res.get('error', 'unknown')}")
                        else:
                            self._create_compiler_symlinks(pkg_name)
                    elif pkg_name.startswith('binutils_'):
                        res = self._install_conda_package(pkg_name)
                        results.setdefault(comp, {})['binutils_impl'] = res
                        if res['status'] != 'success':
                            errors.append(f"binutils 安装失败({pkg_name}): {res.get('error', 'unknown')}")
                        else:
                            self._create_compiler_symlinks(pkg_name)
                self._compiler_cache.clear()

            elif comp == 'gxx':
                res = self._install_conda_package('gxx_impl_linux-64')
                results[comp] = res
                if res['status'] != 'success':
                    errors.append(f"g++ 安装失败: {res.get('error', 'unknown')}")
                else:
                    self._create_compiler_symlinks('gxx_impl_linux-64')
                    self._compiler_cache.clear()

            elif comp == 'cmake':
                res = self._install_conda_package('cmake')
                results[comp] = res
                if res['status'] != 'success':
                    errors.append(f"cmake 安装失败: {res.get('error', 'unknown')}")
                else:
                    self._compiler_cache.clear()

            elif comp == 'make':
                res = self._install_conda_package('make')
                results[comp] = res
                if res['status'] != 'success':
                    errors.append(f"make 安装失败: {res.get('error', 'unknown')}")
                else:
                    self._compiler_cache.clear()

            elif comp == 'gfortran':
                res = self._install_conda_package('gfortran_impl_linux-64')
                results[comp] = res
                if res['status'] != 'success':
                    # gfortran 也可以通过 pip 安装（meson 构建需要 Fortran 编译器）
                    pip_res = self._install_meson_via_pip()
                    results[comp] = {'conda': res, 'pip_fallback': pip_res}
                    if pip_res['status'] != 'success':
                        errors.append(f"gfortran 安装失败: {res.get('error', 'unknown')}")
                else:
                    self._create_compiler_symlinks('gfortran_impl_linux-64')
                    self._compiler_cache.clear()

            elif comp == 'meson':
                # meson 优先通过 pip 安装（纯 Python，更快更可靠）
                res = self._install_meson_via_pip()
                results[comp] = res
                if res['status'] != 'success':
                    # 回退到 conda
                    res = self._install_conda_package('meson')
                    results[comp] = {'pip': results[comp], 'conda': res}
                    if res['status'] != 'success':
                        errors.append(f"meson 安装失败: pip 和 conda 均失败")
                else:
                    self._compiler_cache.clear()

            elif comp == 'ninja':
                # ninja 优先通过 pip 安装
                res = self._install_ninja_via_pip()
                results[comp] = res
                if res['status'] != 'success':
                    # 回退到 conda
                    res = self._install_conda_package('ninja')
                    results[comp] = {'pip': results[comp], 'conda': res}
                    if res['status'] != 'success':
                        errors.append(f"ninja 安装失败: pip 和 conda 均失败")
                else:
                    self._compiler_cache.clear()

        # 链接 conda 解压出的所有库
        self._link_conda_libs()
        # 创建完整的编译器符号链接
        self._create_all_compiler_symlinks()
        # 安装后自动应用环境变量
        if not errors:
            self.apply_build_env()

        return {
            'status': 'success' if not errors else 'partial' if any(
                r.get('status') == 'success' for r in results.values()
                if isinstance(r, dict) and 'status' in r
            ) or any(
                sub.get('status') == 'success'
                for r in results.values() if isinstance(r, dict)
                for sub in r.values() if isinstance(sub, dict)
            ) else 'failed',
            'components': results,
            'errors': errors,
        }

    def _install_meson_via_pip(self) -> Dict[str, Any]:
        """
        通过 pip --user 安装 meson 构建系统

        meson 是纯 Python 包，pip 安装无需编译，是沙箱环境下最可靠的方式。
        同时安装 meson-python（PEP 517 构建后端，numpy 2.x 等包需要）。

        Returns:
            Dict: 安装结果
        """
        python = sys.executable or 'python3'
        try:
            result = subprocess.run(
                [python, '-m', 'pip', 'install', '--user',
                 'meson', 'meson-python', 'ninja'],
                capture_output=True, text=True, timeout=120,
                env=self.get_build_env(),
            )
            if result.returncode == 0:
                # 确保 ~/.local/bin 在 PATH 中
                user_bin = str(Path.home() / '.local' / 'bin')
                current_path = os.environ.get('PATH', '')
                if user_bin not in current_path:
                    os.environ['PATH'] = f"{user_bin}:{current_path}"
                self._compiler_cache.clear()
                return {
                    'status': 'success',
                    'strategy': 'pip_user',
                    'package': 'meson+meson-python+ninja',
                    'output': result.stdout,
                }
            # 尝试 --break-system-packages（Python 3.11+ 外部管理环境）
            if 'externally-managed' in result.stderr:
                result = subprocess.run(
                    [python, '-m', 'pip', 'install', '--user', '--break-system-packages',
                     'meson', 'meson-python', 'ninja'],
                    capture_output=True, text=True, timeout=120,
                    env=self.get_build_env(),
                )
                if result.returncode == 0:
                    user_bin = str(Path.home() / '.local' / 'bin')
                    current_path = os.environ.get('PATH', '')
                    if user_bin not in current_path:
                        os.environ['PATH'] = f"{user_bin}:{current_path}"
                    self._compiler_cache.clear()
                    return {
                        'status': 'success',
                        'strategy': 'pip_user_break_system',
                        'package': 'meson+meson-python+ninja',
                        'output': result.stdout,
                    }
            return {
                'status': 'failed',
                'strategy': 'pip_user',
                'error': result.stderr[:300],
            }
        except Exception as e:
            return {
                'status': 'failed',
                'strategy': 'pip_user',
                'error': str(e),
            }

    def _install_ninja_via_pip(self) -> Dict[str, Any]:
        """
        通过 pip --user 安装 ninja 构建后端

        ninja 在 PyPI 上有 manylinux 预编译 wheel，pip 安装很快。

        Returns:
            Dict: 安装结果
        """
        python = sys.executable or 'python3'
        try:
            result = subprocess.run(
                [python, '-m', 'pip', 'install', '--user', 'ninja'],
                capture_output=True, text=True, timeout=60,
                env=self.get_build_env(),
            )
            if result.returncode == 0:
                user_bin = str(Path.home() / '.local' / 'bin')
                current_path = os.environ.get('PATH', '')
                if user_bin not in current_path:
                    os.environ['PATH'] = f"{user_bin}:{current_path}"
                self._compiler_cache.clear()
                return {
                    'status': 'success',
                    'strategy': 'pip_user',
                    'package': 'ninja',
                    'output': result.stdout,
                }
            if 'externally-managed' in result.stderr:
                result = subprocess.run(
                    [python, '-m', 'pip', 'install', '--user', '--break-system-packages', 'ninja'],
                    capture_output=True, text=True, timeout=60,
                    env=self.get_build_env(),
                )
                if result.returncode == 0:
                    user_bin = str(Path.home() / '.local' / 'bin')
                    current_path = os.environ.get('PATH', '')
                    if user_bin not in current_path:
                        os.environ['PATH'] = f"{user_bin}:{current_path}"
                    self._compiler_cache.clear()
                    return {
                        'status': 'success',
                        'strategy': 'pip_user_break_system',
                        'package': 'ninja',
                        'output': result.stdout,
                    }
            return {
                'status': 'failed',
                'strategy': 'pip_user',
                'error': result.stderr[:300],
            }
        except Exception as e:
            return {
                'status': 'failed',
                'strategy': 'pip_user',
                'error': str(e),
            }

    def _install_conda_package(self, package_name: str) -> Dict[str, Any]:
        """
        从 conda-forge 下载并解压包到用户空间

        不需要 conda，直接下载 .tar.bz2 并解压。
        """
        import urllib.request

        pkg_info = self.CONDA_COMPILER_PACKAGES.get(package_name)
        if not pkg_info:
            return {'status': 'failed', 'error': f'未知包: {package_name}'}

        # 构建下载 URL（需要查询最新版本）
        # 使用 conda-forge repodata 获取最新版本
        repodata_url = f"{pkg_info['url']}repodata.json"
        try:
            req = urllib.request.Request(repodata_url, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                repodata = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            return {
                'status': 'failed',
                'error': f'获取 conda-forge repodata 失败: {e}',
                'hint': '检查网络连接，或手动下载 conda 包到 ~/.sandbox/cpp_toolchain/',
            }

        # 查找最新版本
        packages_meta = repodata.get('packages', {})
        best_pkg = None
        best_ver = ''

        for pkg_filename, pkg_meta in packages_meta.items():
            if not pkg_filename.startswith(package_name):
                continue
            # 只选 linux-64 平台
            if pkg_meta.get('subdir') != 'linux-64':
                continue
            # 选最新版本
            ver = pkg_meta.get('version', '0')
            if ver > best_ver:
                best_ver = ver
                best_pkg = (pkg_filename, pkg_meta)

        if not best_pkg:
            # 也检查 packages.conda 格式
            packages_conda = repodata.get('packages.conda', {})
            for pkg_filename, pkg_meta in packages_conda.items():
                if not pkg_filename.startswith(package_name):
                    continue
                if pkg_meta.get('subdir') != 'linux-64':
                    continue
                ver = pkg_meta.get('version', '0')
                if ver > best_ver:
                    best_ver = ver
                    # .conda 格式需要特殊处理，先用 .tar.bz2
                    raw_filename = pkg_meta.get('fn', pkg_filename)
                    best_pkg = (raw_filename, pkg_meta)

        if not best_pkg:
            return {
                'status': 'failed',
                'error': f'在 conda-forge 未找到 {package_name} (linux-64)',
            }

        pkg_filename, pkg_meta = best_pkg
        download_url = f"{pkg_info['url']}{pkg_filename}"

        # 下载
        cache_path = self.sandbox_dir / 'conda_cache'
        cache_path.mkdir(exist_ok=True)
        local_path = cache_path / pkg_filename

        if not local_path.exists():
            logger.info(f"下载 conda 包: {pkg_filename}")
            try:
                urllib.request.urlretrieve(download_url, str(local_path))
            except Exception as e:
                return {
                    'status': 'failed',
                    'error': f'下载 {pkg_filename} 失败: {e}',
                }

        # 解压到工具链目录
        try:
            if pkg_filename.endswith('.tar.bz2'):
                import tarfile
                with tarfile.open(str(local_path), 'r:bz2') as tar:
                    tar.extractall(path=str(self.toolchain_dir))
            elif pkg_filename.endswith('.conda'):
                # .conda 格式实际上是 zip 包含 tar.zst
                # 需要先用 zipfile 解压，再用 zstd 解压
                return self._install_conda_package_zst(local_path, pkg_filename)
            else:
                return {
                    'status': 'failed',
                    'error': f'不支持的包格式: {pkg_filename}',
                }
        except Exception as e:
            return {
                'status': 'failed',
                'error': f'解压 {pkg_filename} 失败: {e}',
                'hint': '可能需要安装 bzip2: apt install bzip2 或下载 .conda 格式',
            }

        # 设置可执行权限
        self._fix_exec_permissions()

        return {
            'status': 'success',
            'package': package_name,
            'version': best_ver,
            'filename': pkg_filename,
        }

    def _install_conda_package_zst(self, local_path: Path, pkg_filename: str) -> Dict[str, Any]:
        """安装 .conda 格式的包（zstd 压缩）"""
        try:
            import zipfile
            import tarfile

            # .conda 是 zip 文件，包含 pkg-tar.zst 和 info-tar.zst
            extract_tmp = self.sandbox_dir / 'conda_extract_tmp'
            extract_tmp.mkdir(exist_ok=True)

            with zipfile.ZipFile(str(local_path), 'r') as zf:
                zf.extractall(str(extract_tmp))

            # 查找 .tar.zst 文件
            for extracted in extract_tmp.rglob('*.tar.zst'):
                # 尝试用 tarfile 直接解压（Python 3.12+ 支持 zstd 过滤器）
                try:
                    with tarfile.open(str(extracted), 'r:zst') as tar:
                        tar.extractall(path=str(self.toolchain_dir))
                except Exception:
                    # 回退：用 zstd 命令行工具
                    zstd_path = shutil.which('zstd') or shutil.which('zstdmt')
                    if zstd_path:
                        import tempfile
                        tar_path = tempfile.mktemp(suffix='.tar')
                        subprocess.run(
                            [zstd_path, '-d', str(extracted), '-o', tar_path],
                            check=True, timeout=60,
                        )
                        with tarfile.open(tar_path, 'r:') as tar:
                            tar.extractall(path=str(self.toolchain_dir))
                        os.unlink(tar_path)
                    else:
                        return {
                            'status': 'failed',
                            'error': '需要 zstd 工具来解压 .conda 包',
                            'hint': 'pip install zstandard 或安装 zstd 系统包',
                        }

            # 清理临时文件
            shutil.rmtree(extract_tmp, ignore_errors=True)
            self._fix_exec_permissions()

            return {'status': 'success', 'package': pkg_filename}
        except Exception as e:
            return {
                'status': 'failed',
                'error': f'解压 .conda 包失败: {e}',
            }

    def _fix_exec_permissions(self):
        """修复 bin 目录下文件的可执行权限"""
        if self.bin_dir.exists():
            for f in self.bin_dir.iterdir():
                try:
                    if not os.access(f, os.X_OK):
                        os.chmod(f, 0o755)
                except Exception:
                    pass
        # 也修复嵌套的 bin 目录（conda 包结构）
        for bindir in self.toolchain_dir.rglob('bin'):
            if bindir.is_dir():
                for f in bindir.iterdir():
                    try:
                        if not os.access(f, os.X_OK):
                            os.chmod(f, 0o755)
                    except Exception:
                        pass

    def _create_compiler_symlinks(self, package_name: str):
        """创建编译器符号链接（gcc → x86_64-conda-linux-gnu-gcc 等）"""
        pkg_info = self.CONDA_COMPILER_PACKAGES.get(package_name, {})
        symlinks = pkg_info.get('symlinks', {})

        for link_name, target_name in symlinks.items():
            # 在 bin 目录下找 target
            target_path = None
            for bindir in self.toolchain_dir.rglob('bin'):
                candidate = bindir / target_name
                if candidate.exists():
                    target_path = candidate
                    break

            if target_path:
                link_path = self.bin_dir / link_name
                try:
                    if link_path.exists() or link_path.is_symlink():
                        link_path.unlink()
                    os.symlink(str(target_path), str(link_path))
                except Exception as e:
                    logger.debug(f"创建符号链接 {link_name} -> {target_name} 失败: {e}")

        # 也把 conda 解压出来的 bin 下的所有可执行文件链接到我们的 bin
        for bindir in self.toolchain_dir.rglob('bin'):
            if bindir == self.bin_dir:
                continue
            for exe in bindir.iterdir():
                if exe.is_file() and os.access(exe, os.X_OK):
                    link_path = self.bin_dir / exe.name
                    if not link_path.exists():
                        try:
                            os.symlink(str(exe), str(link_path))
                        except Exception:
                            pass

    def _create_all_compiler_symlinks(self):
        """为所有已安装的 conda 编译器创建统一的符号链接到 bin_dir"""
        # 收集所有 conda 解压出来的 bin 目录中的可执行文件
        for bindir in self.toolchain_dir.rglob('bin'):
            if bindir == self.bin_dir:
                continue
            for exe in bindir.iterdir():
                if exe.is_file() and os.access(exe, os.X_OK):
                    link_path = self.bin_dir / exe.name
                    if not link_path.exists():
                        try:
                            os.symlink(str(exe), str(link_path))
                        except Exception:
                            pass

        # 创建常用短名符号链接 (gcc → x86_64-conda-linux-gnu-gcc 等)
        short_links = {
            'gcc': 'x86_64-conda-linux-gnu-gcc',
            'g++': 'x86_64-conda-linux-gnu-g++',
            'gfortran': 'x86_64-conda-linux-gnu-gfortran',
            'cc': 'x86_64-conda-linux-gnu-gcc',
            'c++': 'x86_64-conda-linux-gnu-g++',
            'ld': 'x86_64-conda-linux-gnu-ld',
            'as': 'x86_64-conda-linux-gnu-as',
            'nm': 'x86_64-conda-linux-gnu-nm',
            'objcopy': 'x86_64-conda-linux-gnu-objcopy',
            'ar': 'x86_64-conda-linux-gnu-ar',
            'ranlib': 'x86_64-conda-linux-gnu-ranlib',
            'strip': 'x86_64-conda-linux-gnu-strip',
        }
        for link_name, target_name in short_links.items():
            # 先在已链接的 bin_dir 中查找
            target_path = self.bin_dir / target_name
            if not target_path.exists():
                # 递归搜索
                for bindir in self.toolchain_dir.rglob('bin'):
                    candidate = bindir / target_name
                    if candidate.exists():
                        target_path = candidate
                        break

            if target_path.exists():
                link_path = self.bin_dir / link_name
                try:
                    if link_path.exists() or link_path.is_symlink():
                        link_path.unlink()
                    os.symlink(str(target_path), str(link_path))
                except Exception:
                    pass

    # ==================== 预置共享库 ====================

    def install_prebuilt_lib(self, lib_name: str) -> Dict[str, Any]:
        """
        安装预编译共享库到用户空间

        从 conda-forge 下载 .so 文件，设置好 LD_LIBRARY_PATH，
        使得编译和运行时都能找到这些库。

        Args:
            lib_name: 库名 (如 "libstdc++", "libgcc_s", "libopenblas")

        Returns:
            Dict: 安装结果
        """
        lib_info = self.PREBUILT_LIBS.get(lib_name)
        if not lib_info:
            return {
                'status': 'failed',
                'error': f'未知库: {lib_name}，可选: {list(self.PREBUILT_LIBS.keys())}',
            }

        # 先检查系统是否已有
        existing = self._find_system_lib(lib_name)
        if existing:
            # 建立软链接到用户空间
            link_path = self.lib_dir / lib_name
            if not link_path.exists():
                try:
                    os.symlink(existing, str(link_path))
                except Exception:
                    pass
            return {
                'status': 'success',
                'strategy': 'system_symlink',
                'lib': lib_name,
                'source': existing,
            }

        # 从 conda-forge 下载
        conda_pkg = lib_info.get('conda_pkg')
        if conda_pkg:
            result = self._install_conda_package(conda_pkg)
            if result['status'] == 'success':
                # 从 conda 包中找 .so 并链接
                self._link_conda_libs()
                self.apply_build_env()
                return {
                    'status': 'success',
                    'strategy': 'conda_install',
                    'lib': lib_name,
                    'conda_package': conda_pkg,
                }
            return {
                'status': 'failed',
                'lib': lib_name,
                'error': f'conda 安装 {conda_pkg} 失败: {result.get("error", "")}',
            }

        return {
            'status': 'unavailable',
            'lib': lib_name,
            'message': f'库 {lib_name} 无预编译来源，需要 sudo 安装 {lib_info.get("system_pkg", "")}',
        }

    def _find_system_lib(self, lib_name: str) -> Optional[str]:
        """在系统目录中查找共享库"""
        # 常见系统库路径
        search_dirs = [
            '/usr/lib', '/usr/lib/x86_64-linux-gnu', '/usr/lib64',
            '/usr/lib/aarch64-linux-gnu', '/lib', '/lib/x86_64-linux-gnu',
            '/lib64', '/usr/local/lib',
        ]
        # 也搜索 ldconfig 缓存
        try:
            ld_result = subprocess.run(
                ['ldconfig', '-p'],
                capture_output=True, text=True, timeout=5,
            )
            if ld_result.returncode == 0:
                for line in ld_result.stdout.split('\n'):
                    if lib_name in line:
                        parts = line.strip().split(' => ')
                        if len(parts) == 2:
                            path = parts[1].strip()
                            if os.path.exists(path):
                                return path
        except Exception:
            pass

        # 直接搜索目录
        for search_dir in search_dirs:
            d = Path(search_dir)
            if not d.exists():
                continue
            for f in d.iterdir():
                if f.name.startswith(lib_name) and ('.so' in f.name):
                    return str(f)
            # 也搜索子目录
            for f in d.rglob(f'{lib_name}*'):
                if '.so' in f.name and f.is_file():
                    return str(f)

        return None

    def _link_conda_libs(self):
        """将 conda 包中的 .so 链接到用户 lib 目录"""
        for lib_dir in self.toolchain_dir.rglob('lib'):
            if lib_dir == self.lib_dir:
                continue
            for so_file in lib_dir.iterdir():
                if so_file.suffix == '.so' or '.so.' in so_file.name:
                    link_path = self.lib_dir / so_file.name
                    if not link_path.exists():
                        try:
                            os.symlink(str(so_file), str(link_path))
                        except Exception:
                            pass

        # 也链接 include
        for inc_dir in self.toolchain_dir.rglob('include'):
            if inc_dir == self.include_dir:
                continue
            for header in inc_dir.rglob('*'):
                if header.is_file():
                    rel = header.relative_to(inc_dir)
                    dst = self.include_dir / rel
                    if not dst.exists():
                        try:
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            os.symlink(str(header), str(dst))
                        except Exception:
                            pass

    def install_all_common_libs(self) -> Dict[str, Any]:
        """安装所有常见预编译库"""
        results = {}
        for lib_name in self.PREBUILT_LIBS:
            results[lib_name] = self.install_prebuilt_lib(lib_name)
        self.apply_build_env()
        return results

    # ==================== C++ 包安装入口 ====================

    def install_cpp_package(
        self,
        package: str,
        version: Optional[str] = None,
        fallback: Optional[str] = None,
        pure_python_fallback: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        安装需要 C++ 编译的 Python 包

        自动降级策略：
        0. 预编译嵌入产物（PrebuiltManager，优先级最高）
        1. pip install（可能系统有编译器或包有 manylinux wheel）
        2. 从 PyPI 下载 manylinux 预编译 wheel（跳过编译）
        3. 安装便携编译器后从源码编译
        4. 安装 conda 预编译包
        5. 安装指定的 fallback 包
        6. 安装纯 Python 替代

        Args:
            package: 包名（如 "hnswlib"）
            version: 版本约束
            fallback: 备选二进制包名
            pure_python_fallback: 纯 Python 替代包名

        Returns:
            Dict: 安装结果
        """
        python = sys.executable or 'python3'
        pkg_spec = f"{package}{version}" if version else package
        errors = []

        # 策略0: 预编译嵌入产物（最高优先级）
        if self.prebuilt.has_prebuilt_extension(package):
            logger.info(f"[策略0] 从预编译产物加载 {package}")
            mod = self.prebuilt.load_prebuilt_extension(package)
            if mod is not None:
                return {
                    'status': 'success',
                    'package': package,
                    'strategy': 'prebuilt_extension',
                    'message': f'从嵌入的预编译产物加载 {package}',
                }
            errors.append(f"预编译产物加载 {package} 失败（可能版本/平台不兼容）")

        # 策略0b: 预缓存 wheel
        prebuilt_wheel_result = self.prebuilt.install_prebuilt_wheel(package)
        if prebuilt_wheel_result.get('status') == 'success':
            return prebuilt_wheel_result

        # 策略1: pip install --user（可能系统有编译器，或包有预编译 wheel）
        build_env = self.get_build_env()
        logger.info(f"[策略1] pip install --user {pkg_spec}")
        result = subprocess.run(
            [python, '-m', 'pip', 'install', '--user', pkg_spec],
            capture_output=True, text=True, timeout=300,
            env=build_env,
        )
        if result.returncode == 0:
            return {
                'status': 'success',
                'package': package,
                'strategy': 'pip_user',
                'output': result.stdout,
            }
        errors.append(f"pip install 失败: {result.stderr[:200]}")

        # 策略2: 查找并下载 manylinux 预编译 wheel
        logger.info(f"[策略2] 查找 {package} 的 manylinux wheel")
        wheel_url = self.find_manylinux_wheel(package, version=version)
        if wheel_url:
            wheel_result = self.download_and_install_wheel(wheel_url, package_name=package)
            if wheel_result['status'] == 'success':
                return wheel_result
            errors.append(f"manylinux wheel 安装失败: {wheel_result.get('error', '')}")
        else:
            errors.append(f"未找到 {package} 的 manylinux wheel")

        # 策略3: 安装便携编译器 + 从源码编译
        if not self.has_compiler_toolchain:
            logger.info(f"[策略3] 安装便携编译器后从源码编译")
            compiler_result = self.setup_portable_compiler()
            if compiler_result['status'] != 'failed':
                # 重新尝试 pip install（现在有编译器了）
                build_env = self.get_build_env()
                result = subprocess.run(
                    [python, '-m', 'pip', 'install', '--user', '--no-binary', package, pkg_spec],
                    capture_output=True, text=True, timeout=600,
                    env=build_env,
                )
                if result.returncode == 0:
                    return {
                        'status': 'success',
                        'package': package,
                        'strategy': 'portable_compiler_build',
                        'output': result.stdout,
                    }
                errors.append(f"便携编译器编译失败: {result.stderr[:200]}")
            else:
                errors.append(f"便携编译器安装失败: {compiler_result.get('errors', [])}")

        # 策略4: 尝试 conda 安装
        logger.info(f"[策略4] 尝试 conda install {package}")
        conda_path = shutil.which('conda')
        if conda_path:
            conda_result = subprocess.run(
                [conda_path, 'install', '-y', '-c', 'conda-forge', package],
                capture_output=True, text=True, timeout=300,
            )
            if conda_result.returncode == 0:
                return {
                    'status': 'success',
                    'package': package,
                    'strategy': 'conda_install',
                    'output': conda_result.stdout,
                }
            errors.append(f"conda install 失败: {conda_result.stderr[:200]}")

        # 策略5: 指定的 fallback 包
        if fallback:
            logger.info(f"[策略5] 安装 fallback 包: {fallback}")
            result = subprocess.run(
                [python, '-m', 'pip', 'install', '--user', fallback],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                return {
                    'status': 'success',
                    'package': fallback,
                    'original_package': package,
                    'strategy': 'fallback_binary',
                    'output': result.stdout,
                }
            errors.append(f"fallback {fallback} 安装失败: {result.stderr[:200]}")

        # 策略6: 纯 Python 替代
        if pure_python_fallback:
            logger.info(f"[策略6] 安装纯 Python 替代: {pure_python_fallback}")
            result = subprocess.run(
                [python, '-m', 'pip', 'install', '--user', pure_python_fallback],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                return {
                    'status': 'success',
                    'package': pure_python_fallback,
                    'original_package': package,
                    'strategy': 'pure_python_fallback',
                    'output': result.stdout,
                }
            errors.append(f"纯 Python 替代 {pure_python_fallback} 安装失败: {result.stderr[:200]}")

        return {
            'status': 'failed',
            'package': package,
            'errors': errors,
            'suggestion': (
                f"无法安装 {package}，建议：\n"
                f"  1. 运行 scripts/prebuild.py 预编译 {package} 和工具链\n"
                f"  2. 运行 toolchain.setup_portable_compiler() 安装便携编译器后重试\n"
                f"  3. 运行 toolchain.install_prebuilt_lib('libstdc++') 安装缺失的 C++ 库\n"
                f"  4. 使用内置纯 Python 替代方案（如 HNSWIndex）\n"
                f"  5. 在有 root 权限的环境预编译后复制 wheel 文件到 prebuilt/"
            ),
        }

    def setup_meson_build_system(self) -> Dict[str, Any]:
        """
        一键设置 meson 构建系统（numpy 2.x 等使用 meson 构建的包需要）

        安装: meson + ninja + meson-python + gcc/g++ + gfortran + MKL/OpenBLAS

        BLAS 后端优先级:
        1. Intel MKL（预编译嵌入，性能最优）
        2. OpenBLAS（回退方案）

        Returns:
            Dict: 设置结果
        """
        results = {}
        errors = []

        # 1. 安装 meson + ninja（优先 pip）
        meson_result = self._install_meson_via_pip()
        results['meson'] = meson_result
        if meson_result['status'] != 'success':
            errors.append(f"meson 安装失败: {meson_result.get('error', '')}")

        # 2. 安装 C/Fortran 编译器（numpy meson 构建需要 gfortran）
        compiler_components = ['gcc', 'gxx', 'gfortran']
        compiler_result = self.setup_portable_compiler(components=compiler_components)
        results['compiler'] = compiler_result
        if compiler_result.get('status') == 'failed':
            errors.append(f"编译器安装失败: {compiler_result.get('errors', [])}")

        # 3. 安装 BLAS/LAPACK 后端（优先 MKL，回退 OpenBLAS）
        blas_result = None
        if self.prebuilt and self.prebuilt.has_mkl_runtime():
            # 优先安装 MKL 运行时
            mkl_result = self.prebuilt.setup_mkl_runtime()
            results['mkl'] = mkl_result
            if mkl_result['status'] == 'success':
                blas_result = mkl_result
                # MKL 自带 LAPACK
                results['lapack'] = {'status': 'provided_by_mkl', 'message': 'MKL 包含 LAPACK'}
            else:
                logger.warning("MKL 安装失败，回退到 OpenBLAS")

        if not blas_result:
            # 回退: 安装 OpenBLAS
            openblas_result = self.install_prebuilt_lib('libopenblas.so')
            results['openblas'] = openblas_result
            blas_result = openblas_result
            if openblas_result['status'] not in ('success',):
                # 尝试系统安装
                if self.terminal and self.terminal.has_sudo:
                    sys_result = self.terminal.run(
                        ['apt-get', 'install', '-y', 'libopenblas-dev']
                    )
                    results['openblas_system'] = sys_result
                    if sys_result['returncode'] != 0:
                        errors.append("BLAS 安装失败（MKL + OpenBLAS）")

            # 安装 LAPACK（OpenBLAS 不自带完整 LAPACK）
            lapack_result = self.install_prebuilt_lib('liblapack.so')
            results['lapack'] = lapack_result

        # 5. 应用环境变量
        self.apply_build_env()

        # 6. 验证 meson 构建环境
        env_check = self.check_environment()
        results['environment'] = env_check

        meson_ok = self.has_meson_build_system
        results['meson_ready'] = meson_ok

        if not meson_ok:
            errors.append(
                "meson 构建环境不完整，缺少: " +
                ", ".join(k for k in ('meson', 'ninja', 'gcc')
                         if not self.find_compiler(k))
            )

        return {
            'status': 'success' if meson_ok and not errors else 'partial' if meson_ok else 'failed',
            'results': results,
            'errors': errors,
        }

    def setup_full_environment(self) -> Dict[str, Any]:
        """
        一键设置完整的 C++ 编译环境

        安装便携编译器 + 所有常见预编译库 + 配置环境变量

        Returns:
            Dict: 设置结果
        """
        results = {
            'compiler': self.setup_portable_compiler(),
            'libs': self.install_all_common_libs(),
        }

        self.apply_build_env()

        results['environment'] = self.check_environment()

        return results


class SandboxManager:
    """沙箱管理器"""

    def __init__(self, skill_root: Path = None):
        self.skill_root = skill_root or Path(__file__).parent.parent
        self.sandbox_dir = self.skill_root / '.sandbox'
        self.backup_dir = self.skill_root / '.backups'
        self.version_file = self.skill_root / '.version.json'
        self.config_file = self.skill_root / '.sandbox_config.json'

        # 集成 SudoTerminal（无 sudo 环境终端适配）
        self.terminal = SudoTerminal(sandbox_dir=self.sandbox_dir)
        # 集成 CppToolchain（C++ 编译工具链，无 root 时安装便携编译器）
        self.cpp_toolchain = CppToolchain(sandbox_dir=self.sandbox_dir)

        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保目录存在"""
        self.sandbox_dir.mkdir(exist_ok=True)
        self.backup_dir.mkdir(exist_ok=True)

    def get_current_version(self) -> Optional[VersionInfo]:
        """获取当前版本"""
        if not self.version_file.exists():
            return None

        try:
            data = json.loads(self.version_file.read_text())
            # 仅接受 VersionInfo 已知字段，防止注入
            valid_fields = {f.name for f in VersionInfo.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in valid_fields}
            return VersionInfo(**filtered)
        except Exception:
            return None

    def create_backup(self, name: str = None) -> Path:
        """创建备份"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = name or f'backup_{timestamp}'
        backup_path = self.backup_dir / backup_name

        # 复制私有包
        privileged_src = self.skill_root / 'src' / 'privileged'
        if privileged_src.exists():
            backup_privileged = backup_path / 'src' / 'privileged'
            shutil.copytree(privileged_src, backup_privileged)

        # 复制配置
        config_src = self.skill_root / 'config.json'
        if config_src.exists():
            shutil.copy2(config_src, backup_path / 'config.json')

        # 保存版本信息
        version = self.get_current_version()
        if version:
            version_file = backup_path / '.version.json'
            version_file.parent.mkdir(parents=True, exist_ok=True)
            version_file.write_text(json.dumps({
                'version': version.version,
                'commit': version.commit,
                'timestamp': version.timestamp,
                'checksum': version.checksum
            }))

        return backup_path

    def restore_backup(self, backup_name: str) -> bool:
        """恢复备份"""
        # 防止路径穿越：校验 backup_name 不包含路径分隔符
        if '/' in backup_name or '\\' in backup_name or '..' in backup_name:
            print(f"⚠️ 无效的备份名称: {backup_name}")
            return False

        backup_path = self.backup_dir / backup_name
        # 解析后校验仍在 backup_dir 内
        try:
            backup_path.resolve().relative_to(self.backup_dir.resolve())
        except ValueError:
            print(f"⚠️ 备份路径越界: {backup_name}")
            return False

        if not backup_path.exists():
            return False

        try:
            # 恢复私有包
            backup_privileged = backup_path / 'src' / 'privileged'
            if backup_privileged.exists():
                privileged_dst = self.skill_root / 'src' / 'privileged'
                if privileged_dst.exists():
                    shutil.rmtree(privileged_dst)
                shutil.copytree(backup_privileged, privileged_dst)

            # 恢复配置
            backup_config = backup_path / 'config.json'
            if backup_config.exists():
                shutil.copy2(backup_config, self.skill_root / 'config.json')

            return True
        except Exception as e:
            print(f"Restore failed: {e}")
            return False

    def list_backups(self) -> list:
        """列出备份"""
        backups = []
        for backup in self.backup_dir.iterdir():
            if backup.is_dir():
                version_file = backup / '.version.json'
                if version_file.exists():
                    try:
                        data = json.loads(version_file.read_text())
                        backups.append({
                            'name': backup.name,
                            'version': data.get('version'),
                            'timestamp': data.get('timestamp')
                        })
                    except Exception:
                        backups.append({'name': backup.name, 'version': 'unknown', 'timestamp': None})
        return sorted(backups, key=lambda x: x['timestamp'] or '', reverse=True)

    def calculate_checksum(self, directory: Path = None) -> str:
        """计算校验和"""
        target_dir = directory or (self.skill_root / 'src' / 'privileged')
        if not target_dir.exists():
            return ''

        hasher = hashlib.sha256()

        for file in sorted(target_dir.rglob('*.py')):
            hasher.update(file.read_bytes())

        return hasher.hexdigest()[:16]

    def update_version(self, version: str, commit: str = None):
        """更新版本信息"""
        checksum = self.calculate_checksum()
        version_info = VersionInfo(
            version=version,
            commit=commit or '',
            timestamp=datetime.now().isoformat(),
            checksum=checksum
        )

        self.version_file.write_text(json.dumps({
            'version': version_info.version,
            'commit': version_info.commit,
            'timestamp': version_info.timestamp,
            'checksum': version_info.checksum
        }, indent=2))

    def check_for_updates(self, remote_url: str) -> Dict[str, Any]:
        """检查更新"""
        try:
            # 获取远程最新提交
            result = subprocess.run(
                ['git', 'ls-remote', remote_url, 'HEAD'],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                return {'error': 'Failed to fetch remote', 'has_update': False}

            remote_commit = result.stdout.split()[0] if result.stdout else None

            current_version = self.get_current_version()
            current_commit = current_version.commit if current_version else None

            has_update = remote_commit != current_commit

            return {
                'has_update': has_update,
                'current_commit': current_commit,
                'remote_commit': remote_commit,
                'current_version': current_version.version if current_version else 'unknown'
            }
        except Exception as e:
            return {'error': str(e), 'has_update': False}

    @staticmethod
    def resolve_skill_identifier(identifier: str) -> Tuple[str, str, Optional[str]]:
        """
        解析技能标识为 Git URL、分支和子目录

        支持的标识格式：
        - '@org/skill-name' -> https://github.com/org/skill-name (branch: main)
        - 'skill-name' -> https://github.com/openclaw/skills/tree/main/skill-name
        - 'https://github.com/...' -> 直接使用 (branch: main)
        - 'git@github.com:...' -> 直接使用 (branch: main)
        - '/local/path' -> 本地路径，不使用 git

        Args:
            identifier: 技能标识

        Returns:
            Tuple[str, str, Optional[str]]: (git_url_or_path, branch, sub_dir)

        Raises:
            ValueError: 无效的技能标识
        """
        identifier = identifier.strip()

        if not identifier:
            raise ValueError("技能标识不能为空")

        # 本地路径
        if identifier.startswith('/') or identifier.startswith('./') or identifier.startswith('../'):
            path = Path(identifier).resolve()
            if not path.exists():
                raise ValueError(f"本地路径不存在: {identifier}")
            return (str(path), '', None)

        # 完整 Git SSH URL
        if identifier.startswith('git@'):
            return (identifier, 'main', None)

        # 完整 HTTPS URL
        if identifier.startswith('https://') or identifier.startswith('http://'):
            # 检查是否包含分支指定: URL#branch
            if '#' in identifier:
                url, branch = identifier.rsplit('#', 1)
                return (url, branch, None)
            return (identifier, 'main', None)

        # ClawHub 格式: @org/skill-name
        if identifier.startswith('@'):
            parts = identifier[1:].split('/', 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(
                    f"无效的 ClawHub 技能标识: '{identifier}'。"
                    f"正确格式: '@org/skill-name'，例如: '@openclaw/summarize'"
                )
            org, skill_name = parts
            # org/repo 格式 -> https://github.com/org/skill-name
            url = f"https://github.com/{org}/{skill_name}"
            return (url, 'main', None)

        # 简写: skill-name -> openclaw/skills 仓库的子目录
        if re.match(r'^[a-zA-Z0-9_-]+$', identifier):
            url = f"{CLAWHUB_BASE_URL}"
            # 对于简写格式，返回特殊标记以便后续处理为子目录
            return (url, 'main', identifier)

        # org/skill-name 格式（无 @ 前缀）
        if '/' in identifier and not identifier.startswith('/'):
            parts = identifier.split('/', 1)
            if len(parts) == 2 and parts[0] and parts[1]:
                url = f"https://github.com/{parts[0]}/{parts[1]}"
                return (url, 'main', None)
            raise ValueError(
                f"无效的技能标识: '{identifier}'。"
                f"正确格式: 'org/skill-name' 或 '@org/skill-name'"
            )

        raise ValueError(
            f"无法识别的技能标识: '{identifier}'。"
            f"支持的格式: 'skill-name', '@org/skill-name', "
            f"'https://github.com/...', 'git@github.com:...'"
        )

    @staticmethod
    def _build_git_auth_url(url: str, token: Optional[str] = None) -> str:
        """
        构建 Git 认证 URL

        Args:
            url: 原始 Git URL
            token: GitHub 个人访问令牌（可选，也可从环境变量读取）

        Returns:
            str: 带 token 的 Git URL（如果提供了 token）
        """
        if not token:
            # 尝试从环境变量读取
            token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')

        if not token:
            return url

        # HTTPS URL: 插入 token
        if url.startswith('https://'):
            # https://github.com/org/repo -> https://x-access-token:TOKEN@github.com/org/repo
            return url.replace('https://', f'https://x-access-token:{token}@', 1)

        # SSH URL: 不需要 token，使用 SSH key
        if url.startswith('git@'):
            return url

        return url

    def install_skill(
        self,
        identifier: str,
        branch: str = 'main',
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        从技能标识安装技能

        支持:
        - ClawHub 技能名: 'summarize'
        - 组织/技能名: '@openclaw/summarize' 或 'openclaw/summarize'
        - Git URL: 'https://github.com/org/repo'
        - SSH URL: 'git@github.com:org/repo.git'
        - 本地路径: '/path/to/skill'

        Args:
            identifier: 技能标识
            branch: Git 分支（默认 main）
            token: GitHub 个人访问令牌

        Returns:
            Dict[str, Any]: 安装结果
        """
        try:
            git_url, resolved_branch, sub_dir = self.resolve_skill_identifier(identifier)
            resolved_branch = resolved_branch or branch
        except ValueError as e:
            return {'status': 'failed', 'error': str(e)}

        # 本地路径安装
        if git_url.startswith('/') and not sub_dir:
            return self._install_from_local(git_url)

        # Git 安装
        return self._install_from_git(
            git_url, resolved_branch, sub_dir=sub_dir, token=token
        )

    def _install_from_local(self, local_path: str) -> Dict[str, Any]:
        """从本地路径安装"""
        src = Path(local_path)
        if not src.exists():
            return {'status': 'failed', 'error': f'本地路径不存在: {local_path}'}

        backup_path = self.create_backup(name=f'pre_install_{datetime.now().strftime("%Y%m%d_%H%M%S")}')

        try:
            privileged_dst = self.skill_root / 'src' / 'privileged'
            # 先复制到临时位置，确认成功后再替换
            temp_dst = self.sandbox_dir / 'privileged_temp'
            if temp_dst.exists():
                shutil.rmtree(temp_dst)
            shutil.copytree(src, temp_dst)
            if privileged_dst.exists():
                shutil.rmtree(privileged_dst)
            shutil.move(str(temp_dst), str(privileged_dst))

            self.update_version(version='local', commit='local')
            return {'status': 'success', 'source': local_path, 'backup': str(backup_path)}
        except Exception as e:
            self.restore_backup(backup_path.name)
            return {'status': 'failed', 'error': str(e)}

    def _install_from_git(
        self,
        git_url: str,
        branch: str = 'main',
        sub_dir: Optional[str] = None,
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        从 Git 仓库安装技能

        Args:
            git_url: Git 仓库 URL
            branch: 分支名
            sub_dir: 子目录名（简写标识时使用）
            token: GitHub 认证令牌
        """
        backup_path = self.create_backup(name=f'pre_install_{datetime.now().strftime("%Y%m%d_%H%M%S")}')

        try:
            temp_dir = self.sandbox_dir / 'install_temp'
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

            # 构建 Git 认证 URL
            auth_url = self._build_git_auth_url(git_url, token)

            # 构建克隆命令
            # 使用 GIT_ASKPASS 环境变量传递 token，避免在命令行中暴露
            env = os.environ.copy()
            askpass_script = None

            if token and auth_url != git_url:
                # 创建临时 askpass 脚本，避免 token 出现在 /proc/cmdline
                import tempfile
                askpass_fd, askpass_path = tempfile.mkstemp(suffix='.sh', prefix='git_askpass_')
                try:
                    with os.fdopen(askpass_fd, 'w') as f:
                        f.write(f'#!/bin/sh\necho "{token}"\n')
                    os.chmod(askpass_path, 0o700)
                    env['GIT_ASKPASS'] = askpass_path
                    askpass_script = askpass_path
                    # 使用原始 URL（不含 token），让 GIT_ASKPASS 提供凭证
                    clone_url = git_url
                except Exception:
                    # 回退：如果 askpass 创建失败，仍使用 auth_url
                    clone_url = auth_url
                    askpass_script = None
            else:
                clone_url = auth_url

            clone_cmd = ['git', 'clone', '--depth', '1', '--branch', branch]

            # 如果指定了子目录，先浅克隆整个仓库
            clone_cmd.extend([clone_url, str(temp_dir)])

            try:
                result = subprocess.run(
                    clone_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                )
            finally:
                # 清理 askpass 脚本
                if askpass_script and os.path.exists(askpass_script):
                    try:
                        os.unlink(askpass_script)
                    except Exception:
                        pass

            if result.returncode != 0:
                # 过滤错误消息中的 token
                error_msg = result.stderr.strip()
                if token:
                    error_msg = error_msg.replace(token, '***')
                # 检测认证错误并给出明确提示
                if 'Authentication failed' in error_msg or 'could not read Username' in error_msg:
                    error_msg += (
                        "\n\n💡 解决方法："
                        "\n  1. 设置 GitHub token: export GITHUB_TOKEN=ghp_xxxxx"
                        "\n  2. 或使用 SSH URL: git@github.com:org/repo.git"
                        "\n  3. 或在 install_skill() 中传入 token 参数"
                    )
                return {
                    'status': 'failed',
                    'error': f'Git clone 失败: {error_msg}',
                    'backup': str(backup_path),
                }

            # 如果指定了子目录（简写标识），从子目录复制
            if sub_dir:
                skill_src = temp_dir / sub_dir
                if not skill_src.exists():
                    return {
                        'status': 'failed',
                        'error': f'技能子目录不存在: {sub_dir}',
                        'backup': str(backup_path),
                    }
            else:
                skill_src = temp_dir

            # 获取提交哈希
            commit_result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=temp_dir,
                capture_output=True,
                text=True,
            )
            commit = commit_result.stdout.strip()

            # 更新私有包 — 先复制到临时位置，确认成功后再替换
            privileged_dst = self.skill_root / 'src' / 'privileged'
            temp_dst = self.sandbox_dir / 'privileged_temp'
            if temp_dst.exists():
                shutil.rmtree(temp_dst)
            shutil.copytree(skill_src, temp_dst)
            if privileged_dst.exists():
                shutil.rmtree(privileged_dst)
            shutil.move(str(temp_dst), str(privileged_dst))

            # 清理临时目录
            shutil.rmtree(temp_dir)

            # 更新版本信息
            self.update_version(version='latest', commit=commit)

            return {
                'status': 'success',
                'message': 'Skill installed',
                'source': git_url,
                'sub_dir': sub_dir,
                'commit': commit,
                'branch': branch,
                'backup': str(backup_path),
            }

        except subprocess.TimeoutExpired:
            return {
                'status': 'failed',
                'error': 'Git clone 超时（120s），请检查网络连接或 URL 是否正确',
                'backup': str(backup_path),
            }
        except Exception as e:
            self.restore_backup(backup_path.name)
            return {
                'status': 'failed',
                'error': str(e),
                'restored_from': str(backup_path),
            }

    def auto_update(self, remote_url: str, branch: str = 'main') -> Dict[str, Any]:
        """自动更新（保留向后兼容）"""
        # 检查更新
        update_info = self.check_for_updates(remote_url)

        if not update_info.get('has_update'):
            return {'status': 'up_to_date', 'message': 'Already up to date'}

        # 使用 install_skill 统一逻辑
        return self._install_from_git(remote_url, branch)

    def get_sandbox_config(self) -> Dict[str, Any]:
        """获取沙箱配置"""
        if not self.config_file.exists():
            return self._default_config()

        try:
            return json.loads(self.config_file.read_text())
        except Exception:
            return self._default_config()

    def set_sandbox_config(self, config: Dict[str, Any]):
        """设置沙箱配置"""
        self.config_file.write_text(json.dumps(config, indent=2))

    def _default_config(self) -> Dict[str, Any]:
        """默认配置"""
        return {
            'auto_update': False,
            'auto_update_interval': 86400,  # 24小时
            'max_backups': 10,
            'remote_url': '',
            'branch': 'main'
        }

    def cleanup_old_backups(self, max_count: int = None):
        """清理旧备份"""
        config = self.get_sandbox_config()
        max_count = max_count or config.get('max_backups', 10)

        backups = self.list_backups()

        if len(backups) > max_count:
            for backup in backups[max_count:]:
                backup_path = self.backup_dir / backup['name']
                if backup_path.exists():
                    shutil.rmtree(backup_path)


# ============ 导出 ============

__all__ = [
    'SandboxManager',
    'SudoTerminal',
    'CppToolchain',
    'PrebuiltManager',
    'PortablePythonManager',
    'VersionInfo'
]
