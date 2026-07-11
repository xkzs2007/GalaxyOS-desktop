import { app, BrowserWindow, ipcMain } from 'electron';
import { resolve, join } from 'node:path';
import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { CSP_HEADER_VALUE } from '../csp-config.js';

const APP_ROOT = resolve(import.meta.dirname, '..');
const DIST_DIR = resolve(APP_ROOT, 'dist');
const SIDECAR_SCRIPT = resolve(APP_ROOT, 'server.js');

const AUTH_TOKEN = randomBytes(32).toString('hex');

let sidecarProc = null;
let mainWindow = null;

function log(msg) {
  const line = `[main ${new Date().toISOString()}] ${msg}\n`;
  process.stderr.write(line);
}

function startSidecar() {
  if (!existsSync(SIDECAR_SCRIPT)) {
    log(`Sidecar script not found: ${SIDECAR_SCRIPT}`);
    return;
  }

  const env = {
    ...process.env,
    GALAXYOS_SIDECAR_TOKEN: AUTH_TOKEN,
    GALAXYOS_SIDECAR_HOST: '127.0.0.1',
    GALAXYOS_SIDECAR_HTTP_PORT: '5758',
  };

  sidecarProc = spawn('node', [SIDECAR_SCRIPT], {
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    cwd: APP_ROOT,
  });

  sidecarProc.stdout?.on('data', (d) => process.stderr.write(d));
  sidecarProc.stderr?.on('data', (d) => process.stderr.write(d));
  sidecarProc.on('exit', (code) => {
    log(`Sidecar exited with code ${code}`);
    sidecarProc = null;
  });

  log(`Sidecar started (pid=${sidecarProc.pid})`);
}

function stopSidecar() {
  if (sidecarProc) {
    log('Stopping sidecar...');
    sidecarProc.kill('SIGTERM');
    sidecarProc = null;
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 880,
    minHeight: 540,
    title: 'GalaxyOS Desktop',
    backgroundColor: '#0f1115',
    webPreferences: {
      preload: resolve(import.meta.dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.webContents.session.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [CSP_HEADER_VALUE],
      },
    });
  });

  const useDevServer = process.env.GALAXYOS_DEV === '1';
  if (useDevServer) {
    log('Loading renderer from http://localhost:5173 (dev)');
    win.loadURL('http://localhost:5173');
  } else {
    const htmlPath = resolve(DIST_DIR, 'index.html');
    log(`Loading renderer from file://${htmlPath}`);
    win.loadFile(htmlPath);
  }

  win.webContents.once('did-finish-load', () => {
    win.webContents.send('galaxy:token', AUTH_TOKEN);
    log(`Token injected into renderer`);

    if (process.env.GALAXYOS_DEV === '1') {
      win.webContents.openDevTools({ mode: 'detach' });
    }
  });

  win.on('closed', () => {
    mainWindow = null;
  });

  mainWindow = win;
  return win;
}

ipcMain.handle('galaxy:health', async () => {
  try {
    const resp = await fetch('http://127.0.0.1:5758/health');
    return await resp.json();
  } catch (e) {
    return { status: 'error', detail: e.message };
  }
});

ipcMain.handle('galaxy:getToken', () => AUTH_TOKEN);

app.whenReady().then(() => {
  startSidecar();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  stopSidecar();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  stopSidecar();
});