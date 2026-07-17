#!/usr/bin/env python3
"""
记忆反思对话集成

在对话中自动检测用户纠正并记录。

集成方式：
1. 在 AGENTS.md 中添加规则：检测到用户纠正时调用此脚本
2. 或在对话处理流程中自动调用

Author: GalaxyOS
Version: 1.0.0
Created: 2026-04-19
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from reflection_nl import process_user_message


def check_and_reflect(user_message: str, ai_response: str = None) -> dict:
    """
    检查用户消息并执行反思（如果需要）

    Args:
        user_message: 用户消息
        ai_response: AI 的上一条回复（作为上下文）

    Returns:
        {
            "should_inform": bool,  # 是否需要通知用户
            "message": str,         # 通知消息
            "data": dict           # 详细数据
        }
    """
    result = process_user_message(user_message, ai_response)

    if result["type"] == "none":
        # 不是反思相关消息，静默返回
        return {
            "should_inform": False,
            "message": None,
            "data": {}
        }

    if result["type"] == "correction":
        # 用户纠正，记录成功，简短通知
        return {
            "should_inform": True,
            "message": "📝 已记录，我会记住的。",
            "data": result["data"]
        }

    if result["type"] == "query":
        # 查询请求，返回详细结果
        return {
            "should_inform": True,
            "message": result["response"],
            "data": result["data"]
        }

    if result["type"] == "apply":
        # 应用改进，返回结果
        return {
            "should_inform": True,
            "message": result["response"],
            "data": result["data"]
        }

    return {
        "should_inform": False,
        "message": None,
        "data": {}
    }


def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="记忆反思对话集成")
    parser.add_argument("--user", required=True, help="用户消息")
    parser.add_argument("--ai", help="AI 回复（上下文）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    args = parser.parse_args()

    result = check_and_reflect(args.user, args.ai)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["should_inform"]:
            print(result["message"])


if __name__ == "__main__":
    main()
