# HLD: Agent Core 与 GalaxyOS 桌面端系统集成方案

| 字段 | 值 |
|------|-----|
| 文档版本 | v2.0 |
| 日期 | 2026-07-19 |
| 状态 | Draft |
| 目标读者 | 架构师、开发团队 |

---

## 1. 背景与目标

### 1.1 现状问题

GalaxyOS 桌面端当前存在严重的架构割裂：

1. **渲染层瘫痪**：渲染三级降级链（eui_native → webview_dom → plain_text）中仅 webview_dom 可用，EUI-NEO GPU 直渲尚未完整集成，plain_text 降级路径未实现
2. **Agent Core 集成浅层化**：`agent_core_bridge.py` 仅做了 `create_deep_agent()` 调用和 Rail 配置，未利用 `sys_operation`（文件/Shell/代码执行）、MCP 双向集成、A2A 协议等核心能力
3. **大量死代码**：Python 侧 `render_channel_router.py`、`tokui_degradation.py`、`tokui_chat_sse_client.py`、`tokui_chat_adapter.py`、`tokui_sse_streamer.py` 从未被调用
4. **定位模糊**：名为"桌面 Agent"但无任何桌面操控能力，本质是聊天壳

### 1.2 目标

基于 **路径 A（增强型桌面助手）** 定位，将 Agent Core 深度集成到 GalaxyOS 桌面端，实现：

- 文件管理、终端执行、系统信息查询等确定性桌面能力（100% 可靠）
- 通过 MCP 协议暴露桌面能力给外部 Agent 框架
- 清理死代码，统一渲染路径为三级降级链（eui_native → webview_dom → plain_text）
- 保留 EUI-NEO GPU 直渲为最高优先级渲染路径

---

## 2. 系统架构

### 2.1 整体架构图

```
┌──────────────────────────────────────────────────────────────────┐
│         C++ Native Desktop Shell (GLFW + EUI-NEO GPU + CPack)    │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ EUI-NEO      │  │ HTTP IPC     │  │ NativeProcessManager   │ │
│  │ (GPU 直渲)   │◄─┤ (cpp-httplib)│  │ (Python 子进程管理)     │ │
│  └──────┬───────┘  └──────┬───────┘  └────────────────────────┘ │
│         │                 │                                      │
│         │  NativeEventBus │                                      │
│         │                 ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │           Python 内核 (子进程, MCP Server :8765)              ││
│  │                                                              ││
│  │  ┌────────────────────────────────────────────────────────┐  ││
│  │  │         GalaxyOS MCP Server (FastMCP)                  │  ││
│  │  │                                                       │  ││
│  │  │  ┌──────────────┐  ┌─────────────────────┐           │  ││
│  │  │  │ 认知增强工具   │  │ 桌面操作工具 (新增)   │           │  ││
│  │  │  │ (15个,已有)   │  │ fs / shell / code   │           │  ││
│  │  │  └──────────────┘  └─────────────────────┘           │  ││
│  │  │  ┌──────────────┐  ┌─────────────────────┐           │  ││
│  │  │  │ 技能管理工具   │  │ 窗口/剪贴板工具(新增) │           │  ││
│  │  │  │ (4个,已有)    │  │ pygetwindow/clip    │           │  ││
│  │  │  └──────────────┘  └─────────────────────┘           │  ││
│  │  └────────────────────────────────────────────────────────┘  ││
│  │                      ▲                                       ││
│  │                      │ MCP 协议                              ││
│  │  ┌───────────────────┴─────────────────────────────────┐    ││
│  │  │          Agent Core (openjiuwen)                     │    ││
│  │  │                                                     │    ││
│  │  │  DeepAgent                                          │    ││
│  │  │  ├── ReActAgent (推理+行动循环)                      │    ││
│  │  │  ├── SysOperation (fs/shell/code)                   │    ││
│  │  │  ├── MCPTool (消费外部 MCP 工具)                    │    ││
│  │  │  ├── Rails (安全护栏 x 15+)                         │    ││
│  │  │  ├── PermissionEngine (权限审批)                    │    ││
│  │  │  └── A2A (跨框架 Agent 互操作)                      │    ││
│  │  └──────────────────────────────────────────────────────┘    ││
│  └──────────────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 模块交互边界

| 边界 | 通信机制 | 数据格式 | 方向 |
|------|---------|---------|------|
| C++ Shell ↔ Python Kernel | HTTP IPC + SSE | JSON/SSE | 双向 |
| C++ Shell ↔ Python Kernel | HTTP IPC + SSE /agent-chat (:8765) | JSON/SSE | 双向 |
| Python MCP Server ↔ Agent Core | 进程内 Python 调用 | Python 对象 | 双向 |
| Agent Core ↔ 外部 MCP Server | MCP SSE/Stdio/HTTP | JSON-RPC | 双向 |
| Agent Core ↔ 其他 Agent | A2A 协议 | JSON-RPC over HTTP | 双向 |
| Agent Core ↔ LLM | OpenAI 兼容 API | JSON | 单向出 |

### 2.3 依赖关系

```
C++ Desktop Shell (GLFW + CPack)
  ├── EUI-NEO (add_subdirectory, GPU 直渲)
  ├── cpp-httplib (HTTP IPC + SSE Client)
  ├── nlohmann/json (JSON 解析)
  └── Python Kernel (子进程, NativeProcessManager 管理)
        ├── openjiuwen (Agent Core)
        │     ├── fastmcp (MCP Server/Client)
        │     ├── openai (LLM 调用)
        │     ├── aiohttp (HTTP 客户端)
        │     └── [可选] a2a-sdk, mem0, agent-sandbox
        ├── galaxyos.engine (ONNX Embedding + 检索)
        └── galaxyos.kernel (MCP Server + Bridge)
