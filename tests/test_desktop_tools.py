from __future__ import annotations

import asyncio
import json
import pytest


def test_permission_config_rules_count():
    from galaxyos.kernel.desktop.permission_config import DESKTOP_PERMISSION_RULES
    assert len(DESKTOP_PERMISSION_RULES) == 20


def test_permission_fs_read_allow():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_fs_read", {}) == "ALLOW"


def test_permission_fs_write_default_ask():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_fs_write", {}) == "ASK"


def test_permission_fs_write_workspace_inside_allow():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_fs_write", {"path": "/home/user/project/file.txt"}, workspace_root="/home/user/project") == "ALLOW"


def test_permission_fs_delete_default_deny():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_fs_delete", {}) == "DENY"


def test_permission_process_kill_deny():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_process_kill", {}) == "DENY"


def test_permission_shell_whitelist_allow():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_shell_exec", {"command": "dir"}) == "ALLOW"
    assert check_permission("desktop_shell_exec", {"command": "git status"}) == "ALLOW"


def test_permission_shell_non_whitelist_ask():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_shell_exec", {"command": "rm -rf /"}) == "ASK"


def test_permission_unknown_tool_ask():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("unknown_tool", {}) == "ASK"


def test_permission_system_info_allow():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_system_info", {}) == "ALLOW"


def test_permission_clipboard_allow():
    from galaxyos.kernel.desktop.permission_config import check_permission
    assert check_permission("desktop_clipboard_read", {}) == "ALLOW"
    assert check_permission("desktop_clipboard_write", {}) == "ALLOW"


def test_shell_whitelist_contents():
    from galaxyos.kernel.desktop.permission_config import SHELL_WHITELIST
    assert "dir" in SHELL_WHITELIST
    assert "ls" in SHELL_WHITELIST
    assert "python" in SHELL_WHITELIST
    assert "git status" in SHELL_WHITELIST
    assert len(SHELL_WHITELIST) == 12


def test_clipboard_read_unavailable():
    from galaxyos.kernel.desktop.clipboard_tools import desktop_clipboard_read
    result = json.loads(asyncio.run(desktop_clipboard_read()))
    if not result["success"]:
        assert "unavailable" in result["error"].lower() or "pyperclip" in result["error"].lower()


def test_window_list_graceful_degradation():
    from galaxyos.kernel.desktop.window_tools import desktop_window_list
    result = json.loads(asyncio.run(desktop_window_list()))
    assert result["success"] is True
    assert isinstance(result["data"], list)


def test_system_info():
    from galaxyos.kernel.desktop.system_tools import desktop_system_info
    result = json.loads(asyncio.run(desktop_system_info()))
    assert result["success"] is True
    assert "platform" in result["data"]


def test_process_kill_requires_confirm():
    from galaxyos.kernel.desktop.system_tools import desktop_process_kill
    result = json.loads(asyncio.run(desktop_process_kill(pid=9999, confirm=False)))
    assert result["success"] is False
    assert "confirm" in result["error"].lower()


def test_app_launch_returns_result():
    from galaxyos.kernel.desktop.app_tools import desktop_app_launch
    result = json.loads(asyncio.run(desktop_app_launch(app_name="nonexistent_app_xyz_12345")))
    assert "success" in result
    assert "tool_name" in result


def test_desktop_tools_registry_init():
    from galaxyos.kernel.desktop.desktop_tools_registry import DesktopToolsRegistry
    registry = DesktopToolsRegistry()
    assert len(registry.get_tool_status()) == 0


def test_desktop_tools_registry_custom_tools():
    from galaxyos.kernel.desktop.desktop_tools_registry import DesktopToolsRegistry
    registry = DesktopToolsRegistry()
    custom = registry._get_custom_tools()
    assert len(custom) == 10
    names = [name for name, _ in custom]
    assert "desktop_clipboard_read" in names
    assert "desktop_system_info" in names
    assert "desktop_app_launch" in names
    assert "desktop_schedule_task" in names


def test_mcp_client_config_default():
    from galaxyos.kernel.desktop.mcp_client_config import McpClientConfig
    config = McpClientConfig(config_path="/nonexistent/path.json")
    servers = config.load()
    assert isinstance(servers, dict)
    assert len(servers) == 0


def test_mcp_client_config_add_remove():
    import tempfile
    import os
    from galaxyos.kernel.desktop.mcp_client_config import McpClientConfig
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        f.write('{"servers": {}}')
        tmp_path = f.name
    try:
        config = McpClientConfig(config_path=tmp_path)
        config.load()
        config.add_server("test_server", {"url": "http://localhost:9000", "transport": "sse"})
        assert "test_server" in config.list_servers()
        config2 = McpClientConfig(config_path=tmp_path)
        config2.load()
        assert "test_server" in config2.list_servers()
        config2.remove_server("test_server")
        assert "test_server" not in config2.list_servers()
    finally:
        os.unlink(tmp_path)


def test_sys_operation_adapter_unavailable():
    from galaxyos.kernel.desktop.sys_operation_adapter import SysOperationMcpAdapter
    adapter = SysOperationMcpAdapter()
    assert adapter.available is False
    assert adapter.adapt_tools() == []
