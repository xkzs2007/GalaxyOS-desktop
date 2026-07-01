// src/main.ts — Electron main process for GalaxyOS Desktop.
//
// Stage 5: real desktop packaging. This file:
//   1. Spawns the Python sidecar as a child process (and waits for
//      its zmq REP socket to come up).
//   2. Opens an Electron BrowserWindow that loads the renderer.
//   3. **Intercepts** all renderer→sidecar requests via a custom
//      protocol://sidecar/ scheme. The renderer's `fetch()` calls
//      to /sse/* are rewritten to protocol://sidecar/* and routed
//      through the zmq REP socket (no HTTP port needed).
//   4. Dynamically injects the @jboltai/tokui UMD bundle.
//   5. Cleans up the sidecar process on app quit.
//
// In production (electron-builder NSIS), the sidecar is bundled as
// `extraResources` so the app is self-contained — no Python, no
// port conflicts, no firewall issues.

import { app, BrowserWindow, ipcMain, shell, dialog, protocol, net } from 'electron';
import { spawn, ChildProcess } from 'node:child_process';
import { resolve, dirname, join, basename, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, readFileSync, writeFileSync, mkdirSync, appendFileSync } from 'node:fs';
import * as http from 'node:http';
import * as zmq from 'zeromq';

// ── Logging (file + stdout) — declared FIRST, before any path
//   resolution, so we can debug path issues during startup. ────────
//
// LOG_FILE location matters a lot:
//   - Dev (npm run dev / electron .): userData (per-OS standard
//     location) is fine and survives multiple runs.
//   - Packaged: writing to process.cwd() or APP_ROOT often fails
//     on Windows (Program Files is not writable) and is unclear
//     on Linux AppImage (cwd is the launcher's dir, not the
//     mount). We use `app.getPath('userData')` which is the
//     per-app standard data dir on every platform.
//
// `app` is imported at the top of the file but its API requires
// `app.whenReady()` for some methods; getPath('userData') is
// actually safe to call synchronously at import time.
const LOG_FILE = process.env.GALAXYOS_LOG_FILE
  || (() => {
    try {
      return join(app.getPath('userData'), 'electron.log');
    } catch {
      return join(process.cwd(), 'electron.log');
    }
  })();
function log(msg: string): void {
  const line = `[main ${new Date().toISOString()}] ${msg}\n`;
  try { appendFileSync(LOG_FILE, line); } catch { /* ignore */ }
  try { process.stdout.write(line); } catch { /* ignore */ }
  try { process.stderr.write(line); } catch { /* ignore */ }
}
// Always write to stderr first so we see something even if the file write fails
process.stderr.write(`[main] === GalaxyOS Electron main starting ===\n`);
process.stderr.write(`[main] cwd=${process.cwd()}\n`);
process.stderr.write(`[main] argv=${JSON.stringify(process.argv)}\n`);
process.stderr.write(`[main] LOG_FILE=${LOG_FILE}\n`);
try {
  writeFileSync(LOG_FILE, `=== GalaxyOS Electron main started at ${new Date().toISOString()}\ncwd=${process.cwd()}\nargv=${JSON.stringify(process.argv)}\n`);
  log('log file init OK');
} catch (e) {
  process.stderr.write(`LOG FILE WRITE FAILED: ${(e as Error).message}\n`);
  process.stderr.write(`  stack: ${(e as Error).stack}\n`);
  process.stderr.write(`  LOG_FILE path: ${LOG_FILE}\n`);
  // Don't throw — let the rest of the program try
}
log(`=== GalaxyOS Electron main started, cwd=${process.cwd()}, argv=${JSON.stringify(process.argv.slice(1))} ===`);

// APP_ROOT resolution.
// Three cases (in order of preference):
//   1. `app.getAppPath()` — works for both `electron .` (dev) and
//      packaged builds.  Returns:
//        - dev:        the directory containing package.json
//        - packaged:   <resources>/app/  (electron-builder default)
//   2. `process.resourcesPath` for sidecar lookups (only meaningful
//      in packaged builds; equals APP_ROOT's grandparent in AppImage
//      but `<install_root>/resources/` on Windows NSIS).
//   3. `process.cwd()` as a last-resort fallback (only reliable if
//      the user launched the binary with `.` as the CWD).
//
// We DO NOT use `process.cwd()` as the primary source because:
//   - On Windows NSIS installs to "C:\Program Files\GalaxyOS\", the
//     Start Menu shortcut does not chdir into the install dir —
//     `process.cwd()` is whatever the shell was in (often
//     `C:\Windows\System32`).
//   - On Linux AppImage, `process.cwd()` is the directory the user
//     launched the AppImage from, NOT the mount point.
//
// `app.getAppPath()` is the canonical, Electron-recommended way.
// In packaged builds, electron-builder puts the app under
// `<resources>/app/` (often inside `app.asar`); in dev it returns
// the directory containing package.json. We additionally fall back
// to `__dirname/..` because esbuild bundles main.ts into
// `dist/main.cjs` — `__dirname` in that bundle is the `dist/`
// folder of the source tree, whose parent is `desktop-shell/`.
const _APP_ROOT_FROM_GET = (() => {
  try { return app.getAppPath(); } catch { return ''; }
})();
const _APP_ROOT_FROM_DIR = (() => {
  // esbuild CJS bundle: __dirname = <desktop-shell>/dist
  try { return resolve(__dirname, '..'); } catch { return ''; }
})();
// In packaged builds, process.cwd() is unreliable (NSIS Start Menu
// shortcut cwd = C:\Windows\System32; Linux AppImage cwd = launcher's
// dir, not the mount). Only fall back to cwd in DEV — packaged builds
// must derive APP_ROOT from getAppPath() / __dirname/.. or fail loudly.
const APP_ROOT = _APP_ROOT_FROM_GET
              || _APP_ROOT_FROM_DIR
              || (app.isPackaged ? '' : process.cwd());
if (!APP_ROOT) {
  throw new Error(
    `Cannot resolve APP_ROOT in packaged build. ` +
    `getAppPath()=${_APP_ROOT_FROM_GET} __dirname-based=${_APP_ROOT_FROM_DIR}`
  );
}
const PYTHON_DIR = resolve(APP_ROOT, 'python');
const SIDECAR_SCRIPT = resolve(PYTHON_DIR, 'galaxyos_sidecar.py');
const RENDERER_HTML = resolve(APP_ROOT, 'renderer', 'index.html');
// TokUI paths. In packaged builds the file lives at APP_ROOT/node_modules/@jboltai/tokui/dist/
// (we explicitly add it to build.files + asarUnpack in package.json).
// In dev mode it lives at the same path after `npm install`; we fall
// back to ./renderer/vendor/tokui.umd.js if neither exists (production
// installs without devDependencies will hit this fallback).
const TOKUI_DIST_JS = resolve(APP_ROOT, 'node_modules', '@jboltai', 'tokui', 'dist', 'tokui.umd.js');
const TOKUI_DIST_CSS = resolve(APP_ROOT, 'node_modules', '@jboltai', 'tokui', 'dist', 'tokui.css');

