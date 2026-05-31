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

PrebuiltManager 功能：
- 管理预编译嵌入的二进制产物（扩展 + 工具链 + wheel）
- 预编译 hnswlib C 扩展直接加载（无需 pip install）
- 预打包工具链归档解压即用（无需 conda-forge 下载）
- 运行 scripts/prebuild.py 构建预编译产物
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
        'make', 'cmake',
        'cp', 'mv', 'mkdir', 'ln', 'chmod',
        'echo', 'cat', 'ls', 'find',
        'gcc', 'g++', 'cc',
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

        优先级: .whl 安装 > .so 直接加载

        Args:
            name: 扩展名 (如 'hnswlib')

        Returns:
            模块对象或 None
        """
        ext_dir = self.extensions_dir / name
        if not ext_dir.exists():
            return None

        # 策略1: 从预编译 wheel 安装
        wheels = sorted(ext_dir.glob('*.whl'))
        for wheel in wheels:
            if self._is_wheel_compatible(wheel):
                result = self._install_wheel(wheel)
                if result:
                    try:
                        return __import__(name)
                    except ImportError:
                        pass

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
        """从预缓存目录安装 wheel"""
        if not self.wheels_dir.exists():
            return {'status': 'unavailable', 'message': '无预缓存 wheel'}

        # 查找匹配的 wheel
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

        return {'status': 'not_found', 'message': f'未找到 {package_name} 的预缓存 wheel'}


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
    def has_compiler_toolchain(self) -> bool:
        """是否有完整的 C++ 编译工具链"""
        return self.has_gcc and self.has_gxx and self.has_cmake

    def check_environment(self) -> Dict[str, Any]:
        """
        检查 C++ 编译环境

        Returns:
            Dict: 编译环境信息
        """
        result = {
            'gcc': self.find_compiler('gcc'),
            'g++': self.find_compiler('g++'),
            'cc': self.find_compiler('cc'),
            'c++': self.find_compiler('c++'),
            'cmake': self.find_compiler('cmake'),
            'make': self.find_compiler('make'),
            'has_toolchain': self.has_compiler_toolchain,
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

        # CMAKE_PREFIX_PATH: cmake 搜索路径
        existing_cmake = env.get('CMAKE_PREFIX_PATH', '')
        if str(self.toolchain_dir) not in existing_cmake:
            env['CMAKE_PREFIX_PATH'] = f"{self.toolchain_dir}:{existing_cmake}" if existing_cmake else str(self.toolchain_dir)

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
                if self.find_compiler('cmake'):
                    installed.append('cmake')
                if self.find_compiler('make'):
                    installed.append('make')
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
    'VersionInfo'
]
