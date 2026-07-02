import test from 'node:test';
import assert from 'node:assert/strict';
import { buildWorkbenchHeader, buildTaskPanel } from '../renderer/src/design-system.js';

test('buildWorkbenchHeader renders the workbench summary', () => {
  const html = buildWorkbenchHeader({
    title: 'GalaxyOS',
    subtitle: '本地执行型智能助手',
    status: '已连接',
  });

  assert.match(html, /workspace-toolbar/);
  assert.match(html, /GalaxyOS/);
  assert.match(html, /本地执行型智能助手/);
  assert.match(html, /已连接/);
});

test('buildTaskPanel renders an execution overview', () => {
  const html = buildTaskPanel({
    title: '执行面板',
    summary: '当前任务链路清晰可追踪',
    steps: ['生成执行计划', '调用 Agent', '检索记忆'],
  });

  assert.match(html, /task-panel/);
  assert.match(html, /执行面板/);
  assert.match(html, /当前任务链路清晰可追踪/);
  assert.match(html, /生成执行计划/);
});
