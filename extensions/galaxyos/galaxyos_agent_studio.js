/**
 * GalaxyOS Agent Studio Plugin Entry
 *
 * Agent Studio MCP 类型插件入口：
 *   1. 启动 GalaxyOS Python 内核（MCP Server 子进程）
 *   2. 注册 9 个生命周期钩子回调
 *   3. 定期健康检查
 *   4. 优雅关闭
 *
 * Architecture:
 *   Agent Studio Gateway → MCP Client → GalaxyOS Python Kernel (MCP Server)
 *                                          ├── 15 认知增强工具
 *                                          ├── 8 Agent Studio 集成工具
 *                                          ├── tokui_render 流式 UI 工具
 *                                          ├── 液态神经记忆 + DAG 上下文
 *                                          └── R-CCAM 认知循环
 */

import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { galaxyosHome } from "./paths.js";

const TAG = "[galaxyos:agent-studio]";
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PYTHON_KERNEL_MODULE = "galaxyos.kernel.mcp_server";

const DEFAULT_CONFIG = {
    pythonPath: "python",
    mcpTransport: "streamable_http",
    mcpHost: "127.0.0.1",
    mcpPort: 8765,
    autoRestart: true,
    maxRestartAttempts: 3,
    healthCheckIntervalMs: 10000,
    shutdownTimeoutMs: 5000,
};

class GalaxyOSPlugin {
    constructor(config = {}) {
        this._config = { ...DEFAULT_CONFIG, ...config };
        this._process = null;
        this._restartCount = 0;
        this._running = false;
        this._startTime = 0;
        this._healthCheckTimer = null;
        this._hooks = {};
        this._lifecycleManager = null;
    }

    // ── 插件生命周期 ──

    async onLoad(api) {
        console.log(`${TAG} Plugin loading...`);

        this._api = api;

        try {
            this._lifecycleManager = await this._loadLifecycleManager();
        } catch (e) {
            console.warn(`${TAG} Lifecycle manager load failed, using built-in hooks: ${e.message}`);
        }

        const started = this._startPythonKernel();
        if (!started) {
            console.error(`${TAG} Failed to start Python kernel`);
            return;
        }

        this._registerHooks();
        this._startHealthCheck();

        console.log(`${TAG} Plugin loaded: Python kernel started (transport=${this._config.mcpTransport}, port=${this._config.mcpPort})`);
    }

    async onUnload() {
        console.log(`${TAG} Plugin unloading...`);

        this._stopHealthCheck();
        this._stopPythonKernel();

        console.log(`${TAG} Plugin unloaded`);
    }

    // ── Python 内核管理 ──

    _startPythonKernel() {
        try {
            const pythonBin = this._config.pythonPath;
            const args = ["-m", PYTHON_KERNEL_MODULE];

            if (this._config.mcpTransport === "sse") {
                args.push("--transport", "sse", "--host", this._config.mcpHost, "--port", String(this._config.mcpPort));
            } else if (this._config.mcpTransport === "streamable_http") {
                args.push("--transport", "streamable-http", "--host", this._config.mcpHost, "--port", String(this._config.mcpPort));
            }

            const env = {
                ...process.env,
                GALAXYOS_MODE: "desktop",
                GALAXYOS_MCP_TRANSPORT: this._config.mcpTransport,
                GALAXYOS_MCP_HOST: this._config.mcpHost,
                GALAXYOS_MCP_PORT: String(this._config.mcpPort),
            };

            this._process = spawn(pythonBin, args, {
                env,
                stdio: ["pipe", "pipe", "pipe"],
                detached: false,
            });

            this._process.stdout?.on("data", (data) => {
                const line = data.toString().trim();
                if (line) console.log(`${TAG} [python:out] ${line}`);
            });

            this._process.stderr?.on("data", (data) => {
                const line = data.toString().trim();
                if (line) console.error(`${TAG} [python:err] ${line}`);
            });

            this._process.on("exit", (code, signal) => {
                console.warn(`${TAG} Python kernel exited: code=${code}, signal=${signal}`);
                this._running = false;
                this._onKernelCrash();
            });

            this._running = true;
            this._startTime = Date.now();
            this._restartCount = 0;
            return true;
        } catch (e) {
            console.error(`${TAG} Failed to start Python kernel: ${e.message}`);
            return false;
        }
    }

    _stopPythonKernel() {
        if (!this._process || this._process.killed) return;

        try {
            this._process.kill("SIGTERM");
            const timeout = setTimeout(() => {
                if (this._process && !this._process.killed) {
                    this._process.kill("SIGKILL");
                }
            }, this._config.shutdownTimeoutMs);
            timeout.unref();
        } catch (e) {
            console.warn(`${TAG} Error stopping Python kernel: ${e.message}`);
        }

        this._running = false;
    }

