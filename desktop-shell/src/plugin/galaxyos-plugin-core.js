/**
 * galaxyos-plugin-core.js — extracted from GalaxyOS OpenClaw plugin (5187 lines).
 *
 * v10: ClawWorkerClient, WorkerPool, GalaxyPool — full 3-tier elastic
 * pool that manages claw_worker.py subprocesses via HTTP over UDS (Unix)
 * or HTTP over TCP (Windows).  OpenClaw register() + hooks + tools
 * have been stripped; only the runtime core remains.
 *
 * Public API:
 *   start(workspace)  — spawn pools + workers
 *   stop()            — shutdown all pools + workers
 *   execute(method, params, timeout?)  — route call through tiered pool
 *   getStatus()       — { ready, tiers: { hot/warm/cold: { ... } } }
 */
import path from "node:path";
import { spawn, spawnSync, execSync } from "node:child_process";
import { createInterface } from "node:readline";
import fs, { existsSync, mkdirSync, chmodSync, unlinkSync, readFileSync, openSync, writeSync, closeSync, readSync, writeFileSync, renameSync, copyFileSync, readdirSync } from "node:fs";
import net from "node:net";
import http from "node:http";

// CJS compat: esbuild bundles this to CJS — __dirname and require
// are built-in globals. The ESM fallback only triggers in dev mode.
const _cjsRequire = typeof require !== 'undefined' ? require : null;
const __dir = typeof __dirname !== 'undefined' ? __dirname : process.cwd();
const WORKER_SCRIPT = path.join(__dir, "..", "..", "..", "..", "extensions", "galaxyos", "scripts", "claw_worker.py");
const PIL_WORKER_SCRIPT = path.join(__dir, "..", "..", "..", "..", "extensions", "galaxyos", "scripts", "pil_worker.py");

// ════════════════════════════════════════════════════════════════
// OpenClaw 用户配置目录解析（dev / prod / container 三模式）
// ════════════════════════════════════════════════════════════════
//
// OpenClaw 2026.5.6 实际部署布局：
//   核心代码: /home/sandbox/openclaw/node_modules/openclaw/  (npm 全局)
//   用户配置: $HOME/.openclaw/                              (生产默认)
//   dev 模式: $HOME/.openclaw-dev/                          (开发测试)
//   容器:    /opt/openclaw/                                 (固定路径)
//
// 优先级:
//   1) OPENCLAW_HOME / GALAXYOS_OPENCLAW_HOME 环境变量（显式覆盖）
//   2) /opt/openclaw               (容器固定布局)
//   3) $HOME/.openclaw             (生产)
//   4) $HOME/.openclaw-dev         (dev)
//   5) 自动检测 __dir 上溯找到的 OPENCLAW_HOME
function _openclawHome() {
    const envVars = ["OPENCLAW_HOME", "GALAXYOS_OPENCLAW_HOME"];
    for (const k of envVars) {
        const v = process.env[k];
        if (v && existsSync(v) && statSyncSafe(v)?.isDirectory()) return v;
    }
    // 容器布局
    if (existsSync("/opt/openclaw") && statSyncSafe("/opt/openclaw")?.isDirectory()) {
        return "/opt/openclaw";
    }
    const home = process.env.HOME || "/home/sandbox";
    // 生产
    const prod = `${home}/.openclaw`;
    if (existsSync(prod) && statSyncSafe(prod)?.isDirectory()) return prod;
    // dev
    const dev = `${home}/.openclaw-dev`;
    if (existsSync(dev) && statSyncSafe(dev)?.isDirectory()) return dev;
    // 兜底：生产默认
    return prod;
}
function statSyncSafe(p) {
    try { return fs.statSync(p); } catch { return null; }
}
const OPENCLAW_HOME = _openclawHome();

// Rust 原生扩展 — 三级检测
// 1. PyO3 Python 模块（最优：零序列化，直接 import）
//    支持：编译的 .so 扩展 或 embedded pure-Python shim（galaxyos_native.py）
let _pyo3Native = false;
let _pyo3Shim = false;  // true if using pure-Python shim (no Rust)
try {
    const pyEnv = { ...process.env };
    // 确保 scripts/ 在 PYTHONPATH 中以发现 embedded galaxyos_native.py
    const scriptsDir = path.join(__dir, "scripts");
    pyEnv.PYTHONPATH = [scriptsDir, pyEnv.PYTHONPATH].filter(Boolean).join(":");
    const r = spawnSync(_pythonBin, ["-c", "import galaxyos_native; print(galaxyos_native.__version__)"], {
        encoding: "utf-8", timeout: 5000, stdio: ["pipe", "pipe", "ignore"],
        env: pyEnv,
    });
    if (r.status === 0 && r.stdout?.trim()) {
        _pyo3Native = true;
        // 检测是否为 embedded pure-Python shim（检查 _BACKEND 属性）
        try {
            const r2 = spawnSync(_pythonBin, ["-c", "import galaxyos_native; print(getattr(galaxyos_native, '_BACKEND', 'rust'))"], {
                encoding: "utf-8", timeout: 3000, stdio: ["pipe", "pipe", "ignore"],
                env: pyEnv,
            });
            _pyo3Shim = (r2.stdout?.trim() || "rust") === "python";
        } catch {}
        process.stderr.write(`[galaxyos] galaxyos_native v${r.stdout.trim()} detected${_pyo3Shim ? ' (pure-Python shim)' : ' (Rust/PyO3)'}\n`);
    }
} catch {}
// 2. 独立二进制（stdin/stdout JSON-RPC）
const _nativeBinaryCandidates = [
    path.join(__dir, "scripts", "galaxyos-native"),
    path.join(__dir, "native", "target", "release", "galaxyos-native"),
    path.join(__dir, "..", "..", "native", "target", "release", "galaxyos-native"),
    path.join(process.env.HOME || "/home/sandbox", ".cargo", "bin", "galaxyos-native"),
];
let _nativeBinary = null;
let _nativeAutoBuilt = false;

function _detectNativeBinary() {
    if (_nativeBinary && existsSync(_nativeBinary)) return true;
    for (const p of _nativeBinaryCandidates) {
        if (existsSync(p)) { _nativeBinary = p; return true; }
    }
    try {
        const p = execSync("which galaxyos-native 2>/dev/null || echo ''", { encoding: "utf-8" }).trim();
        if (p && existsSync(p)) { _nativeBinary = p; return true; }
    } catch {}
    return false;
}

_detectNativeBinary();

// ═══ 自动编译 Rust native binary（启动时 cargo build 一把梭）═══
if (!_nativeBinary && !_nativeAutoBuilt) {
    const nativeDir = path.join(__dir, "native");
    const cargoToml = path.join(nativeDir, "Cargo.toml");
    if (existsSync(cargoToml)) {
        try {
            execSync("which cargo 2>/dev/null", { encoding: "utf-8" });
            process.stderr.write(`[galaxyos] auto-building Rust native binary in ${nativeDir}...\n`);
            const result = execSync("cargo build --release 2>&1", {
                cwd: nativeDir,
                encoding: "utf-8",
                timeout: 120000,
                maxBuffer: 1024 * 1024,
            });
            _nativeAutoBuilt = true;
            // 复制到 scripts/ 方便 JS 侧发现
            const src = path.join(nativeDir, "target", "release", "galaxyos-native");
            const dst = path.join(__dir, "scripts", "galaxyos-native");
            if (existsSync(src)) {
                try { copyFileSync(src, dst); chmodSync(dst, 0o755); } catch (_) {}
                _nativeBinary = dst;
                process.stderr.write(`[galaxyos] Rust native auto-built: ${dst}\n`);
            }
        } catch (e) {
            process.stderr.write(`[galaxyos] auto-build skipped (cargo not found or build failed): ${e.message?.slice(0, 80)}\n`);
        }
    }
}

// 解析 python3 可执行路径(兼容 sandbox 环境下 PATH 缺失的情况)
let _pythonBin = "python3";
try {
    const resolved = execSync("which python3 2>/dev/null || echo python3", { encoding: "utf-8" }).trim();
    if (resolved && resolved.startsWith("/")) _pythonBin = resolved;
} catch {
    // Fallback: try common paths directly
    for (const p of ["/usr/bin/python3", "/usr/local/bin/python3"]) {
        try { if (existsSync(p)) { _pythonBin = p; break; } } catch {}
    }
}

function _dbg(...args) {
    process.stderr.write(TAG + ' ' + args.join(' ') + '\n');
}

// ===========================================
let _nativeProc = null;
let _nativeRl = null;
let _nativePending = new Map();
let _nativeNextId = 10000;

function _ensureNativeProc() {
    if (_nativeProc && !_nativeProc.killed && _nativeProc.exitCode === null) return true;
    if (!_nativeBinary) return false;
    try {
        _nativeProc = spawn(_nativeBinary, [], {
            stdio: ['pipe', 'pipe', 'pipe'],
            env: { ...process.env, RUST_LOG: 'warn' },
        });
        _nativeProc.stderr.on('data', (d) => {
            const t = d.toString().trim();
            if (t) process.stderr.write('[galaxyos-native] ' + t + '\n');
        });
        _nativeProc.on('exit', () => { _nativeProc = null; _nativeRl = null; });
        _nativeRl = createInterface({ input: _nativeProc.stdout, crlfDelay: Infinity });
        _nativeRl.on('line', (line) => {
            try {
                const msg = JSON.parse(line.trim());
                if (msg.id !== undefined && msg.id !== null) {
                    const r = _nativePending.get(msg.id);
                    if (r) { _nativePending.delete(msg.id); r(msg); }
                }
            } catch (e) {}
        });
        return true;
    } catch (e) {
        process.stderr.write(`[galaxyos-native] spawn failed: ${e.message}\n`);
        return false;
    }
}

function callNative(method, params, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
        if (!_ensureNativeProc()) {
            reject(new Error('native binary not available'));
            return;
        }
        const id = _nativeNextId++;
        const timer = setTimeout(() => { _nativePending.delete(id); reject(new Error('native call timeout: ' + method)); }, timeoutMs);
        _nativePending.set(id, (msg) => {
            clearTimeout(timer);
            if (msg.error) reject(new Error(msg.error));
            else resolve(msg.result);
        });
        const line = JSON.stringify({ id, method, params }) + '\n';
        _nativeProc.stdin.write(line);
    });
}

