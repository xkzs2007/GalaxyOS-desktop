// renderer/src/tokui/tool-call.js — Agent tool-call visualisation.
//
// Uses TokUI [tool-call] and [agent] components to render live
// tool execution status as streaming, step-animated widgets.
//
// [tool-call] — individual tool invocation with status timeline
// [agent]     — Agent session wrapper grouping tool calls
//
// P1: [terminal] for shell/list_dir output, [sandbox] for file read/write.
//
// GalaxyOS Agent 模式工具集:
//   read_file    — 读取文件
//   write_file   — 写文件
//   web_search   — 网络搜索
//   web_fetch    — 抓取网页
//   call_tool    — 通用工具调用
//   shell        — 执行 shell 命令（通过 sidecar）
//   list_dir     — 列出目录
//
// Usage:
//   import { startAgent, newToolCall, completeToolCall, endAgent, feedToolOutput } from './tool-call.js';
//
//   const agentId = startAgent('GalaxyOS Agent');
//   const tc = newToolCall(agentId, 'read_file', { path: '/src/main.ts' });
//   completeToolCall(tc, 'done', '已读取 234 行', 0.12);
//   feedToolOutput('read_file', { path: '/src/main.ts' }, fileContent);
//   endAgent(agentId, 1.5);

import { getInstance } from './runtime.js';
import { escapeDsl } from '../utils.js';

// ── Tool metadata ──────────────────────────────────────────────

const TOOL_META = {
  read_file:     { icon: '📖', label: '读取文件',   color: 'info' },
  write_file:    { icon: '✏️', label: '写入文件',   color: 'success' },
  web_search:    { icon: '🔍', label: '网络搜索',   color: 'primary' },
  web_fetch:     { icon: '🌐', label: '抓取网页',   color: 'primary' },
  call_tool:     { icon: '🔧', label: '工具调用',   color: 'info' },
  shell:         { icon: '⚡', label: 'Shell',       color: 'warning' },
  list_dir:      { icon: '📂', label: '列出目录',   color: 'info' },
  search:        { icon: '🔎', label: '搜索',       color: 'primary' },
  install_wizard:{ icon: '📥', label: '安装向导',   color: 'info' },
  health:        { icon: '💓', label: '健康检查',   color: 'success' },
};

/** Get tool label + icon (with fallback) */
function toolLabel(name) {
  const m = TOOL_META[name];
  return m ? `${m.icon} ${m.label}` : `🔧 ${name}`;
}
function toolColor(name) {
  return TOOL_META[name]?.color ?? 'info';
}

/** Format params for display */
function formatParams(params) {
  if (!params || typeof params !== 'object') return '';
  const entries = Object.entries(params).slice(0, 3);
  return entries.map(([k, v]) => {
    const val = typeof v === 'string' ? (v.length > 40 ? v.slice(0, 37) + '...' : v) : JSON.stringify(v);
    return `${k}: ${val}`;
  }).join(', ');
}

// ── Agent container ────────────────────────────────────────────

/**
 * Start an Agent container that will host tool-call components.
 * @returns {{ id: string, toolCalls: Array }} agent handle
 */
export function startAgent(name = 'GalaxyOS Agent', model = '') {
  const ui = getInstance();
  if (!ui) return null;

  const id = `agent-${Date.now().toString(36)}`;
  const modelAttr = model ? ` model:${model}` : '';

  const dsl =
    `[agent tt:"🤖 ${escapeDsl(name)}" running id:"${id}"${modelAttr}]\n` +
    `[/agent]`;

  ui.startStream();
  ui.feed(dsl);
  ui.endStream();

  return { id, toolCalls: [] };
}

/**
 * Add a tool-call inside the agent container.
 * @returns {{ agentId: string, toolId: string }} tool handle
 */
export function newToolCall(agentId, toolName, params) {
  if (!agentId || !getInstance()) return null;

  const toolId = `tc-${Date.now().toString(36)}`;
  const iconLabel = toolLabel(toolName);
  const color = toolColor(toolName);
  const paramsStr = params ? escapeDsl(formatParams(params)) : '';
  const paramsAttr = paramsStr ? ` params:"${paramsStr}"` : '';

  const dsl =
    `[tool-call id:"${toolId}" tt:"${iconLabel}" running v:${color}${paramsAttr}]\n` +
    `[/tool-call]`;

  const ui = getInstance();
  ui.startStream();
  ui.feed(dsl);
  ui.endStream();

  return { agentId, toolId };
}

