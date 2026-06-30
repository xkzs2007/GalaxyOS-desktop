// renderer/src/tokui/handlers.js — TokUI msg-action handlers.
//
// C 阶段：适配真实 TokUI 事件总线。
// TokUI 通过 window.TokUI.registerHandler 注册回调，DSL 里的 clk:xxx
// 触发对应 handler。handler 接收 (data, evt, formEl) — data 是事件负载。
//
// 我们保留 7 个原 handler：copy / regenerate / like / dislike /
// verify / recall / save，对应 GalaxyOS 现有 msg-action 按钮。

import { registerHandler } from './runtime.js';
import { galaxy } from '../ipc/client.js';
import { sessionApi } from '../state/session.js';

function getBubbleText(evt) {
  // evt.element is the closest bubble DOM node
  return evt?.element?.innerText ?? evt?.innerText ?? '';
}

function appendNote(bubble, text, color) {
  if (!bubble) return;
  const note = document.createElement('div');
  note.style.cssText = `margin-top:8px;padding:6px 10px;border-radius:4px;background:${color}22;color:${color};font-size:11px;`;
  note.textContent = text;
  bubble.appendChild(note);
}

export function registerMsgActionHandlers() {
  registerHandler('copy', (data, evt) => {
    const text = getBubbleText(evt) ?? data?.text ?? '';
    navigator.clipboard.writeText(text).catch(() => {});
  });

  registerHandler('regenerate', () => {
    // Re-send the last user message
    const bubbles = document.querySelectorAll('#tokui-container [class*="bubble"]');
    const lastUser = Array.from(bubbles).reverse().find((b) => b.className.includes('user'));
    const text = lastUser?.innerText?.trim();
    if (text) {
      // Dispatch custom event for composer to handle
      window.dispatchEvent(new CustomEvent('composer:regenerate', { detail: { text } }));
    }
  });

  registerHandler('like', (data, evt) => {
    if (galaxy.emitEvent) {
      galaxy.emitEvent('msg_action_like', { text: (getBubbleText(evt) ?? '').slice(0, 200) });
    }
  });

  registerHandler('dislike', (data, evt) => {
    if (galaxy.emitEvent) {
      galaxy.emitEvent('msg_action_dislike', { text: (getBubbleText(evt) ?? '').slice(0, 200) });
    }
  });

  registerHandler('verify', async (data, evt) => {
    if (!galaxy.verify) return;
    const text = getBubbleText(evt);
    try {
      const r = await galaxy.verify(text);
      const color = r.verdict === 'verified' ? '#10b981'
                  : r.verdict === 'partial'  ? '#f59e0b'
                  :                              '#ef4444';
      appendNote(evt?.element, `🔍 ${r.verdict} (${(r.confidence*100).toFixed(0)}%) — ${r.evidence_count} 证据`, color);
    } catch (e) { console.warn('[verify] failed:', e); }
  });

  registerHandler('recall', async (data, evt) => {
    if (!galaxy.recall) return;
    const text = (getBubbleText(evt) ?? '').slice(0, 100);
    try {
      const r = await galaxy.recall(text, 3);
      const box = document.createElement('div');
      box.style.cssText = 'margin-top:8px;padding:6px 10px;border-radius:4px;background:#4f9dff22;color:#4f9dff;font-size:11px;';
      const items = (r.results ?? []).map((m, i) =>
        `<div style="margin-top:4px;opacity:0.85">${i+1}. ${(m.content || m.text || JSON.stringify(m)).slice(0, 80)}…</div>`
      ).join('');
      box.innerHTML = `<b>📚 检索到 ${r.count ?? 0} 条相关记忆</b><br>${items}`;
      evt?.element?.appendChild(box);
    } catch (e) { console.warn('[recall] failed:', e); }
  });

  registerHandler('save', async (data, evt) => {
    if (!galaxy.saveMemory) return;
    const text = getBubbleText(evt);
    try {
      const r = await galaxy.saveMemory(text, { source: 'msg_action_save' });
      appendNote(evt?.element, `💾 已保存到长期记忆 (${r.memory_id?.slice(0, 8) || ''}…)`, '#10b981');
    } catch (e) { console.warn('[save] failed:', e); }
  });
}
