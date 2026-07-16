"""
GalaxyOS Extension — JiuwenSwarm Extension Python 入口

JiuwenSwarm AgentServer 启动时自动加载此模块，
导入并调用 GalaxyOSExtension.register_extensions() 注册认知增强能力。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_registry = None
_extension = None


class SimpleExtensionRegistry:
    def __init__(self):
        self._rpc_handlers: Dict[str, Any] = {}
        self._hook_handlers: Dict[str, Any] = {}

    def register_rpc_handler(self, method: str, handler: Any) -> None:
        self._rpc_handlers[method] = handler
        logger.info(f"Registered RPC handler: {method}")

    def register_hook_handler(self, event: str, handler: Any) -> None:
        self._hook_handlers[event] = handler
        logger.info(f"Registered hook handler: {event}")

    def get_rpc_handler(self, method: str) -> Optional[Any]:
        return self._rpc_handlers.get(method)

    def get_hook_handler(self, event: str) -> Optional[Any]:
        return self._hook_handlers.get(event)

    @property
    def rpc_handlers(self) -> Dict[str, Any]:
        return dict(self._rpc_handlers)

    @property
    def hook_handlers(self) -> Dict[str, Any]:
        return dict(self._hook_handlers)


def get_extension():
    global _extension
    if _extension is None:
        from galaxyos.kernel.galaxyos_extension import GalaxyOSExtension
        _extension = GalaxyOSExtension(registry=_registry)
    return _extension


def get_registry():
    global _registry
    if _registry is None:
        try:
            from jiuwenswarm.extensions.registry import ExtensionRegistry
            _registry = ExtensionRegistry()
        except ImportError:
            logger.info("JiuwenSwarm ExtensionRegistry not available, using SimpleExtensionRegistry")
            _registry = SimpleExtensionRegistry()
    return _registry


async def register_extensions(registry=None) -> None:
    reg = registry or get_registry()
    ext = get_extension()
    ext.set_extension_dir(os.path.dirname(os.path.abspath(__file__)))
    await ext.register_extensions(reg)
    logger.info("GalaxyOS Extension loaded successfully")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    registry = get_registry()
    asyncio.run(register_extensions(registry))


if __name__ == "__main__":
    main()