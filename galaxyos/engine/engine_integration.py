"""
增强引擎集成器 —— 将四个新引擎懒加载接入 XiaoYiClawLLM

接入点:
  1. __init__: 懒加载初始化（首次调用时触发）
  2. _control_phase: ReAct 多步推理（对付复杂 / 多步问题）
  3. _action_phase: Chain-of-Verification 自验证（回答生成后）
  4. _memory_phase: 层次化记忆调度（记忆存储前）

用法:
    在 XiaoYiClawLLM.__init__ 中调用:
        from engine_integration import EngineIntegrator
        self._engine_int = EngineIntegrator(self)

    在 process() 开头:
        self._engine_int.lazy_init()

    在 _control_phase 中:
        result = self._engine_int.run_react(state)

    在 _action_phase 中:
        result = self._engine_int.run_cove(state, answer)

    在 _memory_phase 中:
        self._engine_int.run_memory_schedule(state)

Author: GalaxyOS
"""

import logging
import time
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class EngineIntegrator:
    """
    增强引擎集成器

    将四个新引擎（ReAct / CoVe / HierarchicalMemory / CodeAware）
    以懒加载方式接入 XiaoYiClawLLM，不改动核心管线结构。
    """

    def __init__(self, claw_instance):
        """
        Args:
            claw_instance: XiaoYiClawLLM 实例（提供 llm_flash / llm_pro 等）
        """
        self._claw = claw_instance
        self._initialized = False

        # 引擎实例（懒加载）
        self._react = None
        self._cove = None
        self._hierarchical = None
        self._code = None

    # ═══ 懒加载初始化 ═══

    def lazy_init(self):
        """首次调用时初始化所有引擎"""
        if self._initialized:
            return
        self._initialized = True

        try:
            # 获取 LLM 客户端
            llm_flash = getattr(self._claw, 'llm_flash', None)
            llm_pro = getattr(self._claw, 'llm_pro', None)
            flash_model = getattr(self._claw, '_llm_flash_model', "deepseek-v4-flash")
            pro_model = getattr(self._claw, '_llm_pro_model', "deepseek-v4-pro")
            guard = getattr(self._claw, 'hallucination_guard', None)

            # 1. ReAct 多步推理
            try:
                from react_engine import ReActEngine
                self._react = ReActEngine(
                    llm_flash=llm_flash,
                    llm_pro=llm_pro,
                    flash_model=flash_model,
                    pro_model=pro_model
                )
                logger.debug("[引擎] ReAct 就绪")
            except Exception as e:
                logger.warning(f"[引擎] ReAct 初始化失败: {e}")

            # 2. Chain-of-Verification 自验证
            try:
                from chain_of_verification import ChainOfVerificationEngine
                self._cove = ChainOfVerificationEngine(
                    llm_flash=llm_flash,
                    llm_pro=llm_pro,
                    flash_model=flash_model,
                    pro_model=pro_model,
                    hallucination_guard=guard
                )
                logger.debug("[引擎] CoVe 就绪")
            except Exception as e:
                logger.warning(f"[引擎] CoVe 初始化失败: {e}")

            # 3. 层次化记忆管理
            try:
                from hierarchical_memory import HierarchicalMemoryManager
                self._hierarchical = HierarchicalMemoryManager(
                    llm_flash=llm_flash
                )
                logger.debug("[引擎] HierarchicalMemory 就绪")
            except Exception as e:
                logger.warning(f"[引擎] HierarchicalMemory 初始化失败: {e}")

            # 4. 代码感知推理
            try:
                from code_aware_reasoning import CodeAwareReasoningEngine
                self._code = CodeAwareReasoningEngine(
                    llm_flash=llm_flash,
                    flash_model=flash_model
                )
                logger.debug("[引擎] CodeAware 就绪")
            except Exception as e:
                logger.warning(f"[引擎] CodeAware 初始化失败: {e}")

        except Exception as e:
            logger.error(f"[引擎] 集成初始化失败: {e}")

    # ═══ ReAct 入口 ═══

    def run_react(self, state) -> Optional[Dict]:
        """
        在 _control_phase 中调用: 当 info_insufficient 或复杂问题时触发

        Returns:
            result dict or None（无 ReAct 引擎时返回 None）
        """
        if not self._react:
            return None

        query = getattr(state, 'user_input', '')
        if not query:
            return None

        try:
            plan = self._react.execute(query, max_steps=6)

            if plan and plan.success and plan.final_answer:
                setattr(state, 'generated_answer', plan.final_answer)
                setattr(state, 'answer_confidence', 0.85)
                setattr(state, 'strategy', 'answer')
                setattr(state, 'action_success', True)
                setattr(state, 'stop_reason', 'react_multi_step')
                return state.analysis if hasattr(state, 'analysis') else {}
        except Exception as e:
            logger.warning(f"[引擎] ReAct 执行失败: {e}")

        return None

    # ═══ CoVe 入口 ═══

    def run_cove(self, state, answer: str) -> Optional[str]:
        """
        在 _action_phase 中调用: 回答生成后执行自验证

        Returns:
            修正后的回答，或原始回答（无引擎/验证通过）
        """
        if not self._cove:
            return answer

        query = getattr(state, 'user_input', '')
        if not query or not answer:
            return answer

        try:
            result = self._cove.verify_and_refine(
                answer=answer,
                query=query,
                max_rounds=1  # 线上只做一轮，避免延迟过长
            )

            if result and result.refined_answer:
                # 如果有修正且内容不同，记录到 state
                if result.refined_answer != answer and result.contradictions_found > 0:
                    try:
                        analysis = getattr(state, 'analysis', {})
                        if analysis:
                            analysis['cove_applied'] = True
                            analysis['cove_contradictions'] = result.contradictions_found
                    except Exception:
                        pass
                    return result.refined_answer

            return answer

        except Exception as e:
            logger.warning(f"[引擎] CoVe 执行失败: {e}")
            return answer

    # ═══ 层次化记忆 入口 ═══

    def run_memory_schedule(self, state) -> Optional[Dict]:
        """
        在 _memory_phase 中调用: 执行记忆调度

        Returns:
            调度报告或 None
        """
        if not self._hierarchical:
            return None

        # 间隔执行: 每 5 次对话一次调度
        import random
        if random.random() > 0.2:  # 20% 概率执行
            return None

        try:
            report = self._hierarchical.schedule()
            if report.get('forgotten', 0) > 0 or report.get('merged', 0) > 0:
                logger.info(f"[记忆调度] forgot={report['forgotten']} "
                           f"merged={report['merged']} promoted={report['promoted']}")
            return report
        except Exception as e:
            logger.warning(f"[引擎] 记忆调度失败: {e}")
            return None

    # ═══ 代码感知 入口 ═══

    def detect_code_task(self, query: str) -> Optional[Dict]:
        """
        检测是否涉及代码分析任务

        Returns:
            code 分析结果或 None
        """
        code_keywords = [
            '代码', '函数', '类', 'bug', 'debug', '报错', '异常',
            '代码分析', '代码审查', 'code review', 'code analysis',
            '调用', '接口', '重构', '优化', '性能',
        ]

        if not any(kw in query.lower() for kw in code_keywords):
            return None

        if not self._code:
            return None

        try:
            # 尝试从 query 中检测是否提到了具体文件
            file_match = __import__('re').search(
                r'([/\w]+\.py)', query
            )
            if file_match:
                filepath = file_match.group(1)
                return self._code.analyze_file(filepath).__dict__
            else:
                return self._code.analyze_code_snippet(query)
        except Exception as e:
            logger.warning(f"[引擎] 代码感知失败: {e}")
            return None

    # ═══ 状态查询 ═══

    def status(self) -> Dict[str, bool]:
        """查询各引擎状态"""
        return {
            "react": self._react is not None,
            "cove": self._cove is not None,
            "hierarchical_memory": self._hierarchical is not None,
            "code_aware": self._code is not None,
            "initialized": self._initialized
        }
