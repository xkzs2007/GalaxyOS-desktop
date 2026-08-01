---
name: galaxyos
description: GalaxyOS 认知增强型 AI Agent 桌面引擎 v0.3.0 Cognitive Nexus
author: 2997417176
license: MIT
tags: [context-engine, memory, llm, dag, rccam, cosplay, progressive-disclosure, desktop, eui-neo, mcp]
---

# GalaxyOS v0.3.0 Cognitive Nexus

> **定位**：GalaxyOS 认知增强型 AI Agent 桌面引擎 v0.3.0 Cognitive Nexus
> **架构**：C++ 桌面壳 (EUI-NEO GPU 直渲) + GalaxyOS Python 内核 (MCP Server + AgentCore Bridge)
> **最新特性**：v0.3.0 双进程架构落地 + EUI-NEO 原生渲染 + 三级渲染降级链

---

## 🎯 核心能力

GalaxyOS 作为独立桌面引擎，采用双进程架构运行：

| 进程 | 技术栈 | 职责 |
|------|--------|------|
| **C++ 桌面壳** | EUI-NEO C++ SDK + GLFW + OpenGL | GPU 直渲、窗口管理、IPC、SSE、托盘 |
| **Python 内核** | MCP Server + openJiuwen agent-core | 认知引擎、记忆系统、技能编排、工具服务 |

### 7 大能力模块

1. **液态神经记忆** — LTC 突触 / CfC 推理 / NCP 神经电路策略 / SSM 状态预测 / 仿生遗忘曲线
2. **DAG 上下文管理** — SQLite 持久化 / 摘要节点回溯 / 时间衰减排序 / 多粒度优先级
3. **COSPLAY 技能自演化** — 从执行轨迹学习技能合约 → ProtoSkill → 毕业为成熟 Skill
4. **EUI-NEO 原生渲染** — C++ GPU 直渲 / TokUI DSL → EUI-NEO 组件映射 / 流式增量构建
5. **R-CCAM 认知循环** — Retrieval → Cognition → Control → Action → Memory 五阶段结构化
6. **MultiAgent 协同** — 5 角色（searcher/analyst/architect/critic/summarizer）+ 公告板 + 蒸馏 + 交叉验证
7. **三级渲染降级** — `eui_native` (EUI-NEO GPU 直渲) → `webview_dom` → `plain_text`（不可逆）

---

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────────────┐
│              C++ Desktop Shell (EUI-NEO GPU 直渲)            │
│  GLFW 窗口 / OpenGL 渲染 / NativeIPCChannel / NativeSSE     │
│  NativeProcessManager / NativeEventBus / I18nBridge         │
└─────────────────────────────────────────────────────────────┘
                              │
                   HTTP IPC (cpp-httplib)
                   SSE (:8765)
                              │
┌─────────────────────────────────────────────────────────────┐
│              GalaxyOS Python 内核 — MCP Server               │
├─────────────────────────────────────────────────────────────┤
│  MCP Server 入口 (mcp_server_entry)                          │
│  AgentCore Bridge (openJiuwen agent-core @v0.1.16)          │
│  DSL Bridge (TokUI DSL → EUI-NEO 渲染指令)                   │
├─────────────────────────────────────────────────────────────┤
│  1. 液态神经核心 (LTC/CfC/NCP/SSM)                          │
│  2. DAG 上下文 (SQLite + 摘要回溯 + 时间衰减)                │
│  3. COSPLAY 适配 (边界检测 + 合约学习 + 毕业)                │
│  4. R-CCAM 认知循环 (5 阶段 + 元认知调节)                    │
│  5. MultiAgent 编排 (5 角色 + 公告板 + 蒸馏)                 │
│  6. 防幻觉 10 重检测 (Self-RAG/CRAG/CoVe)                    │
│  7. ONNX Embedding (bge-small-zh-v1.5-ONNX)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔌 必需的配置

配置文件位于 `$GALAXYOS_HOME/config/` 目录，核心配置项：

```json
{
  "mode": "desktop",
  "mcp_server": {
    "host": "127.0.0.1",
    "port": 8765
  },
  "rendering": {
    "fallback_chain": ["eui_native", "webview_dom", "plain_text"]
  },
  "agent_core": {
    "backend": "openjiuwen",
    "version": "0.1.16"
  }
}
```

