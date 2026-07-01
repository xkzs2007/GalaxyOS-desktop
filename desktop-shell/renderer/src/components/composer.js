// renderer/src/components/composer.js — composer surface.
//
// D 阶段（TokUI 深用）：
//   - Process/Memo 模式集成 [think] / [think-chain] / [think-step]
//     推理链可视化，R-CCAM 5 阶段 / MeMo 3 阶段实时展示。
//   - Agent 模式集成 [agent] / [tool-call] 工具调用状态可视化
//   - [code] 代码语法高亮（expandCodeBlocks）
//   - [notification] 通知反馈

import {
  startAssistantStream, feed, endAssistantStream,
  isStreaming,
} from '../tokui/feed.js';
import { galaxy } from '../ipc/client.js';
import { sessionStore } from '../state/session.js';
import { registerHandler, getInstance } from '../tokui/runtime.js';
import { escapeDsl, yieldToBrowser, expandCodeBlocks } from '../utils.js';
import notify from '../tokui/notify.js';
import { startThinkChain, updateThinkStep, endThinkChain } from '../tokui/think-chain.js';
import { startAgent, newToolCall, completeToolCall, endAgent } from '../tokui/tool-call.js';

const state = {
  mode: 'ask',
  sessionId: 'default',
};

const MODE_TO_METHOD = {
  ask:     { method: 'ask',     paramKey: 'prompt',      viz: null },
  process: { method: 'process', paramKey: 'user_input',  viz: 'think' },
  agent:   { method: 'agent',   paramKey: 'prompt',      viz: 'agent' },
  memo:    { method: 'memo',    paramKey: 'prompt',      viz: 'think' },
  plan:    { method: 'plan',    paramKey: 'prompt',      viz: null },
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
  if (!tabsHost) return;
  const ui = getInstance();
  if (ui) {
    tabsHost.innerHTML = '';
    ui.startStream(tabsHost);
    ui.feed(renderModeTabs());
    ui.endStream();
  }
}

