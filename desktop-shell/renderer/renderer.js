// renderer/renderer.js — Stage 1.5 renderer using @jboltai/tokui.
//
// Layout: 3-column ZCode/Codex-style
//   - Left: sessions / skills / health
//   - Center: TokUI mount (auto-renders streamed DSL via SSE)
//   - Right: details panel (R-CCAM trace, stage 2 MeMo 3-stage, stage 3 C-A-F)
//
// Communication:
//   - window.galaxy.streamAsk(question)     → opens SSE to /sse/ask
//   - window.galaxy.streamProcess(input)    → opens SSE to /sse/process
//   - window.galaxy.remember / recall / health / etc. — structured zmq calls
//
// The TokUI client (window.TokUI) is injected by the main process via
// webContents.executeJavaScript once the page loads. If the package
// isn't npm-installed yet, we use a tiny stub that shows raw DSL in
// <pre> blocks (useful for first-launch debug).

declare const window: any;
declare const document: any;
const galaxy = window.galaxy;
let TokUI: any = (window as any).TokUI;  // exposed by main.ts after dynamic import

const $ = (id: string) => document.getElementById(id);
const tokuiContainer = $('tokui-container');
const input = $('input') as HTMLTextAreaElement;
const sendBtn = $('send') as HTMLButtonElement;
const connDot = $('conn-indicator');
const connText = $('conn-text');
const healthDetail = $('health-detail');
const detailsBody = $('details-body');

// ── State ──────────────────────────────────────────────────────────
interface State {
  ui: any | null;           // TokUI instance
  mode: 'ask' | 'process';
  sessionId: string;
  isStreaming: boolean;
  currentBubbleId: string | null;
  currentController: AbortController | null;
}
const state: State = {
  ui: null,
  mode: 'ask',
  sessionId: 'default',
  isStreaming: false,
  currentBubbleId: null,
  currentController: null,
};

// ── TokUI bootstrap (lazy) ────────────────────────────────────────

async function bootTokUI() {
  if (state.ui) return state.ui;
  if (!TokUI || typeof TokUI.TokUI !== 'function') {
    // Fallback: log a warning and use a minimal stub that just shows
    // the raw DSL in <pre> blocks. Helps first-launch debugging.
    console.warn('[renderer] TokUI not loaded; using stub renderer');
    state.ui = makeStubRenderer();
    return state.ui;
  }
  const ui = new TokUI.TokUI({ container: tokuiContainer });
  state.ui = ui;
  return ui;
}

/**
 * Wait briefly for main process to inject TokUI. The injection happens
 * in did-finish-load and is async, so we may need to retry a few times
 * before window.TokUI becomes available.
 */