环境变量 `GALAXYOS_HOME` 指向 GalaxyOS 数据目录，未设置时默认 `~/.galaxyos/`。

---

## 📊 生命周期

| 阶段 | 触发时机 | 用途 |
|------|----------|------|
| `mcp_server_start` | MCP Server 启动 | 初始化引擎、注册工具、启动 SSE |
| `mcp_server_stop` | MCP Server 停止 | 统一关闭所有组件、持久化状态 |
| `agent_core_init` | agent-core 初始化 | 加载记忆系统、技能库、认知循环 |
| `agent_core_shutdown` | agent-core 关闭 | 保存记忆、flush engram、关闭连接 |
| `before_tool_call` | 工具调用前 | 记录调用前状态给 BoundaryDetector |
| `after_tool_call` | 工具调用后 | 幂等捕获结果，更新 Skill Bank + engram + DAG |
| `before_compaction` | 压缩前 | 高价值上下文持久化到 engram + DAG |
| `after_compaction` | 压缩后 | 向量索引同步 |
| `render_fallback` | 渲染降级 | eui_native → webview_dom → plain_text 逐级降级 |

---

## 🛠️ 20 个 MCP 工具

| 工具 | 用途 |
|------|------|
| `galaxy_pool` | GalaxyPool 状态查询 |
| `galaxy_rccam_progress` | R-CCAM 实时进度 |
| `galaxy_recall` | 深度语义记忆检索 |
| `galaxy_health` | 系统健康检查 |
| `galaxy_vector_info` | 向量计算能力 |
| `galaxy_events` | 事件日志查询 |
| `galaxy_store` | 记忆存储 |
| `galaxy_verify` | 幻觉验证 |
| `galaxy_rccam` | R-CCAM 认知循环 |
| `galaxy_save_memory` | 记忆持久化 |
| `galaxy_compile_skill` | Skill 编译（SkVM） |
| `galaxy_asset_search` | KnowledgeAsset 搜索 |
| `galaxy_asset_register` | KnowledgeAsset 注册 |
| `galaxy_dag_status` | DAG 上下文状态 |
| `galaxy_dag_query` | DAG 节点查询 |
| `galaxy_embedding_status` | ONNX Embedding 状态 |
| `galaxy_render_info` | 渲染引擎信息（降级链状态） |
| `galaxy_config_get` | 配置读取 |
| `galaxy_config_set` | 配置更新 |
| `galaxy_skill_list` | 技能库列表 |

---

## 🔒 安全模型

### openJiuwen Rails + PermissionEngine

1. **openJiuwen Rails** — agent-core 内置安全护栏，输入/输出双向过滤，工具调用权限校验
2. **PermissionEngine** — 细粒度工具权限声明，读写分离，敏感操作需显式授权
3. **Skill Bank 合约扫描** — 毕业前 `injection_scanner.py` 3 级检测：
   - 高风险（≥0.8）：隔离不毕业
   - 中风险（0.5-0.8）：进入人工审核队列
   - 低风险（<0.5）：放行监控
4. **结构化 Session Key** — `workspace:channel:userId` 格式，不同会话记忆完全隔离

---

## 🚀 快速开始

```bash
# 1. 克隆
git clone https://github.com/openJiuwen-ai/GalaxyOS.git

# 2. 安装 Python 依赖
pip install -r requirements-core.txt
pip install "openjiuwen @ git+https://github.com/openJiuwen-ai/agent-core@v0.1.16"

# 3. （可选）重型依赖 — CPU
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-heavy-cpu.txt

# 3. （可选）重型依赖 — CUDA
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements-heavy-cuda.txt

# 4. 启动 Python 内核
GALAXYOS_MODE=desktop python -m galaxyos.kernel.mcp_server_entry

# 5. （可选）构建 C++ 桌面壳
cmake -B desktop-native/build -S desktop-native
cmake --build desktop-native/build --config Release

# 6. 验证
python -m pytest tests/ -x -q --tb=short
```

---

## 📈 版本历史

### v0.3.0 (2026-07-20) — Cognitive Nexus

