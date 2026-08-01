from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SHELL_WHITELIST: List[str] = [
    "dir", "ls", "cat", "type", "echo", "where", "which",
    "python", "node", "git status", "git log", "pip list",
]


@dataclass
class DesktopPermissionRule:
    tool_name: str
    default: str = "ASK"
    workspace_inside: Optional[str] = None
    allowlist: Optional[List[str]] = None


DESKTOP_PERMISSION_RULES: Dict[str, DesktopPermissionRule] = {
    "desktop_fs_read": DesktopPermissionRule(tool_name="desktop_fs_read", default="ALLOW"),
    "desktop_fs_write": DesktopPermissionRule(tool_name="desktop_fs_write", default="ASK", workspace_inside="ALLOW"),
    "desktop_fs_delete": DesktopPermissionRule(tool_name="desktop_fs_delete", default="DENY", workspace_inside="ASK"),
    "desktop_fs_list": DesktopPermissionRule(tool_name="desktop_fs_list", default="ALLOW"),
    "desktop_fs_search": DesktopPermissionRule(tool_name="desktop_fs_search", default="ALLOW"),
    "desktop_fs_move": DesktopPermissionRule(tool_name="desktop_fs_move", default="ASK"),
    "desktop_shell_exec": DesktopPermissionRule(tool_name="desktop_shell_exec", default="ASK", allowlist=SHELL_WHITELIST),
    "desktop_shell_exec_stream": DesktopPermissionRule(tool_name="desktop_shell_exec_stream", default="ASK", allowlist=SHELL_WHITELIST),
    "desktop_shell_background": DesktopPermissionRule(tool_name="desktop_shell_background", default="ASK"),
    "desktop_code_exec": DesktopPermissionRule(tool_name="desktop_code_exec", default="ASK"),
    "desktop_clipboard_read": DesktopPermissionRule(tool_name="desktop_clipboard_read", default="ALLOW"),
    "desktop_clipboard_write": DesktopPermissionRule(tool_name="desktop_clipboard_write", default="ALLOW"),
    "desktop_window_list": DesktopPermissionRule(tool_name="desktop_window_list", default="ALLOW"),
    "desktop_window_focus": DesktopPermissionRule(tool_name="desktop_window_focus", default="ALLOW"),
    "desktop_window_screenshot": DesktopPermissionRule(tool_name="desktop_window_screenshot", default="ALLOW"),
    "desktop_system_info": DesktopPermissionRule(tool_name="desktop_system_info", default="ALLOW"),
    "desktop_process_list": DesktopPermissionRule(tool_name="desktop_process_list", default="ALLOW"),
    "desktop_process_kill": DesktopPermissionRule(tool_name="desktop_process_kill", default="DENY"),
    "desktop_app_launch": DesktopPermissionRule(tool_name="desktop_app_launch", default="ASK"),
    "desktop_schedule_task": DesktopPermissionRule(tool_name="desktop_schedule_task", default="ASK"),
}


def check_permission(tool_name: str, args: Dict[str, Any], workspace_root: Optional[str] = None) -> str:
    rule = DESKTOP_PERMISSION_RULES.get(tool_name)
    if rule is None:
        return "ASK"

    if rule.allowlist:
        command = args.get("command", "")
        for allowed in rule.allowlist:
            if command.strip().lower().startswith(allowed.lower()):
                return "ALLOW"
        return rule.default

    if rule.workspace_inside and workspace_root:
        path = args.get("path", args.get("cwd", ""))
        if path and workspace_root and path.startswith(workspace_root):
            return rule.workspace_inside

    return rule.default
