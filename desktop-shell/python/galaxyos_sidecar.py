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

# Detect PyInstaller --onefile / --onedir freeze. Inside a frozen
# build, `__file__` points into `sys._MEIPASS` (a temp extraction
# dir) and the `../../` math below would yield a non-existent path.
# PyInstaller also bundles the whole `galaxyos/` package via the
# spec's `datas` entry, so `from galaxyos.engine...` resolves
# through the import system without us touching sys.path.
_FROZEN = getattr(sys, "frozen", False)

if not _FROZEN:
    # Sidecar is at desktop-shell/python/, so the repo root is two levels up.
    _REPO_ROOT = _THIS_DIR.parent.parent
    # Order matters: engine + privileged dirs must be on path BEFORE
    # the engine's bare imports (e.g. `from unified_vector_store import ...`)
    # resolve. We insert them at the FRONT so they take priority.
    _ENGINE_DIR = _REPO_ROOT / "galaxyos" / "engine"
    _PRIVILEGED_DIR = _REPO_ROOT / "galaxyos" / "privileged"
    _GALAXYOS_PKG = _REPO_ROOT / "galaxyos"
    # Use the stdlib logger once it's configured below, but for this
    # early-bootstrap section use print() so the message appears
    # even if the sidecar's logging hasn't been set up yet.
    _print = lambda *a: print("[sidecar bootstrap]", *a, file=sys.stderr)
    for d in (_ENGINE_DIR, _PRIVILEGED_DIR, _GALAXYOS_PKG, _REPO_ROOT):
        if d.exists():
            if str(d) not in sys.path:
                sys.path.insert(0, str(d))
        else:
            # Previously this was a silent skip — if galaxyos/ has
            # been moved/renamed, the user would get a confusing
            # "ModuleNotFoundError" 200ms later without any clue
            # what went wrong. Now we at least surface a warning.
            _print(f"  WARN: expected path missing: {d}")
    if not _REPO_ROOT.exists():
        _print(f"  WARN: repo root missing: {_REPO_ROOT}  "
               f"(sidecar at {_THIS_DIR})")
    # Honor explicit override
    _repo_env = os.environ.get("GALAXYOS_REPO")
    if _repo_env:
        if _repo_env not in sys.path:
            sys.path.insert(0, _repo_env)
    if str(_THIS_DIR) not in sys.path:
        sys.path.insert(0, str(_THIS_DIR))
else:
    # In a frozen build, GALAXYOS_REPO still wins (e.g. when the
    # operator wants the sidecar to use an on-disk data dir at
    # /opt/galaxyos instead of the bundled one). PyInstaller
    # already places _MEIPASS at the front of sys.path, so the
    # bundled galaxyos/ package is importable without any work
    # from us.
    _repo_env = os.environ.get("GALAXYOS_REPO")
    if _repo_env and _repo_env not in sys.path:
        sys.path.insert(0, _repo_env)
    print(f"[sidecar bootstrap] frozen sidecar, _MEIPASS={sys._MEIPASS!r}, "
          f"has galaxyos pkg={os.path.isdir(os.path.join(sys._MEIPASS, 'galaxyos')) if hasattr(sys, '_MEIPASS') else 'N/A'}",
          file=sys.stderr)

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

