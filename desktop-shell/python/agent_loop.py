"""agent_loop.py — Heuristic Agent loop with real tool execution.

This is a **simplified, deterministic agent** for Stage 2 (no LLM
required). It pattern-matches the user's question to pick a tool
chain, executes them, and synthesizes a final answer. Stage 3 will
swap the heuristic for a real LLM-driven C-A-F loop.

Decision tree (v1)
------------------

    question contains "list" / "ls" / "列出" / "目录"
        → call list_dir, then synthesize

    question contains "read" / "cat" / "看" / "查看" / "内容"
        → extract path from question, call read_file, then synthesize

    question contains "write" / "echo" / "保存" / "写" / "创建"
        → extract path + content, call write_file, then synthesize

    question contains "grep" / "搜索" / "find" / "找"
        → call grep, then synthesize

    question contains "shell" / "run" / "执行" / "运行" / "!"  (or starts with $)
        → call shell_run, then synthesize

    else
        → single ask() of the engine, no tool calls

Each tool call is streamed to the renderer as it happens:
    [think-step status:running tt:选择工具]
    [tool-call name:shell_run status:running]
    ... (the call happens) ...
    [tool-call name:shell_run status:done duration:1.2s]  (via [upd id:tc1])
    [terminal title:bash status:success]<output>[/terminal]
    [think-step status:done tt:已执行]
    [md]Final synthesized answer[/md]
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import tokui_dsl
import tools


# ── Decision: which tool? ────────────────────────────────────────

def _extract_path(text: str) -> Optional[str]:
    """Heuristically pull a relative file path out of the question.

    Matches things like:
      - "foo.txt" / "src/main.py" / "docs/readme.md"
      - "看 hello.txt" → hello.txt
      - "in config/llm_config.json" → config/llm_config.json
    """
    m = re.search(r'([\w./-]+\.[A-Za-z0-9]{1,5})', text)
    if m:
        return m.group(1)
    # Try a path-like fragment (no extension)
    m = re.search(r'(?:in|at|路径|文件)\s+([\w./-]{2,})', text)
    if m:
        return m.group(1)
    return None


_EXT_LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".rs": "rust", ".go": "go", ".rb": "ruby", ".php": "php",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".html": "html", ".css": "css", ".scss": "scss",
    ".json": "json", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".md": "markdown", ".sql": "sql", ".vue": "vue",
    ".kt": "kotlin", ".swift": "swift", ".dart": "dart",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini",
}


def _infer_lang(file_path: str) -> str:
    """Infer a syntax-highlighting language from the file extension."""
    import os
    ext = os.path.splitext(file_path)[1].lower()
    return _EXT_LANG_MAP.get(ext, "text")


def _extract_cmd(text: str) -> Optional[str]:
    """For shell_run, extract the command from a prefix.

    Examples:
        "!ls -la"               → "ls -la"
        "run: cat /etc/hosts"  → "cat /etc/hosts"
        "shell: pwd"           → "pwd"
    """
    m = re.search(r'^[!$]\s*(.+)$', text.strip())
    if m:
        return m.group(1).strip()
    m = re.search(r'^(?:shell|run|执行|运行)\s*[:：]\s*(.+)$', text.strip(), re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def decide_tool_chain(question: str, llm_client=None) -> List[Dict[str, Any]]:
    """Decide a tool-call plan from a natural-language question.

    v9.5: if llm_client is provided, uses LLM for intelligent tool
    selection. Falls back to heuristic regex matching if LLM is
    unavailable or fails.
    """
    if llm_client:
        try:
            from openai import OpenAI
            plan = _llm_decide(llm_client, question)
            if plan:
                return plan
        except Exception as e:
            import logging
            logging.getLogger("agent_loop").warning(
                "LLM tool selection failed: %s; fallback to heuristic", e)

    # ── Heuristic fallback ──────────────────────────────────
    q = question.strip()
    lower = q.lower()
    cmd = _extract_cmd(q)
    if cmd:
        return [{"name": "shell_run", "params": {"cmd": cmd},
                 "rationale": f"用户请求执行 shell: {cmd!r}"}]
    if any(k in lower for k in ("list ", "ls ", "列出", "目录", "files in", "what's in")):
        path = _extract_path(q) or "."
        return [{"name": "list_dir", "params": {"path": path},
                 "rationale": f"用户请求列出目录: {path!r}"}]
    if any(k in lower for k in ("read ", "cat ", "看", "查看", "content of", "show file")):
        path = _extract_path(q)
        if path:
            return [{"name": "read_file", "params": {"path": path},
                     "rationale": f"用户请求读取文件: {path!r}"}]
    if any(k in lower for k in ("write ", "echo ", "保存", "创建文件", "写")):
        m = re.search(r'([\w./-]+\.[A-Za-z0-9]{1,5})\s*[=:>]\s*(.+)$', q, re.DOTALL)
        if m:
            return [{"name": "write_file",
                     "params": {"path": m.group(1).strip(), "content": m.group(2).strip()},
                     "rationale": f"用户请求写文件: {m.group(1)!r}"}]
    if any(k in lower for k in ("grep ", "搜索", "find ", "找 ")):
        m = re.search(r'(?:grep|搜索|find|找)\s+["\']?([\w.*+?{}\[\]\\^$|-]+)["\']?', q)
        pattern = m.group(1) if m else q.split(maxsplit=1)[-1]
        path = _extract_path(q) or "."
        return [{"name": "grep", "params": {"pattern": pattern, "path": path},
                 "rationale": f"用户搜索: pattern={pattern!r} path={path!r}"}]
    if any(k in lower for k in ("diff ", "patch ", "modify ", "replace ", "改 ", "替换")):
        path = _extract_path(q)
        if path:
            arrow_m = re.search(r'["\']?(.+?)["\']?\s*(?:→|->|to|改为|换成)\s*["\']?(.+?)["\']?\s*$', q, re.DOTALL)
            if arrow_m:
                return [{"name": "apply_diff",
                         "params": {"path": path, "old": arrow_m.group(1).strip(),
                                    "new": arrow_m.group(2).strip()},
                         "rationale": f"用户请求修改文件 {path!r}"}]
    return []


def _llm_decide(llm_client, question: str) -> List[Dict[str, Any]]:
    """Use LLM to analyze the user request and return a tool-call plan.

    Returns parsed JSON like:
    [{"name": "shell_run", "params": {"cmd": "ls -la"},
      "rationale": "list files"}]
    """
    system = (
        "You are a tool-selection agent. Analyze the user request and return "
        "a JSON array of tool calls to execute. Available tools:\n"
        "- shell_run: execute shell command (params: cmd)\n"
        "- read_file: read a file (params: path)\n"
        "- write_file: write/create a file (params: path, content)\n"
        "- list_dir: list directory (params: path)\n"
        "- grep: search file contents (params: pattern, path)\n"
        "- apply_diff: modify a file with diff (params: path, old, new)\n\n"
        "Rules:\n"
        "- Return ONLY valid JSON array, no other text\n"
        "- If no tool is needed, return empty array []\n"
        "- For write_file, include both path and content\n"
        "- For shell_run, use the exact command the user requested\n"
        "- Extract file paths and patterns from the user's words"
    )
    try:
        rsp = llm_client.chat.completions.create(
            model=getattr(llm_client, '_model_override', None) or 'deepseek-v4-flash',
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": question},
            ],
            max_tokens=400,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = rsp.choices[0].message.content.strip()
        import json
        # The LLM might wrap the array in {"tools": [...]} or return [...] directly
        data = json.loads(raw)
        if isinstance(data, dict):
            arr = data.get("tools") or data.get("plan") or data.get("actions") or []
        elif isinstance(data, list):
            arr = data
        else:
            return []
        # Validate structure
        valid = []
        for item in arr:
            if isinstance(item, dict) and "name" in item:
                valid.append({
                    "name": str(item["name"]),
                    "params": item.get("params", {}),
                    "rationale": str(item.get("rationale", "")),
                })
        return valid
    except Exception:
        return []


# ── Agent loop ─────────────────────────────────────────────────

class AgentLoop:
    """Yields TokUI DSL fragments as the agent executes.

    v9.4: supports stream_id for zmq PUB real-time progress events.
    """

    def __init__(self, question: str, stream_id: str = "",
                 llm_client=None):
        self.question = question
        self.stream_id = stream_id
        self.llm_client = llm_client
        self.plan = decide_tool_chain(question, llm_client=llm_client)

    def _pub(self, event_type: str, detail: str, status: str = "running",
             tool_name: str = "", step_index: int = 0, dur_ms: int = 0):
        """Publish an agent progress event via zmq PUB if stream_id is set."""
        if not self.stream_id:
            return
        try:
            from galaxyos_sidecar import _publish_event
            _publish_event("agent", {
                "stream_id": self.stream_id,
                "type": event_type,
                "status": status,
                "detail": detail,
                "tool_name": tool_name,
                "step_index": step_index,
                "dur_ms": dur_ms,
            })
        except Exception:
            pass  # PUB is best-effort

    def _fmt(self, *fragments: str) -> List[str]:
        return list(fragments)

    async def run(self) -> List[str]:
        """Execute the plan and return the full TokUI DSL fragment list."""
        out: List[str] = []

        # Open the assistant bubble + think-chain header
        out.append(tokui_dsl.open_bubble_ai(model="GalaxyOS-Agent"))
        out.append(tokui_dsl.open_think_chain("Agent 思考过程"))
        self._pub("plan_start", f"决策: {len(self.plan)} 步工具链",
                  status="running", step_index=0)

        if not self.plan:
            # No tools to run — fall back to a direct answer
            out.append(tokui_dsl.think_step(
                title="无工具调用",
                status="done",
                dur="0ms",
                body="问题不需要工具，直接给出答案。",
            ))
            out.append(tokui_dsl.close_think_chain())
            out.append(tokui_dsl.answer_paragraph(
                f"收到问题：{self.question!r}\n\n"
                "（提示：在问题前加 `!` 触发 shell_run，加 `grep 关键字` 触发搜索，"
                "加 `read 文件路径` 触发读文件，加 `list 目录` 触发列目录。）"
            ))
            out.append(tokui_dsl.msg_actions())
            out.append(tokui_dsl.close_bubble())
            return out

        # We have a plan. Run each tool, streaming the result.
        for idx, step in enumerate(self.plan, start=1):
            name = step["name"]
            params = step["params"]
            rationale = step["rationale"]

            # 1) Decide which tool
            out.append(tokui_dsl.think_step(
                title=f"第 {idx} 步：调用 {name}",
                status="running",
                body=rationale,
            ))

            # 2) Tool-call start (running)
            tc_id = f"tc{idx}"
            out.append(
                f'[tool-call id:{tc_id} name:{name} status:running]'
                f'[p]调用 {name}...[/p]'
                f'[/tool-call]'
            )

            # 3) Execute
            self._pub("tool_start", f"执行 {name}", status="running",
                      tool_name=name, step_index=idx)
            t0 = _now_ms()
            result = await tools.call_tool(name, params)
            dur_ms = _now_ms() - t0

            # 4) Update tool-call to done via [upd]
            summary = _summarize(result)
            self._pub("tool_done", summary[:100], status="done",
                      tool_name=name, step_index=idx, dur_ms=dur_ms)
            out.append(
                f'[upd id:{tc_id} status:done duration:{dur_ms}ms]'
            )
            # Re-emit the tool-call (TokUI's [upd] only updates specified
            # attributes; the body needs a separate feed). Use a small
            # tool-call fragment for the summary so the user sees the
            # result inline.
            out.append(
                f'[tool-call name:{name} status:done duration:{_fmt_dur(dur_ms)}]'
                f'[p]{tokui_dsl._esc(summary)}[/p]'
                f'[/tool-call]'
            )

            # 5) For shell_run, also emit a [terminal] block with raw output
            if name == "shell_run" and result.get("output"):
                out.append(
                    f'[terminal title:bash status:{"success" if result.get("ok") else "error"}]'
                    f'{tokui_dsl._esc(result["output"])}'
                    f'[/terminal]'
                )
            elif name == "read_file" and result.get("ok"):
                # Show file content as a [sandbox] with a header
                snippet = result.get("content", "")
                if len(snippet) > 1500:
                    snippet = snippet[:1500] + f"\n... [{result.get('size_bytes', 0) - 1500} more bytes]"
                # Infer language from file extension for syntax highlighting
                file_path = params.get("path", "")
                lang = _infer_lang(file_path)
                out.append(
                    f'[sandbox title:{file_path} lang:{lang}]'
                    f'{tokui_dsl._esc(snippet)}'
                    f'[/sandbox]'
                )
            elif name == "write_file" and result.get("ok"):
                out.append(
                    f'[p v:muted]→ 写入 {result.get("path")} ({result.get("wrote_bytes")} bytes)[/p]'
                )
            elif name == "apply_diff" and result.get("ok"):
                diff_text = result.get("diff", "")
                out.append(
                    f'[sandbox title:diff {params.get("path", "")} lang:diff]'
                    f'{tokui_dsl._esc(diff_text[:3000])}'
                    f'[/sandbox]'
                )
                out.append(
                    f'[p v:muted]→ 修改 {params.get("path", "")} ({result.get("before_size", "?")}→{result.get("after_size", "?")} bytes)[/p]'
                )
            elif name == "grep" and result.get("ok") and result.get("matches"):
                matches = result["matches"]
                md = "\n".join(
                    f"- `{m['file']}:{m['line']}` {m['text']}"
                    for m in matches[:15]
                )
                if result.get("truncated"):
                    md += f"\n- ... [truncated, showing {len(matches)}+ matches]"
                out.append(
                    f'[md]\n**{result.get("total", 0)} 个匹配:**\n\n{md}\n[/md]'
                )
            elif name == "list_dir" and result.get("ok") and result.get("entries"):
                entries = result["entries"]
                md = "\n".join(
                    f"- {'📁' if e['type'] == 'dir' else '📄'} `{e['name']}` ({e['size_bytes']} bytes)"
                    for e in entries[:30]
                )
                if len(entries) > 30:
                    md += f"\n- ... [truncated, showing 30 of {len(entries)}]"
                out.append(
                    f'[md]\n**目录 {result.get("path", ".")} ({len(entries)} 个条目):**\n\n{md}\n[/md]'
                )

            # 6) If the tool errored, surface it
            if not result.get("ok"):
                if result.get("needs_approval"):
                    out.append(
                        f'[p v:warn]⚠️ 需要确认: {tokui_dsl._esc(result.get("command", name))}[/p]'
                    )
                    out.append(
                        f'[confirm id:approve_{idx} tool:{name} cmd:{tokui_dsl._esc(result.get("command",""))}]'
                        f'[p]点击确认执行此操作[/p]'
                        f'[/confirm]'
                    )
                else:
                    out.append(
                        f'[p v:danger]❌ {tokui_dsl._esc(result.get("error", "未知错误"))}[/p]'
                    )
                out.append(
                    f'[upd id:{tc_id} status:error]'
                )

            # 7) Mark the think-step as done
            out.append(
                f'[think-step status:done title:第 {idx} 步：{name} dur:{_fmt_dur(dur_ms)}]'
            )

        # Final synthesis — a one-line summary
        out.append(tokui_dsl.close_think_chain())
        out.append(self._synthesize())

        out.append(tokui_dsl.msg_actions())
        out.append(tokui_dsl.close_bubble())
        return out

    def _synthesize(self) -> str:
        """One-line summary as a markdown paragraph (ZCode/Codex style)."""
        n = len(self.plan)
        verbs = {
            "shell_run": "执行了 shell 命令",
            "read_file": "读取了文件",
            "write_file": "写入了文件",
            "list_dir": "列出了目录",
            "grep": "搜索了内容",
            "apply_diff": "应用了补丁",
        }
        actions = [verbs.get(s["name"], s["name"]) for s in self.plan]
        if n == 1:
            return f'[md]\n完成：{actions[0]} (`{self.plan[0]["params"]}`)。\n[/md]'
        return f'[md]\n完成 {n} 步：{" → ".join(actions)}。\n[/md]'


# ── Helpers ────────────────────────────────────────────────────────

def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _fmt_dur(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.2f}s"


def _summarize(result: Dict[str, Any]) -> str:
    """Short text summary of a tool result for the tool-call card body."""
    if not result.get("ok"):
        return f"❌ {result.get('error', '失败')}"
    name = result.get("tool", "")
    if "output" in result:
        out = result["output"].strip()
        return f"exit {result.get('exit_code', 0)} · {len(out)} chars"
    if "content" in result:
        return f"已读取 · {result.get('size_bytes', '?')} bytes" + (" (truncated)" if result.get("truncated") else "")
    if "wrote_bytes" in result:
        return f"已写入 {result['path']} · {result['wrote_bytes']} bytes"
    if "entries" in result:
        return f"列出 {len(result['entries'])} 个条目"
    if "matches" in result:
        return f"找到 {result.get('total', 0)} 个匹配"
    return "完成"


# ── Self-test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def _test():
        for q in [
            "!ls -la",
            "list the current directory",
            "read hello.txt",
            "grep Galaxy",
            "what is R-CCAM",  # no tool
        ]:
            print(f"=== {q!r} ===")
            plan = decide_tool_chain(q)
            for s in plan:
                print(f"  → {s['name']}({s['params']})")
            if not plan:
                print("  (no tools)")

            # Run the full agent loop and print first 800 chars of DSL
            loop = AgentLoop(q)
            frags = await loop.run()
            dsl = "".join(frags)
            print(f"  → {len(frags)} fragments, {len(dsl)} chars")
            # print first 400 chars
            print("  " + dsl[:400].replace("\n", "\\n"))
            print()

    asyncio.run(_test())
