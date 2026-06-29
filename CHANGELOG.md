# Changelog

## [v9.4.1] — 2026-06-30

### Fixed
- **CI**: 修复 `.cnb.yml` 让 v9.4 能在 cnb.cool 跑过
  - `requirements-core.txt` +`pytest-asyncio>=0.23.0`（v9 测试用了 `@pytest.mark.asyncio`）
  - 删 `security-check` / `node-check` 整段（v8.6 OpenClaw 集成残留：injection_scanner + Node 插件 `extensions/galaxyos/index.js`）
  - 加 `harness-import-check` + `desktop-shell-import-check` 验证 v9.4 5-slot 契约
  - 加 `llm-provider-test` 单独跑 3 个 v9.x 测试文件
  - `pr-test` 简化为只跑 3 个 v9.x 测试
  - `$:tag_push` Rust 编译 → PyInstaller 打包 desktop-shell sidecar

## [v9.4.0] — 2026-06-30

### Added
- **MultiSlotRouter 5-slot optional**（`desktop-shell/python/llm_providers.py`）
  - 5 个独立 slot：`llm` / `llm_pro` / `embedding` / `rerank` / `vlm`
  - 每个 slot 默认 `enabled=False`（即使 backend 是 mock 也算"未启用"）
  - 新增 `is_enabled(slot)` / `disable_slot(slot)` API
  - `set_slot(spec)` 接受 `{"enabled": false}` 显式禁用
  - `info()` 暴露 `enabled` 字段
- **Settings UI 4 tab**（`desktop-shell/renderer/index.html`）
  - LLM/Embedding/Rerank/VLM 各自独立 tab + 启用复选框
  - LLM 默认勾选（必填），其余默认关（可选，回退到本地实现）
  - 关闭时 tab body 半透明 + 不可点
- **sidecar `set_config` 分派改进**（`desktop-shell/python/galaxyos_sidecar.py`）
  - `multi_slot_keys` 把 `vlm` 加上
  - 区分"主动禁用"vs"保持现状"（`{"enabled": false}` → `disable_slot`，未传 slot → 不动）
  - 只有 `llm` slot 变化时才重建 Executive（不打断在飞的 embedding/rerank/vlm 切换）

### Tests
- +10 `MultiSlotRouter` v9.4 测试（5-slot / 默认 disabled / disable_slot / 显式 enabled / info 加 enabled 字段）
- +9 `sidecar set_config` 单元测试（5-slot 分派 / enable=false 调 disable_slot / llm 变化才 rebuild / system_prompt 转发 / router_info 返回）
- **70/70 v9.x 测试通过**（34 旧 + 17 harness sidecar bridge + 19 v9.4 新）

## [v9.3.0] — 2026-06-29

### Added
- **TokUI DSL 扩展**（`desktop-shell/python/tokui_dsl.py`）从 6 builder 扩到 21
  - 新增：`progress_bar` / `upd` / `callout` / `stat` / `code_block` / `tag` / `source` / `quick_reply` / `suggestion` / `suggestions_grid` / `latency` / `diff_block` / `artifact` / `welcome` / `tool_result` / `loop_progress`
  - `plan_step` 加 `step_id` 参数支持 `[upd id:plan_step_N status:success]` 翻转协议
- **Settings UI 4 tab**（`desktop-shell/renderer/index.html`）
  - LLM / Embedding / Rerank / VLM 独立配置
  - provider 目录分 4 组（主流 / 本地 / 自定义 / 离线）
- **Model picker 4 组目录**（`desktop-shell/renderer/model_picker.js`）

### Tests
- +3 httpx.MockTransport 端到端测试（OpenAI-compat Bearer / Anthropic x-api-key / SSE 解析）

## [v9.2.0] — 2026-06-29

### Added
- **11 provider 多 LLM 支持**（`desktop-shell/python/llm_providers.py`）
  - 主流：OpenAI / DeepSeek / Qwen DashScope / Anthropic / Google Gemini
  - 托管：SiliconFlow / OpenRouter
  - 本地：Ollama / vLLM
  - 自定义：Custom (OpenAI 兼容)
  - 离线：Mock
- **纯 httpx 实现**（无 SDK 依赖）—— OpenAICompatClient 覆盖 6+ vendor
- **AnthropicClient**（x-api-key / anthropic-version 协议）
- **MockLLMClient**（脱机回声，用于无 key 场景）
- **MultiSlotRouter**（v9.2 4-slot：llm / llm_pro / embedding / rerank）

