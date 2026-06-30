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
  // clawRecall: OpenClaw worker-pool recall (calls claw_recall method
  // on the sidecar, which routes to claw_worker). Distinct from the
  // in-process recall() above.
  clawRecall(query: string, topK?: number): Promise<{ query: string; count: number; results: unknown[] }>;
  saveMemory(content: string, metadata?: object): Promise<{ memory_id: string; ok: boolean }>;
  emitEvent(type: string, payload?: any): Promise<{ ok: boolean; received: string }>;
  graphSearch(query: string, topK?: number): Promise<{ count: number; results: any[] }>;
  skillNeighbors(name: string): Promise<{ name: string; successors: any[]; predecessors: any[] }>;
  /**
   * Run install_wizard.py with given CLI args (e.g.
   * ['--download-lfm-onnx', '--download-lfm-onnx-quant', 'q4']).
   * onProgress is called for each line of stdout/stderr the wizard
   * emits (forwarded from the sidecar's zmq PUB stream). Returns
   * the final { ok, exit_code, stdout, stderr, duration_s } when
   * the wizard exits.
   */
  installWizard(
    args: string[],
    onProgress?: (event: IwProgressEvent) => void,
    timeout?: number,
  ): Promise<IwResult>;
  onInstallWizardProgress(callback: (event: IwProgressEvent) => void): () => void;
  openExternal(url: string): Promise<void>;
  /** API schema introspection — returns the JSON contract of all
   *  IPC channels. Fetch once at startup for runtime validation. */
  schema(): Promise<ApiSchema>;
};

/** API schema (returned by `schema()`). */
export interface ApiSchema {
  version: string;
  generated_at: string;
  transport: 'ipc';
  channels: Array<{ name: string; args: string[]; returns: string }>;
  types: Record<string, Record<string, string>>;
}

/** Progress event for install_wizard (forwarded from sidecar PUB). */
export interface IwProgressEvent {
  event: 'started' | 'pid' | 'line' | 'done';
  args?: string[];
  pid?: number;
  stream?: 'stdout' | 'stderr';
  line?: string;
  elapsed_s?: number;
  ok?: boolean;
  exit_code?: number;
  duration_s?: number;
  error?: string;
}

/** Final result of install_wizard (returned via zmq REP). */
export interface IwResult {
  ok: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  duration_s: number;
  args: string[];
  error?: string;
}

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
  // clawRecall replaces the duplicate 'recall' that caused
  // "Attempted to register a second handler for 'galaxy:recall'" —
  // see main.ts galaxy:clawRecall handler for context.
  clawRecall: (q, k) => ipcRenderer.invoke('galaxy:clawRecall', q, k) as any,
  saveMemory: (c, m) => ipcRenderer.invoke('galaxy:saveMemory', c, m) as any,
  emitEvent: (t, p) => ipcRenderer.invoke('galaxy:emitEvent', t, p) as any,
  graphSearch: (q, k) => ipcRenderer.invoke('galaxy:graphSearch', q, k) as any,
  skillNeighbors: (n) => ipcRenderer.invoke('galaxy:skillNeighbors', n) as any,
  installWizard: (args, onProgress, timeout) => {
    // If onProgress callback is provided, subscribe to iw:progress
    // events for the duration of this call and unsubscribe when the
    // promise resolves/rejects.
    let unsubscribe: (() => void) | null = null;
    if (onProgress) {
      const handler = (_e: unknown, payload: IwProgressEvent) => onProgress(payload);
      ipcRenderer.on('iw:progress', handler);
      unsubscribe = () => ipcRenderer.removeListener('iw:progress', handler);
    }
    const p = ipcRenderer.invoke('galaxy:installWizard', args, timeout) as Promise<IwResult>;
    p.finally(() => {
      if (unsubscribe) unsubscribe();
    });
    return p;
  },
  onInstallWizardProgress: (cb) => {
    const handler = (_e: unknown, payload: IwProgressEvent) => cb(payload);
    ipcRenderer.on('iw:progress', handler);
    return () => ipcRenderer.removeListener('iw:progress', handler);
  },
  openExternal: (u) => ipcRenderer.invoke('galaxy:openExternal', u) as any,
  schema: () => ipcRenderer.invoke('galaxy:schema') as Promise<ApiSchema>,
};

contextBridge.exposeInMainWorld('galaxy', api);
