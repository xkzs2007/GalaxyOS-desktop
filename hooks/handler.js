/**
 * Claw Bootstrap Hook V6 — 人格注入 + DAG 上下文持久化
 *
 * 职责：
 *   1. 人格注入（V5 继承）
 *   2. 消息注入 DAG（通过 dag_shim.py）
 *   3. 跨会话记忆恢复（V5 继承）
 *
 * V5 → V6 变化：
 *   - 在人格注入后自动调 dag_shim init 写入 DAG
 *   - 每条消息落 DAG，为 ContextEngine 的 assemble/compact 提供数据源
 *   - 消息分 user（event.content）和 assistant（generated_answer）
 */

import { execFile } from "child_process";
import { readFileSync, existsSync, readdirSync } from "fs";
import { join } from "path";

const DAG_SHIM = join(
  process.env.HOME || "/home/sandbox",
  ".openclaw/workspace/scripts/dag_shim.py"
);
const PYTHON = "/usr/bin/python3";

// DAG 节流：同一 session 最近 N 秒内已调过 add 则跳过
const _dag_throttle = new Map();
const DAG_THROTTLE_MS = 3000;

function _runDagShim(...args) {
  return new Promise((resolve) => {
    const start = Date.now();
    const child = execFile(PYTHON, [DAG_SHIM, ...args], {
      timeout: 15000,
      maxBuffer: 1024 * 64,
    }, (err, stdout, stderr) => {
      const elapsed = Date.now() - start;
      if (err) {
        console.log(`[claw-bootstrap] dag_shim ${args[0]} 失败 (${elapsed}ms): ${err.message}`);
        resolve(null);
        return;
      }
      try {
        const result = JSON.parse(stdout.trim());
        console.log(`[claw-bootstrap] dag_shim ${args[0]} 成功 (${elapsed}ms)`);
        resolve(result);
      } catch {
        console.log(`[claw-bootstrap] dag_shim 解析失败: ${stdout.slice(0, 200)}`);
        resolve(null);
      }
    });
  });
}

