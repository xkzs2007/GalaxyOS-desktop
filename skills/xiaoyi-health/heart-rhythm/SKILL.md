---
name: heart-rhythm-cli
description: "通过 CLI 获取用户心律数据。当用户询问心律、心律不齐、心房颤动、房颤相关问题时，使用此 CLI 命令获取数据后再分析。"
metadata:
  {
    "pha": {
      "emoji": "💗",
      "category": "health-data-cli",
      "tags": ["cli", "heart-rhythm", "arrhythmia", "afib"],
      "requires": { "tools": ["get_heart_rhythm"] }
    }
  }
---

# 心律数据 CLI 获取指南

心律数据记录心跳节律是否规则，可用于检测心律不齐等异常情况。

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

### 获取今日心律数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_heart_rhythm --date today
```

### 获取指定日期心律数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_heart_rhythm --date 2024-01-15
```

### 获取最近 7 天心律趋势
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_heart_rhythm --last-days 7
```

### 获取最近 30 天心律趋势
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_heart_rhythm --last-days 30
```

### 获取指定日期范围心律数据
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_heart_rhythm --start-date 2024-01-01 --end-date 2024-01-31
```

## 返回字段说明

| 字段 | 说明 |
|------|------|
| `status` | 心律状态（正常/异常） |
| `afibDetected` | 是否检测到房颤 |
| `readings[]` | 各时段心律检测记录 |
| `abnormalCount` | 当日异常次数 |

## 注意事项

- 心律异常（尤其是房颤）可能增加脑卒中风险，建议及时就医确认
- 可穿戴设备检测仅供参考，不能替代专业医疗诊断

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
