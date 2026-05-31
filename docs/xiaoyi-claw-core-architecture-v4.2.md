# 小艺 Claw 系统架构 v4.2

> **定位**: OpenClaw 的核心底层能力引擎
> **更新时间**: 2026-05-18 19:20
> **架构层数**: 15 层（16层→15层精简）
> **总功能数**: 360+ 项
> **IPC 通道**: UDS RPC（一级主通道）+ ZMQ 事件推送 + mmap 共享内存（DAG上下文零拷贝）
> **默认通信**: UDS RPC，HTTP :8765 二级降级，无 stdin/stdout，无 spawnSync
> **IPC 演进**: stdin/stdout → UDS RPC（单 Worker 单例，Plugin 直连 supervisor 进程）
> **独立服务线程池**: 6 个（Memory/Retrieval/Session/Thinking/Hardware/HTTP-RPC）

---

## 🎯 核心定位

小艺 Claw 系统是 OpenClaw 的**核心底层能力引擎**，提供：

1. **记忆能力** — 三层记忆体系（腾讯云插件 + yaoyao Memory + 本地记忆系统）+ 记忆巩固引擎（CLS固化 + 离线重放 + 艾宾浩斯遗忘曲线 + 干扰合并 + 预测编码冲突检测）+ 隐式偏好学习
2. **检索能力** — 向量检索 + 知识图谱 + Self-RAG + CRAG 混合检索 + CRAG 动态纠错 + 场景锚定注入（Drawing on Memory 双迹编码 + GRAVITY 结构锚定）+ bge-reranker-v2-m3 重排序 + 预测编码冲突检测 + GraphRAG社区检测 [MS 2024] + RAPTOR分层摘要树 [Sarthi 2024]
3. **智能处理能力** — 查询改写（Pro）+ 结果总结（Flash）+ 语义过滤 + 图像理解（SmartProcessor 三模型通道：Flash/Pro/VLM）+ 自进化上下文注入 + Flash 开推理场景编码 + KV 缓存优化 + Flash NLP 路由
4. **思考能力** — 9 个思考技能 + 11 个方法论技能 + 10 个 Matt Pocock 工程技能 + 决策引擎 + 多智能体协作 + 全部 29 技能 SKILL.md 上下文注入 + Reflexion 反思 [Shinn 2023] + Self-Refine 迭代精炼 [Madaan 2023] + Multi-Path 多路径并行探索 [Yao 2023] + Toolformer 工具路由 [Meta 2023] + GA 反思 [Park 2023]
5. **执行能力** — 44 个工作流全 IPC 并行调度 + R-CCAM 结构化认知循环（统一深度管线） + DAG 上下文中继 + Worker UDS 主通道（Plugin直连，无stdin/stdout）+ ZMQ 事件推送 + mmap 共享内存 + Worker 自动重启 + Merge Gate + 后台4论文引擎并行
6. **多模态能力** — 图像理解（三引擎: xiaoyi + DeepSeek-OCR-2 + GLM-4.6V-Flash VLM）+ 图像生成（seedream）+ OCR2 深度整合 + VLM 第三通道 + Visual RAG（Cognition 阶段自动 OCR2/VLM 提取→上下文注入）
7. **可靠性能力** — 防幻觉 10 重检测 + 自我修复 + 故障转移 + ACP 持久化通道 + 全局上下文窗口比例压缩 + 自进化决策执行层 + 系统消息噪声过滤 + Rails 护栏系统 + 隐式偏好学习 + Worker 自动重启 + Merge Gate 合入门禁

---

| **文档版本**: v4.2 | 2026-05-18 19:20 | UDS主通道 + ZMQ/mmag DAG + 统一深度管线 + 用户位置注入 |

---

