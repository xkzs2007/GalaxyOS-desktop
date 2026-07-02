// renderer/src/components/composer.js — composer surface.
//
// D 阶段（TokUI 深用）：
//   - Process/Memo 模式集成 [think] / [think-chain] / [think-step]
//     推理链可视化，R-CCAM 5 阶段 / MeMo 3 阶段实时展示。
//   - Agent 模式集成 [agent] / [tool-call] 工具调用状态可视化
//   - Plan 模式集成 [plan] / [plan-step] 计划步骤可视化
//   - [terminal] / [sandbox] 工具输出渲染
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
import { startAgent, newToolCall, completeToolCall, endAgent, feedToolOutput } from '../tokui/tool-call.js';
import { startPlan, endPlan } from '../tokui/plan.js';

const state = {
  mode: 'ask',
  sessionId: 'default',
};

const MODE_TO_METHOD = {
  ask:     { method: 'ask',     paramKey: 'prompt',      viz: null },
  process: { method: 'process', paramKey: 'user_input',  viz: 'think' },
  agent:   { method: 'agent',   paramKey: 'prompt',      viz: 'agent' },
  memo:    { method: 'memo',    paramKey: 'prompt',      viz: 'think' },
  plan:    { method: 'plan',    paramKey: 'prompt',      viz: 'think' },
  ocr:     { method: 'ocr',     paramKey: 'params',      viz: null },
};

const MODE_LABELS = {
  ask: 'Ask',
  process: 'Process',
  agent: 'Agent',
  memo: 'MeMo',
  plan: 'Plan',
  ocr: 'OCR',
};

/**
 * v9.6: Generate contextual follow-up suggestions based on current mode.
 */
function getModeSuggestions(mode) {
  const all = {
    ask: [
      { title: '深入分析', desc: '用 Process 模式深度推理' },
      { title: '查记忆', desc: '搜索相关长期记忆' },
      { title: '写代码', desc: '切换到 Agent 模式' },
    ],
    process: [
      { title: '执行计划', desc: '用 Plan 模式生成步骤' },
      { title: '工具调用', desc: '用 Agent 执行操作' },
      { title: '保存记忆', desc: '结果写入长期记忆' },
    ],
    agent: [
      { title: '查看文件', desc: '浏览 sandbox 目录' },
      { title: '分析结果', desc: '用 Process 模式总结' },
      { title: '继续改进', desc: '进一步优化代码' },
    ],
    memo: [
      { title: '常规提问', desc: '切回 Ask 模式' },
      { title: '复杂推理', desc: '用 Process 模式分析' },
    ],
    plan: [
      { title: '自动执行', desc: '切 Agent 按计划执行' },
      { title: '调整计划', desc: '重新生成执行计划' },
      { title: '简单问答', desc: '切回 Ask 模式' },
    ],
  };
  return all[mode] || [];
}

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
  // Quick action buttons for a Proma-style workbench (TokUI-driven)
  ui.feed(`[row]`);
  ui.feed(`  [btn tx:"🧩 生成执行计划" clk:onModeTab act:plan sm][/btn]`);
  ui.feed(`  [btn tx:"🤖 调度 Agent" clk:onModeTab act:agent sm][/btn]`);
  ui.feed(`  [btn tx:"🧠 检索记忆" clk:onMemOpenTimeline sm v:muted][/btn]`);
  ui.feed(`[/row]`);

  ui.feed(renderModeTabs());
  ui.feed(`[chat-input ph:"输入任务、命令或问题，GalaxyOS 会按工作流执行" clk:onComposerSend auto rows:2 max:2000][/chat-input]`);
  ui.endStream();
}

