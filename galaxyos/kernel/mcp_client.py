"""
GalaxyOS MCP Client — JiuwenSwarm Gateway 端 MCP 客户端

连接 GalaxyOS Python 内核 MCP Server，提供工具发现和调用能力：
  - 支持 stdio / SSE / streamable_http 三种传输方式
  - 工具发现（list_tools）
  - 工具调用（call_tool）
  - 认证（Bearer Token）
  - 连接管理（重连、超时）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MCPToolInfo:
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    policy: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolCallResult:
    status: str
    data: Any = None
    error: str = ""
    latency_ms: float = 0


class GalaxyOSMCPClient:
    CONNECT_TIMEOUT = 10.0
    CALL_TIMEOUT = 30.0
    RECONNECT_MAX = 3
    RECONNECT_DELAY_BASE = 2.0

    def __init__(
        self,
        transport: str = "streamable_http",
        host: str = "127.0.0.1",
        port: int = 8765,
        auth_token: str = "",
        command: str = "",
        args: Optional[List[str]] = None,
    ):
        self._transport = transport
        self._host = host
        self._port = port
        self._auth_token = auth_token
        self._command = command
        self._args = args or []
        self._client = None
        self._connected = False
        self._tools: Dict[str, MCPToolInfo] = {}
        self._call_counts: Dict[str, int] = {}
        self._reconnect_count = 0

    async def connect(self) -> bool:
        try:
            if self._transport == "stdio":
                return await self._connect_stdio()
            elif self._transport in ("sse", "streamable_http"):
                return await self._connect_http()
            else:
                logger.error(f"Unknown transport: {self._transport}")
                return False
        except Exception as e:
            logger.error(f"MCP client connect failed: {e}")
            return False

    async def disconnect(self) -> None:
        if self._client:
            try:
                if hasattr(self._client, "disconnect"):
                    await self._client.disconnect()
                elif hasattr(self._client, "close"):
                    await self._client.close()
            except Exception as e:
                logger.warning(f"MCP client disconnect error: {e}")
            finally:
                self._client = None
                self._connected = False

    async def list_tools(self) -> List[MCPToolInfo]:
        if not self._connected:
            await self.connect()

        if self._tools:
            return list(self._tools.values())

        try:
            if self._client and hasattr(self._client, "list_tools"):
                result = await asyncio.wait_for(
                    self._client.list_tools(),
                    timeout=self.CONNECT_TIMEOUT,
                )
                for tool in result:
                    info = MCPToolInfo(
                        name=tool.name if hasattr(tool, "name") else str(tool),
                        description=tool.description if hasattr(tool, "description") else "",
                        input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                    )
                    self._tools[info.name] = info
                return list(self._tools.values())
        except Exception as e:
            logger.warning(f"list_tools failed: {e}")

        return self._get_default_tools()

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any] = None) -> MCPToolCallResult:
        start = time.monotonic()
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1

        if not self._connected:
            reconnected = await self._try_reconnect()
            if not reconnected:
                return MCPToolCallResult(status="error", error="Not connected to MCP server")

        try:
            if self._client and hasattr(self._client, "call_tool"):
                result = await asyncio.wait_for(
                    self._client.call_tool(tool_name, arguments or {}),
                    timeout=self.CALL_TIMEOUT,
                )
                latency = (time.monotonic() - start) * 1000

                if isinstance(result, str):
                    try:
                        data = json.loads(result)
                    except json.JSONDecodeError:
                        data = result
                else:
                    data = result

                return MCPToolCallResult(status="success", data=data, latency_ms=latency)
        except asyncio.TimeoutError:
            return MCPToolCallResult(status="timeout", error=f"Tool call timed out ({self.CALL_TIMEOUT}s)")
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return MCPToolCallResult(status="error", error=str(e), latency_ms=latency)

        return MCPToolCallResult(status="error", error="No MCP client available")

    async def _connect_stdio(self) -> bool:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            server_params = StdioServerParameters(
                command=self._command or "python",
                args=self._args or ["-m", "galaxyos.kernel.mcp_server"],
            )

            read_stream, write_stream = await asyncio.wait_for(
                stdio_client(server_params),
                timeout=self.CONNECT_TIMEOUT,
            )
            self._client = ClientSession(read_stream, write_stream)
            await self._client.initialize()
            self._connected = True
            self._reconnect_count = 0
            return True
        except ImportError:
            logger.warning("mcp package not available, using HTTP fallback")
            return await self._connect_http()
        except Exception as e:
            logger.error(f"stdio connect failed: {e}")
            return False

    async def _connect_http(self) -> bool:
        try:
            import aiohttp

            url = f"http://{self._host}:{self._port}/mcp"
            headers = {}
            if self._auth_token:
                headers["Authorization"] = f"Bearer {self._auth_token}"

            session = aiohttp.ClientSession(
                base_url=f"http://{self._host}:{self._port}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.CONNECT_TIMEOUT),
            )

            async with session.post("/mcp", json={"method": "initialize", "params": {}}) as resp:
                if resp.status == 200:
                    self._client = session
                    self._connected = True
                    self._reconnect_count = 0
                    return True
                else:
                    await session.close()
                    return False
        except Exception as e:
            logger.error(f"HTTP connect failed: {e}")
            return False

    async def _try_reconnect(self) -> bool:
        if self._reconnect_count >= self.RECONNECT_MAX:
            return False

        self._reconnect_count += 1
        backoff = min(self.RECONNECT_DELAY_BASE ** self._reconnect_count, 10)

        logger.info(f"Reconnecting MCP client (attempt {self._reconnect_count}/{self.RECONNECT_MAX}) in {backoff}s")
        await asyncio.sleep(backoff)

        await self.disconnect()
        return await self.connect()

    def _get_default_tools(self) -> List[MCPToolInfo]:
        default_tools = [
            "galaxy_pool", "claw_rccam_progress", "claw_recall", "claw_lobster",
            "claw_health", "claw_vector_info", "claw_events", "claw_store",
            "claw_verify", "claw_rccam", "claw_save_memory", "claw_compile_skill",
            "claw_asset_search", "claw_asset_register", "claw_node_invoke",
            "skill_execute", "skill_install", "skill_discover", "skill_compile",
            "llm_call", "tokui_render",
        ]
        return [MCPToolInfo(name=name) for name in default_tools]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "transport": self._transport,
            "host": self._host,
            "port": self._port,
            "tools_discovered": len(self._tools),
            "total_calls": sum(self._call_counts.values()),
            "call_counts": self._call_counts,
            "reconnect_count": self._reconnect_count,
        }
