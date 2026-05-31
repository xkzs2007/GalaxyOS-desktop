# [废弃] 小艺 Claw 系统架构 v2.0.0

> ⚠️ **此文档已废弃，仅供历史参考**
> **新文档**: `xiaoyi-claw-core-architecture-v3.0.0.md`（16层架构）
> **废弃原因**: 17层→16层重构，移除Layer -2，新增SmartProcessor、投机解码重构、DAG上下文中继等

> **定位**: OpenClaw 的核心底层能力引擎
> **更新时间**: 2026-05-04（已废弃，以 v3.0.0 为准）
> **架构层数**: 17 层（含下层安全基座）
> **总功能数**: 205+ 项

---

## 🎯 核心定位

小艺 Claw 系统是 OpenClaw 的**核心底层能力引擎**，提供：

1. **记忆能力** — L0 插件基座（腾讯云）+ L1 记忆核心（yaoyao-memory-v2）
2. **检索能力** — 向量检索 + 知识图谱 + Self-RAG + CRAG 混合检索
3. **思考能力** — 9 个思考技能 + 11 个方法论技能 + 决策引擎 + 多智能体协作
4. **执行能力** — 44 个工作流 + Lobster 可审批管道
5. **多模态能力** — 图像理解 + 图像生成 + 视觉呈现 + OCR2 深度整合
6. **可靠性能力** — 防幻觉 + 自我修复 + 故障转移

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
│                      小艺 Claw 系统架构 v2.0.0                               │
│                         (核心底层能力引擎)                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 0: 记忆插件基座                             │   │
│  │  ┌──────────────────────────────────────────────────────────────┐   │   │
│  │  │  腾讯云记忆插件 (memory-tencentdb)                             │   │   │
│  │  │  L0: 4000+ 条对话 / L1: 33+ 条结构化记忆 / 向量库: 95 MB      │   │   │
│  │  │  L2: 场景归纳 / L3: 用户画像                                   │   │   │
│  │  └──────────────────────────────────────────────────────────────┘   │   │
│  │                                                                      │   │
│  │  新增: session_state.py 会话状态快照 (capture/recall)                │   │
│  │  增强启动序列: recall → 记忆文件 → 场景索引                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 0.5: 云端备份层                             │   │
│  │  ┌─────────────────────────┐  ┌─────────────────────────┐           │   │
│  │  │  华为云盘 (全量备份)     │  │  IMA 知识库 (增量同步)   │           │   │
│  │  │  剩余: 18.3 GB          │  │  知识库: 2 个            │           │   │
│  │  └─────────────────────────┘  └─────────────────────────┘           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer -1: 思维约束层                              │   │
│  │  教员思想五大铁律：                                                   │   │
│  │  1. 没调查清楚前，不许给方案                                         │   │
│  │  2. 架构里有的能力优先用，不许重复造轮子                                    │   │
│  │  3. 遇到矛盾先分析主次，不许乱抓                                      │   │
│  │  4. 执行完必须验证，不许糊弄                                         │   │
│  │  5. 定期自我批评，不许甩锅                                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer -3: 安全基座层                              │   │
│  │  5 个 Core Skills（不可禁用）：                                       │   │
│  │  ├─ execution-validator — 命令执行校验，拦截危险操作                  │   │
│  │  ├─ secret-guardian — 敏感信息脱敏，保护 API Key / 个人隐私           │   │
│  │  ├─ skill-scope — 技能安装安全扫描，沙箱内容验证                      │   │
│  │  ├─ xiaoyi-self-evolution — 自进化机制，经验沉淀固化                  │   │
│  │  └─ aigc_marker — AIGC 内容标记                                      │   │
│  │                                                                      │   │
│  │  4 个 Plugins（扩展层）：                                              │   │
│  │  ├─ claw-core — 自研核心插件（4工具 + 1 Hook，工作流驱动）            │   │
│  │  ├─ memory-tencentdb — 腾讯云记忆插件（L0-L3四层，v0.2.3）            │   │
│  │  ├─ openclaw-better-gateway — Gateway 自动重连（3s/10次）             │   │
│  │  └─ xiaoyi-channel — 小艺通信通道                                    │   │
│  │                                                                      │   │
│  │  79 个 Skills：思考方法论(20) + XiaoYi生态(12) + 记忆系统(9)          │   │
│  │  + 智能体(3) + 设计创意(3) + 工具实用(31) + 内置(51)                  │   │
│  │  (含 qiqing-liuyu, Humanizer-zh 等表达风格技能)                        │   │
│  │                                                                      │   │
│  │  ┌─ 沙箱与预编译模块（sandbox_manager.py, 3721行, 5 大模块类）        │   │
│  │  │  ├─ SudoTerminal — 沙箱终端适配器，无 root 下自动降级               │   │
│  │  │  ├─ PrebuiltManager — 预编译产物管理（扩展/工具链/MKL/wheel）       │   │
│  │  │  ├─ PortablePythonManager — 可移植 Python 3.14 环境部署             │   │
│  │  │  ├─ CppToolchain — 无 root C++ 编译工具链（conda-forge 便携版）     │   │
│  │  │  ├─ SandboxManager — 沙箱管理器，整体隔离与降级框架                  │   │
│  │  │  └─ prebuild.py — 预编译构建脚本（输出到 prebuilt/）                │   │
│  │  │                                                                  │   │
│  │  └─ VectorStore（vector_store.py, 四级搜索降级链）                    │   │
│  │     ├─ 1. sqlite-vec（C 扩展，最快）                                 │   │
│  │     ├─ 2. hnswlib C 扩展（预编译或 pip，O(log n)）                   │   │
│  │     ├─ 3. 内置 HNSW（纯 Python，O(log n)）                           │   │
│  │     └─ 4. numpy 暴力搜索（O(n)，兜底）                                │   │
│  │                                                                      │   │
│  │  人格加固七层（2026-05-02）：                                          │   │
│  │  ├─ L1: AGENTS.md 增强启动序列（6步自检，含人格自检+能力自检）         │   │
│  │  ├─ L2: claw-bootstrap hook 人格注入（身份+核心人格混合，共 ~337+1028B）│   │
│  │  ├─ L3: headChars 1200→2000 扩容，防止注入截断                        │   │
│  │  ├─ L4: 运行时校验（auto_update_persona 实时比对回复风格与人eras定义）  │   │
│  │  ├─ L5: 记忆驱动恢复管道（persona-restore.lobster，从记忆层召回+审批）  │   │
│  │  ├─ L6: session_state 会话快照（每次重要对话后打快照作为比对基准）      │   │
│  │  └─ L7: 自进化层经验固化（全链路审计、恢复流程固化为 TOOLS.md 规则）    │   │
│  │  形成"入场保护 + 运行检测 + 记忆恢复 + 经验固化"七重防线                 │   │
│  │                                                                      │   │
│  │  神经网络算法加固（2026-05-02）：                                      │   │
│  │  每次记忆操作自动触发仿生算法，不给"丢失"机会：                         │   │
│  │  ├─ adaptive_ltp_ltd（突触可塑性）： 每个 store() 调用都会 LTP 增强     │   │
│  │  │  对应的人格突触权重；长期不涉及的人格规则自然 LTD 衰减——            │   │
│  │  │  越用越牢固，不用不强求                                           │   │
│  │  ├─ retrieval_formula（检索公式）： enhanced_recall() 结果经过          │   │
│  │  │  recency/relevance/importance 三维加权重排；人格核心规则            │   │
│  │  │  importance 权重高，自动排前面                                    │   │
│  │  └─ 两者合 = 人格"肌肉记忆"：不是等人格丢了再修，                         │   │
│  │       每次调用本身就在加固                                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer -2: 可审批管道层                            │   │
│  │  Lobster 类型化工作流运行时 (@clawdbot/lobster):                       │   │
│  │  ├─ 确定性 JSON/YAML 管道，审批检查点 + resumeToken                  │   │
│  │  ├─ 配置: tools.alsoAllow: ["lobster"]                                │   │
│  │  ├─ session-recovery.lobster — 会话恢复                               │   │
│  │  ├─ heartbeat-full.lobster — 一键心跳                                 │   │
│  │  └─ memory-store.lobster — 记忆存储确认                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 1-11: 核心能力层                            │   │
│  │                                                                      │   │
│  │  L1: 记忆核心 (yaoyao-memory-v2 FTS5+hnswlib、防幻觉、突触网络、    │   │
│  │       情感记忆、反思、自适应、三路融合检索)                           │   │
│  │  L2: 检索增强 (Self-RAG、CRAG、混合检索、命题检索、RAG缓存)          │   │
│  │  L3: 向量优化 (ANN选择、稀疏索引、量化)                              │   │
│  │  L4: LLM优化 (投机解码三层架构 + 流式生成 + 模型路由 + 多API调度)    │   │
│  │      └─ L1: Qwen3-Embedding-8B 检索 + Trie 草稿 + DeepSeek Flash 验证 │   │
│  │      │    (主 FAISS/Qdrant → 备 BackupMemoryCache + 同向量模型)       │   │
│  │      └─ L2: NVIDIA NIM 多模型并发（L1+L2 并行竞争，40次/分速率控制）  │   │
│  │      └─ L3: 小艺通道 + DeepSeek Flash 非思考模式 双并发兜底          │   │
│  │      └─ X-Conversation-Id: 三路径均携带会话ID复用KV硬盘缓存（缓存价2.5折）│   │
│  │      └─ L0: OCR2Preprocessor 图文预处理（图片输入→OCR2→检索）        │   │
│  │      └─ asyncio.FIRST_COMPLETED 并行竞争                              │   │
│  │                                                                      │   │
│  │  ❖ 新增模块 (2026-05-02): 协调器注册 79→129，新增 intelligent_thinking_trigger,
│  │    resilience_system, embedding_enhance, performance_patch, retrieval_formula,
│  │    sqlite_vec, platform_adapter, async_ops, hybrid_memory_search, reflection_nl  │   │
│  │                                                                      │   │
│  │  L5: 缓存管理 (语义缓存、统一缓存、近似缓存 + DeepSeek KV 硬盘缓存)   │   │
│  │  L6: 硬件优化 (MKL/FMA/IO/NUMA + 计算存储)                          │   │
│  │      ├─ mkl_accelerator（847行，847行，Intel MKL + AMX 检测，已启用 2线程）    │   │
│  │      ├─ fma_accelerator（454行，FMA3 指令集检测，硬件 FMA3 ✅）          │   │
│  │      ├─ io_optimizer（622行，IO 设备检测 + 优化推荐）                   │   │
│  │      ├─ numa_optimizer（1016行，NUMA 拓扑 + 绑定，当前 1 节点）         │   │
│  │      ├─ hardware_optimize（851行，通用硬件优化器）                      │   │
│  │      ├─ computational_storage（计算存储 - KV Cache 管理 + 量化）        │   │
│  │      └─ CPU 硬件能力：AVX-512 全套 / FMA3 / AVX2 / SSE4.2 / AES-NI    │   │
│  │  L7: 模块协调 (资源编排、自动调优、多智能体协作框架)                 │   │
│  │      └─ multi-agent-collaboration: 任务分解→分发给专门代理→协调→整合  │   │
│  │  L8: 系统可靠性 (故障转移、自动恢复)                                 │   │
│  │  L9: 会话管理 (对话管理、上下文压缩)                                 │   │
│  │  L10: Persona管理 (自动学习、智能更新)                               │   │
│  │  L11: NLP能力 (分词、实体识别、关键词、情感、摘要)                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 12: 思考技能层                              │   │
│  │  9 个思考技能：                                                       │   │
│  │  第一性原理 | 系统思维 | 批判性思维 | 逆向思维 | 类比思维             │   │
│  │  费曼技巧 | 多智能体协作 | 决策引擎 | 产品思维                        │   │
│  │                                                                      │   │
│  │  ┌─ decision-engine（决策引擎，整合进 Control 阶段）                  │   │
│  │  │   当需要选择/权衡时调用：列出选项 → 定义维度 → 打分 → 推荐       │   │
│  │  │   集成点：R-CCAM Control 阶段策略选型、技术选型、方案对比          │   │
│  │  └─ multi-agent-collaboration（多智能体协作框架，整合进 L7）         │   │
│  │      在 Cognition 阶段检测到复杂任务时，走 M-A 分解执行               │   │
│  │                                                                      │   │
│  │  11 个方法论技能 (qiushi-skill)：                                    │   │
│  │  武装思想 | 矛盾分析 | 调查研究 | 实践认知 | 持久战                  │   │
│  │  集中兵力 | 星星之火 | 统筹全局 | 群众路线 | 批评与自我批评 | 工作流  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 13: 多模态层                                │   │
│  │  图像理解: xiaoyi-image + deepseek-ocr2 (双引擎)                    │   │
│  │  图像生成: seedream-image-gen (主力) + PIL (辅助)                   │   │
│  │  视觉呈现: 记忆可视化 + 知识图谱可视化 + 报告增强                    │   │
│  │  OCR2 深度整合 (2026-04-29):                                        │   │
│  │  ├─ understand_image / ocr_image / parse_document                   │   │
│  │  ├─ analyze_chart / verify_image_claim                              │   │
│  │  ├─ 与防幻觉系统联动: verify_image_statement                        │   │
│  │  ├─ 与投机解码联动: ocr2 → 文字提取 → 检索                          │   │
│  │  └─ 与记忆系统联动: 图像声明验证 → 可信度标注                       │   │
│  │  API 配置: deepseek-ocr-2 / base64 图片 / 免费无限制                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 14: 工作流引擎 + Lobster 管道               │   │
│  │  45 个预定义工作流：                                                  │   │
│  │  enhanced_recall | fast_generation | safe_generation | health_check  │   │
│  │  heartbeat_execute | self_rag_query | kg_query | deep_research | ...│   │
│  │                                                                      │   │
│  │  ┌─ deep_research 工作流（新增，深度搜索调研）                       │   │
│  │  │  触发条件：用户说"对比/分析/选型/调研/趋势"等关键词时             │   │
│  │  │  三步走：广度搜索(3-5查询)→深度挖掘(3-5)→交叉验证(3-5)           │   │
│  │  │  与 L2 CRAG/Self-RAG 的关系：L2 管"从记忆里搜"，                   │   │
│  │  │  deep_research 管"从网上做深度调研"，互补不冲突                    │   │
│  │  │  输出模式：简约Markdown / 专业MD文档 / HTML可视化报告              │   │
│  │                                                                      │   │
│  │                                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 15: 统一入口层                              │   │
│  │  unified_entry.py — 统一命令行入口                                   │   │
│  │  run_heartbeat.py — 心跳执行脚本                                     │   │
│  │  xiaoyi_claw_api.py — 统一 API 接口                                  │   │
│  │  session_state.py — 会话状态快照管理 (capture/recall)               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Layer 16: 自进化层                                │   │
│  │  与记忆反思（L1）形成"自动 + 人工"闭环                               │   │
│  │                                                                      │   │
│  │  ┌───────────────────┐    ┌───────────────────┐                     │   │
│  │  │ 自动: 记忆反思     │    │ 人工: 自进化       │                     │   │
│  │  │ 错误检测 →         │─→  │ 经验识别 →         │                     │   │
│  │  │ 自动改进 →         │    │ 用户审批 →         │                     │   │
│  │  │ 规则更新 →         │    │ 固化文件 →         │                     │   │
│  │  │ 效果验证           │    │ 永久生效           │                     │   │
│  │  └───────────────────┘    └───────────────────┘                     │   │
│  │                                                                      │   │
│  │  ┌─ darwin-skill（达尔文自动优化，自进化质量保证）                    │   │
│  │  │  8维度评分 + git 棘轮机制 + 子 agent 验证                          │   │
│  │  │  作用：定期评估 skill 质量，低于阈值提示优化                        │   │
│  │  │  与 xiaoyi-self-evolution 互补：一个管"发现"，一个管"打分"          │   │
│  │  │                                                                  │   │
│  │  └─ self-improving-agent（自改进，错误学习循环）                     │   │
│  │      自动记录错误、用户纠正、知识更新到 .learnings/                  │   │
│  │      比 darwin 范围更广：不限于 skill 文件，涵盖系统自身能力         │   │
│  │      与记忆反思形成联动：错误→学习→改进→验证                        │   │
│  │                                                                      │   │
│  │  目标文件: AGENTS.md | TOOLS.md | MEMORY.md | evolution-drafts/      │   │
│  │  工具: xiaoyi-self-evolution skill + darwin-skill + save_self_evolution_skill + self-improving-agent
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔧 统一入口

