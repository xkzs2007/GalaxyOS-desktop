# Changelog

GalaxyOS 版本变更记录。

## [6.1.0] — 2026-06-07

### Added
- 测试套件：32 文件, 428 用例, 3706 行测试代码
- CI/CD：4 GitHub Actions workflows (test, lint, security scan)
- Docker 支持：Dockerfile + docker-compose.yml + .dockerignore
- 安全加固：.gitleaks.toml + pre-commit hooks + CI 密钥扫描
- 安装向导增强：services/ 模块检查 + pip 依赖验证 + --test/--deps 模式
- 开发工具：Makefile + pyproject.toml (pytest/coverage/ruff/mypy)
- CONTRIBUTING.md / CHANGELOG.md / docs/API.md

### Changed
- God Object 解耦：xiaoyi_claw_api 4529→4291 行
- 提取 3 个独立模块：_imports.py / rccam_state.py / claw_helpers.py
- 导入管理：18 个 try/except 统一为 _imports.py
- 版本号/许可证对齐：setup.py 6.1.0 / MIT
- 模块数修正：README/SKILL 与实际计数同步
- README 新增 Docker 快速开始 + 配置模板说明

## [6.1.0-beta] — 2026-06-06

### Added
- BlobArena v2 mmap 无损存储
- ONNX bge-small-zh-v1.5 中文嵌入 (512d, ~42ms)
- RetrievalHub 7 通道全链路
- MN-RU siliconflow fallback
- ANNSelector v2 + FAISS 动态索引
- GNN Graph Builder (GraphSAGE/GAT/GCN)

## [6.0.0] — 2026-05-21

### Added
- 五路神经检索 → ContextEngine 全链路
- ncps 神经电路策略集成 (LTC + CfC + 遗忘曲线)
- memory_synapse_network.py 神经突触网络
- NLP 增强神经网络 (依存句法/实体链接/指代消解/对比检测)
- 防幻觉双向闭环 (LTP/LTD + verified_memories)
- Galaxy Kernel 认知注入 assemble
- TKG 事件日志系统

## [5.5.0] — 2026-06-05

### Added
- IntelligentThinkingTrigger v2.0 (RCR-Router + Springdrift + A-ToM)
- skill_scorer.py RCR-Router 引擎 (28KB)
- thinking_memory.py Springdrift CBR 层 (14KB)
- Cognition Forest 子树内容修正

## [5.4.0] — 2026-05-31

### Added
- KG as Memory Backbone 4 阶段全链路
- 实体持久化 + 图检索主通道 + Cognition 图推理 + 睡眠图推理
- 检索通道升级为 6 路并行

## [5.2.0] — 2026-05-25

### Added
- KoRa v2 行为模式引擎
- DAG 上下文持久化修复

## [5.1.0] — 2026-05-20

### Added
- R-CCAM 延迟优化 (问候快速通路 ~24s→0.1s)
- FLARE 并行化防幻觉验证
- 安装向导 6 阶段自检
- 系统品牌更名 GalaxyOS
- Galaxy Kernel 扩容 (308 行独立后台线程)

## [4.x] — 2026-05

### Added
- UDS+ZMQ+mmap IPC 三通道
- Galaxy DAG 三维绑定
- Cognition Forest 子树复用
- Merge Gate 合入门禁
- 人格视觉 + Rails 增强版

## [3.x] — 2026-04

### Added
- R-CCAM 认知循环
- 16 层架构
- ContextEngine 注册
- DAG 上下文中继