*小艺 Claw — OpenClaw 的核心底层能力引擎*
*文档版本: v4.2 | 最后更新: 2026-05-18 19:20*

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
│                      小艺 Claw 系统架构 v4.2                               │
│                         (核心底层能力引擎 · 15层)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 0: 记忆架构                                 │   │
│  │  ┌─────────────────────────┐  ┌─────────────────────────┐           │   │
│  │  │  腾讯云记忆插件 (持久层) │  │  Session State (快照层) │           │   │
│  │  │  L0: 对话存储           │  │  capture（只录不recall）│           │   │
│  │  │  L1: 结构化记忆         │  │  跨会话恢复由DAG接管   │           │   │
│  │  │  向量库 + 三元组        │  │  长上下文保活           │           │   │
│  │  └─────────────────────────┘  └─────────────────────────┘           │   │
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
│  │  │  记忆巩固引擎 (memory_consolidation.py — 四论文方向集成)     │   │   │
│  │  │  ├─ CLS 互补学习系统: DAG高重要性节点→突触网络长期固化     │   │   │
│  │  │  │  参考: McClelland et al. (1995) "CLS"                     │   │   │
│  │  │  ├─ 离线重放: 空闲时强化高频突触路径+修剪弱突触            │   │   │
│  │  │  │  参考: Wilson & McNaughton (1994) "Replay"               │   │   │
│  │  │  ├─ 干扰合并: 相似记忆自动合并/替换,降低冗余               │   │   │
│  │  │  │  参考: Retrieval-Induced Forgetting 干扰理论              │   │   │
│  │  │  └─ 预测编码: 检索结果冲突检测,标记矛盾项需验证            │   │   │
│  │  │    参考: Friston (2010) "Free Energy Principle"             │   │   │
│  │  │  └─ 运行: Worker 常驻后台 daemon 线程, 每5分钟一轮           │   │   │
│  │  │                                                              │   │   │
│  │  │  自适应遗忘 (adaptive_forgetter) + 情感记忆 (emotion_memory)   │   │   │
│  │  └──────────────────────────────────────────────────────────────┘   │   │
│  │                                                                      │   │
│  │  Yaoyao Memory (v1.5.0, 插件接管) — 归属 L1 记忆核心层               │   │
│  │  ├─ 定位: 核心记忆的辅助检索层 + 情感记忆增强                        │   │
│  │  ├─ 集成: SQLite FTS5 + hnswlib 混合搜索 + bge-reranker-v2-m3 重排序│   │
│  │  ├─ 三路记忆融合: store() 三路写入 / recall() 三路融合 RRF            │   │
│  │  ├─ 心理学模型: PersonaStateMachine (mood/energy/trust)             │   │
│  │  │              + Ekman 6 情绪分析 + L4 反馈学习                    │   │
│  │  ├─ 情感: 小艺管记忆存取强度(突触权重), Yaoyao管交互风格           │   │
│  │  ├─ 34 个工具: mood / timeline / graph / trends / quality /        │   │
│  │  │  cloud_sync / psychology / insights / export / ...               │   │
│  │  └─ 分工(双持不冲突):                                               │   │
│  │     ├─ 小艺 Claw: 自动记忆管理(Recall/Store/Capture) + 推理         │   │
│  │     └─ Yaoyao: 按需工具(搜索/可视化/趋势/导出/云备份)             │   │
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
│  │                    Layer 4: 智能处理层 (SmartProcessor)              │   │
│  │  smart_processor.py — 三模型通道                                    │   │
│  │                                                                      │   │
│  │  ├─ Flash 通道 (DeepSeek Flash):                                    │   │
│  │  │  简单查询、过滤、快速响应                                        │   │
│  │  │                                                                  │   │
│  │  ├─ Pro 通道 (DeepSeek V4 Pro):                                     │   │
│  │  │  查询改写、结果总结、语义过滤                                    │   │
│  │  │                                                                  │   │
│  │  ├─ VLM 通道 (GLM-4.6V-Flash) ★新增:                               │   │
│  │  │  图像理解、场景分析、视觉语义提取                                │   │
│  │  │  ├─ API: https://open.bigmodel.cn/api/paas/v4                    │   │
│  │  │  ├─ OpenAI 兼容格式 (image_url + text prompt)                    │   │
│  │  │  ├─ 支持 reasoning_content 思考链输出                            │   │
│  │  │  └─ 与防幻觉联动: 图像声明验证                                   │   │
│  │  │                                                                  │   │
│  │  └─ 集成点:                                                          │   │
│  │     ├─ enhanced_recall 工作流: 改写 → 检索 → 总结 → 过滤            │   │
│  │     ├─ R-CCAM Cognition 阶段: 深度分析 + 决策建议 + 视觉注入        │   │
│  │     ├─ DAG 上下文中继: 缓存 smart_processor 中间结果                │   │
│  │     └─ Worker RPC understand_image 方法: HTTP :8765 可调            │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 5: LLM 优化层                               │   │
│  │  ├─ 投机解码 (Speculative Decoding)                                  │   │
│  │  │  核心原理: 小模型生成草稿 → 大模型并行验证 → 拒绝采样保持分布一致 │   │
│  │  │  参考: https://www.cnblogs.com/rossiXYZ/p/18837229                │   │
│  │  │  ├─ Draft Model（草稿模型）:                                      │   │
│  │  │  │  L1: 检索型草稿（FAISS/Qdrant → Trie 草稿序列）              │   │
│  │  │  │  L2: 模型型草稿（NIM 小模型 → 草稿序列，40次/分钟速率限制）  │   │
│  │  │  │  L1 + L2 并发生成草稿，草稿+验证链并行                        │   │
│  │  │  └─ Target Model（验证模型）:                                     │   │
│  │  │     L3: DeepSeek Flash 作为验证模型                                │   │
│  │  │     接受：草稿经前缀续写验证通过 → 直接返回续写结果              │   │
│  │  │     拒绝：草稿不通过 → 走 API 直接生成（兜底）                    │   │
│  │  │  └─ 集成: XiaoYiClawLLM.fast_generate() 调用 SmartHybridGenerator │   │
│  │  │  └─ X-Conversation-Id: KV 硬盘缓存复用 (缓存价2.5折)              │   │
│  │  │  └─ prefix: True 前缀续写验证草稿                                │   │
│  │  │  └─ OCR2Preprocessor: 图片输入 → OCR2 → 检索预处理                │   │
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
│  │  · ★v4.2 升级: ZMQ 事件推送 (dag_ingest/compact→Plugin) +        │   │
│  │  │  mmap 共享内存 (assemble/compact 结果→Plugin 零拷贝读取)       │   │
│  │  │  Plugin dagCall 优先 mmap → 未命中才走 UDS                    │   │
│  │  │  · 跨 8 层联动：L9 组装 + L1 神经反馈 + L6 硬件 + L11 NLP       │   │
│  │  │  · compact 80% 阈值修复：优先取 dagGlobalRatio 而非轮级预算       │   │
│  │  │  替换 OpenClaw 默认 contextPruning 机制                          │   │
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
│  │                    Layer 12: 思考技能层                             │   │
│  │  9 个思考技能:                                                       │   │
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
│  │  ★新·论文引擎 (Worker 后台 4 引擎并行, 每 10 分钟一轮):            │   │
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
│  │  │  └─ GLM-4.6V-Flash ★新增: VLM 视觉理解 (场景/关系/情感)        │   │
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
│  │  │  - 判 needs_more_info: 检索置信度<0.3 且无 DAG 摘要 = 需要联网   │   │
│  │  │                                                                  │   │
│  │  ├─ Cognition（认知阶段）:                                           │   │
│  │  │  - IntelligentThinkingTrigger 智能分类: 类型/复杂度/困惑度       │   │
│  │  │  - 实时查询检测: 天气/新闻/温度等关键词 → 强制联网               │   │
│  │  │  - 降级: 关键词匹配分类 + 意图分析                               │   │
│  │  │                                                                  │   │
│  │  ├─ Control（控制阶段 — 策略选型）:                                 │   │
│  │  │  优先级: info_insufficient → boundary_violation →               │   │
│  │  │          action_required → clarify_needed → answer               │   │
│  │  │  - info_insufficient 条件: needs_more_info=True 且 cycle≤1       │   │
│  │  │  - 设定回退策略和边界约束                                        │   │
│  │  │                                                                  │   │
│  │  ├─ Action（行动阶段 — 不生成最终回答，交由主模型）:               │   │
│  │  │  - 【v4.2 改造】所有深度统一走深度 3 完整管线，depth 0/1/2 已移除│   │
│  │  │  - 所有分支不调 Flash 生成 answer，改为空 answer + action_delegated|   │
│  │  │  - info_insufficient（仅搜索，不生成回答）:                     │   │
│  │  │    ① 用户位置自动注入（user_location.json: 城市/拼音/坐标）    │   │
│  │  │    ② 天气查询 → wttr.in 自动前缀位置 → 写入 weather_data       │   │
│  │  │    ③ 联网搜索 → xiaoyi-web-search 自动前缀位置 → search_results │   │
│  │  │    ④ 全部结果通过 cognition_payload 打包传主模型              │   │
│  │  │  - answer（默认）: Pro 查询改写 + 双路检索 + reranker →         │   │
│  │  │    cognition_payload 打包（改写/重排/摘要/场景/位置）→ 主模型   │   │
│  │  │  - boundary_violation → 友善拒绝                                │   │
│  │  │  - clarify_needed → 让用户说清楚                                │   │
│  │  │  - 【多Agent批评者】: Worker 常驻 httpx 调 Pro + thinking       │   │
│  │  │    Action 后逐项检查相关性/事实性/完整性，修正时覆盖 answer       │   │
│  │  │  - 【LLM-as-Judge 自评分】: Flash 三维评分(faithfulness/relevance/    │   │
│  │  │    completeness)，高分自动存 verified_memories.jsonl            │   │
│  │  │                                                                  │   │
│  │  └─ Memory（记忆阶段）:                                            │   │
│  │     - 持久化: 存储到向量库 + DAG 节点 + 情感标记                    │   │
│  │     - 突触反馈: LTP 增强 + 摘要节点更新                            │   │
│  │     - 自进化检测                                                    │   │
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
│  │  ★更新: 取代 XiaoYiClawLLM.enhanced_recall 降级方案                │
│  │  多源采集: 内部记忆 + recall补充 + 16联网搜索 + 知识图谱            │
│  │  一致性子评分: agreements/disagreements → consensus → is_reliable   │
│  │  降级: ImportError → XiaoYiClawLLM.enhanced_recall                  │
│  │                                                                      │
│  │  smart_processor 工作流: 改写 → 检索 → 总结 → 过滤                  │   │
│  │                                                                      │   │
│  │  Worker IPC 三通道（UDS 主通道 + ZMQ 事件推送 + mmap 共享内存）:    │   │
│  │  ├─ UDS RPC（一级）：Plugin 直连 supervisor Worker，替代 stdin/stdout│   │
│  │  │  所有工具调用走 UDS 4 字节大端长度前缀协议                       │   │
│  │  ├─ ZMQ PUB/SUB（事件推送）: dag_ingest/compact 事件 → Plugin 刷新  │   │
│  │  ├─ mmap 共享内存（零拷贝）: dag_assemble/compact 结果 → mmap 写入  │   │
│  │  │  Plugin dagCall(dag_assemble) 优先从 mmap 读，跳过 UDS 反序列化   │   │
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

