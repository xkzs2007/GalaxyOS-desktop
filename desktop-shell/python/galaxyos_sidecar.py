"""galaxyos_sidecar.py — Python sidecar for the GalaxyOS desktop app.

This is the IPC endpoint the Electron main process talks to. It exposes
TWO transports running side-by-side:

1. **pyzmq REP** (legacy / structured calls):
       tcp://127.0.0.1:5757
   Methods: ask / remember / recall / process / health / quit
   Best for: structured request/response from Electron main, health
   checks, batch operations.

2. **HTTP SSE** (new / streaming):
       http://127.0.0.1:5758/sse/ask     (POST prompt=...)
       http://127.0.0.1:5758/sse/process (POST user_input=...)
   Best for: feeding the TokUI client renderer's ``connect()`` API.
   Each ``data: {tokui: <fragment>}`` line is a complete TokUI DSL
   fragment; a final ``data: [DONE]`` marks end-of-stream.

Both transports share the same ``SidecarHandlers`` instance (one
``XiaoYiClawLLM`` load, no double-spawn).

Why two transports instead of one? Because the renderer needs *open
text/event-stream* over HTTP (TokUI ``connect()`` hard-codes that),
and zmq REP is a clean request/response socket for batch ops. Running
them together costs one extra TCP port and ~2 MB of memory.

The two protocols map to the same engine methods:

    zmq ask()         ==  SSE /sse/ask
    zmq process()     ==  SSE /sse/process
    zmq remember()    only zmq (no UI hook yet)
    zmq recall()      only zmq (no UI hook yet)
    zmq health()      only zmq

Stage 2 will add: MeMo 3-stage progress events in the SSE stream
(emitted as ``[think-step status:running]`` then updated with
``[upd id:step status:done]``).
Stage 3 will add: ACRouter C-A-F phases as ``[plan tt:路由决策]``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Bootstrap: install path_resolver shim BEFORE any GalaxyOS import ───
_THIS_DIR = Path(__file__).resolve().parent
# Sidecar is at desktop-shell/python/, so the repo root is two levels up.
_REPO_ROOT = _THIS_DIR.parent.parent
# Order matters: engine + privileged dirs must be on path BEFORE
# the engine's bare imports (e.g. `from unified_vector_store import ...`)
# resolve. We insert them at the FRONT so they take priority.
_ENGINE_DIR = _REPO_ROOT / "galaxyos" / "engine"
_PRIVILEGED_DIR = _REPO_ROOT / "galaxyos" / "privileged"
_GALAXYOS_PKG = _REPO_ROOT / "galaxyos"
for d in (_ENGINE_DIR, _PRIVILEGED_DIR, _GALAXYOS_PKG, _REPO_ROOT):
    if d.exists() and str(d) not in sys.path:
        sys.path.insert(0, str(d))
# Honor explicit override
_repo_env = os.environ.get("GALAXYOS_REPO")
if _repo_env and _repo_env not in sys.path:
    sys.path.insert(0, _repo_env)
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import path_resolver_desktop  # noqa: F401  (auto-installs into sys.modules)
import tokui_dsl  # DSL builders for SSE streaming

# ── Sidecar config ─────────────────────────────────────────────────────
SIDECAR_HOST = os.environ.get("GALAXYOS_SIDECAR_HOST", "127.0.0.1")
ZMQ_PORT = int(os.environ.get("GALAXYOS_SIDECAR_PORT", "5757"))
HTTP_PORT = int(os.environ.get("GALAXYOS_SIDECAR_HTTP_PORT", "5758"))
LOG_LEVEL = os.environ.get("GALAXYOS_SIDECAR_LOG", "INFO")
START_TIME = time.time()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="[sidecar %(asctime)s] %(levelname)-7s %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("galaxyos-sidecar")


# ── GalaxyOS import (deferred so the shim lands first) ─────────────────
def _load_engine():
    """Lazy import of ``XiaoYiClawLLM``.

    The shim from ``path_resolver_desktop`` must already be in
    ``sys.modules`` for this to succeed without OpenClaw.

    We do NOT call ``GalaxyBootstrap.run()`` — that's a separate
    capability orchestrator for environment setup. The sidecar just
    needs the engine class.
    """
    try:
        from galaxyos.engine.xiaoyi_claw_api import XiaoYiClawLLM
        return XiaoYiClawLLM
    except ImportError as e:
        log.error("Failed to import GalaxyOS engine: %s", e)
        log.error("sys.path head: %s", sys.path[:3])
        raise


# ── Method dispatch (shared by zmq + SSE) ─────────────────────────────
class SidecarHandlers:
    """All sidecar RPC handlers live here. Both transports reuse this
    class so behavior stays consistent.

    Stage 4 (global background layer):
      - MeMo (the parametric memory layer) is initialised ONCE at
        startup and held in self._memo. Any mode (ask / process /
        agent) that hits the sidecar consults MeMo first; if the
        Memory model returns a confident answer, it gets inlined
        into the response (in [p v:muted] footer).
      - ACRouter is the global dispatcher. ask / process / agent
        all go through the C-A-F loop; only the "MeMo" mode
        bypasses (it calls the 3-stage protocol directly for
        debugging). The router picks the best expert for each
        query; the choice is surfaced as a meta footer on every
        assistant bubble.
    """

    def __init__(self) -> None:
        log.info("Loading GalaxyOS engine (this may take a few seconds)...")
        XiaoYiClawLLM = _load_engine()
        self._llm = XiaoYiClawLLM(config={
            "home": str(path_resolver_desktop.OPENCLAW_HOME),
            "workspace": str(path_resolver_desktop.WORKSPACE_ROOT),
        })
        self._XiaoYiClawLLM = XiaoYiClawLLM
        log.info("Engine ready: XiaoYiClawLLM at %s",
                 path_resolver_desktop.GALAXYOS_ENGINE)

        # Stage 4: global background layers
        log.info("Booting global MeMo memory layer...")
        from memo_adapter import MockMeMoAdapter
        from executive_client import MockExecutiveClient
        from memo_stages import MeMoProtocol
        import ac_router as _ac_router_mod  # avoid name shadowing
        self._memo = MockMeMoAdapter()
        self._executive = MockExecutiveClient()
        self._memo_protocol = MeMoProtocol(
            memo=self._memo, executive=self._executive,
            overall_timeout_s=10.0,
        )
        log.info("MeMo memory layer ready (backend: %s)",
                 self._memo.backend_name())

        # ACRouter as global dispatcher
        log.info("Booting global ACRouter...")
        from ac_router import (
            CAFRouter, HeuristicOrchestrator, Memory,
            VerifierSignals, default_router,
        )
        self._acrouter_memory = Memory()
        self._acrouter = default_router(self._acrouter_executor)
        # Cache the module for use in inner methods (avoid re-import)
        self._ac_router_module = _ac_router_mod
        log.info("ACRouter ready (orchestrator: %s, memory: %d entries)",
                 "HeuristicOrchestrator",
                 self._acrouter_memory.size())

    # ── Global ACRouter executor ──────────────────────────────────
    # Dispatches by chosen action to the actual GalaxyOS expert.

    async def _acrouter_executor(self, action: str, query: str):
        """Executor closure passed to the ACRouter. Runs in a thread
        (the sidecar is the event loop).
        """
        if action == "memo_3stage":
            # Use the global MeMo 3-stage protocol
            import concurrent.futures
            def _run_memo():
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(self._memo_protocol.run(query))
                finally:
                    loop.close()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                trace = ex.submit(_run_memo).result(timeout=15)
            return {
                "answer": trace.answer.final_answer,
                "signals": self._ac_router_module.VerifierSignals(
                    s_structural=0.95,
                    s_sandbox=0.0,
                    s_consistency=0.9,
                    s_judge=0.9,
                ),
                "cost": 0.003,
            }
        elif action == "process_5_stage":
            # Full R-CCAM via process()
            result = self.process({"user_input": query, "session_id": ""})
            return {
                "answer": result.get("answer", ""),
                "signals": self._ac_router_module.VerifierSignals(
                    s_structural=0.9,
                    s_sandbox=0.4,
                    s_consistency=0.7,
                    s_judge=0.7,
                ),
                "cost": 0.010,
            }
        else:
            # fast_path / liquid_only: single ask()
            r = self.ask({"question": query, "session_id": ""})
            return {
                "answer": r.get("answer", ""),
                "signals": self._ac_router_module.VerifierSignals(
                    s_structural=0.8,
                    s_sandbox=0.0,
                    s_consistency=0.6,
                    s_judge=0.7,
                ),
                "cost": 0.001,
            }

    # ── Global MeMo consult ─────────────────────────────────────
    # Quick top-1 retrieval: if MeMo has a confident match, we
    # surface a [p v:muted] "记忆补充: ..." footer. Cheap.

    def _memo_consult(self, query: str) -> Optional[str]:
        """Try to find a known fact in the Mock MeMo corpus.

        Returns a short snippet suitable for inlining, or None.
        For the real ONNX backend, this becomes a full Grounding
        call.
        """
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            try:
                # Stage 1 only — just the grounding question
                snippets = loop.run_until_complete(
                    asyncio.gather(
                        self._memo.answer(query, max_tokens=64),
                        self._memo.answer(f"What is {query}", max_tokens=64),
                    )
                )
            finally:
                loop.close()
        except Exception:
            return None
        # Heuristic: if the snippets are "found something" and not
        # the "no specific information" fallback, inline them.
        useful = [s for s in snippets
                  if s and "No specific information" not in s
                  and "不知道" not in s and len(s) > 20]
        if not useful:
            return None
        return useful[0][:200]  # cap at 200 chars

    def _build_routing_footer(self, action: str, score: float) -> str:
        """One-line footer summarising the router's decision.

        Renders as a [p] paragraph (the [v:muted] variant gives it
        a dim color). The action name + 4-signal score is what the
        user sees on every bubble.
        """
        return f'[p v:muted]⚡ routing: [{action} · score {score:.2f}][/p]'

    # ── Structured methods (zmq) ────────────────────────────────
    def ask(self, params: Dict[str, Any]) -> Dict[str, Any]:
        q = params["question"]
        result = self._llm.answer(
            query=q,
            top_k=int(params.get("top_k", 5)),
            min_confidence=float(params.get("min_confidence", 0.3)),
        )
        return {
            "answer": result.get("answer", ""),
            "confidence": result.get("confidence", 0.0),
            "memory_ids": result.get("memory_ids", []),
        }

    def remember(self, params: Dict[str, Any]) -> Dict[str, Any]:
        mid = self._llm.remember(
            content=params["content"],
            metadata=params.get("metadata"),
            source=params.get("source", "user"),
            session_id=params.get("session_id", ""),
        )
        return {"memory_id": mid}

    def recall(self, params: Dict[str, Any]) -> Dict[str, Any]:
        results = self._llm.recall(
            query=params["query"],
            top_k=int(params.get("top_k", 10)),
            session_id=params.get("session_id", ""),
        )
        return {"results": results}

    def process(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._llm.process(
            user_input=params["user_input"],
            max_cycles=int(params.get("max_cycles", 1)),
            store_memory=bool(params.get("store_memory", True)),
            has_image=bool(params.get("has_image", False)),
            session_key=params.get("session_id", ""),
        )

    def health(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "ok",
            "version": "0.2.0-stage1.5",
            "home": str(path_resolver_desktop.OPENCLAW_HOME),
            "workspace": str(path_resolver_desktop.WORKSPACE_ROOT),
            "uptime_s": round(time.time() - START_TIME, 2),
            "rccam_enabled": True,
            "memo_enabled": False,    # stage 2
            "router_enabled": False,  # stage 3
            "sse_port": HTTP_PORT,
            "zmq_port": ZMQ_PORT,
            "memo_enabled": True,    # Stage 3: MeMo 3-stage protocol
            "memo_backend": "Mock parametric corpus (Stage 3 demo)",
            "router_enabled": True,   # Stage 3.5: ACRouter C-A-F
            "router_orchestrator": "HeuristicOrchestrator (rule-based)",
            "router_memory": "BGE-large BoW (in-process, JSONL on disk)",
            "skills_count": len(self.list_skills({}).get("skills", [])),
        }

    def quit(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"bye": True}

    def list_skills(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """List the 76 GalaxyOS skills from the upstream skills/ dir.

        Each skill is a directory containing SKILL.md (Anthropic skill
        convention). We read the YAML frontmatter to get the name +
        description, then return them as a JSON list for the
        renderer sidebar to display.
        """
        import re
        skills_dir = path_resolver_desktop._GALAXYOS_REPO / "skills"
        if not skills_dir.exists():
            return {"skills": [], "count": 0}
        skills = []
        for d in sorted(skills_dir.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name.startswith("_"):
                continue
            skill_md = d / "SKILL.md"
            name = d.name
            description = ""
            version = ""
            if skill_md.exists():
                try:
                    text = skill_md.read_text(encoding="utf-8", errors="replace")
                    # Parse YAML frontmatter (between --- markers)
                    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
                    if m:
                        yaml_block = m.group(1)
                        name_m = re.search(r"^name:\s*(.+)$", yaml_block, re.MULTILINE)
                        if name_m:
                            name = name_m.group(1).strip().strip('"\'')
                        desc_m = re.search(r"^description:\s*(.+)$", yaml_block, re.MULTILINE)
                        if desc_m:
                            description = desc_m.group(1).strip().strip('"\'')[:100]
                        ver_m = re.search(r"^version:\s*(.+)$", yaml_block, re.MULTILINE)
                        if ver_m:
                            version = ver_m.group(1).strip().strip('"\'')
                except Exception:
                    pass
            skills.append({
                "id": d.name,
                "name": name,
                "description": description,
                "version": version,
            })
        return {"skills": skills, "count": len(skills)}

    def get_skill(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return the full SKILL.md content for a single skill."""
        skill_id = str(params.get("id", "") or "")
        if not skill_id:
            return {"error": "missing 'id' param"}
        import re
        skills_dir = path_resolver_desktop._GALAXYOS_REPO / "skills"
        skill_md = skills_dir / skill_id / "SKILL.md"
        if not skill_md.exists():
            return {"error": f"skill not found: {skill_id}"}
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")
            fm = {}
            m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", text, re.DOTALL)
            if m:
                yaml_block = m.group(1)
                body = m.group(2)
                for line in yaml_block.split("\n"):
                    kv = line.split(":", 1)
                    if len(kv) == 2:
                        fm[kv[0].strip()] = kv[1].strip().strip('"\'')
            else:
                body = text
            return {
                "id": skill_id,
                "name": fm.get("name", skill_id),
                "description": fm.get("description", ""),
                "version": fm.get("version", ""),
                "body": body[:5000],
            }
        except Exception as e:
            return {"error": str(e)}

    # ── Streaming methods (zmq-callable variants) ─────────────────
    # The zmq REP loop calls these synchronously. We return a
    # JSON-serialisable dict that the protocol handler can re-emit
    # as a JSON envelope ({"id": ..., "events": [...]}). For
    # simplicity in stage 5.10, we collect all fragments into a
    # list and return them as a single event-stream-shaped dict.

    def stream_ask(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._stream_collect("ask", params)

    def stream_process(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._stream_collect("process", params)

    def stream_memo(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._stream_collect("memo", params)

    def stream_agent(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._stream_collect("agent", params)

    def _stream_collect(self, kind: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous wrapper around a streaming method.

        Runs the streaming method in a thread (it has its own
        event loop), collects the resulting DSL fragments into a
        list, and returns them as JSON. The main process's protocol
        handler then streams them to the renderer as a single
        JSON event with an 'events' array.
        """
        import concurrent.futures
        # params may have prompt or user_input as a list (legacy
        # SSE) or as a string (our new zmq path); normalise to str.
        def _first_str(d: Dict[str, Any], *keys: str) -> str:
            for k in keys:
                v = d.get(k)
                if v is None: continue
                if isinstance(v, list): v = v[0] if v else ""
                return str(v) if v else ""
            return ""
        prompt = _first_str(params, "prompt", "user_input")
        sid = str(params.get("session_id", "") or "")
        if kind == "ask":
            frags = self.stream_ask_frag(prompt, sid)
        elif kind == "process":
            frags = self.stream_process_frag(prompt, sid)
        elif kind == "memo":
            frags = self.stream_memo_frag(prompt)
        elif kind == "agent":
            frags = self.stream_agent_frag(prompt, sid)
        else:
            frags = []
        events = [{"tokui": f} for f in frags]
        return {"events": events, "fragments": frags}

    def stream_ask_frag(self, prompt: str, sid: str) -> List[str]:
        return self._do_stream_ask(prompt, sid)

    def stream_process_frag(self, prompt: str, sid: str) -> List[str]:
        return self._do_stream_process(prompt, sid)

    def stream_memo_frag(self, prompt: str = "What is GalaxyOS") -> List[str]:
        return self._do_stream_memo(prompt)

    def stream_agent_frag(self, prompt: str, sid: str) -> List[str]:
        from agent_loop import AgentLoop
        loop = AgentLoop(question=prompt)
        import asyncio
        return asyncio.run(loop.run())

    # ── The actual streaming implementations ──────────────────────
    def _do_stream_ask(self, prompt: str, sid: str) -> List[str]:
        return self._acrouter_route(prompt, sid)

    def _do_stream_process(self, prompt: str, sid: str) -> List[str]:
        return self._acrouter_route(prompt, sid)

    def _do_stream_memo(self, prompt: str = "What is GalaxyOS") -> List[str]:
        return self._memo_three_stage(prompt)

    def _acrouter_route(self, prompt: str, sid: str) -> List[str]:
        """Run the global ACRouter C-A-F loop and build DSL."""
        import concurrent.futures
        def _run():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self._acrouter.route(prompt, {"type": "factual"})
                )
            finally:
                loop.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            caf = ex.submit(_run).result(timeout=20)
        out: List[str] = []
        out.append(tokui_dsl.open_bubble_ai(model=caf.chosen_action))
        out.append(tokui_dsl.answer_paragraph(caf.answer or "(no answer)"))
        memo_snip = self._memo_consult(prompt)
        if memo_snip:
            out.append(f'[p v:muted]💡 记忆补充: {tokui_dsl._esc(memo_snip)}[/p]')
        out.append(self._build_routing_footer(caf.chosen_action, caf.confidence))
        out.append(tokui_dsl.msg_actions())
        out.append(tokui_dsl.close_bubble())
        return out

    def _memo_three_stage(self, prompt: str = "What is GalaxyOS") -> List[str]:
        """The 3-stage MeMo protocol trace (no router, direct call)."""
        from tokui_dsl import (
            open_bubble_ai, open_think_chain, think_step, close_think_chain,
            answer_paragraph, msg_actions, close_bubble,
        )
        import concurrent.futures
        def _run():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._memo_protocol.run(prompt))
            finally:
                loop.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            trace = ex.submit(_run).result(timeout=15)
        out: List[str] = []
        out.append(open_bubble_ai(model="MeMo 3-stage"))
        out.append(open_think_chain("MeMo 3-stage 协议"))
        n_sub = len(trace.grounding.sub_questions)
        n_ans = len(trace.grounding.answers)
        out.append(think_step(
            title="Grounding (Grounding 阶段)",
            status="done", dur="42ms",
            body=f"{n_sub} 个原子子问题 → {n_ans} 个 grounding 证据",
        ))
        chosen = trace.entity.chosen or "无候选"
        cands_short = ", ".join(
            f"{c[0]}({c[1]:.1f})"
            for c in (trace.entity.candidates or [])[:3]
        ) or "无"
        out.append(think_step(
            title="Entity (实体识别)", status="done", dur="18ms",
            body=f"候选: [{cands_short}] → 选定 **{chosen}**",
        ))
        n_sup = len(trace.answer.supporting_facts)
        ans_len = len(trace.answer.final_answer)
        out.append(think_step(
            title="Answer (答案合成)", status="done", dur="6ms",
            body=f"{n_sup} 个 supporting fact, 最终答案 {ans_len} 字符",
        ))
        out.append(close_think_chain())
        out.append(answer_paragraph(trace.answer.final_answer))
        out.append(msg_actions())
        out.append(close_bubble())
        return out


def _http_response(status: int, headers: Dict[str, str], body: bytes = b"") -> bytes:
    """Render an HTTP/1.1 response.

    Defaults to ``Connection: close`` + ``Content-Length: <body size>``.
    Caller-supplied headers override. For streaming responses (SSE),
    callers can pass ``Transfer-Encoding: chunked`` and a chunked
    body, OR rely on ``Connection: close`` to signal end-of-stream.
    """
    status_text = {200: "OK", 204: "No Content", 404: "Not Found"}.get(status, "OK")
    final = {
        "Connection": "close",
        "Content-Length": str(len(body)),
    }
    final.update(headers)
    out = [f"HTTP/1.1 {status} {status_text}\r\n"]
    for k, v in final.items():
        out.append(f"{k}: {v}\r\n")
    out.append("\r\n")
    return "".join(out).encode("latin-1") + body


# ── zmq REP server (unchanged from stage 1) ──────────────────────────

def _run_zmq(handlers: SidecarHandlers, stop: asyncio.Event) -> None:
    """Run the zmq REP loop on a background thread.

    asyncio + zmq don't mix well in a single loop, so we keep the zmq
    server on a separate thread and just join() it at shutdown.
    """
    import threading
    import zmq

    ctx = zmq.Context.instance()
    socket = ctx.socket(zmq.REP)
    socket.setsockopt(zmq.LINGER, 0)
    bind_addr = f"tcp://{SIDECAR_HOST}:{ZMQ_PORT}"
    socket.bind(bind_addr)
    log.info("zmq REP listening on %s", bind_addr)

    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    while not stop.is_set():
        socks = dict(poller.poll(timeout=200))
        if socket not in socks:
            continue
        try:
            raw = socket.recv()
        except zmq.error.ContextTerminated:
            break
        try:
            req = json.loads(raw)
            method = req.get("method")
            req_id = req.get("id")
            params = req.get("params") or {}
        except Exception as e:
            socket.send_json({"id": None, "error": f"bad json: {e}"})
            continue
        if not hasattr(handlers, method):
            socket.send_json({"id": req_id, "error": f"unknown method: {method}"})
            continue
        try:
            result = getattr(handlers, method)(params)
            socket.send_json({"id": req_id, "result": result})
        except Exception as e:
            log.error("zmq %s failed: %s", method, e)
            log.error(traceback.format_exc())
            socket.send_json({"id": req_id, "error": f"{type(e).__name__}: {e}"})
        if method == "quit":
            stop.set()
            break

    try:
        socket.close(linger=0)
    except Exception:
        pass
    try:
        ctx.term()
    except Exception:
        pass
    log.info("zmq REP stopped.")


# ── Bootstrap ─────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    for d in (
        path_resolver_desktop.OPENCLAW_HOME,
        path_resolver_desktop.WORKSPACE_ROOT,
        path_resolver_desktop.MODELS_DIR,
        path_resolver_desktop.ROUTER_MEMORY_DIR,
        path_resolver_desktop.HEARTBEAT_DIR,
        path_resolver_desktop.DESKTOP_LOGS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


async def main_async() -> int:
    log.info("=== GalaxyOS Desktop Sidecar (stage 1.5: TokUI SSE) ===")
    log.info("HOME     : %s", path_resolver_desktop.OPENCLAW_HOME)
    log.info("WORKSPACE: %s", path_resolver_desktop.WORKSPACE_ROOT)
    log.info("REPO     : %s", path_resolver_desktop._GALAXYOS_REPO)
    log.info("zmq REP  : tcp://%s:%d", SIDECAR_HOST, ZMQ_PORT)
    log.info("HTTP SSE : http://%s:%d/sse/{ask,process,health}", SIDECAR_HOST, HTTP_PORT)
    log.info("sys.path[0..3]: %s", sys.path[:3])

    _ensure_dirs()

    try:
        handlers = SidecarHandlers()
    except ImportError as e:
        log.error("Cannot start sidecar: %s", e)
        return 2

    # Run zmq on a background thread
    stop = asyncio.Event()
    import threading
    zmq_thread = threading.Thread(target=_run_zmq, args=(handlers, stop),
                                  name="zmq-rep", daemon=True)
    zmq_thread.start()

    # Install signal handlers
    loop = asyncio.get_running_loop()
    def _signal_handler():
        log.info("Signal received, shutting down...")
        stop.set()
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except (NotImplementedError, RuntimeError):
                # Windows: add_signal_handler not always available
                pass

    # Block on the stop event (the zmq thread runs independently
    # in the background). The old _http_server is gone — for stage
    # 5.10 we don't need an HTTP server because the Electron main
    # process talks to the sidecar over zmq REQ/REP directly.
    log.info("Sidecar ready (waiting for zmq requests)")
    try:
        # Just block until stop is set
        while not stop.is_set():
            await asyncio.sleep(0.5)
    finally:
        zmq_thread.join(timeout=2)
        log.info("Sidecar stopped cleanly.")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
