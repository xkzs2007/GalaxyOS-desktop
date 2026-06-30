// renderer/src/tokui/feed.js — high-level DSL feed helpers.
//
// 在 C 阶段替换为真实 TokUI：
//   - emitUserMessage  → [bubble role:user]
//   - startAssistantStream + feed + endAssistantStream  → 流式 [bubble role:ai]
//   - feedError  → [bubble role:ai time:错误]
//   - feedBatch  → 批量喂 DSL
//
// 所有操作直接走 ui.startStream / ui.feed / ui.endStream —— 真实 TokUI 解析器。
// 老的 makeStubRenderer 兜底由 runtime.js 提供（TokUI UMD 加载失败时）。

import { bootTokUI, getInstance } from './runtime.js';

let _busy = false;
let _indicator = null;

export function isStreaming() { return _busy; }

export async function emitUserMessage(text) {
  const ui = await bootTokUI();
  ui.startStream();
  ui.feed(`[bubble role:user][p]${escapeDsl(text)}[/bubble]`);
  ui.endStream();
}

export async function startAssistantStream() {
  const ui = await bootTokUI();
  ui.startStream();
  _busy = true;
  showStreamingIndicator();
  return ui;
}

export function feed(dsl) {
  const ui = getInstance();
  if (!ui) { console.warn('[tokui] feed() before boot'); return; }
  ui.feed(dsl);
}

export function endAssistantStream() {
  const ui = getInstance();
  if (!ui) return;
  ui.endStream();
  _busy = false;
  hideStreamingIndicator();
}

export async function feedError(message) {
  await startAssistantStream();
  feed(`[bubble role:ai model:GalaxyOS time:错误][p v:danger]${escapeDsl(message)}[/p][/bubble]`);
  endAssistantStream();
}

/**
 * Feed a batch of DSL fragments as one streaming session. Used by
 * tool-result popups, notifications, and any module that wants to
 * render DSL without managing startStream/endStream itself.
 */
export async function feedBatch(fragments, { withDelayMs = 0 } = {}) {
  if (!fragments?.length) return;
  await startAssistantStream();
  for (const dsl of fragments) {
    feed(dsl);
    if (withDelayMs > 0) await new Promise((r) => setTimeout(r, withDelayMs));
  }
  endAssistantStream();
}

// ── Streaming indicator (3-dot pulse, removed when stream ends) ──
function showStreamingIndicator() {
  if (_indicator) return;
  const composer = document.querySelector('.composer') || document.body;
  _indicator = document.createElement('div');
  _indicator.className = 'streaming-indicator';
  _indicator.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
  composer.appendChild(_indicator);
}
function hideStreamingIndicator() {
  if (_indicator) { _indicator.remove(); _indicator = null; }
}

// ── DSL escape (handles [ ] in user text) ──────────────────────
function escapeDsl(s) {
  if (s.includes('[') || s.includes(']')) {
    return '"' + String(s).replace(/"/g, '\\"') + '"';
  }
  return s;
}
