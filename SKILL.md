---
name: xiaoyi-claw-omega-final
description: GalaxyOS v5.5 — IntelligentThinkingTrigger v2.0 三论文集成 (RCR-Router + Springdrift + A-ToM) + Cognition Forest 子树修正
author: xkzs2007
license: MIT-0
tags: [architecture, memory, llm, rccam, dag, kora, knowledge-graph]
---

# GalaxyOS v5.5

> **定位**: OpenClaw 的核心底层能力引擎
> **更新时间**: 2026-06-05 09:06
> **架构层数**: 16 层（含 Layer 0 安装向导）
> **总能力项**: 440+ 项
> **IPC 三通道**: UDS RPC（双向互通，动态注册表）+ ZMQ 事件推送（双向回复）+ mmap 结构化状态（4KB JSON段）
> **默认通信**: UDS RPC，HTTP :8765 二级降级，无 stdin/stdout，无 spawnSync
> **IPC 演进**: stdin/stdout → UDS RPC（单 Worker 单例） → **Gateway-Worker 全透明互通（UDS注册表 + mmap 结构化 + ZMQ 双向）**
> **独立服务线程池**: 6 个（Memory/Retrieval/Session/Thinking/Hardware/HTTP-RPC）
> **Galaxy 增强** ✅: DAG三维绑定 / Cognition Forest 子树复用 / KoRa 主动能力 / Kernel 持续元认知
> **透明互通** ✅: Gateway `_gatewayMethods` 注册表（14 预注册 + 自动 tool.* 暴露）+ Worker `_GatewayProxy`（__getattr__ 透明 RPC）+ mmap 结构化状态（双向零拷贝 + heartbeat 5s）

---

## 🎯 核心定位

GalaxyOS是 OpenClaw 的**核心底层能力引擎**，提供：

1. **记忆能力** — 二层记忆体系（本地记忆系统 + DAG上下文管理）+ 记忆巩固引擎（CLS固化 + 仿生睡眠5阶段周期 + 艾宾浩斯遗忘曲线 + 干扰合并 + 预测编码冲突检测）+ **仿生睡眠巩固引擎（NREM-SWR/NREM-CASCADE/REM-GENERATIVE/REM-EMOTION/DEEP-SLEEP + KG睡眠图推理 + Dreaming Bridge双向同步）** + 隐式偏好学习
2. **检索能力** — 向量检索 + 知识图谱 + Self-RAG + CRAG 混合检索 + CRAG 动态纠错 + 场景锚定注入（Drawing on Memory 双迹编码 + GRAVITY 结构锚定）+ bge-reranker-v2-m3 重排序 + 预测编码冲突检测 + GraphRAG社区检测 [MS 2024] + RAPTOR分层摘要树 [Sarthi 2024]
3. **智能处理能力** — 查询改写（Pro）+ 结果总结（Flash）+ 语义过滤 + 图像理解（SmartProcessor 三模型通道：Flash/Pro/VLM）+ 自进化上下文注入 + Flash 开推理场景编码 + KV 缓存优化 + Flash NLP 路由 + **用户画像驱动内在元认知分析（Flash以用户视角分析体验数据→惰性激活下游模块）**
4. **思考能力** — IntelligentThinkingTrigger v2.0 (RCR-Router动态评分 + Springdrift CBR记忆 + A-ToM认知推断) + 20方法论 + 10工程技能 + 决策引擎 + Reflexion 反思 [Shinn 2023] + Self-Refine 迭代精炼 [Madaan 2023] + Multi-Path 多路径并行探索 [Yao 2023] + Toolformer 工具路由 [Meta 2023] + GA 反思 [Park 2023]
5. **执行能力** — 44 个工作流全 IPC 并行调度 + R-CCAM 结构化认知循环（统一深度管线）+ DAG 上下文中继 + Worker UDS 主通道（Plugin直连，无stdin/stdout）+ ZMQ 事件推送 + mmap 共享内存 + Worker 自动重启 + **Merge Gate** + 后台4论文引擎并行 + **Galaxy DAG 三维绑定（semantic_map/function_map/design_ref 全链路传递）**
6. **多模态能力** — 图像理解（三引擎: xiaoyi + DeepSeek-OCR-2 + GLM-4.6V-Flash VLM）+ 图像生成（seedream）+ OCR2 深度整合 + VLM 第三通道 + Visual RAG（Cognition 阶段自动 OCR2/VLM 提取→上下文注入）
7. **可靠性能力** — 防幻觉 10 重检测 + 自我修复 + 故障转移 + ACP 持久化通道 + 全局上下文窗口比例压缩 + 自进化决策执行层 + 系统消息噪声过滤 + **Rails 护栏增强版** + 隐式偏好学习 + Worker 自动重启 + Merge Gate 合入门禁 + **人格视觉** + **用户画像驱动内在元认知进化** + **Galaxy KoRa 主动能力（行为建模+模式识别）** + **Galaxy Kernel 持续元认知后台**

---

| **文档版本**: v5.5 | 2026-06-05 09:06 | IntelligentThinkingTrigger v2.0 三论文集成 + Cognition Forest 子树修正 |

---

*GalaxyOS — OpenClaw 的核心底层能力引擎*
*文档版本: v5.5 | 最后更新: 2026-06-05 09:06 | IntelligentThinkingTrigger v2.0 三论文集成 + Cognition Forest 子树修正*

---

