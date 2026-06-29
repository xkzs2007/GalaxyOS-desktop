#!/usr/bin/env python3
"""
规划引擎模块 (Planning Engine Module)

基于 Generative Agents 论文实现的规划机制：
- 基于记忆生成行动计划
- 支持短期、中期、长期规划
- 动态调整计划
- 与反思机制联动

论文参考: Generative Agents: Interactive Simulacra (2023)
https://arxiv.org/abs/2304.03442

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import re

try:
    from .memory_stream import Memory, MemoryStream, MemoryType
    from .retrieval_formula import MemoryRetriever
    from .reflection_engine import ReflectionEngine, Insight
except ImportError:
    from memory_stream import Memory, MemoryStream, MemoryType
    from retrieval_formula import MemoryRetriever
    from reflection_engine import ReflectionEngine, Insight


# ==================== 数据结构 ====================

class PlanHorizon(Enum):
    """规划时间范围"""
    IMMEDIATE = "immediate"    # 立即执行（几小时内）
    SHORT_TERM = "short_term"  # 短期（几天内）
    MEDIUM_TERM = "medium_term"  # 中期（几周内）
    LONG_TERM = "long_term"    # 长期（几个月以上）


class ActionPriority(Enum):
    """行动优先级"""
    CRITICAL = "critical"  # 紧急且重要
    HIGH = "high"          # 高优先级
    MEDIUM = "medium"      # 中等优先级
    LOW = "low"            # 低优先级


class ActionStatus(Enum):
    """行动状态"""
    PENDING = "pending"        # 待执行
    IN_PROGRESS = "in_progress"  # 进行中
    COMPLETED = "completed"    # 已完成
    CANCELLED = "cancelled"    # 已取消
    DEFERRED = "deferred"      # 已延期


@dataclass
class Action:
    """
    行动项
    
    代表一个具体的可执行任务
    """
    id: str
    title: str
    description: str
    priority: ActionPriority
    status: ActionStatus
    horizon: PlanHorizon
    
    # 时间信息
    created_at: datetime
    deadline: Optional[datetime] = None
    estimated_duration: Optional[int] = None  # 分钟
    
    # 关联信息
    source_memory_ids: List[str] = field(default_factory=list)
    source_insight_ids: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他行动 ID
    
    # 元数据
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority.value,
            "status": self.status.value,
            "horizon": self.horizon.value,
            "created_at": self.created_at.isoformat(),
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "estimated_duration": self.estimated_duration,
            "source_memory_ids": self.source_memory_ids,
            "source_insight_ids": self.source_insight_ids,
            "dependencies": self.dependencies,
            "tags": self.tags,
            "metadata": self.metadata
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Action':
        return cls(
            id=data["id"],
            title=data["title"],
            description=data["description"],
            priority=ActionPriority(data["priority"]),
            status=ActionStatus(data["status"]),
            horizon=PlanHorizon(data["horizon"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            deadline=datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None,
            estimated_duration=data.get("estimated_duration"),
            source_memory_ids=data.get("source_memory_ids", []),
            source_insight_ids=data.get("source_insight_ids", []),
            dependencies=data.get("dependencies", []),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {})
        )


@dataclass
class Plan:
    """
    计划
    
    包含一组相关的行动项
    """
    id: str
    title: str
    description: str
    horizon: PlanHorizon
    actions: List[Action]
    created_at: datetime
    updated_at: datetime
    
    # 关联
    source_insights: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "horizon": self.horizon.value,
            "actions": [a.to_dict() for a in self.actions],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "source_insights": self.source_insights
        }


# ==================== 规划引擎 ====================

class PlanningEngine:
    """
    规划引擎
    
    核心功能：
    1. 基于记忆和洞察生成行动计划
    2. 管理计划的执行状态
    3. 动态调整计划
    4. 与反思机制联动
    """
    
    def __init__(
        self,
        memory_stream: MemoryStream,
        reflection_engine: Optional[ReflectionEngine] = None,
        llm_client: Optional[Any] = None
    ):
        """
        初始化规划引擎
        
        Args:
            memory_stream: 记忆流实例
            reflection_engine: 反思引擎实例（可选）
            llm_client: LLM 客户端
        """
        self.memory_stream = memory_stream
        self.reflection_engine = reflection_engine
        self.llm_client = llm_client
        self.retriever = MemoryRetriever()
        
        # 计划存储
        self.plans: Dict[str, Plan] = {}
        self.actions: Dict[str, Action] = {}
    
    def plan(
        self,
        context: str,
        horizon: PlanHorizon = PlanHorizon.SHORT_TERM,
        use_reflections: bool = True,
        max_actions: int = 10
    ) -> Plan:
        """
        生成行动计划
        
        Args:
            context: 规划上下文/目标
            horizon: 规划时间范围
            use_reflections: 是否使用反思结果
            max_actions: 最大行动数
            
        Returns:
            生成的计划
        """
        # 1. 检索相关记忆
        relevant_memories = self._retrieve_relevant_memories(context)
        
        # 2. 获取反思洞察
        insights = []
        if use_reflections and self.reflection_engine:
            insights = self._get_recent_insights()
        
        # 3. 生成行动项
        actions = self._generate_actions(
            context, 
            relevant_memories, 
            insights, 
            horizon,
            max_actions
        )
        
        # 4. 创建计划
        now = datetime.now(timezone.utc)
        plan = Plan(
            id=f"plan_{now.strftime('%Y%m%d%H%M%S')}",
            title=f"计划: {context[:50]}",
            description=context,
            horizon=horizon,
            actions=actions,
            created_at=now,
            updated_at=now,
            source_insights=[i.id for i in insights]
        )
        
        # 5. 存储计划
        self.plans[plan.id] = plan
        for action in actions:
            self.actions[action.id] = action
        
        # 6. 将计划存入记忆流
        self._store_plan_as_memory(plan)
        
        return plan
    
    def get_next_actions(self, limit: int = 5) -> List[Action]:
        """
        获取下一步要执行的行动
        
        按优先级和截止时间排序
        """
        pending_actions = [
            a for a in self.actions.values()
            if a.status == ActionStatus.PENDING
        ]
        
        # 排序：优先级 > 截止时间
        priority_order = {
            ActionPriority.CRITICAL: 0,
            ActionPriority.HIGH: 1,
            ActionPriority.MEDIUM: 2,
            ActionPriority.LOW: 3
        }
        
        pending_actions.sort(
            key=lambda a: (
                priority_order.get(a.priority, 3),
                a.deadline or datetime.max.replace(tzinfo=timezone.utc)
            )
        )
        
        return pending_actions[:limit]
    
    def update_action_status(
        self,
        action_id: str,
        status: ActionStatus
    ) -> Optional[Action]:
        """更新行动状态"""
        if action_id not in self.actions:
            return None
        
        action = self.actions[action_id]
        action.status = status
        
        # 更新关联计划
        for plan in self.plans.values():
            if action_id in [a.id for a in plan.actions]:
                plan.updated_at = datetime.now(timezone.utc)
        
        return action
    
    def replan(
        self,
        plan_id: str,
        reason: str
    ) -> Optional[Plan]:
        """
        重新规划
        
        当情况变化时调整计划
        """
        if plan_id not in self.plans:
            return None
        
        old_plan = self.plans[plan_id]
        
        # 获取已完成和未完成的行动
        completed = [a for a in old_plan.actions if a.status == ActionStatus.COMPLETED]
        pending = [a for a in old_plan.actions if a.status == ActionStatus.PENDING]
        
        # 生成新的行动项
        new_context = f"{old_plan.description}\n调整原因: {reason}"
        new_plan = self.plan(
            new_context,
            horizon=old_plan.horizon,
            max_actions=len(pending) + 5
        )
        
        # 合并已完成的行动
        new_plan.actions = completed + new_plan.actions
        new_plan.updated_at = datetime.now(timezone.utc)
        
        return new_plan
    
    def get_daily_plan(self) -> Plan:
        """生成今日计划"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        context = f"今日计划 ({today})"
        
        return self.plan(
            context,
            horizon=PlanHorizon.IMMEDIATE,
            max_actions=10
        )
    
    def get_status(self) -> Dict[str, Any]:
        """获取规划引擎状态"""
        total_actions = len(self.actions)
        pending = len([a for a in self.actions.values() if a.status == ActionStatus.PENDING])
        completed = len([a for a in self.actions.values() if a.status == ActionStatus.COMPLETED])
        
        return {
            "total_plans": len(self.plans),
            "total_actions": total_actions,
            "pending_actions": pending,
            "completed_actions": completed,
            "completion_rate": completed / total_actions if total_actions > 0 else 0
        }
    
    # ==================== 私有方法 ====================
    
    def _retrieve_relevant_memories(self, context: str) -> List[Memory]:
        """检索相关记忆"""
        all_memories = self.memory_stream.get_all()
        
        # 排除计划类记忆（避免循环）
        non_plan_memories = [
            m for m in all_memories
            if m.memory_type != MemoryType.PLAN
        ]
        
        return self.retriever.retrieve_for_planning(
            non_plan_memories,
            context,
            top_k=20
        )
    
    def _get_recent_insights(self) -> List[Insight]:
        """获取最近的反思洞察"""
        if not self.reflection_engine:
            return []
        
        # 从记忆流中获取最近的反思记忆
        reflections = self.memory_stream.get_by_type(MemoryType.REFLECTION)
        recent = [
            m for m in reflections
            if (datetime.now(timezone.utc) - m.created_at).total_seconds() < 7 * 24 * 3600  # 一周内
        ]
        
        # 转换为 Insight 格式
        insights = []
        for m in recent[:5]:
            insights.append(Insight(
                id=m.metadata.get("insight_id", m.id),
                content=m.content.replace("[反思] ", ""),
                source_memories=m.metadata.get("source_memories", []),
                created_at=m.created_at,
                confidence=m.metadata.get("confidence", 0.7),
                tags=m.metadata.get("tags", [])
            ))
        
        return insights
    
    def _generate_actions(
        self,
        context: str,
        memories: List[Memory],
        insights: List[Insight],
        horizon: PlanHorizon,
        max_actions: int
    ) -> List[Action]:
        """生成行动项"""
        # 构建提示词
        prompt = self._build_planning_prompt(context, memories, insights, horizon)
        
        # 调用 LLM
        if self.llm_client:
            response = self._call_llm(prompt)
            actions = self._parse_llm_actions(response, memories, insights, horizon)
        else:
            # 无 LLM 时使用规则生成
            actions = self._rule_based_actions(context, memories, insights, horizon)
        
        return actions[:max_actions]
    
    def _build_planning_prompt(
        self,
        context: str,
        memories: List[Memory],
        insights: List[Insight],
        horizon: PlanHorizon
    ) -> str:
        """构建规划提示词"""
        memory_summaries = [
            f"- [{m.memory_type.value}] {m.content}"
            for m in memories[:20]
        ]
        
        insight_summaries = [
            f"- {i.content}"
            for i in insights
        ]
        
        horizon_desc = {
            PlanHorizon.IMMEDIATE: "几小时内完成",
            PlanHorizon.SHORT_TERM: "几天内完成",
            PlanHorizon.MEDIUM_TERM: "几周内完成",
            PlanHorizon.LONG_TERM: "几个月内完成"
        }
        
        return f"""你是一个具有规划能力的 AI 助手。请基于以下信息制定行动计划。

## 目标
{context}

## 时间范围
{horizon_desc.get(horizon, "短期")}

## 相关记忆
{chr(10).join(memory_summaries) if memory_summaries else "暂无"}

## 反思洞察
{chr(10).join(insight_summaries) if insight_summaries else "暂无"}

## 要求
1. 生成 3-10 个具体的行动项
2. 每个行动项应该明确、可执行
3. 考虑优先级和依赖关系
4. 以 JSON 格式输出：{{"actions": [{{"title": "...", "description": "...", "priority": "high/medium/low", "estimated_duration": 60}}]}}

请输出你的行动计划："""
    
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
    
    def _parse_llm_actions(
        self,
        response: str,
        memories: List[Memory],
        insights: List[Insight],
        horizon: PlanHorizon
    ) -> List[Action]:
        """解析 LLM 响应为行动项"""
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                actions_data = data.get("actions", [])
                
                actions = []
                now = datetime.now(timezone.utc)
                
                for i, item in enumerate(actions_data):
                    priority_str = item.get("priority", "medium").lower()
                    priority_map = {
                        "critical": ActionPriority.CRITICAL,
                        "high": ActionPriority.HIGH,
                        "medium": ActionPriority.MEDIUM,
                        "low": ActionPriority.LOW
                    }
                    
                    action = Action(
                        id=f"action_{now.strftime('%Y%m%d%H%M%S')}_{i}",
                        title=item.get("title", f"行动 {i+1}"),
                        description=item.get("description", ""),
                        priority=priority_map.get(priority_str, ActionPriority.MEDIUM),
                        status=ActionStatus.PENDING,
                        horizon=horizon,
                        created_at=now,
                        estimated_duration=item.get("estimated_duration"),
                        source_memory_ids=[m.id for m in memories[:5]],
                        source_insight_ids=[i.id for i in insights[:3]]
                    )
                    actions.append(action)
                
                return actions
        except Exception as e:
            print(f"[PlanningEngine] 解析 LLM 响应失败: {e}")
        
        return self._rule_based_actions("", memories, insights, horizon)
    
    def _rule_based_actions(
        self,
        context: str,
        memories: List[Memory],
        insights: List[Insight],
        horizon: PlanHorizon
    ) -> List[Action]:
        """基于规则的行动生成"""
        actions = []
        now = datetime.now(timezone.utc)
        
        # 从记忆中提取待办事项
        for m in memories[:5]:
            if "需要" in m.content or "应该" in m.content or "计划" in m.content:
                actions.append(Action(
                    id=f"action_{now.strftime('%Y%m%d%H%M%S')}_{len(actions)}",
                    title=m.content[:50],
                    description=f"来自记忆: {m.content}",
                    priority=ActionPriority.MEDIUM,
                    status=ActionStatus.PENDING,
                    horizon=horizon,
                    created_at=now,
                    source_memory_ids=[m.id]
                ))
        
        # 从洞察中生成行动
        for insight in insights[:3]:
            actions.append(Action(
                id=f"action_{now.strftime('%Y%m%d%H%M%S')}_{len(actions)}",
                title=f"应用洞察: {insight.content[:30]}",
                description=insight.content,
                priority=ActionPriority.HIGH if insight.confidence > 0.7 else ActionPriority.MEDIUM,
                status=ActionStatus.PENDING,
                horizon=horizon,
                created_at=now,
                source_insight_ids=[insight.id]
            ))
        
        # 默认行动
        if not actions:
            actions.append(Action(
                id=f"action_{now.strftime('%Y%m%d%H%M%S')}_default",
                title="收集更多信息",
                description="需要更多记忆来生成具体行动",
                priority=ActionPriority.LOW,
                status=ActionStatus.PENDING,
                horizon=horizon,
                created_at=now
            ))
        
        return actions
    
    def _store_plan_as_memory(self, plan: Plan):
        """将计划存入记忆流"""
        action_summary = "\n".join([
            f"- [{a.priority.value}] {a.title}"
            for a in plan.actions
        ])
        
        content = f"[计划] {plan.title}\n\n行动项:\n{action_summary}"
        
        self.memory_stream.add(
            content=content,
            memory_type=MemoryType.PLAN,
            importance=7.0,
            metadata={
                "plan_id": plan.id,
                "horizon": plan.horizon.value,
                "action_count": len(plan.actions)
            }
        )


