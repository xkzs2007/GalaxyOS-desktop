/**
 * worker-pool.js — GalaxyOS WorkerPool standalone (extracted from plugin)
 *
 * Architecture:
 *   Electron main → WorkerPool → ClawWorkerClient(UDS/HTTP or TCP) → claw_worker.py
 *
 * Features:
 *   - 3-tier elastic scaling (Hot/Warm/Cold)
 *   - Session affinity routing
 *   - HTTP over UDS (Unix) + HTTP over TCP (Windows)
 *   - Circuit breaker per worker
 *   - 59 methods exposed via execute(method, params)
 *
 * Windows TCP support:
 *   On win32, the worker writes its TCP port to a .port file.
 *   We probe that file first, then connect via http.request({ hostname, port }).
 */

/* eslint-disable no-undef */
// Use CJS require style (compatible with esbuild)
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { createConnection } from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import http from 'node:http';

const TAG = '[worker-pool]';
// __dirname: use CJS built-in when available (esbuild cjs bundle),
// fallback to import.meta.url for ESM runtime (dev).
const __dirname = typeof __dirname !== 'undefined'
  ? __dirname
  : path.dirname(new URL('.', import.meta.url).pathname);
const IS_WIN = process.platform === 'win32';

// ── Path resolution ──────────────────────────────────────────────

function resolveHome() {
  if (process.env.OPENCLAW_HOME) return process.env.OPENCLAW_HOME;
  // Desktop mode: use ~/.galaxyos/ or fallback to ~/.openclaw/
  const home = os.homedir();
  const galaxyHome = path.join(home, '.galaxyos');
  if (fs.existsSync(galaxyHome)) return galaxyHome;
  return path.join(home, '.openclaw');
}

const HOME = resolveHome();
const VAR_DIR = path.join(HOME, 'extensions', 'galaxyos', 'var');
const DEFAULT_WORKER_SCRIPT = path.join(
  __dirname, '..', '..', '..', 'extensions', 'galaxyos', 'scripts', 'claw_worker.py'
);

function resolvePythonBin() {
  if (process.env.GALAXYOS_PYTHON && fs.existsSync(process.env.GALAXYOS_PYTHON)) {
    return process.env.GALAXYOS_PYTHON;
  }
  if (IS_WIN) {
    for (const ver of ['312', '311', '310']) {
      for (const base of [`C:\\Program Files\\Python${ver}\\python.exe`, `C:\\Python${ver}\\python.exe`, `${process.env.LOCALAPPDATA}\\Programs\\Python\\Python${ver}\\python.exe`]) {
        if (fs.existsSync(base)) return base;
      }
    }
    return 'python';
  }
  return 'python3';
}

const _pythonBin = resolvePythonBin();

// ── UDS / TCP path helpers ────────────────────────────────────────

function getUdsPath() {
  if (process.env.GALAXYOS_UDS_PATH && fs.existsSync(path.dirname(process.env.GALAXYOS_UDS_PATH))) {
    return process.env.GALAXYOS_UDS_PATH;
  }
  return path.join(VAR_DIR, 'claw-worker.sock');
}

function getUdsProbePaths() {
  const paths = [getUdsPath()];
  paths.push(path.join(VAR_DIR, 'claw-worker-worker-1.sock'));
  paths.push(path.join(VAR_DIR, 'claw-worker-worker-2.sock'));
  return [...new Set(paths)];
}

function getPortFilePath(workerId) {
  return path.join(VAR_DIR, `claw-worker-${(workerId || 'default').replace(':', '-')}.port`);
}

// ── UDS connection pool ───────────────────────────────────────────

const _udsHttpAgent = new http.Agent({
  keepAlive: true,
  keepAliveMsecs: 30000,
  maxSockets: 8,
  maxFreeSockets: 4,
  timeout: 60000,
});

const _workerAgents = new Map();
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

// ── Mmap reader (large payload dereference) ───────────────────────

