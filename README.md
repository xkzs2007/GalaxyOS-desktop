# 🌌 GalaxyOS — 认知增强引擎

> OpenClaw 的开源认知增强引擎，为 AI Assistant 提供记忆、检索、推理、验证、自进化等全套认知能力
> 版本: v6.2 · BlobArena无损存储 + ONNX bge-small-zh + 7通道检索Hub + ContextEngine neural_rerank + Galaxy Kernel assemble 注入 + 上下文内容类型排序

## 总览

`GalaxyOS`（前身：小艺 Claw 系统）是 **OpenClaw 的底层认知增强引擎**。提供 **15 层架构、470+ 功能项、160+ 服务模块**，覆盖从记忆管理到认知推理的全链路。

### 核心能力

| 能力 | 说明 |
|------|------|
| **记忆** | 三层记忆体系 + 记忆巩固引擎（CLS固化 + 仿生睡眠5阶段 + 干扰合并 + KG图推理）+ **神经突触记忆网络（ncps LTC+CfC+遗忘曲线+NLP增强）** + **BlobArena v2 mmap 无损存储** |
| **检索** | 向量检索 + 知识图谱 + Self-RAG + CRAG 混合检索 + bge-reranker-v2-m3 重排序 + GraphRAG + RAPTOR + **RetrievalHub 7通道 (KG/Local/DAG/MN-RU/Synapse/Paper/Cognitive/Web)** + **ANNSelector v2 动态索引** |
| **智能处理** | 查询改写（Pro）/ 结果总结（Flash）/ 语义过滤 / 图像理解（SmartProcessor 三模型通道：Flash/Pro/VLM）+ Visual RAG 自动 OCR2 触发 |
| **认知循环（R-CCAM）** | 五阶段结构化认知循环：Retrieval→Cognition→Control→Action→Memory，元认知 5 种动态策略调节器 + 异步注入三层兜底 |
| **Galaxy Kernel** | 独立后台元认知线程（6s轮询），持 Reflexion 记录 + 自进化产出，不阻塞主推理。process() 精简 **70%**（863→254行） |
| **思考方法论** | 20 个思考方法论技能 + **IntelligentThinkingTrigger v2.0**（RCR-Router 动态评分 + Springdrift CBR 记忆 + A-ToM 认知阶段推断） |
| **防幻觉** | 10 重交叉验证 + 多源证据 + 矛盾检测 + CRAG 动态纠错 + Rails 护栏增强版 + **验证→突触双向闭环（LTP/LTD + verified_memories 持久化）** |
| **自进化** | 隐式偏好学习 + Galaxy Kernel 自进化（~10分钟/次，4条/轮进化建议）+ 动态锚定（574 条触发词匹配）|
| **IPC 通信** | UDS RPC（一级主通道注册表，14 预注册方法）+ ZMQ 事件推送（双向回复）+ mmap 共享内存（4KB JSON段，双向零拷贝 + heartbeat 5s）|

## 目录结构