```

---

## 3. 模块分解

### 3.1 新增模块：桌面操作工具集

基于 Agent Core 的 `SysOperation` 扩展，新增 GalaxyOS 桌面操作工具：

#### 3.1.1 文件系统工具（复用 SysOperation.fs）

| 工具名 | 说明 | 可靠性 |
|--------|------|--------|
| `desktop_fs_read` | 读取文件（支持 head/tail/行范围） | 100% |
| `desktop_fs_write` | 写入文件（支持追加/创建） | 100% |
| `desktop_fs_list` | 列出文件/目录（支持递归/过滤） | 100% |
| `desktop_fs_search` | 搜索文件（glob 模式） | 100% |
| `desktop_fs_move` | 移动/重命名文件 | 100% |
| `desktop_fs_delete` | 删除文件（带确认护栏） | 100% |

#### 3.1.2 Shell 执行工具（复用 SysOperation.shell）

| 工具名 | 说明 | 可靠性 |
|--------|------|--------|
| `desktop_shell_exec` | 执行命令（支持 PowerShell/CMD/Bash） | 100% |
| `desktop_shell_exec_stream` | 流式执行命令（实时输出） | 100% |
| `desktop_shell_background` | 后台执行命令 | 100% |

#### 3.1.3 代码执行工具（复用 SysOperation.code）

| 工具名 | 说明 | 可靠性 |
|--------|------|--------|
| `desktop_code_exec` | 执行 Python/JS 代码 | 100% |

#### 3.1.4 新增桌面操作工具（自建 MCP Tool）

| 工具名 | 说明 | 依赖 | 可靠性 |
|--------|------|------|--------|
| `desktop_clipboard_read` | 读取剪贴板 | `pyperclip` | 99% |
| `desktop_clipboard_write` | 写入剪贴板 | `pyperclip` | 99% |
| `desktop_window_list` | 列出窗口 | `pygetwindow` | 95% |
| `desktop_window_focus` | 聚焦窗口 | `pygetwindow` | 95% |
| `desktop_window_screenshot` | 窗口截图 | `Pillow` | 95% |
| `desktop_system_info` | 系统信息 | `psutil` | 100% |
| `desktop_process_list` | 进程列表 | `psutil` | 100% |
| `desktop_process_kill` | 结束进程（带护栏） | `psutil` | 100% |
| `desktop_app_launch` | 启动应用 | `subprocess` | 98% |
| `desktop_schedule_task` | 定时任务 | `APScheduler` | 99% |

### 3.2 重构模块：AgentCoreBridge

当前 `agent_core_bridge.py`（499 行）需要重构为分层架构：

```
galaxyos/kernel/
├── agent_core_bridge.py          # 瘦入口，仅初始化和代理
├── desktop_tools/                # 新增：桌面操作工具包
│   ├── __init__.py
│   ├── fs_tools.py               # 文件系统 MCP 工具
│   ├── shell_tools.py            # Shell 执行 MCP 工具
│   ├── code_tools.py             # 代码执行 MCP 工具
│   ├── clipboard_tools.py        # 剪贴板 MCP 工具
│   ├── window_tools.py           # 窗口管理 MCP 工具
│   ├── system_tools.py           # 系统信息 MCP 工具
│   ├── app_launcher_tools.py     # 应用启动 MCP 工具
│   └── scheduler_tools.py        # 定时任务 MCP 工具
├── sys_operation_adapter.py      # 新增：SysOperation → MCP Tool 适配器
└── permission_config.py          # 新增：桌面操作权限配置
```

### 3.3 清理模块：死代码删除

以下文件确认从未被调用，应删除或归档：

| 文件 | 行数 | 状态 | 处置 |
|------|------|------|------|
| `kernel/render_channel_router.py` | 170 | Python 侧渲染路由，从未被 MCP Server 调用 | **删除** |
| `kernel/tokui_degradation.py` | 192 | 10 种降级策略，从未被调用 | **删除** |
| `kernel/tokui_chat_sse_client.py` | 234 | SSE/WebSocket 客户端，连接不存在的 Sidecar | **删除** |
| `kernel/tokui_chat_adapter.py` | 113 | 前端加载器，默认回退到 "swarm" | **删除** |
| `kernel/tokui_sse_streamer.py` | 248 | DSL 分片推送，Sidecar 不存在 | **删除** |
| `kernel/tokui_chat_i18n_adapter.py` | — | i18n 适配，依赖上述死代码 | **删除** |
| `kernel/cognitive_panel_injector.py` | — | 认知面板注入器 | **删除** |
| `kernel/cognitive_data_pusher.py` | — | 认知数据推送 | **删除** |

C++ Desktop Shell 模块（已实现）：

| 模块 | 头文件 | 源文件 | 职责 |
|------|--------|--------|------|
| GalaxyOSApp | — | galaxyos_app.cpp | EUI-NEO 应用入口、UI 构建、生命周期 |
| NativeLogger | native_logger.h | native_logger.cpp | JSON 结构化日志 |
| NativeConfig | native_config.h | native_config.cpp | 配置管理（JSON） |
| NativeEventBus | native_event_bus.h | native_event_bus.cpp | 发布-订阅事件总线 |
| NativeIPCChannel | native_ipc_channel.h | native_ipc_channel.cpp | HTTP IPC 通道（URL 编码） |
| NativeSSEClient | native_sse_client.h | native_sse_client.cpp | SSE 协议客户端（无锁 dispatch） |
| NativeProcessManager | native_process_manager.h | native_process_manager.cpp | Python 子进程管理 |
| I18nBridge | i18n_bridge.h | i18n_bridge.cpp | i18n 翻译桥接 |

C++ Desktop Shell 模块（待实现）：

| 模块 | 职责 |
|------|------|
| NativeWindowManager | GLFW 窗口管理 |
| EuiNeoFFIWrapper | EUI-NEO FFI 安全包装 |
| DslMappingTable | TokUI→EUI-NEO 组件映射 |
| NativeRenderEngine | 渲染引擎 |
| NativeTrayIcon | 系统托盘 |

### 3.4 统一渲染路径

**决策：三级降级链 eui_native → webview_dom → plain_text**

```
用户输入 → HTTP IPC → Python MCP Server → Agent Core (DeepAgent)
                                                    │
                                                    ▼
                                              LLM 推理 + 工具调用
                                                    │
                                                    ▼
                                           TokUI DSL / Markdown
                                                    │
                                    ┌───────────────┼───────────────┐
                                    │               │               │
                              eui_native?      webview_dom?     plain_text
                              (GPU 直渲)       (HTML/CSS/JS)    (纯文本)
                                    │               │               │
                                    ▼               ▼               ▼
                              EUI-NEO FFI     WebView DOM     终端输出
                              NativeRender     嵌入式渲染      文本流
