#!/usr/bin/env python3
"""
Generative Agents 模块单元测试

测试覆盖：
- memory_stream.py: 记忆流
- retrieval_formula.py: 检索公式
- reflection_engine.py: 反思引擎
- planning_engine.py: 规划引擎

Author: GalaxyOS
Version: 1.0.0
Created: 2026-04-21
"""

import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 导入被测模块
from memory_stream import (
    MemoryStream, Memory, MemoryType,
    create_memory_stream
)
from retrieval_formula import (
    MemoryRetriever, RetrievalConfig, RetrievalWeights,
    calculate_recency, calculate_relevance, calculate_importance,
    cosine_similarity, keyword_relevance, retrieve_memories
)
from reflection_engine import (
    ReflectionEngine, ReflectionConfig, ReflectionTrigger, Insight,
    reflect, create_reflection_engine
)
from planning_engine import (
    PlanningEngine, Plan, Action, ActionPriority, ActionStatus, PlanHorizon,
    plan, create_planning_engine
)


# ==================== 记忆流测试 ====================

class TestMemoryStream(unittest.TestCase):
    """记忆流模块测试"""

    def setUp(self):
        """测试前准备"""
        self.stream = MemoryStream()

    def test_add_memory(self):
        """测试添加记忆"""
        memory = self.stream.add(
            content="测试记忆内容",
            memory_type=MemoryType.OBSERVATION,
            importance=7.0
        )

        self.assertIsNotNone(memory.id)
        self.assertEqual(memory.content, "测试记忆内容")
        self.assertEqual(memory.memory_type, MemoryType.OBSERVATION)
        self.assertEqual(memory.importance, 7.0)
        self.assertEqual(len(self.stream.get_all()), 1)

    def test_importance_range(self):
        """测试重要性评分范围限制"""
        # 超出上限
        m1 = self.stream.add("test", importance=15.0)
        self.assertEqual(m1.importance, 10.0)

        # 超出下限
        m2 = self.stream.add("test", importance=-5.0)
        self.assertEqual(m2.importance, 1.0)

    def test_auto_importance(self):
        """测试自动计算重要性"""
        # 反思类默认高分
        m1 = self.stream.add("反思内容", memory_type=MemoryType.REFLECTION)
        self.assertGreaterEqual(m1.importance, 8.0)

        # 计划类中等分数
        m2 = self.stream.add("计划内容", memory_type=MemoryType.PLAN)
        self.assertGreaterEqual(m2.importance, 5.0)

    def test_get_by_type(self):
        """测试按类型获取"""
        self.stream.add("观察1", memory_type=MemoryType.OBSERVATION)
        self.stream.add("反思1", memory_type=MemoryType.REFLECTION)
        self.stream.add("观察2", memory_type=MemoryType.OBSERVATION)

        observations = self.stream.get_by_type(MemoryType.OBSERVATION)
        reflections = self.stream.get_by_type(MemoryType.REFLECTION)

        self.assertEqual(len(observations), 2)
        self.assertEqual(len(reflections), 1)

    def test_get_recent(self):
        """测试获取最近记忆"""
        self.stream.add("记忆1", memory_type=MemoryType.OBSERVATION)
        self.stream.add("记忆2", memory_type=MemoryType.OBSERVATION)

        recent = self.stream.get_recent(hours=1)
        self.assertEqual(len(recent), 2)

    def test_get_important(self):
        """测试获取高重要性记忆"""
        self.stream.add("普通", importance=5.0)
        self.stream.add("重要", importance=8.0)
        self.stream.add("非常", importance=9.5)

        important = self.stream.get_important(threshold=7.0)
        self.assertEqual(len(important), 2)

    def test_delete_memory(self):
        """测试删除记忆"""
        m = self.stream.add("待删除", importance=5.0)
        self.assertEqual(len(self.stream.get_all()), 1)

        result = self.stream.delete(m.id)
        self.assertTrue(result)
        self.assertEqual(len(self.stream.get_all()), 0)

    def test_touch_updates_access(self):
        """测试访问更新"""
        m = self.stream.add("测试", importance=5.0)
        initial_count = m.access_count

        retrieved = self.stream.get(m.id)

        self.assertEqual(retrieved.access_count, initial_count + 1)

    def test_persistence(self):
        """测试持久化"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_memory.json")

            # 创建并保存
            stream1 = MemoryStream(path)
            stream1.add("持久化测试", importance=7.0)

            # 重新加载
            stream2 = MemoryStream(path)
            self.assertEqual(len(stream2.get_all()), 1)
            self.assertEqual(stream2.get_all()[0].content, "持久化测试")

    def test_stats(self):
        """测试统计信息"""
        self.stream.add("观察", memory_type=MemoryType.OBSERVATION, importance=5.0)
        self.stream.add("反思", memory_type=MemoryType.REFLECTION, importance=8.0)

        stats = self.stream.get_stats()

        self.assertEqual(stats["total"], 2)
        self.assertIn("observation", stats["by_type"])
        self.assertIn("reflection", stats["by_type"])
        self.assertGreater(stats["avg_importance"], 0)


# ==================== 检索公式测试 ====================

class TestRetrievalFormula(unittest.TestCase):
    """检索公式模块测试"""

    def setUp(self):
        """测试前准备"""
        self.stream = MemoryStream()
        self.retriever = MemoryRetriever()

        # 添加测试记忆
        self.m1 = self.stream.add("Python 编程学习", MemoryType.OBSERVATION, importance=7.0)
        self.m2 = self.stream.add("异步编程很重要", MemoryType.REFLECTION, importance=8.0)
        self.m3 = self.stream.add("明天复习 asyncio", MemoryType.PLAN, importance=6.0)

    def test_calculate_recency(self):
        """测试时效性计算"""
        # 刚创建的记忆，时效性接近 1
        recency = calculate_recency(self.m1)
        self.assertGreater(recency, 0.9)

        # 模拟旧记忆
        old_time = datetime.now(timezone.utc) - timedelta(hours=48)
        old_memory = Memory(
            id="old",
            content="旧记忆",
            memory_type=MemoryType.OBSERVATION,
            importance=5.0,
            created_at=old_time,
            last_accessed=old_time
        )

        old_recency = calculate_recency(old_memory, decay_hours=24.0)
        self.assertLess(old_recency, 0.5)

    def test_calculate_importance(self):
        """测试重要性计算"""
        # importance 7.0 -> (7-1)/9 = 0.667
        imp = calculate_importance(self.m1)
        self.assertAlmostEqual(imp, 0.667, places=2)

        # importance 10.0 -> 1.0
        high_imp_memory = Memory(
            id="high",
            content="高重要性",
            memory_type=MemoryType.REFLECTION,
            importance=10.0,
            created_at=datetime.now(timezone.utc),
            last_accessed=datetime.now(timezone.utc)
        )
        self.assertEqual(calculate_importance(high_imp_memory), 1.0)

    def test_keyword_relevance(self):
        """测试关键词相关性"""
        # 高相关性
        rel1 = keyword_relevance("Python 编程学习笔记", "Python 编程")
        self.assertGreater(rel1, 0.3)

        # 低相关性
        rel2 = keyword_relevance("今天天气不错", "Python 编程")
        self.assertLess(rel2, 0.2)

    def test_cosine_similarity(self):
        """测试余弦相似度"""
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [1.0, 0.0, 0.0]
        vec3 = [0.0, 1.0, 0.0]

        # 相同向量
        self.assertAlmostEqual(cosine_similarity(vec1, vec2), 1.0)

        # 正交向量
        self.assertAlmostEqual(cosine_similarity(vec1, vec3), 0.0)

    def test_retrieve(self):
        """测试检索功能"""
        memories = self.stream.get_all()
        results = self.retriever.retrieve(memories, "Python")

        self.assertGreater(len(results), 0)

        # 检查返回格式
        memory, score, breakdown = results[0]
        self.assertIsInstance(memory, Memory)
        self.assertIsInstance(score, float)
        self.assertIn("recency", breakdown)
        self.assertIn("relevance", breakdown)
        self.assertIn("importance", breakdown)

    def test_retrieve_top_k(self):
        """测试 top_k 限制"""
        # 添加更多记忆
        for i in range(20):
            self.stream.add(f"测试记忆 {i}", MemoryType.OBSERVATION, importance=5.0)

        memories = self.stream.get_all()
        results = self.retriever.retrieve(memories, "测试", top_k=5)

        self.assertEqual(len(results), 5)

    def test_retrieve_for_reflection(self):
        """测试反思专用检索"""
        memories = self.stream.get_all()
        results = self.retriever.retrieve_for_reflection(memories, top_k=10)

        self.assertGreater(len(results), 0)

    def test_retrieve_for_planning(self):
        """测试规划专用检索"""
        memories = self.stream.get_all()
        results = self.retriever.retrieve_for_planning(memories, "编程", top_k=10)

        self.assertGreater(len(results), 0)


# ==================== 反思引擎测试 ====================

class TestReflectionEngine(unittest.TestCase):
    """反思引擎模块测试"""

    def setUp(self):
        """测试前准备"""
        self.stream = MemoryStream()
        self.engine = ReflectionEngine(self.stream)

    def test_should_reflect_initial(self):
        """测试初始反思判断"""
        # 记忆不足，不应反思
        should, trigger = self.engine.should_reflect()
        self.assertFalse(should)

    def test_should_reflect_threshold(self):
        """测试阈值触发"""
        # 添加足够记忆
        for i in range(15):
            self.stream.add(f"记忆 {i}", MemoryType.OBSERVATION, importance=5.0)

        should, trigger = self.engine.should_reflect()
        self.assertTrue(should)
        self.assertEqual(trigger, ReflectionTrigger.THRESHOLD)

    def test_reflect_generates_insights(self):
        """测试反思生成洞察"""
        # 添加记忆
        for i in range(10):
            self.stream.add(f"测试记忆 {i}", MemoryType.OBSERVATION, importance=6.0)

        insights = self.engine.reflect()

        self.assertGreater(len(insights), 0)
        self.assertIsInstance(insights[0], Insight)
        self.assertIsNotNone(insights[0].content)

    def test_reflection_stores_in_stream(self):
        """测试反思结果存入记忆流"""
        for i in range(10):
            self.stream.add(f"记忆 {i}", MemoryType.OBSERVATION, importance=5.0)

        initial_count = len(self.stream.get_all())
        self.engine.reflect()

        # 应该有新的反思记忆
        reflections = self.stream.get_by_type(MemoryType.REFLECTION)
        self.assertGreater(len(reflections), 0)

    def test_get_reflection_questions(self):
        """测试反思问题生成"""
        memories = [
            Memory(
                id=f"m{i}",
                content=f"测试记忆 {i}",
                memory_type=MemoryType.OBSERVATION,
                importance=5.0,
                created_at=datetime.now(timezone.utc),
                last_accessed=datetime.now(timezone.utc)
            )
            for i in range(5)
        ]

        questions = self.engine.get_reflection_questions(memories)

        self.assertGreater(len(questions), 0)
        self.assertTrue(any("学" in q for q in questions))

    def test_engine_status(self):
        """测试引擎状态"""
        status = self.engine.get_status()

        self.assertIn("reflection_count", status)
        self.assertIn("should_reflect_now", status)
        self.assertEqual(status["reflection_count"], 0)

    def test_reflect_function_interface(self):
        """测试 reflect 函数接口"""
        memories = [
            Memory(
                id=f"m{i}",
                content=f"测试 {i}",
                memory_type=MemoryType.OBSERVATION,
                importance=5.0,
                created_at=datetime.now(timezone.utc),
                last_accessed=datetime.now(timezone.utc)
            )
            for i in range(10)
        ]

        insights = reflect(memories)

        self.assertIsInstance(insights, list)
        self.assertTrue(all(isinstance(i, str) for i in insights))


# ==================== 规划引擎测试 ====================

class TestPlanningEngine(unittest.TestCase):
    """规划引擎模块测试"""

    def setUp(self):
        """测试前准备"""
        self.stream = MemoryStream()
        self.engine = PlanningEngine(self.stream)

    def test_plan_generates_actions(self):
        """测试计划生成行动"""
        # 添加记忆
        self.stream.add("需要学习 Docker", MemoryType.OBSERVATION, importance=7.0)
        self.stream.add("项目需要容器化", MemoryType.OBSERVATION, importance=8.0)

        plan_obj = self.engine.plan("学习容器技术")

        self.assertIsNotNone(plan_obj.id)
        self.assertGreater(len(plan_obj.actions), 0)
        self.assertIsInstance(plan_obj.actions[0], Action)

    def test_action_properties(self):
        """测试行动属性"""
        self.stream.add("测试记忆", MemoryType.OBSERVATION, importance=5.0)

        plan_obj = self.engine.plan("测试计划")
        action = plan_obj.actions[0]

        self.assertIsNotNone(action.id)
        self.assertIsNotNone(action.title)
        self.assertIn(action.priority, ActionPriority)
        self.assertEqual(action.status, ActionStatus.PENDING)

    def test_get_next_actions(self):
        """测试获取下一步行动"""
        self.stream.add("任务1", MemoryType.OBSERVATION, importance=7.0)
        self.stream.add("任务2", MemoryType.OBSERVATION, importance=8.0)

        self.engine.plan("测试")
        next_actions = self.engine.get_next_actions(limit=3)

        self.assertGreater(len(next_actions), 0)
        self.assertTrue(all(a.status == ActionStatus.PENDING for a in next_actions))

    def test_update_action_status(self):
        """测试更新行动状态"""
        self.stream.add("任务", MemoryType.OBSERVATION, importance=5.0)
        plan_obj = self.engine.plan("测试")

        action_id = plan_obj.actions[0].id
        updated = self.engine.update_action_status(action_id, ActionStatus.COMPLETED)

        self.assertEqual(updated.status, ActionStatus.COMPLETED)

    def test_plan_horizons(self):
        """测试不同时间范围的计划"""
        self.stream.add("记忆", MemoryType.OBSERVATION, importance=5.0)

        # 短期计划
        short_plan = self.engine.plan("短期", horizon=PlanHorizon.SHORT_TERM)
        self.assertEqual(short_plan.horizon, PlanHorizon.SHORT_TERM)

        # 长期计划
        long_plan = self.engine.plan("长期", horizon=PlanHorizon.LONG_TERM)
        self.assertEqual(long_plan.horizon, PlanHorizon.LONG_TERM)

    def test_plan_stores_in_memory(self):
        """测试计划存入记忆流"""
        self.stream.add("记忆", MemoryType.OBSERVATION, importance=5.0)

        self.engine.plan("测试计划")

        plans = self.stream.get_by_type(MemoryType.PLAN)
        self.assertGreater(len(plans), 0)

    def test_engine_status(self):
        """测试引擎状态"""
        self.stream.add("记忆", MemoryType.OBSERVATION, importance=5.0)
        self.engine.plan("测试")

        status = self.engine.get_status()

        self.assertIn("total_plans", status)
        self.assertIn("total_actions", status)
        self.assertGreater(status["total_plans"], 0)

    def test_plan_function_interface(self):
        """测试 plan 函数接口"""
        stream = MemoryStream()
        stream.add("测试记忆", MemoryType.OBSERVATION, importance=5.0)

        actions = plan("测试上下文", memory_stream=stream)

        self.assertIsInstance(actions, list)
        self.assertTrue(all(isinstance(a, Action) for a in actions))

    def test_daily_plan(self):
        """测试每日计划生成"""
        self.stream.add("今日任务", MemoryType.OBSERVATION, importance=7.0)

        daily = self.engine.get_daily_plan()

        self.assertEqual(daily.horizon, PlanHorizon.IMMEDIATE)
        self.assertIn("今日", daily.title)


# ==================== 集成测试 ====================

class TestIntegration(unittest.TestCase):
    """集成测试"""

    def test_full_workflow(self):
        """测试完整工作流：记忆 -> 检索 -> 反思 -> 规划"""
        # 1. 创建记忆流
        stream = MemoryStream()

        # 2. 添加记忆
        for i in range(15):
            stream.add(
                f"学习笔记 {i}: Python 异步编程",
                MemoryType.OBSERVATION,
                importance=5.0 + i * 0.3
            )

        # 3. 检索记忆
        retriever = MemoryRetriever()
        relevant = retriever.retrieve(stream.get_all(), "Python", top_k=5)
        self.assertEqual(len(relevant), 5)

        # 4. 反思
        reflection_engine = ReflectionEngine(stream)
        should, _ = reflection_engine.should_reflect()
        self.assertTrue(should)

        insights = reflection_engine.reflect()
        self.assertGreater(len(insights), 0)

        # 5. 规划
        planning_engine = PlanningEngine(stream, reflection_engine)
        plan_obj = planning_engine.plan("继续学习 Python")

        self.assertGreater(len(plan_obj.actions), 0)

        # 6. 验证整个流程
        stats = stream.get_stats()
        self.assertIn("reflection", stats["by_type"])
        self.assertIn("plan", stats["by_type"])

    def test_reflection_planning_integration(self):
        """测试反思与规划的联动"""
        stream = MemoryStream()

        # 添加记忆
        stream.add("完成了项目第一阶段", MemoryType.ACTION, importance=7.0)
        stream.add("遇到了一些技术难题", MemoryType.OBSERVATION, importance=6.0)

        # 反思
        reflection_engine = ReflectionEngine(stream)
        insights = reflection_engine.reflect()

        # 规划（使用反思结果）
        planning_engine = PlanningEngine(stream, reflection_engine)
        plan_obj = planning_engine.plan("推进项目", use_reflections=True)

        # 验证规划使用了反思结果
        self.assertTrue(
            any(len(a.source_insight_ids) > 0 for a in plan_obj.actions)
        )


# ==================== 运行测试 ====================

def run_tests():
    """运行所有测试"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加测试类
    suite.addTests(loader.loadTestsFromTestCase(TestMemoryStream))
    suite.addTests(loader.loadTestsFromTestCase(TestRetrievalFormula))
    suite.addTests(loader.loadTestsFromTestCase(TestReflectionEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestPlanningEngine))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegration))

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result


if __name__ == "__main__":
    run_tests()
