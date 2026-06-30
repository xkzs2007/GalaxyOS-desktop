// renderer/src/tokui/runtime.js — TokUI runtime wrapper.
//
// TokUI exposes `window.TokUI` (UMD) once the page loads it. The
// runtime provides a thin wrapper with:
//   - safe initialization (falls back to a stub if TokUI didn't load)
//   - automatic startStream/feed/endStream pairing for one-shot feeds
//   - typed event-handler registration (clk:/sub:/reg:)
//
// The renderer never touches window.TokUI directly — always through
// this module. That way if we swap renderers later (Solid, Preact,
// custom), only this file changes.

let _TokUI = null;
let _instance = null;
let _handlers = new Map();

/**
 * Wait for window.TokUI to appear (UMD load + main.ts injection).
 * Times out after `maxWaitMs` and returns false.
 */
export async function waitForTokUI(maxWaitMs = 3000) {
  if (window.TokUI) {
    _TokUI = window.TokUI;
    return true;
  }
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    if (window.TokUI) {
      _TokUI = window.TokUI;
      return true;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
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
    render(_dsl) { /* noop */ },
  };
}

/**
 * Create the renderer instance. MUST be awaited before use.
 */
export async function bootTokUI(container, { theme = 'dark' } = {}) {
  if (_instance) return _instance;
  const loaded = await waitForTokUI();
  const TokUIClass = _TokUI?.TokUI || _TokUI?.default;
  if (!loaded || !TokUIClass || typeof TokUIClass !== 'function') {
    console.warn('[tokui] not loaded; using stub renderer');
    _instance = makeStubRenderer(container);
  } else {
    _instance = new TokUIClass({ container, theme });
    // Re-register any handlers that were queued before boot
    for (const [name, fn] of _handlers) {
      _instance.registerHandler?.(name, fn);
    }
  }
  return _instance;
}

/**
 * Register a TokUI event handler (e.g. 'copy', 'regenerate', 'like').
 * Safe to call before or after bootTokUI.
 */
export function registerHandler(name, fn) {
  _handlers.set(name, fn);
  if (_instance && typeof _instance.registerHandler === 'function') {
    _instance.registerHandler(name, fn);
  }
}

export function getInstance() { return _instance; }
