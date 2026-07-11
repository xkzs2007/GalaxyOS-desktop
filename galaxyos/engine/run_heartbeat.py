#!/usr/bin/env python3
"""
心跳执行脚本
供 OpenClaw 心跳系统调用

集成 Galaxy Engine 持续学习管线。

Author: 小艺 Claw
Version: 2.0.0
Updated: 2026-06-14
"""

import sys
import os
import json
import subprocess
from pathlib import Path
from datetime import datetime
from galaxyos.shared.paths import workspace

# 添加核心模块路径
CORE_DIR = Path(__file__).parent.parent / "skills/llm-memory-integration/core"
sys.path.insert(0, str(CORE_DIR))
# 添加自身引擎目录
ENGINE_DIR = Path(__file__).parent.resolve()
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

try:
    from heartbeat_task_executor import HeartbeatTaskExecutor
    HEARTBEAT_AVAILABLE = True
except ImportError as e:
    print(f"警告: 心跳执行器导入失败: {e}")
    HEARTBEAT_AVAILABLE = False

# ── Galaxy Engine 持续学习（降级友好） ──
GALAXY_AVAILABLE = False
try:
    from galaxy_engine_integration import get_galaxy_engine
    GALAXY_ENGINE = get_galaxy_engine()
    GALAXY_AVAILABLE = True
    print(f"GalaxyEngine 持续学习管线: {'可用' if GALAXY_ENGINE.get_status()['continual']['available'] else '已加载但模块不可用'}")
except Exception as e:
    GALAXY_ENGINE = None
    print(f"GalaxyEngine 持续学习管线不可用: {e}")


def run_heartbeat():
    """执行心跳任务"""
    # ── 1. 原有心跳任务 ──
    heartbeat_result = None
    if HEARTBEAT_AVAILABLE:
        try:
            executor = HeartbeatTaskExecutor()
            result = executor.execute_heartbeat(duration_minutes=15)
            heartbeat_result = {
                "status": "success",
                "tasks_executed": result.tasks_executed,
                "duration_ms": result.duration_ms,
                "results": result.results if hasattr(result, 'results') else [],
            }
        except Exception as e:
            heartbeat_result = {"status": "error", "error": str(e)}
    else:
        heartbeat_result = {"status": "skipped", "reason": "HeartbeatTaskExecutor 不可用"}

    # ── 2. Galaxy Engine 持续学习步进 ──
    continual_result = None
    if GALAXY_AVAILABLE and GALAXY_ENGINE:
        try:
            continual_result = GALAXY_ENGINE.execute_continual_learning(max_steps=10)
        except Exception as e:
            continual_result = {"learned": 0, "error": str(e)}

    # ── 3. 综合结果 ──
    return {
        "status": "success",
        "timestamp": datetime.now().isoformat(),
        "heartbeat": heartbeat_result,
        "continual_learning": continual_result or {"learned": 0, "reason": "未执行"},
    }


def run_continual_learning_only():
    """仅执行持续学习（给 cron 调用）"""
    if not GALAXY_AVAILABLE or not GALAXY_ENGINE:
        return {"status": "error", "message": "Galaxy Engine 不可用"}
    try:
        result = GALAXY_ENGINE.execute_continual_learning(max_steps=20)
        return {"status": "success", **result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def status():
    """获取 Galaxy Engine 状态"""
    info = {"heartbeat_executor": HEARTBEAT_AVAILABLE}
    if GALAXY_AVAILABLE and GALAXY_ENGINE:
        info["galaxy_engine"] = GALAXY_ENGINE.get_full_status()
    else:
        info["galaxy_engine"] = {"available": False}
    return info


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="心跳执行器")
    parser.add_argument("mode", nargs="?", default="full",
                        choices=["full", "continual", "status"],
                        help="执行模式: full(默认)/continual/status")
    args = parser.parse_args()

    if args.mode == "status":
        result = status()
    elif args.mode == "continual":
        result = run_continual_learning_only()
    else:
        result = run_heartbeat()

    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 如果是 full 模式，执行智能备份检查
    if args.mode == "full":
        print("\n" + "=" * 50)
        print("☁️ 智能备份检查")
        print("=" * 50)
        try:
            bp_result = subprocess.run(
                ["python3", str(Path(workspace()) / "skills" / "huawei-drive" / "scripts" / "smart_backup.py")],
                capture_output=True,
                text=True,
                timeout=60
            )
            print(bp_result.stdout)
        except Exception as e:
            print(f"智能备份检查失败: {e}")
