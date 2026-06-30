// renderer/src/tokui/feed.js — high-level DSL feed helpers.
//
// Most renderer code wants to do one of three things:
//   1. emitUserMessage(text)    — show a user bubble, no streaming
//   2. startAssistantStream()   — begin a streaming assistant bubble
//   3. feed(dsl)                — append DSL to the current stream
//   4. endAssistantStream()     — close the current stream
//
// The handlers below centralise the startStream/feed/endStream
// pairing so renderer code doesn't have to remember to call them.

import { bootTokUI, getInstance } from './runtime.js';

let _busy = false;

export function isStreaming() { return _busy; }

export async function emitUserMessage(text) {
  const ui = await bootTokUI(document.getElementById('tokui-container'));
  ui.startStream();
  ui.feed(`[bubble role:user][p]${escapeDsl(text)}[/bubble]`);
  ui.endStream();
}

export async function startAssistantStream() {
  const ui = await bootTokUI(document.getElementById('tokui-container'));
  ui.startStream();
  _busy = true;
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
}

export async function feedError(message) {
  const ui = await startAssistantStream();
  ui.feed(`[bubble role:ai model:GalaxyOS time:错误][p v:danger]${message}[/p][/bubble]`);
  ui.endStream();
  _busy = false;
}

/**
 * Feed a batch of DSL fragments in one streaming session. The
 * composer's `consumeStream` is the primary caller; this helper
 * exists so other modules (e.g. tool-result popups, toast-like
 * notifications) can render DSL without touching feed() directly.
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

function escapeDsl(s) {
  if (s.includes('[') || s.includes(']')) {
    return '"' + String(s).replace(/"/g, '\\"') + '"';
  }
  return s;
}
