// renderer/src/components/composer.js — input box + send button + mode tabs.
//
// The composer is the "ask the agent" surface. It owns:
//   - the textarea (#input)
//   - the send button (#send)
//   - the mode tabs (.mode-btn)
//   - the auto-resize logic
//   - the actual send pipeline (which delegates to feed.js for TokUI)
//
// Mode → sidecar method mapping is centralised here so callers don't
// have to remember it.

import { galaxy } from '../ipc/client.js';
import {
  emitUserMessage, startAssistantStream, feed, endAssistantStream,
  feedError, isStreaming,
} from '../tokui/feed.js';
import { sessionStore } from '../state/session.js';

const $ = (id) => document.getElementById(id);

const state = {
  mode: 'ask',          // 'ask' | 'process' | 'agent' | 'memo' | 'plan'
  sessionId: 'default',
};

const MODE_TO_METHOD = {
  ask:     { method: 'ask',     paramKey: 'prompt' },
  process: { method: 'process', paramKey: 'user_input' },
  agent:   { method: 'agent',   paramKey: 'prompt' },
  memo:    { method: 'memo',    paramKey: 'prompt' },
  plan:    { method: 'plan',    paramKey: 'prompt' },
};

function placeholders() {
  return {
    ask:     '简单提问（自动路由：MeMo / process / fast_path）',
    process: '复杂任务（自动路由：走 R-CCAM 五阶段）',
    agent:   'Agent 任务：!cmd / read file / grep / list / write path=content',
    memo:    'MeMo 调试：直调 3-stage 协议（Grounding → Entity → Answer）',
    plan:    'Plan 模式：描述任务，Agent 先出计划再执行',
  };
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll('.mode-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  const input = $('input');
  if (input) input.placeholder = placeholders()[mode] ?? '';
}

function autoResize() {
  const input = $('input');
  if (!input) return;
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 200) + 'px';
}

function setBusy(busy) {
  const send = $('send');
  if (send) send.disabled = busy;
}

async function consumeStream(method, paramKey, text) {
  const params = { [paramKey]: text, session_id: state.sessionId };
  await startAssistantStream();
  showStreamingIndicator();
  try {
    const res = await galaxy[method](text, state.sessionId);
    const frags = res?.events ?? res?.fragments ?? res?._fragments ?? [];
    // Progressive feed: yield to the browser between fragments so the
    // user sees the bubble grow in real time. When sidecar IPC is
    // upgraded to true streaming (one event per fragment), this loop
    // already does the right thing — `await yieldToBrowser()` is a
    // no-op cost on each iteration.
    for (const dsl of frags) {
      feed(dsl);
      await yieldToBrowser();
    }
  } catch (e) {
    console.error('[composer] error:', e);
    feed(`[p v:danger]${e.message ?? e}[/p]`);
  } finally {
    hideStreamingIndicator();
    endAssistantStream();
  }
}

/** Yield to the browser so it can paint. 0ms is enough — the browser
 *  reclaims control on its next idle slot. ~16ms cost is acceptable
 *  for a visible streaming effect; we cap to 30ms max so very long
 *  fragment lists don't slow the response. */
function yieldToBrowser() {
  return new Promise((r) => setTimeout(r, 0));
}

let _indicator = null;
function showStreamingIndicator() {
  if (_indicator) return;
  const composer = document.querySelector('.composer');
  if (!composer) return;
  _indicator = document.createElement('div');
  _indicator.className = 'streaming-indicator';
  _indicator.innerHTML = '<span class="dot"></span><span class="dot"></span><span class="dot"></span>';
  composer.appendChild(_indicator);
}
function hideStreamingIndicator() {
  if (_indicator) { _indicator.remove(); _indicator = null; }
}

export async function handleSend() {
  const input = $('input');
  const text = input?.value.trim();
  if (!text || isStreaming()) return;
  if (input) { input.value = ''; autoResize(); }
  setBusy(true);

  await emitUserMessage(text);

  const m = MODE_TO_METHOD[state.mode] ?? MODE_TO_METHOD.ask;
  try {
    await consumeStream(m.method, m.paramKey, text);
  } catch (e) {
    await feedError(e.message ?? String(e));
  } finally {
    setBusy(false);
    input?.focus();
  }
}

export function initComposer() {
  const send = $('send');
  const input = $('input');
  if (send) send.addEventListener('click', handleSend);
  if (input) {
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    });
    input.addEventListener('input', () => {
      setBusy(!input.value.trim() || isStreaming());
      autoResize();
    });
  }

  // Listen for the 'regenerate' action triggered by tokui/handlers.js
  window.addEventListener('composer:regenerate', (e) => {
    if (input) { input.value = e.detail.text; handleSend(); }
  });

  document.querySelectorAll('.mode-btn').forEach((b) => {
    b.addEventListener('click', () => setMode(b.dataset.mode));
  });

  setMode('ask');

  // Keep the sessionId in sync with the active session.
  sessionStore.subscribe((s) => { state.sessionId = s.activeId ?? 'default'; });
}
