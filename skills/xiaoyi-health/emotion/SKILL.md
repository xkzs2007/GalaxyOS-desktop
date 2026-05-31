---
name: emotion-cli
description: "通过 CLI 获取用户情绪数据。当用户询问情绪状态、心情、情绪波动相关问题时，使用此 CLI 命令获取数据后再分析。"
metadata:
  {
    "pha": {
      "emoji": "😊",
      "category": "health-data-cli",
      "tags": ["cli", "emotion", "mood", "mental-health"],
      "requires": { "tools": ["get_emotion"] }
    }
  }
---

# 情绪数据 CLI 获取指南

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

### 获取今日情绪
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_emotion --date today
```

### 获取指定日期情绪数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_emotion --date 2024-01-15
```

### 获取最近 7 天情绪趋势
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_emotion --last-days 7
```

### 获取最近 30 天情绪趋势
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_emotion --last-days 30
```

### 获取指定日期范围情绪数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_emotion --start-date 2024-01-01 --end-date 2024-01-31
```

## 返回字段说明

| 字段 | 说明 |
|------|------|
| `current` | 当前情绪状态 |
| `score` | 情绪评分（越高越积极） |
| `avg` | 当日平均情绪评分 |
| `readings[]` | 全天各时段情绪记录 |

## 情绪等级参考

| 等级 | 说明 | 建议 |
|------|------|------|
| 非常好 | 情绪积极愉快 | 保持当前状态 |
| 良好 | 情绪平稳正常 | 无需特别关注 |
| 一般 | 情绪略有波动 | 适当放松调节 |
| 较差 | 情绪低落或焦虑 | 建议休息或倾诉 |
| 很差 | 情绪明显异常 | 建议关注心理健康 |

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
