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
    // If skills_count is available, fetch and render them
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

async function loadSkills() {
  if (!galaxy.skills) return;
  try {
    const result = await galaxy.skills();
    const list = document.getElementById('skills-list');
    if (!list) return;
    list.innerHTML = '';
    const skills = (result.skills || []).slice(0, 12);
    for (const s of skills) {
      const li = document.createElement('li');
      li.className = 'skill-pill clickable';
      li.textContent = s.name || s.id;
      li.title = s.description || '';
      li.addEventListener('click', () => showSkillDetail(s.id));
      list.appendChild(li);
    }
    if (result.count > 12) {
      const li = document.createElement('li');
      li.className = 'skill-pill';
      li.style.opacity = '0.5';
      li.textContent = `+${result.count - 12}`;
      list.appendChild(li);
    }
  } catch (e) {
    console.warn('[renderer] skills load failed:', e);
  }
}

async function showSkillDetail(skillId) {
  if (!galaxy.skill) return;
  try {
    const detail = await galaxy.skill(skillId);
    // Show the skill in the details panel (right column)
    const detailsBody = document.getElementById('details-body');
    if (!detailsBody) return;
    const body = detail.body || '(no content)';
    detailsBody.innerHTML = `
      <div class="skill-detail">
        <h3>${escapeHtml(detail.name || skillId)}</h3>
        <p class="hint">${escapeHtml(detail.description || '')}</p>
        ${detail.version ? `<p class="hint">v${escapeHtml(detail.version)}</p>` : ''}
        <pre class="skill-body">${escapeHtml(body.slice(0, 2000))}</pre>
      </div>`;
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
  //   ask     → /sse/ask    (default: routed through global ACRouter;
  //                          also consults global MeMo as background
  //                          memory; routing_debug appears as a footer
  //                          line on every bubble)
  //   process → /sse/process (same: routed through global ACRouter;
  //                          router typically picks process_5_stage)
  //   agent   → /sse/agent  (real tool execution; also routed
  //                          through ACRouter)
  //   memo    → /sse/memo   (MANUAL debug mode: directly calls the
  //                          MeMo 3-stage protocol, bypassing ACRouter;
  //                          useful for inspecting the Grounding →
  //                          Entity → Answer trace step by step)
  const endpoint =
    state.mode === 'process' ? 'process' :
    state.mode === 'agent' ? 'agent' :
    state.mode === 'memo' ? 'memo' : 'ask';
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

// ── Boot ──────────────────────────────────────────────────────

(async () => {
  const tokuiReady = await waitForTokUI(3000);
  console.log(tokuiReady ? '[renderer] TokUI loaded' : '[renderer] TokUI did not load within 3s');
  await bootTokUI();
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