# In a frozen (PyInstaller) build we deliberately strip heavy ML deps
# (torch / transformers / onnxruntime) to keep the binary under 200 MB.
# The engine catches the resulting ImportError in its try/except blocks
# and falls back to lighter in-process implementations. Those fallbacks
# are expected and not actionable, so suppress just the ImportError
# noise from the engine's lazy-loader try/except blocks. We do this
# with a logging.Filter rather than mutating logger levels, because
# the basicConfig level governs the root and `setLevel` on a child
# logger does NOT bypass the root's effective level when root is
# more permissive (e.g. INFO) than the child (DEBUG).
if getattr(sys, "frozen", False):
    class _SuppressFrozenImportNoise(logging.Filter):
        """Drop WARNING records that are purely about missing optional
        heavy-ML modules (torch / transformers / onnxruntime) inside
        a frozen build. Keep all other WARNINGs untouched.
        """
        _QUIET = (
            "No module named 'torch'",
            "No module named 'transformers'",
            "No module named 'onnxruntime'",
            "No module named 'faiss'",
            "No module named 'hnswlib'",
        )

        def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
            try:
                msg = record.getMessage()
            except Exception:
                return True
            if record.levelno != logging.WARNING:
                return True
            return not any(q in msg for q in self._QUIET)

    _filt = _SuppressFrozenImportNoise()
    for h in logging.getLogger().handlers:
        h.addFilter(_filt)


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
        # When frozen (PyInstaller onefile), `sys._MEIPASS` is the
        # bundle root that contains `galaxyos/`, `path_resolver_desktop.py`,
        # etc. Logging it makes it obvious whether the engine files
        # are even in the bundle — a missing _MEIPASS / missing
        # galaxyos/ subdir is a PyInstaller spec bug, not a runtime
        # config issue.
        log.error("Frozen: %s  _MEIPASS=%s", getattr(sys, 'frozen', False),
                  getattr(sys, '_MEIPASS', '(unset)'))
        log.error("sys.path head: %s", sys.path[:5])
        log.error("CWD: %s", os.getcwd())
        # Also dump which path entries actually exist — silent
        # missing paths are the #1 cause of "works in dev, fails
        # when packaged".
        for p in sys.path[:8]:
            log.error("  sys.path: %s  exists=%s", p, os.path.isdir(p))
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
        from memo_adapter import (
            MockMeMoAdapter, OnnxMeMoAdapter, load_default_adapter,
        )
        from executive_client import MockExecutiveClient
        from memo_stages import MeMoProtocol
        import ac_router as _ac_router_mod  # avoid name shadowing
        # Probe for real ONNX weights first (LFM2.5 downloaded via
        # install_wizard --download-lfm-onnx). Falls back to Mock if
        # weights are missing/corrupt. Cached at module level inside
        # load_default_adapter so this is a one-time cost.
        try:
            self._memo = load_default_adapter()
            log.info("MeMo backend: %s", self._memo.backend_name())
        except Exception as e:
            log.warning("load_default_adapter() failed (%s); using Mock", e)
            self._memo = MockMeMoAdapter()
        # _executive is built in _build_executive() AFTER _live_config
        # is initialised (see below)
        self._executive = None
        self._memo_protocol = MeMoProtocol(
            memo=self._memo, executive=MockExecutiveClient(),  # placeholder
            overall_timeout_s=10.0,
        )
        # Cache the backend identity + (if ONNX) the model size for
        # /sse/health surface. Also remember whether we have real
        # weights so /sse/memo can warn on cold start.
        self._memo_backend_name = self._memo.backend_name()
        self._memo_is_onnx = isinstance(self._memo, OnnxMeMoAdapter)
        self._memo_load_lock = asyncio.Lock()  # for lazy onnx load
        log.info("MeMo memory layer ready (backend: %s)",
                 self._memo_backend_name)

        # ACRouter as global dispatcher
        log.info("Booting global ACRouter...")
        from ac_router import (
            CAFRouter, HeuristicOrchestrator, Memory,
            VerifierSignals, default_router,
        )
        self._acrouter_memory = Memory()
        self._acrouter = default_router(self._acrouter_executor)
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

        # Stage 13: SkillGraph — load from the 76 skills + edges
        log.info("Booting SkillGraph...")
        try:
            import sys as _sys
            _scripts = str(path_resolver_desktop._GALAXYOS_REPO / "extensions" / "galaxyos" / "scripts")
            if _scripts not in _sys.path:
                _sys.path.insert(0, _scripts)
            from skill_graph import SkillGraph, GraphAwareRetriever
            self._skill_graph = SkillGraph(auto_load=False)
            self._load_skill_graph()
            self._skill_retriever = GraphAwareRetriever(self._skill_graph)
            log.info("SkillGraph ready (%d nodes, %d edges)",
                     self._skill_graph.stats().get("nodes", 0),
                     self._skill_graph.stats().get("edges", 0))
        except Exception as e:
            log.warning("SkillGraph init failed: %s — skills will use flat search", e)
            self._skill_graph = None
            self._skill_retriever = None

        # Live config — updated by set_config() from the renderer
        self._live_config = {
            "api_key": os.environ.get("LLM_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "")),
            "api_base": os.environ.get("LLM_API_BASE", "https://api.deepseek.com/v1"),
            "model": "deepseek-chat",
            "system_prompt": "",
        }
        # Model name mapping: UI label → actual API model string
        self._model_map = {
            "Qwen-2.5": "deepseek-chat",
            "Qwen-3": "deepseek-reasoner",
            "DeepSeek-V4": "deepseek-chat",
            "Gemini-3-Flash": "gemini-3-flash",
            "LFM-2.5-1.2B": "lfm-2.5-1.2b",
        }
        # Stage 14.2: build the Executive client from live config
        # v9.2: also initialise the multi-slot provider router
        # (llm / llm_pro / embedding / rerank). The Executive is
        # still built from the legacy single-slot live_config for
        # backward compat; set_config() can promote to the multi-slot
        # form when the renderer sends it.
        try:
            from llm_providers import MultiSlotRouter
            self._router = MultiSlotRouter()
        except Exception as e:
            log.warning("MultiSlotRouter init failed: %s", e)
            self._router = None
        self._executive = self._build_executive()
        # Try to apply config to the LLM client on first boot
        self._apply_live_config()

    def _build_executive(self):
        """Build the Executive (LLM client) for MeMo from live config.

        If an API key is set in live config OR env vars, instantiate
        DeepSeekExecutiveClient. Otherwise fall back to MockExecutiveClient.
        """
        cfg = self._live_config
        if cfg.get("api_key"):
            try:
                from executive_client import DeepSeekExecutiveClient
                log.info("Using DeepSeekExecutiveClient (model=%s, api_base=%s)",
                         cfg["model"], cfg["api_base"])
                # DeepSeekExecutiveClient takes (api_key, model=) only.
                # The base URL is configured via env var DEEPSEEK_API_BASE
                # in the executive_client module itself; we propagate
                # the user's setting so the client picks it up.
                if cfg.get("api_base"):
                    os.environ["DEEPSEEK_API_BASE"] = cfg["api_base"]
                return DeepSeekExecutiveClient(
                    api_key=cfg["api_key"],
                    model=cfg["model"],
                )
            except Exception as e:
                log.warning("DeepSeekExecutiveClient init failed: %r — falling back to Mock", e)
        from executive_client import MockExecutiveClient
        log.info("Using MockExecutiveClient (no API key set)")
        return MockExecutiveClient()

    def set_config(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Hot-update the LLM config from the renderer's settings modal.

        v9.2 — accepts BOTH the legacy single-slot form
        ({api_key, api_base, model, system_prompt}) and the new
        multi-slot form ({llm: {provider, base_url, api_key, model},
        llm_pro: {...}, embedding: {...}, rerank: {...}, vlm: {...}}).

        v9.4 — multi-slot now covers **5 slots** (vlm added), and
        every slot is **optional**. A slot is only enabled when the
        caller passes it in the params; absent slots keep their
        current enabled/disabled state. Within a slot, a spec with
        ``{"enabled": false}`` explicitly disables that slot (useful
        for the "off" toggle in the Settings UI).

        If any multi-slot spec is present, routes through MultiSlotRouter
        (independent providers per slot). Otherwise falls back to the
        legacy single-slot path for backward compat with the old 5-label
        model picker.
        """
        changed: List[str] = []
        api_key_changed = False

        # ── Multi-slot path (v9.2 → v9.4) ─────────────────────────
        # v9.4: vlm joined the party; also honour per-slot `enabled`
        # flag so the Settings UI can turn individual capabilities
        # on/off without touching the other slots.
        multi_slot_keys = ("llm", "llm_pro", "embedding", "rerank", "vlm")
        has_multi_slot = any(k in params for k in multi_slot_keys)
        if has_multi_slot and self._router is not None:
            llm_slot_changed = False
            for slot in multi_slot_keys:
                if slot not in params:
                    continue  # absent = keep current state
                spec = params[slot]
                if not isinstance(spec, dict):
                    continue
                # Explicit disable: revert to mock + flip enabled=False
                if spec.get("enabled") is False:
                    self._router.disable_slot(slot)
                    changed.append(f"slot:{slot}:disabled")
                else:
                    self._router.set_slot(slot, spec)
                    changed.append(f"slot:{slot}")
                if slot == "llm":
                    llm_slot_changed = True
            # Only rebuild the Executive when the *llm* slot actually
            # changed — touching embedding/rerank/vlm shouldn't churn
            # the LLM client (which may have in-flight streams).
            if llm_slot_changed:
                self._executive = self._build_executive()
                if self._memo_protocol is not None:
                    self._memo_protocol.executive = self._executive
            # Forward legacy top-level system_prompt if any
            if "system_prompt" in params:
                self._live_config["system_prompt"] = str(params["system_prompt"] or "")
            log.info("Multi-slot config updated: %s", ", ".join(changed))
            return {
                "ok": True,
                "updated": changed,
                "router_info": self._router.info(),
            }

        # ── Legacy single-slot path (backward compat) ─────────────
        for k in ("api_key", "api_base", "model", "system_prompt"):
            if k in params and params[k] != self._live_config.get(k):
                if k == "model":
                    raw = str(params[k])
                    self._live_config[k] = self._model_map.get(raw, raw)
                else:
                    self._live_config[k] = str(params[k] or "")
                if k == "api_key":
                    api_key_changed = True
                changed.append(k)
        if changed:
            self._apply_live_config()
            if api_key_changed:
                self._executive = self._build_executive()
                if self._memo_protocol is not None:
                    self._memo_protocol.executive = self._executive
            # Also propagate the legacy config to the "llm" slot of
            # the multi-slot router (so v9.2 clients reading the
            # router see the right value).
            if self._router is not None:
                self._router.set_slot("llm", {
                    "provider": "deepseek",
                    "base_url": self._live_config.get("api_base", ""),
                    "api_key":  self._live_config.get("api_key", ""),
                    "model":    self._live_config.get("model", ""),
                })
            log.info("Live config updated: %s", ", ".join(changed))
        return {"ok": True, "updated": changed, "current_model": self._live_config["model"]}

    def _apply_live_config(self) -> None:
        """Push the live config into the actual LLM client.

        The GalaxyOS engine's XiaoYiClawLLM stores its LLM client at
        self._llm.llm_flash / self._llm.llm_pro. We try to
        re-initialize them with the new api_key/base/model.
        """
        cfg = self._live_config
        if not cfg.get("api_key"):
            return  # no key = stay in mock mode
        try:
            # Write a llm_config.json the engine can read
            import json
            config_dir = path_resolver_desktop.GALAXYOS_CONFIG
            config_dir.mkdir(parents=True, exist_ok=True)
            llm_config = config_dir / "llm_config.json"
            llm_config_data = {
                "api_key": cfg["api_key"],
                "base_url": cfg["api_base"],
                "model": cfg["model"],
            }
            # Also set embedding config if openai is available
            llm_config_data["embedding"] = {
                "api_key": cfg["api_key"],
                "base_url": cfg["api_base"],
                "model": "text-embedding-3-small",
            }
            llm_config.write_text(
                json.dumps(llm_config_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            log.info("Wrote llm_config.json with model=%s", cfg["model"])
            # Set env vars so the engine picks them up on next init
            os.environ["LLM_API_KEY"] = cfg["api_key"]
            os.environ["DEEPSEEK_API_KEY"] = cfg["api_key"]
            os.environ["LLM_API_BASE"] = cfg["api_base"]
        except Exception as e:
            log.warning("Failed to apply live config: %s", e)

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

    def list_providers(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return the mainstream provider catalogue + current router state.

        v9.2: the renderer calls this to populate the model picker
        and the provider dropdown in the Settings modal.
        """
        from llm_providers import MAINSTREAM_PROVIDERS
        result: Dict[str, Any] = {
            "providers": [
                {"id": p[0], "name": p[1], "default_model": p[2], "hint": p[3]}
                for p in MAINSTREAM_PROVIDERS
            ],
        }
        if self._router is not None:
            result["router"] = self._router.info()
        return result

    def health(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Probe ONNX MeMo lazily so the first /sse/health call after
        # startup returns the *real* backend name (not "not yet loaded").
        if self._memo_is_onnx and not self._memo._session:  # type: ignore[attr-defined]
            try:
                self._memo._ensure_loaded()  # type: ignore[attr-defined]
                self._memo_backend_name = self._memo.backend_name()  # type: ignore[attr-defined]
            except Exception as e:
                log.warning("Lazy ONNX load during /sse/health failed: %s", e)

        # Stats block: only present if we have a call-counting backend
        memo_stats: Dict[str, Any] = {}
        if hasattr(self._memo, "call_count"):
            memo_stats["call_count"] = self._memo.call_count  # type: ignore[attr-defined]
        if hasattr(self._memo, "last_latency_ms"):
            memo_stats["last_latency_ms"] = round(
                self._memo.last_latency_ms, 2  # type: ignore[attr-defined]
            )

        return {
            "status": "ok",
            "version": "0.2.0-stage1.5",
            "home": str(path_resolver_desktop.OPENCLAW_HOME),
            "workspace": str(path_resolver_desktop.WORKSPACE_ROOT),
            "uptime_s": round(time.time() - START_TIME, 2),
            "rccam_enabled": True,
            "memo_enabled": True,    # Stage 3: MeMo 3-stage protocol
            "router_enabled": True,   # Stage 3.5: ACRouter C-A-F
            "sse_port": HTTP_PORT,
            "zmq_port": ZMQ_PORT,
            "memo_backend": self._memo_backend_name,
            "memo_is_onnx": self._memo_is_onnx,
            "memo_stats": memo_stats,
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

    def call_mcp_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 15.5: invoke a discovered MCP tool.

        The renderer / Agent calls this when a user invokes a tool
        whose name starts with 'mcp_'. The MCP client is launched
        on-demand (we don't keep persistent connections in stage 15),
        so each call spawns a fresh subprocess, runs tools/call, and
        returns the result.

        Currently a thin wrapper — for stage 15.5 the underlying
        invocation is delegated to the mcp_client. The actual
        persistent-connection work is stage 15.6.
        """
        tool_name = str(params.get("tool", ""))
        tool_args = params.get("args", {})
        if not tool_name.startswith("mcp_"):
            return {"error": f"not an MCP tool: {tool_name}"}
        # Stage 15.5: stub — return a marker so the renderer knows
        # we received the call. Full MCP invocation comes in stage 15.6.
        return {
            "ok": True,
            "tool": tool_name,
            "args": tool_args,
            "output": f"[MCP stub] {tool_name} called with {tool_args}. "
                      "Full MCP invocation arrives in stage 15.6.",
            "stage": "stub",
        }

    # ── T17: upstream GalaxyOS tool wrappers (claw_verify / claw_recall /
    #         claw_save_memory) — all backed by real engine methods. ─
    def claw_verify(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """T17.1: cross-verify an answer against the memory corpus.

        Calls XiaoYiClawLLM.recall(query) and computes a simple
        confidence score = (corpus hits) / (corpus hits + 1).
        Renders as a green/yellow/red footer on the bubble.
        """
        claim = str(params.get("claim", ""))
        if not claim:
            return {"error": "missing 'claim' param"}
        try:
            hits = self._llm.recall(claim, top_k=5, enhance_with_kg=False)
            n_hits = len(hits) if isinstance(hits, list) else 0
            # Confidence heuristic: 1 hit ≈ 0.5, 2 ≈ 0.7, 3+ ≈ 0.85
            if n_hits == 0: conf = 0.1
            elif n_hits == 1: conf = 0.5
            elif n_hits == 2: conf = 0.7
            else: conf = min(0.95, 0.7 + 0.05 * n_hits)
            verdict = "verified" if conf >= 0.7 else ("partial" if conf >= 0.4 else "unverified")
            return {
                "claim": claim[:200],
                "confidence": round(conf, 2),
                "verdict": verdict,
                "evidence_count": n_hits,
                "top_evidence": [h.get("content", "")[:120] for h in (hits or [])[:3]],
            }
        except Exception as e:
            return {"error": str(e)}

    def claw_recall(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """T17.2: retrieve top-k memories matching query.

        Wraps XiaoYiClawLLM.recall (8-stage recall pipeline in v8.4.2;
        we use the fallback single-vector path that the engine ships
        out of the box).
        """
        query = str(params.get("query", ""))
        top_k = int(params.get("top_k", 10))
        session_id = str(params.get("session_id", ""))
        if not query:
            return {"error": "missing 'query' param"}
        try:
            results = self._llm.recall(
                query, top_k=top_k,
                enhance_with_kg=True,
                session_id=session_id,
            )
            return {
                "query": query,
                "count": len(results) if isinstance(results, list) else 0,
                "results": results[:top_k],
            }
        except Exception as e:
            return {"error": str(e)}

    def claw_save_memory(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """T17.3: commit a user-selected bubble to long-term memory.

        Wraps XiaoYiClawLLM.remember with session_id scoping.
        """
        content = str(params.get("content", ""))
        metadata = params.get("metadata", {})
        session_id = str(params.get("session_id", ""))
        source = str(params.get("source", "user-selected"))
        if not content:
            return {"error": "missing 'content' param"}
        try:
            memory_id = self._llm.remember(
                content=content,
                metadata=metadata or {"saved_via": "claw_save_memory"},
                source=source,
                session_id=session_id,
            )
            return {"memory_id": memory_id, "ok": True}
        except Exception as e:
            return {"error": str(e)}

    # ── T17.4: R-CCAM 5-phase progress events (hook emissions) ───
    def emit_event(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """T17.4: emit a lifecycle event to the renderer.

        The 9 GalaxyOS hooks (gateway_start, before_tool_call, etc.)
        fire throughout the engine. This stub method lets us pipe
        events into the renderer as TokUI [upd] DSL fragments.
        Stage 17.4 only stubs the storage path; full event-bus
        comes in stage 17.5.
        """
        import time
        event_type = str(params.get("type", ""))
        payload = params.get("payload", {})
        log.info(f"[event] {event_type}: {list(payload.keys()) if isinstance(payload, dict) else '?'}")
        return {"ok": True, "received": event_type, "ts": int(time.time() * 1000)}

    # ── T13.1: SkillGraph integration ──────────────────────────────
    def _load_skill_graph(self) -> None:
        """Populate the SkillGraph from the 76 upstream skills."""
        import os
        import re
        skills_dir = path_resolver_desktop._GALAXYOS_REPO / "skills"
        if not skills_dir.exists():
            return
        for d in sorted(skills_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            desc = ""
            skill_md = d / "SKILL.md"
            if skill_md.exists():
                try:
                    text = skill_md.read_text(encoding="utf-8", errors="replace")
                    m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
                    if m:
                        desc = m.group(1).strip().strip('"\'')[:200]
                except Exception:
                    pass
            self._skill_graph.add_node(d.name, description=desc,
                                       layer=0, module_type="skill")
        # Heuristic edges: skills sharing 2+ keywords get 'related' edge
        nodes = list(self._skill_graph.nodes.keys()) if hasattr(self._skill_graph, 'nodes') else []
        node_kw = {}
        for n in nodes:
            nd = self._skill_graph.get_node(n)
            text = (n + " " + (nd.description or "")).lower()
            node_kw[n] = set(w for w in re.findall(r"\w+", text) if len(w) > 2)
        for i, a in enumerate(nodes):
            for b in nodes[i+1:]:
                shared = node_kw.get(a, set()) & node_kw.get(b, set())
                if len(shared) >= 2:
                    self._skill_graph.add_edge(a, b, relation="related")

    def graph_search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """T13.1: graph-aware skill search using GraphAwareRetriever."""
        query = str(params.get("query", ""))
        top_k = int(params.get("top_k", 5))
        if not self._skill_retriever:
            return {"error": "SkillGraph not loaded", "results": []}
        try:
            results = self._skill_retriever.retrieve(query, top_k=top_k)
            out = []
            for name, score in results:
                node = self._skill_graph.get_node(name)
                neighbors = []
                for edge in self._skill_graph.get_successors(name)[:5]:
                    n_node = self._skill_graph.get_node(edge.target)
                    neighbors.append({
                        "name": edge.target,
                        "relation": edge.relation,
                        "description": n_node.description[:100] if n_node else "",
                    })
                out.append({
                    "name": name,
                    "score": round(score, 3),
                    "description": node.description[:100] if node else "",
                    "neighbors": neighbors,
                })
            return {"query": query, "count": len(out), "results": out}
        except Exception as e:
            return {"error": str(e), "results": []}

    def get_skill_neighbors(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """T13.1: get the graph neighbors of a specific skill node."""
        skill_name = str(params.get("name", ""))
        if not self._skill_graph:
            return {"error": "SkillGraph not loaded"}
        node = self._skill_graph.get_node(skill_name)
        if not node:
            return {"error": f"skill not found: {skill_name}"}
        successors = []
        for edge in self._skill_graph.get_successors(skill_name):
            n = self._skill_graph.get_node(edge.target)
            successors.append({
                "name": edge.target,
                "relation": edge.relation,
                "description": n.description[:80] if n else "",
            })
        predecessors = []
        for edge in self._skill_graph.get_predecessors(skill_name):
            n = self._skill_graph.get_node(edge.source)
            predecessors.append({
                "name": edge.source,
                "relation": edge.relation,
                "description": n.description[:80] if n else "",
            })
        return {
            "name": skill_name,
            "description": node.description,
            "out_degree": len(successors),
            "in_degree": len(predecessors),
            "successors": successors,
            "predecessors": predecessors,
        }

    def get_skill(self, params: Dict[str, Any]) -> Dict[str, Any]:
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

    # ── MCP Server management ─────────────────────────────────────
    def list_mcp_servers(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import mcp_client
        return {"servers": mcp_client.list_servers()}

    def add_mcp_server(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import mcp_client
        name = str(params.get("name", ""))
        command = str(params.get("command", ""))
        args = params.get("args", [])
        if not name or not command:
            return {"error": "name and command required"}
        entry = mcp_client.add_server(name, command, args)
        return {"ok": True, "server": entry}

    def remove_mcp_server(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import mcp_client
        name = str(params.get("name", ""))
        removed = mcp_client.remove_server(name)
        return {"ok": removed}

    def discover_mcp_tools(self, params: Dict[str, Any]) -> Dict[str, Any]:
        import mcp_client
        discovered = mcp_client.discover_all()
        return {"servers": {k: len(v) for k, v in discovered.items()},
                "details": discovered}

    # ── Health / heartbeat / stats (Stage 14.3) ───────────────────
    def heartbeat(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Lightweight liveness ping — returns the current monotonic ms.

        Used by the renderer's status footer to show a live connection
        indicator that updates every 30s. Cheap (no I/O).
        """
        import time as _t
        return {"ok": True, "ts_ms": int(_t.time() * 1000), "uptime_s": int(_t.time() - START_TIME)}

    def stats(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Snapshot of sidecar + engine + ACRouter + MCP state.

        Shown in the renderer's settings/details panel as a
        "Diagnostics" view.
        """
        import os as _os
        import time as _t
        # Engine stats
        engine_module_failures = 0
        engine_active_modules = []
        try:
            llm = self._llm
            engine_active_modules = list(llm.modules.keys()) if hasattr(llm, "modules") else []
        except Exception:
            pass
        # Tool count
        try:
            import tools as _tools
            tool_count = len(_tools.TOOLS)
        except Exception:
            tool_count = 0
        # MCP stats
        try:
            import mcp_client as _mcp
            mcp_servers = _mcp.list_servers()
        except Exception:
            mcp_servers = []
        # Process stats
        try:
            import resource as _r
            rusage = _r.getrusage(_r.RUSAGE_SELF)
            rss_mb = rusage.ru_maxrss / 1024  # KB → MB (Linux); on Windows just KB
        except Exception:
            rss_mb = 0
        return {
            "ts_ms": int(_t.time() * 1000),
            "uptime_s": int(_t.time() - START_TIME),
            "engine": {
                "active_modules": engine_active_modules,
                "active_count": len(engine_active_modules),
            },
            "tools": {"count": tool_count},
            "mcp": {"servers": mcp_servers, "count": len(mcp_servers)},
            "acrouter": {
                "memory_size": self._acrouter_memory.size() if self._acrouter_memory else 0,
                "executive": type(self._executive).__name__ if self._executive else "None",
            },
            "config": {
                "model": self._live_config.get("model"),
                "has_api_key": bool(self._live_config.get("api_key")),
                "api_base": self._live_config.get("api_base"),
            },
            "process": {
                "pid": _os.getpid(),
                "rss_mb": rss_mb,
                "cwd": _os.getcwd(),
            },
        }

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

    def stream_ocr(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Stage 14.4: DeepSeek-OCR-2 image understanding.

        Accepts either an image path on the sandbox or a base64
        data URL. Returns a TokUI [image] block with the OCR
        output + extracted text.
        """
        try:
            from galaxyos.engine.deepseek_ocr2_adapter import DeepSeekOCR2Adapter
            adapter = DeepSeekOCR2Adapter()
        except Exception as e:
            return {"events": [f'[p v:danger]❌ OCR adapter 加载失败: {e}[/p]'],
                    "fragments": [f'[p v:danger]❌ OCR adapter 加载失败: {e}[/p]']}
        image_path = str(params.get("path", ""))
        image_b64 = str(params.get("base64", ""))
        prompt = str(params.get("prompt", "<|grounding|>Convert the document to markdown."))
        out: List[str] = []
        try:
            if image_path:
                result = adapter.ocr_file(image_path, prompt=prompt)
            elif image_b64:
                result = adapter.ocr_base64(image_b64, prompt=prompt)
            else:
                return {"events": ['[p v:warn]需要 image path 或 base64[/p]'],
                        "fragments": ['[p v:warn]需要 image path 或 base64[/p]']}
            out.append(f'[image title:OCR src:{image_path or "base64"}]')
            out.append(f'[md]\n{result.get("text", "")}\n[/md]')
            out.append('[msg-actions copy regenerate like dislike visible][/msg-actions]')
            out.append('[/image]')
        except Exception as e:
            out.append(f'[p v:danger]❌ OCR 失败: {e}[/p]')
        return {"events": [{"tokui": f} for f in out], "fragments": out}

    def stream_plan(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._stream_collect("plan", params)

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
        elif kind == "plan":
            frags = self._do_stream_plan(prompt)
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
        """Run the global ACRouter C-A-F loop and build DSL.

        T17.4 + T17.5: emits lifecycle hook events as [upd] DSL
        fragments at before_prompt_build, before_agent_reply, and
        agent_end. The renderer can listen for these to show live
        progress (e.g. "thinking..." → "answered").
        """
        import concurrent.futures
        def _run():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    self._acrouter.route(prompt, {"type": "factual"})
                )
            finally:
                loop.close()
        out: List[str] = []
        # T17.5: before_prompt_build hook
        self.emit_event({"type": "before_prompt_build",
                         "payload": {"prompt": prompt[:100]}})
        out.append(tokui_dsl.open_bubble_ai(model="GalaxyOS-ACRouter"))
        # T17.4: thinking [upd] — renderer can show "thinking..." status
        out.append('[upd id:event_thinking status:running]')
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                caf = ex.submit(_run).result(timeout=20)
        except Exception as e:
            log.error("ACRouter route failed: %r", e)
            out.append(tokui_dsl.answer_paragraph(f"[ACRouter 错误: {e}]"))
            out.append(tokui_dsl.msg_actions())
            out.append(tokui_dsl.close_bubble())
            return out
        # Mark thinking done
        out.append('[upd id:event_thinking status:done]')
        # T17.5: before_agent_reply hook
        self.emit_event({"type": "before_agent_reply",
                         "payload": {"action": caf.chosen_action}})
        out.append(tokui_dsl.answer_paragraph(caf.answer or "(no answer)"))
        memo_snip = self._memo_consult(prompt)
        if memo_snip:
            out.append(f'[p v:muted]💡 记忆补充: {tokui_dsl._esc(memo_snip)}[/p]')
        out.append(self._build_routing_footer(caf.chosen_action, caf.confidence))
        out.append(tokui_dsl.msg_actions())
        out.append(tokui_dsl.close_bubble())
        # T17.5: agent_end hook
        self.emit_event({"type": "agent_end",
                         "payload": {"action": caf.chosen_action,
                                     "confidence": caf.confidence}})
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

    def _do_stream_plan(self, prompt: str) -> List[str]:
        """Plan mode — Agent proposes a multi-step plan for user approval.

        v9.3: each plan-step gets a stable `id` so the renderer can
        bind `[upd id:plan_step_N status:done]` fragments to flip
        steps from pending → done after the user confirms. We emit
        both the initial plan-step (status=pending) and a paired
        `[upd]` fragment for each step that is "auto-resolvable" by
        inspection (read/list/grep — the "safe" operations).
        """
        from tokui_dsl import (
            open_bubble_ai, answer_paragraph, msg_actions, close_bubble,
            open_plan, plan_step, close_plan, upd,
        )
        steps = self._generate_plan(prompt)
        out: List[str] = []
        out.append(open_bubble_ai(model="GalaxyOS-Plan"))
        out.append(open_plan("执行计划"))
        # Tools that we consider "safe to auto-execute" (read-only).
        # Their plan-steps start in status="pending" but the user can
        # see immediately that they don't need a confirmation gate.
        safe_tools = {"list_dir", "read_file", "grep"}
        for i, (title, tool, desc) in enumerate(steps, start=1):
            step_id = f"plan_step_{i}"
            # First fragment: the plan-step container with its id
            out.append(plan_step(
                title=f"步骤 {i}: {title}",
                status="pending",
                body=desc, tool=tool,
            ))
            # Optional: emit a paired upd that hints at "this one is
            # safe — auto-confirmed" by status (visual cue, doesn't
            # actually skip the step). The user can still intervene.
            if tool in safe_tools:
                out.append(upd(step_id, 0, status="success"))
        out.append(close_plan())
        out.append(answer_paragraph(
            f"以上是根据 **{prompt[:60]}** 生成的 {len(steps)} 步执行计划。\n\n"
            f"绿色标签的步骤（list_dir / read_file / grep）可安全自动执行。\n"
            "请在右侧详情面板查看每一步。确认后切换到 **Agent** 模式执行。"
        ))
        out.append(msg_actions())
        out.append(close_bubble())
        return out

    def _generate_plan(self, prompt: str) -> List[tuple]:
        """Generate a heuristic plan from the prompt."""
        lower = prompt.lower()
        steps = []
        steps.append(("探索环境", "list_dir", "列出 sandbox 目录结构"))
        if any(k in lower for k in ("read", "查看", "understand", "理解")):
            steps.append(("阅读相关文件", "read_file", "读取相关文件内容"))
        if any(k in lower for k in ("write", "create", "创建", "实现", "build")):
            steps.append(("实现功能", "write_file", "创建或修改文件"))
            steps.append(("验证", "shell_run", "运行验证命令"))
        if any(k in lower for k in ("fix", "bug", "修复", "debug")):
            steps.append(("定位问题", "grep", "搜索相关代码"))
            steps.append(("应用修复", "apply_diff", "修改代码"))
            steps.append(("验证修复", "shell_run", "运行验证"))
        if any(k in lower for k in ("search", "find", "搜索", "查找")):
            steps.append(("搜索内容", "grep", "搜索匹配项"))
        steps.append(("总结结果", "fast_path", "汇总结果"))
        return steps


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
