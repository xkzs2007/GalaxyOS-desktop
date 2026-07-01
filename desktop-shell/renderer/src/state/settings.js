// renderer/src/state/settings.js — user settings (API key, theme, slots).
// v10: 用 store.persisted 自动持久化

import { createStore, persisted } from './store.js';
import { galaxy } from '../ipc/client.js';

export const settingsStore = persisted(
  createStore({
    apiKey: '', apiBase: '', model: '', systemPrompt: '', theme: 'dark',
    // v9.4: 5-slot config
    llm: {}, llm_pro: {}, embedding: {}, rerank: {}, vlm: {},
  }, { name: 'settings' }),
  'galaxyos.settings.v1'
);

settingsStore.subscribe((s) => {
  document.documentElement.dataset.tokuiTheme = s.theme || 'dark';
});

export async function saveSettings(next) {
  settingsStore.set(next);

  const update = {
    api_key: next.apiKey || next.llm?.api_key || '',
    api_base: next.apiBase || next.llm?.base_url || '',
    system_prompt: next.systemPrompt || '',
  };

  const SLOT_KEYS = ['llm', 'llm_pro', 'embedding', 'rerank', 'vlm'];
  for (const slot of SLOT_KEYS) {
    const sc = next[slot];
    if (sc && typeof sc === 'object') {
      update[slot] = {
        provider: sc.provider || 'mock',
        api_key: sc.api_key || next.apiKey || '',
        base_url: sc.base_url || '',
        model: sc.model || '',
        enabled: sc.enabled !== false,
      };
    }
  }

  if (galaxy.updateSettings) {
    try { return await galaxy.updateSettings(update); } catch (e) { console.warn('[settings] sidecar update failed:', e); }
  }
}
