# 🦞 小艺 Claw — LLM Memory Integration

> OpenClaw 核心底层能力引擎 | 138 模块 | 7通道检索 | 395+ 能力项

## ✨ 核心能力

| 能力 | 技术栈 | 说明 |
|------|--------|------|
| **中文嵌入** | ONNX bge-small-zh (512d) | 本地 ~42ms/embed，零网络依赖 |
| **7通道检索** | KG + Local + DAG + Synapse + Paper + Cognitive + Web | RRF v2 → neural rerank → quality assessment |
| **无损存储** | BlobArena mmap | 替代 DAG 512/2000 字符硬截断 |
| **MN-RU 索引** | BAAI/bge-m3 via siliconflow | 3879 节点，双索引结构 |
| **神经网络** | GAT / GraphSAGE / GCN + CfC AutoNCP | 突触网络 + 序列预测 |
| **DAG 上下文** | 有向无环图 | 增量压缩 + 降级摘要 + 完整回溯 |
| **防幻觉** | 10 重检测 + Self-RAG + 多Agent验证 | 自适应阈值 |
| **认知映射** | MN-RU 三通道 | Mental / Relational / Unconscious |

## 🏗️ 检索流程

```
查询 → query_preprocess → RetrievalHub (7通道并行)
                                    ↓
KG ───→ temporal_kg (三元组 GNN)
Local ─→ sqlite-vec (dense 向量)
DAG ───→ MN-RU bge-m3 siliconflow (双索引)
Synapse→ bge-small-zh ONNX → GAT → CfC
Paper ─→ arXiv / s2
Cognitive→ MN-RU (Mental / Relational / Unconscious)
Web ───→ 联网搜索 (RRF 之外)
                                    ↓
RRF v2 → neural rerank → dedup → CRAG → 质量评估
                                    ↓
                              merged_context
```

## ⚡ 性能

- ONNX 嵌入: ~42ms/run
- 7通道检索冷起: ~12s (含 138 模块加载 + 3879 节点索引重建)
- 融合置信度: 0.93
- 模块全加载: 138/138

## 📦 安装

```bash
openclaw plugins install claw-core
```

## 🔧 配置

```json
{
  "embedding": {
    "provider": "siliconflow",
    "base_url": "https://api.siliconflow.cn/v1",
    "api_key": "YOUR_API_KEY",
    "model": "BAAI/bge-m3"
  }
}
```

## 📜 许可

MIT-0 — 无任何限制，可自由使用、修改、分发。

---

*维护者: xkzs2007 | 仓库: https://cnb.cool/llm-memory-integrat/GalaxyOS*
