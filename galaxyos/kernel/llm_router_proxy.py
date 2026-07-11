"""
LLMRouterProxy — 拦截 agent-core LLM 调用，转发到 PilotDeck Smart Routing

职责：
1. call() — LLM 调用经 Smart Routing 路由
2. 技能路由偏好（grill-me → 旗舰模型，tdd → 轻量模型）
3. 路由降级策略
4. token 消耗记录
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SKILL_MODEL_PREFERENCE = {
    "grill-me": "flagship",
    "grill-with-docs": "flagship",
    "diagnosing-bugs": "flagship",
    "wayfinder": "flagship",
    "ask-matt": "flagship",
    "tdd": "lightweight",
    "code-review": "lightweight",
    "triage": "lightweight",
    "implement": "balanced",
    "prototype": "lightweight",
    "research": "flagship",
}


@dataclass
class TokenUsageRecord:
    workspace_id: str
    skill_name: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    timestamp: float = field(default_factory=time.time)


class LLMRouterProxy:
    def __init__(self, default_model: str = "balanced", config: Optional[Dict[str, Any]] = None):
        self._default_model = default_model
        self._config = config or {}
        self._token_records: List[TokenUsageRecord] = []
        self._fallback_model = self._config.get("fallback_model", default_model)

    async def call(
        self,
        prompt: str,
        model: Optional[str] = None,
        skill_name: Optional[str] = None,
        workspace_id: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        selected_model = self._select_model(model, skill_name)

        try:
            result = await self._route_to_model(selected_model, prompt, temperature, max_tokens)
            self._record_token_usage(workspace_id, skill_name, selected_model, result)
            return result
        except Exception:
            if selected_model != self._fallback_model:
                result = await self._route_to_model(self._fallback_model, prompt, temperature, max_tokens)
                self._record_token_usage(workspace_id, skill_name, self._fallback_model, result)
                return result
            raise

    def _select_model(self, requested_model: Optional[str], skill_name: Optional[str]) -> str:
        if requested_model:
            return requested_model
        if skill_name and skill_name in SKILL_MODEL_PREFERENCE:
            return SKILL_MODEL_PREFERENCE[skill_name]
        return self._default_model

    async def _route_to_model(
        self,
        model: str,
        prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> Dict[str, Any]:
        return {
            "model": model,
            "response": f"[routed to {model}]",
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": 100,
            "total_tokens": len(prompt) // 4 + 100,
        }

    def _record_token_usage(
        self,
        workspace_id: str,
        skill_name: Optional[str],
        model: str,
        result: Dict[str, Any],
    ) -> None:
        self._token_records.append(TokenUsageRecord(
            workspace_id=workspace_id,
            skill_name=skill_name or "unknown",
            model=model,
            prompt_tokens=result.get("prompt_tokens", 0),
            completion_tokens=result.get("completion_tokens", 0),
            total_tokens=result.get("total_tokens", 0),
        ))

    def get_token_usage(
        self,
        workspace_id: Optional[str] = None,
        skill_name: Optional[str] = None,
    ) -> List[TokenUsageRecord]:
        records = self._token_records
        if workspace_id:
            records = [r for r in records if r.workspace_id == workspace_id]
        if skill_name:
            records = [r for r in records if r.skill_name == skill_name]
        return records

    def get_cost_summary(self, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        records = self.get_token_usage(workspace_id=workspace_id)
        by_model: Dict[str, int] = {}
        by_skill: Dict[str, int] = {}
        for r in records:
            by_model[r.model] = by_model.get(r.model, 0) + r.total_tokens
            by_skill[r.skill_name] = by_skill.get(r.skill_name, 0) + r.total_tokens
        return {
            "total_tokens": sum(r.total_tokens for r in records),
            "by_model": by_model,
            "by_skill": by_skill,
        }