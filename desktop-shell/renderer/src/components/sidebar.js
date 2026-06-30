// renderer/src/components/sidebar.js — left sidebar (sessions + skills + status).
//
// Renders the session list, skill pills, and connection status. Each
// subsection re-renders independently on store changes — full re-render
// of the whole sidebar is the worst-case fallback (used only on init).

import { sessionStore, sessionApi } from '../state/session.js';
import { skillsStore, loadSkills } from '../state/skills.js';
import { connectionStore, startHealthCheck } from '../state/connection.js';

const $ = (id) => document.getElementById(id);

function isToday(ts) {
  const d = new Date(ts);
  return d.toDateString() === new Date().toDateString();
}
function formatTime(ts) {
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}
function formatDate(ts) {
  const d = new Date(ts);
  const now = new Date();
  if ((now - d) / 86400000 < 7) {
    return ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
  }
  return d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

function renderSessions() {
  const list = $('sessions-list');
  if (!list) return;
  list.innerHTML = '';
  const { byId, order, activeId } = sessionStore.get();
  for (const id of order) {
    const s = byId[id];
    if (!s) continue;
    const isActive = id === activeId;
    const li = document.createElement('li');
    li.className = 'session-item' + (isActive ? ' active' : '');
    li.dataset.session = id;

    const dot = document.createElement('span');
    dot.className = 'dot';
    li.appendChild(dot);

    const title = document.createElement('span');
    title.className = 'title';
    title.textContent = s.title;
    title.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      const newName = prompt('重命名会话', s.title);
      if (newName && newName.trim()) sessionApi.rename(id, newName.trim());
    });
    li.appendChild(title);

    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = isToday(s.createdAt) ? formatTime(s.createdAt) : formatDate(s.createdAt);
    li.appendChild(time);

    if (isActive && order.length > 1) {
      const del = document.createElement('span');
      del.className = 'session-del';
      del.textContent = '×';
      del.title = '删除';
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        if (confirm(`删除会话 "${s.title}"？`)) sessionApi.remove(id);
      });
      li.appendChild(del);
    }

    li.addEventListener('click', () => sessionApi.activate(id));
    list.appendChild(li);
  }
}

function renderSkillPills(skills) {
  const list = $('skills-list');
  if (!list) return;
  list.innerHTML = '';
  const shown = skills.slice(0, 30);
  for (const s of shown) {
    const li = document.createElement('li');
    li.className = 'skill-pill clickable';
    li.textContent = s.name || s.id;
    li.title = s.description || '';
    li.addEventListener('click', () => {
      // delegated: dispatch a CustomEvent so details.js can handle it
      window.dispatchEvent(new CustomEvent('skill:open', { detail: { id: s.id } }));
    });
    list.appendChild(li);
  }
  if (skills.length > 30) {
    const li = document.createElement('li');
    li.className = 'skill-pill';
    li.style.opacity = '0.5';
    li.textContent = `+${skills.length - 30}`;
    list.appendChild(li);
  }
}

function renderConnection() {
  const dot = $('conn-indicator');
  const text = $('conn-text');
  const detail = $('health-detail');
  const s = connectionStore.get();
  if (dot) dot.className = 'dot ' + (s.status === 'ok' ? 'ok' : s.status === 'error' ? 'err' : '');
  if (text) text.textContent = s.status === 'ok' ? '已连接' : s.status === 'error' ? '连接失败' : '连接中…';
  if (detail) detail.textContent = s.detail || '';
}

export function initSidebar() {
  // Wire "+ 新对话" button
  $('new-chat-btn')?.addEventListener('click', () => sessionApi.newSession());

  // Wire skill search
  const search = $('skill-search');
  if (search) {
    search.addEventListener('input', () => {
      const q = search.value.toLowerCase().trim();
      const list = skillsStore.get().list;
      if (!q) return renderSkillPills(list);
      renderSkillPills(list.filter((s) =>
        (s.name || '').toLowerCase().includes(q) ||
        (s.id || '').toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q)
      ));
    });
  }

  // Subscribe to stores
  sessionStore.subscribe(renderSessions);
  skillsStore.subscribe((s) => renderSkillPills(s.list));
  connectionStore.subscribe(renderConnection);

  // Initial actions
  loadSkills();
  startHealthCheck(30000);
  renderConnection();

  // Apply current session's HTML on boot
  const s = sessionStore.get();
  const active = s.byId[s.activeId];
  if (active) {
    const container = $('tokui-container');
    if (container && active.html && !container.innerHTML.trim()) {
      container.innerHTML = active.html;
    }
    const name = $('active-session-name');
    if (name) name.textContent = active.title;
  }
}