```

三级降级链渲染方案：
- **eui_native（最高优先级）**：EUI-NEO GPU 直渲，通过 NativeRenderEngine 驱动，性能最优
- **webview_dom（次优先级）**：HTML/CSS/JS 实现，TokUI DSL 通过 JS 解析器转为 DOM 元素，SSE 流式更新通过 NativeSSEClient → NativeEventBus 推送到渲染层
- **plain_text（兜底降级）**：纯文本终端输出，无 GPU/WebView 依赖，确保任何环境下可用
- 降级链不可逆：eui_native 不可用时降级到 webview_dom，webview_dom 不可用时降级到 plain_text
- 认知面板作为侧边栏组件（eui_native 下由 EUI-NEO 渲染，webview_dom 下为 HTML 组件）

---

## 4. 核心技术选型

| 决策点 | 选型 | 理由 |
|--------|------|------|
| Agent 框架 | openjiuwen (Agent Core) | 已集成、有 SysOperation、Rails、PermissionEngine、MCP 双向、A2A |
| 桌面操作 | SysOperation (local mode) + 自建 MCP Tool | 文件/Shell/代码 100% 可靠，窗口/剪贴板 95%+ |
| 渲染路径 | 三级降级链 eui_native → webview_dom → plain_text | EUI-NEO GPU 直渲优先，逐级降级保证可用性 |
| MCP 传输 | SSE (:8765) | 已有实现，C++ Shell 通过 HTTP IPC 直接调用 |
| LLM 接入 | OpenAI 兼容 API | Agent Core 已内置，支持多 Provider |
| 进程间通信 | HTTP IPC + SSE (C++ Shell↔Python) | cpp-httplib 实现，稳定可靠 |
| 权限系统 | Agent Core PermissionEngine | 分层审批，危险操作自动升级 |
| 安全护栏 | Agent Core Rails (15+) | SecurityRail + SysOperationRail + AskUserRail + ConfirmRail |
| 桌面壳构建 | CMake + CPack (NSIS/DEB) | 原生 C++ 构建，跨平台打包 |

---

## 5. 数据流 / 调用链

### 5.1 用户聊天流程

```
1. 用户在 C++ Shell 界面输入消息
2. C++ Shell HTTP IPC POST → Python MCP Server :8765
3. Python MCP Server → AgentCoreBridge.chat()
4. AgentCoreBridge → DeepAgent.stream(inputs)
5. DeepAgent → ReActAgent → LLM 推理 → 工具调用循环
6. 工具调用 → SysOperation.fs/shell/code 或 自建 MCP Tool
7. 流式输出 → SSE chunk → NativeSSEClient → NativeEventBus → RenderChannel
```

### 5.2 桌面操作流程

```
1. 用户："帮我整理下载文件夹里的 PDF"
2. DeepAgent → LLM 决定调用 desktop_fs_list + desktop_fs_move
3. PermissionEngine 检查：fs_move 涉及工作空间外路径 → ASK
4. AskUserRail 中断 → 推送确认请求到前端
5. 用户确认 → 工具执行 → 返回结果
6. DeepAgent → LLM 生成总结 → 流式输出
```

### 5.3 MCP 双向集成流程

```
# GalaxyOS 作为 MCP Server（暴露工具给外部）
外部 Agent → MCP SSE → GalaxyOS MCP Server (:8765)
  → desktop_fs_read / desktop_shell_exec / ...

