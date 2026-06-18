#!/usr/bin/env python3
"""
claw_worker — 小艺 Claw 常驻 Python Worker 进程

三通道 JSON-RPC 2.0:
  1. UDS socket:   ~/.openclaw/extensions/galaxyos/var/claw-worker.sock (主通道, fallback: claw-core/var)
  2. ZMQ PUB:      tcp://127.0.0.1:5559 (事件推送)
  3. Shared mmap:  ~/.openclaw/extensions/galaxyos/var/claw_worker_mmap (缓存快读, fallback: claw-core/var)
  4. Fallback:     stdin/stdout (兼容旧版 Plugin)

Request:  {"id":1, "method":"<name>", "params":{...}}
Response: {"id":1, "result":{...}, "timing_ms":123}
Error:    {"id":1, "error":"...", "traceback":"..."}

方法: ping | health | recall | store | verify | rccam | hardinfo | implicit_feedback | dag_summary | restore_context | shutdown

Request:  {"id":1, "method":"<name>", "params":{...}}
Response: {"id":1, "result":{...}, "timing_ms":123}
Error:    {"id":1, "error":"...", "traceback":"..."}

方法: ping | health | recall | store | verify | rccam | hardinfo | implicit_feedback | dag_summary | restore_context | shutdown
"""

import sys
import os
import json
import time
import traceback
import signal
import contextlib
import socket as _socket
import struct
import threading
import selectors

# ========== 路径初始化 ==========
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE",
    os.path.expanduser("~/.openclaw/workspace"))

# 自动检测 GalaxyOS 仓库路径（galaxyos/engine/ 或 extensions/galaxyos/dist/scripts/）
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_GALAXYOS_REPO = os.environ.get("GALAXYOS_REPO", "")
if not _GALAXYOS_REPO:
    # 从 __file__ 推断：galaxyos/engine/ → galaxyos/ → repo root
    if os.path.basename(_THIS_DIR) == "engine" and os.path.basename(os.path.dirname(_THIS_DIR)) == "galaxyos":
        _GALAXYOS_REPO = os.path.dirname(os.path.dirname(_THIS_DIR))
    elif os.path.basename(_THIS_DIR) == "scripts":
        _GALAXYOS_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))

# v7.0: 统一使用 galaxyos/engine/ 和 galaxyos/privileged/
_GALAXYOS_ENGINE = os.path.join(_GALAXYOS_REPO, "galaxyos", "engine") if _GALAXYOS_REPO else _THIS_DIR
_GALAXYOS_PRIVILEGED = os.path.join(_GALAXYOS_REPO, "galaxyos", "privileged") if _GALAXYOS_REPO else _THIS_DIR
_GALAXYOS_SCRIPTS = os.path.join(_GALAXYOS_REPO, "galaxyos", "scripts") if _GALAXYOS_REPO else _THIS_DIR

# 优先级: galaxyos repo → workspace skills (legacy fallback)
sys.path.insert(0, _GALAXYOS_ENGINE)
sys.path.insert(0, _GALAXYOS_PRIVILEGED)
sys.path.insert(0, _GALAXYOS_SCRIPTS)

# v8.2.2: LFM 预加载依赖，加入 .venv 的 site-packages（软链在 galaxyos/ 同级）
_GALAXYOS_VENV_SITE = os.path.join(os.path.dirname(_THIS_DIR), ".venv", "lib", "python3.12", "site-packages")
if os.path.isdir(_GALAXYOS_VENV_SITE):
    sys.path.insert(0, _GALAXYOS_VENV_SITE)

# Legacy fallback: workspace skills path (backward compat)
_LEGACY_CORE = os.path.join(WORKSPACE, "skills", "xiaoyi-claw-omega-final", "skills", "llm-memory-integration", "core")
_LEGACY_SCRIPTS = os.path.join(WORKSPACE, "skills", "xiaoyi-claw-omega-final", "scripts")
if os.path.isdir(_LEGACY_CORE):
    sys.path.insert(0, _LEGACY_CORE)
if os.path.isdir(_LEGACY_SCRIPTS):
    sys.path.insert(0, _LEGACY_SCRIPTS)

# 部署环境: extensions/galaxyos/dist/scripts/ 自身也在 path 上
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# 模块级缓存
_worker_inst = None

def _get_worker():
    """获取全局 ClawWorker 单例（供后台线程使用）"""
    return _worker_inst

# ═══ MN-RU 增量索引桥接（2407.07871 / 2404.13556）═══
_RETRIEVAL_HUB_IMPORTED = False
def _ensure_retrieval_hub():
    """懒加载 retrieval_hub 中的 MN-RU 单例"""
    global _RETRIEVAL_HUB_IMPORTED
    if _RETRIEVAL_HUB_IMPORTED:
        return True
    try:
        sys.path.insert(0, os.path.join(_OPENCLAW_HOME, "extensions", "galaxyos", "dist", "scripts"))
        from retrieval_hub import _get_hnsw_mn, _update_session_history
        global _MN_HNSW, _UPDATE_SESSION
        _MN_HNSW = _get_hnsw_mn()
        _UPDATE_SESSION = _update_session_history
        _MN_HNSW.init_embedding()
        _RETRIEVAL_HUB_IMPORTED = True
        return True
    except Exception as e:
        _RETRIEVAL_HUB_IMPORTED = False
        return False

def _push_to_hnsw_mini(node: dict):
    """向 MN-RU 小索引推送新节点（dag_ingest 阶段触发）"""
    if not _ensure_retrieval_hub():
        return
    try:
        _MN_HNSW.push_pending(node)
    except Exception:
        pass

def _push_to_session_index(session_node: dict):
    """向 Session 索引推送 cycle 会话上下文（rccam_compact_cycle 阶段触发）"""
    if not _ensure_retrieval_hub():
        return
    try:
        _MN_HNSW._pending_session.append(session_node)
    except Exception:
        pass

# ========== 三通道路径 ==========
# v7.0: 统一使用 galaxyos/var/ 路径（claw-core/var 仅作为 fallback）
_OPENCLAW_HOME = os.path.expanduser(
    os.environ.get("OPENCLAW_HOME", "~/.openclaw"))
_GALAXYOS_VAR = os.path.join(_OPENCLAW_HOME, "extensions", "galaxyos", "var")
_CLAW_CORE_VAR = os.path.join(_OPENCLAW_HOME, "extensions", "claw-core", "var")

def _resolve_var_path(subpath, mkdirs=True):
    """解析 var 路径：优先 galaxyos/var，fallback claw-core/var"""
    primary = os.path.join(_GALAXYOS_VAR, subpath)
    fallback = os.path.join(_CLAW_CORE_VAR, subpath)
    if os.path.isdir(os.path.dirname(primary)):
        return primary
    if mkdirs:
        try:
            os.makedirs(os.path.dirname(primary), exist_ok=True)
            return primary
        except Exception:
            pass
    if os.path.isdir(os.path.dirname(fallback)):
        return fallback
    # 最后尝试：创建 galaxyos/var/
    return primary

_WORKER_ID = os.environ.get('WORKER_ID', 'worker')
_WORKER_SUFFIX = _WORKER_ID.replace(':', '-')  # worker:1 → worker-1
UDS_PATH = _resolve_var_path(f"claw-worker-{_WORKER_SUFFIX}.sock")
ZMQ_PUB_PORT = 5559
MMAP_PATH = _resolve_var_path("claw_worker_mmap")

# 心跳专用 mmap（独立文件，插件只读 8 字节时间戳 float64，不跟 GIL 抢锁）
HB_PATH = _resolve_var_path("claw_worker_heartbeat")
_zmq_pub = None  # ZMQ socket (optional)

# ========== Gateway UDS 代理（Worker → Gateway 透明 RPC） ==========
_GATEWAY_UDS_PATH = _resolve_var_path("claw-gateway.sock")
_MMAP_SHM_PATH = _resolve_var_path("claw_shared_state")
_MMAP_SHM_SIZE = 4096

