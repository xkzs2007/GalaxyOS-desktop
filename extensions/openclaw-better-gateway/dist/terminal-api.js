/**
 * Terminal API — PTY management + SSE/POST bridge
 *
 * Spawns server-side PTY sessions via node-pty and bridges them
 * to the browser through Server-Sent Events (PTY → browser) and
 * POST requests (browser → PTY). Everything runs on the main
 * gateway HTTP port — no WebSocket, no side port, no extra SSH
 * tunnel forwarding required.
 *
 * All external types are defined locally so the module compiles without
 * @types/node-pty being installed.
 */
import { createRequire } from "node:module";
import { randomBytes } from "node:crypto";
// ---------------------------------------------------------------------------
// Cached dynamic import for node-pty
//
// When OpenClaw loads plugins via jiti, bare `import("node-pty")` resolves
// from the *gateway's* node_modules, not the plugin's. We use `createRequire`
// anchored at this file's location to force resolution from the plugin's own
// node_modules, then fall back to a bare dynamic import() for environments
// where it works natively.
// ---------------------------------------------------------------------------
let _pty = null;
let _ptyPromise = null;
// String variable dodges TS module resolution for dynamic import()
const PTY_PKG = "node-pty";
/**
 * Try to load a native/CJS module using multiple resolution strategies.
 * Each strategy is tried in order; all failures are logged so we can
 * actually diagnose what's going wrong under jiti.
 */
