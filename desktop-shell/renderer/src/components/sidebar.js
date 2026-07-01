// renderer/src/components/sidebar.js — left sidebar (sessions + skills + status).
//
// D 阶段（[upd] 增量更新）：
//   - 初始渲染保持 [conversations][conv] 全量（含 id 属性）
//   - 会话切换 / 新建 / 删除 / 重命名用 [upd id:conv-XXX ...] 增量更新
//   - 连接状态变化用 [upd id:conn-dot/conn-text] 增量更新
//   - 技能列表变化仍需全量重渲染（list items 动态 id 管理成本高）
//
// TokUI 的 [upd] 支持更新：id, v, act, tt, tx, bg, fc 等属性。

import { sessionStore, sessionApi } from '../state/session.js';
import { skillsStore, loadSkills } from '../state/skills.js';
import { connectionStore, startHealthCheck } from '../state/connection.js';
import { bootTokUI, registerHandler, getInstance } from '../tokui/runtime.js';
import { escapeDsl } from '../utils.js';
import notify from '../tokui/notify.js';
import { fetchAndShowMemories } from '../tokui/memory-browser.js';

const $ = (id) => document.getElementById(id);

// ── Track last known state for diff-based [upd] ──────────────────
let _lastSessions = [];
let _lastActiveId = null;
let _lastConn = { status: '', detail: '' };

// ── DSL builders (full render) ──────────────────────────────────

function buildConversationsDSL() {
  const s = sessionStore.get();
  if (!s.order.length) return '[p v:muted]暂无会话[/p]';
  const items = s.order.map((id) => {
    const sess = s.byId[id];
    if (!sess) return '';
    const active = id === s.activeId ? 'active' : '';
    return `[conv id:"conv-${id}" tt:"${escapeDsl(sess.title)}" time:"${formatTime(sess.createdAt)}" act:${id} ${active}]`;
  }).join('\n  ');
  _lastSessions = s.order;
  _lastActiveId = s.activeId;
  return `[conversations clk:onConvSwitch act:conv-list]\n  ${items}\n[/conversations]`;
}

function buildSkillListDSL() {
  const { list, loading } = skillsStore.get();
  if (loading) return `[p v:muted]加载技能中…[/p]`;
  if (!list.length) return `[p v:muted]暂无技能[/p]`;
  const shown = list.slice(0, 12);
  const items = shown.map((s) =>
    `[li clk:onSkillOpen act:${s.id}]${escapeDsl(s.name || s.id)}[/li]`
  ).join('\n  ');
  return `[card tt:"已加载技能 ${list.length}"]\n  [list sm]\n  ${items}\n  [/list]\n[/card]`;
}

function buildConnectionDSL() {
  const s = connectionStore.get();
  _lastConn = { status: s.status, detail: s.detail };
  const dot = s.status === 'ok' ? 'ok' : s.status === 'error' ? 'err' : '';
  const text = s.status === 'ok' ? '已连接' : s.status === 'error' ? '连接失败' : '连接中…';
  return `[row]\n  [dot id:conn-dot ${dot}][span id:conn-text]${text}[/span] ${s.detail ? `[span v:muted sm id:conn-detail]${escapeDsl(s.detail)}[/span]` : ''}\n[/row]`;
}

/** Full initial render — used on first boot and skill list changes */
async function renderSidebarFull() {
  const host = $('sidebar-host');
  if (!host) return;
  const ui = await bootTokUI();
  if (!ui) return;
  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(`[card tt:"GalaxyOS 桌面端" v:highlight]`);
  ui.feed(buildConversationsDSL());
  ui.feed(`[/card]`);
  ui.feed(buildSkillListDSL());
  ui.feed(`[card tt:"状态"]${buildConnectionDSL()}[/card]`);
  ui.feed(`[toolbar pos:bottom align:right]`);
  ui.feed(`  [btn tx:"+ 新对话" v:primary sm clk:onNewChat]`);
  ui.feed(`  [btn tx:"🧬 记忆" sm clk:onBrowseMemories]`);
  ui.feed(`  [btn tx:"📥 下载模型" sm clk:onOpenWizard]`);
  ui.feed(`[/toolbar]`);
  ui.endStream();
}

// ── Incremental update helpers (use [upd]) ─────────────────────

