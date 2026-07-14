#!/usr/bin/env python3
"""
galaxyos_native — Pure-Python shim (embedded fallback for Rust PyO3 extension)

Provides the same API as the compiled galaxyos_native Rust module:
  resize, enhance, ocr_preprocess, vector_dot, vector_cosine, vector_batch_cosine

When the real compiled .so is available, it takes precedence over this shim.
This ensures import succeeds and all ops work via PIL/numpy without requiring
a Rust toolchain at deploy time.
"""

__version__ = "0.2.0"
__doc__ = "GalaxyOS native extension — PIL replacement + SIMD vector compute + LFM UDS client"
_BACKEND = "python"  # "rust" when compiled PyO3 .so; "python" when shim

import json
import math
import base64
import io
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

# ── LFM IPC Client (跨平台: Unix=UDS, Windows=TCP) ───────────────────

import sys as _sys

_LFM_SOCKET_PATH = None      # Unix: UDS 路径
_LM_TCP_PORT = None          # Windows: TCP 端口
_LFM_PROCESS = None
_LFM_IPC_MODE = None         # "uds" 或 "tcp"，从 ready 消息解析

_IS_WINDOWS = _sys.platform.startswith("win")


def _lfm_socket_path() -> str:
    """Get the default LFM IPC path (Unix: UDS socket; Windows: 占位路径)."""
    if _IS_WINDOWS:
        home = os.environ.get("USERPROFILE", os.environ.get("HOME", "C:\\Users\\Public"))
        return os.path.join(home, ".openclaw", "extensions", "galaxyos", "var", "lfm.tcp")
    home = os.environ.get("HOME", "/root")
    return os.path.join(home, ".openclaw", "extensions", "galaxyos", "var", "lfm.sock")


def _lfm_binary_path() -> Optional[str]:
    """Find the lfm_server binary (跨平台: Linux/macOS/Windows)."""
    exe_suffix = ".exe" if _IS_WINDOWS else ""
    candidates = [
        # 编译产物（开发环境 — debug）
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "native", "target", "debug", f"lfm_server{exe_suffix}"
        ),
        # 安装产物（发布环境 — release）
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "native", "target", "release", f"lfm_server{exe_suffix}"
        ),
        # 绝对路径（release）
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "native", "target", "debug", f"lfm_server{exe_suffix}"
        ),
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def lfm_start(model_dir: Optional[str] = None, uds_path: Optional[str] = None) -> str:
    """Start the LFM server as a subprocess (跨平台).
    
    Args:
        model_dir: Path to LFM2.5-1.2B-ONNX directory
        uds_path: IPC path (Unix: UDS socket; Windows: 未使用, TCP 自动分配)
    
    Returns:
        Unix: UDS socket path; Windows: "tcp://127.0.0.1:{port}"
    """
    global _LFM_PROCESS, _LFM_SOCKET_PATH, _LM_TCP_PORT, _LFM_IPC_MODE

    if _LFM_PROCESS is not None and _LFM_PROCESS.poll() is None:
        # Already running
        if _LFM_IPC_MODE == "tcp" and _LM_TCP_PORT:
            return f"tcp://127.0.0.1:{_LM_TCP_PORT}"
        return _LFM_SOCKET_PATH or _lfm_socket_path()

    binary = _lfm_binary_path()
    if binary is None:
        raise RuntimeError("lfm_server binary not found. Run 'cargo build --bin lfm_server' first.")

    if model_dir is None:
        home = os.environ.get("USERPROFILE" if _IS_WINDOWS else "HOME",
                              os.environ.get("HOME", "/root"))
        workspace = os.environ.get("OPENCLAW_WORKSPACE",
                                   os.path.join(home, ".openclaw", "workspace"))
        model_dir = os.path.join(workspace, "models", "LFM2.5-1.2B-ONNX")

    if uds_path is None:
        uds_path = _lfm_socket_path()

    # Ensure parent dir exists
    os.makedirs(os.path.dirname(uds_path), exist_ok=True)

    # Spawn server via environment variables (not CLI args) to avoid
    # leaking model path and IPC path in `ps aux` output.
    _env = os.environ.copy()
    _env["GALAXYOS_LFM_MODEL_DIR"] = model_dir
    _env["GALAXYOS_LFM_UDS_PATH"] = uds_path
    _LFM_PROCESS = subprocess.Popen(
        [binary],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env
    )

    # Wait for ready signal — 解析 IPC 模式和连接信息
    start_ts = time.time()
    ready_line = None
    while time.time() - start_ts < 30:
        line = _LFM_PROCESS.stdout.readline()  # type: ignore[union-attr]
        if not line:
            # process died
            stderr = _LFM_PROCESS.stderr.read()  # type: ignore[union-attr]
            raise RuntimeError("lfm_server exited early")  # stderr redacted (may contain paths)
        try:
            msg = json.loads(line.strip())
            if msg.get("event") == "ready":
                ready_line = msg
                break
        except json.JSONDecodeError:
            continue

    if ready_line is None:
        raise RuntimeError("lfm_server did not signal ready within 30s")

    # 解析 IPC 模式（从 ready 消息中获取）
    _LFM_IPC_MODE = ready_line.get("ipc", "uds" if not _IS_WINDOWS else "tcp")
    if _LFM_IPC_MODE == "tcp":
        _LM_TCP_PORT = ready_line.get("tcp_port")
        if not _LM_TCP_PORT:
            raise RuntimeError("lfm_server ready but no tcp_port in ready message")
        return f"tcp://127.0.0.1:{_LM_TCP_PORT}"
    else:
        _LFM_SOCKET_PATH = ready_line.get("uds", uds_path)
        return _LFM_SOCKET_PATH


