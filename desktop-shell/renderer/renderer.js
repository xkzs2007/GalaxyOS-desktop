// renderer/renderer.js — Stage 1.5 renderer using @jboltai/tokui.
//
// Plain JavaScript so it loads directly via <script type="module">
// without a build step. Works in:
//   - Electron renderer (production)
//   - Chromium / Playwright (smoke tests, demos)
//   - Any modern browser
//
// Layout: 3-column ZCode/Codex-style
//   - Left: sessions / skills / health
//   - Center: TokUI mount + composer
//   - Right: details panel (R-CCAM trace)
//
// Communication:
//   - window.galaxy.ask(question)   → SSE /sse/ask (standalone) or zmq (Electron)
//   - window.galaxy.process(input)  → SSE /sse/process (standalone) or zmq (Electron)
//   - window.galaxy.health()       → zmq health check
//   - window.galaxy.streamAsk/process → streaming variants
//
// TokUI: loaded from CDN (standalone) or injected by main.ts (Electron).

const galaxy = window.galaxy ?? makeStandaloneGalaxy();
let TokUI = window.TokUI;  // initialized at runtime via waitForTokUI()

const $ = (id) => document.getElementById(id);
const tokuiContainer = $('tokui-container');
const input = $('input');
const sendBtn = $('send');
const connDot = $('conn-indicator');
const connText = $('conn-text');
const healthDetail = $('health-detail');
const detailsBody = $('details-body');

// ── State ──────────────────────────────────────────────────────────
const state = {
  ui: null,
  mode: 'ask',
  sessionId: 'default',
  isStreaming: false,
  allSkills: [],
};

// ── TokUI bootstrap ─────────────────────────────────────────────

