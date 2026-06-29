"""mcp_client.py — MCP (Model Context Protocol) server discovery + tool merge.

Reads ``~/.galaxyos/mcp.json`` and connects to configured MCP
servers via stdio. Discovers their ``tools/list`` and merges them
into the ``tools.TOOLS`` registry so the Agent can call them.

Config format (``~/.galaxyos/mcp.json``)::

    {
      "servers": [
        {
          "name": "filesystem",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        }
      ]
    }

For Stage 10.2 this is a lightweight stdio-only implementation —
it spawns each MCP server as a subprocess, sends JSON-RPC
``initialize`` + ``tools/list``, and caches the results.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("galaxyos-mcp")


def config_path() -> Path:
    """Return the MCP config file path."""
    return Path.home() / ".galaxyos" / "mcp.json"


def load_config() -> Dict[str, Any]:
    """Load the MCP config. Returns empty dict if not found."""
    p = config_path()
    if not p.exists():
        return {"servers": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to load MCP config: %s", e)
        return {"servers": []}


def save_config(config: Dict[str, Any]) -> None:
    """Save the MCP config."""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def list_servers() -> List[Dict[str, Any]]:
    """Return the configured server list."""
    return load_config().get("servers", [])


def add_server(name: str, command: str, args: List[str] = None, env: Dict[str, str] = None) -> Dict[str, Any]:
    """Add a server to the config."""
    config = load_config()
    servers = config.setdefault("servers", [])
    # Remove existing with same name
    servers[:] = [s for s in servers if s.get("name") != name]
    entry = {"name": name, "command": command, "args": args or []}
    if env:
        entry["env"] = env
    servers.append(entry)
    save_config(config)
    return entry


def remove_server(name: str) -> bool:
    """Remove a server. Returns True if found."""
    config = load_config()
    servers = config.get("servers", [])
    before = len(servers)
    servers[:] = [s for s in servers if s.get("name") != name]
    if len(servers) < before:
        save_config(config)
        return True
    return False


async def _discover_tools_stdio(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Connect to a stdio MCP server and get its tools/list.

    Spawns the server process, sends JSON-RPC initialize + tools/list,
    then closes. Returns the tool definitions.
    """
    cmd = server.get("command", "")
    args = server.get("args", [])
    env = {**os.environ, **server.get("env", {})}
    if not cmd:
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except Exception as e:
        log.warning("MCP server '%s' failed to start: %s", server.get("name"), e)
        return []

    async def _send(msg: dict) -> Optional[dict]:
        line = json.dumps(msg) + "\n"
        proc.stdin.write(line.encode())
        await proc.stdin.drain()
        # Read one line back
        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
        if not raw:
            return None
        return json.loads(raw.decode().strip())

    try:
        # Initialize
        resp = await _send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "galaxyos-desktop", "version": "0.1"}},
        })
        if not resp or "error" in resp:
            log.warning("MCP '%s' initialize failed: %s", server.get("name"), resp)
            return []
        # Send initialized notification
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
        await proc.stdin.drain()
        # List tools
        resp = await _send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        if not resp or "error" in resp:
            log.warning("MCP '%s' tools/list failed: %s", server.get("name"), resp)
            return []
        tools = resp.get("result", {}).get("tools", [])
        log.info("MCP '%s' discovered %d tools", server.get("name"), len(tools))
        return tools
    except asyncio.TimeoutError:
        log.warning("MCP '%s' timed out", server.get("name"))
        return []
    except Exception as e:
        log.warning("MCP '%s' discovery error: %s", server.get("name"), e)
        return []
    finally:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass


def discover_all() -> Dict[str, List[Dict[str, Any]]]:
    """Discover tools from all configured MCP servers.

    Returns {server_name: [tool_defs...]}.
    """
    servers = list_servers()
    if not servers:
        return {}
    results = {}
    for srv in servers:
        try:
            loop = asyncio.new_event_loop()
            try:
                tools = loop.run_until_complete(_discover_tools_stdio(srv))
            finally:
                loop.close()
            if tools:
                results[srv["name"]] = tools
        except Exception as e:
            log.warning("MCP discovery for '%s' failed: %s", srv.get("name"), e)
    return results


def merge_into_registry() -> int:
    """Discover all MCP tools and merge them into tools.TOOLS.

    Returns the number of new tools added.
    """
    import tools
    discovered = discover_all()
    count = 0
    for server_name, tool_list in discovered.items():
        for td in tool_list:
            tool_name = f"mcp_{server_name}_{td.get('name', 'unknown')}"
            if tool_name not in tools.TOOLS:
                # Create a thin wrapper that we can't actually call
                # yet (full MCP call support requires a persistent
                # connection, not the discover-then-close pattern).
                # For now, register the tool metadata so it shows up
                # in the tools list.
                tools.TOOLS[tool_name] = {
                    "fn": _make_mcp_stub(tool_name, td),
                    "description": f"[MCP/{server_name}] {td.get('description', td.get('name', ''))}",
                    "params": {k: v.get("description", "") for k, v in
                               (td.get("inputSchema", {}).get("properties", {})).items()},
                }
                count += 1
    if count:
        log.info("MCP: merged %d tools into registry", count)
    return count


def _make_mcp_stub(name: str, tool_def: dict):
    """Create a stub async function for an MCP tool.

    For Stage 10.2 this just logs that the tool was called —
    actual MCP tool invocation requires a persistent server
    connection, which is a Stage 10.3 enhancement.
    """
    async def _stub(**kwargs):
        log.info("MCP tool '%s' called with %s (stub — not yet connected)", name, list(kwargs.keys()))
        return {
            "ok": True,
            "output": f"[MCP tool {name} — stub response. Connect to server for real execution.]",
            "note": "MCP tool execution requires a persistent server connection (Stage 10.3)",
        }
    return _stub
