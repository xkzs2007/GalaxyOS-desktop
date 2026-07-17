#!/usr/bin/env python3
"""
示例 2: 记忆系统使用
演示如何使用记忆存储和检索功能
"""

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))

from memory_context.memory_manager import MemoryLayer


def main():
    print("=" * 60)
    print("示例 2: 记忆系统使用")
    print("=" * 60)
    print()

    # 1. 创建记忆层
    print("1️⃣ 初始化记忆层...")
    memory = MemoryLayer({})
    memory.start()
    print("✅ 记忆层已启动")
    print()

    # 2. 存储不同级别的记忆
    print("2️⃣ 存储记忆...")

    memories = [
        ("L1", "用户偏好：喜欢简洁的回答风格"),
        ("L1", "重要决策：选择使用 Python 作为主要开发语言"),
        ("L0", "今天讨论了系统架构设计"),
        ("L0", "用户询问了性能优化方案"),
    ]

    for level, content in memories:
        memory_id = memory.store(content, level=level)
        print(f"   ✅ [{level}] {content[:30]}... -> {memory_id}")

    print()

    # 3. 检索记忆
    print("3️⃣ 检索记忆...")

    queries = [
        ("用户偏好", "L1"),
        ("架构", "L0"),
        ("Python", "L1"),
    ]

    for query, level in queries:
        results = memory.retrieve(query, level=level, limit=2)
        print(f"   查询 '{query}' [{level}]: {len(results)} 条结果")

    print()

    # 4. 查看统计
    print("4️⃣ 记忆统计:")
    stats = memory.get_stats()
    print(f"   L1 记忆: {stats['L1_count']} 条")
    print(f"   L0 对话: {stats['L0_count']} 条")
    print()

    # 5. 上下文管理
    print("5️⃣ 上下文管理:")
    memory.set_context("current_task", "记忆系统演示")
    memory.set_context("user_intent", "学习")

    ctx = memory.get_context()
    print(f"   当前上下文: {ctx}")
    print()

    # 6. 停止记忆层
    memory.stop()

    print("=" * 60)
    print("✅ 示例完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
