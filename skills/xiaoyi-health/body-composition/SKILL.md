---
name: body-composition-cli
description: "通过 CLI 获取用户体成分数据（体重、BMI、体脂率等）。当用户询问体重、BMI、体脂、肌肉量相关问题时，使用此 CLI 命令获取数据后再分析。"
metadata:
  {
    "pha": {
      "emoji": "⚖️",
      "category": "health-data-cli",
      "tags": ["cli", "body-composition", "weight", "bmi"],
      "requires": { "tools": ["get_body_composition"] }
    }
  }
---

# 体成分数据 CLI 获取指南

## 时间查询规范

- **无时间词** → 不传参数（工具默认今天）
- **"最近" / "这几天"** → 最近 7 天
- **"本周"** → 本周一至今天；**"上周"** → 上周完整自然周
- **"本月"** → 当月 1 日至今天；**"上个月"** → 上个完整自然月
- **"最近 X 天/周/月"** → 对应范围向前推算
- **"至今" / "历史"** → 两年前至今
- 查"上次何时"：先查最近 1 个月 → 无数据扩至 3 个月 → 最多 1 年

**日期准确性（不可违反）**：查到哪天说哪天，不可将 A 日期的数据说成 B 日期；部分日期无数据时如实说明，不得用相邻日期数据替代。

## 命令示例

### 获取今日体成分
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_body_composition --date today
```

### 获取指定日期
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_body_composition --date 2024-01-15
```

### 获取最近 7 天体成分趋势
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_body_composition --last-days 7
```

### 获取最近 30 天趋势（观察体重变化）
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_body_composition --last-days 30
```

### 获取指定日期范围体成分数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_body_composition --start-date 2024-01-01 --end-date 2024-01-31
```

## 返回字段说明

| 字段 | 说明 |
|------|------|
| `weight` | 体重（kg） |
| `height` | 身高（cm） |
| `bmi` | 体质指数 |
| `bodyFatPercent` | 体脂率（%） |
| `bodyScore` | 综合体成分评分 |

## BMI 参考范围

| 分类 | BMI |
|------|-----|
| 偏瘦 | <18.5 |
| 正常 | 18.5–23.9 |
| 超重 | 24.0–27.9 |
| 肥胖 | ≥28.0 |

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
