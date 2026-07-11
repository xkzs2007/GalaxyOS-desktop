/**
 * GalaxyOS CSP (Content Security Policy) 配置
 *
 * 用于 Electron 主进程 session.webRequest.onHeadersReceived 注入。
 * 策略：
 *   script-src 'self'           — 仅加载自身脚本
 *   style-src 'self' 'unsafe-inline' — 自身样式 + 内联样式（TokUI 需要）
 *   connect-src 'self' ws://localhost:* http://localhost:* — 自身 + 本地 WebSocket/HTTP
 */

const CSP_DIRECTIVES = {
  'script-src': ["'self'"],
  'style-src': ["'self'", "'unsafe-inline'"],
  'connect-src': [
    "'self'",
    'ws://localhost:*',
    'http://localhost:*',
  ],
  'img-src': ["'self'", 'data:', 'blob:'],
  'font-src': ["'self'"],
  'default-src': ["'self'"],
  'frame-src': ["'none'"],
  'object-src': ["'none'"],
  'base-uri': ["'self'"],
  'form-action': ["'self'"],
};

function buildCSPHeader() {
  return Object.entries(CSP_DIRECTIVES)
    .map(([directive, values]) => `${directive} ${values.join(' ')}`)
    .join('; ');
}

const CSP_HEADER_VALUE = buildCSPHeader();

module.exports = {
  CSP_DIRECTIVES,
  CSP_HEADER_VALUE,
  buildCSPHeader,
};