async function waitForTokUI(maxWaitMs = 3000) {
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    if (window.TokUI) {
      TokUI = window.TokUI;
      return true;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
}

function makeStubRenderer() {
  return {
    startStream() { /* noop */ },
    feed(chunk) {
      const pre = document.createElement('pre');
      pre.style.cssText = 'opacity:0.5;font-size:11px;margin:4px 0;';
      pre.textContent = chunk;
      tokuiContainer.appendChild(pre);
      tokuiContainer.scrollTop = tokuiContainer.scrollHeight;
    },
    endStream() { /* noop */ },
    render(_dsl) { /* noop */ },
  };
}

/**
 * Create the TokUI client (or stub). MUST be called inside a
 * startStream/feed/endStream pair, or feed() will warn "called
 * before startStream()".
 */
async function bootTokUI() {
  if (state.ui) return state.ui;
  await waitForTokUI();
  const TokUIClass = TokUI?.TokUI || TokUI?.default;
  if (!TokUIClass || typeof TokUIClass !== 'function') {
    console.warn('[renderer] TokUI not loaded; using stub renderer');
    state.ui = makeStubRenderer();
  } else {
    state.ui = new TokUIClass({ container: tokuiContainer });
  }
  return state.ui;
}

// ── SSE consumer (manual; sidesteps TokUI's built-in connect()
//    which sends application/json — our sidecar uses
//    application/x-www-form-urlencoded to be fetch-friendly from
//    regular browsers too). ─────────────────────────────────────

/**
 * Open an SSE connection to the sidecar, feed each TokUI fragment
 * to `ui.feed()`, and close the stream. Returns when [DONE] is
 * received or an error occurs.
 */
async function consumeSseStream(ui, endpoint, params) {
  const url = `http://127.0.0.1:5758/sse/${endpoint}`;
  const body = new URLSearchParams(params).toString();
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!res.ok || !res.body) {
    throw new Error(`SSE ${endpoint} HTTP ${res.status}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let fragmentCount = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const dataLines = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      const data = dataLines.join('\n');
      if (data === '[DONE]') {
        console.log(`[SSE] ${endpoint} done, fed ${fragmentCount} fragments`);
        return;
      }
      try {
        const obj = JSON.parse(data);
        if (obj.tokui) {
          ui.feed(obj.tokui);
          fragmentCount++;
        }
      } catch (e) {
        console.warn(`[SSE] parse error:`, e, data.slice(0, 80));
      }
    }
  }
  console.log(`[SSE] ${endpoint} stream ended (no [DONE] seen), fed ${fragmentCount} fragments`);
}

// ── Health probe ──────────────────────────────────────────────

async function healthCheck() {
  try {
    const h = await galaxy.health();
    connDot.classList.remove('err');
    connDot.classList.add('ok');
    const stage = h.rccam_enabled ? 'R-CCAM ✓' : 'R-CCAM ✗';
    const memo = h.memo_enabled ? 'MeMo ✓' : 'MeMo ✗';
    const router = h.router_enabled ? 'Router ✓' : 'Router ✗';
    const skillsN = h.skills_count != null ? ` · ${h.skills_count} skills` : '';
    connText.textContent = `已连接 · v${h.version}`;
    healthDetail.innerHTML = `${stage} · ${memo} · ${router}${skillsN}<br>zmq :${h.zmq_port} · sse :${h.sse_port}`;
    if (h.skills_count && h.skills_count > 0 && galaxy.skills) {
      loadSkills();
    }
  } catch (e) {
    connDot.classList.remove('ok');
    connDot.classList.add('err');
    connText.textContent = `连接失败`;
    healthDetail.textContent = String(e.message ?? e);
  }
}

// Stage 14.3: heartbeat — show live uptime + last ping in the status footer
let lastHeartbeat = 0;
async function heartbeat() {
  if (!galaxy.heartbeat) return;
  try {
    const hb = await galaxy.heartbeat();
    if (hb.ok) {
      lastHeartbeat = Date.now();
      const m = Math.floor(hb.uptime_s / 60);
      const s = hb.uptime_s % 60;
      // Show the uptime in the status footer (next to the conn status)
      const uptimeEl = document.getElementById('conn-uptime');
      if (uptimeEl) uptimeEl.textContent = `uptime ${m}m${s}s`;
    }
  } catch (e) { /* ignore */ }
}
setInterval(heartbeat, 30000);  // every 30s

async function loadSkills() {
  if (!galaxy.skills) return;
  try {
    const result = await galaxy.skills();
    state.allSkills = result.skills || [];
    const countEl = document.getElementById('skill-count');
    if (countEl) countEl.textContent = `(${result.count})`;
    renderSkillPills(state.allSkills);
  } catch (e) {
    console.warn('[renderer] skills load failed:', e);
  }
}

function renderSkillPills(skills) {
  const list = document.getElementById('skills-list');
  if (!list) return;
  list.innerHTML = '';
  const shown = skills.slice(0, 30);
  for (const s of shown) {
    const li = document.createElement('li');
    li.className = 'skill-pill clickable';
    li.textContent = s.name || s.id;
    li.title = s.description || '';
    li.addEventListener('click', () => showSkillDetail(s.id));
    list.appendChild(li);
  }
  if (skills.length > 30) {
    const li = document.createElement('li');
    li.className = 'skill-pill';
    li.style.opacity = '0.5';
    li.textContent = `+${skills.length - 30}`;
    list.appendChild(li);
  }
}

function filterSkills(query) {
  if (!query || !query.trim()) {
    renderSkillPills(state.allSkills);
    return;
  }
  const q = query.toLowerCase().trim();
  const filtered = state.allSkills.filter(s =>
    (s.name || '').toLowerCase().includes(q) ||
    (s.id || '').toLowerCase().includes(q) ||
    (s.description || '').toLowerCase().includes(q)
  );
  renderSkillPills(filtered);
}

async function showSkillDetail(skillId) {
  if (!galaxy.skill) return;
  try {
    const detail = await galaxy.skill(skillId);
    const detailsBody = document.getElementById('details-body');
    if (!detailsBody) return;
    const body = detail.body || '(no content)';

    // Also fetch graph neighbors if available
    let neighborsHtml = '';
    if (galaxy.skillNeighbors) {
      try {
        const nb = await galaxy.skillNeighbors(skillId);
        if (nb.successors && nb.successors.length > 0) {
          neighborsHtml = '<div class="skill-neighbors"><h4>相关技能 (SkillGraph)</h4>';
          for (const s of nb.successors.slice(0, 8)) {
            neighborsHtml += `<div class="neighbor-pill" data-skill="${s.name}">${s.name} <span class="neighbor-rel">${s.relation}</span></div>`;
          }
          neighborsHtml += '</div>';
        }
      } catch (e) { /* ignore */ }
    }

    detailsBody.innerHTML = `
      <div class="skill-detail">
        <h3>${escapeHtml(detail.name || skillId)}</h3>
        <p class="hint">${escapeHtml(detail.description || '')}</p>
        ${detail.version ? `<p class="hint">v${escapeHtml(detail.version)}</p>` : ''}
        ${neighborsHtml}
        <pre class="skill-body">${escapeHtml(body.slice(0, 2000))}</pre>
      </div>`;

    // Make neighbor pills clickable
    detailsBody.querySelectorAll('.neighbor-pill').forEach(p => {
      p.addEventListener('click', () => showSkillDetail(p.dataset.skill));
    });
  } catch (e) {
    console.warn('[renderer] skill detail failed:', e);
  }
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = String(s || '');
  return d.innerHTML;
}

// ── UI handlers ──────────────────────────────────────────────

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll('.mode-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.mode === mode);
  });
  input.placeholder = mode === 'ask'
    ? '简单提问（自动路由：MeMo / process / fast_path）'
    : mode === 'agent'
    ? 'Agent 任务：!cmd / read file / grep / list / write path=content'
    : mode === 'memo'
    ? 'MeMo 调试：直调 3-stage 协议（Grounding → Entity → Answer）'
    : mode === 'plan'
    ? 'Plan 模式：描述任务，Agent 先出计划再执行'
    : '复杂任务（自动路由：走 R-CCAM 五阶段）';
}

function escapeDsl(s) {
  if (s.includes('[') || s.includes(']')) {
    return '"' + s.replace(/"/g, '\\"') + '"';
  }
  return s;
}

function autoResize() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 200) + 'px';
}

async function handleSend() {
  const text = input.value.trim();
  if (!text || state.isStreaming) return;
  input.value = '';
  autoResize();

  const ui = await bootTokUI();

  // 1) User bubble (own stream, sync)
  ui.startStream();
  ui.feed(`[bubble role:user][p]${escapeDsl(text)}[/bubble]`);
  ui.endStream();

  // 2) Assistant bubble (its own stream; SSE fragments go in)
  ui.startStream();
  state.isStreaming = true;
  sendBtn.disabled = true;
  // Pick endpoint + params per mode.
  const endpoint =
    state.mode === 'process' ? 'process' :
    state.mode === 'agent' ? 'agent' :
    state.mode === 'memo' ? 'memo' :
    state.mode === 'plan' ? 'plan' : 'ask';
  const params = state.mode === 'process'
    ? { user_input: text, session_id: state.sessionId }
    : { prompt: text, session_id: state.sessionId };
  try {
    await consumeSseStream(ui, endpoint, params);
  } catch (e) {
    console.error('[renderer] SSE error:', e);
    ui.feed(`[bubble role:ai model:GalaxyOS time:错误][p v:danger]${e.message ?? e}[/p][/bubble]`);
  } finally {
    ui.endStream();
    state.isStreaming = false;
    sendBtn.disabled = false;
    input.focus();
  }
}

function renderError(ui, msg) {
  ui.feed(`[bubble role:ai model:GalaxyOS time:错误][p v:danger]${msg}[/p][/bubble]`);
}

// ── Wire up events ────────────────────────────────────────────

sendBtn.addEventListener('click', handleSend);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});
input.addEventListener('input', () => {
  sendBtn.disabled = !input.value.trim() || state.isStreaming;
  autoResize();
});

document.querySelectorAll('.mode-btn').forEach((b) => {
  b.addEventListener('click', () => {
    setMode(b.dataset.mode);
  });
});

// new-chat-btn is wired by sessions.js (window.Sessions.init)

$('collapse-details').addEventListener('click', () => {
  document.getElementById('app').classList.toggle('details-collapsed');
});

// ── Skill search ──────────────────────────────────────────────
const skillSearch = $('skill-search');
if (skillSearch) {
  skillSearch.addEventListener('input', () => filterSkills(skillSearch.value));
}

// ── Settings modal ──────────────────────────────────────────────
const SETTINGS_KEY = 'galaxyos.settings.v1';
function loadSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}'); }
  catch { return {}; }
}
function saveSettings(s) {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(s));
}
function openSettings() {
  const s = loadSettings();
  const modal = $('settings-modal');
  $('setting-api-key').value = s.apiKey || '';
  $('setting-api-base').value = s.apiBase || '';
  $('setting-workspace').value = s.workspace || '';
  $('setting-system-prompt').value = s.systemPrompt || '';
  $('setting-theme').value = s.theme || 'dark';
  // Apply TokUI theme to <html> so bubble/md text color follows dark/light
  document.documentElement.dataset.tokuiTheme = s.theme || 'dark';
  modal.hidden = false;
}
function closeSettings() { $('settings-modal').hidden = true; }
function applySettings() {
  const s = {
    apiKey: $('setting-api-key').value,
    apiBase: $('setting-api-base').value,
    workspace: $('setting-workspace').value,
    systemPrompt: $('setting-system-prompt').value,
    theme: $('setting-theme').value,
  };
  saveSettings(s);
  // Sync TokUI theme attribute so bubble content (text/md/p) uses the
  // matching color scale. Without this, dark background + TokUI default
  // (light) theme => dim/gray AI output text (UI inconsistency).
  document.documentElement.dataset.tokuiTheme = s.theme || 'dark';
  // v9.4: build per-slot specs from the 4 new tab bodies, honouring
  // the per-slot "enabled" checkbox. LLM is enabled by default;
  // embedding / rerank / vlm default to disabled (fall back to local
  // implementations on the sidecar).
  const slotSpecs = {};
  for (const slot of ['llm', 'embedding', 'rerank', 'vlm']) {
    const enabledEl = $(`setting-${slot}-enabled`);
    const enabled = !enabledEl || enabledEl.checked;
    const provider = $(`setting-${slot}-provider`).value;
    const model = $(`setting-${slot}-model`).value;
    const apiKey = $(`setting-${slot}-key`).value;
    const baseUrl = $(`setting-${slot}-base`).value;
    if (!enabled) {
      // Explicitly turn this slot off — sidecar disables it regardless
      // of any previously-saved spec.
      slotSpecs[slot] = { enabled: false };
      continue;
    }
    if (provider) {
      slotSpecs[slot] = {
        enabled: true,
        provider, model, api_key: apiKey, base_url: baseUrl,
      };
    }
    // else: enabled checkbox on but no provider picked — leave the
    // slot alone on the sidecar (it stays at whatever it was).
  }
  // Persist slots to localStorage so they survive reloads
  s.slots = slotSpecs;
  saveSettings(s);
  closeSettings();
  // Send to sidecar via IPC — hot-updates LLM config + system prompt
  if (galaxy.updateSettings) {
    const update = {
      api_key: s.apiKey,
      api_base: s.apiBase,
      system_prompt: s.systemPrompt,
    };
    if (Object.keys(slotSpecs).length > 0) {
      Object.assign(update, slotSpecs);  // adds llm/embedding/rerank/vlm keys
    }
    galaxy.updateSettings(update).then(r => {
      console.log('[settings] sidecar response:', r);
    }).catch(e => {
      console.warn('[settings] sidecar update failed:', e);
    });
  }
  console.log('[settings] saved', Object.keys(s).join(', '));
}
const settingsBtn = $('settings-btn');
if (settingsBtn) settingsBtn.addEventListener('click', openSettings);
const settingsClose = $('settings-close');
if (settingsClose) settingsClose.addEventListener('click', closeSettings);
const settingsSave = $('settings-save');
if (settingsSave) settingsSave.addEventListener('click', applySettings);

// ── Settings tabs (General / Diagnostics) ───────────────────
document.querySelectorAll('.modal-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.modal-tab').forEach(t => t.classList.toggle('active', t === tab));
    const tabName = tab.dataset.tab;
    // Hide all tab bodies
    for (const id of ['tab-general','tab-llm','tab-embedding','tab-rerank','tab-vlm','tab-diagnostics']) {
      const el = $(id);
      if (el) el.hidden = (id !== `tab-${tabName}`);
    }
    if (tabName === 'diagnostics') loadDiagnostics();
    if (['llm','embedding','rerank','vlm'].includes(tabName)) populateProviderSelect(tabName);
  });
});

// ── Provider select population (v9.3) ────────────────────────────────

// v9.4: toggle a slot body's interactivity based on its `enabled` checkbox.
// Disabled slot bodies are dimmed + non-interactive in the UI; the spec
// is still serialised (with enabled:false) so the sidecar can disable it.
function syncSlotBodyDisabled(slot) {
  const enabledEl = $(`setting-${slot}-enabled`);
  const body = document.querySelector(`[data-slot-body="${slot}"]`);
  if (!enabledEl || !body) return;
  const on = enabledEl.checked;
  body.setAttribute('aria-disabled', on ? 'false' : 'true');
}

// Wire up enabled-checkbox listeners once on boot.
for (const slot of ['llm', 'embedding', 'rerank', 'vlm']) {
  const el = $(`setting-${slot}-enabled`);
  if (el) el.addEventListener('change', () => syncSlotBodyDisabled(slot));
}

async function populateProviderSelect(slot) {
  const sel = $(`setting-${slot}-provider`);
  if (!sel) return;
  // If already populated, skip
  if (sel.options.length > 1 && sel.options[0].value !== '') return;
  let providers = [];
  if (window.ModelPicker && typeof window.ModelPicker.listProviders === 'function') {
    providers = window.ModelPicker.listProviders() || [];
  }
  if (!providers.length && window.galaxy && window.galaxy.listProviders) {
    try {
      const r = await window.galaxy.listProviders();
      providers = r.providers || [];
    } catch (e) { /* ignore */ }
  }
  sel.innerHTML = '';
  const def = document.createElement('option');
  def.value = '';
  def.textContent = '— select —';
  sel.appendChild(def);
  for (const p of providers) {
    const opt = document.createElement('option');
    opt.value = p.id;
    opt.textContent = `${p.name} (${p.default_model})`;
    sel.appendChild(opt);
  }
  // Restore last saved value + enabled state (v9.4)
  try {
    const s = JSON.parse(localStorage.getItem('galaxyos.settings.v1') || '{}');
    const saved = (s.slots || {})[slot];
    if (saved) {
      // enabled flag: explicit false → off, otherwise on if provider set
      const enabledEl = $(`setting-${slot}-enabled`);
      if (enabledEl) {
        if (saved.enabled === false) {
          enabledEl.checked = false;
        } else if (saved.provider) {
          enabledEl.checked = true;
        }
        syncSlotBodyDisabled(slot);
      }
      if (saved.provider) {
        sel.value = saved.provider;
        $(`setting-${slot}-model`).value = saved.model || '';
        $(`setting-${slot}-key`).value = saved.api_key || '';
        $(`setting-${slot}-base`).value = saved.base_url || '';
      }
    } else {
      // No prior save — make sure UI body reflects the default checkbox state
      syncSlotBodyDisabled(slot);
    }
  } catch { /* ignore */ }
}

async function loadDiagnostics() {
  if (!galaxy.stats) return;
  const content = $('diagnostics-content');
  if (!content) return;
  content.innerHTML = '<p class="hint">加载中…</p>';
  try {
    const s = await galaxy.stats();
    const r = (k) => `<div class="stat-row"><span class="stat-key">${k}</span><span class="stat-val">${escapeHtml(String(s[k] ?? '—'))}</span></div>`;
    const rArr = (k) => {
      const v = s[k];
      const items = Array.isArray(v) ? v : (v ? Object.entries(v).map(([a,b]) => `${a}=${b}`) : []);
      return items.length === 0 ? '<span class="hint">empty</span>'
        : items.slice(0, 6).map(x => `<div class="stat-row"><span class="stat-key">·</span><span class="stat-val">${escapeHtml(x)}</span></div>`).join('');
    };
    content.innerHTML =
      `<div class="stat-group"><h4>Process</h4>${r('pid')}${r('cwd')}${r('rss_mb')}</div>` +
      `<div class="stat-group"><h4>Engine</h4>${r('engine')}${rArr('engine')}</div>` +
      `<div class="stat-group"><h4>ACRouter</h4>${rArr('acrouter')}</div>` +
      `<div class="stat-group"><h4>Config</h4>${rArr('config')}</div>` +
      `<div class="stat-group"><h4>MCP</h4>${rArr('mcp')}</div>` +
      `<div class="stat-group"><h4>Tools</h4>${rArr('tools')}</div>`;
  } catch (e) {
    content.innerHTML = `<p class="hint">加载失败: ${escapeHtml(String(e.message ?? e))}</p>`;
  }
}
const diagRefresh = $('diagnostics-refresh');
if (diagRefresh) diagRefresh.addEventListener('click', loadDiagnostics);

// ── File upload ───────────────────────────────────────────────
const attachBtn = $('attach-btn');
const fileInput = $('file-input');
const attachmentPreview = $('attachment-preview');
const attachments = [];

if (attachBtn) {
  attachBtn.addEventListener('click', () => fileInput && fileInput.click());
}
if (fileInput) {
  fileInput.addEventListener('change', async () => {
    for (const f of fileInput.files) {
      // T15.6: if file is an image, automatically call deepseek_ocr2
      if (f.type.startsWith('image/')) {
        try {
          // Use FileReader to get base64
          const b64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.onerror = reject;
            reader.readAsDataURL(f);
          });
          // Send to sidecar via IPC
          const result = await galaxy.ocr({ base64: b64, prompt: '' });
          // Display OCR result as a system message
          ui.startStream();
          ui.feed(`[p v:muted]📷 已识别 ${tokui_dsl._esc(f.name)}[/p]`);
          if (result.error) {
            ui.feed(`[p v:warn]OCR 失败: ${tokui_dsl._esc(result.error)}[/p]`);
          } else {
            const text = (result.output || result.text || JSON.stringify(result)).slice(0, 2000);
            ui.feed(`[md]\n${tokui_dsl._esc(text)}\n[/md]`);
          }
          ui.endStream();
        } catch (e) {
          console.warn('[upload] OCR failed:', e);
          attachments.push({ name: f.name, size: f.size, file: f });
          renderAttachments();
        }
      } else {
        attachments.push({ name: f.name, size: f.size, file: f });
        renderAttachments();
      }
    }
    fileInput.value = '';
  });
}

function renderAttachments() {
  if (!attachmentPreview) return;
  attachmentPreview.innerHTML = '';
  for (let i = 0; i < attachments.length; i++) {
    const a = attachments[i];
    const chip = document.createElement('span');
    chip.className = 'attachment-chip';
    chip.innerHTML = `📎 ${escapeHtml(a.name)} <span class="remove" data-i="${i}">✕</span>`;
    chip.querySelector('.remove').addEventListener('click', () => {
      attachments.splice(i, 1);
      renderAttachments();
    });
    attachmentPreview.appendChild(chip);
  }
}

// ── Standalone-mode shim for window.galaxy.* ───────────────────

/**
 * In Electron, preload exposes window.galaxy via contextBridge with
 * IPC + zmq → sidecar. In standalone (Playwright / plain browser),
 * there's no preload — we instead provide a `galaxy` object that
 * proxies the sidecar's HTTP/SSE endpoints directly. This is the
 * only place the renderer talks to the sidecar in standalone mode.
 */
function makeStandaloneGalaxy() {
  async function sseCollect(endpoint, params) {
    const body = new URLSearchParams(params).toString();
    const res = await fetch(`http://127.0.0.1:5758/sse/${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
    });
    if (!res.ok || !res.body) throw new Error(`SSE ${endpoint} HTTP ${res.status}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    const out = [];
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const dataLines = [];
        for (const line of frame.split('\n')) {
          if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
        }
        if (dataLines.length === 0) continue;
        const data = dataLines.join('\n');
        if (data === '[DONE]') continue;
        try {
          const obj = JSON.parse(data);
          if (obj.tokui) out.push(obj.tokui);
        } catch { /* ignore */ }
      }
    }
    return out;
  }
  return {
    ask: async (q) => {
      const frags = await sseCollect('ask', { prompt: q, session_id: 'default' });
      const mdFrag = frags.find((f) => f.includes('[md]')) || '';
      const confFrag = frags.find((f) => f.includes('置信度')) || '0%';
      const answer = (mdFrag.match(/\[md\]\n([\s\S]*?)\n\[\/md\]/) || [, ''])[1];
      const confidence = parseFloat((confFrag.match(/(\d+)%/) || [, '0'])[1]) / 100;
      return { answer, confidence, _fragments: frags };
    },
    process: async (u) => {
      const frags = await sseCollect('process', { user_input: u, session_id: 'default' });
      const mdFrag = frags.find((f) => f.includes('[md]')) || '';
      const answer = (mdFrag.match(/\[md\]\n([\s\S]*?)\n\[\/md\]/) || [, ''])[1];
      return { answer, _fragments: frags };
    },
    health: async () => {
      const res = await fetch('http://127.0.0.1:5758/sse/health', { method: 'POST' });
      if (!res.ok) throw new Error(`health HTTP ${res.status}`);
      return res.json();
    },
    skills: async () => {
      // In standalone mode, fetch skills from the sidecar's zmq
      // (proxied through SSE health endpoint which includes skills_count)
      const h = await fetch('http://127.0.0.1:5758/sse/health', { method: 'POST' });
      const health = await h.json();
      return { skills: [], count: health.skills_count || 0 };
    },
  };
}

// ── Keyboard shortcuts (ZCode/Codex parity) ────────────────────
document.addEventListener('keydown', (e) => {
  const ctrl = e.ctrlKey || e.metaKey;
  // Ctrl+N → new session
  if (ctrl && e.key === 'n') {
    e.preventDefault();
    if (window.Sessions) window.Sessions.new();
    return;
  }
  // Ctrl+, → settings
  if (ctrl && e.key === ',') {
    e.preventDefault();
    openSettings();
    return;
  }
  // Ctrl+B → toggle sidebar
  if (ctrl && e.key === 'b') {
    e.preventDefault();
    document.querySelector('.sidebar').classList.toggle('hidden');
    return;
  }
  // Ctrl+K → clear chat
  if (ctrl && e.key === 'k') {
    e.preventDefault();
    while (tokuiContainer.firstChild) tokuiContainer.removeChild(tokuiContainer.firstChild);
    return;
  }
  // Esc → stop streaming or close modal
  if (e.key === 'Escape') {
    if (state.currentController) {
      state.currentController.abort();
      console.log('[kbd] stream aborted');
    }
    const modal = $('settings-modal');
    if (modal && !modal.hidden) closeSettings();
    return;
  }
});

// ── TokUI msg-action handlers (copy / regenerate / like / dislike) ─
function registerTokUIHandlers() {
  if (!TokUI || typeof TokUI.registerHandler !== 'function') return;
  try {
    TokUI.registerHandler('copy', (ctx) => {
      const text = ctx?.element?.innerText || '';
      navigator.clipboard.writeText(text).then(() => {
        console.log('[msg-action] copied to clipboard');
      });
    });
    TokUI.registerHandler('regenerate', (ctx) => {
      // Re-send the last user message
      const bubbles = tokuiContainer.querySelectorAll('[class*="bubble"]');
      const lastUser = Array.from(bubbles).reverse().find(b =>
        b.className.includes('user'));
      if (lastUser) {
        const text = lastUser.innerText.trim();
        if (text) {
          input.value = text;
          handleSend();
        }
      }
    });
    TokUI.registerHandler('like', (ctx) => {
      console.log('[msg-action] liked');
      // T17: send like to sidecar (could feed into skill bank later)
      if (galaxy.emitEvent) {
        const text = ctx?.element?.innerText || '';
        galaxy.emitEvent('msg_action_like', { text: text.slice(0, 200) });
      }
    });
    TokUI.registerHandler('dislike', (ctx) => {
      console.log('[msg-action] disliked');
      if (galaxy.emitEvent) {
        const text = ctx?.element?.innerText || '';
        galaxy.emitEvent('msg_action_dislike', { text: text.slice(0, 200) });
      }
    });
    // T17.1: verify — calls claw_verify on the bubble text
    TokUI.registerHandler('verify', async (ctx) => {
      const text = ctx?.element?.innerText || '';
      console.log('[msg-action] verifying...');
      if (!galaxy.verify) return;
      try {
        const r = await galaxy.verify(text);
        const color = r.verdict === 'verified' ? '#10b981'
                    : r.verdict === 'partial' ? '#f59e0b' : '#ef4444';
        // Render the verdict in the same bubble
        if (ctx?.element) {
          const ver = document.createElement('div');
          ver.style.cssText = `margin-top:8px;padding:6px 10px;border-radius:4px;background:${color}22;color:${color};font-size:11px;`;
          ver.textContent = `🔍 ${r.verdict} (${(r.confidence*100).toFixed(0)}%) — ${r.evidence_count} 证据`;
          ctx.element.appendChild(ver);
        }
      } catch (e) { console.warn('[verify] failed:', e); }
    });
    // T17.2: recall — retrieves the memories that informed this answer
    TokUI.registerHandler('recall', async (ctx) => {
      const text = (ctx?.element?.innerText || '').slice(0, 100);
      if (!galaxy.recall) return;
      try {
        const r = await galaxy.recall(text, 3);
        if (ctx?.element && r.results) {
          const box = document.createElement('div');
          box.style.cssText = 'margin-top:8px;padding:6px 10px;border-radius:4px;background:#4f9dff22;color:#4f9dff;font-size:11px;';
          box.innerHTML = `<b>📚 检索到 ${r.count} 条相关记忆</b><br>` +
            r.results.map((m, i) => `<div style="margin-top:4px;opacity:0.85">${i+1}. ${(m.content || m.text || JSON.stringify(m)).slice(0, 80)}…</div>`).join('');
          ctx.element.appendChild(box);
        }
      } catch (e) { console.warn('[recall] failed:', e); }
    });
    // T17.3: save — commits bubble to long-term memory
    TokUI.registerHandler('save', async (ctx) => {
      const text = ctx?.element?.innerText || '';
      if (!galaxy.saveMemory) return;
      try {
        const r = await galaxy.saveMemory(text, { source: 'msg_action_save' });
        if (ctx?.element) {
          const note = document.createElement('div');
          note.style.cssText = 'margin-top:8px;padding:4px 8px;border-radius:4px;background:#10b98122;color:#10b981;font-size:11px;';
          note.textContent = `💾 已保存到长期记忆 (${r.memory_id?.slice(0, 8) || ''}…)`;
          ctx.element.appendChild(note);
        }
      } catch (e) { console.warn('[save] failed:', e); }
    });
    console.log('[renderer] TokUI msg-action handlers registered (copy/regen/like/dislike/verify/recall/save)');
  } catch (e) {
    console.warn('[renderer] TokUI handler registration failed:', e);
  }
}

// ── Context menu (right-click on messages) ────────────────────
const ctxMenu = document.createElement('div');
ctxMenu.className = 'context-menu';
ctxMenu.hidden = true;
document.body.appendChild(ctxMenu);

function showContextMenu(x, y, items) {
  ctxMenu.innerHTML = '';
  for (const item of items) {
    const el = document.createElement('div');
    el.className = 'ctx-item' + (item.danger ? ' danger' : '');
    el.textContent = item.label;
    el.addEventListener('click', () => {
      ctxMenu.hidden = true;
      item.action();
    });
    ctxMenu.appendChild(el);
  }
  ctxMenu.style.left = x + 'px';
  ctxMenu.style.top = y + 'px';
  ctxMenu.hidden = false;
}

document.addEventListener('click', () => { ctxMenu.hidden = true; });
document.addEventListener('contextmenu', (e) => {
  // Find the closest TokUI bubble
  const bubble = e.target.closest('[class*="bubble"]');
  if (!bubble) return;
  e.preventDefault();
  const text = bubble.innerText || '';
  showContextMenu(e.clientX, e.clientY, [
    { label: '📋 复制', action: () => navigator.clipboard.writeText(text) },
    { label: '🔄 重新生成', action: () => {
      const isUser = bubble.className.includes('user');
      if (isUser) { input.value = text.trim(); handleSend(); }
      else { const ub = tokuiContainer.querySelector('[class*="bubble"][class*="user"]');
             if (ub) { input.value = ub.innerText.trim(); handleSend(); } }
    }},
    { label: '❌ 删除', danger: true, action: () => bubble.remove() },
  ]);
});

(async () => {
  const tokuiReady = await waitForTokUI(3000);
  console.log(tokuiReady ? '[renderer] TokUI loaded' : '[renderer] TokUI did not load within 3s');
  await bootTokUI();
  registerTokUIHandlers();
  // Initialise session manager (renders sidebar, restores active session)
  if (window.Sessions) window.Sessions.init();
  if (window.ModelPicker) window.ModelPicker.init();

  // Initial welcome (always wrapped in startStream/endStream so we don't
  // get "feed() called before startStream()" warnings)
  const tokuiContainerInner = tokuiContainer.innerHTML.trim();
  if (!tokuiContainerInner) {
    // Only emit welcome if no session is being restored
    state.ui.startStream();
    state.ui.feed(
      `[bubble role:ai model:GalaxyOS time:就绪]` +
      `[md]\n# 欢迎使用 GalaxyOS 桌面端\n\n` +
      `本机桌面版已脱离 OpenClaw，\`XiaoYiClawLLM\` 由 Python 子进程加载。\n\n` +
      `- **Ask 模式** 走 *ask()*，单步检索 + 答案\n` +
      `- **Process 模式** 走 *process()*，完整 R-CCAM 五阶段 + 推理链 + 工具调用\n` +
      `- **Agent 模式** 走 */sse/agent*，能跑 shell / 读文件 / 写文件 / 搜索 / 列目录\n\n` +
      `试试输入 *"今天我学了 R-CCAM 五阶段"*（用 Process 模式），或 *!ls -la*（用 Agent 模式）。\n` +
      `[/md]` +
      `[msg-actions copy regenerate like dislike visible][/msg-actions]` +
      `[/bubble]`
    );
    state.ui.endStream();
  }
  setMode('ask');
  healthCheck();
  setInterval(healthCheck, 30000);
})();

