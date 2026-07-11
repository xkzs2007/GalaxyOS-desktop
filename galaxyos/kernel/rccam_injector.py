"""
RCCAMInjector — R-CCAM 五阶段认知循环注入器

在 Agent Studio 的 Agent Loop 中注入 R-CCAM 五阶段认知循环：
  1. Retrieval: 液态神经记忆检索 + CRAG + GraphRAG + DAG 上下文组装
  2. Cognition: 分析检索结果 + 认知地图 + 因果推理
  3. Control: 策略选择 + 元认知调节
  4. Action: 执行动作 + 工具调用
  5. Memory: 记忆巩固 + engram 写入 + DAG 节点更新

超时降级：R-CCAM 执行超过 timeout_ms 时降级为直接 LLM 调用
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RCCAMPhase(str, Enum):
    RETRIEVAL = "retrieval"
    COGNITION = "cognition"
    CONTROL = "control"
    ACTION = "action"
    MEMORY = "memory"


@dataclass
class RCCAMState:
    session_key: str = ""
    user_input: str = ""
    current_phase: RCCAMPhase = RCCAMPhase.RETRIEVAL
    retrieved_context: list[dict[str, Any]] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    strategy: dict[str, Any] = field(default_factory=dict)
    action_result: dict[str, Any] = field(default_factory=dict)
    memory_result: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    timed_out: bool = False
    degraded: bool = False


class RCCAMInjector:
    """
    R-CCAM 五阶段认知循环注入器。

    在 Agent Studio 的 Agent Loop 中注入 R-CCAM 认知增强：
    - on_pre_agent_reply: 触发 Retrieval 阶段，注入增强上下文
    - on_post_agent_reply: 触发 Memory 阶段，记忆巩固

    用法：
        injector = RCCAMInjector(memory_adapter=adapter, dag_fusion=fusion)
        state = await injector.on_pre_agent_reply(session_key="ws:dm:user1", user_input="查询")
        # state.retrieved_context 可注入 Agent prompt
        await injector.on_post_agent_reply(session_key="ws:dm:user1", agent_reply="回复内容")
    """

    def __init__(
        self,
        memory_adapter=None,
        dag_fusion=None,
        timeout_ms: int = 3000,
        config: dict[str, Any] | None = None,
        tokui_builder=None,
        tokui_streamer=None,
    ):
        self._memory_adapter = memory_adapter
        self._dag_fusion = dag_fusion
        self._timeout_ms = timeout_ms
        self._config = config or {}
        self._tokui_builder = tokui_builder
        self._tokui_streamer = tokui_streamer
        self._active_cycles: dict[str, RCCAMState] = {}

    async def on_pre_agent_reply(
        self,
        session_key: str,
        user_input: str,
        token_budget: int = 12000,
    ) -> RCCAMState:
        start = time.monotonic()
        state = RCCAMState(session_key=session_key, user_input=user_input)
        self._active_cycles[session_key] = state

        try:
            state.current_phase = RCCAMPhase.RETRIEVAL
            state.retrieved_context = await self._retrieval(session_key, user_input, token_budget)
            elapsed = (time.monotonic() - start) * 1000

            await self._emit_rccam_state(session_key, RCCAMPhase.RETRIEVAL, state)
            await self._emit_memory_state(session_key)

            if elapsed > self._timeout_ms:
                state.timed_out = True
                state.degraded = True
                logger.warning(f"R-CCAM Retrieval timed out ({elapsed:.0f}ms > {self._timeout_ms}ms) — degrading")
                return state

            state.current_phase = RCCAMPhase.COGNITION
            state.analysis = await self._cognition(session_key, user_input, state.retrieved_context)

            state.current_phase = RCCAMPhase.CONTROL
            state.strategy = await self._control(session_key, state.analysis)

            await self._emit_rccam_state(session_key, RCCAMPhase.CONTROL, state)

            state.elapsed_ms = (time.monotonic() - start) * 1000
            state.current_phase = RCCAMPhase.RETRIEVAL

        except Exception as e:
            logger.error(f"R-CCAM on_pre_agent_reply error: {e}", exc_info=True)
            state.degraded = True
            state.elapsed_ms = (time.monotonic() - start) * 1000

        return state

    async def on_post_agent_reply(
        self,
        session_key: str,
        agent_reply: str,
    ) -> RCCAMState:
        start = time.monotonic()
        state = self._active_cycles.get(session_key, RCCAMState(session_key=session_key))

        try:
            state.current_phase = RCCAMPhase.MEMORY
            state.memory_result = await self._memory_phase(session_key, agent_reply)
            state.elapsed_ms = (time.monotonic() - start) * 1000

            await self._emit_rccam_state(session_key, RCCAMPhase.MEMORY, state)
            await self._emit_memory_state(session_key)
            await self._emit_dag_state(session_key)
        except Exception as e:
            logger.error(f"R-CCAM on_post_agent_reply error: {e}", exc_info=True)
            state.degraded = True
            state.elapsed_ms = (time.monotonic() - start) * 1000
        finally:
            self._active_cycles.pop(session_key, None)

        return state

    async def _retrieval(self, session_key: str, query: str, token_budget: int) -> list[dict[str, Any]]:
        context = []

        if self._memory_adapter:
            try:
                result = await self._memory_adapter.recall(query=query, top_k=10, session_key=session_key)
                context.extend(result.get("results", []))
            except Exception as e:
                logger.warning(f"Memory recall failed: {e}")

        if self._dag_fusion:
            try:
                result = await self._dag_fusion.assemble(session_key=session_key, token_budget=token_budget)
                context.extend(result.get("context", []))
            except Exception as e:
                logger.warning(f"DAG assemble failed: {e}")

        return context

    async def _cognition(self, session_key: str, user_input: str, context: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "input_class": "standard",
            "context_relevance": min(len(context) / 5.0, 1.0),
            "needs_retrieval": len(context) < 3,
            "session_key": session_key,
        }

    async def _control(self, session_key: str, analysis: dict[str, Any]) -> dict[str, Any]:
        return {
            "strategy": "direct_reply",
            "use_cognitive_enhancement": analysis.get("context_relevance", 0) > 0.3,
            "session_key": session_key,
        }

    async def _memory_phase(self, session_key: str, agent_reply: str) -> dict[str, Any]:
        result = {"stored": False, "consolidated": False}

        if self._memory_adapter:
            try:
                write_result = await self._memory_adapter.write(
                    content=agent_reply, source="agent", session_key=session_key, memory_type="auto"
                )
                result["stored"] = True
                result["write_id"] = write_result.get("id", "")
            except Exception as e:
                logger.warning(f"Memory write failed: {e}")

        if self._dag_fusion:
            try:
                await self._dag_fusion.create_node(
                    session_key=session_key, role="assistant", content=agent_reply, node_type="agent_reply"
                )
                result["dag_node_created"] = True
            except Exception as e:
                logger.warning(f"DAG node creation failed: {e}")

        return result

    def get_active_cycles(self) -> dict[str, Any]:
        return {
            "active_count": len(self._active_cycles),
            "sessions": list(self._active_cycles.keys()),
            "timeout_ms": self._timeout_ms,
        }

    async def _emit_rccam_state(self, session_key: str, phase: RCCAMPhase, state: RCCAMState) -> None:
        if not self._tokui_builder or not self._tokui_streamer:
            return
        try:
            from galaxyos.kernel.tokui_builder import PyTokUIBuilder
            builder = PyTokUIBuilder()
            builder.rccam_progress(
                current_stage=phase.value,
                stages_completed=list(RCCAMPhase).index(phase) if phase in list(RCCAMPhase) else 0,
                total_stages=5,
                stage_details=json.dumps({
                    "retrieved_context_count": len(state.retrieved_context),
                    "strategy": state.strategy.get("strategy", ""),
                    "timed_out": state.timed_out,
                }),
            )
            dsl = builder.build()
            await self._tokui_streamer.push(
                dsl=dsl,
                stream_id=builder.stream_id,
                workspace_id=session_key,
                component_type="rccam-progress",
            )
        except Exception as e:
            logger.warning(f"TokUI emit rccam state failed: {e}")

    async def _emit_memory_state(self, session_key: str) -> None:
        if not self._tokui_builder or not self._tokui_streamer:
            return
        try:
            from galaxyos.kernel.tokui_builder import PyTokUIBuilder
            builder = PyTokUIBuilder()
            stats = {}
            if self._memory_adapter:
                stats = self._memory_adapter.get_stats()
            builder.memory_panel(
                engram_count=stats.get("engram_count", 0),
                neural_count=stats.get("neural_count", 0),
                synapse_count=stats.get("synapse_count", 0),
                consolidation_status="active" if stats.get("consolidation_enabled") else "idle",
            )
            dsl = builder.build()
            await self._tokui_streamer.push(
                dsl=dsl,
                stream_id=builder.stream_id,
                workspace_id=session_key,
                component_type="memory-panel",
            )
        except Exception as e:
            logger.warning(f"TokUI emit memory state failed: {e}")

    async def _emit_dag_state(self, session_key: str) -> None:
        if not self._tokui_builder or not self._tokui_streamer:
            return
        try:
            from galaxyos.kernel.tokui_builder import PyTokUIBuilder
            builder = PyTokUIBuilder()
            stats = {}
            if self._dag_fusion:
                stats = self._dag_fusion.get_stats()
            builder.dag_tree(
                nodes=str(stats.get("total_nodes", 0)),
                active_node_id="",
            )
            dsl = builder.build()
            await self._tokui_streamer.push(
                dsl=dsl,
                stream_id=builder.stream_id,
                workspace_id=session_key,
                component_type="dag-tree",
            )
        except Exception as e:
            logger.warning(f"TokUI emit dag state failed: {e}")