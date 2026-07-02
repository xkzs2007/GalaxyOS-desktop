// renderer/src/tokui/demo-panel.js — 演示面板（Plan / Agent 可视化示例）

import { getInstance } from './runtime.js';

export async function renderDemoPanel(container) {
  const host = typeof container === 'string' ? document.getElementById(container) : container;
  if (!host) return;
  const ui = getInstance();
  if (!ui) return;

  host.innerHTML = '';
  ui.startStream(host);

  // Plan 演示
  ui.feed(`[card tt:"演示：计划与执行" v:highlight]`);
  ui.feed(`  [p v:muted]这是一个 Plan / 执行可视化示例，展示计划步骤的增量更新与状态。[/p]`);
  ui.feed(`  [plan tt:"部署任务"]`);
  ui.feed(`    [plan-step id:step-1]准备构建环境[/plan-step]`);
  ui.feed(`    [plan-step id:step-2]安装依赖[/plan-step]`);
  ui.feed(`    [plan-step id:step-3]运行测试[/plan-step]`);
  ui.feed(`    [plan-step id:step-4]打包并发布[/plan-step]`);
  ui.feed(`  [/plan]`);
  ui.feed(`[/card]`);

  // Agent / tool-call 演示
  ui.feed(`[card tt:"演示：Agent 与工具调用" v:highlight]`);
  ui.feed(`  [p v:muted]Agent 会创建 tool-call 实例并显示输出；下面为示例工具调用流程。[/p]`);
  ui.feed(`  [agent tt:"Demo Agent"]`);
  ui.feed(`    [tool-call id:tc-1 tt:"拉取代码" status:running]正在从仓库克隆…[/tool-call]`);
  ui.feed(`    [tool-call id:tc-2 tt:"运行构建" status:pending][/tool-call]`);
  ui.feed(`  [/agent]`);
  ui.feed(`[/card]`);

  // Small dashboard snippet
  ui.feed(`[card tt:"小仪表盘" v:muted]`);
  ui.feed(`  [row]`);
  ui.feed(`    [stat v:"3" tt:"未完成" sm]`);
  ui.feed(`    [stat v:"1200" tt:"今日 Tokens" sm]`);
  ui.feed(`  [/row]`);
  ui.feed(`  [chart tt:"示例分布" type:bar data:"A:40,B:25,C:35" w:full h:140]`);
  ui.feed(`[/card]`);

  ui.endStream();
}

export default renderDemoPanel;

/**
 * Simulate incremental updates for the demo: advance plan steps and tool-calls.
 * This will send `[upd id:...]` fragments and terminal outputs to demonstrate streaming.
 */
export async function simulateDemo() {
  // Use galaxy emitters so composer demo subscriptions receive events
  const gal = window.galaxy;
  if (!gal) return;
  // Start a demo stream via composer so subscriptions exist and we have a stream_id
  const { startDemoStream } = await import('../components/composer.js');
  const { streamId } = startDemoStream('plan');

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Emit plan step running/done events
  gal.emitPlanStep({ stream_id: streamId, status: 'running', step_id: '1', step_title: '准备构建环境' });
  await sleep(700);
  gal.emitPlanStep({ stream_id: streamId, status: 'done', step_id: '1' });
  await sleep(400);

  gal.emitPlanStep({ stream_id: streamId, status: 'running', step_id: '2', step_title: '安装依赖' });
  await sleep(600);
  gal.emitAgentTool({ stream_id: streamId, type: 'tool_start', tool_name: 'git-clone', params: {} });
  await sleep(300);
  gal.emitAgentTool({ stream_id: streamId, type: 'tool_done', tool_name: 'git-clone', output: "Cloning into 'repo'...\ndone.\n", dur_ms: 300 });
  await sleep(400);
  gal.emitPlanStep({ stream_id: streamId, status: 'done', step_id: '2' });

  await sleep(500);
  gal.emitPlanStep({ stream_id: streamId, status: 'running', step_id: '3', step_title: '运行测试' });
  await sleep(500);
  gal.emitAgentTool({ stream_id: streamId, type: 'tool_start', tool_name: 'build', params: {} });
  await sleep(600);
  gal.emitAgentTool({ stream_id: streamId, type: 'tool_done', tool_name: 'build', output: 'Building...\nCompiled successfully.\n', dur_ms: 800 });
  await sleep(400);
  gal.emitPlanStep({ stream_id: streamId, status: 'done', step_id: '3' });

  await sleep(400);
  gal.emitPlanStep({ stream_id: streamId, status: 'running', step_id: '4', step_title: '打包并发布' });
  await sleep(600);
  gal.emitPlanStep({ stream_id: streamId, status: 'done', step_id: '4' });
}
