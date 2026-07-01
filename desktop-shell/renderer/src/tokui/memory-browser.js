// renderer/src/tokui/memory-browser.js — GalaxyOS long-term memory visualisation.
//
// Uses TokUI [timeline] and [carousel] components to browse and navigate
// the agent's long-term memory entries retrieved via galaxy.recall().
//
// Two view modes:
//   timeline  — chronological stream with timestamps, colours, and categories
//   carousel  — swipeable card deck with previews and full-content drill-down
//
// Usage:
//   import { renderMemoryTimeline, renderMemoryCarousel, fetchAndShowMemories } from './memory-browser.js';
//
//   // Show recent memories in the details panel
//   fetchAndShowMemories('#details-host', '', 10);
//
//   // Show timeline in the chat after recall
//   renderMemoryTimeline('#tokui-container', memories);

import { getInstance } from './runtime.js';
import { escapeDsl } from '../utils.js';
import { galaxy } from '../ipc/client.js';
import { buildSkeleton } from './polish.js';

// ── Memory browser: timeline view ─────────────────────────────

/**
 * Render memories as a TokUI [timeline] in a container.
 * @param {string|HTMLElement} container - host selector or element
 * @param {Array} memories - array of { content/text, id, createdAt/timestamp, source, confidence }
 * @param {object} opts - { title, limit }
 */
export function renderMemoryTimeline(container, memories, opts = {}) {
  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  const title = opts.title ?? `记忆检索 · ${memories.length} 条`;
  const shown = (opts.limit && memories.length > opts.limit)
    ? memories.slice(0, opts.limit)
    : memories;

  if (!shown.length) {
    host.innerHTML = '';
    ui.startStream(host);
    ui.feed(`[card tt:"${escapeDsl(title)}"][p v:muted]暂无记忆条目[/p][/card]`);
    ui.endStream();
    return;
  }

  host.innerHTML = '';
  ui.startStream(host);

  // Card wrapper
  ui.feed(`[card tt:"${escapeDsl(title)}" v:highlight]`);

  // Timeline with alternating layout for readable density
  ui.feed(`[timeline alternate]`);

  for (let i = 0; i < shown.length; i++) {
    const m = shown[i];
    const content = m.content || m.text || JSON.stringify(m);
    const snippet = content.length > 120 ? content.slice(0, 117) + '...' : content;
    const time = m.createdAt || m.timestamp || '';
    const timeLabel = time ? formatMemoryTime(time) : '';
    const source = m.source || '';
    const color = getMemoryColor(m);
    const alt = i % 2 === 1 ? 'alt-right' : 'alt-left';

    // Timeline item with memory content
    ui.feed(`[ti tt:"${escapeDsl(snippet)}" v:${color} ${alt} ${timeLabel ? `time:"${timeLabel}"` : ''}]`);
    if (source) {
      ui.feed(`  [tag sm v:muted]${escapeDsl(source)}[/tag]`);
    }
    if (m.id || m.memory_id) {
      ui.feed(`  [span sm v:muted]${escapeDsl(m.id || m.memory_id || '').slice(0, 12)}[/span]`);
    }
    ui.feed(`[/ti]`);
  }

  ui.feed(`[/timeline]`);
  // v9.6: pagination for large memory sets
  if (memories.length > (opts.limit || shown.length)) {
    const totalPages = Math.ceil(memories.length / (opts.limit || shown.length));
    ui.feed(`[pagination page:1 total:${totalPages} count:${memories.length} clk:onMemPage sm]`);
  }
  ui.feed(`[/card]`);
  ui.endStream();
}

// ── Memory browser: carousel view ─────────────────────────────

/**
 * Render memories as a TokUI [carousel] card deck.
 * Each slide is a memory card with full content preview.
 * @param {string|HTMLElement} container
 * @param {Array} memories
 * @param {object} opts - { title, limit }
 */
export function renderMemoryCarousel(container, memories, opts = {}) {
  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  const title = opts.title ?? `记忆卡片 · ${memories.length} 条`;
  const shown = (opts.limit && memories.length > opts.limit)
    ? memories.slice(0, opts.limit)
    : memories;

  if (!shown.length) {
    host.innerHTML = '';
    ui.startStream(host);
    ui.feed(`[card tt:"${escapeDsl(title)}"][p v:muted]暂无记忆条目[/p][/card]`);
    ui.endStream();
    return;
  }

  host.innerHTML = '';
  ui.startStream(host);

  ui.feed(`[card tt:"${escapeDsl(title)}" v:highlight]`);
  ui.feed(`[carousel)`);

  for (const m of shown) {
    const content = m.content || m.text || JSON.stringify(m);
    const srcName = m.source || '系统记忆';
    const time = m.createdAt || m.timestamp || '';
    const timeLabel = time ? formatMemoryTime(time) : '';
    const color = getMemoryColor(m);

    // Each carousel slide is a compact memory card
    ui.feed(`[carousel-slide]`);
    ui.feed(`  [card tt:"${escapeDsl(srcName)}" v:${color}]`);
    ui.feed(`    [p]${escapeDsl(content.slice(0, 300))}${content.length > 300 ? '…' : ''}[/p]`);
    if (timeLabel) {
      ui.feed(`    [row]`);
      ui.feed(`      [tag sm v:muted]${timeLabel}[/tag]`);
      if (m.id || m.memory_id) {
        ui.feed(`      [span sm v:muted]${escapeDsl((m.id || m.memory_id || '').slice(0, 10))}[/span]`);
      }
      ui.feed(`    [/row]`);
    }
    ui.feed(`  [/card]`);
    ui.feed(`[/carousel-slide]`);
  }

  ui.feed(`[/carousel]`);
  ui.feed(`[/card]`);
  ui.endStream();
}

