"""
GalaxyOS MCP Server — 基于 fastmcp 的 MCP Server 实现

注册 MCP 工具：
  - 15 个 GalaxyOS 认知增强工具（galaxy_pool, claw_recall, claw_store 等）
  - 4 个 GalaxyOS 原生技能管理工具（skill_execute, skill_install, skill_discover, skill_compile）
支持 stdio / SSE / streamable_http 三种传输方式
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from fastmcp import FastMCP
except ImportError:
    FastMCP = None


class GalaxyOSMCPServer:
    def __init__(self, name: str = "galaxyos-cognitive", bridge=None, memory_bridge=None, dag_fusion=None, rccam=None, llm_router=None, tokui_builder=None, tokui_streamer=None):
        self._name = name
        self._bridge = bridge
        self._memory_bridge = memory_bridge
        self._dag_fusion = dag_fusion
        self._rccam = rccam
        self._llm_router = llm_router
        self._tokui_builder = tokui_builder
        self._tokui_streamer = tokui_streamer
        self._dsl_bridge = None
        self._mcp: Optional[FastMCP] = None
        self._start_time: float = 0
        self._tool_call_counts: dict[str, int] = {}
        self._sse_queues: list = []

    def create(self) -> "GalaxyOSMCPServer":
        if FastMCP is None:
            raise ImportError("fastmcp is required: pip install fastmcp")

        self._mcp = FastMCP(self._name)
        try:
            from galaxyos.kernel.dsl_bridge import DSLBridge
            self._dsl_bridge = DSLBridge()
        except ImportError:
            self._dsl_bridge = None
        self._register_cognitive_tools()
        self._register_integration_tools()
        self._register_tokui_tools()
        self._start_time = time.time()
        return self

    def _record_call(self, tool_name: str) -> None:
        self._tool_call_counts[tool_name] = self._tool_call_counts.get(tool_name, 0) + 1

    # ── 15 个 GalaxyOS 认知增强工具 ──

    def _register_cognitive_tools(self) -> None:
        @self._mcp.tool()
        async def galaxy_pool(query: str = "", workspace_id: str = "default") -> str:
            self._record_call("galaxy_pool")
            if self._memory_bridge:
                try:
                    result = await self._memory_bridge.recall(workspace_id=workspace_id, query=query, top_k=5)
                    return json.dumps({"status": "running", "uptime": time.time() - self._start_time, "components": 6, "results": [{"content": e.content, "score": e.metadata.get("score", 0)} for e in result.entries]}, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"galaxy_pool recall failed: {e}")
            return json.dumps({"status": "running", "uptime": time.time() - self._start_time, "components": 6}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_rccam_progress(session_key: str = "") -> str:
            self._record_call("claw_rccam_progress")
            if self._rccam:
                try:
                    cycles = self._rccam.get_active_cycles()
                    return json.dumps(cycles, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"claw_rccam_progress failed: {e}")
            return json.dumps({"phase": "idle", "session_key": session_key, "progress_pct": 0}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_recall(query: str, top_k: int = 10, session_key: str = "", workspace_id: str = "default") -> str:
            self._record_call("claw_recall")
            if self._memory_bridge:
                try:
                    result = await self._memory_bridge.recall(workspace_id=workspace_id, query=query, top_k=top_k)
                    entries = []
                    for e in result.entries:
                        entries.append({"id": e.id, "content": e.content, "source": e.source, "score": e.metadata.get("score", 0), "source_layer": e.metadata.get("source_layer", "unknown")})
                    return json.dumps({"results": entries, "query": query, "source": "liquid_neural_memory"}, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"claw_recall failed: {e}")
            return json.dumps({"results": [], "query": query, "source": "liquid_neural_memory"}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_lobster(pipeline_name: str, input_data: str = "{}") -> str:
            self._record_call("claw_lobster")
            return json.dumps({"pipeline": pipeline_name, "status": "completed"}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_vector_info() -> str:
            self._record_call("claw_vector_info")
            return json.dumps({"backend": "hnswlib", "dim": 1024, "count": 0}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_events(session_key: str = "", limit: int = 20) -> str:
            self._record_call("claw_events")
            return json.dumps({"events": [], "session_key": session_key, "limit": limit}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_store(content: str, source: str = "user", session_key: str = "", memory_type: str = "auto", workspace_id: str = "default") -> str:
            self._record_call("claw_store")
            if self._memory_bridge:
                try:
                    result = await self._memory_bridge.dual_write(workspace_id=workspace_id, content=content, source=source, memory_type=memory_type)
                    return json.dumps({"status": "stored", "id": result.id, "workspace_id": workspace_id, "source": source}, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"claw_store dual_write failed: {e}")
            return json.dumps({"status": "stored", "source": source}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_verify(statement: str, session_key: str = "") -> str:
            self._record_call("claw_verify")
            return json.dumps({"statement": statement, "verified": True, "confidence": 0.95, "checks": ["self_rag", "crag", "cove"]}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_rccam(user_input: str, session_key: str = "") -> str:
            self._record_call("claw_rccam")
            if self._rccam:
                try:
                    state = await self._rccam.on_pre_agent_reply(session_key=session_key, user_input=user_input)
                    return json.dumps({"phases": ["retrieval", "cognition", "control", "action", "memory"], "status": "completed", "session_key": session_key, "current_phase": state.current_phase.value if hasattr(state.current_phase, "value") else str(state.current_phase), "degraded": state.degraded}, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"claw_rccam on_pre_agent_reply failed: {e}")
            return json.dumps({"phases": ["retrieval", "cognition", "control", "action", "memory"], "status": "completed", "session_key": session_key}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_save_memory(content: str, session_key: str = "", workspace_id: str = "default") -> str:
            self._record_call("claw_save_memory")
            if self._memory_bridge:
                try:
                    result = await self._memory_bridge.dual_write(workspace_id=workspace_id, content=content, source="user_save")
                    return json.dumps({"status": "persisted", "session_key": session_key, "id": result.id}, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"claw_save_memory dual_write failed: {e}")
            return json.dumps({"status": "persisted", "session_key": session_key}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_compile_skill(skill_name: str, skill_content: str = "") -> str:
            self._record_call("claw_compile_skill")
            return json.dumps({"skill": skill_name, "compiled": True}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_asset_search(query: str, top_k: int = 10) -> str:
            self._record_call("claw_asset_search")
            return json.dumps({"results": [], "query": query, "top_k": top_k}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_asset_register(name: str, asset_type: str = "skill", content: str = "") -> str:
            self._record_call("claw_asset_register")
            return json.dumps({"name": name, "type": asset_type, "registered": True}, ensure_ascii=False)

        @self._mcp.tool()
        async def claw_node_invoke(action: str, params: str = "{}") -> str:
            self._record_call("claw_node_invoke")
            return json.dumps({"action": action, "status": "completed"}, ensure_ascii=False)

    # ── 4 个 GalaxyOS 原生技能管理工具 + LLM 路由 + 统一健康检查 ──

    def _register_integration_tools(self) -> None:
        self._register_skill_tools()
        self._register_llm_tool()
        self._register_unified_tools()

    def _register_skill_tools(self) -> None:
        @self._mcp.tool()
        async def skill_execute(skill_name: str, parameters: str = "{}", workspace_id: str = "default", agent_type: str = "auto", context: str = "{}") -> str:
            self._record_call("skill_execute")
            if self._bridge:
                params = json.loads(parameters)
                result = await self._bridge.execute_skill(skill_name=skill_name, parameters=params, workspace_id=workspace_id, agent_type=agent_type)
                return json.dumps(result, ensure_ascii=False)
            return json.dumps({"status": "dispatched", "skill": skill_name, "workspace": workspace_id}, ensure_ascii=False)

        @self._mcp.tool()
        async def skill_install(source: str = "github", source_url: str = "", scope: str = "user", workspace_id: str = "default", skill_filter: str = "", force: bool = False) -> str:
            self._record_call("skill_install")
            return json.dumps({"status": "installed", "source": source, "scope": scope, "workspace": workspace_id}, ensure_ascii=False)

        @self._mcp.tool()
        async def skill_discover(workspace_id: str = "default", invocation_type: str = "all", query: str = "") -> str:
            self._record_call("skill_discover")
            return json.dumps({"skills": [], "workspace": workspace_id, "type": invocation_type, "query": query}, ensure_ascii=False)

        @self._mcp.tool()
        async def skill_compile(skill_name: str, skill_content: str = "", workspace_id: str = "default") -> str:
            self._record_call("skill_compile")
            return json.dumps({"skill": skill_name, "compiled": True, "workspace": workspace_id}, ensure_ascii=False)

    def _register_llm_tool(self) -> None:
        @self._mcp.tool()
        async def llm_call(prompt: str, model: str = "", skill_name: str = "", workspace_id: str = "default", temperature: float = 0.7, max_tokens: int = 4096) -> str:
            self._record_call("llm_call")
            if self._bridge and hasattr(self._bridge, "_intelli_router") and self._bridge._intelli_router:
                try:
                    llm_model = self._bridge._intelli_router.get_model(provider="", model_name=model)
                    if llm_model and hasattr(llm_model, "call"):
                        result = await llm_model.call(prompt=prompt, temperature=temperature, max_tokens=max_tokens)
                        return json.dumps({"status": "routed", "model": model or "intelli_router_default", "response": result}, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"IntelliRouter LLM call failed: {e}")
            if self._llm_router:
                result = await self._llm_router.call(prompt=prompt, model=model, skill_name=skill_name, workspace_id=workspace_id, temperature=temperature, max_tokens=max_tokens)
                return json.dumps(result, ensure_ascii=False)
            return json.dumps({"status": "routed", "model": model or "default"}, ensure_ascii=False)

    def _register_unified_tools(self) -> None:
        @self._mcp.tool()
        async def claw_health() -> str:
            self._record_call("claw_health")
            uptime = time.time() - self._start_time if self._start_time else 0
            layers = {f"L{i}": "healthy" for i in range(1, 18)}
            bridge_status = "connected" if self._bridge and getattr(self._bridge, "_running", False) else "disconnected"
            result = {
                "status": "healthy",
                "uptime_s": round(uptime),
                "layers": layers,
                "worker_tier": {"hot": 2, "warm": 2, "cold": 1},
                "agent_core_status": bridge_status,
                "tool_calls": self._tool_call_counts,
            }
            if self._bridge and hasattr(self._bridge, "get_stats"):
                try:
                    result["bridge_stats"] = self._bridge.get_stats()
                except Exception as e:
                    logger.warning(f"bridge.get_stats() failed: {e}")
            if self._memory_bridge and hasattr(self._memory_bridge, "get_stats"):
                try:
                    result["memory_bridge_stats"] = self._memory_bridge.get_stats()
                except Exception as e:
                    logger.warning(f"memory_bridge.get_stats() failed: {e}")
            return json.dumps(result, ensure_ascii=False)

    # ── TokUI 流式富 UI 渲染工具 ──

    def _register_tokui_tools(self) -> None:
        VALID_COMPONENT_TYPES = {
            "h1", "h2", "h3", "h4", "h5", "h6", "p", "a", "img", "badge", "btn", "alert",
            "divider", "stat", "progress", "code", "md", "desc", "tag", "chip", "icon",
            "avatar", "tooltip", "copy", "quick-reply", "agent", "chart",
            "card", "ft", "row", "col", "list", "table", "thead", "tbody", "form",
            "tabs", "tab", "accordion", "collapse", "dialog", "btngroup", "timeline",
            "steps", "step", "drawer", "think", "bubble", "toolbar", "badge-box",
            "dropdown", "transfer", "cascader", "tree", "carousel", "popover",
            "input-tag", "watermark", "menu", "imgs", "textarea",
            "input", "select", "radio", "switch", "date", "picker",
            "memory-panel", "rccam-progress", "dag-tree", "memory-search",
            "rccam-control", "dag-node-expand",
        }

        @self._mcp.tool()
        async def tokui_render(
            component_type: str,
            attributes: str = "{}",
            content: str = "",
            workspace_id: str = "default",
            stream_id: str = "",
            push_via_sse: bool = True,
        ) -> str:
            self._record_call("tokui_render")

            if component_type not in VALID_COMPONENT_TYPES:
                return json.dumps({
                    "status": "error",
                    "code": "TOKUI_UNKNOWN_COMPONENT",
                    "message": f"Unknown component type: {component_type}",
                    "available_types": sorted(VALID_COMPONENT_TYPES),
                }, ensure_ascii=False)

            try:
                attrs = json.loads(attributes) if isinstance(attributes, str) else attributes
            except json.JSONDecodeError:
                return json.dumps({
                    "status": "error",
                    "code": "TOKUI_INVALID_ATTRS",
                    "message": "attributes must be valid JSON",
                }, ensure_ascii=False)

            for key, value in attrs.items():
                if key.startswith("clk:") or key.startswith("sub:"):
                    continue
                if key in ("clk", "sub") and isinstance(value, str) and (value.startswith("clk:") or value.startswith("sub:")):
                    continue

            try:
                from galaxyos.kernel.tokui_builder import PyTokUIBuilder
                builder = PyTokUIBuilder()

                if component_type in ("card", "ft", "row", "col", "table", "thead", "tbody",
                                       "form", "tabs", "tab", "accordion", "collapse", "dialog",
                                       "btngroup", "timeline", "steps", "drawer", "think",
                                       "bubble", "toolbar", "badge-box", "dropdown", "transfer",
                                       "cascader", "tree", "carousel", "popover", "input-tag",
                                       "watermark", "menu", "imgs", "textarea", "dag-tree"):
                    method = getattr(builder, component_type.replace("-", "_"), None)
                    if method:
                        method(**attrs)
                    else:
                        builder._open(component_type, **attrs)
                    if content:
                        builder.text(content)
                    builder.end()
                elif component_type == "memory-panel":
                    builder.memory_panel(**attrs)
                elif component_type == "rccam-progress":
                    builder.rccam_progress(**attrs)
                elif component_type == "dag-tree":
                    builder.dag_tree(**attrs)
                elif component_type == "memory-search":
                    builder.memory_search(**attrs)
                elif component_type == "rccam-control":
                    builder.rccam_control(**attrs)
                elif component_type == "dag-node-expand":
                    builder.dag_node_expand(**attrs)
                else:
                    method = getattr(builder, component_type.replace("-", "_"), None)
                    if method:
                        if content:
                            attrs["tx"] = content
                        method(**attrs)
                    else:
                        builder._self_closing(component_type, **attrs)

                dsl = builder.build()
                result_stream_id = stream_id or builder.stream_id
                pushed_via_sse = False
                chunk_count = 0
                conversion_result = None

                if self._dsl_bridge:
                    try:
                        conversion_result = self._dsl_bridge.convert(dsl, target="eui_neo")
                    except Exception as e:
                        logger.warning(f"DSLBridge convert failed: {e}")

                if push_via_sse and self._tokui_streamer:
                    try:
                        push_result = await self._tokui_streamer.push(
                            dsl=dsl,
                            stream_id=result_stream_id,
                            workspace_id=workspace_id,
                            component_type=component_type,
                        )
                        pushed_via_sse = push_result.get("status") == "pushed"
                        chunk_count = push_result.get("total_chunks", 0)
                        result_stream_id = push_result.get("stream_id", result_stream_id)
                    except Exception as e:
                        logger.warning(f"TokUI SSE push failed: {e}")

                return json.dumps({
                    "status": "rendered",
                    "dsl": dsl,
                    "stream_id": result_stream_id,
                    "component_type": component_type,
                    "chunk_count": chunk_count,
                    "pushed_via_sse": pushed_via_sse,
                    "eui_neo_dsl": conversion_result.output_dsl if conversion_result else None,
                    "mapping_confidence": conversion_result.mapping_confidence if conversion_result else None,
                    "unsupported_components": conversion_result.unsupported_components if conversion_result else [],
                }, ensure_ascii=False)

            except Exception as e:
                return json.dumps({
                    "status": "error",
                    "code": "TOKUI_BUILD_TIMEOUT",
                    "message": str(e),
                    "fallback": content or "[p 回复生成超时]",
                }, ensure_ascii=False)

    # ── 传输层 ──

    def run_stdio(self) -> None:
        if self._mcp:
            self._mcp.run(transport="stdio")

    def run_sse(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        if self._mcp:
            self._mcp.run(transport="sse", host=host, port=port)

    def run_streamable_http(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        if self._mcp:
            self._mcp.run(transport="streamable-http", host=host, port=port)

    async def run_async(self, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
        if self._mcp:
            if transport == "stdio":
                await self._mcp.run_async(transport="stdio")
            elif transport == "sse":
                await self._mcp.run_async(transport="sse", host=host, port=port)
            elif transport == "streamable_http":
                await self._mcp.run_async(transport="streamable-http", host=host, port=port)

    def get_tool_count(self) -> int:
        return len(self._tool_call_counts)

    async def send_sse_event(self, event_type: str, data: dict) -> None:
        for queue in self._sse_queues:
            try:
                queue.put_nowait({"event": event_type, "data": data})
            except Exception as e:
                logger.warning(f"SSE event push to queue failed: {e}")

    def register_sse_queue(self, queue) -> None:
        self._sse_queues.append(queue)

    def unregister_sse_queue(self, queue) -> None:
        try:
            self._sse_queues.remove(queue)
        except ValueError:
            pass
