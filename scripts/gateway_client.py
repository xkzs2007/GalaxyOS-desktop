"""gateway_client.py — Worker → Gateway 反向 RPC

三通道双向互通客户端：
  1. UDS：主通道请求-响应（Gateway UDS 服务端）
  2. ZMQ：DEALER 异步双向 + 事件收发
  3. mmap：控制信令 + 状态同步（flock 锁）
"""

import socket as _socket
import struct
import json
import time
import threading
import os

_GATEWAY_UDS = os.path.join(
    os.path.expanduser("~/.openclaw/extensions/claw-core/var"),
    "claw-gateway.sock"
)
_GATEWAY_ZMQ_DEALER = "tcp://127.0.0.1:5560"
_GATEWAY_MMAP = os.path.join(
    os.path.expanduser("~/.openclaw/extensions/claw-core/var"),
    "claw_mmap_control"
)

_zmq_dealer = None
_zmq_lock = threading.Lock()


class GatewayClient:
    """连接到 Gateway 三通道，发送反向 RPC 请求"""
    _sock = None
    _lock = threading.Lock()

    # ────────── UDS 通道 ──────────

    @classmethod
    def connect_uds(cls, max_retries=3):
        with cls._lock:
            if cls._sock is not None:
                try:
                    cls._sock.sendall(b"")
                    return True
                except Exception:
                    cls._sock = None
            for i in range(max_retries):
                try:
                    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                    sock.settimeout(3.0)
                    sock.connect(_GATEWAY_UDS)
                    sock.settimeout(None)
                    cls._sock = sock
                    import sys
                    sys.stderr.write(f"[gateway-client] UDS connected to {_GATEWAY_UDS}\n")
                    return True
                except Exception:
                    if i < max_retries - 1:
                        time.sleep(1.0)
                    continue
            return False

    @classmethod
    def call(cls, method, params=None, timeout_ms=10000):
        """通过 UDS 调用 Gateway 方法（同步 JSON-RPC）"""
        params = params or {}
        req_id = int(time.time() * 1000) % 1000000
        payload = json.dumps({"id": req_id, "method": method, "params": params}, ensure_ascii=False)
        data = payload.encode("utf-8")
        header = struct.pack(">I", len(data))

        with cls._lock:
            if cls._sock is None:
                if not cls.connect_uds():
                    return {"_error": "gateway UDS not connected"}
            try:
                cls._sock.sendall(header + data)
                cls._sock.settimeout(timeout_ms / 1000.0)
                resp_buf = b""
                while len(resp_buf) < 4:
                    chunk = cls._sock.recv(4 - len(resp_buf))
                    if not chunk:
                        raise ConnectionError("gateway disconnected")
                    resp_buf += chunk
                need = struct.unpack(">I", resp_buf[:4])[0]
                while len(resp_buf) < 4 + need:
                    chunk = cls._sock.recv(4 + need - len(resp_buf))
                    if not chunk:
                        raise ConnectionError("gateway disconnected")
                    resp_buf += chunk
                cls._sock.settimeout(None)
                msg = json.loads(resp_buf[4:4 + need].decode("utf-8"))
                if "error" in msg:
                    return {"_error": msg["error"]}
                return msg.get("result", {})
            except Exception as e:
                cls._sock = None
                return {"_error": str(e)}

    # ────────── 便捷方法（UDS） ──────────

    @classmethod
    def web_fetch(cls, url, max_chars=50000):
        return cls.call("web_fetch", {"url": url, "maxChars": max_chars}, timeout_ms=15000)

    @classmethod
    def web_search(cls, query, num=3):
        return cls.call("web_search", {"query": query, "num": num}, timeout_ms=20000)

    @classmethod
    def call_tool(cls, tool_name, args=None, timeout_ms=20000):
        return cls.call("call_tool", {"tool": tool_name, "args": args or {}}, timeout_ms=timeout_ms)

    @classmethod
    def read_file(cls, path, max_chars=50000):
        return cls.call("read_file", {"path": path, "maxChars": max_chars}, timeout_ms=5000)

    @classmethod
    def get_env(cls, key):
        return cls.call("get_env", {"key": key})

    @classmethod
    def get_workspace(cls):
        return cls.call("get_workspace")

    # ────────── ZMQ 双向通道 ──────────

    @classmethod
    def _ensure_zmq(cls):
        global _zmq_dealer
        if _zmq_dealer is not None:
            return True
        with _zmq_lock:
            if _zmq_dealer is not None:
                return True
            try:
                import zmq
                ctx = zmq.Context.instance()
                dealer = ctx.socket(zmq.DEALER)
                dealer.setsockopt(zmq.LINGER, 1000)
                dealer.setsockopt(zmq.RCVTIMEO, 5000)
                dealer.setsockopt(zmq.SNDTIMEO, 5000)
                dealer.identity = f"worker-{os.getpid()}".encode()
                dealer.connect(_GATEWAY_ZMQ_DEALER)
                _zmq_dealer = dealer
                import sys
                sys.stderr.write(f"[gateway-client] ZMQ DEALER connected :5560\n")
                return True
            except Exception as e:
                import sys
                sys.stderr.write(f"[gateway-client] ZMQ DEALER init failed: {e}\n")
                return False

    @classmethod
    def zmq_send(cls, label, data, timeout_ms=10000):
        """异步发送 ZMQ 消息到 Gateway（DEALER → ROUTER）"""
        if not cls._ensure_zmq():
            return {"_error": "zmq not available"}
        try:
            payload = json.dumps({"label": label, "data": data, "ts": time.time()}, ensure_ascii=False)
            _zmq_dealer.send_multipart([b"", payload.encode("utf-8")])
            return {"ok": True}
        except Exception as e:
            return {"_error": str(e)}

    @classmethod
    def zmq_recv(cls, timeout_ms=3000):
        """异步接收 Gateway 通过 ROUTER 发来的消息"""
        if not cls._ensure_zmq():
            return None
        try:
            _zmq_dealer.setsockopt(zmq.RCVTIMEO, timeout_ms)
            parts = _zmq_dealer.recv_multipart()
            if len(parts) >= 2:
                return json.loads(parts[1].decode("utf-8"))
            return None
        except Exception:
            return None

    # ────────── mmap 控制信令 ──────────

    @classmethod
    def mmap_write_signal(cls, signal_type, value=1):
        """写入 mmap 控制信令（原子 4 字节写）"""
        try:
            if not os.path.exists(_GATEWAY_MMAP):
                return {"_error": "mmap not created"}
            import fcntl
            fd = os.open(_GATEWAY_MMAP, os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                # Format: 4 bytes signal_type + 4 bytes value + 8 bytes timestamp
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, struct.pack("i", signal_type) + struct.pack("i", value) + struct.pack("d", time.time()))
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            return {"ok": True}
        except Exception as e:
            return {"_error": str(e)}

    @classmethod
    def mmap_read_signal(cls):
        """读取 mmap 控制信令"""
        try:
            if not os.path.exists(_GATEWAY_MMAP):
                return {"signal": 0, "value": 0, "ts": 0}
            import fcntl
            fd = os.open(_GATEWAY_MMAP, os.O_RDONLY)
            fcntl.flock(fd, fcntl.LOCK_SH)
            try:
                data = os.read(fd, 16)
                if len(data) >= 8:
                    sig, val = struct.unpack("ii", data[:8])
                    ts = struct.unpack("d", data[8:16])[0] if len(data) >= 16 else 0
                    return {"signal": sig, "value": val, "ts": ts}
                return {"signal": 0, "value": 0, "ts": 0}
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
        except Exception:
            return {"signal": 0, "value": 0, "ts": 0}


# 信号常量
MMAP_SIGNAL_IDLE = 0
MMAP_SIGNAL_WORKER_WANTS_ATTENTION = 1
MMAP_SIGNAL_GATEWAY_WANTS_ATTENTION = 2
MMAP_SIGNAL_DONE = 3
MMAP_SIGNAL_HEARTBEAT = 4
