---
name: example-skill
description: 示例技能，演示 ClawHub 发布流程
version: 1.0.0
tags:
  - example
  - demo
  - tutorial
author: xkzs2007
---

# Example Skill

这是一个示例技能，用于演示 ClawHub 的发布和更新流程。

## 功能

- 演示 skill 结构
- 测试发布流程
- 验证更新机制

## 使用方法

```bash
# 安装
npx clawhub@latest install example-skill

# 使用
# 在 OpenClaw 中自动加载
```

## 文件结构

```
example-skill/
├── SKILL.md          # 技能说明文件
├── scripts/          # 脚本目录
│   └── example.sh    # 示例脚本
└── config/           # 配置目录
    └── example.json  # 示例配置
```

## 更新日志

### v1.0.0 (2026-04-07)
- 初始版本
- 演示发布流程
