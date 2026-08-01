# 贡献指南

欢迎为 GalaxyOS 贡献代码！以下是如何开始的说明。

## 环境搭建

```bash
git clone https://github.com/xkzs2007/GalaxyOS-desktop.git
cd GalaxyOS-desktop
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-core.txt
pip install "openjiuwen @ git+https://github.com/openJiuwen-ai/agent-core@v0.1.16"
pip install onnxruntime
pip install pytest pytest-cov ruff mypy

# 配置 API Key
cp config/llm_config.example.json config/llm_config.json
# 编辑填入你的 API Key
```

## 开发工作流

```bash
python -m pytest tests/ -x -q --tb=short  # 跑全部测试
ruff check .                                # 代码检查
python -m mypy galaxyos/ --config-file mypy.ini  # 类型检查

# C++ 桌面壳构建（需先 checkout EUI-NEO 到项目根目录）
cmake -B desktop-native/build -S desktop-native
cmake --build desktop-native/build --config Release
```

## 项目结构

```
desktop-native/         # C++ 桌面壳（EUI-NEO GPU 直渲）
  src/                    # C++ 源码（8 已实现模块 + 5 待实现）
  include/                # C++ 头文件
  third_party/            # cpp-httplib, nlohmann/json
galaxyos/               # Python 核心包
  kernel/                 # 认知内核（MCP Server + AgentCore Bridge + DSL Bridge）
  engine/                 # 引擎模块（ONNX Embedding、检索、神经网络）
  skill_infra/            # 技能基础设施
skills/                 # 76 技能包（mattpocock/skills 格式）
tests/                  # 测试
config/                 # 配置文件
scripts/                # 辅助脚本
```

## 提交规范

使用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 格式：

```
feat: 新功能
fix: 修复 bug
refactor: 重构
test: 测试相关
docs: 文档
chore: 构建/工具
security: 安全相关
```

提交前确保：
- [ ] `python -m pytest tests/ -x -q` 全绿
- [ ] `ruff check .` 无新增警告
- [ ] 新功能有对应测试

## 分支策略

```
main     → 稳定版本，只接受 PR
feat/*   → 新功能
fix/*    → Bug 修复
```

## 版本管理

版本号格式 `vMAJOR.MINOR.PATCH`，详见 `VERSIONING.md`。
发布新版本时必须：
1. 更新 `pyproject.toml` 中的 version
2. 更新 `CHANGELOG.md`
3. 打 GPG signed tag (`git tag -s vx.y.z`)
4. 推送 tag (`git push origin vx.y.z`)

## 添加新模块

1. 在 `galaxyos/engine/` 或 `galaxyos/kernel/` 下创建模块
2. 在 `tests/` 下创建 `test_your_module.py`
3. 更新 `requirements-core.txt`（如有新依赖）

## 需要帮助？

- 查看 `README.md` 了解架构概览
- 查看 `UBIQUITOUS_LANGUAGE.md` 了解术语定义
- 查看 `docs/API.md` 了解 API 速查
- 提 Issue 或 PR 讨论

## 许可证

MIT License — 详见 `LICENSE` 文件。
