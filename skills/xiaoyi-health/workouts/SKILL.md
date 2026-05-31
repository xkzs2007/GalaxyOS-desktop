---
name: workouts-cli
description: "通过 CLI 获取用户运动记录。当用户询问运动、锻炼、跑步、游泳、健身等运动相关问题时，使用此 CLI 命令获取数据后再分析。"
metadata:
  {
    "pha": {
      "emoji": "🏃",
      "category": "health-data-cli",
      "tags": ["cli", "workouts", "exercise", "fitness"],
      "requires": { "tools": ["get_workouts"] }
    }
  }
---

# 运动数据 CLI 获取指南

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

### 获取今日运动记录
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_workouts --date today
```

### 获取指定日期运动记录
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_workouts --date 2024-01-15
```

### 获取最近 7 天运动记录
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_workouts --last-days 7
```

### 获取最近 30 天运动记录
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_workouts --last-days 30
```

### 获取指定日期范围运动记录
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_workouts --start-date 2024-01-01 --end-date 2024-01-31
```

## 返回字段说明

| 字段 | 说明 |
|------|------|
| `type` | 运动类型（如跑步、游泳、骑行） |
| `durationMinutes` | 运动时长（分钟） |
| `caloriesBurned` | 消耗卡路里（千卡） |
| `distanceKm` | 运动距离（公里，部分运动有） |
| `avgHeartRate` | 平均心率（bpm） |
| `startTime` / `endTime` | 开始/结束时间 |
| `count` | 当日运动次数 |

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