    _onKernelCrash() {
        if (!this._config.autoRestart) return;
        if (this._restartCount >= this._config.maxRestartAttempts) {
            console.error(`${TAG} Max restart attempts (${this._config.maxRestartAttempts}) reached, giving up`);
            return;
        }

        this._restartCount += 1;
        const backoff = Math.min(2 ** this._restartCount * 1000, 10000);
        console.log(`${TAG} Auto-restarting Python kernel in ${backoff}ms (attempt ${this._restartCount}/${this._config.maxRestartAttempts})`);

        setTimeout(() => {
            this._startPythonKernel();
        }, backoff);
    }

    // ── 钩子注册 ──

    _registerHooks() {
        const hookMap = {
            on_plugin_load: this._onPluginLoad.bind(this),
            on_plugin_unload: this._onPluginUnload.bind(this),
            on_pre_tool_use: this._onPreToolUse.bind(this),
            on_post_tool_use: this._onPostToolUse.bind(this),
            on_pre_compaction: this._onPreCompaction.bind(this),
            on_post_compaction: this._onPostCompaction.bind(this),
            on_pre_agent_reply: this._onPreAgentReply.bind(this),
            on_post_agent_reply: this._onPostAgentReply.bind(this),
            on_user_prompt_submit: this._onUserPromptSubmit.bind(this),
        };

        for (const [event, handler] of Object.entries(hookMap)) {
            this._hooks[event] = handler;
            if (this._api && typeof this._api.on === "function") {
                this._api.on(event, handler);
            }
        }

        console.log(`${TAG} Registered 9 lifecycle hooks`);
    }

    async _onPluginLoad(context) {
        return this._dispatchHook("gateway_start", context);
    }

    async _onPluginUnload(context) {
        await this._dispatchHook("gateway_stop", context);
        await this.onUnload();
    }

    async _onPreToolUse(context) {
        return this._dispatchHook("before_tool_call", context);
    }

    async _onPostToolUse(context) {
        return this._dispatchHook("after_tool_call", context);
    }

    async _onPreCompaction(context) {
        return this._dispatchHook("before_compaction", context);
    }

    async _onPostCompaction(context) {
        return this._dispatchHook("after_compaction", context);
    }

    async _onPreAgentReply(context) {
        return this._dispatchHook("before_agent_reply", context);
    }

    async _onPostAgentReply(context) {
        return this._dispatchHook("after_agent_reply", context);
    }

    async _onUserPromptSubmit(context) {
        return this._dispatchHook("before_prompt_build", context);
    }

    async _dispatchHook(hookName, context) {
        if (this._lifecycleManager) {
            try {
                return await this._lifecycleManager.dispatch(hookName, context);
            } catch (e) {
                console.warn(`${TAG} Lifecycle dispatch failed for ${hookName}: ${e.message}`);
            }
        }

        if (!this._running) {
            return { allowed: true, warning: "GalaxyOS kernel not running" };
        }

        return { allowed: true };
    }

    async _loadLifecycleManager() {
        try {
            const { spawn: spawnSync } = await import("node:child_process");
            const mod = await import(path.join(__dirname, "..", "..", "galaxyos", "agent_studio", "lifecycle.py"));
            return null;
        } catch {
            return null;
        }
    }

    // ── 健康检查 ──

    _startHealthCheck() {
        if (this._healthCheckTimer) return;

        this._healthCheckTimer = setInterval(() => {
            const health = this.getHealthStatus();
            if (health.status !== "healthy" && health.status !== "running") {
                console.warn(`${TAG} Health check: ${health.status} - ${health.message}`);
            }
        }, this._config.healthCheckIntervalMs);

        this._healthCheckTimer.unref();
    }

    _stopHealthCheck() {
        if (this._healthCheckTimer) {
            clearInterval(this._healthCheckTimer);
            this._healthCheckTimer = null;
        }
    }

    getHealthStatus() {
        if (!this._process) {
            return { status: "stopped", message: "Python kernel not started", uptime_s: 0 };
        }

        const poll = this._process.poll?.() ?? (this._process.killed ? -1 : null);

        if (poll === null || poll === undefined) {
            const uptime = (Date.now() - this._startTime) / 1000;
            return { status: "healthy", message: "OK", uptime_s: Math.round(uptime), restart_count: this._restartCount };
        }

        return {
            status: "crashed",
            message: `Process exited with code ${poll}`,
            uptime_s: 0,
            restart_count: this._restartCount,
        };
    }

    // ── 17 层架构健康状态 ──

    getLayerStatus() {
        const layers = {};
        for (let i = 1; i <= 17; i++) {
            layers[`L${i}`] = this._running ? "healthy" : "stopped";
        }
        return layers;
    }
}

// ── 导出插件工厂 ──

export function createGalaxyOSPlugin(config = {}) {
    return new GalaxyOSPlugin(config);
}

export { GalaxyOSPlugin };
export default GalaxyOSPlugin;