# GalaxyOS 作为 MCP Client（消费外部工具）
DeepAgent → MCPTool → 外部 MCP Server (Playwright/OpenAPI/...)
  → 浏览器自动化 / API 调用 / ...
```

---

## 6. 关键接口定义

### 6.1 DesktopToolsRegistry（新增）

```python
class DesktopToolsRegistry:
    """注册所有桌面操作工具到 MCP Server 和 Agent Core"""

    def __init__(self, mcp_server: GalaxyOSMCPServer, bridge: AgentCoreBridge):
        self._mcp = mcp_server
        self._bridge = bridge
        self._sys_op: Optional[SysOperation] = None

    async def initialize(self) -> None:
        # 1. 初始化 SysOperation (local mode)
        card = SysOperationCard(
            id="galaxyos_desktop",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(
                sandbox_root=[os.path.expanduser("~")],
                restrict_to_sandbox=False,  # 桌面助手需访问全文件系统
            ),
        )
        self._sys_op = SysOperation(card)

        # 2. 注册 SysOperation 工具到 MCP Server
        self._register_sys_operation_tools()

        # 3. 注册自建桌面工具到 MCP Server
        self._register_custom_desktop_tools()

        # 4. 注入到 DeepAgent 的 ability_manager
        await self._inject_to_agent()

    def _register_sys_operation_tools(self) -> None:
        # 文件系统工具
        self._mcp.tool()(self._fs_read)
        self._mcp.tool()(self._fs_write)
        self._mcp.tool()(self._fs_list)
        self._mcp.tool()(self._fs_search)
        # Shell 工具
        self._mcp.tool()(self._shell_exec)
        self._mcp.tool()(self._shell_exec_stream)
        # 代码执行工具
        self._mcp.tool()(self._code_exec)

    def _register_custom_desktop_tools(self) -> None:
        self._mcp.tool()(self._clipboard_read)
        self._mcp.tool()(self._clipboard_write)
        self._mcp.tool()(self._window_list)
        self._mcp.tool()(self._system_info)
        self._mcp.tool()(self._process_list)
        self._mcp.tool()(self._app_launch)
        self._mcp.tool()(self._schedule_task)
