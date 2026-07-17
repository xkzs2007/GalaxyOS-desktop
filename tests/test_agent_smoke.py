"""
Agent Smoke Tests — 快速验证 Agent 启动和基本消息处理

验证:
1. Agent 可实例化（核心子系统降级容错）
2. Meta 命令（ping/health/status）秒级响应
3. 5 阶段 R-CCAM 返回结构完整性
4. 内存操作（remember/recall/forget）端到端可通
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import json


@pytest.fixture(scope="module")
def claw():
    """创建一次 Agent 实例，所有 smoke test 复用"""
    from services.xiaoyi_claw_api import XiaoYiClawLLM
    import logging
    logging.getLogger('galaxyos').setLevel(logging.WARNING)
    logging.getLogger('galaxyos.engine').setLevel(logging.WARNING)
    return XiaoYiClawLLM()


class TestAgentInstantiation:
    """Agent 实例化 smoke tests"""

    def test_agent_creates_without_crash(self, claw):
        """Agent 应能成功创建不崩溃（降级容错）"""
        assert claw is not None

    def test_agent_has_process_method(self, claw):
        """Agent 必须有 process 方法"""
        assert callable(claw.process)

    def test_agent_has_health_check(self, claw):
        """Agent 必须有 health_check 方法"""
        assert callable(claw.health_check)

    def test_agent_has_memory_operations(self, claw):
        """Agent 必须有基本记忆操作"""
        assert callable(claw.remember)
        assert callable(claw.recall)
        assert callable(claw.forget)


class TestMetaCommands:
    """Meta 命令快速响应测试"""

    def test_ping_returns_pong(self, claw):
        result = claw.process("ping")
        assert result["answer"] == "pong"
        assert result["confidence"] > 0.9
        assert result["stop_reason"] == "meta_shortcut"
        assert result["cycle_count"] == 0

    def test_health_returns_ok(self, claw):
        result = claw.process("health")
        assert result["answer"] == "ok"
        assert result["strategy"] == "answer"

    def test_status_returns_ok(self, claw):
        result = claw.process("status")
        assert result["answer"] == "ok"

    def test_hello_shortcut(self, claw):
        result = claw.process("hello")
        assert result["stop_reason"] == "meta_shortcut"
        assert result["cycle_count"] == 0

    def test_all_meta_cmds_return_correct_structure(self, claw):
        """所有 meta 命令返回结构完整性"""
        meta_cmds = ["ping", "test", "health", "status", "ok"]
        for cmd in meta_cmds:
            result = claw.process(cmd)
            assert "answer" in result, f"Meta cmd '{cmd}' missing 'answer'"
            assert "rccam_phase_states" in result, f"Meta cmd '{cmd}' missing 'rccam_phase_states'"
            phases = result["rccam_phase_states"]
            for phase in ["retrieval", "cognition", "control", "action", "memory"]:
                assert phase in phases, f"Meta cmd '{cmd}' missing phase '{phase}'"


class TestProcessReturnStructure:
    """process() 返回值结构完整性测试"""

    REQUIRED_KEYS = [
        "answer", "confidence", "critic_context", "routing_debug",
        "strategy", "knowledge_type", "intent",
        "cycle_count", "thinking_skills_used",
        "retrieval_confidence", "memory_ids",
        "stop_reason", "action_success", "rccam_phase_states",
    ]

    def test_process_returns_all_required_keys_non_meta(self, claw):
        """非 meta 查询应返回所有必需字段"""
        result = claw.process("什么是人工智能？", max_cycles=1)
        for key in self.REQUIRED_KEYS:
            assert key in result, f"Missing required key: {key}"

    def test_answer_is_non_empty_string(self, claw):
        result = claw.process("你好，请简单介绍一下自己")
        assert isinstance(result["answer"], str)
        assert len(result["answer"]) > 0

    def test_rccam_phase_states_have_all_phases(self, claw):
        result = claw.process("什么是机器学习？", max_cycles=1)
        phases = result["rccam_phase_states"]
        assert isinstance(phases, dict)
        for phase in ["retrieval", "cognition", "control", "action", "memory"]:
            assert phase in phases

    def test_cycle_count_is_valid(self, claw):
        result = claw.process("测试问题", max_cycles=1)
        assert result["cycle_count"] >= 0
        assert result["cycle_count"] <= 3

    def test_stop_reason_is_set(self, claw):
        result = claw.process("正常的提问")
        assert isinstance(result["stop_reason"], str)
        assert len(result["stop_reason"]) > 0


class TestMemoryOperationsEndToEnd:
    """记忆操作端到端验证"""

    def test_remember_returns_id(self, claw):
        mem_id = claw.remember("这是一条测试记忆")
        assert isinstance(mem_id, str)
        assert len(mem_id) > 0

    def test_remember_with_metadata(self, claw):
        mem_id = claw.remember(
            "Python 是一门高级编程语言",
            metadata={"category": "programming", "source": "smoke_test"},
        )
        assert isinstance(mem_id, str)

    def test_recall_returns_list(self, claw):
        results = claw.recall("Python")
        assert isinstance(results, list)

    def test_forget_returns_int(self, claw):
        result = claw.forget("nonexistent_id_12345")
        assert isinstance(result, int)

    def test_remember_recall_roundtrip(self, claw):
        """记忆写入后应能被检索到或至少不崩溃"""
        import uuid
        test_content = f"SMOKE_TEST_UNIQUE_{uuid.uuid4().hex[:8]}"
        mem_id = claw.remember(test_content)
        results = claw.recall(test_content)
        # 不强制要求检索到（取决于后端可用性），但不应崩溃
        assert isinstance(results, list)
        assert isinstance(mem_id, str)


class TestHealthCheck:
    """health_check 端到端测试"""

    def test_health_check_returns_dict(self, claw):
        result = claw.health_check()
        assert isinstance(result, dict)
        # health_check 返回组件状态字典，检查至少有 vector_store key
        has_components = any(k in result for k in [
            "vector_store", "status", "healthy", "components",
            "ontology_bridge", "memory_v2",
        ])
        assert has_components, f"Unexpected health_check keys: {list(result.keys())[:5]}"


class TestProcessWithMaxCycles:
    """不同 max_cycles 参数行为"""

    def test_max_cycles_0_still_works(self, claw):
        """max_cycles=0 应走 meta 或返回空"""
        # 非 meta 在 max_cycles 为 0 时行为取决于实现，不崩溃即可
        try:
            result = claw.process("测试", max_cycles=0)
            assert result is not None
        except Exception as e:
            # max_cycles=0 时抛异常也可以接受
            assert "cycle" in str(e).lower() or "max" in str(e).lower()

    def test_max_cycles_3_respected(self, claw):
        """max_cycles=3 应不超过 3 轮"""
        result = claw.process("复杂的推理问题", max_cycles=3)
        assert result["cycle_count"] <= 3
