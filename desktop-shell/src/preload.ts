// src/preload.ts — contextBridge API for the renderer.
//
// Stage 1 exposes the four core RPCs (ask / remember / recall / process)
// plus health and openExternal. Stage 2 will add streaming events for
// MeMo 3-stage trace. Stage 3 will add ACRouter C-A-F debug events.

import { contextBridge, ipcRenderer } from 'electron';

export type GalaxyApi = {
  ask(question: string, sessionId?: string, streamId?: string): Promise<{ answer: string; confidence: number }>;
  remember(content: string, metadata?: object, source?: string): Promise<{ memory_id: string }>;
  recall(query: string, topK?: number, sessionId?: string): Promise<{ results: unknown[] }>;
  process(userInput: string, sessionId?: string, streamId?: string): Promise<Record<string, unknown>>;
  memo(prompt: string, sessionId?: string, streamId?: string): Promise<Record<string, unknown>>;
  plan(prompt: string, sessionId?: string, streamId?: string): Promise<Record<string, unknown>>;
  agent(prompt: string, sessionId?: string, streamId?: string): Promise<Record<string, unknown>>;
  ocr(params: { path?: string; base64?: string; prompt?: string; sessionId?: string }): Promise<Record<string, unknown>>;
  health(): Promise<Record<string, unknown>>;
  skills(): Promise<{ skills: Array<{id: string; name: string; description: string}>; count: number }>;
  skill(id: string): Promise<{ id: string; name: string; description: string; body: string }>;
  updateSettings(settings: Record<string, string>): Promise<{ ok: boolean; updated: string[] }>;
  listProviders(): Promise<{ providers: ProviderInfo[]; router?: Record<string, unknown> }>;
  /** Fetch live model list from a provider's API. */
  fetchModels(params: { provider: string; api_key?: string; base_url?: string }): Promise<FetchModelsResult>;
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
  /** Check if this is the first launch (setup marker not present). */
  isFirstLaunch(): Promise<{ isFirstLaunch: boolean }>;
  /** Write the setup-complete marker after first-launch wizard finishes. */
  completeSetup(): Promise<{ ok: boolean; error?: string }>;
  /** Restart the Python sidecar (e.g. after pip install of new deps). */
  restartSidecar(): Promise<{ ok: boolean; error?: string }>;
  openExternal(url: string): Promise<void>;
  /** API schema introspection — returns the JSON contract of all
   *  IPC channels. Fetch once at startup for runtime validation. */
  schema(): Promise<ApiSchema>;
  /** Subscribe to real-time think-chain progress events.
   *  Returns an unsubscribe function. */
  onThinkStep(callback: (event: ThinkStepEvent) => void): () => void;
  /** Subscribe to real-time MeMo 3-stage progress events. */
  onMemoStage(callback: (event: MemoStageEvent) => void): () => void;
  /** Subscribe to real-time plan generation progress events. */
  onPlanStep(callback: (event: PlanStepEvent) => void): () => void;
  /** Subscribe to real-time agent tool execution events. */
  onAgentTool(callback: (event: AgentToolEvent) => void): () => void;
  /** Subscribe to real-time DSL fragment events (true streaming). */
  onDslFragment(callback: (event: { stream_id: string; index: number; tokui: string }) => void): () => void;
  /** Subscribe to stream lifecycle events (start/done/error). */
  onStreamEvent(callback: (event: { stream_id: string; status: string; detail?: string }) => void): () => void;

  // ── P0: 记忆管理完整闭环 ──
  forget(memoryId: string): Promise<{ ok: boolean; deleted: number; memory_id: string; error?: string }>;
  getEntity(entityName: string): Promise<{ entity: string; result: unknown; error?: string }>;
  learnPreference(key: string, value: unknown): Promise<{ ok: boolean; error?: string }>;
  learnCorrection(original: string, corrected: string): Promise<{ ok: boolean; error?: string }>;
  autoLearn(userInput: string, assistantResponse: string, feedback?: string): Promise<{ ok: boolean; error?: string }>;
  analyzeForget(memories: unknown[]): Promise<{ analysis: unknown; error?: string }>;
  runCleanup(memories: unknown[], dryRun?: boolean): Promise<{ cleanup: unknown; error?: string }>;
  linkTaskMemory(taskId: string, memoryId: string, linkType?: string): Promise<{ ok: boolean; error?: string }>;
  getTaskMemories(taskId: string): Promise<{ task_id: string; memories: unknown[]; error?: string }>;

  // ── P0: 图像/文档理解 ──
  understandImage(imageSource: string, prompt?: string): Promise<{ result: unknown; error?: string }>;
  ocrImage(imageSource: string): Promise<{ result: unknown; error?: string }>;
  parseDocument(imageSource: string): Promise<{ result: unknown; error?: string }>;
  analyzeChart(imageSource: string): Promise<{ result: unknown; error?: string }>;
  verifyImageClaim(claim: string, imageSource: string): Promise<{ result: unknown; error?: string }>;

  // ── P0: 优化与主动任务 ──
  optimizeQuery(query: string, context?: string): Promise<{ optimization: unknown; error?: string }>;
  getProactiveTask(): Promise<{ task: unknown | null; error?: string }>;
  classifyKnowledge(content: string): Promise<{ classification: unknown; error?: string }>;
  correctAnswer(query: string, wrongAnswer: string, correction?: string): Promise<{ corrected: unknown; error?: string }>;
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

/** Real-time streaming event types (zmq PUB → main.ts → renderer). */

export interface ThinkStepEvent {
  stream_id: string;
  phase: string;       // routing | retrieval | cognition | control | action | memory
  status: string;      // running | done | error
  detail: string;
  dur_ms: number;
}

export interface MemoStageEvent {
  stream_id: string;
  stage: string;       // grounding | entity | answer
  status: string;      // running | done | error
  detail: string;
  dur_ms: number;
}

export interface PlanStepEvent {
  stream_id: string;
  step: string;        // generate | step_N | done
  status: string;      // running | pending | done
  detail: string;
  tool?: string;
  step_id?: string;
  total_steps?: number;
}

export interface AgentToolEvent {
  stream_id: string;
  type: string;        // plan_start | tool_start | tool_done
  status: string;      // running | done | error
  detail: string;
  tool_name: string;
  step_index: number;
  dur_ms: number;
}

/** Provider catalogue entry (from sidecar llm_providers.MAINSTREAM_PROVIDERS). */
export interface ProviderInfo {
  id: string;
  name: string;
  default_model: string;
  hint: string;
  models?: Record<string, string>;  // model_id → display name
}

/** Result of fetchModels() — live model list from provider API. */
export interface FetchModelsResult {
  ok: boolean;
  provider: string;
  models?: Array<{ id: string; owned_by: string; label: string; curated: boolean }>;
  source: 'api' | 'curated';
  error?: string;
}

const api: GalaxyApi = {
  ask: (q, sid, streamId) => ipcRenderer.invoke('galaxy:ask', q, sid, streamId) as any,
  remember: (c, m, s) => ipcRenderer.invoke('galaxy:remember', c, m, s) as any,
  recall: (q, k, sid) => ipcRenderer.invoke('galaxy:recall', q, k, sid) as any,
  process: (u, sid, streamId) => ipcRenderer.invoke('galaxy:process', u, sid, streamId) as any,
  memo: (p, sid, streamId) => ipcRenderer.invoke('galaxy:memo', p, sid, streamId) as any,
  plan: (p, sid, streamId) => ipcRenderer.invoke('galaxy:plan', p, sid, streamId) as any,
  agent: (p, sid, streamId) => ipcRenderer.invoke('galaxy:agent', p, sid, streamId) as any,
  ocr: (params) => ipcRenderer.invoke('galaxy:ocr', params) as any,
  health: () => ipcRenderer.invoke('galaxy:health') as any,
  skills: () => ipcRenderer.invoke('galaxy:skills') as any,
  skill: (id) => ipcRenderer.invoke('galaxy:skill', id) as any,
  updateSettings: (s) => ipcRenderer.invoke('galaxy:updateSettings', s) as any,
  listProviders: () => ipcRenderer.invoke('galaxy:listProviders') as any,
  fetchModels: (params) => ipcRenderer.invoke('galaxy:fetchModels', params) as any,
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
  isFirstLaunch: () => ipcRenderer.invoke('galaxy:isFirstLaunch') as any,
  completeSetup: () => ipcRenderer.invoke('galaxy:completeSetup') as any,
  restartSidecar: () => ipcRenderer.invoke('galaxy:restartSidecar') as any,
  openExternal: (u) => ipcRenderer.invoke('galaxy:openExternal', u) as any,
  schema: () => ipcRenderer.invoke('galaxy:schema') as Promise<ApiSchema>,
  // Real-time streaming event listeners (zmq PUB → webContents.send)
  onThinkStep: (cb) => {
    const h = (_e: unknown, payload: ThinkStepEvent) => cb(payload);
    ipcRenderer.on('think:step', h);
    return () => ipcRenderer.removeListener('think:step', h);
  },
  onMemoStage: (cb) => {
    const h = (_e: unknown, payload: MemoStageEvent) => cb(payload);
    ipcRenderer.on('memo:stage', h);
    return () => ipcRenderer.removeListener('memo:stage', h);
  },
  onPlanStep: (cb) => {
    const h = (_e: unknown, payload: PlanStepEvent) => cb(payload);
    ipcRenderer.on('plan:step', h);
    return () => ipcRenderer.removeListener('plan:step', h);
  },
  onAgentTool: (cb) => {
    const h = (_e: unknown, payload: AgentToolEvent) => cb(payload);
    ipcRenderer.on('agent:tool', h);
    return () => ipcRenderer.removeListener('agent:tool', h);
  },
  onDslFragment: (cb) => {
    const h = (_e: unknown, payload: { stream_id: string; index: number; tokui: string }) => cb(payload);
    ipcRenderer.on('dsl:fragment', h);
    return () => ipcRenderer.removeListener('dsl:fragment', h);
  },
  onStreamEvent: (cb) => {
    const h = (_e: unknown, payload: { stream_id: string; status: string; detail?: string }) => cb(payload);
    ipcRenderer.on('stream:event', h);
    return () => ipcRenderer.removeListener('stream:event', h);
  },

  // ── P0: 记忆管理完整闭环 ──
  forget: (memoryId) => ipcRenderer.invoke('galaxy:forget', memoryId) as any,
  getEntity: (entityName) => ipcRenderer.invoke('galaxy:getEntity', entityName) as any,
  learnPreference: (key, value) => ipcRenderer.invoke('galaxy:learnPreference', key, value) as any,
  learnCorrection: (original, corrected) => ipcRenderer.invoke('galaxy:learnCorrection', original, corrected) as any,
  autoLearn: (userInput, assistantResponse, feedback) => ipcRenderer.invoke('galaxy:autoLearn', userInput, assistantResponse, feedback) as any,
  analyzeForget: (memories) => ipcRenderer.invoke('galaxy:analyzeForget', memories) as any,
  runCleanup: (memories, dryRun) => ipcRenderer.invoke('galaxy:runCleanup', memories, dryRun) as any,
  linkTaskMemory: (taskId, memoryId, linkType) => ipcRenderer.invoke('galaxy:linkTaskMemory', taskId, memoryId, linkType) as any,
  getTaskMemories: (taskId) => ipcRenderer.invoke('galaxy:getTaskMemories', taskId) as any,

  // ── P0: 图像/文档理解 ──
  understandImage: (imageSource, prompt) => ipcRenderer.invoke('galaxy:understandImage', imageSource, prompt) as any,
  ocrImage: (imageSource) => ipcRenderer.invoke('galaxy:ocrImage', imageSource) as any,
  parseDocument: (imageSource) => ipcRenderer.invoke('galaxy:parseDocument', imageSource) as any,
  analyzeChart: (imageSource) => ipcRenderer.invoke('galaxy:analyzeChart', imageSource) as any,
  verifyImageClaim: (claim, imageSource) => ipcRenderer.invoke('galaxy:verifyImageClaim', claim, imageSource) as any,

  // ── P0: 优化与主动任务 ──
  optimizeQuery: (query, context) => ipcRenderer.invoke('galaxy:optimizeQuery', query, context) as any,
  getProactiveTask: () => ipcRenderer.invoke('galaxy:getProactiveTask') as any,
  classifyKnowledge: (content) => ipcRenderer.invoke('galaxy:classifyKnowledge', content) as any,
  correctAnswer: (query, wrongAnswer, correction) => ipcRenderer.invoke('galaxy:correctAnswer', query, wrongAnswer, correction) as any,
};

contextBridge.exposeInMainWorld('galaxy', api);
