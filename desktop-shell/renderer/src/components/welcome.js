// renderer/src/components/welcome.js — 对话区空状态提示
//
// v10: 极简版 — 只显示一行欢迎语 + 模式快捷入口
// 不再灌入 demo dashboard / think-chain / agent / memory 等展示内容
// 那些应该在 details 面板或用户主动触发时才显示

import { getInstance } from '../tokui/runtime.js';

const MODES = [
  { id: 'ask',     icon: '💬', label: '提问',     desc: '快速问答' },
  { id: 'process', icon: '🧠', label: '深度分析',  desc: 'R-CCAM 五阶段推理' },
  { id: 'agent',   icon: '🤖', label: 'Agent',    desc: '执行 shell / 读写文件' },
  { id: 'memo',    icon: '🧬', label: '记忆',     desc: '检索长期记忆' },
];

/** Render a lightweight empty-state into the chat container. */
export function renderWelcome() {
  const host = document.getElementById('tokui-container');
  if (!host) return;
  if (host.innerHTML.trim()) return; // don't overwrite existing chat

  const ui = getInstance();
  if (!ui) return;

  const modeCards = MODES.map(m =>
    `[feature tt:"${m.icon} ${m.label}" tx:"${m.desc}" clk:onWelcomePick]`
  ).join('\n  ');

  ui.startStream(host);
  ui.feed(`[empty tt:"👋 你好，我是 GalaxyOS" desc:"在下方输入框开始对话，或选择一个模式快速开始" i:sparkles]`);
  ui.feed(`[row]`);
  ui.feed(`  ${modeCards}`);
  ui.feed(`[/row]`);
  ui.endStream();
}

// Keep demo builders for backward compat (welcome page in setup)
export function buildDemoThinkChain() { return ''; }
export function buildDemoAgent() { return ''; }
export function buildDemoMemoryTimeline() { return ''; }
export function buildDemoDashboard() { return ''; }
export function buildDemoPlan() { return ''; }
