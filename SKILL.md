---
name: galaxyos
description: GalaxyOS 认知增强引擎 v8.6.0 — OpenClaw 深度集成 + 液态神经记忆 + 技能自演化
author: 2997417176
license: MIT
tags: [context-engine, memory, llm, dag, rccam, cosplay, lfm, qaqc, nps, syna, lsafety, progressive-disclosure]
---

# GalaxyOS v8.6.0

> **定位**：OpenClaw 的认知增强引擎，占据两个核心插槽
> **架构**：DAG 上下文 + 液态记忆 + COSPLAY 技能自演化 + MultiAgent 协同
> **最新特性**：v8.6.0 OpenClaw 深度集成改造（4 阶段全量落地）

---

## 🎯 核心能力

GalaxyOS 作为 OpenClaw 插件，同时占据两个插槽：

| 插槽 | 注册 ID | 接管能力 |
|------|--------|----------|
| **contextEngine** | `claw-core-engine` | 上下文组装 (assemble) / 压缩 (compact) / 摄入 (ingest) |
| **memory** | `galaxyos` | 记忆检索 / 写入 / flushPlan / publicArtifacts |

### 7 大能力模块

1. **液态神经记忆** — LTC 突触 / CfC 推理 / NCP 神经电路策略 / SSM 状态预测 / 仿生遗忘曲线
2. **DAG 上下文管理** — SQLite 持久化 / 摘要节点回溯 / 时间衰减排序 / 多粒度优先级
3. **COSPLAY 技能自演化** — 从执行轨迹学习技能合约 → ProtoSkill → 毕业为成熟 Skill
4. **LFM 技能库** — 22 方向论文集成 / 效果签名 / 合并·拆分·精修·淘汰 / 跨会话记忆
5. **R-CCAM 认知循环** — Retrieval → Cognition → Control → Action → Memory 五阶段结构化
6. **MultiAgent 协同** — 5 角色（searcher/analyst/architect/critic/summarizer）+ 公告板 + 蒸馏 + 交叉验证
7. **OpenClaw 深度集成** — 9 钩子 / 15 工具（含 policy）/ 上下文压缩协同 / 心跳与定时任务

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenClaw Gateway                          │
│  (12+ 消息平台 / 7 层纵深防御 / Lane Queue / 设备配对)        │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌──────────────┐    ┌────────────────┐    ┌──────────────┐
│ contextEngine│    │  memory slot   │    │   hooks      │
│claw-core-eng │    │   galaxyos     │    │ 9 lifecycle  │
│   ingest     │    │  search/write  │    │ before/after │
│   assemble   │    │  flushPlan     │    │  tool/compact│
│   compact    │    │  artifacts     │    │  agent_reply │
└──────┬───────┘    └────────┬───────┘    └──────┬───────┘
       │                     │                    │
       └─────────────────────┼────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────┐
│              GalaxyOS v8.6.0 — 8 大子系统                      │
├─────────────────────────────────────────────────────────────┤
│  1. 液态神经核心 (LTC/CfC/NCP/SSM)                          │
│  2. DAG 上下文 (SQLite + 摘要回溯 + 时间衰减)                │
│  3. COSPLAY 适配 (边界检测 + 合约学习 + 毕业)                │
│  4. LFM 技能库 (ProtoSkill → Skill + 5 维评分)              │
│  5. R-CCAM 认知循环 (5 阶段 + 元认知调节)                    │
│  6. MultiAgent 编排 (5 角色 + 公告板 + 蒸馏)                 │
│  7. 防幻觉 10 重检测 (Self-RAG/CRAG/CoVe)                    │
│  8. Rust 加速 (PIL + 向量 + LFM UDS 跨平台)                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔌 必需的配置

⚠️ **必须**在 `~/.openclaw/openclaw.json` 中手动指定插槽：

```json
{
  "plugins": {
    "slots": {
      "contextEngine": "claw-core-engine",
      "memory": "galaxyos"
    },
    "entries": {
      "claw-core-engine": { "enabled": true },
      "galaxyos": { "enabled": true }
    }
  }
}
```

