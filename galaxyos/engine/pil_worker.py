#!/usr/bin/env python3
"""
pil_worker — 独立 PIL 图像处理子进程

设计目标:
- 完全隔离于主 Worker 进程，零 GIL 竞争
- 长驻进程，PIL/Image 只 import 一次
- stdin/stdout JSON-RPC 协议，主进程 fork 后通信
- 超时 8s，失败返回 error 而非阻塞

协议（每行一个 JSON）:
  Request:  {"id":1, "method":"resize", "params":{"data_b64":"...", "width":800, "height":600}}
  Response: {"id":1, "result":{"data_b64":"..."}, "timing_ms":42}
  Error:    {"id":1, "error":"..."}
  Ready:    {"id":0, "event":"ready", "pid":12345}

支持方法: resize | enhance | ocr_preprocess | ping | shutdown
"""

import sys
import os
import io
import json
import time
import base64
import signal
from typing import Any

# ── 启动时一次性导入 PIL，后续复用 ──
try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def _b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)


def _bytes_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# ── 方法实现 ──

def resize(params: dict) -> dict:
    data_b64 = params.get("data_b64", "")
    width = int(params.get("width", 800))
    height = int(params.get("height", 600))
    keep_ratio = params.get("keep_ratio", True)
    fmt = params.get("fmt", "JPEG")

    data = _b64_to_bytes(data_b64)
    img: Any = Image.open(io.BytesIO(data))
    if img.mode == "RGBA":
        img = img.convert("RGB")  # type: ignore[assignment]

    w, h = img.size
    if keep_ratio:
        ratio = min(width / w, height / h)
        new_w, new_h = int(w * ratio), int(h * ratio)
    else:
        new_w, new_h = width, height

    img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)  # type: ignore[attr-defined, assignment]
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=85)
    result_bytes = buf.getvalue()
    return {"data_b64": _bytes_to_b64(result_bytes), "size": [new_w, new_h]}


def enhance(params: dict) -> dict:
    data_b64 = params.get("data_b64", "")
    brightness = float(params.get("brightness", 1.0))
    contrast = float(params.get("contrast", 1.0))
    sharpness = float(params.get("sharpness", 1.0))
    fmt = params.get("fmt", "JPEG")

    data = _b64_to_bytes(data_b64)
    img: Any = Image.open(io.BytesIO(data))
    if img.mode == "RGBA":
        img = img.convert("RGB")  # type: ignore[assignment]

    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)  # type: ignore[assignment]
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)  # type: ignore[assignment]
    if sharpness != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(sharpness)  # type: ignore[assignment]

    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=90)
    return {"data_b64": _bytes_to_b64(buf.getvalue())}


def ocr_preprocess(params: dict) -> dict:
    data_b64 = params.get("data_b64", "")
    fmt = params.get("fmt", "PNG")

    data = _b64_to_bytes(data_b64)
    img: Any = Image.open(io.BytesIO(data))
    if img.mode == "RGBA":
        img = img.convert("RGB")  # type: ignore[assignment]

    img = img.convert("L")  # type: ignore[assignment]
    img = img.filter(ImageFilter.MedianFilter(size=3))  # type: ignore[assignment]
    img = ImageOps.autocontrast(img, cutoff=5)  # type: ignore[assignment]
    img = ImageOps.equalize(img)  # type: ignore[assignment]

    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return {"data_b64": _bytes_to_b64(buf.getvalue())}


METHODS = {
    "resize": resize,
    "enhance": enhance,
    "ocr_preprocess": ocr_preprocess,
}


def main():
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    if not HAS_PIL:
        sys.stdout.write(json.dumps({"id": 0, "event": "ready", "pid": os.getpid(), "pil_available": False}) + "\n")
        sys.stdout.flush()
        sys.stderr.write("[pil-worker] PIL not available, exiting\n")
        sys.exit(1)

    # 发送就绪信号
    sys.stdout.write(json.dumps({"id": 0, "event": "ready", "pid": os.getpid(), "pil_available": True}) + "\n")
    sys.stdout.flush()
    sys.stderr.write(f"[pil-worker] ready (pid={os.getpid()})\n")

    # JSON-RPC over stdin/stdout 循环
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stderr.write(f"[pil-worker] invalid JSON: {line[:100]}\n")
            continue

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method == "shutdown":
            sys.stdout.write(json.dumps({"id": req_id, "result": "ok"}) + "\n")
            sys.stdout.flush()
            sys.stderr.write("[pil-worker] shutdown\n")
            sys.exit(0)

        if method == "ping":
            sys.stdout.write(json.dumps({"id": req_id, "result": "pong"}) + "\n")
            sys.stdout.flush()
            continue

        if method not in METHODS:
            sys.stdout.write(json.dumps({"id": req_id, "error": f"unknown method: {method}"}) + "\n")
            sys.stdout.flush()
            continue

        t0 = time.time()
        try:
            result = METHODS[method](params)
            elapsed = round((time.time() - t0) * 1000, 1)
            sys.stdout.write(json.dumps({"id": req_id, "result": result, "timing_ms": elapsed}) + "\n")
            sys.stdout.flush()
        except Exception as e:
            elapsed = round((time.time() - t0) * 1000, 1)
            sys.stdout.write(json.dumps({"id": req_id, "error": str(e), "timing_ms": elapsed}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
