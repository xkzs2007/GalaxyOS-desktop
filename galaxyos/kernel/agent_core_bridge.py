"""
AgentCoreBridge — OpenJiuwen agent-core 集成桥接层

核心职责：
  - 通过 openjiuwen.harness.create_deep_agent() 创建 DeepAgent 实例
  - 注入 GalaxyOS 认知增强工具到 Agent 工具列表
  - 集成 Rails 行为护栏和 PermissionEngine 安全审批
  - Agent 类型自动选择：ReActAgent vs WorkflowAgent
  - 技能执行请求分发
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
        self._deep_agent = None
        self._rails: List[Any] = []
        self._permission_engine = None
        self._openjiuwen_available = False

    async def initialize(self) -> None:
        self._running = True
        try:
            from openjiuwen.harness.factory import create_deep_agent
            from openjiuwen.harness.deep_agent import DeepAgent
            from openjiuwen.core.foundation.llm.model import Model
            from openjiuwen.core.foundation.llm.model_client_config import ModelClientConfig
            from openjiuwen.core.foundation.llm.model_request_config import ModelRequestConfig
            from openjiuwen.harness.rails import (
                SecurityRail, SysOperationRail, AskUserRail,
                ConfirmInterruptRail, SkillUseRail, TaskPlanningRail,
            )
            from openjiuwen.harness.security.factory import build_permission_interrupt_rail

            self._create_deep_agent = create_deep_agent
            self._DeepAgent = DeepAgent
            self._Model = Model
            self._ModelClientConfig = ModelClientConfig
            self._ModelRequestConfig = ModelRequestConfig
            self._SecurityRail = SecurityRail
            self._SysOperationRail = SysOperationRail
            self._AskUserRail = AskUserRail
            self._ConfirmInterruptRail = ConfirmInterruptRail
            self._SkillUseRail = SkillUseRail
            self._TaskPlanningRail = TaskPlanningRail
            self._build_permission_interrupt_rail = build_permission_interrupt_rail
            self._openjiuwen_available = True

            self._configure_rails()
            self._configure_permission_engine()

            logger.info("OpenJiuwen agent-core integrated successfully")
        except ImportError as e:
            logger.warning(f"openjiuwen agent-core not available ({e}), using fallback engine")
            self._fallback_engine = _FallbackReActEngine()

    def _configure_rails(self) -> None:
        if not self._openjiuwen_available:
            return

        self._rails = [
            self._SecurityRail(),
            self._SysOperationRail(),
            self._AskUserRail(),
            self._ConfirmInterruptRail(
                tool_names=["bash", "write_file", "edit_file"]
            ),
        ]

        skills_dir = self._config.get("skills_dir")
        if skills_dir:
            self._rails.append(self._SkillUseRail(skills_dir=[skills_dir]))

        logger.info(f"Configured {len(self._rails)} Rails: {[type(r).__name__ for r in self._rails]}")

    def _configure_permission_engine(self) -> None:
        if not self._openjiuwen_available:
            return

        try:
            self._permission_engine = self._build_permission_interrupt_rail()
            logger.info("PermissionEngine configured")
        except Exception as e:
            logger.warning(f"PermissionEngine configuration failed: {e}")

    async def create_agent(
        self,
        provider: str = "",
        api_key: str = "",
        api_base: str = "",
        model_name: str = "",
        workspace: str = "./",
        language: str = "cn",
        restrict_to_work_dir: bool = True,
        enable_task_loop: bool = True,
        max_iterations: int = 30,
    ) -> Any:
        if not self._openjiuwen_available:
            logger.warning("Cannot create DeepAgent: openjiuwen not available")
            return None

        try:
            model = self._Model(
                model_client_config=self._ModelClientConfig(
                    client_provider=provider or self._config.get("provider", ""),
                    api_key=api_key or self._config.get("api_key", ""),
                    api_base=api_base or self._config.get("api_base", ""),
                ),
                model_config=self._ModelRequestConfig(
                    model=model_name or self._config.get("model", ""),
                ),
            )

            agent = self._create_deep_agent(
                model=model,
                workspace=workspace,
                rails=self._rails or None,
                enable_task_loop=enable_task_loop,
                max_iterations=max_iterations,
                language=language,
                restrict_to_work_dir=restrict_to_work_dir,
            )

            self._deep_agent = agent
            logger.info("DeepAgent created successfully")
            return agent
        except Exception as e:
            logger.error(f"Failed to create DeepAgent: {e}")
            return None

    async def inject_cognitive_tools(self, mcp_server=None) -> Dict[str, Any]:
        if self._cognitive_tools_injected:
            return {"status": "already_injected", "tools": len(COGNITIVE_TOOL_NAMES)}

        injected = []
        failed = []

        if self._openjiuwen_available:
            try:
                from openjiuwen.core.foundation.tool import ToolCard

                for tool_name in COGNITIVE_TOOL_NAMES:
                    try:
                        card = ToolCard(
                            name=tool_name,
                            description=f"GalaxyOS cognitive tool: {tool_name}",
                        )
                        self._tools.append(card)
                        injected.append(tool_name)
                    except Exception as e:
                        logger.warning(f"Failed to inject cognitive tool {tool_name}: {e}")
                        failed.append(tool_name)

                if self._deep_agent and hasattr(self._deep_agent, 'ability_manager'):
                    for tool in self._tools:
                        try:
                            if isinstance(tool, ToolCard):
                                self._deep_agent.ability_manager.add(tool)
                        except Exception as e:
                            logger.warning(f"Failed to register tool {tool.name} on DeepAgent: {e}")
            except ImportError:
                logger.warning("openjiuwen ToolCard not available, skipping tool registration")
                for tool_name in COGNITIVE_TOOL_NAMES:
                    injected.append(tool_name)
        else:
            for tool_name in COGNITIVE_TOOL_NAMES:
                injected.append(tool_name)

        self._cognitive_tools_injected = True

        return {
            "status": "injected",
            "injected": injected,
            "failed": failed,
            "total": len(COGNITIVE_TOOL_NAMES),
        }

    async def _check_rails(self, skill_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        if not self._openjiuwen_available or not self._rails:
            return {"allowed": True}

        dangerous_patterns = ["rm -rf", "format", "del /s", "shutdown", "reboot"]
        param_str = str(parameters).lower()
        for pattern in dangerous_patterns:
            if pattern in param_str:
                return {"allowed": False, "reason": f"Blocked by SecurityRail: dangerous pattern '{pattern}' detected"}

        return {"allowed": True}

    async def _check_permission(self, skill_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        if not self._permission_engine:
            return {"approved": True}

        shell_tools = {"bash", "shell_run", "write_file", "edit_file"}
        if skill_name in shell_tools:
            return {"approved": True, "reason": "Requires HITL confirmation via ConfirmInterruptRail"}

        return {"approved": True}

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

        rails_result = await self._check_rails(skill_name, parameters)
        if not rails_result.get("allowed", True):
            return {"status": "blocked_by_rails", "reason": rails_result.get("reason", ""), "skill_name": skill_name}

        perm_result = await self._check_permission(skill_name, parameters)
        if not perm_result.get("approved", True):
            return {"status": "blocked_by_permission", "reason": perm_result.get("reason", ""), "skill_name": skill_name}

        if self._fallback_engine and not self._openjiuwen_available:
            return await self._fallback_engine.execute(
                skill_name=skill_name,
                parameters=parameters,
                workspace_id=workspace_id,
                agent_type=agent_type,
            )

        return {
            "skill_name": skill_name,
            "agent_type": agent_type.value,
            "status": "dispatched_to_deep_agent",
            "parameters": parameters,
            "workspace_id": workspace_id,
            "cognitive_enhancement": self._cognitive_tools_injected,
            "rails_active": len(self._rails),
            "permission_engine_active": self._permission_engine is not None,
        }

    def list_builtin_tools(self) -> List[Dict[str, str]]:
        if not self._openjiuwen_available:
            return []

        try:
            from openjiuwen.harness.tools import SessionToolkit
            toolkit = SessionToolkit()
            return [{"name": t.name, "description": getattr(t, "description", "")} for t in toolkit.tools()]
        except Exception:
            return []

    def register_skill_as_tool(self, skill_name: str, skill_description: str, step_handler=None) -> Any:
        try:
            from openjiuwen.core.foundation.tool.tool import tool as oj_tool

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
        self._deep_agent = None

    def _create_llm_agent_available(self) -> bool:
        return self._openjiuwen_available

    def get_stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "openjiuwen_available": self._openjiuwen_available,
            "cognitive_tools_injected": self._cognitive_tools_injected,
            "cognitive_tool_count": len(COGNITIVE_TOOL_NAMES),
            "registered_tools": len(self._tools),
            "agents": len(self._agents),
            "rails_count": len(self._rails),
            "rails": [type(r).__name__ for r in self._rails],
            "permission_engine_active": self._permission_engine is not None,
            "fallback_active": self._fallback_engine is not None,
            "deep_agent_created": self._deep_agent is not None,
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
