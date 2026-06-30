// renderer/src/main.js — entry point. Wires every module together.
//
// C 阶段：删掉 122 行手写逻辑，改为 ~40 行 TokUI-first boot 流程。
//
// 模块图（架构在 A 阶段确立，未变）：
//   ipc/client        → window.galaxy (IPC bridge to sidecar)
//   state/*           → pub-sub stores (4 个)
//   tokui/runtime     → TokUI 适配层（UMD lazy load）
//   tokui/feed        → 高阶 DSL feed helper
//   tokui/handlers    → msg-action 回调
//   components/sidebar / composer / details / welcome
//                      → 用 TokUI DSL 渲染

import { bootTokUI, getInstance, registerHandler } from './tokui/runtime.js';
import { registerMsgActionHandlers } from './tokui/handlers.js';
import { startAssistantStream, feed, endAssistantStream, isStreaming } from './tokui/feed.js';
import { initSidebar, renderSidebar } from './components/sidebar.js';
import { initComposer, renderComposer, onComposerSend, setMode } from './components/composer.js';
import { initDetails } from './components/details.js';
import { renderWelcome } from './components/welcome.js';
import { sessionStore, sessionApi } from './state/session.js';

function installKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key === 'n') { e.preventDefault(); sessionApi.newSession(); return; }
    if (ctrl && e.key === 'b') { e.preventDefault(); document.querySelector('.sidebar')?.classList.toggle('hidden'); return; }
    if (ctrl && e.key === 'k') {
      e.preventDefault();
      const c = document.getElementById('tokui-container');
      if (c) while (c.firstChild) c.removeChild(c.firstChild);
      return;
    }
    if (ctrl && e.key === ',') {
      e.preventDefault();
      // Theme picker dialog — defer to settings page (future)
      cycleTheme();
      return;
    }
    if (e.key === 'Escape') {
      if (isStreaming()) {
        // 简单兜底：结束当前流
        endAssistantStream();
      }
    }
  });
}

const THEMES = ['modern-dark', 'modern', 'dark', 'default'];
function cycleTheme() {
  const cur = (window.TokUI?.getTheme?.() ?? 'modern-dark');
  const idx = THEMES.indexOf(cur);
  const next = THEMES[(idx + 1) % THEMES.length];
  window.TokUI?.setTheme?.(next);
  console.log('[main] theme →', next);
}

// ── Welcome feature click → set composer mode + fill placeholder ──
registerHandler('onWelcomePick', (data) => {
  const value = typeof data === 'string' ? data : data?.value ?? data?.id;
  if (value) setMode(value);
});

(async () => {
  // 1. Boot TokUI
  const ui = await bootTokUI('#tokui-container');
  window._tokuiInstance = ui;  // for composer.js to access

  // 2. Register all event handlers
  registerMsgActionHandlers();

  // 3. Init stores + components
  initSidebar();
  initComposer();
  initDetails();

  // 4. Wire regenerate (msg-action → composer)
  window.addEventListener('composer:regenerate', (e) => {
    onComposerSend(e.detail.text);
  });

  // 5. Render initial UI
  renderSidebar();
  renderComposer();
  renderWelcome();

  // 6. Install keyboard shortcuts
  installKeyboardShortcuts();

  console.log('[main] GalaxyOS renderer ready (TokUI C-stage)');
})();
