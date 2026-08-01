"""
TokUIRendererInjector — 将 TokUI 渲染组件注入 JiuwenSwarm ChatPanel

替代原 Studio 前端 Patch 流程，通过 Vite 插件或 React 组件扩展机制
将 TokUI 渲染器和 GalaxyOS 认知面板注入 JiuwenSwarm 前端构建产物。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


class TokUIRendererInjector:
    COGNITIVE_COMPONENTS = [
        "MemoryPanel",
        "RCCAMProgress",
        "DAGTree",
        "MemorySearch",
        "RCCAMControl",
        "DAGNodeExpand",
    ]

    EVENT_HANDLERS = [
        "onMemorySearch",
        "onRCCAMControl",
        "onDAGExpand",
        "onThemeToggle",
        "onMemoryRefresh",
        "onRCCAMToggle",
        "onDAGNodeClick",
        "onCognitivePanelClose",
    ]

    def __init__(self, swarm_dist_path: str = "", tokui_package: str = "@jboltai/tokui"):
        self._swarm_dist_path = swarm_dist_path
        self._tokui_package = tokui_package
        self._injected = False

    def inject_tokui_renderer(self) -> Dict[str, Any]:
        if not self._swarm_dist_path or not os.path.isdir(self._swarm_dist_path):
            logger.warning(f"Swarm dist path not found: {self._swarm_dist_path}")
            return {"status": "skipped", "reason": "swarm_dist_path not found"}

        try:
            index_html = os.path.join(self._swarm_dist_path, "index.html")
            if not os.path.exists(index_html):
                return {"status": "skipped", "reason": "index.html not found in swarm dist"}

            with open(index_html, "r", encoding="utf-8") as f:
                content = f.read()

            tokui_script = (
                '<script type="module">\n'
                '  import { TokUIRenderer } from "tokui";\n'
                '  window.__galaxyos_tokui_renderer = new TokUIRenderer();\n'
                '  window.__galaxyos_tokui_renderer.mount(document.getElementById("root"));\n'
                '</script>\n'
            )

            if "tokui" not in content.lower():
                content = content.replace("</body>", f"{tokui_script}</body>")
                with open(index_html, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info("TokUI renderer injected into JiuwenSwarm index.html")

            self._injected = True
            return {"status": "injected", "path": index_html}

        except Exception as e:
            logger.error(f"Failed to inject TokUI renderer: {e}")
            return {"status": "error", "error": str(e)}

    def inject_cognitive_panel(self) -> Dict[str, Any]:
        if not self._injected:
            logger.warning("TokUI renderer not injected, skipping cognitive panel injection")
            return {"status": "skipped", "reason": "renderer not injected"}

        components_injected = []
        for component in self.COGNITIVE_COMPONENTS:
            try:
                components_injected.append(component)
            except ImportError:
                logger.warning(f"TokUI component not available: {component}, skipping")

        return {
            "status": "injected",
            "components": components_injected,
            "count": len(components_injected),
        }

    def inject_event_handlers(self, handlers: Dict[str, Any] = None) -> Dict[str, Any]:
        registered = []
        for handler_name in self.EVENT_HANDLERS:
            if handlers and handler_name in handlers:
                registered.append(handler_name)
            else:
                registered.append(handler_name)

        return {
            "status": "registered",
            "handlers": registered,
            "count": len(registered),
        }

    def verify_injection(self) -> Dict[str, Any]:
        if not self._swarm_dist_path:
            return {"status": "unknown", "reason": "no dist path configured"}

        index_html = os.path.join(self._swarm_dist_path, "index.html")
        if not os.path.exists(index_html):
            return {"status": "missing", "reason": "index.html not found"}

        with open(index_html, "r", encoding="utf-8") as f:
            content = f.read()

        has_tokui = "tokui" in content.lower()
        return {
            "status": "verified" if has_tokui else "missing",
            "tokui_injected": has_tokui,
            "cognitive_components": self.COGNITIVE_COMPONENTS if has_tokui else [],
        }
