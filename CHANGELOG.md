# Changelog

## [8.0.0] — 2026-06-12

### Added
- **MemGAS-SkVM 融合架构 (Layer 13)** — KnowledgeAsset + AssetRegistry + CapabilityRegistry
  + SkillCompiler + MultiGranularity + EntropyRouter 全套资产编排模块
- **AutoPromptOptimizer (APO)** — ProTeGi 算法实现，基于 Automatic Prompt
  Optimization with "Gradient Descent" and Beam Search (arXiv 2305.03495)
- **PipelineEngine + PipelineRegistry** — 自动化依赖推导/拓扑排序/并行调度
- **ImpactTracker** — 模块效果追踪器，命中率/有效性统计
- **MetaOptimizer** — 从 ImpactTracker 读取指标自动调参
- **ValueGate** — 注入价值评估器，检测 LLM 回复中 injection 利用率
- **七情六欲 skill 集成** — qiqing-liuyu 表达风格技能纳入 GalaxyOS 生态
- **安全加固** — `llm_config.json` 脱敏 + `.gitignore` 强化

### Changed
- **claw_worker.py context_assemble** — 全论文模块编排 → PipelineEngine 统一调度
- **xiaoyi_claw_api.py** — 启动时初始化 MemGAS-SkVM + APO 注入 + 后台巩固线程
- **self_evolution_engine.py** — 注入 APO PromptOptimizer 懒加载
- **unified_coordinator.py** — 注册 Layer 13 全部 6 个新模块
- **install_wizard.py** — 依赖扫描从手写精简表改为 requirements.txt 全量扫描

### Fixed
- **install_wizard PyG index URL** — torch>2.11 回落到 torch-2.10.0+cpu.html

## [7.3.2] — 2026-06-11

### Added
- **GAT 双路径 + 稀疏 PyG 后端** — 5.8GB 容器下 N=2000 节点 0.4GB RSS + 7ms 延迟（vs 原稠密矩阵 20GB OOM），`mode=auto/dense/sparse` + `backend=auto/pyg/native` 开关
- **HNSW 隐式边构造** — `gnn_graph_builder._build_implicit_edges` jieba 全 N² → HNSW 索引 O(N·log N)，5.8GB 容器下 N=3078 节点 135ms（vs 原 ~2min）
- **HNSW 端到端集成** — `load_from_database` ≥ 200 节点自动挂载 HNSW 索引 + `query_neighbors(query_vec, k)` 语义召回接口；`_load_from_dag_db` 大表走 HNSW 召回取代纯时间序
- **install_wizard 阶段 0.5** — torch/torch_geometric/torch_scatter/torch_sparse/hnswlib/faiss/ncps 检测 + 清华源 + PyG wheel + PyTorch CPU 索引
- **install_wizard `--fix-torch`** — 一键补齐 ML 栈（自动选解释器 + 清华源 + 预编译 hnswlib wheel fallback）
- **install_wizard `--python` / `--openclaw-home`** — 显式覆盖 Python 解释器和 OpenClaw 用户配置目录（生产容器固定运行时）
- **Python 3.12.13 运行时** — `/opt/python/bin/python3.12`（python-build-standalone 预编译），torch 2.12.0+cpu + torch_geometric 2.8.0 + hnswlib 0.8.0 + faiss 1.14.2 + ncps 0.0.2 全家桶就绪
- **PyG GATConv 加速** — 2.8x speedup vs 手写稠密，CPU 上 N=2000 节点 7ms
- **KG 集成 5.8GB 适配** — N=3000 节点 forward RSS=373MB（之前稠密 OOM）
- **GAT 预算估算 + 硬上限** — `_estimate_dense_bytes` + `_dense_n_hard_limit`，单层 attention ≤ 50MB（num_heads=4 → N≤1768）
- **OpenClaw 路径解析** — `_openclawHome()` / `_resolve_openclaw_home()` 覆盖 dev/prod/容器三模式，`OPENCLAW_HOME` / `GALAXYOS_OPENCLAW_HOME` 显式 env 覆盖；9 处 `process.env.HOME` 替换为 `OPENCLAW_HOME`
- **互动模式自动询问 --fix-torch** — tty 缺包时问 `[Y/n]`，非交互/CI 不阻塞

