// renderer/src/tokui/image-tools.js — 图片/文件处理 UI (P0)
//
// 功能：
//   - 拖拽/点击上传图片 → base64 编码 → 缩略图预览
//   - 操作按钮: 缩放 / 增强 / OCR / 理解 / 解析文档 / 图表分析
//   - 结果在 details-host 中展示
//
// 依赖: preload 里已有的 galaxy.understandImage / galaxy.ocrImage / etc.
//       Python sidecar 的 claw_worker.py 处理实际推理

import { getInstance, registerHandler } from './runtime.js';
import { safeFeed } from '../error-boundary.js';

// ── State ──────────────────────────────────────────────────
let _currentImage = null;  // { name, dataUrl, base64, size, type }

// ── Helpers ────────────────────────────────────────────────

function toBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result;
      const base64 = dataUrl.split(',')[1];
      resolve({ dataUrl, base64, name: file.name, size: file.size, type: file.type });
    };
    reader.onerror = () => reject(new Error('读取文件失败'));
    reader.readAsDataURL(file);
  });
}

async function pickImage() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.onchange = async () => {
      if (input.files[0]) {
        try {
          const img = await toBase64(input.files[0]);
          resolve(img);
        } catch { resolve(null); }
      } else { resolve(null); }
    };
    input.click();
  });
}

// ── Public API ─────────────────────────────────────────────

/**
 * 打开图片处理对话框。支持从文件选择或直接传入 base64。
 */
export async function openImageTools(existingBase64 = null, existingName = '') {
  const ui = getInstance();
  if (!ui || !window.galaxy) return;

  if (existingBase64) {
    _currentImage = { dataUrl: `data:image/png;base64,${existingBase64}`, base64: existingBase64, name: existingName, size: 0, type: 'image/png' };
    await _renderDialog(ui);
    return;
  }

  // 弹出选择 → 渲染对话框
  const img = await pickImage();
  if (!img) return;
  _currentImage = img;
  await _renderDialog(ui);
}

/**
 * 直接用已知图片渲染处理面板（跳过文件选择）
 */
export async function renderImagePanel(base64, name, container) {
  const host = typeof container === 'string' ? document.getElementById(container) : container;
  if (!host) return;
  _currentImage = { dataUrl: `data:image/png;base64,${base64}`, base64, name, size: 0, type: 'image/png' };
  await _renderPanel(host);
}

// ── Internal render ────────────────────────────────────────

async function _renderDialog(ui) {
  const img = _currentImage;
  if (!img) return;

  const sizeStr = img.size > 0 ? `${(img.size / 1024).toFixed(0)} KB` : '';

  ui.startStream(document.getElementById('details-host'));
  ui.feed(`[card tt:"🖼 图片处理 · ${img.name}" v:highlight]`);
  ui.feed(`  [row]`);
  ui.feed(`    [dv w:200]`);
  ui.feed(`      [img src:${img.dataUrl} w:200 h:200 fit:cover br:8][/img]`);
  ui.feed(`    [/dv]`);
  ui.feed(`    [dv flex:1]`);
  ui.feed(`      [p tt:"文件信息" v:bold]${img.name} · ${img.type}${sizeStr ? ' · ' + sizeStr : ''}[/p]`);
  ui.feed(`      [btngroup]`);
  ui.feed(`        [btn tx:"📐 缩放" clk:onImageResize sm v:accent]`);
  ui.feed(`        [btn tx:"✨ 增强" clk:onImageEnhance sm v:accent]`);
  ui.feed(`        [btn tx:"📝 OCR" clk:onImageOcr sm]`);
  ui.feed(`      [/btngroup]`);
  ui.feed(`      [btngroup]`);
  ui.feed(`        [btn tx:"🧠 理解图片" clk:onImageUnderstand sm]`);
  ui.feed(`        [btn tx:"📄 解析文档" clk:onImageParseDocument sm]`);
  ui.feed(`        [btn tx:"📊 图表分析" clk:onImageAnalyzeChart sm]`);
  ui.feed(`      [/btngroup]`);
  ui.feed(`    [/dv]`);
  ui.feed(`  [/row]`);
  ui.feed(`  [dv id:image-result-host][/dv]`);
  ui.feed(`[/card]`);
  ui.endStream();

  document.getElementById('details-panel')?.classList.remove('hidden');
}

// ── Handlers (registered once at module level) ────────────

function _currentBase64() {
  return _currentImage?.base64 || '';
}

