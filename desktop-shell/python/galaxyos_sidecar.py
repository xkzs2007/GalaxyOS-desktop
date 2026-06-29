"""galaxyos_sidecar.py — Python sidecar for the GalaxyOS desktop app.

This is the IPC endpoint the Electron main process talks to. It runs
a single ``XiaoYiClawLLM`` instance and exposes a JSON-RPC-ish API
over pyzmq REP socket on ``tcp://127.0.0.1:5757``.

Why pyzmq (not UDS, not HTTP):

* pyzmq is in ``requirements-core.txt`` — no new dependency.
* Cross-platform: works on Windows / macOS / Linux identically.
* REQ/REP pattern is the simplest reliable request/response model.
* Already used inside GalaxyOS (`xiaoyi_claw_api._rci_publish`).

Methods exposed (JSON request shape: ``{"id": "...", "method": "...", "params": {...}``):

    ask(question: str, session_id?: str) -> {answer, confidence, ...}
    remember(content: str, metadata?: dict, source?: str) -> {memory_id}
    recall(query: str, top_k?: int, session_id?: str) -> {results: [...]}
    process(user_input: str, session_id?: str, has_image?: bool) -> {...}
    health() -> {status, version, home, uptime_s}
    quit() -> {bye: true}  (and shuts down the REP socket)

Design notes:

* Stage 1 (this file): direct method pass-through to ``XiaoYiClawLLM``.
* Stage 2 will add MeMo 3-stage wrapping in ``process()``/``recall()``.
* Stage 3 will wrap everything in an Agent-as-a-Router C-A-F loop and
  return ``routing_debug`` in the result dict.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict

# ── Bootstrap: install path_resolver shim BEFORE any GalaxyOS import ───
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import path_resolver_desktop  # noqa: F401  (auto-installs into sys.modules)

# ── Sidecar config ─────────────────────────────────────────────────────
SIDECAR_HOST = os.environ.get("GALAXYOS_SIDECAR_HOST", "127.0.0.1")
SIDECAR_PORT = int(os.environ.get("GALAXYOS_SIDECAR_PORT", "5757"))
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
    """
    try:
        # First try the canonical public path (init/__init__.py PEP 562 map)
        from galaxyos.init import GalaxyBootstrap, BootstrapContext
        from galaxyos.engine.xiaoyi_claw_api import XiaoYiClawLLM
        return GalaxyBootstrap, BootstrapContext, XiaoYiClawLLM
    except ImportError as e:
        log.error("Failed to import GalaxyOS: %s", e)
        log.error("Did you set GALAXYOS_REPO? Current: %s",
                  os.environ.get("GALAXYOS_REPO"))
        log.error("sys.path head: %s", sys.path[:3])
        raise


# ── Method dispatch ────────────────────────────────────────────────────
class SidecarHandlers:
    """All sidecar RPC handlers live here.

    Stage 1: pass-through to ``XiaoYiClawLLM``. Each method maps
    directly to one public method on the engine class.
    """

    def __init__(self) -> None:
        log.info("Loading GalaxyOS engine (this may take a few seconds)...")
        GalaxyBootstrap, BootstrapContext, XiaoYiClawLLM = _load_engine()
        ctx = BootstrapContext(home=path_resolver_desktop.OPENCLAW_HOME)
        GalaxyBootstrap.apply_context(ctx)
        self._ctx = ctx
        self._llm = XiaoYiClawLLM(config={
            "home": str(path_resolver_desktop.OPENCLAW_HOME),
            "workspace": str(path_resolver_desktop.WORKSPACE_ROOT),
        })
        self._XiaoYiClawLLM = XiaoYiClawLLM
        log.info("Engine ready: XiaoYiClawLLM at %s",
                 path_resolver_desktop.GALAXYOS_ENGINE)

    # ── RPC methods ────────────────────────────────────────────────
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
        result = self._llm.process(
            user_input=params["user_input"],
            max_cycles=int(params.get("max_cycles", 1)),
            store_memory=bool(params.get("store_memory", True)),
            has_image=bool(params.get("has_image", False)),
            session_key=params.get("session_id", ""),
        )
        return result

    def health(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "ok",
            "version": "0.1.0-stage1",
            "home": str(path_resolver_desktop.OPENCLAW_HOME),
            "workspace": str(path_resolver_desktop.WORKSPACE_ROOT),
            "uptime_s": round(time.time() - START_TIME, 2),
            "rccam_enabled": True,
            "memo_enabled": False,   # stage 2
            "router_enabled": False,  # stage 3
        }

    def quit(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"bye": True}


# ── REP server loop ────────────────────────────────────────────────────
def _run_server(handlers: SidecarHandlers) -> None:
    import zmq

    ctx = zmq.Context.instance()
    socket = ctx.socket(zmq.REP)
    bind_addr = f"tcp://{SIDECAR_HOST}:{SIDECAR_PORT}"
    socket.bind(bind_addr)
    log.info("Sidecar listening on %s (Ctrl-C to quit)", bind_addr)

    # Graceful shutdown on SIGTERM / SIGINT
    _shutdown_requested = {"flag": False}

    def _handle_signal(signum, frame):
        log.info("Signal %d received, shutting down...", signum)
        _shutdown_requested["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    # Make sure HOME and required dirs exist (idempotent, lazy)
    for d in (
        path_resolver_desktop.OPENCLAW_HOME,
        path_resolver_desktop.WORKSPACE_ROOT,
        path_resolver_desktop.MODELS_DIR,
        path_resolver_desktop.ROUTER_MEMORY_DIR,
        path_resolver_desktop.HEARTBEAT_DIR,
        path_resolver_desktop.DESKTOP_LOGS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)

    poller = zmq.Poller()
    poller.register(socket, zmq.POLLIN)

    while not _shutdown_requested["flag"]:
        socks = poller.poll(timeout=500)  # 500ms
        if not socks:
            continue
        if socket not in dict(socks):
            continue

        raw = socket.recv()
        try:
            req = json.loads(raw)
            method = req.get("method")
            req_id = req.get("id")
            params = req.get("params") or {}
        except Exception as e:
            log.error("Bad request: %s", e)
            socket.send_json({"id": None, "error": f"bad json: {e}"})
            continue

        if not hasattr(handlers, method):
            socket.send_json({
                "id": req_id,
                "error": f"unknown method: {method}",
            })
            log.warning("Unknown method: %s", method)
            continue

        try:
            log.debug("RPC call: %s(%s)", method,
                      ", ".join(f"{k}={v!r:.40}" for k, v in params.items()))
            result = getattr(handlers, method)(params)
            socket.send_json({"id": req_id, "result": result})
        except Exception as e:
            log.error("Handler error in %s: %s", method, e)
            log.error(traceback.format_exc())
            socket.send_json({
                "id": req_id,
                "error": f"{type(e).__name__}: {e}",
            })

        if method == "quit":
            break

    socket.close(linger=0)
    ctx.term()
    log.info("Sidecar stopped cleanly.")


def main() -> int:
    log.info("=== GalaxyOS Desktop Sidecar ===")
    log.info("HOME     : %s", path_resolver_desktop.OPENCLAW_HOME)
    log.info("WORKSPACE: %s", path_resolver_desktop.WORKSPACE_ROOT)
    log.info("REPO     : %s", path_resolver_desktop._GALAXYOS_REPO)

    try:
        handlers = SidecarHandlers()
    except ImportError as e:
        log.error("Cannot start sidecar: %s", e)
        return 2

    _run_server(handlers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