/** Render the composer area: mode tabs + chat-input */
export function renderComposer() {
  const host = document.getElementById('composer-host');
  if (!host) return;
  const ui = getInstance();
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

  const m = MODE_TO_METHOD[state.mode] ?? MODE_TO_METHOD.ask;
  const t0 = performance.now();

  // 2. Start visualisation (think-chain or agent tool-calls)
  let chain = null;
  let agentHandle = null;
  const viz = m.viz;

  if (viz === 'think') {
    const thinkMode = state.mode === 'memo' ? 'memo' : 'rccam';
    chain = startThinkChain(thinkMode);
  } else if (viz === 'agent') {
    agentHandle = startAgent('GalaxyOS Agent');
    // Detect likely tools from the user prompt
    const tools = detectToolsFromPrompt(text);
    for (const t of tools) {
      const tc = newToolCall(agentHandle?.id, t.name, t.params);
      if (tc) agentHandle.toolCalls.push(tc);
    }
  }

  // 3. Call the sidecar
  try {
    const res = await galaxy[m.method](text, state.sessionId);
    const frags = res?.events ?? res?.fragments ?? res?._fragments ?? [];

    // 3a. Stream the response fragments
    for (const dsl of frags) {
      feed(expandCodeBlocks(dsl));
      await yieldToBrowser();
    }

    // 3b. Animate visualisation completion
    const totalSec = (performance.now() - t0) / 1000;
    if (chain) {
      await animateThinkChain(chain, totalSec);
    } else if (agentHandle) {
      await animateAgentTools(agentHandle, totalSec);
    }
  } catch (e) {
    console.error('[composer] error:', e);
    feed(`[p v:danger]${escapeDsl(e.message ?? String(e))}[/p]`);
    if (chain) {
      updateThinkStep(chain, 0, 'error', `请求失败: ${e.message ?? '未知错误'}`);
    }
    notify.error(`请求失败: ${e.message ?? '未知错误'}`, { duration: 5000 });
  } finally {
    endAssistantStream();
  }
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

// ── Think chain animation ─────────────────────────────────────

/**
 * Animate think chain steps to completion with staggered timing.
 */
async function animateThinkChain(chain, totalSec) {
  const steps = chain.steps;
  const perStepDelay = Math.min(180, Math.max(60, (totalSec * 1000) / steps.length / 3));
  for (let i = 0; i < steps.length; i++) {
    updateThinkStep(chain, i, 'done', null, totalSec / steps.length);
    await new Promise((r) => setTimeout(r, perStepDelay));
  }
  endThinkChain(chain, totalSec);
}

// ── Agent tool detection ──────────────────────────────────────

/**
 * Parse user prompt for tool-related keywords.
 * Returns an array of { name, params } objects for visual feedback.
 */
function detectToolsFromPrompt(text) {
  const tools = [];

  // File reading patterns
  if (/(?:读|查看|打开|read|cat)\s*(?:文件|file)?/i.test(text)) {
    const pathMatch = text.match(/(?:文件|file)\s*[:：]?\s*(\S+\.\w{1,6})/i);
    tools.push({ name: 'read_file', params: pathMatch ? { path: pathMatch[1] } : {} });
  }

  // File writing patterns
  if (/(?:写|创建|生成|write|create)\s*(?:文件|file)?/i.test(text)) {
    const pathMatch = text.match(/(?:文件|file)\s*[:：]?\s*(\S+\.\w{1,6})/i);
    tools.push({ name: 'write_file', params: pathMatch ? { path: pathMatch[1] } : {} });
  }

  // Search patterns
  if (/(?:搜索|查找|search|find|grep)/i.test(text)) {
    const queryMatch = text.match(/(?:搜索|search)\s*[:：]?\s*(.+?)(?:$|[,，])/i);
    tools.push({ name: 'web_search', params: queryMatch ? { query: queryMatch[1].trim() } : {} });
  }

  // Shell/command patterns
  if (/(?:运行|执行|shell|bash|cmd|run|命令|编译)/i.test(text)) {
    const cmdMatch = text.match(/(?:命令|command)\s*[:：]?\s*(.+?)(?:$|[，,])/i);
    tools.push({ name: 'shell', params: cmdMatch ? { command: cmdMatch[1].trim() } : {} });
  }

  // Directory listing patterns
  if (/(?:列出|目录|列表|list|ls|dir)\s*(?:文件|目录)?/i.test(text)) {
    const pathMatch = text.match(/(?:目录|dir)\s*[:：]?\s*(\S+)/i);
    tools.push({ name: 'list_dir', params: pathMatch ? { path: pathMatch[1] } : {} });
  }

  // If no tools detected, show a generic call_tool
  if (tools.length === 0) {
    tools.push({ name: 'call_tool', params: { prompt: text.slice(0, 40) } });
  }

  return tools;
}

// ── Agent tool animation ──────────────────────────────────────

/**
 * Animate agent tool calls to completion with staggered timing.
 * Uses the toolCalls array tracked in the agent handle (no DOM query needed).
 */
async function animateAgentTools(agentHandle, totalSec) {
  if (!agentHandle || !agentHandle.id) return;

  const tools = agentHandle.toolCalls || [];
  if (!tools.length) {
    endAgent(agentHandle.id, totalSec);
    return;
  }

  const perToolDelay = Math.min(150, Math.max(50, (totalSec * 1000) / tools.length / 2));

  for (const tc of tools) {
    completeToolCall(tc, 'done', '完成', totalSec / tools.length);
    await new Promise((r) => setTimeout(r, perToolDelay));
  }

  endAgent(agentHandle.id, totalSec);
}

export function initComposer() {
  // Keep sessionId in sync with the active session.
  sessionStore.subscribe((s) => { state.sessionId = s.activeId ?? 'default'; });
  // After TokUI boots, render the composer into the host div.
  // main.js will call renderComposer() after boot.
}
