"""test_llm_providers.py — v9.2 multi-provider LLM layer tests.

Verifies:
  1. Provider defaults resolve correctly (10+ providers)
  2. build_llm_backend() returns the right class per provider
  3. Mock fallback when no API key (for any cloud provider)
  4. MultiSlotRouter manages 4 slots independently
  5. set_slot / set_all work; is_mock / backend_name reflect state
  6. OpenAI-compat client builds correct headers + payload
  7. Anthropic client splits system message into top-level field
  8. Mock client echoes user content (no network required)

Tests run WITHOUT any real API key (all paths fall back to mock).
"""
from __future__ import annotations

import sys
from pathlib import Path

# conftest.py handles sys.path injection; just import directly.

import pytest

import llm_providers  # noqa: E402
from llm_providers import (  # noqa: E402
    LLMBackend,
    OpenAICompatClient,
    AnthropicClient,
    MockLLMClient,
    MultiSlotRouter,
    build_llm_backend,
    get_provider_defaults,
    MAINSTREAM_PROVIDERS,
)


# ── 1. Provider defaults exist for 11 providers ───────────────────────

@pytest.mark.parametrize("provider,key", [
    ("openai", "base_url"),
    ("deepseek", "base_url"),
    ("qwen", "base_url"),
    ("anthropic", "base_url"),
    ("google", "base_url"),
    ("siliconflow", "base_url"),
    ("openrouter", "base_url"),
    ("ollama", "base_url"),
    ("vllm", "base_url"),
    ("custom", "default_model"),
    ("mock", "default_model"),
])
def test_provider_defaults_exist(provider, key):
    defaults = get_provider_defaults(provider)
    assert key in defaults, f"provider {provider!r} missing {key}"
    assert defaults[key], f"provider {provider!r} has empty {key}"


def test_mainstream_catalogue_has_10_plus():
    assert len(MAINSTREAM_PROVIDERS) >= 10
    providers = [p[0] for p in MAINSTREAM_PROVIDERS]
    for required in ("openai", "deepseek", "qwen", "anthropic"):
        assert required in providers


# ── 2. build_llm_backend routes to the right class ─────────────────────

def test_anthropic_routes_to_anthropic_client():
    b = build_llm_backend({
        "provider": "anthropic",
        "api_key": "sk-ant-test",
        "model": "claude-3-5-sonnet-20241022",
    })
    assert isinstance(b, AnthropicClient)
    assert "anthropic" in b.backend_name()


def test_openai_compat_providers_all_use_openai_client():
    for prov in ("openai", "deepseek", "qwen", "siliconflow", "openrouter",
                 "ollama", "vllm", "google", "custom"):
        b = build_llm_backend({
            "provider": prov,
            "api_key": "sk-test",
            "model": "test-model",
        })
        if prov != "anthropic":
            assert isinstance(b, OpenAICompatClient), (
                f"provider {prov!r} should build OpenAICompatClient, "
                f"got {type(b).__name__}"
            )


# ── 3. No API key → Mock fallback (except local servers) ──────────────

def test_no_api_key_falls_back_to_mock():
    for prov in ("openai", "deepseek", "qwen", "anthropic", "siliconflow"):
        b = build_llm_backend({"provider": prov})
        assert b.is_mock(), f"{prov} without key should fall back to mock"


def test_local_servers_dont_require_api_key():
    for prov in ("ollama", "vllm"):
        b = build_llm_backend({"provider": prov})
        assert not b.is_mock(), f"{prov} should not fall back to mock"


def test_explicit_mock_provider():
    b = build_llm_backend({"provider": "mock", "model": "test"})
    assert b.is_mock()
    assert b.backend_name() == "mock/test"


# ── 4. MultiSlotRouter manages 4 slots independently ──────────────────

def test_router_starts_with_4_mocks():
    r = MultiSlotRouter()
    for slot in ("llm", "llm_pro", "embedding", "rerank"):
        assert r.get(slot).is_mock(), f"{slot} should start as mock"


def test_router_set_slot_rebuilds():
    r = MultiSlotRouter()
    r.set_slot("llm", {"provider": "anthropic", "api_key": "sk-test", "model": "claude-x"})
    assert not r.get("llm").is_mock()
    assert "anthropic" in r.get("llm").backend_name()
    assert r.get("embedding").is_mock()
    assert r.get("rerank").is_mock()


