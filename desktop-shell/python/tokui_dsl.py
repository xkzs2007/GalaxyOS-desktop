"""tokui_dsl.py — Build TokUI DSL strings from GalaxyOS process() results.

The GalaxyOS engine returns a dict like::

    {
      "answer": "...",                      # final answer text
      "confidence": 0.82,                   # 0..1
      "routing_debug": "...",               # human-readable debug string
      "strategy": "rccam_v2",               # which strategy was used
      "knowledge_type": "factual",          # one of factual | procedural | ...
      "intent": "recall",                   # the resolved intent
      "cycle_count": 1,                     # R-CCAM cycles used
      "thinking_skills_used": ["..."],      # which skills fired
      "retrieval_confidence": 0.71,         # the retrieval stage score
      "memory_ids": ["..."],                # ids of memories written
      "stop_reason": "completed",            # why the loop ended
      "rccam_phase_states": {                # per-phase metadata
         "retrieval": { "duration_ms": 120, "sources": 8 },
         "cognition": { "duration_ms": 350, "skills": 3 },
         "control":   { "duration_ms": 12  },
         "action":    { "duration_ms": 800, "tokens": 220 },
         "memory":    { "duration_ms": 25,  "wrote": 1 },
      },
    }

This module turns that into TokUI's bracket-DSL so the client renderer
can stream it incrementally. The DSL is built in *fragments* so a
streaming consumer (SSE) can flush after each one.

Mapping (v1 — minimal, only what we have today; Stage 2/3 will extend
this with MeMo 3-stage think-step upd and ACRouter C-A-F plan-step
upd):

    process() return shape           TokUI DSL
    ─────────────────────────────────────────────────────────────────
    (whole response)                 [bubble role:ai model:Qwen-2.5
                                          time:<iso>]
                                       [think-chain tt:推理过程]
                                         [think-step tt:检索
                                          status:done dur:120ms]
                                         ...
                                       [/think-chain]
                                       [p 答案正文]
                                       [p v:muted
                                          confidence: 0.82]
                                       [tool-call name:recall
                                        status:done duration:0.5s]
                                       [p <reasoning>]
                                       [/tool-call]
                                       [msg-actions copy regenerate
                                        like dislike visible]
                                       [/bubble]

DSL escapes: any literal "[" / "]" inside content must be quoted
(TokUI rule). This module takes care of that automatically.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Dict, List, Optional, Sequence


# ── Low-level DSL helpers ─────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape content for TokUI: square brackets and full-width colon hint.

    TokUI rule: any literal ``[`` / ``]`` must be wrapped in double
    quotes. We also avoid ASCII ``:`` after a single CJK character
    (parser ambiguity) by inserting a zero-width joiner — but in
    practice our content rarely hits that case, so we just quote
    when needed.
    """
    if text is None:
        return ""
    s = str(text)
    if "[" in s or "]" in s:
        # Wrap whole content in double quotes; TokUI treats quoted
        # content as literal.
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _attr(key: str, value: Any) -> str:
    """Render one ``key:value`` attribute, quoting if value has spaces."""
    if value is None or value is False or value == "":
        return ""
    if value is True:
        return f" {key}"
    v = str(value)
    if " " in v or "," in v:
        return f' {key}:"{v}"'
    return f" {key}:{v}"


def _bool_attr(key: str) -> str:
    return f" {key}"


def _attrs(d: Dict[str, Any]) -> str:
    return "".join(_attr(k, v) for k, v in d.items() if v is not None and v is not False)


# ── Fragment builders (one DSL chunk each) ────────────────────────────

def open_bubble_ai(model: str = "Qwen-2.5", time_iso: Optional[str] = None) -> str:
    """First fragment: open the assistant bubble.

    The renderer begins streaming the moment this arrives.
    """
    if time_iso is None:
        from datetime import datetime
        time_iso = datetime.now().strftime("%H:%M")
    return f"[bubble{_attr('role', 'ai')}{_attr('model', model)}{_attr('time', time_iso)}]"


