#!/usr/bin/env python3
"""
记忆反思心跳集成

在心跳期间自动执行反思任务：
1. 检查未处理的错误
2. 分析错误模式
3. 应用待处理的改进
4. 验证已应用的改进

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-19
"""

import sys
import os

# 添加模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_reflector import MemoryReflector, ReflectionStatus


def run_reflection_heartbeat():
    """运行反思心跳"""
    print("🔄 记忆反思心跳开始...")

    reflector = MemoryReflector()

    # 1. 获取反思摘要
    summary = reflector.get_reflection_summary()
    print("\n📊 反思摘要:")
    print(f"  - 总错误数: {summary['total_errors']}")
    print(f"  - 错误类型: {summary['error_types']}")
    print(f"  - 检测到的模式: {summary['total_patterns']}")
    print(f"  - 超阈值的模式: {summary['patterns_above_threshold']}")
    print(f"  - 待处理改进: {summary['pending_improvements']}")
    print(f"  - 可自动应用: {summary['auto_applicable']}")

    # 2. 自动应用可自动应用的改进
    pending = reflector.recorder.get_pending_improvements()
    auto_applicable = [i for i in pending if i.auto_applicable]

    if auto_applicable:
        print(f"\n🚀 自动应用 {len(auto_applicable)} 个改进...")
        for improvement in auto_applicable:
            success = reflector.apply_improvement(improvement=improvement)
            if success:
                print(f"  ✅ {improvement.suggestion[:50]}...")
            else:
                print(f"  ❌ {improvement.suggestion[:50]}...")

    # 3. 提示需要人工确认的改进
    manual_confirm = [i for i in pending if not i.auto_applicable]

    if manual_confirm:
        print(f"\n⚠️  需要人工确认的改进 ({len(manual_confirm)} 个):")
        for improvement in manual_confirm:
            print(f"  - [{improvement.priority.value}] {improvement.suggestion}")
            print(f"    目标文件: {improvement.target_file}")
            print(f"    根本原因: {improvement.root_cause}")

    # 4. 检查最近 7 天的错误模式
    print("\n📈 最近错误模式分析:")
    patterns = reflector.recorder.get_patterns()
    for pattern in patterns:
        if pattern.occurrence_count >= 2:
            print(f"  - {pattern.error_type.value}: {pattern.occurrence_count} 次")
            print(f"    签名: {pattern.pattern_signature[:60]}...")

    print("\n✅ 记忆反思心跳完成")

    return {
        "summary": summary,
        "auto_applied": len(auto_applicable),
        "manual_confirm": len(manual_confirm)
    }


if __name__ == "__main__":
    result = run_reflection_heartbeat()
    print(f"\n结果: {result}")
