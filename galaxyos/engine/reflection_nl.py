#!/usr/bin/env python3
"""
记忆反思自然语言接口

让用户可以通过自然语言与反思模块交互：
- "不对，应该是xxx" → 自动记录用户纠正
- "最近有什么问题吗？" → 返回反思摘要
- "帮我改进一下" → 应用待处理的改进

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-19
"""

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from memory_reflector import MemoryReflector, ErrorType, Priority


class NaturalLanguageReflector:
    """自然语言反思接口"""

    # 用户纠正关键词
    CORRECTION_KEYWORDS = [
        "不对", "错了", "不是", "应该是", "其实是", "实际上",
        "搞错了", "理解错了", "记错了", "说错了",
        "wrong", "incorrect", "actually", "should be"
    ]

    # 查询关键词
    QUERY_KEYWORDS = [
        "有什么问题", "最近错误", "反思摘要", "改进建议",
        "需要改进", "待处理", "错误统计",
        "what's wrong", "any issues", "recent errors"
    ]

    # 应用改进关键词
    APPLY_KEYWORDS = [
        "应用改进", "执行改进", "帮我改进", "自动改进",
        "apply improvement", "fix it"
    ]

    def __init__(self, workspace_path: str = None):
        self.reflector = MemoryReflector(workspace_path)

    def process_message(self, message: str, context: str = None) -> dict:
        """
        处理自然语言消息

        Args:
            message: 用户消息
            context: 上下文（可选，如之前的 AI 回复）

        Returns:
            {
                "type": "correction" | "query" | "apply" | "none",
                "action_taken": bool,
                "response": str,
                "data": dict
            }
        """
        message_lower = message.lower()

        # 1. 检测用户纠正
        if self._is_correction(message):
            return self._handle_correction(message, context)

        # 2. 检测查询请求
        if self._is_query(message):
            return self._handle_query(message)

        # 3. 检测应用改进请求
        if self._is_apply_request(message):
            return self._handle_apply(message)

        # 4. 无匹配
        return {
            "type": "none",
            "action_taken": False,
            "response": None,
            "data": {}
        }

    def _is_correction(self, message: str) -> bool:
        """检测是否为用户纠正"""
        for keyword in self.CORRECTION_KEYWORDS:
            if keyword in message:
                return True
        return False

    def _is_query(self, message: str) -> bool:
        """检测是否为查询请求"""
        for keyword in self.QUERY_KEYWORDS:
            if keyword in message:
                return True
        return False

    def _is_apply_request(self, message: str) -> bool:
        """检测是否为应用改进请求"""
        for keyword in self.APPLY_KEYWORDS:
            if keyword in message:
                return True
        return False

    def _handle_correction(self, message: str, context: str = None) -> dict:
        """处理用户纠正"""
        # 提取纠正内容
        correction_content = self._extract_correction(message)

        # 记录错误
        error_id = self.reflector.record_error(
            type="user_correction",
            context=context or "用户纠正",
            detail=correction_content,
            source="user"
        )

        return {
            "type": "correction",
            "action_taken": True,
            "response": "收到，已记录你的纠正。我会从中学习改进。",
            "data": {
                "error_id": error_id,
                "correction": correction_content
            }
        }

    def _handle_query(self, message: str) -> dict:
        """处理查询请求"""
        summary = self.reflector.get_reflection_summary()

        # 构建响应
        response_parts = [
            "📊 **反思摘要**",
            "",
            f"- 总错误数: {summary['total_errors']}",
            f"- 检测到的模式: {summary['total_patterns']}",
            f"- 超阈值的模式: {summary['patterns_above_threshold']}",
            f"- 待处理改进: {summary['pending_improvements']}",
        ]

        # 错误类型分布
        if summary['error_types']:
            response_parts.append("")
            response_parts.append("**错误类型分布**:")
            for error_type, count in summary['error_types'].items():
                response_parts.append(f"  - {error_type}: {count} 次")

        # 待处理改进
        pending = self.reflector.recorder.get_pending_improvements()
        if pending:
            response_parts.append("")
            response_parts.append(f"**待处理改进** ({len(pending)} 个):")
            for imp in pending[:5]:  # 只显示前 5 个
                priority_emoji = {"low": "📝", "medium": "⚠️", "high": "🔴", "critical": "🚨"}
                emoji = priority_emoji.get(imp.priority.value, "📝")
                response_parts.append(f"  {emoji} [{imp.priority.value}] {imp.suggestion[:50]}...")

        return {
            "type": "query",
            "action_taken": True,
            "response": "\n".join(response_parts),
            "data": summary
        }

    def _handle_apply(self, message: str) -> dict:
        """处理应用改进请求"""
        pending = self.reflector.recorder.get_pending_improvements()

        if not pending:
            return {
                "type": "apply",
                "action_taken": False,
                "response": "没有待处理的改进建议。",
                "data": {"applied": 0}
            }

        # 应用所有可自动应用的改进
        applied = []
        for imp in pending:
            if imp.auto_applicable:
                success = self.reflector.apply_improvement(improvement=imp)
                if success:
                    applied.append(imp)

        if applied:
            response = f"✅ 已应用 {len(applied)} 个改进:\n"
            for imp in applied:
                response += f"  - {imp.suggestion[:50]}...\n"
        else:
            response = f"⚠️ 有 {len(pending)} 个改进需要人工确认，暂无自动应用的改进。"

        return {
            "type": "apply",
            "action_taken": len(applied) > 0,
            "response": response,
            "data": {"applied": len(applied), "total_pending": len(pending)}
        }

    def _extract_correction(self, message: str) -> str:
        """提取纠正内容"""
        # 尝试提取"应该是xxx"或"其实是xxx"后面的内容
        patterns = [
            r"应该是[：:]\s*(.+)",
            r"其实是[：:]\s*(.+)",
            r"实际上是[：:]\s*(.+)",
            r"不对[，,。]\s*(.+)",
            r"错了[，,。]\s*(.+)",
            r"不是[，,。]\s*(.+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                return match.group(1).strip()

        # 如果没有匹配到模式，返回整个消息
        return message


def process_user_message(message: str, context: str = None) -> dict:
    """
    处理用户消息的便捷函数

    可在对话中直接调用：

    ```python
    from reflection_nl import process_user_message

    result = process_user_message("不对，数据仓库应该是 gitee.com/xkzs2007/xkzs")
    if result["action_taken"]:
        print(result["response"])
    ```
    """
    reflector = NaturalLanguageReflector()
    return reflector.process_message(message, context)


# CLI 接口
def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="记忆反思自然语言接口")
    parser.add_argument("message", help="用户消息")
    parser.add_argument("--context", help="上下文", default=None)
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")

    args = parser.parse_args()

    result = process_user_message(args.message, args.context)

    if args.json:
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["response"]:
            print(result["response"])
        else:
            print("(未识别为反思相关消息)")


if __name__ == "__main__":
    main()
