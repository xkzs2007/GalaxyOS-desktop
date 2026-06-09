# 版本管理规范

## 版本号格式

```
vMAJOR.MINOR.PATCH

例: v6.5.1
```

**禁止：** `v6.4.0e`、`v6.5-beta`、`v6.5.1-hotfix` 等非标准后缀。

## 变更规则

| 级别 | 触发条件 | 例子 |
|------|---------|------|
| **MAJOR** | 架构层重构、破坏性 API 变更、底层存储 Schema 不兼容 | 引入新引擎、删除已公开接口 |
| **MINOR** | 新增模块/功能/技能，向后兼容 | 新 services 模块、新工具注册 |
| **PATCH** | Bug 修复、性能优化、文档补漏 | 死锁修复、import 错误修正 |

## 必须同步更新的文件

1. `setup.py` 中 `version="x.y.z"`
2. `CHANGELOG.md` 中追加对应版本条目
3. Git tag 打 `vx.y.z`（GPG signed）

## Tag 规范

```
git tag -s vx.y.z -m "简短说明"
git push origin vx.y.z
```

禁止未打 tag 的版本变更。版本号未变更时不打 tag。

## 同一版本多个 commit

如果在打 tag 后又有小幅修改（如文档补漏、注释修正），版本号不变，
不打新 tag。只有代码功能变更时升版本。
