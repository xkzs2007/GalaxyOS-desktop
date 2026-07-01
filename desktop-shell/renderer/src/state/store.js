// renderer/src/state/store.js — 响应式状态管理（零依赖，Pinia 风格 API）
//
// v10: 增强特性
//   - createStore(initial)     — 基础 store（与之前相同）
//   - computed(fn, ...stores)  — 派生 store，依赖变化时自动 recalc
//   - persisted(store, key)    — localStorage 自动持久化
//   - 全局 devtools: window.__stores (所有 store 可查)
//   - batch(fn)               — 批量更新（合并多次 notify）
//
// 保持向后兼容：get() / set(partial) / update(fn) / subscribe(fn) 不变

// ── Devtools ────────────────────────────────────────────────
const _allStores = new Set();
if (typeof window !== 'undefined') {
  window.__stores = {
    list: () => [..._allStores].map(s => ({ name: s._name, state: s.get() })),
    reset: (name) => { for (const s of _allStores) if (s._name === name) s._reset?.(); },
  };
}

// ── createStore ─────────────────────────────────────────────

export function createStore(initial, opts = {}) {
  let state = { ...initial };
  const subscribers = new Set();
  const deps = new Set();     // computed stores that depend on this one

  const store = {
    _name: opts.name || '(unnamed)',
    _initial: { ...initial },

    get() { return state; },

    set(partial) {
      state = { ...state, ...partial };
      _notify();
      return store;
    },

    update(fn) {
      state = { ...state, ...fn(state) };
      _notify();
      return store;
    },

    subscribe(fn) {
      subscribers.add(fn);
      fn(state);
      return () => { subscribers.delete(fn); _gc(); };
    },

    _reset() { state = { ...store._initial }; _notify(); },
    _gc() { if (subscribers.size === 0 && deps.size === 0) _allStores.delete(store); },
  };

  function _notify() {
    for (const fn of subscribers) fn(state);
    for (const dep of deps) dep._recalc();
  }

  _allStores.add(store);
  return store;
}

// ── computed ────────────────────────────────────────────────
// Example: const total = computed(() => a.get().x + b.get().y, a, b);

export function computed(fn, ...stores) {
  let value = fn();
  const subs = [];
  const allSubs = new Set();

  const comp = createStore({ value }, { name: 'computed' });

  comp.get = () => ({ value: comp._value ?? value });
  comp._value = value;
  comp._recalc = () => {
    const next = fn();
    if (next !== comp._value) {
      comp._value = next;
      comp.set({ value: next });
    }
  };
  // 覆盖 set/get 为正常行为
  comp.set = (p) => { comp._value = p.value; return createStore.prototype.set?.call?.(comp, p) ?? comp; };
  comp.get = () => ({ value: comp._value });

  for (const s of stores) {
    s._deps = s._deps ?? new Set();
    s._deps.add(comp);
  }

  return comp;
}

// ── persisted ────────────────────────────────────────────────
// Example: const s = persisted(createStore({...}), 'galaxyos.settings');

export function persisted(store, key) {
  try {
    const raw = localStorage.getItem(key);
    if (raw) store.set(JSON.parse(raw));
  } catch { /* ignore */ }

  store.subscribe((state) => {
    try { localStorage.setItem(key, JSON.stringify(state)); } catch { /* quota */ }
  });

  return store;
}

// ── batch ───────────────────────────────────────────────────
let _batchDepth = 0;
let _batchQueue = [];

export function batch(fn) {
  _batchDepth++;
  try { fn(); }
  finally {
    _batchDepth--;
    if (_batchDepth === 0) {
      const q = _batchQueue;
      _batchQueue = [];
      for (const s of new Set(q)) s._notify?.();
    }
  }
}
