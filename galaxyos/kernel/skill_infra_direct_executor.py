"""
SkillInfraDirectExecutor — GalaxyOS 原生技能直接执行器

绕过 Agent Studio Gateway，由 GalaxyOS skill_infra 模块直接执行技能管理。
技能执行经 AgentCoreBridge 路由到 ReActAgent/WorkflowAgent。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SkillNotFoundError(Exception):
    pass


class SkillExecutionError(Exception):
    pass


class RailBlockError(Exception):
    pass


class SkillInfraDirectExecutor:
    def __init__(self, bridge=None, skill_infra_path: str = ""):
        self._bridge = bridge
        self._skill_infra_path = skill_infra_path
        self._installed_skills: Dict[str, Dict[str, Any]] = {}

    async def execute(
        self,
        skill_name: str,
        parameters: Dict[str, Any] = None,
        workspace_id: str = "default",
        agent_type: str = "auto",
    ) -> Dict[str, Any]:
        if skill_name not in self._installed_skills:
            raise SkillNotFoundError(f"Skill not found: {skill_name}")

        if self._bridge:
            try:
                result = await self._bridge.execute_skill(
                    skill_name=skill_name,
                    parameters=parameters or {},
                    workspace_id=workspace_id,
                    agent_type=agent_type,
                )
                return result
            except Exception as e:
                error_msg = str(e)
                if "rail" in error_msg.lower() or "blocked" in error_msg.lower():
                    raise RailBlockError(f"Skill blocked by rail: {error_msg}") from e
                raise SkillExecutionError(f"Skill execution failed: {error_msg}") from e

        return {
            "status": "executed",
            "skill": skill_name,
            "workspace": workspace_id,
            "agent_type": agent_type,
        }

    async def install(
        self,
        source: str = "github",
        source_url: str = "",
        scope: str = "user",
        workspace_id: str = "default",
    ) -> Dict[str, Any]:
        skill_name = source_url.split("/")[-1] if source_url else source
        self._installed_skills[skill_name] = {
            "source": source,
            "source_url": source_url,
            "scope": scope,
            "workspace_id": workspace_id,
        }
        logger.info(f"Skill installed: {skill_name} from {source}")
        return {
            "status": "installed",
            "skill": skill_name,
            "source": source,
            "scope": scope,
            "workspace": workspace_id,
        }

    async def discover(
        self,
        workspace_id: str = "default",
        invocation_type: str = "all",
        query: str = "",
    ) -> Dict[str, Any]:
        skills = list(self._installed_skills.values())
        if query:
            skills = [s for s in skills if query.lower() in s.get("source_url", "").lower()]
        return {
            "skills": skills,
            "workspace": workspace_id,
            "type": invocation_type,
            "query": query,
            "count": len(skills),
        }

    async def compile_skill(
        self,
        skill_name: str,
        skill_content: str = "",
    ) -> Dict[str, Any]:
        if skill_name not in self._installed_skills:
            raise SkillNotFoundError(f"Skill not found: {skill_name}")
        return {
            "skill": skill_name,
            "compiled": True,
            "content_length": len(skill_content),
        }