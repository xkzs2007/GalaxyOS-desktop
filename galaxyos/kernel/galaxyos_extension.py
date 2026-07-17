"""
GalaxyOSExtension — JiuwenSwarm Extension 注册 GalaxyOS 认知增强能力

作为 JiuwenSwarm Extension 注册到 ExtensionRegistry，提供：
  - RPC 方法：galaxyos.tokui_render, galaxyos.rccam_status, galaxyos.memory_query, galaxyos.cognitive_tools
  - Hook handlers：before_chat_request (R-CCAM 注入), memory_after_chat (记忆双写), before_system_prompt_build (认知上下文注入)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class GalaxyOSExtension:
    def __init__(self, registry=None, config: Optional[Dict[str, Any]] = None):
        self._registry = registry
        self._config = config or {}
        self._extension_dir = ""
        self._rpc_handlers: Dict[str, Callable] = {}
        self._hook_handlers: Dict[str, Callable] = {}
        self._bridge = None
        self._memory_bridge = None
        self._rccam_injector = None

    def set_extension_dir(self, extension_dir: str) -> None:
        self._extension_dir = extension_dir

    def set_bridges(self, bridge=None, memory_bridge=None, rccam_injector=None) -> None:
        self._bridge = bridge
        self._memory_bridge = memory_bridge
        self._rccam_injector = rccam_injector

    async def register_extensions(self, registry=None) -> None:
        reg = registry or self._registry
        if not reg:
            logger.warning("No ExtensionRegistry available, skipping extension registration")
            return

        self._register_rpc_handlers(reg)
        self._register_hook_handlers(reg)
        logger.info("GalaxyOS Extension registered: 4 RPC handlers + 3 hook handlers")

    def _register_rpc_handlers(self, registry) -> None:
        registry.register_rpc_handler("galaxyos.tokui_render", self.handle_tokui_render)
        registry.register_rpc_handler("galaxyos.rccam_status", self.handle_rccam_status)
        registry.register_rpc_handler("galaxyos.memory_query", self.handle_memory_query)
        registry.register_rpc_handler("galaxyos.cognitive_tools", self.handle_cognitive_tools)

    def _register_hook_handlers(self, registry) -> None:
        registry.register("gateway:before_chat_request", self._on_before_chat_request)
        registry.register("agent_server:memory_after_chat", self._on_memory_after_chat)
        registry.register("agent_server:before_system_prompt_build", self._on_before_system_prompt_build)

    async def handle_tokui_render(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from galaxyos.kernel.tokui_builder import PyTokUIBuilder
            dsl_type = params.get("type", "card")
            content = params.get("content", "")
            builder = PyTokUIBuilder()
            result = builder.card(tt=dsl_type).p(content).end().build()
            return {"status": "success", "dsl": result}
        except ImportError:
            return {"status": "error", "error": "tokui package not available"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def handle_rccam_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._rccam_injector:
            try:
                status = self._rccam_injector.get_status()
                return {"status": "success", "rccam": status}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        return {"status": "success", "rccam": {"phase": "idle", "available": False}}

    async def handle_memory_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._memory_bridge:
            try:
                query = params.get("query", "")
                results = await self._memory_bridge.recall(query)
                return {"status": "success", "results": results}
            except Exception as e:
                return {"status": "error", "error": str(e)}
        return {"status": "success", "results": []}

    async def handle_cognitive_tools(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tools = [
            "galaxy_pool", "claw_rccam_progress", "claw_recall", "claw_lobster",
            "claw_health", "claw_vector_info", "claw_events", "claw_store",
            "claw_verify", "claw_rccam", "claw_save_memory", "claw_compile_skill",
            "claw_asset_search", "claw_asset_register", "claw_node_invoke",
        ]
        return {"status": "success", "tools": tools, "count": len(tools)}

    async def _on_before_chat_request(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if self._rccam_injector:
            try:
                await self._rccam_injector.inject_rccam(context)
            except Exception as e:
                logger.warning(f"R-CCAM injection failed: {e}")
        return context

    async def _on_memory_after_chat(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if self._memory_bridge:
            try:
                await self._memory_bridge.dual_write(context)
            except Exception as e:
                logger.warning(f"Memory dual-write failed: {e}")
        return context

    async def _on_before_system_prompt_build(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if self._memory_bridge:
            try:
                cognitive_context = await self._memory_bridge.assemble_context(context)
                context["cognitive_context"] = cognitive_context
            except Exception as e:
                logger.warning(f"Cognitive context injection failed: {e}")
        return context