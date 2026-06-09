# 🌌 GalaxyOS — 认知增强引擎

> OpenClaw 的开源认知增强引擎，为 AI Assistant 提供记忆、检索、推理、验证、自进化等全套认知能力
> 版本: v7.0 · 统一包 galaxyos/ + WorkerPool 弹性扩缩 + PIL 独立子进程 + Rust 原生扩展

## 总览

`GalaxyOS` 是 **OpenClaw 的底层认知增强引擎**。提供 **统一包架构 (313 files)、470+ 功能项**，覆盖从记忆管理到认知推理的全链路。

### 核心能力

| 能力 | 说明 |
|------|------|
| **记忆** | 三层记忆体系 + 记忆巩固引擎 + 神经突触记忆网络 (ncps LTC+CfC) + BlobArena mmap 无损存储 |
| **检索** | RetrievalHub 7通道 (KG/Local/DAG/MN-RU/Synapse/Paper/Cognitive/Web) + bge-reranker 重排序 |
| **智能处理** | SmartProcessor 三模型通道 (Flash/Pro/VLM) + Visual RAG 自动 OCR2 |
| **认知循环** | R-CCAM 五阶段结构化认知 (Retrieval→Cognition→Control→Action→Memory) |
| **弹性基础设施** | WorkerPool 自动扩缩 (2~8) + CircuitBreaker 熔断 + SessionContext 粒度隔离 |
| **PIL 隔离** | 独立子进程图像处理 (Python/Rust)，零 GIL 竞争 |
| **防幻觉** | 10 重交叉验证 + 多源证据 + 突触双向闭环 (LTP/LTD) |
| **IPC 通信** | UDS RPC (selectors 串行) + ZMQ 事件推送 + mmap 共享内存 |

## 目录结构

```
GalaxyOS/
├── galaxyos/          # 📦 统一 Python 包 (pip install galaxyos)
│   ├── engine/        #   核心引擎 (ClawWorker, RetrievalHub, DAG, FastPIL)
│   ├── privileged/    #   跨平台系统模块 (VectorAPI, PlatformAdapter, GPU/NUMA)
│   ├── orchestration/ #   工作流调度 (WorkflowEngine, TaskEngine)
│   ├── config/        #   引擎配置
│   └── scripts/       #   运维工具
├── extensions/        # OpenClaw 插件
│   └── galaxyos/      #   核心插件 (index.js + dist/scripts/ + native/)
│       ├── index.js               # WorkerPool 弹性 + CircuitBreaker
│       ├── dist/scripts/          # Python 运行时
│       └── native/                # Rust 原生扩展
├── skills/            # 52 个技能包
├── scripts/           # 运维工具脚本
├── tests/             # 测试套件
├── path_resolver.py   # 统一路径定义
├── setup.py           # pip install 入口
└── Makefile           # make test / make native
```

## 技能包生态系统

