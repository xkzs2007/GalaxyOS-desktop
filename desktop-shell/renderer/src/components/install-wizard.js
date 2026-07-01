// renderer/src/components/install-wizard.js — D 阶段（TokUI 深用）.
//
// 用手写 DOM 的地方替换为 TokUI 内置组件：
//   - [dialog]    → 弹窗容器（替 iw-modal CSS）
//   - [progress]  → 进度条（替 iw-progress-fill div）
//   - [terminal]  → 日志面板（替 iw-log <pre>）
//   - [tag]       → 阶段标签（替 iw-stage span）
//   - [upd]       → 运行时增量更新 DOM
//
// 核心下载逻辑不变（zmq IPC + install_wizard args），UI 层全面 TokUI 化。

import { bootTokUI, getInstance, registerHandler } from '../tokui/runtime.js';
import notify from '../tokui/notify.js';

const PRESETS = {
  'lfm-onnx-q4':      ['--download-lfm-onnx', '--download-lfm-onnx-quant', 'q4'],
  'lfm-onnx-fp16':    ['--download-lfm-onnx', '--download-lfm-onnx-quant', 'fp16'],
  'lfm-safetensors':  ['--download-lfm'],
  'embedding':        ['--download-embedding'],
  'check':            ['--check'],
};

const PRESET_LABELS = {
  'lfm-onnx-q4':     'LFM2.5-1.2B ONNX Q4 (~1.2GB, 推荐)',
  'lfm-onnx-fp16':   'LFM2.5-1.2B ONNX FP16 (~2.4GB)',
  'lfm-safetensors': 'LFM2.5-1.2B safetensors (~2.2GB)',
  'embedding':       'BGE-small-zh ONNX (~96MB)',
  'check':           '仅系统体检 (--check)',
};

let _running = false;
let _t0 = 0;
let _elapsedTimer = null;
let _dialogHost = null;

// ── TokUI DSL builders ─────────────────────────────────────────

function buildDialogDSL(preset) {
  const options = Object.entries(PRESET_LABELS).map(([k, v]) =>
    `[picker-option value:${k} ${k === preset ? 'selected' : ''}]${v}[/picker-option]`
  ).join('\n    ');
  return `[dialog id:iw-dialog open tt:"📥 下载模型" closable clk:onIWDialogClose]\n` +
    `  [progress id:iw-progress v:primary w:0 tt:"准备中…" stripe]\n` +
    `  [row]\n` +
    `    [tag id:iw-stage v:muted sm]idle[/tag]\n` +
    `    [span id:iw-elapsed v:muted sm]0.0s[/span]\n` +
    `  [/row]\n` +
    `  [terminal id:iw-term v:dark]等待下载…[/terminal]\n` +
    `  [row align:right]\n` +
    `    [picker id:iw-preset clk:onIWPresetPick sm]\n    ${options}\n    [/picker]\n` +
    `    [btn id:iw-start tx:"开始下载" v:primary sm clk:onIWStart]\n` +
    `  [/row]\n` +
    `[/dialog]`;
}

// ── DOM update helpers (via TokUI [upd]) ──────────────────────

async function feedUpd(dsl) {
  const ui = getInstance();
  if (!ui || !_dialogHost) return;
  ui.startStream(_dialogHost);
  ui.feed(dsl);
  ui.endStream();
}

function updProgress(pct) {
  const clamped = Math.max(0, Math.min(100, pct));
  feedUpd(`[upd id:iw-progress w:${clamped}]`);
}

function updStage(stage) {
  feedUpd(`[upd id:iw-stage tx:${stage}]`);
}

function updElapsed(s) {
  feedUpd(`[upd id:iw-elapsed tx:"${s.toFixed(1)}s"]`);
}

function updStatus(text) {
  // Update progress bar title to show status
  feedUpd(`[upd id:iw-progress tt:"${text.replace(/"/g, '\\"')}"]`);
}

function appendTerminal(line) {
  // TokUI terminal: feed new content; since [terminal] is raw-content,
  // we need to append by re-rendering. For streaming append, we use
  // the container directly.
  if (!_dialogHost) return;
  const term = _dialogHost.querySelector('.tokui-terminal__content');
  if (term) {
    term.textContent += line + '\n';
    term.scrollTop = term.scrollHeight;
  }
}

function clearTerminal() {
  if (!_dialogHost) return;
  const term = _dialogHost.querySelector('.tokui-terminal__content');
  if (term) term.textContent = '';
}

// ── Core wizard logic ──────────────────────────────────────────

async function showDialog() {
  const host = document.getElementById('iw-dialog-host');
  if (!host) {
    // Create a persistent host for the dialog
    _dialogHost = document.createElement('div');
    _dialogHost.id = 'iw-dialog-host';
    document.body.appendChild(_dialogHost);
  } else {
    _dialogHost = host;
  }

  const ui = await bootTokUI();
  if (!ui) return;

  // Find last-used preset or default
  const preset = localStorage.getItem('galaxyos.iwPreset.v1') || 'lfm-onnx-q4';

  _dialogHost.innerHTML = '';
  ui.startStream(_dialogHost);
  ui.feed(buildDialogDSL(preset));
  ui.endStream();
}