```
GalaxyOS/
├── services/          # 📦 核心服务模块包 (160+ 模块, pip install 入口)
│   ├── __init__.py    # 包入口，导出全部工具
│   ├── xiaoyi_claw_api.py        # 统一 API 接口 (Galaxy Kernel + R-CCAM 核心)
│   ├── _imports.py               # 选装模块降级导入管理
│   ├── rccam_state.py            # R-CCAM PhaseState 状态对象
│   ├── claw_helpers.py           # 模块级便捷 API 函数
│   ├── retrieval_hub.py          # 统一检索入口 (RRF 融合 + DAG 权重)
│   ├── memory_consolidation.py   # 记忆巩固引擎
│   ├── memory_synapse_network.py # 神经突触网络 (ncps LTC/CfC/遗忘曲线)
│   ├── smart_processor.py        # 智能处理层 (三模型通道)
│   ├── enhanced_hallucination_guard.py  # 防幻觉守卫
│   ├── cognitive_map.py          # 认知地图 (AriGraph 空间推理)
│   ├── temporal_kg.py            # 时序知识图谱 (Graphiti)
│   ├── self_evolution_engine.py  # 自进化引擎
│   ├── thinking_enhanced.py      # 增强思考引擎
│   ├── ... (共 160+ 个模块文件)
├── tests/             # 🧪 测试套件 (32 文件, 428 用例)
├── extensions/        # OpenClaw 扩展插件
│   ├── claw-core/     # 核心插件 (UDS+ZMQ+mmap 三通道 + ContextEngine + Hook)
│   └── xiaoyi-channel/  # 小艺通道通信插件
├── scripts/           # 辅助脚本 + 安装向导
├── config/            # 系统配置文件
│   └── llm_config.example.json  # API Key 配置模板
├── docs/              # 架构文档 + API 速查
├── skills/            # 技能定义 + llm-memory-integration core (260 模块)
├── .github/           # CI/CD workflows
├── Makefile           # 开发命令速查
└── pyproject.toml     # pytest/coverage/ruff/mypy 配置
```

## 安装

> GalaxyOS 是 OpenClaw 的底层引擎包，作为 Python 库安装到 OpenClaw 环境中使用。

### pip install（开发者模式）

```bash
git clone https://cnb.cool/llm-memory-integrat/GalaxyOS.git
cd GalaxyOS

# 推荐使用虚拟环境
python3 -m venv .venv && source .venv/bin/activate

# 安装
pip install -e .

# 配置 API Key
cp config/llm_config.example.json config/llm_config.json
# 编辑填入你的 DeepSeek / 硅基流动 API Key

# 运行安装向导（自检 + 配置验证）
python scripts/install_wizard.py --check
```

### 仅安装依赖

```bash
pip install -r requirements.txt
cp config/llm_config.example.json config/llm_config.json
```

### 📋 配置说明

项目通过 `config/llm_config.json` 管理所有外部 API 配置：

| 配置项 | 用途 | 提供商 |
|--------|------|--------|
| `llm` | 对话推理 (DeepSeek v4-flash) | DeepSeek |
| `llm_pro` | 并行/批量处理 (DeepSeek v4-pro) | DeepSeek |
| `embedding` | 文本向量嵌入 (bge-m3, 1024d) | 硅基流动 |
| `rerank` | 结果重排序 (bge-reranker-v2-m3) | 硅基流动 |
| `vlm` | 图像理解 | 硅基流动 |

> 模板文件：`config/llm_config.example.json`，复制后填入 `YOUR_API_KEY` 替换为实际密钥。

### 环境要求

- **OS**: Linux x86_64
- **Python**: 3.12+
- **内存**: ≥ 2 GB
- **磁盘**: ≥ 1 GB

### 依赖总览

| 包名 | 版本 | 用途 |
|------|------|------|
| numpy | >=2.4.5 | 数值计算基础 |
| torch | >=2.0.0 | GNN / 图神经网络 / ncps |
| faiss-cpu | >=1.7.0 | 向量检索 |
| pyzmq | >=25.0.0 | 进程间事件推送 |
| onnxruntime | >=1.15.0 | 推理优化 |
| ncps | >=1.0.0 | 神经电路策略 (LTC/CfC) |
| openai | >=1.0.0 | LLM API 客户端 |
| pydantic | >=2.0.0 | 数据校验 |

## 快速使用

### 作为 Python 库

```python
from services.xiaoyi_claw_api import XiaoYiClawLLM

claw = XiaoYiClawLLM()

# 记忆操作
claw.remember("内容")           # → str (memory_id)
results = claw.recall("查询关键词")  # → List[Dict]

# R-CCAM 认知循环（含 Galaxy Kernel 后台处理）
output = claw.process("用户输入")   # → str

# 系统状态
status = claw.health_check()    # → Dict
```

### CLI 命令