/** Real send handler — triggered by TokUI's chat-input clk:onComposerSend */
async function onComposerSend(text) {
  if (!text?.trim() || isStreaming()) return;
  await startAssistantStream();
  // 1. User bubble
  // Render user input: keep chat bubble for Ask mode, otherwise use a compact card (Proma 风格)
  if (state.mode === 'ask') {
    feed(`[bubble role:user][p]${escapeDsl(text)}[/bubble]`);
  } else {
    const title = MODE_LABELS[state.mode] || 'User';
    feed(`[card tt:"${escapeDsl(title)} · 用户输入" v:muted]`);
    feed(`[p]${escapeDsl(text)}[/p]`);
    feed(`[/card]`);
  }

  const m = MODE_TO_METHOD[state.mode] ?? MODE_TO_METHOD.ask;
  const t0 = performance.now();

  // 2. Start visualisation (think-chain or agent tool-calls or plan)
  let chain = null;
  let agentHandle = null;
  let planHandle = null;
  const viz = m.viz;

  // Generate a unique stream_id so PUB events can be correlated
  const streamId = `${state.mode}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;

  // Subscribe to real-time events BEFORE sending the request
  let unsubs = [];
  // v9.6: subscribe to PUB-based DSL fragments for true streaming
  const unsubDsl = galaxy.onDslFragment?.((ev) => {
    if (ev.stream_id === streamId) {
      // Feed the DSL fragment immediately — it arrives before the REP response
      feed(expandCodeBlocks(ev.tokui));
    }
  });
  if (unsubDsl) unsubs.push(unsubDsl);

  if (viz === 'think') {
    const thinkMode = state.mode === 'memo' ? 'memo' : 'rccam';
    chain = startThinkChain(thinkMode);
    if (state.mode === 'memo') {
      const unsub1 = galaxy.onMemoStage?.((ev) => {
        if (ev.stream_id === streamId) handleLiveMemoEvent(chain, ev);
      });
      if (unsub1) unsubs.push(unsub1);
    }
    // R-CCAM modes (ask/process/plan) also get think events
    const unsub2 = galaxy.onThinkStep?.((ev) => {
      if (ev.stream_id === streamId) handleLiveThinkEvent(chain, ev);
    });
    if (unsub2) unsubs.push(unsub2);
  } else if (viz === 'agent') {
    agentHandle = startAgent('GalaxyOS Agent');
    // Don't pre-guess tools — let real PUB events create them
    const unsub3 = galaxy.onAgentTool?.((ev) => {
      if (ev.stream_id === streamId) handleLiveAgentEvent(agentHandle, ev);
    });
    if (unsub3) unsubs.push(unsub3);
  }
  // Subscribe to plan events for plan mode
  if (state.mode === 'plan') {
    planHandle = startPlan('执行计划');
    const unsub4 = galaxy.onPlanStep?.((ev) => {
      if (ev.stream_id === streamId) handleLivePlanEvent(planHandle, ev);
    });
    if (unsub4) unsubs.push(unsub4);
  }

  // 3. Call the sidecar (with stream_id for real-time PUB events)
  try {
    let res;
    if (state.mode === 'ocr') {
      res = await galaxy.ocr({ path: '', base64: '', prompt: text, sessionId: state.sessionId });
    } else {
      // Pass stream_id so the sidecar publishes events tagged with it
      res = await galaxy[m.method](text, state.sessionId, streamId);
    }
    const frags = res?.events ?? res?.fragments ?? res?._fragments ?? [];
    // Track whether we're inside a think-chain/agent/plan from the batch response
    let inBatchThinkChain = false;
    let inBatchAgent = false;
    let inBatchPlan = false;

    // 3a. Stream the response fragments (skip think-chain DSL if we already
    //     rendered one via startThinkChain for real-time PUB updates)
    for (const dsl of frags) {
      // If we have a frontend-created chain, skip batch think-chain DSL
      if (chain) {
        if (/^\[think-chain\b/i.test(dsl)) { inBatchThinkChain = true; continue; }
        if (inBatchThinkChain) {
          if (/^\[\/think-chain\]/i.test(dsl)) { inBatchThinkChain = false; continue; }
          if (/^\[think-step\b/i.test(dsl) || /^\[\/think-step\]/i.test(dsl)) continue;
        }
      }
      // If we have a frontend-created agent, skip batch agent wrapper DSL
      if (agentHandle) {
        if (/^\[agent\b/i.test(dsl)) { inBatchAgent = true; continue; }
        if (inBatchAgent) {
          if (/^\[\/agent\]/i.test(dsl)) { inBatchAgent = false; continue; }
        }
      }
      // If we have a frontend-created plan, skip batch plan wrapper DSL
      if (planHandle) {
        if (/^\[plan\b/i.test(dsl)) { inBatchPlan = true; continue; }
        if (inBatchPlan) {
          if (/^\[\/plan\]/i.test(dsl)) { inBatchPlan = false; continue; }
          if (/^\[plan-step\b/i.test(dsl) || /^\[\/plan-step\]/i.test(dsl)) continue;
        }
      }

      // P1: Attempt to wrap tool output in [terminal] or [sandbox]
      const wrapped = maybeWrapToolOutput(dsl);
      feed(expandCodeBlocks(wrapped));
      await yieldToBrowser();
    }

    // 3b. Animate visualisation completion
    const totalSec = (performance.now() - t0) / 1000;
    const totalMs = Math.round(totalSec * 1000);
    if (chain) {
      endThinkChain(chain, totalSec);
    } else if (agentHandle) {
      endAgent(agentHandle.id, totalSec);
    }
    if (planHandle) {
      endPlan(planHandle, totalSec);
    }

    // If the sidecar returned no structured fragments but provided a textual reply,
    // render it using a card (for non-ask modes) or a bubble (for ask mode).
    if ((!frags || frags.length === 0) && res) {
      const replyText = res.reply || res.text || res.result || '';
      if (replyText && replyText.trim()) {
        if (state.mode === 'ask') {
          feed(`[bubble role:assistant][p]${escapeDsl(replyText)}[/p][/bubble]`);
        } else {
          feed(`[card tt:"助手回复" v:accent][md]\n${escapeDsl(replyText)}\n[/md][/card]`);
        }
      }
    }

    // v9.6: Post-response enrichments — latency + suggestions
    feed(`[latency v:${totalMs} t:primary]`);
    // Add follow-up suggestions based on the mode
    const suggestions = getModeSuggestions(state.mode);
    if (suggestions.length) {
      feed(`[suggestions cols:2 clk:onSuggestionPick]`);
      for (const s of suggestions) {
        feed(`  [suggestion tt:"${escapeDsl(s.title)}" tx:"${escapeDsl(s.desc)}" clk:onSuggestionPick]`);
      }
      feed(`[/suggestions]`);
    }
  } catch (e) {
    console.error('[composer] error:', e);
    feed(`[callout t:danger tt:"请求失败"]${escapeDsl(e.message ?? String(e))}[/callout]`);
    if (chain) {
      updateThinkStep(chain, 0, 'error', `请求失败: ${e.message ?? '未知错误'}`);
    }
    if (planHandle) {
      endPlan(planHandle, 0, `失败: ${e.message ?? '未知错误'}`);
    }
    notify.error(`请求失败: ${e.message ?? '未知错误'}`, { duration: 5000 });
  } finally {
    // Unsubscribe from all real-time event listeners
    for (const unsub of unsubs) {
      try { unsub(); } catch { /* ignore */ }
    }
    unsubs = [];
    endAssistantStream();
  }
}

// ── Real-time streaming event handlers ────────────────────────

/**
 * Map a sidecar PUB event phase to a think-chain step index.
 * Returns { idx, detail } for updateThinkStep().
 */
function mapThinkPhase(phase, detail) {
  const RCCAM_IDX = { routing: 0, retrieval: 1, cognition: 2, control: 3, action: 3, memory: 4 };
  const MEMO_IDX  = { grounding: 0, entity: 1, answer: 2 };
  return { rccam: RCCAM_IDX, memo: MEMO_IDX };
}

/**
 * Map sidecar PUB events to think-chain updates in real time.
 * @param {{ id, steps, mode }} chain - from startThinkChain
 * @param {object} event - PUB event payload
 */
function handleLiveThinkEvent(chain, event) {
  if (!chain) return;
  const idxMap = mapThinkPhase();
  const map = chain.mode === 'memo' ? idxMap.memo : idxMap.rccam;
  const idx = map[event.phase];
  if (idx === undefined) return;

  const statusMap = { running: 'running', done: 'done', error: 'error' };
  const status = statusMap[event.status] || event.status;
  const detail = event.detail || '';
  const durSec = event.dur_ms ? event.dur_ms / 1000 : undefined;

  updateThinkStep(chain, idx, status, detail, durSec);
}

function handleLiveMemoEvent(chain, event) {
  if (!chain) return;
  const idxMap = { grounding: 0, entity: 1, answer: 2 };
  const idx = idxMap[event.stage];
  if (idx === undefined) return;

  const statusMap = { running: 'running', done: 'done', error: 'error' };
  const status = statusMap[event.status] || event.status;
  const detail = event.detail || '';
  const durSec = event.dur_ms ? event.dur_ms / 1000 : undefined;

  updateThinkStep(chain, idx, status, detail, durSec);
}

/**
 * Handle live agent tool events from PUB.
 * Creates tool-call widgets on-the-fly as they execute.
 * Tracks last completed tool for output wrapping.
 */
function handleLiveAgentEvent(agentHandle, event) {
  if (!agentHandle) return;

  if (event.type === 'tool_start' && event.tool_name) {
    const tc = newToolCall(agentHandle.id, event.tool_name, event.params || { step: event.step_index });
    if (tc) {
      tc._name = event.tool_name;
      agentHandle.toolCalls.push(tc);
    }
  } else if (event.type === 'tool_done' && event.tool_name) {
    const tc = agentHandle.toolCalls.find(
      t => t._name === event.tool_name && t._status !== 'done'
    );
    if (tc) {
      tc._status = 'done';
      const durSec = event.dur_ms ? event.dur_ms / 1000 : undefined;
      completeToolCall(tc, 'done', event.detail || '完成', durSec);

      // P1: If we have tool output content, render it
      if (event.output) {
        const output = typeof event.output === 'string' ? event.output : event.output.content || '';
        if (output) {
          feedToolOutput(event.tool_name, event.params || {}, output);
        }
      }
    }
  }
}

/**
 * Handle live plan events from PUB.
 * P1: Replaces lightweight notify with full [plan] / [plan-step] visualisation.
 */
function handleLivePlanEvent(planHandle, event) {
  if (!planHandle) return;

  if (event.status === 'running' && event.step_title) {
    // Add or update a plan step
    const stepId = event.step_id || event.step;
    planHandle._steps = planHandle._steps || [];
    const existing = planHandle._steps.find(s => s.id === stepId);
    if (!existing) {
      planHandle._steps.push({ id: stepId, title: event.step_title, status: 'running' });
      // Re-render all steps
      updatePlanSteps(planHandle);
    }
  } else if (event.status === 'done') {
    // Mark matching step as done
    const stepId = event.step_id || event.step;
    if (planHandle._steps) {
      const step = planHandle._steps.find(s => s.id === stepId);
      if (step) step.status = 'done';
      updatePlanSteps(planHandle);
    }
  }
}

/** Re-render plan steps via [upd] for each step. */
function updatePlanSteps(planHandle) {
  if (!planHandle?._steps) return;
  const ui = getInstance();
  if (!ui) return;
  ui.startStream();
  for (const s of planHandle._steps) {
    const pid = `plan-${s.id}`;
    ui.feed(`[upd id:${pid} act:${s.status}]`);
  }
  ui.endStream();
}

/**
 * P1: Auto-detect tool output fragments and wrap in appropriate component.
 * - Shell-like content (lines with $, >, or common CLI patterns) → [terminal]
 * - Code content (blocks with keywords, brackets, indentation) → [sandbox]
 * Returns transformed DSL or original if content doesn't match.
 */
function maybeWrapToolOutput(dsl) {
  // Only transform simple [p]...[/p] fragments
  const match = dsl.match(/^\[p\]([\s\S]*)\[\/p\]$/);
  if (!match) return dsl;
  const content = match[1];
  if (!content.trim() || content.length < 20) return dsl;

  // Detect shell output: common CLI patterns
  const shellPatterns = /(^\s*[$#>]\s|\b(command not found|Permission denied|error:|fail(ed)?:|installed|removed|updated|running|downloading|cloning|building|compiling|total\s+\d+)\b|^\s*\w+@\w+[:~]|\bstd(out|err)\b|exit\s*code|pid\s*\d+|\[\w+\]\s)/mi;
  if (shellPatterns.test(content)) {
    return `[terminal v:dark]\n${escapeDsl(content)}\n[/terminal]`;
  }

  // Detect code content: structured patterns
  const codePatterns = /(^\s*(import|export|const|let|var|function|class|def|async|await|return|if|for|while|#include|package|use|require|from|module)\b|\{\s*$|^\s*\/[/*]|^\s*#\w|^\s*<\w+[ >])/mi;
  if (codePatterns.test(content) && content.split('\n').length >= 3) {
    return `[sandbox]\n${escapeDsl(content)}\n[/sandbox]`;
  }

  // Detect explicit tool output markers and attach to tool-call widget
  // Format e.g. "TOOL:git\n<output>" or "tool: name\noutput"
  const m = content.match(/^\s*(?:TOOL|tool)\s*[:]\s*([\w-]+)\s*\n([\s\S]*)$/i);
  if (m) {
    const toolName = m[1];
    const output = m[2];
    try {
      // feed to tool-call widget if available
      feedToolOutput(toolName, {}, output);
      return '';
    } catch (e) {
      return `[terminal v:dark]\n${escapeDsl(output)}\n[/terminal]`;
    }
  }

  return dsl;
}

// ── Real send handler ──────────────────────────────────────────
registerHandler('onComposerSend', (data) => {
  // TokUI chat-input hands the value as the first arg
  const text = typeof data === 'string' ? data : data?.value ?? data?.text ?? '';
  onComposerSend(text);
});

registerHandler('onModeTab', (data) => {
  const value = typeof data === 'string' ? data : data?.value ?? data?.tab;
  if (value && MODE_TO_METHOD[value]) setMode(value);
});

/**
 * Start a demo stream (used by demo-panel to create subscriptions and a stream_id).
 * Returns { streamId, chain, agentHandle, planHandle }
 */
export function startDemoStream(demoMode = 'plan') {
  const m = MODE_TO_METHOD[demoMode] ?? MODE_TO_METHOD.plan;
  const viz = m.viz;
  const streamId = `${demoMode}-demo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,6)}`;

  let chain = null;
  let agentHandle = null;
  let planHandle = null;

  // subscribe to standalone galaxy pub-sub
  const unsubs = [];
  const unsubDsl = galaxy.onDslFragment?.((ev) => { if (ev.stream_id === streamId) feed(expandCodeBlocks(ev.tokui)); });
  if (unsubDsl) unsubs.push(unsubDsl);

  if (viz === 'think') {
    const thinkMode = demoMode === 'memo' ? 'memo' : 'rccam';
    chain = startThinkChain(thinkMode);
    const u1 = galaxy.onMemoStage?.((ev) => { if (ev.stream_id === streamId) handleLiveMemoEvent(chain, ev); });
    if (u1) unsubs.push(u1);
    const u2 = galaxy.onThinkStep?.((ev) => { if (ev.stream_id === streamId) handleLiveThinkEvent(chain, ev); });
    if (u2) unsubs.push(u2);
  } else if (viz === 'agent') {
    agentHandle = startAgent('Demo Agent');
    const u3 = galaxy.onAgentTool?.((ev) => { if (ev.stream_id === streamId) handleLiveAgentEvent(agentHandle, ev); });
    if (u3) unsubs.push(u3);
  }
  if (demoMode === 'plan') {
    planHandle = startPlan('演示计划');
    const u4 = galaxy.onPlanStep?.((ev) => { if (ev.stream_id === streamId) handleLivePlanEvent(planHandle, ev); });
    if (u4) unsubs.push(u4);
  }

  return { streamId, chain, agentHandle, planHandle, _unsubs: unsubs };
}

// v9.6: Handle suggestion clicks — switch mode + fill composer
registerHandler('onSuggestionPick', (data) => {
  const title = typeof data === 'string' ? data : data?.tt || data?.tx || '';
  const mode = guessSuggestionMode(title);
  if (mode && mode !== state.mode) setMode(mode);
  // Fill composer with the suggestion text as a prompt hint
  const inputEl = document.querySelector('#tokui-container [class*="chat-input"] input, #tokui-container [data-tokui-tag="chat-input"] input');
  if (inputEl) {
    inputEl.value = title;
    inputEl.focus();
  }
});

/** v9.6: Map suggestion title to a mode. */
function guessSuggestionMode(title) {
  const t = (title || '').toLowerCase();
  if (/process|推理|分析|深度/i.test(t)) return 'process';
  if (/agent|执行|操作|代码|改/i.test(t)) return 'agent';
  if (/plan|计划|步骤/i.test(t)) return 'plan';
  if (/ask|提问|问答/i.test(t)) return 'ask';
  if (/memo|记忆|保存/i.test(t)) return 'memo';
  return null;
}

// Export internals for main.js to wire
export { onComposerSend, setMode };

export function initComposer() {
  // Keep sessionId in sync with the active session.
  sessionStore.subscribe((s) => { state.sessionId = s.activeId ?? 'default'; });
  // After TokUI boots, render the composer into the host div.
  // main.js will call renderComposer() after boot.
}
