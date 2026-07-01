// renderer/src/main.js — entry point. Wires every module together.
//
// D 阶段（TokUI 组件深用）：[code] 代码高亮 / [notification] 通知 /
// [upd] 增量更新 / [dialog][progress][terminal] install-wizard。
//
// 模块图（架构在 A 阶段确立，未变）：
//   ipc/client        → window.galaxy (IPC bridge to sidecar)
//   state/*           → pub-sub stores (4 个)
//   tokui/runtime     → TokUI 适配层（UMD lazy load）
//   tokui/feed        → 高阶 DSL feed helper + code block 转换
//   tokui/handlers    → msg-action 回调 + notification 反馈
//   tokui/notify      → 全局 toast 通知系统
//   components/sidebar / composer / details / welcome / install-wizard
//                      → 用 TokUI DSL 渲染，[upd] 增量更新

import { bootTokUI, registerHandler } from './tokui/runtime.js';
import { registerMsgActionHandlers } from './tokui/handlers.js';
import { endAssistantStream, isStreaming } from './tokui/feed.js';
import { initSidebar, renderSidebar } from './components/sidebar.js';
import { initComposer, renderComposer, onComposerSend, setMode } from './components/composer.js';
import { initDetails } from './components/details.js';
import { renderWelcome } from './components/welcome.js';
import { initInstallWizard, openWizard } from './components/install-wizard.js';
import { openSettings } from './components/settings-panel.js';
import { openCommandPalette } from './tokui/polish.js';
import { setTheme } from './tokui/runtime.js';
import { sessionApi } from './state/session.js';

function installKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key === 'n') { e.preventDefault(); sessionApi.newSession(); return; }
    if (ctrl && e.key === 'b') { e.preventDefault(); document.querySelector('.sidebar')?.classList.toggle('hidden'); return; }
    if (ctrl && e.key === 'j') {
      e.preventDefault();
      const panel = document.getElementById('details-panel');
      if (panel) panel.classList.toggle('hidden');
      return;
    }
    if (ctrl && e.key === 'k') {
      e.preventDefault();
      openCommandPalette(async (cmdId) => {
        await handleCommand(cmdId);
      });
      return;
    }
    if (ctrl && e.shiftKey && e.key === 'D') {
      e.preventDefault();
      handleCommand('cmd-dsl-inspector');
      return;
    }
    if (ctrl && e.key === ',') {
      e.preventDefault();
      openSettings();
      return;
    }
    if (ctrl && e.shiftKey && e.key === 'T') {
      e.preventDefault();
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
  setTheme(next);
}

// ── Command palette handler ────────────────────────────────────

async function handleCommand(cmdId) {
  switch (cmdId) {
    case 'cmd-new-session':     sessionApi.newSession(); break;
    case 'cmd-toggle-sidebar':  document.querySelector('.sidebar')?.classList.toggle('hidden'); break;
    case 'cmd-toggle-details':  document.getElementById('details-panel')?.classList.toggle('hidden'); break;
    case 'cmd-clear-chat': {
      const c = document.getElementById('tokui-container');
      if (c) while (c.firstChild) c.removeChild(c.firstChild);
      break;
    }
    case 'cmd-dashboard': {
      const { renderDashboard } = await import('./tokui/dashboard.js');
      renderDashboard('details-host');
      const panel = document.getElementById('details-panel');
      if (panel) panel.classList.remove('hidden');
      break;
    }
    case 'cmd-memories': {
      const { fetchAndShowMemories } = await import('./tokui/memory-browser.js');
      fetchAndShowMemories('details-host', '', 10, 'timeline');
      const panel = document.getElementById('details-panel');
      if (panel) panel.classList.remove('hidden');
      break;
    }
    case 'cmd-dsl-inspector': {
      const { default: dslInspector } = await import('./tokui/dsl-inspector.js');
      const wasActive = dslInspector.isActive();
      dslInspector.setActive(!wasActive);
      if (!wasActive) dslInspector.clear();
      dslInspector.render('details-host');
      const panel = document.getElementById('details-panel');
      if (panel) panel.classList.remove('hidden');
      break;
    }
    case 'cmd-settings':        openSettings(); break;
    case 'cmd-theme-dark':      setTheme('dark'); break;
    case 'cmd-theme-modern-dark': setTheme('modern-dark'); break;
    case 'cmd-theme-default':   setTheme('default'); break;
    case 'cmd-theme-modern':    setTheme('modern'); break;
    default: break;
  }
}

// ── Welcome feature click → set composer mode + fill placeholder ──
registerHandler('onWelcomePick', (data) => {
  const value = typeof data === 'string' ? data : data?.value ?? data?.id;
  if (value) setMode(value);
});

(async () => {
  // 1. Boot TokUI
  await bootTokUI('#tokui-container');

  // 2. Register all event handlers
  registerMsgActionHandlers();

  // 3. Init stores + components
  initSidebar();
  initComposer();
  initDetails();
  initInstallWizard();
  // Expose openWizard() globally so sidebar [btn clk:onOpenWizard] can
  // call it via window.TokUI handler dispatch (TokUI click handlers
  // look up functions on window by name).
  window.openWizard = openWizard;

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

  console.log('[main] GalaxyOS renderer ready (TokUI D-stage)');
})();