class _GatewayProxy:
    """Gateway 调用代理 — 透明远程调用（线程安全，每线程一个连接）

    用法:
        await gateway.web_fetch(url="https://...")
        await gateway.channel_send(message="hello")
        status = gateway.mmap_read()  # mmap 直接读，不走 UDS
    """
    def __init__(self):
        self._local = threading.local()

    def _init_thread(self):
        self._local.conn = None
        self._local.id = 0

    def _get_conn(self):
        """获取或创建当前线程 HTTP over UDS 连接（线程安全，无锁）"""
        if not hasattr(self._local, 'conn'):
            self._init_thread()
        if self._local.conn is not None:
            return self._local.conn
        import http.client
        class _UnixHTTPConn(http.client.HTTPConnection):
            def __init__(self, path):
                self._uds_path = path
                super().__init__('localhost')
            def connect(self):
                import socket
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(10.0)
                self.sock.connect(self._uds_path)
        self._local.conn = _UnixHTTPConn(_GATEWAY_UDS_PATH)
        return self._local.conn

    def _call(self, method, params=None, timeout=10.0):
        """HTTP over UDS 调用（线程安全：每线程独立连接 + ID）"""
        if params is None:
            params = {}
        if not hasattr(self._local, 'id'):
            self._init_thread()
        self._local.id += 1
        req = {"id": self._local.id, "method": method, "params": params}
        body = json.dumps(req, ensure_ascii=False).encode('utf-8')
        try:
            conn = self._get_conn()
            conn.request('POST', '/', body=body, headers={'Content-Type': 'application/json', 'Content-Length': str(len(body))})
            resp = conn.getresponse()
            data = resp.read()
            result = json.loads(data.decode('utf-8'))
            if 'error' in result and result.get('error'):
                raise RuntimeError(f"Gateway RPC error: {result.get('error')}")
            return result.get('result')
        except Exception as e:
            self._local.conn = None  # 断线，仅清理当前线程的连接
            raise RuntimeError(f"Gateway call '{method}' failed: {e}")

    def __getattr__(self, name):
        """透明远程调用：gateway.ping() → _call("ping")"""
        if name.startswith('_'):
            raise AttributeError(name)
        def _callable(*args, **kwargs):
            if args:
                # 如果传了位置参数，当 params 处理
                return self._call(name.replace('_', '.'), args[0] if isinstance(args[0], dict) else {"arg": args[0]})
            return self._call(name.replace('_', '.'), kwargs)
        return _callable

    def mmap_read(self):
        """本地读 mmap（不经过 UDS）"""
        try:
            if not os.path.exists(_MMAP_SHM_PATH):
                return {"status": "uninitialized"}
            import struct as _struct
            with open(_MMAP_SHM_PATH, "rb") as f:
                f.seek(0)
                buf = f.read(_MMAP_SHM_SIZE)
            if len(buf) < 4:
                return {"status": "empty"}
            payload_len = _struct.unpack("<I", buf[:4])[0]
            if payload_len < 1 or payload_len > _MMAP_SHM_SIZE - 4:
                return {"status": "invalid"}
            json_str = buf[4:4+payload_len].decode("utf-8")
            return json.loads(json_str)
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def mmap_write(self, data):
        """本地写 mmap"""
        try:
            json_str = json.dumps({"ts": time.time(), **data}, ensure_ascii=False)
            payload = json_str.encode("utf-8")
            import struct as _struct
            buf = bytearray(_MMAP_SHM_SIZE)
            _struct.pack_into("<I", buf, 0, len(payload))
            buf[4:4+len(payload)] = payload
            os.makedirs(os.path.dirname(_MMAP_SHM_PATH), exist_ok=True)
            with open(_MMAP_SHM_PATH, "wb") as f:
                f.write(buf)
            return {"ok": True, "bytes": len(payload)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


gateway = _GatewayProxy()

# ========== HTTP JSON-RPC ==========
HTTP_PORT = 8765

# 记忆巩固引擎（后台线程）
_consolidation = None


# ═══ Session 粒度上下文 — 多会话隔离 ═══

class SessionContext:
    """每个 session 的独立运行时上下文

    解决: 多会话共享一个 ClawWorker 导致压缩排队、记忆串扰。
    """

    __slots__ = ('session_key', '_last_access', '_compact_lock',
                 '_compact_state', '_dag_cache', '_memory_cache')

    def __init__(self, session_key: str):
        self.session_key = session_key
        self._last_access = time.time()
        self._compact_lock = threading.Lock()
        self._compact_state = {}
        self._dag_cache = None
        self._memory_cache = {}

    def touch(self):
        self._last_access = time.time()

    @property
    def idle_s(self) -> float:
        return time.time() - self._last_access


# ═══ 记忆链路熔断器 — Python 端 ═══

class CircuitBreaker:
    """方法级熔断器 — 连续失败 N 次后 OPEN，RESET_TIMEOUT 秒后半开探测"""

    def __init__(self, name: str, failure_threshold: int = 3, reset_timeout: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"      # CLOSED | OPEN | HALF_OPEN
        self._lock = threading.Lock()

    def call(self, fn, *args, **kwargs):
        with self._lock:
            if self.state == "OPEN":
                if time.time() - self.last_failure_time > self.reset_timeout:
                    self.state = "HALF_OPEN"
                else:
                    raise RuntimeError(f"circuit breaker OPEN [{self.name}]")

        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self.failures = 0
                self.state = "CLOSED"
            return result
        except Exception as e:
            with self._lock:
                self.failures += 1
                self.last_failure_time = time.time()
                if self.failures >= self.failure_threshold:
                    self.state = "OPEN"
            raise


class ClawWorker:
    """常驻 Worker — 单进程，session 粒度上下文隔离"""

    def __init__(self):
        self._entry = None
        self._hardware_info = {}
        self._load_time_ms = 0
        self._init_time = time.time()
        self._persona_snapshot = ""
        self._soul_snapshot = ""
        self._identity_snapshot = ""
        self._ensure_lock = threading.Lock()
        # Session 粒度上下文
        self._sessions: Dict[str, SessionContext] = {}
        self._sessions_lock = threading.Lock()
        # 记忆链路熔断器（独立于 session 的全局断路器）
        self._dag_cb = CircuitBreaker("dag_chain", failure_threshold=3, reset_timeout=30.0)
        self._compact_cb = CircuitBreaker("compact", failure_threshold=3, reset_timeout=30.0)
        self._assemble_cb = CircuitBreaker("assemble", failure_threshold=2, reset_timeout=20.0)
        # 后台 GC：清理 > 10min 无活动的 session 上下文
        self._gc_timer = None
        self._start_gc()

    def _start_gc(self):
        """60s 清理一次过期 session（> 600s 无活动）"""
        def _gc():
            while True:
                time.sleep(60)
                with self._sessions_lock:
                    stale = [k for k, v in self._sessions.items() if v.idle_s > 600]
                for k in stale:
                    with self._sessions_lock:
                        self._sessions.pop(k, None)
        t = threading.Thread(target=_gc, daemon=True, name="session-gc")
        t.start()

    def _get_session_ctx(self, session_key: str) -> SessionContext:
        """获取或创建 session 上下文（线程安全）"""
        if not session_key:
            session_key = "default"
        with self._sessions_lock:
            if session_key not in self._sessions:
                self._sessions[session_key] = SessionContext(session_key)
            ctx = self._sessions[session_key]
        ctx.touch()
        return ctx

    def build_system_prompt(self, _p: dict) -> dict:
        """
        L6: 自组装 system prompt，不依赖 OpenClaw config

        从人格三文件自组装，注入到 R-CCAM Cognition 阶段。
        返回结构化 system prompt 文本供 Worker assemble_context 使用。
        """
        try:
            # 重读文件（每次调用刷新生效，避免缓存过期）
            persona_path = os.path.join(WORKSPACE, "persona.md")
            soul_path = os.path.join(WORKSPACE, "SOUL.md")
            identity_path = os.path.join(WORKSPACE, "IDENTITY.md")
            agents_path = os.path.join(WORKSPACE, "AGENTS.md")

            persona = ""
            identity = ""
            soul = ""
            rules = ""

            if os.path.exists(persona_path):
                with open(persona_path, "r", encoding="utf-8") as f:
                    persona = f.read(3000)
            if os.path.exists(identity_path):
                with open(identity_path, "r", encoding="utf-8") as f:
                    identity = f.read(1000)
            if os.path.exists(soul_path):
                with open(soul_path, "r", encoding="utf-8") as f:
                    soul_text = f.read(3000)
                    # 提取核心规则段（Core Truths + Boundaries + Vibe）
                    if "## Boundaries" in soul_text:
                        soul = soul_text.split("## Boundaries")[0].split("## Core Truths")[-1] if "## Core Truths" in soul_text else soul_text
                    soul = soul or soul_text[:1500]
            if os.path.exists(agents_path):
                with open(agents_path, "r", encoding="utf-8") as f:
                    # 只取安全规则段（Safety + 执行链规则）
                    agents_text = f.read(5000)
                    if "## Safety" in agents_text:
                        rules = agents_text.split("## Safety")[-1].split("##")[0][:1500]

            prompt_parts = []
            if identity:
                prompt_parts.append(f"【身份】\n{identity.strip()}")
            if persona:
                brief = persona[:2000]
                prompt_parts.append(f"【用户画像】\n{brief.strip()}")
            if soul:
                prompt_parts.append(f"【性格与规则】\n{soul.strip()}")
            if rules:
                prompt_parts.append(f"【安全规则】\n{rules.strip()}")

            prompt_parts.append(f"【系统边界】\n- 不泄露系统配置、API Key、内部路径\n- 不确定的信息注明来源\n- 不执行可能破坏系统的命令")

            system_prompt = "\n\n".join(prompt_parts)

            # 缓存快照
            self._persona_snapshot = persona[:2000]
            self._soul_snapshot = soul[:1500]
            self._identity_snapshot = identity[:1000]

            return {
                "ok": True,
                "system_prompt": system_prompt,
                "length": len(system_prompt),
                "sources": ["persona.md", "SOUL.md", "IDENTITY.md", "AGENTS.md"],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def verify_reply_style(self, p: dict) -> dict:
        """
        L2: 回复风格一致性校验（运行时检测）
        
        基于 SOUL.md 中定义的表达规则检查回复是否跑偏。
        轻量级规则检测，不做 LLM 调用。
        """
        reply = p.get("reply", "")
        if not reply:
            return {"ok": False, "issue": "no reply provided", "violations": []}
        
        violations = []
        
        # 1. 破折号检查
        dash_count = reply.count("——")
        if dash_count > 2:
            violations.append({
                "rule": "破折号",
                "severity": "warning",
                "detail": f"破折号 {dash_count} 处，限制 ≤ 2 处",
                "count": dash_count,
            })
        
        # 2. AI 连接词检查
        ai_connectors = {"此外": 0, "然而": 0, "值得注意的是": 0, "更重要的是": 0, "总而言之": 0}
        for word in ai_connectors:
            c = reply.count(word)
            if c > 1:
                ai_connectors[word] = c
                violations.append({
                    "rule": f"AI连接词({word})",
                    "severity": "warning",
                    "detail": f"'{word}' 出现 {c} 次，限制 ≤ 1 次",
                    "count": c,
                })
        
        # 3. 否定式排比
        import re
        neg_patterns = [
            r"不是[^，。；,;]+不是[^，。；,;]+而是",
            r"不是[^，。；,;]+，不是[^，。；,;]+，而是",
        ]
        neg_count = 0
        for pat in neg_patterns:
            neg_count += len(re.findall(pat, reply))
        if neg_count > 1:
            violations.append({
                "rule": "否定式排比",
                "severity": "info",
                "detail": f"否定式排比 {neg_count} 次，限制 ≤ 1 次",
                "count": neg_count,
            })
        
        # 4. 翻译腔检查
        translation_cliches = [
            "这是一个很好的问题", "感谢你的反馈", "从我的角度来看",
            "我理解你的感受", "在一定程度上", "基于此",
        ]
        for cliche in translation_cliches:
            if cliche in reply:
                violations.append({
                    "rule": "翻译腔",
                    "severity": "warning",
                    "detail": f"发现翻译腔：'{cliche}'",
                    "text": cliche,
                })
        
        # 5. 宣传性语言
        propaganda_words = ["深刻地", "意义深远", "不可或缺", "历史性的", "里程碑式的"]
        for pw in propaganda_words:
            if pw in reply:
                violations.append({
                    "rule": "宣传性语言",
                    "severity": "info",
                    "detail": f"宣传性语言：'{pw}'，建议替换为具体描述",
                    "text": pw,
                })
        
        # 6. 表格过度（超过3个表格可能太工整）
        table_count = reply.count("|---") + reply.count("| ---")
        if table_count > 3:
            violations.append({
                "rule": "表格过度",
                "severity": "info",
                "detail": f"发现 {table_count} 个表格，建议精简",
                "count": table_count // 3,
            })
        
        severity_map = {"error": 3, "warning": 2, "info": 1}
        max_severity = max((severity_map.get(v["severity"], 0) for v in violations), default=0)
        
        return {
            "ok": len(violations) == 0,
            "violations": violations,
            "violation_count": len(violations),
            "max_severity": [k for k, v in severity_map.items() if v == max_severity][0] if max_severity > 0 else "none",
        }

    def get_persona_core(self, _p: dict) -> dict:
        """取人格核心摘要供 R-CCAM Cognition 阶段注入（L1 入场保护）"""
        if not self._persona_snapshot:
            # 回退：读文件
            try:
                p = os.path.join(WORKSPACE, "persona.md")
                with open(p, "r", encoding="utf-8") as f:
                    self._persona_snapshot = f.read(2000)
            except Exception:
                pass
        return {
            "persona": self._persona_snapshot[:1500],
            "soul": self._soul_snapshot[:800],
            "identity": self._identity_snapshot[:500],
        }

    def _ensure(self):
        try:
            open('/tmp/rci_ensure_marker','w').write('entered\n')
        except OSError:
            pass
        if self._entry is not None:
            return
        with self._ensure_lock:
            # 双重检查：拿到锁后再次确认
            if self._entry is not None:
                return
            t0 = time.time()
            try:
                from unified_entry import UnifiedEntry
                # 只创建一次实例，后续复用
                self._entry = UnifiedEntry()
                # Inject RCI three-channel publish callbacks into XiaoYiClawLLM
                _RCI_MARKER = "/tmp/rci_inject_marker"
                try:
                    open(_RCI_MARKER, "w").write("pre-inject\n")
                    if not hasattr(self._entry, 'xiaoyi_claw') or self._entry.xiaoyi_claw is None:
                        open(_RCI_MARKER, "a").write("xiaoyi_claw is None\n")
                    elif not hasattr(self._entry.xiaoyi_claw, 'set_rci_publisher'):
                        open(_RCI_MARKER, "a").write("NO set_rci_publisher!\n")
                        ty = type(self._entry.xiaoyi_claw)
                        open(_RCI_MARKER, "a").write(f"type={ty}\n")
                    else:
                        _RCI_MMAP = "/tmp/rci_shared_state"
                        def _rci_mmap(key, data):
                            full = {key: data}
                            raw = json.dumps(full, ensure_ascii=False).encode("utf-8")
                            hdr = struct.pack("<I", len(raw))
                            try:
                                os.makedirs(os.path.dirname(_RCI_MMAP), exist_ok=True)
                                with open(_RCI_MMAP, "wb") as f:
                                    f.write(hdr + raw)
                            except Exception:
                                pass
                        self._entry.xiaoyi_claw.set_rci_publisher(zmq_fn=_zmq_pub_event, mmap_fn=_rci_mmap)
                        open(_RCI_MARKER, "a").write("INJECTED OK\n")
                except Exception as _e:
                    open(_RCI_MARKER, "a").write(f"EXCEPTION: {_e}\n")
                    import traceback as _tb
                    open(_RCI_MARKER, "a").write(_tb.format_exc() + "\n")
                # 触发一次健康检查，让模块懒加载
                self._entry.health_check()
                
                # 预加载 LFM2.5-1.2B 模型到内存（神经网络常驻）
                try:
                    sys.stderr.write("[claw-worker] 预加载 LFM2.5-1.2B-Thinking...\n")
                    _t0 = time.time()
                    from lfm_adaptive_operator import RealLFMNetwork
                    _lfm = RealLFMNetwork()
                    _lfm._ensure()
                    # 触发一次 embedding 让模型完全预热（+ KV cache init）
                    _lfm.embed_text("预热")
                    _t1 = time.time()
                    # 赋值到 self 防止 GC
                    self._lfm_preloaded = _lfm
                    sys.stderr.write(f"[claw-worker] LFM2.5-1.2B 预加载完成 ({_t1-_t0:.1f}s)\n")
                except Exception as _e:
                    sys.stderr.write(f"[claw-worker] LFM 预加载跳过: {_e}\n")
                    self._lfm_preloaded = None
                
                self._load_hardware()
                self._load_time_ms = round((time.time() - t0) * 1000, 1)
            except Exception as e:
                raise RuntimeError(f"Worker init failed: {e}")

    def _load_hardware(self):
        """硬件信息检测（纯文件读取，不走 Python 模块，避免 pipe_read 阻塞）"""
        info = {}
        try:
            with open('/proc/cpuinfo', 'r') as _f:
                _cpu = _f.read()
            info['intel_cpu'] = 'Intel' in _cpu
            info['amx'] = 'amx' in _cpu.lower()
            info['cpu_count'] = _cpu.count('processor')
        except Exception:
            info['intel_cpu'] = False
        try:
            with open('/proc/meminfo', 'r') as _f:
                _mem = _f.read()
            for _line in _mem.split('\n'):
                if 'MemTotal' in _line:
                    info['mem_kb'] = int(_line.split()[1])
                    break
        except Exception:
            pass
        self._hardware_info = info

    def ping(self, _p: dict) -> dict:
        return {"ok": True, "uptime_s": round(time.time() - self._init_time, 1)}

    def health(self, _p: dict) -> dict:
        # ⚡ 轻量健康检查：不做 _ensure()（避免 ONNX 加载超时）
        # 如果已经初始化，可以获取深层信息；否则只做系统级检查
        _healthy = True
        _issues = []
        _components = {}
        _entry_ready = self._entry is not None

        if _entry_ready:
            try:
                result = self._entry.health_check()
                _components = result.get('components', {})
            except Exception:
                _components = {'unified_entry': {'healthy': False}}
                _issues.append('unified_entry_error')
                _healthy = False
        else:
            _components['xiaoyi_claw'] = {'healthy': False, 'note': '延迟初始化（轻量模式）'}
            _components['coordinator'] = {'healthy': False, 'note': '延迟初始化（轻量模式）'}
            _components['workflow_engine'] = {'healthy': False, 'note': '延迟初始化（轻量模式）'}

        # 检查 Worker 连通性（轻量：看 socket 文件存在）
        _var_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'var')
        _sock_files = [f for f in os.listdir(_var_dir) if f.endswith('.sock')] if os.path.isdir(_var_dir) else []
        _dag_ok = len(_sock_files) > 0

        # 检查最近 R-CCAM 活动（5 分钟内）
        _rccam_recent = (time.time() - getattr(self, '_last_rccam_ts', 0)) < 300 if hasattr(self, '_last_rccam_ts') else False

        # Worker 自身状态
        _uptime = round(time.time() - self._init_time, 1) if hasattr(self, '_init_time') else 0
        _components['worker'] = {
            'healthy': True,
            'uptime_s': _uptime,
            'pid': os.getpid(),
            'entry_initialized': _entry_ready,
        }

        return {
            'healthy': _healthy and not _issues,
            'issues': _issues,
            'components': _components,
            'dag_available': _dag_ok,
            'rccam_recent_5m': _rccam_recent,
            'vector_api': self._get_vector_api_status() if hasattr(self, '_get_vector_api_status') else {'status': '轻量模式'},
            'worker_uptime_s': _uptime,
            'pid': os.getpid(),
        }

    def recall(self, p: dict) -> dict:
        self._ensure()
        return self._entry.recall(p.get("query", ""), p.get("top_k", 5),
                                  session_id=p.get("session_id", ""))

    def smart_retrieval(self, p: dict) -> dict:
        """神经网络增强检索：走 retrieval_hub 完整五路管道 + neural_rerank_dedup

        供 ContextEngine assemble 注入使用，确保注入上下文经过噪声过滤和去重。
        """
        query = p.get("query", "")
        top_k = p.get("top_k", 5)
        session_id = p.get("session_id", "")
        if not query:
            return {"results": [], "success": False, "error": "empty query"}
        try:
            from retrieval_hub import retrieval_hub
            result = retrieval_hub(query, top_k=top_k, session_id=session_id,
                                   include_web=False, enable_crag=False)
            merged = result.get("results", [])
            merged = merged[:top_k]
            # 确保每条结果都有 _content_type 标记（兼容旧版 retrieval_hub 缓存）
            for _item in merged:
                if '_content_type' in _item:
                    continue
                _content = _item.get('content', '')
                _source = _item.get('source', '')
                if _content.strip().startswith('{') and '"name"' in _content and '"trigger"' in _content:
                    _item['_content_type'] = 'metadata'
                elif _source in ('user', 'ai', 'dag_msg') or '用户:' in _content or '系统:' in _content or '助手:' in _content:
                    _item['_content_type'] = 'conversation'
                else:
                    _item['_content_type'] = 'summary'
            return {"results": merged, "success": True,
                    "stats": result.get("stats", {})}
        except Exception as e:
            # 降级到旧 recall
            try:
                self._ensure()
                fallback = self._entry.recall(query, top_k)
                return {"results": fallback[:top_k], "success": True,
                        "fallback": True, "error": str(e)}
            except Exception as e2:
                return {"results": [], "success": False,
                        "error": f"smart_retrieval failed: {e}, fallback failed: {e2}"}

    def rlm_compress(self, p: dict) -> dict:
        """RLM 递归环境压缩 — 将超长消息列表递归分解为摘要

        用于 ContextEngine 紧急裁剪时替代简单截断。
        """
        messages = p.get("messages", [])
        max_summary_tokens = p.get("max_tokens", 500)
        try:
            content = ""
            if isinstance(messages, list):
                content = "\n".join(
                    f"[{m.get('role','?')}] {str(m.get('content',''))[:300]}"
                    for m in messages[:20]
                )
            elif isinstance(messages, str):
                content = messages

            from rlm_env import RLMEnvironment
            rlm = RLMEnvironment(content)
            # 用 RLM 的 slice 函数递归分解
            parts = rlm.auto_slice(max_chunk=300, overlap=30)
            summarized = []
            for chunk in parts[:5]:
                summarized.append(chunk[:200])
            return {"compressed": "\n".join(summarized), "parts": len(parts),
                    "original_len": len(content)}
        except Exception as e:
            return {"compressed": "", "error": str(e)}

    def memory_search(self, p: dict) -> dict:
        """OpenClaw standard memory_search interface.

        Converts GalaxyOS retrieval results to OpenClaw MemorySearchResult format:
          {corpus, path, score, snippet, ...}

        The Plugin (index.js) MemorySearchManager.search() calls this via UDS.
        """
        query = p.get("query", "")
        max_results = p.get("max_results", 10) or 10
        min_score = p.get("min_score", 0.0) or 0.0
        sources = p.get("sources", None)  # ["memory"] | ["sessions"] | None
        if not query:
            return {"results": [], "error": "empty query"}
        try:
            # Use smart_retrieval as the actual retrieval backend
            result = self.smart_retrieval({"query": query, "top_k": max_results})
            if not result.get("success"):
                return {"results": [], "error": result.get("error", "retrieval failed")}
            raw_results = result.get("results", [])
            # Filter by min_score and convert to OpenClaw format
            openclaw_results = []
            for item in raw_results:
                score = item.get("score", 0.0) or 0.0
                if score < min_score:
                    continue
                content_type = item.get("_content_type", "unknown")
                source_tag = item.get("source", "memory")
                snippet = item.get("content", item.get("text", "")) or ""
                if isinstance(snippet, str):
                    snippet = snippet[:500]
                else:
                    snippet = str(snippet)[:500]
                # Build a stable path: memory/{session_id}_{idx}.md for conversation, or file path
                file_id = item.get("id", item.get("path", "")) or ""
                path_val = f"memory/{file_id}" if file_id else "memory/recall_result"
                openclaw_results.append({
                    "corpus": "memory",
                    "path": path_val,
                    "title": item.get("title", "") or "",
                    "kind": content_type,
                    "score": score,
                    "snippet": snippet,
                    "id": file_id or path_val,
                    "source": source_tag,
                })
            # Sort by score descending
            openclaw_results.sort(key=lambda x: x["score"], reverse=True)
            return {"results": openclaw_results[:max_results]}
        except Exception as e:
            return {"results": [], "error": str(e)}

    def store(self, p: dict) -> dict:
        self._ensure()
        return self._entry.store(p.get("content", ""), source=p.get("source", "user"),
                                 session_id=p.get("session_id", ""))

    def context_assemble(self, p: dict) -> dict:
        """全论文模块编排上下文组装 — 声明式流水线驱动

        供 JS ContextEngine assemble() 调用。
        所有 Phase 在 galaxy_pipeline.py 中以清单声明，
        自动执行、跳过、依赖管理。
        """
        query = p.get("query", "")
        session_id = p.get("session_id", "")
        top_k = p.get("top_k", 5)
        mode = p.get("mode", "full")  # quick | full
        try:
            result: Dict[str, Any] = {"session_id": session_id, "layers": {}, "decisions": {}}

            # ═══ quick 模式：只跑 IsREL（几百微秒），JS 用来决定是否要跑全量 ═══
            if mode == "quick":
                try:
                    from galaxy_pipeline import _phase_isrel
                    _ctx: Dict[str, Any] = {}
                    _phase_isrel(query, session_id, top_k, result, _ctx, self)
                except Exception:
                    pass
                result["success"] = True
                return result

            # ═══ 声明式流水线执行（galaxy_pipeline.py 驱动）═══
            try:
                from galaxy_pipeline import build_pipeline, run_pipeline
                _pipeline = build_pipeline()
                _ctx: Dict[str, Any] = {}
                run_pipeline(_pipeline, query, session_id, top_k, result, _ctx, self)
            except Exception as e:
                result["pipeline_error"] = str(e)

            # ═══ IsREL 跳过时直接返回 ═══
            if result.get("skipped") == "isrel_no_retrieve":
                result["injection"] = ""
                result["success"] = True
                return result

            # ── 汇总：合并所有 layer 为单一注入文本 ────
            parts = []

            # ═══ SKILL0 课程状态：报告哪些技能已内化（不再注入）═
            _s0 = result["decisions"].get("skill0", {})
            if _s0:
                try:
                    if hasattr(self, '_skill_curriculum'):
                        _sc = self._skill_curriculum
                        _active = sorted(_sc.active_skills)
                        _removed = sorted(set(_sc._all_skills) - _sc.active_skills) if hasattr(_sc, '_all_skills') else []
                        _lines = [f"[SKILL0] Stage {_s0.get('stage',0)+1}/5 | 可用 {len(_active)}/{len(_sc._all_skills) if hasattr(_sc,'_all_skills') else len(_active)+len(_removed)}"]
                        if _active:
                            _lines.append(f"  可用: {', '.join(_active)}")
                        if _removed:
                            _lines.append(f"  已内化: {', '.join(_removed)}")
                        parts.append('\n'.join(_lines))
                except Exception:
                    pass

            # ═══ DAG 能力节点：APO/ThinkingEnhanced 产出的改进建议 ══
            try:
                _dag = self._get_dag() if hasattr(self, '_get_dag') else None
                if _dag:
                    _caps = _dag.query_capability_nodes(limit=3, session_key='xiaoyi-claw-dag')
                    if _caps:
                        _cap_lines = []
                        for _c in _caps:
                            _name = _c.get('name', '')[:60]
                            _sug = _c.get('suggestion', '')[:120]
                            if _name and _sug:
                                _cap_lines.append(f"[{_name}] {_sug}")
                        if _cap_lines:
                            parts.append("[APO 优化建议]\n" + '\n'.join(_cap_lines))
            except Exception:
                pass
            if result["layers"].get("blob_arena_restored"):
                blob_parts = []
                for b in result["layers"]["blob_arena_restored"][:3]:
                    blob_parts.append(f"[Blob#{b['blob_id'][:8]}] {b['content'][:400]}")
                parts.append("[无损上下文还原]\n" + "\n".join(blob_parts))
            if result["layers"].get("memgpt_context"):
                parts.append(result["layers"]["memgpt_context"])
            if result["layers"].get("spatial_scene"):
                parts.append(f"[场景] {result['layers']['spatial_scene']}")
            if result["layers"].get("ssm_predicted"):
                preds = ", ".join(f"{p['id']}({p['prob']})" for p in result["layers"]["ssm_predicted"][:3])
                parts.append(f"[记忆预测] 下一步可能需要: {preds}")
            if result["layers"].get("memoryos_profile"):
                parts.append(f"[长期画像] {result['layers']['memoryos_profile']}")
            if result["layers"].get("kora_pattern"):
                parts.append(f"[行为模式] {result['layers']['kora_pattern']}")
            if result["layers"].get("code_aware"):
                parts.append(f"[代码分析] {result['layers']['code_aware']}")
            if result["layers"].get("thinking_enhanced"):
                parts.append(f"[多路推理] {result['layers']['thinking_enhanced']}")
            if result["layers"].get("raptor_summaries"):
                parts.append("[历史摘要] " + "\n".join(result["layers"]["raptor_summaries"]))
            result["injection"] = "\n\n".join(parts)[:6000]
            result["success"] = True
            return result

        except Exception as e:
            return {"success": False, "error": str(e), "session_id": session_id}

    def verify(self, p: dict) -> dict:
        """验证 — 走 MultiSourceCrossValidator 多源交叉验证"""
        claim = p.get("claim", "")
        try:
            from enhanced_hallucination_guard import (
                EnhancedHallucinationGuard
            )
            guard = EnhancedHallucinationGuard(str(WORKSPACE))
            result = guard.verify_with_cross_validation(
                statement=claim,
                use_web_search=True,
                use_thinking=False  # Worker 内不做 LLM 思考，太贵
            )
            return {
                "claim": claim,
                "statement": result.get("statement", claim),
                "is_reliable": result.get("is_reliable", False),
                "final_confidence": result.get("final_confidence", 0.5),
                "verification_level": result.get("verification_level", ""),
                "recommendation": result.get("recommendation", ""),
                "cross_validation": {
                    "consensus": result.get("cross_validation", {}).get("consensus", "insufficient_data"),
                    "agreements": result.get("cross_validation", {}).get("agreements", 0),
                    "disagreements": result.get("cross_validation", {}).get("disagreements", 0),
                    "sources_count": len(result.get("cross_validation", {}).get("sources", [])),
                },
                "thinking_analysis": result.get("thinking_analysis"),
                "success": True
            }
        except ImportError as e:
            sys.stderr.write(f"[claw-worker] MultiSourceCrossValidator import failed: {e}\n")
            # 降级到 enhanced_recall
            self._ensure()
            if hasattr(self._entry.xiaoyi_claw, 'enhanced_recall'):
                results = self._entry.xiaoyi_claw.enhanced_recall(claim, top_k=3)
                return {"claim": claim, "results": results, "success": True, "fallback": "cross_validator_not_available"}
            return {"claim": claim, "error": "verify not available", "success": False}
        except Exception as e:
            return {"claim": claim, "error": str(e), "success": False}

    def understand_image(self, p: dict) -> dict:
        """图像理解 — VLM 第三通道 (glm-4v-plus)
        使用独立轻量 VLM Client，不经过 SmartProcessor 重初始化
        """
        try:
            if not hasattr(self, '_vlm'):
                from openai import OpenAI
                VLM_API_KEY = "YOUR_VLM_API_KEY"
                VLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
                self._vlm = OpenAI(api_key=VLM_API_KEY, base_url=VLM_BASE_URL)
            
            resp = self._vlm.chat.completions.create(
                model="glm-4v-plus",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": p.get("image_url", "")}},
                        {"type": "text", "text": p.get("prompt", "请详细描述这张图片的内容")},
                    ]
                }],
                max_tokens=p.get("max_tokens", 1000),
            )
            msg = resp.choices[0].message
            content = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", None) or ""
            # glm-4v-plus 有时将答案放在 reasoning_content
            if not content and reasoning:
                content = reasoning
                reasoning = ""
            return {"content": content, "reasoning": reasoning, "model": "glm-4v-plus", "success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==================== unified_entry 全接口迁移 ====================

    def _ensure_entry(self):
        if self._entry is None:
            self._ensure()
        return self._entry

    def answer(self, p: dict) -> dict:
        """智能回答（SmartProcessor 路由优先）"""
        query = p.get("query", "")
        try:
            entry = self._ensure_entry()
            if entry.xiaoyi_claw:
                return entry.xiaoyi_claw.fast_generate(query, top_k=3)
        except Exception:
            pass
        return {"error": "不可用"}

    def smart_process(self, p: dict) -> dict:
        """SmartProcessor RPC 端点 — 查询改写 + 检索 + 回答合成"""
        query = p.get("query", "")
        if not query:
            return {"error": "缺少 query"}
        try:
            entry = self._ensure_entry()
            from smart_processor import SmartProcessor
            sp = SmartProcessor(
                llm_flash=entry.xiaoyi_claw.llm_flash if entry.xiaoyi_claw else None,
                llm_pro=entry.xiaoyi_claw.llm_pro if entry.xiaoyi_claw else None,
                persona_context=p.get("persona", ""),
            )
            return sp.process(query, top_k=p.get("top_k", 5))
        except Exception as e:
            return {"error": str(e)}

    def forget(self, p: dict) -> dict:
        """智能遗忘"""
        entry = self._ensure_entry()
        memory_id = p.get("memory_id", "")
        if entry.memory and hasattr(entry.memory, 'forget'):
            return entry.memory.forget(memory_id)
        return {"error": "遗忘功能不可用"}

    def learn_preference(self, p: dict) -> dict:
        """学习用户偏好"""
        entry = self._ensure_entry()
        key = p.get("key", "")
        value = p.get("value", "")
        if entry.xiaoyi_claw and hasattr(entry.xiaoyi_claw, 'learn_preference'):
            return {"result": entry.xiaoyi_claw.learn_preference(key, value)}
        return {"error": "偏好学习不可用"}

    def learn_correction(self, p: dict) -> dict:
        """学习用户纠正"""
        entry = self._ensure_entry()
        original = p.get("original", "")
        corrected = p.get("corrected", "")
        if entry.xiaoyi_claw and hasattr(entry.xiaoyi_claw, 'learn_correction'):
            return {"result": entry.xiaoyi_claw.learn_correction(original, corrected)}
        return {"error": "纠正学习不可用"}

    def link_task_memory(self, p: dict) -> dict:
        """关联任务和记忆"""
        entry = self._ensure_entry()
        task_id = p.get("task_id", "")
        memory_id = p.get("memory_id", "")
        link_type = p.get("link_type", "related_to")
        if entry.xiaoyi_claw and hasattr(entry.xiaoyi_claw, 'link_task'):
            return {"result": entry.xiaoyi_claw.link_task(task_id, memory_id, link_type)}
        return {"error": "任务关联不可用"}

    def remember(self, p: dict) -> dict:
        """记忆（store 别名）"""
        return self.store(p)

    def learn(self, p: dict) -> dict:
        """学习反馈"""
        entry = self._ensure_entry()
        feedback = p.get("feedback", "")
        return entry.execute_workflow("learn_from_mistake", {"feedback": feedback})

    def get_entity(self, p: dict) -> dict:
        """查询实体"""
        entry = self._ensure_entry()
        name = p.get("name", "")
        return entry.get_entity(name)

    def recall_images(self, p: dict) -> dict:
        """检索图像记忆"""
        entry = self._ensure_entry()
        query = p.get("query", "")
        top_k = p.get("top_k", 10)
        if entry.workflow_engine:
            result = entry.workflow_engine.execute_workflow("multimodal_recall", {"query": query})
            if hasattr(result, 'status') and result.status.value == "completed":
                return {"results": result.results}
        return {"results": []}

    def ocr_image(self, p: dict) -> dict:
        """OCR 文字识别"""
        entry = self._ensure_entry()
        image_source = p.get("image_source", "")
        try:
            if hasattr(entry.xiaoyi_claw, 'ocr_image'):
                return entry.xiaoyi_claw.ocr_image(image_source)
            return {"success": False, "error": "ocr_image not available"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute_workflow(self, p: dict) -> dict:
        """执行工作流 — 透传 unified_entry"""
        entry = self._ensure_entry()
        scenario = p.get("scenario", "")
        input_data = p.get("input_data")
        return entry.execute_workflow(scenario, input_data)

    def list_workflows(self, _p: dict) -> dict:
        """列出所有工作流"""
        entry = self._ensure_entry()
        wfs = entry.list_workflows()
        return {"workflows": wfs, "count": len(wfs)}

    def get_workflow_info(self, p: dict) -> dict:
        """获取工作流信息"""
        entry = self._ensure_entry()
        name = p.get("name", "")
        return entry.get_workflow_info(name)

    def call_module(self, p: dict) -> dict:
        """调用单个模块"""
        entry = self._ensure_entry()
        module_name = p.get("module_name", "")
        action = p.get("action")
        input_data = p.get("input_data")
        return entry.call_module(module_name, action, input_data)

    def list_modules(self, _p: dict) -> dict:
        """列出所有可用模块"""
        entry = self._ensure_entry()
        mods = entry.list_modules()
        return {"modules": mods, "count": len(mods)}

    def get_module_info(self, p: dict) -> dict:
        """获取模块信息"""
        entry = self._ensure_entry()
        module_name = p.get("module_name", "")
        return entry.get_module_info(module_name)

    def get_status(self, _p: dict) -> dict:
        """获取系统完整状态"""
        entry = self._ensure_entry()
        return entry.get_status()

    # ==================== 以上为 unified_entry 全接口迁移 ====================

    def rccam(self, p: dict) -> dict:
        """R-CCAM cognitive cycle via XiaoYiClawLLM.process()"""
        self._ensure()
        session_key = p.get("sessionKey", "") or getattr(self, '_last_session_key', '')
        user_input = p.get("user_input", "")
        
        # 没 session_key 时扫 DAG 找最新活跃 session
        if not session_key:
            try:
                dag = self._get_dag()
                _keys = dag.get_all_session_keys()
                if _keys:
                    # 优先找 agent:main:direct:* 模式(OpenClaw 主会话)
                    _direct = [k for k in _keys if 'agent:main:direct' in k]
                    _target = _direct if _direct else _keys
                    # 取这些 key 的最新节点时间戳
                    _candidates = []
                    for _k in _target[:10]:
                        try:
                            _nodes = dag.dag.get_session_nodes(_k, limit=1)
                            if _nodes:
                                _ts = max(n.timestamp or 0 for n in _nodes)
                                _candidates.append((_ts, _k))
                        except Exception:
                            pass
                    if _candidates:
                        _candidates.sort(key=lambda x: x[0], reverse=True)
                        session_key = _candidates[0][1]
            except Exception:
                pass
        
        if session_key and user_input:
            try:
                dag = self._get_dag()
                dag.add_message_with_scene(session_key, "user", user_input)
            except Exception:
                pass
        
        if hasattr(self._entry.xiaoyi_claw, 'process'):
            _result = self._entry.xiaoyi_claw.process(
                user_input=user_input,
                max_cycles=p.get("max_cycles", 1),
                store_memory=p.get("store_memory", True),
                has_image=p.get("has_image", False),
                image_source=p.get("image_source"),
                session_key=session_key,
            )
            self._last_rccam_ts = time.time()
            # 通知 Galaxy Kernel 进行后处理（有 answer 就触发，不依赖 action_success）
            if _result.get('answer'):
                try:
                    _galaxy_pending.append({
                        'type': 'post_response',
                        'query': user_input,
                        'answer': _result['answer'],
                        'confidence': _result.get('confidence', 0.5),
                        'ts': time.time(),
                    })
                except Exception:
                    pass
            return _result
        raise RuntimeError("rccam not available")

    def hardinfo(self, _p: dict) -> dict:
        """返回缓存的硬件信息"""
        return {k: str(v) for k, v in self._hardware_info.items()}

    def _get_vector_api_status(self) -> dict:
        """内部辅助：获取 VectorAPI 状态（供 health() / vector_info() 复用）"""
        global _vector_api
        if _vector_api is None:
            return {"available": False, "arch": "scalar"}
        bi = _vector_api.backend_info
        return {
            "available": True,
            "arch": bi.arch.value,
            "lane_count": bi.lane_count,
            "register_width_bits": bi.register_width_bits,
            "supports_fma": bi.supports_fma,
            "description": bi.description,
        }

    def vector_info(self, _p: dict) -> dict:
        """跨平台 SIMD 向量计算能力报告
        
        返回当前平台的向量计算后端信息：
        - 架构 (AVX-512/AVX2/AVX/SSE/NEON/SVE/Scalar)
        - SIMD lane 数 (每寄存器并行 float32 数)
        - 寄存器位宽
        - FMA 支持
        - 遮蔽运算支持
        """
        global _vector_api
        if _vector_api is None:
            return {"available": False, "error": "VectorAPI not initialized"}
        bi = _vector_api.backend_info
        return {
            "available": True,
            "arch": bi.arch.value,
            "lane_count": bi.lane_count,
            "register_width_bits": bi.register_width_bits,
            "supports_fma": bi.supports_fma,
            "supports_masking": bi.supports_masking,
            "description": bi.description,
        }

    def implicit_feedback(self, p: dict) -> dict:
        """隐式偏好学习 — 记录用户纠错/校正信号
        
        当 XiaoYiClawLLM 的 process() 检测到用户不满或纠正时，
        将信号持久化到 .learnings/implicit_preferences.jsonl，
        长期积累可提升重放缓冲区质量。
        """
        signal_text = p.get("signal", "")
        context = p.get("context", "")
        confidence = p.get("confidence", 0.5)
        try:
            learn_dir = os.path.join(WORKSPACE, ".learnings") if WORKSPACE else ""
            if learn_dir and os.path.exists(learn_dir):
                pref_path = os.path.join(learn_dir, "implicit_preferences.jsonl")
                entry = {
                    "id": f"IP-{int(time.time())}-{os.urandom(4).hex()}",
                    "signal": signal_text,
                    "context": context[:300],
                    "confidence": confidence,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": "implicit_feedback_rpc",
                }
                with open(pref_path, "a") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                return {"ok": True, "written": True}
            return {"ok": True, "written": False, "note": "no .learnings dir"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def restore_context(self, p: dict) -> dict:
        """
        L3: 跨会话记忆恢复 + 人格恢复联动
        
        从 DAG 检索最近记忆摘要 + 最新人格快照。
        """
        session_key = p.get("sessionKey", "default")
        recent_days = p.get("recentDays", 3)
        try:
            from dag_context_manager import DAGContextManager
            from DAGIntegration_addon import DAGIntegration
            import os
            dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
            if not os.path.exists(dag_db):
                dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
            dag = DAGContextManager(db_path=dag_db)
            from xiaoyi_memory import XiaoyiMemoryV2
            memory = XiaoyiMemoryV2()
            integration = DAGIntegration(dag, memory=memory)
            summary = integration.cross_session_memory_restore(session_key, recent_days)
            
            # L3: 同时拉最新人格快照
            persona_text = ""
            try:
                persona_nodes = dag.get_session_nodes(
                    session_key=session_key,
                    node_type="persona",
                    limit=1
                )
                if persona_nodes:
                    persona_text = persona_nodes[0].content[:2000]
            except Exception:
                pass
            
            # 如果 DAG 无人格快照，读文件
            if not persona_text:
                persona_path = os.path.join(WORKSPACE, "persona.md")
                if os.path.exists(persona_path):
                    with open(persona_path, "r", encoding="utf-8") as f:
                        persona_text = f.read(2000)
            
            return {
                "restored_text": summary or "",
                "persona_text": persona_text,
                "recent_days": recent_days,
                "persona_source": "dag_snapshot" if persona_nodes else "persona.md",
            }
        except Exception as e:
            return {"restored_text": "", "persona_text": "", "error": str(e)}

    def _get_dag(self):
        """获取 DAGIntegration 实例（懒加载）
        
        DAGIntegration 包裹 DAGContextManager，提供 auto_summarize、
        add_message_with_scene 等完整方法集。
        统一 DB: 优先 workspace，再 fallback HOME。
        """
        if not hasattr(self, '_dag'):
            from dag_context_manager import DAGContextManager
            from DAGIntegration_addon import DAGIntegration
            import os
            dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
            if not os.path.exists(dag_db):
                dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
            raw = DAGContextManager(db_path=dag_db)
            self._dag = DAGIntegration(dag=raw)
        return self._dag

    def dag_summary(self, p: dict) -> dict:
        """从 DAG 库获取指定会话的最新摘要（带熔断）"""
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"summary": ""}
        ctx = self._get_session_ctx(session_id)
        try:
            return self._dag_cb.call(self._do_dag_summary, session_id)
        except RuntimeError as e:
            return {"summary": "", "error": str(e), "circuit": "open"}

    def _do_dag_summary(self, session_id: str) -> dict:
        import sqlite3
        dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
        if not os.path.exists(dag_db):
            return {"summary": "", "note": "dag db not found"}
        conn = sqlite3.connect(dag_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT content FROM dag_nodes WHERE session_key=? AND is_summary=1 ORDER BY timestamp DESC LIMIT 1",
            (session_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row and row["content"]:
            return {"summary": row["content"]}
        return {"summary": ""}

    def dag_ingest(self, p: dict) -> dict:
        """
        L4: 将消息写入 DAG 节点 + 人格注入 + ZMQ 推送 ingest 事件

        首次写入该 session 时自动从 SOUL.md / IDENTITY.md / AGENTS.md
        三文件组装人格定义注入 DAG（CRITICAL 优先，永不压缩）。
        后续每 10 条消息存一次人格快照（文件变更时更新）。
        """
        session_id = p.get("sessionId", "")
        role = p.get("role", "user")
        content = p.get("content", "")
        if not session_id or not content:
            return {"_dag_degraded": True, "reason": "missing params"}
        ctx = self._get_session_ctx(session_id)
        try:
            dag = self._get_dag()
            dag.add_message_with_scene(session_id, role, content)
            self._last_session_key = session_id

            # 首次写入 → 查 DAG 有没有该 session 的 persona 节点
            # 没有则自组装注入（SOUL.md + IDENTITY.md + AGENTS.md）
            try:
                persona_nodes = dag.dag.get_session_nodes(
                    session_key=session_id, node_type="persona", limit=1
                )
                if not persona_nodes:
                    persona_parts = []
                    for fname, label in [
                        ("IDENTITY.md", "=== 身份定义 ==="),
                        ("SOUL.md", "=== 性格风格 ==="),
                        ("AGENTS.md", "=== 系统规则 ==="),
                    ]:
                        fp = os.path.join(WORKSPACE, fname)
                        if os.path.exists(fp):
                            with open(fp, "r", encoding="utf-8") as f:
                                text = f.read(2000)
                            persona_parts.append(f"{label}\n{text.strip()}")
                    if persona_parts:
                        persona_text = "\n\n".join(persona_parts)
                        dag.dag.add_persona_node(
                            session_key=session_id,
                            persona_text=persona_text,
                            source="bootstrap_inject",
                        )
            except Exception:
                pass

            # 每 10 条消息存一次人格快照（文件有变更时更新）
            try:
                node_count = dag.dag.get_node_count()
                total = sum(c for cat in node_count.values() for c in cat.values())
                if total > 0 and total % 10 == 0:
                    persona_path = os.path.join(WORKSPACE, "persona.md")
                    if os.path.exists(persona_path):
                        with open(persona_path, "r", encoding="utf-8") as f:
                            persona_text = f.read(2000)
                        dag.dag.add_persona_node(
                            session_key=session_id,
                            persona_text=persona_text,
                            source="auto_snapshot",
                        )
            except Exception:
                pass

            # ZMQ 推送 ingest 事件
            _zmq_pub_event("dag_ingest", {"session": session_id, "role": role})

            # ── MN-RU 增量索引：异步推送新节点到小索引 ──
            try:
                _push_to_hnsw_mini({
                    'content': content[:1000],
                    'source': 'dag',
                    'phase': role,
                    'importance': 0.5,
                    'timestamp': time.time(),
                })
            except Exception:
                pass

            return {"ok": True, "session": session_id}
        except Exception as e:
            return {"_dag_degraded": True, "reason": f"ingest failed: {e}"}

    def persona_snapshot(self, p: dict) -> dict:
        """
        L4: 手动触发人格快照写入 DAG
        """
        session_id = p.get("sessionId", "default")
        try:
            persona_path = os.path.join(WORKSPACE, "persona.md")
            if not os.path.exists(persona_path):
                return {"ok": False, "reason": "persona.md not found"}
            with open(persona_path, "r", encoding="utf-8") as f:
                persona_text = f.read(2000)
            dag = self._get_dag()
            dag.dag.add_persona_node(
                session_key=session_id,
                persona_text=persona_text,
                source="explicit_snapshot",
            )
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def dag_status(self, p: dict) -> dict:
        """获取 DAG 压缩状态"""
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"_dag_degraded": True, "reason": "missing sessionId"}
        try:
            dag = self._get_dag()
            needs_compact, stats = dag.dag.should_compact(session_id)
            return {"needs_compact": needs_compact, "stats": stats}
        except Exception as e:
            return {"_dag_degraded": True, "reason": f"status failed: {e}"}

    def dag_assemble(self, p: dict) -> dict:
        """
        从 DAG 组装上下文 + mmap 写入共享内存（session 隔离 + 熔断保护）。
        优先使用 cycle-aware 新策略，无 R-CCAM 数据时回退旧策略。
        """
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"text": ""}
        ctx = self._get_session_ctx(session_id)
        try:
            return self._assemble_cb.call(self._do_dag_assemble, p, session_id)
        except RuntimeError as e:
            return {"text": "", "error": str(e), "circuit": "open"}

    def _do_dag_assemble(self, p: dict, session_id: str) -> dict:
        dag = self._get_dag()
        fresh_cycles = p.get("freshCycles", 3)
        max_tokens = p.get("maxTokens", 240000)
        rccam_cycles = dag.dag.get_rccam_session_cycles(session_id)
        if rccam_cycles:
            text, stats = dag.dag.assemble_from_cycles(
                session_key=session_id,
                fresh_cycles=fresh_cycles,
                max_tokens=max_tokens,
            )
        else:
            text, stats = dag.dag.assemble_context(
                session_key=session_id,
                fresh_tail_count=p.get("maxRecentMessages", 20),
                max_tokens=max_tokens,
            )
        result = {"text": text or "", "stats": stats}
        _mmap_write("dag_context", result)
        return result

    def dag_compact(self, p: dict) -> dict:
        """执行 DAG 增量压缩 + ZMQ 推送 + mmap 写入"""
        session_id = p.get("sessionId", "")
        batch_size = p.get("batchSize", 10)
        if not session_id:
            return {"summarized": 0, "error": "missing sessionId"}
        try:
            dag = self._get_dag()
            override_tokens = p.get("overrideLeafTokens", None)
            if override_tokens is not None:
                old_threshold = dag.dag.leaf_chunk_tokens
                dag.dag.leaf_chunk_tokens = override_tokens
                try:
                    result = dag.ensure_auto_compact(session_key=session_id)
                finally:
                    dag.dag.leaf_chunk_tokens = old_threshold
            else:
                result = dag.ensure_auto_compact(session_key=session_id)

            # ZMQ 推送压缩事件
            _zmq_pub_event("dag_compact", {
                "session": session_id,
                "summarized": result.get("summarized", 0),
            })

            # compact 后 mmap 刷新最新上下文
            try:
                text, stats = dag.dag.assemble_context(session_key=session_id, fresh_tail_count=20, max_tokens=240000)
                if text:
                    _mmap_write("dag_context", {"text": text, "stats": stats})
            except Exception:
                pass

            return result
        except Exception as e:
            return {"summarized": 0, "error": str(e)}

    # ========================================================================
    # R-CCAM DAG 新方法
    # ========================================================================

    def rccam_dag_stats(self, p: dict) -> dict:
        """获取 R-CCAM DAG 全景统计"""
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"error": "missing sessionId"}
        try:
            dag = self._get_dag()
            stats = dag.dag.get_rccam_stats(session_id)
            _, _, _, compact_stats = dag.dag.rccam_compact_needed(session_id)
            stats.update(compact_stats)
            return stats
        except Exception as e:
            return {"error": str(e)}

    def rccam_compact_needed(self, p: dict) -> dict:
        """检查是否需要触发 R-CCAM 压缩（session 隔离）"""
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"needs": False}
        ctx = self._get_session_ctx(session_id)
        try:
            dag = self._get_dag()
            needs_soft, needs_hard, compressible, stats = dag.dag.rccam_compact_needed(session_id)
            ctx._compact_state = stats
            return {"needs_soft": needs_soft, "needs_hard": needs_hard,
                    "compressible_cycles": compressible, "stats": stats}
        except Exception as e:
            return {"needs": False, "error": str(e)}

    def rccam_compact_cycle(self, p: dict) -> dict:
        """压缩一个指定的 R-CCAM cycle（session 锁 + 熔断保护）"""
        session_id = p.get("sessionId", "")
        cycle_id = p.get("cycleId", "")
        if not session_id or not cycle_id:
            return {"error": "missing sessionId or cycleId"}
        ctx = self._get_session_ctx(session_id)
        try:
            return self._compact_cb.call(self._do_compact_cycle, session_id, cycle_id, ctx)
        except RuntimeError as e:
            return {"error": str(e), "circuit": "open"}

    def _do_compact_cycle(self, session_id: str, cycle_id: str, ctx: SessionContext) -> dict:
        """在 session 锁内执行压缩（防止同 session 并发压缩）"""
        with ctx._compact_lock:
            dag = self._get_dag()
            result = dag.dag.compact_rccam_cycle(session_id, cycle_id)
            if result:
                _zmq_pub_event("rccam_compact", {"session": session_id, "cycle": cycle_id})
                # ── Session 级索引：从压缩结果构建 session embedding ──
                try:
                    cycle_nodes = dag.dag.expand_rccam_cycle(session_id, cycle_id)
                    if cycle_nodes:
                        user_input = ""
                        cognition = ""
                        answer = ""
                        for node in cycle_nodes:
                            phase = node.get("phase_name", node.get("node_type", ""))
                            content = node.get("content", "")
                            if phase == "user_input":
                                user_input = content[:500]
                            elif phase in ("cognition", "control"):
                                cognition += content[:300] + " "
                            elif phase == "action":
                                answer = content[:500]
                        session_text = f"用户: {user_input}\n分析: {cognition.strip()}\n回答: {answer}"
                        if len(session_text) > 100:
                            _push_to_session_index({
                                'session_text': session_text[:2000],
                                'cycle_id': cycle_id,
                                'timestamp': time.time(),
                            })
                except Exception:
                    pass
            return {"ok": True, "result": result}

    def expand_rccam_cycle(self, p: dict) -> dict:
        """展开 R-CCAM cycle 获取原始阶段节点"""
        session_id = p.get("sessionId", "")
        cycle_id = p.get("cycleId", "")
        if not session_id or not cycle_id:
            return {"nodes": []}
        try:
            dag = self._get_dag()
            nodes = dag.dag.expand_rccam_cycle(session_id, cycle_id)
            return {"nodes": nodes}
        except Exception as e:
            return {"error": str(e), "nodes": []}

    def cognitive_compress_dag(self, p: dict) -> dict:
        """对 dag_nodes 旧消息执行认知压缩（session 隔离 + 熔断）"""
        session_id = p.get("sessionId", "")
        max_to_compress = p.get("maxToCompress", 20)
        if not session_id:
            return {"error": "missing sessionId"}
        ctx = self._get_session_ctx(session_id)
        try:
            return self._compact_cb.call(
                self._do_cognitive_compress, session_id, max_to_compress, ctx)
        except RuntimeError as e:
            return {"error": str(e), "circuit": "open"}

    def _do_cognitive_compress(self, session_id: str, max_to_compress: int, ctx: SessionContext) -> dict:
        with ctx._compact_lock:
            dag = self._get_dag()
            result = dag.dag.cognitive_compress_dag_messages(
                session_key=session_id,
                max_to_compress=max_to_compress,
            )
            if result.get("summarized", 0) > 0:
                _zmq_pub_event("dag_cognitive_compress", {
                    "session": session_id,
                    "summarized": result["summarized"],
                    "nodes": result["nodes_affected"],
                })
            return result
            return result

    def save_memory(self, p: dict) -> dict:
        """将 AI 最终回答持久化到记忆系统（被 agent_end hook 调用）"""
        session_key = p.get("session_key", "")
        user_input = p.get("user_input", "")
        answer = p.get("answer", "")
        metadata = p.get("metadata", {})
        if not user_input and not answer:
            return {"saved": False, "reason": "no content"}
        try:
            entry = self._ensure_entry()
            if entry and hasattr(entry.xiaoyi_claw, 'process'):
                # 用 process 的 store_memory=True 路径持久化
                # 直接调用 remember() + DAG ingest
                if answer:
                    entry.xiaoyi_claw.remember(
                        content=f"Q: {user_input[:500]}\nA: {answer[:2000]}",
                        source="user",
                        metadata=metadata,
                    )
                # DAG ingest
                try:
                    integration = self._get_dag()
                    dag_dm = integration.dag if hasattr(integration, 'dag') else integration
                    sk = session_key or "xiaoyi-claw-dag"
                    if user_input:
                        dag_dm.add_message(sk, "user", user_input[:2000])
                    if answer:
                        dag_dm.add_message(sk, "assistant", answer[:2000])
                except Exception:
                    pass
                return {"saved": True, "session_key": session_key}
            return {"saved": False, "reason": "xiaoyi_claw not ready"}
        except Exception as e:
            return {"saved": False, "error": str(e)}

    def dag_clear_session(self, p: dict) -> dict:
        """清空指定 session 在 DAG 中的节点 + BlobArena（新会话或 session 结束时调用）"""
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"cleared": False, "reason": "missing sessionId"}
        delete_arena = p.get("deleteArena", False)
        try:
            dag = self._get_dag()
            deleted = 0
            if dag and dag.dag:
                import sqlite3
                conn = sqlite3.connect(dag.dag.db_path)
                try:
                    c = conn.execute(
                        "DELETE FROM dag_nodes WHERE session_key=?",
                        (session_id,)
                    )
                    deleted += c.rowcount
                except Exception:
                    pass
                try:
                    c = conn.execute(
                        "DELETE FROM rccam_nodes WHERE session_key=?",
                        (session_id,)
                    )
                    deleted += c.rowcount
                except Exception:
                    pass
                conn.commit()
                conn.close()

            # 清理 BlobArena（per-session 目录 rm -rf）
            blob_deleted = False
            if delete_arena:
                try:
                    if dag and dag.dag:
                        blob_deleted = dag.dag.delete_blob_arena(session_id)
                    else:
                        from blob_arena import delete_session_arena
                        blob_deleted = delete_session_arena(session_id)
                except Exception:
                    pass

            return {"cleared": True, "deleted_nodes": deleted, "blob_arena_deleted": blob_deleted}
        except Exception as e:
            return {"cleared": False, "error": str(e)}

    def mmap_cleanup(self, _p: dict) -> dict:
        """清理 mmap 共享内存文件"""
        results = {}
        for path_key, path in [("mmap", MMAP_PATH), ("heartbeat", HB_PATH)]:
            try:
                if os.path.exists(path):
                    os.unlink(path)
                    results[path_key] = "deleted"
                else:
                    results[path_key] = "not_found"
            except Exception as e:
                results[path_key] = f"error: {e}"
        return {"cleaned": True, "results": results}

    def shutdown(self, _p: dict) -> dict:
        _handle_shutdown()
        return {"ok": True, "message": "shutting down"}


