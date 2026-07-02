/**
 * worker-bridge.js — IPC bridge: Electron ipcMain ↔ WorkerPool
 *
 * Registers ~50+ ipcMain.handle() channels that forward renderer
 * requests to the GalaxyPool worker backend. All 59 claw_worker
 * methods are exposed with zero boilerplate — add a method name
 * to EXPOSE_METHODS and it's instantly available as:
 *   window.galaxy.<method>(...params) → workerPool.execute(method, params)
 */

import { ipcMain } from 'electron';
import { createRequire } from 'node:module';
const _require = createRequire(typeof __dirname !== 'undefined' ? `${__dirname}/` : import.meta.url);

// ── Method registry ───────────────────────────────────────────────
//
// Each entry:  [ ipcChannelName,  workerMethodName,  paramKeys ]
//
// paramKeys: ordered list of keys to build the params object from
// renderer args.  The first value is always passed as the primary
// key; the rest map to their key name.

const EXPOSE_METHODS = [
  // ── Core (health / meta) ──
  ['galaxy:ping',              'ping',               []],
  ['galaxy:health',            'health',             []],
  ['galaxy:getStatus',         'get_status',         []],
  ['galaxy:hardinfo',          'hardinfo',           []],
  ['galaxy:vectorInfo',        'vector_info',        []],

  // ── Memory ──
  ['galaxy:recall',            'recall',             ['query', 'top_k', 'session_id']],
  ['galaxy:smartRetrieval',    'smart_retrieval',    ['query', 'top_k', 'session_id']],
  ['galaxy:memorySearch',      'memory_search',      ['query', 'top_k', 'session_id']],
  ['galaxy:memoryStatus',      'memory_status',      []],
  ['galaxy:store',             'store',              ['content', 'source', 'session_id']],
  ['galaxy:saveMemory',        'save_memory',        ['content', 'metadata']],
  ['galaxy:verify',            'verify',             ['claim', 'context']],
  ['galaxy:forget',            'forget',             ['memory_id']],
  ['galaxy:remember',          'remember',           ['key', 'value']],
  ['galaxy:learn',             'learn',              ['content', 'source']],
  ['galaxy:learnPreference',   'learn_preference',   ['key', 'value']],
  ['galaxy:learnCorrection',   'learn_correction',   ['original', 'corrected']],
  ['galaxy:linkTaskMemory',    'link_task_memory',   ['task_id', 'memory_id', 'link_type']],
  ['galaxy:getEntity',         'get_entity',         ['entity_name']],

  // ── Context / DAG / R-CCAM ──
  ['galaxy:rccam',              'rccam',              ['session_id', 'dag_key']],
  ['galaxy:contextAssemble',    'context_assemble',    ['session_id', 'query']],
  ['galaxy:restoreContext',     'restore_context',     ['sessionKey', 'recentDays']],
  ['galaxy:dagIngest',          'dag_ingest',          ['session_id', 'messages', 'dag_key']],
  ['galaxy:dagAssemble',        'dag_assemble',        ['session_id', 'dag_key']],
  ['galaxy:dagCompact',         'dag_compact',         ['session_id', 'dag_key']],
  ['galaxy:dagClearSession',    'dag_clear_session',   ['session_id']],
  ['galaxy:dagStatus',          'dag_status',          ['session_id']],
  ['galaxy:dagSummary',         'dag_summary',         ['session_id']],
  ['galaxy:dagSearch',          'dag_search',          ['query', 'limit', 'exclude_session']],
  ['galaxy:personaSnapshot',    'persona_snapshot',    ['session_id']],
  ['galaxy:getPersonaCore',     'get_persona_core',    []],
  ['galaxy:rlmCompress',        'rlm_compress',        ['messages', 'session_id']],
  ['galaxy:rccamDagStats',      'rccam_dag_stats',     ['session_id']],
  ['galaxy:rccamCompactNeeded', 'rccam_compact_needed',['session_id']],
  ['galaxy:rccamCompactCycle',  'rccam_compact_cycle', ['session_id', 'cycle_id']],
  ['galaxy:expandRccamCycle',   'expand_rccam_cycle',  ['session_id', 'cycle_id']],
  ['galaxy:cognitiveCompress',  'cognitive_compress_dag', ['session_id']],

  // ── Multimodal ──
  ['galaxy:understandImage',   'understand_image',   ['image_b64', 'prompt']],
  ['galaxy:ocrImage',          'ocr_image',           ['image_b64']],
  ['galaxy:recallImages',      'recall_images',       ['query', 'top_k']],

  // ── Smart / Agent ──
  ['galaxy:smartProcess',      'smart_process',       ['query', 'session_id']],
  ['galaxy:answer',            'answer',              ['query', 'session_id']],
  ['galaxy:buildSystemPrompt', 'build_system_prompt', ['session_id']],
  ['galaxy:implicitFeedback',  'implicit_feedback',   ['signal', 'context', 'confidence']],
  ['galaxy:verifyReplyStyle',  'verify_reply_style',  ['text', 'session_id']],

  // ── Workflow / Module ──
  ['galaxy:executeWorkflow',   'execute_workflow',    ['name', 'params']],
  ['galaxy:listWorkflows',     'list_workflows',      []],
  ['galaxy:getWorkflowInfo',   'get_workflow_info',   ['name']],
  ['galaxy:callModule',        'call_module',         ['name', 'params']],
  ['galaxy:listModules',       'list_modules',        []],
  ['galaxy:getModuleInfo',     'get_module_info',     ['name']],

  // ── Admin ──
  ['galaxy:shutdown',          'shutdown',            []],
  ['galaxy:mmapCleanup',       'mmap_cleanup',        []],
];

