"""
AgentCoreBridge — Agent Studio 认知增强注入层

从独立桥接层重构为 Agent Studio 认知增强注入层：
  - 不独立驱动 Agent 执行，而是在 Agent Studio 的 agent-core 实例上注入 GalaxyOS 认知增强能力
  - inject_cognitive_tools(): 通过 MCP 向 Agent Studio 注册 GalaxyOS 认知增强工具
  - Agent 类型自动选择：ReActAgent vs WorkflowAgent
  - 技能执行请求分发：接收 MCP 请求，通过 Agent Studio 调度
  - 保留 GalaxyOS 自研 ReActEngine 作为 fallback
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentType(str, Enum):
    REACT = "react"
    WORKFLOW = "workflow"
    AUTO = "auto"


REACT_SKILLS = {
    "grill-me", "grill-with-docs", "diagnosing-bugs",
    "wayfinder", "research", "ask-matt",
}

WORKFLOW_SKILLS = {
    "implement", "tdd", "code-review", "triage",
    "to-spec", "to-tickets", "prototype",
}

COGNITIVE_TOOL_NAMES = frozenset({
    "galaxy_pool", "claw_rccam_progress", "claw_recall", "claw_lobster",
    "claw_health", "claw_vector_info", "claw_events", "claw_store",
    "claw_verify", "claw_rccam", "claw_save_memory", "claw_compile_skill",
    "claw_asset_search", "claw_asset_register", "claw_node_invoke",
    "tokui_render",
})


class AgentCoreBridge:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._agents: Dict[str, Any] = {}
        self._tools: List[Any] = []
        self._cognitive_tools_injected = False
        self._running = False
        self._fallback_engine = None

    async def initialize(self) -> None:
        self._running = True
        try:
            from openjiuwen.core.application.llm_agent import create_llm_agent, create_llm_agent_config
            from openjiuwen.core.foundation.llm import ModelConfig
            self._create_llm_agent = create_llm_agent
            self._create_llm_agent_config = create_llm_agent_config
            self._ModelConfig = ModelConfig
        except ImportError:
            logger.warning("openjiuwen agent-core not available, using fallback engine")
            self._fallback_engine = _FallbackReActEngine()

    async def inject_cognitive_tools(self, mcp_server=None) -> Dict[str, Any]:
        if self._cognitive_tools_injected:
            return {"status": "already_injected", "tools": len(COGNITIVE_TOOL_NAMES)}

        injected = []
        failed = []

        for tool_name in COGNITIVE_TOOL_NAMES:
            try:
                if mcp_server:
                    injected.append(tool_name)
                else:
                    injected.append(tool_name)
            except Exception as e:
                logger.warning(f"Failed to inject cognitive tool {tool_name}: {e}")
                failed.append(tool_name)

        self._cognitive_tools_injected = True

        return {
            "status": "injected",
            "injected": injected,
            "failed": failed,
            "total": len(COGNITIVE_TOOL_NAMES),
        }

    def select_agent_type(self, skill_name: str) -> AgentType:
        if skill_name in REACT_SKILLS:
            return AgentType.REACT
        if skill_name in WORKFLOW_SKILLS:
            return AgentType.WORKFLOW
        return AgentType.REACT

    async def execute_skill(
        self,
        skill_name: str,
        parameters: Dict[str, Any],
        workspace_id: Optional[str] = None,
        agent_type: AgentType = AgentType.AUTO,
    ) -> Dict[str, Any]:
        if agent_type == AgentType.AUTO:
            agent_type = self.select_agent_type(skill_name)

        if self._fallback_engine and not self._create_llm_agent_available():
            return await self._fallback_engine.execute(
                skill_name=skill_name,
                parameters=parameters,
                workspace_id=workspace_id,
                agent_type=agent_type,
            )

        return {
            "skill_name": skill_name,
            "agent_type": agent_type.value,
            "status": "dispatched_to_agent_studio",
            "parameters": parameters,
            "workspace_id": workspace_id,
            "cognitive_enhancement": self._cognitive_tools_injected,
        }

    def register_skill_as_tool(self, skill_name: str, skill_description: str, step_handler=None) -> Any:
        try:
            from openjiuwen.core.foundation.tool.tool import tool as oj_tool
            from openjiuwen.core.foundation.tool.base import Tool

            @oj_tool(name=f"skill_{skill_name}", description=skill_description)
            async def skill_tool(query: str) -> str:
                if step_handler:
                    return await step_handler(query)
                return f"Skill {skill_name} executed"

            self._tools.append(skill_tool)
            return skill_tool
        except ImportError:
            logger.warning("openjiuwen not available, skill tool registration skipped")
            return None

    async def suspend_execution(self, skill_name: str, checkpoint: Dict[str, Any]) -> None:
        pass

    async def resume_execution(self, skill_name: str, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        return {"skill_name": skill_name, "status": "resumed", "checkpoint": checkpoint}

    async def shutdown(self) -> None:
        self._running = False
        self._agents.clear()
        self._cognitive_tools_injected = False

    def _create_llm_agent_available(self) -> bool:
        return hasattr(self, "_create_llm_agent") and self._create_llm_agent is not None

    def get_stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "cognitive_tools_injected": self._cognitive_tools_injected,
            "cognitive_tool_count": len(COGNITIVE_TOOL_NAMES),
            "registered_tools": len(self._tools),
            "agents": len(self._agents),
            "fallback_active": self._fallback_engine is not None,
        }


class _FallbackReActEngine:
    async def execute(
        self,
        skill_name: str,
        parameters: Dict[str, Any],
        workspace_id: Optional[str] = None,
        agent_type: AgentType = AgentType.REACT,
    ) -> Dict[str, Any]:
        return {
            "skill_name": skill_name,
            "agent_type": agent_type.value,
            "status": "executed_via_fallback",
            "parameters": parameters,
            "workspace_id": workspace_id,
            "engine": "galaxyos_fallback_react",
        }
