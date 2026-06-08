#!/usr/bin/env python3
"""
claw_worker — 小艺 Claw 常驻 Python Worker 进程

三通道 JSON-RPC 2.0:
  1. UDS socket:   ~/.openclaw/extensions/claw-core/var/claw-worker.sock (主通道)
  2. ZMQ PUB:      tcp://127.0.0.1:5559 (事件推送)
  3. Shared mmap:  ~/.openclaw/extensions/claw-core/var/claw_worker_mmap (缓存快读)
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

# ========== 路径初始化 ==========
WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE",
    os.path.expanduser("~/.openclaw/workspace"))
CORE_DIR = os.path.join(WORKSPACE,
    "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core")
SCRIPTS_DIR = os.path.join(WORKSPACE,
    "skills/xiaoyi-claw-omega-final/scripts")
sys.path.insert(0, CORE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE,
    "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration"))
sys.path.insert(0, os.path.join(WORKSPACE,
    "skills/xiaoyi-claw-omega-final"))
sys.path.insert(0, SCRIPTS_DIR)

# 模块级缓存
_worker_inst = None

# ═══ MN-RU 增量索引桥接（2407.07871 / 2404.13556）═══
_RETRIEVAL_HUB_IMPORTED = False
def _ensure_retrieval_hub():
    """懒加载 retrieval_hub 中的 MN-RU 单例"""
    global _RETRIEVAL_HUB_IMPORTED
    if _RETRIEVAL_HUB_IMPORTED:
        return True
    try:
        sys.path.insert(0, os.path.expanduser("~/.openclaw/extensions/claw-core/dist/scripts"))
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
UDS_PATH = os.path.join(
    os.path.expanduser("~/.openclaw/extensions/claw-core/var"),
    "claw-worker.sock"
)
ZMQ_PUB_PORT = 5559
MMAP_PATH = os.path.join(
    os.path.expanduser("~/.openclaw/extensions/claw-core/var"),
    "claw_worker_mmap"
)

# 心跳专用 mmap（独立文件，插件只读 8 字节时间戳 float64，不跟 GIL 抢锁）
HB_PATH = os.path.join(
    os.path.expanduser("~/.openclaw/extensions/claw-core/var"),
    "claw_worker_heartbeat"
)
_zmq_pub = None  # ZMQ socket (optional)

# ========== Gateway UDS 代理（Worker → Gateway 透明 RPC） ==========
_GATEWAY_UDS_PATH = os.path.join(
    os.path.expanduser("~/.openclaw/extensions/claw-core/var"),
    "claw-gateway.sock"
)
_MMAP_SHM_PATH = os.path.expanduser("~/.openclaw/extensions/claw-core/var/claw_shared_state")
_MMAP_SHM_SIZE = 4096

class _GatewayProxy:
    """Gateway 调用代理 — 透明远程调用

    用法:
        await gateway.web_fetch(url="https://...")
        await gateway.channel_send(message="hello")
        status = gateway.mmap_read()  # mmap 直接读，不走 UDS
    """
    def __init__(self):
        self._conn = None
        self._id = 0

    def _get_conn(self):
        """获取或创建 HTTP over UDS 连接"""
        if self._conn is not None:
            return self._conn
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
        self._conn = _UnixHTTPConn(_GATEWAY_UDS_PATH)
        return self._conn

    def _call(self, method, params=None, timeout=10.0):
        """HTTP over UDS 调用"""
        if params is None:
            params = {}
        self._id += 1
        req = {"id": self._id, "method": method, "params": params}
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
            self._conn = None  # 断线，下次重建
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


class ClawWorker:
    """常驻 Worker — 生命周期内只初始化一次"""

    def __init__(self):
        self._entry = None
        self._hardware_info = {}
        self._load_time_ms = 0
        self._init_time = time.time()
        self._persona_snapshot = ""  # 人格快照缓存
        self._soul_snapshot = ""     # 性格快照
        self._identity_snapshot = "" # 身份快照

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
        self._ensure()
        try:
            result = self._entry.health_check()
            # 叠加实时系统状态
            _healthy = True
            _issues = []

            # 检查 DAG 可用性
            try:
                _dag = self._get_dag()
                _dag_ping = _dag.get_all_session_keys() if _dag else []
                _dag_ok = len(_dag_ping) > 0
            except:
                _dag_ok = False
                _issues.append('dag_unavailable')
                _healthy = False

            # 检查最近 R-CCAM 活动（5 分钟内）
            _rccam_recent = (time.time() - getattr(self, '_last_rccam_ts', 0)) < 300 if hasattr(self, '_last_rccam_ts') else False

            # 检查突触网络
            _synapse_ok = False
            _synapse_stats = {'total_neurons': 0, 'total_synapses': 0}
            try:
                from memory_synapse_network import SynapseNetwork
                _sn = SynapseNetwork(workspace_path=WORKSPACE)
                _nrn = len(_sn._neurons_cache)
                _syn = len(_sn._synapses_cache)
                _synapse_stats = {'total_neurons': _nrn, 'total_synapses': _syn}
                _synapse_ok = _nrn > 0
            except:
                pass

            result.update({
                'healthy': _healthy and not _issues,
                'issues': _issues,
                'dag_available': _dag_ok,
                'rccam_recent_5m': _rccam_recent,
                'synapse_network': _synapse_stats,
                'worker_uptime_s': self._uptime_s() if hasattr(self, '_uptime_s') else 0,
                'pid': os.getpid(),
            })
            return result
        except AttributeError:
            # 兜底：返回真实组件状态
            _real_healthy = False
            _real_issues = ['unified_entry_unavailable']
            try:
                _real_healthy = os.path.exists(os.path.join(WORKSPACE, '.learnings'))
            except:
                pass
            return {
                'healthy': _real_healthy and not _real_issues,
                'issues': _real_issues,
                'components': {
                    'unified_entry': {'healthy': False},
                    'worker': {
                        'healthy': True,
                        'uptime_s': self._uptime_s() if hasattr(self, '_uptime_s') else 0,
                    },
                },
                'synapse_network': {'total_neurons': 0},
                'pid': os.getpid(),
            }

    def recall(self, p: dict) -> dict:
        self._ensure()
        return self._entry.recall(p.get("query", ""), p.get("top_k", 5))

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

    def store(self, p: dict) -> dict:
        self._ensure()
        return self._entry.store(p.get("content", ""), source=p.get("source", "user"))

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
        """从 DAG 库获取指定会话的最新摘要"""
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"summary": ""}
        try:
            import sqlite3
            import os
            dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
            if not os.path.exists(dag_db):
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
        except Exception as e:
            return {"summary": "", "error": f"dag_summary failed: {e}"}

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
        从 DAG 组装上下文 + mmap 写入共享内存。
        优先使用 cycle-aware 新策略，无 R-CCAM 数据时回退旧策略。
        """
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"text": ""}
        try:
            dag = self._get_dag()
            fresh_cycles = p.get("freshCycles", 3)
            max_tokens = p.get("maxTokens", 240000)

            # 优先：cycle-aware 组装
            rccam_cycles = dag.dag.get_rccam_session_cycles(session_id)
            if rccam_cycles:
                text, stats = dag.dag.assemble_from_cycles(
                    session_key=session_id,
                    fresh_cycles=fresh_cycles,
                    max_tokens=max_tokens,
                )
            else:
                # 回退：旧策略
                text, stats = dag.dag.assemble_context(
                    session_key=session_id,
                    fresh_tail_count=p.get("maxRecentMessages", 20),
                    max_tokens=max_tokens,
                )
            result = {"text": text or "", "stats": stats}
            _mmap_write("dag_context", result)
            return result
        except Exception as e:
            return {"text": "", "error": str(e)}

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
        """检查是否需要触发 R-CCAM 压缩"""
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"needs": False}
        try:
            dag = self._get_dag()
            needs_soft, needs_hard, compressible, stats = dag.dag.rccam_compact_needed(session_id)
            return {"needs_soft": needs_soft, "needs_hard": needs_hard,
                    "compressible_cycles": compressible, "stats": stats}
        except Exception as e:
            return {"needs": False, "error": str(e)}

    def rccam_compact_cycle(self, p: dict) -> dict:
        """压缩一个指定的 R-CCAM cycle（压缩后触发 session 级索引更新）"""
        session_id = p.get("sessionId", "")
        cycle_id = p.get("cycleId", "")
        if not session_id or not cycle_id:
            return {"error": "missing sessionId or cycleId"}
        try:
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
        except Exception as e:
            return {"error": str(e)}

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
        """对 dag_nodes 旧消息执行认知压缩"""
        session_id = p.get("sessionId", "")
        max_to_compress = p.get("maxToCompress", 20)
        if not session_id:
            return {"error": "missing sessionId"}
        try:
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
        except Exception as e:
            return {"error": str(e)}

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
        """清空指定 session 在 DAG 中的节点（新会话时调用）"""
        session_id = p.get("sessionId", "")
        if not session_id:
            return {"cleared": False, "reason": "missing sessionId"}
        try:
            dag = self._get_dag()
            if dag and dag.dag:
                # 清空 dag_nodes 表中该 session 的全部节点
                import sqlite3
                conn = sqlite3.connect(dag.dag.db_path)
                deleted = 0
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
                return {"cleared": True, "deleted_nodes": deleted}
            return {"cleared": False, "reason": "dag not ready"}
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
        "get_status": worker.get_status,
        "smart_process": worker.smart_process,
        "smart_retrieval": worker.smart_retrieval,
        "save_memory": worker.save_memory,
    }


