# Changelog

## [v8.4.0] — 2026-06-23

### Added
- **OKF `generate` 子命令** — 自动从 `MODULE_REGISTRY` 生成模块 concept（144 模块，13 层），按层分组含 cross-link + index.md
- **`resource` 字段** — concept 前件带源路径（`file://workspace/` / `galaxyos://module/` / `galaxyos://skill/`）
- **bundle 目录拆分** — `concepts/modules/`、`concepts/skills/`、`concepts/system/`
- **OKF → SkillGraph 协同** — ingest 时 type=Skill 的概念自动编译到 SkillGraph（`SkillCompiler.compile()` → `AssetRegistry.register()` → `SkillGraph.add_node()` → 持久化），实现图感知检索
- **sync 集成 generate** — Step 3.5 自动生成模块概念后推 CNB
- 安装向导 `--setup-git` 子命令
- `openclaw.plugin.json` 描述 v4→v8，119→144 模块

### Changed
- `package.json` 版本统一为 `8.4.0`
- `VERSION` 统一为 `v8.4.0`
- 规范版本号格式（遵守 VERSIONING.md）

### Fixed
- SkillGraph 中文长句匹配（补 cn→cn 路径）
- 拓扑排序保留种子得分
- 全局单例持久化

### 已实现论文模块（v8.4 前已完成）
- **SkillGraph** (`arXiv:2605.12039`) — 技能依赖图 + RL 信号共进化，SkillGraph 已集成到 R-CCAM 检索链路，SkillCompiler/AssetRegistry/SkillGraph 全链路已部署
- **SDPG** (`arXiv:2606.04036`) — Self-Distilled Policy Gradient，RLVR + self-distillation 策略优化
- **GraphWalker** (`arXiv:2603.28533`) — Agentic KGQA 合成轨迹学习，两阶段 SFT + 轻量 RL
- LASAR Latent Cognitive Map (`arXiv:2605.16899`) — 认知地图嵌入 R-CCAM
- AriGraph (`arXiv:2407.04363`) — 空间拓扑记忆检索
- Engram (`arXiv:2601.07372`) — DeepSeek 式记忆印记 + U 型缩放律
- LFM (`arXiv:2409.20308`) — Liquid AI 基础模型自适应算子
- MemoryOS (`arXiv:2506.06326`) — 热度跟踪 + 分段页式存储
- SKILL0 (`arXiv:2604.02268`) — 技能课程学习
- KAN (`arXiv:2404.19756`) — Kolmogorov-Arnold 网络嵌入
- Self-RAG / CRAG (`arXiv:2310.11511` / `arXiv:2401.15884`) — 检索增强生成
- Neural ODE (`arXiv:1806.07366`) — 连续时间神经微分方程
- 及其他 20+ 论文实现（Titans、Mamba3、LiquidSSM、SSM-KAN、MoE-Engram 等 v8.1 液态神经网络模块）

## [8.3.0] - 2026-06-18
### Added
- **Open Knowledge Format (OKF) 集成**: 新增 `galaxyos_okf.py` 三层整合工具
  - `export`: 扫描 workspace 系统文件 + skills 导出为 OKF Knowledge Bundle（244 concepts）
  - `ingest`: 消费 OKF bundle，索引到 knowledge_assets 供检索
  - `verify`: 验证 bundle 结构和 concept 合法性
  - 输出目录: `var/okf-bundles/`, `var/okf-index/`, `var/knowledge_assets/`

### Changed
- **LFM2.5-1.2B: torch bf16 → ONNX Runtime Q4**
  - 模型大小从 2.2GB safetensors 降为 811MB ONNX（Q4 量化）
  - ONNX Runtime mmap 加载，多进程共享物理页
  - 内存占用：双 worker 物理增量从 ~2.2GB 降为 ~0.8GB
  - 启动速度：秒级 vs torch 全家桶十几秒
  - embed_text: `present_conv.15` mean pooling 替代 `hidden_states[-1]`
  - Tokenizer: `tokenizers.Tokenizer.from_file()` 替代 `AutoTokenizer.from_pretrained()`
- **安装向导**：--download-lfm 从下载 2.2GB safetensors 改为 811MB ONNX Q4
- **setup.py**: 补 `tokenizers>=0.20.0`、`transformers>=4.44.0`，去重 psutil
- **claw_worker.py**: 预加载日志同步更新

### Added
- **兼容迁移**: check_lfm_weights 识别旧版 safetensors 并提示迁移到 ONNX

## [8.2.12] - 2026-06-18
### Added
- 198 个文件提交同步（包含 V81 神经网络 embedding + 论文实现 33 模块 + 全链路修复）

### Fixed
- 测试套件修复：9 个失败的测试用例修正