```

### 6.2 SysOperation → MCP Tool 适配器

```python
class SysOperationMcpAdapter:
    """将 SysOperation 的方法适配为 FastMCP tool 函数"""

    def __init__(self, sys_op: SysOperation):
        self._op = sys_op

    async def fs_read(self, path: str, mode: str = "text",
                      head: int = 0, tail: int = 0,
                      encoding: str = "utf-8") -> str:
        result = await self._op.fs().read_file(
            path=path, mode=mode, head=head, tail=tail, encoding=encoding
        )
        return json.dumps(result, ensure_ascii=False)

    async def shell_exec(self, command: str, cwd: str = "",
                         timeout: int = 30,
                         shell_type: str = "auto") -> str:
        result = await self._op.shell().execute_cmd(
            command=command, cwd=cwd or None,
            timeout=timeout, shell_type=ShellType(shell_type)
        )
        return json.dumps(result, ensure_ascii=False)
```

### 6.3 权限配置

```python
# permission_config.py
DESKTOP_PERMISSION_RULES = {
    # 文件操作：工作空间内 ALLOW，外 ASK
    "desktop_fs_read": {"default": "ALLOW"},
    "desktop_fs_write": {"default": "ASK", "workspace_inside": "ALLOW"},
    "desktop_fs_delete": {"default": "DENY", "workspace_inside": "ASK"},

    # Shell 操作：白名单 ALLOW，其他 ASK
    "desktop_shell_exec": {
        "default": "ASK",
        "allowlist": ["dir", "ls", "cat", "type", "echo", "where", "which",
                       "python", "node", "git status", "git log", "pip list"],
    },

    # 代码执行：ASK
    "desktop_code_exec": {"default": "ASK"},

    # 系统信息：ALLOW
    "desktop_system_info": {"default": "ALLOW"},
    "desktop_process_list": {"default": "ALLOW"},

    # 进程控制：DENY（需显式授权）
    "desktop_process_kill": {"default": "DENY"},

    # 剪贴板：ALLOW
    "desktop_clipboard_read": {"default": "ALLOW"},
    "desktop_clipboard_write": {"default": "ALLOW"},

    # 窗口管理：ALLOW
    "desktop_window_list": {"default": "ALLOW"},
    "desktop_window_focus": {"default": "ALLOW"},
}
```

---

## 7. 集成步骤

### Phase 1：清理死代码（1 天）

1. 删除 Python 侧 8 个死代码文件（见 3.3 节）
2. 运行 `ruff check` + `pytest` 验证 Python 侧完整性
3. 确认 `desktop-native/` 模块编译通过：`cmake -B desktop-native/build -S desktop-native && cmake --build desktop-native/build --config Release`

### Phase 2：SysOperation 集成（2 天）

1. 创建 `galaxyos/kernel/desktop_tools/` 目录结构
2. 实现 `SysOperationMcpAdapter`：将 SysOperation 方法适配为 MCP tool
3. 实现 `DesktopToolsRegistry`：统一注册入口
4. 在 `mcp_server.py` 中调用 `DesktopToolsRegistry.initialize()`
5. 在 `agent_core_bridge.py` 中将桌面工具注入 DeepAgent
6. 配置权限规则 `permission_config.py`
7. 编写单元测试

### Phase 3：自建桌面工具（3 天）

1. 实现 `clipboard_tools.py`（`pyperclip`）
2. 实现 `window_tools.py`（`pygetwindow` + `Pillow`）
3. 实现 `system_tools.py`（`psutil`）
4. 实现 `app_launcher_tools.py`（`subprocess`）
5. 实现 `scheduler_tools.py`（`APScheduler`）
6. 每个工具注册到 MCP Server + DeepAgent
7. 编写集成测试

### Phase 4：C++ Shell 渲染集成（3 天）

1. 实现 NativeSSEClient 与 MCP Server :8765 的 SSE 连接
2. 实现 NativeIPCChannel HTTP POST 发送用户消息
3. 实现 NativeEventBus 事件分发到渲染层
4. 实现三级降级链判断逻辑（eui_native → webview_dom → plain_text）
5. 实现桌面操作确认对话框（AskUserRail 前端）
6. 端到端测试

### Phase 5：MCP 双向集成（2 天）

1. 配置 DeepAgent 消费外部 MCP Server（Playwright 等）
2. 配置 GalaxyOS MCP Server 暴露给外部 Agent
3. A2A 协议集成（可选）
4. 集成测试

---

## 8. 非功能设计

### 8.1 安全

| 措施 | 说明 |
|------|------|
| PermissionEngine | 分层权限审批：ALLOW / ASK / DENY |
| SecurityRail | Prompt 注入检测、工具安全检查 |
| SysOperationRail | 系统操作审批 |
| AskUserRail | 危险操作中断等待用户确认 |
| Shell 白名单 | 常见安全命令自动放行，其他需确认 |
| 文件删除 DENY | 默认拒绝删除操作，需显式授权 |
| 沙箱模式 | SysOperation 支持 sandbox 模式（未来） |

### 8.2 可用性

| 措施 | 说明 |
|------|------|
| 降级策略 | Agent Core 不可用时回退到 `_FallbackReActEngine` |
| 渲染三级降级链 | eui_native 不可用时降级到 webview_dom，再降级到 plain_text |
| 工具降级 | `pygetwindow` 不可用时 `desktop_window_list` 返回空列表 |
| 健康检查 | MCP Server /health 端点 + Agent Core 状态 |

### 8.3 性能

| 指标 | 目标 | 措施 |
|------|------|------|
| 首次响应延迟 | < 2s | 流式输出 + SSE 推送 |
| 文件操作延迟 | < 100ms | SysOperation 本地模式 |
| Shell 执行延迟 | < 5s（简单命令） | 流式输出 + 超时控制 |
| 内存占用 | < 500MB | Python 进程独立，C++ Desktop Shell 轻量 |

---

## 9. 部署架构

```
Windows 桌面 (单机)
├── GalaxyOS.exe (CMake + CPack NSIS)
│     ├── EUI-NEO (GPU 直渲, add_subdirectory 集成)
│     ├── galaxyos-mcp.exe 或 python -m galaxyos.kernel.mcp_server_entry
│     │     ├── FastMCP Server (:8765)
│     │     ├── Agent Core (openjiuwen)
│     │     │     ├── DeepAgent + SysOperation
│     │     │     └── MCP Client (消费外部工具)
│     │     └── GalaxyOS Engine (ONNX Embedding + 检索)
│     └── [可选] WebView 组件 (webview_dom 降级路径)
└── 依赖
      ├── Python 3.11-3.13
      ├── openjiuwen @ v0.1.16
      ├── psutil, pyperclip, pygetwindow, Pillow, APScheduler
      └── ONNX Runtime + bge-small-zh-v1.5
