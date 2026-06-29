"""tests/test_tokui_dsl.py — Unit tests for TokUI DSL builders.

Covers:
- open_bubble_ai / close_bubble pairs
- open_think_chain / think_step / close_think_chain
- open_plan / plan_step / close_plan
- answer_paragraph escapes
- tool_call + msg_actions
- _esc() handles brackets + newlines
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tokui_dsl as t


class TestBubbleBuilders:
    def test_open_close_bubble_ai(self):
        s = t.open_bubble_ai(model="Qwen-2.5")
        assert s.startswith("[bubble") and "model:Qwen-2.5" in s
        assert t.close_bubble() == "[/bubble]"

    def test_open_bubble_no_model(self):
        s = t.open_bubble_ai()
        assert s.startswith("[bubble")


class TestThinkChain:
    def test_think_step(self):
        s = t.think_step(title="Search", status="done", dur="42ms",
                         body="Found 3 results")
        assert s.startswith("[think-step")
        assert "tt:Search" in s
        assert "status:done" in s
        assert "Found 3 results" in s
        assert s.endswith("[/think-step]")

    def test_think_chain_pair(self):
        chain = []
        chain.append(t.open_think_chain("My chain"))
        chain.append(t.think_step(title="Step 1", status="running"))
        chain.append(t.think_step(title="Step 2", status="done"))
        chain.append(t.close_think_chain())
        joined = "".join(chain)
        assert joined.startswith("[think-chain tt:\"My chain\"]")
        assert joined.endswith("[/think-chain]")
        assert joined.count("[think-step") == 2

    def test_think_chain_no_title(self):
        # No title = no tt attribute
        s = t.open_think_chain("")
        assert s == "[think-chain]"


class TestPlanBuilders:
    def test_open_plan_default(self):
        # No title → no tt attribute
        s = t.open_plan("")
        assert s == "[plan]"

    def test_plan_step_full(self):
        s = t.plan_step(title="Read", status="done", body="...", tool="read_file")
        assert "[plan-step" in s
        assert "tt:Read" in s
        assert "status:done" in s
        assert "tool:read_file" in s
        assert "..." in s

    def test_plan_step_minimal(self):
        s = t.plan_step(title="Read")
        assert "[plan-step" in s
        assert "tt:Read" in s

    def test_open_close_plan_pair(self):
        chain = [t.open_plan("Test plan"),
                 t.plan_step(title="s1"),
                 t.close_plan()]
        joined = "".join(chain)
        assert joined.startswith("[plan tt:\"Test plan\"]")
        assert joined.endswith("[/plan]")


class TestEscapeUtility:
    def test_esc_plain_text(self):
        assert t._esc("hello world") == "hello world"

    def test_esc_open_bracket(self):
        assert t._esc("[foo]") == '"[foo]"'

    def test_esc_close_bracket(self):
        assert t._esc("a]b") == '"a]b"'

    def test_esc_none(self):
        assert t._esc(None) == ""

    def test_esc_empty(self):
        assert t._esc("") == ""


class TestToolCallBuilder:
    def test_tool_call_full(self):
        s = t.tool_call(name="shell_run", status="done", duration="1.5s",
                        summary="exit 0")
        assert "[tool-call" in s
        assert "name:shell_run" in s
        assert "exit 0" in s
        assert s.endswith("[/tool-call]")

    def test_tool_call_minimal(self):
        s = t.tool_call(name="ls")
        assert "[tool-call" in s
        assert s.endswith("[/tool-call]")


class TestMsgActions:
    def test_msg_actions_default(self):
        s = t.msg_actions()
        # All 4 verbs present
        for verb in ("copy", "regenerate", "like", "dislike"):
            assert verb in s
        assert s.startswith("[msg-actions") and s.endswith("[/msg-actions]")


class TestAnswerParagraph:
    def test_plain_text(self):
        s = t.answer_paragraph("Hello world")
        assert s.startswith("[md]") and s.endswith("[/md]")
        assert "Hello world" in s

    def test_multiline(self):
        s = t.answer_paragraph("line 1\nline 2")
        assert "line 1" in s
        assert "line 2" in s
