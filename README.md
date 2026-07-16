# GalaxyOS Desktop Agent

> 认知增强型桌面 AI Agent — JiuwenSwarm 宿主 + GalaxyOS 认知引擎 + TokUI 流式富 UI
>
> **v0.2.0** · JiuwenSwarm 集成版

---

## 总览

GalaxyOS Desktop Agent 将 **GalaxyOS 认知增强引擎**（17 层架构 + 液态神经记忆 + R-CCAM + DAG 上下文）作为 **JiuwenSwarm** Extension 注入，通过 MCP 协议 + Extension 系统双通道连接，形成生产级桌面 AI Agent 产品。

| 层级 | 框架 | 职责 |
|------|------|------|
| **桌面宿主层** | JiuwenSwarm | 前端 React 18 + AgentServer + WebSocket + Extension 系统 + 多 Agent 协作 |
| **认知增强层** | GalaxyOS v8.6.0 | 17 层架构 + 液态神经记忆 + R-CCAM + DAG 上下文 + 76 技能包 |
| **连接协议** | MCP (streamable_http) + Extension RPC | 20 个工具 + 5 个工作流事件钩子 |
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
| **MCP 工具协议** | 20 个工具（15 核心 + 4 技能管理 + 1 LLM 路由）+ policy 声明 |
| **TokUI 流式渲染** | DSL 分片推送 + SSE Sidecar + 6 自定义认知面板 + 容错降级 |
| **JiuwenSwarm Extension** | 4 RPC 方法 + 3 Hook handlers + 认知增强注入 |

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/xkzs2007/GalaxyOS-desktop.git
cd GalaxyOS-desktop

# 2. Python 依赖
pip install -r requirements.txt
pip install jiuwenswarm

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
│                      JiuwenSwarm Desktop                         │
│  (React 18 + TailwindCSS + Zustand + WebSocket + Extension)     │
└──────────────────────────┬──────────────────────────────────────┘
                           │ MCP (streamable_http) + Extension RPC
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                GalaxyOS Desktop Agent v0.2.0                     │
├─────────────────────────────────────────────────────────────────┤
│  MCP Server (20 tools)          │  Workflow Hooks (5)           │
│  ├─ 15 core tools               │  ├─ on_workflow_start         │
│  ├─ 4 skill tools               │  ├─ on_workflow_step          │
│  └─ llm_call                    │  ├─ on_workflow_end           │
│                                  │  ├─ on_tool_call              │
│  TokUI SSE Pipeline             │  └─ on_agent_reply            │
│  ├─ PyTokUIBuilder (DSL)        │                                │
│  ├─ TokUISSEStreamer            │  Cognitive Core               │
│  ├─ SSESidecar (push)           │  ├─ LiquidMemoryAdapter       │
│  └─ DegradationManager          │  ├─ DAGContextFusion          │
│                                  │  ├─ RCCAMInjector             │
│  JiuwenSwarm Integration        │  ├─ MemorySyncBridge          │
│  ├─ SwarmAgentServerBridge      │  └─ DualRuntimeManager        │
│  ├─ SwarmHookBridge             │                                │
│  ├─ GalaxyOSExtension           │  Direct Executors             │
│  └─ TokUIRendererInjector       │  ├─ SkillInfraDirectExecutor  │
│                                  │  └─ LLMRouterDirect           │
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

GalaxyOS Desktop Agent 集成 `@jboltai/tokui` v0.1.4+ 实现流式富 UI 渲染：

- **PyTokUIBuilder**：30+ 内置组件 + 6 自定义认知组件 DSL 生成器
- **TokUISSEStreamer**：DSL 分片推送 + SSE Sidecar
- **6 自定义组件**：MemoryPanel / RCCAMProgress / DAGTree / MemorySearch / RCCAMControl / DAGNodeExpand
- **8 事件处理器**：记忆检索 / R-CCAM 控制 / DAG 展开 / 主题切换等
- **容错降级**：10 种降级策略（纯文本 / 骨架屏 / 缓存回放等）

## 5 个工作流事件钩子

| 钩子 | 触发 | 用途 |
|------|------|------|
| `on_workflow_start` | 工作流启动 | R-CCAM 认知循环注入 |
| `on_workflow_step` | 工作流步骤 | 工具调用前校验 |
| `on_workflow_end` | 工作流结束 | 记忆双写 + L0 日志 |
| `on_tool_call` | 工具调用后 | 幂等捕获结果，更新 Skill Bank + engram + DAG |
| `on_agent_reply` | Agent 回复 | 认知上下文注入 + 动态锚定 |

