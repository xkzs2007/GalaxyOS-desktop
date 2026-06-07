---
name: llm-memory-integration
description: LLM + 向量模型集成方案。支持任意 LLM + Embedding 模型，用户自行配置。支持混合检索、智能路由、渐进式启用、用户画像自动更新。v6.1: ONNX bge-small-zh-v1.5 中文原生嵌入 + MN-RU siliconflow BAAI/bge-m3 + 7通道检索 Hub。
version: 6.2.0
license: MIT-0
author: xkzs2007
homepage: https://clawhub.ai/skill/llm-memory-integration
requirements:
  binaries:
    - python3
    - sqlite3
  envVars:
    required:
      - EMBEDDING_API_KEY
    optional:
      - LLM_API_KEY
  network: true
security_note: |
  ⚠️ 安全说明（v2.0.9 元数据修复版）：
  
  【重要修复】
  - ✅ **已移除所有硬编码 API 密钥**
  - ✅ **所有配置文件中 auto_update: false**（与文档一致）
  - ✅ **无任何真实凭据或端点**
  - ✅ **元数据一致性修复**：package.json 与 SKILL.md 完全一致
  
  【必需配置】
  - ⚠️ **EMBEDDING_API_KEY**（必需）- 用户必须配置 Embedding API 密钥
  - ⚠️ **LLM_API_KEY**（可选）- 如需 LLM 功能需配置
  
  【数据访问声明】
  - 本技能会读写 ~/.openclaw 下的文件（vectors.db, MEMORY.md, persona.md, logs, configs）
  - 此行为与声明的功能一致（向量搜索、记忆管理、用户画像更新）
  
  【用户画像自动更新】
  - ⚠️ **默认禁用**（所有配置文件中 auto_update: false）
  - ✅ 更新前**强制用户确认**（require_confirmation: true）
  - ✅ 更新前**自动备份** persona.md（backup_before_update: true）
  - ✅ 最多保留 5 个备份文件
  
  【SQLite 扩展安全加载】
  - ✅ SHA256 哈希验证（safe_extension_loader.py 完整实现）
  - ✅ 信任列表管理（.trusted_hashes.json）
  - ✅ 文件完整性检查（大小、权限、路径验证）
  - ✅ 权限验证（仅允许 644/755）
  - ⚠️ **首次加载需用户明确确认**
  - ⚠️ **生产环境禁止自动确认**
  
  【代码质量】
  - ✅ 已彻底移除所有 shell=True 调用
  - ✅ 所有 subprocess 调用使用参数列表（无命令注入风险）
  - ✅ 核心脚本已改用 sqlite3 直接连接（cached_search.py, check_coverage.py）
  - ✅ 移除所有硬编码路径，使用相对路径
  - ✅ 已清理所有过时的 SECURITY FIX 注释
  
  【数据导出安全】
  - ✅ 白名单模式（仅允许 MEMORY.md, persona.md）
  - ✅ 自动脱敏 API 密钥、密码、token
  - ✅ 文件大小限制（1MB）
  - ✅ 时间范围限制（仅最近 7 天的每日记录）
  
  【其他安全措施】
  - ✅ **不内置任何 API 密钥或凭据**（已验证）
  - ✅ 所有 API 端点从配置文件或环境变量读取
  - ✅ 使用参数化查询防止 SQL 注入
  - ✅ 不自动安装 cron 任务
  
  🔒 v6.1.0：BlobArena 无损存储 + ONNX bge-small-zh-v1.5 中文原生嵌入 + RetrievalHub 7通道全链路 + ANNSelector v2 + GNN Graph Builder + ~140 模块全同步。
---

# LLM Memory Integration v6.1.0

> BlobArena 无损存储 | ONNX bge-small-zh 中文嵌入 | RetrievalHub 7通道检索
> 138 模块 | 395+ 能力项 | 融合置信度 0.93

## ⚠️ 安全须知

- **本技能会读写本地文件**（向量库、记忆文件、日志）
- **不内置任何 API 密钥** — 所有凭据从 `config/llm_config.json` 读取（．gitignore 保护）
- **用户画像默认关闭** — `auto_update: false`，更新前需用户确认
- **数据导出白名单** — 仅 MEMORY.md / persona.md，自动脱敏，≤1MB

