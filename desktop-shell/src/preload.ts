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
  openExternal(url: string): Promise<void>;
};

const api: GalaxyApi = {
  ask: (q, sid) => ipcRenderer.invoke('galaxy:ask', q, sid) as any,
  remember: (c, m, s) => ipcRenderer.invoke('galaxy:remember', c, m, s) as any,
  recall: (q, k, sid) => ipcRenderer.invoke('galaxy:recall', q, k, sid) as any,
  process: (u, sid) => ipcRenderer.invoke('galaxy:process', u, sid) as any,
  health: () => ipcRenderer.invoke('galaxy:health') as any,
  skills: () => ipcRenderer.invoke('galaxy:skills') as any,
  openExternal: (u) => ipcRenderer.invoke('galaxy:openExternal', u) as any,
};

contextBridge.exposeInMainWorld('galaxy', api);
