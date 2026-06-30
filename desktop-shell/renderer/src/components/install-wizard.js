// renderer/src/components/install-wizard.js — E 阶段：wire iw-modal 全部交互.
//
// 背景：D 阶段清理了 hidden sidebar fallback，但保留了 iw-modal 弹窗（真实
// 功能：下载 LFM2.5-1.2B ONNX / BGE-small 等模型）。C 阶段删旧 renderer.js
// 时把 wire 也一起删了，结果：按钮无 click handler、modal 打不开。
//
// 职责：
//   1. 提供 openWizard() 给 sidebar [btn] 调 — 显示 iw-modal
//   2. closeWizard() 调 — 隐藏 iw-modal
//   3. iw-start 按钮 → window.galaxy.installWizard(args, onProgress, timeout)
//   4. onProgress 回调：把 line / elapsed / stage 写入对应 DOM
//   5. 完成时：恢复按钮文字 + 把 ok / exit_code 写到 iw-status-line
//
// 不依赖任何 framework / library；纯 DOM API + window.galaxy.* IPC。
//
// 注意：直接读 window.galaxy 而不是 import ipc/client.js。原因是
// ipc/client.js 顶层 const galaxy = window.galaxy ?? makeStandaloneGalaxy()
// 只在模块加载时取一次值；如果侧车 / preload 注入 window.galaxy 比 renderer
// ESM 加载晚，这里就会拿到 null。这里在 click-time 实时取 window.galaxy，
// preload 总是在 renderer 启动前完成 contextBridge.exposeInMainWorld，所以
// window.galaxy 永远是真实 API。

const PRESETS = {
  'lfm-onnx-q4':      ['--download-lfm-onnx', '--download-lfm-onnx-quant', 'q4'],
  'lfm-onnx-fp16':    ['--download-lfm-onnx', '--download-lfm-onnx-quant', 'fp16'],
  'lfm-safetensors':  ['--download-lfm'],
  'embedding':        ['--download-embedding'],
  'check':            ['--check'],
};

let _running = false;
let _t0 = 0;
let _elapsedTimer = null;

function $(id) { return document.getElementById(id); }

function showModal() {
  const m = $('iw-modal');
  if (m) m.classList.remove('hidden');
}
function hideModal() {
  const m = $('iw-modal');
  if (m) m.classList.add('hidden');
}

function setStatus(text) {
  const el = $('iw-status-line');
  if (el) el.textContent = text;
}

function appendLog(line) {
  const pre = $('iw-log');
  if (!pre) return;
  pre.textContent += line + '\n';
  // Auto-scroll to bottom
  pre.scrollTop = pre.scrollHeight;
}

function clearLog() {
  const pre = $('iw-log');
  if (pre) pre.textContent = '';
}

function setProgress(pct) {
  const fill = $('iw-progress-fill');
  if (!fill) return;
  const clamped = Math.max(0, Math.min(100, pct));
  fill.style.width = `${clamped}%`;
}

function setStage(stage) {
  const el = $('iw-stage');
  if (el) el.textContent = stage;
}

function startElapsedTimer() {
  _t0 = Date.now();
  if (_elapsedTimer) clearInterval(_elapsedTimer);
  const el = $('iw-elapsed');
  _elapsedTimer = setInterval(() => {
    if (!el) return;
    const s = (Date.now() - _t0) / 1000;
    el.textContent = `${s.toFixed(1)}s`;
  }, 200);
}

function stopElapsedTimer() {
  if (_elapsedTimer) {
    clearInterval(_elapsedTimer);
    _elapsedTimer = null;
  }
}

/**
 * Parse a "12% / 5.2MB / 1.2GB" style line and update the progress bar.
 * Returns true if the line matched a known progress pattern (and the
 * bar was updated); false otherwise.
 */
function maybeUpdateProgressFromLine(line) {
  // Common install_wizard output patterns:
  //   "[1.2GB / 2.4GB] 50%"  /  "downloading 50%"  /  "50% complete"
  const pctMatch = line.match(/(\d{1,3})\s*%/);
  if (pctMatch) {
    setProgress(parseInt(pctMatch[1], 10));
    return true;
  }
  return false;
}