## ✨ 核心特征 (v6.1)

| 特征 | 说明 |
|------|------|
| **BlobArena v2** | mmap 无损存储，DAG 节点不再 512/2000 字符硬截断 |
| **ONNX bge-small-zh** | 中文原生 512d 嵌入，~42ms/embed |
| **RetrievalHub 7通道** | KG + Local + DAG + Synapse + Paper + Cognitive + Web |
| **MN-RU 双索引** | siliconflow BAAI/bge-m3 1024d，3879 节点 |
| **ANNSelector v2** | FAISS 动态索引，<5000 自动 HNSW |
| **GNN Graph Builder** | GraphSAGE/GAT/GCN 三卷积层 |
| **~140 模块同步** | scripts_core / integration / memory / rails / privileged / api |

## 🏗️ 技术架构

```
用户查询
    ↓
query_preprocess → intent / complexity / rewrite
    ↓
┌─ RetrievalHub (7 通道并行) ─────────────────────┐
│  KG (temporal_kg GNN)                            │
│  Local (XiaoyiClawLLM.recall + dag_fallback)     │
│  DAG (MN-RU bge-m3 siliconflow 双索引)           │
│  Synapse (jieba → bge-small-zh ONNX → GAT → CfC)│
│  Paper (arXiv / s2)                              │
│  Cognitive (MN-RU 三通道: Mental/Relational/Unconscious)│
│  Web (联网搜索)                                  │
└───────────────────────────────────────────────────┘
    ↓
RRF v2 融合 → neural rerank (jaccard去重)
    ↓  dedup → CRAG 分解 → quality assessment
    ↓
merged_context (置信度加权) → 主模型
```

## 🔧 核心模块

### 检索层 (Layer 2)
| 模块 | 功能 |
|------|------|
| `retrieval_hub.py` | 7通道并行检索入口 + RRF v2 + neural rerank + quality assessment |
| `hybrid_search.py` | 混合检索 (Dense + Sparse + RRF) |
| `crag.py` | CRAG 动态纠错 + Self-RAG 预测器 |
| `dynamic_crag_threshold.py` | CRAG 自适应阈值 |
| `self_rag.py` | Self-RAG 实现 (isrel/issup/isuse) |

### 向量存储 (Layer 3)
| 模块 | 功能 |
|------|------|
| `onnx_embedding.py` | ONNX Runtime bge-small-zh (512d, ~42ms) |
| `unified_vector_store.py` | 统一向量存储 (FAISS/FAISSIndex) |
| `ann_selector.py` | ANN 索引自动选择 (HNSW/IVF/Flat) |
| `embedding_enhance.py` | bge-m3 在线 embedding 增强 |
| `vector_store.py` / `vector_api.py` | 四级搜索降级链 + 统一接口 |

### 记忆核心 (Layers 0-1)
| 模块 | 功能 |
|------|------|
| `hallucination_guard.py` | 防幻觉 10 重检测 |
| `memory_consolidation.py` | 记忆巩固引擎 (CLS + 重放 + 干扰合并) |
| `memory_synapse_network.py` | 突触网络 (ncps) |
| `cognitive_map.py` | MN-RU 三通道认知映射 |
| `emotion_memory.py` | 情感记忆 (Ekman 6 情绪) |
| `kora_behavior.py` | KoRa 行为建模 |
| `biorhythm_sleep_consolidation.py` | 生物节律睡眠巩固 |

### DAG 上下文 (Layer 9)
| 模块 | 功能 |
|------|------|
| `dag_context_manager.py` | DAG 上下文管理器 (v2, BlobArena 无损) |
| `blob_arena.py` | mmap 无损 blob 存储 (替代字符截断) |
| `DAGIntegration_addon.py` | DAG × GalaxyOS 三维绑定 |

