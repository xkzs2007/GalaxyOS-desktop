// renderer/src/error-boundary.js — 全局错误处理（~20 行，零依赖）
//
// TokUI 的 ui.feed(dsl) 如果 DSL 格式错误会导致渲染断裂。
// safeFeed 包装后捕获异常，在当前流种显示红色 callout 而不炸全局。
//
// 用法:
//   import { safeFeed, installGlobalHandler } from './error-boundary.js';
//   safeFeed(ui, '[callout t:info tt:安全]这个 feed 不会炸[/callout]');

import { getInstance } from './tokui/runtime.js';

/** 安全的 ui.feed，异常捕获后显示红色 callout */
export function safeFeed(ui, dsl) {
  try {
    ui.feed(dsl);
  } catch (e) {
    const msg = String(e).slice(0, 200).replace(/\[/g, '(').replace(/\]/g, ')');
    try {
      ui.feed(`[callout t:danger tt:"渲染错误"]${msg}[/callout]`);
    } catch {
      console.error('safeFeed failed:', e);
    }
  }
}

/** 安装全局 unhandled rejection 处理器 */
export function installGlobalHandler() {
  window.onerror = (msg, source, lineno) => {
    console.error(`[app] ${source}:${lineno}:`, msg);
    const ui = getInstance();
    if (ui) safeFeed(ui, `[callout t:danger tt:"全局错误"]${String(msg).slice(0, 200)}[/callout]`);
    return false; // 不阻止浏览器错误
  };

  window.onunhandledrejection = (event) => {
    console.error('[app] unhandled rejection:', event.reason);
    const ui = getInstance();
    if (ui) safeFeed(ui, `[callout t:danger tt:"异步错误"]${String(event.reason).slice(0, 200)}[/callout]`);
  };
}
