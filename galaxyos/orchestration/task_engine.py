#!/usr/bin/env python3
"""
L3 - Orchestration Layer
任务编排层

职责：
- 任务路由
- 工作流管理
- 调度执行
- 状态管理
"""

import os
import sys
import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from enum import Enum

logger = logging.getLogger('xiaoyi-claw-omega.L3')


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task:
    """任务定义"""

    def __init__(self, task_id: str, name: str, handler: Optional[Callable] = None):
        self.task_id = task_id
        self.name = name
        self.handler = handler
        self.status = TaskStatus.PENDING
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.result: Any = None
        self.error: Optional[str] = None
        self.dependencies: List[str] = []
        self.metadata: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": str(self.result)[:200] if self.result else None,
            "error": self.error
        }


class OrchestrationLayer:
    """
    L3 - 任务编排层
    
    职责：
    - 任务路由和分发
    - 工作流管理
    - 调度执行
    - 状态跟踪
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.tasks: Dict[str, Task] = {}
        self.workflows: Dict[str, List[str]] = {}
        self.handlers: Dict[str, Callable] = {}
        self._running = False

    def start(self):
        """启动编排层"""
        logger.info("L3 Orchestration: 启动任务编排层")
        self._register_default_handlers()
        self._running = True
        logger.info("L3 Orchestration: 任务编排层启动完成")

    def stop(self):
        """停止编排层"""
        self._running = False
        logger.info("L3 Orchestration: 任务编排层已停止")

    def _register_default_handlers(self):
        """注册默认处理器"""
        self.handlers = {
            "search": self._handle_search,
            "create": self._handle_create,
            "update": self._handle_update,
            "delete": self._handle_delete,
            "query": self._handle_query
        }
        logger.info(f"  ✅ 注册处理器: {len(self.handlers)} 个")

    def _handle_search(self, task: Task) -> Any:
        """处理搜索任务"""
        logger.info(f"执行搜索任务: {task.name}")
        return {"type": "search", "status": "completed"}

    def _handle_create(self, task: Task) -> Any:
        """处理创建任务"""
        logger.info(f"执行创建任务: {task.name}")
        return {"type": "create", "status": "completed"}

    def _handle_update(self, task: Task) -> Any:
        """处理更新任务"""
        logger.info(f"执行更新任务: {task.name}")
        return {"type": "update", "status": "completed"}

    def _handle_delete(self, task: Task) -> Any:
        """处理删除任务"""
        logger.info(f"执行删除任务: {task.name}")
        return {"type": "delete", "status": "completed"}

    def _handle_query(self, task: Task) -> Any:
        """处理查询任务"""
        logger.info(f"执行查询任务: {task.name}")
        return {"type": "query", "status": "completed"}

    def create_task(self, name: str, task_type: str = "query",
                    dependencies: Optional[List[str]] = None,
                    metadata: Optional[Dict] = None) -> Task:
        """创建任务"""
        task_id = f"task_{datetime.now().strftime('%Y%m%d%H%M%S')}_{len(self.tasks)}"

        task = Task(task_id, name, self.handlers.get(task_type))
        task.dependencies = dependencies or []
        task.metadata = metadata or {}

        self.tasks[task_id] = task
        logger.info(f"L3 Orchestration: 创建任务 {task_id}")

        return task

    def execute_task(self, task_id: str) -> bool:
        """执行任务"""
        task = self.tasks.get(task_id)
        if not task:
            logger.error(f"任务不存在: {task_id}")
            return False

        # 检查依赖
        for dep_id in task.dependencies:
            dep_task = self.tasks.get(dep_id)
            if dep_task and dep_task.status != TaskStatus.COMPLETED:
                logger.warning(f"依赖未完成: {dep_id}")
                return False

        try:
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now()

            if task.handler:
                task.result = task.handler(task)

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            logger.info(f"L3 Orchestration: 任务完成 {task_id}")
            return True

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"L3 Orchestration: 任务失败 {task_id}: {e}")
            return False

    def create_workflow(self, name: str, task_ids: List[str]) -> str:
        """创建工作流"""
        workflow_id = f"wf_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        self.workflows[workflow_id] = task_ids
        logger.info(f"L3 Orchestration: 创建工作流 {workflow_id}")
        return workflow_id

    def execute_workflow(self, workflow_id: str) -> bool:
        """执行工作流"""
        task_ids = self.workflows.get(workflow_id)
        if not task_ids:
            logger.error(f"工作流不存在: {workflow_id}")
            return False

        logger.info(f"L3 Orchestration: 执行工作流 {workflow_id}")

        for task_id in task_ids:
            if not self.execute_task(task_id):
                logger.error(f"工作流执行失败: {workflow_id}")
                return False

        logger.info(f"L3 Orchestration: 工作流完成 {workflow_id}")
        return True

    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务状态"""
        task = self.tasks.get(task_id)
        return task.to_dict() if task else None

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        status_counts = {}
        for task in self.tasks.values():
            status = task.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

        return {
            "total_tasks": len(self.tasks),
            "total_workflows": len(self.workflows),
            "status_counts": status_counts
        }
