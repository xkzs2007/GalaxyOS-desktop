#!/usr/bin/env python3
"""
防幻觉系统集成器 (Hallucination Integration)

将防幻觉系统集成到现有记忆流程中：
- 记忆写入时验证
- 记忆召回时过滤
- 输出生成时验证
- 不确定性表达

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-20
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from galaxyos.shared.paths import workspace
from galaxyos.engine.hallucination_guard import (
    HallucinationGuard,
    VerifiedMemory,
    SourceType,
    VerificationStatus,
    ConfidenceLevel
)


class HallucinationIntegratedMemory:
    """
    集成防幻觉的记忆管理器

    使用示例:
        memory_manager = HallucinationIntegratedMemory()

        # 存储记忆（自动验证）
        memory_id = memory_manager.store(
            content="数据仓库地址是 gitee.com/xkzs2007/xkzs",
            source="user",
            context={"is_user_statement": True}
        )

        # 召回记忆（自动过滤）
        memories = memory_manager.recall("数据仓库")

        # 生成回答（自动验证和不确定性表达）
        answer = memory_manager.generate_answer(query, memories)
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or
            workspace())

        # 初始化防幻觉系统
        self.guard = HallucinationGuard(str(self.workspace_path))

        # 记忆存储路径
        self.store_path = self.workspace_path / ".learnings" / "verified_memories.jsonl"

    # ==================== 存储接口 ====================

    def store(
        self,
        content: str,
        source: str = "unknown",
        context: Dict = None,
        entities: List[str] = None,
        tags: List[str] = None,
        validity_days: int = None
    ) -> str:
        """
        存储记忆（带验证）

        Args:
            content: 记忆内容
            source: 来源类型
            context: 上下文
            entities: 相关实体
            tags: 标签
            validity_days: 有效期（天）

        Returns:
            记忆 ID
        """
        context = context or {}

        # 创建验证记忆
        memory = self.guard.create_verified_memory(
            content=content,
            source=source,
            context=context,
            custom_validity_days=validity_days
        )

        # 添加额外信息
        if entities:
            memory.related_entities = entities
        if tags:
            memory.tags = tags

        # 更新存储
        self._update_memory(memory)

        return memory.id

    def _update_memory(self, memory: VerifiedMemory):
        """更新记忆"""
        memories = self.guard._load_memories()

        # 查找并更新
        found = False
        for i, m in enumerate(memories):
            if m.id == memory.id:
                memories[i] = memory
                found = True
                break

        if not found:
            memories.append(memory)

        # 重写文件
        with open(self.store_path, "w", encoding="utf-8") as f:
            for m in memories:
                f.write(json.dumps(m.to_dict(), ensure_ascii=False) + "\n")

    # ==================== 召回接口 ====================

    def recall(
        self,
        query: str,
        top_k: int = 10,
        min_confidence: float = 0.3,
        exclude_expired: bool = True,
        exclude_false: bool = True
    ) -> List[Dict]:
        """
        召回记忆（带过滤）

        Args:
            query: 查询
            top_k: 返回数量
            min_confidence: 最小置信度
            exclude_expired: 排除过期
            exclude_false: 排除验证为假

        Returns:
            记忆列表
        """
        # 加载所有记忆
        memories = self.guard._load_memories()

        # 过滤
        filtered = []
        for memory in memories:
            # 排除验证为假
            if exclude_false and memory.verification_status == VerificationStatus.VERIFIED_FALSE:
                continue

            # 排除过期
            if exclude_expired and memory.is_expired():
                continue

            # 置信度过滤
            effective_conf = memory.get_effective_confidence()
            if effective_conf < min_confidence:
                continue

            # 简单关键词匹配（实际应用中可用向量检索）
            if self._match_query(memory.content, query):
                filtered.append({
                    "id": memory.id,
                    "content": memory.content,
                    "confidence": effective_conf,
                    "source": memory.source.value,
                    "status": memory.verification_status.value,
                    "tags": memory.tags,
                    "created_at": memory.created_at
                })

        # 按置信度排序
        filtered.sort(key=lambda x: x["confidence"], reverse=True)

        return filtered[:top_k]

    def _match_query(self, content: str, query: str) -> bool:
        """简单的查询匹配"""
        query_words = set(query.lower().split())
        content_words = set(content.lower().split())
        return len(query_words & content_words) > 0

    # ==================== 生成前检查 ====================

    def pre_check(self, query: str) -> Dict:
        """
        生成前检查

        Args:
            query: 用户查询

        Returns:
            {
                "can_answer": bool,
                "should_retrieve": bool,
                "unfamiliar_concepts": [...],
                "message": str
            }
        """
        should_refuse, reason = self.guard.check_before_generation(query)

        if should_refuse:
            return {
                "can_answer": False,
                "should_retrieve": False,
                "unfamiliar_concepts": [],
                "message": reason
            }

        # 检查是否需要检索
        familiarity, unfamiliar = self.guard.familiarity_checker.check_query_familiarity(query)

        return {
            "can_answer": True,
            "should_retrieve": "[建议检索]" in reason,
            "unfamiliar_concepts": unfamiliar,
            "message": reason if reason else "可以回答"
        }

    # ==================== 输出生成 ====================

    def generate_answer(
        self,
        query: str,
        recalled_memories: List[Dict] = None,
        raw_answer: str = None
    ) -> Dict:
        """
        生成带验证的回答

        Args:
            query: 用户查询
            recalled_memories: 召回的记忆
            raw_answer: 原始回答（可选）

        Returns:
            {
                "answer": str,
                "confidence": float,
                "validation": {...},
                "sources": [...]
            }
        """
        # 生成前检查
        pre_check = self.pre_check(query)

        if not pre_check["can_answer"]:
            return {
                "answer": pre_check["message"],
                "confidence": 0.0,
                "validation": {"refused": True},
                "sources": []
            }

        # 如果没有提供召回记忆，尝试召回
        if recalled_memories is None:
            recalled_memories = self.recall(query)

        # 计算整体置信度
        if recalled_memories:
            avg_confidence = sum(m["confidence"] for m in recalled_memories) / len(recalled_memories)
        else:
            avg_confidence = 0.4  # 无记忆时的默认置信度

        # 如果提供了原始回答，验证它
        if raw_answer:
            # 转换为 VerifiedMemory 对象
            vm_memories = []
            for m in recalled_memories:
                vm = VerifiedMemory(
                    id=m["id"],
                    content=m["content"],
                    source=SourceType(m["source"]),
                    confidence=m["confidence"],
                    created_at=m["created_at"],
                    verification_status=VerificationStatus(m["status"])
                )
                vm_memories.append(vm)

            validation = self.guard.validate_output(raw_answer, vm_memories)

            # 如果验证发现问题，降低置信度
            if not validation["is_valid"]:
                avg_confidence *= 0.7

            # 添加不确定性表达
            final_answer = self.guard.express_with_confidence(
                raw_answer,
                avg_confidence,
                pre_check["unfamiliar_concepts"],
                [m["content"][:30] for m in recalled_memories[:3]]
            )
        else:
            validation = {"no_raw_answer": True}
            final_answer = self.guard.express_with_confidence(
                "我需要更多信息才能回答这个问题。",
                avg_confidence,
                pre_check["unfamiliar_concepts"]
            )

        return {
            "answer": final_answer,
            "confidence": avg_confidence,
            "validation": validation,
            "sources": [m["content"][:50] for m in recalled_memories[:3]]
        }

    # ==================== 用户纠正处理 ====================

    def handle_user_correction(
        self,
        original_content: str,
        corrected_content: str,
        context: Dict = None
    ) -> Dict:
        """
        处理用户纠正

        Args:
            original_content: 原始内容
            corrected_content: 纠正后的内容
            context: 上下文

        Returns:
            处理结果
        """
        # 查找原始记忆
        memories = self.guard._load_memories()
        original_memory = None

        for memory in memories:
            if original_content in memory.content or memory.content in original_content:
                original_memory = memory
                break

        # 创建纠正记忆
        correction_id = self.store(
            content=corrected_content,
            source="user_correction",
            context={"is_correction": True, "original": original_content}
        )

        # 标记原始记忆为假
        if original_memory:
            original_memory.verification_status = VerificationStatus.VERIFIED_FALSE
            original_memory.conflict_ids.append(correction_id)
            self._update_memory(original_memory)

        return {
            "correction_id": correction_id,
            "original_marked_false": original_memory is not None,
            "message": "已记录纠正，原始信息已标记为不可靠"
        }

    # ==================== 批量验证 ====================

    def batch_verify(self, method: str = "multi_agent") -> Dict:
        """
        批量验证记忆

        Args:
            method: 验证方法 ("multi_agent" | "kg" | "temporal")

        Returns:
            验证结果统计
        """
        memories = self.guard._load_memories()
        results = {
            "total": len(memories),
            "verified_true": 0,
            "verified_false": 0,
            "conflicting": 0,
            "expired": 0,
            "details": []
        }

        for memory in memories:
            # 时效性检查
            if memory.is_expired():
                memory.verification_status = VerificationStatus.EXPIRED
                results["expired"] += 1
                continue

            if method == "multi_agent":
                verify_result = self.guard.verify_statement(memory.content)

                if verify_result["is_reliable"]:
                    memory.verification_status = VerificationStatus.VERIFIED_TRUE
                    results["verified_true"] += 1
                else:
                    memory.verification_status = VerificationStatus.CONFLICTING
                    results["conflicting"] += 1
                    results["details"].append({
                        "id": memory.id,
                        "content": memory.content[:50],
                        "issues": verify_result["issues"]
                    })

            elif method == "kg":
                kg_result = self.guard.verify_with_kg(memory.content)

                if kg_result["entities_missing"]:
                    memory.verification_status = VerificationStatus.CONFLICTING
                    results["conflicting"] += 1
                else:
                    memory.verification_status = VerificationStatus.VERIFIED_TRUE
                    results["verified_true"] += 1

        # 保存更新
        with open(self.store_path, "w", encoding="utf-8") as f:
            for m in memories:
                f.write(json.dumps(m.to_dict(), ensure_ascii=False) + "\n")

        return results

    # ==================== 统计信息 ====================

    def get_statistics(self) -> Dict:
        """获取统计信息"""
        return self.guard.get_stats()


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="防幻觉集成记忆系统")
    parser.add_argument("command", choices=[
        "store", "recall", "check", "generate", "correct", "verify", "stats"
    ])
    parser.add_argument("--content", help="记忆内容")
    parser.add_argument("--query", help="查询")
    parser.add_argument("--source", default="unknown", help="来源")
    parser.add_argument("--answer", help="原始回答")
    parser.add_argument("--original", help="原始内容（纠正时）")
    parser.add_argument("--method", default="multi_agent", help="验证方法")

    args = parser.parse_args()

    manager = HallucinationIntegratedMemory()

    if args.command == "store":
        if not args.content:
            print("错误: 需要提供 --content")
            return

        memory_id = manager.store(args.content, args.source)
        print(f"✅ 已存储: {memory_id}")

    elif args.command == "recall":
        if not args.query:
            print("错误: 需要提供 --query")
            return

        memories = manager.recall(args.query)
        print(f"找到 {len(memories)} 条记忆:")
        for m in memories:
            print(f"  [{m['confidence']:.2f}] {m['content'][:50]}...")

    elif args.command == "check":
        if not args.query:
            print("错误: 需要提供 --query")
            return

        result = manager.pre_check(args.query)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "generate":
        if not args.query:
            print("错误: 需要提供 --query")
            return

        result = manager.generate_answer(args.query, raw_answer=args.answer)
        print(f"回答: {result['answer']}")
        print(f"置信度: {result['confidence']:.2f}")
        print(f"来源: {result['sources']}")

    elif args.command == "correct":
        if not args.original or not args.content:
            print("错误: 需要提供 --original 和 --content")
            return

        result = manager.handle_user_correction(args.original, args.content)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "verify":
        result = manager.batch_verify(args.method)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "stats":
        stats = manager.get_statistics()
        print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
