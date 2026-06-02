# 🌌 GalaxyOS — 认知增强引擎

> OpenClaw 的开源认知增强引擎，为 AI Assistant 提供记忆、检索、推理、验证、自进化等全套认知能力
> 版本: v5.3 · KG as Memory Backbone 4 阶段全链路 + KoRa v2 + DAG 持久化

## 总览

`GalaxyOS`（前身：小艺 Claw 系统）是 **OpenClaw 的底层认知增强引擎**。提供 **15 层架构、430+ 功能项、144+ 服务模块**，覆盖从记忆管理到认知推理的全链路。

### 核心能力

| 能力 | 说明 |
|------|------|
| **记忆** | 三层记忆体系 + 记忆巩固引擎（CLS固化 + 仿生睡眠5阶段 + 干扰合并 + KG图推理） |
| **检索** | 向量检索 + 知识图谱 + Self-RAG + CRAG 混合检索 + bge-reranker-v2-m3 重排序 + GraphRAG + RAPTOR + Merge Gate 五路去重融合 |
| **智能处理** | 查询改写（Pro）/ 结果总结（Flash）/ 语义过滤 / 图像理解（SmartProcessor 三模型通道：Flash/Pro/VLM）+ Visual RAG 自动 OCR2 触发 |
| **认知循环（R-CCAM）** | 五阶段结构化认知循环：Retrieval→Cognition→Control→Action→Memory，元认知 5 种动态策略调节器 + 异步注入三层兜底 |
| **Galaxy Kernel** | 独立后台元认知线程（6s轮询），持 Reflexion 记录 + 自进化产出，不阻塞主推理。process() 精简 **70%**（863→254行） |
| **思考方法论** | 20 个思考方法论技能 + Reflexion + Self-Refine + Multi-Path + Toolformer + 10 论文引擎全量集成 |
| **防幻觉** | 10 重交叉验证 + 多源证据 + 矛盾检测 + CRAG 动态纠错 + Rails 护栏增强版 |
| **自进化** | 隐式偏好学习 + Galaxy Kernel 自进化（~10分钟/次，4条/轮进化建议）+ 动态锚定（574 条触发词匹配） |
| **IPC 通信** | UDS RPC（一级主通道注册表，14 预注册方法）+ ZMQ 事件推送（双向回复）+ mmap 共享内存（4KB JSON段，双向零拷贝 + heartbeat 5s）|

## 目录结构

```
GalaxyOS/
├── services/          # 📦 核心服务模块包 (pip install 入口)
│   ├── __init__.py    # 包入口，导出全部工具
│   ├── xiaoyi_claw_api.py        # 统一 API 接口 (Galaxy Kernel + R-CCAM 核心)
│   ├── claw_worker.py            # Worker 进程 (Galaxy Kernel 后台线程)
│   ├── retrieval_hub.py          # 统一检索入口 (RRF 融合 + DAG 权重)
│   ├── memory_consolidation.py   # 记忆巩固引擎
│   ├── smart_processor.py        # 智能处理层 (三模型通道)
│   ├── enhanced_hallucination_guard.py  # 防幻觉守卫
│   ├── cognitive_map.py          # 认知地图 (AriGraph 空间推理)
│   ├── temporal_kg.py            # 时序知识图谱 (Graphiti)
│   ├── spatial_topology.py       # 空间拓扑
│   ├── self_evolution_engine.py  # 自进化引擎
│   ├── thinking_enhanced.py      # 思考增强 (_extract_json_block)
│   ├── ... (共 144+ 个模块)
├── extensions/        # OpenClaw 扩展插件
│   ├── claw-core/     # 核心插件 (UDS+ZMQ+mmap 三通道 + ContextEngine + Hook)
│   
├── workspace-scripts/ # 工作台部署/同步脚本
├── scripts/           # 辅助脚本
├── config/            # 系统配置文件
├── docs/              # 架构文档
├── supervisor/        # supervisor 进程管理配置
├── hooks/             # OpenClaw 管道钩子 (claw-bootstrap)
└── services/          # 服务模块包
```

## 安装

### 从 PyPI（未来）

```bash
pip install galaxyos
```

### 从仓库

```bash
# 克隆仓库
git clone https://cnb.cool/llm-memory-integrat/GalaxyOS.git
cd GalaxyOS

# 安装核心服务包
pip install -e .

# 或仅安装依赖
pip install -r requirements.txt
```

### 安装依赖

```bash
pip install -r requirements.txt
```

**核心依赖**：

| 包名 | 版本 | 用途 |
|------|------|------|
| numpy | >=2.4.5 | 数值计算基础 |
| scipy | >=1.10.0 | 科学计算（MKL 后端优化） |
| pyzmq | >=25.0.0 | 进程间事件推送 |
| aiohttp | >=3.9.0 | 异步 HTTP 客户端 |
| httpx | >=0.27.0 | HTTP 客户端 |
| faiss-cpu | >=1.7.0 | 向量检索 |
| Pillow | >=10.0.0 | 图像处理 |
| orjson | >=3.9.0 | 高速 JSON 序列化 |
| polars | >=1.0.0 | 结构化数据处理 |
| duckdb | >=1.0.0 | SQL 嵌入式引擎 |
| jieba | >=0.42.0 | 中文分词 |
| snownlp | >=0.12.0 | 情感分析 |
| tiktoken | >=0.5.0 | Token 计数 |
| uvloop | >=0.19.0 | 事件循环加速 |
| torch | >=2.0.0 | GNN / 图神经网络 |
| scikit-learn | >=1.3.0 | 机器学习工具 |
| pandas | >=2.0.0 | 数据分析 |
| onnxruntime | >=1.15.0 | 推理优化 |
| psutil | >=5.9.0 | 系统监控 |
| openai | >=1.0.0 | LLM API 客户端 |
| requests | >=2.31.0 | HTTP 请求 |
| pydantic | >=2.0.0 | 数据校验 |