## 20 个 MCP 工具

| 类别 | 工具 | 说明 |
|------|------|------|
| 核心 | `galaxy_pool` / `claw_rccam_progress` / `claw_recall` / `claw_health` / ... | 15 个核心工具 |
| 技能管理 | `skill_execute` / `skill_install` / `skill_discover` / `skill_compile` | 4 个 GalaxyOS 原生技能工具 |
| LLM | `llm_call` | GalaxyOS LLM Router 直接路由 |
| TokUI | `tokui_render` | DSL 渲染 + 流式推送 + 自定义组件 |

## 目录结构

```
GalaxyOS-desktop/
├── extensions/galaxyos/
│   ├── index.js                    # OpenClaw 插件（5200+ 行，保留）
│   ├── extension.py                # JiuwenSwarm Extension Python 入口
│   ├── extension.yaml              # JiuwenSwarm Extension 声明
│   ├── plugin.json                 # 插件声明（20 工具 + 5 钩子）
│   └── openclaw.plugin.json        # OpenClaw 原始插件声明
├── galaxyos/
│   ├── kernel/                     # 核心内核模块
│   │   ├── mcp_server.py           # MCP Server（20 工具）
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
│   │   ├── tokui_degradation.py    # TokUI 容错降级管理
│   │   ├── swarm_agent_server_bridge.py # JiuwenSwarm AgentServer 桥接
│   │   ├── swarm_hook_bridge.py    # JiuwenSwarm Hook 事件桥接
│   │   ├── galaxyos_extension.py   # GalaxyOS Extension 注册
│   │   ├── workflow_hook_dispatcher.py # agent-core 工作流钩子分发
│   │   ├── skill_infra_direct_executor.py # 技能直接执行器
│   │   └── llm_router_direct.py    # LLM 直接路由器
│   ├── engine/
│   │   └── sse_sidecar.py          # SSE Sidecar 事件推送服务
│   ├── frontend/                   # TokUI React 前端组件
│   │   ├── components/
│   │   │   ├── TokUIChatRenderer.tsx
│   │   │   ├── MessageRenderer.tsx
│   │   │   ├── CognitivePanel.tsx
│   │   │   └── tokui/              # 6 自定义组件 + 事件处理器 + 主题桥接
│   │   ├── sse_proxy.js            # Gateway SSE 转发端点
│   │   └── tokui_renderer_injector.py # TokUI 渲染组件注入器
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

1. **工具策略** — 20 工具全部声明 channels/roles/rateLimit
2. **Skill Bank 合约扫描** — `injection_scanner.py` 3 级检测（高/中/低风险）
3. **Channel 感知** — 群聊场景记忆写入降级为只读
4. **结构化 Session Key** — `workspace:channel:userId` 隔离

## 跨平台

- **Python**：Windows (winloop) / Linux (uvloop)
- **Rust 扩展**：Linux/Windows × x64/ARM64 条件编译
- **桌面模式**：`GALAXYOS_MODE=desktop`（默认）

## 版本历史

### v0.2.0 (2026-07-17) — JiuwenSwarm 集成版

**JiuwenSwarm 替换 Agent Studio**：轻量前端（30 deps vs 100+）+ 无登录 + 原生桌面支持 + Extension 系统

**集成层重构**：SwarmAgentServerBridge + SwarmHookBridge + GalaxyOSExtension + TokUIRendererInjector

**MCP 工具精简**：24 → 20 工具，移除 8 个 Studio 集成工具，新增 skill_compile + LLMRouterDirect

**生命周期钩子**：9 钩子 → 5 个 agent-core 工作流事件驱动

**CI/CD**：移除脆弱的 Patch 流程，直接构建 JiuwenSwarm 前端

### v0.1.4 (2026-07-11) — Desktop Agent 首发版

Agent Studio 平台集成 + 认知增强内核 + TokUI 流式富 UI

### v8.6.0 (2026-06-28) — OpenClaw 深度集成改造

Phase 1-4 全量落地：9 钩子 + 15 工具 + 安全加固 + Rust 跨平台

## 生态

- **[JiuwenSwarm](https://github.com/openJiuwen-ai/jiuwenswarm)** — 多 Agent 协作桌面宿主
- **[agent-core](https://github.com/openJiuwen-ai/agent-core)** — Agent 运行时 SDK
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
