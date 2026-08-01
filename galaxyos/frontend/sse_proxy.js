/**
 * GalaxyOS Gateway SSE Proxy — JiuwenSwarm Gateway 端 SSE 转发
 *
 * 将来自 SSE Sidecar（端口 5758）的 tokui_chunk/tokui_error 事件
 * 透传到前端 EventSource 连接。
 *
 * 端点：GET /api/chat/sse?workspaceId=xxx
 */

import http from "node:http";

const SIDECAR_HOST = "127.0.0.1";
const SIDECAR_PORT = 5758;
const AUTH_HEADER_PREFIX = "Bearer ";
const IDLE_TIMEOUT_MS = 30000;

export function createSSEProxy(gatewayApp, config = {}) {
    const sidecarHost = config.sidecarHost || SIDECAR_HOST;
    const sidecarPort = config.sidecarPort || SIDECAR_PORT;
    const authToken = config.authToken || process.env.GALAXYOS_MCP_TOKEN || "";

    gatewayApp.get("/api/chat/sse", (req, res) => {
        const workspaceId = req.query.workspaceId || "default";

        const authHeader = req.headers.authorization || "";
        if (authToken && (!authHeader.startsWith(AUTH_HEADER_PREFIX) || authHeader.slice(AUTH_HEADER_PREFIX.length) !== authToken)) {
            res.status(401).json({ error: "Unauthorized" });
            return;
        }

        res.writeHead(200, {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            Connection: "keep-alive",
            "X-Accel-Buffering": "no",
        });

        res.write(`event: connected\ndata: ${JSON.stringify({ workspaceId })}\n\n`);

        const sidecarReq = http.request(
            {
                hostname: sidecarHost,
                port: sidecarPort,
                path: `/events?workspaceId=${encodeURIComponent(workspaceId)}`,
                method: "GET",
            },
            (sidecarRes) => {
                let buffer = "";
                sidecarRes.on("data", (chunk) => {
                    buffer += chunk.toString();
                    const lines = buffer.split("\n\n");
                    buffer = lines.pop() || "";

                    for (const block of lines) {
                        if (!block.trim()) continue;

                        let eventType = "message";
                        let data = "";

                        for (const line of block.split("\n")) {
                            if (line.startsWith("event:")) {
                                eventType = line.slice(6).trim();
                            } else if (line.startsWith("data:")) {
                                data = line.slice(5).trim();
                            }
                        }

                        if (eventType === "tokui_chunk" || eventType === "tokui_error" || eventType === "cognitive_result" || eventType === "heartbeat") {
                            res.write(`event: ${eventType}\ndata: ${data}\n\n`);
                        }
                    }
                });

                sidecarRes.on("end", () => {
                    res.write(`event: streamEnd\ndata: {}\n\n`);
                    res.end();
                });
            },
        );

        sidecarReq.on("error", (e) => {
            console.error(`[GalaxyOS SSE Proxy] Sidecar connection error: ${e.message}`);
            res.write(`event: tokui_error\ndata: ${JSON.stringify({ error: "SSE Sidecar unavailable", code: "SIDECAR_UNAVAILABLE" })}\n\n`);
            res.end();
        });

        sidecarReq.end();

        const idleTimer = setTimeout(() => {
            res.write(`event: timeout\ndata: {}\n\n`);
            res.end();
        }, IDLE_TIMEOUT_MS);

        req.on("close", () => {
            clearTimeout(idleTimer);
            sidecarReq.destroy();
        });
    });

    gatewayApp.get("/api/cognitive/panel", (req, res) => {
        const workspaceId = req.query.workspaceId || "default";
        res.json({
            rccam: { currentStage: "idle", stagesCompleted: 0, totalStages: 5, isRunning: false, strategy: "direct_reply", depth: 3 },
            memory: { engramCount: 0, neuralCount: 0, synapseCount: 0, consolidationStatus: "idle" },
            dag: { totalNodes: 0, sessions: 0 },
            workspaceId,
        });
    });

    console.log("[GalaxyOS] SSE Proxy endpoints registered: GET /api/chat/sse, GET /api/cognitive/panel");
}

export default createSSEProxy;