function _readWorkerMmap(key) {
  const mmapPath = path.join(VAR_DIR, 'claw_worker_mmap');
  try {
    if (!fs.existsSync(mmapPath)) return null;
    const fd = fs.openSync(mmapPath, 'r');
    try {
      const header = Buffer.alloc(4);
      if (fs.readSync(fd, header, 0, 4, 0) < 4) return null;
      const len = header.readUInt32BE(0);
      if (len < 10 || len > 10 * 1024 * 1024) return null;
      const body = Buffer.alloc(len);
      if (fs.readSync(fd, body, 0, len, 4) < len) return null;
      const data = JSON.parse(body.toString('utf-8'));
      return data?.[key] ?? null;
    } finally {
      try { fs.closeSync(fd); } catch { /* */ }
    }
  } catch {
    return null;
  }
}

// ── ClawWorkerClient — single worker connection ───────────────────

class ClawWorkerClient {
  constructor(workspace, id, workerScript) {
    this.id = id || 'worker:default';
    this.workspace = workspace;
    this._workerScript = workerScript || DEFAULT_WORKER_SCRIPT;
    this.client = null;
    this.pending = new Map();
    this._nextId = 1;
    this._ready = false;
    this._fails = 0;
    this._maxFails = 5;
    this._shutdown = false;
    this._startPromise = null;
    this._udsPath = (this.id && this.id !== 'worker:default')
      ? path.join(VAR_DIR, `claw-worker-${this.id.replace(':', '-')}.sock`)
      : getUdsPath();
    this._tcpPort = null;    // Windows TCP port (read from .port file)
    this._tcpHost = '127.0.0.1';
    this._mode = null;        // 'uds-http' | 'tcp-http' | 'stdin'
  }

  // ── Start ──────────────────────────────────────────────────────

  async start() {
    if (this._startPromise) return this._startPromise;
    if (this._ready) return;
    this._shutdown = false;
    this._startPromise = this._doStart();
    try { await this._startPromise; } finally { this._startPromise = null; }
  }

  async _doStart() {
    this._cleanup();
    await this._probeOrSpawn();
  }

  // ── Probe or spawn ──────────────────────────────────────────────

  async _probeOrSpawn() {
    // Windows: try TCP first (read .port file)
    if (IS_WIN) {
      const port = this._readPortFile();
      if (port) {
        try {
          this._tcpPort = port;
          await this._connectTcp();
          process.stderr.write(`${TAG} [TCP] connected to ${this.id} on port ${port}\n`);
          return;
        } catch (e) {
          process.stderr.write(`${TAG} [TCP] probe port ${port} failed: ${e.message}\n`);
          this._tcpPort = null;
        }
      }
    }

    // Unix: probe UDS paths
    if (!IS_WIN) {
      const probePaths = this.id.includes(':') ? [this._udsPath] : getUdsProbePaths();
      for (const p of probePaths) {
        try {
          this._udsPath = p;
          await this._connectUds();
          process.stderr.write(`${TAG} [UDS] connected to ${this.id} at ${p}\n`);
          return;
        } catch (e) {
          process.stderr.write(`${TAG} [UDS] probe ${p}: ${e.message}\n`);
        }
      }
    }

    // Fallback: spawn process
    process.stderr.write(`${TAG} spawning worker ${this.id}...\n`);
    return new Promise((resolve, reject) => this._spawn(resolve, reject));
  }

  _readPortFile() {
    const portPath = getPortFilePath(this.id);
    try {
      if (!fs.existsSync(portPath)) return null;
      const stat = fs.statSync(portPath);
      if (Date.now() - stat.mtimeMs > 30000) return null; // stale
      const port = parseInt(fs.readFileSync(portPath, 'utf-8').trim(), 10);
      if (port > 0 && port < 65536) return port;
    } catch { /* */ }
    return null;
  }

  // ── Windows TCP connect ─────────────────────────────────────────

  async _connectTcp() {
    return new Promise((resolve, reject) => {
      const sock = createConnection({ host: this._tcpHost, port: this._tcpPort }, () => {
        sock.write('POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n');
        let resp = '';
        sock.on('data', (d) => {
          resp += d.toString();
          if (resp.includes('HTTP/1.1') || resp.includes('HTTP/1.0')) {
            this._mode = 'tcp-http';
            sock.destroy();
            this._ready = true;
            this._fails = 0;
            resolve();
          }
        });
        setTimeout(() => {
          sock.destroy();
          reject(new Error('TCP probe: no HTTP response'));
        }, 3000);
      });
      sock.on('error', (err) => reject(err));
      setTimeout(() => reject(new Error('TCP connect timeout')), 3000);
    });
  }

