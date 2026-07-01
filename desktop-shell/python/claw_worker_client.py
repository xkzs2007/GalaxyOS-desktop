"""
claw_worker_client.py — Thin UDS/TCP client for claw_worker.py JSON-RPC.

Drop-in replacement for direct XiaoYiClawLLM calls in galaxyos_sidecar.py.
Connects to claw_worker via HTTP over UDS (Unix) or HTTP over TCP (Windows).
"""

import json
import os
import socket
import time
from http.client import HTTPConnection
from pathlib import Path
from typing import Any, Dict, Optional
import sys

_IS_WINDOWS = sys.platform.startswith("win")

# ── default UDS / TCP paths ────────────────────────────────────────

def _default_var_dir() -> Path:
    home = Path(os.environ.get(
        "OPENCLAW_HOME",
        os.path.expanduser("~/.openclaw"),
    ))
    return home / "extensions" / "galaxyos" / "var"


def _default_uds_path(worker_id: str = "worker:1") -> Path:
    var = _default_var_dir()
    if worker_id and worker_id != "worker:default":
        safe_id = worker_id.replace(":", "-")
        return var / f"claw-worker-{safe_id}.sock"
    return var / "claw-worker.sock"


class ClawWorkerUdsClient:
    """HTTP-over-UDS (Unix) or HTTP-over-TCP (Windows) client for claw_worker.py.

    Usage:
        worker = ClawWorkerUdsClient()
        worker.start()
        result = worker.call("recall", {"query": "...", "top_k": 5})
        print(result)
        worker.stop()
    """

    RESPECTED_METHODS = frozenset({
        "ping", "health", "recall", "smart_retrieval", "memory_search",
        "memory_status", "store", "save_memory", "remember", "forget",
        "learn", "learn_preference", "learn_correction",
        "get_entity", "verify", "rccam",
        "dag_ingest", "dag_assemble", "dag_compact",
        "dag_summary", "dag_status", "dag_clear_session",
        "dag_search", "context_assemble",
        "understand_image", "ocr_image", "recall_images",
        "answer", "smart_process", "rlm_compress",
        "build_system_prompt", "verify_reply_style",
        "implicit_feedback", "restore_context",
        "persona_snapshot", "get_persona_core",
        "execute_workflow", "list_workflows", "get_workflow_info",
        "call_module", "list_modules", "get_module_info",
        "hardinfo", "vector_info", "get_status",
        "rccam_dag_stats", "rccam_compact_needed", "rccam_compact_cycle",
        "expand_rccam_cycle", "cognitive_compress_dag",
        "mmap_cleanup", "shutdown",
    })

    def __init__(
        self,
        uds_path: Optional[str] = None,
        tcp_port: Optional[int] = None,
        worker_id: str = "worker:1",
        timeout: float = 30.0,
    ):
        self._uds_path = uds_path or str(_default_uds_path(worker_id))
        self._tcp_port = tcp_port
        self._worker_id = worker_id
        self._timeout = timeout
        self._ready = False
        self._mode: Optional[str] = None  # 'uds' | 'tcp'
        self._next_id = 0

    # ── start / stop ───────────────────────────────────────────────

    def start(self, timeout: float = 15.0) -> None:
        """Connect to the claw_worker process. Tries UDS first, then TCP,
        then raises if neither works."""
        if self._ready:
            return

        deadline = time.time() + timeout

        # Priority: env var → UDS probe → TCP probe
        env_path = os.environ.get("GALAXYOS_UDS_PATH")
        if env_path and os.path.exists(env_path):
            self._uds_path = env_path

        # Try UDS (Unix)
        if not _IS_WINDOWS:
            while time.time() < deadline:
                if os.path.exists(self._uds_path):
                    try:
                        self._probe_uds(self._uds_path)
                        self._mode = "uds"
                        self._ready = True
                        return
                    except Exception:
                        pass
                time.sleep(0.3)
        else:
            # Windows: try TCP (read port file)
            port_file = str(Path(self._uds_path).with_suffix(".port"))
            tcp_port = self._tcp_port
            if not tcp_port:
                try:
                    if os.path.exists(port_file):
                        with open(port_file) as f:
                            tcp_port = int(f.read().strip())
                except Exception:
                    pass

            if tcp_port:
                while time.time() < deadline:
                    try:
                        self._probe_tcp(tcp_port)
                        self._mode = "tcp"
                        self._tcp_port = tcp_port
                        self._ready = True
                        return
                    except Exception:
                        pass
                    time.sleep(0.3)

        raise RuntimeError(
            f"claw_worker not reachable within {timeout}s "
            f"(uds={self._uds_path}, port={self._tcp_port})"
        )

    def stop(self) -> None:
        self._ready = False
        self._mode = None

    # ── probe helpers ──────────────────────────────────────────────

    def _probe_uds(self, path: str) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        try:
            sock.connect(path)
            sock.sendall(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
            resp = sock.recv(1024).decode()
            if "HTTP/1." not in resp:
                raise RuntimeError("not HTTP")
        finally:
            sock.close()

    def _probe_tcp(self, port: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        try:
            sock.connect(("127.0.0.1", port))
            sock.sendall(b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
            resp = sock.recv(1024).decode()
            if "HTTP/1." not in resp:
                raise RuntimeError("not HTTP")
        finally:
            sock.close()

    # ── RPC call ───────────────────────────────────────────────────

    def call(self, method: str, params: Optional[Dict[str, Any]] = None,
             timeout: Optional[float] = None) -> Any:
        if not self._ready:
            raise RuntimeError("ClawWorkerUdsClient not started")

        if method not in self.RESPECTED_METHODS:
            raise ValueError(f"Unknown method: {method}")

        params = params or {}
        self._next_id += 1
        req_id = self._next_id
        body = json.dumps({"id": req_id, "method": method, "params": params})

        timeout = timeout or self._timeout

        try:
            if self._mode == "uds":
                return self._http_call_uds(self._uds_path, body, timeout)
            elif self._mode == "tcp":
                return self._http_call_tcp(self._tcp_port, body, timeout)
            else:
                raise RuntimeError(f"Unknown mode: {self._mode}")
        except Exception as e:
            raise RuntimeError(
                f"claw_worker call '{method}' failed: {e}"
            ) from e

    # ── HTTP transport ─────────────────────────────────────────────

    def _http_call_uds(self, sock_path: str, body: str, timeout: float) -> Any:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(sock_path)
            req = (
                f"POST / HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
                f"{body}"
            )
            sock.sendall(req.encode())

            # Read response
            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
                if b"\r\n\r\n" in data:
                    # Try to parse headers to get Content-Length
                    header_end = data.index(b"\r\n\r\n") + 4
                    headers_text = data[:header_end].decode(errors="replace")
                    body_start = header_end
                    # Simple Content-Length extraction
                    content_length = None
                    for line in headers_text.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                content_length = int(line.split(":", 1)[1].strip())
                            except Exception:
                                pass
                    if content_length is not None:
                        remaining = content_length - (len(data) - body_start)
                        while remaining > 0:
                            chunk = sock.recv(min(65536, remaining))
                            if not chunk:
                                break
                            data += chunk
                            remaining -= len(chunk)
                        break
        finally:
            sock.close()

        # Parse HTTP response
        if b"\r\n\r\n" not in data:
            raise RuntimeError("Invalid HTTP response from worker")

        header_end = data.index(b"\r\n\r\n") + 4
        json_body = data[header_end:].decode(errors="replace").strip()

        if not json_body:
            raise RuntimeError("Empty response from worker")

        try:
            msg = json.loads(json_body)
        except json.JSONDecodeError:
            raise RuntimeError(f"Non-JSON response: {json_body[:200]}")

        if msg.get("error"):
            err = msg["error"]
            raise RuntimeError(
                err if isinstance(err, str) else json.dumps(err)
            )

        return msg.get("result")

    def _http_call_tcp(self, port: int, body: str, timeout: float) -> Any:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(("127.0.0.1", port))
            req = (
                f"POST / HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
                f"{body}"
            )
            sock.sendall(req.encode())

            data = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
                if b"\r\n\r\n" in data:
                    header_end = data.index(b"\r\n\r\n") + 4
                    body_start = header_end
                    content_length = None
                    headers_text = data[:header_end].decode(errors="replace")
                    for line in headers_text.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                content_length = int(line.split(":", 1)[1].strip())
                            except Exception:
                                pass
                    if content_length is not None:
                        remaining = content_length - (len(data) - body_start)
                        while remaining > 0:
                            chunk = sock.recv(min(65536, remaining))
                            if not chunk:
                                break
                            data += chunk
                            remaining -= len(chunk)
                        break
        finally:
            sock.close()

        if b"\r\n\r\n" not in data:
            raise RuntimeError("Invalid HTTP response from worker")

        header_end = data.index(b"\r\n\r\n") + 4
        json_body = data[header_end:].decode(errors="replace").strip()

        if not json_body:
            raise RuntimeError("Empty response from worker")

        try:
            msg = json.loads(json_body)
        except json.JSONDecodeError:
            raise RuntimeError(f"Non-JSON response: {json_body[:200]}")

        if msg.get("error"):
            err = msg["error"]
            raise RuntimeError(
                err if isinstance(err, str) else json.dumps(err)
            )

        return msg.get("result")
