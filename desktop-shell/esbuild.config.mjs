// esbuild.config.mjs — bundle Electron main + preload into dist/
//
// Why: Electron 32 + ESM requires the main entry to be pre-bundled.
// We bundle src/main.ts → dist/main.cjs (CJS so Electron's default
// module loader works without `--experimental-vm-modules`).

import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Sourcemap policy:
//   - default (dev / `npm run build:main`): emit .map for debugging
//   - in CI / release builds: set GALAXYOS_RELEASE=1 to skip them,
//     so the released app.asar / unpacked tree doesn't ship with
//     .map files pointing at the source tree.
const RELEASE = process.env.GALAXYOS_RELEASE === '1';
const sourcemap = RELEASE ? false : true;

await build({
  entryPoints: [resolve(__dirname, 'src/main.ts')],
  bundle: true,
  platform: 'node',
  target: 'node20',
  format: 'cjs',
  outfile: resolve(__dirname, 'dist/main.cjs'),
  // Externalise native modules + optional renderer-side deps.
  // esbuild's bundle doesn't handle .node addons or UMD bundles
  // correctly, so we require() them at runtime from node_modules.
  // `electron` itself is provided by the host process.
  // `@jboltai/tokui` is a UMD renderer-side bundle that
  // main.ts uses only for `existsSync` to detect its install
  // location — bundling would copy the whole UMD into main.cjs
  // and double the dist size. Keep it external.
  // `zeromq` is a native module with .node files we must NOT
  // try to bundle.
  external: [
    'electron',
    'fsevents',
    'zeromq',
    '@jboltai/tokui',
  ],
  sourcemap,
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
  sourcemap,
  logLevel: 'info',
});

console.log(`[esbuild] main + preload bundled → dist/  (sourcemap=${sourcemap})`);
