#!/usr/bin/env python3
"""
反思引擎模块 (Reflection Engine Module)

基于 Generative Agents 论文实现的反思机制：
- 定期从记忆中提取洞察
- 生成高层次抽象
- 发现模式和规律
- 自动触发反思

论文参考: Generative Agents: Interactive Simulacra (2023)
https://arxiv.org/abs/2304.03442

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import json
import random
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import re

try:
    from .memory_stream import Memory, MemoryStream, MemoryType
    from .retrieval_formula import MemoryRetriever, RetrievalConfig
except ImportError:
    from memory_stream import Memory, MemoryStream, MemoryType
    from retrieval_formula import MemoryRetriever, RetrievalConfig


# ==================== 配置 ====================

@dataclass
class ReflectionConfig:
    """反思配置"""
    # 触发条件
    min_memories_for_reflection: int = 10  # 最少记忆数才触发
    reflection_interval_hours: float = 24.0  # 反思间隔（小时）
    importance_threshold: float = 5.0  # 重要性阈值

    # 检索参数
    memories_per_reflection: int = 100  # 每次反思处理的记忆数

    # LLM 配置
    llm_model: str = "default"
    max_insights: int = 5  # 每次反思最多生成的洞察数


class ReflectionTrigger(Enum):
    """反思触发类型"""
    SCHEDULED = "scheduled"      # 定时触发
    THRESHOLD = "threshold"      # 阈值触发（记忆数达到阈值）
    IMPORTANCE = "importance"    # 高重要性事件触发
    MANUAL = "manual"           # 手动触发


@dataclass
class Insight:
    """洞察/反思结果"""
    id: str
    content: str
    source_memories: List[str]  # 来源记忆 ID
    created_at: datetime
    confidence: float  # 置信度 0-1
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "content": self.content,
            "source_memories": self.source_memories,
            "created_at": self.created_at.isoformat(),
            "confidence": self.confidence,
            "tags": self.tags
        }


# ==================== 反思引擎 ====================

class ReflectionEngine:
    """
    反思引擎
    
    核心功能：
    1. 检测反思触发条件
    2. 从记忆流中检索相关记忆
    3. 使用 LLM 生成洞察
    4. 将洞察存回记忆流
    """

    def __init__(
        self,
        memory_stream: MemoryStream,
        config: Optional[ReflectionConfig] = None,
        llm_client: Optional[Any] = None
    ):
        """
        初始化反思引擎
        
        Args:
            memory_stream: 记忆流实例
            config: 反思配置
            llm_client: LLM 客户端（用于生成洞察）
        """
        self.memory_stream = memory_stream
        self.config = config or ReflectionConfig()
        self.llm_client = llm_client
        self.retriever = MemoryRetriever()

        # 状态追踪
        self._last_reflection_time: Optional[datetime] = None
        self._reflection_count: int = 0
        self._pending_reflection: bool = False

    def should_reflect(self) -> Tuple[bool, ReflectionTrigger]:
        """
        检查是否应该触发反思
        
        Returns:
            (should_reflect, trigger_type)
        """
        now = datetime.now(timezone.utc)
        memories = self.memory_stream.get_all()

        # 1. 检查定时触发
        if self._last_reflection_time:
            elapsed = (now - self._last_reflection_time).total_seconds() / 3600
            if elapsed >= self.config.reflection_interval_hours:
                return True, ReflectionTrigger.SCHEDULED
        else:
            # 首次运行，如果记忆数足够则触发
            if len(memories) >= self.config.min_memories_for_reflection:
                return True, ReflectionTrigger.THRESHOLD

        # 2. 检查阈值触发
        if len(memories) >= self.config.min_memories_for_reflection:
            # 检查是否有足够多的未反思记忆
            unreflected = [m for m in memories if m.memory_type != MemoryType.REFLECTION]
            if len(unreflected) >= self.config.min_memories_for_reflection * 2:
                return True, ReflectionTrigger.THRESHOLD

        # 3. 检查高重要性事件
        important_memories = self.memory_stream.get_important(self.config.importance_threshold)
        recent_important = [
            m for m in important_memories
            if (now - m.created_at).total_seconds() < 3600  # 最近1小时
        ]
        if len(recent_important) >= 3:
            return True, ReflectionTrigger.IMPORTANCE

        return False, ReflectionTrigger.MANUAL

    def reflect(
        self,
        trigger: ReflectionTrigger = ReflectionTrigger.MANUAL,
        focus_query: Optional[str] = None
    ) -> List[Insight]:
        """
        执行反思
        
        Args:
            trigger: 触发类型
            focus_query: 聚焦查询（可选）
            
        Returns:
            生成的洞察列表
        """
        # 检索用于反思的记忆
        memories = self._retrieve_memories_for_reflection(focus_query)

        if not memories:
            return []

        # 生成洞察
        insights = self._generate_insights(memories, trigger)

        # 将洞察存入记忆流
        for insight in insights:
            self.memory_stream.add(
                content=f"[反思] {insight.content}",
                memory_type=MemoryType.REFLECTION,
                importance=8.0,  # 反思类记忆高重要性
                metadata={
                    "insight_id": insight.id,
                    "source_memories": insight.source_memories,
                    "confidence": insight.confidence,
                    "trigger": trigger.value
                }
            )

        # 更新状态
        self._last_reflection_time = datetime.now(timezone.utc)
        self._reflection_count += 1
        self._pending_reflection = False

        return insights

    def get_reflection_questions(self, memories: List[Memory]) -> List[str]:
        """
        生成反思问题
        
        这些问题用于引导 LLM 生成洞察
        """
        # 提取记忆主题
        topics = self._extract_topics(memories)

        questions = [
            "从这些经历中，我学到了什么重要的教训？",
            "这些事件之间有什么共同的模式或规律？",
            "有哪些事情我应该改变做法？",
            "哪些决策是正确的，哪些需要改进？",
            "我对自己的认识有什么新的理解？"
        ]

        # 根据主题添加特定问题
        if "学习" in topics:
            questions.append("我的学习方法是否有效？如何改进？")
        if "工作" in topics:
            questions.append("我的工作效率如何？有什么可以优化的地方？")
        if "错误" in topics or "失败" in topics:
            questions.append("从这些错误中，我能学到什么？")

        return questions

    def get_status(self) -> Dict[str, Any]:
        """获取反思引擎状态"""
        should, trigger = self.should_reflect()

        return {
            "last_reflection": self._last_reflection_time.isoformat() if self._last_reflection_time else None,
            "reflection_count": self._reflection_count,
            "should_reflect_now": should,
            "trigger_if_needed": trigger.value if should else None,
            "pending_reflection": self._pending_reflection,
            "config": {
                "interval_hours": self.config.reflection_interval_hours,
                "min_memories": self.config.min_memories_for_reflection
            }
        }

    # ==================== 私有方法 ====================

    def _retrieve_memories_for_reflection(
        self,
        focus_query: Optional[str] = None
    ) -> List[Memory]:
        """检索用于反思的记忆"""
        all_memories = self.memory_stream.get_all()

        # 排除已有的反思记忆（避免循环反思）
        non_reflection_memories = [
            m for m in all_memories
            if m.memory_type != MemoryType.REFLECTION
        ]

        if focus_query:
            # 有聚焦查询，使用相关性检索
            return self.retriever.retrieve_for_planning(
                non_reflection_memories,
                focus_query,
                top_k=self.config.memories_per_reflection
            )
        else:
            # 无聚焦查询，使用反思专用检索
            return self.retriever.retrieve_for_reflection(
                non_reflection_memories,
                top_k=self.config.memories_per_reflection
            )

    def _generate_insights(
        self,
        memories: List[Memory],
        trigger: ReflectionTrigger
    ) -> List[Insight]:
        """使用 LLM 生成洞察"""
        # 准备记忆摘要
        memory_summaries = [
            f"- [{m.memory_type.value}] {m.content}"
            for m in memories[:50]  # 限制数量避免过长
        ]

        # 生成反思问题
        questions = self.get_reflection_questions(memories)

        # 构建提示词
        prompt = self._build_reflection_prompt(memory_summaries, questions, trigger)

        # 调用 LLM
        if self.llm_client:
            response = self._call_llm(prompt)
            insights = self._parse_llm_response(response, memories)
        else:
            # 无 LLM 时使用规则生成
            insights = self._rule_based_insights(memories)

        return insights[:self.config.max_insights]

    def _build_reflection_prompt(
        self,
        memory_summaries: List[str],
        questions: List[str],
        trigger: ReflectionTrigger
    ) -> str:
        """构建反思提示词"""
        return f"""你是一个具有自我反思能力的 AI 助手。请基于以下记忆进行反思。