不配置的话默认走 `legacy` + `memory-core`，GalaxyOS 全部能力不生效。

---

## 📊 9 个生命周期钩子

| 钩子 | 触发时机 | 用途 |
|------|----------|------|
| `gateway_start` | Gateway 启动 | 注册 lane type / heartbeat / cron |
| `gateway_stop` | Gateway 停止 | 统一关闭所有组件 |
| `before_tool_call` | 工具调用前 | 记录调用前状态给 BoundaryDetector |
| `after_tool_call` | 工具调用后 | 幂等捕获结果，更新 Skill Bank + engram + DAG |
| `before_compaction` | 压缩前 | 高价值上下文持久化到 engram + DAG |
| `after_compaction` | 压缩后 | 向量索引同步 |
| `before_agent_reply` | Agent 回复前 | 异步触发 R-CCAM 认知循环（fire-and-forget） |
| `agent_end` | Agent 回复后 | L0 日志 + 关键词追踪 + 持久化记忆 |
| `before_prompt_build` | 提示构建前 | R-CCAM 注入 + 动态锚定 + 记忆验证 |

---

## 🛠️ 15 个工具（含 policy 声明）

| 工具 | 用途 | 频道 | 角色 | Rate Limit |
|------|------|------|------|------------|
| `galaxy_pool` | GalaxyPool 状态查询 | dm+group | owner+member | 30/min |
| `claw_rccam_progress` | R-CCAM 实时进度 | dm+group | owner+member | 60/min |
| `claw_recall` | 深度语义记忆检索 | dm | owner+member | 60/min |
| `claw_lobster` | Lobster 管道执行 | dm | owner | 20/min |
| `claw_health` | 系统健康检查 | dm+group | owner+member | 30/min |
| `claw_vector_info` | 向量计算能力 | dm+group | owner+member | 30/min |
| `claw_events` | 事件日志查询 | dm | owner+member | 60/min |
| `claw_store` | 记忆存储 | dm | owner | 30/min |
| `claw_verify` | 幻觉验证 | dm+group | owner+member | 30/min |
| `claw_rccam` | R-CCAM 认知循环 | dm | owner+member | 20/min |
| `claw_save_memory` | 记忆持久化 | dm | owner | 30/min |
| `claw_compile_skill` | Skill 编译（SkVM） | dm | owner | 10/min |
| `claw_asset_search` | KnowledgeAsset 搜索 | dm+group | owner+member | 60/min |
| `claw_asset_register` | KnowledgeAsset 注册 | dm | owner | 20/min |
| `claw_node_invoke` | Node 外设调用 | dm | owner | 10/min |

---

## 🔒 安全模型

### 4 层防护

1. **工具策略（policy）** — 14 工具全部声明 channels/roles/rateLimit，OpenClaw 策略层自动拦截
2. **Skill Bank 合约扫描** — 毕业前 `injection_scanner.py` 3 级检测：
   - 高风险（≥0.8）：隔离不毕业
   - 中风险（0.5-0.8）：进入人工审核队列
   - 低风险（<0.5）：放行监控
3. **Channel 感知** — 群聊场景记忆写入降级为只读（`allowMemoryWrite(channel)` 守卫）
4. **结构化 Session Key** — `workspace:channel:userId` 格式，不同 channel 记忆完全隔离

---

## 🚀 快速开始

```bash
# 1. 克隆
git clone https://cnb.cool/llm-memory-integrat/GalaxyOS.git

# 2. 装依赖
pip install -r requirements.txt
cd extensions/galaxyos && pnpm install && cd ../..

# 3. 编辑 openclaw.json（必须步骤）
# 添加 slots.contextEngine + slots.memory 配置

# 4. 重启 Gateway
supervisorctl restart openclaw-gateway

# 5. 验证
python3.12 -m galaxyos.scripts.install_wizard --check
openclaw doctor
```

---

## 📈 版本历史

### v8.6.0 (2026-06-28) — OpenClaw 深度集成改造

