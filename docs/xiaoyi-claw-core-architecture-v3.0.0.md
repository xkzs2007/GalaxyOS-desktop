# [废弃] 小艺 Claw 系统架构 v3.1.0

> ⚠️ **此文档已废弃，仅供历史参考**
> **新文档**: `xiaoyi-claw-core-architecture-v3.1.0.md`（16层架构，上下文引擎全面注册）
> **废弃原因**: 已升级至 v3.1.0，新增 ContextEngine 注册（claw-core-engine）、DAG ingest/assemble/compact 全生命周期描述

> **定位**: OpenClaw 的核心底层能力引擎
> **更新时间**: 2026-05-05（已废弃，以 v3.1.0 为准）
> **架构层数**: 16 层
> **总功能数**: 225+ 项

---

## 🎯 核心定位

小艺 Claw 系统是 OpenClaw 的**核心底层能力引擎**，提供：

1. **记忆能力** — 双层记忆架构（腾讯云插件 + 本地记忆系统）
2. **检索能力** — 向量检索 + 知识图谱 + Self-RAG + CRAG 混合检索 + CRAG 动态纠错
3. **智能处理能力** — 查询改写、结果总结、语义过滤（SmartProcessor 双模型通道）
4. **思考能力** — 9 个思考技能 + 11 个方法论技能 + 决策引擎 + 多智能体协作
5. **执行能力** — 44 个工作流 + R-CCAM 结构化认知循环 + DAG 上下文中继
6. **多模态能力** — 图像理解 + 图像生成 + 视觉呈现 + OCR2 深度整合
7. **可靠性能力** — 防幻觉 + 自我修复 + 故障转移 + ACP 持久化通道

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
│                      小艺 Claw 系统架构 v3.0.0                               │
│                         (核心底层能力引擎)                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 0: 记忆架构                                 │   │
│  │  ┌─────────────────────────┐  ┌─────────────────────────┐           │   │
│  │  │  腾讯云记忆插件 (持久层) │  │  Session State (快照层) │           │   │
│  │  │  L0: 对话存储           │  │  capture/recall         │           │   │
│  │  │  L1: 结构化记忆         │  │  会话快照 → 恢复        │           │   │
│  │  │  向量库 + 三元组        │  │  长上下文保活           │           │   │
│  │  └─────────────────────────┘  └─────────────────────────┘           │   │
│  │                                                                      │   │
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
│  │  │  ┌─ adaptive_ltp_ltd: 频率增强 / 低频衰减                    │   │   │
│  │  │  └─ retrieval_formula: recency/relevance/importance 三维加权 │   │   │
│  │  │                                                              │   │   │
│  │  │  自适应遗忘 (adaptive_forgetter) + 情感记忆 (emotion_memory)   │   │   │
│  │  └──────────────────────────────────────────────────────────────┘   │   │
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
│  │  ├─ Dense: FAISS/Qdrant 向量检索 (Qwen3-Embedding-8B, 4096维)      │   │
│  │  ├─ Sparse: TF-IDF / BM25 关键词检索                                 │   │
│  │  └─ RRF 融合: 自适应权重 + Top-K 截断                                │   │
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
│  │  └─ embedding_enhance: Qwen3-Embedding-8B (4096维, 70K+ ops/s)    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 4: 智能处理层 (SmartProcessor)              │   │
│  │  smart_processor.py — 双模型通道                                    │   │
│  │                                                                      │   │
│  │  ├─ 快通道 (DeepSeek Flash / LLM_DeepSeekV4_Thinking):              │   │
│  │  │  简单查询、过滤、快速响应                                        │   │
│  │  │                                                                  │   │
│  │  ├─ 重通道 (DeepSeek V4 Pro / 小艺通道):                             │   │
│  │  │  查询改写、结果总结、语义过滤、复杂推理                          │   │
│  │  │                                                                  │   │
│  │  └─ 集成点:                                                          │   │
│  │     ├─ enhanced_recall 工作流: 改写 → 检索 → 总结 → 过滤            │   │
│  │     ├─ R-CCAM Cognition 阶段: 深度分析 + 决策建议                   │   │
│  │     └─ DAG 上下文中继: 缓存 smart_processor 中间结果                │   │
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
│  │  ├─ KV 硬盘缓存: DeepSeek Flash X-Conversation-Id 复用              │   │
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
│  │  │  · 跨 8 层联动：L9 组装 + L1 神经反馈 + L6 硬件 + L11 NLP       │   │
│  │  │  替换 OpenClaw 默认 contextPruning 机制                          │   │
│  │  ├─ 对话管理 (conversation.py): 上下文窗口 + 会话 ID 绑定           │   │
│  │  └─ 上下文压缩 (context_compressor): 长对话历史摘要 + 关键信息提取  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 10: Persona 管理层                          │   │
│  │  ├─ 人格加固七重防线:                                               │   │
│  │  │  L1: AGENTS.md 增强启动序列（6步自检）                           │   │
│  │  │  L2: claw-bootstrap hook 人格注入 (身份+核心人格混合)            │   │
│  │  │  L3: headChars 扩容 (1200→2000)                                  │   │
│  │  │  L4: 运行时校验 (auto_update_persona)                            │   │
│  │  │  L5: 记忆驱动恢复管道 (persona-restore.lobster)                  │   │
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
│  │  ├─ 中文分词 + 词性标注 (jieba)                                    │   │
│  │  ├─ 命名实体识别: PER/LOC/ORG/TIME/NUM                             │   │
│  │  ├─ 关键词提取: TF / TF-IDF                                        │   │
│  │  ├─ 情感分析: SnowNLP + 词典方法                                   │   │
│  │  └─ 文本摘要: 句子重要性评分                                       │   │
│  │                                                                      │   │
│  │  集成: 记忆模块(NLP→关键词/实体→存储), 检索(NLP→分词→搜索)        │   │
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
│  │  集成: R-CCAM Cognition 阶段触发，intelligent_thinking_trigger 路由   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 13: 多模态层                               │   │
│  │  ├─ 图像理解: xiaoyi-image + deepseek-ocr2 (双引擎)                │   │
│  │  ├─ 图像生成: seedream-image-gen (主力) + PIL (辅助)               │   │
│  │  ├─ 视觉呈现: 记忆可视化 + 知识图谱可视化 + 报告增强                │   │
│  │  ├─ OCR2 深度整合:                                                 │   │
│  │  │  understand_image / ocr_image / parse_document                   │   │
│  │  │  analyze_chart / verify_image_claim                              │   │
│  │  │  与防幻觉联动: verify_image_statement                            │   │
│  │  │  与投机解码联动: ocr2 → 文字提取 → 检索                          │   │
│  │  │  与记忆系统联动: 图像声明验证 → 可信度标注                       │   │
│  │  └─ API: deepseek-ocr-2 / base64 图片 / 免费无限制                  │   │
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
│  │  ├─ Action（行动阶段）:                                             │   │
│  │  │  - info_insufficient（联网搜索）:                                │   │
│  │  │    ① 天气查询 → wttr.in 实时数据 → 直接返回                     │   │
│  │  │    ② 其他联网查询 → xiaoyi-web-search (node search.js)          │   │
│  │  │    ③ 搜索结果 → DeepSeek Flash 整合回答                         │   │
│  │  │    ④ 全部失败 → recall + answer 兜底                            │   │
│  │  │  - answer（默认）: Pro 查询改写 → 双路检索 → Pro 总结 → 组装   │   │
│  │  │  - boundary_violation → 友善拒绝                                │   │
│  │  │  - clarify_needed → 让用户说清楚                                │   │
│  │  │                                                                  │   │
│  │  └─ Memory（记忆阶段）:                                            │   │
│  │     - 持久化: 存储到向量库 + DAG 节点 + 情感标记                    │   │
│  │     - 突触反馈: LTP 增强 + 摘要节点更新                            │   │
│  │     - 自进化检测                                                    │   │
│  │                                                                      │   │
│  │  DAG 上下文中继: Init → Process → Persist → Resume                  │   │
│  │  └─ 8 层联动：L9 组装 + L1 双向神经反馈 + L6 硬件 + L11 NLP 索引   │   │
│  │                                                                      │   │
│  │  smart_processor 工作流: 改写 → 检索 → 总结 → 过滤                  │   │
│  │                                                                      │   │
│  │  Lobster 管道:                                                       │   │
│  │  ├─ session-recovery.lobster — 会话恢复                              │   │
│  │  ├─ heartbeat-full.lobster — 一键心跳                                │   │
│  │  ├─ memory-store.lobster — 记忆存储确认                              │   │
│  │  └─ persona-restore.lobster — 人格恢复                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 15: 统一入口层                             │   │
│  │  ├─ unified_entry.py — CLI 统一入口                                  │   │
│  │  │  支持: health / status / store / recall / workflow               │   │
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
│  │  ├─ 自动: 记忆反思 → 错误检测 → 自动改进 → 规则更新 → 效果验证      │   │
│  │  ├─ 人工: 经验识别 → 用户审批 → 固化文件 → 永久生效                  │   │
│  │  │                                                                  │   │
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

