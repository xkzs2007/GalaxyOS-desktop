from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

_apscheduler_available = False
_scheduler_instance = None
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    _apscheduler_available = True
    _scheduler_instance = AsyncIOScheduler()
except ImportError:
    pass

_scheduled_tasks: Dict[str, Dict[str, Any]] = {}


async def desktop_schedule_task(task_name: str, cron_expression: str, command: str) -> str:
    t0 = time.time()
    if not _apscheduler_available:
        return json.dumps({"success": False, "error": "APScheduler not installed", "tool_name": "desktop_schedule_task", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    try:
        import subprocess

        parts = cron_expression.split()
        if len(parts) != 5:
            return json.dumps({"success": False, "error": "Invalid cron expression (expected 5 fields: min hour day month weekday)", "tool_name": "desktop_schedule_task", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)

        if not _scheduler_instance.running:
            _scheduler_instance.start()

        job_id = f"galaxyos_{task_name}"
        _scheduler_instance.add_job(
            subprocess.Popen,
            "cron",
            args=[command],
            kwargs={"shell": True},
            id=job_id,
            replace_existing=True,
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        _scheduled_tasks[task_name] = {
            "job_id": job_id,
            "cron_expression": cron_expression,
            "command": command,
        }

        return json.dumps({"success": True, "data": {"task_name": task_name, "job_id": job_id, "cron": cron_expression}, "tool_name": "desktop_schedule_task", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e), "tool_name": "desktop_schedule_task", "execution_time_ms": round((time.time() - t0) * 1000, 1)}, ensure_ascii=False)