def open_think_chain(title: str = "推理过程") -> str:
    return f"[think-chain{_attr('tt', title)}]"


def think_step(title: str, status: str = "done", dur: Optional[str] = None,
               body: str = "") -> str:
    inner = f"[think-step{_attr('status', status)}{_attr('tt', title)}"
    if dur:
        inner += _attr("dur", dur)
    inner += "]"
    if body:
        inner += f"[p {_esc(body)}]"
    return inner + f"[/think-step]"


def close_think_chain() -> str:
    return "[/think-chain]"


# ── Plan builders (ZCode/Codex plan mode) ──────────────────────────

def open_plan(title: str = "执行计划") -> str:
    """Open a [plan] container."""
    return f"[plan{_attr('tt', title)}]"


def plan_step(title: str, status: str = "pending",
              body: str = "", tool: str = "", step_id: Optional[str] = "") -> str:
    """One step in a plan. status: pending / running / done / skipped.

    v9.3: accepts ``step_id`` for `[upd id:plan_step_N status:done]`
    binding. Renderer can flip status without re-emitting the whole step.
    """
    inner = f"[plan-step{_attr('status', status)}{_attr('tt', title)}"
    if step_id:
        inner += _attr("id", step_id)
    if tool:
        inner += _attr("tool", tool)
    inner += "]"
    if body:
        inner += f"[p {_esc(body)}]"
    return inner + f"[/plan-step]"


def close_plan() -> str:
    return "[/plan]"


def answer_paragraph(text: str) -> str:
    """Render the final answer as a Markdown block (TokUI ``[md]`` parses
    bold/italic/list, which is what we want for richer answers)."""
    return f"[md]\n{text}\n[/md]"


def confidence_footer(confidence: float) -> str:
    """Small status row below the answer."""
    pct = f"{max(0.0, min(1.0, float(confidence))) * 100:.0f}%"
    return f'[p v:muted]置信度 {pct}[/p]'


def tool_call(name: str, status: str = "done", duration: Optional[str] = None,
              summary: str = "") -> str:
    """One tool invocation — mapped from a ``thinking_skills_used`` entry
    or a ``rccam_phase_states`` row."""
    head = f"[tool-call{_attr('name', name)}{_attr('status', status)}"
    if duration:
        head += _attr("duration", duration)
    head += "]"
    if summary:
        head += f"[p {_esc(summary)}]"
    return head + "[/tool-call]"


def msg_actions() -> str:
    return "[msg-actions copy regenerate like dislike visible][/msg-actions]"


def close_bubble() -> str:
    return "[/bubble]"


def error_bubble(message: str) -> str:
    """Render an error as a single self-contained bubble."""
    return (
        f'[bubble role:ai model:GalaxyOS time:错误]'
        f'[p v:danger]{_esc(message)}[/p]'
        f'[/bubble]'
    )


# ── v9.3 component builders (15+ new fragments) ───────────────────────
#
# Each builder emits one TokUI DSL fragment. Builders are pure functions
# — they don't talk to the network or filesystem. Keep them small and
# composable so the streaming code in _do_stream_* can call them in
# any order.

# ── 1. Progress bar + upd (live status) ───────────────────────────────

def progress_bar(progress_id: str, value: float = 0,
                 label: str = "", status: str = "primary") -> str:
    """Render a progress bar bound to a target id (for [upd])."""
    pct = max(0, min(100, int(value)))
    attrs = f" id:{progress_id} v:{pct}"
    if label:
        attrs += f" l:\"{_esc(label)}\""
    return f"[progress{attrs}]"


def upd(progress_id: str, value: float, status: str = "") -> str:
    """Emit an [upd] fragment that updates a target element by id.

    Status can be: primary / success / warning / danger / "" (no change).
    """
    pct = max(0, min(100, int(value)))
    attrs = f" id:{progress_id} v:{pct}"
    if status:
        attrs += f" status:{status}"
    return f"[upd{attrs}]"


