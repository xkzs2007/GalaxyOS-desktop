// renderer/src/components/sidebar.js — tree-navigation sidebar (v9.5).
//
// Sections: Conversations · Memory · Skills · MCP · Settings · Status
// Each section is a [card] with click-to-expand title bar.
// Uses [upd] for incremental updates where possible.

import { sessionStore, sessionApi } from '../state/session.js';
import { skillsStore, loadSkills } from '../state/skills.js';
import { connectionStore, startHealthCheck } from '../state/connection.js';
import { settingsStore } from '../state/settings.js';
import { bootTokUI, registerHandler, getInstance } from '../tokui/runtime.js';
import { galaxy } from '../ipc/client.js';
import { escapeDsl } from '../utils.js';
import notify from '../tokui/notify.js';
import { fetchAndShowMemories } from '../tokui/memory-browser.js';
import { renderDashboard } from '../tokui/dashboard.js';
import { buildEmpty } from '../tokui/polish.js';

const $ = (id) => document.getElementById(id);

// ── Section state (which sections are expanded) ────────────────
let _expanded = {
  sessions: true,
  memory: false,
  skills: false,
  mcp: false,
};

// ── Track last known state for diff-based [upd] ──────────────────
let _lastSessions = [];
let _lastActiveId = null;
let _lastConn = { status: '', detail: '' };

// ── Helpers ────────────────────────────────────────────────────

