import { request as httpRequest } from "node:http";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { createFileApiHandler, DEFAULT_MAX_FILE_SIZE } from "./file-api.js";
import { generateIdePage } from "./ide-page.js";
import { generateTerminalPage } from "./terminal-page.js";
import { createTerminalManager } from "./terminal-api.js";
function loadGatewayToken() {
    try {
        const configPath = join(process.env.HOME || "/root", ".openclaw", "openclaw.json");
        const config = JSON.parse(readFileSync(configPath, "utf-8"));
        return config?.gateway?.auth?.token ?? null;
    }
    catch {
        return null;
    }
}
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const DEFAULT_CONFIG = {
    reconnectIntervalMs: 3000,
    maxReconnectAttempts: 10,
    maxFileSize: DEFAULT_MAX_FILE_SIZE,
};
function loadInjectScript() {
    const scriptPath = join(__dirname, "inject.js");
    return readFileSync(scriptPath, "utf-8");
}
function generateConfigScript(config) {
    return `window.__BETTER_GATEWAY_CONFIG__ = ${JSON.stringify({
        reconnectIntervalMs: config.reconnectIntervalMs,
        maxReconnectAttempts: config.maxReconnectAttempts,
    })};`;
}
function generateLandingPage(config, gatewayHost) {
    const script = loadInjectScript();
    const bookmarklet = `javascript:(function(){${encodeURIComponent(script.replace(/\n/g, " "))}})()`;
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Better Gateway</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      max-width: 800px;
      margin: 40px auto;
      padding: 20px;
      background: #1a1a2e;
      color: #eee;
    }
    h1 { color: #00d4ff; }
    h2 { color: #888; margin-top: 2em; }
    code {
      background: #2d2d44;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 0.9em;
    }
    pre {
      background: #2d2d44;
      padding: 16px;
      border-radius: 8px;
      overflow-x: auto;
    }
    .bookmarklet {
      display: inline-block;
      background: #00d4ff;
      color: #1a1a2e;
      padding: 12px 24px;
      border-radius: 8px;
      text-decoration: none;
      font-weight: bold;
      margin: 10px 0;
    }
    .bookmarklet:hover { background: #00b8e6; }
    .status {
      display: inline-block;
      padding: 4px 12px;
      border-radius: 4px;
      font-size: 0.85em;
    }
    .status.ok { background: #2d5a27; color: #7fff7f; }
    .feature { margin: 8px 0; padding-left: 20px; }
    .feature::before { content: "✓ "; color: #00d4ff; }
    .new { color: #ff6b6b; font-size: 0.8em; margin-left: 8px; }
  </style>
</head>
<body>
  <h1>🔌 Better Gateway</h1>
  <p>Auto-reconnect enhancement for OpenClaw Gateway UI</p>

  <h2>Features</h2>
  <div class="feature">Automatic WebSocket reconnection on disconnect</div>
  <div class="feature">Visual connection status indicator</div>
  <div class="feature">Network online/offline detection</div>
  <div class="feature">Configurable retry attempts (${config.maxReconnectAttempts} max)</div>
  <div class="feature">Reconnect interval: ${config.reconnectIntervalMs}ms</div>
  <div class="feature">File API for workspace access <span class="new">NEW</span></div>
  <div class="feature">Monaco-powered IDE <span class="new">NEW</span></div>
  <div class="feature">Embedded terminal (xterm.js + PTY) <span class="new">NEW</span></div>

  <h2>Option 1: Bookmarklet</h2>
  <p>Drag this to your bookmarks bar, then click it when on the Gateway UI:</p>
  <p><a class="bookmarklet" href="${bookmarklet}">⚡ Better Gateway</a></p>

  <h2>Option 2: Console Injection</h2>
  <p>Open DevTools (F12) on the Gateway UI and paste:</p>
  <pre>fetch('/better-gateway/inject.js').then(r=>r.text()).then(eval)</pre>

  <h2>Option 3: Userscript (Tampermonkey)</h2>
  <p>Create a new userscript with:</p>
  <pre>// ==UserScript==
// @name         Better Gateway
// @match        ${gatewayHost}/*
// @grant        none
// ==/UserScript==

fetch('/better-gateway/inject.js').then(r=>r.text()).then(eval);</pre>

  <h2>IDE <span class="new">NEW</span></h2>
  <p>Full-featured code editor with Monaco:</p>
  <p><a class="bookmarklet" href="/better-gateway/ide">🚀 Open IDE</a></p>
  <ul style="margin: 16px 0; padding-left: 24px; color: #aaa;">
    <li>File explorer with tree navigation</li>
    <li>Syntax highlighting for 30+ languages</li>
    <li>Multi-tab editing with Ctrl+S save</li>
    <li>Keyboard shortcuts (Ctrl+B sidebar, Ctrl+W close)</li>
  </ul>

  <h2>Terminal <span class="new">NEW</span></h2>
  <p>Full interactive terminal in the browser:</p>
  <p><a class="bookmarklet" href="/better-gateway/terminal">🖥 Open Terminal</a></p>
  <ul style="margin: 16px 0; padding-left: 24px; color: #aaa;">
    <li>Real PTY backend via node-pty</li>
    <li>xterm.js with 256-color support</li>
    <li>Resize, scroll, links, full interactivity</li>
    <li>Keyboard shortcut: Ctrl+\` to toggle</li>
  </ul>

  <h2>File API <span class="new">NEW</span></h2>
  <p>Access workspace files programmatically:</p>
  <pre>// List files
GET /better-gateway/api/files?path=/

// Read file
GET /better-gateway/api/files/read?path=/AGENTS.md

// Write file
POST /better-gateway/api/files/write
{"path": "/test.md", "content": "Hello!"}

// Delete file
DELETE /better-gateway/api/files?path=/test.md</pre>

  <h2>Script URL</h2>
  <p><code>/better-gateway/inject.js</code></p>

  <hr style="margin: 40px 0; border-color: #333;">
  <p style="color: #666; font-size: 0.85em;">
    <a href="https://github.com/ThisIsJeron/openclaw-better-gateway" style="color: #00d4ff;">GitHub</a> ·
    Config: reconnect=${config.reconnectIntervalMs}ms, maxAttempts=${config.maxReconnectAttempts}
  </p>
</body>
</html>`;
}
function generateUserscript(config, gatewayUrl) {
    const script = loadInjectScript();
    return `// ==UserScript==
// @name         Better Gateway - Auto Reconnect
// @namespace    https://github.com/ThisIsJeron/openclaw-better-gateway
// @version      1.0.0
// @description  Adds automatic WebSocket reconnection to OpenClaw Gateway UI
// @match        ${gatewayUrl}/*
// @grant        none
// ==/UserScript==

window.__BETTER_GATEWAY_CONFIG__ = ${JSON.stringify({
        reconnectIntervalMs: config.reconnectIntervalMs,
        maxReconnectAttempts: config.maxReconnectAttempts,
    })};

${script}`;
}
export default {
    // ID must match openclaw.plugin.json
    id: "openclaw-better-gateway",
    name: "Better Gateway",
    configSchema: {
        parse(raw) {
            const config = raw || {};
            return {
                reconnectIntervalMs: config.reconnectIntervalMs ?? DEFAULT_CONFIG.reconnectIntervalMs,
                maxReconnectAttempts: config.maxReconnectAttempts ?? DEFAULT_CONFIG.maxReconnectAttempts,
                maxFileSize: config.maxFileSize ?? DEFAULT_CONFIG.maxFileSize,
            };
        },
        uiHints: {
            reconnectIntervalMs: {
                label: "Reconnect Interval (ms)",
                placeholder: "3000",
            },
            maxReconnectAttempts: {
                label: "Max Reconnect Attempts",
                placeholder: "10",
            },
            maxFileSize: {
                label: "Max File Size (bytes)",
                placeholder: "10485760",
                advanced: true,
            },
        },
    },
    register(api) {
        const config = {
            ...DEFAULT_CONFIG,
            ...(api.pluginConfig || {}),
        };
        // Resolve workspace directory.
        // api.resolvePath("") may return empty; fall back to <cwd>/workspace if it exists.
        let workspaceDir = api.resolvePath("");
        if (!workspaceDir) {
            const cwdWorkspace = resolve(process.cwd(), "workspace");
            if (existsSync(cwdWorkspace)) {
                workspaceDir = cwdWorkspace;
            }
            else {
                workspaceDir = process.cwd();
            }
        }
        api.logger.info(`Better Gateway loaded (reconnect: ${config.reconnectIntervalMs}ms, max: ${config.maxReconnectAttempts}, workspace: ${workspaceDir})`);
        // Create file API handler
        const fileApiHandler = createFileApiHandler({
            workspaceDir,
            maxFileSize: config.maxFileSize,
        });
        // Create terminal manager (PTY + SSE/POST bridge)
        const terminalManager = createTerminalManager(api.logger, workspaceDir);
        // Load gateway token once for auth validation
        const gatewayToken = loadGatewayToken();
        // Register the main HTTP handler for /better-gateway/* routes
        api.registerHttpRoute({
            path: "/better-gateway",
            match: "prefix",
            auth: "plugin",
            handler: async (req, res) => {
                const url = new URL(req.url || "/", `http://${req.headers.host}`);
                const pathname = url.pathname;
                // Auth check: accept token from Authorization header, ?token= query param, or session cookie
                // Note: Control UI generates plugin auth URLs as /better-gateway/token?=TOKEN (empty key)
                // so we check both ?token=VALUE and ?=VALUE formats
                if (gatewayToken) {
                    const tokenFromQuery = url.searchParams.get("token") ?? url.searchParams.get("");
                    const authHeader = req.headers["authorization"] || "";
                    const bearerToken = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : authHeader;
                    // Parse bg_auth session cookie
                    const cookieToken = (req.headers["cookie"] || "")
                        .split(";")
                        .map((c) => c.trim())
                        .find((c) => c.startsWith("bg_auth="))
                        ?.slice("bg_auth=".length) ?? null;
                    const providedToken = tokenFromQuery || bearerToken || cookieToken;
                    if (providedToken !== gatewayToken) {
                        res.writeHead(401, { "Content-Type": "application/json" });
                        res.end(JSON.stringify({ error: { message: "Unauthorized", type: "unauthorized" } }));
                        return true;
                    }
                    // Token came via query param — set cookie and redirect to clean URL
                    if (tokenFromQuery === gatewayToken) {
                        url.searchParams.delete("token");
                        const cleanUrl = url.pathname + (url.searchParams.toString() ? `?${url.searchParams.toString()}` : "");
                        res.writeHead(302, {
                            "Set-Cookie": `bg_auth=${gatewayToken}; Path=/better-gateway; HttpOnly; SameSite=Strict; Max-Age=31536000`,
                            "Location": cleanUrl,
                        });
                        res.end();
                        return true;
                    }
                }
                const hostHeader = req.headers.host || "localhost:18789";
                const gatewayHost = `http://${hostHeader}`;
                // Handle file API routes FIRST (before proxy catches them)
                if (pathname.startsWith("/better-gateway/api/files")) {
                    const handled = await fileApiHandler(req, res, pathname);
                    if (handled)
                        return true;
                }
                // Serve the IDE page
                if (pathname === "/better-gateway/ide") {
                    const html = generateIdePage({ theme: "vs-dark" });
                    res.writeHead(200, {
                        "Content-Type": "text/html",
                        "Content-Length": Buffer.byteLength(html),
                        "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
                        Pragma: "no-cache",
                        Expires: "0",
                    });
                    res.end(html);
                    api.logger.debug("Served IDE page");
                    return true;
                }
                // Serve the terminal page (exact match only)
                if (pathname === "/better-gateway/terminal") {
                    const html = generateTerminalPage();
                    res.writeHead(200, {
                        "Content-Type": "text/html",
                        "Content-Length": Buffer.byteLength(html),
                        "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
                        Pragma: "no-cache",
                        Expires: "0",
                    });
                    res.end(html);
                    api.logger.debug("Served terminal page");
                    return true;
                }
                // Terminal API sub-routes: /stream, /input, /resize
                if (pathname.startsWith("/better-gateway/terminal/")) {
                    const subpath = pathname.slice("/better-gateway/terminal".length);
                    const handled = await terminalManager.handleRequest(req, res, subpath);
                    if (handled)
                        return true;
                }
                // Serve the inject script
                if (pathname === "/better-gateway/inject.js") {
                    const script = loadInjectScript();
                    const configuredScript = `${generateConfigScript(config)}\n${script}`;
                    res.writeHead(200, {
                        "Content-Type": "application/javascript",
                        "Content-Length": Buffer.byteLength(configuredScript),
                        "Cache-Control": "no-cache",
                    });
                    res.end(configuredScript);
                    api.logger.debug("Served inject.js");
                    return true;
                }
                // Serve userscript download
                if (pathname === "/better-gateway/userscript.user.js") {
                    const userscript = generateUserscript(config, gatewayHost);
                    res.writeHead(200, {
                        "Content-Type": "application/javascript",
                        "Content-Length": Buffer.byteLength(userscript),
                        "Content-Disposition": "attachment; filename=better-gateway.user.js",
                    });
                    res.end(userscript);
                    api.logger.debug("Served userscript");
                    return true;
                }
                // Serve landing/help page at /better-gateway/help
                if (pathname === "/better-gateway/help") {
                    const html = generateLandingPage(config, gatewayHost);
                    res.writeHead(200, {
                        "Content-Type": "text/html",
                        "Content-Length": Buffer.byteLength(html),
                    });
                    res.end(html);
                    api.logger.debug("Served help page");
                    return true;
                }
                // Enhanced gateway UI - proxy ALL /better-gateway/* paths to internal gateway
                // Strip /better-gateway prefix and proxy the rest
                const internalPort = 18789;
                let targetPath = pathname.replace(/^\/better-gateway/, "") || "/";
                // Extract ?token= query param and inject as Authorization header (strip from forwarded URL)
                const proxyHeaders = {
                    ...req.headers,
                    "Host": "127.0.0.1:18789",
                };
                const tokenFromQuery = url.searchParams.get("token") ?? url.searchParams.get("");
                if (tokenFromQuery && !proxyHeaders["authorization"]) {
                    proxyHeaders["authorization"] = `Bearer ${tokenFromQuery}`;
                }
                // Strip token from forwarded query string
                const forwardParams = new URLSearchParams(url.searchParams);
                forwardParams.delete("token");
                const forwardSearch = forwardParams.toString();
                targetPath += forwardSearch ? `?${forwardSearch}` : "";
                return new Promise((resolve) => {
                    const proxyReq = httpRequest({
                        hostname: "127.0.0.1",
                        port: internalPort,
                        path: targetPath,
                        method: req.method || "GET",
                        family: 4,
                        headers: proxyHeaders,
                    }, (proxyRes) => {
                        const contentType = proxyRes.headers["content-type"] || "";
                        const chunks = [];
                        proxyRes.on("data", (chunk) => chunks.push(chunk));
                        proxyRes.on("end", () => {
                            let body = Buffer.concat(chunks).toString("utf-8");
                            if (contentType.includes("text/html")) {
                                const injectTag = `<script>${generateConfigScript(config)}\n${loadInjectScript()}</script>`;
                                const baseTag = `<base href="/">`;
                                if (body.includes("<head>")) {
                                    body = body.replace("<head>", `<head>${baseTag}`);
                                }
                                if (body.includes("</head>")) {
                                    body = body.replace("</head>", `${injectTag}</head>`);
                                }
                                else if (body.includes("</body>")) {
                                    body = body.replace("</body>", `${injectTag}</body>`);
                                }
                                else {
                                    body = body + injectTag;
                                }
                            }
                            const headers = {
                                "Content-Type": contentType,
                                "Content-Length": Buffer.byteLength(body),
                                "Cache-Control": "no-store, no-cache, must-revalidate, proxy-revalidate",
                                "Pragma": "no-cache",
                                "Expires": "0",
                            };
                            res.writeHead(proxyRes.statusCode || 200, headers);
                            res.end(body);
                            api.logger.debug("Served enhanced gateway UI");
                            resolve(true);
                        });
                    });
                    proxyReq.on("error", (err) => {
                        api.logger.error(`Proxy error: ${err.message}`);
                        res.writeHead(502, { "Content-Type": "text/plain" });
                        res.end("Failed to fetch gateway UI");
                        resolve(true);
                    });
                    proxyReq.end();
                });
            }
        });
    },
};
//# sourceMappingURL=index.js.map