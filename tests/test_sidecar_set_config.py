"""test_sidecar_set_config.py — v9.4 unit tests for SidecarHandlers.set_config.

We don't boot the real engine (which is expensive and would require
api keys); instead we monkey-patch the SidecarHandlers instance after
construction to bypass _build_executive and inject a fake MultiSlotRouter.
This isolates the v9.4 dispatch logic:

  - 5-slot routing (vlm now included)
  - {"enabled": False} → router.disable_slot(slot)
  - {"enabled": True, ...} → router.set_slot(slot, ...)
  - absent slot → router state untouched
  - only llm changes trigger the executive rebuild
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# conftest.py handles sys.path injection; just import directly.


class FakeRouter:
    """Records every set_slot / disable_slot call for assertion."""
    SLOTS = ("llm", "llm_pro", "embedding", "rerank", "vlm")

    def __init__(self):
        self.set_slot_calls: list[tuple] = []
        self.disable_calls: list[str] = []
        self._info = {s: {"backend": f"mock/{s}", "is_mock": True,
                          "enabled": False, "spec": {"provider": "mock"}}
                     for s in self.SLOTS}

    def set_slot(self, slot, spec):
        self.set_slot_calls.append((slot, dict(spec)))
        self._info[slot] = {"backend": f"{spec.get('provider', 'mock')}/{slot}",
                            "is_mock": False, "enabled": True,
                            "spec": dict(spec)}

    def disable_slot(self, slot):
        self.disable_calls.append(slot)
        self._info[slot] = {"backend": f"mock/{slot}", "is_mock": True,
                            "enabled": False, "spec": {"provider": "mock"}}

    def info(self):
        return self._info


def _make_handlers():
    """Build a bare SidecarHandlers that skips the engine load.

    We monkey-patch __init__ to do nothing, then inject the minimum
    attributes set_config needs.
    """
    from galaxyos_sidecar import SidecarHandlers

    # Bypass __init__ entirely
    h = SidecarHandlers.__new__(SidecarHandlers)
    h._router = FakeRouter()
    h._live_config = {
        "api_key": "", "api_base": "", "model": "deepseek-chat",
        "system_prompt": "",
    }
    h._executive = MagicMock(name="executive")
    h._memo_protocol = MagicMock(name="memo_protocol")
    h._build_executive = MagicMock(name="build_executive",
                                   return_value=MagicMock(name="new_executive"))
    return h


# ── 1. Multi-slot path covers 5 slots (v9.4) ─────────────────────────

def test_set_config_routes_vlm_slot():
    h = _make_handlers()
    r = h.set_config({
        "vlm": {"enabled": True, "provider": "openai", "api_key": "sk",
                "model": "gpt-4o"},
    })
    assert ("vlm", {"enabled": True, "provider": "openai", "api_key": "sk",
                    "model": "gpt-4o"}) in h._router.set_slot_calls
    assert r["ok"] is True
    assert "slot:vlm" in r["updated"]


def test_set_config_routes_all_5_slots():
    h = _make_handlers()
    h.set_config({
        "llm":       {"enabled": True, "provider": "deepseek", "api_key": "sk-d",
                      "model": "deepseek-chat"},
        "llm_pro":   {"enabled": True, "provider": "anthropic", "api_key": "sk-a",
                      "model": "claude-x"},
        "embedding": {"enabled": True, "provider": "openai", "api_key": "sk-o",
                      "model": "text-embedding-3-small"},
        "rerank":    {"enabled": True, "provider": "mock", "model": "r"},
        "vlm":       {"enabled": True, "provider": "openai", "api_key": "sk-v",
                      "model": "gpt-4o"},
    })
    slots_called = [slot for slot, _ in h._router.set_slot_calls]
    assert slots_called == ["llm", "llm_pro", "embedding", "rerank", "vlm"]


# ── 2. {"enabled": False} explicitly disables ────────────────────────

def test_set_config_enabled_false_calls_disable_slot():
    h = _make_handlers()
    # Pre-enable the slot so we can verify disable happens
    h._router.set_slot("embedding", {"provider": "openai", "api_key": "sk",
                                       "model": "text-embedding-3-small"})
    # Now the UI sends an "off" toggle
    r = h.set_config({"embedding": {"enabled": False}})
    assert "embedding" in h._router.disable_calls
    assert not any(slot == "embedding" for slot, _ in h._router.set_slot_calls
                   if not h._router.set_slot_calls[0][0])  # set_slot shouldn't
    assert r["router_info"]["embedding"]["enabled"] is False
    assert r["router_info"]["embedding"]["is_mock"] is True


def test_set_config_enabled_false_triggers_no_executive_rebuild():
    """Disabling embedding/rerank/vlm must NOT rebuild the LLM
    Executive (which may have in-flight streams). Only llm changes
    should trigger a rebuild."""
    h = _make_handlers()
    h._build_executive.reset_mock()
    h.set_config({
        "embedding": {"enabled": False},
        "rerank":    {"enabled": False},
        "vlm":       {"enabled": False},
    })
    h._build_executive.assert_not_called()


# ── 3. Absent slot is not touched ────────────────────────────────────

def test_set_config_absent_slot_unchanged():
    h = _make_handlers()
    # Pre-enable embedding
    h._router.set_slot("embedding", {"provider": "openai", "api_key": "sk",
                                       "model": "text-embedding-3-small"})
    # Now send ONLY a llm spec
    h.set_config({"llm": {"enabled": True, "provider": "deepseek",
                          "api_key": "sk", "model": "deepseek-chat"}})
    # embedding should NOT appear in set_slot_calls again
    embedding_resets = [c for c in h._router.set_slot_calls if c[0] == "embedding"]
    assert len(embedding_resets) == 1  # only the pre-enable, no reset
    assert h._router.info()["embedding"]["enabled"] is True


# ── 4. Only llm change rebuilds executive ────────────────────────────

def test_set_config_llm_change_rebuilds_executive():
    h = _make_handlers()
    h._build_executive.reset_mock()
    h.set_config({"llm": {"enabled": True, "provider": "anthropic",
                          "api_key": "sk", "model": "claude-x"}})
    h._build_executive.assert_called_once()


def test_set_config_non_llm_change_does_not_rebuild_executive():
    h = _make_handlers()
    h._build_executive.reset_mock()
    h.set_config({"embedding": {"enabled": True, "provider": "openai",
                                "api_key": "sk", "model": "text-embedding-3-small"}})
    h._build_executive.assert_not_called()


# ── 5. system_prompt forwarding still works ───────────────────────────

def test_set_config_forwards_system_prompt():
    h = _make_handlers()
    h.set_config({
        "llm": {"enabled": True, "provider": "deepseek", "api_key": "sk",
                "model": "deepseek-chat"},
        "system_prompt": "you are a pirate",
    })
    assert h._live_config["system_prompt"] == "you are a pirate"


# ── 6. router_info returned in response ──────────────────────────────

def test_set_config_response_includes_router_info():
    h = _make_handlers()
    h.set_config({"llm": {"enabled": True, "provider": "qwen",
                          "api_key": "sk", "model": "qwen-plus"}})
    info = h._router.info()
    assert info["llm"]["enabled"] is True
    assert info["vlm"]["enabled"] is False  # untouched
    assert "embedding" in info
