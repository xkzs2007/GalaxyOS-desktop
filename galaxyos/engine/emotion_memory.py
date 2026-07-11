#!/usr/bin/env python3
"""
情感驱动记忆 (Emotion-Driven Memory)

情绪强度影响记忆权重：
- 强烈情绪（开心/生气/焦虑）→ 高权重 → 长期记忆
- 平淡情绪 → 低权重 → 短期记忆

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-19
"""

import re
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
from galaxyos.shared.paths import workspace


# ==================== 情绪类型 ====================

class EmotionType(Enum):
    """情绪类型"""
    JOY = "joy"           # 开心
    EXCITEMENT = "excitement"  # 兴奋
    ANGER = "anger"       # 生气
    ANXIETY = "anxiety"   # 焦虑
    SADNESS = "sadness"   # 悲伤
    FRUSTRATION = "frustration"  # 挫败
    NEUTRAL = "neutral"   # 平淡
    CURIOSITY = "curiosity"  # 好奇


@dataclass
class EmotionScore:
    """情绪评分"""
    type: EmotionType
    intensity: float  # 0.0 - 1.0
    confidence: float = 1.0  # 检测置信度

    def to_dict(self) -> Dict:
        return {
            "type": self.type.value,
            "intensity": self.intensity,
            "confidence": self.confidence
        }


# ==================== 情绪检测器 ====================

class EmotionDetector:
    """
    情绪检测器
    
    基于关键词和模式检测用户情绪
    """

    # 情绪关键词
    EMOTION_KEYWORDS = {
        EmotionType.JOY: [
            "开心", "高兴", "太好了", "棒", "赞", "厉害", "牛", "强",
            "happy", "great", "awesome", "amazing", "love it"
        ],
        EmotionType.EXCITEMENT: [
            "激动", "兴奋", "期待", "迫不及待", "太棒了",
            "excited", "can't wait", "thrilled"
        ],
        EmotionType.ANGER: [
            "生气", "愤怒", "烦死了", "气死", "讨厌", "恶心",
            "angry", "hate", "annoying", "frustrated"
        ],
        EmotionType.ANXIETY: [
            "焦虑", "担心", "害怕", "紧张", "不安", "压力",
            "anxious", "worried", "nervous", "stressed"
        ],
        EmotionType.SADNESS: [
            "难过", "伤心", "悲伤", "失落", "沮丧", "郁闷",
            "sad", "depressed", "down", "upset"
        ],
        EmotionType.FRUSTRATION: [
            "挫败", "失败", "不行", "做不到", "太难了",
            "frustrated", "failed", "can't do", "too hard"
        ],
        EmotionType.CURIOSITY: [
            "好奇", "想知道", "有趣", "有意思", "为什么",
            "curious", "wonder", "interesting", "why"
        ],
    }

    # 强度修饰词
    INTENSITY_MODIFIERS = {
        "very": 0.3,
        "really": 0.3,
        "so": 0.2,
        "太": 0.3,
        "超": 0.3,
        "特别": 0.2,
        "非常": 0.2,
        "有点": -0.2,
        "稍微": -0.3,
        "一点": -0.2,
    }

    def detect(self, text: str) -> EmotionScore:
        """
        检测情绪
        
        Args:
            text: 用户消息
        
        Returns:
            EmotionScore
        """
        text_lower = text.lower()

        # 统计各情绪的匹配数
        emotion_scores: Dict[EmotionType, float] = {}

        for emotion_type, keywords in self.EMOTION_KEYWORDS.items():
            score = 0.0
            for keyword in keywords:
                if keyword.lower() in text_lower:
                    # 基础分数
                    base_score = 0.5

                    # 检查强度修饰词
                    for modifier, adjustment in self.INTENSITY_MODIFIERS.items():
                        if modifier in text_lower:
                            base_score += adjustment

                    score = max(score, min(1.0, max(0.0, base_score)))

            if score > 0:
                emotion_scores[emotion_type] = score

        # 如果没有检测到情绪，返回平淡
        if not emotion_scores:
            return EmotionScore(
                type=EmotionType.NEUTRAL,
                intensity=0.3,
                confidence=0.5
            )

        # 返回得分最高的情绪
        best_emotion = max(emotion_scores.items(), key=lambda x: x[1])

        return EmotionScore(
            type=best_emotion[0],
            intensity=best_emotion[1],
            confidence=0.8
        )


# ==================== 情感权重计算器 ====================

