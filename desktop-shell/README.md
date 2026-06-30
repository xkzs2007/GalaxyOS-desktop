# GalaxyOS Desktop

> **ZCode/Codex 级别的独立桌面 AI Agent** — 脱离 OpenClaw，直接运行 GalaxyOS 引擎
>
> v0.1.4 · TokUI-first · ESM 模块化 · 19 IPC channel · 76 Skills · 4 LLM 模式

## 截图

| 功能 | 截图文件 |
|---|---|
| 欢迎界面 | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage1.6-initial.png` |
| Agent 模式跑 shell | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage2-agent-shell.png` |
| 多会话管理 | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage2.1-multisession.png` |
| Model picker | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage2.2-model-picker.png` |
| MeMo 3-stage 协议 | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage3.0-memo-3stage.png` |
| ACRouter 自动路由 | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage4.0-global-routing-ask.png` |
| 真 Electron 窗口 | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage5.0-electron-real-window.png` |
| 打包后 GalaxyOS.exe | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage5.3-packaged-exe.png` |
| 76 Skills 加载 | `docs/superpowers/specs/evidence/screenshots/2026-06-29-stage6-76-skills-loaded.png` |

## 功能清单

| 功能 | 状态 | 说明 |
|---|---|---|
| **3 栏 ZCode 布局** | ✅ | sidebar / center chat / details panel（TokUI 挂载点） |
| **TokUI 流式 AI 气泡** | ✅ | think-chain / tool-call / terminal / sandbox / msg-actions |
| **TokUI 原生 UI** | ✅ | [conversations][card][dot][tabs][chat-input][welcome][feature] 全部就位 |
| **4 模式** | ✅ | Ask（自动路由）/ Process（R-CCAM）/ Agent（工具调用）/ MeMo*（调试） |
| **多会话管理** | ✅ | 新建 / 切换 / 重命名 / 删除 / localStorage 持久化 |
| **模型设置** | ✅ | API Key / Base URL / Provider / 5-slot 独立启用 → sidecar 热更新 |
| **76 Skills** | ✅ | 列表 + 详情 + 邻居图谱（SkillGraph） |
| **Agent 工具调用** | ✅ | shell_run / read_file / write_file / list_dir / grep / apply_diff |
| **Diff view** | ✅ | Agent 模式 `diff path "old"→"new"` → `[sandbox lang:diff]` |
| **MCP Server 配置** | ✅ | mcp_client.py · 读 ~/.galaxyos/mcp.json · 发现 tools/list · 合并到注册表 |
| **MeMo 3-stage** | ✅ | Grounding → Entity → Answer · 全局背景层 · parametric 记忆 |
| **ACRouter C-A-F** | ✅ | Context → Action → Feedback → Memorize · 全局路由 |
| **键盘快捷键** | ✅ | Ctrl+N / Ctrl+, / Ctrl+B / Ctrl+K / Esc |
| **msg-action 按钮** | ✅ | copy / regenerate / like / dislike / verify / recall / save |
| **ESM 模块化** | ✅ | renderer 13 个模块 + 4 个 pub-sub store |
| **API schema** | ✅ | `galaxy:schema` IPC channel 暴露 19 channels + 14 types 完整契约 |
| **zmq IPC bridge** | ✅ | renderer → main → sidecar via zmq REQ/REP + PUB/SUB |
| **PyInstaller 打包** | ✅ | galaxyos-sidecar.exe (17MB) |
| **electron-builder** | ✅ | GalaxyOS.exe (186MB standalone) |
| **应用图标** | ✅ | 512x512 GalaxyOS brand icon |
| **OpenClaw 解耦** | ✅ | path_resolver shim · 不需要 OpenClaw 运行 |

## 架构

