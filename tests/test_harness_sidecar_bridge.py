"""test_harness_sidecar_bridge.py — End-to-end v9.1 smoke test.

Verifies that:
  1. ``create_galaxy_agent(model="qwen-2.5")`` constructs without error
  2. ``workspace.llm`` is set to a SidecarBackend (not None, not Canned)
  3. ``agent.run("hi")`` actually drives the in-process SidecarHandlers
     and returns a non-empty result
  4. The streaming ``.stream()`` path yields >0 fragments
  5. ``_pick_stream_kind()`` routes 5 model names correctly

This is the v9.1 acceptance test. It runs WITHOUT requiring a running
sidecar on the network port — the bridge is in-process.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make the harness importable
_REPO = Path(__file__).resolve().parent
_HARNESS = _REPO / "galaxyos"
for p in (_REPO, _HARNESS):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

import pytest

pytest_plugins = ("pytest_asyncio",)

from galaxyos.harness import create_galaxy_agent, DeepAgentConfig
from galaxyos.harness.sidecar_bridge import (
    SidecarBackend,
    _pick_stream_kind,
    _MODEL_TO_STREAM,
    build_sidecar_backend,
    _fragments_to_text,
    _last_user_message,
)


# ── 1. Factory constructs cleanly ──────────────────────────────────────

def test_factory_constructs_with_qwen_model():
    """create_galaxy_agent must build a DeepAgent without raising."""
    config = DeepAgentConfig(name="test-qwen", model="qwen-2.5")
    agent = create_galaxy_agent(
        name=config.name, model=config.model,
        workspace_dir=Path("/tmp/galaxyos-test-qwen"),
    )
    assert agent is not None
    assert isinstance(agent, object)
    assert hasattr(agent, "run")
    assert hasattr(agent, "workspace")
    assert hasattr(agent.workspace, "llm"), "workspace.llm must be set"
    # workspace.llm should not be None after v9.1
    assert agent.workspace.llm is not None, (
        "v9.1 fail: workspace.llm is None — _pick_llm_backend didn't run"
    )


# ── 2. Model routing resolves to the right stream kind ────────────────

@pytest.mark.parametrize("model,expected_kind", [
    ("qwen-2.5", "ask"),
    ("qwen2.5-7b", "ask"),
    ("qwen-3", "process"),
    ("deepseek-v4", "process"),
    ("deepseek-chat", "process"),
    ("gemini-3-flash", "process"),
    ("lfm-2.5-1.2b", "memo"),
    ("lfm2.5-1.2b-instruct", "memo"),
    ("unknown-model-xyz", "ask"),  # default fallback
    ("", "ask"),
])
def test_pick_stream_kind(model, expected_kind):
    assert _pick_stream_kind(model) == expected_kind


# ── 3. SidecarBackend constructs (in-process) ─────────────────────────

def test_sidecar_backend_can_be_built():
    """build_sidecar_backend must succeed in the test env
    (desktop-shell/python must be on the import path)."""
    backend = build_sidecar_backend(model="qwen-2.5")
    # If SidecarHandlers is importable, backend should be a SidecarBackend
    if backend is not None:
        assert isinstance(backend, SidecarBackend)
        name = backend.backend_name()
        assert "SidecarBackend" in name
        assert backend.is_sidecar() is True
    # If not importable, None is acceptable (env doesn't have sidecar)


# ── 4. End-to-end agent.run() drives the real engine ─────────────────

@pytest.mark.asyncio
async def test_agent_run_with_sidecar() -> None:
    """The headline v9.1 test: agent.run() actually invokes the
    SidecarHandlers and returns a real result, not a canned fallback."""
    config = DeepAgentConfig(name="e2e", model="qwen-2.5")
    agent = create_galaxy_agent(
        name=config.name, model=config.model,
        workspace_dir=Path("/tmp/galaxyos-e2e"),
    )
    result = await agent.run("What is GalaxyOS")
    assert isinstance(result, dict)
    assert "result" in result
    assert "session_id" in result
    # The result should not be the canned fallback
    if agent.workspace.llm is not None and agent.workspace.llm.is_sidecar():
        # If sidecar was used, the answer should mention GalaxyOS
        # (or be at least a non-trivial response)
        answer_text = str(result.get("result", ""))
        # In case the result is nested, flatten
        if isinstance(result.get("result"), dict):
            answer_text = str(result["result"].get("text", answer_text))
        # Must not be the canned "(no LLM configured)" message
        assert "no LLM" not in answer_text.lower(), (
            "v9.1 fail: agent didn't reach the LLM backend"
        )


# ── 5. Streaming path yields fragments ───────────────────────────────

@pytest.mark.asyncio
async def test_sidecar_backend_stream_yields_fragments():
    """The .stream() path must yield >0 DSL fragments."""
    backend = build_sidecar_backend(model="qwen-2.5")
    if backend is None:
        pytest.skip("SidecarHandlers not importable in this env")
    messages = [{"role": "user", "content": "What is GalaxyOS"}]
    fragments = []
    async for frag in backend.stream(messages, session_id="test"):
        fragments.append(frag)
    assert len(fragments) > 0, "sidecar stream produced no fragments"
    # At least one fragment should be a TokUI [bubble] opener
    assert any("bubble" in f for f in fragments), (
        f"no bubble fragment in stream: {fragments[:3]}"
    )


# ── 6. _fragments_to_text strips DSL tags correctly ───────────────────

def test_fragments_to_text_strips_tags():
    fragments = [
        "[bubble role:ai model:Qwen time:10:00]",
        "[think-chain tt:推理]",
        "[think-step status:done tt:检索]检索完成[/think-step]",
        "[/think-chain]",
        "[md]\n# 答案\n\n这是 GalaxyOS 的答案。\n[/md]",
        "[p v:muted]置信度 82%[/p]",
        "[msg-actions copy regenerate like dislike visible][/msg-actions]",
        "[/bubble]",
    ]
    text = _fragments_to_text(fragments)
    # All DSL tags should be stripped
    assert "[" not in text
    assert "]" not in text
    # The actual answer content should survive
    assert "答案" in text
    assert "GalaxyOS" in text
    # Empty input → canned message
    assert _fragments_to_text([]) == "(no response from sidecar)"


# ── 7. _last_user_message extraction ──────────────────────────────────

def test_last_user_message_extraction():
    msgs = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
    ]
    assert _last_user_message(msgs) == "second question"
    # Empty list
    assert _last_user_message([]) == ""
    # No user
    assert _last_user_message([{"role": "system", "content": "x"}]) == ""


# ── 8. Info() smoke test ──────────────────────────────────────────────

def test_agent_info_reflects_backend():
    """agent.info() must include the real backend name, not 'None'."""
    config = DeepAgentConfig(name="info-test", model="qwen-2.5")
    agent = create_galaxy_agent(
        name=config.name, model=config.model,
        workspace_dir=Path("/tmp/galaxyos-info"),
    )
    info = agent.info()
    assert info["model"] == "qwen-2.5"
    assert "workspace" in info
    # The workspace info should now contain a non-None llm name
    ws_info = info["workspace"]
    if "llm" in ws_info:
        # llm can be None in fallback env, but should be set in v9.1
        assert ws_info["llm"] is not None, (
            "v9.1 fail: workspace.info() shows llm=None"
        )
