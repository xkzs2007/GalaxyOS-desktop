from __future__ import annotations

import base64
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

_pygetwindow_available = False
_pillow_available = False
try:
    import pygetwindow
    _pygetwindow_available = True
except ImportError:
    pass
try:
    from PIL import ImageGrab
    _pillow_available = True
except ImportError:
    pass


async def desktop_window_list() -> str:
    t0 = time.time()
    if not _pygetwindow_available:
        return json.dumps({"success": True, "data": [], "note": "pygetwindow unavailable", "tool_name": "desktop_window_list", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    try:
        windows = pygetwindow.getAllWindows()
        result = [{"title": w.title, "visible": w.visible, "left": w.left, "top": w.top, "width": w.width, "height": w.height} for w in windows if w.title]
        return json.dumps({"success": True, "data": result, "tool_name": "desktop_window_list", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": True, "data": [], "note": f"pygetwindow error: {e}", "tool_name": "desktop_window_list", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)


async def desktop_window_focus(title: str) -> str:
    t0 = time.time()
    if not _pygetwindow_available:
        return json.dumps({"success": False, "error": "pygetwindow unavailable", "tool_name": "desktop_window_focus", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    try:
        windows = pygetwindow.getWindowsWithTitle(title)
        if not windows:
            return json.dumps({"success": False, "error": f"Window '{title}' not found", "tool_name": "desktop_window_focus", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
        windows[0].activate()
        return json.dumps({"success": True, "data": {"title": title, "focused": True}, "tool_name": "desktop_window_focus", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_window_focus", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)


async def desktop_window_screenshot(title: Optional[str] = None) -> str:
    t0 = time.time()
    if not _pillow_available:
        return json.dumps({"success": False, "error": "Pillow unavailable", "tool_name": "desktop_window_screenshot", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    try:
        if title and _pygetwindow_available:
            windows = pygetwindow.getWindowsWithTitle(title)
            if windows:
                w = windows[0]
                img = ImageGrab.grab(bbox=(w.left, w.top, w.left + w.width, w.top + w.height))
            else:
                img = ImageGrab.grab()
        else:
            img = ImageGrab.grab()

        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return json.dumps({"success": True, "data": {"image_base64": b64, "format": "png", "width": img.width, "height": img.height}, "tool_name": "desktop_window_screenshot", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_window_screenshot", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
