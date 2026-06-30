// renderer/src/store.js — minimal pub-sub store (no deps).
//
// Centralised state primitive shared by sidebar / composer / details /
// welcome. Each store exposes `get()`, `set(partial)`, and
// `subscribe(fn)`. Subscribers are called with the full new state
// after every set. `subscribe()` returns an unsubscribe function.
//
// Usage:
//   const sessionStore = createStore({ activeId: null, items: [] });
//   const unsub = sessionStore.subscribe((s) => renderSidebar(s));
//   sessionStore.set({ activeId: 's_1' });

export function createStore(initial) {
  let state = { ...initial };
  const subscribers = new Set();

  return {
    get: () => state,
    set(partial) {
      state = { ...state, ...partial };
      for (const fn of subscribers) fn(state);
    },
    update(fn) {
      state = { ...state, ...fn(state) };
      for (const s of subscribers) s(state);
    },
    subscribe(fn) {
      subscribers.add(fn);
      fn(state);  // immediate call so consumers render initial state
      return () => subscribers.delete(fn);
    },
  };
}
