# GalaxyOS Desktop Agent

> 认知增强型桌面 AI Agent — Agent Studio 平台 + GalaxyOS 认知引擎 + TokUI 流式富 UI
>
> **v0.1.4** · Desktop Agent 首个完整发布

---

## 总览

GalaxyOS Desktop Agent 将 **GalaxyOS 认知增强引擎**（17 层架构 + 液态神经记忆 + R-CCAM + DAG 上下文）与 **Agent Studio**（openJiuwen Studio）平台框架深度融合，通过 MCP 协议连接，形成生产级桌面 AI Agent 产品。

| 层级 | 框架 | 职责 |
|------|------|------|
| **平台框架层** | Agent Studio | 前端 React 18 + 后端 FastAPI + 数据库 + 插件系统 + 工作流画布 |
| **认知增强层** | GalaxyOS v8.6.0 | 17 层架构 + 液态神经记忆 + R-CCAM + DAG 上下文 + 76 技能包 |
| **连接协议** | MCP (streamable_http) | 24 个工具 + 9 个生命周期钩子 |
| **流式渲染** | TokUI (@jboltai/tokui) | DSL 流式推送 + 6 自定义认知组件 + SSE Sidecar |

## 核心能力

| 能力 | 说明 |
|------|------|
| **液态神经记忆** | LTC 突触 + CfC 推理 + NCP 神经电路 + 仿生遗忘曲线 + 三层记忆架构 |
| **DAG 上下文** | SQLite 持久化 + 摘要节点回溯 + 时间衰减排序 + 上下文融合层 |
| **R-CCAM 认知循环** | Retrieval→Cognition→Control→Action→Memory 五阶段 + TokUI 进度渲染 |
| **COSPLAY 自演化** | 从执行轨迹学习技能合约 → ProtoSkill → 成熟 Skill |
| **LFM 技能库** | 5 维评分 + 合并·拆分·精修·淘汰 + 76 技能包 |
| **MultiAgent 协同** | 5 角色 + 公告板 + Judge 蒸馏 + 交叉验证 |
| **防幻觉 10 重检测** | Self-RAG / CRAG / CoVe + 10 重验证链 |
| **MCP 工具协议** | 24 个工具（15 核心 + 8 集成 + 1 tokui_render）+ policy 声明 |
| **TokUI 流式渲染** | DSL 分片推送 + SSE Sidecar + 6 自定义认知面板 + 容错降级 |
| **双模式运行** | `plugin`（OpenClaw 插件）/ `desktop`（桌面端），环境变量切换 |

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/xkzs2007/GalaxyOS-desktop.git
cd GalaxyOS-desktop

# 2. Python 依赖
pip install -r requirements.txt

# 3. 前端依赖（TokUI 组件）
cd galaxyos/frontend && npm install && cd ../..

# 4. 桌面模式启动
GALAXYOS_MODE=desktop python -m galaxyos.kernel.mcp_server_entry

# 5. 验证
python tests/verify_core_capabilities.py
```

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      Agent Studio Platform                       │
│  (React 18 + MUI 6 + Zustand + React Flow + FastAPI + Milvus)  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ MCP (streamable_http)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                GalaxyOS Desktop Agent v0.1.4                     │
├─────────────────────────────────────────────────────────────────┤
│  MCP Server (24 tools)          │  Lifecycle Hooks (9)          │
│  ├─ 15 core tools               │  ├─ gateway_start/stop        │
│  ├─ 8 integration tools         │  ├─ before/after_tool_call    │
│  └─ tokui_render                │  ├─ before/after_compaction   │
│                                  │  ├─ before_agent_reply        │
│  TokUI SSE Pipeline             │  ├─ agent_end                 │
│  ├─ PyTokUIBuilder (DSL)        │  └─ before_prompt_build       │
│  ├─ TokUISSEStreamer            │                                │
│  ├─ SSESidecar (push)           │  Cognitive Core               │
│  └─ DegradationManager          │  ├─ LiquidMemoryAdapter       │
│                                  │  ├─ DAGContextFusion          │
│  Agent Core Bridge              │  ├─ RCCAMInjector             │
│  ├─ AgentCoreBridge             │  ├─ MemorySyncBridge          │
│  ├─ SkillExecutor               │  └─ DualRuntimeManager        │
│  └─ MCPClient (3 transports)    │                                │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              GalaxyOS Engine v8.6.0 — 8 大子系统                   │
│  1. 液态神经核心  2. DAG 上下文  3. COSPLAY 适配  4. LFM 技能库   │
│  5. R-CCAM 循环   6. MultiAgent  7. 防幻觉检测   8. Rust 跨平台   │
└─────────────────────────────────────────────────────────────────┘
```

## TokUI 流式渲染

GalaxyOS Desktop Agent 集成 `@jboltai/tokui` v0.1.4 实现流式富 UI 渲染：

- **PyTokUIBuilder**：30+ 内置组件 + 6 自定义认知组件 DSL 生成器
- **TokUISSEStreamer**：DSL 分片推送 + SSE Sidecar
- **6 自定义组件**：MemoryPanel / RCCAMProgress / DAGTree / MemorySearch / RCCAMControl / DAGNodeExpand
- **8 事件处理器**：记忆检索 / R-CCAM 控制 / DAG 展开 / 主题切换等
- **容错降级**：10 种降级策略（纯文本 / 骨架屏 / 缓存回放等）

## 9 个生命周期钩子