function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return '刚刚';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`;
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

// ── Section builder ────────────────────────────────────────────

/**
 * Render a collapsible sidebar section.
 * @param {string} key - unique section key
 * @param {string} icon - emoji icon
 * @param {string} title - section title
 * @param {string} badge - optional badge text (e.g. "69")
 * @param {string} contentDSL - content inside the section
 * @returns {string} TokUI DSL
 */
function renderSection(key, icon, title, badge, contentDSL) {
  const expanded = _expanded[key];
  const arrow = expanded ? '▾' : '▸';
  const badgeStr = badge ? ` [tag sm]${badge}[/tag]` : '';
  const expandAttr = `clk:onSidebarToggle act:${key}`;

  return `[card]\n` +
    `  [row ${expandAttr}]\n` +
    `    [span]${arrow} ${icon} ${escapeDsl(title)}${badgeStr}[/span]\n` +
    `  [/row]\n` +
    (expanded ? contentDSL : '') +
    `[/card]`;
}

// ── Section content builders ───────────────────────────────────

function buildSessionsContent() {
  const s = sessionStore.get();
  if (!s.order.length) return `    [p v:muted]暂无会话[/p]\n    [btn tx:"+ 新对话" clk:onConvNew sm][/btn]`;

  _lastSessions = s.order;
  _lastActiveId = s.activeId;

  const items = s.order.map((id) => {
    const sess = s.byId[id];
    if (!sess) return '';
    const active = id === s.activeId ? 'active' : '';
    const title = sess.title || '新对话';
    return `[conv id:"conv-${id}" tt:"${escapeDsl(title)}" time:"${formatTime(sess.createdAt)}" act:${id} ${active}]`;
  }).join('\n    ');

  return `    [list sm]\n${items}\n    [/list]\n` +
    `    [row]\n      [btn tx:"+ 新工作区" clk:onConvNew sm][/btn]\n    [/row]`;
}

function buildMemorySection() {
  return renderSection('memory', '🧠', '记忆', null, buildMemoryContent());
}

function buildMemoryContent() {
  return `    [badge-box t:info label:"长期记忆" dot]\n` +
    `    [p v:muted sm]浏览与搜索记忆时间线[/p]\n` +
    `    [btn tx:"📋 查看时间线" clk:onMemOpenTimeline sm][/btn]\n` +
    `    [btn tx:"🔍 搜索记忆" clk:onMemSearch sm v:muted][/btn]\n` +
    `    [/badge-box]`;
}

function buildSkillsSection() {
  const { list, loading } = skillsStore.get();
  const count = list.length || 69;
  return renderSection('skills', '🧩', '工具与技能', `${count}`, buildSkillsContent());
}

function buildSkillsContent() {
  const { list, loading } = skillsStore.get();
  if (loading) return `    [p v:muted]加载中…[/p]`;
  if (!list.length) {
    // Trigger load if needed
    loadSkills();
    return `    [p v:muted]加载中…[/p]`;
  }

  // Categories
  const categories = {};
  for (const s of list) {
    const cat = guessCategory(s.id, s.description);
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(s);
  }

  let dsl = `    [input id:sidebar-skill-search ph:"搜索技能…" clk:onSkillSearch sm v:muted][/input]\n`;
  for (const [cat, skills] of Object.entries(categories)) {
    dsl += `    [p v:muted sm]${cat} (${skills.length})[/p]\n`;
    dsl += `    [list sm]\n`;
    for (const s of skills.slice(0, 6)) {
      dsl += `      [li clk:onSkillOpen act:${s.id}]${escapeDsl(s.name || s.id)}[/li]\n`;
    }
    if (skills.length > 6) dsl += `      [li v:muted]... +${skills.length - 6} 更多[/li]\n`;
    dsl += `    [/list]\n`;
  }
  return dsl;
}

/** Heuristic: guess skill category from id and description. */
function guessCategory(id, desc) {
  const d = (id + ' ' + (desc || '')).toLowerCase();
  if (/think|reason|cognit|critic|decision|analyz|problem|logic|system|first-princip|feynman|zoom|overall|plan|strate/i.test(d)) return '🧠 推理方法论';
  if (/code|react|tdd|test|debug|improve|architecture|program|python|javascript|typescript|api/i.test(d)) return '💻 编程开发';
  if (/pdf|docx|pptx|excel|markdown|markitdown|nano-pdf|read|write|extract|convert/i.test(d)) return '📄 文档处理';
  if (/search|web|find|investigat|research|deep-search|multi-search/i.test(d)) return '🔍 搜索研究';
  if (/agent|multi-agent|autonomous|proactive|execution|tool|self-improve/i.test(d)) return '🤖 Agent 自动化';
  if (/design|prototype|image|gen|seedream|visual|creative|brainstorm/i.test(d)) return '🎨 设计创意';
  if (/email|imap|smtp|news|communication|handoff/i.test(d)) return '✉️ 通讯';
  if (/language|translat|chinese|humanize|nlp|natural-language/i.test(d)) return '🌐 语言 NLP';
  return '📦 其他';
}

function buildMcpSection() {
  return renderSection('mcp', '🔧', 'MCP 工具', null, buildMcpContent());
}

// ── Handler helpers ────────────────────────────────────────────

let _showAddForm = false;

function buildMcpContent() {
  return `    [p v:muted sm]Model Context Protocol 工具集成[/p]\n` +
    `    [btn tx:"🔍 发现 MCP 工具" clk:onMcpDiscover sm][/btn]\n` +
    `    [btn tx:"+ 添加 MCP 服务器" clk:onMcpShowAddForm sm v:muted]\n` +
    `    [btn tx:"📋 查看工具表" clk:onMcpOpenPanel sm v:muted][/btn]`;
}

function buildConnectionDSL() {
  const conn = connectionStore.get();
  _lastConn = { status: conn.status, detail: conn.detail };

  if (conn.status === 'error') {
    // [callout] is a coloured alert block — perfect for disconnection warnings
    const detail = conn.detail ? `\n[detail: ${escapeDsl(conn.detail)}]` : '';
    return `[callout t:danger tt:"⚠ Sidecar 断连"]请确认 Python 进程正在运行${detail}[/callout]\n` +
      `[row]\n  [dot err][span sm]断连[/span]\n[/row]`;
  }

  if (conn.status === 'connecting') {
    return `[callout t:warn tt:"⏳ 连接中"]正在连接 GalaxyOS Sidecar 后端…[/callout]`;
  }

  // Get model info from settings
  const s = settingsStore.get();
  const llmModel = s.llm?.model || s.api_key ? 'V4 Flash' : '';

  const modelInfo = llmModel ? ` · ${llmModel}` : '';
  const slotInfo = s.llm?.enabled ? ' · LLM 已启用' : '';

  return `[row]\n  [dot ok][span sm]已连接${modelInfo}${slotInfo}[/span]\n[/row]`;
}

// ── Settings entry ─────────────────────────────────────────────

function buildSettingsEntry() {
  return `[btn tx:"⚙️ 设置" clk:onSettingsOpen sm v:muted][/btn]`;
}

// ── Full sidebar render ────────────────────────────────────────

export function renderSidebar() {
  const host = $('#sidebar-host');
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  // Load skills if not loaded
  loadSkills();

  host.innerHTML = '';
  ui.startStream(host);

  // Header
  ui.feed('[h4]⚡ GalaxyOS 工作台[/h4]');

  // Quick actions (workbench-oriented)
  ui.feed('[card tt:"工作流入口"]');
  ui.feed('  [row]');
  ui.feed('    [btn tx:"📊 仪表盘" clk:onDashboardOpen sm][/btn]');
  ui.feed('    [btn tx:"🧠 记忆时间线" clk:onMemOpenTimeline sm v:muted][/btn]');
  ui.feed('    [btn tx:"🔧 MCP 面板" clk:onMcpOpenPanel sm v:muted][/btn]');
  ui.feed('    [btn tx:"🧪 运行面板" clk:onDemoOpen sm v:muted][/btn]');
  ui.feed('  [/row]');
  ui.feed('  [p v:muted sm]管理会话、记忆与工具调用，保持执行链条清晰。[/p]');
  ui.feed('[/card]');

  // Sessions / Workspaces section
  ui.feed(renderSection('sessions', '📁', '工作区', null, buildSessionsContent()));

  // Memory section
  ui.feed(buildMemorySection());

  // Skills section
  ui.feed(buildSkillsSection());

  // MCP section
  ui.feed(buildMcpSection());

  // Settings + Status
  ui.feed(buildSettingsEntry());
  ui.feed(buildConnectionDSL());

  ui.endStream();
}

// ── Incremental updates ────────────────────────────────────────

function updateSidebarSessions() {
  const s = sessionStore.get();
  if (s.order === _lastSessions && s.activeId === _lastActiveId) return; // no change
  renderSidebar(); // session list: full re-render is simpler
}

function updateSidebarConnection() {
  const conn = connectionStore.get();
  if (conn.status === _lastConn.status && conn.detail === _lastConn.detail) return;
  // Full re-render when connection status changes — [callout] presence depends on status
  renderSidebar();
}

// ── Sidebar toggle for main view ───────────────────────────────

export function toggleSidebar() {
  document.querySelector('.sidebar')?.classList.toggle('hidden');
}

// ── Initialize ─────────────────────────────────────────────────

export function initSidebar() {
  // Subscribe to stores for live updates
  sessionStore.subscribe(() => updateSidebarSessions());
  connectionStore.subscribe(() => updateSidebarConnection());
  skillsStore.subscribe(() => renderSidebar());

  // Start health check
  startHealthCheck(30000);
}

// ── Handlers ──────────────────────────────────────────────────

registerHandler('onSidebarToggle', (data) => {
  const key = typeof data === 'string' ? data : data?.act || data?.value || '';
  if (!key) return;
  _expanded[key] = !_expanded[key];
  renderSidebar();
});

registerHandler('onConvSwitch', (data) => {
  const id = typeof data === 'string' ? data : data?.act || data?.value || '';
  if (id) sessionApi.switchSession(id);
});

registerHandler('onConvNew', () => {
  sessionApi.newSession();
  renderSidebar();
});

registerHandler('onSkillOpen', (data) => {
  const id = typeof data === 'string' ? data : data?.act || data?.value || '';
  if (!id) return;
  // Open skill detail in the details panel
  import('./details.js').then(({ showSkill }) => showSkill?.(id))
    .catch(() => notify.info(`技能: ${id}`, { duration: 2000 }));
});

registerHandler('onSkillSearch', (data) => {
  // Will be handled by the search input; trigger graph search
  const query = typeof data === 'string' ? data : data?.value || '';
  if (!query || query.length < 2) return;
  galaxy.graphSearch?.(query, 10).then(r => {
    if (r?.results?.length) {
      // Show results in details panel
      const host = $('#details-host');
      if (!host) return;
      const ui = getInstance();
      if (!ui) return;
      host.innerHTML = '';
      ui.startStream(host);
      ui.feed(`[card tt:"技能搜索: ${escapeDsl(query)}"]`);
      for (const item of r.results.slice(0, 10)) {
        ui.feed(`[p]${escapeDsl(item.name)} — ${escapeDsl(item.description || '')} (score: ${item.score})[/p]`);
      }
      ui.feed('[/card]');
      ui.endStream();
      const panel = document.getElementById('details-panel');
      if (panel) panel.classList.remove('hidden');
    } else {
      notify.info(`未找到 "${query}" 相关技能`, { duration: 2000 });
    }
  }).catch(() => {});
});

registerHandler('onMemOpenTimeline', () => {
  fetchAndShowMemories('details-host', '', 20, 'timeline');
  const panel = document.getElementById('details-panel');
  if (panel) panel.classList.remove('hidden');
});

registerHandler('onDashboardOpen', () => {
  renderDashboard('details-host');
  const panel = document.getElementById('details-panel');
  if (panel) panel.classList.remove('hidden');
});

registerHandler('onMemSearch', () => {
  // Simple prompt for memory search
  const query = prompt('搜索记忆关键词:');
  if (!query) return;
  fetchAndShowMemories('details-host', query, 20, 'search');
  const panel = document.getElementById('details-panel');
  if (panel) panel.classList.remove('hidden');
});

registerHandler('onMcpDiscover', () => {
  import('./mcp-panel.js').then(({ discoverMcpTools }) => discoverMcpTools());
  const panel = document.getElementById('details-panel');
  if (panel) panel.classList.remove('hidden');
});

registerHandler('onMcpAdd', () => {
  import('./mcp-panel.js').then(({ renderMcpPanel }) => {
    _showAddForm = true;
    const host = $('#details-host');
    if (host) renderMcpPanel(host);
    const panel = document.getElementById('details-panel');
    if (panel) panel.classList.remove('hidden');
  });
});

registerHandler('onMcpShowAddForm', () => {
  import('./mcp-panel.js').then(({ renderMcpPanel }) => {
    _showAddForm = true;
    const host = $('#details-host');
    if (host) renderMcpPanel(host);
    const panel = document.getElementById('details-panel');
    if (panel) panel.classList.remove('hidden');
  });
});

registerHandler('onMcpOpenPanel', () => {
  import('./mcp-panel.js').then(({ discoverMcpTools }) => discoverMcpTools());
  const panel = document.getElementById('details-panel');
  if (panel) panel.classList.remove('hidden');
});

registerHandler('onDemoOpen', () => {
  import('../tokui/demo-panel.js').then(({ renderDemoPanel }) => renderDemoPanel('details-host'))
    .catch(() => {});
  const panel = document.getElementById('details-panel');
  if (panel) panel.classList.remove('hidden');
});

registerHandler('onSettingsOpen', () => {
  import('./settings-panel.js').then(({ openSettings }) => openSettings());
});