```bash
# 健康检查
python3 -m services.xiaoyi_claw_api health

# 存储记忆
python3 -m services.xiaoyi_claw_api store --content "内容"

# 检索记忆
python3 -m services.xiaoyi_claw_api recall --query "查询"
```

## 架构

### 架构文档（按版本）

| 版本 | 文件 | 说明 |
|------|------|------|
| **v6.2 (最新)** | — | **睡眠巩固(5阶段+CfC+GAT+LTP) + 对比学习预训练 + SparseGAT + MemGAS熵路由/GMM关联 + GAT→RRF融合** |
| **v5.6** | — | **神经检索全链路集成: neural_rerank → ContextEngine assemble + Galaxy Kernel 认知注入 + _content_type 上下文排序** |
| **v5.5** | `docs/xiaoyi-claw-core-architecture-v5.0.md` | **IntelligentThinkingTrigger v2.0** 三论文集成 + Cognition Forest 子树修正 |
| **v5.4** | `docs/xiaoyi-claw-core-architecture-v5.0.md` | **KG as Memory Backbone 4 阶段** + 检索通道 + Cognition 图推理 + 睡眠图维护 |
| **v5.2** | `docs/xiaoyi-claw-core-architecture-v5.0.md` | **KoRa v2 行为模式引擎** + DAG 上下文持久化修复 |
| **v5.1** | `docs/xiaoyi-claw-core-architecture-v5.0.md` | **R-CCAM 延迟优化** + 四思考技能管道重架构 + DAG 上下文管理器升级 + 安装向导 |
| v4.6 | `docs/xiaoyi-claw-core-architecture-v4.6.md` | SmartProcessor 统一路由 + 三通道透明互通 |
| v4.5 | `docs/xiaoyi-claw-core-architecture-v4.5.md` | Galaxy 增强（DAG三维绑定/Cognition Forest/KoRa/Kernel） |
| v4.4 | `docs/xiaoyi-claw-core-architecture-v4.4.md` | 人格视觉 + Merge Gate + Rails增强版 + 内在元认知 |
| v4.2 | `docs/xiaoyi-claw-core-architecture-v4.2.md` | UDS 主通道、ZMQ+mmap DAG、统一深度管线 |
| v3.4.0 | `docs/xiaoyi-claw-core-architecture-v3.4.0.md` | 三通道通信建成、15 层精简 |
| v3.1.0 | `docs/xiaoyi-claw-core-architecture-v3.1.0.md` | ContextEngine 注册、DAG 上下文中继 |
| v3.0.0 | `docs/xiaoyi-claw-core-architecture-v3.0.0.md` | 16 层架构、R-CCAM 认知循环 |

