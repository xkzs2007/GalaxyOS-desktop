/**
 * launcher.js — GalaxyOS 双模式启动器
 *
 * 模式检测逻辑：
 *   1. GALAXYOS_MODE=plugin  → 注入 OpenClawAdapter
 *   2. GALAXYOS_MODE=desktop → 注入 DesktopAdapter
 *   3. 未设置时自动检测：api 对象可用则为 plugin，否则为 desktop
 */

import { OpenClawAdapter, DesktopAdapter } from "./plugin_host.js";

/**
 * 创建 IPluginHost 实例
 *
 * @param {object} [api] - OpenClaw api 对象（插件模式传入）
 * @returns {import('./plugin_host.js').IPluginHost}
 */
export function createHost(api) {
    const mode = process.env.GALAXYOS_MODE;

    if (mode === "plugin") {
        if (!api) throw new Error("[galaxyos] GALAXYOS_MODE=plugin but no api object provided");
        return new OpenClawAdapter(api);
    }

    if (mode === "desktop") {
        return new DesktopAdapter();
    }

    // 自动检测：api 对象可用且有 registerTool 方法则为 plugin
    if (api && typeof api.registerTool === "function") {
        return new OpenClawAdapter(api);
    }

    return new DesktopAdapter();
}

/**
 * 获取当前运行模式
 * @returns {"plugin"|"desktop"}
 */
export function getMode() {
    const mode = process.env.GALAXYOS_MODE;
    if (mode === "plugin" || mode === "desktop") return mode;
    return "auto";
}