```
┌──────────────────────────────────────────────────────────┐
│  GalaxyOS.exe (Electron 32, 1280×820 native window)       │
│  ┌──────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │ Sidebar  │  │  Center (TokUI) │  │  Details Panel  │   │
│  │ sessions │  │  流式 AI 气泡    │  │  R-CCAM trace   │   │
│  │ skills   │  │  4 mode 切换     │  │  Skill 详情      │   │
│  │ settings │  │  composer        │  │  Diff view      │   │
│  └──────────┘  └────────┬─────────┘  └─────────────────┘   │
│                         │ IPC (zmq REQ/REP :5757)          │
│  ┌──────────────────────▼──────────────────────────────┐  │
│  │  Python Sidecar (galaxyos-sidecar.exe)               │  │
│  │  ┌─────────┐  ┌──────────┐  ┌────────────────────┐  │  │
│  │  │ XiaoYi  │  │  MeMo    │  │  ACRouter          │  │  │
│  │  │ ClawLLM │  │  3-stage │  │  C-A-F loop        │  │  │
│  │  │ (engine)│  │  (global)│  │  Orch+Verif+Memory │  │  │
│  │  └─────────┘  └──────────┘  └────────────────────┘  │  │
│  │  ┌──────────┐  ┌──────────┐  ┌────────────────────┐  │  │
│  │  │ 6 Tools  │  │ MCP      │  │ 76 Skills          │  │  │
│  │  │ shell/   │  │ Client   │  │ (from skills/)     │  │  │
│  │  │ read/    │  │          │  │                    │  │  │
│  │  │ write/   │  │          │  │                    │  │  │
│  │  │ grep/    │  │          │  │                    │  │  │
│  │  │ diff     │  │          │  │                    │  │  │
│  │  └──────────┘  └──────────┘  └────────────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

## 快速启动

### 方式 1：开发模式

```bash
# 1. 安装依赖
cd galaxyos/desktop-shell
npm install
python -m pip install -r ../requirements-core.txt pyzmq openai

# 2. 构建 + 启动
npm run dev
```

### 方式 2：直接运行

```bash
# 1. 启 sidecar
cd galaxyos/desktop-shell
python -c "import sys; sys.path.insert(0,'python'); import asyncio; from galaxyos_sidecar import main_async; asyncio.run(main_async())"

# 2. 启 renderer (任意浏览器)
cd galaxyos/desktop-shell/renderer && python -m http.server 8080

