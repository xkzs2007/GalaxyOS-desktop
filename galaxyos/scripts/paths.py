#!/usr/bin/env python3
"""
向量扩展路径动态检测工具
所有脚本应使用此函数获取向量扩展路径
"""

from pathlib import Path
from typing import Optional


# ── Centralized path resolution ──
import os as _os, sys as _sys
from galaxyos.shared.paths import workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
def get_vec_extension_path() -> Path:
    """动态获取向量扩展路径"""
    possible_paths = [
        path_resolver.SQLITE_VEC_TENCENTDB,
        path_resolver.SQLITE_VEC_TENCENTDB,
    ]
    for p in possible_paths:
        if p.exists():
            return p
    # 返回默认路径（用户需确保扩展存在）
    return possible_paths[0]

def get_vectors_db_path() -> Path:
    """获取向量数据库路径"""
    return path_resolver.VECTORS_DB

def get_skill_path() -> Path:
    """获取技能目录路径"""
    return path_resolver.SKILLS_DIR / "llm-memory-integration"

# 常用路径常量（动态获取）
VECTORS_DB = get_vectors_db_path()
VEC_EXT = get_vec_extension_path()
SKILL_PATH = get_skill_path()