# ── 2. Callout (highlight box) ────────────────────────────────────────

def callout(kind: str, title: str, body: str) -> str:
    """Render a [callout] — info / success / warning / danger box."""
    if kind not in ("info", "success", "warning", "danger"):
        kind = "info"
    inner = f"[callout t:{kind}{_attr('tt', title)}]{_esc(body)}[/callout]"
    return inner


# ── 3. Stat (big number with trend) ───────────────────────────────────

def stat(value: str, title: str = "", suffix: str = "",
         trend: Optional[str] = None) -> str:
    """Render a [stat] — title, value, optional suffix/trend arrow."""
    attrs = _attr("tt", title) + _attr("v", value)
    if suffix:
        attrs += _attr("suf", suffix)
    if trend:
        # trend can be "up:12%" / "down:5%" / "flat:0%"
        attrs += _attr("trend", trend)
    return f"[stat{attrs}]"


# ── 4. Code block (with syntax hint) ──────────────────────────────────

def code_block(code: str, lang: str = "text") -> str:
    """Render a [code] block — TokUI will syntax-highlight by lang."""
    # Escape: leave [ and ] alone inside code; TokUI requires quoted
    # string for content with special chars
    safe = code.replace("[/code]", "[/code]")  # noop but explicit
    return f'[code lang:{lang}]\n{safe}\n[/code]'


# ── 5. Tag (label/chip) ───────────────────────────────────────────────

def tag(text: str, kind: str = "primary", closable: bool = False) -> str:
    """Render a [tag] — small label/chip."""
    attrs = _attr("tx", text) + f" t:{kind}"
    if closable:
        attrs += " closable"
    return f"[tag{attrs}]"


# ── 6. Source (citation) ───────────────────────────────────────────────

def source(name: str, snippet: str = "", url: str = "") -> str:
    """Render a [source] — citation chip for retrieval results."""
    attrs = _attr("n", name) + _attr("sn", snippet)
    if url:
        attrs += _attr("u", url)
    return f"[source{attrs}]"


# ── 7. Quick-reply (one-tap response options) ──────────────────────────

def quick_reply(items: List[str]) -> str:
    """Render [quick-reply] with given options as a list."""
    if not items:
        return ""
    items_str = ",".join(items)
    return f"[quick-reply items:\"{_esc(items_str)}\"]"


# ── 8. Suggestion (follow-up card) ────────────────────────────────────

def suggestion(title: str, body: str = "", on_click: str = "") -> str:
    """Render a [suggestion] card inside a [suggestions] container."""
    attrs = _attr("tt", title) + _attr("tx", body)
    if on_click:
        attrs += _attr("clk", on_click)
    return f"[suggestion{attrs}]"


def suggestions_grid(cols: int = 2, items: Optional[List[str]] = None) -> str:
    """Render a [suggestions] grid container (with default examples)."""
    if items is None:
        items = [
            "用 ACRouter 分析这个问题",
            "查 SkillGraph 找相关技能",
            "写入长期记忆",
        ]
    out = [f"[suggestions cols:{cols}]"]
    for it in items:
        out.append(suggestion(title=it, on_click="picks"))
    out.append("[/suggestions]")
    return "".join(out)


# ── 9. Latency / Status indicators ────────────────────────────────────

def latency(ms: int, kind: str = "primary") -> str:
    """Render a [latency] chip — shows response time."""
    return f"[latency v:{ms} t:{kind}]"


# ── 10. Diff (side-by-side or unified) ────────────────────────────────

def diff_block(title: str, before: str, after: str,
               lang: str = "text") -> str:
    """Render a [diff] block with old/new content."""
    out = [f'[diff{_attr("title", title)}{_attr("lang", lang)}]']
    out.append(f"[p v:muted]- {_esc(before)}[/p]")
    out.append(f"[p v:success]+ {_esc(after)}[/p]")
    out.append("[/diff]")
    return "".join(out)


