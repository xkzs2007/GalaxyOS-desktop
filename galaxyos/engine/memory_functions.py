#!/usr/bin/env python3
"""
内存管理函数 (Memory Functions)

提供 MemGPT 风格的内存操作函数：
- core_memory_append: 追加到核心记忆
- core_memory_replace: 替换核心记忆内容
- archival_memory_insert: 插入归档记忆
- archival_memory_search: 搜索归档记忆
- conversation_search: 搜索对话历史

这些函数可以被 LLM 直接调用，实现自主内存管理。

Author: GalaxyOS
Version: 1.0.0
Created: 2026-04-21
"""

import json
import re
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from datetime import datetime, timezone

# 导入 MemGPT 记忆管理器
from memgpt_memory import (
    MemGPTMemory,
    Memory,
    MemoryType,
    MemoryPriority
)


class MemoryFunctions:
    """
    内存管理函数集合

    这些函数设计为可被 LLM 直接调用，
    实现 MemGPT 论文中的自主内存管理能力。
    """

    def __init__(self, memory_manager: MemGPTMemory = None, workspace_path: str = None):
        self.memory = memory_manager or MemGPTMemory(workspace_path)

    # ==================== 核心记忆操作 ====================

    def core_memory_append(
        self,
        content: str,
        section: str = None,
        priority: str = "HIGH"
    ) -> Dict:
        """
        追加内容到核心记忆

        Args:
            content: 要追加的内容
            section: 可选的分区名称（如 "用户偏好"、"项目信息"）
            priority: 优先级 (CRITICAL/HIGH/MEDIUM/LOW)

        Returns:
            {
                "success": bool,
                "memory_id": str,
                "message": str
            }

        示例:
            core_memory_append("用户喜欢使用 Python 进行数据分析", section="用户偏好")
        """
        try:
            # 解析优先级
            priority_map = {
                "CRITICAL": MemoryPriority.CRITICAL,
                "HIGH": MemoryPriority.HIGH,
                "MEDIUM": MemoryPriority.MEDIUM,
                "LOW": MemoryPriority.LOW
            }
            mem_priority = priority_map.get(priority.upper(), MemoryPriority.HIGH)

            # 构建元数据
            metadata = {}
            if section:
                metadata["section"] = section

            # 添加到核心记忆
            memory_id = self.memory.core_memory.add(
                content=content,
                priority=mem_priority,
                metadata=metadata
            )

            return {
                "success": True,
                "memory_id": memory_id,
                "message": f"已添加到核心记忆 [{section or '默认'}]"
            }

        except Exception as e:
            return {
                "success": False,
                "memory_id": None,
                "message": f"添加失败: {str(e)}"
            }

    def core_memory_replace(
        self,
        old_content: str,
        new_content: str,
        section: str = None
    ) -> Dict:
        """
        替换核心记忆中的内容

        Args:
            old_content: 要替换的旧内容（支持部分匹配）
            new_content: 新内容
            section: 可选的分区限制

        Returns:
            {
                "success": bool,
                "memory_id": str,
                "message": str
            }

        示例:
            core_memory_replace(
                old_content="用户使用 Python 3.8",
                new_content="用户升级到了 Python 3.12"
            )
        """
        try:
            # 搜索匹配的记忆
            memories = self.memory.core_memory.get_all()

            for m in memories:
                # 检查分区
                if section and m.metadata.get("section") != section:
                    continue

                # 检查内容匹配
                if old_content in m.content or m.content in old_content:
                    # 更新内容
                    success = self.memory.core_memory.update(m.id, new_content)

                    if success:
                        return {
                            "success": True,
                            "memory_id": m.id,
                            "message": "已更新核心记忆"
                        }

            return {
                "success": False,
                "memory_id": None,
                "message": "未找到匹配的核心记忆"
            }

        except Exception as e:
            return {
                "success": False,
                "memory_id": None,
                "message": f"替换失败: {str(e)}"
            }

    def core_memory_delete(
        self,
        content_pattern: str = None,
        memory_id: str = None,
        section: str = None
    ) -> Dict:
        """
        删除核心记忆

        Args:
            content_pattern: 内容匹配模式（删除匹配的第一条）
            memory_id: 直接指定记忆 ID
            section: 分区名称（删除整个分区）

        Returns:
            {
                "success": bool,
                "deleted_count": int,
                "message": str
            }
        """
        try:
            deleted_count = 0

            if memory_id:
                # 直接删除指定 ID
                if self.memory.core_memory.remove(memory_id):
                    deleted_count = 1

            elif section:
                # 删除整个分区
                memories = self.memory.core_memory.get_all()
                for m in memories:
                    if m.metadata.get("section") == section:
                        if self.memory.core_memory.remove(m.id):
                            deleted_count += 1

            elif content_pattern:
                # 按内容匹配删除
                memories = self.memory.core_memory.get_all()
                for m in memories:
                    if content_pattern.lower() in m.content.lower():
                        if self.memory.core_memory.remove(m.id):
                            deleted_count += 1
                            break  # 只删除第一条

            return {
                "success": deleted_count > 0,
                "deleted_count": deleted_count,
                "message": f"已删除 {deleted_count} 条核心记忆"
            }

        except Exception as e:
            return {
                "success": False,
                "deleted_count": 0,
                "message": f"删除失败: {str(e)}"
            }

    # ==================== 归档记忆操作 ====================

    def archival_memory_insert(
        self,
        content: str,
        importance: float = 0.5,
        tags: List[str] = None,
        metadata: Dict = None
    ) -> Dict:
        """
        插入记忆到归档存储

        Args:
            content: 记忆内容
            importance: 重要性 (0.0-1.0)
            tags: 标签列表
            metadata: 元数据

        Returns:
            {
                "success": bool,
                "memory_id": str,
                "message": str
            }

        示例:
            archival_memory_insert(
                content="讨论了微服务架构设计方案",
                importance=0.7,
                tags=["架构", "设计", "微服务"]
            )
        """
        try:
            memory_id = self.memory.archival_memory.add(
                content=content,
                importance=importance,
                tags=tags or [],
                metadata=metadata or {}
            )

            return {
                "success": True,
                "memory_id": memory_id,
                "message": f"已存入归档记忆 (重要性: {importance})"
            }

        except Exception as e:
            return {
                "success": False,
                "memory_id": None,
                "message": f"插入失败: {str(e)}"
            }

    def archival_memory_search(
        self,
        query: str,
        top_k: int = 10,
        min_importance: float = 0.0,
        tags: List[str] = None
    ) -> Dict:
        """
        搜索归档记忆

        Args:
            query: 搜索查询
            top_k: 返回数量
            min_importance: 最小重要性过滤
            tags: 标签过滤

        Returns:
            {
                "success": bool,
                "results": List[Dict],
                "count": int,
                "message": str
            }

        示例:
            archival_memory_search(
                query="微服务架构",
                top_k=5,
                tags=["架构"]
            )
        """
        try:
            memories = self.memory.archival_memory.search(
                query=query,
                top_k=top_k,
                min_importance=min_importance
            )

            # 标签过滤
            if tags:
                memories = [
                    m for m in memories
                    if any(tag in m.tags for tag in tags)
                ]

            results = [
                {
                    "id": m.id,
                    "content": m.content,
                    "importance": m.importance,
                    "created_at": m.created_at,
                    "access_count": m.access_count,
                    "tags": m.tags
                }
                for m in memories
            ]

            return {
                "success": True,
                "results": results,
                "count": len(results),
                "message": f"找到 {len(results)} 条相关记忆"
            }

        except Exception as e:
            return {
                "success": False,
                "results": [],
                "count": 0,
                "message": f"搜索失败: {str(e)}"
            }

    def archival_memory_delete(
        self,
        memory_id: str = None,
        older_than_days: int = None,
        max_importance: float = None
    ) -> Dict:
        """
        删除归档记忆

        Args:
            memory_id: 直接指定记忆 ID
            older_than_days: 删除 N 天前的记忆
            max_importance: 删除重要性低于此值的记忆

        Returns:
            {
                "success": bool,
                "deleted_count": int,
                "message": str
            }
        """
        try:
            deleted_count = 0

            if memory_id:
                # 直接删除
                if self.memory.archival_memory.delete(memory_id):
                    deleted_count = 1

            elif older_than_days or max_importance is not None:
                # 批量删除
                memories = self.memory.archival_memory.get_recent(limit=1000)

                cutoff_date = None
                if older_than_days:
                    cutoff_date = datetime.now(timezone.utc) - timedelta(days=older_than_days)

                for m in memories:
                    should_delete = False

                    # 检查日期
                    if cutoff_date:
                        m_date = datetime.fromisoformat(m.created_at.replace('Z', '+00:00'))
                        if m_date < cutoff_date:
                            should_delete = True

                    # 检查重要性
                    if max_importance is not None:
                        if m.importance < max_importance:
                            should_delete = True

                    if should_delete:
                        if self.memory.archival_memory.delete(m.id):
                            deleted_count += 1

            return {
                "success": deleted_count > 0,
                "deleted_count": deleted_count,
                "message": f"已删除 {deleted_count} 条归档记忆"
            }

        except Exception as e:
            return {
                "success": False,
                "deleted_count": 0,
                "message": f"删除失败: {str(e)}"
            }

    # ==================== 对话历史操作 ====================

    def conversation_search(
        self,
        query: str,
        search_archival: bool = True
    ) -> Dict:
        """
        搜索对话历史

        Args:
            query: 搜索查询
            search_archival: 是否同时搜索归档记忆

        Returns:
            {
                "success": bool,
                "results": List[Dict],
                "message": str
            }
        """
        try:
            results = []

            # 搜索工作记忆中的消息
            working_context = self.memory.working_memory.get_context(include_summary=True)
            if query.lower() in working_context.lower():
                results.append({
                    "source": "working_memory",
                    "content": working_context[:500],
                    "relevance": "high"
                })

            # 搜索归档记忆
            if search_archival:
                archival_results = self.memory.archival_memory.search(query, top_k=5)
                for m in archival_results:
                    results.append({
                        "source": "archival_memory",
                        "id": m.id,
                        "content": m.content,
                        "importance": m.importance,
                        "created_at": m.created_at
                    })

            return {
                "success": True,
                "results": results,
                "message": f"找到 {len(results)} 条相关记录"
            }

        except Exception as e:
            return {
                "success": False,
                "results": [],
                "message": f"搜索失败: {str(e)}"
            }

    # ==================== 记忆迁移操作 ====================

    def memory_promote(self, memory_id: str) -> Dict:
        """
        将归档记忆提升到核心记忆

        Args:
            memory_id: 归档记忆 ID

        Returns:
            {
                "success": bool,
                "message": str
            }
        """
        try:
            success = self.memory.promote_to_core(memory_id)

            return {
                "success": success,
                "message": "已提升到核心记忆" if success else "提升失败，记忆不存在"
            }

        except Exception as e:
            return {
                "success": False,
                "message": f"提升失败: {str(e)}"
            }

    def memory_demote(self, memory_id: str) -> Dict:
        """
        将核心记忆降级到归档记忆

        Args:
            memory_id: 核心记忆 ID

        Returns:
            {
                "success": bool,
                "message": str
            }
        """
        try:
            # 从核心记忆获取
            memories = self.memory.core_memory.get_all()
            target = None

            for m in memories:
                if m.id == memory_id:
                    target = m
                    break

            if not target:
                return {
                    "success": False,
                    "message": "未找到核心记忆"
                }

            # 添加到归档
            self.memory.archival_memory.add(
                content=target.content,
                importance=target.importance * 0.8,
                metadata=target.metadata,
                tags=target.tags
            )

            # 从核心移除
            self.memory.core_memory.remove(memory_id)

            return {
                "success": True,
                "message": "已降级到归档记忆"
            }

        except Exception as e:
            return {
                "success": False,
                "message": f"降级失败: {str(e)}"
            }

    # ==================== 统计与诊断 ====================

    def memory_stats(self) -> Dict:
        """
        获取记忆系统统计

        Returns:
            {
                "core_memory": {...},
                "working_memory": {...},
                "archival_memory": {...},
                "recommendations": List[str]
            }
        """
        stats = self.memory.stats()
        recommendations = []

        # 分析并给出建议
        if stats["core_memory"]["utilization"] > 0.8:
            recommendations.append("核心记忆接近满载，建议清理或降级低优先级记忆")

        if stats["working_memory"]["utilization"] > 0.8:
            recommendations.append("工作记忆接近满载，将自动压缩旧对话")

        if stats["archival_memory"]["total_memories"] > 1000:
            recommendations.append("归档记忆较多，建议定期清理低重要性记忆")

        return {
            **stats,
            "recommendations": recommendations
        }


