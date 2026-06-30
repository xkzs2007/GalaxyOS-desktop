// renderer/src/components/composer.js — composer surface.
//
// C 阶段：用 [chat-input ph: clk:onSend] 替换手写 textarea + send button。
// TokUI 的 chat-input 组件自带：
//   - 自适应高度（auto attribute）
//   - 发送按钮（clk handler 触发）
//   - 占位符（ph attribute）
//   - 禁用态（dis attribute）
//
// Mode tabs（ask/process/agent/memo/plan）保留为 [tabs] 组件的 DSL 切换，
// 但当前阶段先用 [toolbar pos:bottom] 占位 —— 下一迭代可换为 [tabs]。

import {
  startAssistantStream, feed, endAssistantStream,
  feedError, isStreaming,
} from '../tokui/feed.js';
import { galaxy } from '../ipc/client.js';
import { sessionStore } from '../state/session.js';
import { registerHandler } from '../tokui/runtime.js';

const state = {
  mode: 'ask',
  sessionId: 'default',
};

const MODE_TO_METHOD = {
  ask:     { method: 'ask',     paramKey: 'prompt' },
  process: { method: 'process', paramKey: 'user_input' },
  agent:   { method: 'agent',   paramKey: 'prompt' },
  memo:    { method: 'memo',    paramKey: 'prompt' },
  plan:    { method: 'plan',    paramKey: 'prompt' },
};

const MODE_LABELS = {
  ask: 'Ask',
  process: 'Process',
  agent: 'Agent',
  memo: 'MeMo',
  plan: 'Plan',
};

/** Mode tabs 渲染（用 TokUI [tabs][tab] 组件） */
function renderModeTabs() {
  const modes = Object.keys(MODE_TO_METHOD);
  const tabs = modes.map((m, i) =>
    `[tab value:${m} ${state.mode === m ? 'active' : ''}]${MODE_LABELS[m]}[/tab]`
  ).join('\n  ');
  return `[tabs tt:"模式" clk:onModeTab]\n  ${tabs}\n[/tabs]`;
}

/** Mode 切换 handler — 切换后重渲染 tabs */
function setMode(mode) {
  state.mode = mode;
  const tabsHost = document.getElementById('mode-tabs-host');
  if (tabsHost) {
    // TokUI render 替换内容
    const { getInstance } = window;
    const ui = window._tokuiInstance;
    if (ui) {
      tabsHost.innerHTML = '';
      ui.startStream(tabsHost);
      ui.feed(renderModeTabs());
      ui.endStream();
    }
  }
}

/** Render the composer area: mode tabs + chat-input */
export function renderComposer() {
  const host = document.getElementById('composer-host');
  if (!host) return;
  const { getInstance } = window;
  const ui = window._tokuiInstance;
  if (!ui) return;
  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(renderModeTabs());
  ui.feed(`[chat-input ph:"输入消息，按 Enter 发送" clk:onComposerSend auto rows:2 max:2000][/chat-input]`);
  ui.endStream();
}

/** Real send handler — triggered by TokUI's chat-input clk:onComposerSend */
async function onComposerSend(text) {
  if (!text?.trim() || isStreaming()) return;
  await startAssistantStream();
  // 1. User bubble
  feed(`[bubble role:user][p]${escapeDsl(text)}[/bubble]`);
  // 2. Assistant bubble — streamed fragments
  const m = MODE_TO_METHOD[state.mode] ?? MODE_TO_METHOD.ask;
  try {
    const res = await galaxy[m.method](text, state.sessionId);
    const frags = res?.events ?? res?.fragments ?? res?._fragments ?? [];
    for (const dsl of frags) {
      feed(dsl);
      await yieldToBrowser();
    }
  } catch (e) {
    console.error('[composer] error:', e);
    feed(`[p v:danger]${escapeDsl(e.message ?? String(e))}[/p]`);
  } finally {
    endAssistantStream();
  }
}

function escapeDsl(s) {
  if (s.includes('[') || s.includes(']')) {
    return '"' + String(s).replace(/"/g, '\\"') + '"';
  }
  return s;
}

function yieldToBrowser() {
  return new Promise((r) => setTimeout(r, 0));
}

// ── Wire handlers (queueable, registered before/after bootTokUI) ──
registerHandler('onComposerSend', (data) => {
  // TokUI chat-input hands the value as the first arg
  const text = typeof data === 'string' ? data : data?.value ?? data?.text ?? '';
  onComposerSend(text);
});

registerHandler('onModeTab', (data) => {
  const value = typeof data === 'string' ? data : data?.value ?? data?.tab;
  if (value && MODE_TO_METHOD[value]) setMode(value);
});

// Export internals for main.js to wire
export { onComposerSend, setMode };

export function initComposer() {
  // Keep sessionId in sync with the active session.
  sessionStore.subscribe((s) => { state.sessionId = s.activeId ?? 'default'; });
  // After TokUI boots, render the composer into the host div.
  // main.js will call renderComposer() after boot.
}