# ========== 批量 RPC 独立函数（注册到 _METHODS） ==========

def _handle_batch(p: dict) -> dict:
    """批量 RPC：一次请求执行多个方法，返回结果数组
    
    params: { calls: [{method, params}, ...] }
    返回: { results: [{result}, ...], count: N }
    """
    calls = p.get("calls", [])
    if not calls:
        return {"results": [], "error": "empty calls"}
    results = []
    for call in calls:
        method = call.get("method", "")
        cparams = call.get("params", {})
        handler = _METHODS.get(method)
        if handler is None:
            results.append({"_error": f"unknown method: {method}"})
            continue
        try:
            results.append(handler(cparams))
        except Exception as e:
            results.append({"_error": str(e)})
    return {"results": results, "count": len(results)}


# ========== 跨会话DAG搜索 ==========

_dag_search_cache = None

def _dag_search(params):
    """跨会话DAG搜索 — UDS方法

    通过DAGContextManager.cross_session_search查询其他会话的历史记录。
    供ContextEngine assemble时补充smartRecall遗漏的旧会话关键记录。
    """
    global _dag_search_cache
    query = params.get("query", "")
    limit = params.get("limit", 5)
    exclude_session = params.get("exclude_session", None)
    if not query:
        return {"results": []}
    try:
        if _dag_search_cache is None:
            from dag_context_manager import DAGContextManager
            import os
            dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
            if not os.path.exists(dag_db):
                dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
            _dag_search_cache = DAGContextManager(db_path=dag_db)
        results = _dag_search_cache.cross_session_search(
            query=query,
            limit=limit,
            exclude_session=exclude_session,
        )
        return {"results": results}
    except Exception as e:
        return {"results": [], "error": str(e)}