function _stopNativeProc() {
    if (_nativeProc && !_nativeProc.killed) {
        try { _nativeProc.stdin.write(JSON.stringify({ id: 99999, method: 'shutdown', params: {} }) + '\n'); } catch (e) {}
        setTimeout(() => { try { _nativeProc?.kill('SIGTERM'); } catch (e) {} }, 2000).unref();
        _nativeProc = null;
        _nativeRl = null;
        _nativePending.clear();
    }
}

// ==========================================
// ClawWorkerClient - 常驻 Python 进程通信层
// ==========================================

/** UDS path for claw-worker socket — 多路径自发现（按优先级探测） */
function getUdsPath() {
    // 优先环境变量
    if (process.env.GALAXYOS_UDS_PATH && existsSync(path.dirname(process.env.GALAXYOS_UDS_PATH))) {
        return process.env.GALAXYOS_UDS_PATH;
    }
    // Python Worker 监听 claw-worker.sock（单文件，所有 worker 通过 HTTP keep-alive 复用）
    return path.join(
        OPENCLAW_HOME,
        "extensions/galaxyos/var/claw-worker.sock"
    );
}

/** 所有已知 UDS 路径（按优先级排序，自发现 fallback） */
function getUdsProbePaths() {
    const ocHome = OPENCLAW_HOME;
    const primary = getUdsPath();
    const paths = [primary];
    // worker:1 独立 socket（worker:1 绑定 claw-worker-worker-1.sock）
    const w1 = path.join(ocHome, "extensions/galaxyos/var/claw-worker-worker-1.sock");
    if (primary !== w1) paths.push(w1);
    // worker:2 独立 socket
    const w2 = path.join(ocHome, "extensions/galaxyos/var/claw-worker-worker-2.sock");
    if (primary !== w2) paths.push(w2);
    // 兼容旧版 galaxyos 共享 socket (legacy 模式，Python 端也可能 fallback 到它)
    paths.push(path.join(ocHome, "extensions/galaxyos/var/claw-worker.sock"));
    // 兼容旧版 claw-core 路径
    paths.push(path.join(ocHome, "extensions/claw-core/var/claw-worker.sock"));
    return [...new Set(paths)]; // 去重
}

/** Gateway UDS path for Worker → Gateway reverse RPC */
function getGatewayUdsPath() {
    if (process.env.GALAXYOS_GATEWAY_UDS_PATH) return process.env.GALAXYOS_GATEWAY_UDS_PATH;
    return path.join(
        OPENCLAW_HOME,
        "extensions/galaxyos/var/claw-gateway.sock"
    );
}

// ==========================================
// 三通道双向互通 - Gateway 端
// 1. UDS 服务端(Worker → Gateway 反向 RPC)
// 2. ZMQ ROUTER(Worker → Gateway 异步双向)
// 3. mmap 共享状态(双向同步)
// ==========================================

// ────────── 统一 RPC 注册表 ──────────
const _gatewayMethods = {};

function registerGatewayMethod(name, handler) {
    if (typeof handler !== 'function') throw new Error(`gateway method ${name} handler must be a function`);
    _gatewayMethods[name] = handler;
}

// ────────── Gateway RPC 帮助函数 ──────────
// HTTP over UDS: Gateway UDS server 改用 http.createServer
// _sendUdsResult/_sendUdsError 已废弃，由 HTTP 响应体替代


let _gatewayServer = null;
let _gatewayServerSock = null;
let _zmqRouter = null;
let _zmqRouterThread = null;

// ======== UDS 连接池（Plugin → Worker，复用连接减少握手开销） ========
const _udsHttpAgent = new http.Agent({
    keepAlive: true,
    keepAliveMsecs: 30000,
    maxSockets: 8,        // 单 Worker 最多 8 个并发连接
    maxFreeSockets: 4,    // 空闲保留 4 个（热连接）
    timeout: 60000,        // 空闲 60s 后回收
});

// 同时为每个 pool Worker 创建独立 Agent，避免跨 Worker 连接串扰
const _workerAgents = new Map(); // workerId → http.Agent
function _getWorkerAgent(workerId) {
    if (!_workerAgents.has(workerId)) {
        _workerAgents.set(workerId, new http.Agent({
            keepAlive: true,
            keepAliveMsecs: 30000,
            maxSockets: 8,
            maxFreeSockets: 4,
            timeout: 60000,
        }));
    }
    return _workerAgents.get(workerId);
}

// ======== Python Worker mmap 读取（大 payload 解引用）========
// v7.0: galaxyos/var 为主路径，claw-core/var 为 fallback
const WORKER_MMAP_PATH = path.join(
    OPENCLAW_HOME,
    "extensions/galaxyos/var/claw_worker_mmap"
);
const WORKER_MMAP_BACKCOMPAT = path.join(
    OPENCLAW_HOME,
    "extensions/claw-core/var/claw_worker_mmap"
);
function _readWorkerMmap(key) {
    // 读取 Python Worker 写的 mmap（4 字节大端长度前缀 + JSON）
    for (const p of [WORKER_MMAP_PATH, WORKER_MMAP_BACKCOMPAT]) {
        try {
            if (!existsSync(p)) continue;
            const fd = openSync(p, "r");
            try {
                const header = Buffer.alloc(4);
                if (readSync(fd, header, 0, 4, 0) < 4) { closeSync(fd); continue; }
                const len = header.readUInt32BE(0);
                if (len < 10 || len > 10 * 1024 * 1024) { closeSync(fd); continue; }
                const body = Buffer.alloc(len);
                if (readSync(fd, body, 0, len, 4) < len) { closeSync(fd); continue; }
                closeSync(fd);
                const data = JSON.parse(body.toString("utf-8"));
                if (data && data[key] !== undefined) return data[key];
            } catch (e) { try { closeSync(fd); } catch (_) {} }
        } catch (e) { /* file not found */ }
    }
    return null;
}

// ======== R-CCAM 会话级去重：同一 sessionKey 不重复提交 ========
const _rccamFlying = new Map(); // sessionKey → { promise: Promise, ts: number }
const _rccamProgress = new Map(); // sessionKey → { phase, status, cycle, ts, elapsedMs }
const _rccamFlyingMaxAgeMs = 300000; // 5 分钟过期（防止内存泄漏）
const _rccamProgressMaxAgeMs = 120000; // 2 分钟过期
function _rccamCleanStale() {
    const now = Date.now();
    for (const [k, v] of _rccamFlying) {
        if (now - v.ts > _rccamFlyingMaxAgeMs) _rccamFlying.delete(k);
    }
    for (const [k, v] of _rccamProgress) {
        if (now - v.ts > _rccamProgressMaxAgeMs) _rccamProgress.delete(k);
    }
}
setInterval(_rccamCleanStale, 60000).unref();

// ======== Worker 池 + 任务队列（替代单 Worker 架构） ========
let _workerPool = null;
let _poolConfig = { minSize: 2, maxSize: 8, size: 2, maxQueue: 20 };
let _galaxyPool = null;  // 统一系统管理器（GalaxyPool）

// ═══ 三级 Worker Tiers ═══
const TIER_HOT   = 'hot';
const TIER_WARM  = 'warm';
const TIER_COLD  = 'cold';

// Method → Tier 路由表
const METHOD_TIER = {
  // Hot：快进快出，<= 5s
  ping: TIER_HOT, health: TIER_HOT, memory_search: TIER_HOT, recall: TIER_HOT,
  vector_info: TIER_HOT, mmap_cleanup: TIER_HOT, events: TIER_HOT,
  memory_status: TIER_HOT,
  // Warm：中等负载，5~15s
  store: TIER_WARM, save_memory: TIER_WARM, verify: TIER_WARM,
  dag_ingest: TIER_WARM, dag_assemble: TIER_WARM, dag_compact: TIER_WARM,
  dag_search: TIER_WARM, dag_status: TIER_WARM, dag_summary: TIER_WARM,
  dag_clear_session: TIER_WARM,
  learn: TIER_WARM, learn_preference: TIER_WARM, learn_correction: TIER_WARM,
  remember: TIER_WARM, forget: TIER_WARM, get_entity: TIER_WARM, link_task_memory: TIER_WARM,
  import_knowledge: TIER_WARM,
  understand_image: TIER_WARM, ocr_image: TIER_WARM, recall_images: TIER_WARM,
  persona_snapshot: TIER_WARM, get_persona_core: TIER_WARM,
  implicit_feedback: TIER_WARM, hardinfo: TIER_WARM,
  list_workflows: TIER_WARM, list_modules: TIER_WARM, get_workflow_info: TIER_WARM,
  // Cold：重型负载，15~60s
  rccam: TIER_COLD, context_assemble: TIER_COLD, rlm_compress: TIER_COLD,
  compile_skill: TIER_COLD, asset_search: TIER_COLD, asset_register: TIER_COLD,
  cognitive_compress_dag: TIER_COLD, rccam_compact_needed: TIER_COLD,
  rccam_compact_cycle: TIER_COLD, expand_rccam_cycle: TIER_COLD,
  rccam_dag_stats: TIER_COLD, get_module_info: TIER_COLD,
  smart_process: TIER_COLD, execute_workflow: TIER_COLD,
  build_system_prompt: TIER_COLD, verify_reply_style: TIER_COLD,
  restore_context: TIER_COLD, answer: TIER_COLD,
  call_module: TIER_COLD,
};

// 每层独立配置 （Worker 数量、队列上限、默认超时）
const TIER_CONFIG = {
  [TIER_HOT]:  { minSize: 2, maxSize: 4, size: 2, maxQueue: 20, defaultTimeout: 8000,  workerIdPrefix: 'hot' },
  [TIER_WARM]: { minSize: 2, maxSize: 4, size: 2, maxQueue: 10, defaultTimeout: 20000, workerIdPrefix: 'warm' },
  [TIER_COLD]: { minSize: 1, maxSize: 2, size: 1, maxQueue: 5,  defaultTimeout: 60000, workerIdPrefix: 'cold' },
};

// Session 亲和路由缓存
const _sessionAffinity = new Map();  // sessionId → { tier, workerId, ts }
const AFFINITY_TTL = 5 * 60 * 1000; // 5 分钟过期

