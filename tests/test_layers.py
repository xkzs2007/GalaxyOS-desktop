#!/usr/bin/env python3
"""
单元测试 - 六层架构测试
"""

import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SKILL_ROOT))


class TestCoreLayer(unittest.TestCase):
    """测试 L1 核心层"""
    
    def setUp(self):
        from core.prompt_integration import CoreLayer
        self.core = CoreLayer({})
        self.core.start()
    
    def tearDown(self):
        self.core.stop()
    
    def test_identity_loaded(self):
        """测试身份加载"""
        identity = self.core.get_identity()
        self.assertIsNotNone(identity)
        self.assertIn('name', identity)
    
    def test_prompts_loaded(self):
        """测试提示词加载"""
        system_prompt = self.core.get_prompt('system')
        self.assertIsNotNone(system_prompt)
        self.assertIn('小艺', system_prompt)


class TestMemoryLayer(unittest.TestCase):
    """测试 L2 记忆层"""
    
    def setUp(self):
        from memory_context.memory_manager import MemoryLayer
        self.memory = MemoryLayer({})
        self.memory.start()
    
    def tearDown(self):
        self.memory.stop()
    
    def test_store_memory(self):
        """测试记忆存储"""
        memory_id = self.memory.store("测试内容", level="L1")
        self.assertIsNotNone(memory_id)
        self.assertTrue(memory_id.startswith("mem_"))
    
    def test_retrieve_memory(self):
        """测试记忆检索"""
        self.memory.store("测试关键词内容", level="L1")
        results = self.memory.retrieve("关键词", level="L1")
        self.assertIsInstance(results, list)
    
    def test_context_management(self):
        """测试上下文管理"""
        self.memory.set_context("test_key", "test_value")
        ctx = self.memory.get_context()
        self.assertIn("test_key", ctx)
        self.assertEqual(ctx["test_key"], "test_value")


class TestOrchestrationLayer(unittest.TestCase):
    """测试 L3 编排层"""
    
    def setUp(self):
        from orchestration.task_engine import OrchestrationLayer
        self.orchestration = OrchestrationLayer({})
        self.orchestration.start()
    
    def tearDown(self):
        self.orchestration.stop()
    
    def test_create_task(self):
        """测试任务创建"""
        task = self.orchestration.create_task("测试任务")
        self.assertIsNotNone(task)
        self.assertEqual(task.name, "测试任务")
    
    def test_execute_task(self):
        """测试任务执行"""
        task = self.orchestration.create_task("测试任务", task_type="query")
        success = self.orchestration.execute_task(task.task_id)
        self.assertTrue(success)
    
    def test_workflow(self):
        """测试工作流"""
        task1 = self.orchestration.create_task("任务1")
        task2 = self.orchestration.create_task("任务2", dependencies=[task1.task_id])
        
        workflow_id = self.orchestration.create_workflow(
            "测试工作流",
            [task1.task_id, task2.task_id]
        )
        
        success = self.orchestration.execute_workflow(workflow_id)
        self.assertTrue(success)


class TestExecutionLayer(unittest.TestCase):
    """测试 L4 执行层"""
    
    def setUp(self):
        from execution.skill_adapter_gateway import ExecutionLayer
        self.execution = ExecutionLayer({})
        self.execution.start()
    
    def tearDown(self):
        self.execution.stop()
    
    def test_list_skills(self):
        """测试技能列表"""
        skills = self.execution.list_skills()
        self.assertIsInstance(skills, list)
        self.assertGreater(len(skills), 0)
    
    def test_execute_skill(self):
        """测试技能执行"""
        skills = self.execution.list_skills()
        if skills:
            result = self.execution.execute(skills[0], {"test": True})
            self.assertIn("status", result)


class TestGovernanceLayer(unittest.TestCase):
    """测试 L5 治理层"""
    
    def setUp(self):
        from governance.security.auth_integration import GovernanceLayer
        self.governance = GovernanceLayer({})
        self.governance.start()
    
    def tearDown(self):
        self.governance.stop()
    
    def test_permission_check(self):
        """测试权限检查"""
        # 默认用户应该有读权限
        has_permission = self.governance.check_permission("default", "read", "test")
        self.assertTrue(has_permission)
    
    def test_audit_log(self):
        """测试审计日志"""
        self.governance.audit("test_action", user="test_user")
        logs = self.governance.get_audit_logs(limit=1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["action"], "test_action")
    
    def test_validate_operation(self):
        """测试操作验证"""
        # 允许的操作应该通过
        valid = self.governance.validate_operation("read", {})
        self.assertTrue(valid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
