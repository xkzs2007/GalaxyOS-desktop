# 🌌 GalaxyOS — 独立 Agent APP 框架

> 为 LLM 提供记忆、检索、推理、验证、自进化的全套认知能力，**自带桌面端 Agent 应用**
>
> **v9.4** · MultiSlotRouter 5-slot optional（LLM 必填，其余可选）· 脱离 OpenClaw 独立运行

---

## 🖥️ GalaxyOS Desktop（独立桌面 Agent APP）

类似 ZCode / Codex 的桌面端 AI 体验，开箱即用：

```
desktop-shell/
├── python/
│   ├── galaxyos_sidecar.py   # SidecarHandlers (zmq + SSE 双传输)
│   ├── llm_providers.py      # MultiSlotRouter (11 provider / 5 slot)
│   └── tokui_dsl.py          # 流式 UI DSL (21 builder)
├── renderer/
│   ├── index.html            # 3 栏 ZCode 布局
│   ├── renderer.js           # SSE 消费者 + 消息操作
│   └── model_picker.js       # 4 组 provider 目录（主流/本地/自定义/离线）
├── src/                      # Electron 主进程 (TypeScript)
└── package.json
```

**功能**：3 栏布局 · TokUI 流式 AI 气泡 · Agent 工具（shell/read/write/grep/diff）· 69 skills 搜索+调用 · 多会话持久化 · 多 LLM 切换 · MeMo 3-stage 全局记忆 · Agent-as-a-Router C-A-F 路由 · 键盘快捷键 · Diff view · 设置面板（5 tab：通用/LLM/Embedding/Rerank/VLM）

**快速启动**：
```bash
cd desktop-shell
python -c "import sys; sys.path.insert(0,'python'); import asyncio; \
  from galaxyos_sidecar import main_async; asyncio.run(main_async())"
# 浏览器打开 http://127.0.0.1:8080
```

详见 [`desktop-shell/README.md`](desktop-shell/README.md) 和 [`docs/superpowers/specs/2026-06-29-galaxyos-desktop-design.md`](docs/superpowers/specs/2026-06-29-galaxyos-desktop-design.md)

---

## 🐍 GalaxyOS Harness（Python 库）

GalaxyOS v9.0 起成为**独立 Python Agent 框架**——`create_galaxy_agent()` 入口对标 openJiuwen 的 `create_deep_agent()`：

```python
from galaxyos.harness import create_galaxy_agent

agent = create_galaxy_agent(
    name="assistant",
    model="lfm2.5-1.2b-instruct",   # 或 "anthropic/claude-3-5-sonnet"
    memory="vector",                 # vector | liquid | mock
    skill_graph=True,
)
result = await agent.run("列出我的技能")
print(result["result"])
```

**5 大件**（harness 视角）：

| 组件 | 模块 | 说明 |
|------|------|------|
| 1. Agent Loop | `harness.deep_agent.DeepAgent` | async 优先，TaskLoopEvent 三件套 |
| 2. Tool Registry | `harness.desktop_shell_compat.tools` | 6 工具：shell/read/write/grep/diff/list |
| 3. LLM Client | `harness.workspace.llm` | 可注入；MultiSlotRouter 路由 |
| 4. Memory System | `harness.workspace.memory` | vector + liquid + mock 三选一 |
| 5. SkillGraph | `harness.workspace.skills` | 69 节点 / 278 边的技能图 |

`SidecarBackend`（`harness/sidecar_bridge.py`）桥接 DeepAgent ↔ SidecarHandlers，让 harness 跑桌面端同一套 76 skills 栈。

---

## 核心能力

| 能力 | 说明 |
|------|------|
| **液态神经记忆** | LTC 突触 + CfC 推理 + NCP 神经电路 + 仿生遗忘曲线 |
| **DAG 上下文** | SQLite 持久化 + 摘要节点回溯 + 时间衰减排序 |
| **SkillGraph 自演化** | 69 节点 278 边有向图 + 邻接检索 + GRPO 优化 |
| **LFM 技能库** | 5 维评分（质量/复用/合约/一致/探索）+ 合并·拆分·精修·淘汰 |
| **R-CCAM 认知循环** | Retrieval→Cognition→Control→Action→Memory 五阶段 |
| **MeMo 3-stage** | Grounding → Entity → Answer 全局记忆协议 |
| **ACRouter C-A-F** | Agent-as-a-Router：复杂度感知分发 |
| **MultiSlotRouter** | 11 provider × 5 slot（LLM 必填，其余可选） |
| **TokUI DSL** | 21 builder 流式 UI 协议（SSE `data: {tokui: "..."}`） |
| **Harness + Sidecar** | 同进程双形态：Python 库 / 桌面 APP |

---

## 快速开始

```bash
# 1. 克隆 + 装依赖
git clone https://cnb.cool/llm-memory-integrat/GalaxyOS.git
cd GalaxyOS
pip install -r requirements.txt

# 2. 跑 Python 库
python3.12 -c "
from galaxyos.harness import create_galaxy_agent
import asyncio
agent = create_galaxy_agent(name='demo', model='mock-1')
print(asyncio.run(agent.run('hi')))
"

# 3. 跑桌面端（另一个终端）
cd desktop-shell && python python/galaxyos_sidecar.py
# 浏览器打开 http://127.0.0.1:8080
```