/**
 * Complete a tool call with final status.
 * @param {'done'|'error'|'danger'|'denied'} status
 * @param {string} [result]  - result summary text
 * @param {number} [durationSec] - execution time
 */
export function completeToolCall(toolHandle, status, result, durationSec) {
  if (!toolHandle || !getInstance()) return;

  const updates = [];
  updates.push(`[upd id:${toolHandle.toolId} act:${status}]`);

  if (result !== undefined) {
    updates.push(`[upd id:${toolHandle.toolId} tx:"${escapeDsl(result)}"]`);
  }
  if (durationSec !== undefined) {
    updates.push(`[upd id:${toolHandle.toolId} dur:${durationSec.toFixed(2)}s]`);
  }

  const ui = getInstance();
  ui.startStream();
  for (const u of updates) ui.feed(u);
  ui.endStream();
}

/**
 * End the agent container.
 */
export function endAgent(agentId, durationSec) {
  if (!agentId || !getInstance()) return;

  const ui = getInstance();
  ui.startStream();
  ui.feed(`[upd id:${agentId} act:done]`);
  if (durationSec !== undefined) {
    ui.feed(`[upd id:${agentId} tx:"完成 (${durationSec.toFixed(1)}s)"]`);
  }
  ui.endStream();
}

// ── Batch tool execution helper ─────────────────────────────────

/**
 * Execute tools sequentially with visual feedback.
 * Each tool shows running → done/error status.
 *
 * @param {string} agentId
 * @param {Array<{name: string, params: object, fn: () => Promise<any>}>} steps
 * @param {number} [minDelayMs=80] minimum delay between steps for visual feedback
 */
export async function executeAgentSteps(agentId, steps, minDelayMs = 80) {
  if (!agentId || !steps?.length) return;

  const t0 = performance.now();

  for (const step of steps) {
    const tc = newToolCall(agentId, step.name, step.params);
    if (!tc) continue;

    const st0 = performance.now();
    try {
      const result = await step.fn();
      const dur = (performance.now() - st0) / 1000;
      const summary = summarizeResult(step.name, result);
      completeToolCall(tc, 'done', summary, dur);
    } catch (e) {
      const dur = (performance.now() - st0) / 1000;
      completeToolCall(tc, 'error', e.message ?? String(e), dur);
    }

    // Brief delay for visual rhythm
    if (minDelayMs > 0) await delay(minDelayMs);
  }

  const totalSec = (performance.now() - t0) / 1000;
  endAgent(agentId, totalSec);
}

// ── Result summarisers ─────────────────────────────────────────

function summarizeResult(name, result) {
  switch (name) {
    case 'read_file':
      return result?.content
        ? `读取 ${result.length ?? '?'} 字节`
        : '读取完成';
    case 'write_file':
      return `写入 ${result?.bytes ?? '?'} 字节`;
    case 'web_search':
      return result?.content
        ? `搜索返回 ${result.content.length} 字符`
        : '搜索完成';
    case 'web_fetch':
      return result?.ok
        ? `抓取 ${result.content?.length ?? 0} 字符`
        : '抓取失败';
    case 'list_dir':
      return result?.files
        ? `${result.files.length} 个文件`
        : '列目录完成';
    case 'shell':
      return result?.stdout
        ? `${result.stdout.length} 字符输出`
        : '命令执行完成';
    default:
      return result?.ok ? '完成' : (result?.error ? '失败' : '完成');
  }
}

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ── Tool output rendering (P1: terminal / sandbox) ────────────

/**
 * Detect programming language from filename extension.
 */
