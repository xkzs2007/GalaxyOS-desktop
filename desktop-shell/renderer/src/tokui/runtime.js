// renderer/src/tokui/runtime.js — TokUI 适配层.
//
// B-stage 验证结果（jsdom + UMD 19/19 通过）：
//   - window.TokUI 是对象，TokUI 类在 window.TokUI.TokUI
//   - setTheme / getTheme / registerHandler / removeHandler 挂在 window.TokUI
//   - setSeedColor 在 window.TokUI, 注入 <style data-tokui-dynamic-palette>
//   - TokUIParser / TokUIRenderer / TokUIEventBus 在 window.TokUI._internal
//
// 适配层职责：
//   1. 等 window.TokUI 出现（UMD 加载延迟）
//   2. 实例化 TokUI 类（懒加载到 container）
//   3. 暴露 setTheme / setSeedColor / registerHandler 的便捷方法
//   4. 提供 fallback stub（TokUI 未加载时降级到 plain HTML）

const $ = (id) => document.getElementById(id);

let _ui = null;
let _handlers = new Map();
let _currentTheme = 'modern-dark';   // 默认 modern-dark（深色高级感）

function whenTokUIReady(maxWaitMs = 3000) {
  return new Promise((resolve) => {
    if (window.TokUI?.TokUI && window.TokUI._internal?.TokUIRenderer) {
      return resolve(window.TokUI);
    }
    const t0 = Date.now();
    const t = setInterval(() => {
      if (window.TokUI?.TokUI && window.TokUI._internal?.TokUIRenderer) {
        clearInterval(t);
        resolve(window.TokUI);
      } else if (Date.now() - t0 > maxWaitMs) {
        clearInterval(t);
        resolve(null);
      }
    }, 50);
  });
}

function makeStubRenderer(container) {
  return {
    startStream() { /* noop */ },
    feed(chunk) {
      const pre = document.createElement('pre');
      pre.style.cssText = 'opacity:0.5;font-size:11px;margin:4px 0;';
      pre.textContent = chunk;
      container.appendChild(pre);
      container.scrollTop = container.scrollHeight;
    },
    endStream() { /* noop */ },
    render(_dsl, _c) { /* noop */ },
    setTheme() {},
    setSeedColor() {},
    registerHandler() {},
  };
}

/**
 * Boot TokUI. Returns the instance (real or stub).
 * Safe to call multiple times — returns the cached instance.
 */
export async function bootTokUI(containerSel = '#tokui-container') {
  if (_ui) return _ui;
  const container = typeof containerSel === 'string' ? $(containerSel) : containerSel;
  const tokuiGlobal = await whenTokUIReady();
  if (!tokuiGlobal) {
    console.warn('[tokui] UMD not loaded within 3s; using stub');
    _ui = makeStubRenderer(container);
    return _ui;
  }
  try {
    const TokUIClass = tokuiGlobal.TokUI;
    _ui = new TokUIClass({
      container,
      theme: _currentTheme,
      onEvent: (type, data) => {
        if (type === 'streamEnd') {
          // [typing] component handles its own cleanup when [/typing] is fed
        }
      },
    });
    // Re-apply any handlers that were registered before boot
    for (const [name, fn] of _handlers) {
      _ui.registerHandler?.(name, fn);
    }
    console.log('[tokui] booted with theme:', _currentTheme);
  } catch (e) {
    console.error('[tokui] construct failed:', e);
    _ui = makeStubRenderer(container);
  }
  return _ui;
}

export function getInstance() { return _ui; }
export function isReady() { return !!_ui && typeof _ui.feed === 'function'; }

// ── Theme API ────────────────────────────────────────────────
export function setTheme(name) {
  _currentTheme = name;
  if (window.TokUI?.setTheme) window.TokUI.setTheme(name);
  else if (_ui?.setTheme) _ui.setTheme(name);
}
export function getTheme() {
  return window.TokUI?.getTheme?.() ?? _currentTheme;
}
export function setSeedColor(seed, semantic) {
  if (window.TokUI?.setSeedColor) window.TokUI.setSeedColor(seed, semantic);
}

// ── Event handler API (queueable before boot) ────────────────
export function registerHandler(name, fn) {
  _handlers.set(name, fn);
  if (window.TokUI?.registerHandler) window.TokUI.registerHandler(name, fn);
  _ui?.registerHandler?.(name, fn);
}

// ── Direct class access (for advanced use) ──────────────────
export function getTokUIClass() { return window.TokUI?.TokUI ?? null; }
export function getTokUIParser() { return window.TokUI?._internal?.TokUIParser ?? null; }
export function getRenderer() { return window.TokUI?._internal?.TokUIRenderer ?? null; }