function _resolveAffinity(sessionId, tier) {
  if (!sessionId) return null;
  const entry = _sessionAffinity.get(sessionId);
  if (!entry || entry.tier !== tier) return null;
  if (Date.now() - entry.ts > AFFINITY_TTL) { _sessionAffinity.delete(sessionId); return null; }
  return entry.workerId;
}

function _setAffinity(sessionId, tier, workerId) {
  if (!sessionId) return;
  _sessionAffinity.set(sessionId, { tier, workerId, ts: Date.now() });
  // 懒惰剪枝：每 100 条清理一次
  if (_sessionAffinity.size > 200) {
    const now = Date.now();
    for (const [k, v] of _sessionAffinity) {
      if (now - v.ts > AFFINITY_TTL) _sessionAffinity.delete(k);
    }
  }
}

class WorkerPool {
    constructor(ws, cfg = {}) {
        this.ws = ws;
        this.minSize = cfg.minSize || 2;
        this.maxSize = cfg.maxSize || 8;
        this.size = Math.max(this.minSize, cfg.size || 2);
        this.maxQueue = cfg.maxQueue || 20;
        this.workerIdPrefix = cfg.workerIdPrefix || 'worker';  // 'hot', 'warm', 'cold' 等
        this.workers = new Map();
        this.busy = new Set();
        this.queue = [];
        this.counter = 0;
        this._healthTimer = null;
        this._scaleTimer = null;
        this._scaleCooldown = false;
        this._scaleHistory = [];
        this._ready = false;
        this._init();
    }

    _init() {
        for (let i = 0; i < this.size; i++) {
            this._spawnOne(`${this.workerIdPrefix}:${i + 1}`);
        }
        this._healthTimer = setInterval(() => this._healthCheck(), 10000).unref();
        // 弹性扩缩：15s 检查一次
        this._scaleTimer = setInterval(() => this._scaleCheck(), 15000).unref();
        this._ready = true;
    }

    _spawnOne(id) {
        if (this.workers.has(id)) return;
        const w = new ClawWorkerClient(this.ws, id);
        w.start().catch((e) => {
            process.stderr.write(`[galaxyos] pool spawn ${id} failed: ${e.message}\n`);
        });
        this.workers.set(id, w);
    }

    // ═══ 负载感知调度：选择最健康的空闲 Worker ═══
    _getIdleWorker() {
        let bestId = null, bestScore = -Infinity;
        for (const [id, w] of this.workers) {
            if (!w.ready || this.busy.has(id)) continue;
            // 评分：fail 少 > latency 低 > 最近活跃
            const fails = (w._fails || 0);
            const latency = w._lastLatencyMs || 100;  // 上次调用耗时
            const ageMs = Date.now() - (w._lastActiveTs || 0);
            // score = -fails*100 - latency/10 + ageMs/1000  (偏好低失败、低延迟、近期使用过的)
            const score = -fails * 100 - latency / 10 + Math.min(ageMs / 1000, 30);
            if (score > bestScore) { bestScore = score; bestId = id; }
        }
        return bestId;
    }

    _getOtherWorker(id) {
        for (const [oid, w] of this.workers) {
            if (oid !== id && w.ready && !this.busy.has(oid)) return oid;
        }
        return null;
    }

    // ═══ Worker 延迟追踪 ═══
    _trackLatency(workerId, ms) {
        const w = this.workers.get(workerId);
        if (!w) return;
        w._lastLatencyMs = ms;
        w._lastActiveTs = Date.now();
    }

    // ═══ 弹性扩缩 ═══

    _scaleCheck() {
        const total = this.workers.size;
        const busy = this.busy.size;
        const queueLen = this.queue.length;

        this._scaleHistory.push({ ts: Date.now(), queueLen, busy, total });
        if (this._scaleHistory.length > 20) this._scaleHistory.shift();

        // 扩容：队列堆积 >= 3 或全忙
        if ((queueLen >= 3 || busy >= total) && total < this.maxSize && !this._scaleCooldown) {
            const add = Math.min(2, this.maxSize - total);
            for (let i = 0; i < add; i++) {
                const newId = `${this.workerIdPrefix}:${total + i + 1}`;
                if (!this.workers.has(newId)) {
                    process.stderr.write(`[galaxyos] pool ▲ SCALE UP: +${newId} (q=${queueLen}, busy=${busy}/${total})\n`);
                    this._spawnOne(newId);
                }
            }
            this._scaleCooldown = true;
            setTimeout(() => { this._scaleCooldown = false; }, 30000).unref();
            return;
        }

        // 缩容：连续 3 个点全空闲 + 队列为空 + 多于 minSize
        if (this._scaleHistory.length >= 3 && total > this.minSize) {
            const last3 = this._scaleHistory.slice(-3);
            const allIdle = last3.every(p => p.busy === 0 && p.queueLen === 0 && p.total > this.minSize);
            if (allIdle) {
                let victimId = null;
                for (const [id, w] of this.workers) {
                    if (!this.busy.has(id) && w.ready) { victimId = id; break; }
                }
                if (victimId) {
                    process.stderr.write(`[galaxyos] pool ▼ SCALE DOWN: -${victimId} (idle, ${total}→${total-1})\n`);
                    const w = this.workers.get(victimId);
                    this.workers.delete(victimId);
                    this.busy.delete(victimId);
                    if (w) { w.call('shutdown', {}, 2000).catch(() => {}).finally(() => w.stop()); }
                }
            }
        }
    }

    // ═══ 请求调度 ═══

    async execute(method, params, priority = 'normal', timeoutMs = 28000) {
        const idle = this._getIdleWorker();
        if (idle) {
            this.busy.add(idle);
            try {
                const w = this.workers.get(idle);
                if (!w) throw new Error('worker disappeared');
                return await this._callWithRetry(w, method, params, timeoutMs, idle);
            } finally {
                this.busy.delete(idle);
                this._drainQueue();
            }
        }
        if (this.queue.length >= this.maxQueue) {
            throw new Error('pool queue full');
        }
        return new Promise((resolve, reject) => {
            const entry = { priority, run: () => this.execute(method, params, priority, timeoutMs).then(resolve).catch(reject), resolve, reject, label: method, ts: Date.now() };
            if (priority === 'high') {
                this.queue.unshift(entry);
            } else if (priority === 'background') {
                this.queue.push(entry);
            } else {
                const lastNormal = [...this.queue].reverse().find(e => e.priority === 'normal');
                const idx = lastNormal ? this.queue.lastIndexOf(lastNormal) + 1 : this.queue.length;
                this.queue.splice(idx, 0, entry);
            }
        });
    }

    // ═══ 批量 RPC：一次请求发送多个方法调用，单个 Worker 串行执行 ═══
    async batch(calls, timeoutMs = 30000) {
        if (!Array.isArray(calls) || calls.length === 0) return [];
        const idle = this._getIdleWorker();
        if (idle) {
            this.busy.add(idle);
            try {
                const w = this.workers.get(idle);
                if (!w) throw new Error('worker disappeared');
                const params = { calls: calls.map(c => ({ method: c.method, params: c.params || {} })) };
                const t0 = Date.now();
                const result = await w.call('batch', params, timeoutMs * calls.length);
                this._trackLatency(idle, Date.now() - t0);
                return Array.isArray(result) ? result : (result?.results || []);
            } finally {
                this.busy.delete(idle);
                this._drainQueue();
            }
        }
        if (this.queue.length >= this.maxQueue) throw new Error('pool queue full');
        return new Promise((resolve, reject) => {
            this.queue.push({ priority: 'normal', run: () => this.batch(calls, timeoutMs).then(resolve).catch(reject), resolve, reject, label: 'batch:' + calls.length, ts: Date.now() });
        });
    }

    async _callWithRetry(w, method, params, timeoutMs, workerId) {
        const MAX_RETRIES = 1;
        let lastError = null;
        const t0 = Date.now();
        for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
            try {
                const result = await w.call(method, params, timeoutMs);
                this._trackLatency(workerId, Date.now() - t0);
                return result;
            } catch (e) {
                lastError = e;
                w._fails = (w._fails || 0) + 1;
                if (!e.message?.includes('timeout') && !e.message?.includes('ECONNRESET') && !e.message?.includes('EPIPE')) {
                    throw e;
                }
                if (attempt >= MAX_RETRIES) {
                    process.stderr.write(`[galaxyos] pool ${workerId} retry exhausted for ${method}: ${e.message}\n`);
                    throw e;
                }
                const otherId = this._getOtherWorker(workerId);
                if (!otherId) {
                    process.stderr.write(`[galaxyos] pool ${workerId} no other worker for retry\n`);
                    throw e;
                }
                const otherW = this.workers.get(otherId);
                if (!otherW) throw e;
                process.stderr.write(`[galaxyos] pool ${workerId} timeout retry → ${otherId} for ${method}\n`);
                w = otherW;
                workerId = otherId;
                timeoutMs = Math.max(timeoutMs - 3000, 15000);
            }
        }
        throw lastError;
    }

    // ═══ 直接派发到指定 Worker（session 亲和性用） ═══
    async _executeOne(workerId, method, params, timeoutMs, tier) {
        const w = this.workers.get(workerId);
        if (!w || !w.ready) throw new Error(`worker ${workerId} not ready`);
        this.busy.add(workerId);
        try {
            const result = await this._callWithRetry(w, method, params, timeoutMs, workerId);
            // 记录 session affinity
            let sessionId = (params && (params.sessionId || params.session_id || params.session_key || params.dag_key)) || null;
            if (sessionId && typeof sessionId !== 'string') { try { sessionId = String(sessionId); } catch (e) { sessionId = null; } }
            if (sessionId) _setAffinity(sessionId, tier, workerId);
            return result;
        } finally {
            this.busy.delete(workerId);
            this._drainQueue();
        }
    }

    // ═══ 获取指定 tier 的 fallback Worker（降级用） ═══
    getFallback() {
        const firstWorker = this.workers.values().next().value;
        return firstWorker || null;
    }

    _drainQueue() {
        while (this.queue.length > 0) {
            const idle = this._getIdleWorker();
            if (!idle) break;
            const entry = this.queue.shift();
            if (entry) {
                this.busy.add(idle);
                entry.run().finally(() => {
                    this.busy.delete(idle);
                    this._drainQueue();
                });
            }
        }
    }

    // ═══ 健康检查 ═══

    _healthCheck() {
        for (const [id, w] of this.workers) {
            if (!w.ready || w._fails >= w._maxFails) {
                process.stderr.write(`[galaxyos] pool ${id} unhealthy (ready=${w.ready}, fails=${w._fails}/${w._maxFails}), respawn if needed\n`);
                w.stop();
                this.workers.delete(id);
                this.busy.delete(id);
                if (this.workers.size < this.minSize) {
                    this._spawnOne(id);
                }
            }
        }
    }

    async shutdown() {
        if (this._healthTimer) clearInterval(this._healthTimer);
        if (this._scaleTimer) clearInterval(this._scaleTimer);
        const promises = [];
        for (const [id, w] of this.workers) {
            promises.push(w.call('shutdown', {}, 2000).catch(() => {}).finally(() => w.stop()));
        }
        await Promise.allSettled(promises);
        this.workers.clear();
        this.busy.clear();
        this.queue = [];
        this._ready = false;
    }
}

