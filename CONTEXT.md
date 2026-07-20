# GalaxyOS 上下文

## 产品定位

GalaxyOS 是一个**认知增强型 AI Agent 桌面引擎**（Cognitive-Enhanced AI Agent Desktop Engine），代号 "Cognitive Nexus"。它融合液态神经记忆、R-CCAM 自适应认知循环与 DAG 上下文融合引擎，为桌面用户提供增强型 AI Agent 体验。

## 核心架构

```
┌──────────────────────────────────────────────────┐
│  C++ Desktop Shell (GalaxyOSNativeApp)           │
│  ┌───────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ EUI-NEO   │  │ Native   │  │ HTTP IPC      │  │
│  │ (C++ FFI) │  │ EventBus │  │ (cpp-httplib) │  │
│  └─────┬─────┘  └────┬─────┘  └──────┬────────┘  │
│  ┌─────┴─────────────┴───────────────┴────────┐  │
│  │ NativeRenderEngine (三级降级链)              │  │
│  │ eui_native → webview_dom → plain_text       │  │
│  └─────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐ │
│  │ NativeTrayIcon + NativeProcessManager        │ │
│  └──────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────┐ │
│  │ SSE Client → TokUI Stream Renderer           │ │
│  └──────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┘
          │                │
┌─────────┼────────────────┼────────────────────────┐
│  GalaxyOS Python Kernel (9 层)                    │
│  ┌──────┴──────┐  ┌────┴──────────┐              │
│  │ MCP Server  │  │ AgentCore     │              │
│  │ :8765       │  │ Bridge        │              │
│  │ (FastMCP +  │  │ (openJiuwen   │              │
│  │  /agent-chat│  │  agent-core)  │              │
│  │   SSE)      │  │               │              │
│  └──────┬──────┘  └──────┬────────┘              │
│         │                │                        │
│  ┌──────┴────────────────┴─────────────────────┐ │
│  │ 认知内核                                     │ │
│  │ LiquidMemory / R-CCAM / DAG Fusion / TokUI   │ │
│  └──────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────┘
          │
     ┌────┴────┐
     │ GalaxyOS│
     │ MCP     │
     │ (8765)  │
     └─────────┘
```

## 双后端进程

| 进程 | 端口 | 职责 |
|------|------|------|
| GalaxyOS MCP | 8765 | 认知增强工具服务 (FastMCP) + SSE /agent-chat 端点 |
| AgentCore | — | Agent 运行时 (openJiuwen agent-core，进程内调用) |

## 9 层 Python 架构

| 层 | 名称 | 职责 |
|----|------|------|
| L1 | shared | 零依赖基础 (types, interfaces, constants, paths, sanitize) |
| L2 | init | 基础设施 (bootstrap, install_wizard, path_resolver, deployment_profile) |
| L3 | engine | 核心引擎 |
| L4 | privileged | 高性能层 |
| L5 | orchestration | 编排层 |
| L6 | workflow | 工作流层 |
| L7 | compat | 兼容层 |
| L8 | hooks | 钩子层 |
| L9 | scripts | 脚本层 |

## 关键技术决策

- **C++ 原生桌面壳** 作为桌面壳（非 Tauri/Electron），基于 GLFW + EUI-NEO GPU 直渲
- **EUI-NEO** 作为原生渲染加速层（C++ FFI，非 webview 替代）
- **FastMCP** 作为 MCP Server 实现
- **openJiuwen** 作为 Agent 执行内核（agent-core，进程内调用）
- **双渲染通道**：eui_native（原生）→ webview_dom（降级）→ plain_text（最终降级），降级不可逆
- **SSE /agent-chat** 替代 Gateway WebSocket 通信
- **Apple 设计规范**：弹簧动画、直接操控、中断性、空间一致性、材质深度、橡皮筋效果

## 术语表

详见 [UBIQUITOUS_LANGUAGE.md](../UBIQUITOUS_LANGUAGE.md)。

## 历史品牌残留

- **OpenClaw**：`OPENCLAW_HOME` 等环境变量保留为向后兼容别名
- **xiaoyi/小义**：已标记 removed，仅存于注释
- **claw** 前缀：MCP 工具名中保留（`claw_recall` 等），运行时兼容