# ==================== 便捷函数 ====================

def create_planning_engine(
    memory_stream: MemoryStream,
    reflection_engine: Optional[ReflectionEngine] = None,
    llm_client: Optional[Any] = None
) -> PlanningEngine:
    """创建规划引擎实例"""
    return PlanningEngine(memory_stream, reflection_engine, llm_client)


def plan(
    context: str,
    memory_stream: Optional[MemoryStream] = None,
    llm_client: Optional[Any] = None
) -> List[Action]:
    """
    规划接口（符合任务要求的接口约定）
    
    Args:
        context: 规划上下文
        memory_stream: 记忆流实例
        llm_client: LLM 客户端
        
    Returns:
        行动列表
    """
    if memory_stream is None:
        memory_stream = MemoryStream()
    
    engine = PlanningEngine(memory_stream, llm_client=llm_client)
    plan_obj = engine.plan(context)
    
    return plan_obj.actions


if __name__ == "__main__":
    # 测试代码
    stream = MemoryStream()
    
    # 添加测试记忆
    stream.add("需要学习 Docker 容器技术", MemoryType.OBSERVATION, importance=7.0)
    stream.add("项目需要容器化部署", MemoryType.OBSERVATION, importance=8.0)
    stream.add("已经学习了 Kubernetes 基础", MemoryType.ACTION, importance=6.0)
    
    # 创建规划引擎
    engine = PlanningEngine(stream)
    
    # 生成计划
    plan_obj = engine.plan("学习容器技术并应用到项目中")
    
    print(f"计划: {plan_obj.title}")
    print(f"行动数: {len(plan_obj.actions)}")
    print("\n行动列表:")
    for action in plan_obj.actions:
        print(f"  [{action.priority.value}] {action.title}")
    
    # 获取下一步行动
    next_actions = engine.get_next_actions()
    print(f"\n下一步行动: {len(next_actions)} 个")
