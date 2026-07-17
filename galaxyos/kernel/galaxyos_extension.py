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
        self._ws_pusher = None

    def set_extension_dir(self, extension_dir: str) -> None:
        self._extension_dir = extension_dir

    def set_bridges(self, bridge=None, memory_bridge=None, rccam_injector=None, ws_pusher=None) -> None:
        self._bridge = bridge
        self._memory_bridge = memory_bridge
        self._rccam_injector = rccam_injector
        self._ws_pusher = ws_pusher

    async def register_extensions(self, registry=None) -> None:
        reg = registry or self._registry
        if not reg:
            logger.warning("No ExtensionRegistry available, skipping extension registration")
            return

        self._register_rpc_handlers(reg)
        self._register_hook_handlers(reg)
        self._register_ws_push_handlers(reg)
        logger.info("GalaxyOS Extension registered: 5 RPC handlers + 3 hook handlers + WS push")

    def _register_rpc_handlers(self, registry) -> None:
        registry.register_rpc_handler("galaxyos.tokui_render", self.handle_tokui_render)
        registry.register_rpc_handler("galaxyos.rccam_status", self.handle_rccam_status)
        registry.register_rpc_handler("galaxyos.memory_query", self.handle_memory_query)
        registry.register_rpc_handler("galaxyos.cognitive_tools", self.handle_cognitive_tools)
        registry.register_rpc_handler("galaxyos.cognitive_panel_data", self.handle_cognitive_panel_data)

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

    async def handle_cognitive_panel_data(self, params: Dict[str, Any]) -> Dict[str, Any]:
        tab = params.get("tab", "memory")
        locale = params.get("locale", "zh")

        if tab == "memory":
            data = await self._collect_memory_data(locale)
        elif tab == "rccam":
            data = await self._collect_rccam_data(locale)
        elif tab == "dag":
            data = await self._collect_dag_data(locale)
        elif tab == "search":
            data = await self._collect_search_data(params, locale)
        else:
            data = {"html": f"<div style='padding:8px;color:#999'>Unknown tab: {tab}</div>"}

        return {"status": "success", **data}

    async def _collect_memory_data(self, locale: str) -> Dict[str, Any]:
        zh = locale == "zh"
        title = "液态神经记忆" if zh else "Liquid Neural Memory"
        waiting = "等待记忆数据..." if zh else "Waiting for memory data..."

        if self._memory_bridge:
            try:
                stats = await self._memory_bridge.get_stats()
                html = f"<div style='padding:8px'><h4 style='margin:0 0 8px'>{title}</h4>"
                html += f"<div style='display:flex;gap:8px;margin-bottom:8px'>"
                for key, label_zh, label_en in [
                    ("engram_count", "记忆碎片", "Engrams"),
                    ("neural_count", "神经节点", "Neural"),
                    ("synapse_count", "突触连接", "Synapses"),
                ]:
                    count = stats.get(key, 0)
                    label = label_zh if zh else label_en
                    html += f"<div style='flex:1;text-align:center;padding:8px;background:#f5f5f5;border-radius:6px'>"
                    html += f"<div style='font-size:18px;font-weight:600;color:#1976d2'>{count}</div>"
                    html += f"<div style='font-size:11px;color:#888'>{label}</div></div>"
                html += "</div></div>"
                return {"html": html, "tab": "memory"}
            except Exception as e:
                logger.warning(f"Memory data collection failed: {e}")

        return {"html": f"<div style='padding:8px'><h4>{title}</h4><p style='color:#999'>{waiting}</p></div>", "tab": "memory"}

    async def _collect_rccam_data(self, locale: str) -> Dict[str, Any]:
        zh = locale == "zh"
        title = "R-CCAM 认知循环" if zh else "R-CCAM Loop"
        waiting = "等待认知循环状态..." if zh else "Waiting for cognitive loop state..."

        if self._rccam_injector:
            try:
                status = self._rccam_injector.get_status()
                phase = status.get("phase", "idle")
                phase_label = {"idle": "空闲" if zh else "Idle", "retrieval": "检索" if zh else "Retrieval",
                               "cognition": "认知" if zh else "Cognition", "action": "行动" if zh else "Action",
                               "memory": "记忆" if zh else "Memory"}.get(phase, phase)
                color = "#4caf50" if phase != "idle" else "#9e9e9e"
                html = f"<div style='padding:8px'><h4 style='margin:0 0 8px'>{title}</h4>"
                html += f"<div style='display:flex;align-items:center;gap:6px;margin-bottom:8px'>"
                html += f"<span style='width:8px;height:8px;border-radius:50%;background:{color}'></span>"
                html += f"<span style='font-size:12px;color:{color}'>{phase_label}</span></div></div>"
                return {"html": html, "tab": "rccam"}
            except Exception as e:
                logger.warning(f"RCCAM data collection failed: {e}")

        return {"html": f"<div style='padding:8px'><h4>{title}</h4><p style='color:#999'>{waiting}</p></div>", "tab": "rccam"}

    async def _collect_dag_data(self, locale: str) -> Dict[str, Any]:
        zh = locale == "zh"
        title = "DAG 上下文树" if zh else "DAG Context Tree"
        waiting = "等待上下文节点..." if zh else "Waiting for context nodes..."
        return {"html": f"<div style='padding:8px'><h4>{title}</h4><p style='color:#999'>{waiting}</p></div>", "tab": "dag"}

    async def _collect_search_data(self, params: Dict[str, Any], locale: str) -> Dict[str, Any]:
        zh = locale == "zh"
        placeholder = "搜索记忆..." if zh else "Search memories..."
        btn = "搜索" if zh else "Search"
        html = f"<div style='padding:8px'><div style='display:flex;gap:6px'>"
        html += f"<input type='text' placeholder='{placeholder}' style='flex:1;padding:6px 10px;font-size:13px;border:1px solid #ccc;border-radius:4px' />"
        html += f"<button style='padding:6px 14px;font-size:13px;background:#1976d2;color:#fff;border:none;border-radius:4px;cursor:pointer'>{btn}</button></div></div>"
        return {"html": html, "tab": "search"}

    def _register_ws_push_handlers(self, registry) -> None:
        registry.register("agent_server:memory_after_chat", self._on_push_cognitive_update)
        registry.register("gateway:after_chat_request", self._on_push_cognitive_update)

    async def _on_push_cognitive_update(self, context: Dict[str, Any]) -> Dict[str, Any]:
        if self._ws_pusher:
            try:
                await self._ws_pusher.push_cognitive_update(context)
            except Exception as e:
                logger.warning(f"WS cognitive push failed: {e}")
        return context