// ═══════════════════════════════════════════════════════════════
// GalaxyPool — 统一兜住整个 GalaxyOS 系统的组件管理器
// 单入口启动/停止/健康检查，6 类组件全生命周期托管：
//   workers | gateway_uds | zmq_router | mmap_control | native_binary | gateway_heartbeat
// ═══════════════════════════════════════════════════════════════
const _componentDefaults = {
    workers:           { order: 5, critical: true,  restartMax: 10 },
    gateway_uds:       { order: 1, critical: true,  restartMax: 5  },
    zmq_router:        { order: 2, critical: false, restartMax: 5  },
    mmap_control:      { order: 0, critical: true,  restartMax: 3  },
    native_binary:     { order: 3, critical: false, restartMax: 3  },
    gateway_heartbeat: { order: 4, critical: false, restartMax: 0  },
};

class GalaxyPool {
    constructor(api, ws, cfg = {}) {
        this.api = api;
        this._logger = { info: (...a) => _dbg(...a), warn: (...a) => _dbg('WARN:', ...a), debug: (...a) => _dbg('DBG:', ...a) };
        this.ws = ws;
        this.status = 'init';  // init → starting → running → degraded → stopping → stopped
        this.cfg = cfg;
        // 内部组件注册表：name → { status, start, stop, health, order, critical, restartCount, restartMax }
        this._comps = new Map();
        // 三级 WorkerPool（Hot / Warm / Cold）
        this._tierPools = new Map();  // tier → { pool: WorkerPool, cfg: {…} }
        this._poolCfg = cfg.workers || { minSize: 2, maxSize: 8, size: 2, maxQueue: 20 };
        this._workerPool = null;  // 保留兼容引用，getStatus 用
        this._healthTimer = null;
        this._ready = false;
        // 所有模块级 global 统一从这取
        this._refs = {};  // 存 zmq router / gateway server / native proc 等句柄
    }

    // ═══ 组件注册 ═══
    _reg(name, opts) {
        const def = _componentDefaults[name] || {};
        this._comps.set(name, {
            status: 'stopped',
            start:  opts.start  || (() => Promise.resolve()),
            stop:   opts.stop   || (() => Promise.resolve()),
            health: opts.health || (() => ({ ok: true })),
            order:  def.order,
            critical: def.critical,
            restartMax: def.restartMax,
            restartCount: 0,
            dependsOn: opts.dependsOn || [],
        });
    }

    // ═══ 单入口启动（依赖拓扑排序） ═══
    async start() {
        if (this.status === 'running' || this.status === 'degraded') return;
        this.status = 'starting';
        const ordered = [...this._comps.entries()]
            .sort(([, a], [, b]) => a.order - b.order)
            .map(([name]) => name);

        for (const name of ordered) {
            const comp = this._comps.get(name);
            if (!comp) continue;
            try {
                const r = comp.start();
                const result = r instanceof Promise ? await r : r;
                if (result && typeof result === 'object') Object.assign(this._refs, result);
                comp.status = 'running';
                comp.restartCount = 0;
                this._logger.info?.(`${TAG} [galaxy-pool] ${name} started`);
            } catch (e) {
                comp.status = 'failed';
                comp.restartCount++;
                this._logger.warn?.(`${TAG} [galaxy-pool] ${name} start failed: ${e.message}`);
                if (comp.critical) {
                    this.status = 'degraded';
                }
            }
        }
        this._ready = true;
        if (this.status !== 'degraded') this.status = 'running';
        this._startHealthLoop();
    }

    // ═══ 单入口停止（逆序） ═══
    async stop() {
        this.status = 'stopping';
        if (this._healthTimer) { clearInterval(this._healthTimer); this._healthTimer = null; }

        // 关闭前先 flush cron state
        try {
            if (process.env.OPENCLAW_NO_RESPAWN || process.argv.includes('gateway')) {
                await this._flushCronState();
            }
        } catch (e) {
            this._logger.warn?.(`${TAG} cron state flush error (non-fatal): ${e.message}`);
        }

        const ordered = [...this._comps.entries()]
            .sort(([, a], [, b]) => b.order - a.order)
            .map(([name]) => name);

        // Tier pools first (internal)
        for (const [tier, tp] of this._tierPools) {
            try { await tp.pool.shutdown(); } catch (e) {}
        }
        this._tierPools.clear();
        if (this._workerPool) {
            try { await this._workerPool.shutdown(); } catch (e) {}
            this._workerPool = null;
        }

        for (const name of ordered) {
            const comp = this._comps.get(name);
            if (comp && comp.status === 'running') {
                try { await comp.stop(); } catch (e) {}
                comp.status = 'stopped';
            }
        }
        this._ready = false;
        this.status = 'stopped';
        this._logger.info?.(`${TAG} [galaxy-pool] all components stopped`);
    }

    // ═══ 统一健康检查（每 10s） ═══
    _startHealthLoop() {
        this._healthTimer = setInterval(() => {
            for (const [name, comp] of this._comps) {
                if (comp.status !== 'running') continue;
                try {
                    const h = comp.health();
                    if (h && typeof h === 'object' && h.ok === false) {
                        this._handleUnhealthy(name, comp, h.error || 'health check failed');
                    }
                } catch (e) {
                    this._handleUnhealthy(name, comp, e.message);
                }
            }
            // 也检查所有 tier pools 内部
            for (const [tier, tp] of this._tierPools) {
                try { tp.pool._healthCheck(); } catch (e) {}
            }
            if (this._workerPool) {
                this._workerPool._healthCheck();
            }
            // 非关键检查：cron state 文件存在性
            const cronStatePath = path.join(OPENCLAW_HOME, "cron", "jobs-state.json");
            const cronJobsPath = path.join(OPENCLAW_HOME, "cron", "jobs.json");
            if (!existsSync(cronStatePath) && !existsSync(cronJobsPath)) {
                this._logger.warn?.(`${TAG} cron state files missing (non-critical), cron flush may be unavailable`);
            }
        }, 10000).unref();
    }

    _handleUnhealthy(name, comp, reason) {
        comp.restartCount++;
        this._logger.warn?.(`${TAG} [galaxy-pool] ${name} unhealthy (${comp.restartCount}/${comp.restartMax}): ${reason}`);
        if (comp.restartCount > comp.restartMax) {
            comp.status = 'dead';
            if (comp.critical) this.status = 'degraded';
            return;
        }
        comp.status = 'restarting';
        try {
            comp.stop();
        } catch (_) {}
        try {
            const r = comp.start();
            if (r instanceof Promise) { r.then(() => { comp.status = 'running'; }).catch(() => { comp.status = 'failed'; }); }
            else { comp.status = 'running'; }
        } catch (_) {
            comp.status = 'failed';
        }
    }

    // ═══ Cron 状态持久化 ═══
    async _flushCronState() {
        const cronFlusher = path.join(__dir, "scripts", "cron_state_flusher.py");
        if (!existsSync(cronFlusher)) {
            this._logger.warn?.(`${TAG} cron_state_flusher.py not found, skipping flush`);
            return;
        }
        const CRON_DIR = path.join(OPENCLAW_HOME, "cron");
        if (!existsSync(CRON_DIR)) {
            this._logger.info?.(`${TAG} cron dir not found, skipping flush`);
            return;
        }
        return new Promise((resolve) => {
            const timeout = setTimeout(() => {
                this._logger.warn?.(`${TAG} cron state flush timed out after 8s`);
                try { proc.kill('SIGTERM'); } catch {}
                resolve();
            }, 8000);
            const proc = spawn(_pythonBin, [cronFlusher, 'flush'], {
                stdio: ['ignore', 'pipe', 'pipe'],
                env: { ...process.env },
            });
            let stdout = '';
            let stderr = '';
            proc.stdout.on('data', (d) => { stdout += d.toString(); });
            proc.stderr.on('data', (d) => { stderr += d.toString(); });
            proc.on('close', (code) => {
                clearTimeout(timeout);
                if (code !== 0) {
                    this._logger.warn?.(`${TAG} cron state flush exited code=${code}: ${(stderr || stdout).slice(0, 200)}`);
                } else {
                    this._logger.info?.(`${TAG} cron state flushed successfully`);
                }
                resolve();
            });
            proc.on('error', (err) => {
                clearTimeout(timeout);
                this._logger.warn?.(`${TAG} cron state flush spawn error: ${err.message}`);
                resolve();
            });
        });
    }

    // ═══ 状态快照 ═══
    getStatus() {
        const comps = {};
        for (const [name, c] of this._comps) {
            comps[name] = { status: c.status, restartCount: c.restartCount, critical: c.critical };
        }
        // 三级 Tier 状态
        const tierStatus = {};
        for (const [tier, tp] of this._tierPools) {
            if (tp.pool) {
                tierStatus[tier] = {
                    total: tp.pool.workers.size,
                    busy: tp.pool.busy.size,
                    queue: tp.pool.queue.length,
                    config: tp.cfg,
                };
            }
        }
        comps.tier_pools = tierStatus;
        if (this._workerPool) {
            comps.worker_pool_legacy = {
                total: this._workerPool.workers.size,
                busy: this._workerPool.busy.size,
                queue: this._workerPool.queue.length,
            };
        }
        // R-CCAM 实时进度
        const activeProgress = [];
        for (const [k, v] of _rccamProgress) {
            if (Date.now() - v.ts < _rccamProgressMaxAgeMs) {
                activeProgress.push({ session: k.slice(0, 40), ...v });
            }
        }
        return { status: this.status, components: comps, pid: process.pid, rccam_active: activeProgress.length, rccam_progress: activeProgress };
    }