# 投机解码快速生成
claw.fast_generate("查询", top_k=3)

# R-CCAM 结构化认知循环
claw.rccam_cycle("用户输入", max_cycles=1)

# 验证
claw.verify_image_claim("图片路径", "声明内容")
```

## 📊 统计数据

| 指标 | 数值 | 更新时间 |
|------|------|----------|
| 架构层数 | **15 层** | 2026-05-17 |
| 总功能数 | **360+ 项** | 2026-05-17 |
| IPC 四通道 | **4** (HTTP+UDS+ZMQ+mmap) | 2026-05-17 |
| 默认通信 | **UDS RPC** (一级)，HTTP :8765 (二级降级)，无spawnSync | 2026-05-17 |
| 独立服务线程池 | **6 个** (Memory/Retrieval/Session/Thinking/Hardware/HTTP-RPC) | 2026-05-17 |
| Python 模块文件 | **450+ 个** | 2026-05-14 |
| 核心模块目录 | **159 个** | 2026-05-03 |
| 弹性系统加载组件 | **57 个** | 2026-04-27 |
| 技能数 | **93 个** | 2026-05-12 |
| 工作流 | **44 个** | 2026-05-05 |
| 思考技能 | **20 个** | 2026-05-12 |
| 工程与效率技能 (Matt Pocock) | **10 个** | 2026-05-12 |
| Yaoyao Memory 工具 | **34 个** | 2026-05-12 |
| 插件数 | **5 个** | 2026-05-12 |
| Core Skills | **5 个** | 2026-05-05 |
| 腾讯云 L0 对话 | **4000+ 条** | 2026-05-02 |
| 腾讯云 L1 记忆 | **33+ 条** | 2026-05-02 |
| 腾讯云向量库 | **95 MB** | 2026-05-02 |
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
| 记忆巩固引擎 | **4 项** (CLS/重放/干扰/预测编码) | 2026-05-14 |
| 增强 NLP | **4 项** (依存/链接/消解/对比) | 2026-05-14 |
| 增强思考 | **3 项** (Reflexion/Self-Refine/MultiPath) | 2026-05-14 |
| 论文引擎 | **4 个** (RAPTOR/GraphRAG/Reflection/Toolformer) | 2026-05-14 |
| Flash NLP | **3 项** (指代消解/对比检测/意图分析) | 2026-05-14 |
| 艾宾浩斯遗忘曲线 | **1 项** (adaptive_ltp_ltd) | 2026-05-14 |
| Worker 自动重启 | **1 项** (exit handler respawn) | 2026-05-14 |
| 隐式偏好学习 | **1 项** (implicit_feedback RPC) | 2026-05-14 |
| 三路记忆融合 | **3 路** (XiaoYiClawLLM + yaoyao + 降级) | 2026-05-12 |
| Merge Gate 门禁 | **5 项** + Continuity Guard | 2026-05-12 |
| Rails 护栏 | **4 个方法** | 2026-05-12 |
| 系统核心存储 | **~177 MB** (不含工具链) | 2026-05-14 |
| CNB 仓库 | **1317 个文件 / 260 MB** | 2026-05-14 |

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
| 小艺生态 | 12+ | xiaoyi-* 系列技能（搜索/图像/文档等） |
| 记忆系统 | 9 | 记忆存储/检索/遗忘/场景等 |
| 智能体 | 3 | 多智能体协作/agent-reach/agent-builder |
| 设计创意 | 3 | 前端设计/可视化等 |
| 工具实用 | 30+ | 文件/搜索/转换/分析等 |
| 内置 Core | 5 | 安全校验/脱敏/自进化/AIGC标记等 |
| 插件 | 4 | claw-core / memory-tencentdb / better-gateway / xiaoyi-channel |

其中 qiqing-liuyu 和 Humanizer-zh 为表达风格技能，整合在 SOUL.md 中。

---

## 🎯 设计理念

### 核心原则

1. **规则约束** — 划红线不枷锁，框架内灵活判断
2. **双层记忆** — 持久存储 + 快照恢复
3. **不瞎编** — 多层验证，不确定就说不确定
4. **记得住** — 突触网络 + 情感驱动 + 会话快照
5. **想得深** — R-CCAM 五阶段结构化认知 + 20 个思考技能
6. **跑得快** — 投机解码 Draft → Target 验证 → 拒绝采样
7. **能多模态** — 看图、画图、OCR、可视化
8. **可调用** — API + CLI + 心跳 + 会话快照
9. **能推送** — 双渠道推送 + 任务完成通知
10. **Beta 端点** — 全线迁移至 https://api.deepseek.com/beta

### 架构特点

| 特点 | 说明 |
|------|------|
| **分层解耦** | 16 层架构，各层职责清晰 |
| **认知循环** | Retrieval → Cognition → Control → Action → Memory |
| **上下文中继** | DAG 替换 OpenClaw 默认 contextPruning |
| **投机解码** | 草稿 + 验证 + 拒绝采样，真·投机解码 |
| **并行加速** | L1 检索 + L2 NIM 并发生成草稿 |
| **持久连接** | ACP 长连接 + KV Cache 会话复用 |
| **自进化** | 错误检测 → 自动改进 → 用户审批 → 固化 |
| **事件驱动** | 备份、推送、同步均为事件触发 |

---

## 📝 更新日志

### v4.2 (2026-05-18 19:20) — UDS主通道（Plugin直连）+ ZMQ+mmap DAG + 统一深度管线 + 用户位置注入

- ✨ **Plugin→Worker 通信从 stdin/stdout 升级为 UDS RPC 主通道**：
  - Plugin `ClawWorkerClient` 重写：启动时优先连已有 UDS socket（supervisor Worker），未命中才 spawn
  - 同一 Worker 进程被 Plugin 和 supervisor 共享，消除双 Worker 打架问题
  - UDS ping 0.0ms，通信延迟从 spawn ~900ms 降至 ~0ms
  - 保留 stdin/stdout 为初始 ready 事件，ready 后切 UDS
- ✨ **ZMQ PUB/SUB 事件推送接入 DAG 上下文管理器**：
  - `dag_ingest` 完成后 ZMQ 推送 ingest 事件
  - `dag_compact` 完成后 ZMQ 推送 compact 事件
  - Plugin 端初始化 ZMQ Subscriber 持续监听，收到事件后自动刷新 mmap 缓存
- ✨ **mmap 共享内存用于 DAG 上下文零拷贝**：
  - `dag_assemble` / `dag_compact` 结果写入 `claw_worker_mmap`
  - Plugin 侧 `dagCall("dag_assemble")` 优先从 mmap 读取代 UDS 反序列化
  - 大 payload（10KB+上下文）零拷贝读取，跳过 JSON 序列化/反序列化开销
- ✨ **R-CCAM Action 阶段改造：不再生成回答，全部交由主模型**：
  - Depth 0/1/2 提前返回块全部移除，所有请求统一走深度 3 完整管线
  - 所有分支（answer / info_insufficient / clarify_needed）不调用 Flash 生成 answer
  - 改为空 answer + `action_delegated` 标记
  - `cognition_payload` 打包完整中间产物（改写/重排/摘要/场景/联网结果）供主模型使用
- ✨ **用户位置自动注入**：
  - `user_location.json` 记录城市/省份/拼音/坐标
  - 天气查询 → wttr.in 自动前缀位置（城市中文→拼音降级）
  - 联网搜索 → 关键词自动前缀城市，搜索结果精准定位
  - `cognition_payload` 新增 `user_location` / `weather_data` / `search_results` 字段

### v4.1 (2026-05-17 20:49) — HTTP通道 + VLM第三通道 + verify重写 + Plugin路由层重构 + 持久化路径
- ✨ **HTTP JSON-RPC 端口 (:8765)** — Worker 新增 `_http_serve()` daemon 线程，纯 Python http.server，零依赖
  - POST JSON-RPC 2.0 (与 UDS 同一 `_METHODS` 注册表)
  - GET `/` 返回 Worker 元信息 (可用方法列表)
  - daemon 线程，服务线程池扩展: 5 → 6 个
- ✨ **VLM 第三通道 (GLM-4.6V-Flash)** — SmartProcessor 从双模型升级为三模型架构
  - `_call_vlm()`: 调用智谱 GLM-4.6V-Flash (OpenAI 兼容格式)
  - `understand_image()`: Worker RPC 入口，支持 image_url + prompt
  - 返回结构化结果: content + reasoning + model
  - API: https://open.bigmodel.cn/api/paas/v4
- ✨ **Plugin 加 claw_understand_image 工具注册** — OpenClaw 工具链可调
- ✨ **verify 方法重写** — 从 `XiaoYiClawLLM.enhanced_recall` 改为 `EnhancedHallucinationGuard.verify_with_cross_validation()`
  - 多源采集: 内部记忆 + recall() 补充 + 16 联网搜索 + 知识图谱
  - 一致性计算: agreements/disagreements → consensus → is_reliable
- ✨ **Plugin 路由层重构** — `ClawWorkerClient.call()` 调用链改为双通道降级
  - **一级** UDS RPC (首选，延迟0.2ms)
  - **二级** HTTP POST :8765 (UDS降级，延迟4ms)
  - 移除 spawnSync 兜底（Worker 全栈负责）
  - `stop()` 只断连接不杀进程 (supervisor 管重启)
- ✨ **IPC 路径持久化** — 从 `/tmp` + `/dev/shm` 迁移到 `~/.openclaw/extensions/claw-core/var/`
- ✨ **supervisor 配置修复** — `autostart=true` `autorestart=true`
- ✨ **Worker 服务线程池** — 6 个: Memory/Retrieval/Session/Thinking/Hardware/HTTP-RPC
- 📊 **总功能数更新**：360+ 项（新增 VLM 3 项 + HTTP 端口 + Plugin 工具 + verify 改造 + 路径修复）

### v4.0 (2026-05-15 08:10) — 15层精简 + 四通道IPC + 6线程池 + 4论文引擎
- ✨ **架构精简 16→15 层**：合并 L14(工作流)+L15(统一入口) 为 R-CCAM 认知循环层，新增 L15 系统能力层(IPC+自进化+安全)，Lobster 全由 Worker IPC RPC 替代
- ✨ **IPC 四通道全线就绪**：UDS RPC(var/claw-worker.sock) + ZMQ SUB(:5559) + mmap(var/claw_worker_mmap) + HTTP JSON-RPC(:8765)，UDS 为主通道（0.2ms），ZMQ+mmap 补齐异步事件和零拷贝大结果
- ✨ **六独立服务线程池**：MemoryService / RetrievalService / SessionService / ThinkingService / HardwareService / HTTPService 各挂载到 Worker，并行不阻塞
- ✨ **五论文引擎全量接入 Worker 后台**（每 10 分钟并行一轮）：
  - **RAPTOR 分层摘要树** [Sarthi 2024 arXiv:2401.18059]：200+ DAG 节点聚类建摘要树，检索粗筛下钻
  - **GraphRAG 社区检测** [MS 2024 arXiv:2404.16130]：实体关系图社区发现，按社区聚类检索
  - **Generative Agents 反思** [Park 2023 arXiv:2304.03442]：周期性自省轨迹，注入 Cognition 阶段
  - **Toolformer 工具路由** [Meta 2023 arXiv:2302.04761]：关键词匹配路由，Control 阶段 use_tool 增强
  - 四引擎通过 Flash API 驱动，Worker 常驻连接
- 📊 **总功能数更新**：335+ → 360+ 项
- ✨ **Visual RAG 自动路由**（参考: Gupta et al. 2024 "Visual RAG" 思路）
  - `_cognition_phase` 检测 `has_image=True` + 视觉关键词（"看/识别/截图/图片"等）→ 自动调 OCR2
  - OCR2 提取文字/结构化数据 → 注入 `state.analysis.skill_guide` → Action 阶段自动带走
  - 智能模式判断：表格图表走 CHART，文档海报走 DOCUMENT，其余走 OCR
  - `process()` 新增 `has_image` / `image_source` 参数，Worker RPC 透传
  - `PhaseState` 新增 `has_image` / `image_source` 字段
- 📊 **总功能数更新**：330+ → 335+ 项（Visual RAG 1 + OCR 模式3 + 参数 2）

### v3.4.2 (2026-05-14 19:45) — 艾宾浩斯遗忘曲线 + 记忆巩固引擎 + 增强NLP + Flash NLP路由 + Reflexion/Self-Refine/Multi-Path + Worker自动重启
- ✨ **艾宾浩斯遗忘曲线**（Ebbinghaus 1885）— `adaptive_ltp_ltd.py` LTD 机制革命性重构
  - 原线性分段衰减 → R = e^(-t/S) 指数衰减
  - 记忆强度 S 由强化次数×重要性决定（S = base × 2^min(reinforce,5) × (1+importance)）
  - 验证：t=S 时保留率 0.41（理论 0.37），3次强化后7天保留率从21%→82%
  - 旧模型前7天零衰减（错误），新模型第1天就衰减12%（接近真实遗忘节奏）
- ✨ **记忆巩固引擎**（memory_consolidation.py — 四论文方向）— Worker 常驻后台 daemon 线程
  - **CLS 互补学习系统**（McClelland 1995）：DAG 高重要性节点 → 突触网络固化，每5分钟一轮
  - **离线重放巩固**（Wilson & McNaughton 1994）：空闲时强化高频突触路径 + 艾宾浩斯修剪弱突触
  - **干扰合并**（Retrieval-Induced Forgetting）：新记忆写入时 cosine 相似度检测，≥0.85 自动合并；无 embedding 时 jieba 关键词重叠降级匹配
  - **预测编码冲突检测**（Friston 2010 Free Energy）：检索结果含否定词+共享关键词 → 降低置信度 + 标记需验证，持久化冲突记录
- ✨ **增强 NLP**（nlp_enhanced.py — 四论文方向）
  - **依存句法分析**（Ramshaw & Marcus 1995）：基于 POS 模板的轻量依赖解析（SBV/VOB/ATT/ADV/CMP）+ 主谓宾三元组提取
  - **实体链接**（Shen et al. 2015）：36 个系统内置实体（小艺Claw / OpenClaw / 腾讯云插件 / DAG 等），别名匹配+知识库跳转
  - **指代消解**（Hobbs 1978）：就近原则+词性角色的代词解析，支持人称代词+指示代词
  - **对比句检测**（Jindal & Liu 2006 "Mining Comparative Sentences"）：A比B更X / A不如B / A和B一样 / 最高级，基于 jieba 分词定位比较主体
- ✨ **Flash NLP 路由**（thinking_enhanced.py FlashNLP）
  - 指代消解（Flash 版）：理解语义上下文，比就近原则准确得多
  - 对比检测（Flash 版）：无模板漏检，覆盖各种中文比较表达
  - 意图分析（Flash 版）：非传统关键词分类，理解真实用户意图
  - 策略：简单 0ms 走规则，复杂语义走 Flash API
- ✨ **Reflexion 反思系统**（Shinn et al. 2023）— Judge 低分自动触发
  - Flash 分析失败原因（幻觉/遗漏/矛盾/冗余/偏离）
  - 存储反思三元组（失败模式/根因/修复策略）到 .learnings/reflexions.jsonl
  - 下次同类问题启动时注入，避免重复踩坑
- ✨ **Self-Refine 迭代精炼**（Madaan et al. 2023）— Judge 低分后自动迭代
  - Flash 根据自我反馈修正回答 → 再评分 → 最多3轮
  - 全部 ≥7 或达上限即停止
- ✨ **Multi-Path 多路径探索**（Yao et al. 2023 ToT 风格）
  - Flash 前置判断复杂度，仅 high 自动触发
  - 问题拆 3 个视角并行推理 → Flash 评分选最优路径
- ✨ **Worker 自动重启** — claw-core exit handler 新增自动 respawn
  - Worker 崩溃退出后自动重启，延迟 2s → 4s → 6s ... 最大 30s
  - 最多试 5 次，不依赖 supervisor 或 Gateway
- ✨ **隐式偏好学习完整落地** — Worker implicit_feedback RPC 方法补上
  - 纠错信号持久化到 .learnings/implicit_preferences.jsonl
  - 与现有显式自进化体系并行工作
- ✨ **思考技能全联动完善** — Cognition 阶段 29 个技能全映射
- 📊 **总功能数更新**：295+ → 330+ 项（艾宾浩斯1 + 巩固4 + NLP4 + FlashNLP3 + 思考3 + Worker重启1 + 隐式1 + 其他~18）


- ✨ **方向1: 多Agent批评者（论文: AutoGen/ChatDev）** — Worker 常驻进程内 httpx 直调 DeepSeek Pro + thinking
  - R-CCAM Action 阶段后：批评者逐项检查相关性/事实性/完整性/模糊性
  - 思考 token（reasoning_content）隔离在 API 响应内，只取 content 覆盖回答
  - 有修正时覆盖 `generated_answer`，标记 `critic_applied: True`
  - 批评者上下文注入验证记忆参考（重放缓冲区）+ 思考技能指导
- ✨ **方向2: LLM-as-Judge 自评分（论文: RAGAS）** — Action 阶段后用 Flash 对回答打分
  - 三维评分：faithfulness(忠实度) / relevance(相关性) / completeness(完整性) 各 1-10
  - 三项均 ≥7 → 自动存入 `verified_memories.jsonl` 作为正例（供重放缓冲区使用）
  - 低分不存储，不影响主流程
- ✨ **方向3: 重放缓冲区（论文: EWC/Progressive NNs）** — R-CCAM 循环前采样验证记忆
  - 从 `verified_memories.jsonl` 按置信度+关键字匹配排序，top-3 注入
  - 置信度 ≥0.7 才入选，注入到 `state.analysis['replay_buffer']`
  - Cognition 阶段自动合并到 `skill_guide`，Action/总结都可看到
  - 与 Judge 形成闭环：Judge 高分 → 存验证记忆 → 后续查询重放
- ✨ **方向4: 隐式偏好学习（论文: DPO/SPIN）** — Worker 新增 `implicit_feedback` RPC 方法
  - 分析用户后续行为信号：纠错/感兴趣/跳话题/正反馈
  - 信号持久化到 `.learnings/implicit_preferences.jsonl`
  - 与现有显式自进化体系并行工作
- ✨ **KV 缓存全线修复 — Pro 命中率 0% 问题根治**
  - XiaoYiClawLLM 查询改写：补 `prefix: True`，`skill_guide` 从 system prompt 移到 user message
  - XiaoYiClawLLM 批评者：补 `prefix: True`，拆分固定 system + variable user
  - SmartProcessor `_call_pro`：自动补 system 前缀，`rewrite_query` 改为 system/user 双消息
  - 全线 `extra_body.prefix: True` + `extra_body.user_id` 完备
- ✨ **WORKSPACE 路径修复** — 模块级定义 `WORKSPACE` 替代 `if WORKSPACE else ""` 的裸引用
- ✨ **`replay_buffer` → `skill_guide` 注入修复** — 验证记忆不再空存 `state.analysis`，实际拼入 LLM prompt
- 🔧 **批评者 `thinking` 不关** — 保持默认开启，`reasoning_content` 不用，`content` 只拿结果，思考 token 远端消化
- 📊 **总功能数更新**：290+ → 295+ 项（4 项论文方向 + KV 修复 + 路径修复 + 隐式偏好）
- ✨ **10 个 Matt Pocock Skills 适配完成**，分工程技能（Layer 12）和效率技能
- ✨ **工程技能 (7 个)**：diagnose / grill-with-docs / tdd / improve-codebase-architecture / prototype / zoom-out / grill-me
- ✨ **效率技能 (3 个)**：caveman / handoff / write-a-skill
- ✨ **教员思想 ↔ Matt Pocock 协同映射**：调查研究→diagnose、矛盾分析→grill-with-docs、实践认知→improve-codebase-architecture、集中兵力→prototype、统筹全局→zoom-out、武装思想→grill-me、批评与自我批评→tdd
- ✨ **MODULE_REGISTRY 注册**：`EXTENDED_MODULES_P3`，新增 `ENGINEERING_SKILLS` / `PRODUCTIVITY_SKILLS` ModuleType
- ✨ **统一协调器兼容修复**：修正 WORKSPACE_ROOT 未定义问题
- 🎯 **SmartProcessor Pro 模型修复**：`llm_config.json` 缺失 `llm_pro` 节导致 Pro 模型空转，已补全 api_key/base_url/model 配置
- 📊 **统计数据更新**：技能数 83 → 93，工程与效率技能数 10，总功能数 260+

### v3.3.0 (2026-05-10 15:30)
- ✨ **Drawing on Memory 双迹编码系统**：基于 arXiv:2604.12948 论文实现场景轨迹编码
  - DAGNode 新增 `scene_trace` 字段（场景轨迹文本）
  - `DAGContextManager.update_node_scene_trace()` — 更新节点场景轨迹
  - `DAGIntegration.encode_scene_trace()` — Flash 开推理生成场景轨迹（30-60字）
  - `DAGIntegration.add_message_with_scene()` — 消息存储自动附加场景轨迹
  - `dag_shim.py` add_message 改为 `add_message_with_scene`
  - SQLite 数据库新增 `scene_trace` 列 + 旧表兼容
  - 场景轨迹说明：消息的意图、上下文语境、用户目的
- ✨ **GRAVITY 结构锚定注入**：基于 arXiv:2605.01688 论文实现检索锚定
  - `DAGIntegration.inject_scene_anchors()` — 检索时注入场景锚定
  - `unified_entry.py` 的 `enhanced_recall` 分支调用 scene anchor 注入
  - 检索结果每个 item 附 scene_trace + anchor 字段
  - 锚定格式：「场景锚定」[场景描述] → 关键信息片段
- ✨ **跨会话记忆恢复系统**：
  - `DAGIntegration.cross_session_memory_restore()` — Flash 开推理汇总近 3 天关键记忆
  - 方法：从 DAG 捞取重要性 > 0.5 的节点 → Flash 生成 2-3 句跨会话背景摘要
  - `dag_shim.py` 新增 `restore` action → `restore_context()`
  - `claw_worker.py` 新增 `restore_context` RPC 方法（走 Flash 开推理）
  - 注册 `restore_context` 到 `_init_methods` RPC 路由
- ✨ **Worker RPC 调用改造**：
  - `handler.js` 跨会话记忆恢复从 `execSync` 调 `dag_shim.py` 改为 Worker RPC
  - 通过 `getWorker(WS)` 获取 Worker 实例 → `worker.call("restore_context", {sessionKey, recentDays: 3})`
  - 避免每次 bootstrap 启动子进程的开销，复用 Worker 常驻连接
- ✨ **系统消息噪声过滤**（解决 skill 推荐/安全警告干扰）：
  - `handler.js` 新增前置过滤逻辑，匹配以下模式直接 return：
    - "用户查询相关skill列表如下"
    - "系统消息，非用户发言"
    - "当前任务已经调用了较多次数的工具"
    - "当前行为存在安全隐患"
  - 过滤在消息注入之前执行，不消耗人格/记忆注入的 headChars
- ✨ **全量改动验证**：
  - 4 个文件语法验证全部通过（dag_context_manager.py / dag_shim.py / unified_entry.py / handler.js）
  - 14 个全量工作流测试全部通过（覆盖 6 个层级）
  - Gateway 重启生效
  - 三阶段方案全量落地：编码 → 锚定注入 → 会话恢复
- 📊 **统计数据更新**：总功能数 238+ → 250+ 项，新增场景编码 12 项、GRAVITY 锚定 6 项、跨会话恢复 4 项、噪声过滤 1 项
- ✨ **决策执行层** — `SelfEvolutionEngine.active_decision_and_execute()` 实现完整三档决策：
  - **低风险** → 自动修改 `memory_params.json`（先备份到 `.learnings/backups/`，修改后验证，失败自动回滚）
  - **中风险** → 生成可执行 `.sh` 脚本到 `.learnings/pending_executions/`，等人审批
  - **高风险** → 跳过仅记录
  - 执行历史持久化到 `EXECUTION_HISTORY.jsonl`，避免重复执行
- ✨ **主动调度器集成** — 10分钟周期循环中自动调用 `active_decision_and_execute()`，改进建议自动流转
- ✨ **参数自动调整** — 支持 `recall_threshold` / `ltp_strength` / `decay_rate` / `max_recall_results` 四类参数
- ✨ **引擎规模增长**：`self_evolution_engine.py` 从 682 行 → 1011 行（含调度器 + 决策执行层）
- ✨ **架构全景图更新**：L16 自进化层新增决策执行三档 + ActiveEvolutionScheduler 描述
- 📊 **统计数据更新**：新增自进化调度器 1 项、决策执行层 3 类
- ✨ **DAG 80% 全局窗口压缩修复**：`_compactInner` 优先取 `dagGlobalRatio`（基于 `contextWindowTokens=256000`）
  - 不再依赖框架传的轮级 `tokenBudget/currentTokenCount`（那是轮预算不是 256K 窗口）
  - 只有 DAG 返回空数据时回退到 `currentTokenCount / tokenBudget`
- ✨ **`should_compact` 新增 `global_context_ratio` 字段**：Python 侧基于 `context_window_tokens` 的全局占比
- ✨ **`claw-bootstrap` hook 注入自进化上下文**：从 `evolution_tracker.jsonl` 读最近 5 条自评均分
  - 人格 + 自进化双字段注入 system prompt（幂等去重）
- ✨ **Gateway 重启生效**：kill 旧进程后自动 respawn，Worker 重新加载代码
- 🔧 **修复 compact 回调调度真空问题**：之前 OpenClaw 框架调 `compact()` 时传的 `currentTokenCount` 是轮级预算，DAG 侧 `should_compact` 返回的 `context_usage_ratio` 也是基于 `max_context_tokens`（非 256K）。现统一为 `global_context_ratio` 作为单一真相源
- ✨ **Worker 常驻单例体系完成**：SmartProcessor / DeepSeek-OCR-2 / XiaoYiClawLLM / SelfEvolutionEngine 全部懒加载常驻，告别冷启动
- ✨ **enhanced_recall**：22ms 进程内检索（替代 3s subprocess 冷启动）
- ✨ **enhanced_health**：进程内探活，毫秒级返回全部组件状态
- ✨ **enhanced_store**：进程内记忆存储，不走 subprocess
- ✨ **OCR2 常驻连接**：DeepSeek-OCR-2 适配器在 Worker 内常驻，统计数据持久化
- ✨ **KV 缓存注入**：Flash 调用添加 `extra_body.user_id` + `prefix: True`，Pro 调用添加 `extra_body`（prefix + user_id）+ `extra_headers`（X-Conversation-Id），全线命中 KV 硬盘缓存
- ✨ **自进化引擎 SelfEvolutionEngine**：质量自评（completeness/relevance/conciseness/factuality 四维评分）、模式发现、进化追踪（evolution_tracker.jsonl 持久化）
- ✨ **smart_process 自动触发自评**：每次检索完成后自动 `evolve()`，结果记录改进建议
- ✨ **架构文档 v3.2.0**：统计数据和更新日志更新，总功能数 235+
- ✨ **论文深度分析**：3 篇关键论文全量研读（Self-Evolving Agents 综述 / Self-Evolving GPT / Missing Knowledge Layer）+ 小艺 AI 报告《自进化AI智能体》23页逐页分析
- ✨ **架构定位确认**：用户为 16 层架构原始设计者，架构文档 v3.1.1 在 IMA 知识库已发布四个版本（v2.0.0 废弃、v3.0.0、v3.1.0、v3.1.1）
- 🔧 **SelfEvolutionEngine 修复**：去掉重复的 `__init__` 和 `_last_pattern_check`，修复 `_should_run_pattern_detection` 中缺失的 `time` 导入
- 🔧 **模块加载认知纠正**：130 注册 + 139 文件全存在，12 个直接初始化，其余懒加载是设计行为，非僵尸代码

### v3.1.1 (2026-05-05)
- ✨ **Beta 端点全线迁移**：DeepSeek Flash 和 Pro 的 base_url 切换至 `https://api.deepseek.com/beta`
- ✨ **prefix: True 前缀续写**：三层调用添加 `"prefix": True` 标记（Pro 查询改写 + 结果总结、Flash 联网搜索整合）
- ✨ **Pro 关闭思考模式**：SmartProcessor 的 Pro 调用显式关闭 `thinking`（简单任务无需推理）
- ✨ **双层 KV Cache 优化**：user_id + X-Conversation-Id + prefix: True 三重保障（chat_prefix_completion）
- ✨ **temperature 回归生效**：非思考模式下 temperature 参数恢复有效（查询改写 0.1 / 结果总结 0.3）
- 🔧 **已就绪 Beta 功能**：chat_prefix_completion / strict 模式 Tool Calls / FIM 补全 / JSON Mode 均可使用