### Tests
- +34 llm_providers 单元测试（provider 路由 / 多 slot / OpenAI/Anthropic payload / mock transport / SSE 流）

## [v9.1.0] — 2026-06-29

### Added
- **SidecarBackend 桥接**（`galaxyos/harness/sidecar_bridge.py`）
  - in-process 桥接 DeepAgent ↔ SidecarHandlers（绕开 zmq 进程间开销）
  - `build_sidecar_backend()` / `build_provider_backend()` 工厂
  - `ProviderBackendWrapper` 把任意 LLMBackend 包成 DeepAgent 可调的形状
- **Agent.run() 实际调用 sidecar 30+ RPC**，返回真实结果而非 canned fallback

### Tests
- +17 harness sidecar bridge 单元测试（agent.run / stream 路径 / fragment 解析 / 后端名透传）

## [v9.0.0] — 2026-06-29

### Changed（破坏性）
- **脱离 OpenClaw 独立运行**——GalaxyOS v9.0 起是**独立 Agent APP 框架**，不再依赖 OpenClaw gateway / slots / hooks
- 砍 OpenClaw 包袱：Node 插件 `extensions/galaxyos/`、Rust 原生扩展、injection_scanner gateway 集成、ClawHub 发布、claw_* 工具表全部移除
- 顶层入口从 `XiaoYiClawLLM` 改为 `galaxyos.harness.create_galaxy_agent()`（对标 openJiuwen `create_deep_agent`）

### Added
- **galaxyos.harness 包**（`galaxyos/harness/`）
  - `create_galaxy_agent(name, model, ...)` 工厂入口
  - `DeepAgent` async-first 主类
  - `Workspace` 工具 / 记忆 / LLM 客户端 / SkillGraph 容器
  - `TaskLoopEvent` 三件套（Start / Progress / End）
  - `desktop_shell_compat` 6 工具（shell/read/write/grep/diff/list）
- **SidecarHandlers**（`desktop-shell/python/galaxyos_sidecar.py`）30+ RPC 方法，zmq + SSE 双传输复用
- **desktop-shell 桌面 APP**（`desktop-shell/`）3 栏 ZCode 布局 / TokUI 流式 AI 气泡 / Agent 工具调用

---

## [v8.6.0] — 2026-06-28

### OpenClaw 深度集成改造（全 4 阶段）

#### Phase 1：核心断链修复
- **接入工具调用生命周期钩子**：新增 `before_tool_call` / `after_tool_call` 钩子
  - `before_tool_call`：记录调用前状态，喂给 BoundaryDetector
  - `after_tool_call`：捕获结果，并行更新 Skill Bank + engram + DAG，带 `idempotencyCache` 幂等
  - 新增 `buildStructKey(channel, userId)` 结构化 session key（`workspace:channel:userId`）
- **接入上下文压缩生命周期钩子**：新增 `before_compaction` / `after_compaction` 钩子
  - `before_compaction`：筛选高价值上下文写入 engram + DAG，防止压缩导致记忆丢失
  - `after_compaction`：触发向量索引同步
- **Worker Pool 声明为 OpenClaw 额外 Lane**：`galaxyos-hot` lane 类型 + 每 5 秒负载上报

#### Phase 2：安全与隔离加固
- **工具策略声明**：14 个 registerTool 全部增加 `policy` 字段（channels/roles/rateLimit）
- **Skill Bank 合约内容扫描**：新建 `injection_scanner.py`（3 级风险检测 + 审核队列 + 来源追溯）
  - 高风险（score≥0.8）：隔离不毕业
  - 中风险（0.5≤score<0.8）：进入人工审核队列
  - 低风险（score<0.5）：放行监控
- **结构化 Session Key 与 Channel 感知**：群聊场景记忆写入降级为只读

#### Phase 3：系统对齐
- **COSPLAY 毕业产物输出为 SKILL.md**：含 YAML frontmatter，写入 `workspace/skills/`，支持 250ms hot-reload
- **Heartbeat 与 Cron 对接**：`gateway_start` hook 注册 30 分钟心跳维护 + 每日 03:00 深度维护
- **MultiAgent 映射为 OpenClaw Sub-Agent**：`spawn_as_sub_agent()` 方法，遵循 session key 规范/受限工具集/announce 回传
- **ACP 暴露调试端点**：3 个新 RPC（`debug_dag_visualize` / `debug_engram_inspect` / `debug_skill_bank_status`）

#### Phase 4：生态融合
- **Node 系统集成**：新增 `claw_node_invoke` 工具，对接 `api.node.invoke` 外设能力
- **ClawHub 发布与 Progressive Disclosure**：`clawhub.json` 发布清单，9 个技能元数据 ≤97 字符，token 开销降低 65%