  // ── Unix UDS connect ────────────────────────────────────────────

  async _connectUds(sockPath) {
    const targetPath = sockPath || this._udsPath;
    return new Promise((resolve, reject) => {
      const sock = createConnection(targetPath, () => {
        sock.write('POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n');
        let resp = '';
        sock.on('data', (d) => {
          resp += d.toString();
          if (resp.includes('HTTP/1.1') || resp.includes('HTTP/1.0')) {
            this._mode = 'uds-http';
            sock.destroy();
            this._ready = true;
            this._fails = 0;
            resolve();
          }
        });
        setTimeout(() => {
          sock.destroy();
          reject(new Error('UDS probe: no HTTP response'));
        }, 3000);
      });
      sock.on('error', (err) => reject(err));
      setTimeout(() => reject(new Error('UDS connect timeout')), 3000);
    });
  }

  // ── Spawn fallback ──────────────────────────────────────────────

  _spawn(resolve, reject) {
    try {
      this._cleanup();
      const galaxosVarDir = path.dirname(getUdsPath());
      this.proc = spawn(_pythonBin, [this._workerScript], {
        cwd: this.workspace,
        env: {
          ...process.env,
          PYTHONIOENCODING: 'utf-8',
          OPENCLAW_WORKSPACE: this.workspace,
          WORKER_UDS: IS_WIN ? '0' : '1',
          WORKER_TCP: IS_WIN ? '1' : '0',
          WORKER_ID: this.id,
          WORKER_TIER: (this.id || '').split(':')[0],
          GALAXYOS_VAR_DIR: galaxosVarDir,
        },
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      });
      let settled = false;
      const settle = (fn, arg) => { if (!settled) { settled = true; fn(arg); } };
      const timeout = setTimeout(() => settle(reject, new Error('Worker start timeout (10s)')), 10000);

      this.proc.on('exit', (code, signal) => {
        this._ready = false;
        clearTimeout(timeout);
        settle(reject, new Error(`Worker exited (code=${code}, signal=${signal})`));
      });
      this.proc.on('error', (err) => {
        clearTimeout(timeout);
        settle(reject, new Error(`Worker spawn error: ${err.message}`));
      });
      this.proc.stderr.on('data', (data) => {
        const text = data.toString().trim();
        if (text) process.stderr.write(`${TAG} [${this.id}] ${text}\n`);
      });

      this._rl = createInterface({ input: this.proc.stdout, crlfDelay: Infinity });
      this._rl.on('line', (line) => {
        if (!line.trim()) return;
        try {
          const msg = JSON.parse(line.trim());
          if (msg.event === 'ready') {
            clearTimeout(timeout);
            this._ready = true;
            this._fails = 0;
            this._mode = 'stdin';
            process.stderr.write(`${TAG} [spawn] Worker ${this.id} ready (pid=${msg.pid}), stdin RPC\n`);
            settle(resolve, undefined);
          }
          if (msg.id !== undefined && msg.id !== null) {
            const resolver = this.pending.get(msg.id);
            if (resolver) { this.pending.delete(msg.id); resolver(msg); }
          }
        } catch { /* ignore parse errors */ }
      });
    } catch (e) {
      reject(new Error(`Worker spawn failed: ${e.message}`));
    }
  }

  // ── RPC call ────────────────────────────────────────────────────

  async call(method, params = {}, timeoutMs = 30000) {
    if (!this._ready) await this.start();
    if (this._mode === 'uds-http' || this._mode === 'tcp-http') {
      return this._httpCall(method, params, timeoutMs);
    }
    return this._stdinCall(method, params, timeoutMs);
  }

