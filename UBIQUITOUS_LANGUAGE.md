# Ubiquitous Language

## 认知架构

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **GalaxyOS** | 认知增强型 AI Agent 桌面引擎，项目主品牌 | OpenClaw, xiaoyi, 小义 |
| **Cognitive Nexus** | GalaxyOS 版本代号 | — |
| **Liquid Neural Memory** | GalaxyOS 三层记忆系统（Engram → Neural → Synapse） | 液态记忆, LNM |
| **Engram** | 记忆第一层：N-gram 嵌入 O(1) 条件查找 | — |
| **Neural** | 记忆第二层：LTC/CfC/NCP 液态神经网络 | — |
| **Synapse** | 记忆第三层：突触网络传播 (ActivationSpreader) | — |
| **R-CCAM** | 五阶段认知循环：Retrieval → Cognition → Control → Action → Memory | R-CCAM Loop, 认知循环 |
| **DAG Fusion** | DAG 上下文融合引擎，摘要节点回溯 + 时间衰减排序 | DAG Context, 上下文树 |
| **DAGNode** | DAG 上下文节点，携带 session_key 和 SummaryChain | — |
| **SummaryChain** | 摘要节点回溯链 | — |

## Agent 运行时

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **openJiuwen** | 第三方 Agent 执行内核库，提供 DeepAgent、Rails、PermissionEngine | openjiuwen, agent-core |
| **AgentCore** | openJiuwen agent-core 运行时，进程内调用（非独立服务） | agentcore |
| **DeepAgent** | openJiuwen 深度 Agent 实例 | — |
| **ReActAgent** | ReAct 推理型 Agent | — |
| **WorkflowAgent** | 工作流型 Agent | — |

## MCP 工具

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **MCP Server** | 基于 FastMCP 的认知增强工具服务 (端口 8765) | — |
| **GalaxyOSMCPServer** | MCP Server 实现类 | — |
| **claw_recall** | MCP 工具：液态神经记忆检索 | — |
| **claw_store** | MCP 工具：记忆写入 | — |
| **claw_verify** | MCP 工具：声明验证 (Self-RAG + CRAG + CoVe) | — |
| **claw_rccam** | MCP 工具：R-CCAM 五阶段认知循环执行 | — |
| **claw_health** | MCP 工具：统一健康检查 (L1-L17 层 + worker tier) | — |
| **tokui_render** | MCP 工具：TokUI 流式富 UI 渲染 | — |
| **skill_execute** | MCP 工具：技能执行 | — |

## 技能系统

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **Skill** | 可执行的能力单元，由 SKILL.md 定义 | 技能 |
| **SKILL.md** | mattpocock/skills 格式的技能定义文件 | — |
| **SkillExecutor** | 技能步骤解析和执行驱动器 | — |
| **SkillState** | 技能状态机 (discovered → loading → parsing → ready → executing → completed/failed) | — |
| **leading_words** | 技能触发关键词 | — |
| **completion_criterion** | 步骤完成判定标准 | — |

## 安全护栏

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **Rails** | Agent 行为约束和安全检查 | 护栏 |
| **SecurityRail** | 安全护栏 | — |
| **PermissionEngine** | 安全审批引擎 | — |
| **HITL** | Human-in-the-Loop 人机协同确认机制 | 确认中断 |

## 渲染系统

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **TokUI** | GalaxyOS 统一回复格式化器 / DSL 渲染框架 | tokui |
| **TokUI DSL** | TokUI 声明式 UI 描述语言，格式 `[type attrs]content[/type]` | — |
| **EUI-NEO** | C++ 原生 UI 渲染框架，通过 add_subdirectory 源码集成到 C++ Desktop Shell | eui_neo |
| **DSLBridge** | TokUI DSL ↔ EUI-NEO DSL 双向转换引擎 | — |
| **RenderChannel** | 渲染通道：eui_native / webview_dom / plain_text | — |
| **RenderSurface** | EUI-NEO 渲染表面 | — |
| **RenderChannelRouter** | 三级降级链路由器，降级不可逆 | — |
| **SSE /agent-chat** | MCP Server SSE 端点，替代 Gateway WebSocket (端口 8765) | — |
| **SpringAnimationEngine** | Apple 设计规范弹簧动画引擎 | — |
| **RubberBandEffect** | Apple 设计规范橡皮筋回弹效果 | — |
| **DirectManipulation** | Apple 设计规范直接操控手势 | — |
| **MaterialDepth** | Apple 设计规范材质深度层次 | — |

## C++ Desktop Shell

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **C++ Desktop Shell** | C++ 原生桌面壳层，基于 GLFW + EUI-NEO GPU 直渲 + CMake/CPack 打包 | Tauri 桌面壳, Rust 桌面壳 |
| **NativeIPCChannel** | C++ Desktop Shell 中的 HTTP IPC 通道模块，基于 cpp-httplib | Tauri IPC |
| **NativeProcessManager** | C++ Desktop Shell 中的 Python 子进程管理模块 | — |
| **NativeEventBus** | C++ Desktop Shell 中的发布-订阅事件总线模块 | — |
| **NativeSSEClient** | C++ Desktop Shell 中的 SSE 协议客户端模块（无锁 dispatch） | — |
| **NativeLogger** | C++ Desktop Shell 中的 JSON 结构化日志模块 | — |
| **NativeConfig** | C++ Desktop Shell 中的配置管理模块（JSON） | — |
| **I18nBridge** | C++ Desktop Shell 中的 i18n 翻译桥接模块 | — |
| **GalaxyOSApp** | EUI-NEO 应用入口、UI 构建、生命周期管理 | — |

