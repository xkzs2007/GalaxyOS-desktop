// renderer/src/tokui/polish.js — UX polish helpers (D 阶段).
//
//   buildSkeleton()     → TokUI [skeleton] loading placeholder
//   buildEmpty()        → TokUI [empty] friendly empty-state
//   buildDiff()         → TokUI [diff] code comparison
//   buildCommandPalette → TokUI [command] Ctrl+K search palette
//   openDiffView()      → Render a diff in the details panel
//
// All return DSL strings usable with ui.feed() or render().

import { getInstance } from './runtime.js';
import { escapeDsl } from '../utils.js';

// ── Skeleton (loading placeholder) ────────────────────────────

/** Build a skeleton loading card for chat or panel */
export function buildSkeleton(type = 'card') {
  switch (type) {
    case 'chat':
      return `[shimmer rows:4]\n`;
    case 'card':
    default:
      return `[skeleton v:card]`;
  }
}

/** Show skeleton then replace with content. Returns a cleanup fn. */
export function showSkeleton(container, type = 'card') {
  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return () => {};

  const ui = getInstance();
  if (!ui) return () => {};

  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(buildSkeleton(type));
  ui.endStream();

  return () => {
    host.innerHTML = '';
  };
}

// ── Empty state ───────────────────────────────────────────────

/** Build an empty-state placeholder */
export function buildEmpty(title = '暂无数据', desc = '', icon = 'inbox') {
  const descAttr = desc ? ` desc:"${escapeDsl(desc)}"` : '';
  return `[empty tt:"${escapeDsl(title)}"${descAttr} i:${icon}]`;
}

// ── Code diff view ────────────────────────────────────────────

/**
 * Build a TokUI [diff] component for side-by-side code comparison.
 * TokUI diff uses [diff__line--add], [diff__line--remove] variants.
 */
export function buildDiff(filePath, oldCode, newCode, lang = '') {
  const langAttr = lang ? ` lang:${lang}` : '';
  return `[diff tt:"${escapeDsl(filePath)}"${langAttr}]\n` +
    oldCode.split('\n').map(l => `  [-]${escapeDsl(l)}`).join('\n') + '\n' +
    newCode.split('\n').map(l => `  [+]${escapeDsl(l)}`).join('\n') + '\n' +
    `[/diff]`;
}

// ── Command palette ───────────────────────────────────────────

/**
 * Build a command palette DSL. Registered via Ctrl+K in main.js.
 * Commands should be pre-registered with onCommandSelect handler.
 */
export function buildCommandPalette() {
  return `[command tt:"命令面板" placeholder:"搜索命令…" clk:onCommandSelect]\n` +
    `  [command__group tt:"会话"]\n` +
    `    [command__item id:cmd-new-session tt:"+ 新对话" desc:"Ctrl+N"]\n` +
    `    [command__item id:cmd-toggle-sidebar tt:"切换侧边栏" desc:"Ctrl+B"]\n` +
    `    [command__item id:cmd-toggle-details tt:"切换详情面板" desc:"Ctrl+J"]\n` +
    `    [command__item id:cmd-clear-chat tt:"清空对话" desc:""]\n` +
    `  [/command__group]\n` +
    `  [command__group tt:"视图"]\n` +
    `    [command__item id:cmd-dashboard tt:"仪表盘" desc:"系统状态 + 统计"]\n` +
    `    [command__item id:cmd-memories tt:"记忆浏览" desc:"时间线 + 搜索"]\n` +
    `    [command__item id:cmd-dsl-inspector tt:"DSL Inspector" desc:"查看原始 DSL 源码"]\n` +
    `    [command__item id:cmd-mcp-panel tt:"MCP 工具面板" desc:"发现与管理 MCP 工具"]\n` +
    `    [command__item id:cmd-settings tt:"设置" desc:"Ctrl+,"]\n` +
    `  [/command__group]\n` +
    `  [command__group tt:"主题"]\n` +
    `    [command__item id:cmd-theme-dark tt:"Dark 暗色" desc:""]\n` +
    `    [command__item id:cmd-theme-modern-dark tt:"Modern Dark" desc:"推荐"]\n` +
    `    [command__item id:cmd-theme-default tt:"Default 默认" desc:""]\n` +
    `    [command__item id:cmd-theme-modern tt:"Modern 亮色" desc:""]\n` +
    `  [/command__group]\n` +
    `[/command]`;
}

/**
 * Show command palette overlay and register result handler.
 * @returns cleanup function
 */
export function openCommandPalette(onSelect) {
  const host = document.createElement('div');
  host.id = 'command-palette-host';
  host.style.cssText = 'position:fixed;inset:0;z-index:10001;display:flex;align-items:flex-start;justify-content:center;padding-top:80px;background:rgba(0,0,0,0.3);';

  // Remove previous
  const prev = document.getElementById('command-palette-host');
  if (prev) prev.remove();
  document.body.appendChild(host);

  const ui = getInstance();
  if (!ui) return () => {};

  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(buildCommandPalette());
  ui.endStream();

  // Click outside to close
  const onClickOutside = (e) => {
    if (e.target === host) closePalette();
  };
  host.addEventListener('click', onClickOutside);

  // Store callback
  host._onSelect = onSelect;

  function closePalette() {
    host.removeEventListener('click', onClickOutside);
    host.remove();
  }

  return closePalette;
}

// ── Diff view (rendered in details panel) ──────────────────────

/**
 * Open a diff view in the details panel.
 * @param {string} filePath
 * @param {string} label  — e.g. "修改前" / "修改后"
 * @param {object} before — { path, content, lang }
 * @param {object} after  — { path, content, lang }
 */
export function openDiffView(filePath, before, after) {
  const host = document.getElementById('details-host');
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  const lang = after.lang || before.lang || '';
  const beforePath = before.path || '修改前';
  const afterPath = after.path || '修改后';

  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(`[card tt:"📝 文件变更: ${escapeDsl(filePath)}"]`);
  ui.feed(buildDiff(beforePath, before.content || '', '', lang));
  ui.feed(buildDiff(afterPath, '', after.content || '', lang));
  ui.feed(`[/card]`);
  ui.endStream();
}