    // ═══ Worker 请求调度（三级 Tier 路由） ═══
    execute(method, params, priority = 'normal', timeoutMs = 28000) {
        // 按 method 路由到对应 tier
        const tier = METHOD_TIER[method] || TIER_HOT;
        const tierPool = this._tierPools.get(tier);
        if (tierPool && tierPool.pool) {
            // session 亲和性
            let sessionId = (params && (params.sessionId || params.session_id || params.session_key || params.dag_key)) || null;
            if (sessionId && typeof sessionId !== 'string') {
                try { sessionId = String(sessionId); } catch (e) { sessionId = null; }
            }
            const affinityId = sessionId ? _resolveAffinity(sessionId, tier) : null;
            if (affinityId) {
                // 如果绑定的 Worker 还活着，优先用它
                const affWorker = tierPool.pool.workers.get(affinityId);
                if (affWorker && affWorker.ready && !tierPool.pool.busy.has(affinityId)) {
                    return tierPool.pool._executeOne(affinityId, method, params, timeoutMs, tier);
                }
                // Worker 挂了，删掉绑定，重新调度
                if (sessionId) _sessionAffinity.delete(sessionId);
            }
            // 普通调度，完成后尝试记录 session 亲和性
            const p = tierPool.pool.execute(method, params, priority, timeoutMs);
            if (sessionId) {
                return p.then(result => {
                    // 找刚刚活跃的 Worker（基于 _lastActiveTs）
                    const now = Date.now();
                    for (const [wid, w] of tierPool.pool.workers) {
                        if (w.ready && !tierPool.pool.busy.has(wid) && (w._lastActiveTs || 0) > now - 3000) {
                            _setAffinity(sessionId, tier, wid);
                            break;
                        }
                    }
                    return result;
                });
            }
            return p;
        }
        // 降级到旧 WorkerPool
        if (this._workerPool) {
            return this._workerPool.execute(method, params, priority, timeoutMs);
        }
        return Promise.reject(new Error('GalaxyPool: no worker pool available for method=' + method));
    }

    // ═══ 组件引用访问 ═══
    getGatewayServer() { return this._refs._gatewayServer; }
    getZmqRouter()    { return this._refs._zmqRouter; }
    getNativeProc()   { return this._refs._nativeProc; }
    get isReady()     { return this._ready && this.status === 'running'; }
}

/**
 * 启动 Gateway UDS 服务端 - Worker 通过此通道 RPC 调 Gateway 能力
 * 使用动态 _gatewayMethods 注册表替代旧 switch-case
 */
function startGatewayUdsServer(api, _ws) {
    const _workspace = _ws;
    const udsPath = getGatewayUdsPath();
    // 强制清理：多次 unlink + 短暂的等待确保内核释放 socket inode
    for (let i = 0; i < 3; i++) {
        try { unlinkSync(udsPath); } catch (e) {}
    }
    // 同时清理可能遗留的 Worker UDS（旧进程残留）
    for (const p of getUdsProbePaths()) {
        try { unlinkSync(p); } catch (e) {}
    }
    const dir = path.dirname(udsPath);
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });

    // ── 注册默认方法 ──
    registerGatewayMethod("ping", async (params, ctx) => ({
        ok: true, pid: process.pid, gateway: true, methods: Object.keys(_gatewayMethods).length
    }));
    registerGatewayMethod("list_methods", async (params, ctx) => ({
        methods: Object.keys(_gatewayMethods).sort()
    }));
    registerGatewayMethod("get_env", async (params, ctx) => ({
        ok: true, value: process.env[params.key] || ""
    }));
    registerGatewayMethod("get_workspace", async (params, ctx) => ({
        ok: true, workspace: _workspace
    }));
    registerGatewayMethod("read_file", async (params, ctx) => {
        const fpath = params.path || "";
        if (!fpath) throw new Error("path required");
        const maxChars = params.maxChars || 50000;
        const content = readFileSync(fpath, "utf-8");
        const truncated = content.length > maxChars ? content.slice(0, maxChars) : content;
        return { ok: true, content: truncated, length: content.length };
    });
    registerGatewayMethod("write_file", async (params, ctx) => {
        const fpath = params.path || "";
        const content = params.content || "";
        if (!fpath) throw new Error("path required");
        writeFileSync(fpath, content, "utf-8");
        return { ok: true, bytes: Buffer.byteLength(content, "utf-8") };
    });
    registerGatewayMethod("web_fetch", async (params, ctx) => {
        const url = params.url || "";
        if (!url) throw new Error("url required");
        const maxChars = params.maxChars || 50000;
        const resp = await fetch(url);
        let text = await resp.text();
        if (text.length > maxChars) text = text.slice(0, maxChars);
        return { ok: true, content: text, contentType: resp.headers.get("content-type"), status: resp.status };
    });
    registerGatewayMethod("web_search", async (params, ctx) => {
        const query = params.query || "";
        if (!query) throw new Error("query required");
        const num = params.num || 3;
        const { execSync } = await import("node:child_process");
        const script = path.join(_workspace, "skills", "xiaoyi-web-search", "scripts", "search.js");
        if (!existsSync(script)) throw new Error("search script not found");
        const result = execSync(
            `node ${JSON.stringify(script)} ${JSON.stringify(query)} -n ${num}`,
            { encoding: "utf-8", timeout: 15000, cwd: path.dirname(script) }
        );
        return { ok: true, content: result || "" };
    });
    registerGatewayMethod("call_tool", async (params, ctx) => {
        const toolName = params.tool || "";
        const toolArgs = params.args || {};
        if (!toolName) throw new Error("tool name required");
        if (api.tools && typeof api.tools.call === "function") {
            const toolResult = await api.tools.call(toolName, toolArgs);
            return { ok: true, result: toolResult };
        }
        throw new Error("api.tools.call not available");
    });
    registerGatewayMethod("dag_status", async (params, ctx) => ({
        ok: true, circuit: _dagCB.status, pid: process.pid
    }));
    registerGatewayMethod("mmap_read", async (params, ctx) => {
        return _mmapSyncRead();
    });
    // ── GalaxyPool 状态查询（供 Worker / 外部监控）──
    registerGatewayMethod("galaxy_pool_status", async (params, ctx) => {
        if (_galaxyPool) return _galaxyPool.getStatus();
        return { status: 'not_initialized' };
    });
    registerGatewayMethod("channel_send", async (params, ctx) => {
        // 通过 api.emit 或 hook 触发消息发送
        // 实际发送由 OpenClaw 通道处理
        api.logger.info?.(`${TAG} [gateway-uds] channel_send requested: ${JSON.stringify(params).slice(0, 200)}`);
        // 通过 api 的 event 触发--这里先做 stub
        return { ok: true, note: "channel_send noted - handled by OpenClaw pipeline" };
    });
    // ── Rust native 向量计算（Gateway 代理 → galaxyos-native 二进制）──
    registerGatewayMethod("vector_batch_cosine", async (params, ctx) => {
        const result = await callNative("vector_batch_cosine", params, 10000);
        return result;
    });
    registerGatewayMethod("vector_topk", async (params, ctx) => {
        const result = await callNative("vector_topk", params, 15000);
        return result;
    });
    registerGatewayMethod("vector_cosine", async (params, ctx) => {
        const result = await callNative("vector_cosine", params, 5000);
        return result;
    });
    // 自动从注册的工具列表暴露
    if (api.tools && api.tools.list) {
        try {
            const toolNames = api.tools.list();
            for (const tn of (toolNames || [])) {
                registerGatewayMethod(`tool.${tn}`, async (params, ctx) => {
                    const toolResult = await api.tools.call(tn, params);
                    return { ok: true, result: toolResult };
                });
            }
            api.logger.info?.(`${TAG} [gateway-uds] auto-registered ${(toolNames || []).length} tool methods`);
        } catch (e) {
            api.logger.debug?.(`${TAG} [gateway-uds] tool auto-register: ${e.message}`);
        }
    }

    const server = http.createServer((req, res) => {
        let body = '';
        req.on('data', chunk => body += chunk);
        req.on('end', async () => {
            try {
                const rpc = JSON.parse(body);
                const id = rpc.id;
                const method = rpc.method || '';
                const params = rpc.params || {};
                const handler = _gatewayMethods[method];
                if (!handler) {
                    res.writeHead(404, { 'Content-Type': 'application/json' });
                    res.end(JSON.stringify({ id, error: 'Unknown gateway method: ' + method }));
                    return;
                }
                const result = await handler(params, { req, res });
                res.writeHead(200, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ id, result }));
            } catch (e) {
                res.writeHead(500, { 'Content-Type': 'application/json' });
                res.end(JSON.stringify({ id: null, error: e.message }));
            }
        });
    });

    server.listen(udsPath, () => {
        try { chmodSync(udsPath, 0o600); } catch (e) {}
        api.logger.info?.(`${TAG} [gateway-uds] HTTP over UDS listening on ${udsPath} (${Object.keys(_gatewayMethods).length} methods registered)`);
    });

    _gatewayServer = server;
    _gatewayServerSock = udsPath;
    return server;
}

function stopGatewayUdsServer() {
    if (_gatewayServer) {
        try { _gatewayServer.close(); } catch (e) {}
        _gatewayServer = null;
    }
    try { unlinkSync(_gatewayServerSock); } catch (e) {}
}

