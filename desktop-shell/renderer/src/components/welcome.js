// renderer/src/components/welcome.js — first-run welcome page.
//
// D 阶段（TokUI 深用）：
//   - [welcome][feature] 快速入口
//   - [think][think-chain] R-CCAM 推理流程示意
//   - [agent][tool-call] Agent 工具调用示意
//   - [timeline] 长期记忆时间线示意
//   - 用户点 feature → clk:onWelcomePick → 自动切到对应 mode

import { bootTokUI } from '../tokui/runtime.js';
import { buildDemoThinkChain } from '../tokui/think-chain.js';
import { buildDemoAgent } from '../tokui/tool-call.js';
import { buildDemoMemoryTimeline } from '../tokui/memory-browser.js';
import { buildDemoDashboard } from '../tokui/dashboard.js';
import { buildDemoPlan } from '../tokui/plan.js';

const FEATURES = [
  { id: 'ask',     icon: '💬', title: 'Ask 模式',      desc: '简单提问，自动路由：MeMo / process / fast_path' },
  { id: 'process', icon: '🧠', title: 'Process 模式',  desc: '复杂任务，走 R-CCAM 五阶段 + 推理链 + 工具调用' },
  { id: 'agent',   icon: '🤖', title: 'Agent 模式',    desc: '跑 shell / 读文件 / 写文件 / 搜索 / 列目录' },
  { id: 'memo',    icon: '🧬', title: 'MeMo 调试',     desc: '直调 3-stage 协议（Grounding → Entity → Answer）' },
];

function buildWelcomeDSL() {
  const features = FEATURES.map(f =>
    `[feature tt:"${f.icon} ${f.title}" tx:"${f.desc}" i:code clk:onWelcomePick]`
  ).join('\n  ');

  // Demo think chain (R-CCAM 5-stage)
  const rccamDemo = buildDemoThinkChain('rccam');

  // Demo plan steps
  const planDemo = buildDemoPlan();

  // Demo agent tool calls
  const agentDemo = buildDemoAgent();

  // Demo memory timeline
  const memoryDemo = buildDemoMemoryTimeline();

  // Demo dashboard
  const dashDemo = buildDemoDashboard();

  return `[watermark tx:"GalaxyOS" s:md gap:160 ro]\n` +
    `[welcome tt:"欢迎使用 GalaxyOS 桌面端" st:"独立 AI Agent · 76 技能 · 多 LLM"]\n  ${features}\n[/welcome]` +
    `\n[card tt:"📊 快速概览" v:highlight]\n` +
    `  [row]\n` +
    `    [stat v:"76" tt:"技能" suf:"个" i:code]\n` +
    `    [stat v:"5" tt:"LLM" suf:"Slot" i:brain]\n` +
    `    [stat v:"6" tt:"模式" suf:"种" i:layout]\n` +
    `    [stat v:"v9.6" tt:"版本" suf:"" i:tag]\n` +
    `  [/row]\n` +
    `[/card]\n` +
    `\n[dv]` +
    `\n${dashDemo}` +
    `\n[p v:muted sm]↑ 仪表盘：侧边栏「📊 仪表盘」查看 Token / 延迟 / 模式分布[/p]` +
    `\n[dv]` +
    `\n${planDemo}` +
    `\n[p v:muted sm]↑ 计划步骤：Plan 模式按 分析→研究→生成→优化→输出 五阶段生成执行计划[/p]` +
    `\n[dv]` +
    `\n${rccamDemo}` +
    `\n[p v:muted sm]↑ 推理链示意：Process 模式按 5 阶段执行复杂任务[/p]` +
    `\n[dv]` +
    `\n${agentDemo}` +
    `\n[p v:muted sm]↑ Agent 工具调用：Agent 模式执行 shell / 读写文件 / 搜索[/p]` +
    `\n[dv]` +
    `\n${memoryDemo}` +
    `\n[p v:muted sm]↑ 长期记忆时间线：侧边栏「🧬 记忆」可浏览全部记忆条目[/p]\n` +
    `[/watermark]`;
}

/** Render welcome into the chat container. Called by main.js after boot.
 *  No-op if the container already has content (restored from session). */
export function renderWelcome() {
  const host = document.getElementById('tokui-container');
  if (!host) return;
  if (host.innerHTML.trim()) return;
  bootTokUI().then((ui) => {
    if (!ui) return;
    host.innerHTML = '';
    ui.startStream(host);
    ui.feed(buildWelcomeDSL());
    ui.endStream();
  });
}