> 来源：[llm-memory-integrat/llm](https://cnb.cool/llm-memory-integrat/llm) — 52 个 LLM 技能包，覆盖文档处理、搜索调研、认知方法论、AI 代理、图像、邮件、前端设计等。

### 🧠 认知与思考方法

| 技能 | 说明 |
|------|------|
| **arming-thought** | 在每次新对话开始时自动调用，建立"实事求是"总原则，为后续任务选择下游 skill |
| **concentrate-forces** | 多任务竞争时确定主攻方向、停止分散用力；信号：优先级过多、资源紧张 |
| **contradiction-analysis** | 问题复杂、存在冲突因素时调用；信号：trade-off、瓶颈、根因不明 |
| **criticism-self-criticism** | 工作完成/阶段验收/收到批评/反复犯同类错误时系统纠偏；信号：review、retrospective |
| **investigation-first** | 下判断/决策/提建议前事实不充分时优先调用；信号：unknowns、信息缺口、证据不足 |
| **mass-line** | 收集多方意见、将零散反馈整合为可执行方案；信号：stakeholder input、user feedback |
| **overall-planning** | 多目标/利益方/制约指标间动态平衡；信号：trade-offs、目标冲突、系统性约束 |
| **practice-cognition** | 方案/假设/判断需通过实践验证、试错迭代或复盘升级认知；信号：experiment、prototype |
| **protracted-strategy** | 目标长期、资源劣势、短期无法速胜但不可放弃；信号：long-term、phased plan、endurance |
| **spark-prairie-fire** | 从零起步、资源极少，需找最小可行切入口建立根据地；信号：bootstrap、MVP、pilot |
| **workflows** | 任务明显需要多个思想武器协作时调用；信号：启动新项目、攻坚复杂问题、迭代优化 |

### 📄 文档与格式处理

| 技能 | 说明 |
|------|------|
| **docx** | Word 文档创建、读取、编辑、操作。支持 tracked changes、评论、格式保留 |
| **pdf** | PDF 全套处理：创建、读取、编辑、合并、拆分、表单填写、提取文本/表格 |
| **pptx** | PowerPoint 演示文稿创建与编辑。支持 pptxgenjs 生成、模板修改 |
| **xiaoyi-doc-convert** | 专业文档格式转换。Docx/PDF/Xlsx/Pptx/Markdown 互转，自动路径规划 |
| **markitdown** | 文件与办公文档转为 Markdown。支持 PDF、DOCX、PPTX、XLSX、图片、音频等 |
| **nano-pdf** | 自然语言指令编辑 PDF，使用 nano-pdf CLI |
| **excel-analysis** | Excel 电子表格分析：数据透视表、图表生成、数据分析 |

### 🔍 搜索与信息调研

| 技能 | 说明 |
|------|------|
| **deep-search-and-insight-synthesize** | 深度调研专用。多层搜索 + 多源交叉验证 + 综合分析，替代普通搜索 |
| **multi-search-engine** | 17 引擎聚合搜索（8 国内 + 9 全球），支持高级筛选和结果合并 |
| **xiaoyi-web-search** | 华为云 AI 联网搜索 API，获取实时网络信息 |
| **news-extractor** | 新闻站点内容提取。支持微信公众号、头条、网易、腾讯、BBC、CNN 等 12 平台 |

### 🖼️ 图像处理

| 技能 | 说明 |
|------|------|
| **xiaoyi-image-search** | 图片搜索，返回图链接、缩略图、图片尺寸，适用于文档/PPT 配图 |
| **xiaoyi-image-understanding** | 小艺图像理解 API，图像内容识别与文字描述 |
| **seedream-image_gen** | AI 图像生成，支持文生图、图生图、多图融合、图像编辑、组图生成 |

### 📧 邮件通信

| 技能 | 说明 |
|------|------|
| **himalaya** | CLI 邮件管理（IMAP/SMTP），支持列表、读取、撰写、回复、转发、附件 |
| **imap-smtp-email** | IMAP/SMTP 邮件工具。支持 Gmail、Outlook、163/126/188 邮箱 |

### 🤖 AI 代理与自动化

| 技能 | 说明 |
|------|------|
| **hz-proactive-agent** | 将 AI Agent 从被动任务执行者转变为主动伙伴，WAL 协议 + 自进化 [v3.1.0] |
| **autonomous-tasks** | 自驱型 AI 工作器。读取目标 → 生成任务 → 执行 → 记录进度 |
| **proactive-tasks** | 主动性目标与任务管理系统。目标分解、进度追踪、定时任务 |
| **self-improving-agent** | 捕获学习、错误与纠偏，实现持续改进。每次出错后自动沉淀经验 |
| **xiao-gui-agent** | 手机屏幕自动化操作（点击、滑动、输入），模拟人操作 APP |
| **skill-creator** | AgentSkill 创建与更新。设计、结构化、打包技能 |
| **skillhub-preference** | skillhub 优先用于技能发现/安装/更新，fallback 到 clawhub |
| **find-skills** | 技能发现流程。用户寻找/安装/发现技能包时自动触发 |
| **openclaw-skills-agent-builder** | 端到端构建高性能 OpenClaw Agent。设计、结构化并生成完整 agent |
| **openclaw-skills-soulcraft** | 通过引导式对话为 OpenClaw Agent 创建/改进 SOUL.md 文件 |
| **example-skill** | 示例技能，演示 ClawHub 发布流程 [v1.0.0] |

### 🎨 前端与设计

| 技能 | 说明 |
|------|------|
| **react-best-practices** | Vercel Engineering 的 React/Next.js 性能优化指南 |
| **superdesign** | 前端设计专家指南，创建美观现代的 UI |
| **webapp-testing** | Playwright 本地 Web 应用测试工具包 |
| **bytedance-deer-flow-vercel-deploy** | 部署应用到 Vercel。触发：用户请求部署网站或应用 |
| **calcom-web-design-guidelines** | UI 代码 Web Interface Guidelines 合规审查 |

### 🌐 通用工具

| 技能 | 说明 |
|------|------|
| **2nd-brain** | 个人知识库，捕获与检索人物、地点、餐厅、设备、事件、媒体等信息 [v1.3.0] |
| **Humanizer-zh-main** | 去除中文文本 AI 生成痕迹，使其更自然、更像人类书写 |
| **best-minds** | 模拟器思维：世界上谁最懂这个？TA 会怎么说？触发：最强大脑、顶级专家 |
| **ontology** | 结构化知识图谱，用于 Agent 记忆与可组合技能 |
| **ima-skill** | 统一的 IMA OpenAPI 技能，支持知识库操作和笔记管理 |
| **read-arxiv-paper** | 读取 arXiv 论文，给定 URL 自动解析、总结 |
| **weather** | 天气查询，通过 wttr.in 或 Open-Meteo 获取当前天气和预报 |
| **xiaoyi-health** | 小艺健康 CLI 技能索引，健康相关问题入口 |
| **xiaoyi-file-upload** | 上传本地文件到对象存储（OSMS/NSP），获取共享下载 URL |

## 安装

```bash
git clone https://cnb.cool/llm-memory-integrat/GalaxyOS.git
cd GalaxyOS

pip install -e .            # 安装 galaxyos 包

# 编译 Rust 原生扩展（可选，需要 Rust 工具链）
make native

# 运行安装向导
GALAXYOS_REPO=. python3 -m galaxyos.scripts.install_wizard --check
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
| **v6.3 (最新)** | — | **睡眠巩固(5阶段+CfC+GAT+LTP) + 对比学习预训练 + SparseGAT + MemGAS熵路由/GMM关联 + GAT→RRF融合** |
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

### v6.3 新特性

| 特性 | 说明 |
|------|------|
| **睡眠巩固 (BioRhythmSleepConsolidator v2)** | NREM→REM→DeepSleep 5阶段接入 CfC 序列预测 + GAT 注意力权重 + LTP/LTD 自适应调权。空闲 120s 自动触发模拟睡眠巩固 |
| **对比学习预训练 (SynapseContrastor)** | GraphCL 风格自监督学习，子图采样/特征掩码/边扰动三增强，InfoNCE loss。预训练权重建入 GAT 初始化替代随机 |
| **SparseGAT** | O(E·d) 稀疏注意力替代 O(N²·d) 稠密，全量 3078 节点 ~3MB（原 20GB OOM），`use_sparse` 开关默认 True |
| **MemGAS 熵路由 (EntropyRouter)** | 自适应通道权重分配：低熵通道高权重，高熵通道压权重。平滑系数 0.3*熵+0.7*均匀 |
| **GMM 记忆关联 (GMMMemoryAssociator)** | 新记忆→GMM 聚 2 类(accept/reject)→accept 类建关联边，边权重=余弦相似度 |
| **GAT 注意力权重 → RRF 融合** | `forward_with_attention()` 暴露边级注意力，RRF 融合用 GAT 权重增强/替代评分 |

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