  async _httpCall(method, params, timeoutMs) {
    const id = this._nextId++;
    const body = JSON.stringify({ id, method, params });

    return new Promise((resolve, reject) => {
      const reqOpts = this._mode === 'tcp-http'
        ? {
            hostname: this._tcpHost,
            port: this._tcpPort,
            path: '/',
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Content-Length': Buffer.byteLength(body),
              'Connection': 'keep-alive',
            },
            timeout: timeoutMs,
          }
        : {
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
          };

      const req = http.request(reqOpts, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const msg = JSON.parse(data);
            if (msg.event === 'ready') { this._ready = true; this._fails = 0; }
            if (msg.error) {
              reject(new Error(typeof msg.error === 'string' ? msg.error : JSON.stringify(msg.error)));
            } else {
              let result = msg.result;
              if (result?._mmap_key) {
                const mmapData = _readWorkerMmap(result._mmap_key);
                if (mmapData !== null) result = mmapData;
              }
              resolve(result);
            }
          } catch (e) {
            reject(new Error(`HTTP parse: ${e.message}`));
          }
        });
      });

      req.on('error', (err) => { this._mode = null; reject(err); });
      req.on('timeout', () => { req.destroy(); reject(new Error(`HTTP timeout: ${method}`)); });
      req.write(body);
      req.end();
    });
  }

  async _stdinCall(method, params, timeoutMs) {
    const id = this._nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`Worker call timeout: ${method}`));
      }, timeoutMs);
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
          this.pending.delete(id);
          clearTimeout(timer);
          reject(new Error('Worker not connected'));
        }
      } catch (e) {
        this.pending.delete(id);
        clearTimeout(timer);
        reject(new Error(`Worker send failed: ${e.message}`));
      }
    });
  }

  // ── Lifecycle ──────────────────────────────────────────────────

  async ping() {
    try { return await this.call('ping', {}, 5000); }
    catch (e) { return { ok: false, error: e.message }; }
  }

  stop() {
    this._shutdown = true;
    try { this.call('shutdown', {}, 1000).catch(() => {}); } catch { /* */ }
    setTimeout(() => { this._cleanup(); }, 1500);
  }

  _cleanup() {
    if (this.client) { try { this.client.destroy(); } catch { /* */ } this.client = null; }
    if (this._rl) { try { this._rl.close(); } catch { /* */ } this._rl = null; }
    if (this.proc) {
      try { this.proc.stdin?.end(); this.proc.kill('SIGTERM'); } catch { /* */ }
      setTimeout(() => { try { this.proc?.kill('SIGKILL'); } catch { /* */ } }, 2000);
      this.proc = null;
    }
    const agent = _workerAgents.get(this.id);
    if (agent) { try { agent.destroy(); } catch { /* */ } _workerAgents.delete(this.id); }
    this._ready = false;
    for (const [, resolver] of this.pending) {
      resolver({ id: -1, error: 'Worker stopped' });
    }
    this.pending.clear();
  }
}

// ── WorkerPool — elastic scaling ──────────────────────────────────

class WorkerPool {
  constructor(ws, cfg = {}) {
    this.ws = ws;
    this.minSize = cfg.minSize || 2;
    this.maxSize = cfg.maxSize || 8;
    this.size = Math.max(this.minSize, cfg.size || 2);
    this.maxQueue = cfg.maxQueue || 20;
    this.workerIdPrefix = cfg.workerIdPrefix || 'worker';
    this.defaultTimeout = cfg.defaultTimeout || 30000;
    this._workerScript = cfg.workerScript || DEFAULT_WORKER_SCRIPT;
    this.workers = new Map();
    this.busy = new Set();
    this.queue = [];
    this._healthTimer = null;
    this._scaleTimer = null;
    this._scaleCooldown = false;
    this._scaleHistory = [];
    this._ready = false;
  }

  _init() {
    for (let i = 0; i < this.size; i++) {
      this._spawnOne(`${this.workerIdPrefix}:${i + 1}`);
    }
    this._healthTimer = setInterval(() => this._healthCheck(), 10000);
    this._scaleTimer = setInterval(() => this._scaleCheck(), 15000);
    if (this._healthTimer.unref) this._healthTimer.unref();
    if (this._scaleTimer.unref) this._scaleTimer.unref();
    this._ready = true;
  }

  _spawnOne(id) {
    if (this.workers.has(id)) return;
    const w = new ClawWorkerClient(this.ws, id, this._workerScript);
    w.start().catch((e) => {
      process.stderr.write(`${TAG} pool spawn ${id} failed: ${e.message}\n`);
    });
    this.workers.set(id, w);
  }

