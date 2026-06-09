#!/usr/bin/env python3
"""
fast_pil — 独立子进程图像处理加速器 (v2)

核心问题: Python PIL 在 ProcessPoolExecutor 中与 Worker 主进程共享资源,
同步降级直接阻塞主线程 GIL → 高并发卡死。

v2 方案: 独立的 pil_worker 子进程, stdin/stdout JSON-RPC 隔离通信。
- PIL 完全隔离, 零 GIL 竞争
- 不再有同步降级（subprocess 不可用则直接报错, 不阻塞 UDS 循环）
- LRU 缓存 + 自动重启
- 所有操作 ≤8s 超时

协议:
  Request:  {"id":1, "method":"resize", "params":{...}}\n
  Response: {"id":1, "result":{...}, "timing_ms":42}\n
"""

import os
import io
import sys
import json
import time
import base64
import logging
import hashlib
import subprocess
import threading
from typing import Dict, List, Optional, Tuple
from collections import OrderedDict

logger = logging.getLogger(__name__)

PIL_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pil_worker.py")
# Rust 原生扩展（更快，无 GIL）
_RUST_BINARY = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "native", "target", "release", "galaxyos-native"
)
_USE_RUST = os.path.exists(_RUST_BINARY)
if _USE_RUST:
    logger.info(f"[fast-pil] using Rust native extension: {_RUST_BINARY}")
PIL_TIMEOUT = 8.0  # 单操作超时


# ── 线程安全 LRU 缓存 ──

class LRUCache:
    def __init__(self, maxsize: int = 50):
        self._cache = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[bytes]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def set(self, key: str, value: bytes):
        with self._lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            if len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


# ── PIL 子进程管理 ──

