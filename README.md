# GalaxyOS Desktop

> 认知增强型桌面 AI Agent — EUI-NEO 原生渲染 + GalaxyOS 认知引擎 + Tauri 2 桌面框架
>
> **v0.3.0** · EUI-NEO 迁移版

---

## 总览

GalaxyOS Desktop 将 **GalaxyOS 认知增强引擎**（17 层架构 + 液态神经记忆 + R-CCAM + DAG 上下文）通过 **Tauri 2** 桌面框架交付，使用 **EUI-NEO C++ DSL** 原生 GPU 直渲替代 React WebView，配合 **openJiuwen agent-core** 作为桌面 Agent 运行时。

| 层级 | 框架 | 职责 |
|------|------|------|
| **桌面框架** | Tauri 2 | 跨平台桌面壳 + IPC + 原生窗口 + 资源打包 |
| **原生渲染** | EUI-NEO C++ DSL | GPU 直渲 + 弹簧动画 + 材质深度 + 三级降级 |
| **认知增强层** | GalaxyOS v8.6.0 | 17 层架构 + 液态神经记忆 + R-CCAM + DAG 上下文 + 76 技能包 |
| **Agent 运行时** | openJiuwen agent-core | DeepAgent + ReActAgent + 安全护栏 + 工作流引擎 |
| **连接协议** | MCP (streamable_http) + SSE /agent-chat | 20 个工具 + 流式认知面板 |
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
| **EUI-NEO 原生渲染** | C++ GPU 直渲 + 弹簧物理动画 + 直接操控 + 材质深度 + 橡皮筋效果 |
| **三级渲染降级** | eui_native → webview_dom → plain_text，降级不可逆 |
| **TokUI 流式渲染** | DSL 分片推送 + SSE Sidecar + 6 自定义认知面板 + 容错降级 |

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/xkzs2007/GalaxyOS-desktop.git
cd GalaxyOS-desktop

# 2. Python 依赖
pip install -r requirements-core.txt
pip install "openjiuwen>=0.1.13"
pip install "pymilvus>=2.6.2,<2.6.10"
pip install onnxruntime

# 3. 重型依赖（可选，有 GPU 用 CUDA 版）
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-heavy-cpu.txt
# 或 CUDA: pip install -r requirements-heavy-cuda.txt

# 4. 下载 ONNX 模型
python scripts/install_wizard.py --ci --target-dir models/embeddings

# 5. 桌面模式启动
GALAXYOS_MODE=desktop python -m galaxyos.kernel.mcp_server_entry
```

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Tauri 2 Desktop Shell                         │
│  (Rust IPC + EUI-NEO GPU Rendering + NSIS/DEB/AppImage)        │
└──────────────────────────┬──────────────────────────────────────┘
                           │ Tauri Commands + SSE /agent-chat
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                GalaxyOS Desktop Agent v0.3.0                     │
├─────────────────────────────────────────────────────────────────┤
│  MCP Server (20 tools)          │  Render Channel Router        │
│  ├─ 15 core tools               │  ├─ eui_native (EUI-NEO)     │
│  ├─ 4 skill tools               │  ├─ webview_dom (fallback)   │
│  └─ llm_call                    │  └─ plain_text (last resort)  │
│                                  │                                │
│  Spring Animation Engine         │  SSE Client                   │
│  ├─ 弹簧物理模型                │  ├─ /agent-chat SSE endpoint  │
│  ├─ 中断性动画                  │  ├─ TokUI DSL stream          │
│  └─ 橡皮筋效果                  │  └─ Cognitive panels          │
│                                  │                                │
│  AgentCore Bridge               │  Cognitive Core               │
│  ├─ openJiuwen agent-core       │  ├─ LiquidMemoryAdapter       │
│  ├─ DeepAgent / ReActAgent      │  ├─ DAGContextFusion          │
│  └─ Security Rails              │  ├─ RCCAMInjector             │
│                                  │  ├─ MemorySyncBridge          │
│                                  │  └─ DualRuntimeManager        │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              GalaxyOS Engine v8.6.0 — 8 大子系统                   │
│  1. 液态神经核心  2. DAG 上下文  3. COSPLAY 适配  4. LFM 技能库   │
│  5. R-CCAM 循环   6. MultiAgent  7. 防幻觉检测   8. Rust 跨平台   │
└─────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
GalaxyOS-desktop/
├── desktop-tauri/                 # Tauri 2 桌面应用
│   ├── src/                       # Rust 源码
│   │   ├── eui_neo.rs             # EUI-NEO 数据结构 + Command + DSL
│   │   ├── eui_neo_ffi.rs         # FFI 绑定层（C++ ↔ Rust）
│   │   ├── render_channel.rs      # 三级渲染降级管理
│   │   ├── spring_animation.rs    # Apple 设计规范弹簧动画
│   │   ├── sse_client.rs          # SSE 客户端
│   │   ├── tokui_renderer.rs      # TokUI 流式渲染器
│   │   └── backend.rs             # 双进程架构管理
│   ├── minimal-webview/           # WebView 降级占位页面
│   └── native_translations/       # i18n 翻译文件
├── vendor/eui-neo/sdk/            # EUI-NEO C++ SDK
│   ├── include/eui_neo_bridge.h   # C 接口头文件
│   └── src/eui_neo_bridge.cpp     # C++ 桥接实现
├── galaxyos/
│   ├── kernel/                    # 核心内核模块
│   │   ├── mcp_server_entry.py    # MCP Server 入口 + SSE /agent-chat
│   │   ├── agent_core_bridge.py   # AgentCore 桥接
│   │   ├── dsl_bridge.py          # DSL 组件映射
│   │   └── render_channel_router.py # 渲染降级路由
│   ├── engine/                    # 引擎模块
│   │   ├── onnx_embedding.py      # 本地 ONNX Embedding 服务
│   │   └── install_wizard.py      # 安装向导（--ci 模式）
│   └── skill_infra/               # 技能基础设施
├── skills/                        # 76 技能包
├── .github/workflows/             # CI/CD 工作流
├── Dockerfile.buildkit            # BuildKit 多阶段构建
├── Dockerfile.kernel              # 服务部署镜像（CPU/CUDA）
├── requirements-heavy-cpu.txt     # 重型依赖（CPU-only）
├── requirements-heavy-cuda.txt    # 重型依赖（CUDA）
└── pyproject.toml
```