# ── 11. Artifact (Codex/Codex-style rich content) ─────────────────────

def artifact(title: str, lang: str = "text",
             pos: str = "right", width: str = "60%") -> str:
    """Open an [artifact] container. Pair with close_artifact()."""
    return f'[artifact{_attr("tt", title)}{_attr("lang", lang)}{_attr("pos", pos)}{_attr("w", width)}]'


def artifact_code(code: str, lang: str = "text") -> str:
    """The code slot of an artifact."""
    return f'[artifact-code]\n{_esc(code)}\n[/artifact-code]'


def artifact_preview(html_or_md: str) -> str:
    """The preview slot of an artifact (rendered HTML or markdown)."""
    return f"[artifact-preview]\n{_esc(html_or_md)}\n[/artifact-preview]"


def close_artifact() -> str:
    return "[/artifact]"


# ── 12. Welcome (initial page) ────────────────────────────────────────

def welcome(title: str = "GalaxyOS 桌面端", subtitle: str = "",
            features: Optional[List[Dict[str, str]]] = None) -> str:
    """Render a [welcome] page with feature cards."""
    if features is None:
        features = [
            {"tt": "Ask",     "tx": "简单提问（自动路由：MeMo / process / fast_path）", "i": "💬"},
            {"tt": "Process", "tx": "R-CCAM 五阶段深度推理",                        "i": "🔍"},
            {"tt": "Agent",   "tx": "工具调用（shell / read / write / grep / diff）", "i": "🛠"},
            {"tt": "Plan",    "tx": "先出计划，确认后再执行",                       "i": "📋"},
            {"tt": "MeMo*",   "tx": "调试 MeMo 3-stage 协议",                       "i": "🧠"},
        ]
    out = [f'[welcome{_attr("tt", title)}]']
    for f in features:
        f_attrs = _attr("tt", f.get("tt", "")) + _attr("tx", f.get("tx", ""))
        if f.get("i"):
            f_attrs += _attr("i", f["i"])
        if f.get("clk"):
            f_attrs += _attr("clk", f["clk"])
        out.append(f"[feature{f_attrs}]")
    out.append("[/welcome]")
    return "".join(out)


# ── 13. Tool-call with timing + summary ───────────────────────────────

def tool_call_with_timing(name: str, started_ms: int,
                          status: str = "done",
                          summary: str = "") -> str:
    """Tool-call with auto-computed duration."""
    duration = max(0, int(time.time() * 1000) - started_ms) if started_ms else 0
    return tool_call(name=name, status=status,
                     duration=f"{duration}ms", summary=summary)


# ── 14. Tool result (echoed back into the bubble) ─────────────────────

def tool_result(name: str, ok: bool = True, preview: str = "") -> str:
    """Render the result of a tool call as a [p] block."""
    kind = "success" if ok else "danger"
    marker = "✓" if ok else "✗"
    body = f"{marker} {name}"
    if preview:
        body += f": {_esc(preview[:200])}"
    return f"[p v:{kind}]{body}[/p]"


# ── 15. Multi-line progress (loop status) ─────────────────────────────

def loop_progress(progress_id: str, current: int, total: int,
                  label: str = "") -> str:
    """Emit a progress bar fragment with current/total."""
    pct = (current / total * 100) if total > 0 else 0
    full_label = f"{label} ({current}/{total})" if label else f"{current}/{total}"
    return progress_bar(progress_id, value=pct, label=full_label)


# ── Top-level conversion ──────────────────────────────────────────────

