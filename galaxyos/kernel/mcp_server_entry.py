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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


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


def main():
    parser = argparse.ArgumentParser(description="GalaxyOS MCP Server")
    parser.add_argument("--transport", default="streamable_http", choices=["stdio", "sse", "streamable_http"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    logger.info(f"Starting GalaxyOS MCP Server: transport={args.transport}, host={args.host}, port={args.port}")

    server, bridge, memory_bridge, rccam = create_kernel()
    server.create()

    logger.info(f"GalaxyOS MCP Server created: {server.get_tool_count()} tools registered")

    if args.transport == "stdio":
        server.run_stdio()
    elif args.transport == "sse":
        server.run_sse(host=args.host, port=args.port)
    elif args.transport == "streamable_http":
        server.run_streamable_http(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