  _getIdleWorker() {
    let bestId = null, bestScore = -Infinity;
    for (const [id, w] of this.workers) {
      if (!w._ready || this.busy.has(id)) continue;
      const fails = w._fails || 0;
      const latency = w._lastLatencyMs || 100;
      const ageMs = Date.now() - (w._lastActiveTs || 0);
      const score = -fails * 100 - latency / 10 + Math.min(ageMs / 1000, 30);
      if (score > bestScore) { bestScore = score; bestId = id; }
    }
    return bestId;
  }

  _getOtherWorker(id) {
    for (const [oid, w] of this.workers) {
      if (oid !== id && w._ready && !this.busy.has(oid)) return oid;
    }
    return null;
  }

  _trackLatency(workerId, ms) {
    const w = this.workers.get(workerId);
    if (!w) return;
    w._lastLatencyMs = ms;
    w._lastActiveTs = Date.now();
  }

  _scaleCheck() {
    const total = this.workers.size;
    const busy = this.busy.size;
    const queueLen = this.queue.length;

    this._scaleHistory.push({ ts: Date.now(), queueLen, busy, total });
    if (this._scaleHistory.length > 20) this._scaleHistory.shift();

    if ((queueLen >= 3 || busy >= total) && total < this.maxSize && !this._scaleCooldown) {
      const add = Math.min(2, this.maxSize - total);
      for (let i = 0; i < add; i++) {
        const newId = `${this.workerIdPrefix}:${total + i + 1}`;
        if (!this.workers.has(newId)) {
          process.stderr.write(`${TAG} ▲ SCALE UP: +${newId} (q=${queueLen}, busy=${busy}/${total})\n`);
          this._spawnOne(newId);
        }
      }
      this._scaleCooldown = true;
      setTimeout(() => { this._scaleCooldown = false; }, 30000);
      return;
    }

    if (this._scaleHistory.length >= 3 && total > this.minSize) {
      const last3 = this._scaleHistory.slice(-3);
      if (last3.every(p => p.busy === 0 && p.queueLen === 0 && p.total > this.minSize)) {
        let victimId = null;
        for (const [id, w] of this.workers) {
          if (!this.busy.has(id) && w._ready) { victimId = id; break; }
        }
        if (victimId) {
          process.stderr.write(`${TAG} ▼ SCALE DOWN: -${victimId} (${total}→${total - 1})\n`);
          const w = this.workers.get(victimId);
          this.workers.delete(victimId);
          this.busy.delete(victimId);
          if (w) { w.call('shutdown', {}, 2000).catch(() => {}).finally(() => w.stop()); }
        }
      }
    }
  }

  async execute(method, params = {}, priority = 'normal', timeoutMs) {
    if (timeoutMs === undefined) timeoutMs = this.defaultTimeout;

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
      const entry = {
        priority,
        run: () => this.execute(method, params, priority, timeoutMs).then(resolve, reject),
        ts: Date.now(),
      };
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
          process.stderr.write(`${TAG} ${workerId} retry exhausted for ${method}: ${e.message}\n`);
          throw e;
        }
        const otherId = this._getOtherWorker(workerId);
        if (!otherId) throw e;
        const otherW = this.workers.get(otherId);
        if (!otherW) throw e;
        process.stderr.write(`${TAG} ${workerId} timeout retry → ${otherId} for ${method}\n`);
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

  _healthCheck() {
    for (const [id, w] of this.workers) {
      if (!w._ready) continue;
      w.ping().catch(() => {
        w._fails = (w._fails || 0) + 1;
      });
    }
  }

  getStatus() {
    return {
      ready: this._ready,
      total: this.workers.size,
      busy: this.busy.size,
      queue: this.queue.length,
      workers: [...this.workers.entries()].map(([id, w]) => ({
        id,
        ready: w._ready,
        fails: w._fails || 0,
        mode: w._mode,
        lastLatencyMs: w._lastLatencyMs || 0,
      })),
    };
  }

  async shutdown() {
    this._ready = false;
    if (this._healthTimer) { clearInterval(this._healthTimer); this._healthTimer = null; }
    if (this._scaleTimer) { clearInterval(this._scaleTimer); this._scaleTimer = null; }
    const stops = [];
    for (const [, w] of this.workers) stops.push(w.stop());
    await Promise.allSettled(stops);
    this.workers.clear();
    this.busy.clear();
    this.queue.length = 0;
  }
}

