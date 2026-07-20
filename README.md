# GalaxyOS Desktop

> 认知增强型桌面 AI Agent — EUI-NEO C++ 原生渲染 + GalaxyOS 认知引擎
>
> **v0.3.0** · EUI-NEO 迁移版

---

## 总览

GalaxyOS Desktop 将 **GalaxyOS 认知增强引擎**（17 层架构 + 液态神经记忆 + R-CCAM + DAG 上下文）通过 **C++ 原生桌面壳**交付，使用 **EUI-NEO C++ DSL** 原生 GPU 直渲替代 React WebView，配合 **openJiuwen agent-core** 作为桌面 Agent 运行时。

| 层级 | 框架 | 职责 |
|------|------|------|
| **桌面框架** | C++ Native (GLFW + CPack) | 跨平台桌面壳 + HTTP IPC + 原生窗口 + NSIS/DEB 打包 |
| **原生渲染** | EUI-NEO C++ DSL | GPU 直渲 + 弹簧动画 + 材质深度 + 三级降级 |
| **认知增强层** | GalaxyOS v8.6.0 | 17 层架构 + 液态神经记忆 + R-CCAM + DAG 上下文 + 76 技能包 |
| **Agent 运行时** | openJiuwen agent-core | DeepAgent + ReActAgent + 安全护栏 + 工作流引擎 |
| **连接协议** | MCP (streamable_http) + SSE /agent-chat | 20 个工具 + 流式认知面板 |
| **流式渲染** | TokUI (@jboltai/tokui) | DSL 流式推送 + 6 自定义认知组件 + MCP Server SSE |

## 核心能力

| 能力 | 说明 |
|------|------|
| **液态神经记忆** | LTC 突触 + CfC 推理 + NCP 神经电路 + 仿生遗忘曲线 + 三层记忆架构 |
| **DAG 上下文** | SQLite 持久化 + 摘要节点回溯 + 时间衰减排序 + 上下文融合层 |
| **R-CCAM 认知循环** | Retrieval→Cognition→Control→Action→Memory 五阶段 + TokUI 进度渲染 |
| **COSPLAY 自演化** | 从执行轨迹学习技能合约 → ProtoSkill → 成熟 Skill |
| **76 技能包** | mattpocock/skills 格式 + SkillExecutor 驱动 + 状态机管理 |
| **MultiAgent 协同** | 5 角色 + 公告板 + Judge 蒸馏 + 交叉验证 |
| **防幻觉 10 重检测** | Self-RAG / CRAG / CoVe + 10 重验证链 |
| **MCP 工具协议** | 20 个工具（15 核心 + 4 技能管理 + 1 LLM 路由）+ policy 声明 |
| **EUI-NEO 原生渲染** | C++ GPU 直渲 + 弹簧物理动画 + 直接操控 + 材质深度 + 橡皮筋效果 |
| **三级渲染降级** | eui_native → webview_dom → plain_text，降级不可逆 |
| **TokUI 流式渲染** | DSL 分片推送 + MCP Server SSE + 6 自定义认知面板 + 容错降级 |

## 快速开始

