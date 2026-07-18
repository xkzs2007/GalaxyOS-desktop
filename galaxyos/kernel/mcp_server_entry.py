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
    from galaxyos.kernel.tokui_sse_streamer import TokUISSEStreamer
    from galaxyos.kernel.mcp_server import GalaxyOSMCPServer

    memory_adapter = LiquidMemoryAdapter()
    dag_fusion = DAGContextFusion()
    memory_bridge = MemorySyncBridge(
        liquid_memory_adapter=memory_adapter,
        dag_context_fusion=dag_fusion,
    )
    bridge = AgentCoreBridge()
    tokui_streamer = TokUISSEStreamer()

    rccam = RCCAMInjector(
        memory_adapter=memory_adapter,
        dag_fusion=dag_fusion,
        tokui_builder=PyTokUIBuilder,
        tokui_streamer=tokui_streamer,
    )

    server = GalaxyOSMCPServer(
        bridge=bridge,
        memory_bridge=memory_bridge,
        tokui_builder=PyTokUIBuilder,
        tokui_streamer=tokui_streamer,
    )

    return server, bridge, memory_bridge, rccam


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

    server, bridge, memory_bridge, rccam = create_kernel()
    server.create()

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

        async def health_handler(request):
            return JSONResponse({
                "status": "healthy",
                "uptime_s": round(time.time() - _start_time, 1),
                "kernel": "running",
                "agent_core_available": bridge._openjiuwen_available,
            })

        app.routes.insert(0, Route("/health", health_handler))

        async def agent_chat_sse(request):
            from starlette.responses import StreamingResponse
            import asyncio
            import json as _json

            workspace_id = request.query_params.get("workspace_id", "")
            if not workspace_id:
                from starlette.responses import JSONResponse
                return JSONResponse({"error": "workspace_id is required"}, status_code=400)

            async def event_generator():
                try:
                    if bridge._openjiuwen_available and bridge._deep_agent:
                        yield f"event: agent_chunk\ndata: {_json.dumps({'content': '', 'workspace_id': workspace_id, 'is_final': False}, ensure_ascii=False)}\n\n"

                        agent = bridge._deep_agent
                        if hasattr(agent, 'chat'):
                            async for chunk in agent.chat(workspace_id=workspace_id):
                                if isinstance(chunk, str):
                                    yield f"event: agent_chunk\ndata: {_json.dumps({'content': chunk, 'workspace_id': workspace_id, 'is_final': False}, ensure_ascii=False)}\n\n"
                                elif isinstance(chunk, dict):
                                    content = chunk.get('content', chunk.get('text', str(chunk)))
                                    yield f"event: agent_chunk\ndata: {_json.dumps({'content': content, 'workspace_id': workspace_id, 'is_final': False}, ensure_ascii=False)}\n\n"

                        yield f"event: agent_done\ndata: {_json.dumps({'workspace_id': workspace_id, 'is_final': True}, ensure_ascii=False)}\n\n"
                    else:
                        yield f"event: agent_error\ndata: {_json.dumps({'error': 'Agent core not available', 'workspace_id': workspace_id}, ensure_ascii=False)}\n\n"
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"Agent chat SSE error: {e}")
                    yield f"event: agent_error\ndata: {_json.dumps({'error': str(e), 'workspace_id': workspace_id}, ensure_ascii=False)}\n\n"

            return StreamingResponse(event_generator(), media_type="text/event-stream", headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            })

        app.routes.insert(0, Route("/agent-chat", agent_chat_sse))
        logger.info(f"/health and /agent-chat endpoints mounted, {args.transport} on http://{args.host}:{args.port}{path_prefix}")
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
