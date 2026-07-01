// renderer/src/components/settings-panel.js — Settings drawer (D 阶段).
//
// v9.4 upgrade: multi-tab settings with 5-slot MultiSlotRouter config.
//   Tab 1 (通用): API key · API base · system prompt · theme
//   Tab 2 (Slot):  llm / llm_pro / embedding / rerank / vlm per-slot config
//
// Data flows through state/settings.js → settingsStore → saveSettings.

import { bootTokUI, getInstance, registerHandler } from '../tokui/runtime.js';
import { settingsStore, saveSettings } from '../state/settings.js';
import notify from '../tokui/notify.js';
import { setTheme } from '../tokui/runtime.js';
import { galaxy } from '../ipc/client.js';

const $ = (id) => document.getElementById(id);

const THEME_OPTIONS = [
  { value: 'dark',        label: 'Dark 暗色' },
  { value: 'modern-dark', label: 'Modern Dark 现代暗色' },
  { value: 'default',     label: 'Default 默认' },
  { value: 'modern',      label: 'Modern 现代亮色' },
];

const SLOTS = [
  { key: 'llm',       label: 'LLM',        desc: '主语言模型（必选）',         icon: '🧠' },
  { key: 'llm_pro',   label: 'LLM Pro',    desc: '辅助推理模型（R-CCAM 可选）', icon: '🔮' },
  { key: 'embedding', label: 'Embedding',  desc: '向量嵌入（关闭则用 BoW）',    icon: '📐' },
  { key: 'rerank',    label: 'Rerank',     desc: '检索重排序（关闭则用原始分）', icon: '🎯' },
  { key: 'vlm',       label: 'VLM (视觉)',  desc: '多模态视觉（关闭则跳过图片）',  icon: '👁️' },
];

// Cached provider list (loaded once when settings opens)
let _providers = [];
let _routerInfo = null;

async function loadProviders() {
  if (_providers.length > 0) return;
  try {
    const res = await galaxy.listProviders?.();
    _providers = res?.providers ?? [];
    _routerInfo = res?.router ?? null;
  } catch {
    // offline fallback: hardcoded defaults
    _providers = [
      { id: 'openai', name: 'OpenAI', default_model: 'gpt-4o-mini', hint: 'GPT-4o / 4o-mini / o1' },
      { id: 'deepseek', name: 'DeepSeek', default_model: 'deepseek-chat', hint: 'deepseek-chat / reasoner' },
      { id: 'qwen', name: 'Qwen (DashScope)', default_model: 'qwen-plus', hint: 'qwen-plus / max / coder' },
      { id: 'anthropic', name: 'Anthropic', default_model: 'claude-3-5-sonnet-20241022', hint: 'Claude 3.5/3.7' },
      { id: 'google', name: 'Google Gemini', default_model: 'gemini-1.5-flash', hint: 'Gemini 1.5/2.0' },
      { id: 'siliconflow', name: 'SiliconFlow', default_model: 'Qwen/Qwen2.5-7B-Instruct', hint: '硅基流动' },
      { id: 'openrouter', name: 'OpenRouter', default_model: 'anthropic/claude-3.5-sonnet', hint: 'OpenRouter' },
      { id: 'ollama', name: 'Ollama (本地)', default_model: 'qwen2.5:7b', hint: 'Ollama 本地推理' },
      { id: 'vllm', name: 'vLLM (本地)', default_model: 'Qwen/Qwen2.5-7B-Instruct', hint: 'vLLM' },
      { id: 'custom', name: '自定义 (OpenAI 兼容)', default_model: 'default', hint: '任意兼容端点' },
      { id: 'mock', name: 'Mock (脱机)', default_model: 'mock-1', hint: '无网络' },
    ];
  }
}