### v3.1.0 (2026-05-05)
- ✨ **ContextEngine 注册**：claw-core 通过 `registerContextEngine("claw-core-engine")` 注册为 OpenClaw 自定义上下文引擎，接管 ingest/assemble/compact/afterTurn 全生命周期
- ✨ **ownsCompaction=true**：禁用 OpenClaw 默认上下文剪裁，DAG 自管压缩
- ✨ **assemble 摘要恢复**：dag_summary RPC 从 SQLite dag_nodes 表恢复上次摘要到 `_sessionSummaries` 缓存
- ✨ **熔断保护**：Worker 连续 3 次失败 → 30s 冷却期 → 降级路径
- ✨ **Worker 扩展**：新增 `dag_summary` RPC handler（Worker + index.js 两端）
- ✨ **Layer 9 DAG 详细说明补充**：ingest/assemble/compact/afterTurn 完整流程
- ✨ **claw-bootstrap hook 修复**：openclaw.json 新增 `hooks.internal.enabled=true` 配置使 Hook 生效
- ✨ **R-CCAM 联网搜索增强（Action 阶段）**：
  - 天气查询 → wttr.in 实时数据 → 直接返回
  - 其他联网查询 → xiaoyi-web-search (node search.js)
  - 搜索结果 → DeepSeek Flash 整合（廉，1/12 价格）
  - 全部失败 → recall + answer 兜底
