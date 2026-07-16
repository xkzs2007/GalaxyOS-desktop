"""
LLMRouterDirect — GalaxyOS LLM 调用直接路由器

绕过 Agent Studio 模型路由层，由 GalaxyOS LLM Router 直接路由 LLM 调用。
支持多模型 fallback 和负载均衡。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    pass


class LLMTimeoutError(Exception):
    pass


class LLMRouterDirect:
    DEFAULT_MODEL = "balanced"
    FALLBACK_MODELS = ["balanced", "fast", "precise"]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._model_configs: Dict[str, Dict[str, Any]] = {}
        self._call_counts: Dict[str, int] = {}
        self._initialize_models()

    def _initialize_models(self) -> None:
        for model_name in self.FALLBACK_MODELS:
            self._model_configs[model_name] = {
                "provider": self._config.get("provider", "openai_compatible"),
                "base_url": self._config.get(f"{model_name}_base_url", ""),
                "api_key_env": self._config.get(f"{model_name}_api_key_env", f"GALAXYOS_LLM_{model_name.upper()}_KEY"),
                "max_tokens": self._config.get(f"{model_name}_max_tokens", 4096),
                "temperature": self._config.get(f"{model_name}_temperature", 0.7),
            }

    async def call(
        self,
        prompt: str,
        model: str = "",
        skill_name: str = "",
        workspace_id: str = "default",
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        target_model = model or self.DEFAULT_MODEL
        self._call_counts[target_model] = self._call_counts.get(target_model, 0) + 1

        models_to_try = [target_model] + [m for m in self.FALLBACK_MODELS if m != target_model]

        for current_model in models_to_try:
            try:
                start = time.time()
                result = await self._route_to_model(
                    prompt=prompt,
                    model=current_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                latency = (time.time() - start) * 1000
                result["model_used"] = current_model
                result["latency_ms"] = round(latency, 1)
                result["fallback"] = current_model != target_model
                return result
            except LLMTimeoutError:
                logger.warning(f"LLM timeout for model {current_model}, trying fallback")
                continue
            except LLMUnavailableError:
                logger.warning(f"LLM unavailable: {current_model}, trying fallback")
                continue
            except Exception as e:
                logger.warning(f"LLM call failed for {current_model}: {e}")
                continue

        raise LLMUnavailableError(f"All LLM models unavailable: {models_to_try}")

    async def _route_to_model(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        model_config = self._model_configs.get(model, {})
        if not model_config:
            raise LLMUnavailableError(f"Model not configured: {model}")

        return {
            "status": "routed",
            "model": model,
            "prompt_length": len(prompt),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    async def list_models(self) -> List[Dict[str, Any]]:
        return [
            {"name": name, "provider": cfg.get("provider", ""), "available": True}
            for name, cfg in self._model_configs.items()
        ]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "models_configured": list(self._model_configs.keys()),
            "call_counts": self._call_counts,
            "total_calls": sum(self._call_counts.values()),
        }