### 环境要求

- **OS**: Linux x86_64（已验证华为云 EulerOS 2.0）
- **Python**: 3.12+
- **CPU**: 支持 AVX-512 指令集（Xeon 8378C 等）
- **内存**: ≥ 2 GB
- **磁盘**: ≥ 1 GB

## 快速使用

### 作为 Python 库

```python
from services.xiaoyi_claw_api import XiaoYiClawLLM

claw = XiaoYiClawLLM()

# 记忆操作
claw.remember("内容")
results = claw.recall("查询关键词")

# 验证
result = claw.verify_with_cross_validation("声明内容")

# R-CCAM 认知循环（含 Galaxy Kernel 后台处理）
output = claw.rccam_cycle("用户输入")
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
| **v5.3 (最新)** | `docs/xiaoyi-claw-core-architecture-v5.0.md` | **KG as Memory Backbone 4 阶段** + 检索通道 + Cognition 图推理 + 睡眠图维护 |
| **v5.2** | `docs/xiaoyi-claw-core-architecture-v5.0.md` | **KoRa v2 行为模式引擎** + DAG 上下文持久化修复 |
| **v5.1** | `docs/xiaoyi-claw-core-architecture-v5.0.md` | **R-CCAM 延迟优化** + 四思考技能管道重架构 + DAG 上下文管理器升级 + 安装向导 |
| v4.6 | `docs/xiaoyi-claw-core-architecture-v4.6.md` | SmartProcessor 统一路由 + 三通道透明互通 |
| v4.5 | `docs/xiaoyi-claw-core-architecture-v4.5.md` | Galaxy 增强（DAG三维绑定/Cognition Forest/KoRa/Kernel） |
| v4.4 | `docs/xiaoyi-claw-core-architecture-v4.4.md` | 人格视觉 + Merge Gate + Rails增强版 + 内在元认知 |
| v4.2 | `docs/xiaoyi-claw-core-architecture-v4.2.md` | UDS 主通道、ZMQ+mmap DAG、统一深度管线 |
| v3.4.0 | `docs/xiaoyi-claw-core-architecture-v3.4.0.md` | 三通道通信建成、15 层精简 |
| v3.1.0 | `docs/xiaoyi-claw-core-architecture-v3.1.0.md` | ContextEngine 注册、DAG 上下文中继 |
| v3.0.0 | `docs/xiaoyi-claw-core-architecture-v3.0.0.md` | 16 层架构、R-CCAM 认知循环 |

**完整架构文档（含 15 层全景图、440+ 功能列表、更新日志）：** 👉 [📖 查看 Skills 文档栏](https://cnb.cool/llm-memory-integrat/GalaxyOS?tabValue=SKILLS-ov-file)

### v5.3 新特性

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
| **DAG 上下文持久化修复** | dag_shim.py 路径修复 + should_compact 参数修复，当前对话数据正常落库 |

### v5.1 新特性

| 特性 | 说明 |
|------|------|
| **R-CCAM 延迟优化** | 查询改写+分类二合一，问候快速通路 ~24s→0.1s（240×） |
| **FLARE 并行化** | ThreadPoolExecutor 3 线程并行防幻觉验证，节省 3-5s |
| **四思考技能管道重架构** | thinking_skills_content 从 skill_guide 分离，budget routing |
| **DAG 上下文管理器升级** | 时间衰减权重排序 + Cognition Forest 子树 + cycle_summary 追加 |
| **WorkflowEngine 修复** | claw_worker.py 补 ORCHESTRATION_DIR，健康检查链路正常 |
| **记忆验证增强** | from_dict 容错、SourceType 补全、health_check 仅验证记忆检查 |
| **install_wizard 安装向导** | 6 阶段全自动自检（环境→模块→文件→服务→断路器→配置） |
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
| L1 | 记忆核心层 | 三层记忆 + 记忆巩固 + 情感驱动 + 艾宾浩斯遗忘曲线 |
| L2 | 上下文层 | DAG SQLite + 摘要回溯 + scene_trace + ContextEngine |
| L3 | 检索增强层 | CRAG + GraphRAG + RAPTOR + Self-RAG + Merge Gate |
| L4 | 防幻觉层 | 10 重交叉验证 + 多源证据 + 矛盾检测 |
| L5 | 知识图谱层 | 实体链接 + 关系抽取 + 三元组 + 时序KG (Graphiti) |
| L6 | 智能处理层 | SmartProcessor (Flash/Pro/VLM) + Visual RAG |
| L7 | 缓存优化层 | KV Cache 硬件磁盘复用 + 语义缓存 + ACP 持久化 |
| L8 | 思考技能层 | 20 方法论 + Reflexion + Self-Refine + 10 论文引擎 |
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

## 许可证

MIT License。详见 `LICENSE` 文件。