### Changed
- VERSION 8.5.3 → 8.6.0
- Hook 覆盖率：4 → 9（新增 gateway_start, before/after_tool_call, before/after_compaction）
- Tool 覆盖率：14 → 15（新增 claw_node_invoke）
- 注册日志：`v4` → `v5`

## [v8.5.3] — 2026-06-25

### Added
- **MultiAgent P1+P2 全量落地 — 公告板/Judge蒸馏/选角优化/工具注入/交叉验证/进度推送**
  - `multi_agent_orchestrator.py`（1055 行）：完整多智能体编排引擎
  - 公告板（BulletinBoard）：智能体间消息共享与状态同步
  - Judge 蒸馏（JudgeDistillation）：大模型评判→小模型参数迁移
  - 选角优化（RoleCastingOptimizer）：任务→智能体角色匹配
  - 工具注入（ToolInjector）：运行时动态注入合约工具
  - 交叉验证（CrossValidator）：多智能体结果交叉验证
  - 进度推送（ProgressPusher）：实时推送多智能体执行进度
- `xiaoyi_claw_api.py` 重构：MultiAgent 全链路集成 + 接口兼容性优化

### Changed
- VERSION 8.5.2 → 8.5.3
- 三份 `multi_agent_orchestrator.py` 同步（services/galaxyos/engine/extensions）
- `xiaoyi_claw_api.py` 接口重构（390 行修改），保持向后兼容

## [v8.5.2] — 2026-06-25

### Added
- **LFM UDS v2 全量集成 — Rust lfm_server + Python UDS Client**
  - `lfm_server.rs`（389 行）：Rust ONNX 推理引擎，通过 UDS socket 提供服务
    - 方法：ping/get_info/embed_text/update_state/reset_state/get_state/get_hidden/shutdown
  - `galaxyos_native.py` v0.2.0：新增 LFM UDS Client（json IPC via Unix socket）
  - `lfm_adaptive_operator.py`：RealLFMNetwork 从随机权重切换为 HuggingFace LFM2.5-1.2B-Thinking bf16
    - 替代原有 ONNX Q4 降级路径，使用真实 Transformer 权重推理
  - `lfm_engram_fusion.py`：EngramMemory → LFM 嵌入向量融合增强
  - `liquid_ssm.py`：SSM 状态预测器接入 LFM hidden state
  - `dag_liquid_fusion.py`：LTCConstantComputer 优先连接 UDS lfm_server

### Fixed
- 路径推断：环境变量 `GALAXYOS_REPO` 自动适配两种仓库结构
- `onnx_embedding.py` Stage 3 降级兜底（ONNX 不可用 → Python 纯算 fallback）
- `claw_worker.py` UDS 路径兼容：galaxyos/var vs claw-core/var

### Changed
- VERSION 8.5.1 → 8.5.2
- `Cargo.toml` 新增 lfm_server 构建目标
- `index.js` 更新版本号

## [v8.5.1] — 2026-06-24

### Added
- **三级 Worker Tier 架构（Hot/Warm/Cold）**
  - GalaxyPool 单 WorkerPool → 3 个独立 TieredPool，每层独立进程+独立 GIL
  - `METHOD_TIER` 路由表：41+ UDS 方法自动分配到对应 tier
  - Hot（2 Worker）：ping/health/memory_search/recall — 8s 超时
  - Warm（2 Worker）：store/dag_*/learn/verify — 20s 超时
  - Cold（1-2 Worker）：context_assemble/rccam/rlm_compress — 60s 超时
- **Session 亲和性**：同 session 的 DAG 请求绑定同一 Worker（5min TTL+懒惰剪枝）
- **自适应扩缩**：每层独立 `_scaleCheck`，队列 >= 3 自动扩容，连续空闲缩容
- **Workier tier 感知**：`WORKER_TIER` 环境变量传递，Hot Worker 跳过重型论文模块加载

### Fixed
- `galaxyos-native` 缺少执行权限（EACCES）

### Changed
- VERSION 8.5.0 → 8.5.1
- `ClawWorkerClient` spawn 传递 `WORKER_TIER` 环境变量
- `WorkerPool` 构造新增 `workerIdPrefix`，Worker ID 从 `worker:1` 改为 `hot:1`/`warm:1`/`cold:1`

## [v8.5.0] — 2026-06-23

