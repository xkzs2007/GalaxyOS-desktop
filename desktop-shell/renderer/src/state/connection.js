// renderer/src/state/connection.js — sidecar connection + health probe.
//
// Polls sidecar every 30s. Drives the green/red dot in the sidebar
// footer. Backed by galaxy.health() (IPC → zmq).

import { createStore } from './store.js';
import galaxy from '../ipc/client.js';

export const connectionStore = createStore({
  status: 'connecting',   // 'connecting' | 'ok' | 'error'
  detail: '',
  lastCheck: 0,
});

let timer = null;

async function probe() {
  try {
    const h = await galaxy.health();
    connectionStore.set({
      status: 'ok',
      detail: h?.stage ? `${h.stage} · ${h.memo ?? 'mock'} · ${h.router ?? ''}` : '',
      lastCheck: Date.now(),
    });
  } catch (e) {
    connectionStore.set({
      status: 'error',
      detail: e?.message ?? String(e),
      lastCheck: Date.now(),
    });
  }
}

export function startHealthCheck(intervalMs = 30000) {
  probe();
  if (timer) clearInterval(timer);
  timer = setInterval(probe, intervalMs);
}

export function stopHealthCheck() {
  if (timer) { clearInterval(timer); timer = null; }
}
