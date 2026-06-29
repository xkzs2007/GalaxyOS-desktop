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
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
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
    class so behavior stays consistent."""

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
        }

    def quit(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"bye": True}

    # ── Streaming methods (SSE) — yield DSL fragments ────────────
    def stream_ask(self, question: str, session_id: str = "") -> List[str]:
        """Convert an ask() result into a stream of TokUI DSL fragments."""
        try:
            result = self.ask({"question": question, "session_id": session_id})
            # ask() returns a flat {answer, confidence, memory_ids} — wrap
            # it in a process()-shape so the same DSL builder works.
            wrapped = {
                "answer": result.get("answer", ""),
                "confidence": result.get("confidence"),
                "thinking_skills_used": ["recall"],
                "rccam_phase_states": {
                    "retrieval": {"duration_ms": 0, "sources": 0},
                    "cognition": {"duration_ms": 0, "skills": 0},
                    "control": {"duration_ms": 0},
                    "action": {"duration_ms": 0, "tokens": 0},
                    "memory": {"duration_ms": 0, "wrote": 0},
                },
            }
            return tokui_dsl.process_result_to_fragments(wrapped)
        except Exception as e:
            log.error("stream_ask failed: %s", e)
            log.error(traceback.format_exc())
            return tokui_dsl.stream_error(f"ask 失败: {e}")

    def stream_process(self, user_input: str, session_id: str = "") -> List[str]:
        """Convert a process() result into TokUI DSL fragments.

        Stage 1.5: process() runs to completion first, then we emit all
        fragments at once. This is *not yet* true streaming — Stage 2
        will hook into ``rccam_phase_states`` to emit ``[upd]`` events
        as each phase completes.
        """
        try:
            result = self.process({
                "user_input": user_input,
                "session_id": session_id,
            })
            return tokui_dsl.process_result_to_fragments(result)
        except Exception as e:
            log.error("stream_process failed: %s", e)
            log.error(traceback.format_exc())
            return tokui_dsl.stream_error(f"process 失败: {e}")


# ── HTTP SSE server (asyncio, no extra dep) ───────────────────────────

def _format_sse(event: str, data: str) -> bytes:
    """Format one Server-Sent Event frame."""
    lines = [f"event: {event}"]
    for chunk_line in data.split("\n"):
        lines.append(f"data: {chunk_line}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")


async def _handle_sse_ask(handlers: SidecarHandlers, params: Dict[str, str], send) -> None:
    question = params.get("prompt", [""])[0]
    session_id = params.get("session_id", [""])[0]
    fragments = handlers.stream_ask(question=question, session_id=session_id)
    for frag in fragments:
        await send(_format_sse("tokui", json.dumps({"tokui": frag})))
        await asyncio.sleep(0)  # yield to event loop so the client gets each frame
    await send(b"event: end\ndata: [DONE]\n\n")


async def _handle_sse_process(handlers: SidecarHandlers, params: Dict[str, str], send) -> None:
    user_input = params.get("user_input", [""])[0]
    session_id = params.get("session_id", [""])[0]
    fragments = handlers.stream_process(user_input=user_input, session_id=session_id)
    for frag in fragments:
        await send(_format_sse("tokui", json.dumps({"tokui": frag})))
        await asyncio.sleep(0)
    await send(b"event: end\ndata: [DONE]\n\n")


async def _handle_sse_health(handlers: SidecarHandlers, params: Dict[str, str], send) -> None:
    h = handlers.health({})
    body = json.dumps(h, ensure_ascii=False)
    await send(_format_sse("health", body))
    await send(b"event: end\ndata: [DONE]\n\n")


def _parse_query(qs: str) -> Dict[str, List[str]]:
    """Parse a URL query string into {key: [values]} without urllib."""
    out: Dict[str, List[str]] = {}
    if not qs:
        return out
    for pair in qs.split("&"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        # + → space, %xx → char
        v = v.replace("+", " ")
        try:
            from urllib.parse import unquote
            v = unquote(v)
        except Exception:
            pass
        out.setdefault(k, []).append(v)
    return out


async def _read_post_body(reader, content_length: int, max_bytes: int = 64 * 1024) -> Dict[str, str]:
    """Read a URL-encoded POST body and return as flat {key: value} dict.

    Note: stdlib ``asyncio.StreamReader`` does NOT expose the HTTP
    headers — they were consumed by the protocol's ``connection_made``
    callback before our handler is invoked. Callers must parse the
    ``Content-Length`` header from the raw header block (see
    ``_http_server``) and pass it in.
    """
    if content_length <= 0 or content_length > max_bytes:
        return {}
    raw = await reader.readexactly(content_length)
    out: Dict[str, str] = {}
    for p in raw.decode("utf-8", "replace").split("&"):
        if "=" in p:
            k, v = p.split("=", 1)
        else:
            k, v = p, ""
        v = v.replace("+", " ")
        try:
            from urllib.parse import unquote
            v = unquote(v)
        except Exception:
            pass
        out[k] = v
    return out


async def _http_server(handlers: SidecarHandlers) -> None:
    """Minimal asyncio HTTP/1.1 server, SSE-only routes.

    Stdlib only — no aiohttp / fastapi dependency. Handles just the 3
    SSE routes plus a 404 fallback.
    """
    async def handler(reader, writer):
        try:
            # Parse request line
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return
            try:
                method, path_query, _ = request_line.decode("latin-1").split(" ", 2)
            except ValueError:
                writer.close()
                return
            path, _, qs = path_query.partition("?")

            # Parse headers (we only need Content-Length and friends)
            headers: Dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"", b"\n"):
                    break
                k, _, v = line.decode("latin-1").rstrip("\r\n").partition(":")
                headers[k.strip().lower()] = v.strip()

            log.debug("HTTP %s %s", method, path)

            # CORS preflight (always allow localhost)
            if method == "OPTIONS":
                writer.write(_http_response(204, {"Access-Control-Allow-Origin": "*",
                                                  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                                                  "Access-Control-Allow-Headers": "Content-Type"}))
                await writer.drain()
                writer.close()
                return

            # Read body for POSTs
            body: Dict[str, str] = {}
            if method == "POST":
                cl = int(headers.get("content-length", "0") or "0")
                body = await _read_post_body(reader, cl)

            # Route
            params = {**_parse_query(qs), **body}

            # SSE routes
            if path == "/sse/ask" and method == "POST":
                await _handle_sse(handlers, lambda frag: _format_sse("tokui", json.dumps({"tokui": frag})),
                                  writer, lambda: handlers.stream_ask(
                                      question=params.get("prompt", [""])[0],
                                      session_id=params.get("session_id", [""])[0],
                                  ))
            elif path == "/sse/process" and method == "POST":
                await _handle_sse(handlers, lambda frag: _format_sse("tokui", json.dumps({"tokui": frag})),
                                  writer, lambda: handlers.stream_process(
                                      user_input=params.get("user_input", [""])[0],
                                      session_id=params.get("session_id", [""])[0],
                                  ))
            elif path == "/sse/health" and method in ("GET", "POST"):
                h = handlers.health({})
                writer.write(_http_response(200, {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                }, body=json.dumps(h, ensure_ascii=False).encode("utf-8")))
            else:
                writer.write(_http_response(404, {"Content-Type": "text/plain",
                                                  "Access-Control-Allow-Origin": "*"},
                                            body=b"not found"))
            await writer.drain()
        except Exception as e:
            log.error("HTTP handler error: %s", e)
            log.error(traceback.format_exc())
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_sse(handlers, frame, writer, fragment_source):
        """SSE handler.

        We hand-write the response head WITHOUT Content-Length so the
        client reads until the writer is closed (signals end-of-stream).
        We also use ``Connection: close`` so the underlying TCP socket
        is torn down when the stream ends — this guarantees the
        browser's fetch() resolves promptly.
        """
        head = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Cache-Control: no-cache\r\n"
            b"Connection: close\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"X-Accel-Buffering: no\r\n"
            b"\r\n"
        )
        writer.write(head)
        await writer.drain()

        try:
            fragments = fragment_source()
            for frag in fragments:
                writer.write(frame(frag))
                await writer.drain()
        except Exception as e:
            log.error("SSE fragment source failed: %s", e)
            log.error(traceback.format_exc())
            # Emit an error event so the client knows
            try:
                err_frag = tokui_dsl.error_bubble(f"stream 失败: {e}")
                for f in err_frag:
                    writer.write(frame(f))
                    await writer.drain()
            except Exception:
                pass
        writer.write(b"event: end\ndata: [DONE]\n\n")
        await writer.drain()
        # Half-close the write side of the TCP socket so the browser
        # sees EOF and the fetch() promise resolves.
        try:
            transport = writer.transport
            if transport and hasattr(transport, "close"):
                # Half-close: writes done, allow read-side close.
                if hasattr(transport, "write_eof"):
                    await transport.write_eof()
        except Exception:
            pass

    server = await asyncio.start_server(handler, SIDECAR_HOST, HTTP_PORT)
    log.info("SSE server listening on http://%s:%d/sse/{ask,process,health}",
             SIDECAR_HOST, HTTP_PORT)
    async with server:
        await server.serve_forever()


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

    # Run HTTP server (this blocks)
    try:
        await _http_server(handlers)
    finally:
        stop.set()
        zmq_thread.join(timeout=2)
        log.info("Sidecar stopped cleanly.")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
