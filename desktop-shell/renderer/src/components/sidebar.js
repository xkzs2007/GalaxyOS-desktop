// renderer/src/components/sidebar.js — left sidebar (sessions + skills + status).
//
// C 阶段：用 [conversations][conv ...] 替换手写 ul/li session list。
// TokUI 组件自带：
//   - 列表渲染（按 sessions.order）
//   - active 态高亮
//   - clk:onConvSwitch 切换会话
//   - 标题/时间/hover 删除（通过 onConvAction handler 实现）
//
// skills 列表保留为 [card][list] 占位（次要功能，留作下一迭代）。
// 健康状态点用 [dot] 组件。

import { sessionStore, sessionApi } from '../state/session.js';
import { skillsStore, loadSkills } from '../state/skills.js';
import { connectionStore, startHealthCheck } from '../state/connection.js';
import { bootTokUI, registerHandler } from '../tokui/runtime.js';

const $ = (id) => document.getElementById(id);

function renderConversations() {
  const s = sessionStore.get();
  if (!s.order.length) return '[p v:muted]暂无会话[/p]';
  const items = s.order.map((id) => {
    const sess = s.byId[id];
    if (!sess) return '';
    const active = id === s.activeId ? 'active' : '';
    return `[conv tt:"${escapeDsl(sess.title)}" time:"${formatTime(sess.createdAt)}" act:${id} ${active}]`;
  }).join('\n  ');
  return `[conversations clk:onConvSwitch act:conv-list]\n  ${items}\n[/conversations]`;
}

function renderSkillList() {
  const { list, loading } = skillsStore.get();
  if (loading) return `[p v:muted]加载技能中…[/p]`;
  if (!list.length) return `[p v:muted]暂无技能[/p]`;
  // Use [list] for compact pill display
  const shown = list.slice(0, 12);
  const items = shown.map((s) =>
    `[li clk:onSkillOpen act:${s.id}]${escapeDsl(s.name || s.id)}[/li]`
  ).join('\n  ');
  return `[card tt:"已加载技能 ${list.length}"]\n  [list sm]\n  ${items}\n  [/list]\n[/card]`;
}

function renderConnection() {
  const s = connectionStore.get();
  const dot = s.status === 'ok' ? 'ok' : s.status === 'error' ? 'err' : '';
  const text = s.status === 'ok' ? '已连接' : s.status === 'error' ? '连接失败' : '连接中…';
  return `[row]\n  [dot ${dot}][span]${text}[/span] ${s.detail ? `[span v:muted sm]${escapeDsl(s.detail)}[/span]` : ''}\n[/row]`;
}

function renderSidebar() {
  const host = $('sidebar-host');
  if (!host) return;
  bootTokUI().then((ui) => {
    if (!ui) return;
    host.innerHTML = '';
    ui.startStream(host);
    ui.feed(`[card tt:"GalaxyOS 桌面端" v:highlight]`);
    ui.feed(renderConversations());
    ui.feed(`[/card]`);
    ui.feed(renderSkillList());
    ui.feed(`[card tt:"状态"]${renderConnection()}[/card]`);
    ui.feed(`[toolbar pos:bottom align:right]`);
    ui.feed(`  [btn tx:"+ 新对话" v:primary sm clk:onNewChat]`);
    ui.feed(`  [btn tx:"📥 下载模型" sm clk:onOpenWizard]`);
    ui.feed(`[/toolbar]`);
    ui.endStream();
  });
}

// ── Event handlers ────────────────────────────────────────────
registerHandler('onNewChat', () => sessionApi.newSession());

registerHandler('onOpenWizard', () => {
  // openWizard is exposed on window by main.js after initInstallWizard()
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
    // Bubble up as CustomEvent so details.js can render
    window.dispatchEvent(new CustomEvent('skill:open', { detail: { id } }));
  }
});

function escapeDsl(s) {
  if (s.includes('[') || s.includes(']') || s.includes('"')) {
    return '"' + String(s ?? '').replace(/"/g, '\\"') + '"';
  }
  return s;
}

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

export { renderSidebar };

export function initSidebar() {
  sessionStore.subscribe(renderSidebar);
  skillsStore.subscribe(renderSidebar);
  connectionStore.subscribe(renderSidebar);
  loadSkills();
  startHealthCheck(30000);
  // initial render
  renderSidebar();
}