### 智能处理 (Layer 4)
| 模块 | 功能 |
|------|------|
| `smart_processor.py` | 统一路由 (Flash/Pro/VLM 三通道) |
| `thinking_enhanced.py` | 增强思考引擎 |
| `intelligent_thinking_trigger.py` | 智能思考触发 |
| `nlp_enhanced.py` | 增强 NLP (jieba + 实体 + 情感) |

### 神经网络模块
| 模块 | 功能 |
|------|------|
| `neural_pipeline.py` | GNN + CfC 神经管道 |
| `cfc_sequence_predictor.py` | CfC 序列预测 (AutoNCP) |
| `gnn_graph_builder.py` | GNN 图构建 (GraphSAGE/GAT/GCN) |
| `gat_layer.py` | GAT 层实现 |
| `graphsage_layer.py` | GraphSAGE 层实现 |
| `graph_constructor.py` | 图构造器 (kNN/ε-半径) |
| `cnn_graph_builder.py` | 图神经网络图构建器 |

### 系统可靠性 (Layer 8)
| 模块 | 功能 |
|------|------|
| `enhanced_hallucination_guard.py` | 增强防幻觉 |
| `resilience_system.py` | 弹性系统: 自我修复 + 故障转移 |
| `failover.py` | 故障转移 |
| `rules_manager.py` | Rails 护栏 |

## ⚡ 性能指标

| 指标 | 值 |
|------|------|
| ONNX embedding | ~42ms/run (bge-small-zh) |
| RetrievalHub cold start | ~12s (138 模块 + 3879 DAG 节点索引重建) |
| MN-RU retrieval | 3879 节点 via siliconflow bge-m3 (1024d) |
| BlobArena read | mmap O(1) 随机访问 |
| 融合置信度 | 0.93 (DAG 8 + local 2) |
| 模块加载 | 138 模块全加载 |

## 🚀 快速使用

### 检索
```python
from retrieval_hub import retrieval_hub
result = retrieval_hub("上海旅游攻略", top_k=8, include_web=False)
# → 7通道并行 → RRF融合 → 8条结果, 置信度 0.93
```

### 嵌入
```python
from onnx_embedding import get_onnx_embedding
svc = get_onnx_embedding()
svc.initialize()
vec = svc.embed_query("上海旅游攻略")  # 512d float32
# 中文语义: 上海↔迪士尼 0.739, 上海↔北京 0.500, 上海↔Python 0.255
```

### 记忆存储
```python
from memory_consolidation import ConsolidationEngine
ce = ConsolidationEngine()
ce.consolidate("重要事件", importance=0.8)
```

### 认知查询
```python
from cognitive_map import CognitiveMap
cm = CognitiveMap()
queries = cm.run_cognitive_queries("上海旅游攻略")
# Mental / Relational / Unconscious 三通道
```

## ⚙️ 配置

配置文件: `config/llm_config.json` (．gitignore 保护, 不上传仓库)

```json
{
  "embedding": {
    "provider": "siliconflow",
    "base_url": "https://api.siliconflow.cn/v1",
    "api_key": "YOUR_API_KEY",
    "model": "BAAI/bge-m3"
  },
  "llm": {
    "provider": "openai-compatible",
    "base_url": "https://api.deepseek.com",
    "api_key": "YOUR_API_KEY",
    "model": "deepseek-chat"
  }
}
```

**本地 ONNX 嵌入** (`onnx_embedding.py`): 自动加载 `models/embeddings/bge-small-zh.onnx`, 无 API 调用, 零网络依赖。

## 📦 依赖

| 依赖 | 用途 |
|------|------|
| onnxruntime | ONNX 推理引擎 |
| onnx | ONNX 模型加载 |
| faiss-cpu | 向量索引 (HNSW/IVF) |
| torch | CfC/GNN 神经网络 |
| ncps | Liquid CfC / AutoNCP |
| openai | LLM / Embedding API 客户端 |
| sqlite-vec | 向量数据库 |
| hnswlib | HNSW 索引 |
| jieba | 中文分词 |

---
*小艺 Claw — OpenClaw 核心底层能力引擎*
*版本: v6.1.0 | 架构: 15层 + 4插件 | 贡献: xkzs2007*