# ========== 主循环 ==========

_METHODS = {}

def _init_methods(worker):
    global _METHODS
    _METHODS = {
        "ping": worker.ping,
        "health": worker.health,
        "recall": worker.recall,
        "store": worker.store,
        "verify": worker.verify,
        "rccam": worker.rccam,
        "hardinfo": worker.hardinfo,
        "implicit_feedback": worker.implicit_feedback,
        "context_assemble": worker.context_assemble,
        "rlm_compress": worker.rlm_compress,
        "dag_ingest": worker.dag_ingest,
        "persona_snapshot": worker.persona_snapshot,
        "dag_status": worker.dag_status,
        "dag_assemble": worker.dag_assemble,
        "dag_compact": worker.dag_compact,
        "dag_clear_session": worker.dag_clear_session,
        "mmap_cleanup": worker.mmap_cleanup,
        "dag_summary": worker.dag_summary,
        "build_system_prompt": worker.build_system_prompt,
        "verify_reply_style": worker.verify_reply_style,
        "understand_image": worker.understand_image,
        "get_persona_core": worker.get_persona_core,
        "restore_context": worker.restore_context,
        "answer": worker.answer,
        "forget": worker.forget,
        "learn_preference": worker.learn_preference,
        "learn_correction": worker.learn_correction,
        "link_task_memory": worker.link_task_memory,
        "remember": worker.remember,
        "learn": worker.learn,
        "get_entity": worker.get_entity,
        "recall_images": worker.recall_images,
        "ocr_image": worker.ocr_image,
        "execute_workflow": worker.execute_workflow,
        "list_workflows": worker.list_workflows,
        "get_workflow_info": worker.get_workflow_info,
        "call_module": worker.call_module,
        "list_modules": worker.list_modules,
        "get_module_info": worker.get_module_info,
        "rccam_dag_stats": worker.rccam_dag_stats,
        "rccam_compact_needed": worker.rccam_compact_needed,
        "rccam_compact_cycle": worker.rccam_compact_cycle,
        "expand_rccam_cycle": worker.expand_rccam_cycle,
        "cognitive_compress_dag": worker.cognitive_compress_dag,
        "shutdown": worker.shutdown,
        "batch": _handle_batch,
        "get_status": worker.get_status,
        "smart_process": worker.smart_process,
        "dag_search": _dag_search,
        "memory_search": worker.memory_search,
        "smart_retrieval": worker.smart_retrieval,
        "save_memory": worker.save_memory,
        "vector_info": worker.vector_info,
    }