# 3. 浏览器打开 http://127.0.0.1:8080
```

### 方式 3：打包后的 GalaxyOS.exe

```bash
cd galaxyos/desktop-shell
# 打 sidecar
cd python && pyinstaller galaxyos-sidecar.spec && cd ..
# 打 Electron
node_modules/.bin/electron-builder --win --dir
# 运行
./release/win-unpacked/GalaxyOS.exe
```

## 配置

### LLM API Key
点 **⚙ 设置**（或 Ctrl+,）→ 填 API Key + Base URL → 保存。Sidecar 热更新 `llm_config.json`，之后所有回答走真实 LLM。

### MCP Servers
编辑 `~/.galaxyos/mcp.json`：
```json
{"servers": [{"name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}]}
```
Sidecar 启动时自动发现 MCP 工具并合并到 Agent 工具注册表。

### Workspace
默认 `~/.galaxyos/workspace/sandbox/`。Agent 的文件操作（read/write/grep）限制在此目录内。

## 文件结构

```
desktop-shell/
├── package.json              # Electron 32 + esbuild + electron-builder
├── esbuild.config.mjs        # bundles main.ts + preload.ts → dist/
├── tsconfig.json             # TypeScript config (main / preload only)
├── galaxyos-sidecar.spec     # PyInstaller config
├── src/
│   ├── main.ts               # Electron main: spawn sidecar, zmq REQ/REP,
│   │                         # zmq PUB→SUB progress events, 19 IPC channels
│   └── preload.ts            # contextBridge → window.galaxy.* (typed API)
├── renderer/
│   ├── index.html            # 3 栏挂载点 (#sidebar-host / #tokui-container
│   │                         #   / #composer-host / #details-host) +
│   │                         #   iw-modal (install wizard)
│   └── src/                  # ESM 模块（按职责切分）
│       ├── main.js           # 入口：bootTokUI + 装配 components/stores
│       ├── tokui/            # TokUI 适配层
│       │   ├── runtime.js    #   UMD lazy-load + stub fallback
│       │   ├── feed.js       #   startStream/feed/endStream 高阶 API
│       │   └── handlers.js   #   msg-action 回调（copy/regen/like/...）
│       ├── components/       # UI 组件（用 TokUI DSL 渲染）
│       │   ├── sidebar.js    #   左栏 [conversations][card][dot]
│       │   ├── composer.js   #   输入栏 [tabs][chat-input]
│       │   ├── details.js    #   右栏 [card][md][tag]
│       │   └── welcome.js    #   首屏 [welcome][feature]
│       ├── state/            # pub-sub 状态（4 个 store）
│       │   ├── store.js      #   30 行最小 store 原语
│       │   ├── session.js    #   多会话 CRUD + localStorage
│       │   ├── connection.js #   sidecar health 探针
│       │   ├── skills.js     #   技能列表缓存
│       │   └── settings.js   #   用户设置 + theme
│       └── ipc/
│           └── client.js     # window.galaxy 桥接（Electron + 独立 SSE）
├── python/
│   ├── galaxyos_sidecar.py   # zmq REP server + 30+ RPC methods
│   ├── path_resolver_desktop.py  # OpenClaw 解耦 shim
│   ├── tokui_dsl.py          # 21 个 DSL builder（process() → TokUI 流）
│   ├── llm_providers.py      # 11 个 provider + 5-slot MultiSlotRouter
│   ├── tools.py              # 6 工具 (shell/read/write/list/grep/diff)
│   ├── agent_loop.py         # Heuristic Agent tool dispatcher
│   ├── memo_adapter.py       # MeMo Memory model (Mock + ONNX stub)
│   ├── memo_stages.py        # Grounding → Entity → Answer protocol
│   ├── executive_client.py   # MeMo Executive (Mock + DeepSeek)
│   ├── ac_router.py          # Agent-as-a-Router C-A-F loop
│   ├── cumulative_regret.py  # Evaluation metric
│   ├── mcp_client.py         # MCP server discovery + tool merge
│   ├── skill_graph.py        # SkillGraph v8.4.1 集成
│   ├── galaxy_agent.py       # create_galaxy_agent() 工厂入口
│   └── tests/                # 6 个 Python 测试文件
├── scripts/
│   ├── dev.mjs               # one-shot dev launcher
│   ├── build-python.sh       # PyInstaller wrapper
│   └── playwright_smoke.py   # visual E2E test
├── build/icon.png            # 512×512 app icon
└── README.md                 # this file
```

## 渲染器模块图

```
main.js
 ├─ tokui/runtime.js          (bootTokUI / setTheme / registerHandler)
 │   └─ window.TokUI (UMD, lazy load + 3s stub fallback)
 ├─ tokui/feed.js             (startStream/feed/endStream)
 ├─ tokui/handlers.js         (msg-action → galaxy.*)
 ├─ state/store.js × 4        (session / skills / connection / settings)
 ├─ components/sidebar.js     ([conversations][card][dot][toolbar])
 ├─ components/composer.js    ([tabs][chat-input])
 ├─ components/details.js     ([card][md][tag])
 ├─ components/welcome.js     ([welcome][feature])
 └─ ipc/client.js → window.galaxy → IPC → main.ts → zmq → sidecar
```

## 设计文档

完整设计 spec：[`docs/superpowers/specs/2026-06-29-galaxyos-desktop-design.md`](../docs/superpowers/specs/2026-06-29-galaxyos-desktop-design.md)

## 仓库

- **Fork**: https://cnb.cool/TIAMO.xianyao/galaxyos-desktop
- **Upstream**: https://cnb.cool/llm-memory-integrat/GalaxyOS
