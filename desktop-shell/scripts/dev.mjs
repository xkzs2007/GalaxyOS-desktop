#!/usr/bin/env node
// scripts/dev.mjs — one-shot dev launcher.
//
// 1. Ensures the Python venv exists (or creates one).
// 2. Installs requirements-core.txt into the venv.
// 3. Builds main + preload via esbuild.
// 4. Launches Electron with the built dist/main.cjs.

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(__dirname, '..');
const REPO_ROOT = resolve(APP_ROOT, '..');

const VENV = resolve(APP_ROOT, '.venv');
const VENV_PY = process.platform === 'win32'
  ? resolve(VENV, 'Scripts', 'python.exe')
  : resolve(VENV, 'bin', 'python');

function log(msg) { console.log(`[dev] ${msg}`); }

function ensureVenv() {
  if (existsSync(VENV_PY)) return;
  log(`Creating venv at ${VENV}...`);
  // Try several python names so this works on Windows (where
  // `python` is the MS Store stub and the user-installed one is
  // usually `py -3` or `python3`).
  const candidates = process.platform === 'win32'
    ? ['py', 'python', 'python3', 'python3.12', 'python3.11']
    : ['python3.12', 'python3.11', 'python3', 'python'];
  let lastErr;
  for (const cand of candidates) {
    const r = spawnSync(cand, ['-m', 'venv', VENV], { stdio: 'inherit' });
    if (r.status === 0) return;
    lastErr = r.error || new Error(`exit ${r.status}`);
  }
  throw new Error(`venv creation failed with all candidates: ${lastErr}`);
}

function installDeps() {
  log('Installing requirements-core.txt...');
  let r = spawnSync(VENV_PY, ['-m', 'pip', 'install', '-q', '-r',
    resolve(REPO_ROOT, 'requirements-core.txt')], { stdio: 'inherit' });
  if (r.status !== 0) throw new Error('pip install requirements-core failed');
  log('Installing pyzmq into venv...');
  r = spawnSync(VENV_PY, ['-m', 'pip', 'install', '-q', 'pyzmq'], { stdio: 'inherit' });
  // CRITICAL: pyzmq is required for the Electron main process to
  // talk to the Python sidecar. A previous version of this script
  // didn't check r.status, so a failed install was silently
  // swallowed and the user got a confusing "sidecar zmq did not
  // respond" error 30s later in the packaged app.
  if (r.status !== 0) throw new Error('pip install pyzmq failed (sidecar IPC needs it)');
}

function bundle() {
  log('Bundling main + preload via esbuild...');
  const r = spawnSync('node', [resolve(APP_ROOT, 'esbuild.config.mjs')],
    { stdio: 'inherit', cwd: APP_ROOT });
  if (r.status !== 0) throw new Error('esbuild failed');
}

function launchElectron() {
  log('Launching Electron...');
  if (process.platform === 'win32') {
    // On Windows the electron binary is electron.cmd — Node's
    // `spawn` cannot execute a .cmd directly without `shell: true`
    // (or it gets ENOENT). Using `npx electron` is the most
    // portable option: npx wraps the .cmd transparently.
    const child = spawn('npx', ['electron', '.'], {
      cwd: APP_ROOT,
      stdio: 'inherit',
      shell: process.platform === 'win32',
      env: {
        ...process.env,
        GALAXYOS_PYTHON: VENV_PY,
        GALAXYOS_SIDECAR_LOG: 'DEBUG',
      },
    });
    child.on('exit', (code) => process.exit(code ?? 0));
    return;
  }
  const electronBin = resolve(APP_ROOT, 'node_modules', '.bin', 'electron');
  const child = spawn(electronBin, ['.'], {
    cwd: APP_ROOT,
    stdio: 'inherit',
    env: {
      ...process.env,
      GALAXYOS_PYTHON: VENV_PY,
      GALAXYOS_SIDECAR_LOG: 'DEBUG',
    },
  });
  child.on('exit', (code) => process.exit(code ?? 0));
}

try {
  ensureVenv();
  installDeps();
  bundle();
  launchElectron();
} catch (e) {
  console.error('[dev] FATAL:', e.message);
  process.exit(1);
}