// ────────── ZMQ ROUTER 双向通道(回复版)──────────
function startZmqRouter(api) {
    const zmqPath = path.join(
        OPENCLAW_HOME,
        "extensions/galaxyos/var/claw-router.ipc"
    );
    try { unlinkSync(zmqPath); } catch (e) {}

    let zmq;
    try {
        zmq = _cjsRequire(path.join(__dir, "node_modules", "zeromq"));
    } catch (e) {
        api.logger.warn?.(`${TAG} [zmq-router] zeromq not available, skipping`);
        return;
    }

    const router = new zmq.Router();
    // routerHandover: modern zeromq Router instances are non-extensible,
    // and the handover behavior is the default in libzmq >= 4.2.

    (async () => {
        try {
            await router.bind(`tcp://127.0.0.1:5560`);
            api.logger.info?.(`${TAG} [zmq-router] listening on tcp://127.0.0.1:5560`);
        } catch (err) {
            api.logger.warn?.(`${TAG} [zmq-router] bind failed: ${err.message}`);
            return;
        }

        // bind 完成后才进入消息循环，避免 "Socket is blocked by a bind or unbind operation"
        try {
            for await (const [identity, _, ...frames] of router) {
                // 每帧是一个完整 JSON 请求{method, params, id?}
                // 回复发回同一个 identity
                const sendReply = (replyPayload) => {
                    try {
                        router.send([identity, "", Buffer.from(JSON.stringify(replyPayload))]);
                    } catch (e) {}
                };
                for (const frame of frames) {
                    try {
                        const msg = JSON.parse(frame.toString());
                        if (msg.method) {
                            const method = msg.method;
                            const params = msg.params || {};
                            // Worker → Worker 转发
                            if (method === 'worker_send') {
                                const target = params.target || '';
                                const payload = params.payload || {};
                                if (target && _zmqRouter) {
                                    try {
                                        _zmqRouter.send([Buffer.from(target), '', Buffer.from(JSON.stringify({ worker_send: true, from: identity.toString(), payload }))]);
                                        sendReply({ id: msg.id, result: { ok: true, target } });
                                        api.logger.debug?.(`${TAG} [zmq-router] forwarded ${identity.toString()} → ${target}`);
                                    } catch (e) {
                                        sendReply({ id: msg.id, error: `forward failed: ${e.message}` });
                                    }
                                } else {
                                    sendReply({ id: msg.id, error: 'target or router unavailable' });
                                }
                                continue;
                            }
                            const handler = _gatewayMethods[method];
                            if (handler) {
                                try {
                                    const result = await handler(params, { identity: identity.toString() });
                                    sendReply({ id: msg.id, result });
                                } catch (e) {
                                    sendReply({ id: msg.id, error: e.message });
                                }
                            } else {
                                // 透传事件(无 handler 即 pub-sub 事件)
                                api.logger.debug?.(`${TAG} [zmq-router] event from ${identity.toString()}: ${method} ${JSON.stringify(params).slice(0, 200)}`);
                            }
                        } else if (msg.event) {
                            // 纯粹的事件通知(不需要回复)
                            api.logger.debug?.(`${TAG} [zmq-router] event ${msg.event} from ${identity.toString()}`);
                        }
                    } catch (e) {}
                }
            }
        } catch (e) {
            api.logger.warn?.(`${TAG} [zmq-router] loop error: ${e.message}`);
        }
    })();

    _zmqRouter = router;
    return router;
}

function stopZmqRouter() {
    if (_zmqRouter) {
        try { _zmqRouter.close(); } catch (e) {}
        _zmqRouter = null;
    }
}

// ────────── mmap 结构化共享状态 ──────────
const MMAP_PATH = path.join(
    OPENCLAW_HOME,
    "extensions/galaxyos/var/claw_mmap_control"
);
const MMAP_SIZE = 4096; // 4KB shared state
// 固定偏移:
//   0-15:   signal (int32LE) + value (int32LE) + ts (double) [旧兼容]
//   16-47:  gateway_ts (double) + worker_ts (double) + reserved
//   48-79:  heartbeat (double) + config_version (int32) + flags (int32) + reserved
//   80-4095: JSON 段(灵活扩展)

function initMmapControl() {
    const fd = openSync(MMAP_PATH, fs.constants.O_CREAT | fs.constants.O_RDWR, 0o666);
    writeSync(fd, Buffer.alloc(MMAP_SIZE, 0));
    closeSync(fd);
    try { chmodSync(MMAP_PATH, 0o600); } catch (e) {}
}

function mmapWriteSignal(signalType, value = 1) {
    const fd = openSync(MMAP_PATH, fs.constants.O_RDWR);
    try {
        const buf = Buffer.alloc(16);
        buf.writeInt32LE(signalType, 0);
        buf.writeInt32LE(value, 4);
        buf.writeDoubleLE(Date.now(), 8);
        writeSync(fd, buf, 0, 16, 0);
    } finally {
        closeSync(fd);
    }
}

function mmapReadSignal() {
    const fd = openSync(MMAP_PATH, fs.constants.O_RDONLY);
    try {
        const buf = Buffer.alloc(16);
        const bytes = readSync(fd, buf, 0, 16, 0);
        if (bytes >= 8) {
            return {
                signal: buf.readInt32LE(0),
                value: buf.readInt32LE(4),
                ts: bytes >= 16 ? buf.readDoubleLE(8) : 0,
            };
        }
        return { signal: 0, value: 0, ts: 0 };
    } finally {
        closeSync(fd);
    }
}

/**
 * mmap 结构化状态写入(JSON 段,4KB 共享区)
 * 写入 /dev/shm/claw_shared_state 供两边零拷贝
 */
const MMAP_SHM = path.join(
    OPENCLAW_HOME,
    "extensions/galaxyos/var/claw_shared_state"
);

function _mmapSyncInit() {
    try {
        const dir = path.dirname(MMAP_SHM);
        if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
        const fd = openSync(MMAP_SHM, fs.constants.O_CREAT | fs.constants.O_RDWR, 0o666);
        writeSync(fd, Buffer.alloc(MMAP_SIZE, 0));
        closeSync(fd);
        try { chmodSync(MMAP_SHM, 0o600); } catch (e) {}
    } catch (e) {
        // 降级
    }
}

function _mmapSyncWrite(data) {
    try {
        const jsonStr = JSON.stringify({ ts: Date.now(), ...data });
        const buf = Buffer.alloc(MMAP_SIZE, 0);
        const payloadBuf = Buffer.from(jsonStr, "utf-8");
        if (payloadBuf.length > MMAP_SIZE - 4) throw new Error("mmap payload too large");
        buf.writeUInt32LE(payloadBuf.length, 0);
        payloadBuf.copy(buf, 4, 0, payloadBuf.length);
        const fd = openSync(MMAP_SHM, fs.constants.O_RDWR);
        try {
            writeSync(fd, buf, 0, MMAP_SIZE, 0);
        } finally {
            closeSync(fd);
        }
    } catch (e) {
        // mmap silent fail
    }
}

function _mmapSyncRead() {
    try {
        if (!existsSync(MMAP_SHM)) return { status: "uninitialized" };
        const fd = openSync(MMAP_SHM, fs.constants.O_RDONLY);
        try {
            const buf = Buffer.alloc(MMAP_SIZE);
            const bytes = readSync(fd, buf, 0, MMAP_SIZE, 0);
            if (bytes < 4) return { status: "empty" };
            const payloadLen = buf.readUInt32LE(0);
            if (payloadLen < 1 || payloadLen > MMAP_SIZE - 4) return { status: "invalid" };
            const jsonStr = buf.slice(4, 4 + payloadLen).toString("utf-8");
            return JSON.parse(jsonStr);
        } finally {
            closeSync(fd);
        }
    } catch (e) {
        return { status: "error", error: e.message };
    }
}

// ────────── 心跳 mmap(Worker 端写入,只读 8 字节 float64 时间戳)──────────
const HB_PATH = path.join(
    OPENCLAW_HOME,
    "extensions/galaxyos/var/claw_worker_heartbeat"
);

/**
 * 读取 Worker 心跳时间戳
 * 返回: { alive: bool, ts: number|null, age_ms: number|null }
 * alive 条件: 文件存在且时间戳在 5 秒内
 */
function _readWorkerHeartbeat() {
    try {
        const fd = openSync(HB_PATH, fs.constants.O_RDONLY);
        try {
            const buf = Buffer.alloc(8);
            const bytes = readSync(fd, buf, 0, 8, 0);
            if (bytes < 8) return { alive: false, ts: null, age_ms: null };
            const ts = buf.readDoubleLE(0) * 1000;  // float64 秒 → 毫秒
            const now = Date.now();
            const age = now - ts;
            return { alive: age < 5000, ts, age_ms: Math.round(age) };
        } finally {
            closeSync(fd);
        }
    } catch (e) {
        return { alive: false, ts: null, age_ms: null, error: e.message };
    }
}

// Gateway 心跳写入独立 8 字节文件(与 Worker 心跳分离,mmap 仅用于 R-CCAM 持久化)
const GATEWAY_HB_PATH = path.join(
    OPENCLAW_HOME,
    "extensions/galaxyos/var/claw_gateway_heartbeat"
);
let _gatewayHbTimer = null;
function _startGatewayHeartbeat(api) {
    try {
        const dir = path.dirname(GATEWAY_HB_PATH);
        if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
    } catch (_e) {}
    _gatewayHbTimer = setInterval(() => {
        try {
            const buf = Buffer.alloc(8);
            buf.writeDoubleLE(Date.now() / 1000, 0);
            const fd = openSync(GATEWAY_HB_PATH, fs.constants.O_CREAT | fs.constants.O_RDWR, 0o666);
            try {
                writeSync(fd, buf, 0, 8, 0);
            } finally {
                closeSync(fd);
            }
        } catch (_e) {}
    }, 5000).unref();
}

class ClawWorkerClient {
    constructor(workspace, id) {
        this.id = id || 'worker:default';
        this.workspace = workspace;
        this.client = null;          // HTTP mode: null (每次 call 新建连接)
        this.pending = new Map();
        this._nextId = 1;
        this._ready = false;
        this._fails = 0;
        this._maxFails = 5;
        this._shutdown = false;
        this._startPromise = null;
        this._reconnectTimer = null;
        // 每个 Worker 用独立 UDS socket：Python 端用 WORKER_ID → claw-worker-worker-1.sock
        this._udsPath = this.id && this.id !== 'worker:default'
            ? path.join(OPENCLAW_HOME, `extensions/galaxyos/var/claw-worker-${this.id.replace(':', '-')}.sock`)
            : getUdsPath();
        this._udsMode = null;        // 'http' | 'raw' | null
    }

    async start() {
        if (this._startPromise) return this._startPromise;
        if (this._ready) return;

        this._shutdown = false;
        this._startPromise = this._doStart();

        try {
            await this._startPromise;
        } finally {
            this._startPromise = null;
        }
    }

    // === HTTP over UDS (替换原始二进制协议) ===