def process_result_to_fragments(
    result: Dict[str, Any],
    *,
    model: str = "Qwen-2.5",
) -> List[str]:
    """Turn a ``process()`` return dict into an ordered list of DSL fragments.

    Each fragment is a complete, renderable TokUI string. The caller
    (SSE handler) should send one ``data: {tokui: <fragment>}`` per
    fragment, then a final ``[DONE]`` marker.
    """
    out: List[str] = []
    out.append(open_bubble_ai(model=model))

    # 1. reasoning chain
    phase = result.get("rccam_phase_states") or {}
    if phase:
        out.append(open_think_chain())
        for phase_name, label in (
            ("retrieval", "检索"),
            ("cognition", "认知"),
            ("control", "控制"),
            ("action", "执行"),
            ("memory", "记忆"),
        ):
            meta = phase.get(phase_name)
            if not meta:
                continue
            dur = meta.get("duration_ms")
            dur_s = f"{int(dur)}ms" if isinstance(dur, (int, float)) else None
            body_bits = []
            if phase_name == "retrieval":
                n = meta.get("sources")
                if n is not None:
                    body_bits.append(f"召回 {n} 条候选")
                rc = meta.get("confidence") or result.get("retrieval_confidence")
                if rc is not None:
                    body_bits.append(f"检索置信度 {float(rc):.2f}")
            elif phase_name == "cognition":
                skills = meta.get("skills")
                if skills is not None:
                    body_bits.append(f"激活 {skills} 个技能")
            elif phase_name == "action":
                tok = meta.get("tokens")
                if tok is not None:
                    body_bits.append(f"生成 {tok} tokens")
            elif phase_name == "memory":
                wrote = meta.get("wrote")
                if wrote is not None:
                    body_bits.append(f"写入 {wrote} 条记忆")
            out.append(think_step(
                title=label,
                status="done",
                dur=dur_s,
                body="，".join(body_bits) or "完成",
            ))
        out.append(close_think_chain())

    # 2. tool calls (one per thinking skill, plus any explicit tool_calls)
    skills: Sequence[str] = result.get("thinking_skills_used") or []
    if skills:
        for s in skills[:8]:  # cap at 8 to keep the bubble reasonable
            out.append(tool_call(
                name=str(s),
                status="done",
                duration="—",
                summary="已调用",
            ))

    # 3. main answer
    answer = (result.get("answer") or "").strip()
    if answer:
        out.append(answer_paragraph(answer))

    # 4. footer (confidence + meta)
    conf = result.get("confidence")
    if conf is not None:
        out.append(confidence_footer(conf))

    # 5. actions row
    out.append(msg_actions())
    out.append(close_bubble())
    return out


def stream_error(message: str) -> List[str]:
    """Single-fragment error response — usable as the only SSE payload
    when something went wrong before ``process()`` could run."""
    return [error_bubble(message)]


# ── Self-test (run as ``python tokui_dsl.py``) ────────────────────────
if __name__ == "__main__":
    sample = {
        "answer": "**GalaxyOS** 是一个 5 周三阶段改造项目。\n- 阶段一：桌面化\n- 阶段二：MeMo\n- 阶段三：ACRouter",
        "confidence": 0.82,
        "routing_debug": "R-CCAM 1 cycle, recall path",
        "strategy": "rccam_v2",
        "knowledge_type": "factual",
        "intent": "recall",
        "cycle_count": 1,
        "thinking_skills_used": ["recall", "summarize"],
        "retrieval_confidence": 0.71,
        "memory_ids": ["uuid-1", "uuid-2"],
        "stop_reason": "completed",
        "rccam_phase_states": {
            "retrieval": {"duration_ms": 120, "sources": 8, "confidence": 0.71},
            "cognition": {"duration_ms": 350, "skills": 2},
            "control": {"duration_ms": 12},
            "action": {"duration_ms": 800, "tokens": 220},
            "memory": {"duration_ms": 25, "wrote": 1},
        },
    }
    fragments = process_result_to_fragments(sample)
    for f in fragments:
        print(f)
    print("---", len(fragments), "fragments")