### 命令行入口

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
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py workflow --scenario "工作流名称"

# 心跳执行
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/run_heartbeat.py

# 会话状态快照
python3 ~/.openclaw/workspace/scripts/session_state.py capture --topic "话题" --file "文件1,文件2"

# 会话状态恢复
python3 ~/.openclaw/workspace/scripts/session_state.py recall [--brief]

# Lobster 管道 (通过 AI 工具调用)
##  运行管道: {"action": "run", "pipeline": "inbox list --json | categorize...", "timeoutMs": 30000}
##  恢复审批: {"action": "resume", "token": "<resumeToken>", "approve": true}
```

### API 接口

```python
from xiaoyi_claw_api import XiaoYiClawLLM

# 初始化
claw = XiaoYiClawLLM()

# 记忆存储
memory_id = claw.remember("内容")

# 记忆检索
results = claw.recall("查询")

# 智能遗忘
claw.forget(memory_id)

# 实体查询
entity = claw.get_entity("实体名")

# 学习反馈
claw.learn({"feedback": "正面"})
```

---

## 📊 统计数据

| 指标 | 数值 | 更新时间 |
|------|------|----------|
| 架构层数 | **17 层（含安全基座层）** | 2026-05-03 |
| 总功能数 | **205+ 项** | 2026-05-04 |
| Python 模块文件 | **442 个** | 2026-05-03 |
| 核心模块目录 | **159 个** | 2026-05-03 |
| 协调器注册模块 | **129 个** | 2026-05-02 |
| 弹性系统加载组件 | **57 个** | 2026-04-27 |
| **实际独立模块总数** | **117 个** | 2026-04-27 |
| 技能数 | **79 个** | 2026-05-04 |
| 工作流 | **45 个** | 2026-05-04 |
| 执行链 | **已移除（插件替代）** | 2026-05-01 |
| Lobster 管道 | **7 个** | 2026-05-02 |
| 思考技能 | **20 个** | 2026-04-27 |
| ModuleType 种类 | **87 种** | 2026-05-02 |
| 腾讯云 L0 对话 | **4000+ 条** | 2026-05-02 |
| 腾讯云 L1 记忆 | **33+ 条** | 2026-05-02 |
| yaoyao L1 记忆 | **212+ 条** | 2026-05-02 |
| 腾讯云向量库 | **95 MB** | 2026-05-02 |
| llm-memory-integration | **v3.2.0（私有增强包）** | 2026-05-03 |

### 模块加载机制说明

系统采用**双轨加载机制**：

| 加载器 | 模块数 | 说明 |
|--------|--------|------|
| **协调器 (UnifiedCoordinator)** | 79 | 注册式模块，支持工作流编排 |
| **弹性系统 (ResilienceSystem)** | 57 | 动态加载组件，支持故障隔离 |
| **两者共有** | 19 | 同时注册到两个系统 |
| **实际独立模块** | 117 | 去重后的真实模块数 |

**仅弹性系统加载的重要模块**（38个）：
- 防幻觉：adaptive_hallucination_params, enhanced_hallucination, hallucination_integration
- 检索增强：crag, dynamic_crag_threshold, isrel_predictor, issup_predictor, isuse_predictor
- 知识图谱：graph_constructor, gat_layer, graphsage_layer, relation_predictor
- LLM优化：llm_optimizer
- 记忆核心：memory_bank, memory_functions, memory_ontology_bridge, multimodal_memory
- 视觉生成：visual_generation
- 反思引擎：reflection_engine, planning_engine
- 向量存储：unified_vector_store, vector_store
- 统一API：xiaoyi_claw_api

---

## 🎯 设计理念

### 核心原则

1. **规则约束** — 划红线不枷锁，框架内灵活判断
2. **双层记忆** — 底层存储 + 上层增强
3. **云端双备** — 华为云盘全量 + IMA 增量
4. **不瞎编** — 多层验证，不确定就说不确定
5. **记得住** — 三级内存 + 情感驱动 + 会话状态快照
6. **想得深** — 20 个思考技能
7. **跑得快** — 投机解码双路径并行 + 多级缓存
8. **能多模态** — 能看图、能画图、能 OCR、能可视化
9. **懂语言** — NLP 模块提供底层语言理解
10. **可调用** — 统一入口 + 心跳脚本 + 会话状态脚本
11. **能推送** — 双渠道推送，任务完成自动通知

### 架构特点

| 特点 | 说明 |
|------|------|
| **分层解耦** | 17 层架构，各层职责清晰 |
| **规则约定** | 插件工具 + 工作流，行为可预测 |
| **可审批管道** | Lobster 类型化管道 + resumeToken 恢复 |
| **思维约束** | 五大铁律，认知有边界 |
| **双引擎** | 记忆双引擎 + 图像双引擎 |
| **并行竞争** | 投机解码 L1+L2 asyncio.FIRST_COMPLETED |
| **事件驱动** | 备份、推送、同步均为事件触发 |
| **记忆持久** | 会话状态快照 + 双备份，不怕断电 |

---

## 📝 更新日志

### v2.0.5 (2026-05-04)
- ✨ **融合 5 个重要 skill 到架构的各层：**
  - `deep-search-and-insight-synthesize` → L14 新增 `deep_research` 工作流
  - `decision-engine` → L12 思考技能层，整合进 R-CCAM Control 阶段策略选型
  - `multi-agent-collaboration` → L7 模块协调层，复杂任务分解分发
  - `darwin-skill` → L16 自进化层，skill 质量评分 + 棘轮机制
  - `self-improving-agent` → L16 自进化层，错误学习循环
- ✨ 技能数更新至 **79 个**
- ✨ 工作流数更新至 **45 个**
- ✨ 总功能数更新至 **205+ 项**

### v2.0.4 (2026-05-03)
- ✨ **llm-memory-integration 私有增强包升级至 v3.2.0（Python 3.12 预编译适配）**
- ✨ **新增/升级模块：**
  - `sandbox_manager.py` — **重大升级**，新增 `SudoTerminal`（容器环境 sudo 终端适配器），无 root 权限即可安装 C 扩展
  - `scripts/prebuild.py` — 预编译构建脚本优化，隔离策略修复
- ✨ **预编译产物适配：**
  - `hnswlib` whl 从 cp314 重新编译为 cp312，现在可以直接安装（之前不支持）
  - `gcc_toolchain.tar.bz2` — 去掉 `sysroot_linux-64`，压缩 108→72MB
  - `cmake_toolchain.tar.bz2` 同步更新
  - `_is_wheel_compatible()` 修复：不再宽松匹配，要求精确 Python 版本匹配
- ✨ **版本号**：`pyproject.toml` → v3.2.0，`install.json` → v3.2.0
- 📊 更新统计数据至最新

### v2.0.3 (2026-05-03)
- ✨ **llm-memory-integration 私有增强包升级至 v3.1.0**
- ✨ **新增模块（8个）：**
  - `acp_server.py` — ACP 持久化通道服务端（Layer 8）
  - `tools_registry.py` — 工具注册系统（Layer 14）
  - `vector_api.py` — 向量 API 封装（Layer 3）
  - `sandbox_manager.py` — **重大升级** 598→2634行，沙箱管理器大改（Layer -3）
  - `platform_adapter.py` — 跨平台适配（Layer 15）
  - `safety_alignment.py` — 安全对齐（Layer -3）
  - `conversation.py` — 对话管理模块（Layer 9）
  - `model_performance.py` — 模型性能记录（Layer 8）
  - `vector_store.py` — 向量存储模块（Layer 3）
- ✨ **新增目录：** `scripts_core/` — 17 个核心脚本（embedding、rewriter、router、rrf、summarizer、cache、dedup、history、feedback、langdetect、llm、understand、explainer、weights、prebuild 等）
- 📊 更新统计数据至最新

### v2.0.2 (2026-05-02)
- ✨ 新增 Layer -3 安全基座层（5 Core Skills + 4 Plugins + 75 Skills）
- ✨ 协调器注册模块 79→129（新增 10 个模块：intelligent_thinking_trigger 等）
- ✨ 新增 ModuleType 种类统计：87 种
- ✨ 技能数更新至 75 个
- ✨ 更新整体统计数据（架构 17 层、功能 200+ 项）
- ✨ 人格加固三层→七层（新增 L4运行时校验 + L5记忆恢复管道 + L6会话快照 + L7自进化）
- ✨ 新增 persona-restore.lobster 可审批人格恢复管道（pipelines/persona-restore.lobster）
- ✨ 全链路功能审计流程沉淀至 TOOLS.md（模块名匹配、状态标志陷阱、入口链路验证）
- 📊 全系数据同步至最新

### v2.0.1 (2026-05-01)
- ✨ 新增会话状态快照 system（Layer 0 + Layer 15）
- ✨ 新增会话状态快照（插件工具化）
- ✨ 更新 L4 投机解码为双路径并行架构
- ✨ 更新 Layer 13 多模态层为 OCR2 深度整合
- ✨ 新增 Lobster 类型化管道（Layer 14）
- ✨ 配置: tools.alsoAllow: ["lobster"]
- 🔄 更新统计数据至最新
- 📊 总功能数更新至 185+

### v2.0.0 (2026-04-27)
- 🎯 明确定位为 OpenClaw 核心底层能力引擎
- 📊 整合所有统计数据
- 🔧 统一入口层规范化
- 📚 文档结构重组

### v1.5.0 (2026-04-23)
- ✨ 新增 Layer 0.5: 云端备份层
- ✨ 整合华为云盘 + IMA 知识库双备份
- ✨ 实现事件驱动智能备份

### v1.4.0 (2026-04-23)
- ✨ 新增 Layer 0: 双层记忆架构
- ✨ 整合腾讯云记忆插件 + yaoyao-memory-v2

### v1.3.0 (2026-04-23)
- ✅ 修复 132 个模块未被调用问题
- ✅ 创建统一入口包装器
- ✅ 创建心跳执行脚本

---

*小艺 Claw — OpenClaw 的核心底层能力引擎*