## 🏗️ 架构全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           OpenClaw 应用层                                    │
│                    (用户对话、技能调用、任务执行)                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      GalaxyOS v5.0 — Galaxy Kernel 重构 + 异步注入三层兜底        │
│                         (核心底层能力引擎 · 15层)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 0: 记忆架构                                 │   │
│  │  ┌─────────────────────────────────────┐  ┌─────────────────────────┐           │   │
│  │  │  GalaxyOS 原生记忆 (UnifiedVectorStore) │  │  Session State (快照层) │           │   │
│  │  │  UnifiedVectorStore                 │  │  capture（只录不recall）│           │   │
│  │  │  SQLite-vec + FAISS + 语义检索       │  │  跨会话恢复由DAG接管   │           │   │
│  │  │                                      │  │  长上下文保活           │           │   │
│  │  └─────────────────────────────────────┘  └─────────────────────────┘           │   │
│  │                                                                      │   │
│  │  启动序列: recall → 记忆文件 → 场景索引                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 0.5: 云端备份层                            │   │
│  │  ┌─────────────────────────┐  ┌─────────────────────────┐           │   │
│  │  │  华为云盘 (全量备份)     │  │  IMA 知识库 (增量同步)   │           │   │
│  │  │  剩余: 18.3 GB          │  │  知识库: 1 个            │           │   │
│  │  └─────────────────────────┘  └─────────────────────────┘           │   │
│  │                                                                      │   │
│  │ 备份内容: MEMORY.md | USER.md | SOUL.md | memory/*.md | brain/      │   │
│  │ 备份策略: 每 7 天全量 → 云盘 / 每 24h 增量 → IMA                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 1: 记忆核心层                               │   │
│  │  ┌──────────────────────────────────────────────────────────────┐   │   │
│  │  │  防幻觉系统 (enhanced_hallucination_guard)                    │   │   │
│  │  │  ┌─ 多重检测: SELF-FAMILIARITY → SOURCE_TRACING →           │   │   │
│  │  │  │  CONFLICT_DETECTION → TEMPORAL_VALIDITY → OUTPUT_VALIDATION │   │   │
│  │  │  └─ 自适应阈值 + 动态降级                                    │   │   │
│  │  │                                                              │   │   │
│  │  │  突触网络 (Synapse Network + LTP/LTD)                        │   │   │
│  │  │  ├─ adaptive_ltp_ltd: 艾宾浩斯遗忘曲线 R=e^(-t/S) 指数衰减 │   │   │
│  │  │  │  参考: Ebbinghaus (1885) + Hebbian Learning               │   │   │
│  │  │  │  记忆强度S由强化次数×重要性决定, 遗忘先快后慢           │   │   │
│  │  │  └─ retrieval_formula: recency/relevance/importance 三维加权 │   │   │
│  │  │                                                              │   │   │
│  │  │  记忆巩固引擎 (memory_consolidation.py + biorhythm_sleep_consolidation.py)   │   │   │
│  │  │  ├─ CLS 互补学习系统: DAG高重要性节点→突触网络长期固化       │   │   │
│  │  │  │  参考: McClelland et al. (1995) "CLS"                     │   │   │
│  │  │  ├─ 仿生睡眠巩固引擎 (5阶段周期, 替代旧离线重放)            │   │   │
│  │  │  │  参考: Rasch & Born (2013) "System Consolidation"          │   │   │
│  │  │  │  ├─ NREM-SWR: ~200Hz 尖波涟漪压缩重放, 3次涟漪/batch=8   │   │   │
│  │  │  │  ├─ NREM-CASCADE: SO(0.8Hz)→Spindle(14Hz)→Ripple 级联    │   │   │
│  │  │  │  │   + 长尾记忆拯救 + 突触修剪                           │   │   │
│  │  │  │  ├─ REM-GENERATIVE: 记忆碎片随机组合 + 关键词新颖评估    │   │   │
│  │  │  │  ├─ REM-EMOTION: 情感强度衰减 25%/轮 + 情感记忆链增强   │   │   │
│  │  │  │  └─ DEEP-SLEEP: 记忆迁移 + KG 图推理(实体消歧/社区发现)  │   │   │
│  │  │  │  └─ 运行: 空闲>2min自动触发, Dreaming Bridge←→双向同步   │   │   │
│  │  │  ├─ 干扰合并: 相似记忆自动合并/替换,降低冗余               │   │   │
│  │  │  │  参考: Retrieval-Induced Forgetting 干扰理论              │   │   │
│  │  │  └─ 预测编码: 检索结果冲突检测,标记矛盾项需验证            │   │   │
│  │  │    参考: Friston (2010) "Free Energy Principle"             │   │   │
│  │  │                                                              │   │   │
│  │  │                                                              │   │   │
│  │  │  自适应遗忘 (adaptive_forgetter) + 情感记忆 (emotion_memory)   │   │   │
│  │  └──────────────────────────────────────────────────────────────┘   │   │
│  │                                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 2: 检索增强层                               │   │
│  │  Self-RAG (isrel/issup/isuse 预测器)                                │   │
│  │  ├─ isrel_predictor: 相关性判断                                     │   │
│  │  ├─ issup_predictor: 支持度判断                                     │   │
│  │  └─ isuse_predictor: 有用性判断                                     │   │
│  │                                                                      │   │
│  │  CRAG (dynamic_crag_threshold)                                      │   │
│  │  └─ 动态阈值: 检索评估 → 纠正 → 重排                                 │   │
│  │                                                                      │   │
│  │  混合检索 (Hybrid Search)                                            │   │
│  │  ├─ Dense: FAISS/Qdrant 向量检索 (bge-m3, 1024维)                  │   │
│  │  ├─ Sparse: TF-IDF / BM25 关键词检索                                 │   │
│  │  ├─ RRF 融合: 自适应权重 + Top-K 截断                                │   │
│  │  └─ 重排序: bge-reranker-v2-m3 (无问芯穹, 对初筛结果二次精排)       │   │
│  │                                                                      │   │
│  │  **Merge Gate（五路检索去重合并引擎）★v4.4**:                       │   │
│  │  ├─ 输入: 向量检索 + BM25 + KG + DAG场景 + 联联网搜索结果           │   │
│  │  ├─ 去重: embedding余弦相似度(阈值0.92)+文本Jaccard(阈值0.85)双判据 │   │
│  │  ├─ 排序: RRF融合分 + 来源信誉权重                                  │   │
│  │  └─ 输出: merged_context — 统一上下文块供主模型                     │   │
│  │  └─ 元认知参数微调: 检索量高→knowledge_density提升 / 来源多样→familiarity降 │
│  │                                                                      │   │
│  │  命题检索 (Proposition Retrieval)                                    │   │
│  │  └─ 原子命题 → Self-RAG 验证 → 融合                                 │   │
│  │                                                                      │   │
│  │  多源交叉验证 (Enhanced Retrieval)                                   │   │
│  │  └─ 内部记忆 ↔ 网络搜索 ↔ 知识图谱 ↔ 一致性计算                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 3: 向量存储层                               │   │
│  │  四级搜索降级链 (vector_store.py):                                  │   │
│  │  ├─ Level 1: sqlite-vec（C 扩展，最快）                             │   │
│  │  ├─ Level 2: hnswlib（预编译 whl，O(log n)）                       │   │
│  │  ├─ Level 3: 内置 HNSW（纯 Python，兜底）                           │   │
│  │  └─ Level 4: numpy 暴力搜索（O(n)）                                │   │
│  │                                                                      │   │
│  │  统一向量接口: unified_vector_store + vector_api                     │   │
│  │  └─ embedding_enhance: bge-m3 (1024维, 无问芯穹)                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 4: 统一路由层 (SmartProcessor)              │   │
│  │  ★v4.6 — 从三模型通道升级为 R-CCAM 统一路由层                      │   │
│  │  smart_processor.py v2.0.0 — 三模型通道 + 人格注入 + R-CCAM 路由   │   │
│  │                                                                      │   │
│  │  ├─ Flash 通道 (DeepSeek Flash):                                    │   │
│  │  │  结果总结、回答合成、语义过滤                                    │   │
│  │  │                                                                  │   │
│  │  ├─ Pro 通道 (DeepSeek V4 Pro):                                     │   │
│  │  │  查询改写、结果总结、语义过滤（带 KV Cache 前缀共享）             │   │
│  │  │                                                                  │   │
│  │  ├─ VLM 通道 (GLM-4.6V-Flash):                                      │   │
│  │  │  图像理解、场景分析、视觉语义提取                                │   │
│  │  │  ├─ API: https://open.bigmodel.cn/api/paas/v4                    │   │
│  │  │  ├─ OpenAI 兼容格式 (image_url + text prompt)                    │   │
│  │  │  ├─ 支持 reasoning_content 思考链输出                            │   │
│  │  │  └─ 与防幻觉联动: 图像声明验证                                   │   │
│  │  │                                                                  │   │
│  │  ├─ 人格注入:                                                        │   │
│  │  │  构造时接受 persona_context 参数，所有 Flash/Pro system prompt  │   │
│  │  │  自动注入人格，来源: DAG CRITICAL 节点优先 → 文件兜底            │   │
│  │  │                                                                  │   │
│  │  ├─ R-CCAM 统一路由 (process_rccam):                                │   │
│  │  │  替代 _action_phase 内联 Flash/Pro 调用，一站式路由:             │   │
│  │  │  1. Pro 查询改写（带人格 + KV 缓存）                             │   │
│  │  │  2. retrieval_hub 多源检索（向量 + DAG + Web）                   │   │
│  │  │  3. Flash 结果总结（证据摘要）                                   │   │
│  │  │  4. Flash 回答合成（带人格 + 参考资料）                           │   │
│  │  │                                                                  │   │
│  │  ├─ LLM 实例复用:                                                    │   │
│  │  │  接受外部 llm_flash / llm_pro（来自 XiaoYiClawLLM）避免重复     │   │
│  │  │  初始化 OpenAI 客户端，确保 KV Cache 前缀共享和模型名一致性      │   │
│  │  │                                                                  │   │
│  │  └─ 集成点:                                                          │   │
│  │     ├─ enhanced_recall 工作流: 改写 → 检索 → 总结 → 过滤            │   │
│  │     ├─ R-CCAM _action_phase 路由（取代内联 Flash/Pro 调用）         │   │
│  │     ├─ DAG 上下文中继: 缓存 smart_processor 中间结果                │   │
│  │     ├─ Worker RPC smart_process 端点（HTTP :8765 / UDS 可调）       │   │
│  │     └─ Worker RPC understand_image 方法: HTTP :8765 可调            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  │                                                                  │   │
│  │  ├─ 模型路由: 根据任务类型选择快/重通道                             │   │
│  │  │                                                                  │   │
│  │  └─ ACP 持久化通道 (acp_server.py):                                 │   │
│  │     长连接保持 + 会话ID绑定 + 自动重连                              │   │
│  │     支持 DAG 上下文中继跨会话保活                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 6: 缓存管理层                               │   │
│  │  ├─ 语义缓存: 相似查询直接命中                                      │   │
│  │  ├─ 统一缓存: 多级缓存统一接口                                      │   │
│  │  ├─ 近似缓存: 近似匹配加速                                          │   │
│  │  ├─ KV 硬盘缓存: DeepSeek Flash X-Conversation-Id 复用                │   │
│  │  ├─ 双层 KV Cache 优化: user_id + prefix: True (chat_prefix_completion)│   │
│  │  ├─ Pro: user=pro_kv_user_id + extra_headers + prefix: True          │   │
│  │  └─ Flash: extra_body.user_id + prefix: True                         │   │
│  │  └─ RAG 缓存: 检索结果缓存                                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 7: 硬件优化层                               │   │
│  │  ├─ mkl_accelerator: Intel MKL 矩阵加速 + AMX 检测                  │   │
│  │  │  (当前: AVX-512 活跃, 1线程, LD_PRELOAD 劫持 MKL)               │   │
│  │  ├─ fma_accelerator: FMA3 指令集检测 + 自动启用                     │   │
│  │  ├─ io_optimizer: IO 设备检测 + 优化推荐                             │   │
│  │  ├─ numa_optimizer: NUMA 拓扑检测 + 绑定 (当前 1 节点)              │   │
│  │  └─ computational_storage: KV Cache 管理 + 量化                     │   │
│  │                                                                      │   │
│  │  CPU 能力: AVX-512 全套 / FMA3 / AVX2 / SSE4.2 / AES-NI            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 8: 系统可靠性层                             │   │
│  │  ├─ 故障转移 (failover): ACP 连接断开 → 重试 → 切换通道             │   │
│  │  ├─ 自动恢复 (self_healing): 内存泄漏检测 → 异常捕获 → 降级         │   │
│  │  ├─ 弹性系统 (ResilienceSystem): 57 个动态加载组件，故障隔离        │   │
│  │  └─ model_performance: 模型性能记录 + 异常行为检测                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 9: 会话管理层                               │   │
│  │  ├─ DAG 上下文中继 — ContextEngine 注册                            │   │
│  │  │  · registerContextEngine("claw-core-engine") 接管全生命周期      │   │
│  │  │  · ownsCompaction=true：禁用 OpenClaw 默认压缩，DAG 自管          │   │
│  │  │  ├─ ingest（每轮对话后）：smartStore + DAG 节点存储              │   │
│  │  │  ├─ assemble（模型调用前）：预算计算→最近消息→dag_summary        │   │
│  │  │  │  摘要恢复→精简历史(最近4条)+检索注入                          │   │
│  │  │  ├─ compact（触发压缩）：dag_status 检查→assemble 摘要提取       │   │
│  │  │  │  → _sessionSummaries 缓存→dag_compact 创建摘要节点            │   │
│  │  │  └─ afterTurn（维护）：L1 每5轮/L2 每20轮/L3 每50轮              │   │
│  │  │  · 熔断保护：连续3次失败→30s冷却期→降级路径                      │   │
│  │  │  · Worker RPC：dag_ingest/assemble/compact/status/summary        │   │
│  │  │  · ★v4.2 升级: ZMQ 事件推送 (dag_ingest/compact→Plugin) +       │   │
│  │  │  │  mmap 共享内存 (assemble/compact 结果→Plugin 零拷贝读取)       │   │
│  │  │  │  Plugin dagCall 优先 mmap → 未命中才走 UDS                    │   │
│  │  │  │  · 跨 8 层联动：L9 组装 + L1 神经反馈 + L6 硬件 + L11 NLP     │   │
│  │  │  │  · compact 80% 阈值修复：优先取 dagGlobalRatio 而非轮级预算    │   │
│  │  │  │  替换 OpenClaw 默认 contextPruning 机制                       │   │
│  │  ├─ 对话管理 (conversation.py): 上下文窗口 + 会话 ID 绑定           │   │
│  │  └─ 上下文压缩 (context_compressor): 长对话历史摘要 + 关键信息提取  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 10: Persona 管理层                          │   │
│  │  ├─ 人格加固七重防线:                                               │   │
│  │  │  L1: AGENTS.md 增强启动序列（6步自检）                           │   │
│  │  │  L2: claw-bootstrap hook 人格注入 (身份+核心人格混合+自进化上下文)    │   │
│  │  │  |  evolution_tracker.jsonl 最近5条自评 → 注入 system prompt        │   │
│  │  │  L3: headChars 扩容 (1200→2000)                                  │   │
│  │  │  L4: 运行时校验 (auto_update_persona)                            │   │
│  │  │  L5: 记忆驱动恢复管道 (Worker RPC 替代 lobster)                  │   │
│  │  │  L6: session_state 会话快照                                      │   │
│  │  │  L7: 自进化层经验固化                                            │   │
│  │  ├─ **人格视觉（Persona Visual）★v4.4**:                           │   │
│  │  │  ├─ DAG 人格节点 vs 文件状态比对（存在性/时间戳/是否过期检测）  │   │
│  │  │  ├─ 检测不一致自动生成修复建议                                    │   │
│  │  │  └─ 结果注入 cognition_payload（persona_health 字段）            │   │
│  │  ├─ 神经网络算法加固:                                               │   │
│  │  │  ├─ adaptive_ltp_ltd: 每次 store() LTP 增强 / LTD 衰减           │   │
│  │  │  └─ retrieval_formula: recency/relevance/importance 三维加权重排 │   │
│  │  └─ 自动学习: 偏好学习 + 记忆更新 + 智能遗忘                        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 11: NLP 能力层                             │   │
│  │  ├─ 基础层 (0ms, 本地手搓):                                          │   │
│  │  │  ├─ 中文分词 + 词性标注 (jieba)                                  │   │
│  │  │  ├─ 命名实体识别: PER/LOC/ORG/TIME/NUM                           │   │
│  │  │  ├─ 关键词提取: TF / TF-IDF                                      │   │
│  │  │  ├─ 情感分析: SnowNLP + 词典方法                                 │   │
│  │  │  ├─ 文本摘要: 句子重要性评分                                     │   │
│  │  │  └─ 轻量依存句法: 基于词性模板的依赖解析 (SBV/VOB/ATT/ADV/CMP)  │   │
│  │  │                                                                  │   │
│  │  ├─ 增强层 (nlp_enhanced.py — 论文驱动):                            │   │
│  │  │  ├─ 依存句法分析: 基于 POS 模板的轻量依存解析 + 主谓宾三元组     │   │
│  │  │  │  参考: Ramshaw & Marcus (1995) "Text Chunking"                │   │
│  │  │  ├─ 实体链接: 命名实体 → 系统知识库映射 (36个内置实体)          │   │
│  │  │  │  参考: Shen et al. (2015) "Entity Linking: A Survey"          │   │
│  │  │  ├─ 指代消解: 基于就近原则 + 词性角色的代词解析                  │   │
│  │  │  │  参考: Hobbs (1978) "Resolving Pronoun References"            │   │
│  │  │  └─ 对比句检测: A比B更X / A不如B / A和B一样 / 最高级           │   │
│  │  │    参考: Jindal & Liu (2006) "Mining Comparative Sentences"      │   │
│  │  │                                                                  │   │
│  │  └─ Flash NLP 路由层 (thinking_enhanced.py — FlashNLP):            │   │
│  │     ├─ 指代消解 (Flash 版): 理解语义上下文，比就近原则准确得多      │   │
│  │     ├─ 对比检测 (Flash 版): 覆盖各种中文比较表达，无模板漏检       │   │
│  │     └─ 意图分析 (Flash 版): 替代传统关键词分类，理解真实意图       │   │
│  │                                                                      │   │
│  │  集成: 记忆模块(NLP→关键词/实体→存储), 检索(NLP→分词→搜索)        │   │
│  │  策略: 简单任务走 0ms 基础层，复杂语义走 Flash API 层               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 12: 思考技能层 (IntelligentThinkingTrigger v2.0)    │   │
│  │  IntelligentThinkingTrigger v2.0 三论文集成 (RCR-Router + Springdrift + A-ToM):                                                       │   │
│  │  第一性原理 | 系统思维 | 批判性思维 | 逆向思维 | 类比思维           │   │
│  │  费曼技巧 | 多智能体协作 | 决策引擎 | 产品思维                      │   │
│  │                                                                      │   │
│  │  11 个方法论技能 (qiushi-skill):                                    │   │
│  │  武装思想 | 矛盾分析 | 调查研究 | 实践认知 | 持久战                │   │
│  │  集中兵力 | 星星之火 | 统筹全局 | 群众路线 | 批评与自我批评         │   │
│  │                                                                      │   │
│  │  10 个工程与效率技能 (Matt Pocock Skills 适配):                     │   │
│  │  ├─ 工程类 (Layer 12):                                              │   │
│  │  │  diagnose — 系统化 Bug 诊断循环 (复现→假设→插桩→修复)           │   │
│  │  │  grill-with-docs — 需求审问 + 领域语言共建 + ADR                 │   │
│  │  │  tdd — 红-绿-重构，测试驱动开发                                 │   │
│  │  │  improve-codebase-architecture — 模块深度化 + 重构治理            │   │
│  │  │  prototype — 快速原型验证                                         │   │
│  │  │  zoom-out — 全局视角，退一步理解整体                             │   │
│  │  │  grill-me — 方案推敲，反复审问直到决策树清晰                     │   │
│  │  ├─ 效率类:                                                        │   │
│  │  │  caveman — 超压缩通信模式，砍 75% 废话 token                     │   │
│  │  │  handoff — 会话交接文档生成                                       │   │
│  │  │  write-a-skill — 按规范编写新 Skill                             │   │
│  │  │                                                                  │   │
│  │  教员思想 ↔ Matt Pocock 协同映射:                                   │   │
│  │  ├─ 调查研究 → diagnose (先搞清楚再动手)                             │   │
│  │  ├─ 矛盾分析 → grill-with-docs (抓主要矛盾，审清需求)               │   │
│  │  ├─ 实践认知 → improve-codebase-architecture (承认现状，改进架构)   │   │
│  │  ├─ 集中兵力 → prototype (聚焦验证核心假设)                         │   │
│  │  ├─ 统筹全局 → zoom-out (把握整体，看清定位)                        │   │
│  │  ├─ 武装思想 → grill-me (反复推敲，不留盲区)                        │   │
│  │  └─ 批评与自我批评 → tdd (红绿重构，持续验证)                       │   │
│  │                                                                      │   │
│  │  集成: R-CCAM Cognition 阶段触发，intelligent_thinking_trigger 路由   │   │
│  │                                                                      │   │
│  │  增强思考引擎 (thinking_enhanced.py — 三论文方向集成):              │   │
│  │  ├─ Reflexion 反思系统 (Shinn et al. 2023):                         │   │
│  │  │  低分回答 → 分析失败原因 → 存反思三元组(失败/原因/修复)         │   │
│  │  │  下次同类问题 → 注入反思经验 → 避免重复踩坑                    │   │
│  │  │  持久化路径: .learnings/reflexions.jsonl                        │   │
│  │  ├─ Self-Refine 迭代精炼 (Madaan et al. 2023):                     │   │
│  │  │  Judge 低分 → Flash 自我反馈 → 修正回答 → 再评分               │   │
│  │  │  最多 3 轮迭代，全部达标或达上限即停止                          │   │
│  │  │  └─ Multi-Path 多路径探索 (Yao et al. 2023 ToT 风格):              │   │
│  │     问题拆为 3 个视角 → Flash 并行推理 → Flash 评分选最优          │   │
│  │     最优路径 → Pro/Flash 精加工输出                                │   │
│  │     仅高复杂度问题时自动触发（Flash 前置复杂度判断）                │   │
│  │                                                                      │   │
│  │  ★论文引擎 (Worker 后台 4 引擎并行, 每 10 分钟一轮):               │   │
│  │  ├─ RAPTOR 分层摘要树 [Sarthi 2024 arXiv:2401.18059]:             │   │
│  │  │  200+ DAG 节点 → 聚类(15-30个/簇) → Flash 生成摘要 →           │   │
│  │  │  检索先粗筛高层再下钻细节                                       │   │
│  │  ├─ GraphRAG 社区检测 [MS 2024 arXiv:2404.16130]:                 │   │
│  │  │  DAG 提实体关系 → 社区发现 → 按社区聚类检索                     │   │
│  │  ├─ Generative Agents 反思 [Park 2023 arXiv:2304.03442]:          │   │
│  │  │  周期性自省轨迹生成 → 注入 Cognition 阶段                       │   │
│  │  └─ Toolformer 工具路由 [Meta 2023 arXiv:2302.04761]:             │   │
│  │     关键词匹配自动路由到对应工具 (天气/记忆/健康/代码)             │   │
│  │     R-CCAM Control 阶段 use_tool 策略增强                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 13: 多模态层                               │   │
│  │  ├─ 图像理解: xiaoyi-image + deepseek-ocr2 + GLM-4.6V-Flash (三引擎)│   │
│  │  │  ├─ xiaoyi-image: 通用图像描述                                  │   │
│  │  │  ├─ deepseek-ocr2: 文字提取、表格/文档结构化                     │   │
│  │  │  └─ GLM-4.6V-Flash: VLM 视觉理解 (场景/关系/情感)               │   │
│  │  ├─ 图像生成: seedream-image-gen (主力) + PIL (辅助)               │   │
│  │  ├─ 视觉呈现: 记忆可视化 + 知识图谱可视化 + 报告增强                │   │
│  │  ├─ **Visual RAG**: Cognition阶段VLM/OCR2自动提取→上下文注入       │   │
│  │  │  检测 has_image/视觉关键词 → 调VLM语义理解或OCR2文字提取        │   │
│  │  │  智能路由: 场景理解→VLM / 文字提取→OCR2 / 图表→OCR2 CHART      │   │
│  │  ├─ VLM 深度整合:                                                  │   │
│  │  │  understand_image / analyze_scene / verify_image_claim           │   │
│  │  │  与防幻觉联动: 图像声明 VLM 验证                                │   │
│  │  │  与记忆系统联动: 图像语义描述 → 记忆存储                        │   │
│  │  │  与Plugin联动: claw_understand_image 工具注册                    │   │
│  │  └─ API: deepseek-ocr-2 (免费) / GLM-4.6V-Flash (智谱)             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 14: 工作流引擎 + R-CCAM 循环               │   │
│  │  44 个预定义工作流:                                                  │   │
│  │  enhanced_recall | fast_generation | safe_generation | health_check  │   │
│  │  heartbeat_execute | self_rag_query | kg_query | deep_research | ...│   │
│  │                                                                      │   │
│  │  R-CCAM 结构化认知循环 (XiaoYiClawLLM.process):                     │   │
│  │  ├─ Retrieval（检索阶段）:                                           │   │
│  │  │  - 调 recall() 统一入口 → 向量检索 + KG + DAG 摘要 + RRF 融合   │   │
│  │  │  - **Merge Gate**（v4.4）: 五路检索(向量/BM25/KG/DAG场景/联网)  │   │
│  │  │    去重合并→merged_context输出，元认知参数微调                    │   │
│  │  │  - 判 needs_more_info: 检索置信度<0.3 且无 DAG 摘要 = 需要联网   │   │
│  │  │                                                                  │   │
│  │  ├─ Cognition（认知阶段 — 元认知三维评估）:                          │   │
│  │  │  - IntelligentThinkingTrigger 智能分类: 类型/复杂度/困惑度       │   │
│  │  │  - ★元认知产出三维评估: complexity / familiarity / knowledge_density│   │
│  │  │  - **Rails 护栏增强版（v4.4）**:                                  │   │
│  │  │  │  检索结果扫敏感凭据/系统路径→过滤                              │   │
│  │  │  │  认知分析扫违规建议→拦截                                      │   │
│  │  │  ├─ 实时查询检测: 天气/新闻/温度等关键词 → 强制联网               │   │
│  │  │  └─ 降级: 关键词匹配分类 + 意图分析                               │   │
│  │  │                                                                  │   │
│  │  ├─ Control（控制阶段 — 元认知调节器 _meta_regulator）:             │   │
│  │  │  ★由 _meta_regulator 替代固定优先级策略                         │   │
│  │  │  读取 Cognition 产出的 complexity / familiarity / knowledge_density│   │
│  │  │  三维评估 + Merge Gate 微调参数，动态决定策略路径、推理预算、检索深度、探索宽度。│   │
│  │  │  ┌──── 策略阈值（参考值）─────────────────────────────────────   │   │
│  │  │  │ c < 0.35 + f ≥ 0.35 → direct_answer（直答，零检索）          │   │
│  │  │  │ c < 0.35 + f < 0.35 → light_retrieval（轻量检索）            │   │
│  │  │  │ c ≥ 0.35 + f ≥ 0.65 → deep_reasoning（深推理+MultiPath）     │   │
│  │  │  │ c ≥ 0.35 + f < 0.65 + k ≥ 0.35 → full_pipeline（完整管线）   │   │
│  │  │  │ 边界违反 → boundary_violation（友善拒绝）                    │   │
│  │  │  └────────────────────────────────────────────────────────────   │   │
│  │  │  - 推理预算: 0~2 步 / 检索深度: 0~12 / 探索宽度: 1~3           │   │
│  │  │  - 元认知信息传递到 Memory 阶段（跳过/写入决策）                │   │
│  │  │                                                                  │   │
│  │  ├─ Action（行动阶段 — 不生成最终回答，交由主模型）:               │   │
│  │  │  - 所有深度统一走深度 3 完整管线，depth 0/1/2 已移除              │   │
│  │  │  - 所有分支不调 Flash 生成 answer，改为空 answer + action_delegated│   │
│  │  │  - info_insufficient（仅搜索，不生成回答）:                     │   │
│  │  │    ① 用户位置自动注入（user_location.json: 城市/拼音/坐标）    │   │
│  │  │    ② 天气查询 → wttr.in 自动前缀位置 → 写入 weather_data       │   │
│  │  │    ③ 联网搜索 → xiaoyi-web-search 自动前缀位置 → search_results │   │
│  │  │    ④ 全部结果通过 cognition_payload 打包传主模型              │   │
│  │  │  - answer（默认）: Pro 查询改写 + 双路检索 + Merge Gate + reranker →│   │
│  │  │    cognition_payload 打包（改写/Merge Gate重排/摘要/场景/位置/人格视觉）→ 主模型│   │
│  │  │  - boundary_violation → 友善拒绝                                │   │
│  │  │  - clarify_needed → 让用户说清楚                                │   │
│  │  │  - 【多Agent批评者】: Worker 常驻 httpx 调 Pro + thinking       │   │
│  │  │    Action 后逐项检查相关性/事实性/完整性，修正时覆盖 answer       │   │
│  │  │  - 【LLM-as-Judge 自评分】: Flash 三维评分(faithfulness/relevance/    │   │
│  │  │    completeness)，高分自动存 verified_memories.jsonl            │   │
│  │  │                                                                  │   │
│  │  └─ Memory（记忆阶段 — 含内在元认知）:                             │   │
│  │     - 持久化: 存储到向量库 + DAG 节点 + 情感标记                    │   │
│  │     - 突触反馈: LTP 增强 + 摘要节点更新                            │   │
│  │     - 自进化检测                                                    │   │
│  │     - **__memory_phase: 每10轮触发内在元认知分析** ★v4.4            │   │
│  │       Flash以用户视角分析近期体验数据 → 产生进化建议               │   │
│  │     - **__activate_callback**: 根据进化建议类型惰性加载下游模块 ★v4.4 │   │
│  │     - 【隐式偏好收集】: Worker implicit_feedback handler           │   │
│  │       分析用户后续行为信号(纠错/感兴趣/跳话题)→存 .learnings/      │   │
│  │                                                                      │   │
│  │  【重放缓冲区】：R-CCAM 循环前从 verified_memories 采样 top-3      │   │
│  │  置信度≥0.7 按关键词匹配排序，注入 Cognition 阶段的 skill_guide     │   │
│  │                                                                      │   │
│  │  DAG 上下文中继: Init → Process → Persist → Resume                  │   │
│  │  └─ 8 层联动：L9 组装 + L1 双向神经反馈 + L6 硬件 + L11 NLP 索引   │   │
│  │                                                                      │   │
│  │  verify 方法: EnhancedHallucinationGuard.verify_with_cross_validation()
│  │  多源采集: 内部记忆 + recall补充 + 16联网搜索 + 知识图谱            │
│  │  一致性子评分: agreements/disagreements → consensus → is_reliable   │
│  │  降级: ImportError → XiaoYiClawLLM.enhanced_recall                  │
│  │                                                                      │   │
│  │  smart_processor 工作流: 改写 → 检索 → 总结 → 过滤                  │   │
│  │                                                                      │   │
│  │  Worker IPC 三通道（UDS 双向 RPC + ZMQ 双向事件 + mmap 结构化状态）: │   │
│  │  ├─ UDS RPC（★v4.6 双向互通）：                                     │   │
│  │  │  ├─ Gateway 端: 动态 _gatewayMethods 注册表（14 预注册方法      │   │
│  │  │  │  + 自动暴露 api.tools.*），替代旧 switch-case                  │   │
│  │  │  ├─ Worker 端: _GatewayProxy（__getattr__ 透明 RPC 桩）          │   │
│  │  │  │  gateway.ping() / gateway.web_fetch() / gateway.call_tool()    │   │
│  │  │  │  — 像调本地函数一样调 Gateway                                   │   │
│  │  │  └─ 所有工具调用走 UDS 4 字节大端长度前缀协议                   │   │
│  │  ├─ ZMQ ROUTER（★v4.6 双向，非只收不答）：                          │   │
│  │  │  收到 method 请求后查 _gatewayMethods 并 sendReply 回复；        │   │
│  │  │  仅 event 类消息才透传不回复                                    │   │
│  │  ├─ mmap 结构化状态（★v4.6 从 int32 信号量升级）：                  │   │
│  │  │  4KB /var/claw_shared_state 共享区:                              │   │
│  │  │  ├─ JSON 段: 任意结构化数据（配置版本号、队列深度、状态标记等）  │   │
│  │  │  ├─ Gateway 心跳: 每 5s 写入 {pid, uptime, memory_rss, methods} │   │
│  │  │  ├─ Worker 可读写: gateway.mmap_read() / mmap_write()           │   │
│  │  │  └─ 零拷贝: 两边直接读文件，不走 RPC 反序列化                   │   │
│  │  └─ HTTP :8765 保留为二级降级 + 直连调试                            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 15: 统一入口层                             │   │
│  │  ├─ unified_entry.py — CLI 统一入口                                  │   │
│  │  │  支持: health / status / store / recall / workflow               │   │
│  │  ├─ _rails.py — Rails 护栏系统 (PermissionContext + @rail 装饰器)   │   │
│  │  │  ├─ 4 个 @rail 装饰器: store(memory_write), recall(memory_read),  │   │
│  │  │  │  health_check(SESSION), workflow(SYSTEM)                       │   │
│  │  │  ├─ **增强版 Rails ★v4.4**:                                      │   │
│  │  │  │  ├─ 检索结果扫描器: 敏感凭据(API Key/Token)/系统路径/私密数据 │   │
│  │  │  │  └─ 认知分析扫描器: 违规建议检测(越狱/有害指令/社会工程)     │   │
│  │  │  ├─ RailScope: USER / SESSION / SYSTEM / GLOBAL                   │   │
│  │  │  └─ 权限检查: feature 白名单 + scope 级别控制                       │   │
│  │  ├─ xiaoyi_claw_api.py — XiaoYiClawLLM API 接口                      │   │
│  │  │  remember / recall / forget / entity / learn                      │   │
│  │  │  fast_generate / rccam_cycle / answer / verify_image_claim       │   │
│  │  ├─ session_state.py — 会话状态快照管理 (capture/recall)             │   │
│  │  ├─ run_heartbeat.py — 心跳执行脚本                                  │   │
│  │  └─ acp_server.py — ACP 持久化通道服务端                             │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 16: 自进化层                               │   │
│  │  与记忆反思（L1）形成"自动 + 人工"闭环                               │   │
│  │                                                                      │   │
│  │  ├─ 自动: 记忆反思 → 错误检测 → 自动改进 → 决策执行 → 效果验证      │   │
│  │  │  ├─ 低风险 → 自动调参（先备份，后修改，可回滚）                   │   │
│  │  │  ├─ 中风险 → 生成 .sh 脚本到 pending_executions/，等人审批        │   │
│  │  │  └─ 高风险 → 仅记录，跳过                                        │   │
│  │  ├─ 人工: 经验识别 → 用户审批 → 固化文件 → 永久生效                  │   │
│  │  │                                                                  │   │
│  │  ├─ SelfEvolutionEngine：质量自评 + 模式发现 + 进化追踪              │   │
│  │  ├─ ActiveEvolutionScheduler：10分钟周期，趋势分析+自动改进+决策执行  │   │
│  │  │                                                                  │   │
│  │  ├─ **★v4.4 内在元认知进化——用户画像驱动**:                       │   │
│  │  │  ├─ 触发: __memory_phase 每10轮 + ActiveEvolutionScheduler       │   │
│  │  │  ├─ 分析引擎: Flash 以用户视角（8种分析模式）分析体验数据：      │   │
│  │  │  │  工程严谨 | 论文驱动 | 全量推进 | 矛盾分析                    │   │
│  │  │  │  第一性原理 | 系统思维 | 费曼技巧 | 批评与自我批评             │   │
│  │  │  ├─ 进化产出: 结构化进化建议（问题描述/建议类型/优先级/预期效果）│   │
│  │  │  ├─ 惰性激活下游模块（__activate_callback）:                     │   │
│  │  │  │  ├─ AutoTuner（参数调整建议 → 低风险自动执行）                │   │
│  │  │  │  ├─ AutoPersonaUpdater（人格文件更新建议 → 展示待审批）       │   │
│  │  │  │  └─ KnowledgeRefiner（知识库精简/合并/去重建议 → 展示待审批） │   │
│  │  │  └─ 从"自进化上下文注入"升级为"用户视角驱动思考技能分析"          │   │
│  │  │                                                                  │   │
│  │  ├─ 【隐式偏好学习】Worker implicit_feedback RPC 方法               │   │
│  │  │  收集用户纠错/感兴趣/跳话题/正确认信号，持久化到                │   │
│  │  │  .learnings/implicit_preferences.jsonl 与显式自进化并行            │   │
│  │  ├─ darwin-skill: 8维度评分 + git 棘轮机制 + 子 agent 验证          │   │
│  │  ├─ self-improving-agent: 错误学习循环 → .learnings/                │   │
│  │  └─ 目标: AGENTS.md | TOOLS.md | MEMORY.md | evolution-drafts/      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

---

## 🔧 统一入口

### CLI 命令行

```bash
# 健康检查
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py health

# 系统状态
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py status

# 存储记忆
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py store --content "内容"

# 检索记忆
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py recall --query "查询"

# 执行工作流
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py workflow --scenario "名称"

# 心跳执行
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/run_heartbeat.py

# 会话状态快照
python3 ~/.openclaw/workspace/scripts/session_state.py capture --topic "话题"

# 会话状态恢复
python3 ~/.openclaw/workspace/scripts/session_state.py recall
```

### API 接口

```python
from xiaoyi_claw_api import XiaoYiClawLLM

claw = XiaoYiClawLLM()

# 核心记忆
claw.remember("内容")                # 记忆存储
claw.recall("查询")                   # 记忆检索
claw.forget(memory_id)                # 智能遗忘
claw.get_entity("实体名")              # 实体查询
claw.learn({"feedback": "正面"})      # 学习反馈


# R-CCAM 结构化认知循环
claw.rccam_cycle("用户输入", max_cycles=1)

# 验证
claw.verify_image_claim("图片路径", "声明内容")
```

## 📊 统计数据

| 指标 | 数值 | 更新时间 |
|------|------|----------|
| 架构层数 | **16 层** | 2026-05-17 |
| 总能力项 | **440+ 项** | 2026-05-31 |
| IPC 三通道 | **UDS 双向注册表 + ZMQ 双向回复 + mmap 结构化 4KB** | 2026-05-21 |
| Gateway 注册方法 | **14 预注册 + 自动 tool.* 暴露** | 2026-05-21 |
| Worker Gateway 代理 | **_GatewayProxy** 透明 RPC | 2026-05-21 |
| 默认通信 | **UDS RPC** (一级)，HTTP :8765 (二级降级)，无spawnSync | 2026-05-17 |
| 独立服务线程池 | **6 个** (Memory/Retrieval/Session/Thinking/Hardware/HTTP-RPC) | 2026-05-17 |
| Python 模块文件 | **450+ 个** | 2026-05-14 |
| 核心模块目录 | **159 个** | 2026-05-03 |
| 弹性系统加载组件 | **57 个** | 2026-04-27 |
| 技能数 | **93 个** | 2026-05-12 |
| 工作流 | **44 个** | 2026-05-05 |
| 思考技能 | **20 个** (IntelligentThinkingTrigger v2.0) | 2026-06-05 |
| 工程与效率技能 (Matt Pocock) | **10 个** | 2026-05-12 |
| 插件数 | **2 个** | 2026-05-12 |
| Core Skills | **5 个** | 2026-05-05 |
| R-CCAM 循环 | **1 个** | 2026-05-03 |
| DAG 上下文中继 | **1 个** | 2026-05-05 |
| ContextEngine 注册 | **1 个** (claw-core-engine) | 2026-05-05 |
| ACP 持久化通道 | **1 个** | 2026-05-03 |
| SmartProcessor | **1 个** | 2026-05-03 |
| llm-memory-integration | **v3.2.0** | 2026-05-03 |
| DAG 全局窗口比例压缩 | **3 项** | 2026-05-10 |
| 自进化上下文注入 | **1 项** (evolution_tracker→hook system prompt) | 2026-05-10 |
| 主动自进化调度器 | **1 项** (10分钟周期) | 2026-05-10 |
| 决策执行层 | **3 类** (低/中/高风险) | 2026-05-10 |
| Worker RPC 跨会话恢复 | **1 项** (Flash 开推理) | 2026-05-10 |
| 自进化决策执行层 | **3 项** | 2026-05-10 |
| scene_trace 回溯填充 | **330 节点** (旧 268 + old DB 62) | 2026-05-13 |
| 记忆巩固引擎 | **5 项** (CLS/仿生睡眠5阶段/干扰/预测编码/图推理) | 2026-05-14 |
| 增强 NLP | **4 项** (依存/链接/消解/对比) | 2026-05-14 |
| 增强思考 | **3 项** (Reflexion/Self-Refine/MultiPath) | 2026-05-14 |
| 论文引擎 | **4 个** (RAPTOR/GraphRAG/Reflection/Toolformer) | 2026-05-14 |
| Flash NLP | **3 项** (指代消解/对比检测/意图分析) | 2026-05-14 |
| 艾宾浩斯遗忘曲线 | **1 项** (adaptive_ltp_ltd) | 2026-05-14 |
| Worker 自动重启 | **1 项** (exit handler respawn) | 2026-05-14 |
| 隐式偏好学习 | **1 项** (implicit_feedback RPC) | 2026-05-14 |
| 记忆融合 | **单路** (XiaoYiClawLLM) | 2026-05-31 |
| Merge Gate 门禁 | **5 项** + Continuity Guard | 2026-05-12 |
| **Merge Gate（增强版五路检索去重合并）** | **1 项** ★v4.4 | 2026-05-20 |
| **Rails 增强版（敏感凭据扫描+违规建议检测）** | **2 项** ★v4.4 | 2026-05-20 |
| **人格视觉（Persona Visual）** | **1 项** ★v4.4 | 2026-05-20 |
| **用户画像驱动内在元认知** | **8 项**（8种分析模式）★v4.4 | 2026-05-20 |
| **memory_phase 内在元认知触发** | **每10轮触发** ★v4.4 | 2026-05-20 |
| **activate_callback 惰性激活** | **3 项** (AutoTuner/AutoPersonaUpdater/KnowledgeRefiner) ★v4.4 | 2026-05-20 |
| Rails 护栏 | **4 个方法** | 2026-05-12 |
| 系统核心存储 | **~177 MB** (不含工具链) | 2026-05-14 |
| CNB 仓库 | **989 个文件**（清除废弃模块后精简） | 2026-05-19 |
| 系统品牌 | **GalaxyOS** | 2026-05-31 |
| process() 精简 | **70%** (863→254行) | 2026-05-31 |
| Galaxy Kernel | **308行** | 2026-05-31 |
| R-CCAM 异步注入 | **3层兜底** | 2026-05-31 |
| 钩子化工具 | **4 移除** (全部→钩子) | 2026-05-31 |
| Galaxy Kernel 自进化 | **4条/10min** | 2026-05-28 |
| 时空认知论文 | **3 篇** (Graphiti/AriGraph/LASAR) | 2026-05-27 |
| 10论文验证 | **8 编译通过** | 2026-05-27 |
| 自进化首轮产出 | **4 条进化建议** | 2026-05-28 |
| IntelligentThinkingTrigger v2.0三论文集成 | **3 模块** (skill_scorer/thinking_memory/trigger) | 2026-06-05 |
| Cognition Forest 修正 | **4 子树** (user/self/env/meta) | 2026-06-05 |
| core/ 子模块全面同步 | **80 模块** (api/integration/memory/privileged) | 2026-06-05 |
| 问候快速通路 | **~24s→0.1s (240×)** | 2026-06-02 |
| R-CCAM 全链路 | **优化后 10-15s 目标** | 2026-06-02 |
| thinking_skills_content | **分离 skill_guide 独立字段** | 2026-06-02 |
| 思考内容截断 | **500→2000** | 2026-06-02 |
| DAG 上下文排序 | **时间衰减权重重排序** | 2026-06-02 |
| install_wizard.py | **6 阶段全自动自检 + `--kg-test` 专项测试** | 2026-06-02 |

### v5.5 (2026-06-05) — IntelligentThinkingTrigger v2.0 三论文集成 + Cognition Forest 子树修正

- ⭐ **IntelligentThinkingTrigger v2.0** — 三论文集成：RCR-Router（北大等8机构）动态评分引擎 `skill_scorer.py` (28KB, 30 SkillDescriptor) + Springdrift (arXiv 2604.04660) CBR 记忆层 `thinking_memory.py` (14KB) + A-ToM（AAAI 2026, 西北工业大学）认知阶段6推断
- ⭐ **RCR-Router 动态评分** — 四维评分: semantic(0.40)/role(0.20)/stage(0.15)/history(0.10)，贪心路由 top-3 非重复互补
- ⭐ **Springdrift CBR** — ThinkingCase 结构 + Sensorium 持续自感知 + 持久化到 JSON，支持相似 case 召回
- ⭐ **A-ToM 认知阶段推断** — 6阶段 (explore/analyze/verify/breakdown/plan/decide)，pattern 重构：`为什么`→verify, debug 关键词补全
- ⭐ **Cognition Forest 子树内容修正** — user←用户画像 (IDENTITY/SOUL/USER), self←系统能力 (92技能列表), env/meta 不变。运行时 `xiaoyi_claw_api.py` 注入逻辑修复
- ⭐ **core/ 子模块全面同步** — 80 个核心子模块 (api/integration/memory/privileged) 补齐，omega-final 与 GalaxyOS 仓库一致
- 📦 **统计数据更新**：新增 6 项 v5.5 指标


### v5.4 (2026-06-02) — 检索全面修复（JSON污染 + RRF分数压扁 + 多源交叉验证）

- 🐛 **JSON 污染清洗** — `retrieval_hub`: cycle_summary 等节点存 JSON blob，embedding 字段名压扁语义；加 `_extract_plain()` 提取纯文本再索引
- 🐛 **RRF 分数压扁修复** — `_rrf_merge` v3: 混合归一化排名 ×0.4 + 原始语义分 ×0.6，分数从 0.037 恢复到 0.80-0.86
- 🐛 **实体提取全空** — GraphRAG `ent.get('mention')` 替代 `ent.get('text')`，实体不再为空
- 🐛 **采样范围过窄** — 仅扫 200 条 dag_nodes（多为 JSON persona），扩至 300 条含 rccam_nodes
- 🐛 **中文检索失效** — `_do_paper` 改用 `re.findall` 分词，替代 `.split()` 的中文整词问题
- 🔄 **web 通道改为多源交叉验证** — web 不参与 RRF 排名，新增 `_verify_local_with_web()` 验证本地检索，修正 quality 置信度
- 🔄 **web 默认关闭** — 五路本地检索（kg/local/dag/synapse/paper）默认全开，外部验证由调用方按需开启
- 📄 **文档/版本更新**: SKILL.md/README.md v5.3→v5.4


### v5.3 (2026-06-02) — KG as Memory Backbone 4 阶段全链路

- ⭐ **Phase 1: 实体持久化** — `temporal_kg.ingest_text()`: R-CCAM 每轮对话自动提取实体+关系写入 KG，LLM 抽取 + 消歧 + 双向边
- ⭐ **Phase 2: 图检索主通道** — `retrieval_hub._do_kg()`: 第 6 路检索，图遍历 depth 2-3，RRF 自动与向量检索竞争
- ⭐ **Phase 3: Cognition 图推理** — `xiaoyi_claw_api._cognition_phase()`: 共享目标实体检测 + 时序频率分析，注入 thinking_skills_content
- ⭐ **Phase 4: 睡眠图推理** — `biorhythm_sleep_consolidation._deep_sleep_kg_reasoning()`: 实体消歧 + 社区发现 + 30 天低置信度边清理
- 📦 **检索通道升级**: 6 路并行 (kg + local + dag + synapse + paper + web)，KG 优先于向量检索
- 📊 **统计数据更新**: temporal_kg.db 320 entities, 1941 edges
- 📄 **文档更新**: SKILL.md/README.md 版本号 v5.3，新特性表
- 📦 **install_wizard.py v5.3** — 新增 `--kg-test` KG专项测试, Phase2 core/→dist2同步检查, Phase5 KG数据库状态检查
- 📊 **统计数据更新**: 440+ 能力项, KG 320实体/1941边, 989 repo文件


### v5.2 (2026-06-02) — KoRa v2 行为模式引擎 + DAG 上下文持久化修复

- ✨ **KoRa v2 — 行为模式引擎全面升级** — `scripts/kora_behavior.py`
  - 从简单 SQLite 行为日志升级为**时序模式识别 + 自适应参数推荐**
  - 新增 `analyze_patterns()` 四时隙统计（morning/afternoon/evening/night）
  - 新增 `detect_temporal_cycle()` 日/周周期检测（自相关扫描 7 天数据）
  - 新增 `get_strategy_recommendation()` 自适应策略推荐（基于 avg_complexity + negative_rate 调 R-CCAM 参数）
  - 新增 `get_cognition_injection()` 行为模式摘要注入 cognition 阶段
  - 新增 `run_pattern_discovery()` 自动模式发现（时隙-类型关联 + 情感漂移检测）
  - 新增 `record_negative_feedback()` 情感追踪
  - 模式持久化到 SQLite `patterns`/`cycle_cache` 表，重启不丢
  - 保持 v1 接口兼容：`from kora_behavior import KoRaBehaviorEngine` 照常使用
  - Cognition 阶段集成：KoRa 行为摘要自动注入 `skill_guide`，LLM 可见当前时段/策略/情感
- ✨ **DAG 上下文持久化修复** — `scripts/dag_shim.py` + hooks/handler.js
  - 修复 `dag_shim.py` 路径缺失：claw-bootstrap hook 因 `existsSync()` 检测不到文件而跳过全部 DAG 写入
  - 修复 Worker `dag_status` 调用 `should_compact` 参数不匹配（传了 2 个参数但函数只接受 1 个）
  - 当前对话消息（`xiaoyi-channel` session）已正常写入 DAG，持续积累上下文数据
- 📊 **统计数据更新**：总能力项 440+，新增 KoRa v2 7 项能力


### v5.1 (2026-06-02) — R-CCAM 延迟优化 + 四思考技能管道重架构 + DAG 上下文管理器升级 + 安装向导

- ✨ **R-CCAM 延迟优化 — 查询改写+分类二合一** — `_retrieval_phase` 一次 Flash 调用替代两次 Pro 调用，输出格式 `[REWRITTEN]...[TYPE]...`，加关键词兜底防止格式漂移，节省 **1 次 API 往返 (~6s)**
- ✨ **问候/简单查询快速通路** — 检测 `[TYPE]greeting` 或纯关键词命中时，在 retrieval 阶段直接 `should_stop=True`，跳过 Cognition+Control+Action+Memory 四阶段。**~24s → 0.1s（240 倍加速）**
- ✨ **FLARE 并行化** — `ThreadPoolExecutor(max_workers=3)` 并行执行 judge() 和 detect_conflicts() 防幻觉验证，conflicts 只 cycle_count>1 时跑，crag_correction 只 passed=False 时跑。**节省 3-5s**
- ✨ **四思考技能管道重架构** — `_get_thinking_skills_context()` 从 `skill_guide` 完全分离，独立 `thinking_skills_content` 字段。budget routing：<=12字符+事实关键词→`"light"`（仅显技能名），复杂查询（"对比""为什么"）→`"full"`（读 SKILL.md）。思考内容截断 500→2000
- ✨ **DAG 上下文管理器升级** — Cognition Forest 子树常量（`CognitionForestType: USER/SELF/ENV/META`），时间衰减权重排序（30天半衰期），旧消息按时间和重要性排序填充剩余 token，R-CCAM cycle_summary 追加（最近3轮摘要）
- ✨ **WorkflowEngine 链路修复** — `claw_worker.py` sys.path 补 `ORCHESTRATION_DIR`，Worker 重启后 `unified_entry.health_check()` → `WorkflowEngine` 导入正常
- ✨ **记忆验证模块健壮性增强** — `HallucinationGuard from_dict()` 容错：`json.JSONDecodeError` 跳过、非记忆行跳过。`SourceType` 补 `AI_JUDGE="ai_judge"`/`DC_JUDGE="dc_judge"`。`health_check` 仅对已验证记忆检查高置信度，纯 `unverified` 记忆不报警
- ✨ **install_wizard.py（GalaxyOS 安装+配置向导）** — 6 阶段全自动自检：环境→模块语法扫描→文件同步→服务链路（Supervisor→Worker→UDS→ZMQ→mmap→DAG→RCI→Heartbeat）→断路器检测→配置交互编辑。`--fix` 自动修复文件同步，`--report` JSON 报告输出
- ✨ **Heuristic-only 批量记忆验证** — 基于规则检查（绝对语言/数据来源/时效性），零 LLM API 成本。75 条记忆：33 verified_true + 42 conflicting（auto-archived Q&A 历史）
- 📊 **统计数据更新**：总能力项 430+，新增 v5.1 安装向导/延迟优化指标

### v5.0 (2026-05-31) — GalaxyOS 品牌化 + Galaxy Kernel 重构 + 异步注入三层兜底 + 时空认知论文集成

- ✨ **系统品牌更名 GalaxyOS** — 从"小艺 Claw 系统架构"升级为 GalaxyOS
- ✨ **R-CCAM process() 精简 (-70%)** — 863行→254行，删除 engine_integration/paper_integration/时空认知同步等20+非核心串行调用，只保留 SCL 五阶段核心 + RetrievalHub/Reranker/CRAG/Self-RAG/L5护栏。新增认知预算机制（_cognitive_budget 逐阶段递减）
- ✨ **Galaxy Kernel 扩容** — _self_evolution_loop→_galaxy_kernel_loop (308行)，新增模块级 _galaxy_pending 线程安全事件队列，rccam() 返回后 push post_response 事件，循环体6s轮询+Reflexion记录+~50轮自进化
- ✨ **before_agent_reply 阻塞 Bug 修复** — 钩子改成 fire-and-forget 模式：不 await R-CCAM，.then() 回调写缓存 + mmap，timeout 提到 120s
- ✨ **R-CCAM 异步注入三层兜底架构** — assemble() 6a 直接查 _rccamCache 注入 | before_prompt_build 第二层兜底 | agent_end 第三层存 _pendingRccamInjection 下轮捡起
- ✨ **ContextEngine assemble() 加 R-CCAM 注入** — 步骤6.5直接从 _rccamCache 读 cognitionPayload 放入 systemPromptAddition，统一 token budget。注入顺序: [rccamBlock][personaBlock][summaryInjection][smartRecall]
- ✨ **before_prompt_build 精简** — 原来~350行 R-CCAM 格式化渲染全部删除，只剩~100行纯锚定钩子（触发词匹配→强制指令 + 可选记忆验证）
- ✨ **心跳保留 rccam 字段** — 心跳覆写 mmap 时先 read 旧数据保留 rccam 字段，移除 !w.ready 把关
- ✨ **Pro 查询改写移入 _retrieval_phase** — 从 _action_phase（阶段4）移到 _retrieval_phase（阶段1），改写后 query 才进 retrieval_hub，提升检索质量和调用率
- ✨ **recall() 自动查询向量生成** — _init_llm_client() 新增 embedding 客户端，recall() 中 query_vector 为 None 时自动生成
- ✨ **HNSWLib id_map 持久化修复** — _save_index() 补写 id_map 到 meta，重启后不丢数据
- ✨ **DAG 摘要排序优先级修复** — DAG 摘要标记 score=0.0 + _supplementary=True，RRF 融合前 min-max 归一化
- ✨ **RRF 融合权重调整** — DAG 来源结果加 1.5x 权重系数防止 local 路淹没
- ✨ **4 工具→纯钩子模式** — claw_recall(R-CCAM覆盖)/claw_lobster(dead)/claw_health(→agent_end)/claw_verify(→before_prompt_build)全部移除
- ✨ **Galaxy Kernel 自进化首次跑通** — JSON 解析器修复(_extract_json_block)、sys.path 顺序修复、4 条进化建议产出，~10分钟/次
- ✨ **10 论文集成全量验证完成** — 8模块编译通过、7子模块实例化、PaperIntegration 9方法全存在，补 Cognitive Load 钩子
- ✨ **时空认知 3 论文研究** — Graphiti(时序KG三层子图)、AriGraph(情景记忆KG世界模型)、LASAR(潜在认知地图时空推理)深度研究，论文引用已集成
- ✨ **GalaxyOS 管线结构** —
```
用户输入 → before_agent_reply(fire-and-forget R-CCAM) → assemble() 注入
→ before_prompt_build(动态锚定) → LLM推理 → agent_end(兜底)
→ Galaxy Kernel 后台(6s轮询: Reflexion+自进化)
```
- ❌ **移除腾讯云记忆插件** — extensions/memory-tencentdb/ 全量删、tencentdb_integration.py 全量删、retrieval_hub._do_tencentdb 删、full_integration._load_tencentdb/search_tencentdb 删、所有 config 引用清空
- ❌ **移除 Yaoyao Memory 插件** — extensions/yaoyao-memory/ 全量删、yaoyao_bridge.py 全量删、unified_entry store/recall yaoyao 分支移除、配置清空
- ❌ **架构简化: 插件从 5→2** — 只保留 claw-core + xiaoyi-channel，记忆全部走 UnifiedVectorStore 单路
- ❌ **文档同步: Layer 0/SKILL/统计行/更新日志** — 全量移除腾讯云/Yaoyao 引用
- ❌ **删除投机解码 Layer 5 段** — 旧 Speculative Decoding 架构图/功能列表/统计表/更新日志引用全部清除
- 📊 **统计数据更新**：总能力项 420+，新增 v5.0 品牌化指标

### v4.6 (2026-05-21) — SmartProcessor 统一路由 + 三通道透明互通

- ✨ **Layer 4 从三模型通道升级为统一路由层 (SmartProcessor v2.0.0)**：
  - `_action_phase` 内联 Flash/Pro 调用（~90 行）全部移除，改为 `SmartProcessor.process_rccam(state)` 一站式路由
  - 新增 `process_rccam()`: Pro 改写 → retrieval_hub 检索 → Flash 结果总结 → Flash 回答合成
  - SmartProcessor 接受外部 `llm_flash`/`llm_pro` 实例，复用 XiaoYiClawLLM 的 OpenAI 客户端
  - `llm_pro_model` 参数确保模型名与 R-CCAM 一致，修复 Pro 0 调用问题
  - 人格注入统一: 构造时传 `persona_context`，所有 Flash/Pro system prompt 自动注入
  - VLM 第三通道（GLM-4V-Plus）保留为独立方法
  - Worker RPC `smart_process` 端点注册
- ✨ **Gateway-Worker 三通道透明互通（★核心升级）**：
  - **UDS 升级双向 RPC 注册表**：Gateway `startGatewayUdsServer` switch-case → 动态 `_gatewayMethods`（14 预注册 + 自动 `tool.*` 暴露）
  - **Worker `_GatewayProxy` 透明桩**：`__getattr__` 自动远程调用，`gateway.ping()` / `gateway.web_fetch()` / `gateway.call_tool()` 像调本地函数
  - **mmap 32字节 → 4KB 结构化状态**：/var/claw_shared_state，JSON 段 + Gateway 5 秒心跳 {pid, uptime, memory_rss, methods}
  - **ZMQ ROUTER 双向回复**：收到 method 查 `_gatewayMethods` 回复，非只收不答
  - Gateway UDS `registerGatewayMethod()` 统一注册接口
- 📊 **统计数据更新**：总能力项 390+，IPC 指标更新，新增 Gateway 注册方法 / Worker 代理指标

### v4.5 (2026-05-20) — Galaxy 增强：DAG三维绑定 + Cognition Forest + KoRa + Kernel
- ✨ **Galaxy DAG三维绑定（全链路传递）** — Layer 9 + Layer 14 R-CCAM
  - index.js ingest 阶段生成 `galaxyMeta` = {semantic_map, function_map, design_ref}
  - 全链路传递：index.js → Worker dag_ingest → DAGIntegration.add_message_with_scene
  - DAG 节点关联代码入口和实现位置，Kernel 可精确定位问题代码
- ✨ **Cognition Forest 子树复用** — Layer 9 DAG 上下文管理器
  - 四类独立子树：user(用户画像) / self(系统能力) / env(运行环境) / meta(元认知)
  - 子树使用独立 session_key 前缀 `_cog_subtree_`，priority=CRITICAL 永不压缩
  - `add_cognition_subtree()` / `get_cognition_subtree()` / `list_cognition_subtrees()` / `clear_cognition_subtree()`
  - 不同模块可共享子树数据，避免重复抽取
- ✨ **KoRa 主动能力（行为建模+模式识别）** — Layer 15 XiaoYiClawLLM
  - `_koRa_behavioral_model()` — 统计用户 request 类型分布
  - `_koRa_pattern_recognition()` — 频率模式+时序模式检测
  - `_kora_record_request()` — 每次 R-CCAM 循环自动记录
  - `get_next_proactive_task()` 增强 — 空时从 KoRa 模式推荐
- ✨ **Kernel 持续元认知守护进程** — Layer 14 Worker 增强
  - 每 20 次轮检（~4 分钟）检查自进化引擎状态
  - 每 50 次深度进化（~10 分钟）+ ZMQ 推送 `self_evolution` 事件
  - 与 __memory_phase 10 轮内在元认知互补
- 📊 **统计数据更新**：总能力项 375+ → 385+ 项，新增 Galaxy 增强 15+ 项

### v4.4 (2026-05-20) — 人格视觉 + Merge Gate + Rails增强版 + 用户画像驱动内在元认知进化
- ✨ **人格视觉（Persona Visual）** — Layer 10 Persona 管理层新增组件
  - DAG 人格节点 vs 文件状态比对（存在性/时间戳/是否过期检测）
  - 检测不一致自动生成修复建议
  - 结果注入 cognition_payload（persona_health 字段）
- ✨ **Merge Gate（五路检索去重合并引擎）** — Layer 2 检索增强层 + Layer 14 R-CCAM Control 阶段
  - 输入: 向量检索 + BM25 + KG + DAG场景 + 联网搜索结果
  - 去重: embedding余弦相似度(阈值0.92)+文本Jaccard(阈值0.85)双判据
  - 排序: RRF融合分 + 来源信誉权重
  - 输出: merged_context — 统一上下文块供主模型
  - 元认知参数微调: 检索量高→knowledge_density升 / 来源多样→familiarity降
- ✨ **Rails 护栏增强版** — Layer 15 统一入口层 _rails.py
  - 检索结果扫描器: 敏感凭据(API Key/Token)/系统路径/私密数据
  - 认知分析扫描器: 违规建议检测(越狱/有害指令/社会工程)
- ✨ **用户画像驱动内在元认知进化** — Layer 16 自进化层重写
  - Flash 以用户视角（8种分析模式）分析体验数据:
    工程严谨 | 论文驱动 | 全量推进 | 矛盾分析
    第一性原理 | 系统思维 | 费曼技巧 | 批评与自我批评
  - 结构化进化产出 → 惰性激活下游模块
- ✨ **__memory_phase 每10轮触发内在元认知分析**
- ✨ **__activate_callback** — 根据进化建议类型惰性加载:
  - AutoTuner（参数调整建议→低风险自动执行）
  - AutoPersonaUpdater（人格文件更新建议→展示待审批）
  - KnowledgeRefiner（知识库精简/合并/去重建议→展示待审批）
- 📊 **统计数据更新**：总功能数 360+ → 375+ 项，新增15+项

### 模块加载机制说明

| 加载器 | 模块数 | 说明 |
|-------|--------|------|
| **协调器 (UnifiedCoordinator)** | **129 个** | 注册式模块，支持工作流编排 |
| **弹性系统** | **57 个** | 动态加载组件，故障隔离 |
| **ModuleType 种类** | **87 种** | 模块类型分类 |

协调器注册的 129 个模块中，约 10 个为核心活跃模块（记忆、检索、防幻觉等），其余为懒加载注册（按需加载）。模块加载率为 0% 是预期行为——系统设计为懒加载架构，非系统闲置。

### 能力速查

| 能力分类 | 数量 | 说明 |
|---------|------|------|
| 思考方法论 | 20 | 9 个思考技能 + 11 个 qiushi 方法论 |
| GalaxyOS 生态 | 12+ | xiaoyi-* 系列技能（搜索/图像/文档等） |
| 记忆系统 | 9 | 记忆存储/检索/遗忘/场景等 |
| 智能体 | 3 | 多智能体协作/agent-reach/agent-builder |
| 设计创意 | 3 | 前端设计/可视化等 |
| 工具实用 | 30+ | 文件/搜索/转换/分析等 |
| 内置 Core | 5 | 安全校验/脱敏/自进化/AIGC标记等 |
| 插件 | 2 | claw-core, xiaoyi-channel |

其中 qiqing-liuyu 和 Humanizer-zh 为表达风格技能，整合在 SOUL.md 中。

---

## 🎯 设计理念

### 核心原则

1. **规则约束** — 划红线不枷锁，框架内灵活判断
2. **双层记忆** — 持久存储 + 快照恢复
3. **不瞎编** — 多层验证，不确定就说不确定
4. **记得住** — 突触网络 + 情感驱动 + 会话快照
5. **想得深** — R-CCAM 五阶段结构化认知 + 20 个思考技能