class EmotionWeightCalculator:
    """情感权重计算器"""

    # 情绪基础权重（强烈情绪更高）
    EMOTION_BASE_WEIGHTS = {
        EmotionType.JOY: 0.8,
        EmotionType.EXCITEMENT: 0.85,
        EmotionType.ANGER: 0.9,
        EmotionType.ANXIETY: 0.85,
        EmotionType.SADNESS: 0.75,
        EmotionType.FRUSTRATION: 0.8,
        EmotionType.CURIOSITY: 0.7,
        EmotionType.NEUTRAL: 0.3,
    }

    def calculate(self, emotion: EmotionScore) -> float:
        """
        计算情感权重
        
        Args:
            emotion: 情绪评分
        
        Returns:
            权重值 (0.0 - 1.0)
        """
        base_weight = self.EMOTION_BASE_WEIGHTS.get(emotion.type, 0.5)

        # 情绪强度影响
        # intensity 越高，权重越接近 base_weight
        # intensity 越低，权重越低
        adjusted_weight = base_weight * (0.5 + 0.5 * emotion.intensity)

        return round(min(1.0, max(0.0, adjusted_weight)), 3)

    def get_memory_priority(self, emotion: EmotionScore) -> str:
        """
        获取记忆优先级
        
        Args:
            emotion: 情绪评分
        
        Returns:
            "high" | "medium" | "low"
        """
        weight = self.calculate(emotion)

        if weight >= 0.7:
            return "high"
        elif weight >= 0.5:
            return "medium"
        else:
            return "low"


# ==================== 情感记忆管理器 ====================

class EmotionMemoryManager:
    """
    情感记忆管理器
    
    集成情绪检测和权重计算
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or workspace())
        self.emotion_memory_path = self.workspace_path / ".learnings" / "emotion_memories.jsonl"

        # 确保目录存在
        self.emotion_memory_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.emotion_memory_path.exists():
            self.emotion_memory_path.touch()

        self.detector = EmotionDetector()
        self.calculator = EmotionWeightCalculator()

    def _get_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def process_message(
        self,
        user_message: str,
        memory_content: str = None
    ) -> Dict:
        """
        处理用户消息
        
        Args:
            user_message: 用户消息
            memory_content: 要存储的记忆内容（可选，默认使用用户消息）
        
        Returns:
            {
                "emotion": EmotionScore,
                "weight": float,
                "priority": str,
                "memory_id": str
            }
        """
        # 检测情绪
        emotion = self.detector.detect(user_message)

        # 计算权重
        weight = self.calculator.calculate(emotion)

        # 获取优先级
        priority = self.calculator.get_memory_priority(emotion)

        # 存储情感记忆
        memory_id = self._store_emotion_memory(
            content=memory_content or user_message,
            emotion=emotion,
            weight=weight,
            priority=priority
        )

        return {
            "emotion": emotion.to_dict(),
            "weight": weight,
            "priority": priority,
            "memory_id": memory_id
        }

    def _store_emotion_memory(
        self,
        content: str,
        emotion: EmotionScore,
        weight: float,
        priority: str
    ) -> str:
        """存储情感记忆"""
        import hashlib

        memory_id = hashlib.md5(f"{content}{self._get_timestamp()}".encode()).hexdigest()[:12]

        record = {
            "id": memory_id,
            "content": content,
            "emotion": emotion.to_dict(),
            "weight": weight,
            "priority": priority,
            "timestamp": self._get_timestamp()
        }

        with open(self.emotion_memory_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return memory_id

    def get_high_priority_memories(self, limit: int = 10) -> List[Dict]:
        """获取高优先级记忆"""
        memories = []

        with open(self.emotion_memory_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    memory = json.loads(line)
                    if memory["priority"] == "high":
                        memories.append(memory)

        # 按权重排序
        memories.sort(key=lambda x: x["weight"], reverse=True)

        return memories[:limit]

    def get_emotion_stats(self) -> Dict:
        """获取情绪统计"""
        emotion_counts = {}
        total_weight = 0
        count = 0

        with open(self.emotion_memory_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    memory = json.loads(line)
                    emotion_type = memory["emotion"]["type"]
                    emotion_counts[emotion_type] = emotion_counts.get(emotion_type, 0) + 1
                    total_weight += memory["weight"]
                    count += 1

        return {
            "total_memories": count,
            "emotion_distribution": emotion_counts,
            "avg_weight": round(total_weight / count, 3) if count > 0 else 0,
            "high_priority_count": emotion_counts.get("high", 0)
        }


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="情感驱动记忆")
    parser.add_argument("command", choices=["detect", "process", "stats", "high-priority"])
    parser.add_argument("--message", help="用户消息")
    parser.add_argument("--limit", type=int, default=10, help="返回数量限制")

    args = parser.parse_args()

    manager = EmotionMemoryManager()

    if args.command == "detect":
        if not args.message:
            print("错误: 需要提供 --message")
            return

        emotion = manager.detector.detect(args.message)
        weight = manager.calculator.calculate(emotion)
        priority = manager.calculator.get_memory_priority(emotion)

        print(f"情绪: {emotion.type.value}")
        print(f"强度: {emotion.intensity}")
        print(f"权重: {weight}")
        print(f"优先级: {priority}")

    elif args.command == "process":
        if not args.message:
            print("错误: 需要提供 --message")
            return

        result = manager.process_message(args.message)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "stats":
        stats = manager.get_emotion_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    elif args.command == "high-priority":
        memories = manager.get_high_priority_memories(args.limit)
        for memory in memories:
            print(f"[{memory['priority']}] {memory['content'][:50]}... (权重: {memory['weight']})")


if __name__ == "__main__":
    main()
