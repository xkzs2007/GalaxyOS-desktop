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
import type { IncomingMessage, ServerResponse } from "node:http";
interface TerminalLogger {
    info(msg: string): void;
    warn(msg: string): void;
    error(msg: string): void;
    debug(msg: string): void;
}
export declare function createTerminalManager(logger: TerminalLogger, workspaceDir: string): {
    /**
     * Route terminal sub-requests. Call with the sub-path after
     * `/better-gateway/terminal` (e.g. "/stream", "/input", "/resize").
     * Returns true if handled, false otherwise.
     */
    handleRequest(req: IncomingMessage, res: ServerResponse, subpath: string): Promise<boolean>;
    /** Returns true if node-pty is installed and loaded. */
    isAvailable(): Promise<boolean>;
};
export {};
//# sourceMappingURL=terminal-api.d.ts.map