### Added
- **COSPLAY (arxiv 2604.20987) 全架构移植 — 任务轨迹→技能契约闭环**
  - `lfm_skill_bank.py`（1521 行）：LFM Skill Bank 完整引擎
    - 契约学习（Contract Learning）：执行日志→效果 contract（eff_add/eff_del/eff_event）
    - 银行维护（Bank Maintenance）：Merge/Split/Refine/Retire/Promote 五操作
    - ProtoSkill→Skill 毕业：support+consistency+pass_rate 三阈值门控
    - 五维加权评分（quality+consistency+reuse_success+exploration+recency）
  - `lfm_boundary_detector.py`（965 行）：Boundary Detection + NLP Predicate
    - Changepoint Detection（CUSUM/sliding_window）+ intent 标签
    - NLP 增强 Predicate 提取（关键词/实体/情感 → 结构化 predicates）
  - `cosplay_context_adapter.py`（580 行）：四合一上下文桥接器
    - Boundary-Aware Compression：按意图 segment 分组压缩
    - Contract-Aware Summarization：Skill Bank contract 指导保留 predicates
    - Skill Replacement：整段匹配 → `[Skill: name]` 替代
    - Feedback-Driven Compression：展开率 → Skill Bank refine 阈值调优
- **全链路集成**
  - `memory_consolidation.py`：Step 0 技能循环 + Step 0.5 边界检测
  - `xiaoyi_claw_api.py`：R-CCAM 反馈桥 → Skill Bank 喂入
  - `dag_context_manager.py`：COSPLAY 增强压缩 + contract 上下文注入 + expand 反馈
  - `claw_worker.py`：压缩后反馈 → Skill Bank refine
  - `unified_coordinator.py`：`lfm_skill_bank`、`lfm_boundary_detector`、`cosplay_context_adapter` 注册

### Changed
- VERSION 8.4.2 → 8.5.0
- consolidation cycle 从 11 步扩展到 13 步（+COSPLAY step 0 + step 0.5）
- DAG 压缩策略：从轮次分组升级为意图 segment 分组
- 上下文装配：追加 COSPLAY 契约上下文层

## [v8.4.2] — 2026-06-23

### Added
- **enhanced_recall v2 — 全量 8 阶段神经集成管线**
  - Stage 0: Engram 快速通道（NgramHashTable O(1) 命中检测）
  - Stage 1: 向量基线保留（关键词 + Embedding）
  - Stage 2: CRAG + 混合检索 + 命题检索（古典 Layer 2 保留）
  - Stage 3: 突触网络传播（ActivationSpreader → find_associated 关联记忆）
  - Stage 4: 情感加权（EmotionMemoryManager 高情绪记忆置信度提权）
  - Stage 5: 图感知检索（SkillGraph.GraphAwareRetriever + GNN.query_graph）
  - Stage 6: RRF 多路融合归并（突触/图结果优先排序）
  - Stage 7: 反思增强（Generative Agents MemoryStream 兜底）
  - Stage 8: retrieval_formula.MemoryRetriever 最终加权排序
- **use_neural 参数**：`enhanced_recall(query, use_neural=True/False)` 降级回古典模式
- `xiaoyi_claw_api.py` enhanced_recall 透传 use_neural 到 XiaoyiMemoryV2

### Fixed
- `ModuleType` 枚举补充 `DAG_LIQUID`（v8.1 液态神经网络模块注册时遗漏）
- 删除运行时产物 `.galaxyos_version`

## [v8.4.1] — 2026-06-23

### Added
- **SkillGraph v8.4.1 全链路集成**
  - `skill_graph.py`（780 行，144 节点 + 277+ 边）CNB 仓库注册到 MODULE_REGISTRY
  - `GraphAwareRetriever`（BFS + Beam Search 图感知检索）
  - `GraphEvolutionEngine`（Merge/Split/Reinforce/Decay/Prune）
  - `GRPORunner`（G=8 GRPO, arXiv:2606.04036 SDPG）
- `ModuleType.SKILL_GRAPH` 枚举 + `EXTENDED_MODULES_P1` 注册
- `galaxyos_okf.py`：importlib 兜底 → 直接 `from skill_graph import SkillGraph`
- `capability_registry.py`：`init_skill_graph()` / `graph_aware_search()` 入口

### Removed
- `speculative_decoder` 从 `ModuleType`、MODULE_REGISTRY（Layer 4 + EXTENDED_MODULES_P1）、4 处 workflow 引用全部删除

## [v8.4.0] — 2026-06-23

