---
name: multi-metric-cli
description: "通过 CLI 同时获取多项健康数据。当用户需要综合健康报告、同时分析多个健康指标时，使用此 CLI 命令并行获取多项数据。"
metadata:
  {
    "pha": {
      "emoji": "📊",
      "category": "health-data-cli",
      "tags": ["cli", "multi-metric", "health-report"],
      "requires": {
        "tools": [
          "get_activity_data", "get_heart_rate", "get_sleep",
          "get_stress", "get_spo2", "get_workouts"
        ]
      }
    }
  }
---

# 多指标并行获取指南

当需要同时分析多个健康维度时，使用 `--tools` JSON 模式一次调用获取所有数据，避免多次调用。

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

### 今日综合健康概览（步数 + 心率 + 睡眠 + 压力）
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_activity_data","args":{"date":"today"}},
  {"name":"get_heart_rate","args":{"date":"today"}},
  {"name":"get_sleep","args":{"date":"today"}},
  {"name":"get_stress","args":{"date":"today"}}
]'
```

### 最近 7 天多维趋势分析
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_activity_data","args":{"startDate":"2024-01-10","endDate":"2024-01-16"}},
  {"name":"get_heart_rate","args":{"startDate":"2024-01-10","endDate":"2024-01-16"}},
  {"name":"get_sleep","args":{"startDate":"2024-01-10","endDate":"2024-01-16"}},
  {"name":"get_stress","args":{"startDate":"2024-01-10","endDate":"2024-01-16"}}
]'
```

### 心血管综合检查（心率 + 血氧 + 血压 + 静息心率）
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_heart_rate","args":{"date":"today"}},
  {"name":"get_spo2","args":{"date":"today"}},
  {"name":"get_blood_pressure","args":{"date":"today"}},
  {"name":"get_resting_heart_rate","args":{"date":"today"}}
]'
```

### 运动恢复评估（运动 + HRV + 睡眠 + 压力）
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_workouts","args":{"date":"today"}},
  {"name":"get_hrv","args":{"date":"today"}},
  {"name":"get_sleep","args":{"date":"today"}},
  {"name":"get_stress","args":{"date":"today"}}
]'
```

## 如何结合 --last-days 与多工具

`--last-days` 仅在单工具子命令模式下可用。多工具模式下，需手动计算日期范围填入 `startDate` / `endDate`。

## 注意事项

- 所有工具并行执行，整体耗时取决于最慢的那个工具
- 如果某个工具调用失败，其错误信息会出现在响应的 `errors` 字段，不影响其他工具结果
- 响应格式：`{ "results": { "<tool_name>": {...} }, "errors": { "<tool_name>": "..." } }`

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
