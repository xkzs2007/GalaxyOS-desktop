---
name: menstrual-cycle-cli
description: "通过 CLI 获取用户经期数据。当用户询问月经周期、经期、生理期相关问题时，使用此 CLI 命令获取数据后再分析。"
metadata:
  {
    "pha": {
      "emoji": "🌸",
      "category": "health-data-cli",
      "tags": ["cli", "menstrual-cycle", "women-health"],
      "requires": { "tools": ["get_menstrual_cycle"] }
    }
  }
---

# 经期数据 CLI 获取指南

## 时间查询规范

**模糊时间词默认范围**：用户说"最近的经期"、"最近几个月"等模糊表述时，默认查最近 **3 个月**（约 3 个周期），而非通用的 7 天。月经周期以月为单位，7 天不足以覆盖完整的周期数据。

## 命令示例

### 获取今日经期状态
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_menstrual_cycle --date today
```

### 获取指定日期经期数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_menstrual_cycle --date 2024-01-15
```

### 获取最近 30 天经期记录
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_menstrual_cycle --last-days 30
```

### 获取最近 90 天（约 3 个周期）
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_menstrual_cycle --last-days 90
```

### 获取指定日期范围经期数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_menstrual_cycle --start-date 2024-01-01 --end-date 2024-03-31
```

## 返回字段说明

| 字段 | 说明 |
|------|------|
| `phase` | 当前周期阶段（行经期/卵泡期/排卵期/黄体期） |
| `cycleDay` | 当前周期第几天 |
| `cycleLength` | 平均周期长度（天） |
| `periodLength` | 平均行经天数 |
| `nextPeriodDate` | 预测下次经期日期 |
| `records[]` | 历史经期记录 |

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