def test_router_set_all_bulk():
    r = MultiSlotRouter()
    r.set_all({
        "llm":       {"provider": "deepseek", "api_key": "sk-ds", "model": "deepseek-chat"},
        "llm_pro":   {"provider": "anthropic", "api_key": "sk-ant", "model": "claude-3-opus"},
        "embedding": {"provider": "openai", "api_key": "sk-openai", "model": "text-embedding-3-small"},
        "rerank":    {"provider": "mock", "model": "mock-rerank"},
    })
    assert "deepseek" in r.get("llm").backend_name()
    assert "anthropic" in r.get("llm_pro").backend_name()
    assert "openai" in r.get("embedding").backend_name()
    assert r.get("rerank").is_mock()


def test_router_set_slot_rejects_unknown():
    r = MultiSlotRouter()
    with pytest.raises(ValueError, match="unknown slot"):
        r.set_slot("vlm_doesnt_exist_yet", {"provider": "mock"})


def test_router_info_reflects_state():
    r = MultiSlotRouter()
    r.set_slot("llm", {"provider": "qwen", "api_key": "sk-qwen", "model": "qwen-plus"})
    info = r.info()
    assert info["llm"]["is_mock"] is False
    assert info["llm"]["spec"]["provider"] == "qwen"
    assert info["embedding"]["is_mock"] is True


# ── 5. OpenAI-compat client builds correct payload ────────────────────

def test_openai_compat_payload_includes_system_in_messages():
    c = OpenAICompatClient(
        provider="deepseek", base_url="https://api.deepseek.com/v1",
        api_key="sk-test", model="deepseek-chat",
    )
    payload = c._payload(
        [{"role": "user", "content": "hi"}],
        temperature=0.5, max_tokens=100, system="you are a helper",
        stream=False,
    )
    assert payload["model"] == "deepseek-chat"
    assert payload["stream"] is False
    assert payload["messages"][0] == {"role": "system", "content": "you are a helper"}
    assert payload["messages"][1] == {"role": "user", "content": "hi"}


def test_openai_compat_headers_use_bearer():
    c = OpenAICompatClient(
        provider="openai", base_url="https://x", api_key="sk-abc", model="gpt-4o"
    )
    h = c._headers()
    assert h["Authorization"] == "Bearer sk-abc"
    assert h["Content-Type"] == "application/json"


def test_openai_compat_extract_content():
    c = OpenAICompatClient(provider="openai", base_url="x", api_key="k", model="m")
    assert c._extract_content({"choices": [{"message": {"content": "hi"}}]}) == "hi"
    assert c._extract_content({}) == ""


# ── 6. Anthropic client splits system correctly ────────────────────────

def test_anthropic_splits_system_from_messages():
    c = AnthropicClient(base_url="https://api.anthropic.com",
                        api_key="sk-ant", model="claude-3-5-sonnet")
    sys_msg, msgs = c._split_system(
        [
            {"role": "system", "content": "be concise"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "who are you"},
        ],
        system="extra system",
    )
    assert "be concise" in sys_msg
    assert "extra system" in sys_msg
    assert all(m["role"] != "system" for m in msgs)
    assert len(msgs) == 3


def test_anthropic_headers_have_x_api_key():
    c = AnthropicClient(base_url="https://api.anthropic.com",
                        api_key="sk-ant-x", model="claude-3-5-sonnet")
    h = c._headers()
    assert h["x-api-key"] == "sk-ant-x"
    assert h["anthropic-version"] == "2023-06-01"


def test_anthropic_extract_text_from_blocks():
    c = AnthropicClient(base_url="x", api_key="k", model="m")
    data = {
        "content": [
            {"type": "text", "text": "Hello "},
            {"type": "text", "text": "world"},
            {"type": "tool_use", "id": "x"},
        ]
    }
    assert c._extract_text(data) == "Hello world"


# ── 7. Mock client echoes user content ────────────────────────────────

@pytest.mark.asyncio
async def test_mock_chat_echoes_user_content():
    c = MockLLMClient(model="test")
    out = await c.chat(
        [{"role": "user", "content": "ping pong"}],
        temperature=0.7, max_tokens=50,
    )
    assert "ping pong" in out
    assert "mock/test" in out


@pytest.mark.asyncio
async def test_mock_chat_handles_no_user_message():
    c = MockLLMClient()
    out = await c.chat([], temperature=0.7, max_tokens=50)
    assert "(empty)" in out


@pytest.mark.asyncio
async def test_mock_chat_includes_system_prompt():
    c = MockLLMClient()
    out = await c.chat(
        [{"role": "user", "content": "hi"}],
        system="you are a pirate",
    )
    assert "pirate" in out