# ============================================================
# 单线程串行 UDS 服务端 — 一次只处理一个请求，避免 GIL 死锁
# ============================================================

REQUEST_TIMEOUT = 25.0  # 单请求超时秒，低于 JS 端 30s

def _read_http_body(conn, headers_part, body_bytes, content_length):
    """读取 HTTP 请求体的剩余部分"""
    remaining = content_length - len(body_bytes)
    while remaining > 0:
        try:
            conn.settimeout(REQUEST_TIMEOUT)
            chunk = conn.recv(min(65536, remaining))
            if not chunk:
                break
            body_bytes += chunk
            remaining = content_length - len(body_bytes)
        except (_socket.timeout, BlockingIOError):
            break
    return body_bytes


def _dispatch_request(methods_map, req_id, method, params):
    """执行 method handler 并返回 (result, error, traceback)"""
    if method not in methods_map:
        return None, f"unknown method: {method}", None
    result = methods_map[method](params)
    return result, None, None


def _send_http_reply(conn, status, data):
    """发送 HTTP JSON 响应（支持 CORS，兼容 REST 客户端）"""
    status_text = {200: "OK", 400: "Bad Request", 404: "Not Found",
                   405: "Method Not Allowed", 500: "Internal Server Error"}
    reason = status_text.get(status, "OK" if 200 <= status < 300 else "ERROR")
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    resp = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Access-Control-Allow-Origin: *\r\n"
        f"Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
        f"Access-Control-Allow-Headers: Content-Type\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("utf-8") + body
    try:
        conn.sendall(resp)
    except Exception:
        pass


def _uds_server_thread(methods_map):
    """阻塞式 UDS HTTP 服务端 — blocking accept，无 selectors"""
    import socket as _sock

    try:
        os.unlink(UDS_PATH)
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(UDS_PATH), exist_ok=True)

    server = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
    server.bind(UDS_PATH)
    server.listen(8)
    server.settimeout(1.0)
    os.chmod(UDS_PATH, 0o600)

    sys.stderr.write(f"[claw-worker] UDS (blocking, timeout={REQUEST_TIMEOUT}s) listening on {UDS_PATH}\n")

    while not _shutdown_flag:
        try:
            conn, _addr = server.accept()
        except _sock.timeout:
            continue
        except Exception:
            break
        try:
            conn.settimeout(REQUEST_TIMEOUT)
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                raw += chunk
            if raw:
                _handle_one_http_request(conn, raw, methods_map)
        except Exception as _e:
            sys.stderr.write(f"[claw-worker] UDS handler error: {_e}\n")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    server.close()
    try:
        os.unlink(UDS_PATH)
    except Exception:
        pass

def _handle_one_http_request(conn, raw_data, methods_map):
    """解析 HTTP 请求 → 串行执行 → 返回 JSON 响应

    支持两种模式：
    1. JSON-RPC: POST /  body={{"id":1, "method":"recall", "params":{{...}}}}
    2. REST:     GET/POST /<method>  (params 从 query string 或 JSON body 读取)
    """
    try:
        raw = raw_data if isinstance(raw_data, bytes) else raw_data.encode()
        parts = raw.split(b"\r\n\r\n", 1)
        headers_part = parts[0].decode("utf-8", errors="replace")
        body_bytes = parts[1] if len(parts) > 1 else b""

        # 解析 HTTP 方法和路径
        first_line = headers_part.split("\r\n")[0] if headers_part else ""
        http_parts = first_line.split(" ")
        http_method = http_parts[0].upper() if len(http_parts) > 0 else "POST"
        http_path = http_parts[1] if len(http_parts) > 1 else "/"

        # 解析 Content-Length
        content_length = 0
        for line in headers_part.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    content_length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass

        # 读取剩余 body
        if len(body_bytes) < content_length:
            body_bytes = _read_http_body(conn, headers_part, body_bytes, content_length)

        body_str = body_bytes.decode("utf-8", errors="replace")

        # ═══ CORS 预检 ═══
        if http_method == "OPTIONS":
            _send_http_reply(conn, 200, {"ok": True})
            return

        # ═══ REST API 路由 ═══
        if http_path.startswith("/") and http_path != "/":
            _handle_rest_request(conn, http_method, http_path, body_str, methods_map)
            return

        # ═══ GET / — REST API 索引 ═══
        if http_method == "GET" and http_path == "/":
            _send_http_reply(conn, 200, {
                "service": "GalaxyOS ClawWorker",
                "version": "7.3.2",
                "endpoints": {
                    "REST": "GET|POST /<method>  (see /rest for full list)",
                    "JSON-RPC": "POST /  with {id, method, params}",
                },
                "usage": {
                    "GET /health": "系统健康检查",
                    "GET /vector_info": "SIMD 向量计算能力",
                    "POST /recall": "记忆检索 (body: {query, top_k})",
                    "POST /store": "记忆存储 (body: {content, source})",
                    "POST /verify": "幻觉验证 (body: {claim})",
                }
            })
            return
        try:
            req = json.loads(body_str)
        except json.JSONDecodeError:
            _send_http_reply(conn, 400, {"id": None, "error": "invalid JSON"})
            return

        req_id = req.get("id")
        method = req.get("method", "")
        params = req.get("params", {})

        if method not in methods_map:
            _send_http_reply(conn, 404, {"id": req_id, "error": f"unknown method: {method}"})
            return

        t0 = time.time()
        try:
            result = methods_map[method](params)
            elapsed = round((time.time() - t0) * 1000, 1)
            # ═══ mmap 大 payload 路由：结果 >50KB 时走 mmap，UDS 只回引用 ═══
            result_json = json.dumps(result, ensure_ascii=False)
            if len(result_json) > 50000 and method in ("rccam", "recall", "store", "verify"):
                _mmap_key = f"resp_{method}_{req_id}_{int(time.time()*1000)}"
                _mmap_write(_mmap_key, result)
                _zmq_pub_event("mmap_result_ready", {"key": _mmap_key, "method": method, "size": len(result_json)})
                _send_http_reply(conn, 200, {
                    "id": req_id, "result": {"_mmap_key": _mmap_key, "_mmap_size": len(result_json)},
                    "timing_ms": elapsed
                })
            else:
                _send_http_reply(conn, 200, {"id": req_id, "result": result, "timing_ms": elapsed})
        except Exception as e:
            tb = traceback.format_exc()
            _send_http_reply(conn, 500, {
                "id": req_id, "error": str(e),
                "traceback": tb[-600:] if len(tb) > 600 else tb
            })
    except Exception as e:
        try:
            _send_http_reply(conn, 500, {"id": None, "error": str(e)})
        except Exception:
            pass