// ── Install wizard modal (LFM model download) ─────────────────────
// Bound to the #download-model-btn in the sidebar; opens a modal
// with preset download options + live progress streaming from the
// sidecar's zmq PUB socket.

const iwModal = document.getElementById('iw-modal');
const iwLog = document.getElementById('iw-log');
const iwStartBtn = document.getElementById('iw-start');
const iwCloseBtn = document.getElementById('iw-close');
const iwPreset = document.getElementById('iw-preset');
const iwStatusLine = document.getElementById('iw-status-line');
const iwProgressFill = document.getElementById('iw-progress-fill');
const iwElapsed = document.getElementById('iw-elapsed');
const iwStage = document.getElementById('iw-stage');
const downloadModelBtn = document.getElementById('download-model-btn');

const IW_PRESETS = {
  'lfm-onnx-q4':     ['--download-lfm-onnx', '--download-lfm-onnx-quant', 'q4'],
  'lfm-onnx-fp16':   ['--download-lfm-onnx', '--download-lfm-onnx-quant', 'fp16'],
  'lfm-safetensors': ['--download-lfm'],
  'embedding':       ['--download-embedding'],
  'check':           ['--check'],
};

function iwOpen() {
  iwModal.classList.remove('hidden');
  iwLog.innerHTML = '';
  iwProgressFill.style.width = '0%';
  iwStatusLine.textContent = '点击 "开始下载" 启动';
  iwStage.textContent = 'idle';
  iwElapsed.textContent = '0.0s';
  iwStartBtn.disabled = false;
}

