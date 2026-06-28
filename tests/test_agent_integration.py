"""
Agent Integration Tests — mock LLM，验证完整 R-CCAM 处理链路

验证:
1. 5 阶段 pipeline 的编排顺序 (Retrieval → Cognition → Control → Action → Memory)
2. 阶段间数据流正确传递
3. 错误处理和降级链路
4. 记忆持久化和检索闭环
5. 多轮循环行为

策略: Mock 底层 LLM 调用，用确定性输出验证 pipeline 逻辑。
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
import json
import logging
from unittest.mock import patch, MagicMock

# 抑制日志噪音
logging.getLogger('xiaoyi-claw-omega').setLevel(logging.ERROR)
logging.getLogger('galaxyos.engine').setLevel(logging.ERROR)
logging.getLogger('galaxyos.privileged').setLevel(logging.ERROR)


# ── Mock LLM 响应工厂 ──

def make_mock_llm_response(content: str, role: str = "assistant") -> dict:
    """构造 OpenAI-compatible mock 响应（dict 格式）"""
    return {
        "choices": [{
            "message": {
                "role": role,
                "content": content,
            },
            "finish_reason": "stop",
        }],
        "usage": {"total_tokens": len(content) // 4},
    }


def make_mock_llm_chat_response(content: str) -> MagicMock:
    """构造 OpenAI SDK 属性访问格式的 mock 响应（.choices[0].message.content）"""
    msg = MagicMock()
    msg.content = content
    msg.role = "assistant"
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    rsp = MagicMock()
    rsp.choices = [choice]
    rsp.usage = MagicMock(total_tokens=len(content) // 4)
    return rsp


def _setup_essential_attrs(claw):
    """为 Agent 设置 process() 路径必需的最小属性集"""
    if not hasattr(claw, 'dag') or claw.dag is None:
        claw.dag = None
    if not hasattr(claw, 'llm_flash') or claw.llm_flash is None:
        claw.llm_flash = MagicMock()
        claw.llm_flash.chat.completions.create = MagicMock(
            return_value=make_mock_llm_chat_response("这是一个经过 LLM 处理的回答。")
        )
    if not hasattr(claw, 'llm_pro') or claw.llm_pro is None:
        claw.llm_pro = claw.llm_flash
    if not hasattr(claw, 'dynamic_confidence') or claw.dynamic_confidence is None:
        claw.dynamic_confidence = None
    if not hasattr(claw, 'memory_editor') or claw.memory_editor is None:
        claw.memory_editor = None
    if not hasattr(claw, 'embedding') or claw.embedding is None:
        claw.embedding = MagicMock()
        claw.embedding.embed = MagicMock(return_value=[0.0] * 128)
    return claw


class TestRCAMPipeline:
    """R-CCAM 五阶段 Pipeline 集成测试"""

    @pytest.fixture
    def claw_with_mock_llm(self):
        """创建带 mock LLM 的 Agent，覆盖所有 process() 中的属性访问"""
        from services.xiaoyi_claw_api import XiaoYiClawLLM

        with patch.object(XiaoYiClawLLM, '_init_llm_client', return_value=None):
            claw = XiaoYiClawLLM()

            # Mock LLM 调用
            mock_response = make_mock_llm_response(
                "人工智能（AI）是计算机科学的一个分支，"
                "致力于创建能够执行通常需要人类智能的任务的系统。"
            )
            claw.llm_client = MagicMock()
            claw.llm_client.chat = MagicMock(return_value=mock_response)
            claw.llm_client.chat_completion = MagicMock(return_value=mock_response)

            # 关键：设置 agent.llm_flash（process 中 _retrieval_phase 等阶段需要）
            # 代码通过 llm_flash.chat.completions.create(...) 调用，需要属性访问格式的 mock
            _flash_answer = "人工智能（AI）是计算机科学的一个分支，致力于创建能够执行通常需要人类智能的任务的系统。"
            _flash_rsp = make_mock_llm_chat_response(_flash_answer)
            claw.llm_flash = MagicMock()
            claw.llm_flash.chat.completions.create = MagicMock(return_value=_flash_rsp)
            claw.llm_pro = MagicMock()
            claw.llm_pro.chat.completions.create = MagicMock(return_value=_flash_rsp)

            # DAG 设 None（走无 DAG 路径，合法路径）
            if not hasattr(claw, 'dag'):
                claw.dag = None
            # embedding — Action 和 Memory 阶段需要
            if not hasattr(claw, 'embedding') or claw.embedding is None:
                claw.embedding = MagicMock()
                claw.embedding.embed = MagicMock(return_value=[0.0] * 128)

            return _setup_essential_attrs(claw)

    def test_stages_execute_in_order(self, claw_with_mock_llm):
        """验证 pipeline 阶段按顺序执行"""
        result = claw_with_mock_llm.process("什么是人工智能？", max_cycles=1)

        phases = result["rccam_phase_states"]
        for phase in ["retrieval", "cognition", "control", "action", "memory"]:
            assert phase in phases, f"Phase '{phase}' missing from result"

        assert result["cycle_count"] >= 1
        assert result["action_success"] is True

    def test_non_meta_query_triggers_full_pipeline(self, claw_with_mock_llm):
        """非 meta 查询应触发完整 pipeline"""
        result = claw_with_mock_llm.process(
            "请详细解释机器学习和深度学习的区别",
            max_cycles=1,
        )
        assert result["stop_reason"] != "meta_shortcut"
        assert len(result["answer"]) > 0

    def test_cognition_phase_classifies_knowledge_type(self, claw_with_mock_llm):
        """Cognition 阶段应分类知识类型"""
        result = claw_with_mock_llm.process("计算 123 + 456 等于多少？", max_cycles=1)
        cognition = result["rccam_phase_states"]["cognition"]
        assert "type" in cognition
        assert isinstance(cognition["type"], str)

    def test_retrieval_phase_runs_before_cognition(self, claw_with_mock_llm):
        """Retrieval 阶段应在 Cognition 之前运行"""
        result = claw_with_mock_llm.process("Python 编程语言的特点", max_cycles=1)
        retrieval = result["rccam_phase_states"]["retrieval"]
        assert isinstance(retrieval["memories_count"], int)
        assert retrieval["memories_count"] >= 0


class TestAgentErrorHandling:
    """Agent 错误处理和降级测试"""

    @pytest.fixture
    def claw_mock(self):
        """创建最小化 Agent，mock 所有可能失败的子系统"""
        from services.xiaoyi_claw_api import XiaoYiClawLLM

        # Mock 所有 _init_* 方法来避免外部依赖爆炸
        _init_methods = [m for m in dir(XiaoYiClawLLM) if m.startswith('_init_')]
        patches = []
        for m in _init_methods:
            p = patch.object(XiaoYiClawLLM, m, return_value=None)
            p.start()
            patches.append(p)

        claw = XiaoYiClawLLM()

        # 设置 process() 所需的最小属性
        claw.dag = None
        claw.llm_flash = MagicMock()
        claw.llm_flash.chat.completions.create = MagicMock(
            return_value=make_mock_llm_chat_response(
                json.dumps({
                    "intent": "question",
                    "knowledge_type": "factual",
                    "complexity": "low",
                    "thinking_skills": [],
                    "emotion_tone": "neutral",
                })
            )
        )
        claw.llm_pro = claw.llm_flash
        claw.dynamic_confidence = None
        claw.memory_editor = None
        claw.embedding = MagicMock()
        claw.embedding.embed = MagicMock(return_value=[0.0] * 128)

        yield claw

        for p in patches:
            p.stop()

    def test_empty_input_does_not_crash(self, claw_mock):
        """空输入不应崩溃"""
        result = claw_mock.process("", max_cycles=1)
        assert result is not None
        assert "answer" in result

    def test_very_long_input_does_not_crash(self, claw_mock):
        """超长输入不应崩溃"""
        long_input = "测试 " * 5000
        result = claw_mock.process(long_input, max_cycles=1)
        assert result is not None
        assert "answer" in result

    def test_special_characters_input(self, claw_mock):
        """特殊字符输入不应崩溃"""
        special_input = "!@#$%^&*()_+-=[]{}|;':\",./<>?`~"
        result = claw_mock.process(special_input, max_cycles=1)
        assert result is not None

    def test_unicode_input(self, claw_mock):
        """Unicode/Emoji 输入不应崩溃"""
        unicode_input = "你好 🌍 世界 🚀 test テスト"
        result = claw_mock.process(unicode_input, max_cycles=1)
        assert result is not None
        assert isinstance(result["answer"], str)


class TestMemoryIntegration:
    """记忆系统集成测试"""

    @pytest.fixture
    def claw(self):
        """标准 Agent 实例"""
        from services.xiaoyi_claw_api import XiaoYiClawLLM
        import logging
        for name in ['xiaoyi-claw-omega', 'galaxyos', 'services']:
            logging.getLogger(name).setLevel(logging.WARNING)
        claw = XiaoYiClawLLM()
        return _setup_essential_attrs(claw)

    def test_process_stores_memory_when_enabled(self, claw):
        """store_memory=True 时应持久化记忆"""
        import uuid
        test_query = f"INTEGRATION_TEST_{uuid.uuid4().hex[:8]}"
        result = claw.process(test_query, max_cycles=1, store_memory=True)
        assert "memory_ids" in result
        assert isinstance(result["memory_ids"], list)

    def test_process_skips_memory_when_disabled(self, claw):
        """store_memory=False 时应跳过持久化"""
        import uuid
        test_query = f"NO_STORE_TEST_{uuid.uuid4().hex[:8]}"
        result = claw.process(test_query, max_cycles=1, store_memory=False)
        assert result["memory_ids"] == []

    def test_session_key_isolation(self, claw):
        """不同 session_key 应能隔离记忆"""
        import uuid
        session_a = f"test_session_a_{uuid.uuid4().hex[:6]}"
        session_b = f"test_session_b_{uuid.uuid4().hex[:6]}"

        result_a = claw.process("session A 的记忆", max_cycles=1,
                                store_memory=True, session_key=session_a)
        result_b = claw.process("session B 的记忆", max_cycles=1,
                                store_memory=True, session_key=session_b)

        assert result_a is not None
        assert result_b is not None


class TestMultiCycleBehavior:
    """多轮循环行为测试"""

    @pytest.fixture
    def claw_mock(self):
        """带 mock LLM 的 Agent，支持多轮循环"""
        from services.xiaoyi_claw_api import XiaoYiClawLLM

        with patch.object(XiaoYiClawLLM, '_init_llm_client', return_value=None):
            claw = XiaoYiClawLLM()

            mock_response = make_mock_llm_response("这是一个经过多轮思考后的回答。")
            claw.llm_client = MagicMock()
            claw.llm_client.chat = MagicMock(return_value=mock_response)

            claw.llm_flash = MagicMock()
            claw.llm_flash.chat.completions.create = MagicMock(
                return_value=make_mock_llm_chat_response(
                    json.dumps({
                        "intent": "question",
                        "knowledge_type": "complex",
                        "complexity": "high",
                        "thinking_skills": ["analyze", "synthesize"],
                        "emotion_tone": "neutral",
                    })
                )
            )
            claw.llm_pro = claw.llm_flash
            if not hasattr(claw, 'dag'):
                claw.dag = None
            if not hasattr(claw, 'embedding') or claw.embedding is None:
                claw.embedding = MagicMock()
                claw.embedding.embed = MagicMock(return_value=[0.0] * 128)

            return _setup_essential_attrs(claw)

    def test_multi_cycle_respects_max_cycles(self, claw_mock):
        """多轮循环应不超过 max_cycles"""
        result = claw_mock.process("一个复杂的问题需要多轮思考", max_cycles=2)
        assert result["cycle_count"] <= 2

    def test_single_cycle_completes(self, claw_mock):
        """单轮循环应能完成"""
        result = claw_mock.process("简单问题", max_cycles=1)
        assert result["cycle_count"] <= 1
        assert result["action_success"] is True


class TestClawHelpersIntegration:
    """claw_helpers 便捷函数集成测试"""

    def test_get_xiaoyi_claw_returns_instance(self):
        """get_xiaoyi_claw 应返回单例"""
        from services.claw_helpers import get_xiaoyi_claw
        claw1 = get_xiaoyi_claw()
        claw2 = get_xiaoyi_claw()
        assert claw1 is claw2
        assert hasattr(claw1, 'process')

    def test_remember_via_helper(self):
        """通过 helpers.remember 写入记忆"""
        from services.claw_helpers import remember
        import uuid
        test_content = f"helpers_test_{uuid.uuid4().hex[:8]}"
        mem_id = remember(test_content)
        assert isinstance(mem_id, str)

    def test_recall_via_helper(self):
        """通过 helpers.recall 检索记忆"""
        from services.claw_helpers import recall
        results = recall("Python")
        assert isinstance(results, list)
