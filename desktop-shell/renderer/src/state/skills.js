// renderer/src/state/skills.js — skill list cache.
//
// The renderer calls galaxy.skills() once at boot, then keeps the
// list in memory. Search is local; graph neighbors are fetched on
// demand when the user opens a skill detail.

import { createStore } from './store.js';
import { galaxy } from '../ipc/client.js';

export const skillsStore = createStore({
  list: [],
  loading: false,
});

export async function loadSkills() {
  skillsStore.set({ loading: true });
  try {
    const r = await galaxy.skills();
    skillsStore.set({ list: r?.skills ?? [], loading: false });
  } catch (e) {
    console.warn('[skills] load failed:', e);
    skillsStore.set({ loading: false });
  }
}
