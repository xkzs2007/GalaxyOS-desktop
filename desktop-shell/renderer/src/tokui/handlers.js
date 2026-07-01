// renderer/src/tokui/handlers.js — TokUI msg-action handlers.
//
// D 阶段（TokUI 深用）：
//   - recall → [timeline] 记忆时间线渲染
//   - save   → [notification] 通知 + 记忆 ID 展示
//   - copy/like/dislike/verify 保留原有逻辑

import { registerHandler } from './runtime.js';
import { galaxy } from '../ipc/client.js';
import notify from './notify.js';
import { renderMemoryTimeline } from './memory-browser.js';

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
    navigator.clipboard.writeText(text).then(() => {
      notify.success('已复制到剪贴板', { duration: 2000 });
    }).catch(() => {});
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
    notify.info('已标记为有用', { duration: 2000 });
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
      const r = await galaxy.recall(text, 5);
      const memories = r?.results ?? [];

      if (!memories.length) {
        appendNote(evt?.element, '📚 未找到相关记忆', '#4f9dff');
        return;
      }

      // Render as a TokUI [timeline] below the bubble
      const resultHost = document.createElement('div');
      resultHost.className = 'memory-timeline-host';
      evt?.element?.appendChild(resultHost);

      renderMemoryTimeline(resultHost, memories, {
        title: `📚 检索到 ${r.count ?? memories.length} 条相关记忆`,
      });
    } catch (e) { console.warn('[recall] failed:', e); }
  });

  registerHandler('save', async (data, evt) => {
    if (!galaxy.saveMemory) return;
    const text = getBubbleText(evt);
    try {
      const r = await galaxy.saveMemory(text, { source: 'msg_action_save' });
      appendNote(evt?.element, `💾 已保存到长期记忆 (${r.memory_id?.slice(0, 8) || ''}…)`, '#10b981');
      notify.success('已保存到长期记忆', { duration: 2500 });
    } catch (e) { console.warn('[save] failed:', e); }
  });
}
