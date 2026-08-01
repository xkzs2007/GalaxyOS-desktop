from __future__ import annotations

import json
import logging
import os
import platform
import time
from typing import Optional

logger = logging.getLogger(__name__)

_psutil_available = False
try:
    import psutil
    _psutil_available = True
except ImportError:
    pass


async def desktop_system_info() -> str:
    t0 = time.time()
    try:
        if _psutil_available:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            data = {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "platform_version": platform.version(),
                "architecture": platform.machine(),
                "processor": platform.processor(),
                "python_version": platform.python_version(),
                "cpu_count": os.cpu_count(),
                "cpu_percent": cpu_percent,
                "memory_total_gb": round(mem.total / (1024**3), 1),
                "memory_available_gb": round(mem.available / (1024**3), 1),
                "memory_percent": mem.percent,
                "disk_total_gb": round(disk.total / (1024**3), 1),
                "disk_free_gb": round(disk.free / (1024**3), 1),
                "disk_percent": disk.percent,
            }
        else:
            data = {
                "platform": platform.system(),
                "platform_release": platform.release(),
                "architecture": platform.machine(),
                "processor": platform.processor(),
                "python_version": platform.python_version(),
                "cpu_count": os.cpu_count(),
                "note": "psutil not installed, limited info",
            }
        return json.dumps({"success": True, "data": data, "tool_name": "desktop_system_info", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_system_info", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)


async def desktop_process_list(filter_name: Optional[str] = None) -> str:
    t0 = time.time()
    if not _psutil_available:
        return json.dumps({"success": False, "error": "psutil not installed", "tool_name": "desktop_process_list", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    try:
        procs = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                info = p.info
                if filter_name and filter_name.lower() not in (info.get("name") or "").lower():
                    continue
                procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return json.dumps({"success": True, "data": procs, "tool_name": "desktop_process_list", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_process_list", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)


async def desktop_process_kill(pid: int, confirm: bool = False) -> str:
    t0 = time.time()
    if not _psutil_available:
        return json.dumps({"success": False, "error": "psutil not installed", "tool_name": "desktop_process_kill", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    if not confirm:
        return json.dumps({"success": False, "error": "Process kill requires explicit confirmation (confirm=True)", "tool_name": "desktop_process_kill"}, ensure_ascii=False)
    try:
        p = psutil.Process(pid)
        p.terminate()
        return json.dumps({"success": True, "data": {"pid": pid, "terminated": True}, "tool_name": "desktop_process_kill", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except psutil.NoSuchProcess:
        return json.dumps({"success": False, "error": f"Process {pid} not found", "tool_name": "desktop_process_kill", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except psutil.AccessDenied:
        return json.dumps({"success": False, "error": f"Access denied to kill process {pid}", "tool_name": "desktop_process_kill", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_process_kill", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
