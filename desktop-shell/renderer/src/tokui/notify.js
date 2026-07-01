// renderer/src/tokui/notify.js — TokUI notification/toast system.
//
// TokUI provides [notification] (corner cards) and [toast] (centered
// strips) components.  This module wraps them into a simple imperative
// API: notify.success() / notify.error() / notify.info() / notify.warning().
//
// Each call creates a notification in the top-right corner that
// auto-dismisses after a configurable duration.
//
// Usage:
//   import notify from './tokui/notify.js';
//   notify.success('模型下载完成');
//   notify.error('连接 sidecar 失败', { duration: 8000 });

import { bootTokUI } from './runtime.js';

const DEFAULTS = {
  duration: 4000,
  position: 'top-right',  // 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'
};

/**
 * Show a TokUI notification toast.
 * @param {string} type    - 'success' | 'error' | 'info' | 'warning'
 * @param {string} message - notification text
 * @param {object} opts    - { duration, title }
 */
async function notify(type, message, opts = {}) {
  const duration = opts.duration ?? DEFAULTS.duration;
  const title = opts.title ?? typeLabels[type] ?? '';

  // Render the notification into an off-screen container then clone
  // the DOM element into the notification-container so multiple
  // toasts can stack independently.
  const temp = document.createElement('div');
  temp.style.cssText = 'position:absolute;left:-9999px;top:0;';

  const ui = await bootTokUI();
  if (!ui) return;

  ui.startStream(temp);
  ui.feed(`[notification v:${type} tt:"${title}" duration:${duration} closable]${escapeText(message)}[/notification]`);
  ui.endStream();

  // Move the rendered notification(s) into the dedicated container
  const container = ensureContainer(DEFAULTS.position);
  while (temp.firstChild) {
    container.appendChild(temp.firstChild);
  }

  // Auto-remove after duration (TokUI may handle this internally via duration attr,
  // but defensively schedule cleanup too)
  const el = container.lastChild;
  if (el && duration > 0) {
    setTimeout(() => {
      el.remove();
      // Clean up empty container
      if (container && !container.hasChildNodes()) container.remove();
    }, duration + 600);
  }
}

function escapeText(s) {
  const str = String(s ?? '');
  if (str.includes('[') || str.includes(']') || str.includes('"')) {
    return '"' + str.replace(/"/g, '\\"') + '"';
  }
  return str;
}

function ensureContainer(position) {
  const cls = `tokui-notification-container tokui-notification-container--${position}`;
  let c = document.querySelector(`.${cls}`);
  if (!c) {
    c = document.createElement('div');
    c.className = cls;
    // Match TokUI's built-in notification container styles
    c.style.cssText = 'position:fixed;z-index:9999;display:flex;flex-direction:column;gap:8px;width:360px;max-width:90vw;';
    if (position === 'top-right')    c.style.cssText += 'top:16px;right:16px;';
    if (position === 'top-left')     c.style.cssText += 'top:16px;left:16px;';
    if (position === 'bottom-right') c.style.cssText += 'bottom:16px;right:16px;';
    if (position === 'bottom-left')  c.style.cssText += 'bottom:16px;left:16px;';
    document.body.appendChild(c);
  }
  return c;
}

const typeLabels = {
  success: '✓',
  error:   '✗',
  info:    'ℹ',
  warning: '⚠',
};

const api = {
  success: (msg, opts) => notify('success', msg, opts),
  error:   (msg, opts) => notify('error', msg, opts),
  info:    (msg, opts) => notify('info', msg, opts),
  warning: (msg, opts) => notify('warning', msg, opts),
};

export default api;