// ── Method → Tier routing table ───────────────────────────────────

const METHOD_TIER = {
  ping: 'hot', health: 'hot', memory_search: 'hot', recall: 'hot',
  vector_info: 'hot', mmap_cleanup: 'hot', memory_status: 'hot',

  store: 'warm', save_memory: 'warm', verify: 'warm',
  dag_ingest: 'warm', dag_assemble: 'warm', dag_compact: 'warm',
  dag_search: 'warm', dag_status: 'warm', dag_summary: 'warm',
  dag_clear_session: 'warm', learn: 'warm', learn_preference: 'warm',
  learn_correction: 'warm', remember: 'warm', forget: 'warm',
  get_entity: 'warm', link_task_memory: 'warm',
  understand_image: 'warm', ocr_image: 'warm', recall_images: 'warm',
  persona_snapshot: 'warm', get_persona_core: 'warm',
  implicit_feedback: 'warm', hardinfo: 'warm',
  list_workflows: 'warm', list_modules: 'warm', get_workflow_info: 'warm',
  smart_retrieval: 'warm', build_system_prompt: 'warm',

  rccam: 'cold', context_assemble: 'cold', rlm_compress: 'cold',
  cognitive_compress_dag: 'cold', rccam_compact_needed: 'cold',
  rccam_compact_cycle: 'cold', expand_rccam_cycle: 'cold',
  rccam_dag_stats: 'cold', smart_process: 'cold',
  execute_workflow: 'cold', verify_reply_style: 'cold',
  restore_context: 'cold', answer: 'cold', call_module: 'cold',
  get_module_info: 'cold',
};

const TIER_CONFIG = {
  hot:  { minSize: 1, maxSize: 2, size: 1, maxQueue: 20, defaultTimeout: 8000,  workerIdPrefix: 'hot' },
  warm: { minSize: 1, maxSize: 2, size: 1, maxQueue: 10, defaultTimeout: 20000, workerIdPrefix: 'warm' },
  cold: { minSize: 1, maxSize: 1, size: 1, maxQueue: 5,  defaultTimeout: 60000, workerIdPrefix: 'cold' },
};

// ── GalaxyPool — multi-tier pool manager ──────────────────────────

class GalaxyPool {
  constructor(workspace, cfg = {}) {
    this._workspace = workspace;
    this._tierPools = new Map();
    this._ready = false;
    this._workerScript = cfg.workerScript || process.env.GALAXYOS_WORKER_SCRIPT || DEFAULT_WORKER_SCRIPT;
  }

  get pool() {
    return this._tierPools.get('warm');
  }

  async start() {
    const tiers = ['hot', 'warm', 'cold'];
    for (const tier of tiers) {
      const tierCfg = { ...TIER_CONFIG[tier], workerScript: this._workerScript };
      const pool = new WorkerPool(this._workspace, tierCfg);
      pool._init();
      this._tierPools.set(tier, pool);
      process.stderr.write(`${TAG} tier ${tier} pool started (${tierCfg.size} workers)\n`);
    }
    this._ready = true;
  }

  /**
   * Execute a method call routing through the appropriate tier pool.
   */
  async execute(method, params = {}, timeoutMs) {
    if (!this._ready) throw new Error('GalaxyPool not ready');

    const tier = METHOD_TIER[method] || 'warm';
    const pool = this._tierPools.get(tier);
    if (!pool) throw new Error(`No pool for tier: ${tier}`);

    const actualTimeout = timeoutMs || TIER_CONFIG[tier].defaultTimeout;
    return pool.execute(method, params, 'normal', actualTimeout);
  }

  getStatus() {
    const tiers = {};
    for (const [tier, pool] of this._tierPools) {
      tiers[tier] = pool.getStatus();
    }
    return { ready: this._ready, tiers };
  }

  async shutdown() {
    this._ready = false;
    for (const [, pool] of this._tierPools) {
      await pool.shutdown();
    }
    this._tierPools.clear();
  }
}

// ── Exports ───────────────────────────────────────────────────────

export { ClawWorkerClient, WorkerPool, GalaxyPool, TIER_CONFIG, METHOD_TIER };
