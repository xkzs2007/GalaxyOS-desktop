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
import { resolve, dirname, join, basename, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, readFileSync, writeFileSync, mkdirSync, appendFileSync } from 'node:fs';
import os from 'node:os';
import * as http from 'node:http';

// v10: worker-pool backend (3-Tier elastic + 54 methods via UDS/TCP)
import { start as startPool, stop as stopPool, execute as poolExecute } from './plugin/galaxyos-plugin-core.js';
import { registerWorkerHandlers } from './ipc/worker-bridge.js';

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

// ── Worker lifecycle ────────────────────────────────────────────
// v10: 3-Tier WorkerPool (Hot/Warm/Cold) replaces zmq sidecar
let sidecarReady = false;

async function startSidecar(): Promise<void> {
  try { baseDir = app.getPath('userData'); } catch { baseDir = join(os.homedir(), '.galaxyos'); }
  const workspace = process.env.OPENCLAW_WORKSPACE || join(baseDir, 'workspace');
  mkdirSync(workspace, { recursive: true });
  try {
    await startPool(workspace);
    sidecarReady = true;
    log('Worker pool started');
  } catch (e) {
    throw new Error(`Worker pool did not start: ${(e as Error).message}`);
  }
}

function stopSidecar(): void {
  if (sidecarReady) {
    log('Stopping worker pool...');
    try { stopPool(); } catch { /* */ }
    sidecarReady = false;
  }
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
  // v10: 54 worker methods registered via registerWorkerHandlers
  // (no 30+ ipcMain.handle boilerplate — all channels from
  // EXPOSE_METHODS auto-registered via pool.execute)
  log('IPC handlers registered via worker-bridge');
}
// Disable GPU hardware acceleration so PrintWindow / BitBlt can
// capture the window contents reliably (GPU-composited surfaces
// appear black in screenshots on Windows).
app.disableHardwareAcceleration();

// ── App lifecycle ─────────────────────────────────────────────────
app.whenReady().then(async () => {
  try {
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