/**
 * Build the params object from renderer argument values.
 * The first value maps to the first key, etc.
 */
function buildParams(paramKeys, ...args) {
  if (paramKeys.length === 0) return {};
  const params = {};
  for (let i = 0; i < paramKeys.length; i++) {
    if (i < args.length && args[i] !== undefined) {
      params[paramKeys[i]] = args[i];
    }
  }
  return params;
}

/**
 * Register all IPC handlers on the given GalaxyPool instance.
 * Returns a function that unregisters them all.
 */
function registerWorkerHandlers(galaxyPool) {
  const registered = [];

  for (const [channel, method, paramKeys] of EXPOSE_METHODS) {
    const handler = async (_event, ...args) => {
      try {
        const params = buildParams(paramKeys, ...args);
        return await galaxyPool.execute(method, params);
      } catch (e) {
        return { error: e.message };
      }
    };
    ipcMain.handle(channel, handler);
    registered.push(channel);
  }

  // Additional handlers not in the simple mapping
  ipcMain.handle('galaxy:workerStatus', async () => {
    try { return galaxyPool.getStatus(); }
    catch (e) { return { error: e.message }; }
  });

  registered.push('galaxy:workerStatus');

  // installWizard: pass-through to sidecar's zmq install_wizard RPC
  // (sidecar spawns install_wizard.py as subprocess; we wait for the
  // final result via zmq REP).
  const zmq = _require('zeromq');
  ipcMain.handle('galaxy:installWizard', async (_event, args) => {
    let sock;
    try {
      sock = new zmq.Request();
      sock.connect('tcp://127.0.0.1:5757');
      sock.receiveTimeout = 1800_000; // 30 min max for big downloads
      await sock.send(JSON.stringify({
        id: Date.now(),
        method: 'install_wizard',
        params: { args: args || [] },
      }));
      const [reply] = await sock.receive();
      return JSON.parse(reply.toString());
    } catch (e) {
      return { ok: false, error: e.message };
    } finally {
      try { sock?.close(); } catch {}
    }
  });
  registered.push('galaxy:installWizard');

  const unregister = () => {
    for (const ch of registered) {
      ipcMain.removeHandler(ch);
    }
  };

  return { registered, unregister };
}

export { registerWorkerHandlers, EXPOSE_METHODS };
