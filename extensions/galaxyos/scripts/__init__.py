"""
GalaxyOS Engine — 核心引擎层（瘦代理）

scripts/ 仅保留 claw_worker.py 入口 + _path_setup.py 路径注入。
所有引擎模块由 galaxyos/engine/ 提供，通过 sys.path 导入。
"""

import _path_setup  # noqa: F401 — 将 galaxyos/engine/ 加入 sys.path