    async _httpCall(method, params, timeoutMs) {
        const id = this._nextId++;
        const body = JSON.stringify({ id, method, params });
        return new Promise((resolve, reject) => {
            const req = http.request({
                socketPath: this._udsPath,
                path: '/',
                method: 'POST',
                agent: _getWorkerAgent(this.id),
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(body),
                    'Connection': 'keep-alive',
                },
                timeout: timeoutMs,
            }, (res) => {
                let data = '';
                res.on('data', chunk => data += chunk);
                res.on('end', () => {
                    try {
                        const msg = JSON.parse(data);
                        if (msg.event === 'ready') { this._ready = true; this._fails = 0; }
                        if (msg.error) reject(new Error(typeof msg.error === 'string' ? msg.error : JSON.stringify(msg.error)));
                        else {
                            let result = msg.result;
                            // ═══ mmap 大 payload 解引用：Worker 只回了 _mmap_key，从这里读 ═══
                            if (result && result._mmap_key) {
                                const mmapData = _readWorkerMmap(result._mmap_key);
                                if (mmapData !== null) {
                                    result = mmapData;
                                }
                                // 读不到就原样返回（含 _mmap_key，调用方能感知降级）
                            }
                            resolve(result);
                        }
                    } catch (e) { reject(new Error('HTTP parse: ' + e.message)); }
                });
            });
            req.on('error', (err) => { this._udsMode = null; reject(err); });
            req.on('timeout', () => { req.destroy(); reject(new Error('HTTP timeout: ' + method)); });
            req.write(body);
            req.end();
        });
    }

    async _doStart() {
        this._cleanup();
        // 多路径自发现：按优先级探测所有已知 UDS 路径
        await this._probeUdsOrSpawn();
    }

    async _probeUdsOrSpawn() {
        const probePaths = getUdsProbePaths();
        for (let i = 0; i < probePaths.length; i++) {
            const p = probePaths[i];
            try {
                this._udsPath = p;
                await this._connectUdsDirect(p);
                process.stderr.write(TAG + ' [UDS] 自发现成功: ' + p + '\n');
                return;
            } catch (e) {
                process.stderr.write(TAG + ' [UDS] 探测 ' + (i+1) + '/' + probePaths.length + ' 失败 (' + p + '): ' + e.message + '\n');
            }
        }
        // 所有已知路径都探测失败 → 重新 spawn Worker
        process.stderr.write(TAG + ' [UDS] 所有已知路径探测失败，触发 Worker 自愈 spawn\n');
        return new Promise((resolve, reject) => this._reconnectViaSpawn(resolve, reject));
    }

    async _tryUdsOrSpawn(attempt) {
        try {
            await this._connectUdsDirect();
            return;
        } catch (e) {
            process.stderr.write(TAG + ' [UDS] connect attempt ' + (attempt+1) + ' failed: ' + e.message + '\n');
        }
        if (attempt < 1) {
            await new Promise((r) => setTimeout(r, 2000).unref());
            return this._tryUdsOrSpawn(attempt + 1);
        }
        process.stderr.write(TAG + ' [UDS] ' + (attempt+1) + ' attempts failed, spawning Worker...\n');
        return new Promise((resolve, reject) => this._reconnectViaSpawn(resolve, reject));
    }

    async _connectUdsDirect(sockPath) {
        const targetPath = sockPath || this._udsPath;
        return new Promise((resolve, reject) => {
            const sock = net.createConnection(targetPath, () => {
                sock.write('POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n');
                let resp = '';
                sock.on('data', (d) => {
                    resp += d.toString();
                    if (resp.includes('HTTP/1.1') || resp.includes('HTTP/1.0')) {
                        this._udsMode = 'http';
                        sock.destroy();
                        this._ready = true;
                        this._fails = 0;
                        resolve();
                    }
                });
                setTimeout(() => {
                    this._udsMode = null;
                    sock.destroy();
                    reject(new Error('UDS probe: no HTTP response, fallback'));
                }, 3000).unref();
            });
            sock.on('error', (err) => reject(err));
            setTimeout(() => reject(new Error('UDS connect timeout')), 3000).unref();
        });
    }

    _reconnectViaSpawn(resolve, reject) {
        try {
            this._cleanup();
            // 传递通信路径环境变量，确保 Worker 与 Plugin 共享同一套 var/ 目录
            const galaxosVarDir = path.dirname(getUdsPath());
            this.proc = spawn(_pythonBin, [WORKER_SCRIPT], {
                cwd: this.workspace,
                env: { ...process.env, PYTHONIOENCODING: 'utf-8', OPENCLAW_WORKSPACE: this.workspace, WORKER_UDS: '1', WORKER_ID: this.id, WORKER_TIER: (this.id || '').split(':')[0], GALAXYOS_VAR_DIR: galaxosVarDir, GALAXYOS_REPO: path.join(os.homedir(), '.openclaw', 'galaxyos') },
                stdio: ['pipe', 'pipe', 'pipe'],
            });
            let settled = false;
            const settle = (fn, arg) => { if (!settled) { settled = true; fn(arg); } };
            const timeout = setTimeout(() => settle(reject, new Error('Worker start timeout (10s)')), 10000);
            this.proc.on('exit', (code, signal) => { this._ready = false; clearTimeout(timeout); settle(reject, new Error('Worker exited (code=' + code + ', signal=' + signal + ')')); });
            this.proc.on('error', (err) => { clearTimeout(timeout); settle(reject, new Error('Worker spawn error: ' + err.message)); });
            this.proc.stderr.on('data', (data) => { const text = data.toString().trim(); if (text) process.stderr.write(TAG + ' [worker stderr] ' + text + '\n'); });
            this._rl = createInterface({ input: this.proc.stdout, crlfDelay: Infinity });
            this._rl.on('line', (line) => {
                if (!line.trim()) return;
                try {
                    const msg = JSON.parse(line.trim());
                    if (msg.event === 'ready') {
                        clearTimeout(timeout); this._ready = true; this._fails = 0;
                        process.stderr.write(TAG + ' [spawn] Worker ready (pid=' + msg.pid + '), using stdin RPC\n');
                        settle(resolve, undefined);
                    }
                    if (msg.id !== undefined && msg.id !== null) {
                        const resolver = this.pending.get(msg.id);
                        if (resolver) { this.pending.delete(msg.id); resolver(msg); }
                    }
                } catch (e) {}
            });
        } catch (e) {
            reject(new Error('Worker spawn failed: ' + e.message));
        }
    }

    async call(method, params, timeoutMs) {
        if (params === undefined) params = {};
        if (timeoutMs === undefined) timeoutMs = 30000;
        if (!this._ready) await this.start();
        if (this._udsMode === 'http') {
            return this._httpCall(method, params, timeoutMs);
        }
        const id = this._nextId++;
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('Worker call timeout: ' + method)); }, timeoutMs);
            this.pending.set(id, (msg) => {
                clearTimeout(timer);
                if (msg.error) reject(new Error(typeof msg.error === 'string' ? msg.error : JSON.stringify(msg.error)));
                else resolve(msg.result);
            });
            try {
                const line = JSON.stringify({ id, method, params }) + '\n';
                if (this.proc && this.proc.stdin && this.proc.stdin.writable) {
                    this.proc.stdin.write(line);
                } else {
                    this.pending.delete(id); clearTimeout(timer);
                    reject(new Error('Worker not connected'));
                }
            } catch (e) {
                this.pending.delete(id); clearTimeout(timer);
                reject(new Error('Worker send failed: ' + e.message));
            }
        });
    }

    async ping() {
        try { return await this.call('ping', {}, 5000); }
        catch (e) { return { ok: false, error: e.message }; }
    }

    stop() {
        this._shutdown = true;
        try { this.call('shutdown', {}, 1000).catch(() => {}); } catch (e) {}
        setTimeout(() => { this._cleanup(); }, 1500).unref();
    }

    _cleanup() {
        if (this.client) { try { this.client.destroy(); } catch (e) {} this.client = null; }
        if (this._rl) { try { this._rl.close(); } catch (e) {} this._rl = null; }
        if (this.proc) {
            try { this.proc.stdin?.end(); this.proc.kill('SIGTERM'); } catch (e) {}
            setTimeout(() => { try { this.proc?.kill('SIGKILL'); } catch (e) {} }, 2000).unref();
            this.proc = null;
        }
        if (this._reconnectTimer) { clearTimeout(this._reconnectTimer); this._reconnectTimer = null; }
        // 清理该 Worker 的 UDS 连接池
        const agent = _workerAgents.get(this.id);
        if (agent) { try { agent.destroy(); } catch (e) {} _workerAgents.delete(this.id); }
        this._ready = false;
        for (const [id, resolver] of this.pending) { resolver({ id, error: 'Worker stopped' }); }
        this.pending.clear();
    }

    get ready() { return this._ready; }

}

function runClawScript(workspace, action, args, timeoutMs = 20000) {
    // v2026.6.12: unified_entry.py 已迁移到 extensions/galaxyos/scripts/
    const script = existsSync(path.join(__dir, "scripts", "unified_entry.py"))
        ? path.join(__dir, "scripts", "unified_entry.py")
        : path.join(workspace, "extensions", "galaxyos", "dist", "scripts", "unified_entry.py");
    const argParts = [action];
    for (const [key, value] of Object.entries(args)) {
        if (value) {
            argParts.push(`--${key}`, String(value));
        }
    }
    argParts.push("--json");
    try {
        const result = spawnSync(_pythonBin, [script, ...argParts], {
            timeout: timeoutMs,
            maxBuffer: 10 * 1024 * 1024,
            windowsHide: true,
            cwd: workspace,
            env: { ...process.env, PYTHONIOENCODING: "utf-8" },
        });
        const stdout = result.stdout?.toString("utf-8")?.trim();
        const stderr = result.stderr?.toString("utf-8")?.trim();
        if (result.status !== 0) {
            return {
                error: true,
                message: `exit code ${result.status}`,
                stderr: stderr?.slice(0, 500),
                stdout: stdout,
            };
        }
        return JSON.parse(stdout);
    }
    catch (err) {
        return {
            error: true,
            message: err.message || String(err),
            stderr: err.stderr?.toString("utf-8")?.slice(0, 500),
            stdout: err.stdout?.toString("utf-8")?.trim(),
        };
    }
}