async function waitForTokUI(maxWaitMs: number = 2000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < maxWaitMs) {
    if (typeof (window as any).TokUI !== 'undefined' &&
        typeof (window as any).TokUI.TokUI === 'function') {
      TokUI = (window as any).TokUI;
      return true;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  return false;
}

function makeStubRenderer() {
  return {
    startStream() { /* noop */ },
    feed(_chunk: string) {
      // Append the raw DSL to the container as a <pre> for debug visibility
      const pre = document.createElement('pre');
      pre.style.cssText = 'opacity:0.5;font-size:11px;margin:4px 0;';
      pre.textContent = _chunk;
      tokuiContainer.appendChild(pre);
      tokuiContainer.scrollTop = tokuiContainer.scrollHeight;
    },
    endStream() { /* noop */ },
    render(_dsl: string) { /* noop */ },
  };
}

// ── Streaming call (SSE) ────────────────────────────────────────

async function streamViaSse(endpoint: 'ask' | 'process', params: Record<string, string>) {
  if (state.isStreaming) return;
  state.isStreaming = true;
  state.currentController = new AbortController();
  sendBtn.disabled = true;

  const ui = await bootTokUI();
  ui.startStream();

  try {
    const url = `http://127.0.0.1:5758/sse/${endpoint}`;
    const body = new URLSearchParams(params).toString();
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body,
      signal: state.currentController.signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`SSE ${endpoint} HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let pendingFragments: string[] = [];

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE frames end with \n\n
      let idx: number;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const dataLines: string[] = [];
        for (const line of frame.split('\n')) {
          if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
        }
        if (dataLines.length === 0) continue;
        const data = dataLines.join('\n');
        if (data === '[DONE]') {
          // Flush any pending fragments
          for (const frag of pendingFragments) ui.feed(frag);
          pendingFragments = [];
          continue;
        }
        try {
          const obj = JSON.parse(data);
          if (obj.tokui) {
            pendingFragments.push(obj.tokui);
            // Coalesce: feed in micro-batches so very fast streams don't
            // flood the renderer. ~16ms batches ≈ 60fps.
            if (pendingFragments.length >= 3) {
              for (const frag of pendingFragments) ui.feed(frag);
              pendingFragments = [];
              await new Promise((r) => setTimeout(r, 0));
            }
          }
        } catch (e) {
          console.warn('[renderer] SSE parse error:', e, data);
        }
      }
    }
    // Flush remainder
    for (const frag of pendingFragments) ui.feed(frag);
    ui.endStream();
  } catch (e: any) {
    if (e.name === 'AbortError') {
      console.log('[renderer] stream aborted');
    } else {
      console.error('[renderer] SSE error:', e);
      renderError(String(e.message ?? e));
    }
  } finally {
    state.isStreaming = false;
    state.currentController = null;
    sendBtn.disabled = false;
    input.focus();
  }
}

function renderError(msg: string) {
  const ui = state.ui;
  if (!ui) return;
  // Use a simple error bubble in DSL
  ui.feed(`[bubble role:ai model:GalaxyOS time:错误][p v:danger]${msg}[/p][/bubble]`);
  ui.endStream();
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
    connText.textContent = `已连接 · v${h.version}`;
    healthDetail.innerHTML = `${stage} · ${memo} · ${router}<br>zmq :${h.zmq_port} · sse :${h.sse_port}`;
  } catch (e: any) {
    connDot.classList.remove('ok');
    connDot.classList.add('err');
    connText.textContent = `连接失败`;
    healthDetail.textContent = String(e.message ?? e);
  }
}

// ── UI handlers ──────────────────────────────────────────────

function setMode(mode: 'ask' | 'process') {
  state.mode = mode;
  document.querySelectorAll('.mode-btn').forEach((b) => {
    b.classList.toggle('active', (b as HTMLElement).dataset.mode === mode);
  });
  input.placeholder = mode === 'ask'
    ? '简单提问（Enter 发送）'
    : '复杂任务（Enter 启动 R-CCAM 5 阶段推理）';
}

async function handleSend() {
  const text = input.value.trim();
  if (!text || state.isStreaming) return;
  input.value = '';
  autoResize();

  // Render user bubble via DSL (always works, even without TokUI)
  const ui = await bootTokUI();
  ui.startStream();
  const now = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  ui.feed(`[bubble role:user][p]${escapeDsl(text)}[/bubble]`);
  ui.endStream();

  if (state.mode === 'ask') {
    await streamViaSse('ask', { prompt: text, session_id: state.sessionId });
  } else {
    await streamViaSse('process', { user_input: text, session_id: state.sessionId });
  }
}

function escapeDsl(s: string): string {
  // DSL rule: literal [ ] must be wrapped in double quotes
  if (s.includes('[') || s.includes(']')) {
    return '"' + s.replace(/"/g, '\\"') + '"';
  }
  return s;
}

function autoResize() {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 200) + 'px';
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
    setMode((b as HTMLElement).dataset.mode as 'ask' | 'process');
  });
});

$('new-chat-btn').addEventListener('click', () => {
  // Clear TokUI container
  while (tokuiContainer.firstChild) tokuiContainer.removeChild(tokuiContainer.firstChild);
  // Stage 2 will call galaxy.remember() with session reset
});

$('collapse-details').addEventListener('click', () => {
  document.getElementById('app')!.classList.toggle('details-collapsed');
});

// ── Boot ──────────────────────────────────────────────────────

(async () => {
  // Wait for main process to inject TokUI (it does so via
  // webContents.executeJavaScript after did-finish-load).
  const tokuiReady = await waitForTokUI();
  if (tokuiReady) {
    console.log('[renderer] TokUI loaded');
  } else {
    console.warn('[renderer] TokUI did not load within 2s — using stub');
  }
  await bootTokUI();
  // Initial welcome
  state.ui.feed(`[bubble role:ai model:GalaxyOS time:就绪]` +
                `[md]\n# 欢迎使用 GalaxyOS 桌面端\n\n` +
                `本机桌面版已脱离 OpenClaw，\`XiaoYiClawLLM\` 由 Python 子进程加载。\n\n` +
                `- **Ask 模式** 走 *ask()*，单步检索 + 答案\n` +
                `- **Process 模式** 走 *process()*，完整 R-CCAM 五阶段 + 推理链 + 工具调用\n\n` +
                `试试输入 *"今天我学了 R-CCAM 五阶段"*（用 Process 模式，然后切到 Ask 模式回忆）\n` +
                `[/md]` +
                `[msg-actions copy regenerate like dislike visible][/msg-actions]` +
                `[/bubble]`);
  state.ui.endStream();
  setMode('ask');
  healthCheck();
  setInterval(healthCheck, 30_000);
})();
