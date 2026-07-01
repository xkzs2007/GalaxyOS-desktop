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
// Fetched live models per slot (keyed by slot key like "llm")
let _fetchedModels = {};

async function loadProviders() {
  if (_providers.length > 0) return;
  try {
    const res = await galaxy.listProviders?.();
    _providers = res?.providers ?? [];
    _routerInfo = res?.router ?? null;
  } catch {
    // offline fallback: hardcoded defaults
    _providers = [
      { id: 'openai', name: 'OpenAI', default_model: 'gpt-4o-mini', hint: 'GPT-4o / 4o-mini / o1 / o3', models: {} },
      { id: 'deepseek', name: 'DeepSeek', default_model: 'deepseek-v4-flash',
        hint: 'V4 Flash（快）/ V4 Pro（thinking）',
        models: { 'deepseek-v4-flash': 'DeepSeek V4 Flash', 'deepseek-v4-pro': 'DeepSeek V4 Pro',
                  'deepseek-chat': 'DeepSeek V3 Chat（即将废弃）', 'deepseek-reasoner': 'DeepSeek R1（即将废弃）' } },
      { id: 'qwen', name: 'Qwen (DashScope)', default_model: 'qwen-plus', hint: 'qwen-plus / max / coder / turbo',
        models: { 'qwen-plus': 'Qwen Plus', 'qwen-max': 'Qwen Max', 'qwen-coder-plus': 'Qwen Coder Plus' } },
      { id: 'anthropic', name: 'Anthropic', default_model: 'claude-sonnet-4-6-20250514',
        hint: 'Sonnet 5 / Fable 5 / Opus 4.8',
        models: { 'claude-sonnet-5-20250630': 'Claude Sonnet 5', 'claude-fable-5-20250609': 'Claude Fable 5',
                  'claude-opus-4-8-20250514': 'Claude Opus 4.8', 'claude-sonnet-4-6-20250514': 'Claude Sonnet 4.6',
                  'claude-haiku-4-5-20250514': 'Claude Haiku 4.5' } },
      { id: 'google', name: 'Google Gemini', default_model: 'gemini-2.5-flash', hint: 'Gemini 3 Pro / 2.5 Flash',
        models: { 'gemini-3-pro': 'Gemini 3 Pro', 'gemini-3-flash': 'Gemini 3 Flash',
                  'gemini-2.5-pro': 'Gemini 2.5 Pro', 'gemini-2.5-flash': 'Gemini 2.5 Flash' } },
      { id: 'siliconflow', name: 'SiliconFlow', default_model: 'Qwen/Qwen2.5-7B-Instruct', hint: '硅基流动', models: {} },
      { id: 'openrouter', name: 'OpenRouter', default_model: 'anthropic/claude-sonnet-4-6', hint: 'OpenRouter', models: {} },
      { id: 'ollama', name: 'Ollama (本地)', default_model: 'qwen2.5:7b', hint: 'Ollama 本地推理', models: {} },
      { id: 'vllm', name: 'vLLM (本地)', default_model: 'Qwen/Qwen2.5-7B-Instruct', hint: 'vLLM', models: {} },
      { id: 'custom', name: '自定义 (OpenAI 兼容)', default_model: 'default', hint: '任意兼容端点', models: {} },
      { id: 'mock', name: 'Mock (脱机)', default_model: 'mock-1', hint: '无网络', models: { 'mock-1': 'Mock 脱机测试' } },
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
    // [desc] provides a structured label:value layout for provider metadata
    dsl += `    [desc sm]\n`;
    dsl += `      [desc__item tt:"当前 Provider"]${escapeLabel(_providers.find(p=>p.id===provider)?.name || provider)}[/desc__item]\n`;
    if (model) dsl += `      [desc__item tt:"当前模型"]${escapeLabel(model)}[/desc__item]\n`;
    dsl += `    [/desc]\n`;
    dsl += `    [picker id:settings-slot-${slot.key}-provider n:${slot.key}__provider l:"Provider" clk:onSettingsSlotChange sm]\n`;
    dsl += `          ${pickerOptions}\n`;
    dsl += `        [/picker]\n`;

    // Model picker: curated list from provider's models dict + custom input
    const modelsObj = getProviderModels(provider);
    const modelPickerOptions = buildModelPickerOptions(slot.key, modelsObj, model, provider);
    dsl += `    [picker id:settings-slot-${slot.key}-model n:${slot.key}__model l:"Model" sm]\n`;
    dsl += `          ${modelPickerOptions}\n`;
    dsl += `        [/picker]\n`;

    dsl += `    [row]\n`;
    dsl += `      [input id:settings-slot-${slot.key}-custom-model n:${slot.key}__custom_model ph:"或输入自定义模型 ID" l:"自定义模型" value:"${escapeAttr(isCustomModel(provider, model) ? model : '')}" v:muted]\n`;
    dsl += `    [/row]\n`;
    dsl += `    [input id:settings-slot-${slot.key}-url n:${slot.key}__base_url ph:"(默认)" l:"Base URL" value:"${escapeAttr(baseUrl)}" v:muted]\n`;
    dsl += `    [input id:settings-slot-${slot.key}-apikey n:${slot.key}__api_key ph:"(继承通用 API Key)" l:"API Key" value:"${escapeAttr(apiKey)}" type:password v:muted]\n`;
    dsl += `    [btn id:settings-slot-${slot.key}-fetch tx:"🔄 获取模型列表" clk:onSettingsFetchModels data-slot:${slot.key} sm v:muted]\n`;
    dsl += `    [div id:settings-slot-${slot.key}-fetch-status v:muted]\n`;
    dsl += `    [div v:${enabled ? 'success' : 'muted'} tt:"${enabled ? '✅ 已启用' : '⏸ 已禁用'}"]\n`;
    dsl += `      [p]状态: ${enabled ? '🟢 已启用' : '⚫ 已禁用（使用本地回退）'}[/p]\n`;
    dsl += `    [/div]\n`;
    dsl += `    [hr]\n`;
  }

  dsl += `  [/section]`;
  return dsl;
}

/**
 * Get the curated model list for a provider from the catalogue.
 * Returns { modelId: "Display Name", ... } or {}.
 */
function getProviderModels(providerId) {
  const p = _providers.find(pr => pr.id === providerId);
  return p?.models || {};
}

/**
 * Build model picker options from the provider's curated model list.
 * Includes a "custom" option at the end.
 */
function buildModelPickerOptions(slotKey, modelsObj, currentModel, providerId) {
  // Merge curated + live-fetched models
  const liveModels = _fetchedModels[slotKey] || [];
  const merged = { ...modelsObj };

  // Add live models that aren't in the curated list
  for (const m of liveModels) {
    if (!(m.id in merged)) {
      merged[m.id] = m.label || m.id;
    }
  }

  const entries = Object.entries(merged);
  if (entries.length === 0) {
    if (currentModel) {
      return `[picker-option value:"${escapeAttr(currentModel)}" selected]${escapeLabel(currentModel)} (当前)[/picker-option]`;
    }
    const def = getProviderDefaultModel(providerId);
    return `[picker-option value:"${escapeAttr(def)}" selected]${escapeLabel(def)} (默认)[/picker-option]`;
  }

  let opts = '';
  for (const [modelId, label] of entries) {
    const sel = modelId === currentModel ? 'selected' : '';
    const isLive = liveModels.some(m => m.id === modelId);
    const tag = isLive && !(modelId in modelsObj) ? ' (🆕 实时)' : '';
    opts += `[picker-option value:"${escapeAttr(modelId)}" ${sel}]${escapeLabel(String(label))}${tag} (${modelId})[/picker-option]\n          `;
  }
  if (currentModel && !(currentModel in merged)) {
    opts += `[picker-option value:"${escapeAttr(currentModel)}" selected]${escapeLabel(currentModel)} (当前)[/picker-option]\n          `;
  }
  return opts;
}

/** Get the default model for a provider from the catalogue. */
function getProviderDefaultModel(providerId) {
  const p = _providers.find(pr => pr.id === providerId);
  return p?.default_model || '';
}

/** Return true if the model is NOT in the provider's curated list. */
function isCustomModel(providerId, model) {
  if (!model) return false;
  const modelsObj = getProviderModels(providerId);
  const keys = Object.keys(modelsObj);
  if (keys.length === 0) return true;  // no curated list → all models are custom
  return !(model in modelsObj);
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

registerHandler('onSettingsFetchModels', async (data) => {
  const slotKey = typeof data === 'string' ? data : data?.['data-slot'] || data?.slotKey || '';
  if (!slotKey) return;

  const sk = slotKey;
  const host = $('settings-drawer-host');

  // Read current provider + api_key from the form
  const provider = host?.querySelector(`#settings-slot-${sk}-provider [data-selected]`)
    ?.getAttribute('data-value')
    || host?.querySelector(`#settings-slot-${sk}-provider`)?.value
    || 'mock';

  const slotApiKey = host?.querySelector(`#settings-slot-${sk}-apikey`)?.value
    || settingsStore.get()?.apiKey || '';
  const slotBaseUrl = host?.querySelector(`#settings-slot-${sk}-url`)?.value || '';

  // Show loading state
  const statusEl = host?.querySelector(`#settings-slot-${sk}-fetch-status`);
  if (statusEl) statusEl.innerHTML = '<p>⏳ 正在获取模型列表...</p>';

  try {
    const res = await galaxy.fetchModels?.({
      provider,
      api_key: slotApiKey,
      base_url: slotBaseUrl,
    });

    if (!res?.ok) {
      if (statusEl) statusEl.innerHTML = `<p v:warn>⚠️ ${res?.error || '获取失败'}，使用内置列表</p>`;
      return;
    }

    const count = res.models?.length || 0;
    if (statusEl) statusEl.innerHTML = `<p v:success>✅ 获取到 ${count} 个模型 (${res.source})</p>`;

    // Store fetched models for the picker
    _fetchedModels[sk] = res.models || [];

    // Re-render to show the live model list
    if (_drawerRendered) openSettings();
  } catch (e) {
    if (statusEl) statusEl.innerHTML = `<p v:danger>❌ ${e?.message || '请求失败'}</p>`;
  }
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
    // Model: prefer custom input, fall back to picker value
    const customModel = getVal(`settings-slot-${sk}-custom-model`);
    const pickedModel = getVal(`settings-slot-${sk}-model`);
    const model = customModel || pickedModel || '';
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
