/**
 * galaxyos plugin v3 — GalaxyOS 核心引擎
 *
 * v2 upgrade: runs the full workflow engine (enhanced_recall, safe_generation, etc.)
 * instead of bare recall(). This activates all 44 workflows + 119 modules.
 *
 * Architecture:
 *   Agent (tool) → Plugin → Worker (persistent Python) → unified_entry.py workflow
 *                                          ↓ fallback → spawnSync
 *                                                               → CRAG pipeline
 *                                                               → Hybrid search
 *                                                               → Hallucination guard
 *                                                               → 44 workflows
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync, execSync } from "node:child_process";
import { createInterface } from "node:readline";
import fs, { existsSync, mkdirSync, chmodSync, unlinkSync, readFileSync, openSync, writeSync, closeSync, readSync, writeFileSync, renameSync, copyFileSync, readdirSync } from "node:fs";
import net from "node:net";
import http from "node:http";
import { createRequire as _createRequire } from "node:module";
// ESM 兼容垫片: index.js 是 "type":"module" 的 ESM, 但 zeromq 是 CJS,
// 用 createRequire 显式构造 CJS loader
const _cjsRequire = _createRequire(import.meta.url);

const TAG = "[galaxyos]";
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const WORKER_SCRIPT = path.join(__dirname, "scripts", "claw_worker.py");
const PIL_WORKER_SCRIPT = path.join(__dirname, "scripts", "pil_worker.py");

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
//   5) 自动检测 __dirname 上溯找到的 OPENCLAW_HOME
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
    const scriptsDir = path.join(__dirname, "scripts");
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
    path.join(__dirname, "scripts", "galaxyos-native"),
    path.join(__dirname, "native", "target", "release", "galaxyos-native"),
    path.join(__dirname, "..", "..", "native", "target", "release", "galaxyos-native"),
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
    const nativeDir = path.join(__dirname, "native");
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
            const dst = path.join(__dirname, "scripts", "galaxyos-native");
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

function resolveWorkspace(api) {
    const ws = api.runtime.workspace?.cwd?.();
    if (ws && existsSync(ws)) return ws;  // 必须存在
    // 兼容 sandbox: 优先 OPENCLAW_WORKSPACE 显式,再尝试 OPENCLAW_HOME/下的多个候选
    const ocHome = OPENCLAW_HOME;
    const candidates = [
        process.env.OPENCLAW_WORKSPACE,
        `${ocHome}/workspace-dev`,
        `${ocHome}/workspace`,
    ].filter(Boolean);
    for (const c of candidates) {
        if (existsSync(c)) return c;
    }
    // 最后一个: 创建
    const fallback = `${ocHome}/workspace-dev`;
    try { mkdirSync(fallback, { recursive: true }); } catch {}
    return fallback;
}

// ======== Rust native binary spawn helper (stdin/stdout JSON-RPC) ========
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

class WorkerPool {
    constructor(ws, cfg = {}) {
        this.ws = ws;
        this.minSize = cfg.minSize || 2;
        this.maxSize = cfg.maxSize || 8;
        this.size = Math.max(this.minSize, cfg.size || 2);
        this.maxQueue = cfg.maxQueue || 20;
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
            this._spawnOne(`worker:${i + 1}`);
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
                const newId = `worker:${total + i + 1}`;
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

    getFallback() {
        const firstWorker = this.workers.values().next().value;
        return firstWorker || null;
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
        this.ws = ws;
        this.status = 'init';  // init → starting → running → degraded → stopping → stopped
        this.cfg = cfg;
        // 内部组件注册表：name → { status, start, stop, health, order, critical, restartCount, restartMax }
        this._comps = new Map();
        // 内嵌 WorkerPool
        this._poolCfg = cfg.workers || { minSize: 2, maxSize: 8, size: 2, maxQueue: 20 };
        this._workerPool = null;
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
                this.api.logger?.info?.(`${TAG} [galaxy-pool] ${name} started`);
            } catch (e) {
                comp.status = 'failed';
                comp.restartCount++;
                this.api.logger?.warn?.(`${TAG} [galaxy-pool] ${name} start failed: ${e.message}`);
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
            this.api.logger?.warn?.(`${TAG} cron state flush error (non-fatal): ${e.message}`);
        }

        const ordered = [...this._comps.entries()]
            .sort(([, a], [, b]) => b.order - a.order)
            .map(([name]) => name);

        // Worker pool first (internal)
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
        this.api.logger?.info?.(`${TAG} [galaxy-pool] all components stopped`);
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
            // 也检查 WorkerPool 内部
            if (this._workerPool) {
                this._workerPool._healthCheck();
            }
            // 非关键检查：cron state 文件存在性
            const cronStatePath = path.join(OPENCLAW_HOME, "cron", "jobs-state.json");
            const cronJobsPath = path.join(OPENCLAW_HOME, "cron", "jobs.json");
            if (!existsSync(cronStatePath) && !existsSync(cronJobsPath)) {
                this.api.logger?.warn?.(`${TAG} cron state files missing (non-critical), cron flush may be unavailable`);
            }
        }, 10000).unref();
    }

    _handleUnhealthy(name, comp, reason) {
        comp.restartCount++;
        this.api.logger?.warn?.(`${TAG} [galaxy-pool] ${name} unhealthy (${comp.restartCount}/${comp.restartMax}): ${reason}`);
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
        const cronFlusher = path.join(__dirname, "scripts", "cron_state_flusher.py");
        if (!existsSync(cronFlusher)) {
            this.api.logger?.warn?.(`${TAG} cron_state_flusher.py not found, skipping flush`);
            return;
        }
        const CRON_DIR = path.join(OPENCLAW_HOME, "cron");
        if (!existsSync(CRON_DIR)) {
            this.api.logger?.info?.(`${TAG} cron dir not found, skipping flush`);
            return;
        }
        return new Promise((resolve) => {
            const timeout = setTimeout(() => {
                this.api.logger?.warn?.(`${TAG} cron state flush timed out after 8s`);
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
                    this.api.logger?.warn?.(`${TAG} cron state flush exited code=${code}: ${(stderr || stdout).slice(0, 200)}`);
                } else {
                    this.api.logger?.info?.(`${TAG} cron state flushed successfully`);
                }
                resolve();
            });
            proc.on('error', (err) => {
                clearTimeout(timeout);
                this.api.logger?.warn?.(`${TAG} cron state flush spawn error: ${err.message}`);
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
        if (this._workerPool) {
            comps.worker_pool = {
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

    // ═══ Worker 请求调度（透传到内嵌 WorkerPool） ═══
    execute(method, params, priority = 'normal', timeoutMs = 28000) {
        if (!this._workerPool && this._ready && this._comps.get('workers')?.status === 'running') {
            // 懒创建 WorkerPool
            this._workerPool = new WorkerPool(this.ws, this._poolCfg);
        }
        if (!this._workerPool) {
            return Promise.reject(new Error('GalaxyPool: worker pool not available'));
        }
        return this._workerPool.execute(method, params, priority, timeoutMs);
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
        zmq = _cjsRequire(path.join(__dirname, "node_modules", "zeromq"));
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
                env: { ...process.env, PYTHONIOENCODING: 'utf-8', OPENCLAW_WORKSPACE: this.workspace, WORKER_UDS: '1', WORKER_ID: this.id, GALAXYOS_VAR_DIR: galaxosVarDir },
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
    const script = existsSync(path.join(__dirname, "scripts", "unified_entry.py"))
        ? path.join(__dirname, "scripts", "unified_entry.py")
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

export default function register(api) {
    const ws = resolveWorkspace(api);
    api.logger.info?.(`${TAG} v2 plugin initialized, workspace=${ws}`);

    // 原生扩展状态报告
    if (_pyo3Native) {
        api.logger.info?.(`${TAG} galaxyos_native: ${_pyo3Shim ? 'pure-Python shim (PIL/numpy)' : 'Rust/PyO3 compiled — zero-copy, no GIL'}`);
    } else if (_nativeBinary) {
        api.logger.info?.(`${TAG} Rust native: subprocess binary (${_nativeBinary})`);
    } else {
        api.logger.warn?.(`${TAG} Rust native extension NOT found — image/vector ops fall back to Python PIL (slower, GIL-bound). Run \`make native-py\` or \`make native\`.`);
    }

    // ==========================================
    // ═══════════════════════════════════════════════════════════════
    // GalaxyPool — 统一兜住整个 GalaxyOS 系统
    // 6 类组件：mmap_control → gateway_uds → zmq_router → native_binary → gateway_heartbeat → workers
    // 单入口启动/停止/健康检查，电路断路器防级联崩溃
    // ═══════════════════════════════════════════════════════════════
    const pluginConfig = api.getConfig?.() || {};
    const workerEnabled = pluginConfig.worker?.enabled !== false;

    if (!_galaxyPool) {
        _galaxyPool = new GalaxyPool(api, ws, {
            workers: { minSize: 2, maxSize: 8, size: 2, maxQueue: 20 },
        });

        // —— 组件注册（按依赖拓扑排序） ——

        // 0. mmap 控制（最底层，其他组件依赖）
        _galaxyPool._reg('mmap_control', {
            start: () => { initMmapControl(); _mmapSyncInit(); },
            stop:  () => {},
            health: () => { try { return existsSync(MMAP_PATH) ? { ok: true } : { ok: false, error: 'mmap file missing' }; } catch (e) { return { ok: false, error: e.message }; } },
        });

        // 1. Gateway UDS server（Worker ↔ Gateway 反向 RPC）
        _galaxyPool._reg('gateway_uds', {
            start: () => { const srv = startGatewayUdsServer(api, ws); return { _gatewayServer: srv }; },
            stop:  () => stopGatewayUdsServer(),
            health: () => { try { return _gatewayServer?.listening ? { ok: true } : { ok: false, error: 'gateway not listening' }; } catch (e) { return { ok: false, error: 'gateway dead' }; } },
        });

        // 2. ZMQ Router（Worker ↔ Gateway 异步双向）
        _galaxyPool._reg('zmq_router', {
            start: () => { const r = startZmqRouter(api); return { _zmqRouter: r }; },
            stop:  () => stopZmqRouter(),
            health: () => { try { return _zmqRouter ? { ok: true } : { ok: false, error: 'zmq router null' }; } catch (e) { return { ok: false, error: 'zmq dead' }; } },
        });

        // 3. Rust native binary（向量/图像计算）
        _galaxyPool._reg('native_binary', {
            start: () => { const ok = _ensureNativeProc(); return { _nativeProc: ok ? _nativeProc : null }; },
            stop:  () => _stopNativeProc(),
            health: () => {
                try {
                    if (_pyo3Native) return { ok: true };
                    if (!_nativeBinary) return { ok: false, error: 'binary not found' };
                    // galaxyos-native is a one-shot reporter (prints JSON and exits 0)
                    if (!_nativeProc || _nativeProc.killed) return { ok: true, note: 'one-shot completed' };
                    return _nativeProc.exitCode === null
                        ? { ok: true }
                        : { ok: true, note: 'one-shot completed' };
                } catch (e) { return { ok: false, error: e.message }; }
            },
        });

        // 4. Gateway heartbeat（独立 mmap 心跳文件）
        _galaxyPool._reg('gateway_heartbeat', {
            start: () => { _startGatewayHeartbeat(api); },
            stop:  () => { if (_gatewayHbTimer) { clearInterval(_gatewayHbTimer); _gatewayHbTimer = null; } },
            health: () => { try { return existsSync(GATEWAY_HB_PATH) ? { ok: true } : { ok: false, error: 'heartbeat file missing' }; } catch (e) { return { ok: false, error: e.message }; } },
        });

        // 5. Workers（Python worker 池，依赖 gateway_uds）
        if (workerEnabled) {
            _galaxyPool._reg('workers', {
                start: () => {
                    if (!_galaxyPool._workerPool) {
                        _galaxyPool._workerPool = new WorkerPool(ws, _galaxyPool._poolCfg);
                    }
                },
                stop: async () => {
                    if (_galaxyPool._workerPool) {
                        await _galaxyPool._workerPool.shutdown();
                        _galaxyPool._workerPool = null;
                    }
                },
                health: () => {
                    const wp = _galaxyPool._workerPool;
                    if (!wp || !wp._ready) return { ok: false, error: 'worker pool not ready' };
                    const alive = [...wp.workers.values()].filter(w => w.ready).length;
                    return alive > 0 ? { ok: true, alive, total: wp.workers.size } : { ok: false, error: 'no alive workers', alive: 0 };
                },
                dependsOn: ['gateway_uds'],
            });
        }
    }

    // 统一启动
    _galaxyPool.start().then(() => {
        api.logger?.info?.(`${TAG} GalaxyPool running: ${JSON.stringify(_galaxyPool.getStatus())}`);
    }).catch((e) => {
        api.logger?.warn?.(`${TAG} GalaxyPool start error: ${e.message}`);
    });
    api.logger.info?.(`${TAG} REST API available via Worker TCP: GET/POST http://127.0.0.1:8765/<method>`);

    // ═══ Gateway stop：统一关闭所有组件 ═══
    api.on("gateway_stop", async () => {
        if (_zmqSub) { try { _zmqSub.close(); } catch (e) {} _zmqSub = null; }
        _zmqSubActive = false;
        if (_galaxyPool) {
            await _galaxyPool.stop();
        }
        // 兜底：单 worker 清理
        if (_worker) {
            const w = _worker; _worker = null;
            if (w.proc) { w.stop(); } else { w._cleanup(); }
        }
    });
    // ═══ 进程信号处理：SIGTERM/SIGINT 先 flush cron 再退出 ═══
    const _isGatewayProcess = () => process.env.OPENCLAW_NO_RESPAWN || process.argv.includes('gateway') || process.argv.some(a => a.includes('gateway'));
    if (_isGatewayProcess()) {
        const _onExitSignal = async (signal) => {
            api.logger?.info?.(`${TAG} received ${signal}, flushing cron state before exit...`);
            if (_galaxyPool) {
                try {
                    await _galaxyPool._flushCronState();
                } catch (e) {
                    api.logger?.warn?.(`${TAG} cron state flush on signal error: ${e.message}`);
                }
            }
            process.exit(0);
        };
        process.on('SIGTERM', () => _onExitSignal('SIGTERM'));
        process.on('SIGINT', () => _onExitSignal('SIGINT'));
        api.logger?.info?.(`${TAG} SIGTERM/SIGINT handlers registered for Gateway process`);
    }

    // ═══ 暴露 GalaxyPool 运行时状态给 AI Agent ═══
    api.registerTool({
        name: "galaxy_pool",
        label: "GalaxyPool 系统状态",
        description: "查询 GalaxyOS 全系统运行状态：各组件的 alive/dead/degraded, Worker 池负载, 队列深度",
        parameters: { type: "object", properties: {}, required: [] },
        async execute() {
            const status = _galaxyPool ? _galaxyPool.getStatus() : { status: 'not_initialized' };
            const text = JSON.stringify(status, null, 2);
            return { content: [{ type: "text", text }], details: status };
        },
    });
    // ═══ R-CCAM 实时进度查询 ═══
    api.registerTool({
        name: "claw_rccam_progress",
        label: "R-CCAM 进度",
        description: "查询当前正在执行的 R-CCAM 认知循环的实时进度（Retrieval→Cognition→Control→Action→Memory）",
        parameters: {
            type: "object",
            properties: {
                session_key: { type: "string", description: "可选：指定 session 查询，不传则返回所有活跃进度" },
            },
            required: [],
        },
        async execute(_toolCallId, params) {
            const sk = params.session_key;
            if (sk) {
                const p = _rccamProgress.get(sk);
                if (p && Date.now() - p.ts < _rccamProgressMaxAgeMs) {
                    const text = `${p.status} ${p.phase} (cycle ${p.cycle}, ${p.elapsedMs}ms)`;
                    return { content: [{ type: "text", text }], details: p };
                }
                return { content: [{ type: "text", text: `session ${sk}: 无活跃 R-CCAM 进度` }] };
            }
            const active = [];
            for (const [k, v] of _rccamProgress) {
                if (Date.now() - v.ts < _rccamProgressMaxAgeMs) {
                    active.push({ session: k.slice(0, 40), ...v });
                }
            }
            if (active.length === 0) {
                return { content: [{ type: "text", text: "当前无活跃 R-CCAM 认知循环" }] };
            }
            const text = active.map(p => `${p.status} ${p.phase} cycle=${p.cycle} ${p.elapsedMs}ms [${p.session}]`).join('\n');
            return { content: [{ type: "text", text }], details: { active, count: active.length } };
        },
    });

    // ==========================================
    // Tool: claw_recall - Enhanced recall via workflow
    // ==========================================
    api.registerTool({
        name: "claw_recall",
        label: "Claw Memory Recall",
        description: "Enhanced memory retrieval using the full xiaoyi-claw-omega-final workflow engine.\n" +
            "Runs the enhanced_recall workflow (CRAG pipeline → hybrid search → hallucination guard).\n" +
            "Use this for deep semantic memory retrieval with automatic correction.",
        parameters: {
            type: "object",
            properties: {
                query: {
                    type: "string",
                    description: "Search query for retrieving memories",
                },
                top_k: {
                    type: "number",
                    description: "Maximum results to return (default: 5)",
                    default: 5,
                },
            },
            required: ["query"],
        },
        async execute(_toolCallId, params) {
            const query = String(params.query ?? "");
            const topK = Math.min(Math.max(Number(params.top_k) || 5, 1), 20);
            const startMs = Date.now();
            api.logger.debug?.(`${TAG} [tool] claw_recall: query="${query.slice(0, 80)}", top_k=${topK}`);
            try {
                const w = getWorker(ws);
                const result = await w.call("recall", { query, top_k: topK }, 30000);
                const elapsedMs = Date.now() - startMs;
                const text = formatResults(result);
                api.logger.debug?.(`${TAG} [tool] claw_recall completed via Worker (${elapsedMs}ms)`);
                return { content: [{ type: "text", text }], details: { elapsedMs, worker: true } };
            }
            catch (err) {
                api.logger.warn?.(`${TAG} [tool] claw_recall Worker failed, falling back to spawnSync: ${err.message}`);
                const result = runClawScript(ws, "workflow", {
                    scenario: "smart_recall",
                    input: JSON.stringify({ query, top_k: topK }),
                }, 30000);
                const elapsedMs = Date.now() - startMs;
                if (result.error) {
                    api.logger.warn?.(`${TAG} [tool] claw_recall (fallback) also failed: ${result.message}`);
                    const fbResult = runClawScript(ws, "workflow", {
                        scenario: "enhanced_recall",
                        input: JSON.stringify({ query, top_k: topK }),
                    }, 20000);
                    return { content: [{ type: "text", text: formatResults(fbResult) }], details: { count: 0, elapsedMs, fallback: true, fallback_error: result.message } };
                }
                return { content: [{ type: "text", text: formatResults(result) }], details: { elapsedMs } };
            }
        },
    });
    // ==========================================
    // Tool: claw_lobster - Run Lobster pipelines
    // ==========================================
    api.registerTool({
        name: "claw_lobster",
        label: "Claw Lobster Pipeline",
        description: "Run a Lobster pipeline or workflow file.\n" +
            "Lobster pipelines (e.g., session-recovery, heartbeat-full, memory-store) combine\n" +
            "multiple deterministic steps into a single call, reducing token consumption.",
        parameters: {
            type: "object",
            properties: {
                pipeline: {
                    type: "string",
                    description: "Pipeline name or file path.\n" +
                        "Built-in pipelines: session-recovery, heartbeat-full, memory-store\n" +
                        "Built-in workflows: claw-recall, claw-store, claw-health, claw-status, claw-verify, claw-workflow\n" +
                        "Or use 'inline:<command>' for ad-hoc pipelines (e.g., 'inline:cat config/*.json')",
                },
                args: {
                    type: "object",
                    description: "Optional JSON arguments passed to the pipeline",
                },
            },
            required: ["pipeline"],
        },
        async execute(_toolCallId, params) {
            const pipeline = String(params.pipeline ?? "");
            const args = params.args || {};
            const ws = resolveWorkspace(api);
            const lobsterCli = path.join(ws, "node_modules/@clawdbot/lobster/bin/lobster.js");
            const startMs = Date.now();
            try {
                // 构建 lobster 参数数组(避免字符串 split 破坏引号/空格)
                const lobArgs = ["run"];
                if (pipeline.startsWith("inline:")) {
                    lobArgs.push(pipeline.slice(7));
                }
                else if (pipeline.includes("/") || pipeline.endsWith(".lobster")) {
                    const filePath = path.isAbsolute(pipeline)
                        ? pipeline
                        : path.join(ws, "workflows", pipeline.endsWith(".lobster") ? pipeline : `${pipeline}.lobster`);
                    lobArgs.push("--file", filePath);
                }
                else if (["session-recovery", "heartbeat-full", "memory-store"].includes(pipeline)) {
                    const filePath = path.join(ws, "pipelines", `${pipeline}.lobster`);
                    lobArgs.push("--file", filePath);
                }
                else {
                    const filePath = path.join(ws, "workflows", `claw-${pipeline}.lobster`);
                    lobArgs.push("--file", filePath);
                }
                const argsKeys = Object.keys(args);
                if (argsKeys.length > 0) {
                    lobArgs.push("--args-json", JSON.stringify(args));
                }
                api.logger.debug?.(`${TAG} [tool] claw_lobster: lobster ${lobArgs.join(" ").slice(0, 120)}`);
                const result = spawnSync("node", [lobsterCli, ...lobArgs], {
                    timeout: 30000,
                    maxBuffer: 5 * 1024 * 1024,
                    windowsHide: true,
                    cwd: ws,
                    env: Object.assign(Object.create(Object.getPrototypeOf(process.env)), process.env, { NO_COLOR: "1" }),
                });
                const elapsedMs = Date.now() - startMs;
                const stdout = result.stdout?.toString("utf-8")?.trim() || "";
                const stderr = result.stderr?.toString("utf-8")?.trim() || "";
                if (result.status !== 0) {
                    const msg = stderr || stdout || `exit code ${result.status}`;
                    api.logger.warn?.(`${TAG} [tool] claw_lobster failed (${elapsedMs}ms): ${msg.slice(0, 200)}`);
                    return {
                        content: [{ type: "text", text: `❌ Lobster 管道执行失败:${msg.slice(0, 500)}` }],
                        isError: true,
                    };
                }
                api.logger.debug?.(`${TAG} [tool] claw_lobster completed (${elapsedMs}ms)`);
                return {
                    content: [{ type: "text", text: stdout || "✅ Lobster 管道执行完成" }],
                };
            }
            catch (err) {
                return {
                    content: [{ type: "text", text: `❌ Lobster 执行异常:${err.message}` }],
                    isError: true,
                };
            }
        },
    });
    // ==========================================
    // Tool: claw_health - System health via workflow
    // ==========================================
    api.registerTool({
        name: "claw_health",
        label: "Claw System Health",
        description: "Run system health check using the workflow engine.\n" +
            "Reports memory, coordinator, workflow engine, and hallucination guard status.",
        parameters: {
            type: "object",
            properties: {},
        },
        async execute() {
            const startMs = Date.now();
            api.logger.debug?.(`${TAG} [tool] claw_health called`);
            try {
                const w = getWorker(ws);
                const result = await w.call("health", {}, 20000);
                const elapsedMs = Date.now() - startMs;
                const text = formatResults(result);
                api.logger.debug?.(`${TAG} [tool] claw_health completed via Worker (${elapsedMs}ms)`);
                return { content: [{ type: "text", text }], details: { elapsedMs, worker: true } };
            }
            catch (err) {
                api.logger.warn?.(`${TAG} [tool] claw_health Worker failed, falling back to spawnSync: ${err.message}`);
                const wfResult = runClawScript(ws, "workflow", { scenario: "health_check" }, 20000);
                if (!wfResult.error) {
                    return { content: [{ type: "text", text: formatResults(wfResult) }] };
                }
                const result = runClawScript(ws, "health", {}, 15000);
                const elapsedMs = Date.now() - startMs;
                if (result.error) {
                    return { content: [{ type: "text", text: `健康检查失败:${result.message}` }], isError: true };
                }
                const components = result.components || {};
                let text = `系统健康状态: ${result.healthy ? "✅ 正常" : "⚠️ 异常"}\n`;
                for (const [name, info] of Object.entries(components)) {
                    if (info && typeof info === "object") {
                        text += `  ${info.healthy ? "✅" : "❌"} ${name}\n`;
                        if (info.issues?.length) {
                            text += `    问题: ${info.issues.join(", ")}\n`;
                        }
                    }
                }
                api.logger.debug?.(`${TAG} [tool] claw_health completed via fallback (${elapsedMs}ms)`);
                return { content: [{ type: "text", text }] };
            }
        },
    });
    // ==========================================
    // Tool: claw_vector_info - 跨平台 SIMD 向量计算能力查询
    // ==========================================
    api.registerTool({
        name: "claw_vector_info",
        label: "Vector Compute Capability",
        description: "查询当前平台向量计算的硬件加速能力。\n" +
            "返回 SIMD 架构(如 AVX-512/AVX2/NEON/SVE)、lane 并行数、FMA 支持等。\n" +
            "用于判断向量搜索是否启用硬件加速路径。",
        parameters: {
            type: "object",
            properties: {},
        },
        async execute() {
            try {
                const w = getWorker(ws);
                const result = await w.call("vector_info", {}, 5000);
                if (!result.available) {
                    return { content: [{ type: "text", text: "⚠️ VectorAPI 未初始化，向量计算使用纯 Python numpy (无 SIMD 加速)" }] };
                }
                const text = [
                    `🖥️ 向量计算后端: ${result.arch}`,
                    `   SIMD lane 数 (float32): ${result.lane_count}`,
                    `   寄存器位宽: ${result.register_width_bits} bit`,
                    `   FMA 融合乘加: ${result.supports_fma ? "✅ 支持" : "❌ 不支持"}`,
                    `   遮蔽运算: ${result.supports_masking ? "✅ 支持" : "❌ 不支持"}`,
                    `   ${result.description || ""}`,
                ].join("\n");
                return { content: [{ type: "text", text }], details: result };
            } catch (err) {
                return { content: [{ type: "text", text: `vector_info 失败: ${err.message}` }], isError: true };
            }
        },
    });
    // ==========================================
    // Tool: claw_store - Store via workflow
    // ==========================================
    // Tool: claw_events - TKG 事件日志查询
    // ==========================================
    api.registerTool({
        name: "claw_events",
        label: "Claw Events Query",
        description: "查询事件日志（基于 TKG 时序知识图谱）。\n" +
            "返回按时间倒序排列的操作事件，支持关键词和时间范围过滤。\n" +
            "事件类型包括: remember, recall, forget, tag, health, process 等。\n" +
            "每个事件带精确时间戳 t_ingested (Unix 浮点秒)。",
        parameters: {
            type: "object",
            properties: {
                query: {
                    type: "string",
                    description: "关键词过滤（按内容或目标搜索）",
                },
                limit: {
                    type: "number",
                    description: "最大返回条数 (默认 20, 最多 100)",
                    default: 20,
                },
                since: {
                    type: "number",
                    description: "起始时间戳 (Unix 秒，可选)",
                },
                until: {
                    type: "number",
                    description: "截止时间戳 (Unix 秒，可选)",
                },
            },
        },
        async execute(_toolCallId, params) {
            const query = String(params.query ?? "");
            const limit = Math.min(Math.max(Number(params.limit) || 20, 1), 100);
            const since = Number(params.since) || 0;
            const until = Number(params.until) || 0;
            const startMs = Date.now();
            api.logger.debug?.(`${TAG} [tool] claw_events: query="${query.slice(0, 80)}", limit=${limit}`);
            try {
                const w = getWorker(ws);
                const result = await w.call("events", { query, limit, since, until }, 10000);
                const elapsedMs = Date.now() - startMs;
                const events = result.events || [];
                // 格式化为可读文本
                let text = `📋 事件日志 (${events.length} 条):\n`;
                for (const ev of events) {
                    const ts = new Date(ev.t_ingested * 1000).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" });
                    text += `  [${ts}] ${ev.src_name} → ${ev.dst_name} | ${(ev.content || "").slice(0, 80)}\n`;
                }
                api.logger.debug?.(`${TAG} [tool] claw_events completed via Worker (${elapsedMs}ms)`);
                return { content: [{ type: "text", text }], details: { count: events.length, elapsedMs, worker: true } };
            }
            catch (err) {
                api.logger.warn?.(`${TAG} [tool] claw_events Worker failed: ${err.message}`);
                return { content: [{ type: "text", text: `查询事件日志失败: ${err.message}` }], isError: true };
            }
        },
    });
    // ==========================================
    api.registerTool({
        name: "claw_store",
        label: "Claw Memory Store",
        description: "Store a memory with full pipeline processing:\n" +
            "hallucination guard → synapse network → emotion memory → persistence.",
        parameters: {
            type: "object",
            properties: {
                content: {
                    type: "string",
                    description: "Memory content to store",
                },
                source: {
                    type: "string",
                    enum: ["user", "ai", "observation"],
                    default: "user",
                },
            },
            required: ["content"],
        },
        async execute(_toolCallId, params) {
            const content = String(params.content ?? "");
            const source = String(params.source ?? "user");
            try {
                const w = getWorker(ws);
                const result = await w.call("store", { content, source }, 10000);
                return { content: [{ type: "text", text: "✅ 记忆已存储" }], details: { ...result, worker: true } };
            }
            catch (err) {
                const result = runClawScript(ws, "store", { content, source }, 10000);
                if (result.error) {
                    return { content: [{ type: "text", text: `存储失败:${result.message}` }], isError: true };
                }
                return { content: [{ type: "text", text: "✅ 记忆已存储" }], details: result };
            }
        },
    });
    // ==========================================
    // Tool: claw_verify - 防幻觉验证
    // ==========================================
    api.registerTool({
        name: "claw_verify",
        label: "Claw Hallucination Verify",
        description: "Verify a claim using the enhanced hallucination guard.\n" +
            "Cross-references memory, knowledge graph, and multi-source evidence.",
        parameters: {
            type: "object",
            properties: {
                claim: {
                    type: "string",
                    description: "The claim or statement to verify",
                },
            },
            required: ["claim"],
        },
        async execute(_toolCallId, params) {
            const claim = String(params.claim ?? "");
            try {
                const w = getWorker(ws);
                const result = await w.call("verify", { claim }, 15000);
                return { content: [{ type: "text", text: formatClawVerifyResult(result) }], details: { ...result, worker: true } };
            }
            catch (err) {
                const result = runClawScript(ws, "call", {
                    module: "enhanced_hallucination_guard",
                    action: "verify_with_cross_validation",
                    input: claim,
                }, 15000);
                if (result.error) {
                    return { content: [{ type: "text", text: `验证失败:${result.message}` }], isError: true };
                }
                return { content: [{ type: "text", text: formatClawVerifyResult(result) }], details: result };
            }
        },
    });
    // ==========================================
    // Tool: claw_rccam - R-CCAM 结构化认知循环
    // ==========================================
    api.registerTool({
        name: "claw_rccam",
        label: "R-CCAM 认知循环",
        description: "R-CCAM 结构化认知循环:对用户输入执行完整五阶段循环 Retrieval→Cognition→Control→Action→Memory",
        parameters: {
            type: "object",
            properties: {
                user_input: { type: "string", description: "用户输入文本" },
                max_cycles: { type: "number", description: "最大循环轮次(默认1,最多3)" },
                store_memory: { type: "boolean", description: "是否持久化记忆(默认true)" },
            },
            required: ["user_input"],
        },
        async execute(_toolCallId, params) {
            const userInput = String(params.user_input ?? "");
            const maxCycles = Math.min(Number(params.max_cycles) || 1, 3);
            const storeMem = params.store_memory !== false;
            // R-CCAM 会话级去重：必须带 session_key，否则不做 dedup（避免用户连续问
            // 相同问题时复用前次结果）。F-15 修复。
            const dedupKey = params.session_key
                ? `session:${params.session_key}`
                : null;
            const existing = _rccamFlying.get(dedupKey);
            if (existing && Date.now() - existing.ts < _rccamFlyingMaxAgeMs) {
                process.stderr.write(TAG + ' [rccam] dedup: reusing in-flight for key=' + dedupKey.slice(0, 60) + '\n');
                try {
                    return await existing.promise;
                } catch (e) {
                    // 上次失败了，不清除缓存，直接返回失败状态
                    return { content: [{ type: "text", text: `认知循环已在执行中(前次失败:${e.message})` }], isError: true };
                }
            }
            const startMs = Date.now();
            let rccamPromise;
            try {
                const w = getWorker(ws);
                let result;
                let fromWorker = false;
                try {
                    rccamPromise = w.call("rccam", {
                        user_input: userInput,
                        max_cycles: maxCycles,
                        store_memory: storeMem,
                    }, 120000);
                    if (dedupKey) _rccamFlying.set(dedupKey, { promise: rccamPromise, ts: Date.now() });
                    result = await rccamPromise;
                    fromWorker = true;
                } catch (_workerErr) {
                    // Worker 不可用,降级到 unified_entry
                    rccamPromise = Promise.reject(_workerErr);
                    const r = runClawScript(ws, "process", {
                        input: JSON.stringify({
                            user_input: userInput,
                            max_cycles: maxCycles,
                            store_memory: storeMem,
                        }),
                    }, 120000);
                    if (dedupKey) _rccamFlying.set(dedupKey, { promise: rccamPromise, ts: Date.now() });
                    const elapsedMs = Date.now() - startMs;
                    if (r.error) {
                        return { content: [{ type: "text", text: `认知循环执行失败:${r.message}` }], isError: true };
                    }
                    const text = typeof r === "object" ? JSON.stringify(r, null, 2).slice(0, 4000) : String(r);
                    return { content: [{ type: "text", text }], details: { elapsedMs } };
                } finally {
                    _rccamFlying.delete(dedupKey);
                }
                const elapsedMs = Date.now() - startMs;
                const text = typeof result === "object" ? JSON.stringify(result, null, 2).slice(0, 4000) : String(result);
                return { content: [{ type: "text", text }], details: { elapsedMs, worker: fromWorker } };
            }
            catch (err) {
                return { content: [{ type: "text", text: `认知循环异常:${err.message}` }], isError: true };
            }
        },
    });

    // ==========================================
    // Tool: claw_save_memory - 回复后记忆持久化
    // ==========================================
    api.registerTool({
        name: "claw_save_memory",
        label: "记忆持久化",
        description: "在 AI 回复用户后,将真实回答存储到记忆系统。由 R-CCAM 调用者在使用 claw_rccam 分析后,用真实 answer 调用此工具完成 Memory 阶段",
        parameters: {
            type: "object",
            properties: {
                session_key: { type: "string", description: "claw_rccam 返回的 session_key" },
                user_input: { type: "string", description: "用户的原始问题" },
                answer: { type: "string", description: "AI 的真实回答内容" },
                metadata: { type: "object", description: "可选的元数据(strategy/knowledge_type 等)" },
            },
            required: ["session_key", "user_input", "answer"],
        },
        async execute(_toolCallId, params) {
            const sessionKey = String(params.session_key ?? "");
            const userInput = String(params.user_input ?? "");
            const answer = String(params.answer ?? "");
            const metadata = params.metadata || {};
            const startMs = Date.now();
            try {
                const w = getWorker(ws);
                if (w.ready) {
                    const result = await w.call("save_memory", {
                        session_key: sessionKey,
                        user_input: userInput,
                        answer: answer,
                        metadata: metadata,
                    }, 10000);
                    const elapsedMs = Date.now() - startMs;
                    return { content: [{ type: "text", text: JSON.stringify(result, null, 2) }], details: { elapsedMs, worker: true } };
                }
                return { content: [{ type: "text", text: JSON.stringify({ error: true, message: "Worker not available" }) }], isError: true };
            } catch (err) {
                return { content: [{ type: "text", text: `save_memory 异常:${err.message}` }], isError: true };
            }
        },
    });

    // ==========================================
    // Tool: claw_compile_skill - SkVM 编译技能
    // ==========================================
    api.registerTool({
        name: "claw_compile_skill",
        label: "Skill Compiler (SkVM)",
        description: "编译一个 Skill：CapabilityProfile 匹配 → 环境绑定 → 裁剪 → 优化。\n" +
            "调用 Worker compile_skill UDS，返回 CompiledArtifact。",
        parameters: {
            type: "object",
            properties: {
                skill_text: { type: "string", description: "Skill 原始 Markdown 内容" },
                skill_name: { type: "string", description: "Skill 名称（可选）" },
            },
            required: ["skill_text"],
        },
        async execute(_toolCallId, params) {
            const startMs = Date.now();
            const w = getWorker(ws);
            if (!w || !w.ready) {
                return { content: [{ type: "text", text: "Worker 未就绪" }], isError: true };
            }
            try {
                const result = await w.call("compile_skill", {
                    skill_text: String(params.skill_text),
                    skill_name: String(params.skill_name || ""),
                }, 30000);
                const artifact = result.artifact || {};
                const text = `✅ 编译完成\n` +
                    `  名称: ${artifact.skill_name || params.skill_name || "(unnamed)"}\n` +
                    `  文本: ${(artifact.optimized_text || "").length}B (原始 ${(params.skill_text || "").length}B)\n` +
                    `  环境检查: ${artifact.env_check || "ok"}\n` +
                    `  裁剪比例: ${artifact.prune_ratio || 0}\n` +
                    `  Asset ID: ${result.asset_id || "N/A"}`;
                return { content: [{ type: "text", text }], details: { elapsedMs: Date.now() - startMs } };
            }
            catch (err) {
                return { content: [{ type: "text", text: `编译失败: ${err.message}` }], isError: true };
            }
        },
    });

    // ==========================================
    // Tool: claw_asset_search - AssetRegistry 查询
    // ==========================================
    api.registerTool({
        name: "claw_asset_search",
        label: "Asset Registry Search",
        description: "查询 KnowledgeAsset 注册表。支持按 query/capability/tag/category 搜索。\n" +
            "MemGAS-SkVM 融合系统的核心查询接口。",
        parameters: {
            type: "object",
            properties: {
                query: { type: "string", description: "文本查询" },
                capability_key: { type: "string", description: "按 capability 过滤 (如 web_access, reasoning)" },
                tag: { type: "string", description: "按标签过滤" },
                category: { type: "string", description: "按分类过滤 (neuron, memory, skill)" },
                top_k: { type: "number", default: 10, description: "最大返回条数" },
            },
        },
        async execute(_toolCallId, params) {
            const startMs = Date.now();
            const w = getWorker(ws);
            if (!w || !w.ready) {
                return { content: [{ type: "text", text: "Worker 未就绪" }], isError: true };
            }
            try {
                const result = await w.call("asset_search", {
                    query: params.query || "",
                    capability_key: params.capability_key || "",
                    tag: params.tag || "",
                    category: params.category || "",
                    top_k: Math.min(Number(params.top_k) || 10, 50),
                }, 15000);
                const assets = result.assets || [];
                let text = `📋 AssetRegistry (${assets.length} 条):\n`;
                for (const a of assets) {
                    const mg = a.multi_granularity ? Object.keys(a.multi_granularity).join(",") : "-";
                    text += `  [${a.asset_id?.slice(0, 20) || ""}...] ${a.asset_type || "?"} | mg=${mg} | #${(a.tags || []).slice(0, 3).join(",")}\n`;
                }
                if (!assets.length) text = "没有匹配的资产。";
                return { content: [{ type: "text", text }], details: { elapsedMs: Date.now() - startMs } };
            }
            catch (err) {
                return { content: [{ type: "text", text: `查询失败: ${err.message}` }], isError: true };
            }
        },
    });

    // ==========================================
    // Tool: claw_asset_register - 注册新 Asset
    // ==========================================
    api.registerTool({
        name: "claw_asset_register",
        label: "Asset Registry Register",
        description: "注册一个自定义 KnowledgeAsset 到 AssetRegistry。\n" +
            "支持带 capability_profile、compiled_artifact 和 tags 的高级注册。",
        parameters: {
            type: "object",
            properties: {
                content: { type: "string", description: "资产内容" },
                tags: { type: "array", items: { type: "string" }, description: "标签列表" },
                category: { type: "string", description: "分类 (neuron/skill/memory/other)" },
                source: { type: "string", description: "来源名称" },
            },
            required: ["content"],
        },
        async execute(_toolCallId, params) {
            const startMs = Date.now();
            const w = getWorker(ws);
            if (!w || !w.ready) {
                return { content: [{ type: "text", text: "Worker 未就绪" }], isError: true };
            }
            try {
                const result = await w.call("asset_register", {
                    content: String(params.content),
                    tags: params.tags || [],
                    category: params.category || "other",
                    source: params.source || "gateway_tool",
                }, 15000);
                return { content: [{ type: "text", text: `✅ Asset 已注册: ${result.asset_id || "N/A"}` }], details: { elapsedMs: Date.now() - startMs } };
            }
            catch (err) {
                return { content: [{ type: "text", text: `注册失败: ${err.message}` }], isError: true };
            }
        },
    });

    // ==========================================
    // ContextEngine - 接管上下文压缩,防止 DAG 炸掉
    // 要求 OpenClaw >= 2026.3.7(registerContextEngine API)
    // ==========================================
    const ceConfig = pluginConfig.contextEngine || {};
    const CE_MAX_RECENT = ceConfig.maxRecentMessages || 20;
    const CE_RECALL_ON_ASSEMBLE = ceConfig.recallOnAssemble !== false;
    const CE_TOKEN_BUDGET = ceConfig.tokenBudget || 12000;
    const CE_COMPACT_THRESHOLD = ceConfig.compactThreshold || 0.6;
    const CE_EMERGENCY_CEILING = ceConfig.emergencyCeiling || 0.88;

    // ==========================================
    // Memory Slots — 多层级记忆容量/策略配置
    // ==========================================
    const memSlots = pluginConfig.memorySlots || {};
    const MEM_DAG = { maxNodes: 10000, retentionDays: 90, ...(memSlots.dag || {}) };
    const MEM_VERIFIED = { minConfidence: 0.8, maxEntries: 5000, ...(memSlots.verified || {}) };
    const MEM_NEURAL = { hnswM: 16, hnswEfConstruction: 200, hnswEfSearch: 100, ...(memSlots.neural || {}) };
    const MEM_SYNAPSE = { baseWeight: 0.5, ltpRate: 0.05, ltdRate: 0.03, ...(memSlots.synapse || {}) };
    const MEM_TEMPORAL = { decayHalfLife: 30, ...(memSlots.temporal || {}) };

    // 维护计数器(afterTurn 使用)
    let _maintenanceCounter = 0;
    const MAINTENANCE_L1 = 5;
    const MAINTENANCE_L2 = 20;
    const MAINTENANCE_L3 = 50;

    // --- 辅助函数:从 message 对象提取纯文本 ---
    function extractText(msg) {
        if (!msg?.content) return "";
        if (typeof msg.content === "string") return msg.content;
        if (Array.isArray(msg.content)) {
            return msg.content
                .filter(c => c.type === "text")
                .map(c => c.text || "")
                .join(" ");
        }
        return "";
    }

    // --- 辅助函数:估算消息的 token 数(基于字符长度,更精确) ---
    // 中文约 1.5 字符/token,英文约 4 字符/token,混合取 ~2.5
    // 工具调用结果通常更密集,加 1.3x 系数
    function estimateTokens(msg) {
        const text = extractText(msg);
        // 工具调用结果通常含 JSON,token 密度更高
        const isToolResult = msg.role === "tool" || (msg.role === "assistant" && msg.tool_calls);
        const baseEstimate = Math.ceil(text.length / 2.5);
        return isToolResult ? Math.ceil(baseEstimate * 1.3) : baseEstimate;
    }

    // --- 辅助函数:智能存储(Worker 优先,降级 spawnSync) ---
    // v7.1: sessionId 参与存储 & 检索范围限定（HAConvDR + ChatRetriever 会话隔离）
    async function smartStore(content, source, sessionId = "") {
        if (!content || content.trim().length < 5) return false;
        try {
            const w = getWorker(ws);
            if (w.ready) {
                await w.call("store", { content, source, session_id: sessionId }, 5000);
                return true;
            }
        } catch (e) { /* fall through */ }
        // Worker 不可用,降级 spawnSync
        try {
            const result = runClawScript(ws, "store", { content, source, session_id: sessionId }, 8000);
            return !result.error;
        } catch (e) {
            return false;
        }
    }

    // --- 辅助函数:智能检索(Worker 优先,降级 spawnSync) ---
    // v7.1: sessionId 限定检索范围（HAConvDR 上下文去噪 + ChatRetriever 会话级索引）
    async function smartRecall(query, topK = 3, sessionId = "") {
        try {
            const w = getWorker(ws);
            if (w.ready) {
                const result = await w.call("recall", { query, top_k: topK, session_id: sessionId }, 10000);
                if (result?.results) return Array.isArray(result.results) ? result.results : [];
                if (Array.isArray(result)) return result;
            }
        } catch (e) { /* fall through */ }
        return recallFallback(ws, query, topK, sessionId);
    }

    // --- 会话级摘要缓存(compact 生成后暂存,assemble 可引用) ---
    // 文件持久化:Gateway 重启后恢复最后 500 条摘要
    const _sessionSummaries = new Map();
    const SUMMARY_CACHE_PATH = path.join(
        OPENCLAW_HOME,
        "context-offload/summary-cache.jsonl"
    );
    function _saveSummaryCache() {
        try {
            const dir = path.dirname(SUMMARY_CACHE_PATH);
            if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
            const tmp = SUMMARY_CACHE_PATH + ".tmp";
            const fd = openSync(tmp, "w", 0o600);
            try {
                let count = 0;
                for (const [sid, summary] of _sessionSummaries) {
                    if (!summary || summary.length < 10) continue;
                    const line = JSON.stringify({ sessionId: sid, summary, ts: Date.now() }) + "\n";
                    writeSync(fd, line);
                    count++;
                    if (count >= 500) break;
                }
            } finally {
                closeSync(fd);
            }
            try { unlinkSync(SUMMARY_CACHE_PATH); } catch {}
            renameSync(tmp, SUMMARY_CACHE_PATH);
        } catch (e) {
            api.logger.debug?.(`${TAG} [context-engine] save summary cache failed: ${e.message}`);
        }
    }
    function _loadSummaryCache() {
        try {
            if (!existsSync(SUMMARY_CACHE_PATH)) return;
            const raw = readFileSync(SUMMARY_CACHE_PATH, "utf-8");
            let loaded = 0;
            for (const line of raw.split("\n").filter(Boolean)) {
                try {
                    const entry = JSON.parse(line);
                    if (entry.sessionId && entry.summary) {
                        _sessionSummaries.set(entry.sessionId, entry.summary);
                        loaded++;
                    }
                } catch {}
            }
            if (loaded > 0) {
                api.logger.info?.(`${TAG} [context-engine] loaded ${loaded} session summaries from cache`);
            }
        } catch (e) {
            api.logger.debug?.(`${TAG} [context-engine] load summary cache failed: ${e.message}`);
        }
    }
    _loadSummaryCache();
    setInterval(_saveSummaryCache, 300000).unref();

    const _seenSessions = new Set();

    // --- ZMQ SUB + mmap 共享内存(/dev/shm,DAG 上下文零拷贝) ---
    // 端口/路径可从插件配置覆盖,默认值方便"开箱即用"
    const ZMQ_PUB_PORT = pluginConfig.communication?.zmqPort || 5559;
    const MMAP_SHM_PATH = pluginConfig.communication?.mmapPath || "/dev/shm/claw_dag_cache";
    let _zmqSub = null;
    let _zmqSubActive = false;  // 当前 ZMQ SUB 是否活跃
    let _zmqLastSeq = 0;        // 最后收到的 ZMQ 事件序号（gap 检测）
    let _zmqLastEventTs = 0;    // 最后收到 ZMQ 事件的时间（心跳检测）
    let _mmapDagCache = null;   // { text, stats, ts }

    // 向内部注册 IPC 通道元信息(非 channel 插件,不污染 api.channels)
    api._clawCoreIPC = api._clawCoreIPC || {};
    api._clawCoreIPC.meta = {
      zmq: { endpoint: `tcp://127.0.0.1:${ZMQ_PUB_PORT}`, type: "pub/sub", events: ["dag_assemble", "dag_compact", "rccam_phase", "channel_heartbeat"] },
      mmap: { path: MMAP_SHM_PATH, size: "2MB", format: "4-byte LE length + UTF-8 JSON" },
      uds:  { path: getUdsPath(), protocol: "binary length-prefix JSON-RPC" },
    };

    // ═══ ZMQ SUB 持久重连循环(指数退避 + gap 检测) ═══
    async function _zmqPersistentLoop() {
        const MAX_BACKOFF = 30000;  // 最大退避 30s
        const BASE_BACKOFF = 1000;  // 初始退避 1s
        let backoff = BASE_BACKOFF;
        let consecutiveFails = 0;

        while (true) {
            if (_zmqSub) {
                try { _zmqSub.close(); } catch (e) {}
                _zmqSub = null;
            }
            _zmqSubActive = false;

            try {
                const zmq = _cjsRequire("zeromq");
                const sub = new zmq.Subscriber();
                await sub.connect(`tcp://127.0.0.1:${ZMQ_PUB_PORT}`);
                sub.subscribe("");
                _zmqSub = sub;
                _zmqSubActive = true;
                _zmqLastEventTs = Date.now();
                backoff = BASE_BACKOFF;
                consecutiveFails = 0;

                if (api.logger.info) {
                    api.logger.info(`${TAG} [zmq] SUB 已连接 tcp://127.0.0.1:${ZMQ_PUB_PORT} (重试 #${consecutiveFails})`);
                } else {
                    process.stderr.write(`${TAG} [zmq] SUB connected :${ZMQ_PUB_PORT}\n`);
                }

                for await (const [msg] of sub) {
                    try {
                        const evt = JSON.parse(msg.toString());
                        _zmqLastEventTs = Date.now();

                        // ── 序号 gap 检测 ──
                        if (evt.seq && typeof evt.seq === 'number') {
                            if (_zmqLastSeq > 0 && evt.seq > _zmqLastSeq + 1) {
                                const gapSize = evt.seq - _zmqLastSeq - 1;
                                process.stderr.write(`${TAG} [zmq] ⚠️ 事件跳号: seq ${_zmqLastSeq}→${evt.seq} (丢失 ${gapSize} 条), 触发 mmap 全量同步\n`);
                                // gap 后触发 mmap 全量同步，补偿丢失的 DAG 状态
                                _mmapDagCache = _dagReadMmap();
                            }
                            _zmqLastSeq = evt.seq;
                        }

                        // ── DAG 事件 ──
                        if (evt.event === "dag_compact" || evt.event === "dag_assemble") {
                            _mmapDagCache = _dagReadMmap();
                        }
                        // Worker 主动推送恢复信号 → 自动关熔断器
                        if (evt.event === "dag_recovered" && _dagCB.state === 'OPEN') {
                            _dagCB.reset();
                            if (api.logger.info) {
                                api.logger.info(`${TAG} [context-engine] DAG circuit CLOSED via ZMQ recovery signal`);
                            }
                        }
                        // channel_heartbeat → 标记 ZMQ 通道正常
                        if (evt.event === "channel_heartbeat") {
                            // 仅更新 _zmqLastEventTs，上面已更新
                        }

                        // ── R-CCAM 阶段事件 → 实时进度追踪 ──
                        if (evt.event === "rccam_phase") {
                            const phase = evt.phase || "?";
                            const status = evt.status || "?";
                            const cycle = evt.data?.cycle ?? "?";
                            const sessionKey = evt.session_key || evt.data?.session_key || "?";
                            if (sessionKey !== "?") {
                                _rccamProgress.set(sessionKey, {
                                    phase, status, cycle,
                                    ts: Date.now(),
                                    elapsedMs: evt.elapsed_ms || 0,
                                });
                            }
                            if (api.logger.info) {
                                api.logger.info(`${TAG} [rccam-events] session=${sessionKey} phase=${phase} status=${status} cycle=${cycle}`);
                            }
                        }
                        // ── mmap 大结果通知（Worker 侧写完后推送）──
                        if (evt.event === "mmap_result_ready") {
                            // 已在 _httpCall 中通过 _readWorkerMmap 解引用，
                            // 此处仅记录用于调试
                        }
                    } catch (e) {}
                }
            } catch (e) {
                consecutiveFails++;
                backoff = Math.min(BASE_BACKOFF * Math.pow(2, consecutiveFails - 1), MAX_BACKOFF);
                process.stderr.write(`${TAG} [zmq] SUB 断开 (fail #${consecutiveFails}), ${backoff}ms 后重连: ${e.message}\n`);
            }

            _zmqSubActive = false;
            await new Promise(r => setTimeout(r, backoff).unref());
        }
    }

    // 启动 ZMQ 持久重连循环
    _zmqPersistentLoop().catch((e) => {
        process.stderr.write(`${TAG} [zmq] persistent loop fatal: ${e.message}\n`);
    });

    function _dagReadMmap() {
        try {
            const fs = require("fs");
            const buf = fs.readFileSync(MMAP_SHM_PATH);
            if (buf.length < 4) return null;
            const need = buf.readUInt32LE(0);
            if (buf.length < 4 + need || need <= 0 || need > 2097152) return null;
            const p = JSON.parse(buf.slice(4, 4 + need).toString());
            if (p && p.text) {
                return { text: p.text, stats: p.stats || {}, ts: Date.now() };
            }
            return null;
        } catch { return null; }
    }

    // ZMQ SUB 持久重连循环已在上面 _zmqPersistentLoop() 启动

    // 后台 mmap 过期清理(每 5 分钟检查一次)
    const MMAP_CLEANUP_INTERVAL = 300000; // 5 min
    const _mmapCleanupTimer = setInterval(() => {
        const w = getWorker(ws);
        if (w && w.ready) {
            w.call("mmap_cleanup", { expire_secs: 300 }, 5000).catch(() => {});
        }
    }, MMAP_CLEANUP_INTERVAL).unref();

    // Node 端进程退出时清理 mmap
    process.on("exit", () => {
        clearInterval(_mmapCleanupTimer);
    });

    // --- IPC 通道选路(测速缓存,30秒刷新) ---
    let _channelLatency = { mmap: -1, uds: -1, zmq: -1 };
    let _channelLastCheck = 0;

    function pickFastestChannel() {
        const now = Date.now();
        if (now - _channelLastCheck < 30000) return _channelLatency;
        _channelLastCheck = now;
        // mmap: 读 /dev/shm 测速
        const t0 = process.hrtime.bigint();
        try {
            const fs = require("fs");
            fs.readFileSync(MMAP_SHM_PATH, { flag: "r" });
            _channelLatency.mmap = Number(process.hrtime.bigint() - t0) / 1000; // μs
        } catch { _channelLatency.mmap = -1; }
        // UDS: ping 测速
        _channelLatency.uds = -1;
        const w = getWorker(ws);
        if (w && w.ready) {
            const t1 = process.hrtime.bigint();
            w.call("ping", {}, 3000).then(() => {
                _channelLatency.uds = Number(process.hrtime.bigint() - t1) / 1000;
            }).catch(() => { _channelLatency.uds = -1; });
        }
        return _channelLatency;
    }

    // --- DAG 辅助:调用 Worker 的 DAG 方法(降级时静默跳过) ---
    // 正式熔断器:连续失败 N 次自动 OPEN,RESET_INTERVAL 后半开探测

    class CircuitBreaker {
        constructor(name, opts = {}) {
            this.name = name;
            this.failures = 0;
            this.threshold = opts.failureThreshold || 3;
            this.resetTimeout = opts.resetTimeout || 30000;
            this.state = 'CLOSED'; // CLOSED | OPEN | HALF_OPEN
            this.lastFailureTime = 0;
        }

        async call(fn) {
            if (this.state === 'OPEN') {
                if (Date.now() - this.lastFailureTime > this.resetTimeout) {
                    this.state = 'HALF_OPEN';
                } else {
                    return null; // 熔断中，静默跳过
                }
            }
            try {
                const result = await fn();
                this.failures = 0;
                if (this.state !== 'CLOSED') {
                    this.state = 'CLOSED';
                }
                return result;
            } catch (e) {
                this.failures++;
                this.lastFailureTime = Date.now();
                if (this.failures >= this.threshold) {
                    this.state = 'OPEN';
                }
                throw e;
            }
        }

        reset() {
            this.failures = 0;
            this.state = 'CLOSED';
        }

        get status() {
            return { name: this.name, state: this.state, failures: this.failures };
        }
    }

    const _dagCB = new CircuitBreaker('dag', { failureThreshold: 3, resetTimeout: 30000 });
    const _compactCB = new CircuitBreaker('compact', { failureThreshold: 3, resetTimeout: 30000 });

    const dagEnabled = ceConfig.dagEnabled !== false;

    async function dagCall(method, params) {
        if (!dagEnabled) return null;

        // dag_assemble 优先从 mmap 零拷贝读取
        if (method === "dag_assemble") {
            const cached = _dagReadMmap();
            if (cached && cached.text) {
                api.logger.debug?.(`${TAG} [context-engine] dag_assemble from mmap (~${cached.text.length}B)`);
                _dagCB.reset();
                return { text: cached.text, stats: cached.stats, _from_mmap: true };
            }
        }

        // 熔断检查
        if (_dagCB.state === 'OPEN') {
            if (Date.now() - _dagCB.lastFailureTime > _dagCB.resetTimeout) {
                _dagCB.state = 'HALF_OPEN';
                api.logger.info?.(`${TAG} [context-engine] DAG circuit half-open, probing ${method}`);
            } else {
                return null;
            }
        }

        try {
            const w = getWorker(ws);
            if (w.ready) {
                const result = await w.call(method, params, 10000);
                if (result && result._dag_degraded) {
                    api.logger.debug?.(`${TAG} [context-engine] dag ${method} degraded: ${result.reason || "unavailable"}`);
                    return null;
                }
                _dagCB.reset();
                // dag_assemble 走完 UDS 后也刷新 mmap 缓存
                if (method === "dag_assemble" && result?.text) {
                    _mmapDagCache = { text: result.text, stats: result.stats, ts: Date.now() };
                }
                return result;
            }
        } catch (e) {
            _dagCB.failures++;
            _dagCB.lastFailureTime = Date.now();
            if (_dagCB.failures >= _dagCB.threshold) {
                _dagCB.state = 'OPEN';
                api.logger.warn?.(`${TAG} [context-engine] DAG circuit OPENED — ${_dagCB.threshold} consecutive failures, disabling DAG for ${_dagCB.resetTimeout / 1000}s`);
            }
            api.logger.debug?.(`${TAG} [context-engine] dag ${method} failed (${_dagCB.failures}/${_dagCB.threshold}): ${e.message}`);

            if (_dagCB.failures >= _dagCB.threshold && _dagCB.state !== 'OPEN') {
                _dagCB.state = 'OPEN';
                api.logger.warn?.(`${TAG} [context-engine] DAG circuit OPENED — ${_dagCB.threshold} consecutive failures, disabling DAG calls for ${_dagCB.resetTimeout / 1000}s`);
            }
        }
        return null;
    }

    // --- compact 核心逻辑(独立函数,被 compact() try/catch 包裹) ---
    async function _compactInner(sessionId, force, tokenBudget, currentTokenCount) {
        api.logger.info?.(`${TAG} [context-engine] compact called (force=${force}, budget=${tokenBudget}, current=${currentTokenCount})`);

        // CMV 风格全局阈值:token 使用 ≥ compactThreshold 就触发强制压缩
        const FORCE_RATIO = CE_COMPACT_THRESHOLD;
        if (!force && currentTokenCount > 0 && tokenBudget > 0 && currentTokenCount > tokenBudget * FORCE_RATIO) {
            api.logger.info?.(`${TAG} [context-engine] compact FORCED: current(${currentTokenCount}) > ${Math.round(FORCE_RATIO * 100)}% of budget(${tokenBudget})`);
            force = true;
        }

        // ============================================================
        // R-CCAM cycle compact 路径(LCM 三级,ownsCompaction=true 主力)
        // ============================================================
        const rccamStatus = await dagCall("rccam_compact_needed", { sessionId });
        if (rccamStatus?.needs_soft || rccamStatus?.needs_hard || force) {
            const compressible = rccamStatus?.compressible_cycles || [];
            let sumCount = 0;
            // 自适应步长:根据可压缩 cycle 数量动态决定
            const totalAvail = compressible.length;
            let maxToCompress;
            if (force) {
                maxToCompress = Math.min(totalAvail, Math.max(15, Math.ceil(totalAvail * 0.7)));
            } else {
                if (totalAvail <= 5) maxToCompress = totalAvail;
                else if (totalAvail <= 20) maxToCompress = Math.ceil(totalAvail * 0.66);
                else maxToCompress = Math.ceil(totalAvail * 0.5);
            }
            for (const cycleId of compressible) {
                if (sumCount >= maxToCompress) break;
                await dagCall("rccam_compact_cycle", { sessionId, cycleId });
                sumCount++;
            }
            api.logger.info?.(`${TAG} [context-engine] compact: R-CCAM compacted ${sumCount}/${totalAvail} cycles, raw_tokens_before=${rccamStatus.stats?.raw_tokens}`);

            // 多轮循环压缩:持续重检直到 token 降到阈值以下或没有更多可压缩 cycle
            if (currentTokenCount > 0 && tokenBudget > 0) {
                const TARGET_RATIO = force ? 0.60 : 0.75;
                let rounds = 0;
                const MAX_ROUNDS = 5;
                while (rounds < MAX_ROUNDS && currentTokenCount > tokenBudget * TARGET_RATIO) {
                    const recheck = await dagCall("rccam_compact_needed", { sessionId });
                    const moreCycles = recheck?.compressible_cycles || [];
                    if (moreCycles.length === 0) break;
                    const roundMax = Math.max(3, Math.ceil(moreCycles.length * (0.33 + rounds * 0.12)));
                    let roundCount = 0;
                    for (const cycleId of moreCycles) {
                        if (roundCount >= roundMax) break;
                        await dagCall("rccam_compact_cycle", { sessionId, cycleId });
                        roundCount++;
                        sumCount++;
                    }
                    rounds++;
                    const remainingCycles = moreCycles.length - roundCount;
                    const totalAfter = compressible.length > 0
                        ? currentTokenCount * (1 - roundCount / compressible.length * 0.3)
                        : currentTokenCount * 0.9;
                    currentTokenCount = Math.max(0, Math.round(totalAfter));
                    api.logger.info?.(`${TAG} [context-engine] compact: multi-round #${rounds} compressed ${roundCount} more cycles, estimated tokens now ~${currentTokenCount} (target=${Math.round(tokenBudget * TARGET_RATIO)})`);
                }
            }

            // 更新摘要缓存
            const dagCtx = await dagCall("dag_assemble", { sessionId, freshCycles: 1, maxTokens: tokenBudget || 240000 });
            // 非短路:R-CCAM 压完后继续尝试认知压缩 dag_nodes
            try {
                const dagResult = await dagCall("cognitive_compress_dag", { sessionId, maxToCompress: 20 });
                if (dagResult?.summarized > 0) {
                    api.logger.info?.(`${TAG} [context-engine] compact: DAG cognitive compressed ${dagResult.summarized} groups`);
                }
            } catch (e) {
                api.logger.debug?.(`${TAG} [context-engine] compact: DAG cognitive compress skipped: ${e.message}`);
            }

            if (dagCtx?.text) {
                _sessionSummaries.set(sessionId, dagCtx.text.slice(0, 1200) + (dagCtx.text.length > 1200 ? "\n[...truncated]" : ""));
            } else {
                _sessionSummaries.set(sessionId, `[R-CCAM cycle compacted: ${sumCount} cycles]`);
            }
            // 写后落盘(sync,不阻塞后续)
            setImmediate(() => { try { _saveSummaryCache(); } catch {} });
        }

        // ============================================================
        // 如果 R-CCAM 没数据,检查 dag_nodes 是否需要认知压缩
        // ============================================================
        if (!rccamStatus?.needs_soft && !rccamStatus?.needs_hard && !force) {
            try {
                const dagResult = await dagCall("cognitive_compress_dag", { sessionId, maxToCompress: 10 });
                if (dagResult?.summarized > 0) {
                    api.logger.info?.(`${TAG} [context-engine] compact: DAG cognitive compressed ${dagResult.summarized} groups (no rccam cycles)`);
                    // 压过东西就更新摘要缓存
                    const dagCtx = await dagCall("dag_assemble", { sessionId, freshCycles: 1, maxTokens: tokenBudget || 240000 });
                    if (dagCtx?.text) {
                        _sessionSummaries.set(sessionId, dagCtx.text.slice(0, 1200) + (dagCtx.text.length > 1200 ? "\n[...truncated]" : ""));
                    }
                    return { ok: true, compacted: true };
                }
            } catch (e) {
                api.logger.debug?.(`${TAG} [context-engine] compact: DAG cognitive compress skipped: ${e.message}`);
            }
        }

        // ============================================================
        // 兜底:旧 DAG 路径(仅当以上路径均未触发时)
        // ============================================================
        const dagStatus = await dagCall("dag_status", { sessionId, context_window_tokens: tokenBudget });

        if (!dagStatus) {
            const usage = currentTokenCount > 0 && tokenBudget > 0 ? currentTokenCount / tokenBudget : 0;
            api.logger.info?.(`${TAG} [context-engine] compact: DAG unavailable, usage=${Math.round(usage * 100)}%`);
            // ownsCompaction=true, 没有 OpenClaw safeguard 兜底,必须自己处理
            // Level 3 终极兜底:直接写 hard truncation 摘要
            _sessionSummaries.set(sessionId, `[上下文使用率 ${Math.round(usage * 100)}%,DAG 不可用]`);
            // 如果使用率 > 85% 且我们没法压缩其他东西了,仍然返回 compacted=true
            // 这样 OpenClaw 不会重试压缩(重试也无用),而是用我们给出的摘要继续
            return { ok: true, compacted: usage > 0.85 };
        }

        if (!force && !dagStatus.needs_compact && !(rccamStatus?.needs_soft)) {
            return { ok: true, compacted: false };
        }

        // 旧 DAG 摘要兜底(动态取最新消息数,根据预算和 session 历史量计算)
        const dynamicRecent = tokenBudget > 0 && currentTokenCount > 0
            ? Math.min(50, Math.max(10, Math.round(20 * (1 - currentTokenCount / tokenBudget) + 10)))
            : 20;
        let summary = "";
        const dagAssemble = await dagCall("dag_assemble", { sessionId, maxRecentMessages: dynamicRecent });
        if (dagAssemble && dagAssemble.text) {
            const text = dagAssemble.text;
            const MAX_SUMMARY_LEN = 1200;
            summary = text.length > MAX_SUMMARY_LEN
                ? text.slice(0, MAX_SUMMARY_LEN) + "\n[...truncated]"
                : text;
        }
        if (!summary) summary = "[Context compacted - no content available for summary]";
        _sessionSummaries.set(sessionId, summary);

        const dagResult = await dagCall("dag_compact", { sessionId, summary, batchSize: 10 });
        if (dagResult?.summarized > 0) {
            api.logger.info?.(`${TAG} [context-engine] compact: DAG summarized ${dagResult.summarized} nodes`);
        }
        api.logger.info?.(`${TAG} [context-engine] compact: summary ${summary.length} chars`);
        return { ok: true, compacted: true };
    }

    api.registerContextEngine("claw-core-engine", (ctx) => ({
            info: {
                id: "claw-core-engine",
                name: "Claw Core Context Engine (R-CCAM + LCM三级)",
                ownsCompaction: true,
            },

            // ──────────────────────────────────
            // ingest - 每条新消息进入时索引到知识库 + DAG 节点
            // 关键规则:ingest 失败不应阻止消息进入会话,仅影响检索增强
            // ──────────────────────────────────
            async ingest({ sessionId, message, isHeartbeat }) {
                try {
                    if (isHeartbeat) return { ingested: true };
                    const content = extractText(message);
                    if (!content || content.trim().length < 5) return { ingested: false };
                    api.logger.debug?.(`${TAG} [context-engine] ingest: session=${sessionId}, role=${message?.role}, len=${content.length}`);

                    // 新会话检测:/new 后自动清理当前会话的 DAG 节点和摘要缓存
                    // 注意：不移除 _rccamCache（按 query 做 key，不依赖 session），
                    // 新会话可从旧会话的 R-CCAM 分析结果中直接命中，避免"失忆"。
                    if (!_seenSessions.has(sessionId)) {
                        _seenSessions.add(sessionId);
                        api.logger.info?.(`${TAG} [context-engine] new session detected: ${sessionId}, clearing per-session caches`);
                        _sessionSummaries.delete(sessionId);
                        dagCall("dag_clear_session", { sessionId }).catch(() => {});
                    }

                    const source = message?.role === "assistant" ? "ai" : "user";
                    const tokens = estimateTokens(message);

                    // 生成 Galaxy 三维绑定元数据
                    const galaxyMeta = {
                        semantic_map: message?.role === "assistant" ? "AI回复消息" : "用户输入",
                        function_map: message?.role === "assistant" ? "assistant.generate" : "user.input",
                        design_ref: "index.js#L1287-L1318",
                    };

                    // 并行:smartStore 知识库索引 + DAG 节点存储
                    const [stored, dagResult] = await Promise.all([
                        smartStore(content, source, sessionId).catch(() => false),
                        dagCall("dag_ingest", {
                            sessionId,
                            role: message?.role || source,
                            content,
                            tokens,
                            metadata: galaxyMeta,
                        }),
                    ]);
                    if (!stored) {
                        api.logger.debug?.(`${TAG} [context-engine] ingest: store failed for session=${sessionId}`);
                    }
                    if (!dagResult) {
                        api.logger.debug?.(`${TAG} [context-engine] ingest: DAG ingest skipped (worker unavailable)`);
                    }

                    // ═══ 管道自动路由: ingest 时并行触发 R-CCAM(fire-and-forget) ═══
                    if (message?.role === "user" && content.trim().length >= 3) {
                        // 不阻塞消息流,直接异步触发
                        (async () => {
                            try {
                                const worker = getWorker(ws);
                                if (!worker || !worker.ready) return;
                                const cacheKey = "rccam:" + content.trim();
                                const now = Date.now();
                                const existing = _rccamCache.get(cacheKey);
                                if (existing && now - existing.ts <= RCCAM_CACHE_TTL) return;

                                const result = await worker.call("rccam", {
                                    user_input: content.trim(),
                                    max_cycles: 1,
                                    store_memory: true,
                                    sessionKey: sessionId,
                                }, 30000).catch(() => null);
                                if (!result) return;

                                const cp = result.cognition_payload || result.rccam_phase_states?.retrieval?.cognition_payload || null;
                                const hasPayload = cp && (
                                    (cp.retrieved_memories && cp.retrieved_memories.length > 0) ||
                                    (cp.dag_summaries && cp.dag_summaries.length > 0) ||
                                    cp.reflexion_context
                                );
                                if (!hasPayload && !result.answer) return;

                                _rccamCache.set(cacheKey, {
                                    answer: result.answer || "",
                                    routingDebug: result.routing_debug || result.rccam_phase_states?.control?.strategy || "",
                                    confidence: result.confidence || 0,
                                    cognitionPayload: cp,
                                    phaseLogs: result.phase_logs || [],
                                    sessionKey: result.session_key || "",
                                    strategy: result.strategy || result.rccam_phase_states?.control?.strategy || "",
                                    userInput: content.trim(),
                                    ts: now,
                                });
                                _rccamPrune();

                                // ═══ 同时写 mmap,重启不丢,assemble 零拷贝读取 ═══
                                try {
                                    _mmapSyncWrite({
                                        rccam: {
                                            sessionId: result.session_key || sessionId,
                                            query: content.trim().slice(0, 200),
                                            answer: (result.answer || "").slice(0, 500),
                                            strategy: (result.routing_debug || result.rccam_phase_states?.control?.strategy || "").slice(0, 200),
                                            confidence: result.confidence || 0,
                                            cognitionPayload: cp ? {
                                                hasMemories: (cp.retrieved_memories?.length || 0) > 0,
                                                hasDag: (cp.dag_summaries?.length || 0) > 0,
                                                hasReflexion: !!cp.reflexion_context,
                                            } : null,
                                        },
                                        type: "rccam_update",
                                    });
                                } catch (_) {}
                            } catch (_) {}
                        })();
                    }

                    return { ingested: true };
                } catch (e) {
                    // ingest 失败不应阻止消息进入会话
                    api.logger.warn?.(`${TAG} [context-engine] ingest error (degraded): ${e.message}`);
                    return { ingested: true };
                }
            },

            // ──────────────────────────────────
            // assemble - 每次模型调用前构建上下文
            // 策略:DAG 优先(支持摘要节点回溯),降级到线性累积
            // 关键规则:assemble 失败 → OpenClaw runs 失败(不自动降级 legacy)
            // 所以 assemble 必须有兜底返回,绝不抛异常
            // ──────────────────────────────────
            async assemble({ sessionId, messages, tokenBudget, availableTools, citationsMode }) {
                api.logger.debug?.(`${TAG} [context-engine] assemble: session=${sessionId}, msgs=${messages?.length}, budget=${tokenBudget}`);
                try {
                    // v7.1: 系统消息去重 + 上限（防 Gateway 被塞爆）
                    const dedupedSystem = [];
                    const seenSystem = new Set();
                    for (const m of (messages || []).filter(m => m.role === "system")) {
                        const key = (m.content || "").slice(0, 100);
                        if (!seenSystem.has(key)) { seenSystem.add(key); dedupedSystem.push(m); }
                    }
                    // 系统消息上限：最多保留 5 条（核心 prompt），超出的丢弃
                    const MAX_SYSTEM_MSGS = 5;
                    const systemMsgs = dedupedSystem.slice(-MAX_SYSTEM_MSGS);
                    const nonSystemMsgs = messages.filter(m => m.role !== "system");

                    // 1) 计算系统提示的实际 token 开销
                    const systemTokens = systemMsgs.reduce((sum, m) => sum + estimateTokens(m), 0);

                    // 2) 留预算给摘要注入和检索注入
                    const recallBudget = CE_RECALL_ON_ASSEMBLE ? 600 : 0;
                    // 摘要预算根据 tokenBudget 动态调整:预算宽裕时给更多
                    const summaryBudget = _sessionSummaries.has(sessionId)
                        ? Math.min(800, Math.max(200, Math.round(tokenBudget * 0.003)))
                        : 0;
                    const safetyMargin = 200; // 工具定义等额外开销
                    const rawMsgBudget = tokenBudget - systemTokens - recallBudget - summaryBudget - safetyMargin;
                    const msgBudget = Math.max(rawMsgBudget, Math.min(200, Math.floor(tokenBudget * 0.5)));

                    // 3) 从最新消息往回累积,直到预算耗尽
                    const recentMsgs = [];
                    let usedTokens = 0;
                    for (let i = nonSystemMsgs.length - 1; i >= 0; i--) {
                        const msg = nonSystemMsgs[i];
                        const tokens = estimateTokens(msg);
                        if (usedTokens + tokens > msgBudget && recentMsgs.length > 0) break;
                        recentMsgs.unshift(msg);
                        usedTokens += tokens;
                    }

                    // 3.5) 全局天花板检查:估算总 token 是否接近窗口上限
                    // 临时估算(不含 systemPromptAddition 实际长度),等完整拼接后复查
                    const CRITICAL_USAGE_RATIO = CE_EMERGENCY_CEILING;
                    let needsEmergencyTrim = false;
                    let estimateBeforeAdditions = systemTokens + usedTokens;
                    // 如果系统消息+最近消息已经接近 88%,强制压缩
                    if (estimateBeforeAdditions > tokenBudget * CRITICAL_USAGE_RATIO) {
                        api.logger.info?.(`${TAG} [context-engine] assemble CRITICAL: ${estimateBeforeAdditions} tokens > ${Math.round(CRITICAL_USAGE_RATIO * 100)}% of budget (${tokenBudget}), forcing emergency trim`);
                        needsEmergencyTrim = true;
                        // v7.1: RLM 递归压缩 → 替代简单截断
                        let rlmSummary = "";
                        try {
                            const w = getWorker(ws);
                            if (w.ready) {
                                const rlmResult = await w.call("rlm_compress", {
                                    messages: recentMsgs.slice(0, -8).map(m => ({role: m.role, content: extractText(m)})),
                                    max_tokens: 500
                                }, 8000);
                                if (rlmResult?.compressed) {
                                    rlmSummary = rlmResult.compressed;
                                }
                            }
                        } catch (e) { /* fall through */ }
                        _sessionSummaries.set(sessionId,
                            rlmSummary
                                ? `[RLM递归压缩 ${recentMsgs.length - 8} 条历史消息] ${rlmSummary.slice(0, 300)}`
                                : `[上下文全局天花板触发 - 保留最近 ${recentMsgs.length} 条消息中的后 4 轮]`);
                        // 只保留最近 4 轮(8条,user+assistant 各4)
                        const emergencyKeep = 8;
                        while (recentMsgs.length > emergencyKeep) {
                            const evicted = recentMsgs.shift();
                            usedTokens -= estimateTokens(evicted);
                        }
                        api.logger.info?.(`${TAG} [context-engine] assemble emergency trim: kept ${recentMsgs.length} msgs, ~${systemTokens + usedTokens} tokens, rlm=${rlmSummary ? 'compressed' : 'truncated'}`);
                    }

                    // 4) 增强检索: 全论文模块编排 (MemGPT+MemoryOS+HAConvDR+AriGraph+RAPTOR)
                    //    优先调 context_assemble，降级到 smartRecall
                    let systemPromptAddition = "";
                    if (CE_RECALL_ON_ASSEMBLE) {
                        try {
                            const lastUserMsg = nonSystemMsgs.filter(m => m.role === "user").slice(-1)[0];
                            if (lastUserMsg) {
                                const query = extractText(lastUserMsg);
                                if (query && query.trim().length >= 5) {
                                    // v7.1: 优先走全论文模块编排
                                    let injection = "";
                                    try {
                                        const w = getWorker(ws);
                                        if (w.ready) {
                                            const ctxResult = await w.call("context_assemble", {
                                                query, session_id: sessionId, top_k: 5
                                            }, 15000);
                                            if (ctxResult?.success && ctxResult?.injection) {
                                                injection = ctxResult.injection;
                                                api.logger.debug?.(`${TAG} [context-engine] context_assemble layers: ${Object.keys(ctxResult.layers || {}).filter(k => !k.endsWith('_error')).join(',')}`);
                                            }
                                        }
                                    } catch (e) { /* 降级 */ }
                                    // 降级: smartRecall
                                    if (!injection) {
                                        const items = await smartRecall(query, 3, sessionId);
                                        if (items.length > 0) {
                                            injection = items
                                                .slice(0, 3)
                                                .map((m, i) => `[Claw Recall ${i + 1}] ${m.content || ""}`)
                                                .join("\n");
                                        }
                                    }
                                    systemPromptAddition = injection;
                                }
                            }
                        } catch (e) {
                            api.logger.debug?.(`${TAG} [context-engine] assemble recall failed: ${e.message}`);
                        }
                    }

                    // 4b) 跨会话 DAG 搜索（补充 smartRecall 可能遗漏的旧会话关键记录）
                    let crossSessionInjection = "";
                    if (CE_RECALL_ON_ASSEMBLE) {
                        try {
                            const lastUserMsg = nonSystemMsgs.filter(m => m.role === "user").slice(-1)[0];
                            if (lastUserMsg) {
                                const query = extractText(lastUserMsg);
                                if (query && query.trim().length >= 5) {
                                    const w = getWorker(ws);
                                    if (w && w.ready) {
                                        const dagResults = await w.call("dag_search", {
                                            query: query.trim(),
                                            limit: 3,
                                            exclude_session: sessionId
                                        }, 10000).catch(() => null);
                                        if (dagResults && dagResults.results && dagResults.results.length > 0) {
                                            crossSessionInjection = "\n[其他会话的相关记录]\n";
                                            crossSessionInjection += dagResults.results
                                                .map((r, i) => `[DAG ${i + 1}] [${r.role || "unknown"}] (会话:${(r.session_key || "?").slice(-8)}): ${(r.content || "").slice(0, 300)}`)
                                                .join("\n");
                                            crossSessionInjection += "\n";
                                        }
                                    }
                                }
                            }
                        } catch (e) {
                            api.logger.debug?.(`${TAG} [context-engine] cross-session search failed: ${e.message}`);
                        }
                    }

                    // 5) 人格无条件注入(每次 assemble 都读取,不依赖 Worker/R-CCAM)
                    let personaBlock = "";
                    try {
                        const { readFileSync } = await import("fs");
                        const idPath = path.join(ws, "IDENTITY.md");
                        if (existsSync(idPath)) {
                            const lines = readFileSync(idPath, "utf-8").split("\n");
                            personaBlock += "[人格定义]\n";
                            personaBlock += lines[0] + "\n";
                            personaBlock += lines.slice(3, 15).join("\n").trim() + "\n\n";
                            const soulPath = path.join(ws, "SOUL.md");
                            if (existsSync(soulPath)) {
                                const soulText = readFileSync(soulPath, "utf-8");
                                const truthsMatch = soulText.match(/## Core Truths\n\n([\s\S]*?)\n\n##/);
                                if (truthsMatch) {
                                    personaBlock += truthsMatch[1].trim();
                                }
                            }
                        }
                    } catch (e) {
                        api.logger.debug?.(`${TAG} assemble persona injection skipped: ${e.message}`);
                    }

                                        // 6) R-CCAM 三源:mmap(零拷贝)> _rccamCache(当前轮)> dag_assemble(持久层)+ pending前轮
                    // assemble 不等 R-CCAM,命中了就注入+标记。没命中等 before_prompt_build 或 agent_end 兜底。
                    let summaryInjection = "";

                    // 6a) 检查上轮未消费的 R-CCAM 结果(pending 注入)
                    {
                        const _pending = _pendingRccamInjection.get(sessionId);
                        if (_pending && (Date.now() - _pending.ts) <= PENDING_INJECT_TTL) {
                            summaryInjection = "[上轮 R-CCAM 分析摘要]\n";
                            if (_pending.answer) summaryInjection += _pending.answer.slice(0, 800) + "\n\n";
                            if (_pending.strategy) summaryInjection += "[策略] " + _pending.strategy + "\n\n";
                            summaryInjection += "[提示] 以上是上一轮对话的 R-CCAM 深度分析(实时分析跟不上回复节奏,回追补充)。\n";
                            _pendingRccamInjection.delete(sessionId);
                            api.logger.debug?.(`${TAG} [context-engine] assemble: injected pending R-CCAM from previous turn`);
                        }
                    }

                    // 6b) mmap 读取 Worker 最新 R-CCAM 结果(重启不丢,零拷贝)
                    try {
                        const mmapState = _mmapSyncRead();
                        if (mmapState && mmapState.rccam && typeof mmapState.rccam === 'object') {
                            const rc = mmapState.rccam;
                            // 按 session 匹配
                            if (rc.sessionId === sessionId && rc.query) {
                                const ck = "rccam:" + rc.query;
                                if (!_rccamCache.has(ck)) {
                                    const now = Date.now();
                                    _rccamCache.set(ck, {
                                        answer: rc.answer || "",
                                        routingDebug: rc.strategy || "",
                                        confidence: rc.confidence || 0,
                                        cognitionPayload: rc.cognitionPayload || null,
                                        sessionKey: rc.sessionId || "",
                                        userInput: rc.query,
                                        ts: now,
                                    });
                                    _rccamPrune();
                                }
                            }
                        }
                    } catch (_) {}

                    // 6b) R-CCAM cognitionPayload 完整渲染注入
                    // 优先精确匹配当前 query; 匹配不到则取最近一条未消费的缓存(解决 R-CCAM 60s 滞后问题)
                    let _rccamEntry = null;
                    let _rccamKey = null;
                    const now6b = Date.now();
                    const lastUserMsg = nonSystemMsgs.filter(m => m.role === "user").slice(-1)[0];
                    if (lastUserMsg) {
                        const query = extractText(lastUserMsg);
                        if (query) {
                            const ck = "rccam:" + query.trim();
                            _rccamEntry = _rccamCache.get(ck);
                            _rccamKey = ck;
                            // 未命中 → 取最近一条 TTL 内且未被 consume 的缓存
                            if (!_rccamEntry || now6b - _rccamEntry.ts > RCCAM_CACHE_TTL) {
                                let _best = null, _bestKey = null, _bestTs = 0;
                                for (const [k, v] of _rccamCache) {
                                    if (_rccamConsumedByAssemble.has(k)) continue;
                                    if (now6b - v.ts > RCCAM_CACHE_TTL) continue;
                                    if (v.ts > _bestTs) { _best = v; _bestKey = k; _bestTs = v.ts; }
                                }
                                if (_best) { _rccamEntry = _best; _rccamKey = _bestKey; }
                            }
                        }
                    }
                    if (_rccamEntry && _rccamKey) {
                        const cached = _rccamEntry;
                                const cp = cached.cognitionPayload;
                                // 标准化键名
                                if (cp) {
                                    cp.memories = cp.memories || cp.retrieved_memories || [];
                                    cp.dag = cp.dag || cp.dag_summaries || [];
                                    cp.reflexion = cp.reflexion || cp.reflexion_context || "";
                                    cp.routing = cp.routing || cp.routing_debug || cached.routingDebug || "";
                                }
                                const hasUsefulData = cp && (
                                    (cp.memories && cp.memories.length > 0) ||
                                    (cp.dag && cp.dag.length > 0) ||
                                    cp.rewritten_query ||
                                    cp.flash_summary ||
                                    cp.hub_context ||
                                    cp.reranked_results ||
                                    cp.reflexion ||
                                    cp.skill_guide ||
                                    cp.persona_visual ||
                                    cp.persona_context ||
                                    cp.merged_context ||
                                    cp.self_evolution ||
                                    cp.temporal_kg_extraction ||
                                    cp.spatial_scene ||
                                    cp.causal_context ||
                                    cached.answer ||
                                    cached.routingDebug ||
                                    (cached.phaseLogs && cached.phaseLogs.length > 0)
                                );
                                if (hasUsefulData) {
                                    const route = (cp ? cp.routing : "") || cached.routingDebug || "";
                                    const intent = (cp ? cp.intent : "") || "unknown";
                                    const confidence = cached.confidence || 0;

                                    summaryInjection = "\n[R-CCAM 认知分析]\n";
                                    summaryInjection += "元认知策略: " + (cached.strategy || cached.routingDebug || route || "auto") + "\n";
                                    summaryInjection += "意图: " + intent + " | 置信度: " + confidence.toFixed(2) + "\n\n";

                                    // 阶段日志
                                    if (cached.phaseLogs && cached.phaseLogs.length > 0) {
                                        summaryInjection += "[R-CCAM 阶段日志]:\n";
                                        var _cycleGroups = {};
                                        cached.phaseLogs.forEach(function(l) {
                                            var c = l.cycle || 1;
                                            if (!_cycleGroups[c]) _cycleGroups[c] = [];
                                            _cycleGroups[c].push(l);
                                        });
                                        Object.keys(_cycleGroups).sort().forEach(function(c) {
                                            summaryInjection += "  Cycle " + c + ": ";
                                            _cycleGroups[c].forEach(function(l) {
                                                summaryInjection += "[" + l.phase + " " + l.elapsed_ms + "ms] " + (l.detail || "") + " ";
                                            });
                                            summaryInjection += "\n";
                                        });
                                        summaryInjection += "\n";
                                    }

                                    // DAG
                                    if (cp.dag && cp.dag.length > 0) {
                                        summaryInjection += "[DAG 会话上下文]";
                                        cp.dag.slice(0, 3).forEach(function(d) {
                                            if (d && d.length > 5) summaryInjection += "\n- " + d.slice(0, 300);
                                        });
                                        summaryInjection += "\n\n";
                                    }

                                    // Pro 改写
                                    if (cp.rewritten_query && cp.rewritten_query.length > 5) {
                                        summaryInjection += "[改写查询(Pro)]: " + cp.rewritten_query.slice(0, 300) + "\n\n";
                                    }

                                    // Reranker 精排
                                    if (cp.reranked_results && cp.reranked_results.length > 0) {
                                        summaryInjection += "[reranker精排结果(" + cp.reranked_results.length + "条)]:\n";
                                        cp.reranked_results.slice(0, 8).forEach(function(r, i) {
                                            if (r && r.length > 5) summaryInjection += (i + 1) + ". " + r.slice(0, 400) + "\n";
                                        });
                                        summaryInjection += "\n";
                                    }

                                    // Flash 摘要
                                    if (cp.flash_summary && cp.flash_summary.length > 10) {
                                        summaryInjection += cp.flash_summary.slice(0, 2000) + "\n\n";
                                    }

                                    // Hub context
                                    if (cp.hub_context && cp.hub_context.length > 50) {
                                        summaryInjection += "[原始检索上下文]:\n" + cp.hub_context.slice(0, 2000) + "\n\n";
                                    }

                                    // 记忆
                                    if (cp.memories && cp.memories.length > 0) {
                                        summaryInjection += "[记忆(" + cp.memories.length + "条)]:\n";
                                        cp.memories.slice(0, 5).forEach(function(m) {
                                            if (m && m.length > 5) summaryInjection += "- " + m.slice(0, 300) + "\n";
                                        });
                                        summaryInjection += "\n";
                                    }

                                    // 实体
                                    if (cp.kg_entities && cp.kg_entities.length > 0) {
                                        summaryInjection += "[实体]: " + cp.kg_entities.join(", ") + "\n\n";
                                    }

                                    // 历史经验
                                    if (cp.reflexion && cp.reflexion.length > 5) {
                                        summaryInjection += "[历史经验]: " + cp.reflexion.slice(0, 300) + "\n\n";
                                    }

                                    // 技能指导
                                    if (cp.skill_guide && cp.skill_guide.length > 10) {
                                        summaryInjection += "[方法论指导]:\n" + cp.skill_guide.slice(0, 500) + "\n\n";
                                    }

                                    // 合并上下文
                                    if (cp.merged_context && cp.merged_context.length > 20) {
                                        summaryInjection += "[合并上下文 (来自 " + (cp.merged_count || 0) + "条, " + (cp.merged_sources || []).join("+") + ")]:\n";
                                        summaryInjection += cp.merged_context.slice(0, 2000) + "\n\n";
                                    }

                                    // 人格视觉
                                    if (cp.persona_visual && (cp.persona_visual.exists || cp.persona_visual.note)) {
                                        var _pv = cp.persona_visual;
                                        summaryInjection += "[人格视觉]: ";
                                        if (_pv.exists) {
                                            summaryInjection += "DAG人格节点 ✓ 来源:" + (_pv.source || "?") + " 创建于:" + (_pv.dag_time || "?") + " 字符:" + (_pv.chars || 0);
                                            if (_pv.needs_refresh) summaryInjection += " ⚠️ 文件已更新,DAG需刷新";
                                        } else {
                                            summaryInjection += _pv.note || "无人格节点";
                                        }
                                        summaryInjection += "\n\n";
                                    }

                                    // 人格上下文摘要
                                    if (cp.persona_context && cp.persona_context.length > 20) {
                                        summaryInjection += "[人格定义摘要]:\n" + cp.persona_context.slice(0, 500) + "\n\n";
                                    }

                                    // 自进化
                                    if (cp.self_evolution && cp.self_evolution.success) {
                                        var _se = cp.self_evolution;
                                        summaryInjection += "[内在元认知进化]:\n";
                                        if (_se.patterns && _se.patterns.length > 0) {
                                            _se.patterns.slice(0, 3).forEach(function(p, i) {
                                                summaryInjection += (i + 1) + ". [" + p.confidence + "] " + p.scenario + " → " + p.suggestion.slice(0, 100) + "\n";
                                            });
                                        }
                                        summaryInjection += "系统影响: " + (_se.system_impact || "未知").slice(0, 200) + "\n";
                                        summaryInjection += "自省: " + (_se.self_critique || "未知").slice(0, 200) + "\n\n";
                                    }

                                    // 时空认知
                                    if (cp.temporal_kg_extraction || cp.temporal_kg_neighbors || cp.temporal_kg_conflicts || cp.temporal_kg_community) {
                                        summaryInjection += "[时序上下文]\n";
                                        if (cp.temporal_kg_extraction && cp.temporal_kg_extraction.summary)
                                            summaryInjection += "实体演化: " + (cp.temporal_kg_extraction.summary.slice(0, 200) || "") + "\n";
                                        if (cp.temporal_kg_neighbors && cp.temporal_kg_neighbors.length > 0)
                                            summaryInjection += "相关实体: " + cp.temporal_kg_neighbors.slice(0, 5).join(", ") + "\n";
                                        if (cp.temporal_kg_conflicts && cp.temporal_kg_conflicts.edges_invalidated > 0)
                                            summaryInjection += "⚠️ 旧事实已更新: " + cp.temporal_kg_conflicts.edges_invalidated + " 条历史关联已失效\n";
                                        if (cp.temporal_kg_community && cp.temporal_kg_community.length > 10)
                                            summaryInjection += "社区摘要: " + cp.temporal_kg_community.slice(0, 400) + "\n";
                                        summaryInjection += "\n";
                                    }

                                    // 空间上下文
                                    if (cp.spatial_scene || cp.inferred_scene) {
                                        summaryInjection += "[空间上下文]\n";
                                        if (cp.spatial_scene) summaryInjection += "注册场景: " + cp.spatial_scene + "\n";
                                        if (cp.inferred_scene) summaryInjection += "推断场景: " + cp.inferred_scene + "\n";
                                        summaryInjection += "\n";
                                    }

                                    // 认知状态
                                    if (cp.causal_context || cp.emotion_context || cp.cognitive_map_density > 0 || cp.lasar_introspective) {
                                        summaryInjection += "[认知状态]\n";
                                        if (cp.causal_context) summaryInjection += cp.causal_context.slice(0, 300) + "\n";
                                        if (cp.emotion_context) summaryInjection += "情感轨迹: " + cp.emotion_context.slice(0, 200) + "\n";
                                        if (cp.cognitive_map_density > 0) summaryInjection += "认知密度: " + cp.cognitive_map_density + "\n";
                                        if (cp.lasar_introspective) summaryInjection += "自省: " + cp.lasar_introspective.slice(0, 200) + "\n";
                                        summaryInjection += "\n";
                                    }

                                    // 行为指导
                                    var _behaviorStrats = {
                                        "answer": "信息已充分,直接给出准确、完整的回答,无需额外检索。",
                                        "direct_answer": "信息已充分,直接回答,无需深度分析。",
                                        "info_insufficient": "当前检索到的信息不足以完整回答。优先说明已知部分,明确标注不确定性,可反问用户补充信息。",
                                        "deep_reasoning": "问题需要深度推理。展开分析过程、逻辑链条,再给出结论。",
                                        "full_pipeline": "需要完整处理流程。分步骤回答,每步给出依据。",
                                        "clarify_needed": "用户意图不够明确。优先反问澄清需求,不要强行猜测。",
                                        "boundary_violation": "超出能力或安全边界。礼貌拒绝并说明原因,不尝试绕过限制。",
                                        "polite_refuse": "无法处理该请求。礼貌拒绝,不编造答案。",
                                        "answer_with_uncertainty": "信息有限但可以回答。给出已知部分,明确标注不确定性。",
                                        "ask_user": "需要用户提供更多信息。直接反问。",
                                        "retry": "需要重试。说明上次失败原因后重新尝试。",
                                        "auto": "按正常对话流程处理,无需特殊行为指导。"
                                    };
                                    var _stratKey = cached.strategy || cached.routingDebug || route || "auto";
                                    var _behaviorGuide = _behaviorStrats[_stratKey] || "按正常对话流程处理。";
                                    summaryInjection += "[行为指导] " + _behaviorGuide + "\n\n";

                                    summaryInjection += "[R-CCAM] 以上是你的认知分析数据,请基于这些信息组织你的最终回答。\n";
                                    // ═══ 标记已消费 (assemble 层兜底成功) ═══
                                    _rccamConsumedByAssemble.add(_rccamKey);
                                }
                            }

                    // 6c) dag_assemble 兜底
                    // 6c) dag_assemble 兜底
                    if (!summaryInjection) {
                        const dagCtx = await dagCall("dag_assemble", { sessionId, freshCycles: 2, maxTokens: tokenBudget || 240000 });
                        if (dagCtx?.text && dagCtx?.stats?.total_cycles > 0) {
                            const ctx = dagCtx.text;
                            const MAX_CTX = 3000;
                            summaryInjection = ctx.length > MAX_CTX
                                ? ctx.slice(0, MAX_CTX) + "\n[...older cycles summarized]"
                                : ctx;
                        } else {
                            const cachedSummary = _sessionSummaries.get(sessionId);
                            if (cachedSummary) {
                                summaryInjection = "[Earlier conversation summary]\n" + cachedSummary;
                            }
                        }
                    }
                    const additions = [personaBlock, summaryInjection, systemPromptAddition, crossSessionInjection].filter(Boolean);
                    if (additions.length > 0) {
                        systemPromptAddition = additions.join("\n\n");
                    }

                    // v7.1: 最终消息 — 系统消息上限 + 注入合并为单个 system 消息
                    const finalSystemMsgs = systemMsgs.slice(0, MAX_SYSTEM_MSGS);
                    const finalMessages = [...finalSystemMsgs, ...recentMsgs];
                    const totalEstimate = systemTokens + usedTokens;

                    // 最终复查:如果算上 systemPromptAddition 仍然溢出,丢弃 addition 中的非人格内容
                    const additionTokens = Math.ceil((systemPromptAddition?.length || 0) / 2.5);
                    if (totalEstimate + additionTokens > tokenBudget * 0.95) {
                        api.logger.info?.(`${TAG} [context-engine] assemble final check: dropping recall/summary injection (total+addition=${totalEstimate + additionTokens} > 95% of ${tokenBudget})`);
                        // 只保留人格块,去掉检索和摘要注入
                        const personaOnly = systemPromptAddition?.split("\n\n")[0] || "";
                        systemPromptAddition = personaOnly;
                    }

                    api.logger.info?.(`${TAG} [context-engine] assemble: ${finalMessages.length} msgs, ~${totalEstimate} tokens (budget=${tokenBudget}, recalled=${recentMsgs.length}/${nonSystemMsgs.length})`);

                    return {
                        messages: finalMessages,
                        estimatedTokens: totalEstimate,
                        systemPromptAddition,
                        promptAuthority: "assembled",
                    };
                } catch (e) {
                    // 终极兜底:assemble 绝不能抛异常,否则 OpenClaw runs 直接失败
                    api.logger.error?.(`${TAG} [context-engine] assemble unexpected error (returning raw messages): ${e.message}`);
                    return {
                        messages: messages || [],
                        estimatedTokens: 0,
                        promptAuthority: "assembled",
                    };
                }
            },

            // ──────────────────────────────────
            // compact - 压缩上下文(DAG 增量摘要 + 会话摘要缓存)
            // 策略:优先 DAG auto_summarize(支持节点回溯),降级到线性截断
            // 关键规则(OpenClaw 官方 docs/concepts/context-engine.md):
            //   - ownsCompaction: false 时,compact() 负责所有压缩
            //   - no-op compact() is unsafe(会禁用 /compact 和 overflow recovery)
            //   - compact() 绝不能抛异常,否则 overflow recovery 失败 → runs 失败
            // ──────────────────────────────────
            async compact({ sessionId, force, sessionKey, sessionFile, tokenBudget, currentTokenCount, compactionTarget, customInstructions, runtimeContext }) {
                try {
                    return await _compactInner(sessionId, force, tokenBudget, currentTokenCount);
                } catch (e) {
                    // 终极兜底:compact 绝不能抛异常
                    api.logger.error?.(`${TAG} [context-engine] compact unexpected error (using emergency fallback): ${e.message}`);
                    try {
                        _sessionSummaries.set(sessionId, `[Emergency summary] compact failed: ${e.message.slice(0, 200)}`);
                    } catch (_) { /* 静默 */ }
                    return { ok: true, compacted: true };
                }
            },

            // ──────────────────────────────────
            // ingestBatch - 批量 ingest(OpenClaw 优先调用此方法)
            // 如果实现了此方法,OpenClaw 会在回合结束后一次性传入所有新消息
            // ──────────────────────────────────
            async ingestBatch({ sessionId, sessionKey, messages }) {
                try {
                    const results = [];
                    for (const msg of messages) {
                        const r = await this.ingest({ sessionId, message: msg, isHeartbeat: false });
                        results.push(r);
                    }
                    return { ingested: true, count: results.filter(r => r?.ingested).length };
                } catch (e) {
                    api.logger.warn?.(`${TAG} [context-engine] ingestBatch error: ${e.message}`);
                    return { ingested: true, count: 0 };
                }
            },

            // ──────────────────────────────────
            // afterTurn - 完整的 L1/L2/L3 维护
            // 从 after_message 钩子迁移,避免 spawnSync 阻塞事件循环
            // ──────────────────────────────────
            async afterTurn({ sessionId }) {
                _maintenanceCounter++;
                const counter = _maintenanceCounter;

                // L1: 轻量维护 - 每 5 轮
                if (counter % MAINTENANCE_L1 === 0) {
                    try {
                        const w = getWorker(ws);
                        if (w.ready) {
                            await w.call("health", {}, 5000);
                        } else {
                            runClawScript(ws, "health", {}, 5000);
                        }
                    } catch (e) {
                        // 静默
                    }
                }

                // L2: 缓存预热 + 硬件调优 - 每 20 轮
                if (counter % MAINTENANCE_L2 === 0) {
                    api.logger.info?.(`${TAG} [context-engine] L2 maintenance: cache_warmup + hardware_tune`);
                    try {
                        const w = getWorker(ws);
                        if (w.ready) {
                            await w.call("workflow", {
                                scenario: "cache_warmup",
                                input: "{}",
                            }, 15000);
                        } else {
                            runClawScript(ws, "workflow", { scenario: "cache_warmup", input: "{}" }, 15000);
                        }
                    } catch (e) {
                        api.logger.debug?.(`${TAG} [context-engine] L2 cache_warmup failed: ${e.message}`);
                    }
                    try {
                        const w = getWorker(ws);
                        if (w.ready) {
                            await w.call("workflow", {
                                scenario: "hardware_tune",
                                input: "{}",
                            }, 15000);
                        } else {
                            runClawScript(ws, "workflow", { scenario: "hardware_tune", input: "{}" }, 15000);
                        }
                    } catch (e) {
                        api.logger.debug?.(`${TAG} [context-engine] L2 hardware_tune failed: ${e.message}`);
                    }
                }

                // L3: 全优化周期 - 每 50 轮
                if (counter % MAINTENANCE_L3 === 0) {
                    api.logger.info?.(`${TAG} [context-engine] L3 maintenance: optimization_run`);
                    try {
                        const w = getWorker(ws);
                        if (w.ready) {
                            await w.call("workflow", {
                                scenario: "optimization_run",
                                input: "{}",
                            }, 30000);
                        } else {
                            runClawScript(ws, "workflow", { scenario: "optimization_run", input: "{}" }, 30000);
                        }
                    } catch (e) {
                        api.logger.debug?.(`${TAG} [context-engine] L3 optimization_run failed: ${e.message}`);
                    }
                }

                // 清理过期摘要缓存(防止内存泄漏)
                if (counter % 100 === 0) {
                    if (_sessionSummaries.size > 50) {
                        api.logger.debug?.(`${TAG} [context-engine] trimming summary cache (${_sessionSummaries.size} entries)`);
                        // 保留最近的 20 个
                        const keys = [..._sessionSummaries.keys()];
                        for (let i = 0; i < keys.length - 20; i++) {
                            _sessionSummaries.delete(keys[i]);
                        }
                    }
                }
            },

            // ──────────────────────────────────
            // dispose - 释放资源(Gateway shutdown/reload 时调用)
            // ──────────────────────────────────
            dispose() {
                api.logger.info?.(`${TAG} [context-engine] dispose: cleaning up`);
                try {
                    // 关闭前落盘摘要缓存
                    _saveSummaryCache();
                    if (_worker) {
                        const w = _worker;
                        _worker = null;
                        w.stop();
                    }
                } catch (e) {
                    api.logger.debug?.(`${TAG} [context-engine] dispose worker stop failed: ${e.message}`);
                }
                _sessionSummaries.clear();
            },
        }));
    api.logger.info?.(`${TAG} ContextEngine "claw-core-engine" registered (ownsCompaction=true, tokenBudget=${CE_TOKEN_BUDGET}, compactThreshold=${CE_COMPACT_THRESHOLD}, emergencyCeiling=${CE_EMERGENCY_CEILING}, maxRecent=${CE_MAX_RECENT}, recallOnAssemble=${CE_RECALL_ON_ASSEMBLE}, dag=${dagEnabled}(${MEM_DAG.maxNodes}nodes/${MEM_DAG.retentionDays}d), verified≥${MEM_VERIFIED.minConfidence}/${MEM_VERIFIED.maxEntries}max, neural(HNSW M${MEM_NEURAL.hnswM}/ef${MEM_NEURAL.hnswEfConstruction}/efSearch${MEM_NEURAL.hnswEfSearch}), circuitBreaker=${_dagCB.threshold}fails/${_dagCB.resetTimeout / 1000}s)`);

    // --- L0 日志异步批写(debounce 2 秒,不阻塞 agent_end) ---
    const _l0LogBuffer = [];
    let _l0LogFlushTimer = null;
    async function _l0LogFlush() {
        if (_l0LogFlushTimer) {
            clearTimeout(_l0LogFlushTimer);
            _l0LogFlushTimer = null;
        }
        const batch = _l0LogBuffer.splice(0);
        if (batch.length === 0) return;
        try {
            await fsp.mkdir(path.join(ws, "memory"), { recursive: true });
            const byDate = {};
            for (const item of batch) {
                if (!byDate[item.date]) byDate[item.date] = { items: [], headerSet: false };
                byDate[item.date].items.push(item);
            }
            for (const [date, group] of Object.entries(byDate)) {
                const dailyFile = path.join(ws, "memory", date + ".md");
                let header = "";
                if (!group.headerSet) {
                    try { await fsp.access(dailyFile); } catch (e) { header = `# ${date} 记忆\n\n> 每日对话记录\n\n---\n\n`; }
                }
                const entries = group.items.map(i => `### ${i.timestamp}\n**User:** ${i.user}\n**AI:** ${i.asst}\n\n`).join("");
                await fsp.appendFile(dailyFile, header + entries, "utf-8");
            }
            api.logger.debug?.(`${TAG} [agent_end] L0 log flushed ${batch.length} entries`);
        } catch (e) {
            if (batch.length + _l0LogBuffer.length <= 100) _l0LogBuffer.unshift(...batch);
            api.logger.debug?.(`${TAG} [agent_end] L0 log flush failed: ${e.message}`);
        }
    }
    function _l0LogScheduleFlush() {
        if (_l0LogFlushTimer) return;
        _l0LogFlushTimer = setTimeout(() => {
            _l0LogFlushTimer = null;
            _l0LogFlush().catch(() => {});
        }, 2000).unref();
    }

    // ═══ R-CCAM 三层兜底: assemble → before_prompt_build → agent_end 补充消息 ═══
    const _rccamCache = new Map();
    const RCCAM_CACHE_TTL = 60000;
    const RCCAM_CACHE_MAX = 100;
    // 纪录哪些 cache key 已被 assemble() 消费(用于 agent_end 判断是否需要发补充消息)
    const _rccamConsumedByAssemble = new Set();
    // 存储未被任何层消费的 R-CCAM 结果, 由下一轮 assemble() 注入
    const _pendingRccamInjection = new Map();
    const PENDING_INJECT_MAX = 50;
    const PENDING_INJECT_TTL = 120000;

    function _rccamPrune() {
        if (_rccamCache.size < RCCAM_CACHE_MAX * 1.5) return;
        const now = Date.now();
        const entries = Array.from(_rccamCache.entries())
            .filter(([_, v]) => now - v.ts <= RCCAM_CACHE_TTL * 3)
            .sort((a, b) => a[1].ts - b[1].ts);
        const toRemove = entries.slice(0, Math.max(entries.length - RCCAM_CACHE_MAX, Math.floor(entries.length * 0.3)));
        for (const [k] of toRemove) _rccamCache.delete(k);
    }

    // 钩子1: before_agent_reply — 异步调 R-CCAM,fire-and-forget 不阻塞回复发送
    api.on("before_agent_reply", async (event) => {
        if (!event) { console.error('[rccam_hook] no event'); return; }
        const text = (event.cleanedBody || "").trim();
        if (!text || text.length < 1) { console.error('[rccam_hook] empty text, event keys:', Object.keys(event).join(',')); return; }
        if (text.startsWith("{") && text.endsWith("}")) {
            try { JSON.parse(text); return; } catch(e) {}
        }
        const now = Date.now();
        const cacheKey = "rccam:" + text;
        console.log('[rccam_hook] text=' + text.slice(0,30) + ' cacheKey=' + cacheKey);
        const existing = _rccamCache.get(cacheKey);
        if (existing && now - existing.ts <= RCCAM_CACHE_TTL) { console.log('[rccam_hook] cache hit, skip'); return; }
        if (existing) _rccamCache.delete(cacheKey);

        const w = getWorker(ws);
        if (!w) { console.error('[rccam_hook] no worker'); return; }
        console.log('[rccam_hook] calling w.call rccam... (fire-and-forget)');
        // fire-and-forget: 不等待 R-CCAM 结果,回复立即发送
        w.call("rccam", { user_input: text, max_cycles: 1, store_memory: false }, 120000).then((result) => {
            console.log('[rccam_hook] w.call returned, has result=' + !!result);
            if (!result) { console.log('[rccam_hook] null result'); return; }
            try {
                const _payloadFromNested = result.rccam_phase_states?.retrieval?.cognition_payload || null;
                const cp = _payloadFromNested || result.cognition_payload || null;
                const hasPayload = cp && (
                    (cp.retrieved_memories && cp.retrieved_memories.length > 0) ||
                    (cp.dag_summaries && cp.dag_summaries.length > 0) ||
                    cp.reflexion_context
                );
                if (!hasPayload && !result.answer) return;
                const _now = Date.now();
                _rccamCache.set(cacheKey, {
                    answer: result.answer || "",
                    routingDebug: result.routing_debug || result.rccam_phase_states?.control?.strategy || "",
                    confidence: result.confidence || 0,
                    cognitionPayload: cp,
                    phaseLogs: result.phase_logs || [],
                    sessionKey: result.session_key || "",
                    strategy: result.strategy || result.rccam_phase_states?.control?.strategy || "",
                    userInput: text,
                    ts: _now
                });
                try {
                    _mmapSyncWrite({
                        source: "rccam_cache",
                        rccam: {
                            sessionId: result.session_key || "",
                            query: text.slice(0, 500),
                            answer: (result.answer || "").slice(0, 2000),
                            strategy: result.strategy || result.rccam_phase_states?.control?.strategy || "",
                            confidence: result.confidence || 0,
                            hasCognitionPayload: !!cp
                        }
                    });
                } catch (_e) {}
            } catch (e) {}
        }).catch(() => {});
        _rccamPrune();
    });

    // --- 跨轮关键词追踪(提升召回相关性) ---
    const _sessionKeywords = new Map();
    const MAX_SESSION_KEYWORDS = 20;
    const MAX_SESSIONS_KEYWORDS = 1000;

    function _updateSessionKeywords(sessionKey, keywords) {
        if (!sessionKey || !keywords || keywords.length === 0) return;
        if (!_sessionKeywords.has(sessionKey) && _sessionKeywords.size >= MAX_SESSIONS_KEYWORDS) {
            const firstKey = _sessionKeywords.keys().next().value;
            if (firstKey != null) _sessionKeywords.delete(firstKey);
        }
        if (!_sessionKeywords.has(sessionKey)) {
            _sessionKeywords.set(sessionKey, new Set());
        }
        const ctx = _sessionKeywords.get(sessionKey);
        for (const kw of keywords) {
            ctx.add(kw);
        }
        if (ctx.size > MAX_SESSION_KEYWORDS) {
            const arr = Array.from(ctx);
            const toRemove = arr.slice(0, ctx.size - MAX_SESSION_KEYWORDS);
            for (const kw of toRemove) ctx.delete(kw);
        }
    }

    function _getSessionKeywords(sessionKey) {
        const ctx = _sessionKeywords.get(sessionKey);
        return ctx ? Array.from(ctx) : [];
    }

    function _extractKeywords(text) {
        if (!text) return [];
        const cleaned = text.toLowerCase().replace(/[^\w\u4e00-\u9fff]/g, " ");
        const words = cleaned.split(/\s+/).filter(w => w.length > 1);
        const stopwords = new Set([
            "的","了","是","在","我","有","和","就","不","人","都","一","一个",
            "上","也","很","到","说","要","去","你","会","着","没有","看","好",
            "自己","这","那","他","她","它","们","也","吗","吧","呢","啊","哦",
            "哈","嗯","嘛","哟","还是","或者","但是","因为","所以","如果","虽然","而且","然后","可以",
            "the","a","an","is","are","was","were","be","been","being","have","has","had",
            "do","does","did","will","would","can","could","shall","should","may","might","must",
            "i","you","he","she","it","we","they","me","him","her","us","them",
            "this","that","these","those","and","or","but","if","because","when","where","how","what","which","who","whom",
            "to","of","in","for","on","with","at","by","from","as","into","not","no","yes",
        ]);
        return words.filter(w => !stopwords.has(w) && w.length < 30);
    }

    // --- 钩子3: agent_end - AI 回复后:L0 日志 + save_memory + 关键词追踪 ---
    api.on("agent_end", async (event, ctx) => {
        try {
            const sessionKey = ctx?.sessionKey || event?.channel || "default";

            let userContent = "";
            let asstContent = "";
            if (event && event.messages && Array.isArray(event.messages)) {
                const msgs = event.messages;
                for (let i = msgs.length - 1; i >= 0; i--) {
                    const m = msgs[i];
                    if (m && m.role === "user") {
                        if (typeof m.content === "string") { userContent = m.content; break; }
                        if (Array.isArray(m.content)) {
                            const textBlocks = m.content.filter(c => c && c.type === "text");
                            if (textBlocks.length > 0) { userContent = textBlocks.map(c => c.text || "").join("\n"); break; }
                        }
                    }
                }
                for (let i = msgs.length - 1; i >= 0; i--) {
                    const m = msgs[i];
                    if (m && m.role === "assistant" && m.content) {
                        if (typeof m.content === "string") { asstContent = m.content; break; }
                        if (Array.isArray(m.content)) {
                            const textBlocks = m.content.filter(c => c && c.type === "text");
                            if (textBlocks.length > 0) {
                                asstContent = textBlocks.map(c => c.text || "").join("\n");
                                break;
                            }
                        }
                    }
                }
            }

            // ── L0 每日日志写入 ──
            if (userContent && userContent.trim().length >= 3) {
                try {
                    const date = new Date().toISOString().slice(0, 10);
                    const timestamp = new Date().toISOString().slice(0, 19).replace("T", " ");
                    const safeUser = userContent.trim().slice(0, 500);
                    const safeAsst = (asstContent || "[空内容]").trim().slice(0, 500);

                    if (!/^\[.+\]$/.test(safeUser) && !/^[\p{Emoji}\s]+$/u.test(safeUser) && safeUser.length >= 3) {
                        const dailyDir = path.join(ws, "memory");
                        if (!existsSync(dailyDir)) mkdirSync(dailyDir, { recursive: true });
                        const dailyFile = path.join(dailyDir, date + ".md");
                        let header = "";
                        if (!existsSync(dailyFile)) {
                            header = `# ${date} 记忆\n\n> 每日对话记录\n\n---\n\n`;
                        }
                        const entry = `### ${timestamp}\n**User:** ${safeUser}\n**AI:** ${safeAsst}\n\n`;
                        _l0LogBuffer.push({ date, timestamp, user: safeUser, asst: safeAsst });
                        _l0LogScheduleFlush();

                        const keywords = _extractKeywords(safeUser);
                        if (keywords.length > 0) {
                            _updateSessionKeywords(sessionKey, keywords);
                        }
                    }
                } catch (e) {
                    api.logger.debug?.(`${TAG} [agent_end] L0 log failed: ${e.message}`);
                }
            }

            // ── 从 rccam 缓存持久化 save_memory ──
            const now = Date.now();
            let latest = null;
            for (const [k, v] of _rccamCache) {
                if (now - v.ts > RCCAM_CACHE_TTL * 2) continue;
                if (v.sessionKey && (v.userInput || v.answer)) {
                    latest = v;
                    break;
                }
            }
            if (latest && latest.sessionKey && asstContent) {
                const w = getWorker(ws);
                if (w && w.ready) {
                    w.call("save_memory", {
                        session_key: latest.sessionKey,
                        user_input: latest.userInput || "",
                        answer: asstContent.slice(0, 8000),
                        metadata: {},
                    }, 10000).catch(() => {});
                }

                // ═══ 第三层兜底: R-CCAM 在前两层都没赶上 → 存为下轮注入 ═══
                const _agentEndUserKey = "rccam:" + (userContent || "").trim();
                let _aeCached = null;
                let _aeCk = null;
                const _aeNow = Date.now();
                if (!_rccamConsumedByAssemble.has(_agentEndUserKey)) {
                    _aeCached = _rccamCache.get(_agentEndUserKey);
                    _aeCk = _agentEndUserKey;
                    if (!_aeCached || _aeNow - _aeCached.ts > RCCAM_CACHE_TTL) {
                        for (const [k, v] of _rccamCache) {
                            if (_rccamConsumedByAssemble.has(k)) continue;
                            if (_aeNow - v.ts > RCCAM_CACHE_TTL) continue;
                            if (!_aeCached || v.ts > _aeCached.ts) { _aeCached = v; _aeCk = k; }
                        }
                    }
                }
                if (_aeCached && _aeCk && (_aeNow - _aeCached.ts) <= RCCAM_CACHE_TTL * 2) {
                        // 还没被消费, 存为下轮 pending
                        _pendingRccamInjection.set(sessionKey, {
                            query: _aeCached.userInput || userContent || "",
                            answer: _aeCached.answer || "",
                            strategy: _aeCached.strategy || "",
                            confidence: _aeCached.confidence || 0,
                            ts: Date.now()
                        });
                        // 清理过期
                        if (_pendingRccamInjection.size > PENDING_INJECT_MAX) {
                            const _now = Date.now();
                            for (const [k, v] of _pendingRccamInjection) {
                                if (_now - v.ts > PENDING_INJECT_TTL) _pendingRccamInjection.delete(k);
                            }
                        }
                    }
            }
        } catch (e) {}
    });

    // 钩子2: before_prompt_build - 第二层兜底: assemble 没等到 R-CCAM 但现在已经完成了
    // 如果 cache 有数据且未被 assemble 消费 → 渲染注入
    // 如果已消费 → 继续原来的动态锚定逻辑
    api.on("before_prompt_build", async (event) => {
        try {
            if (!event) return;
            const msgs = event.messages || [];
            let userText = "";
            for (let i = msgs.length - 1; i >= 0; i--) {
                const m = msgs[i];
                if (m.role === "user") {
                    const c = m.content;
                    if (typeof c === "string") { userText = c; break; }
                    if (Array.isArray(c) && c.length > 0) {
                        for (const part of c) {
                            if (typeof part === "string") { userText = part; break; }
                            if (part && typeof part.text === "string") { userText = part.text; break; }
                        }
                        if (userText) break;
                    }
                    if (c && typeof c === "object" && !Array.isArray(c)) {
                        if (typeof c.text === "string") { userText = c.text; break; }
                        if (typeof c.content === "string") { userText = c.content; break; }
                        const vals = Object.values(c);
                        for (const v of vals) if (typeof v === "string") { userText = v; break; }
                        if (userText) break;
                    }
                    break;
                }
            }
            const trimmed = userText.trim();
            if (!trimmed || trimmed.length < 2) return;
            if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
                try { JSON.parse(trimmed); return; } catch(e) {}
            }

            // ═══ 第二层兜底: assemble 没赶上但 R-CCAM 现在完成了 ═══
            const _ck = "rccam:" + trimmed;
            let _bpCached = null;
            let _bpCk = null;
            const _bpNow = Date.now();
            // 优先精确匹配; 未命中则取最近一条未消费
            if (!_rccamConsumedByAssemble.has(_ck)) {
                _bpCached = _rccamCache.get(_ck);
                _bpCk = _ck;
                if (!_bpCached || _bpNow - _bpCached.ts > RCCAM_CACHE_TTL) {
                    for (const [k, v] of _rccamCache) {
                        if (_rccamConsumedByAssemble.has(k)) continue;
                        if (_bpNow - v.ts > RCCAM_CACHE_TTL) continue;
                        if (!_bpCached || v.ts > _bpCached.ts) { _bpCached = v; _bpCk = k; }
                    }
                }
            }
            if (_bpCached && _bpCk) {
                const _cached = _bpCached;
                    const _cp = _cached.cognitionPayload;
                    if (_cp) {
                        _cp.memories = _cp.memories || _cp.retrieved_memories || [];
                        _cp.dag = _cp.dag || _cp.dag_summaries || [];
                        _cp.reflexion = _cp.reflexion || _cp.reflexion_context || "";
                        _cp.routing = _cp.routing || _cp.routing_debug || _cached.routingDebug || "";
                    }
                    const _hasData = _cp && (
                        (_cp.memories && _cp.memories.length > 0) ||
                        (_cp.dag && _cp.dag.length > 0) ||
                        _cp.rewritten_query ||
                        _cp.flash_summary ||
                        _cp.hub_context ||
                        _cp.reranked_results ||
                        _cp.reflexion ||
                        _cp.skill_guide ||
                        _cp.persona_visual ||
                        _cp.persona_context ||
                        _cp.merged_context ||
                        _cp.self_evolution ||
                        _cp.temporal_kg_extraction ||
                        _cp.spatial_scene ||
                        _cp.causal_context ||
                        _cached.answer ||
                        _cached.routingDebug ||
                        (_cached.phaseLogs && _cached.phaseLogs.length > 0)
                    );
                    if (_hasData) {
                        // 组装完整认知分析
                        const _route = (_cp ? _cp.routing : "") || _cached.routingDebug || "";
                        const _intent = (_cp ? _cp.intent : "") || "unknown";
                        const _confidence = _cached.confidence || 0;
                        let _ctx = "\n[R-CCAM 认知分析]\n";
                        _ctx += "元认知策略: " + (_cached.strategy || _cached.routingDebug || _route || "auto") + "\n";
                        _ctx += "意图: " + _intent + " | 置信度: " + _confidence.toFixed(2) + "\n\n";
                        if (_cp.memories && _cp.memories.length > 0) {
                            _ctx += "[记忆] 共" + _cp.memories.length + "条:\n";
                            _cp.memories.slice(0, 3).forEach(function(m) { if (m && m.length > 5) _ctx += "- " + m.slice(0, 300) + "\n"; });
                            _ctx += "\n";
                        }
                        if (_cp.dag && _cp.dag.length > 0) {
                            _ctx += "[DAG 会话上下文]";
                            _cp.dag.slice(0, 3).forEach(function(d) { if (d && d.length > 5) _ctx += "\n- " + d.slice(0, 300); });
                            _ctx += "\n\n";
                        }
                        if (_cached.answer) _ctx += "[R-CCAM 摘要] " + _cached.answer.slice(0, 800) + "\n\n";
                        _ctx += "[行为指导] 按正常对话流程处理,基于以上认知数据组织回答。\n\n";
                        _ctx += "[R-CCAM] 以上是刚刚完成的认知分析数据,请基于此组织最终回答。\n";
                        _rccamConsumedByAssemble.add(_bpCk);
                        return { appendContext: _ctx.slice(0, 4000) };
                    }
                }

            // ═══ 动态锚定:进化建议 → 强制指令 ═══
            let ctx = "";
            try {
                var _evoTriggers = null;
                var _fs = require("fs");
                var _path = require("path");
                var _evoFile = _path.join(ws, "config", "evolution_triggers.json");
                try {
                    var _raw = _fs.readFileSync(_evoFile, "utf8");
                    _evoTriggers = JSON.parse(_raw);
                } catch(e2) { _evoTriggers = []; }
                if (_evoTriggers && _evoTriggers.length > 0) {
                    var _matched = [];
                    for (var _ei = 0; _ei < _evoTriggers.length; _ei++) {
                        var _entry = _evoTriggers[_ei];
                        var _tw = _entry.trigger_words || [];
                        var _hitCount = 0;
                        for (var _wi = 0; _wi < _tw.length; _wi++) {
                            if (trimmed.indexOf(_tw[_wi]) >= 0) _hitCount++;
                        }
                        if (_hitCount >= 2) {
                            _matched.push(_entry);
                            if (_matched.length >= 3) break;
                        }
                    }
                    if (_matched.length > 0) {
                        ctx += "\n## 强制指令(进化系统匹配触发)\n";
                        for (var _mi = 0; _mi < _matched.length; _mi++) {
                            var _m = _matched[_mi];
                            ctx += (_mi+1) + ". [" + (_m.confidence || "?") + "] " + (_m.suggestion || "") + "\n";
                        }
                        ctx += "\n⚠️ 以上指令优先级高于所有常规回答逻辑。匹配到的进化建议必须执行。\n\n";
                    }
                }
            } catch (e3) {}

            // 记忆验证
            if (ctx) {
                try {
                    const now = Date.now();
                    for (const [k, v] of _rccamCache) if (now - v.ts > RCCAM_CACHE_TTL) _rccamCache.delete(k);
                    const cached = _rccamCache.get("rccam:" + trimmed);
                    const cp = cached && cached.cognitionPayload;
                    if (cached && cp) {
                        const claimsToVerify = [];
                        if (cp.memories && cp.memories.length > 0)
                            cp.memories.slice(0, 2).forEach(m => { if (m && m.length > 20) claimsToVerify.push(m.slice(0, 200)); });
                        if (cp.reranked_results && cp.reranked_results.length > 0)
                            cp.reranked_results.slice(0, 1).forEach(r => { if (r && r.length > 20) claimsToVerify.push(r.slice(0, 200)); });
                        if (claimsToVerify.length > 0) {
                            const w = getWorker(ws);
                            if (w && w.ready) {
                                const verifyResult = await w.call("verify", { claim: claimsToVerify.join(" ") }, 10000);
                                if (verifyResult && verifyResult.result) {
                                    const vr = verifyResult.result;
                                    ctx += "[记忆验证] ";
                                    if (vr.final_confidence !== undefined) ctx += `置信度 ${(vr.final_confidence * 100).toFixed(0)}%`;
                                    if (vr.is_reliable !== undefined) ctx += ` | ${vr.is_reliable ? "数据一致 ✅" : "数据矛盾 ⚠️"}`;
                                    ctx += "\n";
                                }
                            }
                        }
                    }
                } catch (e4) {}
            }

            if (!ctx) return;
            return { appendContext: ctx.slice(0, 2000) };
        } catch (e) {}
    });
    // ==========================================
    // R-CCAM 白盒化: 已合并到 ZMQ SUB 持久重连循环（_zmqPersistentLoop）
    // 不再需要独立 ZMQ SUB — 统一由事件循环处理 rccam_phase + gap 检测 + 自动重连
    // ==========================================

    // ==========================================
    // MemorySlot — GalaxyOS 作为 OpenClaw 标准记忆后端
    // 实现 MemorySearchManager 接口并通过 registerMemoryCapability 注册
    // 使 memory_search / memory_get 工具指向 GalaxyOS 管线
    // ==========================================

    class GalaxyOSMemorySearchManager {
        constructor(workspaceDir, slotsConfig = {}) {
            this._workspaceDir = workspaceDir;
            this._slotsConfig = slotsConfig;
            this._worker = null;
        }

        _getWorker() {
            // Lazy resolve worker reference
            if (!this._worker) {
                try { this._worker = getWorker(ws); } catch { return null; }
            }
            return this._worker;
        }

        async search(query, opts = {}) {
            const worker = this._getWorker();
            if (!worker || !worker.ready) {
                return [];
            }
            try {
                const maxResults = opts.maxResults || 10;
                const minScore = opts.minScore || 0.0;
                const sources = opts.sources || undefined;

                // Resolve extraPaths from config + sources
                const extraPaths = opts.extraPaths || [];
                if (!sources || sources.includes("sessions")) {
                    // Auto-discover session transcript dirs
                    const agentsDir = path.join(path.dirname(ws), "agents");
                    if (existsSync(agentsDir)) {
                        try {
                            const agentDirs = readdirSync(agentsDir, { withFileTypes: true });
                            for (const ad of agentDirs) {
                                if (ad.isDirectory()) {
                                    const sessionsDir = path.join(agentsDir, ad.name, "sessions");
                                    if (existsSync(sessionsDir)) {
                                        extraPaths.push(sessionsDir);
                                    }
                                }
                            }
                        } catch {}
                    }
                }
                // Add any configured extraPaths
                if (this._slotsConfig.extraPaths && Array.isArray(this._slotsConfig.extraPaths)) {
                    for (const ep of this._slotsConfig.extraPaths) {
                        if (!extraPaths.includes(ep)) {
                            extraPaths.push(ep);
                        }
                    }
                }
                const result = await worker.call("memory_search", {
                    query,
                    max_results: maxResults,
                    min_score: minScore,
                    sources,
                    extra_paths: extraPaths.length > 0 ? extraPaths : undefined,
                }, 15000);

                if (result && result.results && Array.isArray(result.results)) {
                    return result.results;
                }
                if (result && result.error) {
                    api.logger.warn?.(`${TAG} [memory-slot] search error: ${result.error}`);
                }
                return [];
            } catch (err) {
                api.logger.warn?.(`${TAG} [memory-slot] search failed: ${err.message}`);
                return [];
            }
        }

        async readFile({ relPath, from, lines }) {
            try {
                const fullPath = path.join(ws, relPath);
                if (!existsSync(fullPath)) {
                    return { content: "", fromLine: from || 1, lineCount: 0 };
                }
                const content = readFileSync(fullPath, "utf-8");
                const allLines = content.split("\n");
                const startLine = Math.max(0, (from || 1) - 1);
                const lineCount = lines ? Math.min(lines, allLines.length - startLine) : allLines.length - startLine;
                const excerpt = allLines.slice(startLine, startLine + lineCount).join("\n");
                return {
                    content: excerpt,
                    fromLine: startLine + 1,
                    lineCount,
                };
            } catch (err) {
                api.logger.warn?.(`${TAG} [memory-slot] readFile failed: ${err.message}`);
                return { content: "", fromLine: from || 1, lineCount: 0, error: err.message };
            }
        }

        async status() {
            const worker = this._getWorker();
            const workerOk = worker && worker.ready;
            let workerStatus = null;
            if (workerOk) {
                try {
                    workerStatus = await worker.call("memory_status", {}, 5000);
                } catch {}
            }
            return {
                backend: "galaxyos",
                provider: workerOk ? (workerStatus?.provider || "galaxyos-neural") : "unavailable",
                model: workerStatus?.model || "galaxyos-crag-pipeline",
                fallback: "disabled",
                workspaceDir: this._workspaceDir,
                workerReady: workerOk,
                workerPid: workerStatus?.pid || null,
                workerUptime: workerStatus?.uptime_s || null,
                embedding: workerStatus?.embedding || {},
                indexStats: workerStatus?.index_stats || {},
                slotsConfig: {
                    extraPaths: this._slotsConfig.extraPaths || [],
                    dagEnabled: this._slotsConfig.dag?.enabled !== false,
                    verifiedEnabled: this._slotsConfig.verified?.enabled !== false,
                    neuralEnabled: this._slotsConfig.neural?.enabled !== false,
                },
            };
        }

        async close() {
            this._worker = null;
        }

        async sync(params) {
            // GalaxyOS has its own ingest pipeline — no-op here
        }
    }

    const memSlotsConfig = pluginConfig.memorySlots || {};

    api.registerMemoryCapability({
        runtime: {
            getMemorySearchManager: async () => {
                const manager = new GalaxyOSMemorySearchManager(ws, memSlotsConfig);
                return { manager };
            },
            resolveMemoryBackendConfig: () => ({
                backend: "builtin",
            }),
            closeAllMemorySearchManagers: async () => {
                // GalaxyOS workers manage their own lifecycle
            },
        },
        promptBuilder: ({ availableTools }) => {
            const lines = ["## Memory Recall"];
            if (availableTools.has("memory_search")) {
                lines.push("Before answering about prior work, decisions, dates, people, preferences, or todos: run memory_search on MEMORY.md + memory/*.md + indexed session transcripts; then use memory_get to pull only the needed lines. If low confidence after search, say you checked.");
            } else if (availableTools.has("claw_recall")) {
                lines.push("Before answering about prior work, decisions, dates, people, preferences, or todos: run claw_recall to search the GalaxyOS neural memory pipeline.");
            } else {
                lines.push("Memory search is unavailable — answer based on available context.");
            }
            lines.push("");
            return lines;
        },
        flushPlanResolver: ({ cfg, nowMs }) => {
            const date = new Date(nowMs || Date.now());
            const y = date.getFullYear();
            const m = String(date.getMonth() + 1).padStart(2, "0");
            const d = String(date.getDate()).padStart(2, "0");
            const dateStamp = `${y}-${m}-${d}`;
            return {
                softThresholdTokens: 4000,
                forceFlushTranscriptBytes: 2097152,
                reserveTokensFloor: 20000,
                prompt: [
                    "Pre-compaction memory flush.",
                    `Store durable memories only in memory/${dateStamp}.md (create memory/ if needed).`,
                    "If memory/YYYY-MM-DD.md already exists, APPEND new content only and do not overwrite existing entries.",
                    "Treat workspace bootstrap/reference files such as MEMORY.md, DREAMS.md, SOUL.md, TOOLS.md, and AGENTS.md as read-only during this flush; never overwrite, replace, or edit them.",
                    "Do NOT create timestamped variant files (e.g., YYYY-MM-DD-HHMM.md); always use the canonical YYYY-MM-DD.md filename.",
                    "",
                ].join(" "),
                systemPrompt: "Pre-compaction memory flush turn. The session is near auto-compaction; capture durable memories to disk. " +
                    `Store durable memories only in memory/${dateStamp}.md. ` +
                    "If memory/YYYY-MM-DD.md already exists, APPEND new content only.",
                relativePath: `memory/${dateStamp}.md`,
            };
        },
        publicArtifacts: {
            async listArtifacts(params) {
                const artifacts = [];
                // MEMORY.md
                const memPath = path.join(ws, "MEMORY.md");
                if (existsSync(memPath)) {
                    artifacts.push({
                        kind: "memory-root",
                        workspaceDir: ws,
                        relativePath: "MEMORY.md",
                        absolutePath: memPath,
                        agentIds: ["main"],
                        contentType: "markdown",
                    });
                }
                // memory/*.md files
                const memoryDir = path.join(ws, "memory");
                if (existsSync(memoryDir)) {
                    try {
                        const walkDir = (dir) => {
                            const entries = readdirSync(dir, { withFileTypes: true });
                            for (const entry of entries) {
                                if (entry.isDirectory()) {
                                    walkDir(path.join(dir, entry.name));
                                } else if (entry.name.endsWith(".md")) {
                                    const absPath = path.join(dir, entry.name);
                                    const relPath = path.relative(ws, absPath).replace(/\\/g, "/");
                                    artifacts.push({
                                        kind: relPath.startsWith("memory/dreaming/") ? "dream-report" : "daily-note",
                                        workspaceDir: ws,
                                        relativePath: relPath,
                                        absolutePath: absPath,
                                        agentIds: ["main"],
                                        contentType: "markdown",
                                    });
                                }
                            }
                        };
                        walkDir(memoryDir);
                    } catch {}
                }
                return artifacts;
            }
        },
    });

    api.logger.info?.(`${TAG} v4 plugin registration complete: 6 tools + 4 hooks + context-engine + memory-slot + public-artifacts + rccam-pipeline + 通信自发现 (worker=${workerEnabled ? "enabled" : "disabled"})`);
}
