// src/main.ts — Electron main process for GalaxyOS Desktop.
//
// Stage 5: real desktop packaging. This file:
//   1. Spawns the Python sidecar as a child process (and waits for
//      its HTTP SSE endpoint to come up before opening the window).
//   2. Opens an Electron BrowserWindow that loads the renderer
//      (either from the local dist/ folder or a static HTTP server
//      if dev.mjs has launched one).
//   3. Dynamically injects the @jboltai/tokui UMD bundle into the
//      renderer so the page works without the user having run
//      `npm install` of the renderer-side dep.
//   4. Cleans up the sidecar process on app quit.
//
// In production (electron-builder NSIS), the sidecar is bundled as
// `extraResources` so the app is self-contained.

import { app, BrowserWindow, ipcMain, shell, dialog } from 'electron';
import { spawn, ChildProcess } from 'node:child_process';
import { resolve, dirname, join, basename, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync, readFileSync, writeFileSync, mkdirSync, appendFileSync } from 'node:fs';
import * as http from 'node:http';
import * as zmq from 'zeromq';

// ── Logging (file + stdout) — declared FIRST, before any path
//   resolution, so we can debug path issues during startup. ────────
const LOG_FILE = process.env.GALAXYOS_LOG_FILE
  || join(process.cwd(), 'electron.log');
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

// In a bundled CJS context, import.meta.url is empty AND __dirname
// may not be set when referenced at module top-level (esbuild
// hoists consts in declaration order, but TS-compiled code can
// reference symbols before their const is declared). The safest
// reliable approach is to use process.cwd() — when electron.exe is
// launched with `.` as the arg, cwd is the desktop-shell dir.

const APP_ROOT = process.cwd();  // desktop-shell/ (when launched with `.`)
const PYTHON_DIR = resolve(APP_ROOT, 'python');
const SIDECAR_SCRIPT = resolve(PYTHON_DIR, 'galaxyos_sidecar.py');
const RENDERER_HTML = resolve(APP_ROOT, 'renderer', 'index.html');
const TOKUI_DIST_JS = resolve(APP_ROOT, 'node_modules', '@jboltai', 'tokui', 'dist', 'tokui.umd.js');
const TOKUI_DIST_CSS = resolve(APP_ROOT, 'node_modules', '@jboltai', 'tokui', 'dist', 'tokui.css');

const SIDECAR_PORT = Number(process.env.GALAXYOS_SIDECAR_PORT ?? 5757);
const SIDECAR_HTTP_PORT = Number(process.env.GALAXYOS_SIDECAR_HTTP_PORT ?? 5758);
const SIDECAR_HOST = process.env.GALAXYOS_SIDECAR_HOST ?? '127.0.0.1';

// Where to bundle the Python sidecar for packaged builds
const RESOURCES_DIR = process.resourcesPath
  ? join(process.resourcesPath)
  : APP_ROOT;
const PACKAGED_SIDECAR = join(RESOURCES_DIR, 'galaxyos-sidecar');
const PACKAGED_PYTHON_DIR = join(RESOURCES_DIR, 'python');

function resolveSidecarPath(): string {
  if (process.env.NODE_ENV === 'development' || !app.isPackaged) {
    return SIDECAR_SCRIPT;
  }
  // In a packaged build, look for the bundled executable
  if (process.platform === 'win32') return PACKAGED_SIDECAR + '.exe';
  return PACKAGED_SIDECAR;
}

function resolvePythonInterpreter(): string {
  if (!app.isPackaged) {
    return process.env.GALAXYOS_PYTHON ?? 'python';
  }
  // In packaged builds we'd ship a Python embeddable
  return process.env.GALAXYOS_PYTHON ?? 'python';
}

// ── Sidecar lifecycle ──────────────────────────────────────────────
let sidecar: ChildProcess | null = null;
let sidecarReady = false;

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
      rejectP(new Error(`sidecar script not found: ${script}`));
      return;
    }

    // Redirect sidecar stdout/stderr to a file (EPIPE avoidance:
    // Electron's main process doesn't have a TTY, and piping
    // large WARNING volumes to it fills the buffer and crashes.)
    const SIDECAR_LOG = process.env.GALAXYOS_SIDECAR_LOG
      || join(process.cwd(), 'sidecar.log');
    const sidecarOut = require('fs').openSync(SIDECAR_LOG, 'a');
    const sidecarErr = require('fs').openSync(SIDECAR_LOG, 'a');

    log(`Starting sidecar: ${py} ${script}`);
    log(`Sidecar stdout/stderr → ${SIDECAR_LOG}`);

    sidecar = spawn(py, [script], {
      env: {
        ...process.env,
        PYTHONPATH:
          (process.env.PYTHONPATH ? process.env.PYTHONPATH + ';' : '')
          + (existsSync(PACKAGED_PYTHON_DIR) ? PACKAGED_PYTHON_DIR + ';' : '')
          + PYTHON_DIR,
        GALAXYOS_SIDECAR_PORT: String(SIDECAR_PORT),
        GALAXYOS_SIDECAR_HTTP_PORT: String(SIDECAR_HTTP_PORT),
        GALAXYOS_SIDECAR_HOST: SIDECAR_HOST,
        GALAXYOS_SIDECAR_LOG: SIDECAR_LOG,
      },
      // detached: true makes the sidecar survive main process crashes
      // stdio: pipes to file descriptors (not Electron's stdio)
      detached: process.platform !== 'win32',
      stdio: ['ignore', sidecarOut, sidecarErr],
      windowsHide: true,
    });

    // Don't keep the file descriptors open in this process
    try { require('fs').closeSync(sidecarOut); } catch { /* */ }
    try { require('fs').closeSync(sidecarErr); } catch { /* */ }

    sidecar.on('exit', (code) => log(`Sidecar exited with code ${code}`));
    sidecar.on('error', (e) => log(`Sidecar spawn error: ${e.message}`));

    try {
      await waitForSidecar(30000);
      resolveP();
    } catch (e) {
      rejectP(e);
    }
  });
}

function stopSidecar(): void {
  if (sidecar) {
    log('Stopping sidecar...');
    try {
      sidecar.kill('SIGTERM');
    } catch { /* ignore */ }
    sidecar = null;
  }
}

// ── Window ──────────────────────────────────────────────────────────
function createWindow(): void {
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
  });

  win.on('closed', () => stopSidecar());

  // Hide the default menu in production
  if (!process.env.GALAXYOS_SHOW_MENU) {
    win.setMenuBarVisibility(false);
  }
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

// ── IPC handlers (renderer → main → sidecar via stdlib HTTP) ────
// We don't need real IPC for the SSE path (renderer does fetch
// directly to 127.0.0.1:5758). The IPC handlers below are
// reserved for future use (e.g. zmq-backed methods, file dialogs).

// ── App lifecycle ─────────────────────────────────────────────────
app.whenReady().then(async () => {
  try {
    await startSidecar();
    createWindow();
  } catch (e) {
    log(`Startup failed: ${(e as Error).message}`);
    dialog.showErrorBox('GalaxyOS failed to start',
      'Could not start the Python sidecar. Please ensure Python 3.11+ is installed and run:\n\n  pip install -r requirements-core.txt pyzmq\n\n' + (e as Error).message);
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