**Phase 1 核心断链修复**：
- 5 个新生命周期钩子（before/after_tool_call, before/after_compaction, gateway_start）
- buildStructKey 结构化 session key + idempotencyCache 幂等层
- galaxyos-hot lane 声明 + 5 秒负载上报

**Phase 2 安全与隔离加固**：
- 14 工具全部增加 policy 字段
- 新建 injection_scanner.py（3 级风险检测 + 审核队列 + 来源追溯）
- 群聊场景记忆写入只读降级

**Phase 3 系统对齐**：
- COSPLAY 毕业输出 SKILL.md 格式（含 YAML frontmatter）
- Heartbeat(30min) + Cron(每日03:00) 对接
- MultiAgent 映射为 OpenClaw Sub-Agent
- ACP 3 个调试端点（DAG 可视化 / engram 检查 / Skill Bank 状态）

**Phase 4 生态融合**：
- claw_node_invoke 工具对接 Node 系统
- clawhub.json 发布清单 + progressive disclosure
- 9 技能元数据 ≤97 字符，token 开销降低 65%

**Rust 跨平台**：
- lfm_server.rs 条件编译：Unix=UDS / Windows=TCP localhost
- Python 客户端跨平台适配
- Makefile 4 目标交叉编译（Linux/Windows × x64/ARM64）
- 预编译包打包与自动安装

**安装向导增强**：
- 检查 slots.contextEngine + slots.memory 配置
- 给出完整 openclaw.json 配置示例
- 给出 OpenClaw 文档指引

### v8.5.3 (2026-06-25) — MultiAgent P1+P2 全量

### v8.5.2 (2026-06-25) — LFM UDS v2 全量集成

### v8.4.2 (2026-06-23) — enhanced_recall 8 阶段 + SkillGraph

---

## 📦 文件结构

```
GalaxyOS/
├── extensions/galaxyos/
│   ├── index.js                 # 主插件（~5200 行）— 9 钩子 / 15 工具 / 2 插槽
│   ├── openclaw.plugin.json     # 插件契约
│   ├── clawhub.json             # ClawHub 发布清单
│   ├── scripts/                 # Python 脚本（~140 个）
│   │   ├── claw_worker.py       # 主 Worker
│   │   ├── injection_scanner.py # Skill Bank 内容扫描器
│   │   ├── lfm_skill_bank.py    # LFM 技能库（含 SKILL.md 输出）
│   │   ├── cosplay_context_adapter.py
│   │   ├── multi_agent_orchestrator.py  # 含 Sub-Agent 适配
│   │   ├── dag_context_manager.py
│   │   ├── importance_scorer.py
│   │   └── ...
│   ├── native/                  # Rust 跨平台原生扩展
│   │   ├── src/bin/lfm_server.rs  # Unix/Windows 条件编译
│   │   ├── Cargo.toml
│   │   └── .cargo/config.toml
│   └── package.json
├── galaxyos/                    # 统一 Python 包
│   ├── engine/                  # 引擎模块
│   ├── privileged/              # 特权模块
│   │   └── acp_server.py        # 含 3 个调试端点
│   └── ...
├── skills/                      # 技能库（60+ 个）
├── libs/                        # 预编译包（hnswlib/mkl/tbb/galaxyos_native）
├── tests/                       # 测试套件（37 文件 / 137 用例）
├── requirements.txt
├── pyproject.toml
├── Makefile                     # 含跨平台编译目标
├── SKILL.md                     # 本文件
├── README.md
├── CHANGELOG.md
└── VERSION                      # 8.6.0
```

---

## 🔗 相关链接

- **仓库**：https://cnb.cool/llm-memory-integrat/GalaxyOS
- **OpenClaw 文档**：
  - [Context Engine](https://docs.openclaw.ai/concepts/context-engine)
  - [Memory](https://docs.openclaw.ai/concepts/memory)
- **ClawHub**：`extensions/galaxyos/clawhub.json`

---

*GalaxyOS — 让 OpenClaw 拥有液态记忆与技能自演化能力*
