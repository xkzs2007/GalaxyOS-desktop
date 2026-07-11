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
  4. `~/.openclaw`（插件版遗留默认）
- **向后兼容**：当 `GALAXYOS_HOME` 与 `OPENCLAW_HOME` 同时设置且指向不同目录时，`GALAXYOS_HOME` 优先，并发出 `UserWarning` 警告
- **影响范围**：Python `galaxyos.shared.paths`、JS `extensions/galaxyos/paths.js`

### `OPENCLAW_HOME`

- **说明**：OpenClaw 根目录（遗留兼容）
- **优先级**：低于 `GALAXYOS_HOME`
- **默认值**：无
- **向后兼容**：新代码应使用 `GALAXYOS_HOME`，`openclaw_home()` 内部委托给 `galaxyos_home()`

### `GALAXYOS_OPENCLAW_HOME`

- **说明**：JS 端专用 OpenClaw 兼容路径（仅 `paths.js` 识别）
- **优先级**：与 `OPENCLAW_HOME` 同级（JS 端优先级链第 2 级）
- **默认值**：无

### `OPENCLAW_WORKSPACE` / `WORKSPACE`

- **说明**：工作空间目录覆盖
- **默认值**：`$GALAXYOS_HOME/workspace`
- **优先级**：`OPENCLAW_WORKSPACE` > `WORKSPACE` > 默认值

---

## 2. 运行模式

### `GALAXYOS_MODE`

- **说明**：运行模式选择（插件版 / 桌面端）
- **可选值**：`plugin` | `desktop`
- **默认值**：自动检测（`api` 对象可用 → `plugin`，否则 → `desktop`）
- **影响范围**：`extensions/galaxyos/launcher.js` 的 `createHost()` 函数
- **行为**：
  - `plugin`：注入 `OpenClawAdapter`，委托给 OpenClaw `api` 对象
  - `desktop`：注入 `DesktopAdapter`，本地 Map 注册表 + stderr 日志器
  - 未设置：自动检测 `api.registerTool` 是否可调用来决定

---

## 3. 通信与安全

### `GALAXYOS_UDS_PATH`

- **说明**：Unix Domain Socket 路径（Sidecar 与 Python Worker 通信）
- **默认值**：`null`（使用 TCP）
- **行为**：设置后 Sidecar 通过 UDS 而非 TCP 连接 Python Worker

### `GALAXYOS_SIDECAR_HOST`

- **说明**：SSE Sidecar HTTP 监听地址
- **默认值**：`127.0.0.1`（仅本地访问）
- **安全**：禁止绑定 `0.0.0.0`

### `GALAXYOS_SIDECAR_HTTP_PORT`

- **说明**：SSE Sidecar HTTP 监听端口
- **默认值**：`5758`

### `GALAXYOS_SIDECAR_TOKEN`

- **说明**：SSE Sidecar 认证 Token（由 Electron 主进程注入）
- **默认值**：启动时随机生成 32 字节 hex
- **安全**：所有 `/sse/*` 请求必须携带 `Authorization: Bearer <token>`

### `GALAXYOS_MAX_STREAMS`

- **说明**：SSE 最大并发连接数
- **默认值**：`50`

### `GALAXYOS_MAX_BODY`

- **说明**：SSE 请求 Body 最大字节数
- **默认值**：`8192`

### `GALAXYOS_WORKER_HOST`

- **说明**：Python Worker TCP 地址
- **默认值**：`127.0.0.1`

### `GALAXYOS_WORKER_PORT`

- **说明**：Python Worker TCP 端口
- **默认值**：`5760`

---

## 4. TokUI 相关

### `TOKUI_MODE`

- **说明**：TokUI 渲染模式
- **可选值**：待定义
- **默认值**：待定义

### `TOKUI_SSE_ENDPOINT`

- **说明**：TokUI SSE 连接端点 URL
- **默认值**：`/sse/ask`（相对路径，由 Vite proxy 或 Sidecar 处理）

### `TOKUI_SSE_TOKEN`

- **说明**：TokUI SSE 连接认证 Token
- **默认值**：由 Electron 主进程通过 IPC 注入

---

## 5. 调试与开发

### `GALAXYOS_DEBUG`

- **说明**：启用 DesktopAdapter 的 DEBUG 级别日志
- **默认值**：未设置（不输出 DEBUG 日志）

### `GALAXYOS_DEV`

- **说明**：Electron 开发模式标志
- **默认值**：未设置
- **行为**：设置为 `1` 时，Electron 加载 `http://localhost:5173`（Vite dev server）并自动打开 DevTools

---

## 6. 安全策略摘要

| 安全项 | 策略 |
|--------|------|
| 监听地址 | 默认 `127.0.0.1`，禁止 `0.0.0.0` |
| CORS | 仅允许 localhost 来源 |
| 认证 | Bearer Token（随机生成） |
| 并发限制 | 50 连接 |
| Body 限制 | 8192 字节 |
| CSP | `script-src 'self'`，`connect-src 'self' ws://localhost:* http://localhost:*` |
| Electron | `contextIsolation: true`，`nodeIntegration: false` |

---

## 7. 向后兼容行为

- `openclaw_home()` 委托给 `galaxyos_home()`，旧代码无需修改
- `OPENCLAW_HOME` 环境变量仍然有效，但 `GALAXYOS_HOME` 优先
- Python 端 `path_resolver_compat` 模块自引用保持旧导入路径兼容
- JS 端 `openclawHome()` 为 `galaxyosHome()` 的别名