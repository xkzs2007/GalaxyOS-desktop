// renderer/src/components/settings-panel.js — Settings drawer (D 阶段).
//
// TokUI [drawer] side panel with [form] for user configuration.
// Fields: API key · API base · system prompt · theme · seed color.
// Data flows through state/settings.js → settingsStore → saveSettings.

import { bootTokUI, getInstance, registerHandler } from '../tokui/runtime.js';
import { settingsStore, saveSettings } from '../state/settings.js';
import notify from '../tokui/notify.js';
import { setTheme } from '../tokui/runtime.js';

const $ = (id) => document.getElementById(id);

const THEME_OPTIONS = [
  { value: 'dark',        label: 'Dark 暗色' },
  { value: 'modern-dark', label: 'Modern Dark 现代暗色' },
  { value: 'default',     label: 'Default 默认' },
  { value: 'modern',      label: 'Modern 现代亮色' },
];

function buildSettingsDSL() {
  const s = settingsStore.get();
  const apiKey = s.apiKey ?? s.api_key ?? '';
  const apiBase = s.apiBase ?? s.api_base ?? '';
  const sysPrompt = s.systemPrompt ?? s.system_prompt ?? '';
  const theme = s.theme ?? 'dark';

  const themeOptions = THEME_OPTIONS.map(t =>
    `[picker-option value:${t.value} ${theme === t.value ? 'selected' : ''}]${t.label}[/picker-option]`
  ).join('\n      ');

  return `[drawer id:settings-drawer tt:"⚙️ 设置" position:right]\n` +
    `  [form sub:onSettingsSave]\n` +
    `    [input id:settings-apikey n:apiKey ph:"API Key (sk-...)" l:"API Key" v:${apiKey ? 'success' : 'muted'} value:"${escapeAttr(apiKey)}" type:password]\n` +
    `    [input id:settings-apibase n:apiBase ph:"https://api.openai.com/v1" l:"API Base" value:"${escapeAttr(apiBase)}"]\n` +
    `    [textarea id:settings-sysprompt n:systemPrompt ph:"自定义 system prompt…" l:"System Prompt" rows:3 max:2000]${escapeAttr(sysPrompt)}[/textarea]\n` +
    `    [picker id:settings-theme n:theme l:"主题" clk:onSettingsThemePick sm]\n` +
    `      ${themeOptions}\n` +
    `    [/picker]\n` +
    `    [row]\n` +
    `      [btn tx:"💾 保存" v:primary type:submit]\n` +
    `      [btn tx:"✕ 取消" clk:onSettingsClose]\n` +
    `    [/row]\n` +
    `  [/form]\n` +
    `[/drawer]`;
}

function escapeAttr(s) {
  return String(s ?? '').replace(/"/g, '&quot;');
}

let _drawerRendered = false;

async function openSettings() {
  const host = document.createElement('div');
  host.id = 'settings-drawer-host';
  host.style.cssText = 'position:fixed;inset:0;z-index:10000;pointer-events:none;';
  // Remove previous host if exists
  const prev = $('settings-drawer-host');
  if (prev) prev.remove();
  document.body.appendChild(host);

  const ui = await bootTokUI();
  if (!ui) return;

  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(buildSettingsDSL());
  ui.endStream();
  _drawerRendered = true;

  // Allow clicks on the drawer panel
  host.style.pointerEvents = 'auto';
}

function closeSettings() {
  const host = $('settings-drawer-host');
  if (host) host.remove();
  _drawerRendered = false;
}

// ── Handlers ──────────────────────────────────────────────────

registerHandler('onSettingsOpen', () => {
  openSettings();
});

registerHandler('onSettingsClose', () => {
  closeSettings();
});

registerHandler('onSettingsSave', async (data, _evt, formEl) => {
  // TokUI form sub: handler — extract values from form
  const form = formEl || document.querySelector('#settings-drawer-host form');
  if (!form) {
    notify.warning('表单未找到', { duration: 2000 });
    return;
  }

  // Read values from form inputs
  const apiKey = form.querySelector('#settings-apikey')?.value ?? '';
  const apiBase = form.querySelector('#settings-apibase')?.value ?? '';
  const systemPrompt = form.querySelector('#settings-sysprompt')?.value ?? '';

  const cur = settingsStore.get();
  const next = {
    ...cur,
    apiKey,
    apiBase,
    systemPrompt,
    theme: cur.theme || 'dark',
  };

  try {
    await saveSettings(next);
    notify.success('设置已保存', { duration: 2500 });
    closeSettings();
  } catch (e) {
    notify.error(`保存失败: ${e?.message ?? ''}`, { duration: 4000 });
  }
});

registerHandler('onSettingsThemePick', (data) => {
  const value = typeof data === 'string' ? data : data?.value ?? '';
  if (!value) return;
  const cur = settingsStore.get();
  const next = { ...cur, theme: value };
  saveSettings(next);
  setTheme(value);
  // Re-render the drawer to reflect theme change
  if (_drawerRendered) openSettings();
});

export { openSettings, closeSettings };