## 安全模型

4 层防护：

1. **工具策略** — 20 工具全部声明 channels/roles/rateLimit
2. **Skill Bank 合约扫描** — `injection_scanner.py` 3 级检测（高/中/低风险）
3. **Channel 感知** — 群聊场景记忆写入降级为只读
4. **结构化 Session Key** — `workspace:channel:userId` 隔离

## 跨平台

- **桌面**：Windows (NSIS) / Linux (DEB + AppImage)
- **Python**：Windows (winloop) / Linux (uvloop)
- **Rust 扩展**：Linux/Windows × x64 条件编译
- **渲染降级**：EUI-NEO GPU → WebView DOM → 纯文本

## 版本历史

### v0.3.0 (2026-07-18) — EUI-NEO 迁移版

**EUI-NEO 原生渲染替代 React WebView**：C++ GPU 直渲 + 弹簧物理动画 + 三级降级链

**双进程架构**：Tauri 2 桌面壳 + MCP Server（移除 JiuwenSwarm Gateway/AgentServer）

**SSE 通信**：/agent-chat SSE 端点替代 Gateway WebSocket

**CI/CD 统一构建**：Tauri bundler + BuildKit 多阶段 + GHCR 容器 + Chocolatey

**FFI 安全修复**：C++ 异常捕获 + malloc 替代悬垂指针 + eui_neo_free_response

### v0.2.0 (2026-07-17) — JiuwenSwarm 集成版

JiuwenSwarm 替换 Agent Studio + MCP 工具精简 + 生命周期钩子重构

### v0.1.4 (2026-07-11) — Desktop Agent 首发版

Agent Studio 平台集成 + 认知增强内核 + TokUI 流式富 UI

## 生态

- **[openJiuwen agent-core](https://github.com/openJiuwen-ai/agent-core)** — Agent 运行时 SDK（v0.1.16+）
- **[EUI-NEO](https://github.com/sudoevolve/EUI-NEO)** — C++ GPU 直渲 UI 框架
- **[TokUI](https://www.npmjs.com/package/@jboltai/tokui)** — 零依赖流式 UI 框架
- **[Tauri 2](https://v2.tauri.app/)** — Rust 跨平台桌面框架

## 开发

| 资源 | 说明 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [CONTEXT.md](CONTEXT.md) | 领域上下文文档 |
| [UBIQUITOUS_LANGUAGE.md](UBIQUITOUS_LANGUAGE.md) | 统一语言词汇表 |
| [docs/adr/](docs/adr/) | 架构决策记录 |

## 许可证

MIT License — 详见 [LICENSE](LICENSE)
