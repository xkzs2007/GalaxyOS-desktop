// renderer/src/ipc/client.js — single entry point for all sidecar calls.
//
// C 阶段：TokUI 自带 SSE connect() 协议 ({tokui:"..."} 帧)，可绕过
// zmqCall 一次性响应直接走流。但当前 main.ts 的 IPC 仍然是 invoke/await
// 一次性返回。所以这里提供两套：
//   1. galaxy.ask/process/... → 调 ipcRenderer.invoke → 一次性 fragments
//   2. galaxy.streamAsk(...) → 走 ipcRenderer.on('tokui:fragment') 流
//
// C 阶段用 1（一性响应 + renderer 端 progressive feed），
// 未来 main.ts 改造后切到 2。

const galaxy = window.galaxy ?? makeStandaloneGalaxy();

/**
 * Standalone fallback (Playwright / browser) — proxies SSE /sse/*.
 * 注意：sidecar 实际上没启 HTTP server（main.ts:1958 注释说"_http_server
 * is gone"），所以 standalone 模式目前是 dead code。保留供未来 sidecar
 * 加回 HTTP server 时使用。
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
        for (const line of frame.split('\n')) {
          if (line.startsWith('data:')) {
            const data = line.slice(5).trim();
            if (data === '[DONE]') continue;
            try {
              const obj = JSON.parse(data);
              if (obj.tokui) out.push(obj.tokui);
            } catch { /* ignore */ }
          }
        }
      }
    }
    return out;
  }
  return {
    ask: async (q) => {
      const frags = await sseCollect('ask', { prompt: q, session_id: 'default' });
      return { events: frags.map(f => ({ tokui: f })), fragments: frags };
    },
    process: async (u) => {
      const frags = await sseCollect('process', { user_input: u, session_id: 'default' });
      return { events: frags.map(f => ({ tokui: f })), fragments: frags };
    },
    memo: async (q) => {
      const frags = await sseCollect('memo', { prompt: q, session_id: 'default' });
      return { events: frags.map(f => ({ tokui: f })), fragments: frags };
    },
    plan: async (q) => {
      const frags = await sseCollect('plan', { prompt: q, session_id: 'default' });
      return { events: frags.map(f => ({ tokui: f })), fragments: frags };
    },
    agent: async (q) => {
      const frags = await sseCollect('agent', { prompt: q, session_id: 'default' });
      return { events: frags.map(f => ({ tokui: f })), fragments: frags };
    },
    ocr: async (params) => {
      const body = new URLSearchParams(params).toString();
      const res = await fetch('http://127.0.0.1:5758/sse/ocr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      });
      if (!res.ok) throw new Error(`OCR HTTP ${res.status}`);
      return { events: [], fragments: [`[callout t:warn tt:"OCR 不可用"]需要 Electron IPC 环境[/callout]`] };
    },
    health: async () => {
      const res = await fetch('http://127.0.0.1:5758/sse/health', { method: 'POST' });
      if (!res.ok) throw new Error(`health HTTP ${res.status}`);
      return res.json();
    },
    skills: async () => ({ skills: [], count: 0 }),
    listProviders: async () => ({ providers: [], router: null }),
    fetchModels: async (_params) => ({ ok: false, provider: '', error: '需要 Electron IPC 环境', source: 'curated' }),
    // Real-time streaming event listeners (no-ops in standalone mode —
    // zmq PUB is only available through Electron IPC).
    onThinkStep: (_cb) => () => {},
    onMemoStage: (_cb) => () => {},
    onPlanStep:  (_cb) => () => {},
    onAgentTool: (_cb) => () => {},
  };
}

export { galaxy };
export default galaxy;