# ============================================================
# 三通道服务端
# ============================================================

def _uds_server_thread(methods_map):
    """HTTP/JSON over Unix socket 服务端"""
    import http.server
    import socketserver

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            try:
                req = json.loads(body)
            except json.JSONDecodeError:
                self._respond(400, {"error": "invalid JSON"})
                return
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})
            if method not in methods_map:
                self._respond(404, {"id": req_id, "error": f"unknown method: {method}"})
                return
            t0 = time.time()
            try:
                result = methods_map[method](params)
                elapsed = round((time.time() - t0) * 1000, 1)
                self._respond(200, {"id": req_id, "result": result, "timing_ms": elapsed})
            except Exception as e:
                tb = traceback.format_exc()
                self._respond(500, {"id": req_id, "error": str(e), "traceback": tb[-600:] if len(tb) > 600 else tb})
        def _respond(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, format, *args):
            pass

    class _UDSServer(socketserver.ThreadingUnixStreamServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        os.unlink(UDS_PATH)
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(UDS_PATH), exist_ok=True)
    server = _UDSServer(UDS_PATH, _Handler)
    os.chmod(UDS_PATH, 0o600)
    sys.stderr.write(f"[claw-worker] HTTP UDS listening on {UDS_PATH}\n")
    while not _shutdown_flag:
        server.handle_request()
    server.close()


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
    """通过 ZMQ PUB 推送结构化事件"""
    global _zmq_pub
    if _zmq_pub is None:
        return
    try:
        import zmq
        payload = json.dumps({"event": event_type, "ts": time.time(), **data}, ensure_ascii=False)
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
        header = struct.pack(">I", len(raw))
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
# HTTP JSON-RPC 服务端
# ============================================================