### Fixed
- **dtype mismatch (c10::Half != float)** — chunked 路径 cast W/a 到 h.dtype
- **PyG 2.8 `add_self_loops` bug** — 改 `add_self_loops=False` 手工加自环
- **PyG 收到稠密 (N, N) 当 edge_index** — GAT `_resolve_graph` 区分 `is_edge_index` + `pyg_mode` 透传
- **KG 优先用 `_adj_matrix` 不用 edge_index** — `KG.forward` 优先 `_edge_index`
- **PyG index URL 算法** — torch>2.11 回落到 torch-2.10.0+cpu.html
- **hnswlib cp312 wheel 装不上** — `tar xzf libs/hnswlib-0.8.0-x86_64.tar.gz` 直接展开到 site-packages
- **py3.12 装不上** — `python-build-standalone` 20260602 release tag

## [7.3.0] — 2026-06-10

### Added
- **ContextEngine 全论文决策链** — 21 个论文模块全接入 `context_assemble()` 两阶段编排 (Self-RAG IsREL+CRAG Evaluate → CoVe 验证 → Adaptive Hallucination → Cognitive Load → Dynamic CRAG Threshold → SKILL0 → CoEvolve → Turn Recovery(2505.06120) → MemCoE(2605.00702) → MemGPT ContextLayer → MemoryOS HeatTracker/SegmentedPage → SSM Predicter → AriGraph → RAPTOR → HyperRouting → KoRa Behavior → Code-Aware → Thinking Enhanced → Memory Consolidation → Sleep Consolidation)
- **session_id 全链路隔离** — 15 处检索入口 → MemGPT/MemoryOS/HierMemory/ClawWorker 全部按 session 分区，多会话数据零串扰
- **Gateway 防塞爆** — ZMQ 500ms 去重 + 系统消息上限 5 条，防止消息风暴撑爆上下文
- **IPC 路径统一** — claw-core/var → galaxyos/var (6 个 Python + 1 个 JS 模块)
- **RLM 递归压缩** — 新增 `rlm_compress()` 替代紧急截断，递归分解超长消息为摘要
- **BlobArena 无损还原注入** — 记忆召回时自动还原完整上下文
- **galaxyos_native 纯 Python shim** — 无 Rust 环境自动降级纯 Python；libs/ 预编译包备用
- **native 国内镜像** — `.cargo/config.toml` rsproxy 镜像；Makefile rustup-cn/native-libs target
- **openclaw.plugin.json memorySlots** — ContextEngine 多槽位分离记忆注入配置
- **压测验证** — 451 pass (19 自定义 + 412 原有)，JS 语法全过

### Fixed
- 上下文超长时不截断，改用 RLM 递归压缩

## [7.2.0] — 2026-06-10

### Added
- **GalaxyPool 统一管理** — 6 类组件 (mmap/gateway/zmq/native/heartbeat/workers) 单入口 start/stop + 拓扑排序 + 统一健康检查 + 电路断路器
- **负载感知调度** — WorkerPool 按 fail count + latency + recency 三维评分选择最优 Worker
- **批量 RPC** — 一次 HTTP 请求执行多个方法调用，减少 round-trip
- **R-CCAM 会话互斥** — 同一 sessionKey 5 分钟内不重复提交，防止 Worker 抢占
- **R-CCAM 流式进度** — ZMQ 实时推送 phase 变化
- **mmap 大 payload 路由** — result >50KB 自动走 mmap + ZMQ 通知，UDS 只回引用
- **Rust PyO3 桥梁** — VectorAPI + VectorStore 优先走 `galaxyos_native` (GIL-free SIMD)
- **Rust 自动编译** — `make all` 一键编译 + JS 启动时 auto cargo build
- **CLI-Anything 插件** — 7 工具 (shell_run/git/make/test/file) Agent 自运维

### Fixed
- **神经网络全量修复** — ONNX 路径自发现 + 5 个 services shim + 6 类模型验证通过 (31 神经元 + 25 突触)
- **硬编码路径清零** — 10 处 `/home/sandbox` → `OPENCLAW_WORKSPACE` / `os.path.expanduser`
- **安装向导修复** — 补 `sqlite3` import + KG 检查恢复正常

## [7.1.0] — 2026-06-10

### Added
- **RLM REPL 环境 (arXiv:2512.24601)** — `rlm_env.py`，安全沙箱，模型写 Python 递归处理超长 prompt
- **SKILL0 技能课程 (arXiv:2604.02268)** — `skill_curriculum.py`，47 技能 5 阶段逐步内化
- **MemoryOS 记忆操作系统 (arXiv:2506.06326)** — `memory_os.py`，热度跟踪 + 分段管理
- **10+1 论文集成层** — `paper_integration.py`，12 模块预加载到 R-CCAM 各阶段
- **四论文管线** — `four_advancements.py`，RAPTOR + GraphRAG + Generative Agents + Toolformer
- **GalaxyOS OpenClaw 插件** — 11 个 UDS 工具 + ContextEngine 接管 ingest/compact

