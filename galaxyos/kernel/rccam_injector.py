"""
RCCAMInjector — R-CCAM 五阶段认知循环注入器

在 agent-core 的 Agent Loop 中注入 R-CCAM 五阶段认知循环：
  1. Retrieval: 液态神经记忆检索 + CRAG + GraphRAG + DAG 上下文组装
  2. Cognition: 分析检索结果 + 认知地图 + 因果推理
  3. Control: 策略选择 + 元认知调节
  4. Action: 执行动作 + 工具调用
  5. Memory: 记忆巩固 + engram 写入 + DAG 节点更新

超时降级：R-CCAM 执行超过 timeout_ms 时降级为直接 LLM 调用
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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

    在 agent-core 的 Agent Loop 中注入 R-CCAM 认知增强：
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
        memory_sync_bridge=None,
        cognition_backend=None,
        control_backend=None,
    ):
        self._memory_adapter = memory_adapter
        self._dag_fusion = dag_fusion
        self._timeout_ms = timeout_ms
        self._config = config or {}
        self._tokui_builder = tokui_builder
        self._tokui_streamer = tokui_streamer
        self._memory_sync_bridge = memory_sync_bridge
        self._cognition_backend = cognition_backend
        self._control_backend = control_backend
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
                for item in result.get("results", []):
                    if isinstance(item, dict):
                        item["source_layer"] = "liquid_neural"
                    context.append(item)
            except Exception as e:
                logger.warning(f"Memory recall failed: {e}")

        if self._dag_fusion:
            try:
                result = await self._dag_fusion.assemble(session_key=session_key, token_budget=token_budget)
                for item in result.get("context", []):
                    if isinstance(item, dict):
                        item["source_layer"] = "dag"
                    context.append(item)
            except Exception as e:
                logger.warning(f"DAG assemble failed: {e}")

        if self._memory_sync_bridge:
            try:
                workspace_id = session_key.split(":")[-1] if ":" in session_key else session_key
                oj_entries = await self._memory_sync_bridge._recall_from_oj_context(workspace_id, query, top_k=5)
                for entry in oj_entries:
                    entry_dict = {
                        "id": entry.id,
                        "content": entry.content,
                        "source": entry.source,
                        "score": entry.metadata.get("score", 0),
                        "source_layer": "agent_core",
                    }
                    context.append(entry_dict)
            except Exception as e:
                logger.warning(f"Agent-core context recall failed: {e}")

        return context

    async def _cognition(self, session_key: str, user_input: str, context: list[dict[str, Any]]) -> dict[str, Any]:
        if self._cognition_backend:
            try:
                from galaxyos.engine.cognitive_map import CognitiveMap, VectorOps
                from galaxyos.engine.causal_reasoning import CausalReasoning

                cmap = self._cognition_backend.get("cognitive_map")
                causal_engine = self._cognition_backend.get("causal_reasoning")

                if cmap is None:
                    cmap = CognitiveMap()
                if causal_engine is None:
                    causal_engine = CausalReasoning()

                context_texts = [c.get("content", "") for c in context if isinstance(c, dict) and c.get("content")]
                combined_text = " ".join(context_texts[:10])

                query_vec = VectorOps.randomized_projection(user_input, dim=cmap.dim)
                nearby = cmap.find_nearby(query_vec, k=5)

                causal_result = {}
                if combined_text.strip():
                    causal_result = causal_engine.analyze(combined_text)

                return {
                    "input_class": "complex" if len(context) >= 3 else "standard",
                    "context_relevance": min(len(context) / 5.0, 1.0),
                    "needs_retrieval": len(context) < 3,
                    "session_key": session_key,
                    "nearby_anchors": len(nearby),
                    "causal_pairs": causal_result.get("causal_pairs", []),
                    "causal_confidence": causal_result.get("confidence", 0),
                    "causal_graph": causal_result.get("causal_graph", {}),
                    "cognition_backend": "engine",
                }
            except Exception as e:
                logger.warning(f"R-CCAM cognition backend failed, falling back to heuristic: {e}")

        return {
            "input_class": "standard",
            "context_relevance": min(len(context) / 5.0, 1.0),
            "needs_retrieval": len(context) < 3,
            "session_key": session_key,
            "cognition_backend": "heuristic",
        }

    async def _control(self, session_key: str, analysis: dict[str, Any]) -> dict[str, Any]:
        if self._control_backend:
            try:
                from galaxyos.engine.rccam_classifier import classify

                user_input = ""
                state = self._active_cycles.get(session_key)
                if state:
                    user_input = state.user_input

                classification = classify(user_input)
                is_simple = classification.get("is_simple", False)
                confidence = classification.get("confidence", 0.5)

                if is_simple and confidence > 0.7:
                    strategy = "direct_reply"
                    use_cognitive = False
                else:
                    strategy = "cognitive_enhanced"
                    use_cognitive = analysis.get("context_relevance", 0) > 0.3

                return {
                    "strategy": strategy,
                    "use_cognitive_enhancement": use_cognitive,
                    "session_key": session_key,
                    "classification": classification,
                    "control_backend": "engine",
                }
            except Exception as e:
                logger.warning(f"R-CCAM control backend failed, falling back to threshold: {e}")

        return {
            "strategy": "direct_reply",
            "use_cognitive_enhancement": analysis.get("context_relevance", 0) > 0.3,
            "session_key": session_key,
            "control_backend": "threshold",
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
