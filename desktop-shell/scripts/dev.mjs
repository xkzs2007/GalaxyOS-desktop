#!/usr/bin/env node
// scripts/dev.mjs — dev launcher with type-check + watch + auto-reload.
//
// 1. Runs tsc --noEmit (type checking only, fast).
// 2. Ensures Python venv + deps.
// 3. Builds main + preload via esbuild (watch mode).
// 4. Launches Electron with DevTools auto-open.
// 5. On file change (src/*.ts): rebuild → app.relaunch() → auto-restart.
//
// Renderer files (HTML/JS) are loaded directly from disk — no build step.
// Ctrl+R in the Electron window reloads them.

import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { watch } from 'node:fs/promises';
import { context } from 'esbuild';

const __dirname = dirname(fileURLToPath(import.meta.url));
const APP_ROOT = resolve(__dirname, '..');
const REPO_ROOT = resolve(APP_ROOT, '..');

const VENV = resolve(APP_ROOT, '.venv');
const VENV_PY = process.platform === 'win32'
  ? resolve(VENV, 'Scripts', 'python.exe')
  : resolve(VENV, 'bin', 'python');

const DEV_PORT = process.env.GALAXYOS_DEV_PORT || '5758';

function log(msg) { console.log(`\x1b[36m[dev]\x1b[0m ${msg}`); }
function warn(msg) { console.log(`\x1b[33m[dev]\x1b[0m ${msg}`); }

// ── Type check ─────────────────────────────────────────────────

function typecheck() {
  log('Type checking (tsc --noEmit)...');
  const r = spawnSync(resolve(APP_ROOT, 'node_modules', '.bin', 'tsc'), ['--noEmit'], {
    cwd: APP_ROOT,
    stdio: 'pipe',
    encoding: 'utf-8',
  });
  if (r.status !== 0) {
    warn('⚠️  Type errors found (build continues anyway):');
    // Print first 20 lines of errors
    const lines = (r.stdout + r.stderr).split('\n').filter(Boolean);
    for (const line of lines.slice(0, 20)) console.log(`  ${line}`);
    if (lines.length > 20) console.log(`  ... and ${lines.length - 20} more`);
  } else {
    log('✅  Type check passed');
  }
  return r.status;  // 0 = clean, non-zero = errors
}

// ── Python venv ────────────────────────────────────────────────

function ensureVenv() {
  if (existsSync(VENV_PY)) return;
  log(`Creating venv at ${VENV}...`);
  const candidates = process.platform === 'win32'
    ? ['py', 'python', 'python3', 'python3.12', 'python3.11']
    : ['python3.12', 'python3.11', 'python3', 'python'];
  let lastErr;
  for (const cand of candidates) {
    const r = spawnSync(cand, ['-m', 'venv', VENV], { stdio: 'inherit' });
    if (r.status === 0) return;
    lastErr = r.error || new Error(`exit ${r.status}`);
  }
  throw new Error(`venv creation failed: ${lastErr}`);
}

function installDeps() {
  log('Installing deps...');
  let r = spawnSync(VENV_PY, ['-m', 'pip', 'install', '-q', '-r',
    resolve(REPO_ROOT, 'requirements-core.txt')], { stdio: 'inherit' });
  if (r.status !== 0) throw new Error('pip install requirements-core failed');
  r = spawnSync(VENV_PY, ['-m', 'pip', 'install', '-q', 'pyzmq'], { stdio: 'inherit' });
  if (r.status !== 0) throw new Error('pip install pyzmq failed');
}

// ── esbuild bundling (watch mode) ──────────────────────────────

