// renderer/src/tokui/setup.js — First-launch setup wizard (TokUI DSL).
//
// Replaces the raw HTML setup page in main.js with TokUI-powered
// rendering: [card] container, [steps]/[step] progress, [terminal] log,
// [btn] actions, [upd] incremental updates.
//
// Flow:
//   1. renderSetupPage()   → render initial DSL with steps
//   2. User clicks "开始安装" → startSetup() handler
//   3. Progress updates via [upd] to step status and terminal
//   4. On completion → completeSetup() IPC → reload → main app

import { getInstance, registerHandler } from './runtime.js';
import notify from './notify.js';

// ── State ──────────────────────────────────────────────────────

let _running = false;
let _host = null;

// ── DSL builders ───────────────────────────────────────────────

function buildSetupDSL() {
  return `[card tt:"🚀 欢迎使用 GalaxyOS" v:highlight]\n` +
    `  [p v:muted]你的桌面 AI 助手 · 开箱即用 69 个技能[/p]\n` +
    `  [callout t:info tt:"首次启动"]首次启动需要安装 Python 依赖库（约 200MB），以启用完整的 AI 认知引擎。[/callout]\n` +
    `  [steps s:md id:setup-steps]\n` +
    `    [step id:step-detect tt:"检测 Python" status:pending]扫描系统中可用的 Python 3.11+ 解释器[/step]\n` +
    `    [step id:step-core tt:"核心依赖" status:pending]pip install -r requirements-core.txt（~200MB）[/step]\n` +
    `    [step id:step-heavy tt:"AI 组件（可选）" status:pending]torch / faiss / transformers（~3GB）[/step]\n` +
    `    [step id:step-restart tt:"重启引擎" status:pending]重启 Python 侧车进程以加载新依赖[/step]\n` +
    `  [/steps]\n` +
    `  [terminal id:setup-log v:dark]等待操作…[/terminal]\n` +
    `  [row align:right]\n` +
    `    [btn id:setup-start tx:"⚡ 开始安装" v:primary clk:onSetupStart]\n` +
    `    [btn id:setup-skip tx:"跳过（轻量模式）" v:ghost clk:onSetupSkip]\n` +
    `  [/row]\n` +
    `[/card]`;
}

// ── [upd] helpers ──────────────────────────────────────────────

function feedUpd(dsl) {
  const ui = getInstance();
  if (!ui || !_host) return;
  // [upd] feeds are single-shot — they don't need stream begin/end,
  // but we use startStream+feed+endStream to be safe with TokUI's
  // internal parser state.
  try {
    ui.startStream(_host);
    ui.feed(dsl);
    ui.endStream();
  } catch {
    // Best-effort; don't crash the setup flow on a render hiccup.
  }
}

function updStep(id, status) {
  // status: pending | running | done | error
  feedUpd(`[upd id:${id} status:${status}]`);
}

function appendLog(line) {
  // TokUI [terminal] is raw-content — append directly to its DOM text node.
  if (!_host) return;
  const term = _host.querySelector('.tokui-terminal__content');
  if (term) {
    term.textContent += line + '\n';
    term.scrollTop = term.scrollHeight;
  }
}

function clearLog() {
  if (!_host) return;
  const term = _host.querySelector('.tokui-terminal__content');
  if (term) term.textContent = '';
}

// ── Setup lifecycle ────────────────────────────────────────────

export async function renderSetupPage() {
  const container = document.getElementById('tokui-container');
  if (!container) return;

  const ui = getInstance();
  if (!ui) {
    console.warn('[setup] TokUI not ready, cannot render setup page');
    return;
  }

  _host = container;
  _host.innerHTML = '';

  ui.startStream(_host);
  ui.feed(buildSetupDSL());
  ui.endStream();
}

// ── Handlers (registered globally) ─────────────────────────────

registerHandler('onSetupStart', async () => {
  if (_running) return;
  _running = true;

  const gal = window.galaxy;
  if (!gal?.installWizard) {
    appendLog('[错误] sidecar 未启动，无法安装依赖');
    notify.error('sidecar 未启动');
    _running = false;
    return;
  }

  // Disable buttons
  feedUpd('[upd id:setup-start v:muted tx:"安装中…"]');
  feedUpd('[upd id:setup-skip v:muted]');

  clearLog();
  updStep('step-detect', 'running');
  appendLog('启动依赖安装向导…');

  // Phase 1: install core deps
  try {
    const result = await gal.installWizard(
      ['--install-deps'],
      (evt) => {
        if (evt.event === 'line' && evt.line) {
          const line = evt.line.trim();
          if (line) appendLog(line);
          if (line.includes('Dependencies installed successfully') ||
              line.includes('Core dependencies installed OK')) {
            updStep('step-detect', 'done');
            updStep('step-core', 'done');
          }
        }
      },
      1200
    );

    if (!result?.ok) {
      updStep('step-detect', 'error');
      updStep('step-core', 'error');
      appendLog('依赖安装失败，请检查 Python 3.11+ 是否已安装');
      appendLog('手动运行: pip install -r requirements-core.txt');
      feedUpd('[upd id:setup-skip v:ghost]');
      _running = false;
      return;
    }

    updStep('step-detect', 'done');
    updStep('step-core', 'done');
    appendLog('核心依赖安装完成！');

    // Phase 2: heavy AI deps (always install — 60s in CI, core to the
    // liquid neural memory + knowledge graph + CfC reasoning).
    updStep('step-heavy', 'running');
    appendLog('安装重型 AI 组件 (torch / faiss / transformers)…');

    const heavyResult = await gal.installWizard(
      ['--install-deps', '--with-heavy'],
      (evt) => {
        if (evt.event === 'line' && evt.line) {
          const line = evt.line.trim();
          if (line) appendLog('[AI] ' + line);
          if (line.includes('Heavy AI components installed OK')) {
            updStep('step-heavy', 'done');
          }
        }
      },
      1200
    );

    updStep('step-heavy', heavyResult?.ok ? 'done' : 'error');
    if (!heavyResult?.ok) appendLog('[警告] 重型组件安装失败（部分功能受限）');

    // Phase 3: restart sidecar
    updStep('step-restart', 'running');
    appendLog('重启 Python 引擎以加载新依赖…');

    try {
      const restartR = await gal.restartSidecar();
      updStep('step-restart', restartR?.ok ? 'done' : 'error');
      if (restartR?.ok) {
        appendLog('Python 引擎已重启');
        notify.success('引擎已重启');
      } else {
        appendLog('[警告] 自动重启失败，请手动重启应用');
      }
    } catch (e) {
      updStep('step-restart', 'error');
      appendLog('自动重启失败: ' + e.message);
    }

    // Phase 4: complete
    await gal.completeSetup();
    appendLog('✅ 安装完成！正在进入 GalaxyOS…');
    notify.success('安装完成', { duration: 3000 });
    setTimeout(() => location.reload(), 2000);

  } catch (e) {
    appendLog('安装过程出错: ' + (e?.message ?? String(e)));
    console.error('[setup]', e);
    feedUpd('[upd id:setup-skip v:ghost]');
    notify.error('安装失败: ' + (e?.message ?? '未知错误'));
    _running = false;
  }
});

registerHandler('onSetupSkip', async () => {
  if (_running) return;
  try {
    await window.galaxy?.completeSetup();
  } catch (e) {
    console.warn('[setup] completeSetup failed on skip:', e);
  }
  location.reload();
});
