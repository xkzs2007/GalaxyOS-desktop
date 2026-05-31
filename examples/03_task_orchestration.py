#!/usr/bin/env python3
"""
示例 3: 任务编排使用
演示如何创建和执行任务工作流
"""

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from orchestration.task_engine import OrchestrationLayer


def main():
    print("=" * 60)
    print("示例 3: 任务编排使用")
    print("=" * 60)
    print()
    
    # 1. 创建编排层
    print("1️⃣ 初始化编排层...")
    orchestration = OrchestrationLayer({})
    orchestration.start()
    print("✅ 编排层已启动")
    print()
    
    # 2. 创建任务
    print("2️⃣ 创建任务...")
    
    task1 = orchestration.create_task(
        name="数据收集任务",
        task_type="search",
        metadata={"priority": "high"}
    )
    print(f"   ✅ 任务1: {task1.task_id} - {task1.name}")
    
    task2 = orchestration.create_task(
        name="数据处理任务",
        task_type="update",
        dependencies=[task1.task_id],
        metadata={"priority": "medium"}
    )
    print(f"   ✅ 任务2: {task2.task_id} - {task2.name}")
    
    task3 = orchestration.create_task(
        name="报告生成任务",
        task_type="create",
        dependencies=[task2.task_id],
        metadata={"priority": "low"}
    )
    print(f"   ✅ 任务3: {task3.task_id} - {task3.name}")
    
    print()
    
    # 3. 创建工作流
    print("3️⃣ 创建工作流...")
    workflow_id = orchestration.create_workflow(
        name="数据处理流程",
        task_ids=[task1.task_id, task2.task_id, task3.task_id]
    )
    print(f"   ✅ 工作流: {workflow_id}")
    print()
    
    # 4. 执行工作流
    print("4️⃣ 执行工作流...")
    success = orchestration.execute_workflow(workflow_id)
    if success:
        print("   ✅ 工作流执行成功")
    else:
        print("   ❌ 工作流执行失败")
    print()
    
    # 5. 查看任务状态
    print("5️⃣ 任务状态:")
    for task_id in [task1.task_id, task2.task_id, task3.task_id]:
        status = orchestration.get_task_status(task_id)
        if status:
            print(f"   {status['name']}: {status['status']}")
    print()
    
    # 6. 查看统计
    print("6️⃣ 编排统计:")
    stats = orchestration.get_stats()
    print(f"   总任务数: {stats['total_tasks']}")
    print(f"   总工作流: {stats['total_workflows']}")
    print(f"   状态分布: {stats['status_counts']}")
    print()
    
    orchestration.stop()
    
    print("=" * 60)
    print("✅ 示例完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
