/**
 * cli-anything —— GalaxyOS 插件（轻量版）
 *
 * 赋予 Agent 完整的 workspace 自运维能力：
 *   shell_run | shell_git | shell_make | shell_test | shell_file_read | shell_file_write
 *
 * 灵感来源：HKUDS/CLI-Anything（42.6k stars，Apache 2.0）
 * 上游项目：https://github.com/HKUDS/CLI-Anything
 * 本插件专注于 workspace 内命令执行，上游项目专注于"软件 Agent-Native 化"。
 *
 * 安全限制：
 *   - 命令均在 workspace cwd 下执行
 *   - 默认 30s 超时，输出截断 8KB
 *   - git commit 需显式开启 allowGitCommit
 */

import path from "node:path";
import { spawnSync, execSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";

const TAG = "[cli-anything]";

/** 安全过滤：拦截明显危险的操作 */
const BLOCKED_PATTERNS = [
    /\brm\s+-rf\s+\//, /\bdd\s+if=/, /\b>\/dev\/sda/,
    /\bmkfs\./, /\bformat\s+C:/i, /\bdel\s+\/f\s+\/s\s+C:\\/i,
];

function _isSafe(command) {
    for (const p of BLOCKED_PATTERNS) {
        if (p.test(command)) return false;
    }
    return true;
}

/** 在 workspace 内执行命令 */
function _shell(workspace, command, timeoutMs = 30000, maxChars = 8000) {
    if (!_isSafe(command)) {
        return { ok: false, error: "blocked: potentially destructive command", blocked: true };
    }
    try {
        const r = spawnSync("bash", ["-lc", command], {
            cwd: workspace,
            timeout: timeoutMs,
            maxBuffer: 10 * 1024 * 1024,
            encoding: "utf-8",
            env: { ...process.env, PATH: process.env.PATH },
            shell: false,
        });
        let stdout = (r.stdout || "").trim();
        let stderr = (r.stderr || "").trim();
        if (stdout.length > maxChars) stdout = stdout.slice(0, maxChars) + `\n... (truncated, ${stdout.length} chars total)`;
        if (stderr.length > maxChars) stderr = stderr.slice(0, maxChars) + `\n... (truncated)`;
        return {
            ok: r.status === 0,
            exitCode: r.status,
            stdout,
            stderr: stderr || null,
            signal: r.signal || null,
        };
    } catch (e) {
        return { ok: false, error: e.message, killed: e.killed || false };
    }
}

/** 导出默认注册函数 */
export default function register(api) {
    const ws = api.runtime?.workspace?.cwd?.() || process.env.OPENCLAW_WORKSPACE || process.cwd();
    const cfg = api.getConfig?.() || {};
    if (cfg.enabled === false) return;

    const maxChars = cfg.maxOutputChars || 8000;
    const timeoutMs = cfg.timeoutMs || 30000;

    api.logger?.info?.(`${TAG} loaded, workspace=${ws}`);

    // ═════════════════════════════════════════════════
    // Tool: shell_run
    // ═════════════════════════════════════════════════
    api.registerTool({
        name: "shell_run",
        label: "运行 Shell 命令",
        description:
            "在 workspace 中执行任意 Shell 命令。支持管道、重定向、变量。\n" +
            "安全限制：危险命令（rm -rf / 等）会被拦截。30s 超时。\n" +
            "返回 JSON：{ ok, exitCode, stdout, stderr }",
        parameters: {
            type: "object",
            properties: {
                command: { type: "string", description: "Shell 命令（bash -lc 执行）" },
                timeout_ms: { type: "number", description: "超时（默认 30000ms）" },
            },
            required: ["command"],
        },
        async execute(_id, params) {
            const cmd = String(params.command);
            const t = Number(params.timeout_ms) || timeoutMs;
            const r = _shell(ws, cmd, t, maxChars);
            const text = r.ok
                ? `✅ exit=${
                      r.exitCode
                  }\n${r.stdout || "(no output)"}`
                : `❌ exit=${r.exitCode}\n${r.stderr || r.error || "unknown error"}`;
            return { content: [{ type: "text", text }], details: r };
        },
    });

    // ═════════════════════════════════════════════════
    // Tool: shell_git
    // ═════════════════════════════════════════════════
    api.registerTool({
        name: "shell_git",
        label: "Git 操作",
        description:
            "执行 git 命令。支持 status / diff / log / branch / stash 等只读操作。\n" +
            "git commit 需使用单独的 shell_git_commit 工具。",
        parameters: {
            type: "object",
            properties: {
                subcommand: {
                    type: "string",
                    description: "git 子命令，如 status | diff | diff --staged | log --oneline -5 | branch | stash list",
                },
            },
            required: ["subcommand"],
        },
        async execute(_id, params) {
            const sub = String(params.subcommand);
            // 禁止 commit / push / force push 等写操作
            const writeOps = /\bcommit\b|\bpush\b|\bmerge\b|\brebase\b|\breset\b|\bclean\b|\brm\b/;
            if (writeOps.test(sub)) {
                return { content: [{ type: "text", text: "⛔ git 写操作请使用 shell_git_commit 或 shell_run" }] };
            }
            const r = _shell(ws, `git ${sub}`, 15000, maxChars);
            const text = r.ok ? r.stdout || "(no output)" : `❌ ${r.stderr || r.error}`;
            return { content: [{ type: "text", text }], details: r };
        },
    });

    // ═════════════════════════════════════════════════
    // Tool: shell_git_commit
    // ═════════════════════════════════════════════════
    api.registerTool({
        name: "shell_git_commit",
        label: "Git 提交",
        description: "git add -A && git commit -m <message>。需配置 allowGitCommit=true。",
        parameters: {
            type: "object",
            properties: {
                message: { type: "string", description: "commit message" },
                files: { type: "string", description: "可选：只 add 特定文件（默认 -A）" },
            },
            required: ["message"],
        },
        async execute(_id, params) {
            if (!cfg.allowGitCommit) {
                return { content: [{ type: "text", text: "⛔ git commit 未开启（设置 allowGitCommit=true）" }] };
            }
            const msg = String(params.message).replace(/"/g, '\\"');
            const add = params.files ? `git add ${params.files}` : "git add -A";
            const cmd = `${add} && git commit -m "${msg}"`;
            const r = _shell(ws, cmd, 15000, maxChars);
            const text = r.ok ? `✅ committed\n${r.stdout || ""}` : `❌ ${r.stderr || r.error}`;
            return { content: [{ type: "text", text }], details: r };
        },
    });

    // ═════════════════════════════════════════════════
    // Tool: shell_make
    // ═════════════════════════════════════════════════
    api.registerTool({
        name: "shell_make",
        label: "运行 Makefile 目标",
        description: "执行 make <target>。默认目标：test | sync | lint | clean | install | native。60s 超时。",
        parameters: {
            type: "object",
            properties: {
                target: { type: "string", description: "Makefile 目标名（默认空 = make）" },
            },
            required: [],
        },
        async execute(_id, params) {
            const target = params.target ? ` ${String(params.target)}` : "";
            const r = _shell(ws, `make${target}`, 60000, maxChars);
            const text = r.ok
                ? `✅ make${target}\n${r.stdout || "(no output)"}`
                : `❌ make${target}\n${r.stderr || r.error}`;
            return { content: [{ type: "text", text }], details: r };
        },
    });

    // ═════════════════════════════════════════════════
    // Tool: shell_test
    // ═════════════════════════════════════════════════
    api.registerTool({
        name: "shell_test",
        label: "运行测试",
        description: "运行 Python 测试（pytest）。默认运行 make test 或指定文件。120s 超时。",
        parameters: {
            type: "object",
            properties: {
                target: {
                    type: "string",
                    description: "测试目标（默认空 = make test）。可指定 pytest 参数如 'tests/test_imports.py -v'",
                },
            },
            required: [],
        },
        async execute(_id, params) {
            const target = params.target
                ? `cd ${ws} && .venv/bin/python -m pytest ${String(params.target)} -p no:warnings -q`
                : `make test`;
            const r = _shell(ws, target, 120000, maxChars * 2);
            const text = r.ok
                ? `✅ tests\n${r.stdout || "(all passed)"}`
                : `❌ tests\n${r.stdout || ""}\n${r.stderr || r.error}`;
            return { content: [{ type: "text", text }], details: r };
        },
    });

    // ═════════════════════════════════════════════════
    // Tool: shell_file_read
    // ═════════════════════════════════════════════════
    api.registerTool({
        name: "shell_file_read",
        label: "读文件",
        description: "读取 workspace 内文件内容。支持文本文件和 JSON 解析。",
        parameters: {
            type: "object",
            properties: {
                path: { type: "string", description: "文件路径（相对于 workspace）" },
                max_chars: { type: "number", description: "最大字符数（默认 5000）" },
            },
            required: ["path"],
        },
        async execute(_id, params) {
            const filePath = path.resolve(ws, String(params.path));
            const max = Number(params.max_chars) || 5000;
            if (!filePath.startsWith(path.resolve(ws))) {
                return { content: [{ type: "text", text: "⛔ 禁止访问 workspace 外路径" }] };
            }
            try {
                if (!existsSync(filePath)) {
                    return { content: [{ type: "text", text: `❌ 文件不存在: ${params.path}` }] };
                }
                const content = readFileSync(filePath, "utf-8");
                const truncated = content.length > max ? content.slice(0, max) + `\n... (${content.length} chars total)` : content;
                return { content: [{ type: "text", text: truncated }], details: { path: params.path, size: content.length } };
            } catch (e) {
                return { content: [{ type: "text", text: `❌ ${e.message}` }] };
            }
        },
    });

    // ═════════════════════════════════════════════════
    // Tool: shell_file_write
    // ═════════════════════════════════════════════════
    api.registerTool({
        name: "shell_file_write",
        label: "写文件",
        description: "在 workspace 内写入/覆盖文件。自动创建父目录。",
        parameters: {
            type: "object",
            properties: {
                path: { type: "string", description: "文件路径（相对于 workspace）" },
                content: { type: "string", description: "文件内容" },
            },
            required: ["path", "content"],
        },
        async execute(_id, params) {
            const filePath = path.resolve(ws, String(params.path));
            if (!filePath.startsWith(path.resolve(ws))) {
                return { content: [{ type: "text", text: "⛔ 禁止写入 workspace 外路径" }] };
            }
            try {
                mkdirSync(path.dirname(filePath), { recursive: true });
                writeFileSync(filePath, String(params.content), "utf-8");
                const size = Buffer.byteLength(String(params.content), "utf-8");
                return { content: [{ type: "text", text: `✅ 已写入 ${params.path} (${size} bytes)` }], details: { path: params.path, bytes: size } };
            } catch (e) {
                return { content: [{ type: "text", text: `❌ ${e.message}` }] };
            }
        },
    });
}