# ═══ REST API 路由表 ═══
# 映射 URL path → (methods_map_key, allowed_http_methods)
_REST_ROUTES = {
    "/health":                 ("health",           ["GET"]),
    "/ping":                   ("ping",             ["GET"]),
    "/vector_info":            ("vector_info",      ["GET"]),
    "/get_status":             ("get_status",       ["GET"]),
    "/persona_snapshot":       ("persona_snapshot", ["GET"]),
    "/dag_status":             ("dag_status",       ["GET"]),
    "/hardinfo":               ("hardinfo",         ["GET"]),
    "/recall":                 ("recall",           ["POST"]),
    "/store":                  ("store",            ["POST"]),
    "/verify":                 ("verify",           ["POST"]),
    "/rccam":                  ("rccam",            ["POST"]),
    "/batch":                  ("batch",            ["POST"]),
    "/smart_process":          ("smart_process",    ["POST"]),
    "/implicit_feedback":      ("implicit_feedback",["POST"]),
    "/dag_ingest":             ("dag_ingest",       ["POST"]),
    "/dag_assemble":           ("dag_assemble",     ["POST"]),
    "/dag_compact":            ("dag_compact",      ["POST"]),
    "/save_memory":            ("save_memory",      ["POST"]),
    "/smart_retrieval":        ("smart_retrieval",  ["POST"]),
    "/build_system_prompt":    ("build_system_prompt",["POST"]),
    "/restore_context":        ("restore_context",  ["POST"]),
    "/answer":                 ("answer",           ["POST"]),
    "/remember":               ("remember",         ["POST"]),
    "/learn":                  ("learn",            ["POST"]),
    "/learn_preference":       ("learn_preference", ["POST"]),
    "/learn_correction":       ("learn_correction", ["POST"]),
    "/forget":                 ("forget",           ["POST"]),
    "/execute_workflow":       ("execute_workflow", ["POST"]),
    "/call_module":            ("call_module",      ["POST"]),
    "/rccam_compact_cycle":    ("rccam_compact_cycle",["POST"]),
    "/expand_rccam_cycle":     ("expand_rccam_cycle",["POST"]),
    "/cognitive_compress_dag": ("cognitive_compress_dag",["POST"]), 
    "/verify_reply_style":     ("verify_reply_style",["POST"]),
}


def _handle_rest_request(conn, http_method, http_path, body_str, methods_map):
    """处理 REST 风格请求：GET /health, POST /recall 等"""
    # GET /rest — 列出所有 REST 端点
    if http_path == "/rest":
        routes_list = {
            path: {"method": allowed[0], "rpc": rpc_key}
            for path, (rpc_key, allowed) in sorted(_REST_ROUTES.items())
        }
        _send_http_reply(conn, 200, {"ok": True, "routes": routes_list, "total": len(routes_list)})
        return

    route = _REST_ROUTES.get(http_path)
    if route is None:
        _send_http_reply(conn, 404, {"error": f"unknown endpoint: {http_path}"})
        return

    rpc_key, allowed_methods = route
    if http_method not in allowed_methods:
        _send_http_reply(conn, 405, {
            "error": f"method {http_method} not allowed for {http_path}",
            "allowed": allowed_methods
        })
        return

    # 解析参数：GET 从 query string，POST 从 JSON body
    if http_method == "GET":
        params = {}
        if "?" in http_path:
            try:
                qs = http_path.split("?", 1)[1]
                from urllib.parse import parse_qs
                for k, v in parse_qs(qs).items():
                    params[k] = v[0] if len(v) == 1 else v
            except Exception:
                pass
    else:
        try:
            params = json.loads(body_str) if body_str.strip() else {}
        except json.JSONDecodeError:
            _send_http_reply(conn, 400, {"error": "invalid JSON body"})
            return

    handler = methods_map.get(rpc_key)
    if handler is None:
        _send_http_reply(conn, 500, {"error": f"RPC handler not found: {rpc_key}"})
        return

    t0 = time.time()
    try:
        result = handler(params)
        elapsed = round((time.time() - t0) * 1000, 1)
        # ═══ mmap 大 payload 路由：结果 >50KB 时走 mmap ═══
        result_json = json.dumps(result, ensure_ascii=False)
        if len(result_json) > 50000 and rpc_key in ("rccam", "recall", "store", "verify"):
            _mmap_key = f"rest_{rpc_key}_{int(time.time()*1000)}"
            _mmap_write(_mmap_key, result)
            _zmq_pub_event("mmap_result_ready", {"key": _mmap_key, "method": rpc_key, "size": len(result_json)})
            _send_http_reply(conn, 200, {
                "ok": True, "result": {"_mmap_key": _mmap_key, "_mmap_size": len(result_json)},
                "timing_ms": elapsed
            })
        else:
            _send_http_reply(conn, 200, {"ok": True, "result": result, "timing_ms": elapsed})
    except Exception as e:
        tb = traceback.format_exc()
        _send_http_reply(conn, 500, {
            "ok": False, "error": str(e),
            "traceback": tb[-600:] if len(tb) > 600 else tb
        })


def _zmq_pub_init():
    """初始化 ZMQ PUB socket（可选，无 pyzmq 则跳过）"""
    global _zmq_pub
    if _zmq_pub is not None:
        return _zmq_pub
    try:
        import zmq
        ctx = zmq.Context.instance()
        pub = ctx.socket(zmq.PUB)
        pub.sndhwm = 100
        pub.bind(f"tcp://127.0.0.1:{ZMQ_PUB_PORT}")
        _zmq_pub = pub
        sys.stderr.write(f"[claw-worker] ZMQ PUB on tcp://127.0.0.1:{ZMQ_PUB_PORT}\n")
        return pub
    except Exception as e:
        sys.stderr.write(f"[claw-worker] ZMQ init skipped: {e}\n")
        _zmq_pub = None
        return None


def _zmq_pub_event(event_type, data):
    """通过 ZMQ PUB 推送结构化事件 — v7.1: 速率限制防网关塞爆

    相同 event_type 在 500ms 内只发一次（避免 dag_ingest 每消息都触发 ZMQ 洪泛）。
    """
    global _zmq_pub
    if _zmq_pub is None:
        return
    try:
        # ── 速率限制：同类型事件 500ms 去重 ──
        _now = time.time()
        _zmq_last = getattr(_zmq_pub_event, '_last', {})
        _last_ts, _last_data = _zmq_last.get(event_type, (0, None))
        # 忽略数据（仅检查 session），同 session 同类型 500ms 内跳过
        _session = data.get("session", "")
        if _now - _last_ts < 0.5 and _last_data == _session:
            return
        _zmq_last[event_type] = (_now, _session)
        _zmq_pub_event._last = _zmq_last

        import zmq
        payload = json.dumps({"event": event_type, "ts": _now, **data}, ensure_ascii=False)
        _zmq_pub.send_string(payload)
    except Exception:
        pass


def _heartbeat_writer_thread():
    """心跳 mmap 线程：每秒刷 8 字节 float64 时间戳到独立文件
    
    插件端只读此文件判断存活，不走 UDS，不抢 GIL。
    结构极简：8 字节 little-endian double，无锁、无序列化、无锁竞争。
    """
    os.makedirs(os.path.dirname(HB_PATH), exist_ok=True)
    try:
        os.unlink(HB_PATH)
    except FileNotFoundError:
        pass
    while not _shutdown_flag:
        try:
            now = time.time()
            with open(HB_PATH, "wb") as f:
                f.write(struct.pack("<d", now))
            time.sleep(1.0)
        except Exception:
            time.sleep(0.5)


def _preload_rccam_deps():
    """Worker 启动时预加载 R-CCAM 核心依赖
    
    避免第一次 rccam() 调用时 import + lazy init 卡死 GIL。
    静默失败，不影响启动。
    """
    critical_modules = [
        "unified_entry",
        "smart_processor",
        "enhanced_hallucination_guard",
        "dag_context_manager",
        "xiaoyi_memory",
        "thinking_enhanced",
    ]
    for mod_name in critical_modules:
        try:
            __import__(mod_name)
        except Exception:
            pass
    # 预加载跨平台向量 API（自动检测 AVX-512/AVX2/NEON/SVE）
    global _vector_api
    try:
        from galaxyos.privileged.vector_api import VectorAPI
        _vector_api = VectorAPI()
        sys.stderr.write(f"[claw-worker] VectorAPI: arch={_vector_api.backend_info.arch.value}, "
                         f"lanes={_vector_api.backend_info.lane_count}, "
                         f"fma={_vector_api.backend_info.supports_fma}\n")
    except Exception as e:
        sys.stderr.write(f"[claw-worker] VectorAPI init skipped: {e}\n")
        _vector_api = None
    # 一次快速 health_check 触发懒加载模块
    try:
        from unified_entry import UnifiedEntry
        _preload_entry = UnifiedEntry()
        _preload_entry.health_check()
    except Exception:
        pass


def _mmap_write(cache_key, data):
    """写入共享内存缓存（4字节大端长度前缀 + JSON）"""
    try:
        full = {cache_key: data}
        payload = json.dumps(full, ensure_ascii=False)
        raw = payload.encode("utf-8")
        os.makedirs(os.path.dirname(MMAP_PATH), exist_ok=True)
        # 修复 F-8: mmap 字节序。Python 侧 _mmap_read 用小端 (<I)，
        # 这里 _mmap_write 之前用大端 (>I) 永远读不到；改为小端与 read 对齐。
        header = struct.pack("<I", len(raw))
        with open(MMAP_PATH, "wb") as f:
            f.write(header + raw)
    except Exception:
        pass


_shutdown_flag = False
_galaxy_pending = []


def _handle_shutdown(*_args):
    """优雅关闭：信号处理器"""
    global _shutdown_flag
    if _shutdown_flag:
        return  # 已关闭
    _shutdown_flag = True
    sys.stderr.write('[claw-worker] 收到关闭信号，正在保存数据...\n')
    # 写一条保存标记到共享内存
    try:
        _mmap_write('shutdown', {
            'pid': os.getpid(),
            'ts': time.time(),
            'reason': 'signal',
        })
    except Exception:
        pass
    sys.stderr.write('[claw-worker] 关闭完成\n')


# 注册优雅关闭信号处理器
signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)

# ============================================================
# 单线程串行 TCP HTTP 服务端
# ============================================================

def _http_serve(methods_map):
    """阻塞式 HTTP JSON-RPC — blocking accept，无 selectors"""
    import socket as _sock

    server = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    server.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", HTTP_PORT))
    server.listen(8)
    server.settimeout(1.0)

    sys.stderr.write(f"[claw-worker] HTTP JSON-RPC + REST API on http://127.0.0.1:{HTTP_PORT}\n")
    sys.stderr.write(f"[claw-worker]   REST endpoints: {len(_REST_ROUTES)} routes (GET /health, POST /recall, ...)\n")
    sys.stderr.write(f"[claw-worker]   API index: curl http://127.0.0.1:{HTTP_PORT}/\n")

    while not _shutdown_flag:
        try:
            conn, _addr = server.accept()
        except _sock.timeout:
            continue
        except Exception:
            break
        try:
            conn.settimeout(REQUEST_TIMEOUT)
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                raw += chunk
            if raw:
                _handle_one_http_request(conn, raw, methods_map)
        except Exception as _e:
            sys.stderr.write(f"[claw-worker] HTTP handler error: {_e}\n")
        finally:
            try:
                conn.close()
            except Exception:
                pass

    server.close()

    """HTTP JSON-RPC over localhost — 单线程串行，与 UDS 同模型"""
    import socket as _sock
    import selectors as _sel_mod

    server = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
    server.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", HTTP_PORT))
    server.listen(8)
    server.setblocking(False)

    sel = _sel_mod.DefaultSelector()
    sel.register(server, _sel_mod.EVENT_READ, data=None)

    sys.stderr.write(f"[claw-worker] HTTP JSON-RPC + REST API on http://127.0.0.1:{HTTP_PORT}\n")
    sys.stderr.write(f"[claw-worker]   REST endpoints: {len(_REST_ROUTES)} routes (GET /health, POST /recall, ...)\n")
    sys.stderr.write(f"[claw-worker]   API index: curl http://127.0.0.1:{HTTP_PORT}/\n")

    while not _shutdown_flag:
        events = sel.select(timeout=0.5)
        for key, mask in events:
            if key.data is None:
                try:
                    conn, _addr = server.accept()
                    conn.setblocking(True)  # 阻塞发送，避免 sendall 被吞
                    sel.register(conn, _sel_mod.EVENT_READ, data=b"")
                except Exception:
                    pass
            else:
                conn = key.fileobj
                try:
                    chunk = conn.recv(65536)
                    if chunk:
                        key.data += chunk
                        if b"\r\n\r\n" in key.data:
                            sel.unregister(conn)
                            conn.setblocking(True)  # 保证 sendall 能正常发送
                            _handle_one_http_request(conn, key.data, methods_map)
                            try:
                                conn.close()
                            except Exception:
                                pass
                    else:
                        sel.unregister(conn)
                        try:
                            conn.close()
                        except Exception:
                            pass
                except (BlockingIOError, InterruptedError):
                    pass
                except Exception:
                    sel.unregister(conn)
                    try:
                        conn.close()
                    except Exception:
                        pass

    sel.unregister(server)
    server.close()



