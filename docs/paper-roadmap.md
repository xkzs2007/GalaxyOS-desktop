# GalaxyOS 论文集成路线图

> 液态神经网络 + 条件记忆 + 动态计算 — 面向连续时间智能的论文实现全景
>
> 最后更新: 2026-06-14

---

## 目录

1. [已实现的论文模块](#1-已实现的论文模块)
2. [液神经核心方向（✅ 全部实现）](#2-液神经核心方向待实现)
3. [条件记忆/稀疏计算方向（✅ 全部实现）](#3-条件记忆稀疏计算方向待实现)
4. [KAN/动态函数逼近方向（✅ 全部实现）](#4-kan动态函数逼近方向待实现)
5. [LFM 工业落地方向（✅ 全部实现）](#5-lfm-工业落地方向待实现)
6. [现有模块增强（✅ 全部实现）](#6-现有模块增强待实现)
7. [融合方向](#7-融合方向)
8. [优先级矩阵](#8-优先级矩阵)
9. [实现规划](#9-实现规划)

---

## 1. 已实现的论文模块

| # | 论文 | 对应模块 | 状态 |
|---|------|---------|------|
| 1 | LTC – Liquid Time-Constant Networks (Hasani, AAAI 2021 / arXiv:2006.04439) | `ltc_synapse.py` (356 行) | ✅ 基础突触实现 |
| 2 | CfC – Closed-form Continuous-time Neural Networks (Hasani, Nature MI 2022 / arXiv:2206.01967) | `cfc_inference.py` (967 行) | ✅ 推理引擎 + NCP Wiring |
| 3 | CfC 序列预测 – 多层 CfC 记忆序列预测 | `cfc_sequence_predictor.py` (1421 行) | ✅ |
| 4 | NCP – Neural Circuit Policies (Lechner, Hasani, Nature MI 2020) | 嵌入 `cfc_inference.py` | ✅ |
| 5 | SSM 状态预测 – Mamba/SSM 启发的记忆热度预测 | `ssm_state_predictor.py` (526 行) | ✅ 轻量实现 |
| 6 | Neural Pipeline – GNN + CfC + LiquidWeight 基准测试 | `neural_benchmark.py` (447 行) | ✅ |
| 7 | RLM – Reasoning via Language Models (arXiv:2512.24601) | `rlm_env.py` | ✅ |
| 8 | SKILL0 – 技能课程 (arXiv:2604.02268) | `skill_curriculum.py` | ✅ |
| 9 | MemoryOS – 记忆操作系统 (arXiv:2506.06326) | `memory_os.py` | ✅ |
| 10 | Self-RAG – 自我反思检索增强生成 (arXiv:2310.11511) | `self_rag.py` | ✅ |
| 11 | CRAG – Corrective RAG (arXiv:2401.15884) | `crag.py`, `dynamic_crag_threshold.py` | ✅ |
| 12 | CoVe – Chain-of-Verification (arXiv:2309.11495) | `chain_of_verification.py` | ✅ |
| 13 | AutoPromptOptimizer – ProTeGi (arXiv:2305.03495) | `auto_prompt_optimizer.py` | ✅ |
| 14 | LASAR – Latent Cognitive Map (arXiv:2605.16899) | 嵌入 `paper_integration.py` | ✅ |
| 15 | AriGraph – 空间拓扑场景图 (arXiv:2407.04363) | `paper_integration.py` | ✅ |
| **小计** | **33 个已集成** | | **✅ 全部可用** |

---

## 2. 液神经核心方向（✅ 全部实现）

| # | 论文 | 关键思想 | 优先级 | 说明 |
|---|------|---------|--------|------|
| **P1** ✅ | **LTC-SE** – Expanding the Potential of Liquid Time-Constant Neural Networks for Scalable AI and Embedded Systems (Bidollahkhani, arXiv:2304.08691) | LTC + LIF + CTRNN + NODE + GRU 五合一统一框架，嵌入式优化 | ⭐⭐⭐ | 将现有 `ltc_synapse.py` 扩展为统一框架 |
| **P2** ✅ | **LGTC** – Liquid-Graph Time-Constant Network for Multi-Agent Systems Control | GNN + LTC 结合，连续时间图控制 | ⭐⭐⭐ | 需要 GNN 支持，融合到 `gnn_graph_builder.py` 管线 |
| **P3** ✅ | **Neural ODE** – Neural Ordinary Differential Equations (Chen, Rubanova, Bettencourt, NeurIPS 2018 / arXiv:1806.07366) | ODE 求解器替代离散层，伴随法反向传播 | ⭐⭐⭐ | LTC/CfC 的数学底座，缺了这个就缺了根 |
| **P4** ✅ | **ODE-RNN** – 带记忆增强 Neural ODE 的持续学习 (Scientific Reports 2025 / s41598-025-31685-9) | Neural ODE + memory-augmented transformer 防灾难遗忘 | ⭐⭐ | 与 MemoryOS 结合解决持续学习问题 |
| **P5** ✅ | **NCD** – 神经闭式微分 (CfC 的严格闭式解形式化) | CfC 之后的更严格闭式解逼近，提升精度 | ⭐⭐ | LTC 理论提升方向 |
| **P6** ✅ | **Lipschitz Liquid** – 带 Lipschitz 约束的液体单元 | 稳定训练，可控动态边界 | ⭐⭐ | 工程稳定性改进 |
| **P7** ✅ | **液体 SSM** – Liquid Structural State Space Model (连续时间 SSM 变体) | 融合 Mamba 选择机制 + LTC 连续动态 | ⭐⭐ | Mamba-3 的复数值/MIMO SSM 方向可作为子方向 |
| **P8** ✅ | **Mamba-3 SSM** – 复数值 + MIMO 状态更新新一代 SSM | 比 Mamba-2 更强表现力，状态追踪能力 | ⭐⭐ | 配合液体 SSM |

---

## 3. 条件记忆/稀疏计算方向（✅ 全部实现）

| # | 论文 | 关键思想 | 优先级 | 说明 |
|---|------|---------|--------|------|
| **P9** ✅ | **Engram** – Conditional Memory via Scalable Lookup: A New Axis of Sparsity for LLMs (Cheng et al., DeepSeek, arXiv:2601.07372, 2026.1) | N-gram 嵌入 → O(1) 有条件查找，MoE 之外的稀疏新维度 | ⭐⭐⭐ | **最热点论文**，直接关联记忆系统，DeepSeek 出品 |
| **P10** ✅ | **MoE + Engram 融合** – 稀疏分配 U 型缩放律 | MoE（条件计算）+ Engram（条件记忆）联合分配优化 | ⭐⭐ | Engram 论文中发现 U 型缩放定律，可独立实现 |
| **P11** ✅ | **条件计算/条件记忆统一视图** – MoE × 条件记忆 × 稀疏注意 | 梳理稀疏性设计空间，建立统一框架 | ⭐ | Survey 类，低工程量，高理解价值 |
| **P12** ✅ | **Memory-Augmented Transformer + ODE 持续学习** (同上 P4) | 记忆增强 Transformer 配合 Neural ODE 处理持续学习 | ⭐⭐ | 与 P4 共享 |

---

## 4. KAN/动态函数逼近方向（✅ 全部实现）

| # | 论文 | 关键思想 | 优先级 | 说明 |
|---|------|---------|--------|------|
| **P13** ✅ | **KAN** – Kolmogorov-Arnold Networks (Liu et al., 2024 / arXiv:2404.19756) | 激活函数可学习的网络，可替代 MLP | ⭐⭐⭐ | 与 LTC 的微分方程结合，替换传统 MLP 激活 |
| **P14** ✅ | **ss-Mamba** – Semantic-Spline SSM + KAN (2025) | KAN + SSM 融合，动态捕获复杂时序模式 | ⭐⭐ | KAN 与 SSM 的实用结合 |

---

## 5. ⚠️ 已删除 — LFM 工业落地方向（原 ✅ 全部实现，LFM 模块已从 v0.3.0 移除）

| # | 论文/项目 | 关键思想 | 优先级 | 说明 |
|---|---------|---------|--------|------|
| **P15** ✅ | **LFM-1.0** – Liquid Foundation Models (Liquid AI, 2024.10) | 自适应线性算子替代自注意力，非 Transformer 架构，1.3B/3.1B/40.3B | ⭐⭐⭐ | LTC/CfC 的工业化产品，无 KV Cache 瓶颈 |
| **P16** ✅ | **LFM-2.0 / 2.5** – LFM 进化和端侧推理 (Liquid AI, 2025-2026) | 端侧推理模型，900MB 内存运行，工具调用 + 指令遵循 | ⭐⭐⭐ | LFM2.5 有 Thinking 变体，本地部署友好 |
| **P17** ✅ | **LFM + Engram 融合** – 有条件记忆的液体基础模型 | LFM 自适应线性算子（动态建模）+ Engram O(1) 查找（静态知识）= 能查知识的液体模型 | ⭐⭐⭐ | **高价值融合方向**，两条路互不干扰 |
| **P18** ✅ | **LFM-VL** – LFM 多模态视觉-语言模型 | 像素解混减少 token，原生分辨率输入 | ⭐⭐ | 多模态扩展参考；当前 VLM 已切换为 Qwen3-VL-8B-Instruct（硅基流动） |

---

## 6. 现有模块增强（✅ 全部实现）

| # | 方向 | 描述 | 优先级 | 说明 |
|---|------|------|--------|------|
| **P19** ✅ | **Liquid Weight** – 液态权重记忆融合 | 从 `neural_benchmark.py` 提升为独立模块 | ⭐⭐ | ✅ 完整模块 |
| **P20** ✅ | **GNN + Liquid 时间图** | 时序图网络的连续时间 GNN，LGTC 的完整模块化 | ⭐⭐ | 配合 P2 |
| **P21** ✅ | **DAG + Liquid 融合** | DAG 上下文管理器引入液体时间常数，compact 时间感知 | ⭐ | ✅ 完整模块 |
| **P22** ✅ | **Engram + MemoryOS 融合** | Engram 的 O(1) 查找代替 MemoryOS 的部分热度计算 | ⭐⭐⭐ | **直接落地**，MemoryOS 已有热度跟踪 |

---

## 7. 融合方向

### 7.1 Engram + LTC 融合（最高价值）

```
输入序列
  │
  ├─→ Engram O(1) 查找 ─→ 静态知识（N-gram 模式匹配）
  │
  └─→ LTC/CfC ODE 求解 ─→ 动态建模（液体时间常数）
        │
        └─→ 门控融合 ←─ Engram 结果作为门控输入
              │
              └─→ 输出
```

**效果**：
- LTC 不用分精力"记住"固定模式，专注时序动态建模
- Engram 的静态知识通过液体门控注入——时间常数决定记忆"黏性"
- 快速响应时少依赖记忆，平稳推理时多依赖记忆

### 7.2 LFM + Engram 融合（扩展）

```
LFM 自适应线性算子（怎么想）+ Engram 条件记忆（知道什么）
  = 能查知识的液体模型
```

- LFM 处理序列靠算子对输入的动态响应
- Engram 补充知识检索缺口
- 没有 KV Cache 瓶颈 + O(1) 查找 = 长序列 + 大批量场景优势

### 7.3 三维稀疏：MoE ✕ Engram ✕ LTC

| 维度 | 稀疏方式 | 代表技术 | 作用 |
|------|---------|---------|------|
| **Compute** | 条件计算 | MoE | 决定用哪些专家 |
| **Memory** | 条件记忆 | Engram | 决定查不查记忆 |
| **Time** | 条件时间 | LTC/CfC | 决定响应速度 |

三层加起来，理论上能用更少的计算干更多的事。

---

## 8. 优先级矩阵

| 优先级 | 方向 | 代码量估计 | 依赖关系 | 建议批次 |
|--------|------|-----------|---------|---------|
| ⭐⭐⭐ P1 | LTC-SE 统一框架 | 中 (600-800 行) | 无，在已有 ltc_synapse 上扩展 | **第一批** |
| ⭐⭐⭐ P3 | Neural ODE 底座 | 中 (500-700 行) | 无 | **第一批** |
| ⭐⭐⭐ P9 | Engram 条件记忆 | 中高 (800-1200 行) | 需要 hash/N-gram 工具 | **第一批** |
| ⭐⭐⭐ P13 | KAN | 中 (400-600 行) | 无 | **第一批** |
| ⭐⭐⭐ P15 | LFM 自适应算子 | 高 (1000-1500 行) | 需要 P3 (Neural ODE) 基础 | **第二批** |
| ⭐⭐⭐ P17 | LFM + Engram 融合 | 中 (300-500 行) | 需要 P9 + P15 | **第三批** |
| ⭐⭐ P2/P20 | LGTC | 中 (600-800 行) | 需要 P1 + GNN | **第二批** |
| ⭐⭐ P4/P12 | ODE-RNN + 记忆增强 | 中 (500-700 行) | 需要 P3 | **第二批** |
| ⭐⭐ P10 | MoE/Engram 缩放律 | 低 (200-400 行) | 需要 P9 | **第三批** |
| ⭐⭐ P22 | Engram + MemoryOS | 中 (300-500 行) | 需要 P9 | **第一批优先** |
| ⭐ | P11 统一视图 | 低 (文档为主) | 无 | 随时 |

---

## 9. 实现规划

### 第一批（高优先级，无外部依赖）

```
P3  Neural ODE        ─── LTC/CfC 数学底座
P9  Engram             ─── 条件记忆，最热论文
P13 KAN                ─── 可学习激活函数
P1  LTC-SE             ─── 液神经统一框架
P22 ✅ Engram + MemoryOS  ─── 直接落地融合
```

### 第二批（需要第一批基础/中等复杂度）

```
P15 ✅ LFM 自适应算子     ─── 依赖 P3
P4 ✅ ODE-RNN            ─── 依赖 P3
P2 ✅ LGTC               ─── 依赖 P1
P16 ✅ LFM 端侧推理        ─── 依赖 P15
```

### 第三批（融合/优化方向）

```
P17 ✅ LFM + Engram       ─── 依赖 P9 + P15
P10 ✅ MoE/Engram U 型缩放 ─── 依赖 P9
P8 ✅ Mamba-3 SSM        ─── 依赖 P3 的 ODE 理解
P21 ✅ DAG + Liquid       ─── ✅ 已完成
```

---

## 参考链接

| 论文 | ArXiv / DOI |
|------|-------------|
| LTC | https://arxiv.org/abs/2006.04439 |
| CfC | https://arxiv.org/abs/2206.01967 |
| NCP | https://www.nature.com/articles/s42256-020-00237-z |
| LTC-SE | https://arxiv.org/abs/2304.08691 |
| Neural ODE | https://arxiv.org/abs/1806.07366 |
| Engram | https://arxiv.org/abs/2601.07372 |
| KAN | https://arxiv.org/abs/2404.19756 |
| Self-RAG | https://arxiv.org/abs/2310.11511 |
| CRAG | https://arxiv.org/abs/2401.15884 |
| CoVe | https://arxiv.org/abs/2309.11495 |
| RLM | https://arxiv.org/abs/2512.24601 |
| SKILL0 | https://arxiv.org/abs/2604.02268 |
| MemoryOS | https://arxiv.org/abs/2506.06326 |
| LASAR | https://arxiv.org/abs/2605.16899 |
| AriGraph | https://arxiv.org/abs/2407.04363 |
| ProTeGi (APO) | https://arxiv.org/abs/2305.03495 |
| ODE-RNN (持续学习) | https://www.nature.com/articles/s41598-025-31685-9 |
| LFM | https://www.liquid.ai (Liquid AI) |
| ss-Mamba + KAN | arXiv 2025 |
| LFM2-VL | Liquid AI 2025 |
| LFM2.5 | Liquid AI 2026 |
