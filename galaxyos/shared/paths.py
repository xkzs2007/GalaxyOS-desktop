"""
GalaxyOS Shared — 跨包路径常量

从 galaxyos.engine._types 提取完整路径接口，供 engine / privileged / scripts 共用。
本模块不依赖 galaxyos.engine 或 galaxyos.privileged（仅 stdlib）。

依赖方向: engine → shared, privileged → shared, scripts → shared
"""

from __future__ import annotations
from galaxyos.shared.fusion_guard import fusion_replace

import os
import sys
import warnings
from functools import lru_cache


# ── 基础路径函数 ──

@lru_cache(maxsize=1)
def galaxyos_home() -> str:
    """GalaxyOS 根目录（权威路径定义）

    优先级链: GALAXYOS_HOME > OPENCLAW_HOME > ~/.galaxyos > ~/.openclaw
    当 GALAXYOS_HOME 与 OPENCLAW_HOME 同时设置且不同时，发出警告。
    """
    galaxyos_env = os.environ.get("GALAXYOS_HOME")
    if galaxyos_env:
        galaxyos_env = os.path.expanduser(galaxyos_env)

    openclaw_env = os.environ.get("OPENCLAW_HOME")
    if openclaw_env:
        openclaw_env = os.path.expanduser(openclaw_env)

    if galaxyos_env and openclaw_env and galaxyos_env != openclaw_env:
        warnings.warn(
            f"GALAXYOS_HOME={galaxyos_env} differs from OPENCLAW_HOME={openclaw_env}. "
            f"GALAXYOS_HOME takes precedence.",
            UserWarning,
            stacklevel=2,
        )

    if galaxyos_env:
        return galaxyos_env

    if openclaw_env:
        return openclaw_env

    galaxyos_default = os.path.expanduser("~/.galaxyos")
    if os.path.isdir(galaxyos_default):
        return galaxyos_default

    return os.path.expanduser("~/.openclaw")


@lru_cache(maxsize=1)
def openclaw_home() -> str:
    """GalaxyOS/OpenClaw 根目录（向后兼容别名）

    内部委托给 galaxyos_home()，新代码应使用 galaxyos_home()。
    """
    return galaxyos_home()


@lru_cache(maxsize=1)
def audit_log_dir() -> str:
    """审计日志目录

    返回 $GALAXYOS_HOME/logs/audit/，自动创建。
    """
    d = os.path.join(galaxyos_home(), "logs", "audit")
    os.makedirs(d, exist_ok=True)
    return d


@fusion_replace("galaxyos.init.init_path_resolver", "workspace")
@lru_cache(maxsize=1)
def workspace() -> str:
    """工作空间目录

    优先读取 OPENCLAW_WORKSPACE / WORKSPACE 环境变量，
    fallback 到 galaxyos_home()/workspace。
    """
    env = os.environ.get("OPENCLAW_WORKSPACE") or os.environ.get("WORKSPACE")
    if env:
        return env
    return os.path.join(galaxyos_home(), "workspace")


# ── 路径常量（privileged / scripts 所需的完整子集）──
# 不依赖 deployment_profile，仅基于环境变量和 galaxyos_home()。

GALAXYOS_HOME: str = galaxyos_home()
OPENCLAW_HOME: str = galaxyos_home()
WORKSPACE: str = workspace()
AUDIT_LOG_DIR: str = audit_log_dir()
DATA_DIR: str = os.path.join(WORKSPACE, "data")
CONFIG_DIR: str = os.path.join(WORKSPACE, "config")
MODELS_DIR: str = os.path.join(WORKSPACE, "models")
CACHE_DIR: str = os.path.join(WORKSPACE, "cache")
MEMORY_DIR: str = os.path.join(WORKSPACE, "memory")
LOGS_DIR: str = os.path.join(WORKSPACE, "logs")
MEMORY_TDAI_DIR: str = os.path.join(MEMORY_DIR, "tdai")
VECTORS_DB: str = os.path.join(MEMORY_DIR, "vectors_db")
DAG_DB_PATH: str = os.path.join(WORKSPACE, "dag_context")
CORE_DIR: str = os.path.join(WORKSPACE, "core")
SKILLS_DIR: str = os.path.join(WORKSPACE, "skills")

# ── Module self-reference (path_resolver_compat) ──
# Enables: `from galaxyos.shared.paths import path_resolver_compat`
# then:    `path_resolver_compat.WORKSPACE`, `path_resolver_compat.VECTORS_DB`, etc.
path_resolver_compat = sys.modules[__name__]