- ✨ **DAG 上下文管理器修复**：
  - 4 个 conn 泄漏修复（_init_db / get_session_nodes / expand_summary / get_node_count）
  - leaf_chunk_tokens: 8000 → 3000
  - 新增 ensure_auto_compact 方法 + get_dag_integration 单例
- 🔧 **~/.openclaw/package.json 修复**：新增 `"type": "module"`，消除 claw-bootstrap handler 的 MODULE_TYPELESS_PACKAGE_JSON warning

### v3.0.0 (2026-05-05)
- ✨ **架构重设计：17 层 → 16 层**，移除 Layer -2 执行链层
- ✨ **Layer -3 重编号为 Layer -2**（安全基座层下沉为 OpenClaw 基础设施层）
- ✨ **智能处理层 (Layer 4)**：新增 SmartProcessor 双模型通道
- ✨ **LLM 优化层 (Layer 5)**：重新设计投机解码为 Draft + Target 真·投机解码
  - L1 检索型草稿(Trie) + L2 NIM 模型型草稿 → L3 DeepSeek Flash 验证
- ✨ **会话管理层 (Layer 9)**：新增 DAG 上下文中继（替换 contextPruning）
  - 8 层联动：L9 组装 + L1 神经反馈 + L6 硬件 + L11 NLP
