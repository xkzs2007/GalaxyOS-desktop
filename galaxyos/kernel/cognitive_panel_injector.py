"""
CognitivePanelInjector — 将 GalaxyOS 6 个认知组件注入 tokui_chat

注入失败时降级为纯文本显示。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


COGNITIVE_COMPONENTS = [
    "MemoryPanel",
    "RCCAMProgress",
    "DAGTree",
    "MemorySearch",
    "RCCAMControl",
    "DAGNodeExpand",
]


class CognitivePanelInjector:
    def __init__(self, tokui_builder=None):
        self._tokui_builder = tokui_builder
        self._injected_components: List[str] = []
        self._fallback_components: List[str] = []

    async def inject(self) -> Dict[str, Any]:
        self._injected_components = []
        self._fallback_components = []

        for component in COGNITIVE_COMPONENTS:
            try:
                if self._tokui_builder and hasattr(self._tokui_builder, "register_custom_component"):
                    self._tokui_builder.register_custom_component(component)
                    self._injected_components.append(component)
                else:
                    self._fallback_components.append(component)
            except Exception as e:
                logger.warning(f"Cognitive component {component} inject failed: {e}, falling back to text")
                self._fallback_components.append(component)

        return {
            "status": "injected" if self._injected_components else "fallback",
            "injected": self._injected_components,
            "fallback": self._fallback_components,
            "total": len(COGNITIVE_COMPONENTS),
        }

    def get_status(self) -> Dict[str, Any]:
        return {
            "injected_components": self._injected_components,
            "fallback_components": self._fallback_components,
            "total": len(COGNITIVE_COMPONENTS),
        }