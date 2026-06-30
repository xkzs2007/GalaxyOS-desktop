// renderer/src/main.js — entry point. Wires every module together.
//
// Module map:
//   ipc/client        → window.galaxy (IPC bridge to sidecar)
//   state/{session,connection,skills,settings}
//                      → pub-sub stores, replace the singleton state
//   tokui/runtime     → TokUI wrapper (with stub fallback)
//   tokui/feed        → high-level DSL feed helpers
//   tokui/handlers    → msg-action callbacks (copy/regen/like/verify/...)
//   components/{sidebar,composer,details}
//                      → DOM rendering + event wiring
//   components/command-palette, settings-modal (TODO)
//
// This file is the ONLY one that knows the boot order.

import { bootTokUI } from './tokui/runtime.js';
import { registerMsgActionHandlers } from './tokui/handlers.js';
import { startAssistantStream, feed, endAssistantStream } from './tokui/feed.js';
import { initSidebar } from './components/sidebar.js';
import { initComposer } from './components/composer.js';
import { initDetails } from './components/details.js';
import { sessionApi } from './state/session.js';

async function emitWelcomeIfEmpty() {
  const container = document.getElementById('tokui-container');
  if (!container || container.innerHTML.trim()) return;
  const ui = await bootTokUI(container);
  await startAssistantStream();
  feed(
    `[bubble role:ai model:GalaxyOS time:就绪]` +
    `[md]\n# 欢迎使用 GalaxyOS 桌面端\n\n` +
    `本机桌面版已脱离 OpenClaw，\`XiaoYiClawLLM\` 由 Python 子进程加载。\n\n` +
    `- **Ask 模式** 走 *ask()*，单步检索 + 答案\n` +
    `- **Process 模式** 走 *process()*，完整 R-CCAM 五阶段 + 推理链 + 工具调用\n` +
    `- **Agent 模式** 走 */sse/agent*，能跑 shell / 读文件 / 写文件 / 搜索 / 列目录\n\n` +
    `试试输入 *"今天我学了 R-CCAM 五阶段"*（用 Process 模式），或 *!ls -la*（用 Agent 模式）。\n` +
    `[/md]` +
    `[msg-actions copy regenerate like dislike visible][/msg-actions]` +
    `[/bubble]`
  );
  endAssistantStream();
}

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
    if (e.key === 'Escape') {
      const modal = document.getElementById('settings-modal');
      if (modal && !modal.hidden) modal.hidden = true;
    }
  });
}

function installContextMenu() {
  const menu = document.createElement('div');
  menu.className = 'context-menu';
  menu.hidden = true;
  document.body.appendChild(menu);

  function show(x, y, items) {
    menu.innerHTML = '';
    for (const item of items) {
      const el = document.createElement('div');
      el.className = 'ctx-item' + (item.danger ? ' danger' : '');
      el.textContent = item.label;
      el.addEventListener('click', () => { menu.hidden = true; item.action(); });
      menu.appendChild(el);
    }
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    menu.hidden = false;
  }

  document.addEventListener('click', () => { menu.hidden = true; });
  document.addEventListener('contextmenu', (e) => {
    const bubble = e.target.closest('[class*="bubble"]');
    if (!bubble) return;
    e.preventDefault();
    const text = bubble.innerText || '';
    show(e.clientX, e.clientY, [
      { label: '📋 复制', action: () => navigator.clipboard.writeText(text) },
      { label: '❌ 删除', danger: true, action: () => bubble.remove() },
    ]);
  });
}

(async () => {
  loadStyles();
  const container = document.getElementById('tokui-container');
  await bootTokUI(container);
  registerMsgActionHandlers();
  initSidebar();
  initComposer();
  initDetails();
  installKeyboardShortcuts();
  installContextMenu();
  await emitWelcomeIfEmpty();
  console.log('[main] GalaxyOS renderer ready');
})();

/**
 * Load the design-system CSS files in order: tokens → layout → components.
 * We do this dynamically so the legacy `style.css` doesn't shadow us,
 * and so a future build step can bundle these into a single file.
 */
function loadStyles() {
  const files = ['tokens.css', 'layout.css', 'components.css'];
  for (const f of files) {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = `src/styles/${f}`;
    document.head.appendChild(link);
  }
}