def lfm_stop():
    """Stop the LFM server."""
    global _LFM_PROCESS, _LFM_SOCKET_PATH, _LM_TCP_PORT, _LFM_IPC_MODE
    if _LFM_PROCESS is not None and _LFM_PROCESS.poll() is None:
        # Send shutdown via IPC
        try:
            lfm_request("shutdown", timeout=2)
        except Exception:
            pass
        # Force kill if still alive
        try:
            _LFM_PROCESS.kill()
            _LFM_PROCESS.wait(timeout=5)
        except Exception:
            pass
    _LFM_PROCESS = None
    _LFM_SOCKET_PATH = None
    _LM_TCP_PORT = None
    _LFM_IPC_MODE = None


def _lfm_connect(timeout: float = 10) -> socket.socket:
    """Connect to the LFM server (跨平台: Unix=UDS, Windows=TCP)."""
    if _LFM_IPC_MODE == "tcp" or (_LFM_IPC_MODE is None and _IS_WINDOWS):
        # Windows TCP 模式
        port = _LM_TCP_PORT
        if not port:
            raise RuntimeError("LFM server not running (TCP port not set)")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))
        return sock
    else:
        # Unix UDS 模式
        uds_path = _LFM_SOCKET_PATH or _lfm_socket_path()
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(uds_path)
        return sock


