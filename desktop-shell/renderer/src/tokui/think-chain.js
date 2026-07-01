// renderer/src/tokui/think-chain.js — R-CCAM reasoning chain visualisation.
//
// Uses TokUI [think] / [think-chain] / [think-step] components to render
// the R-CCAM 5-stage pipeline as a live, streaming visual tree.
//
// 5 阶段（R-CCAM）:
//   1. Retrieval  — 查询改写 + 五路并行检索 + RRF 融合
//   2. Cognition  — IntelligentThinkingTrigger top-3 技能推荐
//   3. Control    — 边界检查 + 策略决策 + 约束设定
//   4. Action     — 工具调用 + 答案生成
//   5. Memory     — 记忆写入 + DAG + Cognition Forest
//
// MeMo 3 阶段:
//   1. Grounding  — 事实锚定
//   2. Entity     — 实体链接
//   3. Answer     — 答案生成
//
// Usage:
//   import { startThinkChain, updateThinkStep, endThinkChain } from './think-chain.js';
//   const chain = startThinkChain('R-CCAM', ['Retrieval', 'Cognition', 'Control', 'Action', 'Memory']);
//   await updateThinkStep(chain, 0, 'running', '查询改写中…');
//   await updateThinkStep(chain, 0, 'done', '检索完成 (3 条结果)', 0.8);
//   endThinkChain(chain);

import { getInstance } from './runtime.js';
import { escapeDsl } from '../utils.js';

// ── Stage definitions ──────────────────────────────────────────

export const RCCAM_STAGES = [
  { key: 'retrieval',  label: 'Retrieval 检索',   desc: '查询改写 + 五路并行检索 + RRF 融合' },
  { key: 'cognition',  label: 'Cognition 认知',   desc: 'IntelligentThinkingTrigger 技能推荐' },
  { key: 'control',    label: 'Control 控制',     desc: '边界检查 + 策略决策 + 约束设定' },
  { key: 'action',     label: 'Action 行动',      desc: '工具调用 + 答案生成' },
  { key: 'memory',     label: 'Memory 记忆',      desc: '记忆写入 + DAG + Cognition Forest' },
];

export const MEMO_STAGES = [
  { key: 'grounding',  label: 'Grounding 锚定',   desc: '事实锚定与证据检索' },
  { key: 'entity',     label: 'Entity 实体',       desc: '实体链接与消歧' },
  { key: 'answer',     label: 'Answer 回答',       desc: '答案生成与置信度校验' },
];

// ── Think chain builder ────────────────────────────────────────

/**
 * Start a think chain in the chat container.
 * @param {'rccam'|'memo'} mode
 * @param {number} [confidence] - initial confidence 0-1
 * @returns {{ id: string, steps: string[], mode: string }} chain handle
 */
export function startThinkChain(mode = 'rccam', confidence = 0) {
  const ui = getInstance();
  if (!ui) return null;

  const stages = mode === 'memo' ? MEMO_STAGES : RCCAM_STAGES;
  const id = `think-${Date.now().toString(36)}`;
  const stepIds = stages.map((_, i) => `${id}-s${i}`);
  const label = mode === 'memo' ? 'MeMo 3 阶段' : 'R-CCAM 5 阶段推理';

  const chainTitle = mode === 'memo' ? 'MeMo 推理链' : 'R-CCAM 认知循环';
  const confStr = confidence > 0 ? ` conf:${(confidence * 100).toFixed(0)}` : '';

  // Build initial DSL with all steps pending
  const stepLines = stages.map((s, i) => {
    const icon = stageIcons[s.key] || '○';
    return `[think-step id:"${stepIds[i]}" tt:"${icon} ${s.label}" tx:"${escapeDsl(s.desc)}" pending dur:0s]`;
  }).join('\n    ');

  const dsl =
    `[think tt:"${label}"${confStr}]\n` +
    `  [think-chain tt:"${chainTitle}" running id:"${id}-chain"]\n` +
    `    ${stepLines}\n` +
    `  [/think-chain]\n` +
    `[/think]`;

  ui.startStream();
  ui.feed(dsl);
  ui.endStream();

  return { id: `${id}-chain`, steps: stepIds, mode };
}

/**
 * Update a single step's status and detail via [upd].
 * @param {{ id: string, steps: string[] }} chain - handle from startThinkChain
 * @param {number} stepIdx - 0-based step index
 * @param {'pending'|'running'|'done'|'error'|'danger'} status
 * @param {string} [detail] - updated tx attribute text
 * @param {number} [durationSec] - step duration in seconds
 */
export function updateThinkStep(chain, stepIdx, status, detail, durationSec) {
  if (!chain || !getInstance()) return;

  const stepId = chain.steps[stepIdx];
  if (!stepId) return;

  const updates = [];
  // Clear old state attributes, set new status
  updates.push(`[upd id:${stepId} act:${status}]`);

  if (detail !== undefined) {
    updates.push(`[upd id:${stepId} tx:"${escapeDsl(detail)}"]`);
  }
  if (durationSec !== undefined) {
    updates.push(`[upd id:${stepId} dur:${durationSec.toFixed(1)}s]`);
  }

  const ui = getInstance();
  ui.startStream();
  for (const u of updates) ui.feed(u);
  ui.endStream();
}

/**
 * Mark the think chain as complete (all remaining steps done).
 * @param {{ id: string, steps: string[] }} chain
 * @param {number} [totalDurationSec]
 */
export function endThinkChain(chain, totalDurationSec) {
  if (!chain || !getInstance()) return;

  const ui = getInstance();
  ui.startStream();

  // Mark chain as no longer running
  ui.feed(`[upd id:${chain.id} act:done]`);

  // Mark any still-pending steps as done
  if (totalDurationSec !== undefined) {
    ui.feed(`[upd id:${chain.id} tx:"完成 (${totalDurationSec.toFixed(1)}s)"]`);
  }

  ui.endStream();
}

// ── Stage icons ────────────────────────────────────────────────

const stageIcons = {
  retrieval: '🔍',
  cognition: '🧠',
  control:   '🎯',
  action:    '⚡',
  memory:    '💾',
  grounding: '📌',
  entity:    '🏷️',
  answer:    '💬',
};

/**
 * Build a static DSL demo think chain (for welcome/help pages).
 */
export function buildDemoThinkChain(mode = 'rccam') {
  const stages = mode === 'memo' ? MEMO_STAGES : RCCAM_STAGES;
  const label = mode === 'memo' ? 'MeMo 3 阶段' : 'R-CCAM 5 阶段推理';
  const steps = stages.map((s, i) => {
    const icon = stageIcons[s.key] || '○';
    const status = i === 0 ? 'done' : i === 1 ? 'running' : 'pending';
    const dur = i === 0 ? '0.8s' : '';
    return `[think-step tt:"${icon} ${s.label}" tx:"${escapeDsl(s.desc)}" ${status} dur:${dur}]`;
  }).join('\n    ');
  return `[think tt:"${label}"]\n  [think-chain tt:"推理流程示意"]\n    ${steps}\n  [/think-chain]\n[/think]`;
}