> v9.2 起支持 LLM provider 简写：`create_galaxy_agent(model="anthropic/claude-3-5-sonnet")` 直接走 Anthropic 端点。

---

## LLM Provider 配置（v9.2–v9.4）

GalaxyOS **不绑定任何远端 LLM**。`MultiSlotRouter` 管理 5 个独立 slot：

| Slot | 必填？ | 典型用途 | 未配置时回退 |
|------|--------|----------|------------|
| `llm` | ✅ 必填 | 主对话推理 | 无（必须配置） |
| `llm_pro` | ❌ 可选 | 复杂任务升级 | `llm` slot |
| `embedding` | ❌ 可选 | 向量检索 | BoW 检索（`ac_router.py`） |
| `rerank` | ❌ 可选 | 检索重排 | 原始 top-k |
| `vlm` | ❌ 可选 | 图片 OCR / 多模态 | "VLM 未配置" 提示 |

**11 个支持的 provider**（`MAINSTREAM_PROVIDERS` 目录）：

| 类别 | Provider | 默认模型 |
|------|----------|----------|
| 主流 | OpenAI / DeepSeek / Qwen DashScope / Anthropic / Google Gemini | 各自旗舰 |
| 托管 | SiliconFlow / OpenRouter | 多模型聚合 |
| 本地 | Ollama / vLLM | 开源 LLM |
| 自定义 | Custom (OpenAI 兼容) | 用户填 |
| 离线 | Mock | mock-1（脱机回声） |

**配置方式**：Settings 4 tab（LLM/Embedding/Rerank/VLM）+ 启用复选框，**不勾 = 走本地 fallback**。

详见 `desktop-shell/python/llm_providers.py` + `docs/API.md`。

---

## 架构

```
┌──────────────────────────────────────────────────────────────────┐
│                   GalaxyOS v9.4 — 两大入口                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────────────┐    ┌─────────────────────────────┐  │
│  │   Harness (Python lib)  │    │   Desktop Shell (Electron)  │  │
│  │  create_galaxy_agent() │    │  3 栏 ZCode 布局            │  │
│  │  DeepAgent + Workspace │    │  renderer + SidecarHandlers │  │
│  │  + TaskLoopEvent       │    │  (zmq + SSE 双传输)         │  │
│  └────────────┬───────────┘    └────────────┬────────────────┘  │
│               │                              │                   │
│               └──────────────┬───────────────┘                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              galaxyos/ — 核心运行时                         │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │  harness/         DeepAgent / Workspace / TaskLoop        │  │
│  │  engine/          MeMo 3-stage + R-CCAM 5 阶段            │  │
│  │  orchestration/   ACRouter C-A-F + SkillGraph            │  │
│  │  privileged/      ACP 调试端点（可选）                     │  │
│  │  scripts/         install_wizard / skill_version_check    │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              services/ — 检索/记忆/认知层                   │  │
│  │  retrieval_hub / hybrid_search / unified_vector_store     │  │
│  │  crag / rag_optimizer / memory_consolidation              │  │
│  │  cognitive_map / chain_of_verification / hallucination    │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              skills/ — 69 节点 278 边技能图                 │  │
│  │  skill-creator / proactive-tasks / find-skills / ...      │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              LLM Providers — 11 provider × 5 slot          │  │
│  │  OpenAI / Anthropic / DeepSeek / Qwen / Gemini /          │  │
│  │  SiliconFlow / OpenRouter / Ollama / vLLM / Custom / Mock │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## R-CCAM 5 阶段

```
Retrieval → Cognition → Control → Action → Memory
    │            │          │         │        │
    ▼            ▼          ▼         ▼        ▼
  检索候选    推理分析    路由决策    工具执行    写回记忆
  (BGE/BoW)  (LLM)    (ACRouter)  (shell/IO)   (vector+engram)