const handler = async (event) => {
  if (event.type !== "message") return;

  // ===== 过滤噪音消息 =====
  if (event.content) {
    const noisePatterns = [
      "用户查询相关skill列表如下",
      "系统消息，非用户发言",
      "当前任务已经调用了较多次数的工具",
      "当前行为存在安全隐患",
    ];
    if (noisePatterns.some((p) => event.content.includes(p))) return;
  }

  const sessionKey = event.context?.sessionKey || "default";
  const WS = event.context?.workspaceDir ||
    "/home/sandbox/.openclaw/workspace";

  // ===== 1. 人格注入（V5 逻辑） =====
  let personaContent = "";
  const personaFile = join(WS, "IDENTITY.md");
  const soulFile = join(WS, "SOUL.md");

  if (existsSync(personaFile)) {
    const lines = readFileSync(personaFile, "utf-8").split("\n");
    personaContent += lines.slice(0, 1).join("\n").trim() + "\n";
    personaContent += lines.slice(3, 15).join("\n").trim() + "\n\n";
  }

  if (existsSync(soulFile)) {
    const soulText = readFileSync(soulFile, "utf-8");
    const truthsMatch = soulText.match(/## Core Truths\n\n([\s\S]*?)\n\n##/);
    if (truthsMatch) {
      personaContent += truthsMatch[1].trim() + "\n\n";
    }
  }

  // ===== 2. 记忆预加载（V5 逻辑） =====
  let memoryPreload = "";
  const memoryDir = join(WS, "memory");
  if (existsSync(memoryDir)) {
    try {
      const memoryFiles = readdirSync(memoryDir)
        .filter((f) => f.match(/^\d{4}-\d{2}-\d{2}\.md$/))
        .sort()
        .reverse()
        .slice(0, 3);

      const snippets = [];
      for (const file of memoryFiles) {
        const content = readFileSync(join(memoryDir, file), "utf-8").slice(0, 600).trim();
        if (content) snippets.push(`[${file.replace(".md", "")}]\n${content}`);
      }
      if (snippets.length > 0) {
        memoryPreload = "[近期记忆预加载]\n" + snippets.join("\n\n");
      }
    } catch (memErr) {
      console.log(`[claw-bootstrap] 记忆预加载跳过: ${memErr.message}`);
    }
  }

  // ===== 3. 跨会话记忆恢复（V5 逻辑） =====
  let crossSessionRestore = "";
  const clawCorePath = join(WS, "..", "extensions", "claw-core", "dist", "index.js");
  if (existsSync(clawCorePath)) {
    try {
      const { getWorker } = await import(clawCorePath);
      const worker = getWorker(WS);
      if (worker) {
        const restoreResult = await worker.call("restore_context", {
          sessionKey,
          recentDays: 3,
        });
        if (restoreResult?.restored_text) {
          crossSessionRestore = "[跨会话记忆恢复]\n" + restoreResult.restored_text;
        }
      }
    } catch (restoreErr) {
      console.log(`[claw-bootstrap] 跨会话记忆恢复跳过: ${restoreErr.message}`);
    }
  }

  // ===== 4. 自进化上下文（V5 逻辑） =====
  let evolutionContent = "";
  const evolutionFile = join(WS, "memory", "evolution_tracker.jsonl");
  if (existsSync(evolutionFile)) {
    const lines = readFileSync(evolutionFile, "utf-8")
      .split("\n")
      .filter((l) => l.trim());
    const recentEvals = lines.slice(-5);
    if (recentEvals.length > 0) {
      const scores = recentEvals
        .map((l) => {
          try {
            const e = JSON.parse(l);
            if (!e.scores) return null;
            return {
              completeness: e.scores.completeness || 0,
              relevance: e.scores.relevance || 0,
              conciseness: e.scores.conciseness || 0,
              factuality: e.scores.factuality || 0,
              overall: e.scores.overall || 0,
            };
          } catch { return null; }
        })
        .filter(Boolean);

      if (scores.length > 0) {
        const avg = (key) =>
          (scores.reduce((s, sc) => s + sc[key], 0) / scores.length).toFixed(2);
        evolutionContent = [
          "[自进化上下文]",
          `  综合评分: ${avg("overall")}`,
          `  完备性: ${avg("completeness")} · 相关性: ${avg("relevance")}`,
          `  简洁性: ${avg("conciseness")} · 真实性: ${avg("factuality")}`,
          `  共 ${scores.length} 次自评记录`,
        ].join("\n");
      }
    }
  }

  // ===== 组装注入内容 =====
  let injectContent = "";
  if (personaContent.trim()) injectContent += `[人格定义]\n${personaContent.trim()}`;
  if (memoryPreload) injectContent += "\n\n" + memoryPreload;
  if (crossSessionRestore) injectContent += "\n\n" + crossSessionRestore;
  if (evolutionContent) injectContent += "\n\n" + evolutionContent;

  if (injectContent.trim()) {
    const hasPersona = (event.messages || []).some(
      (m) =>
        m.role === "system" &&
        (m.content.includes("小艺 Claw") ||
          m.content.includes("IDENTITY") ||
          m.content.includes("Core Truths") ||
          m.content.includes("自进化上下文") ||
          m.content.includes("近期记忆预加载"))
    );

    if (!hasPersona) {
      if (!event.messages) event.messages = [];
      const existingSystemIdx = event.messages.findIndex((m) => m.role === "system");
      if (existingSystemIdx >= 0) {
        event.messages.splice(existingSystemIdx + 1, 0, {
          role: "system",
          content: injectContent.trim(),
        });
      } else {
        event.messages.unshift({ role: "system", content: injectContent.trim() });
      }
      console.log(
        `[claw-bootstrap] 注入完成: 人格 ${personaContent.length} 字符, 记忆预加载 ${memoryPreload.length} 字符, 自进化 ${evolutionContent.length} 字符`
      );
    }
  }

  // ===== 5. DAG 上下文持久化（V6 新增） =====

  // 5a. 首次消息触发 DAG init（人格节点注入）
  if (existsSync(DAG_SHIM)) {
    _runDagShim("init", "--session", sessionKey);

    // 5b. 用户消息写入 DAG（带节流，不阻塞主流程）
    if (event.content && typeof event.content === "string") {
      const throttleKey = `${sessionKey}_user`;
      const lastAdd = _dag_throttle.get(throttleKey);
      if (!lastAdd || Date.now() - lastAdd > DAG_THROTTLE_MS) {
        _dag_throttle.set(throttleKey, Date.now());
        _runDagShim("add", "--session", sessionKey, "--msg", event.content.slice(0, 2000), "--role", "user");
      }
    }

    // 5c. Assistant 回答写入 DAG（通过 generated_answer 字段）
    const answer = event.generated_answer || event.message;
    if (answer && typeof answer === "string" && answer.length > 10) {
      const throttleKey = `${sessionKey}_assistant`;
      const lastAdd = _dag_throttle.get(throttleKey);
      if (!lastAdd || Date.now() - lastAdd > DAG_THROTTLE_MS * 2) {
        _dag_throttle.set(throttleKey, Date.now());
        _runDagShim("add", "--session", sessionKey, "--msg", answer.slice(0, 2000), "--role", "assistant");
      }
    }
  }
};

export default handler;
