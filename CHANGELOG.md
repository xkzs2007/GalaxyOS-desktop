# Changelog

## [8.2.9] - 2026-06-15
### Fixed
- **Consolidation 12 个 phase 全部通过**: 修复 `_load()` 调用路径 (`net._load()` → `net.network._load()`)、Python 作用域 (`_np` import 前使用、`prune_stats` 跨 try 块)、类型转换 (Titans embed_text 返回 list 非 ndarray)、CognitiveMap 旧 DB 256 维冲突、MemorySynapseNetwork 缺 `synapses_path` 转发、EngramMemory 缺 `get_hit_rate` 转发
- **CognitiveMap**: numpy truth value 歧义修复 (`bool(cache)` → `len(cache) == 0`)
- **MemorySynapseNetwork**: 补齐 `_load()`, `_neurons_cache`, `_synapses_cache`, `synapses_path`, `neurons_path` 属性转发
- **EngramMemory**: 补齐 `get_hit_rate()` 方法

### Added
- **Titans 持久化路径**: `titans_neural_memory.py` 添加 `persist_state()`/`load_state()` 持久化能力

## [8.2.8] - 2026-06-15
### Fixed
- **Phase 12 DreamDrivenLearner 属性名**: `workspace` → `workspace_path`

## [8.2.7] - 2026-06-15
### Added
- **Consolidation Phase 9-12**: 四块神经记忆 (ReplayBuffer/NeuralMemoryGate/AdaptivePruner/DreamDrivenLearner) 接入 `_run_consolidation_cycle()`

## [8.2.6] - 2026-06-15
### Fixed
- **lfm_full_integration.py**: `moe_engram` 命名一致性修复

## [8.2.5] - 2026-06-15
### Added
- **LFM 全链路 14 模块集成**: ODE-RNN、Neural ODE、KAN、LTC、MoE、SSM(LiquidSSM)、Lipschitz、Sparsity、Edge、NCD、DAG 全部桥接到 `lfm_full_integration.py`

## [8.2.4] - 2026-06-15
### Added
- **LFM→Engram→LiquidWeight→SSM 全链路集成**: engram 融合写入、liquid_weight gated retrieval、liquid_ssm 时序编码、cross_modal 绑定、unified_coordinator 注册

## [8.2.3] - 2026-06-15
### Changed
- **删除 deepseek-ocr2 模块**: 全链路切换至 Qwen3-VL-8B-Instruct VLM
- **SKILL.md/paper-roadmap.md**: 同步更新移除 deepseek-ocr2
- **enhanced_hallucination_guard/smart_processor**: VLM 接口替换

## [8.2.2] - 2026-06-15
### Changed
- **LFM 模型预加载常驻 Worker**: claw_worker.py 启动时加载 LFM，避免每次调用重新加载 2.2GB

## [8.2.1] - 2026-06-15
### Added
- **自动循环集成**: TitansNeuralMemory、CrossModalMemoryBinder 挂入 `ConsolidationEngine._run_consolidation_cycle()` Phase 4+5
- **睡眠周期集成 Titans**: run_full_sleep_cycle() 新增 Phase 7，睡眠结束后自动 store 状态摘要到神经记忆
- **DAG 固化 embedding**: consolidate_from_dag() 创建神经元时走 CrossModal.text_to_embedding() 生成真实 2048 维 embedding
- **安装向导 v8.2**: install_wizard.py 新增 `check_v82_modules()` 验证 Titans/CrossModal/DreamDriven/AdaptivePruner 模块导入 + 持久化目录 + 自动循环集成状态；更新报告包含 v8.2 模块健康分

### Changed
- **版本号**: v8.2.0 → v8.2.1

## [8.2.0] - 2026-06-15
### Added
- **自适应突触修剪 (AdaptiveSynapsePruner)**: 从单阈值(0.3)一刀切升级为多因子保留分数(权重×0.30+频率×0.25+时效×0.20+情感×0.10+重要度×0.15)，阈值自动从分布统计(mean−0.5×std)计算，下限保护 MIN_RETENTION=0.15。集成到 ConsolidationEngine 的背景周期第三阶段
- **Titans 神经记忆模块 (TitansNeuralMemory)**: 2048-dim 在线记忆向量，遗忘门/更新门双门控，每次 store() 直接更新无需空闲等待。持久化到 .learnings/titans_memory/neural_memory.json
- **跨模态记忆绑定 (CrossModalMemoryBinder)**: 文本→LFM 2048 embedding、图像→VisualPatchEmbedding→投影2048、caption桥接路径兜底。统一多模态检索空间
- **梦境驱动学习 (DreamDrivenLearner)**: 2048×2048 adapter 参数(4M)，梦境碎片→对比学习训练，每次睡眠周期末尾自动触发。持久化到 .learnings/dream_learning/dream_adapter.npy
- **梦境学习集成**: biorhythm_sleep_consolidation run_full_sleep_cycle 末尾 Phase 6 自动挂载

### Changed
- **版本号**: v8.1.4 → v8.2.0

## [8.1.4] - 2026-06-15
### Added
- **RealLFMNetwork.embed_text()**: 真实模型隐状态 mean-pooling，返回 2048 维 float32 向量
- **ONNX bge-small-zh 路径修复**: 新增运行时候选目录 galaxyos/models/embeddings
- **requirements.txt**: 新增 `transformers>=4.44.0`（LFM 模型 `AutoModelForCausalLM` 依赖）
- **install_wizard.py**: `check_torch_stack()` 新增 `transformers` 包检测项

### Fixed
- **xiaoyi_claw_api.py**: 移除 `full_integration`、`speculative_hybrid`、`full_recovery` 三个已删除文件的悬挂引用，替换为降级实现
- **v4_services.py**: `_fast_generation` 移除 `SmartHybridGenerator` 依赖，改为纯 cache recall

## [8.1.3] - 2026-06-15
### Added
- V81IntegrationAddon 全链路推理启用，四条管线从随机 np 替换为真实文本 embedding
- 下游模块维度统一 2048: Mamba3/LiquidSSM/SSM-KAN/NeuralODE/ODE-RNN/MoE-Engram
- embedding 三条通道统一 2048 维出口（ONNX 512→padding, LFM 原生, MD5 fallback）

## [8.1.2] - 2026-06-14
### Added
- 液态神经网络模块部署（18+ 模块）：Mamba3/LiquidSSM/SSM-KAN/NeuralODE/ODE-RNN/MoE-Engram
- 2048 维统一 embedding 出口
- RealLFMNetwork 共享实例：LFM2.5-1.2B 2.2GB bf16

## [8.1.1] - 2026-06-13
### Fixed
- 4 条悬挂引用清理

## [8.1.0] - 2026-06-11
### Added
- DAG 上下文管理器集成
- ContextEngine 接入 OpenClaw 管道
- Lobster 管道 arg 环境变量支持

## [8.0.0] - 2026-05-02
### Added
- OpenClaw 全量集成：44 工作流 / 6 lobster / 2 Hook / 双通道 llm_config
- 三层记忆接口统一改造
- 投机解码 KV Cache 激活集成

## [8.2.12] - 2026-06-18
### Added
- 198 个文件提交同步（包含 V81 神经网络 embedding + 论文实现 33 模块 + 全链路修复）

### Fixed
- setup.py 版本同步（8.2.9 → 8.2.12）
