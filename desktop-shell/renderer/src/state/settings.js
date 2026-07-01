// renderer/src/state/settings.js — user settings (API key, theme, slots).
//
// Persisted to localStorage. Sent to sidecar via galaxy.updateSettings()
// on save. Theme is also applied to <html data-tokui-theme="..."> so
// TokUI uses the right color scale.

import { createStore } from './store.js';
import { galaxy } from '../ipc/client.js';

const KEY = 'galaxyos.settings.v1';

function load() {
  try { return JSON.parse(localStorage.getItem(KEY) || '{}'); }
  catch { return {}; }
}

export const settingsStore = createStore(load());

settingsStore.subscribe((s) => {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* */ }
  document.documentElement.dataset.tokuiTheme = s.theme || 'dark';
});

export async function saveSettings(next) {
  settingsStore.set(next);

  // Build single-slot config (backward compat)
  const update = {
    api_key: next.apiKey || next.llm?.api_key || '',
    api_base: next.apiBase || next.llm?.base_url || '',
    system_prompt: next.systemPrompt || '',
  };

  // Pack multi-slot config for sidecar MultiSlotRouter
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
    try { return await galaxy.updateSettings(update); }
    catch (e) { console.warn('[settings] sidecar update failed:', e); }
  }
}