### Fixed
- **RLM 递归参数**: `_rlm_func` 签名修正，支持 `rlm(name, sub_prompt)` 调用
- **路由决策默认值**: `decide_routing` 的 `is_followup` 添加默认值 `False`

## [6.6.0] — 2026-06-09

### Added
- **知识编译引擎** — `services/knowledge_compiler.py`
  - `KnowledgeCompiler`: 多源碎片→聚类→合成→结构化 .md 写出
  - `TopicClusterer': 10 个预定义主题的关键词聚类（CPU only）
  - `GalaxyOSKnowledgeAdapter`: 从惊讶门控/SSM/DAG 自动收集碎片
  - Obsidian 兼容: frontmatter + backlink + `_index.md` 索引
- **心跳集成**: `heartbeat_task_executor.py` 第 6 步 `_compile_knowledge()`



GalaxyOS 版本变更记录。

## [6.5.1] — 2026-06-09

### Added
- **Titans 惊讶度门控** — `services/neural_memory_gate.py`
  - `RecallPatternPredictor`: 共现矩阵预测检索模式
  - `RetrievalSurpriseCalculator`: Jaccard 惊讶度 + 自适应阈值
  - `NeuralMemoryGate`: 输出 consolidate/decay 信号
- **SSM 状态预测器** — `services/ssm_state_predictor.py`
  - `SSMStatePredictor`: 指数衰减 + Hawkes 自激励时序预测
  - `CompositePredictor`: 融合 SSM(0.4) + 共现(0.6) 的综合惊讶度
- **A2A DAG 消息总线** — `services/dag_message_bus.py`
  - `DAGMessageBus`: send/poll/ack/broadcast/reply
  - `SubscriptionManager`: 消息类型过滤 + 路由匹配
  - 复用 DAGContextManager 存储，无独立消息队列
- **版本管理规范** — `VERSIONING.md`

### Changed
- `neural_pipeline.py`: `_get_memory_gate()` 切换为 `CompositePredictor`
- `adaptive_ltp_ltd.py`: `calculate_ltp_strength()` 新增 `modulator` 参数

## [6.5.0] — 2026-06-09

### Added
- **CfC + GAT 全链路激活** — 阈值从 200 调至 5000，3093 神经元走 ONNX→GAT→CfC 完整推理
  - `cfc_inference.py`、`cfc_sequence_predictor.py`、`neural_pipeline.py`、`onnx_embedding.py`、`gnn_graph_builder.py` 迁入 `services/` 包
  - 导入路径全部改为 `from services.xxx import`，消除 `sys.path` 依赖
- **BlobArena per-session 隔离** — 全局单例改为 `{session_id}/arena_X.blob` 独立目录
  - `delete_session_arena()` 精准回收结束 session 的 mmap 磁盘文件
  - `read_blob_compat()` 向后兼容旧全局 arena 数据
  - `dag_clear_session` 新增 `deleteArena` 参数

### Changed
- **SYNAPSE_FULL_THRESHOLD** 200 → 5000，激活 GAT 全链路
- **GAT 全局稀疏化** — `GraphAttentionLayer.forward()` 从稠密 `adj` 改为 `edge_index` COO 格式，segment softmax 替代全展开，300× 内存缩减
- **SynapseGATEncoder** — `gnn_graph_builder.py` 不再构建 N×N 稠密矩阵，直接透传 `edge_index`

## [6.4.0] — 2026-06-09

### Added
- **MemGAS 融合升级** — DAG ingest 时自动为长内容创建 KnowledgeAsset（多粒度表示 + GMM 关联）
- **跨会话 DAG 搜索** — 新增 `cross_session_search()`（FTS5 优先，LIKE 降级），通过 UDS `dag_search` 暴露给 Plugin
- **Plugin 端 3 个新 Tool**：`claw_compile_skill`（SkVM 编译）、`claw_asset_search`（资产查询）、`claw_dag_search`（跨会话 DAG 搜索）

### Changed
- **session_key 全线穿透** — `unified_entry.recall()` / `UnifiedVectorStore.search()` / `XiaoYiClawLLM.remember()` + `recall()` 新增 `session_key` 参数，支持跨会话隔离检索
- **Plugin → Worker 连接池化** — `http.Agent` keepAlive + maxSockets=8，消除单连接串行瓶颈
- **DAG 上下文引擎增强** — MemGAS 自动创建 KnowledgeAsset 和关联边

### Performance
- UDS 并发：去锁化 + 连接池后，Gateway 调用吞吐提升显著

## [6.3.2] — 2026-06-08

### Fixed
- **UDS 并发瓶颈全面修复** — Worker → Gateway / Plugin → Worker 两大通道去锁化
  - `_GatewayProxy` (claw_worker.py): 单连接 `self._conn` → `threading.local()` 每线程独立连接，消除 Gateway 调用串行瓶颈
  - `GatewayClient` (gateway_client.py): 类级 `_lock` + `_http_conn` → `threading.local()` 每线程连接，消除全局锁
  - Plugin → Worker (index.js): 每次 `http.request` 新建连接 → `http.Agent` 连接池 (keepAlive + maxSockets=8)，复用热连接减少握手开销
  - UDS 服务端 `handle_request()` 循环: 增加 `server.timeout = 1.0s`，Shutdown 不再无限阻塞
  - Agent 生命周期绑定 Worker: `_cleanup()` 中 `agent.destroy()` 释放连接池

## [6.3.1] — 2026-06-08

### Fixed
- **HTTP/JSON over UDS 全通道统一** — 原始 socket 二进制协议全部替换为 HTTP over UDS
  - Plugin → Worker: `http.request({ socketPath })` 替代 4 字节二进制帧
  - Gateway UDS 服务端: `http.createServer().listen(udsPath)` 替代 `net.createServer`
  - Worker Python → Gateway: `HTTPConnection` over UDS (`_UnixHTTPConn`) 替代 `struct.pack/unpack`
  - `gateway_client.py` 重构: 移除残留的 `struct.pack`/`unpack`，统一走 HTTP
- **健康检查 `无记忆数据` 误报修复** — 旧数据 `source` 字段（`ai_judge`/`dc_judge`）不在当前 `SourceType` 枚举中
  - 枚举新增 `AI_JUDGE` 和 `DC_JUDGE`
  - `_load_memories` 添加 try/except 跳过格式不兼容的旧数据行

## [6.3.0] — 2026-06-08

### Added
- **MemGAS-SkVM 融合系统全链路实现** — 6 个新建模块 + 5 个现有文件改造
  - KnowledgeAsset 统一模型 (knowledge_asset.py): Skill+Memory 统一 Asset 模型, BlobArena 持久化, 按 capability/tag/category 查询
  - MultiGranularity 四粒度提取 (multi_granularity.py): session/turn/summary/keyword 四级表示 + GMM 聚类关联 (sklearn → KMeans → jieba 三级降级)
  - Capability Registry (capability_registry.py): 26 维 primitive capability Profile + HarnessProfile 自动检测 + ProfileMatcher 适配评分 + SkillClassifier 自动分类
  - Skill Compiler (skill_compiler.py): 编译流水线 profile_check → env_bind → skill_prune → optimize_text
  - Code Solidification (cde_solidifier.py): 参数化模板检测 (curl/requests/shell/fetch) → 直接生成可执行脚本, bypass LLM
  - Concurrency DAG Extractor (concurrency_extractor.py): 步骤拆分 → 依赖检测 → 关键路径 → 并行分组 → speedup 估算

### Changed
- **retrieval_hub.py**: RRF 融合改为 MemGAS 熵权重 (低熵通道×高权重) + 新增第 6 通道 `graph_walk` (PPR 图漫游 + BFS 关联遍历)
- **intelligent_thinking_trigger.py**: `SKILL_MAPPING` 硬编码字典替换为 ProfileMatcher profile-based 路由, 保留三论文引擎
- **xiaoyi_claw_api.py**: `_cognition_phase` 走 SkillCompiler 编译 skill + AssetRegistry 注册, 注入 compiled artifact 而非 raw Markdown
- **claw_worker.py**: 新增 `compile_skill` / `asset_search` / `asset_register` 三个 UDS 方法
- **dag_context_manager.py**: `add_node` 自动触发四粒度提取 + GMM 关联建边 + `get_assets_for_session` 方法

### Tests
- **test_memgas_skvm.py**: 35 个测试用例 (KnowledgeAsset 7 + MultiGranularity 7 + CapabilityRegistry 9 + SkillCompiler 4 + CodeSolidifier 5 + ConcurrencyExtractor 5)
