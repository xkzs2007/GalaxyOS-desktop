---
name: heart-monitor-claw
description: "通过 CLI 获取心率、HRV、运动数据，并进行心血管健康分析（静息心率评估、运动心率区间、过度训练检测）。当用户询问心率、心跳、HRV、心血管相关问题时使用。"
metadata:
  {
    "pha": {
      "emoji": "❤️",
      "category": "health-coaching-cli",
      "tags": ["cli", "heart-rate", "hrv", "coaching", "data-analysis"],
      "requires": { "tools": ["get_heart_rate", "get_hrv", "get_workouts", "get_resting_heart_rate", "get_sleep"] }
    }
  }
---

# 心率数据获取与分析指南

## 一、数据获取策略

### 场景 A：单日心率检查

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_heart_rate --date today
```

### 场景 B：运动心率分析

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_heart_rate","args":{"date":"today"}},
  {"name":"get_workouts","args":{"date":"today"}}
]'
```

### 场景 C：HRV + 静息心率联合趋势（恢复状态评估）

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_hrv","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_resting_heart_rate","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}}
]'
```

或便捷参数：
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_hrv --last-days 7
node ./skills/xiaoyi-health/bin/pha-claw.js get_resting_heart_rate --last-days 7
```

### 场景 D：夜间心率 + 睡眠联合分析

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_heart_rate","args":{"date":"today"}},
  {"name":"get_sleep","args":{"date":"today"}}
]'
```

### 数据采集决策表

| 问题类型 | 取数方案 |
|---------|---------|
| 单日检查 | `get_heart_rate(date)` |
| 运动场景 | `get_heart_rate` + `get_workouts` |
| 睡眠场景 | `get_heart_rate` + `get_sleep` |
| 趋势问题 | `get_heart_rate(startDate, endDate)` |
| HRV 趋势 | `get_hrv(startDate, endDate)` |
| HRV + 静息心率联合 | `get_hrv` + `get_resting_heart_rate`（范围查询）|

---

## 二、分析框架

### 第一步：分类问题

| 用户描述 | 问题类型 | 调查方向 |
|---------|---------|---------|
| "我的心率正常吗？" | **基线检查** | 静息心率与个人历史/人群标准对比 |
| "我心率偏高/偏快" | **急性升高** | 运动？压力？生病？咖啡因？ |
| "运动时的心率" | **运动心率** | 强度区间、恢复速率 |
| "心率趋势" / "心脏怎么样" | **趋势分析** | 拉取周数据，是否有漂移 |
| "睡觉时的心率" | **夜间心率** | 与睡眠质量交叉参考 |

### 第二步：静息心率评估

**个人基线最重要**，绝对值需结合历史趋势解读：

| 分类 | 范围 | 备注 |
|------|------|------|
| 运动员级别 | 40-55 bpm | 训练有素的心血管系统 |
| 体能良好 | 55-65 bpm | 规律锻炼者 |
| 普通水平 | 65-75 bpm | 典型健康成年人 |
| 偏高 | 75-85 bpm | 可能需要更多有氧运动 |
| 升高 | 85-100 bpm | 值得监测，调查生活方式因素 |
| 心动过速 | > 100 bpm | 持续存在建议就医 |

**影响静息心率的因素**：
- 体能提升 → 数周/月内逐渐下降（正面信号）
- 压力、睡眠不足、生病 → 急性升高
- 过度训练 → 训练中反常升高（警告）
- 咖啡因、酒精、脱水 → 暂时性升高

### 第三步：运动心率区间

最大心率 ≈ 220 - 年龄（需用户年龄）

| 区间 | 最大心率占比 | 目的 | 体感 |
|------|------------|------|------|
| Zone 1 | 50-60% | 恢复、热身 | 轻松，可正常聊天 |
| Zone 2 | 60-70% | 燃脂、基础耐力 | 舒适，能说话 |
| Zone 3 | 70-80% | 有氧体能 | 中等，只能说短句 |
| Zone 4 | 80-90% | 乳酸阈值、速度 | 较难，只能说几个词 |
| Zone 5 | 90-100% | 最大强度 | 全力冲刺，无法维持 |

**80/20 原则**：大部分训练（80%）在 Zone 1-2，仅 20% 为高强度

### 第四步：运动后心率恢复

| 恢复速度（运动后 1 分钟） | 评级 |
|------------------------|------|
| 下降 ≥ 20 bpm | 优秀，心血管功能强 |
| 下降 12-20 bpm | 正常/良好 |
| 下降 < 12 bpm | 偏低，建议加强有氧基础 |

### 第五步：HRV 分析（趋势比绝对值更重要）

- HRV 高于个人基线 → 恢复良好
- HRV 低于基线 1-2 天 → 正常波动
- HRV 低于基线 5 天以上 → 系统性压力信号（过训/生病/慢性压力）

**HRV + 静息心率联合评估**：

| HRV 对比基线 | 静息心率 | 评估 |
|-------------|---------|------|
| 达到或高于基线 | 正常 | 恢复良好，按计划训练 |
| 偏低 5-15% | 略有升高 | 轻度疲劳，降低强度 |
| 偏低 > 15% | 升高 > 5 bpm | 明显疲劳，安排恢复日 |
| 偏低 > 25% | 持续升高 | 过度训练风险，停训 2-3 天 |

### 第六步：跨域分析

**心率 + 睡眠**：夜间心率高于基线 → 睡眠质量差；入睡后心率未下降 → 身体未进入恢复模式

**心率 + 运动**：运动后 48 小时以上静息心率仍升高 → 可能过度训练

### 沟通原则

- 不要对每次波动发出警告；每日正常波动 5-10 bpm
- 单次偏高读数不说明问题，要看趋势
- 先给上下文，再说数字（"比你平时的 ~72 高了约 6 bpm，可能是因为昨晚只睡了 5.5 小时"）

### 红线

| 信号 | 行动 |
|------|------|
| 静息心率持续 > 100 bpm | 建议就医检查 |
| 用户报告心悸、胸痛、头晕 | 建议就医，提供数据摘要 |
| 无生活方式解释的突然大幅变化 | 观察 1-2 天，若持续建议就医 |

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
