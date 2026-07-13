---
name: workout-tracker-claw
description: "通过 CLI 获取运动记录及关联数据（心率、睡眠），分析训练负荷、恢复状态和体能进步。当用户询问运动、训练、锻炼相关问题时使用。"
metadata:
  {
    "pha": {
      "emoji": "🏃",
      "category": "health-coaching-cli",
      "tags": ["cli", "workouts", "exercise", "coaching", "data-analysis"],
      "requires": { "tools": ["get_workouts", "get_heart_rate", "get_sleep", "get_hrv", "get_resting_heart_rate"] }
    }
  }
---

# 运动数据获取与分析指南

## 一、数据获取策略

### 场景 A：今天的运动记录

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_workouts --date today
```

### 场景 B：训练准备度评估（今天该不该运动？）

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_workouts","args":{"date":"yesterday"}},
  {"name":"get_sleep","args":{"date":"today"}},
  {"name":"get_heart_rate","args":{"date":"today"}}
]'
```

### 场景 C：恢复状态评估（HRV + 静息心率）

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_hrv","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_resting_heart_rate","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}}
]'
```

### 场景 D：周运动趋势

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_workouts","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_heart_rate","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}}
]'
```

或：
```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_workouts --last-days 7
```

---

## 二、分析框架

### 第一步：分类问题

| 用户描述 | 问题类型 | 取数方案 |
|---------|---------|---------|
| "我今天运动了吗？" | **活动检查** | `get_workouts(today)` |
| "我今天该运动吗？" | **训练准备度** | `get_workouts(yesterday)` + `get_sleep(today)` + `get_heart_rate(today)` |
| "运动后酸痛/疲劳" | **恢复检查** | 近期训练负荷 + `get_sleep` + `get_heart_rate` |
| "我运动够不够？" | **运动量评估** | `get_workouts(week)` 周汇总 |

### 第二步：训练准备度评估

**绿灯（可以开练）**：
- 睡了 7 小时以上且质量不错
- 静息心率处于或低于个人基线
- 昨天没运动或只是轻量训练

**黄灯（轻松练）**：
- 睡了 5-7 小时或质量较差
- 静息心率略微升高（比基线高 5-10 bpm）
- 昨天有高强度训练
- 建议：降低强度，或主动恢复（散步、瑜伽）

**红灯（今天休息）**：
- 睡眠 < 5 小时
- 静息心率高于基线 > 10 bpm
- 连续 2 天以上高强度训练无休息

### 第三步：运动量指南

**每周最低标准（WHO）**：
- 150 分钟中等强度有氧，或 75 分钟高强度有氧
- 另加：每周 2 天以上肌肉强化活动

| 体能水平 | 典型一周 | 建议 |
|---------|---------|------|
| 初学者 | 2-3 次，每次 20-30 分钟 | 先建立习惯，任何运动都有价值 |
| 中级 | 3-4 次，每次 30-45 分钟 | 混合强度：2 次中等 + 1-2 次高强度 |
| 进阶 | 4-6 次，每次 45-90 分钟 | 80/20 原则：80% 轻松，20% 高强度 |

### 第四步：训练强度分析（通过心率判断）

最大心率 ≈ 220 - 年龄

| 平均心率 | 强度区间 |
|---------|---------|
| < 60% 最大心率 | 轻量 / 恢复 |
| 60-75% | 中等 / 有氧基础 |
| 75-85% | 较难 / 节奏训练 |
| > 85% | 非常辛苦 / 接近极限 |

### 第五步：恢复状态评估（HRV 四级）

| 状态 | HRV 对比基线 | 静息心率 | 建议 |
|------|------------|---------|------|
| 恢复良好 | ≥ 基线 | 正常 | 按计划训练 |
| 轻度疲劳 | 低 5-15% | 略有升高 | 降低强度 |
| 明显疲劳 | 低 > 15% | 高 > 5 bpm | 仅恢复性活动 |
| 过度训练 | 低 > 25% | 持续升高 | 完全休息 2-3 天 |

### 第六步：运动类型分析要点

| 运动类型 | 关键指标 | 注意事项 |
|---------|---------|---------|
| 跑步 | 距离、配速、心率区间 | 80/20 原则；周跑量增幅 ≤10% |
| 骑行 | 功率（W）、踏频（RPM） | 平路踏频 90-100 RPM |
| 游泳 | SWOLF 指数 | 水中心率比陆地低 10-15 bpm |
| 力量训练 | 训练容量 | 每周增幅 2-5%；推拉比例约 1:1 |
| HIIT | 峰值心率 85-95% | 每周最多 3 次 |
| 户外（高海拔） | SpO2 | <90% 为警告 |

### 数据展示规范

工具返回数据后，必须完整展示并分析（禁止只说"好的已记录"）：
- 列出关键数值：距离、时长、心率、卡路里
- 计算衍生指标：配速 = 时长/距离，心率区间
- 与历史数据对比，提供个性化洞察
- 给出基于数据的具体建议

### 红线

| 信号 | 行动 |
|------|------|
| 运动中胸痛、头晕或异常气短 | 立即停止运动，就医前勿再训练 |
| 每天 2 小时以上、无休息日、自我惩罚式言语 | 温和询问：身体感觉怎么样？恢复同样重要 |
| 伤后想恢复运动 | 建议先获得医生许可，再制定循序渐进计划 |

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