// ==========================================
// Worker 单例 + 智能调用(优先 GalaxyPool,降级单 Worker,再降级 spawnSync)
// ==========================================
let _worker = null;

function getWorker(ws) {
    // GalaxyPool 优先（统一管理 WorkerPool + 所有组件）
    if (_galaxyPool && _galaxyPool.isReady) {
        const wp = _galaxyPool._workerPool;
        const firstIdle = wp ? wp._getIdleWorker() : null;
        const ready = firstIdle !== null || (wp && wp.workers.size > 0);
        return {
            ready,
            id: 'galaxy-pool',
            call: (method, params, timeoutMs) => {
                const priority = method === 'rccam' || method === 'health' ? 'high' :
                                 method === 'recall' || method === 'store' || method === 'verify' ? 'normal' :
                                 'background';
                return _galaxyPool.execute(method, params, priority, timeoutMs);
            },
        };
    }
    // 旧 WorkerPool（GalaxyPool 未就绪时的降级）
    if (_workerPool && _workerPool._ready) {
        const pool = _workerPool;
        const firstIdle = pool._getIdleWorker();
        const ready = firstIdle !== null || pool.workers.size > 0;
        return {
            ready,
            id: 'pool',
            call: (method, params, timeoutMs) => {
                const priority = method === 'rccam' || method === 'health' ? 'high' :
                                 method === 'recall' || method === 'store' || method === 'verify' ? 'normal' :
                                 'background';
                return pool.execute(method, params, priority, timeoutMs);
            },
        };
    }
    // 兜底：单 Worker（兼容旧代码）
    if (!_worker) _worker = new ClawWorkerClient(ws);
    return _worker;
}

function recallFallback(ws, text, topK = 3, sessionId = "") {
    const result = runClawScript(ws, "workflow", {
        scenario: "smart_recall",
        input: JSON.stringify({ query: text, top_k: topK, session_id: sessionId }),
    }, 15000);
    if (result.error) return [];
    if (Array.isArray(result)) return result;
    if (typeof result === "object") {
        const r = result;
        if (r.workflow && r.results) return Array.isArray(r.results) ? r.results : [];
        const items = (r.basic_results || []).concat(r.enhanced_results || []);
        if (items.length) return items;
    }
    return [];
}

/** Format recall results into a readable string */
function formatResults(data) {
    if (!data)
        return "未找到结果";
    if (typeof data === "object" && !Array.isArray(data)) {
        const d = data;
        if (d.workflow && d.results) {
            if (d.errors?.length > 0) {
                return `工作流 ${d.workflow} 执行有 ${d.errors.length} 个错误`;
            }
            if (Array.isArray(d.results)) {
                return formatResults(d.results);
            }
            if (typeof d.results === "object") {
                const r = d.results;
                const parts = [];
                if (r.healthy !== undefined) {
                    parts.push(`健康状态: ${r.healthy ? "✅ 正常" : "⚠️ 异常"}`);
                }
                if (r.layer_status) {
                    for (const [layer, status] of Object.entries(r.layer_status)) {
                        parts.push(`  ${status} ${layer}`);
                    }
                }
                if (r.stats) {
                    const memStats = r.stats.hallucination_guard || {};
                    parts.push(`  记忆: ${memStats.total_memories || 0} 条`);
                    const nStats = r.stats.synapse_network || {};
                    parts.push(`  神经节点: ${nStats.total_neurons || 0} 个`);
                }
                if (r.issues?.length > 0) {
                    parts.push(`  问题: ${r.issues.join(", ")}`);
                }
                return parts.join("\n");
            }
            return formatResults(d.results);
        }
        if (d.healthy !== undefined) {
            const lines = [];
            lines.push(`系统健康状态: ${d.healthy ? "✅ 正常" : "⚠️ 异常"}`);
            if (d.components && typeof d.components === "object") {
                for (const [name, info] of Object.entries(d.components)) {
                    if (info && typeof info === "object") {
                        lines.push(`  ${info.healthy ? "✅" : "❌"} ${name}`);
                        if (info.issues?.length) {
                            lines.push(`    问题: ${info.issues.join(", ")}`);
                        }
                    } else {
                        lines.push(`  ${info ? "✅" : "❌"} ${name}`);
                    }
                }
            }
            if (d.issues?.length) {
                lines.push(`  问题: ${d.issues.join(", ")}`);
            }
            return lines.join("\n");
        }
        const basic = d.basic_results || [];
        const enhanced = d.enhanced_results || [];
        const corrections = d.corrections || [];
        if (basic.length === 0 && enhanced.length === 0) {
            return "未找到相关记忆";
        }
        const parts = [];
        if (enhanced.length > 0) {
            parts.push("[增强检索结果]");
            enhanced.forEach((item, i) => {
                parts.push(`${i + 1}. ${item.content || ""}`);
            });
        }
        if (basic.length > 0) {
            parts.push("[基础检索结果]");
            basic.forEach((item, i) => {
                parts.push(`${i + 1}. ${item.content || ""} (${(item.confidence || 0).toFixed(2)})`);
            });
        }
        if (corrections.length > 0) {
            parts.push(`[纠正] ${corrections.length} 条`);
            corrections.forEach((c) => {
                parts.push(`  - ${c.correction || c.message || JSON.stringify(c)}`);
            });
        }
        return parts.join("\n");
    }
    if (Array.isArray(data)) {
        if (data.length === 0)
            return "未找到相关记忆";
        return data.map((item, i) => `[${i + 1}] ${item.content || ""} (置信度: ${(item.confidence || 0).toFixed(2)}, 来源: ${item.source || "unknown"})`).join("\n");
    }
    return JSON.stringify(data, null, 2);
}

/** Format call module results (claw_verify etc.) */
function formatClawVerifyResult(data) {
    if (!data || typeof data !== "object")
        return "未找到结果";
    const d = data;
    if (d.success && d.result) {
        const r = d.result;
        if (typeof r === "object") {
            const parts = [];
            if (r.statement)
                parts.push(`陈述: ${r.statement}`);
            if (r.final_confidence !== undefined)
                parts.push(`置信度: ${(r.final_confidence * 100).toFixed(0)}%`);
            if (r.is_reliable !== undefined)
                parts.push(`可靠: ${r.is_reliable ? "✅" : "❌"}`);
            if (r.recommendation)
                parts.push(`建议: ${r.recommendation}`);
            if (r.cross_validation?.consensus)
                parts.push(`共识: ${r.cross_validation.consensus}`);
            return parts.join("\n");
        }
        return JSON.stringify(d.result, null, 2);
    }
    if (d.error)
        return `验证失败:${d.error}`;
    return JSON.stringify(d, null, 2);
}


// ═══════════════════════════════════════════════════════════════════
// Desktop public API (v10 — stripped OpenClaw register())
// ═══════════════════════════════════════════════════════════════════

/**
 * Boot the GalaxyOS worker pool.
 * @param {string} workspaceDir — path to workspace root
 * @returns {Promise<void>}
 */
export async function start(workspaceDir) {
  if (_galaxyPool && _galaxyPool.isReady) {
    _dbg('GalaxyPool already running, skipping start');
    return;
  }

  _galaxyPool = new GalaxyPool({}, workspaceDir, {
    workers: { minSize: 2, maxSize: 8, size: 2, maxQueue: 20 },
  });

  // Register only the workers component (no gateway / zmq / mmap)
  _galaxyPool._reg('workers', {
    order: _componentDefaults.workers.order,
    critical: true,
    dependsOn: [],
    start: () => {
      const tiers = ['hot', 'warm', 'cold'];
      for (const tier of tiers) {
        const tierCfg = { ...TIER_CONFIG[tier], workspace: workspaceDir };
        const pool = new WorkerPool(workspaceDir, tierCfg);
        pool._init();
        _galaxyPool._tierPools.set(tier, pool);
        _dbg(`tier ${tier} pool ready (${tierCfg.size} workers)`);
      }
      _galaxyPool._workerPool = _galaxyPool._tierPools.get('warm');
    },
    stop: async () => {
      for (const [, pool] of _galaxyPool._tierPools) {
        try { await pool.shutdown(); } catch (e) { _dbg('pool shutdown error:', e.message); }
      }
      _galaxyPool._tierPools.clear();
      _galaxyPool._workerPool = null;
    },
    health: () => {
      const status = {};
      for (const [tier, pool] of _galaxyPool._tierPools) {
        status[tier] = {
          total: pool.workers.size,
          busy: pool.busy.size,
          queue: pool.queue.length,
        };
      }
      return { ok: Object.values(status).every(s => s.total > 0), tiers: status };
    },
  });

  await _galaxyPool.start();
  _dbg(`GalaxyPool started (status=${_galaxyPool.status})`);
}

/**
 * Shut down the pool.
 * @returns {Promise<void>}
 */
export async function stop() {
  if (!_galaxyPool) return;
  await _galaxyPool.stop();
  _galaxyPool = null;
  _workerPool = null;
}

/**
 * Execute a method call routed through the appropriate tier.
 * @param {string} method
 * @param {object} [params]
 * @param {number} [timeoutMs]
 * @returns {Promise<any>}
 */
export async function execute(method, params = {}, timeoutMs) {
  if (!_galaxyPool || !_galaxyPool.isReady) {
    throw new Error('GalaxyPool not ready — call start() first');
  }
  const tier = METHOD_TIER[method] || 'warm';
  const pool = _galaxyPool._tierPools.get(tier);
  if (!pool) throw new Error(`No pool for tier: ${tier}`);

  const actualTimeout = timeoutMs || (TIER_CONFIG[tier] ? TIER_CONFIG[tier].defaultTimeout : 30000);
  return pool.execute(method, params, 'normal', actualTimeout);
}

/**
 * @returns {{ ready: boolean, tiers: object }}
 */
export function getStatus() {
  if (!_galaxyPool) return { ready: false, tiers: {} };
  const tiers = {};
  for (const [tier, pool] of _galaxyPool._tierPools) {
    tiers[tier] = {
      ready: pool._ready,
      total: pool.workers.size,
      busy: pool.busy.size,
      queue: pool.queue.length,
      workers: [...pool.workers.entries()].map(([id, w]) => ({
        id, ready: w._ready, fails: w._fails || 0, mode: w._mode,
      })),
    };
  }
  return { ready: _galaxyPool.isReady, tiers };
}
