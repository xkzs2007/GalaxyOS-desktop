#!/usr/bin/env python3
"""
心跳任务执行器

论文参考:
- Autonomous Agents: A Survey (2023)
- Proactive Task Management (2023)

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import os
import sys
import json
import subprocess
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import logging
from galaxyos.shared.paths import workspace

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class HeartbeatResult:
    """心跳执行结果"""
    timestamp: str
    tasks_executed: int
    results: List[Dict[str, Any]]
    duration_ms: int
    status: str


class HeartbeatTaskExecutor:
    """
    心跳任务执行器
    
    基于 Autonomous Agents 论文:
    - 自主智能体应该能够自主规划和执行任务
    - 后台任务是主动性的关键体现
    
    功能:
    1. 检查并执行主动任务
    2. 记忆维护
    3. 健康检查
    4. 技能更新检查
    5. 数据备份检查
    """

    def __init__(self, workspace_dir: str = None):
        """
        初始化心跳执行器
        
        Args:
            workspace_dir: 工作空间目录
        """
        self.workspace_dir = workspace_dir or workspace()
        self.proactive_tasks_dir = os.path.join(self.workspace_dir, "skills/proactive-tasks")
        self.memory_dir = os.path.join(self.workspace_dir, "memory")
        self.logs = []

    def execute_heartbeat(self, duration_minutes: int = 15) -> HeartbeatResult:
        """
        执行心跳任务
        
        Args:
            duration_minutes: 最大执行时间（分钟）
        
        Returns:
            心跳执行结果
        """
        start_time = datetime.now()
        results = []

        logger.info("=" * 60)
        logger.info("心跳任务执行器启动")
        logger.info(f"时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        # 1. 检查主动任务
        task_result = self._check_proactive_tasks(duration_minutes)
        results.append(("proactive_tasks", task_result))

        # 2. 记忆维护
        memory_result = self._optimize_memory()
        results.append(("memory_optimize", memory_result))

        # 3. 健康检查
        health_result = self._health_check()
        results.append(("health_check", health_result))

        # 4. 技能更新检查
        update_result = self._check_skill_updates()
        results.append(("skill_updates", update_result))

        # 5. 数据备份检查
        backup_result = self._check_backup_needed()
        results.append(("backup_check", backup_result))

        # 计算耗时
        end_time = datetime.now()
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # 汇总结果
        heartbeat_result = HeartbeatResult(
            timestamp=start_time.isoformat(),
            tasks_executed=len(results),
            results=[{"task": r[0], "result": r[1]} for r in results],
            duration_ms=duration_ms,
            status="completed"
        )

        logger.info("=" * 60)
        logger.info(f"心跳执行完成，耗时 {duration_ms}ms")
        logger.info("=" * 60)

        return heartbeat_result

    def _check_proactive_tasks(self, max_duration: int) -> Dict[str, Any]:
        """检查并执行主动任务"""
        try:
            # 检查是否有待执行任务
            task_manager_path = os.path.join(self.proactive_tasks_dir, "scripts/task_manager.py")

            if not os.path.exists(task_manager_path):
                return {"status": "skipped", "reason": "task_manager not found"}

            # 获取下一个任务
            result = subprocess.run(
                ["python3", task_manager_path, "next-task"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0 or "No pending tasks" in result.stdout:
                return {"status": "no_tasks", "message": "没有待执行任务"}

            # 解析任务
            task_info = json.loads(result.stdout) if result.stdout.strip() else None

            if not task_info:
                return {"status": "no_tasks", "message": "任务解析失败"}

            # 执行任务（限制时间）
            logger.info(f"执行任务: {task_info.get('name', 'unknown')}")

            return {
                "status": "task_found",
                "task": task_info,
                "message": "发现待执行任务，建议在下次心跳中执行"
            }

        except subprocess.TimeoutExpired:
            return {"status": "timeout", "message": "任务检查超时"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _optimize_memory(self) -> Dict[str, Any]:
        """记忆维护"""
        try:
            memory_files = list(Path(self.memory_dir).glob("*.md"))

            # 统计记忆文件
            stats = {
                "total_files": len(memory_files),
                "files_checked": [],
                "cleaned": 0
            }

            # 检查过期临时笔记（超过7天）
            cutoff_date = datetime.now().timestamp() - 7 * 24 * 3600

            for f in memory_files:
                if f.stat().st_mtime < cutoff_date and "temp" in f.name.lower():
                    stats["files_checked"].append(f.name)
                    # 不自动删除，只记录

            return {"status": "completed", "stats": stats}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _health_check(self) -> Dict[str, Any]:
        """健康检查"""
        try:
            import psutil

            # 内存使用
            memory = psutil.virtual_memory()
            memory_usage = memory.percent

            # 磁盘空间
            disk = psutil.disk_usage('/')
            disk_usage = disk.percent
            disk_free_gb = disk.free / (1024 ** 3)

            # CPU 使用
            cpu_usage = psutil.cpu_percent(interval=1)

            health = {
                "memory_usage": memory_usage,
                "disk_usage": disk_usage,
                "disk_free_gb": round(disk_free_gb, 2),
                "cpu_usage": cpu_usage,
                "warnings": []
            }

            # 检查告警
            if memory_usage > 80:
                health["warnings"].append(f"内存使用率过高: {memory_usage}%")
            if disk_free_gb < 1:
                health["warnings"].append(f"磁盘空间不足: {disk_free_gb:.2f}GB")
            if cpu_usage > 90:
                health["warnings"].append(f"CPU 使用率过高: {cpu_usage}%")

            health["status"] = "warning" if health["warnings"] else "healthy"

            return health

        except ImportError:
            return {"status": "skipped", "reason": "psutil not installed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _check_skill_updates(self) -> Dict[str, Any]:
        """检查技能更新"""
        try:
            # 检查 ClawHub 更新
            result = subprocess.run(
                ["clawhub", "check-updates"],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                return {"status": "checked", "output": result.stdout[:200]}
            else:
                return {"status": "no_updates", "message": "无可用更新"}

        except FileNotFoundError:
            return {"status": "skipped", "reason": "clawhub not found"}
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "message": "更新检查超时"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _check_backup_needed(self) -> Dict[str, Any]:
        """检查是否需要备份"""
        try:
            backup_flag = os.path.join(self.workspace_dir, ".last_backup")

            if os.path.exists(backup_flag):
                with open(backup_flag, 'r') as f:
                    last_backup = datetime.fromisoformat(f.read().strip())
                days_since = (datetime.now() - last_backup).days
            else:
                days_since = 999  # 从未备份

            need_backup = days_since >= 7

            return {
                "status": "checked",
                "days_since_backup": days_since,
                "need_backup": need_backup,
                "message": "需要备份" if need_backup else "无需备份"
            }

        except Exception as e:
            return {"status": "error", "message": str(e)}


def main():
    """命令行入口"""
    executor = HeartbeatTaskExecutor()
    result = executor.execute_heartbeat()

    print(json.dumps({
        "timestamp": result.timestamp,
        "tasks_executed": result.tasks_executed,
        "duration_ms": result.duration_ms,
        "status": result.status,
        "results": result.results
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