def _http_serve(methods_map):
    """HTTP JSON-RPC over localhost，提供外部调用能力"""
    import http.server

    class RpcHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # 不往 stdout 打日志避免干扰 stdin 协议

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            info = {
                "ok": True,
                "worker": "claw-worker",
                "uds": UDS_PATH,
                "zmq": ZMQ_PUB_PORT,
                "methods": list(methods_map.keys()),
            }
            self.wfile.write(json.dumps(info).encode("utf-8"))

        def do_POST(self):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                req = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as e:
                self.wfile.write(json.dumps({
                    "error": f"invalid JSON: {e}", "id": None
                }).encode("utf-8"))
                return
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})
            if method not in methods_map:
                self.wfile.write(json.dumps({
                    "id": req_id, "error": f"unknown method: {method}"
                }).encode("utf-8"))
                return
            t0 = time.time()
            try:
                result = methods_map[method](params)
                elapsed = round((time.time() - t0) * 1000, 1)
                self.wfile.write(json.dumps({
                    "id": req_id, "result": result, "timing_ms": elapsed
                }).encode("utf-8"))
            except Exception as e:
                tb = traceback.format_exc()
                self.wfile.write(json.dumps({
                    "id": req_id, "error": str(e),
                    "traceback": tb[-600:] if len(tb) > 600 else tb
                }).encode("utf-8"))

    server = http.server.HTTPServer(("127.0.0.1", HTTP_PORT), RpcHandler)
    server.timeout = 0.5
    sys.stderr.write(f"[claw-worker] HTTP JSON-RPC on http://127.0.0.1:{HTTP_PORT}\n")
    while not _shutdown_flag:
        server.handle_request()
    server.server_close()



def main():
    # SIGPIPE → SIG_IGN 而非 SIG_DFL：
    # supervisor 模式下 stdout 可能被关闭，写 stdout 触发 SIGPIPE 会立刻杀进程
    # SIG_IGN 让 write() 返回 EPIPE 错误，Worker 优雅处理而非被宰
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
    worker = ClawWorker()
    _init_methods(worker)
    
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

                # 周期任务（每 ~50 轮）
                if _rccam_count >= 50:
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
