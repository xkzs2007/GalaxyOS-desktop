// renderer/renderer.js — Stage 1 chat loop using window.galaxy.*
//
// Stage 1: simple ask/remember/recall dispatch.
// Stage 2: will add streaming + MeMo 3-stage trace rendering.
// Stage 3: will add ACRouter C-A-F debug sidebar.

declare const window: any;
const galaxy = window.galaxy;

const $ = (id) => document.getElementById(id);
const chat = $('chat');
const input = $('input');
const sendBtn = $('send');
const connDot = $('conn-indicator');
const connText = $('conn-text');

function appendBubble(role: 'user' | 'assistant' | 'system', html: string) {
  const el = document.createElement('div');
  el.className = `bubble ${role}`;
  el.innerHTML = `<div class="bubble-body">${html}</div>`;
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  return el;
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
}

async function healthCheck() {
  try {
    const h = await galaxy.health();
    connDot.classList.remove('err');
    connDot.classList.add('ok');
    connText.textContent = `已连接 · v${h.version} · ${h.rccam_enabled ? 'R-CCAM ✓' : 'R-CCAM ✗'}`;
  } catch (e) {
    connDot.classList.remove('ok');
    connDot.classList.add('err');
    connText.textContent = `连接失败: ${(e as Error).message}`;
  }
}

async function handleSend() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  sendBtn.disabled = true;

  appendBubble('user', `<p>${escapeHtml(text)}</p>`);
  const thinking = appendBubble('assistant', `<p class="hint">… 思考中</p>`);

  try {
    // Heuristic: if it starts with "记" or "remember", store it; else ask.
    const lower = text.toLowerCase();
    let resultHtml: string;
    if (/^(记|记住|remember:|remember )/i.test(text)) {
      const content = text.replace(/^(记|记住|remember:|remember )/i, '').trim();
      const r = await galaxy.remember(content);
      resultHtml = `<p>已记住。memory_id: <code>${r.memory_id.slice(0, 8)}…</code></p>`;
    } else if (/^(回忆|recall:|recall )/i.test(text)) {
      const query = text.replace(/^(回忆|recall:|recall )/i, '').trim();
      const r = await galaxy.recall(query, 5);
      const items = (r.results as any[]).slice(0, 3).map((x, i) =>
        `<li>${escapeHtml(String(x.content ?? x.text ?? JSON.stringify(x)).slice(0, 200))}</li>`).join('');
      resultHtml = `<p>找到 ${r.results.length} 条相关记忆（取前 3）：</p><ol>${items}</ol>`;
    } else {
      const r = await galaxy.ask(text);
      resultHtml = `<p>${escapeHtml(String(r.answer ?? '(无答案)'))}</p>
                    <p class="hint">confidence: ${(Number(r.confidence) || 0).toFixed(2)}</p>`;
    }
    thinking.querySelector('.bubble-body')!.innerHTML = resultHtml;
  } catch (e) {
    thinking.querySelector('.bubble-body')!.innerHTML =
      `<p style="color:#ef4444">错误: ${escapeHtml((e as Error).message)}</p>`;
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
}

sendBtn.addEventListener('click', handleSend);
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});

// Boot
healthCheck();
setInterval(healthCheck, 30_000);
