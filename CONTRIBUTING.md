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
make test       # 跑全部测试 (137 用例)
make coverage   # 测试 + 覆盖率 HTML 报告
make lint       # ruff 代码检查
make native     # 编译 Rust 扩展
make ci         # lint + test 一键检查
```

## 项目结构

```
extensions/galaxyos/   # OpenClaw 插件（主开发目录）
  index.js                 # 主插件 — 9 钩子 / 15 工具 / 2 插槽
  openclaw.plugin.json     # 插件契约
  clawhub.json             # ClawHub 发布清单
  scripts/                 # Python 运行时（~140 模块）
    injection_scanner.py   #   Skill Bank 内容扫描器
    lfm_skill_bank.py      #   LFM 技能库
    multi_agent_orchestrator.py
    dag_context_manager.py
    claw_worker.py         #   主 Worker
  native/                  # Rust 跨平台扩展
galaxyos/               # 统一 Python 包
  engine/                   # 引擎模块
  privileged/               # 特权模块（ACP server 等）
services/               # shim 层（转发到 galaxyos/privileged/）
tests/                  # 测试 (37 文件, 137 用例)
skills/                 # 技能库 (60+ 个)
config/                 # 配置文件
scripts/                # 辅助脚本 + 安装��导
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

## 版本管理

版本号格式 `vMAJOR.MINOR.PATCH`，详见 `VERSIONING.md`。
发布新版本时必须：
1. 更新 `setup.py` 中的 version
2. 更新 `CHANGELOG.md`
3. 打 GPG signed tag (`git tag -s vx.y.z`)
4. 推送 tag (`git push origin vx.y.z`)

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
