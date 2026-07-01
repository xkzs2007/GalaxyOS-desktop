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
import { escapeDsl, expandCodeBlocks } from '../utils.js';
import dslInspector from './dsl-inspector.js';

let _busy = false;

export function isStreaming() { return _busy; }

export async function emitUserMessage(text) {
  const ui = await bootTokUI();
  ui.startStream();
  ui.feed(`[bubble role:user][p]${escapeDsl(text)}[/p][/bubble]`);
  ui.endStream();
}

export async function startAssistantStream() {
  const ui = await bootTokUI();
  ui.startStream();
  _busy = true;
  // TokUI [typing] — animated 3-dot indicator, replaces hand-rolled DOM
  ui.feed(`[typing text:"AI 思考中…"]`);
  return ui;
}

export function feed(dsl) {
  const ui = getInstance();
  if (!ui) { console.warn('[tokui] feed() before boot'); return; }
  // P1: Record DSL for inspector (no-op when inactive)
  dslInspector.record(dsl);
  ui.feed(dsl);
}

export function endAssistantStream() {
  const ui = getInstance();
  if (!ui) return;
  // Close the [typing] component before ending stream
  ui.feed(`[/typing]`);
  ui.endStream();
  _busy = false;
}

export async function feedError(message) {
  await startAssistantStream();
  // [callout] provides a coloured alert block, semantically better than [p v:danger]
  feed(`[bubble role:ai model:GalaxyOS time:错误][callout t:danger tt:"错误"]${escapeDsl(message)}[/callout][/bubble]`);
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
    feed(expandCodeBlocks(dsl));
    if (withDelayMs > 0) await new Promise((r) => setTimeout(r, withDelayMs));
  }
  endAssistantStream();
}

// ── Streaming indicator: now handled by TokUI [typing] in startAssistantStream ──
