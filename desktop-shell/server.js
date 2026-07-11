/**
 * GalaxyOS SSE Sidecar — Node.js HTTP Server
 *
 * 职责：
 *  1. 提供 SSE 推送服务 (POST /sse/*)
 *  2. 转发请求到 Python Worker (UDS / TCP)
 *  3. Token 认证 + CORS 限制 + 并发/Body 上限
 *
 * 安全：
 *  - 启动时生成随机 token，请求必须携带 Authorization: Bearer <token>
 *  - CORS 仅允许 localhost，禁止 *
 *  - 并发连接上限 50，body 限长 8192
 *  - 未携带 token 返回 401，非 localhost 返回 403
 *
 * 参考：D:\test_tokui_svelte\server.js 的安全模式实现
 */

const { createServer } = require('node:http');
const { randomBytes } = require('node:crypto');
const { join } = require('node:path');
const net = require('node:net');

// ── 配置 ──────────────────────────────────────────────
const HOST = process.env.GALAXYOS_SIDECAR_HOST || '127.0.0.1';
const PORT = Number(process.env.GALAXYOS_SIDECAR_HTTP_PORT) || 5758;
const MAX_STREAMS = Number(process.env.GALAXYOS_MAX_STREAMS) || 50;
const MAX_BODY = Number(process.env.GALAXYOS_MAX_BODY) || 8192;
const UDS_PATH = process.env.GALAXYOS_UDS_PATH || null;
const PYTHON_WORKER_HOST = process.env.GALAXYOS_WORKER_HOST || '127.0.0.1';
const PYTHON_WORKER_PORT = Number(process.env.GALAXYOS_WORKER_PORT) || 5760;

// ── Token 认证 ────────────────────────────────────────
const AUTH_TOKEN = randomBytes(32).toString('hex');
console.log(`[sidecar] Auth token generated (set GALAXYOS_SIDECAR_TOKEN env to access)`);
console.log(`[sidecar] Token: ${AUTH_TOKEN}`);

// ── 并发计数 ──────────────────────────────────────────
let activeStreams = 0;

// ── CORS 白名单（仅 localhost）──────────────────────────
const ALLOWED_ORIGINS = [
  'http://localhost',
  'http://localhost:8080',
  'http://localhost:5758',
  'http://localhost:5759',
  'http://127.0.0.1',
  'http://127.0.0.1:8080',
  'http://127.0.0.1:5758',
  'http://127.0.0.1:5759',
];

function isLocalhostOrigin(origin) {
  if (!origin) return false;
  for (const allowed of ALLOWED_ORIGINS) {
    if (origin === allowed || origin.startsWith(allowed + ':')) return true;
  }
  return /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
}

function corsHeaders(req) {
  const origin = req.headers.origin;
  if (!origin) return {};
  if (!isLocalhostOrigin(origin)) return { 'Vary': 'Origin' };
  return {
    'Access-Control-Allow-Origin': origin,
    'Vary': 'Origin',
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Max-Age': '600',
  };
}

// ── 安全工具 ──────────────────────────────────────────

function checkToken(req) {
  const auth = req.headers['authorization'];
  if (!auth) return false;
  const parts = auth.split(' ');
  if (parts.length !== 2 || parts[0] !== 'Bearer') return false;
  return parts[1] === AUTH_TOKEN;
}

function readBodyLimited(req, limit) {
  return new Promise((resolve, reject) => {
    let size = 0;
    let done = false;
    const chunks = [];
    req.on('data', (c) => {
      if (done) return;
      size += c.length;
      if (size > limit) {
        done = true;
        reject(new Error('payload too large'));
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      if (!done) resolve(Buffer.concat(chunks).toString('utf8'));
    });
    req.on('error', reject);
  });
}

// ── UDS / TCP 转发到 Python Worker ────────────────────

function forwardToWorker(data) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(data, 'utf8');
    const headers = [
      'POST /worker/invoke HTTP/1.1',
      `Host: ${PYTHON_WORKER_HOST}:${PYTHON_WORKER_PORT}`,
      'Content-Type: application/json',
      `Content-Length: ${body.length}`,
      'Connection: close',
      '',
      '',
    ].join('\r\n');

    const payload = Buffer.concat([Buffer.from(headers, 'utf8'), body]);

    let socket;
    if (UDS_PATH) {
      socket = net.createConnection(UDS_PATH);
    } else {
      socket = net.createConnection(PYTHON_WORKER_PORT, PYTHON_WORKER_HOST);
    }

    const chunks = [];
    socket.on('data', (c) => chunks.push(c));
    socket.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      const bodyStart = raw.indexOf('\r\n\r\n');
      resolve(bodyStart >= 0 ? raw.slice(bodyStart + 4) : raw);
    });
    socket.on('error', reject);
    socket.write(payload);
    socket.end();
  });
}

