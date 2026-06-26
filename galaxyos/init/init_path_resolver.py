"""
GalaxyOS 统一路径解析模块 (Cross-Platform Path Resolver)

所有路径解析应通过本模块函数，而非直接使用 os.path.expanduser。
优先读取环境变量，fallback 到 expanduser 默认值。

环境变量:
  OPENCLAW_HOME    - 根目录 (默认 ~/.openclaw)
  WORKSPACE        - 工作空间 (默认 ~/.openclaw/workspace)
  GALAXYOS_VAR_DIR - 持久化var目录 (默认 ~/.openclaw/var)
  GALAXYOS_REPO    - 代码仓库路径 (默认 从__file__推断)
  GALAXYOS_MODELS_DIR - ONNX模型目录 (默认 ~/.openclaw/workspace/GalaxyOS/models)
"""

import os
from functools import lru_cache
from galaxyos.init.deployment_profile import get_profile

# P1-2: openclaw_home() 的权威定义在 galaxyos.shared.paths，此处重导出以保持向后兼容
from galaxyos.shared.paths import openclaw_home  # noqa: F401


@lru_cache(maxsize=1)
def workspace() -> str:
    """工作空间目录"""
    env = os.environ.get("WORKSPACE")
    if env:
        return env
    return os.path.join(openclaw_home(), "workspace")


@lru_cache(maxsize=1)
def var_dir() -> str:
    """持久化 var 目录 (容器中应挂载 SFS/NAS/CFS)"""
    env = os.environ.get("GALAXYOS_VAR_DIR")
    if env:
        return env
    profile = get_profile()
    profile_var = profile.get("var_dir")
    if profile_var:
        return profile_var
    return os.path.join(openclaw_home(), "var")


@lru_cache(maxsize=1)
def ext_var_dir() -> str:
    """Extension var 目录 (UDS sockets, mmap, temp files)

    Respects GALAXYOS_VAR_DIR environment variable for cloud deployment.
    Default: OPENCLAW_HOME/extensions/galaxyos/var (backward compat).
    In containers, GALAXYOS_VAR_DIR=/var/galaxyos overrides this.
    """
    env = os.environ.get("GALAXYOS_VAR_DIR")
    if env:
        return env
    profile = get_profile()
    profile_var = profile.get("var_dir")
    if profile_var:
        return profile_var
    return os.path.join(openclaw_home(), "extensions", "galaxyos", "var")


@lru_cache(maxsize=1)
def repo_dir() -> str:
    """GalaxyOS 代码仓库路径"""
    env = os.environ.get("GALAXYOS_REPO")
    if env:
        return env
    # 从本文件位置推断: galaxyos/init/init_path_resolver.py -> galaxyos/
    this_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(this_dir))


@lru_cache(maxsize=1)
def models_dir() -> str:
    """ONNX 模型目录"""
    env = os.environ.get("GALAXYOS_MODELS_DIR")
    if env:
        return env
    profile = get_profile()
    profile_models = profile.get("models_dir")
    if profile_models:
        return profile_models
    return os.path.join(workspace(), "GalaxyOS", "models")


def openclaw_path(*parts: str) -> str:
    """构建 OPENCLAW_HOME 下的路径"""
    return os.path.join(openclaw_home(), *parts)


def workspace_path(*parts: str) -> str:
    """构建 WORKSPACE 下的路径"""
    return os.path.join(workspace(), *parts)


def ensure_dir(path: str) -> str:
    """确保目录存在，返回路径"""
    # [LAYER-FIX] Lazy import (was upward ref to galaxyos.memory.dag.dag_integration_addon)
    try:
        import importlib as _lazy_mod_7076_il
        _lazy_mod_7076 = _lazy_mod_7076_il.import_module('galaxyos.memory.dag.dag_integration_addon')
        _ensure_dir = _lazy_mod_7076.ensure_dir
    except (ImportError, AttributeError):
        _ensure_dir = None
    return _ensure_dir(path)

# ── ARM/aarch64 原生库路径支持 ──

import platform as _platform


@lru_cache(maxsize=1)
def native_lib_dirs() -> list:
    """返回原生共享库搜索路径列表（含 ARM aarch64 路径）"""
    dirs = [
        os.path.join(openclaw_home(), "lib"),
        os.path.join(workspace(), "GalaxyOS", "lib"),
        "/opt/galaxyos/lib",
        "/usr/local/lib",
        "/usr/lib",
    ]

    # ARM aarch64 特定路径（鲲鹏处理器）
    if _platform.machine() == 'aarch64' or os.environ.get('GALAXYOS_ARCH', '').lower() == 'aarch64':
        dirs.extend([
            "/opt/galaxyos/lib/aarch64-linux-gnu",
            "/usr/lib/aarch64-linux-gnu",
            "/usr/local/lib/aarch64-linux-gnu",
        ])

    # x86_64 特定路径（仅当非 ARM 时）
    if _platform.machine() in ('x86_64', 'AMD64') and _platform.machine() != 'aarch64':
        dirs.extend([
            "/opt/galaxyos/lib/x86_64-linux-gnu",
            "/usr/lib/x86_64-linux-gnu",
        ])

    return dirs


