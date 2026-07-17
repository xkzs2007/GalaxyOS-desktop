#!/usr/bin/env python3
"""
示例 1: 基础使用
演示如何初始化系统并执行基本操作
"""

import sys
from pathlib import Path

# 添加技能根目录到路径
SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from infrastructure.unified_integration import UnifiedIntegration


def main():
    print("=" * 60)
    print("示例 1: 基础使用")
    print("=" * 60)
    print()

    # 1. 创建并初始化系统
    print("1️⃣ 初始化系统...")
    integration = UnifiedIntegration()

    if not integration.initialize():
        print("❌ 初始化失败")
        return

    print("✅ 初始化成功")
    print()

    # 2. 查看系统状态
    print("2️⃣ 系统状态:")
    status = integration.get_status()
    print(f"   版本: {status['version']}")
    print(f"   技能: {status['skills']['total']} 个")
    print(f"   插件: {status['plugins']['total']} 个")
    print()

    # 3. 存储记忆
    print("3️⃣ 存储记忆...")
    memory_id = integration.store_memory(
        "这是一个测试记忆，记录了重要的信息。",
        level="L1"
    )
    if memory_id:
        print(f"✅ 记忆已存储: {memory_id}")
    print()

    # 4. 检索记忆
    print("4️⃣ 检索记忆...")
    results = integration.retrieve_memory("测试记忆", limit=3)
    print(f"   找到 {len(results)} 条记忆")
    for i, mem in enumerate(results, 1):
        print(f"   {i}. {mem.get('content', '')[:50]}...")
    print()

    # 5. 执行技能
    print("5️⃣ 执行技能...")
    result = integration.execute_skill("example-skill", {
        "action": "test",
        "param": "value"
    })
    print(f"   结果: {result.get('status', 'unknown')}")
    print()

    print("=" * 60)
    print("✅ 示例完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