**完整架构文档（含 15 层全景图、470+ 功能列表、更新日志）：** 👉 [📖 查看 Skills 文档栏](https://cnb.cool/llm-memory-integrat/GalaxyOS?tabValue=SKILLS-ov-file)

### v6.2 新特性

| 特性 | 说明 |
|------|------|
| **睡眠巩固 (BioRhythmSleepConsolidator v2)** | NREM→REM→DeepSleep 5阶段接入 CfC 序列预测 + GAT 注意力权重 + LTP/LTD 自适应调权。空闲 120s 自动触发模拟睡眠巩固 |
| **对比学习预训练 (SynapseContrastor)** | GraphCL 风格自监督学习，子图采样/特征掩码/边扰动三增强，InfoNCE loss。预训练权重建入 GAT 初始化替代随机 |
| **SparseGAT** | O(E·d) 稀疏注意力替代 O(N²·d) 稠密，全量 3078 节点 ~3MB（原 20GB OOM），`use_sparse` 开关默认 True |
| **MemGAS 熵路由 (EntropyRouter)** | 自适应通道权重分配：低熵通道高权重，高熵通道压权重。平滑系数 0.3*熵+0.7*均匀 |
| **GMM 记忆关联 (GMMMemoryAssociator)** | 新记忆→GMM 聚 2 类(accept/reject)→accept 类建关联边，边权重=余弦相似度 |
| **GAT 注意力权重 → RRF 融合** | `forward_with_attention()` 暴露边级注意力，RRF 融合用 GAT 权重增强/替代评分 |

### v6.1 新特性

| 特性 | 说明 |
|------|------|
| **BlobArena v2 — DAG 无损存储** | mmap-backed append-only blob storage，替代 DAG 节点 512/2000 字符硬截断。O(1) 随机访问 + generational GC |
| **ONNX bge-small-zh-v1.5 中文嵌入** | 替换 all-MiniLM-L6-v2 (384d 英文) → BAAI/bge-small-zh-v1.5 (512d, 92MB)。ONNX Runtime ~42ms/embed |
| **RetrievalHub 7通道全链路** | KG + Local + DAG(MN-RU) + Synapse(GNN+CfC) + Paper + Cognitive(MN-RU三通道) + Web。RRF v2 → neural rerank → quality assessment。融合置信度 0.93 |
| **MN-RU siliconflow fallback** | BAAI/bge-m3 的 dimensions 参数 siliconflow 不支持 → 自动无参重试 fallback，3879 节点 |
| **ANNSelector v2 + FAISS 动态索引** | <5000 自动 HNSWFlat，延迟选择，unified_vector_store 集成 |
| **SparseGAT** | O(E·d) 稀疏注意力替代 O(N²·d) 稠密，全量 3078 节点 ~3MB（原 20GB OOM），默认 `use_sparse=True` |
| **GNN Graph Builder** | GraphSAGE/GAT/GCN 三卷积层，突触网络图推理底层构建 |
| **~140 模块全同步** | scripts_core / integration / memory / rails / privileged / api → 138 模块全加载 |

### v6.0 新特性

| 特性 | 说明 |
|------|------|
| **五路神经检索 → ContextEngine 全链路** | `retrieval_hub` 五路并行（KG/Local/DAG/Synapse/Paper）→ neural_rerank_dedup（LTC h_t 门控 + 激活传播提权 + Content Fingerprint 去重）→ ContextEngine assemble 注入 |
| **smart_retrieval UDS 方法** | 新增 Worker RPC 方法，走完整 `retrieval_hub()` 管道，15s 超时 + 自动降级到旧 recall |
| **Galaxy Kernel 认知注入 assemble** | 60s 新鲜期内 `galaxy_kernel_insights.json`（情感/因果/空间/CoVe）自动注入 assemble 上下文，与 R-CCAM 去重 |
| **_content_type 上下文排序** | 每条检索结果标记 `conversation` / `summary` / `metadata`，ContextEngine smartRecall 按类型重排序，对话历史优先 |
| **Galaxy Kernel 产出优化** | `_run_paper_post_response` 输入从仅 query 改为完整对话对（query+answer），使情感/因果/空间分析有足够文本上下文 |
| **ncps 神经电路策略集成** | LTC (Liquid Time-Constant) + CfC (Closed-form Continuous-depth) + 遗忘曲线，每轮对话自动创建神经元/突触，非阻塞 try/except 并行侧效应 |
| **memory_synapse_network.py** | 全新服务模块：MemoryNeuron（含 LTC 15 参数）/ NeuronManager（去重+激活）/ SynapseManager（LTP/LTD）/ CfCSynapseEngine / ForgettingCurveTrainer |
| **NLP 增强神经网络** | MemoryNeuron 新增 4 字段 (nlp_keywords/entities/sentiment/importance)，create_neuron 自动 NLP 特征提取，去重支持 Jaccard 语义兜底，突触权重基于关键词/实体重叠动态计算 (0.3+0.4*kw+0.3*ent) |
| **4 增强 NLP 模块全接入** | 依存句法分析 → 实体链接 → 指代消解 → 对比句检测 结果写入神经元 metadata + 自动创建实体关联神经元 |
| **防幻觉双向闭环** | 验证结果回流神经网络：置信度 < 0.5 → LTD 削弱突触，> 0.8 → LTP 强化突触。高置信度问答持久化到 verified_memories.jsonl |
| **biorhythm_sleep_consolidation LTCCell** | 仿生睡眠 REM 阶段可调用 LTC 激活获取真实 hidden state，梦境碎片拼装受 NLP 实体链约束 |
| **TKG 事件日志系统** | 基于时序知识图谱的事件日志记录 |
| **ncps 依赖** | requirements.txt + ncps>=1.0.0 |

### v5.5 新特性

| 特性 | 说明 |
|------|------|
| **IntelligentThinkingTrigger v2.0** | 三论文集成：RCR-Router 动态评分 + Springdrift CBR 记忆 + A-ToM 认知阶段推断，替换静态关键词匹配 |
| **skill_scorer.py (28KB) — RCR-Router 引擎** | 30 个 SkillDescriptor 元数据，四维评分 (semantic 0.40 / role 0.20 / stage 0.15 / history 0.10)，贪心路由 top-3 |
| **thinking_memory.py (14KB) — Springdrift CBR 层** | ThinkingCase 结构 + Sensorium 持续自感知 + 持久化 JSON，支持相似 case 召回 |
| **A-ToM 认知阶段推断** | 6 阶段 (explore/analyze/verify/breakdown/plan/decide)，pattern 重构：`为什么`→verify, debug 关键词补全 |
| **Cognition Forest 子树内容修正** | user←用户画像 (IDENTITY/SOUL/USER), self←系统能力 (92技能列表), env/meta 不变 |
| **core/ 子模块全面同步** | 80 个核心子模块 (api/integration/memory/privileged) 补齐 |

### v5.4 新特性

| 特性 | 说明 |
|------|------|
| **KG as Memory Backbone 4 阶段全链路** | 实体自动摄入 + 图检索主通道 + Cognition 图推理 + 睡眠图维护 |
| **Phase 1 - 实体持久化** | R-CCAM 每轮对话自动提取实体写入 KG，LLM 抽取 + 消歧 + 双向边 |
| **Phase 2 - 图检索主通道** | `_do_kg()` 第 6 路检索，图遍历 depth 2-3，RRF 自动与向量检索竞争 |
| **Phase 3 - Cognition 图推理** | 共享目标实体检测 + 时序频率分析，注入 thinking_skills_content |
| **Phase 4 - 睡眠图推理** | DEEP-SLEEP 阶段实体消歧 + 社区发现 + 30 天低置信度边清理 |
| **检索通道升级** | 6 路并行（kg + local + dag + synapse + paper + web），KG 优先 |

### v5.2 新特性

| 特性 | 说明 |
|------|------|
| **KoRa v2 行为模式引擎** | 时序周期分析 + 自适应参数推荐 + Cognition 阶段主动注入 |
| **DAG 上下文持久化修复** | dag_shim.py 路径修复 + should_compact 参数修复 |

### v5.1 新特性

| 特性 | 说明 |
|------|------|
| **R-CCAM 延迟优化** | 查询改写+分类二合一，问候快速通路 ~24s→0.1s（240×） |
| **FLARE 并行化** | ThreadPoolExecutor 3 线程并行防幻觉验证，节省 3-5s |
| **四思考技能管道重架构** | thinking_skills_content 从 skill_guide 分离，budget routing |
| **DAG 上下文管理器升级** | 时间衰减权重排序 + Cognition Forest 子树 + cycle_summary 追加 |
| **WorkflowEngine 修复** | claw_worker.py 补 ORCHESTRATION_DIR，健康检查链路正常 |
| **记忆验证增强** | from_dict 容错、SourceType 补全 |
| **install_wizard 安装向导** | 7 阶段全自动自检（环境→模块→services 模块→pip 依赖→文件→断路器→配置） |
| **Heuristic 批量验证** | 规则检查零 LLM 成本，75 条记忆批量处理 |
| **系统品牌更名 GalaxyOS** | 从小艺 Claw 系统架构升级 |
| **process() 精简 -70%** | 863→254 行 |
| **Galaxy Kernel 扩容** | 308 行独立后台线程，6s 轮询 |
| **异步注入三层兜底** | assemble→before_prompt_build→agent_end |
| **自进化首次跑通** | 4 条/10min 进化建议产出 |
| **时空认知集成** | Graphiti/AriGraph/LASAR 三篇论文 |

### GalaxyOS 管线结构

```
用户输入
  ↓
before_agent_reply → fire-and-forget R-CCAM（后台跑，不阻塞）
  ↓
assemble() → [轮次间] 捡 _rccamCache → 注入 systemPromptAddition
  ↓
before_prompt_build → 动态锚定（触发词匹配）
  ↓
LLM 推理 + 回复
  ↓
agent_end → 存 _pendingRccamInjection（兜底）
  ↓
Galaxy Kernel（后台，6s 轮询）
  ┗ Reflexion 记录
  ┗ 自进化（~50 轮 ≈ 10 分钟）
```

### 架构概览（15 层）

| 层 | 名称 | 说明 |
|----|------|------|
| L1 | 记忆核心层 | 三层记忆 + 记忆巩固 + 情感驱动 + 艾宾浩斯遗忘曲线 + **ncps 神经突触网络 (LTC/CfC)** |
| L2 | 上下文层 | DAG SQLite + 摘要回溯 + scene_trace + ContextEngine |
| L3 | 检索增强层 | CRAG + GraphRAG + RAPTOR + Self-RAG + Merge Gate |
| L4 | 防幻觉层 | 10 重交叉验证 + 多源证据 + 矛盾检测 + **LTP/LTD 神经元闭环** |
| L5 | 知识图谱层 | 实体链接 + 关系抽取 + 三元组 + 时序KG (Graphiti) |
| L6 | 智能处理层 | SmartProcessor (Flash/Pro/VLM) + Visual RAG |
| L7 | 缓存优化层 | KV Cache 硬件磁盘复用 + 语义缓存 + ACP 持久化 |
| L8 | 思考技能层 | **IntelligentThinkingTrigger v2.0** (RCR-Router + Springdrift + A-ToM) + 20方法论 + Reflexion + 10论文引擎 |
| L9 | 认知循环层 (R-CCAM) | 五阶段循环 + Galaxy Kernel 异步 + 三层兜底注入 |
| L10 | Persona 层 | 人格七重防线 + 自进化上下文注入 |
| L11 | Agent 层 | 9 思考技能 + 11 方法论技能 + 技能协调器 |
| L12 | 系统能力层 | IPC 三通道 + Worker 自动重启 + 硬件加速 |
| L13 | 多模态层 | OCR2 自动触发 + Visual RAG + 图像理解 |
| L14 | 安全护栏层 | Rails + AIGC 标记 + 脱敏 + 隐式偏好学习 |
| L15 | 自进化层 | Galaxy Kernel 元认知 + 主动进化 + 静态固化 |

## 生态

GalaxyOS 与以下组件协同工作：

- **[OpenClaw](https://github.com/openclaw/openclaw)** — AI Assistant 框架（基础运行平台）
- **claw-core 插件** (`extensions/claw-core/`) — UDS+ZMQ+mmap 三通道 IPC + ContextEngine + 4 个 Hook
- **xiaoyi-channel** (`extensions/xiaoyi-channel/`) — 小艺通道通信插件
- **llm-memory-integration** — 开源版本（ClawHub）

## 开发

| 资源 | 说明 |
|------|------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 — 环境搭建、分支策略、提交规范 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [docs/API.md](docs/API.md) | API 速查 — 核心模块公开接口一览 |
| `make test` | 运行 428 个测试用例 |
| `make lint` | 代码风格检查 |
| `make ci` | lint + test 一键通过 |

## 许可证

MIT License。详见 `LICENSE` 文件。
