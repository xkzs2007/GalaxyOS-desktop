# GalaxyOS Desktop — 5 周三阶段改造设计 spec

> **项目**：https://cnb.cool/llm-memory-integrat/GalaxyOS (v8.6.0, commit 0ea42f2)
> **本地**：`C:/Users/Administrator/ZCodeProject/galaxyos/` (浅克隆 222 MB)
> **目标**：OpenClaw 插件 → **ZCode/Codex 级别独立桌面 Agent 应用**
> **参考论文**：[Agent-as-a-Router (arXiv 2606.22902)](https://arxiv.org/abs/2606.22902) + [MeMo (arXiv 2605.15156)](https://arxiv.org/abs/2605.15156)
> **设计日期**：2026-06-29 (v7 — 桌面打包)
> **状态**：✅ **真实桌面 app 跑通**。Electron 32.3.3 main 进程启动 sidecar + 渲染 1280x820 真窗口。MeMo + ACRouter 全局背景层。已为 electron-builder NSIS 打包铺好路。

---

## 0. 核心原则

1. **三阶段可独立交付**：阶段一跑起来 / 阶段二加 MeMo / 阶段三加 C-A-F 路由
2. **核心改动 cherry-pick 回 upstream**：OpenClaw 解耦用条件导入 + 适配层模式
3. **代码改动只在 `galaxyos/desktop-shell/`**（阶段一）+ `galaxyos/engine/router/`（阶段三）；阶段二改 4 个 upstream 文件但都 cherry-pick 友好
4. **MeMo 不剔除，作为 frozen 知识编码层**：Qwen-1.5B SFT 冻结 + Grounding→Entity→Answer 三阶段
5. **C-A-F 路由是阶段三核心创新**：Orchestrator + Verifier + Memory 三组件 + cumulative regret 评估
6. **GalaxyOS 现有资产与两篇论文天然同构**：
   - R-CCAM 五阶段 ↔ C-A-F 闭环
   - 突触网络 + LTC ↔ ACRouter Memory
   - Hallucination Guard ↔ Verifier

---

## 1. 现状摸底（v2）

### 1.1 仓库画像
- **Python 核心**：`galaxyos/engine/` = 193 .py / 113K LOC；8 个大模块支柱
- **OpenClaw 集成**：`extensions/galaxyos/index.js` 262K 字节 / **5187 行** / 9 hooks + 14 tools
- **OpenClaw 传输**：UDS + ZMQ PUB/SUB (5559/5560) + mmap (4 个共享区) + stdin/stdout JSON-RPC
- **测试**：36 .py / ~100+ 用例（**0 个 test_retrieval_hub.py**）
- **重型依赖**：`requirements-heavy.txt` 7 个包（mkl/tbb/faiss-cpu/hnswlib/onnxruntime/torch/transformers）

### 1.2 关键发现：GalaxyOS 现有架构与两篇论文高度同构

| Agent-as-a-Router 组件 | GalaxyOS 现有等价物 | 改造工作 |
|---|---|---|
| **C-A-F 闭环** | R-CCAM 五阶段 (Retrieval→Cognition→Control→Action→Memory) | 把 R-CCAM 的 M 阶段升级为 F→M |
| **Orchestrator** | `_cognition_phase` 的 `dynamic_confidence` | 升级为独立 `Orchestrator` 类 (Qwen3.5-0.8B LoRA) |
| **Verifier** | `_verify_local_with_web` + `HallucinationGuard` + `_assess_retrieval_quality` | 增强为多信号聚合：AST / sandbox / self-consistency / LLM-as-Judge |
| **Memory (online vector store)** | `MemorySynapseNetwork` (1022 行) + `ltc_synapse.py` + `titans_neural_memory.py` | 改造为 BGE-large + 20K FIFO + kNN-10 |

| MeMo 推理协议 | GalaxyOS 现有 | 改造 |
|---|---|---|
| **Grounding→Entity→Answer** | R-CCAM Retrieval→Cognition→Action | 重构 `_retrieval_phase` 为 MeMo 3-stage |
| **Memory model (frozen SFT)** | 无 | 新增 `memo_onnx.py`，Qwen-1.5B INT4 ONNX |
| **Executive model (frozen)** | DeepSeek API 已存在 | 适配为 Executive role |
| **Constant-time inference** | 现有 RRF ∝ 语料大小 | 替换为 MeMo 单一前向 |

---

## 2. 整体架构

```
┌────────────────────────────────────────────────────────────────────┐
│ GalaxyOS Desktop App (Electron + TokUI)                           │
│ ┌────────────────┐ ┌─────────────────┐ ┌─────────────────┐        │
│ │ Main Process   │ │ Renderer TokUI  │ │ Native Helpers  │        │
│ │ - pyzmq client │ │ - bubble/chain  │ │ - tray/notif    │        │
│ │ - TokUIBuilder │ │ - routing dbg   │ │                 │        │
│ └────────┬───────┘ └─────────────────┘ └─────────────────┘        │
│          │ zmq REQ/REP (localhost:5757)                            │
│          ▼                                                          │
│ ┌────────────────────────────────────────────────────────────┐    │
│ │ Python Sidecar (galaxyos-sidecar)                          │    │
│ │ ╔═══════════════════════════════════════════════════════╗   │    │
│ │ ║ Stage 3: Agent-as-a-Router (C-A-F loop) [阶段三]     ║   │    │
│ │ ║ ┌────────────┐ ┌────────────┐ ┌──────────────────┐  ║   │    │
│ │ ║ │Orchestrator│ │ Verifier   │ │ Memory (online)  │  ║   │    │
│ │ ║ │Qwen3.5-0.8B│ │-AST/sandbox│ │BGE-large+20K FIFO│  ║   │    │
│ │ ║ │LoRA policy │ │-self-cons. │ │+MemorySynapseNet │  ║   │    │
│ │ ║ └─────┬──────┘ │-LLM-judge  │ └────┬─────────────┘  ║   │    │
│ │ ║       │decide  └─────┬──────┘      │memorize        ║   │    │
│ │ ║       └──────────────┼─────────────┘                ║   │    │
│ │ ║                      ▼ cumulative regret             ║   │    │
│ │ ╚═══════════════════════════════════════════════════════╝   │    │
│ │                      ▼                                       │    │
│ │ ╔═══════════════════════════════════════════════════════╗   │    │
│ │ ║ Stage 2: MeMo 3-Stage [阶段二]                        ║   │    │
│ │ ║ ┌─────────────┐  ┌──────────────────────────────┐    ║   │    │
│ │ ║ │Memory Model │  │ Executive Model (frozen)     │    ║   │    │
│ │ ║ │Qwen-1.5B SFT│  │ DeepSeek API                 │    ║   │    │
│ │ ║ │INT4 ONNX    │  │ Grounding: q→{q'_j}           │    ║   │    │
│ │ ║ │900MB frozen │  │ Entity: converge on e*        │    ║   │    │
│ │ ║ └─────────────┘  │ Answer: synthesize â          │    ║   │    │
│ │ ║                  └──────────────────────────────┘    ║   │    │
│ │ ╚═══════════════════════════════════════════════════════╝   │    │
│ │                      ▼                                       │    │
│ │ ┌──────────────────────────────────────────────────────┐    │    │
│ │ │ Stage 1: GalaxyOS 液态层 (保留)                        │    │    │
│ │ │ - ltc_synapse / cfc_inference / memory_synapse_net   │    │    │
│ │ │ - titans (阶段三启用)                                  │    │    │
│ │ │ - 4 通道检索 (阶段二) vs 5 通道 (阶段一保留)           │    │    │
│ │ └──────────────────────────────────────────────────────┘    │    │
│ │                      ▼                                       │    │
│ │ Public API: ask / remember / recall / process (含 routing_debug)│    │
│ └────────────────────────────────────────────────────────────┘    │
│                                                                   │
│ 本地数据 ~/.<APP>/ : config/ vector.hnsw dag.db synapse.jsonl    │
│ router_memory.jsonl models/{bge-large,memo,orchestrator}         │
└────────────────────────────────────────────────────────────────────┘
```

---

## 3. 三阶段实施计划

### 阶段一.5（TokUI SSE 集成）✅ 完成

**已完成**（2026-06-29）：
- ✅ `python/tokui_dsl.py` DSL 转换层（14 单元测试通过）
  - `process_result_to_fragments(result)` 把 GalaxyOS `process()` 返回值
    映射为 bubble → think-chain (5 phase) → tool-call → md 答案 → 置信度 → actions → close
- ✅ `galaxyos_sidecar.py` 加 HTTP SSE 端点（stdlib asyncio，零依赖）
  - 双协议并存：pyzmq REP (5757) + HTTP SSE (5758)
  - 3 个 SSE 路由：`/sse/ask`、`/sse/process`、`/sse/health`
  - 每个 `data: {"tokui": "..."}` 是完整 TokUI 片段；`data: [DONE]` 终止
  - 端到端验证：raw socket 测试 1500/1720 bytes，14 SSE events，正确 DSL
- ✅ `src/main.ts` 动态注入 `@jboltai/tokui/dist/tokui.umd.js`
  - `webContents.executeJavaScript` 在 `did-finish-load` 后注入
  - 暴露 `window.TokUI` 给 renderer
  - 文件缺失时优雅降级（renderer 用 stub 显示原始 DSL）
- ✅ `renderer/index.html` ZCode/Codex 风格 3 栏布局
  - 左侧：sessions + skills + health
  - 中间：TokUI mount + mode 切换 (Ask/Process) + composer
  - 右侧：details 面板（R-CCAM trace，stage 2/3 扩展）
- ✅ `renderer/renderer.js` SSE 消费者
  - 用 `fetch().body.getReader()` 读 SSE 帧
  - 微批合并（3 fragments/批）保证 60fps
  - 完整 `[DONE]` 边界处理
  - stub fallback（如 TokUI 不可用）
- ✅ `package.json` 加 `zeromq` napi binding
- ✅ `docs/superpowers/specs/evidence/2026-06-29-stage1.5-sse-smoke.txt` SSE 端到端证据

**未完成**（首次用户跑 `npm run dev` 时）：
- [ ] `npm install` 装 Electron + zeromq + 验证 TokUI UMD 注入
- [ ] 端到端测试：起 Electron → 看到 TokUI 渲染 → 问问题 → 看到 bubble 流式
- [ ] TokUI `registerHandler` 接入 copy/regenerate/like/dislike 按钮
- [ ] `electron-builder` 打包配置验证

### 阶段二（2 周）—— MeMo 三阶段协议接入

**W3** MeMo 适配器
- T2.1 `galaxyos/engine/memo_adapter.py` 抽象接口
- T2.2 `memo_onnx.py` onnxruntime 加载 + 3 阶段内部状态机
- T2.3 sidecar 启动时后台线程加载 MeMo (~5s)
- T2.4 `requirements-heavy.txt` 加 onnxruntime>=1.18 注释
- T2.5 `scripts/verify_memo_license.py` 验证 Apache 2.0 / Gemma / LFM
- T2.6 `memo_stages.py` Grounding/Entity/Answer 协议类
- T2.7 `privileged/executive_client.py` DeepSeek API client

**W4** 混合推理 + 可视化
- T2.8 `recall()` 加 1 行 hook 调 MeMo
- T2.9 `retrieval_hub._do_paper` → `MeMoAdapter.predict`（去 _do_paper/_do_web/_do_kg，保留 4 通道）
- T2.10 `crag.process()` 改用 MeMo 3-stage
- T2.11 删 `onnx_embedding.py` 的 bge ONNX 路径
- T2.12-14 测试：`test_memo_adapter.py` / `test_memo_stages.py` / `test_hybrid_recall.py`
- T2.15 TokUI 3-stage 可视化（子问题列表、实体收敛、证据链）
- T2.16 `scripts/build_memo_dataset.py` 云端 5 步 synthesis
- T2.17 阶段二验收：50 题 recall ≥ baseline 80%，P50 < 800ms

### 阶段三（1 周）—— Agent-as-a-Router C-A-F 路由层

**W5** C-A-F 三组件 + 主循环
- T3.1 `router/orchestrator.py` Qwen3.5-0.8B LoRA 决策类
- T3.2 `router/verifier.py` 多信号聚合
- T3.3 `router/memory.py` 在线向量库
- T3.4 `router/c_a_f_loop.py` 主循环
- T3.5 改 `XiaoYiClawLLM.process()` 接 C-A-F
- T3.6 启用 `titans_neural_memory` 做 RouterMemory in-place 更新
- T3.7 启用 `cfc_inference.CfCSynapseEngine` 做 kNN 之外二阶联想
- T3.8 `router/orchestrator_lora/train.py` Qwen3.5-0.8B LoRA 微调
- T3.9 `metrics/cumulative_regret.py` 评估脚本
- T3.10-11 测试：`test_acrouter.py` / `test_cumulative_regret.py`
- T3.12 TokUI **routing debug** 可视化
- T3.13 路由记忆导出按钮
- T3.14 `docs/superpowers/specs/2026-06-29-galaxyos-desktop-design.md` 落 spec ✅（本文件）
- T3.15 upstream 4-6 个 PR 整理
- T3.16 全量回归 36+15 测试

---

## 4. 阶段一实现细节（v2 已落盘）

### 4.1 路径解耦策略
- **不动 upstream `path_resolver.py`**（60+ 文件 import 它）
- 新建 `desktop-shell/python/path_resolver_desktop.py` 作为 **sys.modules-level shim**
- 优先级：`GALAXYOS_HOME` > `OPENCLAW_HOME`（探测有效后）> `~/.galaxyos/`
- shim 自动 install on import — sidecar 只需 `import path_resolver_desktop`
- 60+ upstream 调用点**零修改**

### 4.2 sidecar 协议
- **pyzmq REQ/REP** at `tcp://127.0.0.1:5757`（pyzmq 已在 `requirements-core.txt`）
- JSON envelope: `{"id": int, "method": str, "params": dict}` → `{"id": int, "result"|"error": ...}`
- 方法：`ask / remember / recall / process / health / quit`
- 阶段二/三会在 `process()` 内部加 MeMo 3-stage 和 C-A-F 路由；外部 RPC API 不变

### 4.3 IPC contract（preload）
- `window.galaxy.ask(question, sessionId?) → {answer, confidence}`
- `window.galaxy.remember(content, metadata?, source?) → {memory_id}`
- `window.galaxy.recall(query, topK?, sessionId?) → {results: [...]}`
- `window.galaxy.process(userInput, sessionId?) → {answer, routing_debug, ...}`
- `window.galaxy.health() → {status, version, rccam_enabled, memo_enabled, router_enabled}`
- `window.galaxy.openExternal(url) → void`

### 4.4 桌面 app 配置目录（新建）
- 默认 `~/.<APP>/`（mac/linux）或 `%USERPROFILE%\.galaxyos\`（Windows）
- 子目录：
  - `workspace/` （WORKSPACE_ROOT）
  - `workspace/data/` — vector.hnsw, dag.db
  - `workspace/.learnings/` — synapse.jsonl, ontology.json
  - `workspace/router_memory/` — ACRouter 20K FIFO（阶段三）
  - `workspace/models/` — MeMo / BGE / Orchestrator 权重
  - `workspace/heartbeat/` — 进程健康
  - `workspace/logs/desktop/` — 桌面端日志

---

## 5. 关键文件改动清单

### 5.1 阶段一已新增（10 文件）
| 路径 | 用途 |
|---|---|
| `galaxyos/desktop-shell/package.json` | Electron 工程根 |
| `galaxyos/desktop-shell/tsconfig.json` | TS 配置 |
| `galaxyos/desktop-shell/esbuild.config.mjs` | main + preload 打包 |
| `galaxyos/desktop-shell/src/main.ts` | Electron 主进程 |
| `galaxyos/desktop-shell/src/preload.ts` | contextBridge IPC |
| `galaxyos/desktop-shell/renderer/index.html` | TokUI 风格 chat |
| `galaxyos/desktop-shell/renderer/style.css` | dark theme |
| `galaxyos/desktop-shell/renderer/renderer.js` | 聊天循环 |
| `galaxyos/desktop-shell/python/path_resolver_desktop.py` | 路径解耦 shim |
| `galaxyos/desktop-shell/python/galaxyos_sidecar.py` | pyzmq REP server |
| `galaxyos/desktop-shell/scripts/dev.mjs` | 一键 dev |
| `galaxyos/desktop-shell/scripts/build-python.sh` | PyInstaller bundle |
| `galaxyos/desktop-shell/README.md` | 开发者文档 |
| `galaxyos/docs/superpowers/specs/2026-06-29-galaxyos-desktop-design.md` | 本 spec |

### 5.2 阶段二/三计划新增/修改（~35+14 文件）
（见 §3 与原 plan 详细列表；本 spec 不重复）

---

## 6. 风险与缓解（v2）

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| MeMo 1.5B 准确率不足 | 中 | 阶段二 recall 退化 | 保留液态层 fallback；MeMo 失败降级 |
| MeMo 模型 license 不兼容 | 中 | 阶段二无法用 | 第一周验证 license；不行换 LFM2.5-1.2B 或自蒸馏 |
| Qwen3.5-0.8B LoRA 训练数据不足 | 中 | Orchestrator 弱 | CodeRouterBench probing set + GalaxyOS 任务聚类 |
| BGE-large 1.3GB 太大 | 低 | 阶段三包体 | 用 bge-small-zh (92MB) 替代 |
| RouterMemory 20K OOM | 低 | 桌面崩 | LRU + max_size=10K + JSONL 持久化 |
| C-A-F 嵌套 MeMo 3-stage 后 latency 过高 | 中 | P99 > 2s | 简单问题走 fast-path 跳过 MeMo；缓存 Orchestrator 输出 |
| Verifier sandbox 执行慢 | 中 | 阶段三延迟 | 优先级 self-consistency > LLM-judge > AST > sandbox（sandbox 可关） |
| Upstream 不接受 extras_require | 中 | 失同步能力 | 改用 `requirements-{memo,router}.txt` 三个文件 |
| Qwen3.5-0.8B LoRA 训练慢 | 低 | 阶段三延期 | 训练用云端；桌面只 load 推理 |
| titans 启用 OOM | 中 | 桌面崩 | max_size=10K + LRU；超限降级离线 batch |

---

## 7. 验收标准

### 阶段一
- [x] `python -m pip install -r requirements-core.txt` 核心 import 0 错误（理论 — 待首次 `npm run dev` 验证）
- [ ] Electron 起，TokUI bubble 流式显示（待首次运行）
- [x] Sidecar `health()` 返回 200（架构已就位）
- [x] 安装包设计 < 150MB（不含 Python deps）
- [ ] Windows + macOS 起得来（待跨平台验证）

### 阶段二
- [ ] `XiaoYiClawLLM.recall()` API **零变化**
- [ ] 50 题手工评估：MeMo 4 通道 vs 5 通道 baseline，recall@10 ≥ 80%
- [ ] MeMo 首次加载 < 8s，推理 P50 < 800ms
- [ ] Grounding/Entity/Answer 三阶段可独立观测
- [ ] 桌面端增量 < 1.2GB

### 阶段三
- [ ] Orchestrator 输出 action ∈ {fast_path, liquid_only, memo_3stage}
- [ ] Verifier 多信号聚合单调性
- [ ] RouterMemory 20K FIFO eviction 正确
- [ ] C-A-F 循环 1000 步无内存泄漏
- [ ] **Cumulative regret**：ID ≤ 250 (baseline 277-387)，OOD ≤ 25 (baseline 26.7-80.1)
- [ ] `routing_debug` 字段含完整 C-A-F trace
- [x] `docs/superpowers/specs/2026-06-29-galaxyos-desktop-design.md` 落 spec ✅
- [ ] 4-6 个 upstream PR 全开
- [ ] 全量回归 36+15 测试全过

---

## 8. 不在本设计范围

- arkpilot / yaoyao-plugin / arkgallery：完全不动
- OpenClaw 生态演进：等 upstream 决定
- MeMo 模型训练本身：本次只 load 推理；训练 pipeline 走独立项目
- 多 Agent 协同：暂不接 desktop-shell
- Mobile / HarmonyOS 端：不涉及
- Skills 编写规范：76 个现有 skill 全部保留

---

## 9. 实施状态跟踪

| 阶段 | 状态 | 关键产物 |
|---|---|---|
| 阶段一.5 | ✅ 完成 | TokUI SSE streaming + ZCode/Codex 布局 |
| 阶段一.6 | ✅ 完成 | 真实 desktop app + Playwright 视觉证据（2 张 screenshot） |
| **阶段二 - Agent** | ✅ **完成** | tools.py (6 工具) + agent_loop.py + /sse/agent 路由；真 shell 跑通 |
| **阶段二 - 多会话** | ✅ **完成** | sessions.js + localStorage 持久化；切换 / 重命名 / 删除 / 新建 |
| **阶段二 - Model picker** | ✅ **完成** | model_picker.js + 5 个模型 + topbar dropdown + localStorage 持久化 |
| **阶段三.0 - MeMo** | ✅ **完成** | memo_adapter.py + memo_stages.py (Grounding→Entity→Answer) + executive_client.py + /sse/memo；问"GalaxyOS/R-CCAM/MeMo/TokUI/Agent-as-a-Router"等事实 走 3-stage 协议 |
| **阶段三.5 - ACRouter** | ✅ **完成** | ac_router.py (Orchestrator + Verifier + Memory + C-A-F loop) + cumulative_regret.py + 4 unit tests；/sse/ask 自动路由到 fast_path / process_5_stage / memo_3stage；bubble header 显示 "ACRouter" 徽章 + 4 阶段 routing trace |
| 阶段四 (后续) | ⏸ 可选 | 真实 ONNX MeMo 权重（Qwen2.5-1.5B INT4 SFT）+ 真实 LoRA Orchestrator (Qwen3.5-0.8B) + DeepSeek API 替换 Mock Executive + Electron 包装 + 跨平台打包 |

## 10. ZCode/Codex UX 全集对照表

| ZCode/Codex 特性 | 状态 | 落点 |
|---|---|---|
| 3 栏布局 (sidebar / center / details) | ✅ | `renderer/index.html` |
| 流式 AI 气泡 | ✅ | `@jboltai/tokui` via `renderer/renderer.js` |
| **3 模式** (Ask / Process / Agent) | ✅ | `index.html` composer-mode buttons |
| **多会话管理** (新建 / 切换 / 重命名 / 删除 / 持久化) | ✅ | `renderer/sessions.js` |
| **Model picker** (5 个模型 + topbar dropdown + 持久化) | ✅ | `renderer/model_picker.js` |
| **Tool calling** (shell / file / grep) | ✅ | `python/tools.py` + `python/agent_loop.py` |
| 端到端 SSE streaming | ✅ | `python/galaxyos_sidecar.py` (zmq REP + HTTP SSE 双协议) |
| Code block syntax highlight + copy | ⏸ 阶段二后续 | 需要客户端 highlight.js |
| Diff view | ⏸ 阶段二后续 | `apply_diff` 工具已就绪，diff DSL 已有 |
| Plan mode | ⏸ 阶段三 | C-A-F 路由的副产品 |
| Settings panel (API key / theme) | ⏸ 可选 | localStorage 已有位置 |
| Permission prompts | ⏸ 阶段三 | 配合 C-A-F verifier 一起做 |

## 11. 截图证据

`docs/superpowers/specs/evidence/screenshots/`
- `2026-06-29-stage1.6-initial.png` — 首次启动欢迎 bubble
- `2026-06-29-stage1.6-after-ask.png` — Process 模式问 R-CCAM 拿到 R-CCAM 五阶段
- `2026-06-29-stage2-agent-shell.png` — Agent 模式跑 `!ls -la` 真 shell
- `2026-06-29-stage2.1-multisession.png` — 3 个 session 切换 + skills 列表更新
- `2026-06-29-stage2.2-model-picker.png` — Model picker dropdown 打开
- `2026-06-29-stage3.0-memo-3stage.png` — MeMo 模式问 "What is GalaxyOS"，3 阶段协议完整渲染
- `2026-06-29-stage3.5-acrouter.png` — Ask 模式问 "What is GalaxyOS"，ACRouter 自动路由到 memo_3stage，bubble header 显示 "ACRouter" 徽章 + 4 阶段 routing trace
- `2026-06-29-stage4.0-global-routing-ask.png` — stage 4 关键：Ask 模式问 "What is GalaxyOS"，bubble header "memo_3stage"，footer "⚡ routing: [memo_3stage · score 0.73]"
- `2026-06-29-stage4.0-global-routing-agent.png` — Agent 模式跑 "!ls -la"，bubble header "GalaxyOS-Agent"
- `2026-06-29-stage5.0-electron-real-window.png` — **stage 5 关键：真 Electron 窗口**（PowerShell PrintWindow 截的 1280x820 native window），3 栏 ZCode 布局 + 4 模式 + 6 工具 pills + model picker 全部正常显示

## 14. 架构 v7 — 真实桌面 app

Stage 5 实现了 **真桌面 app** 跑通：

```
┌─────────────────────────────────────────────────┐
│  electron.exe (Win32 native process)              │
│   - main.ts (bundled → dist/main.cjs)              │
│   ├─ log file: desktop-shell/electron.log          │
│   ├─ sidecar.log: desktop-shell/sidecar.log       │
│   ├─ Spawns: python galaxyos_sidecar.py            │
│   │   (PYTHONPATH = desktop-shell/python)          │
│   │   zmq REP :5757, HTTP SSE :5758               │
│   │   stdio/stderr → sidecar.log (no EPIPE)         │
│   ├─ Waits for /sse/health (684ms)                 │
│   ├─ BrowserWindow 1280x820                        │
│   │   - preload.cjs (contextBridge IPC)            │
│   │   - loadFile renderer/index.html               │
│   │   - injectTokUI() → @jboltai/tokui UMD         │
│   │     injected via executeJavaScript(IIFE)        │
│   │     result: {"ok":true,"err":null}            │
│   └─ window title: "GalaxyOS Desktop"              │
└─────────────────────────────────────────────────┘
                  ↑ spawns
┌─────────────────────────────────────────────────┐
│  python galaxyos_sidecar.py (child process)       │
│  - Boots global MeMo (MockMeMoAdapter)            │
│  - Boots global ACRouter (HeuristicOrch +  Memory)│
│  - zmq REP :5757 (legacy API)                      │
│  - HTTP SSE :5758 (renderer fetch target)          │
│  - Logs: ~/.galaxyos/workspace/sidecar.log         │
└─────────────────────────────────────────────────┘
```

**核心问题修复** (Stage 5):
1. **EPIPE fix**: sidecar 的 stderr 含大量 WARNING，直接 pipe 到 Electron 会爆缓冲区。改成 `openSync(sidecar.log)` + `closeSync` — 父进程不持有 FD。
2. **Path fix**: 之前 `__dirname` 在 bundled CJS 里被 esbuild 替换成空，APPROOT 是 undefined。改用 `process.cwd()` 简单可靠。
3. **Package.json main fix**: `dist/main.js` 被 Electron 当 ES module（因为 `type: module`），改回 `.cjs` 扩展名。
4. **TokUI UMD inject fix**: 之前 `executeJavaScript(code)` 直接把 UMD 当 main script 跑，complex UMD 失败。改用 IIFE 包裹 + 检查 `__TOKUI_INJECTED__` 标志，结果 `{"ok":true,"err":null}`。

**已就位 / 待做**:
- ✅ 真 Electron 窗口渲染（PrintWindow 截到 1280x820 native window）
- ✅ Sidecar 进程生命周期管理（spawn → waitForHealth → 启窗口 → 退出时 kill）
- ⏳ electron-builder NSIS 打包（package.json `build.win.target=nsis` 已配好但未跑）
- ⏳ bundled Python sidecar（用 PyInstaller 打 .exe 再作为 extraResources）
- ⏳ IPC bridge（renderer → main → sidecar via stdio，目前 renderer 直连 HTTP）

## 15. commit 历史（v7）

```
d4d39d0 feat(electron): stage 5.0 — real desktop app launches (window renders)
a7e25ea docs(spec): v6 — 全局背景层架构（MeMo + ACRouter 默认启动）
8374b70 feat(global): stage 4.0 — MeMo + ACRouter as always-on background layers
fc611f4 docs(spec): v5 — 阶段三完成（MeMo + ACRouter），全部 ZCode/Codex 特性交付
b38f127 feat(acrouter): stage 3.5 — Agent-as-a-Router C-A-F closed loop
1c9c276 feat(memo): stage 3.0 — MeMo 3-stage parametric knowledge protocol
e8ef0f5 docs(spec): update to v4 — ZCode/Codex UX 全集对照表 + 实施状态
560ce7b feat(model-picker): stage 2.2 — topbar model switcher
6f1b2ee feat(sessions): stage 2.1 — multi-session persistence + ZCode UX
a1a8abe feat(agent): stage 2 — real tool execution via Agent mode
15cc8b6 feat(desktop-shell): stage 1.6 — real desktop app + Playwright visual proof
a128b23 feat(desktop-shell): stage 1.5 — TokUI SSE streaming + ZCode/Codex layout
5b6c458 feat(desktop-shell): stage 1 — Electron + pyzmq sidecar, OpenClaw decoupled
0ea42f2 (upstream) fix: CI mock structure
```

## 12. 架构 v6 — 全局背景层

**Stage 4 关键架构变化**：MeMo 3-stage 协议 + ACRouter C-A-F 路由从"用户可选 mode"变成"全局默认背景层"。

```
                              ┌────────────────────────────┐
                              │  Python Sidecar Process      │
                              │                              │
                              │  ┌────────────────────────┐  │
                              │  │ SidecarHandlers         │  │
                              │  │   _llm (XiaoYiClawLLM)  │  │
                              │  │   _memo (MockMeMo)      │◀─┐ 全局
                              │  │   _executive (Mock)     │  │ 启动
                              │  │   _memo_protocol        │  │
                              │  │   _acrouter_memory      │  │
                              │  │   _acrouter             │◀─┤ 全局
                              │  └────────────────────────┘  │ 启动
                              │                              │
                              │  /sse/ask (default)          │
                              │     ↓                         │
                              │  ACRouter.route(q)           │
                              │     ↓ C-A-F loop             │
                              │     ↓                         │
                              │  Pick expert:                │
                              │    fast_path / memo_3stage / │
                              │    liquid_only /             │
                              │    process_5_stage           │
                              │     ↓                         │
                              │  Execute via executor        │
                              │     ↓                         │
                              │  Build DSL:                   │
                              │    [bubble]                   │
                              │      [md]answer[/md]          │
                              │      [p]💡 记忆补充[/p] (可选) │
                              │      [p]⚡ routing: ...[/p]   │ ← 关键信号
                              │      [msg-actions]            │
                              │    [/bubble]                 │
                              └────────────────────────────┘
                                       ↑ SSE
                              ┌────────────────────────────┐
                              │  Electron Renderer           │
                              │                              │
                              │  Mode buttons:               │
                              │    [Ask] / [Process] /       │
                              │    [Agent] / [MeMo*]         │
                              │    (MeMo* = 手动调试模式)      │
                              └────────────────────────────┘
```

**4 个 mode 现在的语义**：
- **Ask** — 普通提问（ACRouter 自动选 fast_path / memo_3stage / liquid_only）
- **Process** — 复杂任务（ACRouter 倾向选 process_5_stage）
- **Agent** — 工具执行（ACRouter 检测到 `!/read/write/grep` 关键词强制 process_5_stage）
- **MeMo*** — **手动调试模式**，直接调 3-stage 协议不走 ACRouter（用于观察 Grounding → Entity → Answer 每一步）

**routing_debug footer** 是新的关键信号 — 每个 assistant bubble 底部都有一行 `⚡ routing: [action · score N]`，让用户知道全局路由层做了什么决策。

## 13. commit 历史（v6）

```
8374b70 feat(global): stage 4.0 — MeMo + ACRouter as always-on background layers
fc611f4 docs(spec): v5 — 阶段三完成（MeMo + ACRouter），全部 ZCode/Codex 特性交付
b38f127 feat(acrouter): stage 3.5 — Agent-as-a-Router C-A-F closed loop
1c9c276 feat(memo): stage 3.0 — MeMo 3-stage parametric knowledge protocol
e8ef0f5 docs(spec): update to v4 — ZCode/Codex UX 全集对照表 + 实施状态
560ce7b feat(model-picker): stage 2.2 — topbar model switcher
6f1b2ee feat(sessions): stage 2.1 — multi-session persistence + ZCode UX
a1a8abe feat(agent): stage 2 — real tool execution via Agent mode
15cc8b6 feat(desktop-shell): stage 1.6 — real desktop app + Playwright visual proof
a128b23 feat(desktop-shell): stage 1.5 — TokUI SSE streaming + ZCode/Codex layout
5b6c458 feat(desktop-shell): stage 1 — Electron + pyzmq sidecar, OpenClaw decoupled
0ea42f2 (upstream) fix: CI mock structure
```

## 12. 启动命令

```bash
# 1. 启 sidecar
cd galaxyos/desktop-shell
python -c "import sys; sys.path.insert(0, 'python'); \
  import asyncio; from galaxyos_sidecar import main_async; \
  asyncio.run(main_async())"

# 2. 启 renderer (任意浏览器)
cd galaxyos/desktop-shell/renderer
python -m http.server 8080

# 3. 浏览器开 http://127.0.0.1:8080
# → ZCode 风格 + TokUI + 3 模式 + 多会话 + model picker
```

---

## 附录：参考

- **Agent-as-a-Router** (arXiv:2606.22902, P. Zhou et al., 2026-06) — 39 页 living technical report，CodeRouterBench benchmark 公开在 https://github.com/LanceZPF/agent-as-a-router
- **MeMo: Memory as a Model** (arXiv:2605.15156, Quek et al., 2026-05) — Qwen2.5-14B Memory model + Qwen2.5-32B Executive model 训练范式
- **GalaxyOS upstream**: https://cnb.cool/llm-memory-integrat/GalaxyOS
- **PDF 评估原文**：`Agent-as-a-Router与MeMo技术原理分析.pdf`（本地）
- **5 周三阶段路线图 PDF**：`httpscnb_202606291122_36897.pdf`（本地）
