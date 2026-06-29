// src/preload.ts — contextBridge API for the renderer.
//
// Stage 1 exposes the four core RPCs (ask / remember / recall / process)
// plus health and openExternal. Stage 2 will add streaming events for
// MeMo 3-stage trace. Stage 3 will add ACRouter C-A-F debug events.

import { contextBridge, ipcRenderer } from 'electron';

export type GalaxyApi = {
  ask(question: string, sessionId?: string): Promise<{ answer: string; confidence: number }>;
  remember(content: string, metadata?: object, source?: string): Promise<{ memory_id: string }>;
  recall(query: string, topK?: number, sessionId?: string): Promise<{ results: unknown[] }>;
  process(userInput: string, sessionId?: string): Promise<Record<string, unknown>>;
  health(): Promise<Record<string, unknown>>;
  skills(): Promise<{ skills: Array<{id: string; name: string; description: string}>; count: number }>;
  skill(id: string): Promise<{ id: string; name: string; description: string; body: string }>;
  updateSettings(settings: Record<string, string>): Promise<{ ok: boolean; updated: string[] }>;
  heartbeat(): Promise<{ ok: boolean; ts_ms: number; uptime_s: number }>;
  stats(): Promise<Record<string, unknown>>;
  verify(claim: string): Promise<{ claim: string; confidence: number; verdict: string; evidence_count: number; top_evidence: string[] }>;
  recall(query: string, topK?: number): Promise<{ query: string; count: number; results: unknown[] }>;
  saveMemory(content: string, metadata?: object): Promise<{ memory_id: string; ok: boolean }>;
  emitEvent(type: string, payload?: any): Promise<{ ok: boolean; received: string }>;
  openExternal(url: string): Promise<void>;
};

const api: GalaxyApi = {
  ask: (q, sid) => ipcRenderer.invoke('galaxy:ask', q, sid) as any,
  remember: (c, m, s) => ipcRenderer.invoke('galaxy:remember', c, m, s) as any,
  recall: (q, k, sid) => ipcRenderer.invoke('galaxy:recall', q, k, sid) as any,
  process: (u, sid) => ipcRenderer.invoke('galaxy:process', u, sid) as any,
  health: () => ipcRenderer.invoke('galaxy:health') as any,
  skills: () => ipcRenderer.invoke('galaxy:skills') as any,
  skill: (id) => ipcRenderer.invoke('galaxy:skill', id) as any,
  updateSettings: (s) => ipcRenderer.invoke('galaxy:updateSettings', s) as any,
  heartbeat: () => ipcRenderer.invoke('galaxy:heartbeat') as any,
  stats: () => ipcRenderer.invoke('galaxy:stats') as any,
  verify: (claim) => ipcRenderer.invoke('galaxy:verify', claim) as any,
  recall: (q, k) => ipcRenderer.invoke('galaxy:recall', q, k) as any,
  saveMemory: (c, m) => ipcRenderer.invoke('galaxy:saveMemory', c, m) as any,
  emitEvent: (t, p) => ipcRenderer.invoke('galaxy:emitEvent', t, p) as any,
  openExternal: (u) => ipcRenderer.invoke('galaxy:openExternal', u) as any,
};

contextBridge.exposeInMainWorld('galaxy', api);