async function startBuildWatch() {
  log('Starting esbuild watch mode...');

  const mainCtx = await context({
    entryPoints: [resolve(APP_ROOT, 'src/main.ts')],
    bundle: true,
    platform: 'node',
    target: 'node20',
    format: 'cjs',
    outfile: resolve(APP_ROOT, 'dist/main.cjs'),
    external: ['electron', 'fsevents', 'zeromq', '@jboltai/tokui'],
    sourcemap: true,
    logLevel: 'info',
  });

  const preloadCtx = await context({
    entryPoints: [resolve(APP_ROOT, 'src/preload.ts')],
    bundle: true,
    platform: 'node',
    target: 'node20',
    format: 'cjs',
    outfile: resolve(APP_ROOT, 'dist/preload.cjs'),
    external: ['electron'],
    sourcemap: true,
    logLevel: 'info',
  });

  // Initial build
  await Promise.all([mainCtx.rebuild(), preloadCtx.rebuild()]);
  log('✅  Initial build complete');

  return { mainCtx, preloadCtx };
}

// ── Electron launcher ──────────────────────────────────────────

function launchElectron() {
  log('Launching Electron...');
  const env = {
    ...process.env,
    GALAXYOS_PYTHON: VENV_PY,
    GALAXYOS_SIDECAR_LOG: 'DEBUG',
    GALAXYOS_DEV: '1',           // signal main.ts to open DevTools
    GALAXYOS_DEV_PORT: DEV_PORT, // reserved for future renderer dev server
    GALAXYOS_SHOW_MENU: '1',     // show menu bar in dev
  };

  if (process.platform === 'win32') {
    const child = spawn('npx', ['electron', '.'], {
      cwd: APP_ROOT, stdio: 'inherit', shell: true, env,
    });
    return child;
  }

  const electronBin = resolve(APP_ROOT, 'node_modules', '.bin', 'electron');
  const child = spawn(electronBin, ['.'], {
    cwd: APP_ROOT, stdio: 'inherit', env,
  });
  return child;
}

// ── Graceful restart helpers ───────────────────────────────────

let _electronProc = null;
let _restartScheduled = false;

function killElectron() {
  if (_electronProc) {
    try { _electronProc.kill('SIGTERM'); } catch { /* */ }
    _electronProc = null;
  }
}

function scheduleRestart() {
  if (_restartScheduled) return;
  _restartScheduled = true;
  // Debounce: wait 300ms to batch rapid file saves
  setTimeout(() => {
    _restartScheduled = false;
    log('🔁  Rebuilding & restarting Electron...');
    killElectron();
    _electronProc = launchElectron();
  }, 300);
}

// ── Main ───────────────────────────────────────────────────────

async function main() {
  // 1. Type check first (non-blocking: errors are warnings)
  typecheck();

  // 2. Python venv
  ensureVenv();
  installDeps();

  // 3. Build + start watch
  const { mainCtx, preloadCtx } = await startBuildWatch();

  // 4. Launch Electron
  _electronProc = launchElectron();

  // 5. Watch src/*.ts files for changes → rebuild → restart
  log('👀  Watching src/*.ts for changes...');
  log('   ┌─ src/*.ts  → rebuild → app restart');
  log('   └─ renderer/* → Ctrl+R to reload in Electron');

  const srcDir = resolve(APP_ROOT, 'src');
  const pattern = /\.ts$/;

  for await (const event of watch(srcDir, { recursive: true })) {
    if (!pattern.test(event.filename)) continue;
    log(`  📝  ${event.filename} changed`);

    try {
      await Promise.all([mainCtx.rebuild(), preloadCtx.rebuild()]);
      log('  ✅  Rebuild OK');
    } catch (e) {
      warn(`  ❌  Build error: ${e.message}`);
      continue;  // don't restart on build failure
    }

    // Re-run type check on change (fast, just for feedback)
    typecheck();

    scheduleRestart();
  }
}

process.on('SIGINT', () => { killElectron(); process.exit(0); });
process.on('SIGTERM', () => { killElectron(); process.exit(0); });

main().catch((e) => {
  console.error('[dev] FATAL:', e.message);
  killElectron();
  process.exit(1);
});
