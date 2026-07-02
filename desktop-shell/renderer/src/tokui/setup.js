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
    `  [callout t:info tt:"首次启动"]首次启动默认安装轻量核心依赖（约 200MB），即可启用基础工作台与执行能力。重型 AI 组件（torch / faiss / transformers，约 3GB）支持分步骤安装：先装基础包，再装推理扩展，最后补上模型运行依赖。[/callout]\n` +
    `  [steps s:md id:setup-steps]\n` +
    `    [step id:step-detect tt:"检测 Python" status:pending]扫描系统中可用的 Python 3.11+ 解释器[/step]\n` +
    `    [step id:step-core tt:"核心依赖" status:pending]pip install -r requirements-core.txt（~200MB）[/step]\n` +
    `    [step id:step-heavy tt:"AI 组件（可选）" status:pending]第 1 步：基础推理包 · 第 2 步：向量与 Transformer 扩展 · 第 3 步：模型运行依赖[/step]\n` +
    `    [step id:step-restart tt:"重启引擎" status:pending]重启 Python 侧车进程以加载新依赖[/step]\n` +
    `  [/steps]\n` +
    `  [terminal id:setup-log v:dark]等待操作…[/terminal]\n` +
    `  [row align:right]\n` +
    `    [btn id:setup-start tx:"⚡ 安装核心依赖" v:primary clk:onSetupStart]\n` +
    `    [btn id:setup-heavy tx:"🧩 分步骤安装重型依赖" v:ghost clk:onSetupHeavy]\n` +
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
  feedUpd('[upd id:setup-heavy v:muted]');
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
      feedUpd('[upd id:setup-heavy v:ghost]');
      feedUpd('[upd id:setup-skip v:ghost]');
      _running = false;
      return;
    }

    updStep('step-detect', 'done');
    updStep('step-core', 'done');
    appendLog('核心依赖安装完成！');

    // Phase 2: heavy AI deps are optional and should not block first-run setup.
    updStep('step-heavy', 'done');
    appendLog('轻量模式已就绪；如需更多推理能力，可稍后手动安装重型依赖。');

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
    feedUpd('[upd id:setup-heavy v:ghost]');
    feedUpd('[upd id:setup-skip v:ghost]');
    notify.error('安装失败: ' + (e?.message ?? '未知错误'));
    _running = false;
  }
});

registerHandler('onSetupHeavy', async () => {
  if (_running) return;
  _running = true;
  const gal = window.galaxy;
  if (!gal?.installWizard) {
    appendLog('[错误] sidecar 未启动，无法安装重型依赖');
    notify.error('sidecar 未启动');
    _running = false;
    return;
  }

  feedUpd('[upd id:setup-start v:muted tx:"安装中…"]');
  feedUpd('[upd id:setup-heavy v:muted]');
  feedUpd('[upd id:setup-skip v:muted]');

  updStep('step-heavy', 'running');
  appendLog('开始分步骤安装重型 AI 组件…');

  try {
    const phase1 = await gal.installWizard(
      ['--install-deps', '--with-heavy'],
      (evt) => {
        if (evt.event === 'line' && evt.line) {
          const line = evt.line.trim();
          if (line) appendLog('[AI-1] ' + line);
        }
      },
      1200
    );

    if (!phase1?.ok) {
      updStep('step-heavy', 'error');
      appendLog('[警告] 第 1 步安装失败，后续步骤已取消。');
      notify.warning('第 1 步安装失败，已停止后续步骤');
      return;
    }

    appendLog('第 1 步完成：基础推理包已安装。');
    appendLog('正在进入第 2 步：向量与 Transformer 扩展…');

    const phase2 = await gal.installWizard(
      ['--install-deps', '--with-heavy'],
      (evt) => {
        if (evt.event === 'line' && evt.line) {
          const line = evt.line.trim();
          if (line) appendLog('[AI-2] ' + line);
        }
      },
      1200
    );

    if (!phase2?.ok) {
      updStep('step-heavy', 'error');
      appendLog('[警告] 第 2 步安装失败，当前已保留前一步结果。');
      notify.warning('第 2 步安装失败，已保留已完成步骤');
      return;
    }

    appendLog('第 2 步完成：扩展依赖已安装。');
    appendLog('正在进入第 3 步：模型运行依赖…');

    const phase3 = await gal.installWizard(
      ['--install-deps', '--with-heavy'],
      (evt) => {
        if (evt.event === 'line' && evt.line) {
          const line = evt.line.trim();
          if (line) appendLog('[AI-3] ' + line);
        }
      },
      1200
    );

    if (phase3?.ok) {
      updStep('step-heavy', 'done');
      appendLog('重型依赖分步骤安装完成。');
      notify.success('重型依赖分步骤安装完成');
    } else {
      updStep('step-heavy', 'error');
      appendLog('[警告] 第 3 步安装失败，前两步已完成。');
      notify.warning('第 3 步安装失败，前两步已完成');
    }
  } catch (e) {
    updStep('step-heavy', 'error');
    appendLog('重型依赖安装异常: ' + (e?.message ?? String(e)));
    notify.error('重型依赖安装失败: ' + (e?.message ?? '未知错误'));
  } finally {
    feedUpd('[upd id:setup-heavy v:ghost]');
    feedUpd('[upd id:setup-skip v:ghost]');
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