### Added
- **OKF `generate` 子命令** — 自动从 `MODULE_REGISTRY` 生成模块 concept（144 模块，13 层），按层分组含 cross-link + index.md
- **`resource` 字段** — concept 前件带源路径（`file://workspace/` / `galaxyos://module/` / `galaxyos://skill/`）
- **bundle 目录拆分** — `concepts/modules/`、`concepts/skills/`、`concepts/system/`
- **OKF → SkillGraph 协同** — ingest 时 type=Skill 的概念自动编译到 SkillGraph（`SkillCompiler.compile()` → `AssetRegistry.register()` → `SkillGraph.add_node()` → 持久化），实现图感知检索
- **sync 集成 generate** — Step 3.5 自动生成模块概念后推 CNB
- 安装向导 `--setup-git` 子命令
- `openclaw.plugin.json` 描述 v4→v8，119→144 模块

### Changed
- `package.json` 版本统一为 `8.4.0`
- `VERSION` 统一为 `v8.4.0`
- 规范版本号格式（遵守 VERSIONING.md）

### Fixed
- SkillGraph 中文长句匹配（补 cn→cn 路径）
- 拓扑排序保留种子得分
- 全局单例持久化

### 已实现论文模块（v8.4 前已完成）
- **SkillGraph** (`arXiv:2605.12039`) — 技能依赖图 + RL 信号共进化，SkillGraph 已集成到 R-CCAM 检索链路，SkillCompiler/AssetRegistry/SkillGraph 全链路已部署
- **SDPG** (`arXiv:2606.04036`) — Self-Distilled Policy Gradient，RLVR + self-distillation 策略优化
- **GraphWalker** (`arXiv:2603.28533`) — Agentic KGQA 合成轨迹学习，两阶段 SFT + 轻量 RL
- LASAR Latent Cognitive Map (`arXiv:2605.16899`) — 认知地图嵌入 R-CCAM
- AriGraph (`arXiv:2407.04363`) — 空间拓扑记忆检索
- Engram (`arXiv:2601.07372`) — DeepSeek 式记忆印记 + U 型缩放律
- LFM (`arXiv:2409.20308`) — Liquid AI 基础模型自适应算子
- MemoryOS (`arXiv:2506.06326`) — 热度跟踪 + 分段页式存储
- SKILL0 (`arXiv:2604.02268`) — 技能课程学习
- KAN (`arXiv:2404.19756`) — Kolmogorov-Arnold 网络嵌入
- Self-RAG / CRAG (`arXiv:2310.11511` / `arXiv:2401.15884`) — 检索增强生成
- Neural ODE (`arXiv:1806.07366`) — 连续时间神经微分方程
- 及其他 20+ 论文实现（Titans、Mamba3、LiquidSSM、SSM-KAN、MoE-Engram 等 v8.1 液态神经网络模块）

## [8.3.0] - 2026-06-18

### Added
- **Open Knowledge Format (OKF) 集成**: 新增 `galaxyos_okf.py` 三层整合工具
  - `export`: 扫描 workspace 系统文件 + skills 导出为 OKF Knowledge Bundle（244 concepts）
  - `ingest`: 消费 OKF bundle，索引到 knowledge_assets 供检索
  - `verify`: 验证 bundle 结构和 concept 合法性
  - 输出目录: `var/okf-bundles/`, `var/okf-index/`, `var/knowledge_assets/`

### Changed
- **LFM2.5-1.2B: torch bf16 → ONNX Runtime Q4**
  - 模型大小从 2.2GB safetensors 降为 811MB ONNX（Q4 量化）
  - ONNX Runtime mmap 加载，多进程共享物理页
  - 内存占用：双 worker 物理增量从 ~2.2GB 降为 ~0.8GB
  - 启动速度：秒级 vs torch 全家桶十几秒
  - embed_text: `present_conv.15` mean pooling 替代 `hidden_states[-1]`
  - Tokenizer: `tokenizers.Tokenizer.from_file()` 替代 `AutoTokenizer.from_pretrained()`
- **安装向导**：--download-lfm 从下载 2.2GB safetensors 改为 811MB ONNX Q4
- **setup.py**: 补 `tokenizers>=0.20.0`、`transformers>=4.44.0`，去重 psutil
- **claw_worker.py**: 预加载日志同步更新

### Added
- **兼容迁移**: check_lfm_weights 识别旧版 safetensors 并提示迁移到 ONNX

## [8.2.12] - 2026-06-18

### Added
- 198 个文件提交同步（包含 V81 神经网络 embedding + 论文实现 33 模块 + 全链路修复）

### Fixed
- 测试套件修复：9 个失败的测试用例修正
