"""llm_providers.py — Multi-provider LLM/embedding/rerank/VLM client layer.

GalaxyOS v9.2 — replaces the single-provider ``DeepSeekExecutiveClient``
model with a multi-provider router. Each "slot" (llm, llm_pro, embedding,
rerank, vlm) can independently point at a different provider. All
backends use **pure httpx** (no SDK dependencies) for portability.

Supported providers (v9.2):
  - openai      : OpenAI / DeepSeek / Qwen DashScope / SiliconFlow / OpenRouter
                  / vLLM / Ollama / any OpenAI-compatible endpoint
  - anthropic   : Anthropic Claude (messages API, x-api-key auth)
  - google      : Google Gemini (generativelanguage API)
  - ollama      : Ollama local (openai-compat at /v1)
  - mock        : deterministic stub (no network)

Each backend implements a common protocol (async)::

    async chat(messages, **kwargs) -> str
    async stream_chat(messages, **kwargs) -> AsyncIterator[str]
    backend_name() -> str
    is_mock() -> bool

The provider router maps a (provider, base_url, model, api_key) tuple
to the right backend. Settings → set_config() → router → live swap.

Why pure httpx (not openai/anthropic SDKs):
  - Zero extra deps (only httpx already in core)
  - Same code path for 6+ OpenAI-compatible vendors
  - Streaming + tool-use handled uniformly
  - Easy to add a new provider (subclass OpenAICompatClient, override 1-2 methods)
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

log = logging.getLogger("galaxyos.llm_providers")

# ── Defaults per provider ──────────────────────────────────────────────
# Each provider has a curated model list that mirrors what Chatbox-style
# clients show. Users pick from this list OR type a custom model ID.
# Format: { "model_id": "Display Name", ... }

_PROVIDER_DEFAULTS: Dict[str, Any] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "models": {
            "gpt-4o-mini":        "GPT-4o Mini",
            "gpt-4o":             "GPT-4o",
            "gpt-4.1":            "GPT-4.1",
            "o1":                 "o1",
            "o3":                 "o3",
            "o3-mini":            "o3 Mini",
            "o4-mini":            "o4 Mini",
        },
    },
    "deepseek": {
        # deepseek-chat / deepseek-reasoner deprecated 2026-07-24
        # → replaced by V4: deepseek-v4-pro (thinking) / deepseek-v4-flash (fast)
        # For now, keep both old + new in the curated list so users can pick
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "models": {
            "deepseek-v4-flash":   "DeepSeek V4 Flash（快，便宜）",
            "deepseek-v4-pro":     "DeepSeek V4 Pro（强，thinking）",
            "deepseek-chat":       "DeepSeek V3 Chat（旧版，即将废弃 7/24）",
            "deepseek-reasoner":   "DeepSeek R1 Reasoner（旧版，即将废弃 7/24）",
        },
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "models": {
            "qwen-plus":           "Qwen Plus",
            "qwen-max":            "Qwen Max",
            "qwen-coder-plus":     "Qwen Coder Plus",
            "qwen-turbo":          "Qwen Turbo",
        },
    },
    "anthropic": {
        # Claude 3.5 retired; current family: Sonnet 5 / Fable 5 / Opus 4.8 / Sonnet 4.6
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-6-20250514",
        "models": {
            "claude-sonnet-5-20250630":     "Claude Sonnet 5（最新）",
            "claude-fable-5-20250609":      "Claude Fable 5（旗舰）",
            "claude-opus-4-8-20250514":     "Claude Opus 4.8",
            "claude-sonnet-4-6-20250514":   "Claude Sonnet 4.6（默认）",
            "claude-haiku-4-5-20250514":    "Claude Haiku 4.5（快）",
        },
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "default_model": "gemini-2.5-flash",
        "models": {
            "gemini-3-pro":          "Gemini 3 Pro（旗舰）",
            "gemini-3-flash":        "Gemini 3 Flash（快）",
            "gemini-2.5-pro":        "Gemini 2.5 Pro",
            "gemini-2.5-flash":      "Gemini 2.5 Flash（默认）",
        },
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
        "models": {},
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-4-6",
        "models": {},
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "default_model": "qwen2.5:7b",
    },
    "vllm": {
        "base_url": "http://127.0.0.1:8000/v1",
        "default_model": "Qwen/Qwen2.5-7B-Instruct",
    },
    "custom": {
        "base_url": "",
        "default_model": "default",
    },
    "mock": {
        "base_url": "",
        "default_model": "mock-1",
    },
}


def get_provider_defaults(provider: str) -> Dict[str, Any]:
    """Return base_url, default_model, and models dict for a provider."""
    return dict(_PROVIDER_DEFAULTS.get(provider.lower(), {}))


# ── Protocol / ABC ─────────────────────────────────────────────────────


class LLMBackend(ABC):
    @abstractmethod
    async def chat(self, messages, *, temperature=0.7, max_tokens=1024,
                   system=None, **kwargs) -> str: ...

    @abstractmethod
    async def stream_chat(self, messages, *, temperature=0.7, max_tokens=1024,
                          system=None, **kwargs) -> AsyncIterator[str]: ...

    @abstractmethod
    def backend_name(self) -> str: ...

    def is_mock(self) -> bool:
        return False


# ── OpenAI-compat (covers 6+ providers) ────────────────────────────────


class OpenAICompatClient(LLMBackend):
    def __init__(self, *, provider: str = "openai", base_url: str,
                 api_key: str, model: str, timeout: float = 60.0) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._chat_path = "/chat/completions"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, messages, temperature, max_tokens, system, stream):
        msgs: List[Dict[str, str]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        return {
            "model": self.model,
            "messages": msgs,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": bool(stream),
        }

    async def chat(self, messages, *, temperature=0.7, max_tokens=1024,
                   system=None, **kwargs) -> str:
        url = f"{self.base_url}{self._chat_path}"
        payload = self._payload(messages, temperature, max_tokens, system, stream=False)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(url, headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()
        return self._extract_content(data)

    async def stream_chat(self, messages, *, temperature=0.7, max_tokens=1024,
                          system=None, **kwargs) -> AsyncIterator[str]:
        url = f"{self.base_url}{self._chat_path}"
        payload = self._payload(messages, temperature, max_tokens, system, stream=True)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, headers=self._headers(),
                                     json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    delta = self._extract_delta(obj)
                    if delta:
                        yield delta

    def _extract_content(self, data: Dict[str, Any]) -> str:
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            log.warning("OpenAI-compat: extract failed: %s — data=%s", e, str(data)[:200])
            return ""

    def _extract_delta(self, obj: Dict[str, Any]) -> str:
        try:
            return obj["choices"][0]["delta"].get("content", "") or ""
        except (KeyError, IndexError, TypeError):
            return ""

    def backend_name(self) -> str:
        return f"{self.provider}/{self.model} (OpenAI-compat @ {self.base_url})"


# ── Anthropic ──────────────────────────────────────────────────────────


class AnthropicClient(LLMBackend):
    def __init__(self, *, base_url: str, api_key: str, model: str,
                 timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._version = "2023-06-01"

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self._version,
            "Content-Type": "application/json",
        }

    def _split_system(self, messages, system):
        sys_msg = system
        cleaned: List[Dict[str, str]] = []
        for m in messages:
            if m.get("role") == "system":
                sys_msg = (sys_msg + "\n" + m["content"]) if sys_msg else m["content"]
            else:
                cleaned.append(m)
        return sys_msg, cleaned

    async def chat(self, messages, *, temperature=0.7, max_tokens=1024,
                   system=None, **kwargs) -> str:
        url = f"{self.base_url}/v1/messages"
        sys_msg, msgs = self._split_system(messages, system)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
        }
        if sys_msg:
            payload["system"] = sys_msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(url, headers=self._headers(), json=payload)
            r.raise_for_status()
            data = r.json()
        return self._extract_text(data)

    async def stream_chat(self, messages, *, temperature=0.7, max_tokens=1024,
                          system=None, **kwargs) -> AsyncIterator[str]:
        url = f"{self.base_url}/v1/messages"
        sys_msg, msgs = self._split_system(messages, system)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "stream": True,
        }
        if sys_msg:
            payload["system"] = sys_msg
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, headers=self._headers(),
                                     json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            obj = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        if obj.get("type") == "content_block_delta":
                            delta = obj.get("delta", {}).get("text", "")
                            if delta:
                                yield delta

    def _extract_text(self, data: Dict[str, Any]) -> str:
        try:
            blocks = data.get("content", [])
            return "".join(b.get("text", "") for b in blocks
                          if b.get("type") == "text")
        except Exception as e:
            log.warning("Anthropic: extract failed: %s", e)
            return ""

    def backend_name(self) -> str:
        return f"anthropic/{self.model} (messages API)"


# ── Mock ───────────────────────────────────────────────────────────────


class MockLLMClient(LLMBackend):
    def __init__(self, *, model: str = "mock-1") -> None:
        self._model = model
        self._calls = 0

    async def chat(self, messages, *, temperature=0.7, max_tokens=1024,
                   system=None, **kwargs) -> str:
        self._calls += 1
        last = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                last = str(m.get("content", ""))[:200]
                break
        sys_note = f" [system: {system[:60]}]" if system else ""
        return (
            f"[mock/{self._model}{sys_note}] 你说的是: {last or '(empty)'}"
        )

    async def stream_chat(self, messages, *, temperature=0.7, max_tokens=1024,
                          system=None, **kwargs) -> AsyncIterator[str]:
        full = await self.chat(messages, temperature=temperature, max_tokens=max_tokens,
                               system=system, **kwargs)
        for ch in full:
            yield ch
            await _async_sleep_ms(2)

    def backend_name(self) -> str:
        return f"mock/{self._model}"

    def is_mock(self) -> bool:
        return True

    @property
    def call_count(self) -> int:
        return self._calls


# ── Factory + Router ───────────────────────────────────────────────────


def build_llm_backend(spec: Dict[str, Any]) -> LLMBackend:
    """Build an LLMBackend from a {provider, base_url, api_key, model} spec."""
    provider = (spec.get("provider") or "openai").lower().strip()
    base_url = (spec.get("base_url") or spec.get("api_base") or "").strip()
    api_key = (spec.get("api_key") or "").strip()
    model = (spec.get("model") or "").strip()
    timeout = float(spec.get("timeout", 60.0))

    defaults = get_provider_defaults(provider)
    if not base_url and defaults.get("base_url"):
        base_url = defaults["base_url"]
    if not model:
        model = defaults.get("default_model", "default")

    if provider == "mock" or (
        not api_key and provider not in ("ollama", "vllm", "mock")
    ):
        return MockLLMClient(model=model)

    if provider == "anthropic":
        return AnthropicClient(base_url=base_url, api_key=api_key,
                               model=model, timeout=timeout)

    return OpenAICompatClient(provider=provider, base_url=base_url,
                              api_key=api_key, model=model, timeout=timeout)


class MultiSlotRouter:
    """Manages 5 independent slots: llm / llm_pro / embedding / rerank / vlm.

    Each slot has its own (provider, base_url, api_key, model) spec and
    is rebuilt lazily on demand. This is the v9.2 answer to the question
    "why does my Qwen-2.5 dropdown secretly use DeepSeek?" — each slot
    is now first-class and explicit.

    v9.4 — "LLM required, others optional":
      - Every slot starts **disabled** (a mock backend sits behind it,
        but ``is_enabled()`` returns False so callers know to fall back
        to local implementations: BoW embedding / no-rerank / no-VLM).
      - ``set_slot(slot, spec)`` **enables** the slot AND rebuilds the
        backend. Pass ``enabled=False`` in the spec to explicitly
        disable a slot (back to mock).
      - ``disable_slot(slot)`` reverts the slot to a fresh mock and
        flips ``is_enabled`` back to False — the proper way to "turn
        off" embedding/rerank/vlm at runtime.
      - ``info()`` now exposes the per-slot ``enabled`` flag so the
        renderer's diagnostics tab can show "embedding: off (BoW)".

    Slot semantics for callers:
      - ``llm``       → required for any LLM call. If disabled, callers
                        fall back to whatever the harness provides.
      - ``llm_pro``   → optional secondary LLM (e.g. Claude for
                        reasoning + Qwen for chat). Used by R-CCAM.
      - ``embedding`` → optional. When disabled, MeMo recall and
                        SkillGraph retrieval fall back to BoW / BM25.
      - ``rerank``    → optional. When disabled, retrieval returns the
                        raw top-k from embedding/BM25 without re-scoring.
      - ``vlm``       → optional. When disabled, image attachments show
                        a [callout v:warn] "VLM not configured" instead
                        of being OCR'd.
    """
    SLOTS = ("llm", "llm_pro", "embedding", "rerank", "vlm")

    def __init__(self) -> None:
        self._slots: Dict[str, LLMBackend] = {}
        self._specs: Dict[str, Dict[str, Any]] = {}
        self._enabled: Dict[str, bool] = {}
        for slot in self.SLOTS:
            self._slots[slot] = MockLLMClient(model=f"mock-{slot}")
            self._specs[slot] = {"provider": "mock"}
            self._enabled[slot] = False  # all disabled by default
        log.info("MultiSlotRouter initialised: %d slots (all disabled)", len(self.SLOTS))

    def get(self, slot: str) -> LLMBackend:
        if slot not in self.SLOTS:
            raise ValueError(f"unknown slot {slot!r}; valid: {self.SLOTS}")
        return self._slots[slot]

    def is_enabled(self, slot: str) -> bool:
        """Return True if the slot has been explicitly enabled via set_slot
        (or set_all) with a real (non-mock) provider. Callers should use
        this to decide whether to use the slot or fall back."""
        if slot not in self.SLOTS:
            raise ValueError(f"unknown slot {slot!r}")
        return self._enabled[slot]

    def set_slot(self, slot: str, spec: Dict[str, Any]) -> None:
        """Configure a slot. The slot is automatically enabled unless
        ``spec['enabled'] is False``. A slot configured with provider=mock
        and no api_key is also considered disabled."""
        if slot not in self.SLOTS:
            raise ValueError(f"unknown slot {slot!r}")
        self._specs[slot] = dict(spec)
        self._slots[slot] = build_llm_backend(spec)
        # enabled: explicit flag wins; otherwise enabled iff non-mock
        explicit = spec.get("enabled")
        if explicit is None:
            self._enabled[slot] = not self._slots[slot].is_mock()
        else:
            self._enabled[slot] = bool(explicit)
        log.info("slot[%s] rebuilt: %s (enabled=%s)",
                 slot, self._slots[slot].backend_name(), self._enabled[slot])

    def disable_slot(self, slot: str) -> None:
        """Revert a slot to mock + disabled. Safe to call multiple times."""
        if slot not in self.SLOTS:
            raise ValueError(f"unknown slot {slot!r}")
        self._specs[slot] = {"provider": "mock"}
        self._slots[slot] = MockLLMClient(model=f"mock-{slot}")
        self._enabled[slot] = False
        log.info("slot[%s] disabled (mock fallback)", slot)

    def set_all(self, specs: Dict[str, Dict[str, Any]]) -> None:
        for slot, spec in specs.items():
            if slot in self.SLOTS:
                self.set_slot(slot, spec)
            else:
                log.warning("set_all: unknown slot %r skipped", slot)

    def info(self) -> Dict[str, Any]:
        return {
            slot: {
                "backend": self._slots[slot].backend_name(),
                "is_mock": self._slots[slot].is_mock(),
                "enabled": self._enabled[slot],
                "spec": self._specs[slot],
            }
            for slot in self.SLOTS
        }


# ── Helpers ────────────────────────────────────────────────────────────


async def _async_sleep_ms(ms: int) -> None:
    import asyncio
    await asyncio.sleep(ms / 1000.0)


# ── Catalogue of "mainstream providers" for the renderer's UI ─────────

MAINSTREAM_PROVIDERS = [
    # (provider_id, display_name, default_model, hint, models_dict)
    ("openai",      "OpenAI",       "gpt-4o-mini",              "GPT-4o / 4o-mini / o1 / o3",
        _PROVIDER_DEFAULTS["openai"]["models"]),
    ("deepseek",    "DeepSeek",     "deepseek-v4-flash",        "V4 Flash（快）/ V4 Pro（thinking）— 旧版 7/24 废弃",
        _PROVIDER_DEFAULTS["deepseek"]["models"]),
    ("qwen",        "Qwen (DashScope)", "qwen-plus",            "qwen-plus / max / coder / turbo",
        _PROVIDER_DEFAULTS["qwen"]["models"]),
    ("anthropic",   "Anthropic",    "claude-sonnet-4-6-20250514", "Sonnet 5 / Fable 5 / Opus 4.8 / Sonnet 4.6 / Haiku 4.5",
        _PROVIDER_DEFAULTS["anthropic"]["models"]),
    ("google",      "Google Gemini","gemini-2.5-flash",         "Gemini 3 Pro / 3 Flash / 2.5 Pro / 2.5 Flash",
        _PROVIDER_DEFAULTS["google"]["models"]),
    ("siliconflow", "SiliconFlow",  "Qwen/Qwen2.5-7B-Instruct","硅基流动 — 多模型托管",
        _PROVIDER_DEFAULTS["siliconflow"]["models"]),
    ("openrouter",  "OpenRouter",   "anthropic/claude-sonnet-4-6", "OpenRouter — 任意模型路由",
        _PROVIDER_DEFAULTS["openrouter"]["models"]),
    ("ollama",      "Ollama (本地)", "qwen2.5:7b",               "Ollama 本地推理",
        _PROVIDER_DEFAULTS["ollama"]["models"]),
    ("vllm",        "vLLM (本地)",  "Qwen/Qwen2.5-7B-Instruct", "vLLM OpenAI-compat 服务",
        _PROVIDER_DEFAULTS["vllm"]["models"]),
    ("custom",      "自定义 (OpenAI 兼容)", "default",          "任意 OpenAI 兼容端点",
        _PROVIDER_DEFAULTS["custom"]["models"]),
    ("mock",        "Mock (脱机)",   "mock-1",                  "无网络，确定性回声",
        _PROVIDER_DEFAULTS["mock"]["models"]),
]


__all__ = [
    "LLMBackend",
    "OpenAICompatClient",
    "AnthropicClient",
    "MockLLMClient",
    "MultiSlotRouter",
    "MAINSTREAM_PROVIDERS",
    "build_llm_backend",
    "get_provider_defaults",
    "_PROVIDER_DEFAULTS",
]
