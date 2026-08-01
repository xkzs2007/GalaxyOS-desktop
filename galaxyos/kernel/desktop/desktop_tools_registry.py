from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from galaxyos.kernel.desktop.sys_operation_adapter import SysOperationMcpAdapter
from galaxyos.kernel.desktop.clipboard_tools import desktop_clipboard_read, desktop_clipboard_write
from galaxyos.kernel.desktop.window_tools import desktop_window_list, desktop_window_focus, desktop_window_screenshot
from galaxyos.kernel.desktop.system_tools import desktop_system_info, desktop_process_list, desktop_process_kill
from galaxyos.kernel.desktop.app_tools import desktop_app_launch
from galaxyos.kernel.desktop.schedule_tools import desktop_schedule_task

logger = logging.getLogger(__name__)


class DesktopToolsRegistry:
    def __init__(self) -> None:
        self._sys_op_adapter = SysOperationMcpAdapter()
        self._tool_status: Dict[str, str] = {}
        self._all_tools: List[Tuple[str, Callable]] = []

    async def initialize(self, workspace_root: Optional[str] = None) -> Dict[str, Any]:
        sys_op_ok = await self._sys_op_adapter.initialize(workspace_root=workspace_root)

        if sys_op_ok:
            sys_tools = self._sys_op_adapter.adapt_tools()
            self._all_tools.extend(sys_tools)
            for name, _ in sys_tools:
                self._tool_status[name] = "available"
            logger.info(f"SysOperation tools registered: {len(sys_tools)}")
        else:
            logger.warning("SysOperation unavailable, skipping SysOperation-based tools")
            for name in ["desktop_fs_read", "desktop_fs_write", "desktop_fs_list",
                         "desktop_fs_search", "desktop_fs_move", "desktop_fs_delete",
                         "desktop_shell_exec", "desktop_shell_exec_stream", "desktop_shell_background",
                         "desktop_code_exec"]:
                self._tool_status[name] = "disabled"

        custom_tools = self._get_custom_tools()
        self._all_tools.extend(custom_tools)
        for name, _ in custom_tools:
            self._tool_status[name] = "available"

        logger.info(f"Desktop tools registry initialized: {len(self._all_tools)} tools ({sum(1 for v in self._tool_status.values() if v == 'available')} available)")
        return {"total": len(self._all_tools), "available": sum(1 for v in self._tool_status.values() if v == "available"), "disabled": sum(1 for v in self._tool_status.values() if v == "disabled")}

    def _get_custom_tools(self) -> List[Tuple[str, Callable]]:
        return [
            ("desktop_clipboard_read", desktop_clipboard_read),
            ("desktop_clipboard_write", desktop_clipboard_write),
            ("desktop_window_list", desktop_window_list),
            ("desktop_window_focus", desktop_window_focus),
            ("desktop_window_screenshot", desktop_window_screenshot),
            ("desktop_system_info", desktop_system_info),
            ("desktop_process_list", desktop_process_list),
            ("desktop_process_kill", desktop_process_kill),
            ("desktop_app_launch", desktop_app_launch),
            ("desktop_schedule_task", desktop_schedule_task),
        ]

    def get_all_tools(self) -> List[Tuple[str, Callable]]:
        return self._all_tools

    def get_tool_status(self) -> Dict[str, str]:
        return dict(self._tool_status)

    def register_to_mcp_server(self, mcp_server: Any) -> None:
        for tool_name, tool_func in self._all_tools:
            try:
                tool_func.__name__ = tool_name
                mcp_server._mcp.tool()(tool_func)
                logger.debug(f"Registered desktop tool to MCP Server: {tool_name}")
            except Exception as e:
                logger.warning(f"Failed to register {tool_name} to MCP Server: {e}")
                self._tool_status[tool_name] = "error"

    async def register_to_agent(self, bridge: Any) -> None:
        if not bridge or not hasattr(bridge, '_deep_agent') or not bridge._deep_agent:
            logger.warning("Bridge or DeepAgent not available, skipping agent registration")
            return

        agent = bridge._deep_agent
        if not hasattr(agent, 'ability_manager'):
            logger.warning("DeepAgent has no ability_manager, skipping agent registration")
            return

        try:
            from openjiuwen.core.foundation.tool.base import ToolCard
            from openjiuwen.core.foundation.tool.function.function import LocalFunction
        except ImportError:
            logger.warning("openjiuwen tool classes not available, skipping agent registration")
            return

        for tool_name, tool_func in self._all_tools:
            try:
                card = ToolCard(
                    name=tool_name,
                    description=f"GalaxyOS desktop tool: {tool_name}",
                    input_params={"type": "object", "properties": {}},
                    stateless=True,
                )
                tool_instance = LocalFunction(card=card, func=tool_func)
                agent.ability_manager.add_ability(card, tool_instance)
                logger.debug(f"Registered desktop tool to DeepAgent: {tool_name}")
            except Exception as e:
                logger.warning(f"Failed to register {tool_name} to DeepAgent: {e}")
                try:
                    from openjiuwen.core.foundation.tool.base import ToolCard
                    card = ToolCard(
                        name=tool_name,
                        description=f"GalaxyOS desktop tool: {tool_name}",
                        input_params={"type": "object", "properties": {}},
                        stateless=True,
                    )
                    agent.ability_manager.add(card)
                except Exception as e2:
                    logger.warning(f"add() also failed for {tool_name}: {e2}")
