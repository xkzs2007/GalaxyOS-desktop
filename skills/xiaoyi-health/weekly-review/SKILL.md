---
name: weekly-review-claw
description: "通过 CLI 获取一周多维度健康数据，生成每周健康报告和趋势分析。当用户明确要求'周回顾'、'这周怎么样'、'周报'时使用。"
metadata:
  {
    "pha": {
      "emoji": "📅",
      "category": "health-coaching-cli",
      "tags": ["cli", "weekly-review", "trends", "report", "data-analysis"],
      "requires": { "tools": ["get_activity_data", "get_sleep", "get_heart_rate", "get_workouts", "get_stress"] }
    }
  }
---

# 周回顾数据获取与分析指南

## 一、数据获取策略

### 标准周回顾（范围查询，一次获取全部）

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_activity_data","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_sleep","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_heart_rate","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}}
]'
```

或便捷参数：
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_activity_data --last-days 7
node ./skills/xiaoyi-health/bin/pha-claw.js get_sleep --last-days 7
node ./skills/xiaoyi-health/bin/pha-claw.js get_heart_rate --last-days 7
```

### 扩展周回顾（含运动和压力）

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_activity_data","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_sleep","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_heart_rate","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_workouts","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_stress","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}}
]'
```

---

## 二、分析框架

### 第一步：确认回顾类型

| 用户说的话 | 回顾类型 | 输出详略 |
|-----------|---------|---------|
| "我这周怎么样？" | **综合回顾** | 简洁版（3-5 句话）|
| "这周和上周比怎么样" | **周期对比** | 简洁版 + 历史对比 |
| "我有进步吗？" | **进度检查** | 简洁版 + 趋势 |
| "给我出个周报" | **正式报告** | 结构化版（≤300 字）|

### 第二步：活动维度

评估指标：日均步数、达标天数（默认 8,000+ 步）

| 达标天数 | 评级 | 评语 |
|---------|------|------|
| 6-7/7 天 | 优秀 | "非常出色的一致性" |
| 4-5/7 天 | 良好 | "稳健的一周" |
| 2-3/7 天 | 需关注 | "活动量偏低" |
| 0-1/7 天 | 令人担忧 | "这周活动非常安静" |

### 第三步：睡眠维度

评估指标：平均时长、规律性、低于 6 小时的夜数

| 平均时长 | 规律性 | 结论 |
|---------|-------|------|
| 7-8 小时 | 波动小 | "睡眠是本周强项" |
| 7-8 小时 | 波动大 | "平均值好，但作息不稳" |
| 6-7 小时 | 任意 | "略低于最佳水平" |
| < 6 小时 | 任意 | "睡眠明显不足" |

### 第四步：心率维度

- 7 天平均心率与正常范围对比
- 持续上升 = 压力/过度训练信号

### 第五步：跨维度综合分析

| 组合模式 | 解读 |
|---------|------|
| 高活动量 + 睡眠差 | 检查晚间运动影响 |
| 低活动量 + 睡眠好 | 精力未转化为运动 |
| 全面提升 | 肯定正向趋势 |
| 多维度同时下降 | 温和关心，引导排查原因 |

### 第六步：报告结构

**简洁版（默认，用户一般性询问）**：

3-5 句话：
1. 总体评价（一句话）
2. 最强维度 + 关键数据
3. 需改进的方面
4. 一个具体建议

示例："总体来说不错的一周。活动很稳定，7 天中有 5 天达到了步数目标。睡眠需要关注——平均 6.3 小时。下周试试固定就寝时间。"

**结构化版（用户要求正式周报）**：

```
本周健康总结

活动：[评级] — 日均 X 步，达标 X/7 天
睡眠：[评级] — 平均每晚 X 小时
心率：均值 X bpm [正常/偏高/偏低]

亮点：
- [本周最佳成就]

需关注：
- [最弱维度 + 具体问题]

下周建议：
- [一个具体、可操作的建议]
```

### 沟通准则

- **先讲故事，再说数字** — 用自然语言串联，不要罗列指标
- **重视一致性胜过峰值** — "7 天中 6 天达标"比"有一天走了 15,000 步"更重要
- **以前瞻性结尾** — 下周具体建议
- **对不好的周也要坦诚** — 不粉饰，但要有建设性

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
