"""
GalaxyOS MCP Server 入口 — 启动 MCP Server 并注册全部 24 个工具

用法：
  python -m galaxyos.kernel.mcp_server_entry [--transport stdio|sse|streamable_http] [--host 127.0.0.1] [--port 8765]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

REQUIRED_DEPS = ["fastmcp", "numpy", "pydantic"]


def check_dependencies() -> list[str]:
    missing = []
    for dep in REQUIRED_DEPS:
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)
    return missing


def create_kernel():
    from galaxyos.kernel.liquid_memory_adapter import LiquidMemoryAdapter
    from galaxyos.kernel.dag_context_fusion import DAGContextFusion
    from galaxyos.kernel.memory_sync_bridge import MemorySyncBridge
    from galaxyos.kernel.rccam_injector import RCCAMInjector
    from galaxyos.kernel.agent_core_bridge import AgentCoreBridge
    from galaxyos.kernel.tokui_builder import PyTokUIBuilder
    from galaxyos.kernel.tokui_streamer import TokUIStreamer
    from galaxyos.kernel.mcp_server import GalaxyOSMCPServer
    from galaxyos.kernel.desktop.desktop_tools_registry import DesktopToolsRegistry
    from galaxyos.kernel.i18n_manager import I18nManager

    memory_adapter = LiquidMemoryAdapter()
    dag_fusion = DAGContextFusion()
    memory_bridge = MemorySyncBridge(
        liquid_memory_adapter=memory_adapter,
        dag_context_fusion=dag_fusion,
    )

    bridge_config = {
        "provider": "",
        "api_key": "",
        "api_base": "",
        "model": "",
        "workspace": "./",
        "skills_dir": "",
        "permissions": {},
        "workspace_root": "",
        "intelli_router": {},
    }

    bridge = AgentCoreBridge(config=bridge_config)

    server = GalaxyOSMCPServer(
        bridge=bridge,
        memory_bridge=memory_bridge,
        tokui_builder=PyTokUIBuilder,
        tokui_streamer=None,
    )

    tokui_streamer = TokUIStreamer(mcp_server=server)

    rccam = RCCAMInjector(
        memory_adapter=memory_adapter,
        dag_fusion=dag_fusion,
        tokui_builder=PyTokUIBuilder,
        tokui_streamer=tokui_streamer,
        memory_sync_bridge=memory_bridge,
    )

    server._tokui_streamer = tokui_streamer

    desktop_registry = DesktopToolsRegistry()

    i18n_manager = I18nManager()
    try:
        i18n_manager.load("zh")
        i18n_manager.load("en")
        logger.info("I18n manager initialized with zh/en translations")
    except Exception as e:
        logger.warning(f"I18n manager initialization failed: {e}")

    return server, bridge, memory_bridge, rccam, desktop_registry, tokui_streamer, i18n_manager


def mount_health_endpoint(server, port: int):
    try:
        import time as _time
        _start_time = _time.time()

        if server._mcp is not None:
            @server._mcp.custom_route("/health", methods=["GET"])
            async def health_handler(request):
                from starlette.responses import JSONResponse
                return JSONResponse({
                    "status": "healthy",
                    "uptime_s": round(_time.time() - _start_time, 1),
                    "kernel": "running",
                })
            logger.info("/health endpoint mounted on MCP server via custom_route")
        else:
            logger.warning("MCP server not created, /health endpoint not mounted")
    except Exception as e:
        logger.warning(f"/health endpoint mount failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="GalaxyOS MCP Server")
    parser.add_argument("--transport", default="streamable_http", choices=["stdio", "sse", "streamable_http"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    missing = check_dependencies()
    if missing:
        logger.error(f"Missing dependencies: {', '.join(missing)}")
        logger.error(f"Install with: pip install {' '.join(missing)}")
        sys.exit(1)

    logger.info(f"Starting GalaxyOS MCP Server: transport={args.transport}, host={args.host}, port={args.port}")

    server, bridge, memory_bridge, rccam, desktop_registry, tokui_streamer, i18n_manager = create_kernel()

    import asyncio as _asyncio
    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(bridge.initialize())
        logger.info("AgentCoreBridge initialized")
    except Exception as e:
        logger.error(f"AgentCoreBridge initialize failed: {e}")

    try:
        agent = loop.run_until_complete(bridge.create_agent())
        if agent:
            logger.info("DeepAgent created")
        else:
            logger.warning("DeepAgent creation returned None (openjiuwen may not be available)")
    except Exception as e:
        logger.error(f"DeepAgent creation failed: {e}")

    try:
        inject_result = loop.run_until_complete(bridge.inject_cognitive_tools(mcp_server=server))
        logger.info(f"Cognitive tools injected: {inject_result.get('status', 'unknown')}")
    except Exception as e:
        logger.error(f"Cognitive tools injection failed: {e}")

    try:
        from galaxyos.kernel.rccam_rail import RCCAMRail
        rccam_rail = RCCAMRail(injector=rccam)
        if bridge._deep_agent and hasattr(bridge._deep_agent, 'rails'):
            bridge._deep_agent.rails.append(rccam_rail)
            logger.info("RCCAMRail registered to DeepAgent rails")
        else:
            logger.warning("DeepAgent not available or has no rails attribute, RCCAMRail not registered")
    except Exception as e:
        logger.error(f"RCCAMRail registration failed: {e}")

    try:
        loop.run_until_complete(desktop_registry.initialize())
        logger.info("Desktop tools initialized")
    except Exception as e:
        logger.error(f"Desktop tools initialization failed: {e}")

    try:
        loop.run_until_complete(desktop_registry.register_to_agent(bridge))
        logger.info("Desktop tools registered to agent")
    except Exception as e:
        logger.error(f"Desktop tools agent registration failed: {e}")

    server.create()

    try:
        desktop_registry.register_to_mcp_server(server)
        logger.info("Desktop tools registered to MCP Server")
    except Exception as e:
        logger.error(f"Desktop tools MCP Server registration failed: {e}")

    tool_count = len(server._mcp._tool_manager._tools) if server._mcp else 0
    logger.info(f"GalaxyOS MCP Server created: {tool_count} tools registered")


    if args.transport == "stdio":
        server.run_stdio()
    elif args.transport in ("sse", "streamable_http"):
        import uvicorn
        path_prefix = "/sse" if args.transport == "sse" else "/mcp"
        app = server._mcp.http_app(path=path_prefix)

        from starlette.responses import JSONResponse
        from starlette.routing import Route

        _start_time = time.time()
        _pending_permissions: dict[str, asyncio.Event] = {}
        _permission_results: dict[str, bool] = {}

        async def localhost_only_middleware(request, call_next):
            client_host = request.client.host if request.client else ""
            if client_host not in ("127.0.0.1", "::1", "localhost"):
                return JSONResponse({"error": "Forbidden: localhost only"}, status_code=403)
            response = await call_next(request)
            return response

        app.middleware("http")(localhost_only_middleware)

        async def health_handler(request):
            desktop_status = desktop_registry.get_tool_status()
            return JSONResponse({
                "status": "healthy",
                "uptime_s": round(time.time() - _start_time, 1),
                "kernel": "running",
                "agent_core_available": bridge._openjiuwen_available,
                "sys_operation_available": desktop_registry._sys_op_adapter.available,
                "desktop_tools": desktop_status,
            })

        app.routes.insert(0, Route("/health", health_handler))

        async def agent_chat_sse(request):
            from starlette.responses import StreamingResponse
            import asyncio
            import json as _json

            workspace_id = request.query_params.get("workspace_id", "")
            message = request.query_params.get("message", "")
            if not workspace_id:
                from starlette.responses import JSONResponse as _JR
                return _JR({"error": "workspace_id is required"}, status_code=400)

            session_key = f"sess-{int(time.time() * 1000)}"

            async def event_generator():
                chunk_index = 0
                try:
                    if bridge._openjiuwen_available and bridge._deep_agent:
                        agent = bridge._deep_agent
                        if hasattr(agent, 'chat'):
                            async for chunk in agent.chat(workspace_id=workspace_id, message=message):
                                chunk_index += 1
                                if isinstance(chunk, str):
                                    yield f"event: text\ndata: {_json.dumps({'type': 'text', 'content': chunk, 'session_key': session_key, 'chunk_index': chunk_index}, ensure_ascii=False)}\n\n"
                                elif isinstance(chunk, dict):
                                    chunk_type = chunk.get('type', 'text')
                                    if chunk_type == 'ask_user':
                                        request_id = chunk.get('request_id', '')
                                        evt = _asyncio.Event()
                                        _pending_permissions[request_id] = evt
                                        yield f"event: ask_user\ndata: {_json.dumps({'type': 'ask_user', 'request_id': request_id, 'tool_name': chunk.get('tool_name', ''), 'tool_args': chunk.get('tool_args', {}), 'risk_level': chunk.get('risk_level', 'medium'), 'timeout': 60, 'session_key': session_key, 'chunk_index': chunk_index}, ensure_ascii=False)}\n\n"
                                        try:
                                            await _asyncio.wait_for(evt.wait(), timeout=60)
                                            approved = _permission_results.pop(request_id, False)
                                            _pending_permissions.pop(request_id, None)
                                        except _asyncio.TimeoutError:
                                            approved = False
                                            _pending_permissions.pop(request_id, None)
                                        chunk_index += 1
                                        yield f"event: permission_result\ndata: {_json.dumps({'type': 'permission_result', 'request_id': request_id, 'approved': approved, 'session_key': session_key, 'chunk_index': chunk_index}, ensure_ascii=False)}\n\n"
                                    elif chunk_type == 'tool_result':
                                        yield f"event: tool_result\ndata: {_json.dumps({'type': 'tool_result', 'tool_name': chunk.get('tool_name', ''), 'result': chunk.get('result', ''), 'session_key': session_key, 'chunk_index': chunk_index}, ensure_ascii=False)}\n\n"
                                    elif chunk_type == 'tokui_dsl':
                                        yield f"event: tokui_dsl\ndata: {_json.dumps({'type': 'tokui_dsl', 'dsl': chunk.get('dsl', chunk.get('content', '')), 'session_key': session_key, 'chunk_index': chunk_index}, ensure_ascii=False)}\n\n"
                                    else:
                                        content = chunk.get('content', chunk.get('text', str(chunk)))
                                        yield f"event: text\ndata: {_json.dumps({'type': 'text', 'content': content, 'session_key': session_key, 'chunk_index': chunk_index}, ensure_ascii=False)}\n\n"

                        yield f"event: agent_done\ndata: {_json.dumps({'type': 'done', 'session_key': session_key, 'is_final': True, 'chunk_index': chunk_index + 1}, ensure_ascii=False)}\n\n"
                    else:
                        yield f"event: error\ndata: {_json.dumps({'type': 'error', 'error': 'Agent core not available', 'session_key': session_key}, ensure_ascii=False)}\n\n"
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Agent chat SSE error: {e}")
                    yield f"event: error\ndata: {_json.dumps({'type': 'error', 'error': str(e), 'session_key': session_key}, ensure_ascii=False)}\n\n"

            return StreamingResponse(event_generator(), media_type="text/event-stream", headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            })

        async def permission_respond(request):
            try:
                body = await request.json()
                request_id = body.get("request_id", "")
                approved = body.get("approved", False)
                if request_id in _pending_permissions:
                    _permission_results[request_id] = approved
                    _pending_permissions[request_id].set()
                    return JSONResponse({"status": "ok", "request_id": request_id, "approved": approved})
                return JSONResponse({"status": "not_found", "request_id": request_id}, status_code=404)
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=400)

        app.routes.insert(0, Route("/agent-chat", agent_chat_sse))
        app.routes.insert(0, Route("/permission-respond", permission_respond, methods=["POST"]))

        async def dsl_convert_handler(request):
            tokui_dsl = request.query_params.get("tokui_dsl", "")
            if not tokui_dsl:
                return JSONResponse({"error": "tokui_dsl parameter is required"}, status_code=400)
            try:
                from galaxyos.kernel.dsl_bridge import DSLBridge
                bridge_instance = DSLBridge()
                result = bridge_instance.tokui_to_eui(tokui_dsl)
                return JSONResponse({
                    "output_dsl": result.output_dsl,
                    "mapping_confidence": result.mapping_confidence,
                    "unsupported_components": result.unsupported_components,
                    "dropped_attrs": result.dropped_attrs,
                    "conversion_time_ms": getattr(result, "conversion_time_ms", 0),
                })
            except Exception as e:
                logger.error(f"DSL convert error: {e}")
                return JSONResponse({"error": str(e)}, status_code=500)

        app.routes.insert(0, Route("/dsl/convert", dsl_convert_handler))

        logger.info(f"/health, /agent-chat, /permission-respond, /dsl/convert endpoints mounted, {args.transport} on http://{args.host}:{args.port}{path_prefix}")
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
