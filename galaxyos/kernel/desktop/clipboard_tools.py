from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

_pyperclip_available = False
try:
    import pyperclip
    _pyperclip_available = True
except ImportError:
    pass


async def desktop_clipboard_read() -> str:
    t0 = time.time()
    if not _pyperclip_available:
        return json.dumps({"success": False, "error": "Clipboard access unavailable (pyperclip not installed)", "tool_name": "desktop_clipboard_read", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    try:
        content = pyperclip.paste()
        return json.dumps({"success": True, "data": {"content": content}, "tool_name": "desktop_clipboard_read", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_clipboard_read", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)


async def desktop_clipboard_write(content: str) -> str:
    t0 = time.time()
    if not _pyperclip_available:
        return json.dumps({"success": False, "error": "Clipboard access unavailable (pyperclip not installed)", "tool_name": "desktop_clipboard_write", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    try:
        pyperclip.copy(content)
        return json.dumps({"success": True, "data": {"written": True}, "tool_name": "desktop_clipboard_write", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_clipboard_write", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
