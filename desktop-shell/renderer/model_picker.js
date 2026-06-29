// model_picker.js — v9.2 model selection with provider catalogue.
//
// On boot, calls galaxy.listProviders() to fetch the mainstream
// provider catalogue (from llm_providers.MAINSTREAM_PROVIDERS on the
// sidecar) and the current router state. Dropdown is grouped:
//   主流 / 本地 / 自定义 / 离线
// Selection sends set_config → sidecar → MultiSlotRouter.

const MODEL_KEY = 'galaxyos.model.v1';
const DEFAULT_MODEL = 'qwen/qwen-plus';

let currentModel = DEFAULT_MODEL;
let providerCatalogue = [];

function load() {
  const m = localStorage.getItem(MODEL_KEY);
  if (m) currentModel = m;
}
function save() {
  localStorage.setItem(MODEL_KEY, currentModel);
}

async function loadProviders() {
  if (!window.galaxy || !window.galaxy.listProviders) {
    providerCatalogue = FALLBACK_CATALOGUE;
    return;
  }
  try {
    const r = await window.galaxy.listProviders();
    providerCatalogue = r.providers || FALLBACK_CATALOGUE;
    console.log('[model] loaded', providerCatalogue.length, 'providers');
  } catch (e) {
    providerCatalogue = FALLBACK_CATALOGUE;
  }
}

const FALLBACK_CATALOGUE = [
  { id: 'openai',      name: 'OpenAI',       default_model: 'gpt-4o-mini' },
  { id: 'deepseek',    name: 'DeepSeek',     default_model: 'deepseek-chat' },
  { id: 'qwen',        name: 'Qwen (DashScope)', default_model: 'qwen-plus' },
  { id: 'anthropic',   name: 'Anthropic',    default_model: 'claude-3-5-sonnet-20241022' },
  { id: 'google',      name: 'Google Gemini',default_model: 'gemini-1.5-flash' },
  { id: 'siliconflow', name: 'SiliconFlow',  default_model: 'Qwen/Qwen2.5-7B-Instruct' },
  { id: 'openrouter',  name: 'OpenRouter',   default_model: 'anthropic/claude-3.5-sonnet' },
  { id: 'ollama',      name: 'Ollama (本地)', default_model: 'qwen2.5:7b' },
  { id: 'vllm',        name: 'vLLM (本地)',  default_model: 'Qwen/Qwen2.5-7B-Instruct' },
  { id: 'custom',      name: '自定义 (OpenAI 兼容)', default_model: 'default' },
  { id: 'mock',        name: 'Mock (脱机)',  default_model: 'mock-1' },
];

const PROVIDER_GROUPS = {
  mainstream: ['openai', 'deepseek', 'qwen', 'anthropic', 'google', 'siliconflow', 'openrouter'],
  local:      ['ollama', 'vllm'],
  custom:     ['custom'],
  mock:       ['mock'],
};
const GROUP_LABELS = { mainstream: '主流', local: '本地', custom: '自定义', mock: '离线' };

function findProvider(id) {
  return providerCatalogue.find((p) => p.id === id);
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = String(s || '');
  return d.innerHTML;
}

function renderMenu() {
  const menu = document.getElementById('model-picker-menu');
  if (!menu) return;
  menu.innerHTML = '';
  const title = document.createElement('div');
  title.className = 'menu-title';
  title.textContent = '选择模型';
  menu.appendChild(title);
  for (const [groupId, providerIds] of Object.entries(PROVIDER_GROUPS)) {
    const sep = document.createElement('div');
    sep.className = 'menu-sep';
    sep.textContent = GROUP_LABELS[groupId] || groupId;
    menu.appendChild(sep);
    for (const pid of providerIds) {
      const p = findProvider(pid);
      if (!p) continue;
      const opt = document.createElement('button');
      opt.className = 'model-option';
      opt.dataset.provider = p.id;
      opt.dataset.model = p.default_model;
      const isCurrent = currentModel === (p.id + '/' + p.default_model);
      if (isCurrent) opt.classList.add('selected');
      const hint = p.hint || '';
      opt.innerHTML = '<span class="model-dot"></span>' +
        '<span class="model-label">' + escapeHtml(p.name) + '</span>' +
        '<span class="model-desc">' + escapeHtml(p.default_model) + '</span>' +
        (hint ? '<span class="model-hint">' + escapeHtml(hint) + '</span>' : '');
      opt.addEventListener('click', () => selectProvider(p));
      menu.appendChild(opt);
    }
  }
}

async function selectProvider(p) {
  const needsKey = ['openai','deepseek','qwen','anthropic','google','siliconflow','openrouter'].indexOf(p.id) >= 0;
  if (needsKey && !getSavedApiKey(p.id)) {
    const ok = window.confirm(p.name + ' 需要 API Key。\n点确定打开「设置」填写。');
    if (ok) {
      const sb = document.getElementById('settings-btn');
      if (sb) sb.click();
    }
    return;
  }
  const spec = { provider: p.id, model: p.default_model, base_url: '', api_key: '' };
  try {
    if (window.galaxy && window.galaxy.updateSettings) {
      await window.galaxy.updateSettings({ llm: spec });
    }
  } catch (e) { console.warn('[model] updateSettings failed:', e); }
  currentModel = p.id + '/' + p.default_model;
  save();
  const chip = document.getElementById('model-name');
  if (chip) chip.textContent = p.name;
  document.querySelectorAll('.model-option').forEach((b) => {
    b.classList.toggle('selected', b.dataset.provider === p.id);
  });
  closeMenu();
  console.log('[model] switched to', currentModel);
}

function getSavedApiKey(provider) {
  try {
    const s = JSON.parse(localStorage.getItem('galaxyos.settings.v1') || '{}');
    const overrides = s.providerKeys || {};
    return overrides[provider] || s.apiKey || '';
  } catch { return ''; }
}

function setModel(name) {
  currentModel = name;
  save();
  const chip = document.getElementById('model-name');
  if (chip) chip.textContent = name;
  document.querySelectorAll('.model-option').forEach((b) => {
    b.classList.toggle('selected', b.dataset.model === name);
  });
}

function toggleMenu() {
  const menu = document.getElementById('model-picker-menu');
  if (!menu) return;
  menu.hidden = !menu.hidden;
  if (!menu.hidden) renderMenu();
}

function closeMenu() {
  const menu = document.getElementById('model-picker-menu');
  if (menu) menu.hidden = true;
}

async function init() {
  load();
  await loadProviders();
  const chip = document.getElementById('model-name');
  if (chip) chip.textContent = currentModel;
  renderMenu();
  const btn = document.getElementById('model-picker');
  if (btn) {
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggleMenu(); });
  }
  document.addEventListener('click', (e) => {
    const menu = document.getElementById('model-picker-menu');
    if (menu && !menu.hidden && !menu.contains(e.target) && e.target.id !== 'model-picker') {
      closeMenu();
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu();
  });
}

window.ModelPicker = {
  init,
  get: () => currentModel,
  set: setModel,
  listProviders: () => providerCatalogue,
};
