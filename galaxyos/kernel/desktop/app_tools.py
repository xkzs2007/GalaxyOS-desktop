from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)


async def desktop_app_launch(app_name: str, arguments: Optional[str] = None) -> str:
    t0 = time.time()
    try:
        cmd = [app_name]
        if arguments:
            cmd.append(arguments)
        proc = subprocess.Popen(cmd, shell=True)
        return json.dumps({"success": True, "data": {"pid": proc.pid, "app_name": app_name}, "tool_name": "desktop_app_launch", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except FileNotFoundError:
        return json.dumps({"success": False, "error": f"Application '{app_name}' not found", "tool_name": "desktop_app_launch", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_app_launch", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