function detectLang(filename) {
  if (!filename) return '';
  const ext = filename.split('.').pop()?.toLowerCase();
  const MAP = {
    js: 'javascript', ts: 'typescript', tsx: 'typescript', jsx: 'javascript',
    py: 'python', rs: 'rust', go: 'go', java: 'java', rb: 'ruby',
    c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
    html: 'html', css: 'css', scss: 'scss', less: 'less',
    json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml',
    md: 'markdown', xml: 'xml', sql: 'sql', sh: 'bash', bash: 'bash',
    dockerfile: 'dockerfile', makefile: 'makefile',
  };
  return MAP[ext] || '';
}

/**
 * Render a single line of tool output as terminal content.
 * Used for shell stdout streaming.
 */
function wrapToolLine(line) {
  return String(line ?? '');
}

/**
 * Feed tool output into the chat stream using the best TokUI component.
 * - shell / list_dir   → [terminal] dark terminal with monospace output
 * - read_file / write_file → [sandbox] code block with syntax highlight
 * - web_search / web_fetch → [card] with formatted result text
 * - call_tool / default → [card] compact result card
 *
 * @param {string} toolName   - tool slug (e.g. 'shell', 'read_file')
 * @param {object} params     - tool invocation params ({ path, query, ... })
 * @param {string} content    - raw output content
 */
export function feedToolOutput(toolName, params, content) {
  const ui = getInstance();
  if (!ui || !content) return;

  const title = params?.path || params?.url || params?.query || toolName;
  const contentStr = String(content);

  if (toolName === 'shell' || toolName === 'list_dir') {
    // [terminal] — dark background, monospace, scrollable
    ui.startStream();
    ui.feed(`[terminal tt:"${escapeDsl(String(title))}" v:dark]\n${escapeDsl(contentStr)}\n[/terminal]`);
    ui.endStream();
  } else if (toolName === 'read_file' || toolName === 'write_file') {
    // [sandbox] — syntax-highlighted code block
    const lang = detectLang(params?.path || params?.filename || '');
    const langAttr = lang ? ` lang:${lang}` : '';
    ui.startStream();
    ui.feed(`[sandbox${langAttr} tt:"${escapeDsl(String(title))}"]\n${escapeDsl(contentStr)}\n[/sandbox]`);
    ui.endStream();
  } else {
    // Generic tool result — compact card
    const truncated = contentStr.length > 2000 ? contentStr.slice(0, 1997) + '…' : contentStr;
    ui.startStream();
    ui.feed(`[card tt:"${escapeDsl(toolLabel(toolName))} 结果" v:${toolColor(toolName)}]\n[p]${escapeDsl(truncated)}[/p]\n[/card]`);
    ui.endStream();
  }
}

// ── Static demo builder (for welcome/help pages) ──────────────

/**
 * Build a static DSL demo agent with tool calls.
 */
export function buildDemoAgent() {
  // A simulated agent session with 4 tool calls in different states
  return `[agent tt:"🤖 GalaxyOS Agent" done]\n` +
    `  [tool-call tt:"📖 读取文件" done v:info params:"path: /src/main.ts" tx:"读取 38,993 字节" dur:0.12s]\n` +
    `  [sandbox lang:typescript tt:"/src/main.ts"]\n` +
    `import { bootTokUI } from './tokui/runtime.js';\n` +
    `import { initSidebar } from './components/sidebar.js';\n` +
    `async function main() {\n` +
    `  await bootTokUI('#tokui-container');\n` +
    `  initSidebar();\n` +
    `}\n` +
    `[/sandbox]\n` +
    `  [tool-call tt:"⚡ Shell" done v:warning params:"cmd: ls -la" tx:"5 行输出" dur:0.45s]\n` +
    `  [terminal v:dark]\n` +
    `$ ls -la\n` +
    `total 128\n` +
    `drwxr-xr-x  12 user  staff   384 Jul  1 10:00 .\n` +
    `drwxr-xr-x   6 user  staff   192 Jun 30 09:00 ..\n` +
    `[/terminal]\n` +
    `  [tool-call tt:"🔍 网络搜索" done v:primary params:"query: GalaxyOS architecture" tx:"3 条结果" dur:1.2s]\n` +
    `  [tool-call tt:"✏️ 写入文件" running v:success params:"path: /output.md"]\n` +
    `[/agent]`;
}
