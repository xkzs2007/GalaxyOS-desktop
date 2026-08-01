#!/usr/bin/env python3
"""
_path_setup.py — 将 galaxyos/engine/ 加入 sys.path

使 scripts/ 中的模块（如 claw_worker.py）能直接 import engine/ 下的模块，
无需符号链接或文件复制。Windows 兼容。
"""

import os
import sys

_ENGINE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "galaxyos", "engine"))

if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)