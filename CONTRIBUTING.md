# 贡献指南

欢迎为 GalaxyOS 贡献代码！以下是如何开始的说明。

## 环境搭建

```bash
git clone https://cnb.cool/llm-memory-integrat/GalaxyOS.git
cd GalaxyOS
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
pip install pytest pytest-cov ruff mypy

# 配置 API Key
cp config/llm_config.example.json config/llm_config.json
# 编辑填入你的 API Key
```

## 开发工作流

```bash
make test       # 跑全部测试 (428+)
make coverage   # 测试 + 覆盖率 HTML 报告
make lint       # ruff 代码检查
make typecheck  # mypy 类型检查
make ci         # lint + test 一键检查
```

## 项目结构

```
services/         # 核心服务包 (160 模块, 103K 行)
  xiaoyi_claw_api.py   # 主入口类 XiaoYiClawLLM
  retrieval_hub.py     # 7 通道统一检索
  _imports.py          # 选装模块降级导入
  rccam_state.py       # R-CCAM 五阶段状态对象
  claw_helpers.py      # 模块级便捷 API
tests/            # 测试 (32 文件, 428 用例)
config/           # 配置文件 (模板: llm_config.example.json)
scripts/          # 辅助脚本 + 安装向导
extensions/       # OpenClaw 插件
skills/           # 技能定义 + llm-memory-integration core
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
- [ ] `make test` 全绿
- [ ] `make lint` 无新增警告
- [ ] 新功能有对应测试

## 分支策略

```
main     → 稳定版本，只接受 PR
feat/*   → 新功能
fix/*    → Bug 修复
```

## 添加新服务模块

1. 在 `services/` 下创建 `your_module.py`
2. 如果模块可选，在 `services/_imports.py` 添加降级导入
3. 在 `tests/` 下创建 `test_your_module.py`
4. 在 `services/__init__.py` 中注册导出

## 需要帮助？

- 查看 `README.md` 了解架构概览
- 查看 `SKILL.md` 了解详细架构文档
- 查看 `docs/API.md` 了解 API 速查
- 提 Issue 或 PR 讨论

## 许可证

MIT License — 详见 `LICENSE` 文件。