function setRunning(running) {
  _running = running;
  const startBtn = $('iw-start');
  const presetSel = $('iw-preset');
  const closeBtn = $('iw-close');
  if (startBtn) {
    startBtn.disabled = running;
    startBtn.textContent = running ? '下载中…' : '开始下载';
  }
  if (presetSel) presetSel.disabled = running;
  // Allow closing the modal mid-download but warn — user can still
  // see log lines streaming in (modal just hides).
  if (closeBtn) closeBtn.disabled = false;
}

async function onStartClick() {
  if (_running) return;
  const presetSel = $('iw-preset');
  // Read galaxy from window at click-time (it's injected by preload.ts
  // after main.ts starts the sidecar; the renderer's ipc/client.js
  // re-binds to it on every call).
  const gal = window.galaxy;
  if (!gal?.installWizard) {
    setStatus('错误：galaxy.installWizard 不可用（sidecar 未启动？）');
    return;
  }
  const preset = presetSel?.value ?? 'lfm-onnx-q4';
  const args = PRESETS[preset] ?? PRESETS['lfm-onnx-q4'];

  clearLog();
  setProgress(0);
  setStage('starting');
  setStatus('准备中…');
  setRunning(true);
  startElapsedTimer();

  try {
    const result = await gal.installWizard(args, (evt) => {
      if (evt.event === 'started') {
        setStatus('启动中…');
        setStage('started');
      } else if (evt.event === 'pid') {
        setStatus(`子进程 pid=${evt.pid}`);
        setStage('running');
      } else if (evt.event === 'line') {
        appendLog(`[${evt.stream ?? 'stdout'}] ${evt.line ?? ''}`);
        if (evt.line) maybeUpdateProgressFromLine(evt.line);
        // Look for stage markers
        const line = evt.line ?? '';
        if (line.includes('downloading') || line.includes('下载')) setStage('downloading');
        else if (line.includes('extracting') || line.includes('解压')) setStage('extracting');
        else if (line.includes('verifying') || line.includes('验证')) setStage('verifying');
        else if (line.includes('linking') || line.includes('链接')) setStage('linking');
      } else if (evt.event === 'done') {
        stopElapsedTimer();
        if (evt.ok) {
          setStatus(`✓ 完成 (exit=${evt.exit_code}, ${evt.duration_s}s)`);
          setStage('done');
          setProgress(100);
        } else {
          setStatus(`✗ 失败 (exit=${evt.exit_code}, ${evt.duration_s}s)${evt.error ? ' — ' + evt.error : ''}`);
          setStage('error');
        }
      }
    }, 1800);
    // Final result (returned via zmq REP, after PUB 'done' has already
    // fired).  Result may carry truncated stdout/stderr; append a
    // summary line.
    stopElapsedTimer();
    if (result?.stderr && !result.ok) {
      appendLog(`[stderr] ${result.stderr.slice(-2000)}`);
    }
    if (result && typeof result.exit_code === 'number' && result.exit_code !== 0) {
      setStatus(`✗ 失败 (exit=${result.exit_code}, ${result.duration_s ?? 0}s)`);
      setStage('error');
    } else if (result && result.ok) {
      setStatus(`✓ 完成 (${result.duration_s ?? 0}s)`);
      setProgress(100);
    }
  } catch (e) {
    stopElapsedTimer();
    setStatus(`✗ 异常: ${e?.message ?? String(e)}`);
    setStage('error');
    appendLog(`[error] ${e?.stack ?? String(e)}`);
  } finally {
    setRunning(false);
  }
}

function onCloseClick() {
  // Mid-download close is allowed; the subprocess keeps running on the
  // sidecar. User can reopen the modal to see status.
  hideModal();
}

export function openWizard() {
  showModal();
  setStatus('点击「开始下载」以运行 install_wizard');
  setStage('idle');
  setProgress(0);
  if (!_running) clearLog();
}

function init() {
  // close button
  $('iw-close')?.addEventListener('click', onCloseClick);
  // click outside modal-content closes
  const modal = $('iw-modal');
  modal?.addEventListener('click', (e) => {
    if (e.target === modal) onCloseClick();
  });
  // start button
  $('iw-start')?.addEventListener('click', onStartClick);
  // Esc closes when not running
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !_running) {
      const m = $('iw-modal');
      if (m && !m.classList.contains('hidden')) onCloseClick();
    }
  });
}

export function initInstallWizard() {
  init();
  console.log('[install-wizard] initialised');
}
