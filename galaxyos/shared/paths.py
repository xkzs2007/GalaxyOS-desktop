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
from functools import lru_cache


# ── 基础路径函数 ──

@lru_cache(maxsize=1)
def openclaw_home() -> str:
    """GalaxyOS/OpenClaw 根目录

    优先读取 OPENCLAW_HOME 环境变量，fallback 到 ~/.openclaw。
    """
    env = os.environ.get("OPENCLAW_HOME")
    if env:
        return env
    return os.path.expanduser("~/.openclaw")


@fusion_replace("galaxyos.init.init_path_resolver", "workspace")
@lru_cache(maxsize=1)
def workspace() -> str:
    """工作空间目录

    优先读取 OPENCLAW_WORKSPACE / WORKSPACE 环境变量，
    fallback 到 openclaw_home()/workspace。
    """
    env = os.environ.get("OPENCLAW_WORKSPACE") or os.environ.get("WORKSPACE")
    if env:
        return env
    return os.path.join(openclaw_home(), "workspace")


# ── 路径常量（privileged / scripts 所需的完整子集）──
# 不依赖 deployment_profile，仅基于环境变量和 openclaw_home()。

OPENCLAW_HOME: str = openclaw_home()
WORKSPACE: str = workspace()
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