def find_native_lib(lib_name: str) -> str:
    """在 native_lib_dirs() 中查找原生共享库，返回首个匹配路径或空字符串"""
    for d in native_lib_dirs():
        candidate = os.path.join(d, lib_name)
        if os.path.exists(candidate):
            return candidate
    return ""


# ── Legacy constants (backward-compat for `path_resolver.CONSTANT_NAME` usage) ──

# ── Core workspace path constants (merged from _workspace_constants.py) ──
# These are the single source of truth for all workspace-derived paths.
# OPENCLAW_WORKSPACE env-var takes precedence, then WORKSPACE env-var,
# then path_resolver.workspace() default.
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE", workspace())
WORKSPACE_ROOT = WORKSPACE  # alias for backward compat (synapse_pretrain, etc.)
DATA_DIR = os.path.join(WORKSPACE, "data")
CONFIG_DIR = os.path.join(WORKSPACE, "config")
MODELS_DIR = os.path.join(WORKSPACE, "models")  # constant form; models_dir() is the function
CACHE_DIR = os.path.join(WORKSPACE, "cache")
MEMORY_DIR = os.path.join(WORKSPACE, "memory")
LOGS_DIR = os.path.join(WORKSPACE, "logs")
MEMORY_TDAI_DIR = os.path.join(MEMORY_DIR, "tdai")
VECTORS_DB = os.path.join(MEMORY_DIR, "vectors_db")
DAG_DB_PATH = os.path.join(WORKSPACE, "dag_context")  # directory for DAG DB files
CORE_DIR = os.path.join(WORKSPACE, "core")

# ── Other legacy constants ──
DAG_DB = openclaw_path("dag_context.db")  # claw_helpers
RCI_SHARED_STATE = openclaw_path("rci_shared_state.json")  # claw_helpers
SKILLS_DIR = workspace_path("skills")  # capability_registry
GALAXYOS_CAPABILITY = openclaw_path("capability")  # capability_registry
XIAOYI_OMEGA_LLM_CONFIG = workspace_path(
    "skills/xiaoyi-claw-omega-final/config/llm_config.json"
)  # capability_registry
LLM_CONFIG_JSON = openclaw_path("llm_config.json")  # data_bridge, smart_processor
GALAXYOS_EXT_VAR = ext_var_dir()  # UDS/mmap/temp var dir (cloud-configurable)

# ── Additional unified constants (P0-1: single source of truth) ──
OPENCLAW_HOME = openclaw_home()  # constant form of openclaw_home()
OPENCLAW_CONFIG = openclaw_path("openclaw.json")  # top-level config file
TDAI_CACHE_DIR = os.path.join(MEMORY_TDAI_DIR, ".cache")  # search cache under tdai
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))  # galaxyos/engine/
PRIVILEGED_DIR = os.path.join(os.path.dirname(ENGINE_DIR), "privileged")  # galaxyos/privileged/
LLM_MEMORY_CORE_DIR = workspace_path("skills", "llm-memory-integration", "core")  # skill core dir

# LLM_MEMORY_DIR — added for backward compat
LLM_MEMORY_DIR = workspace_path("llm-memory-integration")
SCRIPTS_DIR = os.path.join(os.path.dirname(ENGINE_DIR), "scripts")  # galaxyos/scripts/
LOG_DIR = os.path.join(OPENCLAW_HOME, "logs")  # top-level logs
AUDIT_LOG_DIR = os.path.join(LOG_DIR, "audit")  # audit logs

# ── Derived constants (for callers expecting these names) ──
EXTENSIONS_DIR = os.path.join(OPENCLAW_HOME, "extensions")
SQLITE_VEC_TENCENTDB = os.path.join(OPENCLAW_HOME, "extensions", "lib")
_OPENCLAW_HOME = OPENCLAW_HOME

# ── Module self-reference ──
# Enables: `from galaxyos.config.config_path_resolver import path_resolver`
# then:    `path_resolver.workspace()`, `path_resolver.openclaw_home()`, etc.
import sys as _sys
path_resolver = _sys.modules[__name__]
