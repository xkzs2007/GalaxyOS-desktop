# Changelog

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
