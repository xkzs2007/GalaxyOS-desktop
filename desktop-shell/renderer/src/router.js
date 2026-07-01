// renderer/src/router.js — hash-based page router（~30 行，零依赖）
//
// 用法:
//   import { route, navigate, start } from './router.js';
//   route('dashboard', async () => { ... });
//   navigate('dashboard', { id: '123' });
//   start(); // 在 main.js 启动时调用一次

import { getInstance } from './tokui/runtime.js';
import { safeFeed } from './error-boundary.js';

const _routes = {};

/** 注册路由 handler */
export function route(pattern, handler) {
  _routes[pattern] = handler;
}

/** 导航到指定路由 */
export function navigate(pattern, params = {}) {
  const qs = new URLSearchParams(params).toString();
  window.location.hash = qs ? `#/${pattern}?${qs}` : `#/${pattern}`;
}

/** 启动路由监听 */
export function start(defaultRoute = 'welcome') {
  const onHash = async () => {
    const hash = window.location.hash.slice(2) || defaultRoute; // #/dashboard → dashboard
    const [path] = hash.split('?');
    const handler = _routes[path];
    if (!handler) {
      const ui = getInstance();
      if (ui) safeFeed(ui, `[callout t:warning tt:"404"]路由 "${path}" 未注册[/callout]`);
      return;
    }
    try {
      await handler();
    } catch (e) {
      console.error(`[router] ${path} failed:`, e);
      const ui = getInstance();
      if (ui) safeFeed(ui, `[callout t:danger tt:"错误"]${String(e).slice(0, 200)}[/callout]`);
    }
  };
  window.addEventListener('hashchange', onHash);
  onHash(); // 首次触发
}

/** 批量注册（便捷方法，替换 main.js 的 switch/case）*/
export function register(routes) {
  for (const [pattern, handler] of Object.entries(routes)) {
    route(pattern, handler);
  }
}

/** 查路由 handler（供命令面板等直接调用，不触发 hash 变更）*/
export function lookup(pattern) {
  return _routes[pattern] || null;
}