async function _renderPanel(host) {
  const ui = getInstance();
  const img = _currentImage;
  if (!ui || !img) return;

  ui.startStream(host);
  ui.feed(`[card tt:"🖼 ${img.name}" v:highlight]`);
  ui.feed(`  [img src:${img.dataUrl} w:200 h:200 fit:cover br:8][/img]`);
  ui.feed(`  [btngroup]`);
  ui.feed(`    [btn tx:"📐 缩放" clk:onImageResize sm v:accent]`);
  ui.feed(`    [btn tx:"✨ 增强" clk:onImageEnhance sm v:accent]`);
  ui.feed(`    [btn tx:"📝 OCR" clk:onImageOcr sm]`);
  ui.feed(`    [btn tx:"🧠 理解" clk:onImageUnderstand sm]`);
  ui.feed(`  [/btngroup]`);
  ui.feed(`  [dv id:image-result-host][/dv]`);
  ui.feed(`[/card]`);
  ui.endStream();

  _registerHandlers(img.base64);
}

// ── Handlers ───────────────────────────────────────────────

registerHandler('onImageResize', async () => {
  const b64 = _currentBase64();
  if (!b64) return;
  if (!window.galaxy?.resize) return _showResult(null, 'resize 方法不可用');
  _showProgress('正在缩放...');
  try {
    const r = await window.galaxy.resize({ data_b64: b64, width: 400, height: 400, keep_ratio: true });
    if (r?.data_b64) _showResult(`data:image/jpeg;base64,${r.data_b64}`, `${r.size?.[0]||'?'}×${r.size?.[1]||'?'}`);
    else _showResult(null, JSON.stringify(r).slice(0, 200));
  } catch (e) { _showResult(null, e.message); }
});

registerHandler('onImageEnhance', async () => {
  const b64 = _currentBase64();
  if (!b64) return;
  _showProgress('正在增强...');
  try {
    const r = await window.galaxy?.enhance?.({ data_b64: b64, brightness: 1.1, contrast: 1.1, sharpness: 1.2 });
    r?.data_b64 ? _showResult(`data:image/jpeg;base64,${r.data_b64}`, '增强完成') : _showResult(null, JSON.stringify(r || {}).slice(0, 200));
  } catch (e) { _showResult(null, e.message); }
});

registerHandler('onImageOcr', async () => {
  const b64 = _currentBase64();
  if (!b64) return;
  _showProgress('OCR 识别中...');
  try {
    const r = await window.galaxy?.ocrImage?.(b64);
    const text = r?.result?.text || r?.text || r?.result || JSON.stringify(r).slice(0, 500);
    _showResult(null, text);
  } catch (e) { _showResult(null, e.message); }
});

registerHandler('onImageUnderstand', async () => {
  const b64 = _currentBase64();
  if (!b64) return;
  _showProgress('AI 理解中...');
  try {
    const r = await window.galaxy?.understandImage?.(b64);
    _showResult(null, r?.result || JSON.stringify(r).slice(0, 500));
  } catch (e) { _showResult(null, e.message); }
});

registerHandler('onImageParseDocument', async () => {
  const b64 = _currentBase64();
  if (!b64) return;
  _showProgress('解析文档中...');
  try {
    const r = await window.galaxy?.parseDocument?.(b64);
    _showResult(null, r?.result || JSON.stringify(r).slice(0, 500));
  } catch (e) { _showResult(null, e.message); }
});

registerHandler('onImageAnalyzeChart', async () => {
  const b64 = _currentBase64();
  if (!b64) return;
  _showProgress('分析图表中...');
  try {
    const r = await window.galaxy?.analyzeChart?.(b64);
    _showResult(null, r?.result || JSON.stringify(r).slice(0, 500));
  } catch (e) { _showResult(null, e.message); }
});

function _showResult(imageUrl, text) {
  requestAnimationFrame(() => {
    const host = document.getElementById('image-result-host');
    if (!host) return;

    if (imageUrl) {
      host.innerHTML = `<img src="${imageUrl}" style="max-width:100%;border-radius:8px;margin-top:8px;" />`;
      if (text) {
        const p = document.createElement('p');
        p.style.cssText = 'color:#6c7086;font-size:12px;margin-top:4px;';
        p.textContent = text;
        host.appendChild(p);
      }
    } else {
      host.innerHTML = '';
      const pre = document.createElement('pre');
      pre.style.cssText = 'white-space:pre-wrap;word-break:break-all;font-size:12px;color:#cdd6f4;margin-top:8px;max-height:300px;overflow-y:auto;';
      pre.textContent = text || '(无结果)';
      host.appendChild(pre);
    }
  });
}

function _showProgress(msg) {
  const host = document.getElementById('image-result-host');
  if (host) { host.innerHTML = ''; host.textContent = '⏳ ' + msg; }
}