def main():
    # SIGPIPE → SIG_IGN 而非 SIG_DFL：
    # supervisor 模式下 stdout 可能被关闭，写 stdout 触发 SIGPIPE 会立刻杀进程
    # SIG_IGN 让 write() 返回 EPIPE 错误，Worker 优雅处理而非被宰
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    global _worker_inst
    worker = ClawWorker()
    _worker_inst = worker
    _init_methods(worker)

    # 三论文集成: RLM + SKILL0 + MemoryOS
    try:
        from galaxyos.engine.paper_integration_addon import integrate_into_worker
        _paper_addon = integrate_into_worker(worker, _METHODS)
        sys.stderr.write(f"[claw-worker] 三论文集成注册: RLM + SKILL0 + MemoryOS\n")
    except Exception as e:
        sys.stderr.write(f"[claw-worker] 三论文集成跳过: {e}\n")

    # v8.1 论文全量集成: 18新模块 × 4管线
    try:
        from galaxyos.engine.paper_integration_v81 import integrate_v81
        _v81_addon = integrate_v81(worker, _METHODS)
        sys.stderr.write(f"[claw-worker] v8.1 论文全量集成注册: 22 UDS 方法\n")
    except Exception as e:
        sys.stderr.write(f"[claw-worker] v8.1 论文全量集成跳过: {e}\n")
    
    # 启动记忆巩固后台
    try:
        from memory_consolidation import ConsolidationEngine
        global _consolidation
        _consolidation = ConsolidationEngine(WORKSPACE)
        _consolidation.start_background()
        sys.stderr.write(f"[claw-worker] Memory consolidation engine started\n")
    except Exception as e:
        sys.stderr.write(f"[claw-worker] Memory consolidation skipped: {e}\n")

    # 启动内在元认知进化后台（每 50 次 rccam 调用触发一次归纳）
    # 启动 Galaxy Kernel 后台线程（接管所有非核心论文功能，事件驱动 + 定时调度）
    def _galaxy_kernel_loop():
        """GalaxyOS Galaxy Kernel — 独立后台线程"""
        _flash_client = None
        _flash_model = 'deepseek-v4-flash'
        _rccam_count = 0
        _pi = None  # lazy paper_integration

        def _init_flash():
            nonlocal _flash_client, _flash_model
            try:
                _w = _get_worker()
                if _w and getattr(_w, '_entry', None) and getattr(_w._entry, 'xiaoyi_claw', None):
                    _xc = _w._entry.xiaoyi_claw
                    if _xc and getattr(_xc, 'llm_flash', None):
                        _flash_client = _xc.llm_flash
                        _flash_model = getattr(_xc, '_llm_flash_model', 'deepseek-v4-flash')
                        sys.stderr.write('[galaxy-kernel] 复用主系统 Flash 客户端\n')
                        return True
            except Exception:
                pass
            try:
                _cfg_path = os.path.expanduser('~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/config/llm_config.json')
                if os.path.exists(_cfg_path):
                    with open(_cfg_path) as _f: _cfg = json.load(_f)
                    _fc = _cfg.get('llm', {})
                    if _fc.get('api_key'):
                        from openai import OpenAI as _OAI
                        _flash_client = _OAI(api_key=_fc['api_key'], base_url=_fc.get('base_url', 'https://api.deepseek.com/beta'))
                        _flash_model = _fc.get('model', 'deepseek-v4-flash')
                        sys.stderr.write('[galaxy-kernel] 独立 Flash 客户端初始化成功\n')
                        return True
            except Exception:
                pass
            return False

        # ── 神经信号桥：Galaxy Kernel 产出 → 突触网络 ──
        _neural_bridge = None
        _bridge_synapse_cache = {}

        def _get_neural_bridge():
            nonlocal _neural_bridge
            if _neural_bridge is not None:
                return _neural_bridge
            try:
                from memory_synapse_network import SynapseNetwork, NeuronManager, SynapseManager
                _sn = SynapseNetwork(workspace_path=WORKSPACE)
                _nm = NeuronManager(_sn)
                _sm = SynapseManager(_sn, use_ltc=hasattr(_sn, '_get_ltc_template'))
                _neural_bridge = {'net': _sn, 'nm': _nm, 'mgr': _sm}
            except Exception as _e:
                sys.stderr.write(f'[galaxy-kernel] 神经桥初始化延迟: {_e}\n')
                _neural_bridge = False
            return _neural_bridge

        def _neural_find_or_create(content, confidence=0.5):
            """按内容找已有神经元，找不到就创建"""
            b = _get_neural_bridge()
            if not b: return None
            try:
                for nid, n in b['net']._neurons_cache.items():
                    if n.content and len(n.content) > 5 and (n.content in content or content in n.content):
                        return nid
                n = b['nm'].create_neuron(content=content[:300])
                return n.id
            except: return None

        def _neural_signal(type_, strength, source_neurons=None, target_neurons=None, content=''):
            """统一神经信号注入

            type_: 'ltp' | 'ltd' | 'connect' | 'activate'
            """
            b = _get_neural_bridge()
            if not b: return
            try:
                if type_ == 'activate':
                    # 激活一个神经元的兴奋度
                    _src_id = _neural_find_or_create(content or 'kernel_signal', max(0.1, strength))
                    if _src_id:
                        n = b['net']._neurons_cache.get(_src_id)
                        if n: n.apply_activation_signal(strength=min(1.0, abs(strength)))
                elif type_ in ('ltp', 'ltd') and source_neurons and target_neurons:
                    for s in source_neurons:
                        for t in target_neurons:
                            syn = b['mgr'].get_synapse(s, t)
                            if not syn:
                                syn = b['mgr'].create_synapse(s, t, weight=0.5)
                            if type_ == 'ltp':
                                b['mgr'].ltp(syn, strength=min(0.3, abs(strength)))
                            else:
                                b['mgr'].ltd(syn, decay_rate=min(0.3, abs(strength)))
                elif type_ == 'connect' and source_neurons and target_neurons:
                    for s in source_neurons:
                        for t in target_neurons:
                            existing = b['mgr'].get_synapse(s, t)
                            if not existing:
                                b['mgr'].create_synapse(s, t, weight=min(1.0, max(0.3, strength)))
            except: pass

        def _lazy_pi():
            nonlocal _pi
            if _pi is None:
                from paper_integration import get_integration
                _pi = get_integration(_flash_client, WORKSPACE)
            return _pi

        # ── 后处理：每次 rccam 完成后的离线分析（延迟 5s） ──
        def _run_post_response(query, answer, confidence):
            if confidence < 0.5 or not answer:
                return
            try:
                _ref_dir = os.path.join(WORKSPACE, '.learnings')
                os.makedirs(_ref_dir, exist_ok=True)
                _ref_entry = {
                    'id': f'RFX-{int(time.time())}-{os.urandom(4).hex()}',
                    'type': 'galaxy_kernel_record',
                    'question': query[:200],
                    'answer': answer[:300],
                    'scores': {'faithfulness': confidence, 'relevance': confidence, 'completeness': confidence},
                    'priority': 'low' if confidence > 0.8 else 'medium',
                    'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                }
                # 写 jsonl（ThinkingEnhanced 读的格式）
                with open(os.path.join(_ref_dir, 'reflexions.jsonl'), 'a', encoding='utf-8') as _f:
                    _f.write(json.dumps(_ref_entry, ensure_ascii=False) + '\n')
                # 兼容旧版：也写 data/reflexions.json
                try:
                    _old_file = os.path.join(WORKSPACE, 'data', 'reflexions.json')
                    os.makedirs(os.path.dirname(_old_file), exist_ok=True)
                    _old = []
                    if os.path.exists(_old_file):
                        with open(_old_file) as _f:
                            try: _old = json.load(_f)
                            except: _old = []
                    _old.append(_ref_entry)
                    with open(_old_file, 'w') as _f:
                        json.dump(_old[-200:], _f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            except Exception:
                pass

        # ── 后处理：轻量论文功能 ──
        def _run_paper_post_response(query, answer, confidence=0.5):
            """运行所有轻量论文后处理：情感、因果、TKG 建图、CoVe 验证

            优化：所有子调用传入 完整对话对(query+answer) 而非仅 query，
            使情感/因果/空间分析有足够文本上下文，提升数据质量。
            """
            _insights = {'ts': time.time()}
            if not answer:
                return
            # 拼接完整对话对作为分析输入
            _full_text = f"用户说: {query[:300]}\n小艺回答: {answer[:600]}"
            try:
                _p = _lazy_pi()
                # 情感分析：传入完整对话（有 AI 回答才有情感判断依据）
                if hasattr(_p, 'update_emotion'):
                    _p.update_emotion(_full_text[:400], 'xiaoyi-claw-dag')
                if hasattr(_p, 'inject_emotion_context'):
                    _ctx2 = _p.inject_emotion_context(_full_text[:400], 'xiaoyi-claw-dag')
                    if _ctx2: _insights['emotion_context'] = str(_ctx2)[:300]
                # 🧠 情感强度 → 神经兴奋信号
                _emotion_type = (_ctx2 or '')[:10] if _ctx2 else ''
                if _emotion_type and '焦虑' in _emotion_type or '生气' in _emotion_type or '挫败' in _emotion_type:
                    _neural_signal('activate', 0.6, content=f'emotion:{_emotion_type}')
                elif _emotion_type and '开心' in _emotion_type or '兴奋' in _emotion_type or '好奇' in _emotion_type:
                    _neural_signal('activate', 0.4, content=f'emotion:{_emotion_type}')

                # 因果分析：传入完整对话对，user_response 填 answer 本体
                if hasattr(_p, 'inject_causal_context'):
                    _cau = _p.inject_causal_context(query[:300], user_response=answer[:500])
                    if _cau and (_cau.get('causes') or _cau.get('effects') or _cau.get('links')):
                        _insights['causal_context'] = str({k: _cau[k] for k in ('causes','effects','links','graph') if k in _cau})[:400]
                        # 🧠 因果链 → 突触初始连接
                        _causes = _cau.get('causes', [])[:3]
                        _effects = _cau.get('effects', [])[:3]
                        _causal_pairs = 0
                        for _c in _causes:
                            _src_id = _neural_find_or_create(str(_c)[:150], 0.85)
                            for _e in _effects:
                                _tgt_id = _neural_find_or_create(str(_e)[:150], 0.85)
                                if _src_id and _tgt_id:
                                    _neural_signal('connect', 0.85,
                                        source_neurons=[_src_id], target_neurons=[_tgt_id])
                                    _causal_pairs += 1
                        # 🧠 高置信度因果模式：多 pair 时批量 LTP 增强
                        if _causal_pairs >= 2:
                            try:
                                _nb = _get_neural_bridge()
                                if _nb:
                                    for _c in _causes:
                                        _sid = _neural_find_or_create(str(_c)[:150], 0.85)
                                        for _e in _effects:
                                            _tid = _neural_find_or_create(str(_e)[:150], 0.85)
                                            if _sid and _tid:
                                                _s = _nb['mgr'].get_synapse(_sid, _tid)
                                                if _s:
                                                    _nb['mgr'].ltp(_s, strength=0.2)
                                    sys.stderr.write(f'[galaxy-kernel] 因果LTP增强: {_causal_pairs} pairs\n')
                            except: pass

                # 空间场景：从 AI 回答提取场景标签（回答中提到的地点/空间信息）
                if hasattr(_p, 'extract_and_register_scene'):
                    _scene_label = _p.extract_and_register_scene(answer[:300], current_session='xiaoyi-claw-dag')
                    if _scene_label: _insights['spatial_scene'] = str(_scene_label)[:120]
                # 实体抽取：同时传入 query + answer（双倍信息）
                if hasattr(_p, 'extract_and_store_entities'):
                    _p.extract_and_store_entities(_full_text[:800], timestamp=time.time(), session_key="xiaoyi-claw-dag")
            except Exception:
                pass
            # CoVe 验证：对比 query vs answer 一致性（是否自相矛盾/偏离主题）
            try:
                from chain_of_verification import ChainOfVerificationEngine
                _cove = ChainOfVerificationEngine(llm_flash=_flash_client, llm_pro=_flash_client)
                _vr = _cove.verify_and_refine(answer=answer, query=query, max_rounds=1)
                if _vr:
                    _contra = getattr(_vr, 'contradictions_found', 0)
                    if _contra > 0:
                        _insights['cove_contradictions'] = _contra
                        sys.stderr.write(f'[galaxy-kernel] CoVe: {_contra} contradictions\n')
                        # 🧠 矛盾 → LTD 惩罚：降低相关突触权重
                        _neural_signal('activate', 0.0, content=f'cove:contradiction_weight={_contra}')
                        # 对近期活跃突触做 LTD 惩罚
                        try:
                            _snap = _get_neural_bridge()
                            if _snap:
                                _all_s = list(_snap['mgr'].network._synapses_cache.values())
                                _recent = [s for s in _all_s if hasattr(s, 'reinforcement_count') and s.reinforcement_count > 2]
                                for _s in _recent[:10]:
                                    _snap['mgr'].ltd(_s, decay_rate=0.05 * _contra)
                        except: pass
                    _consistency = getattr(_vr, 'consistency_score', None) or getattr(_vr, 'confidence', None)
                    if _consistency is not None:
                        _insights['cove_consistency'] = _consistency
                        # 🧠 高一致 → LTP 增强
                        if _consistency > 0.8:
                            _neural_signal('activate', 0.3, content=f'cove:consistency={_consistency}')
            except Exception:
                pass
            # 写 insights 供下一轮 process() 消费（保留旧通道兼容）
            try:
                _ins_path = os.path.join(WORKSPACE, 'data', 'galaxy_kernel_insights.json')
                os.makedirs(os.path.dirname(_ins_path), exist_ok=True)
                with open(_ins_path, 'w') as _f:
                    json.dump(_insights, _f, ensure_ascii=False, default=str)
            except Exception:
                pass

        # ── 后处理：v8.1 论文全量后处理（每次 rccam 后）──
        def _run_v81_post_response(query, answer, confidence=0.5):
            """运行 v8.1 集成后处理：Engram/ODE-RNN/Sparsity/LFM 等"""
            if not answer:
                return
            try:
                # _v81_addon 是同作用域闭包变量（在 if __name__ 中定义）
                if _v81_addon:
                    _vi = _v81_addon.run_post_response(query[:200], answer[:400], confidence)
                    if _vi:
                        _v81_path = os.path.join(WORKSPACE, '.learnings', 'v81_insights.jsonl')
                        os.makedirs(os.path.dirname(_v81_path), exist_ok=True)
                        _vi['ts'] = time.time()
                        _vi['query_prefix'] = str(query)[:40]
                        with open(_v81_path, 'a') as _f:
                            _f.write(json.dumps(_vi, ensure_ascii=False, default=str) + '\n')
            except Exception:
                pass

        # ── 周期任务：重论文功能（每 ~50 轮）──
        def _run_periodic_paper_tasks():
            """时空认知 + 引擎集成 + 增强推理的后台构造"""
            try:
                # 1. 时空认知：AriGraph 空间拓扑 → LASAR 认知地图
                try:
                    from spatial_topology import AriGraphBuilder
                    _ag = AriGraphBuilder(workspace=WORKSPACE, llm=_flash_client)
                    _ag.build_from_recent(limit=50, session_key='xiaoyi-claw-dag')
                except Exception as _e:
                    sys.stderr.write(f'[galaxy-kernel] AriGraph skip: {_e}\n')
            except Exception:
                pass
            try:
                from cognitive_map import CognitiveMapBuilder
                _cm = CognitiveMapBuilder(workspace=WORKSPACE, llm=_flash_client)
                _cm.build(limit=30, session_key='xiaoyi-claw-dag')
            except Exception as _e:
                sys.stderr.write(f'[galaxy-kernel] CognitiveMap skip: {_e}\n')

            # 3. Graph of Thoughts 建图
            try:
                from graph_of_thoughts import GoTBuilder
                _got = GoTBuilder(llm=_flash_client)
                _got.build_from_recent(limit=20, session_key='xiaoyi-claw-dag')
            except Exception:
                pass

            # 4. 引擎集成层刷新（ReAct + HierarchicalMemory 后台维护）
            try:
                from engine_integration import get_engine_integration
                _ei = get_engine_integration(_flash_client, WORKSPACE)
                if hasattr(_ei, 'background_maintenance'):
                    _ei.background_maintenance()
            except Exception:
                pass

            # 5. 语义熵 + Adaptive-RAG 参数刷新
            try:
                from semantic_entropy import SemanticEntropy
                _se = SemanticEntropy(_flash_client)
                _entropy = _se.compute_background()
                if _entropy and _entropy > 0.5:
                    try:
                        from adaptive_classifier import AdaptiveClassifier
                        _ac = AdaptiveClassifier(_flash_client)
                        _ac.adjust_thresholds(_entropy)
                    except Exception:
                        pass
            except Exception:
                pass

            # 6. 超路由学习
            try:
                from hyper_routing import HyperRouter
                _hr = HyperRouter(_flash_client, WORKSPACE)
                _hr.learn(recent_count=100)
            except Exception:
                pass

            # 7. 因果推理库持续训练
            try:
                from causal_reasoning import CausalReasoningEngine
                _ce = CausalReasoningEngine(_flash_client)
                _ce.train_from_history(limit=30)
            except Exception:
                pass

            # 8. Plan-and-Solve 规划库持续更新
            try:
                from plan_solve import PlanSolveEngine
                _pse = PlanSolveEngine(_flash_client)
                _pse.update_plan_library(limit=20)
            except Exception:
                pass

            # 9. Tree-of-Thought 树库维护
            try:
                from tree_of_thought import TreeOfThoughtEngine
                _tote = TreeOfThoughtEngine(_flash_client)
                _tote.prune_stale_trees(max_age_days=3)
            except Exception:
                pass

            # 10. CognitiveLoad 模型更新
            try:
                from cognitive_load import CognitiveLoad
                _cl2 = CognitiveLoad()
                _cl2.update_model()
            except Exception:
                pass

            # 11. MultiPath 路径库刷新
            try:
                from multi_path import MultiPathEngine
                _mpe = MultiPathEngine(_flash_client)
                _mpe.refresh_profiles(limit=20)
            except Exception:
                pass

            # 12. MultiAgentDebate 角色档案更新
            try:
                from multi_agent_debate import MultiAgentDebate
                _mad = MultiAgentDebate(_flash_client)
                _mad.update_profiles()
            except Exception:
                pass

            # 13. CodeAwareReasoning 代码上下文刷新
            try:
                from code_aware_reasoning import CodeAwareEngine
                _care = CodeAwareEngine(_flash_client)
                _care.refresh_context()
            except Exception:
                pass

        # ══ 启动 ══
        _init_flash()

        while not _shutdown_flag:
            global _galaxy_pending
            try:
                if _flash_client is None:
                    _init_flash()
                    time.sleep(30)
                    continue

                time.sleep(6)
                _rccam_count += 1

                # 事件队列处理（延迟 5s 执行）
                _now = time.time()
                for _ev in list(_galaxy_pending):
                    if _now - _ev.get('ts', 0) >= 5:
                        try: _galaxy_pending.remove(_ev)
                        except ValueError: continue
                        if _ev.get('type') == 'post_response':
                            _run_post_response(
                                _ev.get('query',''), _ev.get('answer',''), _ev.get('confidence',0.5))
                        if _ev.get('type') == 'post_response':
                            _run_paper_post_response(
                                _ev.get('query',''), _ev.get('answer',''), _ev.get('confidence',0.5))
                        if _ev.get('type') == 'post_response':
                            _run_v81_post_response(
                                _ev.get('query',''), _ev.get('answer',''), _ev.get('confidence',0.5))

                # 周期任务（每 ~50 轮）
                if _rccam_count >= 50:
                    # ── ThinkingEnhanced 模式发现 ──
                    try:
                        from thinking_enhanced import ThinkingEnhanced
                        _te = ThinkingEnhanced(_flash_client)
                        _result = _te.evolve()
                        if _result.get('success') and _result.get('patterns'):
                            _dag = _get_dag()
                            if _dag:
                                for _p in _result['patterns']:
                                    try:
                                        _dag.write_capability_node({
                                            'name': str(_p.get('scenario',''))[:80],
                                            'trigger': str(_p.get('pattern',''))[:120],
                                            'suggestion': str(_p.get('suggestion',''))[:200],
                                            'confidence': 0.8 if str(_p.get('confidence','')) == '高' else 0.5,
                                            'source': 'galaxy_kernel', 'created_at': time.time(),
                                        }, session_key='xiaoyi-claw-dag')
                                    except Exception:
                                        pass
                            sys.stderr.write(f'[galaxy-kernel] 自进化完成 (patterns={len(_result["patterns"])})\n')
                    except Exception:
                        pass

                    # ── SelfEvolutionEngine (含 APO) 自优化 ──
                    try:
                        _w = _get_worker()
                        if _w and getattr(_w, '_entry', None) and getattr(_w._entry, 'xiaoyi_claw', None):
                            _xc = _w._entry.xiaoyi_claw
                            _se = getattr(_xc, '_self_evolution', None)
                            if _se:
                                # 从最近事件取 query+answer（_galaxy_pending 是 kernel 闭包变量）
                                _se_ev = _galaxy_pending[-1] if _galaxy_pending else {}
                                _se_q = _se_ev.get('query', '')
                                _se_a = _se_ev.get('answer', '')
                                if _se_q or _se_a:
                                    _se_ev_result = _se.evolve(
                                        query=_se_q[:500], rewritten=_se_q[:500],
                                        results=[], summary=_se_a[:1000],
                                        session_id='xiaoyi-claw-dag')
                                    if _se_ev_result.get('suggestions'):
                                        _dag = _get_dag()
                                        if _dag:
                                            for _s in _se_ev_result['suggestions']:
                                                try:
                                                    _dag.write_capability_node({
                                                        'name': _s.get('target', 'apo_improvement')[:80],
                                                        'trigger': str(_s.get('suggestion',''))[:120],
                                                        'suggestion': str(_s.get('suggestion',''))[:200],
                                                        'confidence': 0.7,
                                                        'source': 'self_evolution', 'created_at': time.time(),
                                                    }, session_key='xiaoyi-claw-dag')
                                                except Exception:
                                                    pass
                                        sys.stderr.write(f'[galaxy-kernel] APO 自优化: {len(_se_ev_result["suggestions"])} 条建议\n')
                    except Exception:
                        pass

                    # 重论文任务
                    _run_periodic_paper_tasks()
                    _rccam_count = 0

            except Exception:
                pass

        sys.stderr.write('[galaxy-kernel] 关闭\n')

    def _dag_compact_loop():
        """后台线程：定期压缩旧 DAG 节点 (含 Cognitive Load 自适应决策)"""
        _dag = None
        _cl = None  # CognitiveLoad 懒加载
        _counter = 0
        while not _shutdown_flag:
            global _galaxy_pending
            try:
                _counter += 1
                if _dag is None:
                    worker = _get_worker()
                    if worker:
                        _dag = worker._get_dag()
                    if _dag is None:
                        time.sleep(30)
                        continue
                # 每 ~60 秒检查一次
                if _counter % 5 == 0:
                    try:
                        # Cognitive Load 评估: 动态调整压缩力度
                        if _cl is None:
                            try:
                                from cognitive_load import CognitiveLoad
                                _cl = CognitiveLoad()
                            except Exception:
                                pass
                        if _cl and _dag and _dag.dag:
                            try:
                                _sessions = _dag.dag.get_all_session_keys()
                                for _sk in _sessions[:3]:  # 最多检查 3 个会话
                                    _nodes = _dag.dag.get_session_nodes(_sk)
                                    _raw_nodes = [n for n in _nodes if not n.is_summary]
                                    if len(_raw_nodes) > 5:
                                        _node_dicts = [{"content": n.content or "", "node_id": n.node_id} for n in _raw_nodes[:20]]
                                        _cl_result = _cl.assess(_sk, _node_dicts, [])
                                        _strength = _cl_result.get("compression_strength", 0.3)
                                        _retain_keys = _cl_result.get("retain_keys", [])
                                        # 根据 cognitive load 调整 leaf_chunk_tokens
                                        # 压缩力度高(>0.7) → 用更激进的阈值, 低(<0.3) → 放宽阈值保留上下文
                                        _base = _dag.dag.leaf_chunk_tokens
                                        if _strength > 0.7:
                                            _dag.dag.leaf_chunk_tokens = max(2000, int(_base * 0.7))
                                        elif _strength < 0.3:
                                            _dag.dag.leaf_chunk_tokens = min(6000, int(_base * 1.3))
                                        sys.stderr.write(f"[dag-compact] CognitiveLoad session={_sk[:20]} strength={_strength:.2f} retain={len(_retain_keys)} threshold={_dag.dag.leaf_chunk_tokens}\n")
                            except Exception as _cle:
                                sys.stderr.write(f"[dag-compact] CognitiveLoad skip: {_cle}\n")
                        result = _dag.ensure_auto_compact(session_key="xiaoyi-claw-dag")
                        if result.get("summarized", 0) > 0:
                            sys.stderr.write(f"[dag-compact] 自动压缩完成: {result.get('summarized')} 节点 → {result.get('summary_node_id','')[:20]}\n")
                    except Exception as _ce:
                        pass
                # 也检查 rccam_nodes 是否需要压缩
                if _counter % 30 == 0:
                    try:
                        _needs_soft, _needs_hard, _compressible, _stats = \
                            _dag.dag.rccam_compact_needed("xiaoyi-claw-dag")
                        if _needs_soft or _needs_hard:
                            _max_c = 5 if _needs_hard else 2
                            _summ = 0
                            for _cid in _compressible[:_max_c]:
                                _dag.dag.compact_rccam_cycle("xiaoyi-claw-dag", _cid)
                                _summ += 1
                            if _summ > 0:
                                sys.stderr.write(f"[dag-compact] R-CCAM cycle 压缩: {_summ} 轮 (raw_tokens={_stats.get('raw_tokens',0)})\n")
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(12)

    # ====== 启动前同步代码 ======
    import subprocess as _sync_sub
    _sync_script = os.path.expanduser("~/.openclaw/scripts/sync_claw_code.sh")
    if os.path.exists(_sync_script):
        try:
            _sync_r = _sync_sub.run(["bash", _sync_script, "--run"], capture_output=True, text=True, timeout=15)
            if _sync_r.returncode == 0:
                sys.stderr.write("[claw-worker] 代码同步: 通过\n")
            else:
                sys.stderr.write(f"[claw-worker] 代码同步: 失败 ({_sync_r.stderr[:100]})\n")
        except Exception as _sync_e:
            sys.stderr.write(f"[claw-worker] 代码同步跳过: {_sync_e}\n")

    # ====== 分通道改造：心跳走独立 mmap，UDS 纯业务 ======
    global _shutdown_flag
    _shutdown_flag = False

    # 1. 心跳 mmap 写入线程（独立文件，不抢 GIL，不依赖 UDS）
    hb_thread = threading.Thread(
        target=_heartbeat_writer_thread, daemon=True, name="heartbeat-mmap"
    )
    hb_thread.start()

    # 2. 预加载 R-CCAM 依赖（避免第一次调用卡死 GIL）
    preload_thread = threading.Thread(
        target=_preload_rccam_deps, daemon=True, name="rccam-preload"
    )
    preload_thread.start()

    # 3. UDS 服务端线程（纯业务，不兼做健康检查）
    uds_thread = threading.Thread(
        target=_uds_server_thread, args=(_METHODS,), daemon=True, name="uds-server"
    )
    uds_thread.start()

    # 2. HTTP JSON-RPC 线程
    http_thread = threading.Thread(
        target=_http_serve, args=(_METHODS,), daemon=True, name="http-rpc"
    )
    http_thread.start()

    # 3.5 Galaxy Kernel 后台线程
    evo_thread = threading.Thread(
        target=_galaxy_kernel_loop, daemon=True, name="galaxy-kernel"
    )
    evo_thread.start()

    # 3.6 后台 DAG 压缩线程
    dc_thread = threading.Thread(
        target=_dag_compact_loop, daemon=True, name="dag-compact"
    )
    dc_thread.start()

    # 3.7 ZMQ DEALER → Gateway ROUTER（多 Worker 通信）
    _worker_id = os.environ.get('WORKER_ID', 'worker:unknown')
    _dealer = None
    try:
        import zmq as _zmq
        _zctx = _zmq.Context.instance()
        _dealer = _zctx.socket(_zmq.DEALER)
        _dealer.setsockopt_string(_zmq.IDENTITY, _worker_id)
        _dealer.connect('tcp://127.0.0.1:5560')
        _dealer.send_json({'event': 'worker_ready', 'id': _worker_id, 'pid': os.getpid()})
        sys.stderr.write(f'[claw-worker] ZMQ DEALER connected as {_worker_id}\n')

        def _dealer_recv_loop():
            while not _shutdown_flag:
                try:
                    _msg = _dealer.recv_json(timeout=1000)
                    if isinstance(_msg, dict):
                        if _msg.get('worker_send'):
                            _payload = _msg.get('payload', {})
                            sys.stderr.write(f'[claw-worker] peer msg from {_msg.get("from","?")}: {str(_payload)[:200]}\n')
                except _zmq.Again:
                    continue
                except Exception:
                    if not _shutdown_flag:
                        time.sleep(1)

        _dealer_thread = threading.Thread(target=_dealer_recv_loop, daemon=True, name='dealer-recv')
        _dealer_thread.start()
    except Exception as _e:
        _dealer = None
        sys.stderr.write(f'[claw-worker] DEALER init skipped: {_e}\n')

    # 4. ZMQ PUB
    _zmq_pub_init()

    # 5. 心跳写入共享内存
    _mmap_write("worker_pid", {"pid": os.getpid(), "ready": True})

    # 发送就绪信号
    sys.stdout.write(json.dumps({
        "id": 0, "event": "ready", "worker": "claw-worker", "pid": os.getpid(),
        "uds": UDS_PATH, "zmq": ZMQ_PUB_PORT, "mmap": MMAP_PATH
    }) + "\n")
    sys.stdout.flush()

    # ====== stdin/stdout 降级循环（仅无 WORKER_UDS 时启用） ======
    # supervisor/UDS 模式下 stdout 管道可能被关闭，写 stdout 触发 SIGPIPE 会杀死进程
    # 有 UDS 时只通过 UDS 通信，不走 stdin/stdout
    if not os.environ.get("WORKER_UDS"):
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                sys.stdout.write(json.dumps({
                    "error": f"invalid JSON: {e}", "id": None
                }) + "\n")
                sys.stdout.flush()
                continue

            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})

            if method not in _METHODS:
                sys.stdout.write(json.dumps({
                    "id": req_id, "error": f"unknown method: {method}"
                }) + "\n")
                sys.stdout.flush()
                continue

            t0 = time.time()
            try:
                result = _METHODS[method](params)
                elapsed = round((time.time() - t0) * 1000, 1)
                sys.stdout.write(json.dumps({
                    "id": req_id, "result": result, "timing_ms": elapsed
                }) + "\n")
                sys.stdout.flush()
                if method == "shutdown":
                    _shutdown_flag = True
                    break
            except Exception as e:
                tb = traceback.format_exc()
                sys.stdout.write(json.dumps({
                    "id": req_id, "error": str(e),
                    "traceback": tb[-600:] if len(tb) > 600 else tb
                }) + "\n")
                sys.stdout.flush()

        _shutdown_flag = True
    else:
        # UDS 模式：等待 shutdown 信号或 stdin EOF（supervisor 关进程时发 EOF）
        sys.stdin.read()


if __name__ == "__main__":
    main()
