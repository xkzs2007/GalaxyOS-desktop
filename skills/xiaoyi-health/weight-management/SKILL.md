---
name: weight-management-claw
description: "通过 CLI 获取体成分、运动、营养数据，分析体重变化、体脂率、身体成分，管理减重/增重目标。当用户询问体重变化、减肥、增肌、身体成分、身体得分相关问题时使用。"
metadata:
  {
    "pha": {
      "emoji": "⚖️",
      "category": "health-coaching-cli",
      "tags": ["cli", "weight", "body-composition", "coaching", "data-analysis"],
      "requires": { "tools": ["get_body_composition", "get_workouts", "get_nutrition", "get_menstrual_cycle"] }
    }
  }
---

# 体重管理数据获取与分析指南

## 一、数据获取策略

### 场景 A：进度检查（体重趋势）

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_body_composition","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_workouts","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}}
]'
```

### 场景 B：平台期诊断

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_body_composition","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_nutrition","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_workouts","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}}
]'
```

### 场景 C：体重波动（女性用户，结合经期）

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js --tools '[
  {"name":"get_body_composition","args":{"startDate":"YYYY-MM-DD","endDate":"YYYY-MM-DD"}},
  {"name":"get_menstrual_cycle","args":{"date":"today"}}
]'
```

### 便捷参数

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js get_body_composition --last-days 30
node ./skills/xiaoyi-health/bin/pha-claw.js get_nutrition --last-days 7
```

---

## 二、分析框架

### 第一步：问题分类

| 用户说 | 问题类型 | 取数方案 |
|--------|---------|---------|
| "我的体重怎么样？" | **进度检查** | 体重/体脂趋势 |
| "我遇到平台期了" | **平台期诊断** | 能量平衡全面分析 |
| "我体重涨了！" | **波动疑虑** | 检查水分潴留、月经周期 |
| "我的体脂率是多少？" | **身体成分检查** | 体脂率、肌肉量、内脏脂肪 |

### 第二步：身体成分参考标准

| 指标 | 健康范围 |
|------|---------|
| 体脂率（女性） | 20-30%（运动员：18-24%） |
| 体脂率（男性） | 10-20%（运动员：8-17%） |
| 健康减重速率 | 0.5-1.0 kg/周（最多不超过体重的 1%） |
| 热量缺口 | 500-750 kcal/天（绝不超过 1,000 kcal）|
| 最低热量摄入 | 女性 ≥ 1,200 kcal，男性 ≥ 1,500 kcal |
| 减脂期蛋白质 | 1.6-2.2 g/kg 体重/天 |

### 第三步：进度评估

关注**体脂率和肌肉量变化趋势**，而非只看体重：

| 场景 | 含义 |
|------|------|
| 体重下降、体脂下降、肌肉维持 | 理想减脂状态 |
| 体重稳定、体脂下降、肌肉增加 | 身体重塑，非常好 |
| 体重快速下降（>1.5 kg/周） | 可能流失肌肉，需放慢节奏 |
| 体重每日波动 ±1 kg | 正常水分/钠盐波动，可忽略 |
| 体重停滞 10 天以上 | 真正的平台期，需调查能量平衡 |

### 第四步：平台期诊断

当体重停滞 10 天以上：
1. 检查热量摄入是否悄悄增加（饮食疲劳）
2. 检查 NEAT 是否下降（身体适应）
3. 建议：1 周维持热量饮食恢复代谢，再恢复热量缺口

### 第五步：女性月经周期注意事项

- 经前体重增加 0.5-2 kg 是水分潴留，**不是脂肪增长**
- 月经来潮后体重通常回落
- 最好在同一月经周期阶段进行月度对比
- 黄体期食欲增加是激素作用，不是缺乏意志力

### 第六步：跨域分析

**体重 + 运动**：力量训练在减脂期保留肌肉比额外有氧更重要；运动后体重增加通常是糖原 + 水分

**体重 + 睡眠**：睡眠不足（<6 小时）→ 饥饿激素升高 + 饱腹感激素下降 → 难以控制食欲

**体重 + 压力**：慢性压力 → 皮质醇升高 → 促进腹部脂肪堆积

### 沟通原则

- **重身体成分，轻体重**：不要只报告体重数字，要解读体脂率和肌肉量变化
- **谨慎处理体重焦虑**：不使用评判性语言，将波动正常化
- **平台期鼓励**："平台期意味着你的身体已经适应了，这是健康代谢的标志"

### 红线

| 信号 | 处理方式 |
|------|---------|
| 每周减重 > 2 kg | 警告肌肉流失和代谢损伤风险，建议咨询营养师 |
| 每日热量 < 1,000 kcal | 低于安全最低限，建议就医 |
| 进食障碍迹象（强迫记录、负罪感、暴食-节食循环）| 温暖无评判的语言，建议专业帮助 |
| BMI < 18.5 仍想减重 | 建议与医生讨论目标 |

## 输出格式要求

- **禁止 Markdown 绘图**：不得输出任何图表语法，包括 Mermaid 图、折线图、柱状图、饼图，以及用字符拼凑的 ASCII 图表
- 合理使用标题（`##`）、列表、**加粗**、表格等排版方式呈现数据和分析结果
