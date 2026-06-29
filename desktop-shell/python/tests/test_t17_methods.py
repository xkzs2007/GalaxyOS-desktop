"""tests/test_t17_methods.py — Unit tests for stage 17 methods.

Covers:
- claw_verify: recall-based confidence scoring
- claw_recall: thin wrapper around xiaoyi_claw_api.recall
- claw_save_memory: thin wrapper around xiaoyi_claw_api.remember
- emit_event: lifecycle event storage
- _acrouter_route: lifecycle hook emissions
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeResult:
    """Mock XiaoYiClawLLM result for recall/remember."""
    pass


class FakeXiaoYi:
    def __init__(self, recall_results=None):
        self._recall_results = recall_results or []
        self._last_remember = None

    def recall(self, query, query_vector=None, top_k=10, source_filter=None,
               enhance_with_kg=True, session_id=""):
        return list(self._recall_results)[:top_k]

    def remember(self, content, metadata=None, source="user", session_id=""):
        self._last_remember = (content, metadata, source, session_id)
        return "mem-" + str(hash(content))[:8]


def make_handler(xiaoyi=None):
    """Create a SidecarHandlers with a fake xiaoyi_claw_api."""
    import galaxyos_sidecar
    h = galaxyos_sidecar.SidecarHandlers.__new__(galaxyos_sidecar.SidecarHandlers)
    h._llm = xiaoyi or FakeXiaoYi()
    return h


class TestClawVerify:
    def test_verify_no_hits_unverified(self):
        h = make_handler(FakeXiaoYi([]))
        r = h.claw_verify({"claim": "anything"})
        assert r["verdict"] == "unverified"
        assert r["confidence"] == 0.1
        assert r["evidence_count"] == 0

    def test_verify_one_hit_partial(self):
        h = make_handler(FakeXiaoYi([{"content": "x"}]))
        r = h.claw_verify({"claim": "test"})
        assert r["verdict"] == "partial"
        assert r["confidence"] == 0.5
        assert r["evidence_count"] == 1

    def test_verify_three_plus_verified(self):
        h = make_handler(FakeXiaoYi([{"content": "a"}, {"content": "b"}, {"content": "c"}, {"content": "d"}]))
        r = h.claw_verify({"claim": "test"})
        assert r["verdict"] == "verified"
        assert r["confidence"] >= 0.8
        assert r["evidence_count"] == 4

    def test_verify_truncates_long_claims(self):
        long_claim = "x" * 500
        h = make_handler()
        r = h.claw_verify({"claim": long_claim})
        assert len(r["claim"]) <= 200

    def test_verify_missing_claim(self):
        h = make_handler()
        r = h.claw_verify({})
        assert "error" in r

    def test_verify_top_evidence_strings(self):
        hits = [
            {"content": "first piece of evidence"},
            {"content": "second piece of evidence"},
            {"content": "third piece of evidence"},
        ]
        h = make_handler(FakeXiaoYi(hits))
        r = h.claw_verify({"claim": "test"})
        assert len(r["top_evidence"]) == 3
        for ev in r["top_evidence"]:
            assert isinstance(ev, str)


class TestClawRecall:
    def test_recall_basic(self):
        results = [{"content": f"hit {i}"} for i in range(3)]
        h = make_handler(FakeXiaoYi(results))
        r = h.claw_recall({"query": "test"})
        assert r["count"] == 3
        assert len(r["results"]) == 3
        assert r["query"] == "test"

    def test_recall_top_k_limit(self):
        results = [{"content": f"hit {i}"} for i in range(10)]
        h = make_handler(FakeXiaoYi(results))
        r = h.claw_recall({"query": "test", "top_k": 3})
        assert len(r["results"]) == 3

    def test_recall_missing_query(self):
        h = make_handler()
        r = h.claw_recall({})
        assert "error" in r

    def test_recall_empty_results(self):
        h = make_handler(FakeXiaoYi([]))
        r = h.claw_recall({"query": "nothing"})
        assert r["count"] == 0
        assert r["results"] == []


class TestClawSaveMemory:
    def test_save_basic(self):
        xiaoyi = FakeXiaoYi()
        h = make_handler(xiaoyi)
        r = h.claw_save_memory({"content": "important fact"})
        assert r["ok"] is True
        assert r["memory_id"].startswith("mem-")
        # Verify the remember() was called with the right args
        assert xiaoyi._last_remember is not None
        content, metadata, source, session_id = xiaoyi._last_remember
        assert content == "important fact"
        assert source == "user-selected"

    def test_save_with_metadata(self):
        xiaoyi = FakeXiaoYi()
        h = make_handler(xiaoyi)
        r = h.claw_save_memory({
            "content": "x",
            "metadata": {"source": "test"},
        })
        assert r["ok"] is True
        # xiaoyi.remember() is called; check that the source got overridden
        # (because we set source='user-selected' default in our impl)
        _, metadata, source, _ = xiaoyi._last_remember
        assert source == "user-selected"

    def test_save_empty_content(self):
        h = make_handler()
        r = h.claw_save_memory({"content": ""})
        assert "error" in r


class TestEmitEvent:
    def test_emit_basic(self):
        h = make_handler()
        r = h.emit_event({"type": "before_tool_call", "payload": {"tool": "ls"}})
        assert r["ok"] is True
        assert r["received"] == "before_tool_call"
        assert "ts" in r and r["ts"] > 0

    def test_emit_no_payload(self):
        h = make_handler()
        r = h.emit_event({"type": "agent_end"})
        assert r["ok"] is True
        assert r["received"] == "agent_end"


class TestACRouterRouteWithHooks:
    """T17.5: _acrouter_route should emit lifecycle hook events."""

    def test_route_emits_three_hooks(self):
        import galaxyos_sidecar
        h = galaxyos_sidecar.SidecarHandlers.__new__(galaxyos_sidecar.SidecarHandlers)
        # Fake ACRouter with a sync route() returning a known result
        from ac_router import CAFResult
        caf_result = CAFResult(
            query="test", chosen_action="fast_path",
            answer="hi", confidence=0.5, cost=0.001,
            verifier_signals=None, k_neighbors=[],
            trace={},
        )
        class FakeACRouter:
            def route(self, *a, **k):
                async def _r():
                    return caf_result
                return _r()
        h._acrouter = FakeACRouter()
        h._memo_consult = lambda *a, **k: None
        h._build_routing_footer = lambda *a, **k: ""
        # Capture hook events
        events = []
        original_emit = h.emit_event
        def _capture(p):
            events.append(p.get("type"))
            return original_emit(p)
        h.emit_event = _capture
        # Mock tokui_dsl methods
        import tokui_dsl
        original_open = tokui_dsl.open_bubble_ai
        original_answer = tokui_dsl.answer_paragraph
        original_msg = tokui_dsl.msg_actions
        original_close = tokui_dsl.close_bubble
        tokui_dsl.open_bubble_ai = lambda **k: "[bubble]"
        tokui_dsl.answer_paragraph = lambda t: f"[md]{t}[/md]"
        tokui_dsl.msg_actions = lambda: "[msg-actions][/msg-actions]"
        tokui_dsl.close_bubble = lambda: "[/bubble]"
        try:
            out = h._acrouter_route("test query", "")
        finally:
            tokui_dsl.open_bubble_ai = original_open
            tokui_dsl.answer_paragraph = original_answer
            tokui_dsl.msg_actions = original_msg
            tokui_dsl.close_bubble = original_close
        # Should have emitted: before_prompt_build, before_agent_reply, agent_end
        assert "before_prompt_build" in events
        assert "before_agent_reply" in events
        assert "agent_end" in events