const SIDECAR_PORT = Number(process.env.GALAXYOS_SIDECAR_PORT ?? 5757);
const SIDECAR_HTTP_PORT = Number(process.env.GALAXYOS_SIDECAR_HTTP_PORT ?? 5758);
const SIDECAR_PUB_PORT = Number(process.env.GALAXYOS_SIDECAR_PUB_PORT ?? 5759);
const SIDECAR_HOST = process.env.GALAXYOS_SIDECAR_HOST ?? '127.0.0.1';

// Where to bundle the Python sidecar for packaged builds.
// electron-builder config (package.json) puts the PyInstaller binary
// into `process.resourcesPath/` via `extraResources.to: "."`, so the
// layout is:
//   Windows NSIS:  C:\Program Files\GalaxyOS\resources\galaxyos-sidecar.exe
//   Linux AppImage: <mount>/resources/galaxyos-sidecar
//   macOS (future): GalaxyOS.app/Contents/Resources/galaxyos-sidecar
const RESOURCES_DIR = app.isPackaged && process.resourcesPath
  ? process.resourcesPath
  : APP_ROOT;
const PACKAGED_SIDECAR_SOURCE = join(RESOURCES_DIR, 'python', 'galaxyos_sidecar.py');

// Log the resolved paths so users can debug "where is it looking"
// issues from the electron.log / sidecar.log files.
log(`APP_ROOT              = ${APP_ROOT}`);
log(`PYTHON_DIR            = ${PYTHON_DIR}`);
log(`RENDERER_HTML         = ${RENDERER_HTML}  (exists=${existsSync(RENDERER_HTML)})`);
log(`TOKUI_DIST_JS         = ${TOKUI_DIST_JS}  (exists=${existsSync(TOKUI_DIST_JS)})`);
log(`TOKUI_DIST_CSS        = ${TOKUI_DIST_CSS}  (exists=${existsSync(TOKUI_DIST_CSS)})`);
log(`app.isPackaged        = ${app.isPackaged}`);
log(`process.resourcesPath = ${process.resourcesPath ?? '(unset)'}`);
log(`RESOURCES_DIR         = ${RESOURCES_DIR}`);
log(`PACKAGED_SIDECAR_SOURCE= ${PACKAGED_SIDECAR_SOURCE}  (exists=${existsSync(PACKAGED_SIDECAR_SOURCE)})`);

function resolveSidecarPath(): string {
  // Both dev and packaged: run the Python source directly with the
  // system Python interpreter.  This is the canonical path now that
  // the NSIS installer handles `pip install -r requirements.txt`
  // during setup.
  //
  // Layout:
  //   Dev:  <repo>/desktop-shell/python/galaxyos_sidecar.py
  //   Pkg:  <install>/resources/python/galaxyos_sidecar.py
  //
  // We ALWAYS resolve to the .py source.  isPackagedExe (below)
  // will be false, and spawn() will use the Python interpreter.
  //
  // As a fallback, we also check for a legacy PyInstaller-frozen
  // binary (from pre-v0.2.0 installs) so existing users are not
  // stranded on upgrade.

  // Primary: Python source (dev + new packaged)
  const sourceCandidates = app.isPackaged
    ? [join(RESOURCES_DIR, 'python', 'galaxyos_sidecar.py')]
    : [SIDECAR_SCRIPT];

  for (const p of sourceCandidates) {
    if (existsSync(p)) {
      log(`sidecar resolved (source): ${p}`);
      return p;
    }
  }

  // Legacy fallback: PyInstaller-frozen binary (pre-v0.2.0 packaged builds)
  if (app.isPackaged) {
    const isWin = process.platform === 'win32';
    const binaryCandidates = isWin
      ? [
          join(RESOURCES_DIR, 'galaxyos-sidecar.exe'),
          join(RESOURCES_DIR, 'python', 'galaxyos-sidecar.exe'),
        ]
      : [
          join(RESOURCES_DIR, 'galaxyos-sidecar'),
          join(RESOURCES_DIR, 'python', 'galaxyos-sidecar'),
        ];
    for (const p of binaryCandidates) {
      if (existsSync(p)) {
        log(`sidecar resolved (legacy frozen binary): ${p}`);
        return p;
      }
    }
  }

  throw new Error(
    `GalaxyOS sidecar not found.\n` +
    `  Looked for source: ${sourceCandidates.join(', ')}\n` +
    (app.isPackaged
      ? `  Looked for legacy binary: ${[
          join(RESOURCES_DIR, 'galaxyos-sidecar.exe'),
          join(RESOURCES_DIR, 'python', 'galaxyos-sidecar.exe'),
          join(RESOURCES_DIR, 'galaxyos-sidecar'),
          join(RESOURCES_DIR, 'python', 'galaxyos-sidecar'),
        ].join(', ')}\n`
      : '') +
    `  RESOURCES_DIR: ${RESOURCES_DIR}\n` +
    `  isPackaged: ${app.isPackaged}\n` +
    `  process.resourcesPath: ${process.resourcesPath ?? '(unset)'}\n` +
    `  APP_ROOT: ${APP_ROOT}\n` +
    `  This is a packaging bug — the app bundle is missing the Python sidecar.\n` +
    `  Please report it with the contents of: ${LOG_FILE}\n`
  );
}

function resolvePythonInterpreter(): string {
  // Honour explicit override first (env var or config)
  if (process.env.GALAXYOS_PYTHON) {
    const p = process.env.GALAXYOS_PYTHON;
    if (existsSync(p)) return p;
    log(`GALAXYOS_PYTHON set but not found: ${p}, falling back to auto-detect`);
  }

  // In packaged builds, the sidecar is a .py source file, so we need
  // a real Python interpreter.  Search in priority order.
  const isWin = process.platform === 'win32';

  // Dev mode: check PATH first
  if (!app.isPackaged) {
    // On Windows the path may include the venv; just return the
    // generic name and let the OS resolve it via PATHEXT / PATH.
    return isWin ? 'python' : 'python3';
  }

  // Packaged: search explicitly
  if (isWin) {
    // Windows: search registry + common paths
    const winCandidates: string[] = [];
    // Python.org installs
    for (const ver of ['312', '311', '310']) {
      winCandidates.push(
        `C:\\Program Files\\Python${ver}\\python.exe`,
        `C:\\Python${ver}\\python.exe`,
        `${process.env.LOCALAPPDATA}\\Programs\\Python\\Python${ver}\\python.exe`,
      );
    }
    winCandidates.push('python.exe', 'python3.exe');
    for (const p of winCandidates) {
      if (existsSync(p)) {
        log(`Python resolved (packaged): ${p}`);
        return p;
      }
    }
  } else {
    // Linux / macOS
    const candidates = ['python3.12', 'python3.11', 'python3.10', 'python3', 'python'];
    for (const name of candidates) {
      try {
        const which = require('node:child_process')
          .execFileSync('which', [name], { encoding: 'utf-8' })
          .trim();
        if (which && existsSync(which)) {
          log(`Python resolved (packaged): ${which}`);
          return which;
        }
      } catch { /* continue */ }
    }
  }

  // Last resort
  log('WARNING: Could not auto-detect Python; falling back to "python3"');
  return isWin ? 'python' : 'python3';
}

