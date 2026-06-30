// renderer/src/tokui/handlers.js — TokUI msg-action handlers.
//
// Maps TokUI event names (copy, regenerate, like, dislike, verify,
// recall, save) to renderer-side callbacks. Registered once at boot.
//
// This file MUST NOT import from components/* (circular dep risk).
// When a handler needs to trigger composer logic, it dispatches a
// CustomEvent on window; the relevant component listens.

import { registerHandler } from './runtime.js';
import { galaxy } from '../ipc/client.js';
import { sessionApi } from '../state/session.js';

function getBubbleText(ctx) {
  return ctx?.element?.innerText ?? '';
}

function appendNote(bubble, text, color) {
  if (!bubble) return;
  const note = document.createElement('div');
  note.style.cssText = `margin-top:8px;padding:6px 10px;border-radius:4px;background:${color}22;color:${color};font-size:11px;`;
  note.textContent = text;
  bubble.appendChild(note);
}

export function registerMsgActionHandlers() {
  registerHandler('copy', (ctx) => {
    navigator.clipboard.writeText(getBubbleText(ctx)).catch(() => {});
  });

  registerHandler('regenerate', () => {
    // Re-send the last user message in the active session.
    const s = sessionApi.getActive();
    if (s?.title) {
      // Walk back through the rendered HTML; simpler: read last user bubble.
      const bubbles = document.querySelectorAll('#tokui-container [class*="bubble"]');
      const lastUser = Array.from(bubbles).reverse().find((b) => b.className.includes('user'));
      const text = lastUser?.innerText?.trim();
      if (text) {
        // Dispatch a custom event instead of importing composer directly
        window.dispatchEvent(new CustomEvent('composer:regenerate', { detail: { text } }));
      }
    }
  });

  registerHandler('like', (ctx) => {
    if (galaxy.emitEvent) {
      galaxy.emitEvent('msg_action_like', { text: getBubbleText(ctx).slice(0, 200) });
    }
  });

  registerHandler('dislike', (ctx) => {
    if (galaxy.emitEvent) {
      galaxy.emitEvent('msg_action_dislike', { text: getBubbleText(ctx).slice(0, 200) });
    }
  });

  registerHandler('verify', async (ctx) => {
    if (!galaxy.verify) return;
    const text = getBubbleText(ctx);
    try {
      const r = await galaxy.verify(text);
      const color = r.verdict === 'verified' ? '#10b981'
                  : r.verdict === 'partial'  ? '#f59e0b'
                  :                              '#ef4444';
      appendNote(ctx?.element, `🔍 ${r.verdict} (${(r.confidence*100).toFixed(0)}%) — ${r.evidence_count} 证据`, color);
    } catch (e) { console.warn('[verify] failed:', e); }
  });

  registerHandler('recall', async (ctx) => {
    if (!galaxy.recall) return;
    const text = getBubbleText(ctx).slice(0, 100);
    try {
      const r = await galaxy.recall(text, 3);
      const box = document.createElement('div');
      box.style.cssText = 'margin-top:8px;padding:6px 10px;border-radius:4px;background:#4f9dff22;color:#4f9dff;font-size:11px;';
      const items = (r.results ?? []).map((m, i) =>
        `<div style="margin-top:4px;opacity:0.85">${i+1}. ${(m.content || m.text || JSON.stringify(m)).slice(0, 80)}…</div>`
      ).join('');
      box.innerHTML = `<b>📚 检索到 ${r.count ?? 0} 条相关记忆</b><br>${items}`;
      ctx?.element?.appendChild(box);
    } catch (e) { console.warn('[recall] failed:', e); }
  });

  registerHandler('save', async (ctx) => {
    if (!galaxy.saveMemory) return;
    const text = getBubbleText(ctx);
    try {
      const r = await galaxy.saveMemory(text, { source: 'msg_action_save' });
      appendNote(ctx?.element, `💾 已保存到长期记忆 (${r.memory_id?.slice(0, 8) || ''}…)`, '#10b981');
    } catch (e) { console.warn('[save] failed:', e); }
  });
}
