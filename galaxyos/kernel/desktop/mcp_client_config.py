from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.expanduser("~/.galaxyos/mcp_servers.json")


class McpClientConfig:
    def __init__(self, config_path: Optional[str] = None) -> None:
        self._config_path = config_path or DEFAULT_CONFIG_PATH
        self._servers: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def load(self) -> Dict[str, Dict[str, Any]]:
        if self._loaded:
            return self._servers

        path = Path(self._config_path)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._servers = data.get("servers", {})
                logger.info(f"Loaded {len(self._servers)} MCP server configs from {self._config_path}")
            except Exception as e:
                logger.warning(f"Failed to load MCP client config from {self._config_path}: {e}")
                self._servers = {}
        else:
            logger.info(f"MCP client config not found at {self._config_path}, using empty config")
            self._servers = {}

        self._loaded = True
        return self._servers

    def get_server(self, name: str) -> Optional[Dict[str, Any]]:
        if not self._loaded:
            self.load()
        return self._servers.get(name)

    def list_servers(self) -> List[str]:
        if not self._loaded:
            self.load()
        return list(self._servers.keys())

    def add_server(self, name: str, config: Dict[str, Any]) -> None:
        self._servers[name] = config
        self._save()

    def remove_server(self, name: str) -> bool:
        if name in self._servers:
            del self._servers[name]
            self._save()
            return True
        return False

    def _save(self) -> None:
        path = Path(self._config_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"servers": self._servers}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save MCP client config: {e}")