### Lobster 管道

```json
{"action": "run", "pipeline": "pipeline-name", "timeoutMs": 30000}
{"action": "resume", "token": "<resumeToken>", "approve": true}
```

---

## 📊 统计数据

| 指标 | 数值 | 更新时间 |
|------|------|----------|
| 架构层数 | **16 层** | 2026-05-05 |
| 总功能数 | **220+ 项** | 2026-05-05 |
| Python 模块文件 | **442+ 个** | 2026-05-03 |
| 核心模块目录 | **159 个** | 2026-05-03 |
| 弹性系统加载组件 | **57 个** | 2026-04-27 |
| 技能数 | **83 个** | 2026-05-05 |
| 工作流 | **44 个** | 2026-05-05 |
| 思考技能 | **20 个** | 2026-04-27 |
| 插件数 | **4 个** | 2026-05-05 |
| Core Skills | **5 个** | 2026-05-05 |
| 腾讯云 L0 对话 | **4000+ 条** | 2026-05-02 |
| 腾讯云 L1 记忆 | **33+ 条** | 2026-05-02 |
| 腾讯云向量库 | **95 MB** | 2026-05-02 |
| Lobster 管道 | **4 个** | 2026-05-05 |
| R-CCAM 循环 | **1 个** | 2026-05-03 |
| DAG 上下文中继 | **1 个** | 2026-05-05 |
| ContextEngine 注册 | **1 个** (claw-core-engine) | 2026-05-05 |
| ACP 持久化通道 | **1 个** | 2026-05-03 |
| SmartProcessor | **1 个** | 2026-05-03 |
| llm-memory-integration | **v3.2.0** | 2026-05-03 |

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
- ✨ Lobster 类型化管道

### v2.0.0 (2026-04-27)
- 🎯 明确定位为 OpenClaw 核心底层能力引擎

---

*小艺 Claw — OpenClaw 的核心底层能力引擎*
*文档版本: v3.0.0 | 最后更新: 2026-05-05*