**双进程架构落地**：
- C++ 桌面壳 8 模块实现（GalaxyOSApp / NativeLogger / NativeConfig / NativeEventBus / NativeIPCChannel / NativeSSEClient / NativeProcessManager / I18nBridge）
- EUI-NEO add_subdirectory 源码集成 + GPU 直渲
- HTTP IPC 通道（cpp-httplib）+ SSE 协议客户端
- Python 子进程生命周期管理（NativeProcessManager）

**渲染三级降级链**：
- eui_native (EUI-NEO GPU 直渲) → webview_dom → plain_text（不可逆）
- TokUI DSL → DSLBridge → EUI-NEO 渲染指令翻译
- 流式增量构建 API（begin_stream / create_node / update_text / end_stream）

**MCP Server + AgentCore Bridge**：
- 20 个 MCP 工具注册
- openJiuwen agent-core @v0.1.16 深度集成
- openJiuwen Rails + PermissionEngine 安全模型

**ONNX Embedding**：
- bge-small-zh-v1.5-ONNX 模型集成（拆分格式）
- CPU/CUDA 双模式支持

### v0.2.0 (2026-07-01) — 架构重构

- 桌面壳从 Web 技术栈迁移至 EUI-NEO C++ 原生
- 原生扩展统一为 Python 纯实现
- 技能库聚焦 COSPLAY 自演化方向
- 统一为 galaxyos.kernel + desktop-native 双目录结构

### v0.1.4 (2026-06-15) — 初始版本

- 液态神经记忆 + DAG 上下文 + R-CCAM 认知循环
- COSPLAY 技能自演化 + MultiAgent 协同
- 防幻觉 10 重检测

---

## 📦 文件结构

```
GalaxyOS/
├── desktop-native/               # C++ 桌面壳
│   ├── src/                      # 源码
│   │   ├── galaxyos_app.cpp      # EUI-NEO 应用入口、UI 构建、生命周期
│   │   ├── native_logger.cpp     # JSON 结构化日志
│   │   ├── native_config.cpp     # 配置管理（JSON）
│   │   ├── native_event_bus.cpp  # 发布-订阅事件总线
│   │   ├── native_ipc_channel.cpp # HTTP IPC 通道（cpp-httplib）
│   │   ├── native_sse_client.cpp # SSE 协议客户端
│   │   ├── native_process_manager.cpp # Python 子进程管理
│   │   └── i18n_bridge.cpp       # i18n 翻译桥接
│   ├── include/                  # 头文件（7 模块）
│   └── CMakeLists.txt
├── galaxyos/                     # Python 统一包
│   ├── kernel/                   # 认知内核
│   │   ├── mcp_server_entry.py   # MCP Server 入口
│   │   ├── agent_core_bridge.py  # AgentCore Bridge
│   │   └── dsl_bridge.py         # TokUI DSL → EUI-NEO 桥接
│   ├── engine/                   # 核心引擎
│   │   ├── onnx_embedding.py     # ONNX Embedding
│   │   ├── retrieval.py          # 检索引擎
│   │   └── neural/               # 神经网络（LTC/CfC/NCP/SSM）
│   └── ...
├── skills/                       # 技能库（76 个）
├── models/embeddings/            # ONNX 模型文件
├── tests/                        # 测试套件
├── docs/                         # 文档
│   ├── adr/                      # 架构决策记录
│   └── agents/                   # Agent 文档
├── requirements-core.txt         # 核心依赖
├── requirements-heavy-cpu.txt    # 重型依赖（CPU）
├── requirements-heavy-cuda.txt   # 重型依赖（CUDA）
├── pyproject.toml
├── SKILL.md                      # 本文件
├── README.md
├── CHANGELOG.md
└── VERSION                       # 0.3.0
```

---

## 🔗 相关链接

- **仓库**：https://github.com/openJiuwen-ai/GalaxyOS
- **EUI-NEO**：https://github.com/openJiuwen-ai/eui-neo
- **openJiuwen agent-core**：https://github.com/openJiuwen-ai/agent-core
- **ONNX 模型**：https://huggingface.co/onnx-community/bge-small-zh-v1.5-ONNX

---

*GalaxyOS — 认知增强型 AI Agent 桌面引擎*