```

---

## 10. 优劣势分析

### 10.1 优势

| 维度 | 说明 |
|------|------|
| **可靠性** | 文件/Shell/代码操作走 API 不走 GUI，100% 可靠 |
| **安全性** | Agent Core 内置 15+ Rails + PermissionEngine，远超自建 |
| **扩展性** | MCP 协议双向集成，可消费/暴露任意工具；A2A 支持跨框架互操作 |
| **开发效率** | SysOperation 已实现文件/Shell/代码操作（3000+ 行），无需自建 |
| **维护成本** | Agent Core 由 openJiuwen 团队维护，GalaxyOS 专注桌面集成层 |
| **渐进增强** | 三级降级链保证可用性，EUI-NEO GPU 直渲为最高优先级渲染路径 |
| **性能** | C++ Desktop Shell 原生性能，EUI-NEO GPU 直渲零拷贝渲染 |

### 10.2 劣势

| 维度 | 说明 |
|------|------|
| **依赖风险** | openjiuwen 是自研框架，非社区标准（LangChain 生态更大） |
| **版本锁定** | 必须从 GitHub 安装 v0.1.16，PyPI 版本滞后 |
| **GUI 操控缺失** | 路径 A 不做 GUI 操控，无法操作不提供 API 的应用 |
| **窗口管理弱** | `pygetwindow` 在 Windows 上功能有限，不如 UIA |
| **C++ 模块待实现** | NativeWindowManager / EuiNeoFFIWrapper / DslMappingTable 等 5 个模块尚未编码 |
| **打包体积** | Python + openjiuwen + 依赖 > 200MB |

---

## 11. 方案对比

### 11.1 与业界方案横向比较

| 维度 | GalaxyOS + Agent Core | Open Interpreter | 微软 UFO² | LangChain + 自建 |
|------|----------------------|-----------------|-----------|-----------------|
| **定位** | 增强型桌面助手 | 通用代码解释器 | Windows GUI Agent | 通用 Agent 框架 |
| **桌面能力** | 文件/Shell/代码/剪贴板/窗口 | 文件/Shell/代码/浏览器 | UIA + Win32 + WinCOM | 无（需自建） |
| **GUI 操控** | 无（路径 A 不做） | 无 | 有（截图+点击） | 无 |
| **安全护栏** | 15+ Rails + PermissionEngine | 基础确认 | 无 | 无（需自建） |
| **MCP 集成** | 双向（Server+Client） | 无 | 无 | 需自建 |
| **多 Agent** | A2A + TeamAgent | 无 | HostAgent+AppAgent | 需自建 |
| **成熟度** | v0.1.16，发展中 | v0.4，社区活跃 | 学术项目 | 生态最大但需组装 |
| **Windows 支持** | 原生（C++ Desktop Shell） | 原生 | 原生 | 需适配 |
| **可靠性** | 确定性操作 100% | 代码执行 95% | OSWorld 14.9% | 取决于自建质量 |

### 11.2 选择理由

1. **Agent Core 已集成**：`agent_core_bridge.py` 已有 499 行集成代码，切换成本最低
2. **SysOperation 开箱即用**：文件/Shell/代码操作 3000+ 行已实现，自建需 2-3 周
3. **安全体系完整**：15+ Rails + PermissionEngine 是 LangChain/Open Interpreter 没有的
4. **MCP 双向**：既能消费外部工具也能暴露自身能力，是路径 A 的关键
5. **A2A 互操作**：未来可与其他 Agent 框架协作，不被锁定

---

## 12. 风险与待决事项

### 12.1 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| openjiuwen API 变更 | 集成代码需适配 | 锁定 v0.1.16，关注 changelog |
| pygetwindow Windows 兼容性 | 窗口管理工具不可用 | 降级返回空列表，未来用 ctypes + Win32 API 替代 |
| Python 进程崩溃 | 桌面功能不可用 | NativeProcessManager 自动重启 + 健康检查 |
| EUI-NEO FFI 内存安全 | 悬垂指针 / 异常跨边界 | malloc+memcpy 释放策略，try/catch 包裹 FFI 调用 |
| C++ 待实现模块延迟 | 渲染降级链不完整 | webview_dom 作为中间态保证可用性 |

### 12.2 待决事项

| 事项 | 选项 | 建议 |
|------|------|------|
| WebView UI 框架 | 原生 JS vs React vs Svelte | 原生 JS（最轻量，无构建步骤） |
| TokUI DSL 渲染 | JS 解析器 vs 服务端渲染 HTML | JS 解析器（减少 C++ Shell↔Python 通信） |
| A2A 是否启用 | Phase 5 集成 vs 暂不 | Phase 5 集成（低优先级） |
| EUI-NEO 待实现模块优先级 | NativeWindowManager 优先 vs EuiNeoFFIWrapper 优先 | EuiNeoFFIWrapper 优先（渲染链关键路径） |
