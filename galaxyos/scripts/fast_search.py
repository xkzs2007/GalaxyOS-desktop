#!/usr/bin/env python3
"""快速搜索 - 禁用 LLM 增强"""
import subprocess
import sys


# ── Centralized path resolution ──
import os as _os
import sys as _sys
from galaxyos.shared.paths import workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
query = sys.argv[1] if len(sys.argv) > 1 else ""
if not query:
    print("用法: fast_search.py '查询'")
    sys.exit(1)

# 直接调用混合搜索，禁用 LLM
subprocess.run([
    "python3",
    str(path_resolver.SKILLS_DIR / "llm-memory-integration" / "scripts" / "hybrid_memory_search.py"),
    query, "--no-llm"
])
