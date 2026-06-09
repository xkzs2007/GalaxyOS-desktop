#!/usr/bin/env python3
"""
心跳执行脚本
供 OpenClaw 心跳系统调用

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-23
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime

# 添加核心模块路径
CORE_DIR = Path(__file__).parent.parent / "skills/llm-memory-integration/core"
sys.path.insert(0, str(CORE_DIR))

try:
    from heartbeat_task_executor import HeartbeatTaskExecutor
    HEARTBEAT_AVAILABLE = True
except ImportError as e:
    print(f"警告: 心跳执行器导入失败: {e}")
    HEARTBEAT_AVAILABLE = False


def run_heartbeat():
    """执行心跳任务"""
    if not HEARTBEAT_AVAILABLE:
        return {
            "status": "error",
            "message": "心跳执行器不可用",
            "timestamp": datetime.now().isoformat()
        }

    executor = HeartbeatTaskExecutor()
    result = executor.execute_heartbeat(duration_minutes=15)

    return {
        "status": "success",
        "timestamp": result.timestamp,
        "tasks_executed": result.tasks_executed,
        "duration_ms": result.duration_ms,
        "results": result.results
    }


if __name__ == "__main__":
    result = run_heartbeat()
    print(json.dumps(result, ensure_ascii=False, indent=2))

# 智能备份检查
print("\n" + "=" * 50)
print("☁️ 智能备份检查")
print("=" * 50)

try:
    result = subprocess.run(
        ["python3", str(Path.home() / ".openclaw" / "workspace" / "skills" / "huawei-drive" / "scripts" / "smart_backup.py")],
        capture_output=True,
        text=True,
        timeout=60
    )
    print(result.stdout)
except Exception as e:
    print(f"智能备份检查失败: {e}")
