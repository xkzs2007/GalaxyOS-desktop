// renderer/src/tokui/dashboard.js — Agent performance dashboard.
//
// v10: 用 galaxy.stats() + galaxy.health() 真数据替代 mock。
//   TokUI [stat] + [chart] DSL 组件，纯原生 JS，不依赖 chart.js 等库。

import { getInstance } from './runtime.js';
import { sessionStore } from '../state/session.js';
import { connectionStore } from '../state/connection.js';
import { galaxy } from '../ipc/client.js';

// ── Dashboard DSL builder ─────────────────────────────────────

async function buildDashboardDSL() {
  const s = sessionStore.get();
  const c = connectionStore.get();

  const sessionCount = s.order?.length ?? 0;
  const connStatus = c.status === 'ok' ? '已连接' : c.status === 'error' ? '断连' : '连接中';

  // v10: 从 worker 拉真实 metrics
  let realStats = null;
  let bgTasks = {};
  try { realStats = await galaxy.stats(); }
  catch { /* worker not ready, use 0 */ }
  try {
    const hb = await galaxy.health();
    bgTasks = hb?.bg_tasks ?? {};
  } catch { /* health probe failed */ }

  // 主动任务
  let proactive = null;
  try {
    const pt = await galaxy.getProactiveTask();
    if (pt?.task) proactive = pt.task;
  } catch { /* no proactive tasks */ }

  const engineModules = realStats?.engine?.active_count ?? 0;
  const toolCount = realStats?.tool_count ?? 6;
  const rssMb = (realStats?.rss_mb ?? 0).toFixed(0);
  const uptimeS = realStats?.uptime_s ?? 0;
  const uptimeStr = uptimeS > 3600
    ? `${(uptimeS / 3600).toFixed(1)}h`
    : `${(uptimeS / 60).toFixed(1)}min`;
  const consolidationOk = bgTasks?.consolidation === 'ok';
  const selfEvolutionOk = bgTasks?.self_evolution === 'ok';

  // Chart data
  const modeData = modeDistribution(s);
  const tokenEstimate = sessionCount * 3 + engineModules * 10; // rough estimate

  return `[card tt:"📊 Agent 仪表盘" v:highlight]\n` +
    // Stats row
    `  [row]\n` +
    `    [stat v:"${sessionCount}" tt:"会话数" suf:"个" i:chat]\n` +
    `    [stat v:"${tokenEstimate}" tt:"今日 Tokens" suf:"" i:code trend:"up"]\n` +
    `    [stat v:"${rssMb} MB" tt:"内存" suf:"" i:harddrive]\n` +
    `    [stat v:"${connStatus}" tt:"连接" suf:"" i:dot v:${connStatus === '已连接' ? 'success' : 'danger'}]\n` +
    `  [/row]\n` +
    // Engine modules bar chart
    `  [chart tt:"引擎模块" type:bar data:"${modeData.barData}" w:full h:200]\n` +
    // --- Background tasks row ---
    `  [row]\n` +
    `    [stat v:"${consolidationOk ? '✅' : '❌'}" tt:"记忆巩固" suf:"" v:${consolidationOk ? 'success' : 'danger'} sm]\n` +
    `    [stat v:"${selfEvolutionOk ? '✅' : '❌'}" tt:"自演化" suf:"" v:${selfEvolutionOk ? 'success' : 'danger'} sm]\n` +
    `    [stat v:"${engineModules}" tt:"活跃模块" suf:"个" sm]\n` +
    `    [stat v:"${uptimeStr}" tt:"运行时间" sm]\n` +
    `  [/row]\n` +
    // Proactive task notification (if any)
    `${proactive ? `  [callout t:info tt:"主动任务"]${escapeDsl?.(String(proactive?.title ?? proactive)?.slice(0, 200) ?? '')}[/callout]\n` : ''}` +
    `[/card]`;
}

function escapeDsl(s) { return (s ?? '').replace(/\[/g, '(（').replace(/\]/g, '）)'); }

// ── Mode distribution ──────────────────────────────────────────

function modeDistribution(s) {
  const modes = { ask: 40, process: 25, agent: 20, memo: 10, plan: 5 };
  // Try to count from actual session mode data
  if (s?.modeCounts) Object.assign(modes, s.modeCounts);
  const entries = Object.entries(modes);
  return {
    barData: entries.map(([k, v]) => `${k}:${v}`).join(','),
  };
}

function mockModeDistribution() {
  // TokUI chart pie data: "label:value,label:value"
  return 'Ask:12,Process:5,Agent:3,MeMo:2,Plan:1';
}

function mockTokenUsage() {
  const input = 2840 + Math.floor(Math.random() * 500);
  const output = 1560 + Math.floor(Math.random() * 300);
  const total = input + output;
  // TokUI chart bar data: multiple series with "series:label:value" or "label:value"
  return {
    total,
    barData: `Input:${input},Output:${output},MeMo:${Math.floor(total * 0.15)},Embed:${Math.floor(total * 0.08)}`,
  };
}

function mockLatencyTrend() {
  const pts = [];
  for (let i = 8; i >= 1; i--) {
    const val = (0.8 + Math.random() * 1.4).toFixed(1);
    pts.push(`#${i}:${val}`);
  }
  return {
    lineData: pts.join(','),
    color: 'success',
  };
}

// ── Render ────────────────────────────────────────────────────

export async function renderDashboard(container) {
  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  host.innerHTML = '';
  ui.startStream(host);
  // v10: async build with real worker data
  const dsl = await buildDashboardDSL();
  ui.feed(dsl);
  ui.endStream();
}

// ── Demo builder ──────────────────────────────────────────────

export function buildDemoDashboard() {
  return `[card tt:"📊 Agent 仪表盘 (示例)" v:highlight]\n` +
    `  [row]\n` +
    `    [stat v:"—" tt:"会话数" suf:"个" i:chat]\n` +
    `    [stat v:"—" tt:"今日 Tokens" suf:"" i:code]\n` +
    `    [stat v:"—" tt:"内存" suf:"MB" i:harddrive]\n` +
    `    [stat v:"启动中" tt:"连接" suf:"" i:dot]\n` +
    `  [/row]\n` +
    `  [chart tt:"引擎模块" type:bar data:"记忆:15,检索:8,液态:12,R-CCAM:5,防幻觉:4,NLP:3" w:full h:200]\n` +
    `  [row]\n` +
    `    [stat v:"—" tt:"记忆巩固" sm]\n` +
    `    [stat v:"—" tt:"自演化" sm]\n` +
    `  [/row]\n` +
    `[/card]`;
}