## 记忆操作

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **write** | 写入记忆到三层存储 | — |
| **recall** | 从记忆中检索 | — |
| **consolidate** | 记忆巩固（含艾宾浩斯遗忘曲线衰减） | — |
| **dual_write** | 同步写液态记忆 + 异步写 agent-core 上下文 | 双写 |
| **dream_mode_sync** | Dream Mode 协同（记忆巩固 + 遗忘曲线） | — |

## 检索增强

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **CRAG** | 校正检索增强生成 | — |
| **GraphRAG** | 图检索增强生成 | — |
| **Self-RAG** | 自反思检索增强 | — |
| **CoVe** | 链式验证 (Chain of Verification) | — |
| **COSPLAY** | 增强上下文压缩 | — |

## 部署

| 术语 | 定义 | 应避免的别名 |
|------|------|-------------|
| **desktop** | 桌面端运行模式 | — |
| **plugin** | 插件运行模式 | — |
| **GALAXYOS_HOME** | GalaxyOS 根目录环境变量 | — |
| **OPENCLAW_HOME** | 历史遗留根目录环境变量（向后兼容别名） | — |

## 关系

- 一个 **GalaxyOS** 实例启动 2 个后端进程：**MCP Server**、**AgentCore**（进程内调用）
- **R-CCAM** 在 Retrieval 阶段调用 **Liquid Neural Memory** 检索和 **DAG Fusion** 组装
- **Liquid Neural Memory** 通过 **dual_write** 与 openJiuwen 上下文双写
- **TokUI** 通过 **DSLBridge** 转换为 **EUI-NEO** DSL，由 **RenderChannelRouter** 路由渲染通道
- **EUI-NEO** 通过 add_subdirectory 源码集成到 **C++ Desktop Shell**，由 **GalaxyOSApp** 管理生命周期
- **C++ Desktop Shell** 通过 **NativeIPCChannel** 与 Python **MCP Server** 通信，通过 **NativeSSEClient** 接收 SSE 流
- **C++ Desktop Shell** 通过 **NativeProcessManager** 管理 Python 子进程启停和健康检查
- **MCP Server** 提供 SSE **/agent-chat** 端点，替代原 Gateway WebSocket
- **SpringAnimationEngine** 实现 Apple 设计规范的弹簧动画、橡皮筋、直接操控、材质深度

## 示例对话

> **Dev:** "当 **R-CCAM** 执行 Retrieval 阶段时，它查的是 **Engram** 还是整个 **Liquid Neural Memory**？"
>
> **Domain expert:** "默认查全部三层——**Engram** 做 O(1) 条件查找，**Neural** 做液态网络推理，**Synapse** 做激活传播。但 `claw_recall` 可以通过 `memory_type` 参数指定只查某一层。"
>
> **Dev:** "那 **DAG Fusion** 和 **Liquid Neural Memory** 是什么关系？"
>
> **Domain expert:** "它们是两个独立的系统。**Liquid Neural Memory** 存原始记忆，**DAG Fusion** 存对话上下文节点。**MemorySyncBridge** 负责 **dual_write**——写记忆的同时创建 **DAGNode**，这样 **R-CCAM** 在 Retrieval 阶段可以同时拿到记忆和上下文。"
>
> **Dev:** "如果 **EUI-NEO** 的 FFI 调用超时了怎么办？"
>
> **Domain expert:** "**RenderChannelRouter** 会降级到 **webview_dom** 通道。如果 WebView 也失败，最终降级到 **plain_text**。**TokUIDegradationManager** 管理整个降级链。"

## 标记的歧义

- **"claw" 前缀**（`claw_recall` 等）来自历史品牌 OpenClaw，现统一归入 GalaxyOS 认知工具。运行时保持兼容，新工具不再使用 claw 前缀。
- **"workspace"** 同时指 GalaxyOS 工作空间目录、openJiuwen Agent 的 workspace 参数、MCP 工具的 workspace_id——三者语义不同但共享同一标识符。
- **"kernel"** 在 GalaxyOS 中指 `galaxyos/kernel/` 目录（认知内核），在 `claw_health` 中指 L1-L17 层，在 C++ Desktop Shell 降级中指 Python 内核进程。
- **"agent"** 同时指 openJiuwen DeepAgent、ReActAgent/WorkflowAgent、TokUI agent 组件——需根据上下文区分。
- **"memory"** 同时指 Liquid Neural Memory 三层系统、MemoryScope.liquid_neural / agent_core、计算机内存——需根据上下文区分。
- **"Engine"** 同时指 galaxyos/engine/ 目录、_FallbackReActEngine、ContextEngine、PlanningEngine、ReflectionEngine——需加限定词。
