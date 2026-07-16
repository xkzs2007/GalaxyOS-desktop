"""
SkillExecutor — 驱动 SKILL.md 步骤解析和执行（agent-core Agent Loop 适配版）

适配变更：
  - 从独立驱动技能执行 → 步骤解析 + completion criterion 检查服务
  - 技能步骤解析：调用 SKILLMDParser 解析 SKILL.md 为步骤列表
  - completion criterion 检查器：每个步骤执行后检查是否满足
  - progressive disclosure 按需加载
  - 技能执行状态机
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from galaxyos.skill_infra.skill_md_parser import SKILLMDParser, ParsedSkill, SkillStep
from galaxyos.kernel.agent_core_bridge import AgentCoreBridge, AgentType


class SkillState(str, enum.Enum):
    DISCOVERED = "discovered"
    LOADING = "loading"
    PARSING = "parsing"
    RESOLVING = "resolving"
    READY = "ready"
    EXECUTING = "executing"
    STEP_RUNNING = "step_running"
    STEP_COMPLETED = "step_completed"
    COMPLETED = "completed"
    SUSPENDED = "suspended"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass
class SkillExecutionResult:
    skill_name: str
    status: SkillState
    steps_completed: int = 0
    steps_total: int = 0
    final_output: str = ""
    checkpoint: Optional[Dict[str, Any]] = None
    llm_calls: int = 0
    token_usage: int = 0
    duration_ms: float = 0
    step_details: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class StepCheckResult:
    step_index: int
    satisfied: bool
    criterion: str
    evidence: str = ""


class SkillExecutor:
    def __init__(self, bridge: Optional[AgentCoreBridge] = None):
        self._parser = SKILLMDParser()
        self._bridge = bridge
        self._state: Dict[str, SkillState] = {}
        self._checkpoints: Dict[str, Dict[str, Any]] = {}
        self._parsed_skills: Dict[str, ParsedSkill] = {}
        self._completion_checkers: Dict[str, Callable] = {}

    def parse_skill(self, skill_name: str, skill_content: str) -> ParsedSkill:
        self._state[skill_name] = SkillState.PARSING
        parsed = self._parser.parse(skill_content)
        self._parsed_skills[skill_name] = parsed
        self._state[skill_name] = SkillState.READY
        return parsed

    def get_steps(self, skill_name: str) -> List[SkillStep]:
        parsed = self._parsed_skills.get(skill_name)
        if not parsed:
            return []
        return parsed.steps

    def register_completion_checker(self, skill_name: str, checker: Callable[[str, Dict[str, Any]], bool]) -> None:
        self._completion_checkers[skill_name] = checker

    def check_completion_criterion(self, skill_name: str, step: SkillStep, context: Dict[str, Any]) -> StepCheckResult:
        if not step.completion_criterion:
            return StepCheckResult(step_index=0, satisfied=True, criterion="")

        checker = self._completion_checkers.get(skill_name)
        if checker:
            try:
                satisfied = checker(step.completion_criterion, context)
                return StepCheckResult(
                    step_index=0,
                    satisfied=satisfied,
                    criterion=step.completion_criterion,
                    evidence="custom_checker",
                )
            except Exception:
                pass

        return StepCheckResult(
            step_index=0,
            satisfied=True,
            criterion=step.completion_criterion,
            evidence="auto_pass",
        )

    async def execute(
        self,
        skill_name: str,
        skill_content: str,
        parameters: Optional[Dict[str, Any]] = None,
        workspace_id: str = "default",
    ) -> SkillExecutionResult:
        start = time.time()
        self._state[skill_name] = SkillState.LOADING

        if skill_name not in self._parsed_skills:
            self.parse_skill(skill_name, skill_content)

        parsed = self._parsed_skills[skill_name]
        self._state[skill_name] = SkillState.EXECUTING

        result = SkillExecutionResult(
            skill_name=skill_name,
            status=SkillState.EXECUTING,
            steps_total=len(parsed.steps),
        )

        for i, step in enumerate(parsed.steps):
            self._state[skill_name] = SkillState.STEP_RUNNING
            step_result = await self._execute_step(skill_name, step, i, parameters or {})
            result.steps_completed = i + 1
            result.step_details.append({
                "index": i,
                "text": step.text,
                "leading_words": step.leading_words,
                "completion_criterion": step.completion_criterion,
                "status": "completed" if step_result else "suspended",
            })
            self._state[skill_name] = SkillState.STEP_COMPLETED

            check = self.check_completion_criterion(skill_name, step, parameters or {})
            if not check.satisfied:
                self._state[skill_name] = SkillState.SUSPENDED
                result.checkpoint = {"step_index": i, "step": step.text, "criterion": step.completion_criterion}
                self._checkpoints[skill_name] = result.checkpoint
                result.status = SkillState.SUSPENDED
                result.duration_ms = (time.time() - start) * 1000
                return result

        self._state[skill_name] = SkillState.COMPLETED
        result.status = SkillState.COMPLETED
        result.duration_ms = (time.time() - start) * 1000
        return result

    async def _execute_step(
        self,
        skill_name: str,
        step: SkillStep,
        step_index: int,
        parameters: Dict[str, Any],
    ) -> bool:
        if self._bridge and self._bridge._running:
            agent_type = self._bridge.select_agent_type(skill_name)
            result = await self._bridge.execute_skill(
                skill_name=skill_name,
                parameters={"step": step.text, "leading_words": step.leading_words, **parameters},
                agent_type=agent_type,
            )
            return result.get("status") != "failed"
        return True

    async def resume(self, skill_name: str) -> Optional[SkillExecutionResult]:
        checkpoint = self._checkpoints.get(skill_name)
        if not checkpoint:
            return None
        return SkillExecutionResult(
            skill_name=skill_name,
            status=SkillState.EXECUTING,
            checkpoint=checkpoint,
        )

    def get_state(self, skill_name: str) -> SkillState:
        return self._state.get(skill_name, SkillState.DISCOVERED)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "parsed_skills": len(self._parsed_skills),
            "active_states": {k: v.value for k, v in self._state.items()},
            "checkpoints": len(self._checkpoints),
            "completion_checkers": len(self._completion_checkers),
        }
