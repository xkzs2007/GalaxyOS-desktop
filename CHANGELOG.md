# Changelog

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

## [v8.5.3] — 2026-06-25

### Added
- **P1: 公告板集成（DAGMessageBus）** — `_ensure_bus()` / `_register_agents()` / `_publish_result()` / `_poll_peer_results()` / `_build_dag_context()`
  - 子 Agent 之间通过公告板广播/拉取同伴结果，Critique 阶段注入同伴上下文
  - 纯内存模式启动，无需 Redis／DAG 持久化，可降级
- **P1: Judge 知识蒸馏（DebateEngine）** — `_judge_distill()` 替代朴素取最高分
  - 3-Agent 并行辩论（正面/反面/中立）→ Judge 裁决 → `refined_answer` 覆盖蒸馏输出
  - `confidence_delta` 和 `verdict` 写入 `merge_stats`
- **P1: 选角优化（收敛缓存 + HyperRouter）** — `_role_cache` 缓存同一 `input_class` 的选角结果
  - HyperRouter 辅助路由接口（通过 `use_hyper_router=True` 启用）
  - `_invalidate_role_cache()` 支持热更新
- **P1: 浏览器工具注入（所有角色可搜）** — `tool_bag['allow_all_roles']` 让全部角色（含 critic/summarizer）都能调 `web_search`/`web_fetch`
- **P1: 交叉验证串联（MultiAgentVerifier）** — `_cross_verify()` 对合并输出逐句验证
  - 结果写入 `merge_stats['verified']` / `verification_confidence` / `verification_issues`
- **P2: 子 Agent 进度推送（AgentProgress）** — QUEUED→STARTED→COGNITION→SEARCHING→CRITIQUE→REFINING→COMPLETED/FAILED 全状态机
  - `set_progress_callback()` 外部注入回调
  - `MergeResult.progress_events` 携带完整进度事件回传 R-CCAM

### Changed
- VERSION 8.5.1 → 8.5.3
- `xiaoyi_claw_api.py._run_swarm_cycle()` 从 SwarmManager 切到 MultiAgentOrchestrator P1，默认启用公告板+蒸馏+交叉验证
- `multi_agent_orchestrator.py` 新增 `use_dag_bus` / `use_debate` / `use_hyper_router` / `use_verifier` 构造参数
- 所有依赖模块降级兼容（`_HAS_DAG_BUS` / `_HAS_DEBATE` / `_HAS_HYPER_ROUTER` / `_HAS_VERIFIER` 标志位）
- 同步 4 副本：`galaxyos/engine/` + `extensions/galaxyos/dist/scripts/` + `extensions/galaxyos/scripts/` + `services/`
