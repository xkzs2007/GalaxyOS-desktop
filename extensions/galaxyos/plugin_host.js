/**
 * plugin_host.js — IPluginHost 接口抽象层
 *
 * 解耦 GalaxyOS 对 OpenClaw API 的硬编码依赖，支持插件版与桌面端双模式运行。
 * - IPluginHost: 接口方法签名（JSDoc）
 * - OpenClawAdapter: 插件模式，委托给 OpenClaw api 对象
 * - DesktopAdapter: 桌面端模式，本地 Map 注册表 + console 日志器
 */

import path from "node:path";
import { galaxyosHome } from "./paths.js";

/**
 * @typedef IPluginHost
 * @property {(name: string, schema: object, handler: Function) => void} registerTool — 注册工具
 * @property {(event: string, handler: Function) => void} on — 事件监听
 * @property {(name: string, fn: Function) => void} registerMemoryCapability — 注册记忆能力
 * @property {(name: string, config: object) => void} registerLaneType — 注册 Lane 类型
 * @property {(name: string, fn: Function) => void} registerHeartbeat — 注册心跳
 * @property {(name: string, schedule: string, fn: Function) => void} registerCron — 注册定时任务
 * @property {(load: object) => void} reportLoad — 报告负载
 * @property {() => string} getWorkspace — 获取工作空间路径
 * @property {() => ILogger} getLogger — 获取日志器
 * @property {() => object} getConfig — 获取插件配置
 * @property {(name: string, factory: Function) => void} registerContextEngine — 注册上下文引擎
 */

/**
 * @typedef ILogger
 * @property {(msg: string) => void} info
 * @property {(msg: string) => void} warn
 * @property {(msg: string) => void} error
 * @property {(msg: string) => void} debug
 */

/**
 * OpenClawAdapter — 插件模式适配器
 *
 * 将 IPluginHost 方法映射到 OpenClaw api 对象的对应方法。
 */
export class OpenClawAdapter {
    /**
     * @param {object} api - OpenClaw api 对象
     */
    constructor(api) {
        this._api = api;
    }

    registerTool(name, schema, handler) {
        if (typeof this._api.registerTool === "function") {
            if (typeof name === "object") {
                this._api.registerTool(name);
            } else {
                this._api.registerTool({ name, ...schema, execute: handler });
            }
        }
    }

    on(event, handler) {
        if (typeof this._api.on === "function") {
            this._api.on(event, handler);
        }
    }

    registerMemoryCapability(name, fn) {
        if (typeof this._api.registerMemoryCapability === "function") {
            if (typeof name === "object") {
                this._api.registerMemoryCapability(name);
            } else {
                this._api.registerMemoryCapability({ name, handler: fn });
            }
        }
    }

    registerLaneType(name, config) {
        if (typeof this._api.registerLaneType === "function") {
            if (typeof name === "object") {
                this._api.registerLaneType(name);
            } else {
                this._api.registerLaneType({ name, ...config });
            }
        }
    }

    registerHeartbeat(name, fn) {
        if (typeof this._api.registerHeartbeat === "function") {
            if (typeof name === "object") {
                this._api.registerHeartbeat(name);
            } else {
                this._api.registerHeartbeat({ name, handler: fn });
            }
        }
    }

    registerCron(name, schedule, fn) {
        if (typeof this._api.registerCron === "function") {
            if (typeof name === "object") {
                this._api.registerCron(name);
            } else {
                this._api.registerCron({ name, schedule, handler: fn });
            }
        }
    }

    reportLoad(load) {
        if (typeof this._api.reportLoad === "function") {
            this._api.reportLoad(load);
        }
    }

    getWorkspace() {
        const ws = this._api.runtime?.workspace?.cwd?.();
        return ws || null;
    }

    getLogger() {
        return this._api.logger;
    }

    getConfig() {
        return this._api.getConfig?.() || {};
    }

    registerContextEngine(name, factory) {
        if (typeof this._api.registerContextEngine === "function") {
            this._api.registerContextEngine(name, factory);
        }
    }
}

/**
 * DesktopAdapter — 桌面端模式适配器
 *
 * 不依赖 OpenClaw api 对象，所有注册维护在本地 Map 中。
 */
export class DesktopAdapter {
    constructor() {
        this._tools = new Map();
        this._events = new Map();
        this._memoryCapabilities = new Map();
        this._laneTypes = new Map();
        this._heartbeats = new Map();
        this._crons = new Map();
        this._workspace = null;
        this._logger = null;
    }

    registerTool(name, schema, handler) {
        if (typeof name === "object") {
            this._tools.set(name.name, name);
        } else {
            this._tools.set(name, { name, ...schema, execute: handler });
        }
    }

    on(event, handler) {
        if (!this._events.has(event)) {
            this._events.set(event, []);
        }
        this._events.get(event).push(handler);
    }

    registerMemoryCapability(name, fn) {
        if (typeof name === "object") {
            this._memoryCapabilities.set(name.name || "default", name);
        } else {
            this._memoryCapabilities.set(name, { name, handler: fn });
        }
    }

    registerLaneType(name, config) {
        if (typeof name === "object") {
            this._laneTypes.set(name.name, name);
        } else {
            this._laneTypes.set(name, { name, ...config });
        }
    }

    registerHeartbeat(name, fn) {
        if (typeof name === "object") {
            this._heartbeats.set(name.name || "default", name);
        } else {
            this._heartbeats.set(name, { name, handler: fn });
        }
    }

    registerCron(name, schedule, fn) {
        if (typeof name === "object") {
            this._crons.set(name.name || "default", name);
        } else {
            this._crons.set(name, { name, schedule, handler: fn });
        }
    }

    reportLoad(load) {
        // 桌面端模式：负载上报为 no-op（无外部调度器）
    }

    getWorkspace() {
        if (this._workspace) return this._workspace;
        const home = galaxyosHome();
        this._workspace = path.join(home, "workspace");
        return this._workspace;
    }

    getLogger() {
        if (this._logger) return this._logger;
        this._logger = {
            info: (msg) => process.stderr.write(`[INFO] ${msg}\n`),
            warn: (msg) => process.stderr.write(`[WARN] ${msg}\n`),
            error: (msg) => process.stderr.write(`[ERROR] ${msg}\n`),
            debug: (msg) => { if (process.env.GALAXYOS_DEBUG) process.stderr.write(`[DEBUG] ${msg}\n`); },
        };
        return this._logger;
    }

    getConfig() {
        return {};
    }

    registerContextEngine(name, factory) {
        // 桌面端模式：上下文引擎注册为 no-op
    }

    // ── 桌面端专用：获取本地注册表 ──

    get tools() { return this._tools; }
    get events() { return this._events; }
    get memoryCapabilities() { return this._memoryCapabilities; }
    get laneTypes() { return this._laneTypes; }
    get heartbeats() { return this._heartbeats; }
    get crons() { return this._crons; }
}