def _lfm_request_blocking(method: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
    """
    Send a JSON-RPC request via IPC (跨平台: UDS 或 TCP).

    Uses raw send/recv instead of makefile+readline because socket.makefile()
    creates an independent BufferedReader that ignores sock.settimeout(),
    causing permanent hangs when the Rust server is slow or locked.

    Error messages are redacted to avoid leaking paths and method names
    in production logs.
    """
    # 检查连接可用性
    if _LFM_IPC_MODE == "tcp" or (_LFM_IPC_MODE is None and _IS_WINDOWS):
        if not _LM_TCP_PORT:
            raise RuntimeError("LFM server not running (TCP port not set)")
    else:
        uds_path = _LFM_SOCKET_PATH or _lfm_socket_path()
        if not os.path.exists(uds_path):
            raise RuntimeError("LFM server not running (UDS socket not found)")

    deadline = time.time() + timeout
    last_error = None

    for attempt in range(3):
        remaining = deadline - time.time()
        if remaining < 1:
            remaining = timeout

        sock = _lfm_connect(min(remaining, timeout))
        try:
            sock.settimeout(min(remaining, timeout))
            req = json.dumps({"id": 1, "method": method, "params": params or {}})
            sock.sendall((req + "\n").encode("utf-8"))

            # Use recv() with timeout instead of makefile().readline()
            # makefile BufferedReader does NOT respect socket timeout
            resp = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                resp += chunk
                if resp.endswith(b"\n"):
                    break

            if not resp:
                raise RuntimeError("LFM RPC empty response")

            response = json.loads(resp.decode("utf-8").strip())
            if "error" in response and response["error"]:
                raise RuntimeError(f"LFM RPC error: {response['error']}")

            return response.get("result")

        except socket.timeout:
            last_error = RuntimeError(f"LFM RPC timeout after {timeout}s")
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise last_error
        except (BrokenPipeError, ConnectionRefusedError, OSError) as _e:
            last_error = RuntimeError(f"LFM connection lost: {type(_e).__name__}")
            if attempt < 2:
                time.sleep(0.2)
                continue
            raise last_error
        finally:
            sock.close()

    raise last_error or RuntimeError("LFM RPC failed after 3 retries")


# ── LFM high-level wrappers ──

def lfm_ping() -> dict:
    """Ping the LFM server."""
    return _lfm_request_blocking("ping")

def lfm_get_info() -> dict:
    """Get LFM server info."""
    return _lfm_request_blocking("get_info")

def lfm_embed_text(input_ids: List[int]) -> List[float]:
    """Embed text (state-free). Returns 2048-dim embedding."""
    result = _lfm_request_blocking("embed_text", {"input_ids": input_ids})
    return result["embedding"]

def lfm_update_state(input_ids: List[int]) -> dict:
    """Feed tokens into stateful inference. Returns dict with total_seq_len."""
    return _lfm_request_blocking("update_state", {"input_ids": input_ids})

def lfm_get_state() -> dict:
    """Get current state info (initialized, total_seq_len, embedding)."""
    return _lfm_request_blocking("get_state")

def lfm_get_hidden(input_ids: List[int], layers: Optional[List[int]] = None) -> dict:
    """Feed tokens and return hidden states for specified layers.
    
    Returns dict like:
        {"present_conv.0": [f32; 2048*3], "present_conv.15": [f32; 2048*3], "embedding": [f32; 2048]}
    """
    if layers is None:
        layers = [0, 15]
    return _lfm_request_blocking("get_hidden", {
        "input_ids": input_ids, "layers": layers
    })

def lfm_reset_state() -> dict:
    """Reset stateful inference (clear conv_states, kv_caches, total_seq)."""
    return _lfm_request_blocking("reset_state")

def lfm_request(method: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
    """Legacy alias. Use the specific wrappers instead."""
    return _lfm_request_blocking(method, params, timeout)

try:
    from PIL import Image, ImageEnhance, ImageFilter
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


def _load_image(data: bytes):
    if not _PIL_AVAILABLE:
        raise RuntimeError("PIL not available")
    img = Image.open(io.BytesIO(data))
    if img.mode == "RGBA":
        img = img.convert("RGB")
    return img


def _encode_image(img, fmt: str) -> str:
    buf = io.BytesIO()
    fmt_lower = fmt.lower()
    save_fmt = fmt_lower.upper() if fmt_lower in ("jpeg", "png", "webp") else "JPEG"
    img.save(buf, format=save_fmt)
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ── Image ops ──────────────────────────────────────────────────

def resize(data: bytes, width: int, height: int, keep_ratio: bool = True, fmt: str = "jpeg"):
    """Resize image. Returns dict with data_b64 and size."""
    img = _load_image(data)
    orig_w, orig_h = img.size
    if keep_ratio:
        ratio = min(width / orig_w, height / orig_h)
        new_w, new_h = int(orig_w * ratio), int(orig_h * ratio)
    else:
        new_w, new_h = width, height
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)  # type: ignore[attr-defined]
    b64 = _encode_image(resized, fmt)
    return {"data_b64": b64, "size": [new_w, new_h]}


def enhance(data: bytes, brightness: float = 1.0, contrast: float = 1.0, sharpness: float = 1.0, fmt: str = "jpeg"):
    """Enhance image. Returns base64-encoded string."""
    img = _load_image(data)
    if abs(brightness - 1.0) > 0.001:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if abs(contrast - 1.0) > 0.001:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if abs(sharpness - 1.0) > 0.001:
        # Simulate sharpness via unsharp mask
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=int((sharpness - 1.0) * 100), threshold=0))
    return _encode_image(img, fmt)


def ocr_preprocess(data: bytes, fmt: str = "png"):
    """OCR preprocess: grayscale + denoise + contrast enhance. Returns base64."""
    img = _load_image(data)
    gray = img.convert("L")
    denoised = gray.filter(ImageFilter.GaussianBlur(0.8))
    enhanced = ImageEnhance.Contrast(denoised).enhance(1.5)
    return _encode_image(enhanced, fmt)


# ── Vector ops ─────────────────────────────────────────────────

def vector_dot(a: List[float], b: List[float]) -> float:
    """Dot product of two float vectors."""
    if len(a) != len(b):
        raise RuntimeError(f"dimension mismatch: {len(a)} vs {len(b)}")
    return sum(x * y for x, y in zip(a, b))


def vector_cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity of two float vectors."""
    if len(a) != len(b):
        raise RuntimeError(f"dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a > 0 and norm_b > 0:
        return dot / (norm_a * norm_b)
    return 0.0


def vector_batch_cosine(query: List[float], candidates: List[List[float]]) -> List[float]:
    """Batch cosine similarity: query vs each candidate."""
    return [vector_cosine(query, c) for c in candidates]