function escapeAttr(s) {
  return String(s ?? '').replace(/"/g, '&quot;');
}

function escapeLabel(s) {
  return String(s ?? '').replace(/\[/g, '\\[').replace(/\]/g, '\\]');
}

// ── DSL builders ───────────────────────────────────────────────

function buildGeneralTab() {
  const s = settingsStore.get();
  const apiKey = s.apiKey ?? s.api_key ?? '';
  const apiBase = s.apiBase ?? s.api_base ?? '';
  const sysPrompt = s.systemPrompt ?? s.system_prompt ?? '';
  const theme = s.theme ?? 'dark';

  const themeOptions = THEME_OPTIONS.map(t =>
    `[picker-option value:${t.value} ${theme === t.value ? 'selected' : ''}]${t.label}[/picker-option]`
  ).join('\n      ');

  return `  [section s:settings-general]\n` +
    `    [input id:settings-apikey n:apiKey ph:"sk-..." l:"API Key" v:${apiKey ? 'success' : 'muted'} value:"${escapeAttr(apiKey)}" type:password]\n` +
    `    [input id:settings-apibase n:apiBase ph:"https://api.openai.com/v1" l:"API Base" value:"${escapeAttr(apiBase)}"]\n` +
    `    [textarea id:settings-sysprompt n:systemPrompt ph:"自定义 system prompt…" l:"System Prompt" rows:3 max:2000]${escapeAttr(sysPrompt)}[/textarea]\n` +
    `    [picker id:settings-theme n:theme l:"主题" clk:onSettingsThemePick sm]\n` +
    `      ${themeOptions}\n` +
    `    [/picker]\n` +
    `  [/section]`;
}

function buildSlotsTab() {
  const s = settingsStore.get();

  let dsl = `  [section s:settings-slots]\n`;
  dsl += `    [p v:muted]每个 Slot 可独立配置 Provider / 模型 / API Key。关闭则回退到本地实现（BoW / 原始分 / 无 VLM）。[/p]\n`;

  for (const slot of SLOTS) {
    const sc = s[slot.key] || {};
    const provider = sc.provider || 'mock';
    const model = sc.model || '';
    const apiKey = sc.api_key || '';
    const baseUrl = sc.base_url || '';
    const enabled = sc.enabled !== false;

    // Provider picker options
    const pickerOptions = _providers.map(p => {
      const sel = p.id === provider ? 'selected' : '';
      return `[picker-option value:${p.id} ${sel}]${p.name} — ${p.hint || p.default_model}[/picker-option]`;
    }).join('\n          ');

    dsl += `    [h4]${slot.icon} ${slot.label} — ${slot.desc}[/h4]\n`;
    dsl += `    [picker id:settings-slot-${slot.key}-provider n:${slot.key}__provider l:"Provider" clk:onSettingsSlotChange sm]\n`;
    dsl += `          ${pickerOptions}\n`;
    dsl += `        [/picker]\n`;
    dsl += `    [row]\n`;
    dsl += `      [input id:settings-slot-${slot.key}-model n:${slot.key}__model ph:"${escapeAttr(getDefaultModel(provider) || 'model name')}" l:"Model" value:"${escapeAttr(model)}"]\n`;
    dsl += `      [input id:settings-slot-${slot.key}-url n:${slot.key}__base_url ph:"(默认)" l:"Base URL" value:"${escapeAttr(baseUrl)}" v:muted]\n`;
    dsl += `    [/row]\n`;
    dsl += `    [input id:settings-slot-${slot.key}-apikey n:${slot.key}__api_key ph:"(继承通用 API Key)" l:"API Key" value:"${escapeAttr(apiKey)}" type:password v:muted]\n`;
    dsl += `    [div v:${enabled ? 'success' : 'muted'} tt:"${enabled ? '✅ 已启用' : '⏸ 已禁用'}"]\n`;
    dsl += `      [p]状态: ${enabled ? '🟢 已启用' : '⚫ 已禁用（使用本地回退）'}[/p]\n`;
    dsl += `    [/div]\n`;
    dsl += `    [hr]\n`;
  }

  dsl += `  [/section]`;
  return dsl;
}

function getDefaultModel(providerId) {
  const p = _providers.find(pr => pr.id === providerId);
  return p?.default_model || '';
}

function buildSettingsDSL(activeTab = 'general') {
  const generalActive = activeTab === 'general' ? 'active' : '';
  const slotsActive = activeTab === 'slots' ? 'active' : '';

  return `[drawer id:settings-drawer tt:"⚙️ 设置 (v9.4)" position:right]\n` +
    `  [tabs clk:onSettingsTabSwitch]\n` +
    `    [tab value:general ${generalActive}]🔑 通用[/tab]\n` +
    `    [tab value:slots ${slotsActive}]🎛️ Slot 配置[/tab]\n` +
    `  [/tabs]\n` +
    `  [form sub:onSettingsSave]\n` +
    buildGeneralTab() +
    buildSlotsTab() +
    `    [row]\n` +
    `      [btn tx:"💾 保存" v:primary type:submit]\n` +
    `      [btn tx:"✕ 取消" clk:onSettingsClose]\n` +
    `    [/row]\n` +
    `  [/form]\n` +
    `[/drawer]`;
}

// ── Drawer lifecycle ───────────────────────────────────────────

let _drawerRendered = false;
let _activeTab = 'general';

async function openSettings() {
  await loadProviders();

  const host = document.createElement('div');
  host.id = 'settings-drawer-host';
  host.style.cssText = 'position:fixed;inset:0;z-index:10000;pointer-events:none;';
  const prev = $('settings-drawer-host');
  if (prev) prev.remove();
  document.body.appendChild(host);

  const ui = await bootTokUI();
  if (!ui) return;

  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(buildSettingsDSL(_activeTab));
  ui.endStream();
  _drawerRendered = true;

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

registerHandler('onSettingsTabSwitch', (data) => {
  const value = typeof data === 'string' ? data : data?.value ?? data?.tab ?? '';
  if (value === 'general' || value === 'slots') {
    _activeTab = value;
    if (_drawerRendered) openSettings();
  }
});

registerHandler('onSettingsSlotChange', (data) => {
  // When provider picker changes, update the default model hint
  // We re-render to show the new default model in the placeholder
  if (_drawerRendered) openSettings();
});

registerHandler('onSettingsSave', async (data, evt, _formEl) => {
  // Read form values using DOM fallback (TokUI may not set native name attrs)
  const host = $('settings-drawer-host');
  const getVal = (id) => {
    const el = host?.querySelector(`#${id}, [data-tokui-id="${id}"]`);
    return el?.value ?? el?.querySelector?.('input,textarea')?.value ?? '';
  };

  // ── General tab ──
  const apiKey = getVal('settings-apikey');
  const apiBase = getVal('settings-apibase');
  const systemPrompt = getVal('settings-sysprompt');
  const theme = settingsStore.get().theme || 'dark'; // theme from store, not form

  // ── Slots tab ──
  const slots = {};
  for (const slot of SLOTS) {
    const sk = slot.key;
    const provider = getVal(`settings-slot-${sk}-provider`) || 'mock';
    const model = getVal(`settings-slot-${sk}-model`);
    const slotApiKey = getVal(`settings-slot-${sk}-apikey`);
    const baseUrl = getVal(`settings-slot-${sk}-url`);

    // Fallback: try reading from settingsStore if form values empty (first save before edits)
    const cur = settingsStore.get()[sk] || {};
    const finalProvider = provider || cur.provider || 'mock';
    const finalModel = model || cur.model || '';
    const finalApiKey = slotApiKey || cur.api_key || apiKey || '';
    const finalBaseUrl = baseUrl || cur.base_url || '';

    // Determine enabled: mock + no api_key = disabled
    const enabled = finalProvider !== 'mock' || (finalApiKey && finalApiKey.length > 5);
    slots[sk] = {
      provider: finalProvider,
      model: finalModel,
      api_key: finalApiKey,
      base_url: finalBaseUrl,
      enabled,
    };
  }

  const next = {
    apiKey,
    apiBase,
    systemPrompt,
    theme,
    ...slots,
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
  _activeTab = 'general';
  if (_drawerRendered) openSettings();
});

export { openSettings, closeSettings };
