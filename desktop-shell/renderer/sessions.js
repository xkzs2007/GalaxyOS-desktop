// sessions.js — multi-session management for the renderer.
//
// Persists chat sessions in localStorage so they survive reloads.
// A session is {id, title, createdAt, messages: [...]} where messages
// are an array of {role: 'user'|'assistant', html: '...', time: 'HH:MM'}.
//
// ZCode/Codex pattern:
//   - Left sidebar lists sessions (newest first)
//   - Click a session → load it
//   - "+ 新对话" creates a new empty session and switches to it
//   - Hover a session → show rename / delete buttons
//
// Note: the actual chat content lives in the TokUI DOM (the renderer
// appends to #tokui-container). To "switch session" we capture the
// current #tokui-container.innerHTML into the current session, then
// restore the target session's HTML.

const STORAGE_KEY = 'galaxyos.sessions.v1';
const ACTIVE_KEY = 'galaxyos.activeSession.v1';

const sessions = {
  byId: new Map(),
  order: [],  // ordered list of session ids (newest first)
  activeId: null,
};

// ── Persistence ───────────────────────────────────────────────────

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const obj = JSON.parse(raw);
      for (const id of obj.order) {
        const s = obj.byId[id];
        if (s) sessions.byId.set(id, s);
      }
      sessions.order = obj.order;
    }
  } catch (e) {
    console.warn('[sessions] load failed:', e);
  }
  if (sessions.order.length === 0) {
    // Create the default session on first run
    const s = createSession('默认会话');
    sessions.activeId = s.id;
  } else {
    sessions.activeId = localStorage.getItem(ACTIVE_KEY) || sessions.order[0];
  }
  save();
}

function save() {
  try {
    const obj = {
      order: sessions.order,
      byId: Object.fromEntries(sessions.byId),
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    if (sessions.activeId) {
      localStorage.setItem(ACTIVE_KEY, sessions.activeId);
    }
  } catch (e) {
    console.warn('[sessions] save failed:', e);
  }
}

// ── Session lifecycle ────────────────────────────────────────────

function uid() {
  return 's_' + Math.random().toString(36).slice(2, 10);
}

function createSession(title = '新会话') {
  const id = uid();
  const s = {
    id,
    title,
    createdAt: Date.now(),
    // We store a placeholder string here. The renderer's
    // #tokui-container is the source of truth for HTML; the moment
    // we switch sessions, we read it back into the previous session.
    html: '',
  };
  sessions.byId.set(id, s);
  sessions.order.unshift(id);
  save();
  return s;
}

function rename(id, newTitle) {
  const s = sessions.byId.get(id);
  if (!s) return;
  s.title = newTitle;
  save();
  renderSidebar();
}

function remove(id) {
  if (sessions.order.length <= 1) return;  // never delete the last session
  sessions.byId.delete(id);
  sessions.order = sessions.order.filter((x) => x !== id);
  if (sessions.activeId === id) {
    sessions.activeId = sessions.order[0];
    activate(sessions.activeId);
  }
  save();
  renderSidebar();
}

// ── Active session switching ─────────────────────────────────────

function captureCurrent() {
  // Save the current DOM into the active session
  const s = sessions.byId.get(sessions.activeId);
  if (s) {
    const container = document.getElementById('tokui-container');
    s.html = container ? container.innerHTML : '';
  }
}

function activate(id) {
  if (id === sessions.activeId) return;
  captureCurrent();
  sessions.activeId = id;
  const s = sessions.byId.get(id);
  // Wipe TokUI container + restore
  const container = document.getElementById('tokui-container');
  if (container) container.innerHTML = s ? s.html : '';
  document.getElementById('active-session-name').textContent = s ? s.title : '';
  save();
  renderSidebar();
}

function newSession() {
  captureCurrent();
  const s = createSession('新会话');
  sessions.activeId = s.id;
  // Wipe container
  const container = document.getElementById('tokui-container');
  if (container) container.innerHTML = '';
  document.getElementById('active-session-name').textContent = s.title;
  save();
  renderSidebar();
}

// ── Sidebar UI ───────────────────────────────────────────────────

function renderSidebar() {
  const list = document.getElementById('sessions-list');
  if (!list) return;
  list.innerHTML = '';
  for (const id of sessions.order) {
    const s = sessions.byId.get(id);
    if (!s) continue;
    const isActive = id === sessions.activeId;
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
      if (newName && newName.trim()) rename(id, newName.trim());
    });
    li.appendChild(title);

    const time = document.createElement('span');
    time.className = 'time';
    time.textContent = isToday(s.createdAt) ? formatTime(s.createdAt) : formatDate(s.createdAt);
    li.appendChild(time);

    if (isActive && sessions.order.length > 1) {
      // Show delete X on hover/active
      const del = document.createElement('span');
      del.className = 'session-del';
      del.textContent = '×';
      del.title = '删除';
      del.addEventListener('click', (e) => {
        e.stopPropagation();
        if (confirm(`删除会话 "${s.title}"？`)) remove(id);
      });
      li.appendChild(del);
    }

    li.addEventListener('click', () => activate(id));
    list.appendChild(li);
  }
}

function isToday(ts) {
  const d = new Date(ts);
  const now = new Date();
  return d.toDateString() === now.toDateString();
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

// ── Wire up to existing UI ────────────────────────────────────────

function init() {
  load();

  // Wire "+ 新对话" button
  const newBtn = document.getElementById('new-chat-btn');
  if (newBtn) {
    // Replace onclick to use our session system
    const newBtn_clone = newBtn.cloneNode(true);
    newBtn.parentNode.replaceChild(newBtn_clone, newBtn);
    newBtn_clone.addEventListener('click', newSession);
  }

  renderSidebar();

  // Apply current session's HTML (in case it's the first load after reload)
  const s = sessions.byId.get(sessions.activeId);
  if (s) {
    const container = document.getElementById('tokui-container');
    if (container && s.html && !container.innerHTML.trim()) {
      container.innerHTML = s.html;
    }
    document.getElementById('active-session-name').textContent = s.title;
  }
}

// Expose for the global renderer
window.Sessions = {
  init,
  new: newSession,
  remove,
  rename,
  activate,
  capture: captureCurrent,
  getActiveId: () => sessions.activeId,
  getActiveTitle: () => {
    const s = sessions.byId.get(sessions.activeId);
    return s ? s.title : '';
  },
};
