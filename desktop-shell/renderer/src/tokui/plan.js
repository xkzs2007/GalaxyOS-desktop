// renderer/src/tokui/plan.js — Plan mode visualisation (P1).
//
// Uses TokUI [plan] / [plan-step] components to render plan generation
// as a live, step-animated visual tree. Replaces lightweight notify
// notifications with full component rendering.
//
// Plan steps are created on-the-fly as PUB events arrive:
//   1. Analysis   — 分析用户意图
//   2. Research   — 搜索相关信息
//   3. Generate   — 生成执行计划
//   4. Refine     — 优化与验证
//   5. Output     — 输出最终计划
//
// Usage:
//   import { startPlan, endPlan } from './plan.js';
//   const plan = startPlan('执行计划');
//   // Steps are created dynamically via handleLivePlanEvent in composer.js
//   endPlan(plan, totalSec);

import { getInstance } from './runtime.js';
import { escapeDsl } from '../utils.js';

// ── Plan step defaults ──────────────────────────────────────────

const DEFAULT_STEPS = [
  { key: 'analysis', label: '分析',   desc: '理解用户意图与需求上下文' },
  { key: 'research', label: '研究',   desc: '搜索相关信息与可用工具' },
  { key: 'generate', label: '生成',   desc: '生成结构化执行计划' },
  { key: 'refine',   label: '优化',   desc: '验证可行性与优化步骤' },
  { key: 'output',   label: '输出',   desc: '输出最终计划给用户' },
];

/**
 * Start a plan container in the chat stream.
 * @param {string} title - plan title
 * @returns {{ id: string, steps: Array }} plan handle
 */
export function startPlan(title = '执行计划') {
  const ui = getInstance();
  if (!ui) return null;

  const id = `plan-${Date.now().toString(36)}`;
  const stepIds = DEFAULT_STEPS.map((_, i) => `${id}-s${i}`);

  const stepLines = DEFAULT_STEPS.map((s, i) =>
    `[plan-step id:"${stepIds[i]}" tt:"${s.label}" tx:"${escapeDsl(s.desc)}" pending]`
  ).join('\n    ');

  const dsl =
    `[plan tt:"📋 ${escapeDsl(title)}" id:"${id}" running]\n` +
    `    ${stepLines}\n` +
    `[/plan]`;

  ui.startStream();
  ui.feed(dsl);
  ui.endStream();

  return { id, steps: stepIds.map((sid, i) => ({ id: sid, key: DEFAULT_STEPS[i].key })) };
}

/**
 * Update a plan step's status via [upd].
 * @param {{ id, steps }} plan - handle from startPlan
 * @param {number} stepIdx - 0-based index
 * @param {'pending'|'running'|'done'|'error'} status
 */
export function updatePlanStep(plan, stepIdx, status) {
  if (!plan || !getInstance()) return;
  const step = plan.steps[stepIdx];
  if (!step) return;

  const ui = getInstance();
  ui.startStream();
  ui.feed(`[upd id:${step.id} act:${status}]`);
  ui.endStream();
}

/**
 * End the plan container.
 * @param {{ id }} plan
 * @param {number} totalDurationSec
 * @param {string} [errorMsg] - if provided, shows error state
 */
export function endPlan(plan, totalDurationSec, errorMsg) {
  if (!plan || !getInstance()) return;

  const ui = getInstance();
  ui.startStream();

  if (errorMsg) {
    ui.feed(`[upd id:${plan.id} act:error]`);
    ui.feed(`[upd id:${plan.id} tx:"${escapeDsl(errorMsg)}"]`);
  } else {
    ui.feed(`[upd id:${plan.id} act:done]`);
    if (totalDurationSec !== undefined) {
      ui.feed(`[upd id:${plan.id} tx:"完成 (${totalDurationSec.toFixed(1)}s)"]`);
    }
  }

  ui.endStream();
}

// ── Static demo builder (for welcome/help pages) ──────────────

/**
 * Build a static demo plan with [steps] for welcome/help pages.
 * v9.6: Uses [steps] / [step] for a cleaner procedural flow visualization.
 */
export function buildDemoPlan() {
  return `[steps s:md]\n` +
    DEFAULT_STEPS.map((s, i) => {
      const status = i < 2 ? 'done' : i === 2 ? 'active' : 'pending';
      return `  [step tt:"${escapeDsl(s.label)}" status:${status}]${escapeDsl(s.desc)}[/step]`;
    }).join('\n') +
    `\n[/steps]`;
}