- ✨ **工作流引擎 (Layer 14)**：新增 R-CCAM 五阶段认知循环
- ✨ **统一入口层 (Layer 15)**：新增 ACP 持久化通道
- ✨ **移除已废弃内容**：旧投机解码三层架构、旧执行链层
- 📊 更新所有统计数据
- 🔄 文档结构全面重构

### v2.0.5 (2026-05-04)
- ✨ 融合 5 个重要 skill 到架构的各层
- ✨ 技能数更新至 79 个，工作流 45 个，功能 205+ 项

### v2.0.4 (2026-05-03)
- ✨ llm-memory-integration 升级至 v3.2.0（Python 3.12 预编译适配）
- ✨ sandbox_manager.py 重大升级，新增 SudoTerminal

### v2.0.3 (2026-05-03)
- ✨ llm-memory-integration 升级至 v3.1.0
- ✨ 新增 ACP 服务端、工具注册系统、向量 API 等

### v2.0.2 (2026-05-02)
- ✨ 新增 Layer -3 安全基座层
- ✨ 协调器注册模块 79→129
- ✨ 人格加固三层→七层

### v2.0.1 (2026-05-01)
- ✨ 会话状态快照系统

### v2.0.0 (2026-04-27)
- 🎯 明确定位为 OpenClaw 核心底层能力引擎

---

*小艺 Claw — OpenClaw 的核心底层能力引擎*
*文档版本: v4.2 | 最后更新: 2026-05-18 19:20*