| 钩子 | 触发 | 用途 |
|------|------|------|
| `gateway_start` | Gateway 启动 | 注册 lane type + heartbeat + cron |
| `gateway_stop` | Gateway 停止 | 统一关闭所有组件 |
| `before_tool_call` | 工具调用前 | 记录调用前状态给 BoundaryDetector |
| `after_tool_call` | 工具调用后 | 幂等捕获结果，更新 Skill Bank + engram + DAG |
| `before_compaction` | 压缩前 | 高价值上下文持久化到 engram + DAG |
| `after_compaction` | 压缩后 | 向量索引同步 |
| `before_agent_reply` | Agent 回复前 | 异步触发 R-CCAM 认知循环 |
| `agent_end` | Agent 回复后 | L0 日志 + 关键词追踪 + 持久化记忆 |
| `before_prompt_build` | 提示构建前 | R-CCAM 注入 + 动态锚定 + 记忆验证 |

## 24 个 MCP 工具

| 类别 | 工具 | 说明 |
|------|------|------|
| 核心 | `galaxy_pool` / `claw_rccam_progress` / `claw_recall` / `claw_health` / ... | 15 个核心工具 |
| 集成 | `agent_studio_query` / `agent_studio_execute` / `memory_search` / ... | 8 个 Agent Studio 集成工具 |
| TokUI | `tokui_render` | DSL 渲染 + 流式推送 + 自定义组件 |

## 目录结构

```
GalaxyOS-desktop/
├── extensions/galaxyos/
│   ├── index.js                    # OpenClaw 插件（5200+ 行，保留）
│   ├── galaxyos_agent_studio.js    # Agent Studio 插件入口
│   ├── plugin.json                 # 插件声明（含 tokui_render + 认知面板）
│   └── plugin_agent_studio.json    # Agent Studio 适配声明
├── galaxyos/
│   ├── kernel/                     # 核心内核模块
│   │   ├── mcp_server.py           # MCP Server（24 工具）
│   │   ├── mcp_client.py           # MCP Client（3 传输 + 重连）
│   │   ├── mcp_server_entry.py     # MCP Server 入口
│   │   ├── agent_core_bridge.py    # 认知增强注入层
│   │   ├── rccam_injector.py       # R-CCAM 注入器 + TokUI 融合
│   │   ├── liquid_memory_adapter.py # 液态神经记忆适配器
│   │   ├── dag_context_fusion.py   # DAG 上下文融合层
│   │   ├── memory_sync_bridge.py   # 记忆双写桥接层
│   │   ├── skill_executor.py       # 技能执行器
│   │   ├── dual_runtime_manager.py # 双运行时进程管理
│   │   ├── tokui_builder.py        # PyTokUIBuilder DSL 生成器
│   │   ├── tokui_sse_streamer.py   # TokUI SSE 流式推送
│   │   └── tokui_degradation.py    # TokUI 容错降级管理
│   ├── engine/
│   │   └── sse_sidecar.py          # SSE Sidecar 事件推送服务
│   ├── agent_studio/
│   │   ├── adapter.py              # Agent Studio 适配器
│   │   └── lifecycle.py            # 9 钩子映射管理器
│   ├── frontend/                   # TokUI React 前端组件
│   │   ├── components/
│   │   │   ├── TokUIChatRenderer.tsx
│   │   │   ├── MessageRenderer.tsx
│   │   │   ├── CognitivePanel.tsx
│   │   │   └── tokui/              # 6 自定义组件 + 事件处理器 + 主题桥接
│   │   └── sse_proxy.js            # Gateway SSE 转发端点
│   ├── skill_infra/                # 技能基础设施
│   └── mcp/                        # MCP 钩子适配器
├── skills/                         # 76 技能包
├── tests/
│   ├── verify_core_capabilities.py # 核心能力验证
│   └── test_integration.py         # 集成测试套件
├── requirements.txt
├── pyproject.toml
└── VERSION
```

## 安全模型

4 层防护：

1. **工具策略** — 24 工具全部声明 channels/roles/rateLimit
2. **Skill Bank 合约扫描** — `injection_scanner.py` 3 级检测（高/中/低风险）
3. **Channel 感知** — 群聊场景记忆写入降级为只读
4. **结构化 Session Key** — `workspace:channel:userId` 隔离

## 跨平台

- **Python**：Windows (winloop) / Linux (uvloop)
- **Rust 扩展**：Linux/Windows × x64/ARM64 条件编译
- **双模式**：`GALAXYOS_MODE=plugin`（OpenClaw）/ `GALAXYOS_MODE=desktop`（桌面端）

## 版本历史

### v0.1.4 (2026-07-11) — Desktop Agent 首发版

**Agent Studio 平台集成**：MCP 协议连接 + 24 工具 + 9 生命周期钩子 + 双运行时管理

**认知增强内核**：液态神经记忆适配器 + DAG 上下文融合 + R-CCAM 注入器 + 记忆双写桥接

**TokUI 流式富 UI**：PyTokUIBuilder + SSE Streamer + 6 自定义认知组件 + 容错降级

**前端组件**：TokUIChatRenderer + MessageRenderer + CognitivePanel + 主题桥接

### v8.6.0 (2026-06-28) — OpenClaw 深度集成改造

Phase 1-4 全量落地：9 钩子 + 15 工具 + 安全加固 + Rust 跨平台

## 生态

- **[Agent Studio](https://github.com/openJiuwen/studio)** — AI Agent 平台框架
- **[TokUI](https://www.npmjs.com/package/@jboltai/tokui)** — 零依赖流式 UI 框架
- **[OpenClaw](https://github.com/openclaw/openclaw)** — AI Assistant 框架

## 开发

| 资源 | 说明 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| `python tests/verify_core_capabilities.py` | 核心能力自检 |
| `python tests/test_integration.py` | 集成测试 |

## 许可证

MIT License — 详见 [LICENSE](LICENSE)
