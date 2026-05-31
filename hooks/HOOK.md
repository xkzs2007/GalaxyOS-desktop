---
name: claw-bootstrap
description: "人格注入器（精简版）：每次消息检查并注入人格 system message"
metadata:
  {
    "openclaw":
      {
        "emoji": "🦞",
        "events": ["message:received"],
        "requires": { "bins": ["python3"] },
        "install": [{ "id": "managed", "kind": "local", "label": "Managed hook at ~/.openclaw/hooks/" }],
      },
  }
---

# Claw Bootstrap Hook (V5 — 精简版)

## 职责

**只做一件事：确保人格定义存在于消息上下文中。**

每次用户消息接收时，自动检查 `event.messages` 中是否已有人格 system message。

如果缺失，从以下文件读取并注入：
- `IDENTITY.md` — 核心身份定义（名字、角色、性格特质）
- `SOUL.md` — Core Truths 段（行为准则、边界）

## 不做什么

- ❌ 不调 `dag_shim.py` subprocess
- ❌ 不替换 `event.messages`
- ❌ 不做 DAG 上下文组装
- ❌ 不做增量摘要

DAG 上下文管理能力已下沉到 `XiaoYiClawLLM` 内部，按需调用 `assemble_with_cache()`。

## 人格注入幂等性

每次消息检查去重标识：
- 内容包含 "小艺 Claw"
- 内容包含 "IDENTITY"
- 内容包含 "Core Truths"

任一匹配 → 跳过注入。

## 与 compaction 的关系

- 人格 system message 是 `role: "system"`，OpenClaw compaction 保留 system message 和最近消息
- handler 不替换 `event.messages`，不跟 compaction 打架
- 每次消息检查一次，人格永不丢失