# ==================== 便捷函数（可直接导入使用）====================

_global_memory = None

def _get_memory() -> MemGPTMemory:
    """获取全局记忆实例"""
    global _global_memory
    if _global_memory is None:
        _global_memory = MemGPTMemory()
    return _global_memory


def remember(content: str, importance: float = None) -> str:
    """
    存储记忆，返回记忆 ID

    这是主要的记忆存储接口。

    Args:
        content: 记忆内容
        importance: 重要性 (0.0-1.0)，默认 0.5
                   >= 0.8 会存入核心记忆
                   >= 0.5 存入高优先级归档
                   < 0.5 存入普通归档

    Returns:
        memory_id: 记忆唯一标识

    示例:
        >>> remember("用户喜欢使用 VS Code", importance=0.8)
        'core_20260421150000_a1b2c3d4'
    """
    memory = _get_memory()
    return memory.remember(content, importance)


def recall(query: str, top_k: int = 10) -> List[Memory]:
    """
    检索记忆

    Args:
        query: 查询字符串
        top_k: 返回数量，默认 10

    Returns:
        记忆列表

    示例:
        >>> memories = recall("VS Code")
        >>> for m in memories:
        ...     print(m.content)
    """
    memory = _get_memory()
    return memory.recall(query, top_k)


def forget(memory_id: str) -> bool:
    """
    遗忘记忆

    Args:
        memory_id: 记忆 ID

    Returns:
        是否成功删除

    示例:
        >>> forget("core_20260421150000_a1b2c3d4")
        True
    """
    memory = _get_memory()
    return memory.forget(memory_id)


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="内存管理函数")
    parser.add_argument("command", choices=[
        "append", "replace", "insert", "search", "promote", "demote", "stats"
    ])
    parser.add_argument("--content", help="内容")
    parser.add_argument("--query", help="查询")
    parser.add_argument("--id", help="记忆 ID")
    parser.add_argument("--old", help="旧内容")
    parser.add_argument("--new", help="新内容")
    parser.add_argument("--importance", type=float, default=0.5, help="重要性")
    parser.add_argument("--section", help="分区")
    parser.add_argument("--tags", nargs="+", help="标签")

    args = parser.parse_args()

    funcs = MemoryFunctions()

    if args.command == "append":
        if not args.content:
            print("错误: 需要提供 --content")
            return
        result = funcs.core_memory_append(
            args.content,
            section=args.section,
            priority="HIGH" if args.importance >= 0.7 else "MEDIUM"
        )
        print(f"{'✅' if result['success'] else '❌'} {result['message']}")

    elif args.command == "replace":
        if not args.old or not args.new:
            print("错误: 需要提供 --old 和 --new")
            return
        result = funcs.core_memory_replace(args.old, args.new, args.section)
        print(f"{'✅' if result['success'] else '❌'} {result['message']}")

    elif args.command == "insert":
        if not args.content:
            print("错误: 需要提供 --content")
            return
        result = funcs.archival_memory_insert(
            args.content,
            importance=args.importance,
            tags=args.tags
        )
        print(f"{'✅' if result['success'] else '❌'} {result['message']}")

    elif args.command == "search":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = funcs.archival_memory_search(args.query)
        print(f"找到 {result['count']} 条记忆:")
        for r in result["results"]:
            print(f"  [{r['importance']:.2f}] {r['content'][:50]}...")

    elif args.command == "promote":
        if not args.id:
            print("错误: 需要提供 --id")
            return
        result = funcs.memory_promote(args.id)
        print(f"{'✅' if result['success'] else '❌'} {result['message']}")

    elif args.command == "demote":
        if not args.id:
            print("错误: 需要提供 --id")
            return
        result = funcs.memory_demote(args.id)
        print(f"{'✅' if result['success'] else '❌'} {result['message']}")

    elif args.command == "stats":
        stats = funcs.memory_stats()
        import json
        print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