function hideDialog() {
  // Close via [upd] — set dialog open attribute to false
  feedUpd('[upd id:iw-dialog act:close]');
  // Remove dialog DOM after animation
  setTimeout(() => {
    if (_dialogHost) _dialogHost.innerHTML = '';
  }, 300);
}

async function onStartClick() {
  if (_running) return;

  // Re-read preset from rendered DOM
  const gal = window.galaxy;
  if (!gal?.installWizard) {
    updStatus('错误：sidecar 未启动');
    notify.error('sidecar 未启动，无法下载模型');
    return;
  }

  // Get current preset from the picker
  const presetEl = _dialogHost?.querySelector('[data-tokui-picker]');
  const preset = localStorage.getItem('galaxyos.iwPreset.v1') || 'lfm-onnx-q4';
  const args = PRESETS[preset] ?? PRESETS['lfm-onnx-q4'];

  clearTerminal();
  updProgress(0);
  updStage('starting');
  updStatus('准备中…');
  _running = true;
  updBtnState(true);
  startElapsedTimer();

  try {
    const result = await gal.installWizard(args, (evt) => {
      if (evt.event === 'started') {
        updStatus('启动中…');
        updStage('started');
      } else if (evt.event === 'pid') {
        updStatus(`子进程 pid=${evt.pid}`);
        updStage('running');
      } else if (evt.event === 'line') {
        appendTerminal(`[${evt.stream ?? 'stdout'}] ${evt.line ?? ''}`);
        if (evt.line) maybeUpdateProgressFromLine(evt.line);
        const line = evt.line ?? '';
        if (line.includes('downloading') || line.includes('下载')) updStage('downloading');
        else if (line.includes('extracting') || line.includes('解压')) updStage('extracting');
        else if (line.includes('verifying') || line.includes('验证')) updStage('verifying');
        else if (line.includes('linking') || line.includes('链接')) updStage('linking');
      } else if (evt.event === 'done') {
        stopElapsedTimer();
        if (evt.ok) {
          updStatus(`✓ 完成 (exit=${evt.exit_code}, ${evt.duration_s}s)`);
          updStage('done');
          updProgress(100);
          notify.success('模型下载完成', { duration: 6000 });
        } else {
          updStatus(`✗ 失败 (exit=${evt.exit_code})`);
          updStage('error');
          notify.error(`下载失败: exit=${evt.exit_code}`);
        }
      }
    }, 1800);

    stopElapsedTimer();
    if (result?.stderr && !result.ok) {
      appendTerminal(`[stderr] ${result.stderr.slice(-2000)}`);
    }
    if (result && typeof result.exit_code === 'number' && result.exit_code !== 0) {
      updStatus(`✗ 失败 (exit=${result.exit_code})`);
      updStage('error');
    } else if (result && result.ok) {
      updStatus(`✓ 完成 (${result.duration_s ?? 0}s)`);
      updProgress(100);
    }
  } catch (e) {
    stopElapsedTimer();
    updStatus(`✗ 异常: ${e?.message ?? String(e)}`);
    updStage('error');
    appendTerminal(`[error] ${e?.stack ?? String(e)}`);
    notify.error(`下载异常: ${e?.message ?? ''}`);
  } finally {
    _running = false;
    updBtnState(false);
  }
}

function updBtnState(running) {
  feedUpd(`[upd id:iw-start tx:"${running ? '下载中…' : '开始下载'}"]`);
  if (running) feedUpd('[upd id:iw-start v:muted]');
  else feedUpd('[upd id:iw-start v:primary]');
}

function maybeUpdateProgressFromLine(line) {
  const pctMatch = line.match(/(\d{1,3})\s*%/);
  if (pctMatch) {
    updProgress(parseInt(pctMatch[1], 10));
    return true;
  }
  return false;
}

function startElapsedTimer() {
  _t0 = Date.now();
  if (_elapsedTimer) clearInterval(_elapsedTimer);
  _elapsedTimer = setInterval(() => {
    const s = (Date.now() - _t0) / 1000;
    updElapsed(s);
  }, 200);
}

function stopElapsedTimer() {
  if (_elapsedTimer) { clearInterval(_elapsedTimer); _elapsedTimer = null; }
}

// ── Exports ────────────────────────────────────────────────────

export async function openWizard() {
  await showDialog();
}

// ── Init + handler registration ────────────────────────────────

registerHandler('onIWDialogClose', () => {
  hideDialog();
});

registerHandler('onIWStart', () => {
  onStartClick();
});

registerHandler('onIWPresetPick', (data) => {
  const value = typeof data === 'string' ? data : data?.value ?? '';
  if (value && PRESETS[value]) {
    localStorage.setItem('galaxyos.iwPreset.v1', value);
  }
});

export function initInstallWizard() {
  console.log('[install-wizard] TokUI-powered initialised');
}