// ── Sidecar lifecycle + zmq REQ client ────────────────────────────
let sidecar: ChildProcess | null = null;
let sidecarReady = false;
let zmqReq: zmq.Request | null = null;
let zmqLock: Promise<unknown> = Promise.resolve();

/**
 * Send a request to the sidecar via the zmq REQ/REP socket and
 * return the parsed response. Uses a lock to serialize calls
 * (zmq REQ is a strict request/response pattern — only one
 * outstanding request at a time).
 */
async function zmqCall(method: string, params: Record<string, unknown> = {}): Promise<any> {
  if (!zmqReq) throw new Error('sidecar zmq not ready');
  // Serialize via a lock chain
  const release = zmqLock;
  let unlock: (value?: unknown) => void = () => {};
  zmqLock = new Promise((r) => { unlock = r; });
  await release;
  try {
    const id = ++zmqCallId;
    await zmqReq.send(JSON.stringify({ id, method, params }));
    const [reply] = await zmqReq.receive();
    const parsed = JSON.parse(reply.toString());
    if (parsed.error) throw new Error(parsed.error);
    return parsed.result;
  } finally {
    unlock();
  }
}
let zmqCallId = 0;

function waitForSidecar(timeoutMs = 30000): Promise<void> {
  const start = Date.now();
  return new Promise((resolveP, rejectP) => {
    const tick = () => {
      const req = http.request({
        host: SIDECAR_HOST,
        port: SIDECAR_HTTP_PORT,
        method: 'POST',
        path: '/sse/health',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      }, (res) => {
        if (res.statusCode === 200) {
          sidecarReady = true;
          log(`sidecar ready (took ${Date.now() - start}ms)`);
          resolveP();
        } else {
          setTimeout(tick, 200);
        }
      });
      req.on('error', () => setTimeout(tick, 200));
      req.end();
    };
    tick();
    setTimeout(() => {
      if (!sidecarReady) rejectP(new Error(`sidecar did not respond within ${timeoutMs}ms`));
    }, timeoutMs);
  });
}

function startSidecar(): Promise<void> {
  return new Promise(async (resolveP, rejectP) => {
    const py = resolvePythonInterpreter();
    const script = resolveSidecarPath();
    if (!existsSync(script)) {
      // Don't pretend the user needs to install Python — they're
      // running a packaged app, the sidecar binary is supposed to
      // be bundled. Tell them EXACTLY which path we expected and
      // where the log went, so they can file a useful bug.
      rejectP(new Error(
        `GalaxyOS sidecar binary not found.\n` +
        `  Expected: ${script}\n` +
        `  APP_ROOT: ${APP_ROOT}\n` +
        `  isPackaged: ${app.isPackaged}\n` +
        `  process.resourcesPath: ${process.resourcesPath ?? '(unset)'}\n` +
        (app.isPackaged
          ? `  This is a packaging bug — the .exe / AppImage is missing the bundled Python sidecar.\n` +
            `  Please report it with the contents of:\n` +
            `    ${LOG_FILE}\n`
          : `  In dev mode, the sidecar source is at desktop-shell/python/galaxyos_sidecar.py.\n` +
            `  If you removed or moved it, restore the source tree.\n`)
      ));
      return;
    }

    // isPackagedExe: true only for legacy PyInstaller-frozen binaries.
    // New source-based packaged installs ship .py files, so the sidecar
    // runs via `python galaxyos_sidecar.py` (not as a standalone .exe).
    // Dev mode always uses the Python interpreter directly.
    const isPackagedExe = app.isPackaged && !script.endsWith('.py');
    const cmd = isPackagedExe ? script : py;
    const args = isPackagedExe ? [] : [script];

    // 关掉重开应用时，旧 sidecar 可能因 TIME_WAIT 或 Windows
    // TerminateProcess 强杀未释放端口。先探测 5757 端口是否被
    // 占用，最多等 5 秒。比直接 "Address in use" 错误友好。
    await waitForPortFree(SIDECAR_PORT, SIDECAR_HOST, 5000);

    // Redirect sidecar stdout/stderr to a file (EPIPE avoidance).
    // The log file lives in the per-user userData dir (NOT in
    // Program Files, which is not user-writable on Windows).
    // Users can find both logs at the same path when filing bugs.
    const SIDECAR_LOG = process.env.GALAXYOS_SIDECAR_LOG
      || (() => {
        try {
          return join(app.getPath('userData'), 'sidecar.log');
        } catch {
          return join(RESOURCES_DIR, 'sidecar.log');
        }
      })();
    let sidecarOut: number | null = null;
    let sidecarErr: number | null = null;
    try {
      // Ensure the log file's parent dir exists (AppImage / Program
      // Files installs may not have a writable cwd at first).
      const logDir = dirname(SIDECAR_LOG);
      if (!existsSync(logDir)) {
        try { mkdirSync(logDir, { recursive: true }); } catch { /* best effort */ }
      }
      sidecarOut = require('fs').openSync(SIDECAR_LOG, 'a');
      sidecarErr = require('fs').openSync(SIDECAR_LOG, 'a');
    } catch (e) {
      // If we can't open the log, fall back to inherited stdio so
      // the user at least sees something in the terminal.
      log(`Failed to open sidecar log file ${SIDECAR_LOG}: ${(e as Error).message}`);
    }

    log(`Starting sidecar: ${cmd} ${args.join(' ')}`);
    log(`Sidecar stdout/stderr → ${SIDECAR_LOG}`);
    log(`isPackagedExe=${isPackagedExe}  py=${py}  script=${script}`);

    try {
      // Build PYTHONPATH for the sidecar so it can find galaxyos.engine
      // and its sibling modules (llm_providers, tokui_dsl, etc.).
      const pathParts: string[] = [];
      if (process.env.PYTHONPATH) pathParts.push(process.env.PYTHONPATH);

      if (app.isPackaged) {
        // Packaged: galaxyos/ is at resources/galaxyos/
        pathParts.push(join(RESOURCES_DIR, 'galaxyos'));
        pathParts.push(join(RESOURCES_DIR, 'python'));
      } else {
        pathParts.push(PYTHON_DIR);
        // Dev: galaxyos package is at repo root
        const repoRoot = resolve(APP_ROOT, '..');
        pathParts.push(repoRoot);
      }

      const pythonPath = pathParts.join(process.platform === 'win32' ? ';' : ':');

      sidecar = spawn(cmd, args, {
        env: {
          ...process.env,
          PYTHONPATH: pythonPath,
          GALAXYOS_SIDECAR_PORT: String(SIDECAR_PORT),
          GALAXYOS_SIDECAR_HTTP_PORT: String(SIDECAR_HTTP_PORT),
          GALAXYOS_SIDECAR_HOST: SIDECAR_HOST,
          GALAXYOS_SIDECAR_LOG: SIDECAR_LOG,
          GALAXYOS_DISABLE_HTTP: '1',
          // LFM_SERVER_BIN: tell sidecar where the Rust lfm_server
          // binary lives. electron-builder puts it at
          // <resources>/lfm_server[.exe] (see package.json
          // build.extraResources). In dev mode we don't set this
          // (sidecar falls back to scanning the cargo target/
          // release/ directory or skips lfm_server entirely).
          ...(app.isPackaged ? {
            LFM_SERVER_BIN: process.platform === 'win32'
              ? join(RESOURCES_DIR, 'lfm_server.exe')
              : join(RESOURCES_DIR, 'lfm_server'),
          } : {}),
        },
        detached: process.platform !== 'win32',
        stdio: sidecarOut !== null && sidecarErr !== null
          ? ['ignore', sidecarOut, sidecarErr]
          : 'inherit',
        windowsHide: true,
      });
    } catch (e) {
      rejectP(new Error(`Failed to spawn sidecar at ${cmd}: ${(e as Error).message}`));
      return;
    } finally {
      if (sidecarOut !== null) { try { require('fs').closeSync(sidecarOut); } catch { /* */ } }
      if (sidecarErr !== null) { try { require('fs').closeSync(sidecarErr); } catch { /* */ } }
    }

    sidecar.on('exit', (code, signal) => {
      log(`Sidecar exited with code=${code} signal=${signal}`);
    });
    sidecar.on('error', (e) => {
      log(`Sidecar spawn error: ${e.message}`);
    });

    try {
      await waitForZmq(30000);
      sidecarReady = true;
      log('Sidecar zmq REP ready');
      resolveP();
    } catch (e) {
      rejectP(new Error(
        `Sidecar did not become ready within 30s.\n` +
        `  Binary:  ${cmd}\n` +
        `  Logs:    ${SIDECAR_LOG}\n` +
        `  Cause:   ${(e as Error).message}\n` +
        `  This usually means the sidecar crashed on startup (missing\n` +
        `  dependency, blocked by antivirus, or a packaging issue).\n` +
        `  Check the log file above for the Python traceback.`
      ));
    }
  });
}

