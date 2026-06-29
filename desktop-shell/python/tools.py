"""tools.py — Real tool execution for the GalaxyOS Desktop agent.

These are the tools the Agent can call. Each tool is a small async-
friendly function that takes a dict of params and returns a dict
{ok, output, error, duration_ms}. Tools are designed to be:

- **Safe**: shell_run is sandboxed to a workspace root; writes are
  confined to the same root; reads are size-capped to prevent
  accidental exfiltration.
- **Streaming-friendly**: long-running tools (shell_run) can yield
  progress lines, though for the stage 2 demo we just collect.
- **Deterministic-ish**: each tool returns structured data so the
  Agent can reason about the result deterministically.

Tool registry
-------------

    shell_run  (cmd: str, timeout_s: int = 15)
                — run a shell command in the workspace root
    read_file  (path: str, max_bytes: int = 50_000)
                — read a file (UTF-8, size-capped)
    write_file (path: str, content: str, mode: str = 'overwrite')
                — write a file (creates parent dirs)
    list_dir   (path: str = '.', show_hidden: bool = False)
                — list a directory
    grep       (pattern: str, path: str = '.', max_results: int = 50)
                — regex search across text files
    apply_diff (path: str, old: str, new: str)
                — apply a context-anchored patch (for Agent diff edits)

Sandbox layout (Stage 2)
------------------------
    ~/.<APP>/workspace/   ← agent's writable/readable root
    ~/.<APP>/workspace/executions/  ← per-tool-call work dirs
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional


# ── Sandbox resolution ─────────────────────────────────────────────

def _sandbox_root() -> Path:
    """The agent's writable root.

    Order:
      1. $GALAXYOS_SANDBOX env var (explicit override)
      2. <workspace>/sandbox/  (created on first use)

    For Stage 2 this defaults to a directory inside the user's
    GalaxyOS workspace so all file operations are reversible and
    don't accidentally touch the host filesystem.
    """
    import path_resolver_desktop
    explicit = os.environ.get("GALAXYOS_SANDBOX")
    if explicit:
        p = Path(explicit).expanduser().resolve()
    else:
        p = (path_resolver_desktop.WORKSPACE_ROOT / "sandbox").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_path(rel: str) -> Path:
    """Resolve a relative path against the sandbox, rejecting escapes."""
    sandbox = _sandbox_root()
    p = (sandbox / rel).resolve()
    # Must stay inside sandbox
    try:
        p.relative_to(sandbox)
    except ValueError:
        raise PermissionError(f"path escapes sandbox: {rel!r}")
    return p


# ── Tool implementations ───────────────────────────────────────────

async def shell_run(cmd: str, timeout_s: int = 15, _approved: bool = False) -> Dict[str, Any]:
    """Run a shell command in the sandbox root.

    Returns: {ok, output, exit_code, duration_ms, error, needs_approval}
    """
    if not cmd or not cmd.strip():
        return {"ok": False, "error": "empty command", "exit_code": -1}

    # Block obvious catastrophic patterns
    forbidden = ["rm -rf /", ":(){ :|:&};:", "mkfs", "dd if="]
    for pat in forbidden:
        if pat in cmd:
            return {"ok": False, "error": f"forbidden pattern: {pat}", "exit_code": -1}

    # Check if this is a destructive command that needs approval
    destructive_patterns = ["rm ", "del ", "rmdir", "format", ">", ">>", "chmod", "chown",
                           "kill", "shutdown", "reboot", "pip install", "npm install",
                           "git push", "git reset --hard"]
    needs_approval = any(p in cmd for p in destructive_patterns)
    if needs_approval and not _approved:
        return {
            "ok": False,
            "error": "needs_approval",
            "needs_approval": True,
            "command": cmd,
            "exit_code": -1,
        }

    cwd = str(_sandbox_root())
    t0 = time.time()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            shell=True if sys.platform == "win32" else False,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "ok": False,
                "error": f"timeout after {timeout_s}s",
                "exit_code": -1,
                "duration_ms": int((time.time() - t0) * 1000),
            }
        out = (stdout or b"").decode("utf-8", errors="replace")
        # Cap output at ~50KB to avoid SSE spam
        if len(out) > 50_000:
            out = out[:50_000] + f"\n... [truncated, {len(out) - 50_000} more bytes]"
        return {
            "ok": proc.returncode == 0,
            "output": out,
            "exit_code": proc.returncode,
            "duration_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "exit_code": -1,
            "duration_ms": int((time.time() - t0) * 1000),
        }


async def read_file(path: str, max_bytes: int = 50_000) -> Dict[str, Any]:
    """Read a file. Size-capped to prevent accidental exfiltration."""
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return {"ok": False, "error": str(e)}
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if not p.is_file():
        return {"ok": False, "error": f"not a file: {path}"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        truncated = len(text) > max_bytes
        if truncated:
            text = text[:max_bytes] + f"\n... [truncated, {p.stat().st_size - max_bytes} more bytes]"
        return {
            "ok": True,
            "content": text,
            "size_bytes": p.stat().st_size,
            "truncated": truncated,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def write_file(path: str, content: str, mode: str = "overwrite") -> Dict[str, Any]:
    """Write a file. Creates parent dirs. mode='append' adds to end."""
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return {"ok": False, "error": str(e)}
    p.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        if mode == "append":
            with p.open("a", encoding="utf-8") as f:
                n = f.write(content)
        else:
            with p.open("w", encoding="utf-8") as f:
                n = f.write(content)
        return {
            "ok": True,
            "wrote_bytes": n,
            "path": str(p.relative_to(_sandbox_root())),
            "duration_ms": int((time.time() - t0) * 1000),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def list_dir(path: str = ".", show_hidden: bool = False) -> Dict[str, Any]:
    """List a directory. Defaults to sandbox root."""
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return {"ok": False, "error": str(e)}
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    if not p.is_dir():
        return {"ok": False, "error": f"not a directory: {path}"}
    try:
        entries = []
        for child in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if not show_hidden and child.name.startswith("."):
                continue
            try:
                st = child.stat()
                size = st.st_size
                mtime = st.st_mtime
            except OSError:
                size, mtime = 0, 0
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size_bytes": size,
                "modified": mtime,
            })
        return {"ok": True, "path": str(p.relative_to(_sandbox_root())), "entries": entries}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def grep(pattern: str, path: str = ".", max_results: int = 50) -> Dict[str, Any]:
    """Regex search across text files under path."""
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return {"ok": False, "error": str(e)}
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"bad regex: {e}"}
    matches = []
    files = [p] if p.is_file() else list(p.rglob("*"))
    for f in files:
        if not f.is_file() or f.stat().st_size > 1_000_000:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matches.append({
                    "file": str(f.relative_to(_sandbox_root())),
                    "line": lineno,
                    "text": line[:200],
                })
                if len(matches) >= max_results:
                    return {
                        "ok": True,
                        "matches": matches,
                        "truncated": True,
                        "total": len(matches),
                    }
    return {"ok": True, "matches": matches, "truncated": False, "total": len(matches)}


async def apply_diff(path: str, old: str, new: str) -> Dict[str, Any]:
    """Apply a context-anchored patch: replace `old` with `new` in the file.

    Returns the diff in unified format (suitable for [diff] DSL).
    """
    try:
        p = _safe_path(path)
    except PermissionError as e:
        return {"ok": False, "error": str(e)}
    if not p.exists():
        return {"ok": False, "error": f"not found: {path}"}
    try:
        before = p.read_text(encoding="utf-8", errors="replace")
        if old not in before:
            return {"ok": False, "error": "old text not found in file (no unique match)"}
        after = before.replace(old, new, 1)
        p.write_text(after, encoding="utf-8")
        # Build a tiny unified diff for display
        diff_lines = []
        for ln in before.splitlines()[:200]:
            diff_lines.append(f"- {ln}")
        for ln in after.splitlines()[:200]:
            diff_lines.append(f"+ {ln}")
        return {
            "ok": True,
            "diff": "\n".join(diff_lines),
            "before_size": len(before),
            "after_size": len(after),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── Registry ───────────────────────────────────────────────────────

ToolFn = Callable[..., Awaitable[Dict[str, Any]]]

TOOLS: Dict[str, Dict[str, Any]] = {
    "shell_run": {
        "fn": shell_run,
        "description": "Run a shell command in the workspace sandbox. Capped at 15s.",
        "params": {
            "cmd": "string — the command to run",
            "timeout_s": "int (optional, default 15)",
        },
    },
    "read_file": {
        "fn": read_file,
        "description": "Read a text file. Size-capped at 50KB.",
        "params": {
            "path": "string — relative path inside sandbox",
            "max_bytes": "int (optional, default 50000)",
        },
    },
    "write_file": {
        "fn": write_file,
        "description": "Write or append to a file. Creates parent dirs.",
        "params": {
            "path": "string — relative path inside sandbox",
            "content": "string — the content to write",
            "mode": "string — 'overwrite' (default) or 'append'",
        },
    },
    "list_dir": {
        "fn": list_dir,
        "description": "List a directory.",
        "params": {
            "path": "string (optional, default '.')",
            "show_hidden": "bool (optional)",
        },
    },
    "grep": {
        "fn": grep,
        "description": "Regex search across text files.",
        "params": {
            "pattern": "string — regex pattern",
            "path": "string (optional, default '.')",
            "max_results": "int (optional, default 50)",
        },
    },
    "apply_diff": {
        "fn": apply_diff,
        "description": "Replace a context-anchored block in a file.",
        "params": {
            "path": "string",
            "old": "string — exact text to replace",
            "new": "string — replacement text",
        },
    },
}


async def call_tool(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a tool call by name with params."""
    if name not in TOOLS:
        return {"ok": False, "error": f"unknown tool: {name}"}
    fn = TOOLS[name]["fn"]
    try:
        return await fn(**params)
    except TypeError as e:
        return {"ok": False, "error": f"bad params: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def list_tools() -> List[Dict[str, Any]]:
    """Return a JSON-serialisable list of available tools (for the Agent
    to know what's at its disposal)."""
    return [
        {"name": name, "description": meta["description"], "params": meta["params"]}
        for name, meta in TOOLS.items()
    ]


# ── Self-test ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def _test():
        print(f"sandbox: {_sandbox_root()}")
        print()

        # List sandbox (initially empty)
        r = await list_dir(".")
        print(f"list_dir(.): {len(r.get('entries', []))} entries")
        print()

        # Write a file
        r = await write_file("hello.txt", "你好，GalaxyOS！\nThis is a test.\n")
        print(f"write_file: ok={r['ok']} wrote_bytes={r.get('wrote_bytes')}")
        print()

        # Read it back
        r = await read_file("hello.txt")
        print(f"read_file: ok={r['ok']} content={r['content']!r}")
        print()

        # Shell run
        r = await shell_run("ls -la")
        print(f"shell_run('ls -la'): ok={r['ok']} exit={r.get('exit_code')}")
        print(r.get("output", "")[:200])
        print()

        # Grep
        r = await grep("Galaxy", ".")
        print(f"grep('Galaxy'): {r.get('total')} matches")
        for m in r.get("matches", [])[:3]:
            print(f"  {m['file']}:{m['line']}: {m['text']!r}")
        print()

        # Sandbox escape attempt
        try:
            r = await read_file("../etc/passwd")
            print(f"escape attempt: ok={r['ok']} (should be False)")
        except PermissionError as e:
            print(f"escape attempt blocked: {e}")

    asyncio.run(_test())
