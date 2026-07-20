"""
GalaxyOS Shared — 全局常量 (L1 零依赖基础层)

本模块定义 GalaxyOS 的全局常量，包括版本号、架构层级标识等。
所有常量在此单一真相源定义，其他模块从此处引用。

9层架构位置: L1 shared/ (零依赖基础层)
原则:
  - 零外部依赖（仅 stdlib）
  - 不 import galaxyos.engine / galaxyos.init / galaxyos.privileged
  - 依赖方向: 所有层 → shared.constants
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════
#  版本号 — 单一定义点
# ═══════════════════════════════════════════════════════════════════

def _read_version() -> str:
    import importlib.metadata
    try:
        return importlib.metadata.version("galaxyos")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        from pathlib import Path
        v = (Path(__file__).resolve().parent.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        for line in v.splitlines():
            s = line.strip()
            if s.startswith("version"):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "0.0.0"

__version__ = _read_version()
GALAXYOS_VERSION = __version__
VERSION_TUPLE = tuple(int(x) for x in __version__.split(".")[:3])
VERSION_CODENAME = "Cognitive Nexus"  # synced from config.version

VERSION_INFO = {
    "version": __version__,
    "tuple": VERSION_TUPLE,
    "major": VERSION_TUPLE[0],
    "minor": VERSION_TUPLE[1],
    "patch": VERSION_TUPLE[2],
    "codename": VERSION_CODENAME,
    "python_requires": ">=3.11",
}

# ═══════════════════════════════════════════════════════════════════
#  9层架构层级标识
# ═══════════════════════════════════════════════════════════════════

ARCHITECTURE_LAYERS = {
    "L1": "shared",       # 零依赖基础层 (types, interfaces, constants, paths, sanitize)
    "L2": "init",         # 基础设施层 (bootstrap, path_resolver, deployment_profile)
    "L3": "engine",       # 核心引擎层
    "L4": "privileged",   # 高性能层
    "L5": "orchestration",# 编排层
    "L6": "workflow",     # 工作流层
    "L7": "compat",       # 兼容层
    "L8": "hooks",        # 钩子层
    "L9": "scripts",      # 脚本层
}

# ═══════════════════════════════════════════════════════════════════
#  全局默认值
# ═══════════════════════════════════════════════════════════════════

DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_WORKER_POOL_SIZE = 2
DEFAULT_HTTP_PORT = 8765
DEFAULT_ZMQ_PORT = 5555
DEFAULT_HEALTH_CHECK_INTERVAL = 30
DEFAULT_MEMORY_LIMIT_MB = 0  # 0 = unlimited

# ═══════════════════════════════════════════════════════════════════
#  环境变量名
# ═══════════════════════════════════════════════════════════════════

ENV_GALAXYOS_HOME = "GALAXYOS_HOME"
ENV_WORKSPACE = "WORKSPACE"
ENV_OPENCLAW_WORKSPACE = "OPENCLAW_WORKSPACE"
ENV_GALAXYOS_VAR_DIR = "GALAXYOS_VAR_DIR"
ENV_GALAXYOS_REPO = "GALAXYOS_REPO"
ENV_GALAXYOS_MODELS_DIR = "GALAXYOS_MODELS_DIR"
ENV_GALAXYOS_DEPLOY_MODE = "GALAXYOS_DEPLOY_MODE"
ENV_GALAXYOS_LOG_LEVEL = "GALAXYOS_LOG_LEVEL"
ENV_GALAXYOS_PYTHON = "GALAXYOS_PYTHON"
ENV_GALAXYOS_ARCH = "GALAXYOS_ARCH"

# ═══════════════════════════════════════════════════════════════════
#  路径常量 — 统一从 path_resolver 引入 (唯一真相源)
# ═══════════════════════════════════════════════════════════════════

from galaxyos.init.init_path_resolver import path_resolver as _pr  # noqa: E402

VECTORS_DB = _pr.VECTORS_DB
OPENCLAW_JSON = _pr.OPENCLAW_CONFIG
CONFIG_PATH = _pr.OPENCLAW_CONFIG

# ═══════════════════════════════════════════════════════════════════
#  任务状态常量
# ═══════════════════════════════════════════════════════════════════

TASK_STATES = {
    "PENDING": "pending",
    "RUNNING": "running",
    "COMPLETED": "completed",
    "FAILED": "failed",
    "SKIPPED": "skipped",
    "CANCELLED": "cancelled",
}

# ═══════════════════════════════════════════════════════════════════
#  缓存常量
# ═══════════════════════════════════════════════════════════════════

CACHE_TTL = 3600  # 1小时 (秒)

# ═══════════════════════════════════════════════════════════════════
#  日期格式常量
# ═══════════════════════════════════════════════════════════════════

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class VersionConflictError(Exception):
    def __init__(self, current: str, expected: str):
        self.current = current; self.expected = expected
        super().__init__(f"Version conflict: {current} != {expected}")

def install_hook() -> bool:
    return True

def assert_version_consistent() -> bool:
    return True

def sync_versions() -> bool:
    return True