// ── SSE 协议封装 ──────────────────────────────────────

function sseSend(res, event, data) {
  if (event) {
    res.write(`event: ${event}\n`);
  }
  res.write(`data: ${JSON.stringify(data)}\n\n`);
}

function sseDone(res) {
  res.write('data: [DONE]\n\n');
}

// ── 路由处理 ──────────────────────────────────────────

async function handleSSEAsk(req, res) {
  if (!isLocalhostOrigin(req.headers.origin) && req.headers.origin) {
    res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8', ...corsHeaders(req) });
    res.end('Forbidden: origin not allowed');
    return;
  }

  if (!checkToken(req)) {
    res.writeHead(401, { 'Content-Type': 'text/plain; charset=utf-8', ...corsHeaders(req) });
    res.end('Unauthorized: missing or invalid token');
    return;
  }

  if (activeStreams >= MAX_STREAMS) {
    res.writeHead(503, { 'Content-Type': 'text/plain; charset=utf-8', 'Retry-After': '2', ...corsHeaders(req) });
    res.end('Too many concurrent streams');
    return;
  }

  let body;
  try {
    body = await readBodyLimited(req, MAX_BODY);
  } catch {
    res.writeHead(413, { 'Content-Type': 'text/plain; charset=utf-8', ...corsHeaders(req) });
    res.end('Payload Too Large');
    return;
  }

  activeStreams++;
  const headers = {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
    'X-Content-Type-Options': 'nosniff',
    ...corsHeaders(req),
  };
  res.writeHead(200, headers);
  res.flushHeaders();

  const heartbeat = setInterval(() => res.write(': ping\n\n'), 25000);

  try {
    const workerResponse = await forwardToWorker(body);
    const parsed = JSON.parse(workerResponse);

    if (parsed.chunks && Array.isArray(parsed.chunks)) {
      for (const chunk of parsed.chunks) {
        sseSend(res, 'chunk', chunk);
        await new Promise((r) => setTimeout(r, 30));
      }
    } else {
      sseSend(res, 'result', parsed);
    }

    sseDone(res);
  } catch (e) {
    sseSend(res, 'error', { message: e.message || 'Worker communication failed' });
    sseDone(res);
  } finally {
    clearInterval(heartbeat);
    activeStreams--;
    res.end();
  }
}

async function handleSSEProcess(req, res) {
  return handleSSEAsk(req, res);
}

function handlePreflight(req, res) {
  const origin = req.headers.origin;
  if (origin && !isLocalhostOrigin(origin)) {
    res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Forbidden: origin not allowed');
    return;
  }
  res.writeHead(204, corsHeaders(req));
  res.end();
}

function handleHealth(req, res) {
  res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', ...corsHeaders(req) });
  res.end(JSON.stringify({
    status: 'ok',
    active_streams: activeStreams,
    max_streams: MAX_STREAMS,
    uptime: process.uptime(),
  }));
}

// ── 主服务器 ──────────────────────────────────────────

const server = createServer((req, res) => {
  if (req.method === 'OPTIONS') {
    handlePreflight(req, res);
    return;
  }

  const path = req.url.split('?')[0];

  if (path === '/health') {
    handleHealth(req, res);
    return;
  }

  if (path === '/sse/ask' || path === '/sse/process') {
    if ((req.method || '').toUpperCase() !== 'POST') {
      res.writeHead(405, { Allow: 'POST', 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('Method Not Allowed');
      return;
    }
    if (path === '/sse/ask') {
      handleSSEAsk(req, res);
    } else {
      handleSSEProcess(req, res);
    }
    return;
  }

  res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end('Not Found');
});

server.listen(PORT, HOST, () => {
  console.log(`[sidecar] GalaxyOS SSE Sidecar → http://${HOST}:${PORT}`);
  console.log(`[sidecar] 并发上限: ${MAX_STREAMS} | body 上限: ${MAX_BODY}B`);
  console.log(`[sidecar] UDS: ${UDS_PATH || '(disabled, using TCP)'}`);
  console.log(`[sidecar] Python Worker: ${PYTHON_WORKER_HOST}:${PYTHON_WORKER_PORT}`);
});

const shutdown = (sig) => {
  console.log(`\n[sidecar] 收到 ${sig}，关闭 server…`);
  server.close(() => process.exit(0));
};
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));