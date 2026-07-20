# GalaxyOS 环境变量配置文档

## 概述

GalaxyOS 通过环境变量控制路径解析、运行模式、安全认证等核心行为。本文档列出所有新增/变更的环境变量、优先级链和默认值。

---

## 1. 路径相关

### `GALAXYOS_HOME`

- **说明**：GalaxyOS 根目录，所有子路径的基准
- **优先级**：最高（覆盖所有其他路径配置）
- **默认值**：无（未设置时走优先级链）
- **优先级链**：
  1. `GALAXYOS_HOME` 环境变量（显式覆盖）
  2. `OPENCLAW_HOME` 环境变量（向后兼容）
  3. `~/.galaxyos`（桌面端默认）
- **向后兼容**：当 `GALAXYOS_HOME` 与 `OPENCLAW_HOME` 同时设置且指向不同目录时，`GALAXYOS_HOME` 优先，并发出 `UserWarning` 警告
- **影响范围**：Python `galaxyos.shared.paths`、C++ Desktop Shell `NativeConfig`

### `OPENCLAW_HOME`

- **说明**：OpenClaw 根目录（遗留兼容）
- **优先级**：低于 `GALAXYOS_HOME`
- **默认值**：无
- **向后兼容**：新代码应使用 `GALAXYOS_HOME`，`openclaw_home()` 内部委托给 `galaxyos_home()`

### `OPENCLAW_WORKSPACE` / `WORKSPACE`

- **说明**：工作空间目录覆盖
- **默认值**：`$GALAXYOS_HOME/workspace`
- **优先级**：`OPENCLAW_WORKSPACE` > `WORKSPACE` > 默认值

---

## 2. 运行模式

### `GALAXYOS_MODE`

- **说明**：运行模式选择
- **可选值**：`desktop`
- **默认值**：`desktop`
- **影响范围**：C++ Desktop Shell 的 `NativeProcessManager` + MCP Server 启动模式
- **行为**：
  - `desktop`：C++ Desktop Shell 启动 Python MCP Server 子进程，通过 HTTP IPC + SSE 通信

---

## 3. 通信与安全

### `GALAXYOS_MCP_HOST`

- **说明**：MCP Server HTTP 监听地址
- **默认值**：`127.0.0.1`（仅本地访问）
- **安全**：禁止绑定 `0.0.0.0`

### `GALAXYOS_MCP_PORT`

- **说明**：MCP Server HTTP 监听端口
- **默认值**：`8765`

### `GALAXYOS_MAX_STREAMS`

- **说明**：SSE 最大并发连接数
- **默认值**：`50`

### `GALAXYOS_MAX_BODY`

- **说明**：SSE 请求 Body 最大字节数
- **默认值**：`8192`

---

## 4. TokUI 相关

### `TOKUI_MODE`

- **说明**：TokUI 渲染模式
- **可选值**：`eui_native` | `webview_dom` | `plain_text`
- **默认值**：`eui_native`（降级不可逆）

### `TOKUI_SSE_ENDPOINT`

- **说明**：TokUI SSE 连接端点 URL
- **默认值**：`/agent-chat`（相对路径，由 MCP Server :8765 提供）

### `TOKUI_SSE_TOKEN`

- **说明**：TokUI SSE 连接认证 Token
- **默认值**：由 C++ Desktop Shell 通过 NativeIPCChannel 注入

---

## 5. 调试与开发

### `GALAXYOS_DEBUG`

- **说明**：启用 DEBUG 级别日志
- **默认值**：未设置（不输出 DEBUG 日志）

### `GALAXYOS_DEV`

- **说明**：C++ Desktop Shell 开发模式标志
- **默认值**：未设置
- **行为**：设置为 `1` 时，CMake Debug 构建，启用详细日志输出

### `GALAXYOS_NATIVE_BUILD_DIR`

- **说明**：CMake 构建目录路径
- **默认值**：`desktop-native/build`

---

## 6. 安全策略摘要

| 安全项 | 策略 |
|--------|------|
| 监听地址 | 默认 `127.0.0.1`，禁止 `0.0.0.0` |
| CORS | 仅允许 localhost 来源 |
| 认证 | Bearer Token（随机生成） |
| 并发限制 | 50 连接 |
| Body 限制 | 8192 字节 |
| C++ Desktop Shell | 进程隔离（独立 Python 子进程）、HTTP IPC 仅 127.0.0.1 |

---

## 7. 向后兼容行为

- `openclaw_home()` 委托给 `galaxyos_home()`，旧代码无需修改
- `OPENCLAW_HOME` 环境变量仍然有效，但 `GALAXYOS_HOME` 优先
- Python 端 `path_resolver_compat` 模块自引用保持旧导入路径兼容
- C++ Desktop Shell 使用 `GALAXYOS_HOME` 配置根目录
