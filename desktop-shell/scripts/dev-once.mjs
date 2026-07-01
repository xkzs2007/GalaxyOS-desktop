#!/usr/bin/env node
// scripts/dev-once.mjs — one-shot dev launcher (no watch, no restart).
// Use `npm run dev` for the full watch-mode experience.
// This is kept for CI / quick smoke tests.

import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(__dirname, '..');
const REPO_ROOT = resolve(APP_ROOT, '..');

const VENV = resolve(APP_ROOT, '.venv');
const VENV_PY = process.platform === 'win32'
  ? resolve(VENV, 'Scripts', 'python.exe')
  : resolve(VENV, 'bin', 'python');

function log(msg) { console.log(`[dev-once] ${msg}`); }

function ensureVenv() {
  if (existsSync(VENV_PY)) return;
  log(`Creating venv at ${VENV}...`);
  const candidates = process.platform === 'win32'
    ? ['py', 'python', 'python3', 'python3.12', 'python3.11']
    : ['python3.12', 'python3.11', 'python3', 'python'];
  for (const cand of candidates) {
    const r = spawnSync(cand, ['-m', 'venv', VENV], { stdio: 'inherit' });
    if (r.status === 0) return;
  }
  throw new Error('venv creation failed');
}

function installDeps() {
  log('Installing deps...');
  let r = spawnSync(VENV_PY, ['-m', 'pip', 'install', '-q', '-r',
    resolve(REPO_ROOT, 'requirements-core.txt')], { stdio: 'inherit' });
  if (r.status !== 0) throw new Error('pip install failed');
  r = spawnSync(VENV_PY, ['-m', 'pip', 'install', '-q', 'pyzmq'], { stdio: 'inherit' });
  if (r.status !== 0) throw new Error('pip install pyzmq failed');
}

function bundle() {
  log('Bundling main + preload...');
  const r = spawnSync('node', [resolve(APP_ROOT, 'esbuild.config.mjs')],
    { stdio: 'inherit', cwd: APP_ROOT });
  if (r.status !== 0) throw new Error('esbuild failed');
}

function launchElectron() {
  log('Launching Electron...');
  const env = {
    ...process.env,
    GALAXYOS_PYTHON: VENV_PY,
    GALAXYOS_SIDECAR_LOG: 'DEBUG',
    GALAXYOS_DEV: '1',
    GALAXYOS_SHOW_MENU: '1',
  };
  if (process.platform === 'win32') {
    const child = spawn('npx', ['electron', '.'], { cwd: APP_ROOT, stdio: 'inherit', shell: true, env });
    child.on('exit', (code) => process.exit(code ?? 0));
    return;
  }
  const electronBin = resolve(APP_ROOT, 'node_modules', '.bin', 'electron');
  const child = spawn(electronBin, ['.'], { cwd: APP_ROOT, stdio: 'inherit', env });
  child.on('exit', (code) => process.exit(code ?? 0));
}

try {
  ensureVenv();
  installDeps();
  bundle();
  launchElectron();
} catch (e) {
  console.error('[dev-once] FATAL:', e.message);
  process.exit(1);
}
