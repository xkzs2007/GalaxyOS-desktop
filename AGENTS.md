# Agent 配置

执行任何操作都应注意启用不少于两个 agent 的多 agent 协作，避免单点故障。

## 项目概览

GalaxyOS 是认知增强型 AI Agent 桌面引擎，v0.3.0，代号 Cognitive Nexus。

**双进程架构**：C++ 桌面壳 (EUI-NEO GPU 直渲) + GalaxyOS Python 内核 (MCP Server + AgentCore Bridge)

**渲染三级降级链**（不可逆）：`eui_native` (EUI-NEO C++ GPU 直渲) → `webview_dom` → `plain_text`

## 关键命令

```bash
# Python 依赖安装（核心）
pip install -r requirements-core.txt
pip install "openjiuwen @ git+https://github.com/openJiuwen-ai/agent-core@v0.1.16"

# 重型依赖（CPU-only，CI 用）
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-heavy-cpu.txt

# 重型依赖（CUDA，本地 GPU 用）
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements-heavy-cuda.txt

# 启动 Python 内核
GALAXYOS_MODE=desktop python -m galaxyos.kernel.mcp_server_entry

# ONNX 模型下载（CI 工作流自动下载 onnx-community/bge-small-zh-v1.5-ONNX）

# C++ 桌面壳构建（需先 checkout EUI-NEO 到项目根目录 EUI-NEO/）
cmake -B desktop-native/build -S desktop-native
cmake --build desktop-native/build --config Release

# Lint + 类型检查
ruff check .
python -m mypy galaxyos/ --config-file mypy.ini

# 测试
python -m pytest tests/ -x -q --tb=short
```

## 目录职责

| 目录 | 职责 | 注意事项 |
|------|------|---------|
| `desktop-native/src/` | C++ 桌面壳源码 | EUI-NEO FFI、GLFW 窗口、IPC、SSE、托盘 |
| `desktop-native/include/` | C++ 头文件 | 7 个模块头文件 |
| `vendor/eui-neo/sdk/` | EUI-NEO C++ SDK | CI 中下载，不入库（.gitignore 排除） |
| `galaxyos/kernel/` | Python 认知内核 | MCP Server、AgentCore Bridge、DSL Bridge |
| `galaxyos/engine/` | Python 核心引擎 | ONNX Embedding、检索、神经网络 |
| `skills/` | 76 技能包 | SKILL.md 格式 |
| `extensions/galaxyos/` | 旧 JiuwenSwarm Extension | **已废弃**，含 `from jiuwenswarm` 残留引用 |
| `models/embeddings/` | ONNX 模型文件 | CI 中从 `onnx-community/bge-small-zh-v1.5-ONNX` 下载 |

## 依赖版本约束

- **openjiuwen**：从 GitHub 安装 `@ v0.1.16`，**不从 PyPI**（PyPI 版本滞后）
- **torch**：CPU 版 `--index-url https://download.pytorch.org/whl/cpu`，CUDA 版 `.../cu128`
- **hnswlib**：`==0.8.0`，无 `__version__` 属性，验证时用 `import hnswlib` 而非 `hnswlib.__version__`
- **onnxruntime**：PyInstaller 打包时已排除 torch/transformers/faiss/hnswlib/pandas（见 `galaxyos-mcp.spec` excludes）

## ONNX 模型

- 下载源：`onnx-community/bge-small-zh-v1.5-ONNX`（**不是** `BAAI/bge-small-zh-v1.5`，该仓库无 ONNX 文件）
- 文件：`model.onnx` + `model.onnx_data` + `tokenizer.json`（拆分格式）
- `onnx_embedding.py` 同时支持 `bge-small-zh.onnx`（单文件）和 `model.onnx`（拆分格式）

## CI/CD

- **ci.yml**：check-deps (Ubuntu) + build-native (Windows CMake)
- **release.yml**：build-wheel + build-docker (GHCR, linux/amd64) + build-native (Windows CMake + NSIS)
- Windows runner **不支持** `container` 指令（GitHub Actions 仅 Linux 支持）
- Docker 构建**仅 linux/amd64**（无法在 Linux runner 上构建 Windows 容器镜像）
- hf-mirror.com 不稳定，curl 需 `--retry 3 --retry-delay 5 --max-time 120` + HuggingFace 官方源降级
- pre-commit hooks：trailing-whitespace, end-of-file-fixer, ruff, gitleaks, mypy, pytest (pre-push)

## 命名约定

- Python 函数/变量：`snake_case`
- C++ 函数/变量：`snake_case`
- C++ 类名：`PascalCase`
- MCP 工具名：`claw_` 前缀（历史品牌残留，新工具不再使用）
- i18n 翻译键：`camelCase`（`zh.json` / `en.json`，82 键）

## 已知陷阱

- **C++ FFI 内存安全**：C++ 返回 `std::string::c_str()` 是悬垂指针，必须用 `malloc+memcpy`；C++ 异常不可跨越 FFI 边界（UB），需 `try/catch` 包裹；必须调用 `eui_neo_free_response()` 释放内存。
- **GLAD 必须在 GLFW 之前 include**：`#include <glad/glad.h>` 必须在 `#include <GLFW/glfw3.h>` 之前，否则编译错误。头文件中使用前向声明 `struct GLFWwindow;` 避免 include 顺序问题。
- **品牌残留**：`OPENCLAW_HOME` 环境变量保留为向后兼容别名；`claw_` 前缀工具名运行时兼容不改；`xiaoyi/小义` 已标记 removed。

## C++ 桌面壳模块架构

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

**待实现模块**（AGENTS.md 规划但尚未编码）：
- NativeWindowManager（GLFW 窗口管理，当前由 EUI-NEO 内部处理）
- EuiNeoFFIWrapper（EUI-NEO FFI 安全包装，当前由 galaxyos_app.cpp 直接调用 EUI-NEO API）
- DslMappingTable（TokUI→EUI-NEO 组件映射，当前由 DSLBridge Python 侧处理）
- NativeRenderEngine（渲染引擎，当前由 EUI-NEO 内部处理）
- NativeTrayIcon（系统托盘，当前由 EUI-NEO `.tray(true)` 配置处理）

## Agent Skills

### Issue 追踪器

Issue 存放在 GitHub Issues 中，使用 `gh` CLI 操作。详见 `docs/agents/issue-tracker.md`。

### Triage 标签

五个标准分诊角色标签。详见 `docs/agents/triage-labels.md`。

### 领域文档

单上下文布局：根目录 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。

### 统一语言

领域术语表见 `UBIQUITOUS_LANGUAGE.md`。

## TokUI ↔ EUI-NEO 桥接架构

分层桥接模型，描述层与渲染层分离：

1. **描述层**：TokUI DSL（AI 友好、流式友好）
2. **桥接层**：`DSLBridge`（`galaxyos/kernel/dsl_bridge.py`），将 TokUI DSL 翻译为 EUI-NEO 渲染指令
3. **渲染层**：EUI-NEO C++ Engine（OpenGL GPU 直渲）

组件映射：TokUI 150+ 专用组件 → EUI-NEO 基础组件组合（原子化组件库）
事件同步：SSE 事件 → NativeEventBus → NativeRenderEngine
流式渲染：增量构建 API（`begin_stream`, `create_node`, `update_text`, `end_stream`），非一次性 `build()`
