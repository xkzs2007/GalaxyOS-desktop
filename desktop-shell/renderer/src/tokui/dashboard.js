// renderer/src/tokui/dashboard.js — Agent performance dashboard (D 阶段).
//
// Visualises GalaxyOS session metrics using TokUI [stats] and [chart]
// components.  Shows token usage, session count, tool calls, and latency
// distribution.  Drives off sessionStore + connectionStore.
//
// Layout:
//   ┌─ [stats] row (4 cards) ──────────────────────────────────┐
//   │  会话数  │  消息数  │  记忆数  │  连接状态               │
//   ├─ [chart bar] token 用量 ─────────────────────────────────┤
//   ├─ [chart pie] 模式分布  ──────────────────────────────────┤
//   └─ [chart line] 延迟趋势 ──────────────────────────────────┘

import { getInstance } from './runtime.js';
import { sessionStore } from '../state/session.js';
import { connectionStore } from '../state/connection.js';

// ── Dashboard DSL builder ─────────────────────────────────────

function buildDashboardDSL() {
  const s = sessionStore.get();
  const c = connectionStore.get();

  const sessionCount = s.order?.length ?? 0;
  const connStatus = c.status === 'ok' ? '已连接' : c.status === 'error' ? '断连' : '连接中';
  const connColor = c.status === 'ok' ? 'success' : c.status === 'error' ? 'danger' : 'warning';

  const modeDist = mockModeDistribution();
  const tokenData = mockTokenUsage();
  const latencyData = mockLatencyTrend();

  return `[card tt:"📊 Agent 仪表盘" v:highlight]\n` +
    // Stats row — uses [stat] for big-number cards with trend indicators
    `  [row]\n` +
    `    [stat v:"${sessionCount}" tt:"会话数" suf:"个" i:chat]\n` +
    `    [stat v:"${tokenData.total}" tt:"今日 Tokens" suf:"" i:code trend:"up:12%"]\n` +
    `    [stat v:"1.2s" tt:"平均延迟" suf:"" i:clock trend:"down:0.3s"]\n` +
    `    [stat v:"${connStatus}" tt:"连接" suf:"" i:dot]\n` +
    `  [/row]\n` +
    // Token usage bar chart
    `  [chart tt:"Token 用量分布" type:bar data:"${tokenData.barData}" w:full h:200]\n` +
    // Mode distribution pie chart
    `  [chart tt:"模式分布" type:pie data:"${modeDist}" w:full h:200]\n` +
    // Latency trend line chart
    `  [chart tt:"响应延迟趋势 (最近 8 次)" type:line data:"${latencyData.lineData}" w:full h:180]\n` +
    `[/card]`;
}

// ── Mock data generators (placeholder until sidecar provides real metrics) ──

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

export function renderDashboard(container) {
  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(buildDashboardDSL());
  ui.endStream();
}

// ── Demo builder ──────────────────────────────────────────────

export function buildDemoDashboard() {
  return `[card tt:"📊 Agent 仪表盘 (示例)" v:highlight]\n` +
    `  [row]\n` +
    `    [stat v:"23" tt:"会话数" suf:"个" i:chat]\n` +
    `    [stat v:"4,520" tt:"今日 Tokens" suf:"" i:code]\n` +
    `    [stat v:"1.2s" tt:"平均延迟" suf:"" i:clock]\n` +
    `    [stat v:"已连接" tt:"连接" suf:"" i:dot]\n` +
    `  [/row]\n` +
    `  [chart tt:"Token 用量分布" type:bar data:"Input:2840,Output:1560,MeMo:420,Embed:200" w:full h:200]\n` +
    `  [chart tt:"模式分布" type:pie data:"Ask:12,Process:5,Agent:3,MeMo:2,Plan:1" w:full h:200]\n` +
    `[/card]`;
}
