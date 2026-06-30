// renderer/src/state/session.js — session list + active session.
//
// Backed by localStorage (ZCode/Codex pattern: survive reloads).
// Sessions store their chat history as TokUI-rendered HTML (the
// simplest possible persistence; we'll swap to a structured
// representation once the API contract stabilises).

import { createStore } from './store.js';

const STORAGE_KEY = 'galaxyos.sessions.v1';
const ACTIVE_KEY = 'galaxyos.activeSession.v1';

function uid() {
  return 's_' + Math.random().toString(36).slice(2, 10);
}

function load() {
  const out = { byId: {}, order: [], activeId: null };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const obj = JSON.parse(raw);
      out.byId = obj.byId ?? {};
      out.order = obj.order ?? [];
    }
  } catch (e) { console.warn('[session] load failed:', e); }
  if (out.order.length === 0) {
    const s = { id: uid(), title: '默认会话', createdAt: Date.now(), html: '' };
    out.byId[s.id] = s;
    out.order.unshift(s.id);
    out.activeId = s.id;
  } else {
    out.activeId = localStorage.getItem(ACTIVE_KEY) || out.order[0];
  }
  return out;
}

function persist(s) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ byId: s.byId, order: s.order }));
    if (s.activeId) localStorage.setItem(ACTIVE_KEY, s.activeId);
  } catch (e) { console.warn('[session] save failed:', e); }
}

export const sessionStore = createStore(load());

sessionStore.subscribe(persist);

export const sessionApi = {
  newSession(title = '新会话') {
    const s = { id: uid(), title, createdAt: Date.now(), html: '' };
    sessionStore.update((cur) => ({
      byId: { ...cur.byId, [s.id]: s },
      order: [s.id, ...cur.order],
      activeId: s.id,
    }));
    return s;
  },
  rename(id, newTitle) {
    sessionStore.update((cur) => {
      const target = cur.byId[id];
      if (!target) return cur;
      return { ...cur, byId: { ...cur.byId, [id]: { ...target, title: newTitle } } };
    });
  },
  remove(id) {
    sessionStore.update((cur) => {
      if (cur.order.length <= 1) return cur;  // never delete the last session
      const { [id]: _, ...rest } = cur.byId;
      const order = cur.order.filter((x) => x !== id);
      const activeId = cur.activeId === id ? order[0] : cur.activeId;
      return { byId: rest, order, activeId };
    });
  },
  activate(id) {
    sessionStore.update((cur) => (cur.activeId === id ? cur : { ...cur, activeId: id }));
  },
  captureCurrentHtml(html) {
    sessionStore.update((cur) => {
      const target = cur.byId[cur.activeId];
      if (!target) return cur;
      return { ...cur, byId: { ...cur.byId, [cur.activeId]: { ...target, html } } };
    });
  },
  getActive() {
    const s = sessionStore.get();
    return s.byId[s.activeId] ?? null;
  },
};