```bash
# 1. 克隆
git clone https://github.com/xkzs2007/GalaxyOS-desktop.git
cd GalaxyOS-desktop

# 2. Python 依赖
pip install -r requirements-core.txt
pip install "openjiuwen @ git+https://github.com/openJiuwen-ai/agent-core@v0.1.16"
pip install onnxruntime

# 3. 重型依赖（可选，有 GPU 用 CUDA 版）
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-heavy-cpu.txt
# 或 CUDA: pip install -r requirements-heavy-cuda.txt

# 4. 下载 ONNX 模型（CI 工作流自动下载，见 .github/workflows/）
# 手动下载: 从 onnx-community/bge-small-zh-v1.5-ONNX 获取 model.onnx + tokenizer.json

# 5. 桌面模式启动
GALAXYOS_MODE=desktop python -m galaxyos.kernel.mcp_server_entry
```

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    C++ Native Desktop Shell                      │
│  (GLFW + EUI-NEO GPU Rendering + CPack NSIS/DEB)               │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP IPC + SSE /agent-chat
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
│              GalaxyOS Engine v0.3.0 — 8 大子系统                   │
│  1. 液态神经核心  2. DAG 上下文  3. COSPLAY 适配  4. ONNX 嵌入引擎   │
│  5. R-CCAM 循环   6. MultiAgent  7. 防幻觉检测   8. MCP 工具协议     │
└─────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
GalaxyOS-desktop/
├── desktop-native/                # C++ 桌面壳（EUI-NEO GPU 直渲）
│   ├── src/                       # C++ 源码
│   │   ├── galaxyos_native_app.cpp # 主入口、生命周期
│   │   ├── native_window_manager.cpp # GLFW 窗口管理
│   │   ├── native_render_engine.cpp # EUI-NEO 渲染引擎
│   │   ├── native_event_bus.cpp    # 发布-订阅事件总线
│   │   ├── native_ipc_channel.cpp  # HTTP IPC 通道
│   │   ├── native_sse_client.cpp   # SSE 协议客户端
│   │   ├── native_process_manager.cpp # Python 子进程管理
│   │   ├── eui_neo_ffi_wrapper.cpp # EUI-NEO FFI 安全包装
│   │   ├── dsl_mapping_table.cpp   # TokUI→EUI-NEO 映射
│   │   ├── i18n_bridge.cpp         # i18n 翻译桥接
│   │   └── native_tray_icon.cpp    # 系统托盘（Win32）
│   ├── include/                   # C++ 头文件
│   └── third_party/               # cpp-httplib, nlohmann/json
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
│   │   └── ...                    # 其他引擎模块
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
2. **openJiuwen Rails** — PermissionEngine 安全审批 + 行为约束
3. **Channel 感知** — 群聊场景记忆写入降级为只读
4. **结构化 Session Key** — `workspace:channel:userId` 隔离

## 跨平台

- **桌面**：Windows (NSIS/CPack) / Linux (DEB/CPack)
- **Python**：Windows (winloop) / Linux (uvloop)
- **C++ 桌面壳**：Linux/Windows × x64（GLFW + EUI-NEO GPU 直渲）
- **渲染降级**：EUI-NEO GPU → WebView DOM → 纯文本

## 版本历史

### v0.3.0 (2026-07-18) — EUI-NEO 迁移版

**EUI-NEO 原生渲染替代 React WebView**：C++ GPU 直渲 + 弹簧物理动画 + 三级降级链

**双进程架构**：C++ 原生桌面壳 + MCP Server（移除 JiuwenSwarm Gateway/AgentServer）

**SSE 通信**：/agent-chat SSE 端点替代 Gateway WebSocket

**CI/CD 统一构建**：CMake + CPack + BuildKit 多阶段 + GHCR 容器

**FFI 安全修复**：C++ 异常捕获 + malloc 替代悬垂指针 + eui_neo_free_response

### v0.2.0 (2026-07-17) — JiuwenSwarm 集成版

JiuwenSwarm 替换 Agent Studio + MCP 工具精简 + 生命周期钩子重构

### v0.1.4 (2026-07-11) — Desktop Agent 首发版

Agent Studio 平台集成 + 认知增强内核 + TokUI 流式富 UI

## 生态

- **[openJiuwen agent-core](https://github.com/openJiuwen-ai/agent-core)** — Agent 运行时 SDK（v0.1.16+）
- **[EUI-NEO](https://github.com/sudoevolve/EUI-NEO)** — C++ GPU 直渲 UI 框架
- **[TokUI](https://www.npmjs.com/package/@jboltai/tokui)** — 零依赖流式 UI 框架


## 开发

| 资源 | 说明 |
|------|------|
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [CONTEXT.md](CONTEXT.md) | 领域上下文文档 |
| [UBIQUITOUS_LANGUAGE.md](UBIQUITOUS_LANGUAGE.md) | 统一语言词汇表 |
| [docs/adr/](docs/adr/) | 架构决策记录 |

## 许可证

MIT License — 详见 [LICENSE](LICENSE)
