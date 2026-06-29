// esbuild.config.mjs — bundle Electron main + preload into dist/
//
// Why: Electron 32 + ESM requires the main entry to be pre-bundled.
// We bundle src/main.ts → dist/main.cjs (CJS so Electron's default
// module loader works without `--experimental-vm-modules`).

import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

await build({
  entryPoints: [resolve(__dirname, 'src/main.ts')],
  bundle: true,
  platform: 'node',
  target: 'node20',
  format: 'cjs',
  outfile: resolve(__dirname, 'dist/main.cjs'),
  external: ['electron', 'fsevents'],
  sourcemap: true,
  logLevel: 'info',
});

await build({
  entryPoints: [resolve(__dirname, 'src/preload.ts')],
  bundle: true,
  platform: 'node',
  target: 'node20',
  format: 'cjs',
  outfile: resolve(__dirname, 'dist/preload.cjs'),
  external: ['electron'],
  sourcemap: true,
  logLevel: 'info',
});

console.log('[esbuild] main + preload bundled → dist/');
