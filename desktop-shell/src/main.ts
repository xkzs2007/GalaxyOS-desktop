// src/main.ts — Electron main process for GalaxyOS Desktop.
//
// Responsibilities (Stage 1):
//   1. Spawn the Python sidecar as a child process.
//   2. Open a pyzmq REQ socket and route renderer→sidecar RPCs.
//   3. Expose a contextBridge API via preload (window.galaxy.*).
//   4. Show a TokUI bubble UI that streams ask() responses.
//
// Stage 2 will add: MeMo model load progress, 3-stage visualization.
// Stage 3 will add: ACRouter C-A-F loop status, routing debug panel.

import { app, BrowserWindow, ipcMain, shell } from 'electron';
import { spawn, ChildProcess } from 'node:child_process';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';
import * as zmq from 'zeromq';  // napi binding; user adds it as a dep in Stage 1.5
// Fallback: if zeromq isn't installed at runtime, we surface a clear error.

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── Paths ─────────────────────────────────────────────────────────────
const APP_ROOT = resolve(__dirname, '..');  // desktop-shell/
const PYTHON_DIR = resolve(APP_ROOT, 'python');
const SIDECAR_SCRIPT = resolve(PYTHON_DIR, 'galaxyos_sidecar.py');
const RENDERER_HTML = resolve(APP_ROOT, 'renderer', 'index.html');

const SIDECAR_PORT = Number(process.env.GALAXYOS_SIDECAR_PORT ?? 5757);
const SIDECAR_HOST = process.env.GALAXYOS_SIDECAR_HOST ?? '127.0.0.1';

// ── Sidecar process handle ────────────────────────────────────────────
let sidecar: ChildProcess | null = null;
let reqSocket: any = null;  // zmq.REQ socket; typed as any to avoid napi import in tsc
let nextRpcId = 1;
const pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();

// ── Spawn / shutdown ──────────────────────────────────────────────────
function startSidecar(): Promise<void> {
  return new Promise((resolveStart, rejectStart) => {
    const py = process.env.GALAXYOS_PYTHON ?? 'python';
    if (!existsSync(SIDECAR_SCRIPT)) {
      rejectStart(new Error(`sidecar script not found: ${SIDECAR_SCRIPT}`));
      return;
    }

    log(`Starting sidecar: ${py} ${SIDECAR_SCRIPT}`);
    sidecar = spawn(py, [SIDECAR_SCRIPT], {
      env: {
        ...process.env,
        PYTHONPATH: PYTHON_DIR + (process.env.PYTHONPATH ? `;${process.env.PYTHONPATH}` : ''),
        GALAXYOS_SIDECAR_PORT: String(SIDECAR_PORT),
        GALAXYOS_SIDECAR_HOST: SIDECAR_HOST,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    sidecar.stdout?.on('data', (d: Buffer) => log(`[sidecar stdout] ${d.toString().trim()}`));
    sidecar.stderr?.on('data', (d: Buffer) => log(`[sidecar stderr] ${d.toString().trim()}`));
    sidecar.on('exit', (code) => log(`Sidecar exited with code ${code}`));

    // Wait for sidecar to bind (poll port or just give it 3s)
    setTimeout(() => {
      try {
        reqSocket = new zmq.Request();
        reqSocket.connect(`tcp://${SIDECAR_HOST}:${SIDECAR_PORT}`);
        reqSocket.receiveTimeout = 30_000;
        resolveStart();
      } catch (e) {
        rejectStart(e as Error);
      }
    }, 3000);
  });
}

function stopSidecar(): void {
  if (sidecar) {
    log('Stopping sidecar...');
    sidecar.kill('SIGTERM');
    sidecar = null;
  }
  if (reqSocket) {
    try { reqSocket.close(); } catch { /* ignore */ }
    reqSocket = null;
  }
}

// ── RPC bridge (renderer ↔ sidecar) ──────────────────────────────────
async function callSidecar(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
  if (!reqSocket) throw new Error('sidecar not connected');
  const id = nextRpcId++;
  const payload = JSON.stringify({ id, method, params });
  await reqSocket.send(payload);
  // Simple REQ/REP loop: read until we get a JSON with matching id.
  // The sidecar guarantees a 1:1 reply, so one recv() is enough.
  const [reply] = await reqSocket.receive();
  const msg = JSON.parse(reply.toString());
  if (msg.error) throw new Error(msg.error);
  return msg.result;
}

// ── IPC handlers (renderer → main) ───────────────────────────────────
function registerIpc(): void {
  ipcMain.handle('galaxy:ask', async (_e, question: string, sessionId?: string) => {
    return callSidecar('ask', { question, session_id: sessionId });
  });

  ipcMain.handle('galaxy:remember', async (_e, content: string, metadata?: object, source?: string) => {
    return callSidecar('remember', { content, metadata, source });
  });

  ipcMain.handle('galaxy:recall', async (_e, query: string, topK?: number, sessionId?: string) => {
    return callSidecar('recall', { query, top_k: topK, session_id: sessionId });
  });

  ipcMain.handle('galaxy:process', async (_e, userInput: string, sessionId?: string) => {
    return callSidecar('process', { user_input: userInput, session_id: sessionId });
  });

  ipcMain.handle('galaxy:health', async () => callSidecar('health', {}));

  ipcMain.handle('galaxy:open-external', async (_e, url: string) => {
    await shell.openExternal(url);
  });
}

// ── Window ────────────────────────────────────────────────────────────
function createWindow(): void {
  const win = new BrowserWindow({
    width: 1100,
    height: 760,
    minWidth: 720,
    minHeight: 480,
    title: 'GalaxyOS Desktop',
    webPreferences: {
      preload: resolve(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  win.loadFile(RENDERER_HTML).catch((e) => log(`loadFile failed: ${e}`));
  win.on('closed', () => stopSidecar());
}

// ── Logging ───────────────────────────────────────────────────────────
function log(msg: string): void {
  // eslint-disable-next-line no-console
  console.log(`[main ${new Date().toISOString()}] ${msg}`);
}

// ── App lifecycle ────────────────────────────────────────────────────
app.whenReady().then(async () => {
  try {
    await startSidecar();
    registerIpc();
    createWindow();
  } catch (e) {
    log(`Startup failed: ${(e as Error).message}`);
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