async function waitForZmq(timeoutMs: number): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      // Try to open a temporary REQ socket and ping
      const tmp = new zmq.Request();
      tmp.connect(`tcp://${SIDECAR_HOST}:${SIDECAR_PORT}`);
      tmp.receiveTimeout = 1000;
      await tmp.send(JSON.stringify({ id: 0, method: 'ping', params: {} }));
      const [reply] = await tmp.receive();
      tmp.close();
      // Save the working socket for later use
      zmqReq = new zmq.Request();
      zmqReq.connect(`tcp://${SIDECAR_HOST}:${SIDECAR_PORT}`);
      zmqReq.receiveTimeout = 30000;
      return;
    } catch (e) {
      // Not ready yet, retry
      try { zmqReq?.close(); } catch { /* */ }
      zmqReq = null;
      await new Promise((r) => setTimeout(r, 200));
    }
  }
  throw new Error(`sidecar zmq did not respond within ${timeoutMs}ms`);
}

function stopSidecar(): void {
  if (sidecar) {
    log('Stopping sidecar...');
    try {
      sidecar.kill('SIGTERM');
    } catch { /* ignore */ }
    sidecar = null;
  }
  try { zmqReq?.close(); } catch { /* ignore */ }
  zmqReq = null;
  stopPubSubscriber();
  sidecarReady = false;
}

// ── zmq PUB/SUB subscriber (streaming progress events) ─────────────
// Subscribes to the sidecar's PUB socket on port ZMQ_PUB_PORT
// (default 5759) and forwards matching events to the renderer via
// webContents.send(). Used by install_wizard to push live download
// progress to the UI without blocking the zmq REP request/response
// channel.
let zmqSub: zmq.Subscriber | null = null;
let mainWindowRef: BrowserWindow | null = null;

function startPubSubscriber(win: BrowserWindow): void {
  if (zmqSub) return;  // already running
  mainWindowRef = win;
  try {
    zmqSub = new zmq.Subscriber();
    zmqSub.connect(`tcp://${SIDECAR_HOST}:${SIDECAR_PUB_PORT}`);
    // Subscribe to all topics (empty prefix = receive everything).
    // We filter by topic prefix in the message handler.
    zmqSub.subscribe();
    log(`zmq SUB connected to tcp://${SIDECAR_HOST}:${SIDECAR_PUB_PORT} (all topics)`);

    // Pump loop: read multipart [topic, body] and forward to renderer.
    // Runs in the background; never rejects (errors are logged + the
    // loop continues so transient zmq hiccups don't kill progress).
    (async () => {
      while (zmqSub) {
        try {
          const [topic, body] = await zmqSub.receive();
          const topicStr = topic.toString();
          const payload = JSON.parse(body.toString());
          // Forward to renderer. The renderer's preload exposes
          // ipcRenderer.on('iw:progress', ...) for install_wizard
          // events (topic 'iw:').
          if (topicStr.startsWith('iw:')) {
            mainWindowRef?.webContents.send('iw:progress', payload);
          } else if (topicStr.startsWith('think:')) {
            mainWindowRef?.webContents.send('think:step', payload);
          } else if (topicStr.startsWith('memo:')) {
            mainWindowRef?.webContents.send('memo:stage', payload);
          } else if (topicStr.startsWith('plan:')) {
            mainWindowRef?.webContents.send('plan:step', payload);
          } else if (topicStr.startsWith('agent:')) {
            mainWindowRef?.webContents.send('agent:tool', payload);
          } else if (topicStr.startsWith('dsl:')) {
            // v9.6: real-time DSL fragment streaming — the sidecar
            // publishes each DSL fragment as it's generated, so the
            // renderer can render progressively instead of waiting
            // for the full REP response.
            mainWindowRef?.webContents.send('dsl:fragment', payload);
          } else if (topicStr.startsWith('stream:')) {
            // Stream lifecycle events (start/done/error)
            mainWindowRef?.webContents.send('stream:event', payload);
          }
        } catch (e) {
          // EAGAIN / closed socket — break out if socket is gone
          if (!zmqSub) break;
          // Otherwise log + continue (transient)
          log(`zmq SUB receive error (non-fatal): ${(e as Error).message}`);
        }
      }
    })();
  } catch (e) {
    log(`Failed to start zmq SUB: ${(e as Error).message} — progress events disabled`);
    zmqSub = null;
  }
}

