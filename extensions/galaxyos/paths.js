/**
 * paths.js — GalaxyOS JS 端路径解析（单一真相源）
 *
 * 优先级链:
 *   1) GALAXYOS_HOME 环境变量（显式覆盖，最高优先级）
 *   2) OPENCLAW_HOME / GALAXYOS_OPENCLAW_HOME 环境变量（向后兼容）
 *   3) /opt/openclaw（容器固定布局）
 *   4) $HOME/.galaxyos（桌面端默认）
 *   5) $HOME/.openclaw（插件版生产默认）
 *   6) $HOME/.openclaw-dev（开发测试）
 *
 * v9.5: 新增 galaxyosHome()，GALAXYOS_HOME 优先于 OPENCLAW_HOME。
 */

import path from "node:path";
import fs, { existsSync } from "node:fs";

function _statSyncSafe(p) {
    try { return fs.statSync(p); } catch { return null; }
}

/**
 * GalaxyOS 根目录（权威路径定义）
 *
 * GALAXYOS_HOME > OPENCLAW_HOME > ~/.galaxyos > /opt/openclaw > ~/.openclaw > ~/.openclaw-dev
 */
export function galaxyosHome() {
    // 1) GALAXYOS_HOME 显式覆盖
    const galaxyosEnv = process.env.GALAXYOS_HOME;
    if (galaxyosEnv && existsSync(galaxyosEnv) && _statSyncSafe(galaxyosEnv)?.isDirectory()) {
        return galaxyosEnv;
    }

    // 2) OPENCLAW_HOME / GALAXYOS_OPENCLAW_HOME 向后兼容
    const openclawEnvVars = ["OPENCLAW_HOME", "GALAXYOS_OPENCLAW_HOME"];
    for (const k of openclawEnvVars) {
        const v = process.env[k];
        if (v && existsSync(v) && _statSyncSafe(v)?.isDirectory()) return v;
    }

    const home = process.env.HOME || process.env.USERPROFILE || "/home/sandbox";

    // 3) ~/.galaxyos（桌面端默认）
    const galaxyosDefault = path.join(home, ".galaxyos");
    if (existsSync(galaxyosDefault) && _statSyncSafe(galaxyosDefault)?.isDirectory()) {
        return galaxyosDefault;
    }

    // 4) /opt/openclaw（容器固定布局）
    if (existsSync("/opt/openclaw") && _statSyncSafe("/opt/openclaw")?.isDirectory()) {
        return "/opt/openclaw";
    }

    // 5) ~/.openclaw（插件版生产默认）
    const prod = path.join(home, ".openclaw");
    if (existsSync(prod) && _statSyncSafe(prod)?.isDirectory()) return prod;

    // 6) ~/.openclaw-dev（开发测试）
    const dev = path.join(home, ".openclaw-dev");
    if (existsSync(dev) && _statSyncSafe(dev)?.isDirectory()) return dev;

    // 兜底：GALAXYOS_HOME 默认
    return galaxyosDefault;
}

/**
 * OpenClaw 根目录（向后兼容别名）
 *
 * 内部委托给 galaxyosHome()，保持向后兼容。
 * 新代码应使用 galaxyosHome()。
 */
export function openclawHome() {
    return galaxyosHome();
}