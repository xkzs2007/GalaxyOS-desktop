"""
GalaxyOS Harness — Workspace 工作空间管理

管理工作空间路径和状态，使用 galaxyos.shared.paths 获取路径。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from galaxyos.shared.paths import galaxyos_home, workspace as _default_workspace

logger = logging.getLogger("galaxyos.harness.workspace")


class Workspace:
    """工作空间管理

    管理工作空间路径和状态，使用 galaxyos.shared.paths 获取路径。

    Args:
        root: 工作空间根路径，默认使用 galaxyos.shared.paths.workspace()
    """

    def __init__(self, root: Optional[str] = None) -> None:
        self._root = root or _default_workspace()
        self._initialized = False
        self._ensure_dirs()

    @property
    def root(self) -> str:
        return self._root

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def data_dir(self) -> str:
        return os.path.join(self._root, "data")

    @property
    def config_dir(self) -> str:
        return os.path.join(self._root, "config")

    @property
    def models_dir(self) -> str:
        return os.path.join(self._root, "models")

    @property
    def cache_dir(self) -> str:
        return os.path.join(self._root, "cache")

    @property
    def memory_dir(self) -> str:
        return os.path.join(self._root, "memory")

    @property
    def logs_dir(self) -> str:
        return os.path.join(self._root, "logs")

    @property
    def skills_dir(self) -> str:
        return os.path.join(self._root, "skills")

    @property
    def home(self) -> str:
        """GalaxyOS 根目录（等同于 galaxyos_home()）"""
        return galaxyos_home()

    def _ensure_dirs(self) -> None:
        """确保工作空间核心目录存在"""
        for d in (
            self.data_dir,
            self.config_dir,
            self.models_dir,
            self.cache_dir,
            self.memory_dir,
            self.logs_dir,
            self.skills_dir,
        ):
            os.makedirs(d, exist_ok=True)
        self._initialized = True
        logger.debug("Workspace 目录已就绪: %s", self._root)

    def status(self) -> dict:
        """返回工作空间状态摘要"""
        return {
            "root": self._root,
            "home": self.home,
            "initialized": self._initialized,
            "dirs": {
                "data": os.path.isdir(self.data_dir),
                "config": os.path.isdir(self.config_dir),
                "models": os.path.isdir(self.models_dir),
                "cache": os.path.isdir(self.cache_dir),
                "memory": os.path.isdir(self.memory_dir),
                "logs": os.path.isdir(self.logs_dir),
                "skills": os.path.isdir(self.skills_dir),
            },
        }