function stopPubSubscriber(): void {
  if (zmqSub) {
    try { zmqSub.close(); } catch { /* */ }
    zmqSub = null;
    log('zmq SUB closed');
  }
  mainWindowRef = null;
}

// ── 端口检测：检查 zmq REP 端口是否被占用 ──────────────────────────
// 关掉重开应用时，旧 sidecar 进程可能因 TIME_WAIT 或未完全释放
// 而继续占用 5757 端口。spawn 新 sidecar 前先检测端口，必要时
// 等 1-2 秒后重试，避免 "Address already in use" 错误。
async function waitForPortFree(port: number, host: string, timeoutMs = 5000): Promise<void> {
  const { createConnection } = require('net') as typeof import('net');
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const inUse = await new Promise<boolean>((resolve) => {
      const conn = createConnection({ port, host }, () => {
        // 连上了 = 端口被占用
        conn.destroy();
        resolve(true);
      });
      conn.on('error', () => {
        // 连接被拒绝 = 端口空闲
        resolve(false);
      });
      setTimeout(() => {
        conn.destroy();
        resolve(false);
      }, 300);
    });
    if (!inUse) return;
    log(`Port ${port} still busy, waiting...`);
    await new Promise((r) => setTimeout(r, 500));
  }
  // 超时也不阻塞，让后续 spawn 自然失败
  log(`Port ${port} still busy after ${timeoutMs}ms timeout, proceeding anyway`);
}