// ── Convenience: fetch + show ──────────────────────────────────

/**
 * Fetch memories from the sidecar and render into a container.
 * @param {string|HTMLElement} container - '#details-host' or element
 * @param {string} query - search query (empty = recent)
 * @param {number} [topK=10] - max results
 * @param {'timeline'|'carousel'} [view='timeline']
 */
export async function fetchAndShowMemories(container, query = '', topK = 10, view = 'timeline') {
  if (!galaxy.recall) return;

  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  // Show loading skeleton
  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(`[card tt:"加载记忆…"]`);
  ui.feed(buildSkeleton('chat'));
  ui.feed(`[/card]`);
  ui.endStream();

  try {
    const r = await galaxy.recall(query, topK);
    const memories = r?.results ?? [];
    if (view === 'carousel') {
      renderMemoryCarousel(host, memories, {
        title: query ? `检索: ${query} · ${memories.length} 条` : `最近记忆 · ${memories.length} 条`,
      });
    } else {
      renderMemoryTimeline(host, memories, {
        title: query ? `检索: ${query} · ${memories.length} 条` : `最近记忆 · ${memories.length} 条`,
      });
    }
  } catch (e) {
    host.innerHTML = '';
    ui.startStream(host);
    ui.feed(`[card tt:"记忆加载失败"][callout t:danger tt:"错误"]${escapeDsl(e.message ?? String(e))}[/callout][/card]`);
    ui.endStream();
  }
}

// ── Demo builder ───────────────────────────────────────────────

/**
 * Build a static demo memory timeline for welcome/help pages.
 */
export function buildDemoMemoryTimeline() {
  return `[card tt:"🧬 长期记忆示意 · 3 条" v:highlight]\n` +
    `  [timeline alternate]\n` +
    `    [ti tt:"R-CCAM 是一个五阶段结构化认知循环，包含 Retrieval → Cognition → Control → Action → Memory" v:info alt-left time:"3 小时前"]\n` +
    `      [tag sm v:muted]agent[/tag]\n` +
    `    [/ti]\n` +
    `    [ti tt:"GalaxyOS 使用 BGE-M3 作为向量模型，部署在 ONNX Runtime 上以降低推理延迟" v:success alt-right time:"昨天"]\n` +
    `      [tag sm v:muted]meeting[/tag]\n` +
    `    [/ti]\n` +
    `    [ti tt:"用户偏好使用 TypeScript + Vite 进行前端开发，数据库首选 SQLite" v:warning alt-left time:"2 天前"]\n` +
    `      [tag sm v:muted]user[/tag]\n` +
    `    [/ti]\n` +
    `  [/timeline]\n` +
    `[/card]`;
}

// ── Helpers ────────────────────────────────────────────────────

function formatMemoryTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMs / 3600000);
    const diffDay = Math.floor(diffMs / 86400000);

    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return `${diffMin} 分钟前`;
    if (diffHr < 24) return `${diffHr} 小时前`;
    if (diffDay < 7) return `${diffDay} 天前`;
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  } catch {
    return String(ts).slice(0, 10);
  }
}

function getMemoryColor(m) {
  const source = (m.source || '').toLowerCase();
  if (source.includes('user')) return 'info';
  if (source.includes('agent')) return 'success';
  if (source.includes('system')) return 'warning';
  if (source.includes('error')) return 'danger';
  return 'info';
}

// ── P1: 搜索增强的记忆面板 ─────────────────────────────────

import { registerHandler } from './runtime.js';

let _memoryQuery = '';
let _memoryView = 'timeline';

/**
 * 渲染带搜索的记忆面板到 details-host。
 */
export async function renderMemorySearchPanel(container) {
  const host = typeof container === 'string' ? document.getElementById(container) : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  ui.startStream(host);
  ui.feed(`[card tt:"🧬 长期记忆" v:highlight]`);
  ui.feed(`  [row]`);
  ui.feed(`    [input id:memory-search-input placeholder:"搜索记忆…" sm flex:1][/input]`);
  ui.feed(`    [btn tx:"🔍" clk:onMemorySearch sm v:accent]`);
  ui.feed(`  [/row]`);
  ui.feed(`  [btngroup]`);
  ui.feed(`    [btn tx:"⏱ 时间线" clk:onMemoryViewTimeline sm v:${_memoryView === 'timeline' ? 'accent' : 'muted'}]`);
  ui.feed(`    [btn tx:"🃏 卡片" clk:onMemoryViewCarousel sm v:${_memoryView === 'carousel' ? 'accent' : 'muted'}]`);
  ui.feed(`  [/btngroup]`);
  ui.feed(`  [dv id:memory-search-results][/dv]`);
  ui.feed(`[/card]`);
  ui.endStream();

  _memoryQuery = '';
  _memoryView = 'timeline';
  await fetchAndShowMemories('memory-search-results', '', 20, 'timeline');
}

registerHandler('onMemorySearch', async () => {
  const input = document.getElementById('memory-search-input');
  const q = input?.value?.trim() || '';
  _memoryQuery = q;
  await fetchAndShowMemories('memory-search-results', q, 20, _memoryView);
});

registerHandler('onMemoryViewTimeline', async () => {
  _memoryView = 'timeline';
  await fetchAndShowMemories('memory-search-results', _memoryQuery, 20, 'timeline');
});

registerHandler('onMemoryViewCarousel', async () => {
  _memoryView = 'carousel';
  await fetchAndShowMemories('memory-search-results', _memoryQuery, 20, 'carousel');
});