async function pluginImport(pkg, logger) {
    const errors = [];
    // Strategy 1: createRequire anchored at this source file
    try {
        const req = createRequire(import.meta.url);
        logger.debug(`Terminal: trying createRequire(${import.meta.url}).resolve("${pkg}")`);
        const resolved = req.resolve(pkg);
        logger.debug(`Terminal: resolved ${pkg} → ${resolved}`);
        const mod = req(pkg);
        return typeof mod === "object" && mod !== null
            ? mod
            : { default: mod };
    }
    catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        errors.push(`createRequire(import.meta.url): ${msg}`);
    }
    // Strategy 2: createRequire anchored at process.cwd()
    try {
        const cwdUrl = `file://${process.cwd()}/package.json`;
        const req = createRequire(cwdUrl);
        logger.debug(`Terminal: trying createRequire(${cwdUrl})("${pkg}")`);
        const mod = req(pkg);
        return typeof mod === "object" && mod !== null
            ? mod
            : { default: mod };
    }
    catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        errors.push(`createRequire(cwd): ${msg}`);
    }
    // Strategy 3: bare dynamic import()
    try {
        logger.debug(`Terminal: trying dynamic import("${pkg}")`);
        const mod = await import(pkg);
        return mod;
    }
    catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        errors.push(`import(): ${msg}`);
    }
    // Strategy 4: try well-known global node_modules paths directly
    const globalPaths = [
        `/usr/lib/node_modules/${pkg}`,
        `/usr/local/lib/node_modules/${pkg}`,
    ];
    for (const gp of globalPaths) {
        try {
            logger.debug(`Terminal: trying direct require("${gp}")`);
            const req = createRequire(import.meta.url);
            const mod = req(gp);
            return typeof mod === "object" && mod !== null
                ? mod
                : { default: mod };
        }
        catch (e) {
            const msg = e instanceof Error ? e.message : String(e);
            errors.push(`global(${gp}): ${msg}`);
        }
    }
    throw new Error(`Cannot load "${pkg}" — tried ${errors.length} strategies:\n  ` +
        errors.join("\n  "));
}
function loadPty(logger) {
    if (_ptyPromise)
        return _ptyPromise;
    _ptyPromise = pluginImport(PTY_PKG, logger).then((mod) => {
        _pty = (mod.default ?? mod);
        logger.info("Terminal: node-pty loaded successfully");
        return true;
    }, (err) => {
        logger.warn(`Terminal: node-pty not available — ${err instanceof Error ? err.message : err}`);
        return false;
    });
    return _ptyPromise;
}
function generateSid() {
    return randomBytes(12).toString("hex");
}
// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
/** Write a single SSE event to a response. */
function sseEvent(res, data, event) {
    if (event)
        res.write(`event: ${event}\n`);
    res.write(`data: ${data}\n\n`);
}
/** Read the full request body as a UTF-8 string. */
function readBody(req) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        req.on("data", (c) => chunks.push(c));
        req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
        req.on("error", reject);
    });
}
// ---------------------------------------------------------------------------
// Public factory
// ---------------------------------------------------------------------------
export function createTerminalManager(logger, workspaceDir) {
    // Start loading node-pty eagerly so it's warm by the time a connection arrives.
    loadPty(logger);
    const sessions = new Map();
    // ---- SSE stream handler ------------------------------------------------
    async function handleStream(_req, res) {
        const available = await loadPty(logger);
        if (!available || !_pty) {
            // Send an error event so the frontend gets a clear message, then close.
            res.writeHead(200, {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                Connection: "keep-alive",
            });
            sseEvent(res, JSON.stringify({
                error: "node-pty is not installed. Run: npm install node-pty",
            }), "error");
            res.end();
            return;
        }
        const shell = process.env.SHELL ||
            (process.platform === "win32" ? "powershell.exe" : "/bin/bash");
        let proc;
        try {
            proc = _pty.spawn(shell, [], {
                name: "xterm-256color",
                cols: 80,
                rows: 24,
                cwd: workspaceDir,
                env: process.env,
            });
        }
        catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            res.writeHead(200, {
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                Connection: "keep-alive",
            });
            sseEvent(res, JSON.stringify({ error: `Failed to spawn terminal: ${msg}` }), "error");
            res.end();
            return;
        }
        const sid = generateSid();
        logger.debug(`Terminal: PTY spawned sid=${sid} pid=${proc.pid} shell=${shell} cwd=${workspaceDir}`);
        // SSE headers
        res.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            Connection: "keep-alive",
            "X-Accel-Buffering": "no", // disable nginx buffering
        });
        // Send session ID as the first event
        sseEvent(res, JSON.stringify({ sid }), "session");
        // Keepalive comment every 15s to prevent proxy/gateway timeouts
        const keepaliveTimer = setInterval(() => {
            try {
                res.write(": keepalive\n\n");
            }
            catch {
                /* response already closed */
            }
        }, 15_000);
        const session = {
            sid,
            pty: proc,
            res,
            keepaliveTimer,
            cleaned: false,
        };
        sessions.set(sid, session);
        function cleanup() {
            if (session.cleaned)
                return;
            session.cleaned = true;
            clearInterval(keepaliveTimer);
            sessions.delete(sid);
            try {
                proc.kill();
            }
            catch {
                /* already exited */
            }
            logger.debug(`Terminal: session ${sid} cleaned up`);
        }
        // PTY → browser (base64-encoded to survive SSE newline framing)
        proc.onData((data) => {
            try {
                if (!res.destroyed) {
                    sseEvent(res, Buffer.from(data, "utf-8").toString("base64"));
                }
            }
            catch {
                /* response closed between check and write */
            }
        });
        proc.onExit(({ exitCode }) => {
            logger.debug(`Terminal: PTY pid=${proc.pid} exited code=${exitCode}`);
            try {
                if (!res.destroyed) {
                    sseEvent(res, JSON.stringify({ code: exitCode }), "exit");
                    res.end();
                }
            }
            catch {
                /* already closed */
            }
            cleanup();
        });
        // When the SSE connection drops, kill the PTY
        res.on("close", () => {
            logger.debug(`Terminal: SSE connection closed for session ${sid}`);
            cleanup();
        });
    }
    // ---- POST input handler ------------------------------------------------
    async function handleInput(req, res) {
        const body = await readBody(req);
        let parsed;
        try {
            parsed = JSON.parse(body);
        }
        catch {
            res.writeHead(400, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Invalid JSON" }));
            return;
        }
        const session = parsed.sid ? sessions.get(parsed.sid) : undefined;
        if (!session) {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Session not found" }));
            return;
        }
        if (typeof parsed.data === "string") {
            session.pty.write(parsed.data);
        }
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
    }
    // ---- POST resize handler -----------------------------------------------
    async function handleResize(req, res) {
        const body = await readBody(req);
        let parsed;
        try {
            parsed = JSON.parse(body);
        }
        catch {
            res.writeHead(400, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Invalid JSON" }));
            return;
        }
        const session = parsed.sid ? sessions.get(parsed.sid) : undefined;
        if (!session) {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Session not found" }));
            return;
        }
        if (typeof parsed.cols === "number" && typeof parsed.rows === "number") {
            session.pty.resize(Math.max(1, Math.floor(parsed.cols)), Math.max(1, Math.floor(parsed.rows)));
        }
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
    }
    // ---- public API ---------------------------------------------------------
    return {
        /**
         * Route terminal sub-requests. Call with the sub-path after
         * `/better-gateway/terminal` (e.g. "/stream", "/input", "/resize").
         * Returns true if handled, false otherwise.
         */
        async handleRequest(req, res, subpath) {
            if (subpath === "/stream" && req.method === "GET") {
                await handleStream(req, res);
                return true;
            }
            if (subpath === "/input" && req.method === "POST") {
                await handleInput(req, res);
                return true;
            }
            if (subpath === "/resize" && req.method === "POST") {
                await handleResize(req, res);
                return true;
            }
            return false;
        },
        /** Returns true if node-pty is installed and loaded. */
        isAvailable() {
            return loadPty(logger);
        },
    };
}
//# sourceMappingURL=terminal-api.js.map