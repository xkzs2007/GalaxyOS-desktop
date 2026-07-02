import { existsSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(__dirname, '..');

export function runCommand(command, args, opts = {}) {
  console.log(`[install] ${command} ${args.join(' ')}`);
  execFileSync(command, args, {
    cwd: projectRoot,
    stdio: 'inherit',
    ...opts,
  });
}

export function hasPlaywrightBrowser() {
  const browserPath = resolve(projectRoot, 'node_modules', '@playwright', 'core', '.local-browsers');
  return existsSync(browserPath);
}

export function installPlaywrightBrowser() {
  if (hasPlaywrightBrowser()) {
    console.log('[install] Playwright browser already present');
    return;
  }

  try {
    runCommand('npx', ['playwright', 'install', 'chromium']);
  } catch (error) {
    console.warn('[install] Playwright browser download failed; continuing because the shell can still run without it.');
  }
}