```

每阶段通过 `PhaseState` 对象传递（`services/rccam_state.py`），从 God Object 模式提取。

---

## 目录结构

```
GalaxyOS/
├── galaxyos/                    # 核心 Python 包
│   ├── harness/                 # DeepAgent + Workspace + TaskLoop + sidecar_bridge
│   ├── engine/                  # MeMo + R-CCAM + SkillGraph + ACRouter
│   ├── orchestration/           # 跨模块编排
│   ├── privileged/              # 调试端点（ACP server）
│   ├── scripts/                 # install_wizard / skill_version_check
│   └── shared/                  # 公共工具
├── desktop-shell/               # 桌面端 Agent APP
│   ├── python/                  # sidecar + LLM providers + TokUI DSL
│   ├── renderer/                # HTML/JS 前端
│   ├── src/                     # Electron 主进程
│   └── package.json
├── services/                    # 检索/记忆/认知层模块
│   ├── retrieval_hub.py
│   ├── hybrid_search.py
│   ├── crag.py / crag_pipeline.py
│   ├── memory_consolidation.py
│   ├── cognitive_map.py
│   ├── chain_of_verification.py
│   ├── enhanced_hallucination_guard.py
│   ├── rccam_state.py
│   └── ...
├── skills/                      # 69 节点技能图（含 skill-creator / proactive-tasks / find-skills）
├── legacy/                      # 旧版 OpenClaw 实现（保留历史，不推荐使用）
├── models/                      # 预训练模型（LFM ONNX 等）
├── data/                        # 持久化数据（向量库 / 记忆）
├── extensions/                  # 第三方扩展（cli-anything 等）
├── docs/                        # API 速查 / 设计文档 / 论文路线图
├── tests/                       # 单元测试
├── bin/                         # 命令行工具
├── core/                        # 核心抽象
├── governance/                  # 治理规则
├── patches/                     # 补丁脚本
├── backups/                     # 备份
├── conftest.py                  # pytest 路径注入
├── pyproject.toml
├── requirements.txt / requirements-core.txt / requirements-heavy.txt
├── Makefile
├── CHANGELOG.md
├── CONTRIBUTING.md
├── docs/API.md                  # 完整 API 速查
└── VERSION
```

---

## 版本历史

### v9.4 (2026-06-30) — MultiSlotRouter 5-slot optional

- MultiSlotRouter 新增 `vlm` slot，扩到 5 个独立槽
- 每个 slot 默认 `enabled=False`，调用方按需启用
- `is_enabled()` / `disable_slot()` 新 API
- Settings UI 4 tab 加"启用"复选框，LLM 默认开，其余默认关
- sidecar `set_config` 区分"主动禁用"vs"保持现状"，未传 slot 不再 reset
- 只有 `llm` slot 变化时才重建 Executive（embedding/rerank/vlm 切换不打断 LLM 流）
- +10 MultiSlotRouter 测试 + 9 sidecar set_config 测试（70/70 v9.x 测试通过）
- 删根目录 `SKILL.md`（OpenClaw 阶段残留，v9 不再需要）

### v9.3 (2026-06-29) — TokUI DSL 扩展 + 4-tab 设置

- `tokui_dsl.py` 从 6 builder 扩到 21（progress / upd / callout / stat / code / tag / source / quick-reply / suggestion / latency / diff / artifact / welcome / tool-result / loop-progress / plan-step with step_id）
- Settings UI 加 4 tab（LLM / Embedding / Rerank / VLM）+ provider 目录
- plan-step 加 `[upd id:plan_step_N status:success]` 翻转协议
- httpx.MockTransport 测试验证 Bearer / x-api-key / SSE 解析

### v9.2 (2026-06-29) — Multi-provider LLM layer

- 11 provider 支持（OpenAI / DeepSeek / Qwen / Anthropic / Google / SiliconFlow / OpenRouter / Ollama / vLLM / Custom / Mock）
- 纯 httpx 实现（无 SDK 依赖）
- MultiSlotRouter 4 slot（llm / llm_pro / embedding / rerank）
- 前端 Model picker 4 组目录（主流 / 本地 / 自定义 / 离线）
- 34 unit test（provider 路由 / 多 slot / mock transport / Anthropic 协议）

### v9.1 (2026-06-29) — SidecarBackend 桥接

- `harness/sidecar_bridge.py::SidecarBackend` in-process 桥接 DeepAgent ↔ SidecarHandlers
- `ProviderBackendWrapper` 包装任意 LLMBackend 让 DeepAgent.run() 走 httpx
- 17 harness sidecar bridge 测试

### v9.0 (2026-06-29) — 独立 Agent APP 框架

- 砍 OpenClaw 包袱，独立 Python 包
- `galaxyos.harness` 顶层入口（对标 openJiuwen `create_deep_agent`）
- DeepAgent / Workspace / TaskLoopEvent 三件套
- SidecarHandlers 30+ RPC 方法复用
- 桌面端双形态（harness lib + desktop-shell app）

### v8.6.0 (2026-06-28) — OpenClaw 深度集成（历史）

> v8.6.0 是 GalaxyOS 与 OpenClaw 集成的最后一个版本。v9.0 起 GalaxyOS 独立运行，**不再依赖 OpenClaw gateway / slots / hooks**。如需旧集成方式，代码仍在 `legacy/openclaw/`。

---

## 生态

- **[docs/API.md](docs/API.md)** — 完整 API 速查
- **[docs/paper-roadmap.md](docs/paper-roadmap.md)** — 论文路线图（R-CCAM / MeMo / COSPLAY）
- **[docs/superpowers/](docs/superpowers/)** — 设计文档 + 评审记录
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — 贡献指南
- **[CHANGELOG.md](CHANGELOG.md)** — 完整变更日志
- **[galaxyos/harness/](galaxyos/harness/)** — harness API docstring

---

## 开发

| 命令 | 说明 |
|------|------|
| `make test` | 运行单元测试 |
| `python3.12 -m galaxyos.scripts.install_wizard --check` | 自检 |
| `cd desktop-shell && python python/galaxyos_sidecar.py` | 启动桌面端 sidecar |
| `cd desktop-shell && python -m http.server 8080` | 启动前端 |

---

## 许可证

MIT License — 详见 [LICENSE](LICENSE)
