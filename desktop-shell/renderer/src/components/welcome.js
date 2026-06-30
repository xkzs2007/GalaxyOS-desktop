// renderer/src/components/welcome.js — first-run welcome page.
//
// C 阶段：用 [welcome tt: st:][feature ...] 替换手写 hardcoded welcome。
// 用户点 feature → clk:onWelcomePick → 自动切到对应 mode。

import { bootTokUI } from '../tokui/runtime.js';

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
  return `[welcome tt:"欢迎使用 GalaxyOS 桌面端" st:"独立 AI Agent · 76 技能 · 多 LLM"]\n  ${features}\n[/welcome]`;
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
