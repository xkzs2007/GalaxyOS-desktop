// renderer/src/ipc/client.js — single entry point for all sidecar calls.
//
// Two modes:
//   1. Electron: window.galaxy is exposed by preload.ts (contextBridge).
//      Calls go through IPC → main process → zmq → sidecar.
//   2. Standalone (Playwright / browser): makeStandaloneGalaxy() proxies
//      HTTP/SSE to the sidecar directly. Used for smoke tests and demos.
//
// This module normalises both into a single API surface that the rest
// of the renderer uses. The contract comes from src/main.ts:API_SCHEMA
// — keep both in sync when adding new RPCs.

const galaxy = window.galaxy ?? makeStandaloneGalaxy();

/**
 * Standalone shim — proxies SSE /sse/* endpoints when there's no
 * preload (i.e. running in plain Chromium for smoke tests).
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
    skills: async () => ({ skills: [], count: 0 }),
  };
}

/**
 * Wraps a streaming RPC so the caller can `for await` fragments.
 * Today: the Electron IPC layer returns a single { events, fragments }
 * payload, so we just emit each fragment once. When main.ts upgrades
 * to true streaming (one IPC event per fragment), this still works
 * unchanged.
 */
async function* streamFragments(method, params) {
  const res = await galaxy[method](params.prompt ?? params.user_input);
  const frags = res?.fragments ?? res?._fragments ?? [];
  for (const dsl of frags) yield dsl;
}

export { galaxy, streamFragments };
export default galaxy;
