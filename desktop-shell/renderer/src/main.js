// renderer/src/main.js — entry point. Wires every module together.
//
// v10: router.js + error-boundary.js + lazy-loading + devtools

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
import { renderSetupPage } from './tokui/setup.js';
import { setTheme } from './tokui/runtime.js';
import { sessionApi } from './state/session.js';
import { register, start, navigate, lookup } from './router.js';
import { installGlobalHandler } from './error-boundary.js';

// ── v10: Global error handler (install early) ─────────────
installGlobalHandler();

// ── v10: Devtools ──────────────────────────────────────────
// Open Console → window.__debug.stores → get/set
//   window.__debug.ui().feed('[card tt:test]hello[/card]')
window.__debug = {
  ui: () => { const m = import('./tokui/runtime.js').then(r => r.getInstance()); return m.then(i => i.g); },
  stores: window.__stores,
  navigate: (p, q) => { window.location.hash = '#' + p + (q ? '?' + new URLSearchParams(q) : ''); },
  resetAll: () => { for (const s of (window.__stores?.list() || [])) s._reset?.(); location.reload(); },
};

// ── Keyboard shortcuts ─────────────────────────────────────

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

// ── Command palette handler (v10: router-based) ───────────

// Register all routes (replaces the old switch/case)
register({
  'cmd-new-session':         () => sessionApi.newSession(),
  'cmd-toggle-sidebar':      () => document.querySelector('.sidebar')?.classList.toggle('hidden'),
  'cmd-toggle-details':      () => document.getElementById('details-panel')?.classList.toggle('hidden'),
  'cmd-clear-chat':          () => { const c = document.getElementById('tokui-container'); if (c) while (c.firstChild) c.removeChild(c.firstChild); },
  'cmd-dashboard':           async () => { const { renderDashboard } = await import('./tokui/dashboard.js'); await renderDashboard('details-host'); document.getElementById('details-panel')?.classList.remove('hidden'); },
  'cmd-memories':            async () => { const { fetchAndShowMemories } = await import('./tokui/memory-browser.js'); fetchAndShowMemories('details-host', '', 10, 'timeline'); document.getElementById('details-panel')?.classList.remove('hidden'); },
  'cmd-dsl-inspector':       async () => { const { default: d } = await import('./tokui/dsl-inspector.js'); const was = d.isActive(); d.setActive(!was); if (!was) d.render('details-host'); document.getElementById('details-panel')?.classList.remove('hidden'); },
  'cmd-mcp-panel':           async () => { const { renderMcp } = await import('./components/mcp-panel.js'); renderMcp('details-host'); document.getElementById('details-panel')?.classList.remove('hidden'); },
  'cmd-settings':            () => openSettings(),
  'cmd-theme-dark':          () => setTheme('dark'),
  'cmd-theme-modern-dark':   () => setTheme('modern-dark'),
  'cmd-theme-default':       () => setTheme('default'),
  'cmd-theme-modern':        () => setTheme('modern'),
  'cmd-install-wizard':      () => openWizard(),
  'cmd-setup-page':          () => renderSetupPage(),
  'cmd-code-editor':         async () => { const { renderCodeEditor } = await import('./tokui/code-editor.js'); await renderCodeEditor('details-host', { id: 'main-editor', lang: 'python', value: '# GalaxyOS Code Editor\nprint("Hello, GalaxyOS!")\n', title: 'GalaxyOS', onSave: (code) => window.galaxy?.saveFile?.('scratch.py', code), onRun: (code) => window.galaxy?.runCode?.(code, 'python') }); document.getElementById('details-panel')?.classList.remove('hidden'); },
  'cmd-image-tools':          async () => { const { openImageTools } = await import('./tokui/image-tools.js'); await openImageTools(); },
  'cmd-memories-search':      async () => { const { renderMemorySearchPanel } = await import('./tokui/memory-browser.js'); await renderMemorySearchPanel('details-host'); document.getElementById('details-panel')?.classList.remove('hidden'); },
  'cmd-plan-panel':           async () => { const { renderPlanPanel } = await import('./tokui/plan.js'); renderPlanPanel('details-host'); document.getElementById('details-panel')?.classList.remove('hidden'); },
});

async function handleCommand(cmdId) {
  const handler = lookup(cmdId);
  if (handler) {
    await handler();
  } else {
    console.warn(`[main] 未注册的命令: ${cmdId}`);
  }
}

// ── First-launch setup wizard (TokUI DSL, see tokui/setup.js) ──

async function checkFirstLaunch() {
  try {
    const { isFirstLaunch } = await window.galaxy.isFirstLaunch();
    return isFirstLaunch;
  } catch {
    // IPC might not be ready yet; wait and retry once
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const { isFirstLaunch } = await window.galaxy.isFirstLaunch();
      return isFirstLaunch;
    } catch {
      console.warn('[main] isFirstLaunch check failed, assuming not first launch');
      return false;
    }
  }
}

// ── Welcome feature click → set composer mode + fill placeholder ──
registerHandler('onWelcomePick', (data) => {
  const value = typeof data === 'string' ? data : data?.value ?? data?.id;
  if (value) setMode(value);
});

(async () => {
  // 0. First-launch check — show setup wizard if never run before
  const isFirst = await checkFirstLaunch();
  if (isFirst) {
    await bootTokUI('#tokui-container');
    renderSetupPage();
    console.log('[main] GalaxyOS first-launch setup page rendered');
    return;
  }

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