async function feedUpdate(dsl) {
  const ui = getInstance();
  if (!ui) return;
  const host = $('sidebar-host');
  if (!host) return;
  ui.startStream(host);
  ui.feed(dsl);
  ui.endStream();
}

/** Update connection status using [upd] — no full rebuild */
function updateConnectionIncr() {
  const s = connectionStore.get();
  if (s.status !== _lastConn.status) {
    const dot = s.status === 'ok' ? 'ok' : s.status === 'error' ? 'err' : '';
    feedUpdate(`[upd id:conn-dot v:${dot}]`);
  }
  if (s.status !== _lastConn.status) {
    const text = s.status === 'ok' ? '已连接' : s.status === 'error' ? '连接失败' : '连接中…';
    feedUpdate(`[upd id:conn-text tx:${text}]`);
  }
  if (s.detail !== _lastConn.detail) {
    feedUpdate(`[upd id:conn-detail tx:${escapeDsl(s.detail || '')}]`);
  }
  _lastConn = { status: s.status, detail: s.detail };
}

/** Incrementally update conversation list */
function updateSessionsIncr() {
  const s = sessionStore.get();
  const newOrder = s.order;
  const newActiveId = s.activeId;

  // If sessions were added or removed, do a full rebuild
  // (simpler & more reliable than manual DOM surgery for list changes)
  if (newOrder.length !== _lastSessions.length ||
      !newOrder.every((id, i) => id === _lastSessions[i])) {
    renderSidebarFull();
    return;
  }

  // Only active session changed — use [upd] to toggle 2 elements
  if (newActiveId !== _lastActiveId) {
    const updates = [];
    if (_lastActiveId) updates.push(`[upd id:conv-${_lastActiveId} act:inactive]`);
    if (newActiveId)   updates.push(`[upd id:conv-${newActiveId} act:active]`);
    if (updates.length) feedUpdate(updates.join(''));
  }

  _lastSessions = newOrder;
  _lastActiveId = newActiveId;
}

// ── Event handlers ────────────────────────────────────────────

registerHandler('onNewChat', () => {
  const s = sessionApi.newSession();
  // Instead of full rebuild, prepend the new [conv] element
  // by feeding it directly into the conversations container.
  // (TokUI slotStack: since we're inside the sidebar host's
  //  streaming session, the [conv] will append to the current
  //  container — which is the sidebar-host root. That works
  //  because after initial render, the conversations list is
  //  already there. For cleanliness, we trigger a full rebuild.)
  renderSidebarFull();
  notify.success('已创建新会话', { duration: 2000 });
});

registerHandler('onOpenWizard', () => {
  if (typeof window.openWizard === 'function') window.openWizard();
  else console.warn('[sidebar] window.openWizard not ready');
});

registerHandler('onConvSwitch', (data) => {
  const id = typeof data === 'string' ? data : data?.value ?? data?.id;
  if (id) sessionApi.activate(id);
});

registerHandler('onSkillOpen', (data) => {
  const id = typeof data === 'string' ? data : data?.value ?? data?.id;
  if (id) {
    window.dispatchEvent(new CustomEvent('skill:open', { detail: { id } }));
  }
});

registerHandler('onBrowseMemories', () => {
  fetchAndShowMemories('details-host', '', 10, 'timeline');
  // Open the details panel if collapsed
  const panel = document.getElementById('details-panel');
  if (panel) panel.classList.remove('hidden');
  notify.info('正在加载记忆时间线…', { duration: 2000 });
});

// ── Time formatting ────────────────────────────────────────────

function isToday(ts) {
  const d = new Date(ts);
  return d.toDateString() === new Date().toDateString();
}
function formatTime(ts) {
  if (isToday(ts)) {
    return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }
  const d = new Date(ts);
  const days = ['日', '一', '二', '三', '四', '五', '六'];
  return days[d.getDay()];
}

export { renderSidebarFull as renderSidebar };

// ── Init ───────────────────────────────────────────────────────

export function initSidebar() {
  // Session changes: use incremental updates when possible
  sessionStore.subscribe(updateSessionsIncr);
  // Skills changes: full rebuild (list items are dynamic)
  skillsStore.subscribe(renderSidebarFull);
  // Connection changes: use [upd] for dot + text
  connectionStore.subscribe(updateConnectionIncr);

  loadSkills();
  startHealthCheck(30000);
  // Initial full render
  renderSidebarFull();
}