## 近期记忆
{chr(10).join(memory_summaries)}

## 反思问题
{chr(10).join(f'{i+1}. {q}' for i, q in enumerate(questions))}

## 要求
1. 从记忆中提取 3-5 个深刻的洞察
2. 每个洞察应该是对经验的抽象和总结
3. 洞察应该具有指导意义，能帮助改进未来行为
4. 以 JSON 格式输出：{{"insights": [{{"content": "...", "confidence": 0.8, "tags": ["..."]}}]}}

## 触发原因
本次反思由 {trigger.value} 触发。

请输出你的反思结果："""

    def _call_llm(self, prompt: str) -> str:
        """调用 LLM"""
        if hasattr(self.llm_client, 'generate'):
            return self.llm_client.generate(prompt)
        elif hasattr(self.llm_client, 'chat'):
            return self.llm_client.chat(prompt)
        elif callable(self.llm_client):
            return self.llm_client(prompt)
        else:
            raise ValueError("Invalid LLM client")

    def _parse_llm_response(
        self,
        response: str,
        source_memories: List[Memory]
    ) -> List[Insight]:
        """解析 LLM 响应"""
        try:
            # 尝试提取 JSON
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                insights_data = data.get("insights", [])

                insights = []
                for i, item in enumerate(insights_data):
                    insight = Insight(
                        id=f"insight_{datetime.now().strftime('%Y%m%d%H%M%S')}_{i}",
                        content=item.get("content", ""),
                        source_memories=[m.id for m in source_memories[:10]],
                        created_at=datetime.now(timezone.utc),
                        confidence=item.get("confidence", 0.7),
                        tags=item.get("tags", [])
                    )
                    insights.append(insight)

                return insights
        except Exception as e:
            print(f"[ReflectionEngine] 解析 LLM 响应失败: {e}")

        # 解析失败，使用规则生成
        return self._rule_based_insights(source_memories)

    def _rule_based_insights(self, memories: List[Memory]) -> List[Insight]:
        """基于规则的洞察生成（无 LLM 时的后备方案）"""
        insights = []

        # 分析记忆类型分布
        type_counts = {}
        for m in memories:
            type_counts[m.memory_type.value] = type_counts.get(m.memory_type.value, 0) + 1

        # 生成洞察
        if type_counts.get("action", 0) > 5:
            insights.append(Insight(
                id=f"insight_{datetime.now().strftime('%Y%m%d%H%M%S')}_0",
                content="我执行了很多行动，需要评估这些行动的效果和效率",
                source_memories=[m.id for m in memories if m.memory_type == MemoryType.ACTION][:10],
                created_at=datetime.now(timezone.utc),
                confidence=0.6,
                tags=["行动分析"]
            ))

        # 分析重要性分布
        high_importance = [m for m in memories if m.importance >= 7.0]
        if len(high_importance) > 3:
            insights.append(Insight(
                id=f"insight_{datetime.now().strftime('%Y%m%d%H%M%S')}_1",
                content=f"有 {len(high_importance)} 个高重要性事件，需要特别关注",
                source_memories=[m.id for m in high_importance[:10]],
                created_at=datetime.now(timezone.utc),
                confidence=0.7,
                tags=["重要性分析"]
            ))

        # 默认洞察
        if not insights:
            insights.append(Insight(
                id=f"insight_{datetime.now().strftime('%Y%m%d%H%M%S')}_default",
                content="需要更多记忆来进行深入反思",
                source_memories=[m.id for m in memories[:5]],
                created_at=datetime.now(timezone.utc),
                confidence=0.5,
                tags=["默认"]
            ))

        return insights

    def _extract_topics(self, memories: List[Memory]) -> List[str]:
        """提取记忆主题"""
        topics = []
        keywords = {
            "学习": ["学习", "学会", "掌握", "理解", "教程", "课程"],
            "工作": ["工作", "任务", "项目", "完成", "进度"],
            "错误": ["错误", "失败", "问题", "bug", "异常"],
            "计划": ["计划", "打算", "准备", "安排", "目标"]
        }

        for topic, kws in keywords.items():
            for m in memories:
                if any(kw in m.content for kw in kws):
                    topics.append(topic)
                    break

        return topics


# ==================== 便捷函数 ====================

def create_reflection_engine(
    memory_stream: MemoryStream,
    llm_client: Optional[Any] = None
) -> ReflectionEngine:
    """创建反思引擎实例"""
    return ReflectionEngine(memory_stream, llm_client=llm_client)


def reflect(
    recent_memories: List[Memory],
    llm_client: Optional[Any] = None
) -> List[str]:
    """
    反思接口（符合任务要求的接口约定）
    
    Args:
        recent_memories: 近期记忆列表
        llm_client: LLM 客户端
        
    Returns:
        洞察内容列表
    """
    # 创建临时记忆流
    stream = MemoryStream()
    for m in recent_memories:
        stream.memories[m.id] = m

    # 创建反思引擎
    engine = ReflectionEngine(stream, llm_client=llm_client)

    # 执行反思
    insights = engine.reflect()

    return [i.content for i in insights]


if __name__ == "__main__":
    # 测试代码
    stream = MemoryStream()

    # 添加测试记忆
    stream.add("完成了 Python 项目开发", MemoryType.ACTION, importance=7.0)
    stream.add("学习了异步编程模式", MemoryType.OBSERVATION, importance=6.0)
    stream.add("修复了一个关键 bug", MemoryType.ACTION, importance=8.0)
    stream.add("代码审查发现了几个问题", MemoryType.OBSERVATION, importance=5.0)

    # 创建反思引擎
    engine = ReflectionEngine(stream)

    # 检查是否应该反思
    should, trigger = engine.should_reflect()
    print(f"应该反思: {should}, 触发类型: {trigger.value}")

    # 执行反思
    insights = engine.reflect()
    print(f"\n生成了 {len(insights)} 个洞察:")
    for insight in insights:
        print(f"  - {insight.content}")
