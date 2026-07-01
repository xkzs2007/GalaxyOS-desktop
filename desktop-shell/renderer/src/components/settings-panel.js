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

registerHandler('onSettingsSave', async (data, evt, _formEl) => {
  // Read values from the form. Try FormData first (standard),
  // fall back to DOM query if TokUI doesn't set native name attrs.
  let apiKey = '', apiBase = '', systemPrompt = '';

  // Attempt 1: TokUI may pass form values through data
  if (data && typeof data === 'object') {
    apiKey = data.apiKey ?? '';
    apiBase = data.apiBase ?? '';
    systemPrompt = data.systemPrompt ?? '';
  }

  // Attempt 2: Use FormData on the form element from event
  const form = evt?.target?.closest?.('form') || evt?.target?.form;
  if (form && (!apiKey || !apiBase)) {
    const fd = new FormData(form);
    apiKey = apiKey || fd.get('apiKey') || '';
    apiBase = apiBase || fd.get('apiBase') || '';
    systemPrompt = systemPrompt || fd.get('systemPrompt') || '';
  }

  // Attempt 3: Walk DOM looking for input/textarea/picker elements
  if (!apiKey && !apiBase && !systemPrompt) {
    const host = $('settings-drawer-host');
    if (host) {
      const inputs = host.querySelectorAll('input, textarea, [data-tokui-type="input"], [data-tokui-type="textarea"]');
      for (const el of inputs) {
        const name = el.getAttribute('name') || el.getAttribute('data-name') || '';
        if (name === 'apiKey') apiKey = el.value || '';
        if (name === 'apiBase') apiBase = el.value || '';
        if (name === 'systemPrompt') systemPrompt = el.value || el.textContent || '';
      }
      // Try to find picker selected value
      const picker = host.querySelector('[data-tokui-type="picker"]');
      if (picker) {
        const selected = picker.querySelector('[data-selected], [aria-selected="true"], .tokui-picker-option--selected');
        // theme is read from store directly, already the source of truth
      }
    }
  }

  if (!apiKey && !apiBase && !systemPrompt) {
    // Last resort: read from rendered TokUI elements by their rendered attributes
    const host = $('settings-drawer-host');
    if (host) {
      // TokUI may render id as data-tokui-id or as element id
      apiKey = host.querySelector('#settings-apikey, [data-tokui-id="settings-apikey"]')?.value || '';
      apiBase = host.querySelector('#settings-apibase, [data-tokui-id="settings-apibase"]')?.value || '';
      const sysEl = host.querySelector('#settings-sysprompt, [data-tokui-id="settings-sysprompt"]');
      systemPrompt = sysEl?.value || sysEl?.textContent || '';
    }
  }

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