// ── Window ──────────────────────────────────────────────────────────
function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 880,
    minHeight: 540,
    title: 'GalaxyOS Desktop',
    backgroundColor: '#0f1115',
    icon: existsSync(resolve(APP_ROOT, 'renderer', 'icon.png'))
      ? resolve(APP_ROOT, 'renderer', 'icon.png')
      : undefined,
    webPreferences: {
      preload: resolve(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  // Load the renderer. Two modes:
  //   1. file://  (the renderer is a local file) — production
  //   2. http://localhost:8080  (dev server) — dev only
  const useDevServer = process.env.GALAXYOS_DEV_HTTP === '1';
  if (useDevServer) {
    log('Loading renderer from http://127.0.0.1:8080 (dev)');
    win.loadURL('http://127.0.0.1:8080/index.html').catch((e) => log(`loadURL failed: ${e}`));
  } else {
    log(`Loading renderer from file://${RENDERER_HTML}`);
    win.loadFile(RENDERER_HTML).catch((e) => log(`loadFile failed: ${e}`));
  }

  // Inject TokUI UMD after the page loads
  win.webContents.once('did-finish-load', () => {
    log('did-finish-load; injecting TokUI UMD');
    injectTokUI(win);

    // Dev mode: auto-open DevTools for renderer debugging
    if (process.env.GALAXYOS_DEV === '1' || process.env.GALAXYOS_DEV_HTTP === '1') {
      win.webContents.openDevTools({ mode: 'detach' });
      log('DevTools opened (GALAXYOS_DEV=1)');
    }
  });

  win.on('closed', () => stopSidecar());

  // Hide the default menu in production
  if (!process.env.GALAXYOS_SHOW_MENU) {
    win.setMenuBarVisibility(false);
  }

  // Start the zmq PUB/SUB subscriber so streaming progress events
  // (think-chain, install_wizard, etc.) flow into the renderer.
  startPubSubscriber(win);

  return win;
}

/**
 * Inject the @jboltai/tokui UMD bundle into the renderer so the
 * page works without the user having run `npm install` of the
 * renderer-side dep.
 */
async function injectTokUI(win: BrowserWindow): Promise<void> {
  // Inject the CSS via a <link> tag
  if (existsSync(TOKUI_DIST_CSS)) {
    const cssUrl = `file:///${TOKUI_DIST_CSS.replace(/\\/g, '/')}`;
    try {
      await win.webContents.executeJavaScript(`
        (() => {
          if (document.querySelector('link[data-tokui-css]')) return;
          const l = document.createElement('link');
          l.rel = 'stylesheet';
          l.href = ${JSON.stringify(cssUrl)};
          l.setAttribute('data-tokui-css', '1');
          document.head.appendChild(l);
        })();
      `);
      log('TokUI CSS injected');
    } catch (e) {
      log(`TokUI CSS injection failed: ${(e as Error).message}`);
    }
  } else {
    log(`TokUI CSS not found at ${TOKUI_DIST_CSS}; renderer will use stub`);
  }

  // Inject the UMD JS via a <script> tag in the page itself
  // (more reliable than executeJavaScript, which can fail on
  // complex UMD bundles because the script runs in the page's
  // isolated world).
  if (existsSync(TOKUI_DIST_JS)) {
    try {
      const code = readFileSync(TOKUI_DIST_JS, 'utf-8');
      // Use executeJavaScript at document_start (early in page load)
      // and wrap the UMD in an IIFE that exposes window.TokUI
      const wrapped = `
        (function() {
          try {
            ${code}
            if (typeof TokUI !== 'undefined') {
              window.TokUI = TokUI;
              window.__TOKUI_INJECTED__ = true;
            } else {
              window.__TOKUI_INJECTED__ = false;
            }
          } catch (e) {
            window.__TOKUI_INJECTED_ERROR__ = String(e);
          }
        })();
      `;
      await win.webContents.executeJavaScript(wrapped, true);
      const injected = await win.webContents.executeJavaScript(
        'JSON.stringify({ok: !!window.__TOKUI_INJECTED__, err: window.__TOKUI_INJECTED_ERROR__ || null})',
      );
      log(`TokUI UMD injection: ${injected}`);
    } catch (e) {
      log(`TokUI injection failed: ${(e as Error).message}`);
    }
  } else {
    log(`TokUI UMD not found at ${TOKUI_DIST_JS}; renderer will use stub`);
  }
}

// ── IPC handlers (renderer → main → sidecar via zmq) ──────────────
// All renderer→sidecar calls go through these ipcMain.handle
// functions, which then call zmqCall() to reach the sidecar. This
// keeps everything inside the app — no HTTP port needed in
// packaged builds.

function registerIpc() {
  ipcMain.handle('galaxy:health', async () => {
    try { return await zmqCall('health'); }
    catch (e) { return { error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:ask', async (_e, question: string, sessionId?: string, streamId?: string) => {
    try { return await zmqCall('stream_ask', { prompt: question, session_id: sessionId || '', stream_id: streamId || '' }); }
    catch (e) { return { events: [], fragments: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:process', async (_e, userInput: string, sessionId?: string, streamId?: string) => {
    try { return await zmqCall('stream_process', { user_input: userInput, session_id: sessionId || '', stream_id: streamId || '' }); }
    catch (e) { return { events: [], fragments: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:agent', async (_e, prompt: string, sessionId?: string, streamId?: string) => {
    try { return await zmqCall('stream_agent', { prompt, session_id: sessionId || '', stream_id: streamId || '' }); }
    catch (e) { return { events: [], fragments: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:memo', async (_e, prompt: string, sessionId?: string, streamId?: string) => {
    try { return await zmqCall('stream_memo', { prompt, session_id: sessionId || '', stream_id: streamId || '' }); }
    catch (e) { return { events: [], fragments: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:plan', async (_e, prompt: string, sessionId?: string, streamId?: string) => {
    try { return await zmqCall('stream_plan', { prompt, session_id: sessionId || '', stream_id: streamId || '' }); }
    catch (e) { return { events: [], fragments: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:ocr', async (_e, params: { path?: string; base64?: string; prompt?: string; sessionId?: string }) => {
    try { return await zmqCall('stream_ocr', { path: params.path || '', base64: params.base64 || '', prompt: params.prompt || '', session_id: params.sessionId || '' }); }
    catch (e) { return { events: [], fragments: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:remember', async (_e, content: string, metadata?: any) => {
    try { return await zmqCall('remember', { content, metadata: metadata || {}, source: 'user' }); }
    catch (e) { return { memory_id: '', error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:recall', async (_e, query: string, topK?: number, sessionId?: string) => {
    try { return await zmqCall('recall', { query, top_k: topK || 10, session_id: sessionId || '' }); }
    catch (e) { return { results: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:openExternal', async (_e, url: string) => {
    await shell.openExternal(url);
  });
  ipcMain.handle('galaxy:skills', async () => {
    try { return await zmqCall('list_skills'); }
    catch (e) { return { skills: [], count: 0, error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:skill', async (_e, skillId: string) => {
    try { return await zmqCall('get_skill', { id: skillId }); }
    catch (e) { return { error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:updateSettings', async (_e, settings: Record<string, string>) => {
    try { return await zmqCall('set_config', settings); }
    catch (e) { return { ok: false, error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:listProviders', async () => {
    try { return await zmqCall('list_providers'); }
    catch (e) { return { providers: [], router: {}, error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:fetchModels', async (_e, params: { provider: string; api_key?: string; base_url?: string }) => {
    try { return await zmqCall('fetch_models', params); }
    catch (e) { return { ok: false, error: String((e as Error).message), source: 'curated' }; }
  });
  ipcMain.handle('galaxy:heartbeat', async () => {
    try { return await zmqCall('heartbeat'); }
    catch (e) { return { ok: false, error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:stats', async () => {
    try { return await zmqCall('stats'); }
    catch (e) { return { ok: false, error: String((e as Error).message) }; }
  });
  // T17: upstream tool wrappers
  ipcMain.handle('galaxy:verify', async (_e, claim: string) => {
    try { return await zmqCall('claw_verify', { claim }); }
    catch (e) { return { error: String((e as Error).message) }; }
  });
  // NOTE: 'galaxy:recall' is already registered above (line ~607) calling
  // the sidecar's `recall` method. The previous block here tried to
  // register a SECOND 'galaxy:recall' that called `claw_recall`, which
  // threw "Attempted to register a second handler for 'galaxy:recall'"
  // at app startup, killing the whole app even though the sidecar was
  // already running. Renamed to galaxy:clawRecall so the OpenClaw
  // worker-pool recall is still callable from the renderer under a
  // distinct name.
  ipcMain.handle('galaxy:clawRecall', async (_e, query: string, topK?: number) => {
    try { return await zmqCall('claw_recall', { query, top_k: topK || 5 }); }
    catch (e) { return { results: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:saveMemory', async (_e, content: string, metadata?: any) => {
    try { return await zmqCall('claw_save_memory', { content, metadata }); }
    catch (e) { return { memory_id: '', error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:emitEvent', async (_e, type: string, payload?: any) => {
    try { return await zmqCall('emit_event', { type, payload: payload || {} }); }
    catch (e) { return { ok: false, error: String((e as Error).message) }; }
  });
  // T13: SkillGraph
  ipcMain.handle('galaxy:graphSearch', async (_e, query: string, topK?: number) => {
    try { return await zmqCall('graph_search', { query, top_k: topK || 5 }); }
    catch (e) { return { results: [], error: String((e as Error).message) }; }
  });
  ipcMain.handle('galaxy:skillNeighbors', async (_e, name: string) => {
    try { return await zmqCall('get_skill_neighbors', { name }); }
    catch (e) { return { error: String((e as Error).message) }; }
  });
  // Install wizard: run install_wizard.py with given args, stream
  // progress via 'iw:progress' events to renderer (handled by
  // startPubSubscriber), return final result when done.
  // Long downloads block the zmq REP thread for several minutes —
  // that's fine because the REP server is on a background thread
  // and the PUB stream keeps the UI updated with live progress.
  ipcMain.handle('galaxy:installWizard', async (_e, args: string[], _timeout?: number) => {
    try {
      return await zmqCall('install_wizard', {
        args: args || [],
        timeout: _timeout || 1800,
      });
    } catch (e) {
      return {
        ok: false,
        exit_code: -3,
        stdout: '',
        stderr: `main.ts zmqCall failed: ${(e as Error).message}`,
        duration_s: 0,
        args: args || [],
      };
    }
  });

  // ── First-launch setup IPC ──────────────────────────────────────
  // Marker file path: ~/AppData/Roaming/GalaxyOS/.setup-complete (win)
  //                   ~/.config/GalaxyOS/.setup-complete        (linux)
  //                   ~/Library/Application Support/GalaxyOS/.setup-complete (mac)
  const _setupMarker = (() => {
    try {
      return join(app.getPath('userData'), '.setup-complete');
    } catch { return join(APP_ROOT, '.setup-complete'); }
  })();

  ipcMain.handle('galaxy:isFirstLaunch', async () => {
    const isFirst = !existsSync(_setupMarker);
    log(`isFirstLaunch: ${isFirst} (marker: ${_setupMarker})`);
    return { isFirstLaunch: isFirst };
  });

  ipcMain.handle('galaxy:completeSetup', async () => {
    try {
      const dir = dirname(_setupMarker);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      writeFileSync(_setupMarker, new Date().toISOString());
      log(`Setup marker written: ${_setupMarker}`);
      return { ok: true };
    } catch (e) {
      log(`Failed to write setup marker: ${(e as Error).message}`);
      return { ok: false, error: (e as Error).message };
    }
  });

  // Restart sidecar: stop the current one, wait, start a fresh one.
  // Used after dependency installation so the new sidecar picks up
  // freshly pip-installed packages.
  ipcMain.handle('galaxy:restartSidecar', async () => {
    log('Restarting sidecar...');
    stopSidecar();
    // Wait for the old zmq socket to fully release
    await new Promise((r) => setTimeout(r, 1500));
    try {
      await startSidecar();
      log('Sidecar restarted OK');
      return { ok: true };
    } catch (e) {
      log(`Sidecar restart failed: ${(e as Error).message}`);
      return { ok: false, error: (e as Error).message };
    }
  });

  // ── API schema (introspection for the renderer / future codegen) ────
  // Returns a JSON description of every IPC channel this app exposes.
  // The renderer can fetch this once at startup to:
  //   1. runtime-validate args/returns (zod / valibot on the renderer)
  //   2. auto-generate TypeScript types (offline build step)
  //   3. show a "what RPCs are available" debug panel
  //
  // Keep this list in sync with the ipcMain.handle() calls above.
  // The schema is intentionally hand-maintained — it's small (15 channels)
  // and explicit beats generated for this size.
  const API_SCHEMA = {
    version: '1.0',
    generated_at: new Date().toISOString(),
    transport: 'ipc',
    channels: [
      { name: 'galaxy:health',           args: [],                              returns: 'HealthReport' },
      { name: 'galaxy:ask',              args: ['question: string', 'sessionId?: string'],                  returns: 'StreamResult' },
      { name: 'galaxy:process',          args: ['userInput: string', 'sessionId?: string'],                 returns: 'StreamResult' },
      { name: 'galaxy:agent',            args: ['prompt: string', 'sessionId?: string'],                    returns: 'StreamResult' },
      { name: 'galaxy:memo',             args: ['prompt: string', 'sessionId?: string'],                    returns: 'StreamResult' },
      { name: 'galaxy:plan',             args: ['prompt: string', 'sessionId?: string'],                    returns: 'StreamResult' },
      { name: 'galaxy:ocr',              args: ['params: {path?, base64?, prompt?, sessionId?}'],           returns: 'StreamResult' },
      { name: 'galaxy:remember',         args: ['content: string', 'metadata?: object', 'source?: string'], returns: 'RememberResult' },
      { name: 'galaxy:recall',           args: ['query: string', 'topK?: number', 'sessionId?: string'],   returns: 'RecallResult' },
      { name: 'galaxy:skills',           args: [],                              returns: 'SkillsList' },
      { name: 'galaxy:skill',            args: ['skillId: string'],              returns: 'SkillDetail' },
      { name: 'galaxy:updateSettings',   args: ['settings: Record<string,string>'], returns: 'UpdateSettingsResult' },
      { name: 'galaxy:heartbeat',        args: [],                              returns: 'Heartbeat' },
      { name: 'galaxy:stats',            args: [],                              returns: 'Stats' },
      { name: 'galaxy:verify',           args: ['claim: string'],               returns: 'VerifyResult' },
      { name: 'galaxy:clawRecall',       args: ['query: string', 'topK?: number'], returns: 'RecallResult' },
      { name: 'galaxy:saveMemory',       args: ['content: string', 'metadata?: object'], returns: 'SaveMemoryResult' },
      { name: 'galaxy:emitEvent',        args: ['type: string', 'payload?: any'], returns: 'EmitEventResult' },
      { name: 'galaxy:graphSearch',      args: ['query: string', 'topK?: number'], returns: 'GraphSearchResult' },
      { name: 'galaxy:skillNeighbors',   args: ['name: string'],                returns: 'SkillNeighbors' },
      { name: 'galaxy:installWizard',    args: ['args: string[]', 'timeout?: number'], returns: 'IwResult' },
      { name: 'galaxy:isFirstLaunch',   args: [],                              returns: '{isFirstLaunch: boolean}' },
      { name: 'galaxy:completeSetup',   args: [],                              returns: '{ok: boolean, error?: string}' },
      { name: 'galaxy:restartSidecar',  args: [],                              returns: '{ok: boolean, error?: string}' },
      { name: 'galaxy:openExternal',     args: ['url: string'],                 returns: 'void' },
    ],
    types: {
      HealthReport:        { zmq_port: 'number', sse_port: 'number', stage: 'string', memo: 'string', router: 'string', skills: 'number' },
      StreamResult:        { events: 'string[]', fragments: 'string[]', error: 'string?' },
      RememberResult:      { memory_id: 'string' },
      RecallResult:        { results: 'any[]', error: 'string?' },
      SkillsList:          { skills: 'Skill[]', count: 'number' },
      SkillDetail:         { id: 'string', name: 'string', description: 'string', body: 'string' },
      Skill:               { id: 'string', name: 'string', description: 'string' },
      UpdateSettingsResult:{ ok: 'boolean', updated: 'string[]' },
      Heartbeat:           { ok: 'boolean', ts_ms: 'number', uptime_s: 'number' },
      Stats:               { /* open */ },
      VerifyResult:        { claim: 'string', confidence: 'number', verdict: 'string', evidence_count: 'number', top_evidence: 'string[]' },
      SaveMemoryResult:    { memory_id: 'string', ok: 'boolean' },
      EmitEventResult:     { ok: 'boolean', received: 'string' },
      GraphSearchResult:   { count: 'number', results: 'any[]' },
      SkillNeighbors:      { name: 'string', successors: 'any[]', predecessors: 'any[]' },
      IwResult:            { ok: 'boolean', exit_code: 'number', stdout: 'string', stderr: 'string', duration_s: 'number', args: 'string[]', error: 'string?' },
    },
  };
  ipcMain.handle('galaxy:schema', async () => API_SCHEMA);

  // ── P0: 记忆管理完整闭环 ──
  ipcMain.handle('galaxy:forget', async (_e, memoryId: string) => {
    return await zmqCall('forget', { memory_id: memoryId });
  });
  ipcMain.handle('galaxy:getEntity', async (_e, entityName: string) => {
    return await zmqCall('get_entity', { entity_name: entityName });
  });
  ipcMain.handle('galaxy:learnPreference', async (_e, key: string, value: any) => {
    return await zmqCall('learn_preference', { key, value });
  });
  ipcMain.handle('galaxy:learnCorrection', async (_e, original: string, corrected: string) => {
    return await zmqCall('learn_correction', { original, corrected });
  });
  ipcMain.handle('galaxy:autoLearn', async (_e, userInput: string, assistantResponse: string, feedback?: string) => {
    return await zmqCall('auto_learn', { user_input: userInput, assistant_response: assistantResponse, feedback });
  });
  ipcMain.handle('galaxy:analyzeForget', async (_e, memories: any[]) => {
    return await zmqCall('analyze_forget', { memories });
  });
  ipcMain.handle('galaxy:runCleanup', async (_e, memories: any[], dryRun?: boolean) => {
    return await zmqCall('run_cleanup', { memories, dry_run: dryRun ?? true });
  });
  ipcMain.handle('galaxy:linkTaskMemory', async (_e, taskId: string, memoryId: string, linkType?: string) => {
    return await zmqCall('link_task_memory', { task_id: taskId, memory_id: memoryId, link_type: linkType ?? 'related_to' });
  });
  ipcMain.handle('galaxy:getTaskMemories', async (_e, taskId: string) => {
    return await zmqCall('get_task_memories', { task_id: taskId });
  });

  // ── P0: 图像/文档理解 ──
  ipcMain.handle('galaxy:understandImage', async (_e, imageSource: string, prompt?: string) => {
    return await zmqCall('understand_image', { image_source: imageSource, prompt: prompt ?? '描述这张图片' });
  });
  ipcMain.handle('galaxy:ocrImage', async (_e, imageSource: string) => {
    return await zmqCall('ocr_image', { image_source: imageSource });
  });
  ipcMain.handle('galaxy:parseDocument', async (_e, imageSource: string) => {
    return await zmqCall('parse_document', { image_source: imageSource });
  });
  ipcMain.handle('galaxy:analyzeChart', async (_e, imageSource: string) => {
    return await zmqCall('analyze_chart', { image_source: imageSource });
  });
  ipcMain.handle('galaxy:verifyImageClaim', async (_e, claim: string, imageSource: string) => {
    return await zmqCall('verify_image_claim', { claim, image_source: imageSource });
  });

  // ── P0: 优化与主动任务 ──
  ipcMain.handle('galaxy:optimizeQuery', async (_e, query: string, context?: string) => {
    return await zmqCall('optimize_query', { query, context });
  });
  ipcMain.handle('galaxy:getProactiveTask', async () => {
    return await zmqCall('get_proactive_task', {});
  });
  ipcMain.handle('galaxy:classifyKnowledge', async (_e, content: string) => {
    return await zmqCall('classify_knowledge', { content });
  });
  ipcMain.handle('galaxy:correctAnswer', async (_e, query: string, wrongAnswer: string, correction?: string) => {
    return await zmqCall('correct_answer', { query, wrong_answer: wrongAnswer, correction: correction ?? '' });
  });

  // ── v9.6: 工具权限查询（借鉴 Apix）──
  ipcMain.handle('galaxy:getAvailableTools', async (_e, permission?: string | string[], agentRole?: string) => {
    return await zmqCall('get_available_tools', { permission, agent_role: agentRole ?? 'main' });
  });
  ipcMain.handle('galaxy:listTools', async () => {
    return await zmqCall('list_tools', {});
  });
}

// Disable GPU hardware acceleration so PrintWindow / BitBlt can
// capture the window contents reliably (GPU-composited surfaces
// appear black in screenshots on Windows).
app.disableHardwareAcceleration();

// ── App lifecycle ─────────────────────────────────────────────────
app.whenReady().then(async () => {
  try {
    // 启动前先确保旧 sidecar（如果僵尸残留）已清理
    stopSidecar();
    await startSidecar();
    registerIpc();
    createWindow();
  } catch (e) {
    const err = e as Error;
    log(`Startup failed: ${err.message}`);
    // If startSidecar succeeded but registerIpc/createWindow threw
    // (e.g. "Attempted to register a second handler for 'galaxy:recall'"
    // from duplicate ipcMain.handle), the sidecar process is still
    // running. We MUST stop it now — otherwise the next launch finds
    // port 5757 still bound ("Address in use") and the sidecar's
    // second zmq REP bind throws, masking the real error.
    stopSidecar();
    // Build a useful error message. The previous version always
    // told users to "install Python", which is WRONG for packaged
    // builds — the sidecar is a self-contained PyInstaller binary.
    // We now branch on dev vs packaged so the suggestion matches
    // the actual situation.
    const isPackaged = app.isPackaged;
    const platform = process.platform;
    let userDataDir = '';
    try { userDataDir = app.getPath('userData'); } catch { /* */ }
    let sidecarLog = '';
    try { sidecarLog = join(app.getPath('userData'), 'sidecar.log'); }
    catch { sidecarLog = join(RESOURCES_DIR, 'sidecar.log'); }
    const hint = isPackaged
      ? `This is a PACKAGED build (${platform}). The sidecar uses your system's\n` +
        `Python interpreter (${resolvePythonInterpreter()}) to run the GalaxyOS engine.\n` +
        `This usually means one of:\n` +
        `  - Python 3.11+ is not installed (download from https://python.org)\n` +
        `  - The NSIS dependency installer was skipped and pip packages are missing\n` +
        `  - Antivirus blocked the Python subprocess launch\n\n` +
        `To install Python dependencies manually:\n` +
        `  pip install -r "${join(RESOURCES_DIR, 'requirements-core.txt')}"\n\n` +
        `Please share the following files when reporting a bug:\n` +
        `  - ${LOG_FILE}\n` +
        `  - ${sidecarLog}\n\n` +
        `To run from source instead:\n` +
        `  git clone https://cnb.cool/TIAMO.xianyao/galaxyos-desktop\n` +
        `  cd galaxyos-desktop/desktop-shell\n` +
        `  npm install\n` +
        `  npm run dev\n`
      : `This is a DEV build. The sidecar is a Python script that needs:\n` +
        `  - Python 3.11+ on PATH (or set GALAXYOS_PYTHON)\n` +
        `  - pip install -r ../requirements-core.txt pyzmq openai\n\n` +
        `Or run the dev launcher: npm run dev\n`;
    dialog.showErrorBox(
      'GalaxyOS failed to start',
      `Could not start the Python sidecar.\n\n` +
      `Platform: ${platform}  (packaged=${isPackaged})\n` +
      `Renderer: ${RENDERER_HTML}\n` +
      `Sidecar:  ${resolveSidecarPath()}\n` +
      `Resources: ${RESOURCES_DIR}\n\n` +
      `${hint}\n` +
      `Underlying error:\n  ${err.message}`,
    );
    app.quit();
  }
});

app.on('window-all-closed', () => {
  stopSidecar();
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

app.on('before-quit', () => stopSidecar());

// Catch-all for unhandled errors
process.on('uncaughtException', (err) => {
  log(`UNCAUGHT: ${err}`);
  try { dialog.showErrorBox('GalaxyOS uncaught error', String(err)); } catch { /* */ }
});
process.on('unhandledRejection', (reason) => {
  log(`UNHANDLED REJECTION: ${reason}`);
});
