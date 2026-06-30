// renderer/src/state/settings.js — user settings (API key, theme, slots).
//
// Persisted to localStorage. Sent to sidecar via galaxy.updateSettings()
// on save. Theme is also applied to <html data-tokui-theme="..."> so
// TokUI uses the right color scale.

import { createStore } from './store.js';
import galaxy from '../ipc/client.js';

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
  const update = {
    api_key: next.apiKey,
    api_base: next.apiBase,
    system_prompt: next.systemPrompt,
  };
  if (next.slots) Object.assign(update, next.slots);
  if (galaxy.updateSettings) {
    try { return await galaxy.updateSettings(update); }
    catch (e) { console.warn('[settings] sidecar update failed:', e); }
  }
}
