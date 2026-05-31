---
name: naps-cli
description: "通过 CLI 获取用户小睡/午休数据。当用户询问午睡、小睡、短暂休息相关问题时，使用此 CLI 命令获取数据后再分析。普通夜间睡眠请使用 sleep-cli。"
metadata:
  {
    "pha": {
      "emoji": "😴",
      "category": "health-data-cli",
      "tags": ["cli", "naps", "sleep", "rest"],
      "requires": { "tools": ["get_naps"] }
    }
  }
---

# 小睡数据 CLI 获取指南

小睡数据记录白天短暂睡眠（午睡、小憩）情况，与夜间睡眠数据分开记录。

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

### 获取今日小睡记录
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_naps --date today
```

### 获取指定日期小睡记录
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_naps --date 2024-01-15
```

### 获取最近 7 天小睡趋势
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_naps --last-days 7
```

### 获取最近 30 天小睡趋势
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_naps --last-days 30
```

### 获取指定日期范围小睡数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_naps --start-date 2024-01-01 --end-date 2024-01-31
```

## 返回字段说明

| 字段 | 说明 |
|------|------|
| `count` | 当日小睡次数 |
| `totalMinutes` | 当日小睡总时长（分钟），建议不超过 40 分钟 |
| `naps[]` | 各次小睡记录 |
| `naps[].startTime` | 小睡开始时间 |
| `naps[].durationMinutes` | 小睡时长（分钟） |

## 小睡时长参考

| 时长 | 效果 |
|------|------|
| 10–20 分钟 | 短暂恢复精力，不影响夜间睡眠（推荐） |
| 30 分钟 | 可能产生睡眠惰性（醒后短暂迷糊） |
| 60–90 分钟 | 深度休息，但可能影响夜间入睡 |

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