class PilWorkerProcess:
    """管理独立的 pil_worker.py 子进程, 通过 stdin/stdout 通信"""

    def __init__(self):
        self._proc = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._stats = {"requests": 0, "errors": 0, "restarts": 0}
        self._ensure_started()

    def _ensure_started(self):
        """启动或重启子进程"""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            self._restart_locked()

    def _restart_locked(self):
        """在锁内重启子进程"""
        if self._proc:
            try:
                self._proc.stdin.write(json.dumps({"id": -1, "method": "shutdown", "params": {}}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

        # 优先使用 Rust 原生扩展，回退到 Python pil_worker
        if _USE_RUST:
            cmd = [_RUST_BINARY]
        else:
            cmd = [sys.executable, PIL_WORKER_SCRIPT]

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # 行缓冲
        )
        self._stats["restarts"] += 1

        # 读取就绪信号
        try:
            ready_line = self._proc.stdout.readline()
            if ready_line:
                ready = json.loads(ready_line.strip())
                if ready.get("event") == "ready":
                    logger.info(f"[fast-pil] pil_worker ready (pid={ready.get('pid')})")
                    return
        except Exception as e:
            pass

        raise RuntimeError("PIL worker failed to start")

    def call(self, method: str, params: dict) -> dict:
        """同步调用 PIL worker（从 Worker 的 UDS handler 线程中调用）

        由于 UDS 是串行的, 这里同步等待不会造成并发问题,
        且 PIL 操作是 I/O 密集型（等待子进程），Python GIL 在 subprocess 通信期间会释放。
        """
        self._ensure_started()
        timeout_at = time.time() + PIL_TIMEOUT

        with self._lock:
            self._next_id += 1
            req_id = self._next_id

        request_line = json.dumps({"id": req_id, "method": method, "params": params}, ensure_ascii=False)

        try:
            with self._lock:
                self._proc.stdin.write(request_line + "\n")
                self._proc.stdin.flush()

            # 读取响应（不在锁内，允许其他线程的请求排队等锁）
            deadline = time.time() + PIL_TIMEOUT
            while time.time() < deadline:
                line = self._proc.stdout.readline()
                if not line:
                    # 子进程退出 — 重启后重试一次
                    logger.warning("[fast-pil] pil_worker exited, restarting...")
                    self._restart_locked()
                    with self._lock:
                        self._next_id += 1
                        retry_id = self._next_id
                    retry_line = json.dumps({"id": retry_id, "method": method, "params": params}, ensure_ascii=False)
                    with self._lock:
                        self._proc.stdin.write(retry_line + "\n")
                        self._proc.stdin.flush()
                    deadline = time.time() + PIL_TIMEOUT
                    continue

                try:
                    resp = json.loads(line.strip())
                    if resp.get("id") == req_id:
                        self._stats["requests"] += 1
                        if "error" in resp:
                            self._stats["errors"] += 1
                            return {"ok": False, "error": resp["error"]}
                        return {"ok": True, **resp["result"]}
                    elif resp.get("id") == retry_id:
                        self._stats["requests"] += 1
                        if "error" in resp:
                            self._stats["errors"] += 1
                            return {"ok": False, "error": resp["error"]}
                        return {"ok": True, **resp["result"]}
                except json.JSONDecodeError:
                    logger.warning(f"[fast-pil] invalid response: {line[:100]}")
                    continue

            return {"ok": False, "error": "PIL timeout"}

        except Exception as e:
            self._stats["errors"] += 1
            logger.warning(f"[fast-pil] call failed: {e}")
            return {"ok": False, "error": str(e)}

    def shutdown(self):
        with self._lock:
            if self._proc:
                try:
                    self._proc.stdin.write(json.dumps({"id": -1, "method": "shutdown", "params": {}}) + "\n")
                    self._proc.stdin.flush()
                    self._proc.wait(timeout=3)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                self._proc = None

    def get_stats(self) -> dict:
        return dict(self._stats)


# ── FastPIL 主类 ──

class FastPIL:
    """PIL 图像处理加速器 — 独立子进程, 零 GIL 竞争"""

    def __init__(self, cache_size: int = 50):
        self.cache = LRUCache(maxsize=cache_size)
        self._worker = None  # 懒初始化
        self._stats = {"cache_hit": 0, "requests": 0, "errors": 0}

    def _get_worker(self):
        if self._worker is None:
            self._worker = PilWorkerProcess()
        return self._worker

    def _read_image(self, path: str) -> Optional[bytes]:
        """读取图片到 bytes"""
        try:
            if path.startswith("http://") or path.startswith("https://"):
                import requests
                r = requests.get(path, timeout=10)
                if r.status_code == 200:
                    return r.content
                logger.warning(f"远程图片下载失败: {path} {r.status_code}")
                return None
            with open(path, "rb") as f:
                return f.read()
        except Exception as e:
            logger.warning(f"读取图片失败 {path}: {e}")
            return None

    def _run(self, method: str, image_path: str, cache_key: str, extra_params: dict = None) -> Optional[bytes]:
        """通过子进程执行 PIL 操作, 带缓存"""
        # 检查缓存
        cached = self.cache.get(cache_key)
        if cached is not None:
            self._stats["cache_hit"] += 1
            return cached

        data = self._read_image(image_path)
        if data is None:
            return None

        params = {"data_b64": base64.b64encode(data).decode("ascii")}
        if extra_params:
            params.update(extra_params)

        self._stats["requests"] += 1
        worker = self._get_worker()
        resp = worker.call(method, params)

        if resp.get("ok") and "data_b64" in resp:
            result = base64.b64decode(resp["data_b64"])
            self.cache.set(cache_key, result)
            return result
        else:
            self._stats["errors"] += 1
            logger.warning(f"[fast-pil] {method} failed for {image_path}: {resp.get('error', 'unknown')}")
            return None

    # ── 对外接口 ──

    def resize(self, image_path: str, width: int, height: int,
               keep_ratio: bool = True, fmt: str = "JPEG") -> Optional[bytes]:
        cache_key = f"resize:{image_path}:{width}x{height}:{keep_ratio}:{fmt}"
        return self._run("resize", image_path, cache_key, {
            "width": width, "height": height,
            "keep_ratio": keep_ratio, "fmt": fmt,
        })

    def enhance(self, image_path: str, brightness: float = 1.0,
                contrast: float = 1.0, sharpness: float = 1.0,
                fmt: str = "JPEG") -> Optional[bytes]:
        cache_key = f"enhance:{image_path}:b{brightness}:c{contrast}:s{sharpness}"
        return self._run("enhance", image_path, cache_key, {
            "brightness": brightness, "contrast": contrast,
            "sharpness": sharpness, "fmt": fmt,
        })

    def ocr_preprocess(self, image_path: str, fmt: str = "PNG") -> Optional[bytes]:
        cache_key = f"ocr_pre:{image_path}"
        return self._run("ocr_preprocess", image_path, cache_key, {"fmt": fmt})

    def get_stats(self) -> dict:
        worker_stats = self._worker.get_stats() if self._worker else {}
        return {
            **self._stats,
            "worker": worker_stats,
            "cache_size": self.cache.size,
        }

    def close(self):
        if self._worker:
            self._worker.shutdown()
            self._worker = None


# ── 全局实例 ──
_instance = None

def get_fast_pil() -> FastPIL:
    global _instance
    if _instance is None:
        _instance = FastPIL()
    return _instance