function iwClose() {
  // Don't allow closing mid-run (let user cancel via the button instead)
  if (iwStartBtn.disabled) {
    iwStatusLine.textContent = '正在运行，无法关闭（请等待完成）';
    return;
  }
  iwModal.classList.add('hidden');
}

function iwAppendLine(text, stream) {
  const div = document.createElement('div');
  div.className = `iw-log-line iw-log-line-${stream || 'stdout'}`;
  div.textContent = text;
  iwLog.appendChild(div);
  iwLog.scrollTop = iwLog.scrollHeight;
}

function iwSetProgress(pct, statusText, stage) {
  iwProgressFill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  if (statusText) iwStatusLine.textContent = statusText;
  if (stage) iwStage.textContent = stage;
}

async function iwStart() {
  const preset = iwPreset.value;
  const args = IW_PRESETS[preset] || ['--check'];
  if (!galaxy.installWizard) {
    iwAppendLine('❌ 当前 renderer 不支持 installWizard API（需要 Electron 模式）', 'stderr');
    return;
  }
  iwStartBtn.disabled = true;
  iwLog.innerHTML = '';
  iwProgressFill.style.width = '5%';
  iwStatusLine.textContent = `启动: install_wizard ${args.join(' ')}`;
  iwStage.textContent = 'starting';

  const t0 = Date.now();
  // Tick elapsed time
  const tick = setInterval(() => {
    iwElapsed.textContent = `${((Date.now() - t0) / 1000).toFixed(1)}s`;
  }, 200);

  try {
    const result = await galaxy.installWizard(args, (event) => {
      // Progress event handler — called for each PUB event
      if (event.event === 'started') {
        iwSetProgress(10, '子进程已启动', 'running');
      } else if (event.event === 'pid') {
        iwStage.textContent = `pid=${event.pid}`;
      } else if (event.event === 'line') {
        iwAppendLine(event.line || '', event.stream);
        // Heuristic progress: install_wizard prints "XX%" during downloads
        const pctMatch = (event.line || '').match(/(\d+)%/);
        if (pctMatch) {
          const pct = parseInt(pctMatch[1], 10);
          // Map 0-100% file download to 10-95% overall (leave 5% for setup
          // + 5% for cleanup)
          iwProgressFill.style.width = `${10 + (pct * 0.85)}%`;
        }
      } else if (event.event === 'done') {
        const ok = event.ok;
        iwSetProgress(
          100,
          ok ? '✅ 完成' : '❌ 失败',
          ok ? 'done' : 'failed',
        );
        iwAppendLine(
          ok
            ? `✅ 完成 (exit=${event.exit_code}, ${event.duration_s}s)`
            : `❌ 失败 (exit=${event.exit_code}, ${event.duration_s}s)${event.error ? ': ' + event.error : ''}`,
          ok ? 'done' : 'stderr',
        );
      }
    }, 1800);  // 30 min timeout

    // Final result from zmq REP (after process exited)
    clearInterval(tick);
    iwElapsed.textContent = `${result.duration_s}s`;
    if (result.ok) {
      iwSetProgress(100, `✅ 成功 (${result.duration_s}s)`, 'done');
      if (!iwLog.children.length) {
        // No progress events received (PUB socket issue?) — show stdout
        iwAppendLine('--- stdout ---', 'stdout');
        iwAppendLine(result.stdout || '(empty)', 'stdout');
      }
    } else {
      iwSetProgress(100, `❌ 失败 (exit=${result.exit_code})`, 'failed');
      iwAppendLine('--- stderr ---', 'stderr');
      iwAppendLine(result.stderr || '(empty)', 'stderr');
    }
  } catch (e) {
    clearInterval(tick);
    iwSetProgress(100, `❌ 异常: ${e.message || e}`, 'failed');
    iwAppendLine(`❌ ${e.stack || e}`, 'stderr');
  } finally {
    iwStartBtn.disabled = false;
  }
}

if (downloadModelBtn) {
  downloadModelBtn.addEventListener('click', iwOpen);
}
if (iwCloseBtn) {
  iwCloseBtn.addEventListener('click', iwClose);
}
if (iwStartBtn) {
  iwStartBtn.addEventListener('click', iwStart);
}
// Close on ESC (only when not running)
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !iwModal.classList.contains('hidden')) {
    iwClose();
  }
});
