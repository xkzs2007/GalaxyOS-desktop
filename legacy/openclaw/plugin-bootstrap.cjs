/**
 * claw-core plugin bootstrap — 带路径 fallback 的入口
 *
 * 加载顺序: dist/index.js → index.js
 * - dist/index.js: 编译后版本（生产首选）
 * - index.js:      源码版本（无构建可直用）
 *
 * OpenClaw 通过 jiti 加载插件，本文件为 CJS 格式，jiti 可直接处理。
 */

const { existsSync } = require('fs');
const { resolve, relative } = require('path');

const ENTRIES = [
  resolve(__dirname, 'dist', 'index.js'),
  resolve(__dirname, 'index.js'),
];

let plugin = null;
let lastError = null;

for (const entry of ENTRIES) {
  if (!existsSync(entry)) continue;

  try {
    // jiti 会把 ESM 编译为 CJS，require() 直接可用
    const mod = require(entry);
    plugin = mod.default || mod;

    if (plugin && (typeof plugin.register === 'function' || plugin.id)) {
      console.warn(
        `[claw-core] ✅ loaded from: ${relative(__dirname, entry)}`
      );
      break;
    }
  } catch (e) {
    lastError = e;
    console.warn(
      `[claw-core] ⚠️  failed to load ${relative(__dirname, entry)}: ${e.message}`
    );
  }
}

if (!plugin) {
  const tried = ENTRIES.map((p) => relative(__dirname, p)).join(', ');
  console.error(
    `[claw-core] ❌ FATAL: no loadable entry found. Tried: ${tried}`
  );
  if (lastError) {
    console.error(`[claw-core] Last error: ${lastError.message}`);
  }
  process.exit(1);
}

module.exports = plugin;
