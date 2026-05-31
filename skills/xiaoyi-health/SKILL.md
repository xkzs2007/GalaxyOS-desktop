---
name: xiaoyi-health-cli-index
description: "xiaoyi-health CLI 技能索引。当用户提出健康相关问题时，先读此文件确定应加载哪个具体 skill，再按需加载对应 skill 获取数据和分析框架。不要一次性加载所有 skill。"
metadata:
  {
    "pha": {
      "emoji": "📋",
      "category": "health-cli-index",
      "tags": ["index", "routing", "cli", "health"]
    }
  }
---

# xiaoyi-health CLI 技能索引

> **使用原则**：先读此索引，根据用户意图定位具体 skill，再按需加载该 skill 的完整内容。**不要一次性加载所有 skill。**

## 一、CLI 执行方式

所有命令使用相对路径调用：

```bash
node ./skills/xiaoyi-health/bin/pha-claw.js <command> [args]
```

---

## 二、技能路由表

### 单指标数据获取型

> 适用：用户只问某一项具体指标，不需要跨指标综合分析。

| 用户意图 | 加载 skill | 核心工具 |
|---------|-----------|---------|
| 问心率、BPM、心跳 | `heart-rate/SKILL.md` | `get_heart_rate` |
| 问静息心率、晨起心率 | `resting-heart-rate/SKILL.md` | `get_resting_heart_rate` |
| 问 HRV、心率变异性 | `hrv/SKILL.md` | `get_hrv` |
| 问心律、心律不齐、房颤 | `heart-rhythm/SKILL.md` | `get_heart_rhythm` |
| 问睡眠时长、质量、睡眠阶段 | `sleep/SKILL.md` | `get_sleep` |
| 问午睡、小睡 | `naps/SKILL.md` | `get_naps` |
| 问压力、焦虑、紧张 | `stress/SKILL.md` | `get_stress` |
| 问情绪、心情 | `emotion/SKILL.md` | `get_emotion` |
| 问步数、活动量、卡路里消耗 | `activity/SKILL.md` | `get_activity_data` |
| 问运动记录、跑步、健身 | `workouts/SKILL.md` | `get_workouts` |
| 问血氧、SpO2 | `blood-oxygen/SKILL.md` | `get_spo2` |
| 问血压、收缩压、舒张压 | `blood-pressure/SKILL.md` | `get_blood_pressure` |
| 问血糖（单日查询） | `blood-glucose/SKILL.md` | `get_blood_glucose` |
| 问体温、发烧 | `body-temperature/SKILL.md` | `get_body_temperature` |
| 问体重、BMI、体脂率 | `body-composition/SKILL.md` | `get_body_composition` |
| 问营养摄入、饮食热量 | `nutrition/SKILL.md` | `get_nutrition` |
| 问月经周期、经期 | `menstrual-cycle/SKILL.md` | `get_menstrual_cycle` |
| 问 VO2Max、有氧能力 | `vo2max/SKILL.md` | `get_vo2max` |
| 需要同时获取多项数据 | `multi-metric/SKILL.md` | 多工具并行 |

---

### 多指标综合教练型

> 适用：用户问题涉及多个健康维度、需要综合分析或专业指导。优先使用这类 skill。

| 用户意图 | 加载 skill | 涉及数据 |
|---------|-----------|---------|
| 睡眠问题、失眠、睡眠质量 | `sleep-coach/SKILL.md` | 睡眠 + 心率 + 血氧 + 压力 |
| 心率/HRV/心血管综合分析 | `heart-monitor/SKILL.md` | 心率 + HRV + 静息心率 + 运动 |
| 压力大、焦虑、倦怠、情绪低落 | `stress-management/SKILL.md` | 压力 + 心率 + 睡眠 + HRV + 情绪 |
| 运动分析、训练负荷、恢复状态 | `workout-tracker/SKILL.md` | 运动 + 心率 + 睡眠 + HRV |
| 血糖 + 饮食关联分析、糖尿病风险 | `blood-sugar-coach/SKILL.md` | 血糖 + 营养 |
| 体温 + 月经周期分析、BBT 排卵 | `body-temp-coach/SKILL.md` | 体温 + 经期 |
| 体重管理、减脂、增肌、体成分 | `weight-management/SKILL.md` | 体成分 + 运动 + 营养 + 经期 |
| 月经周期规律、女性健康指导 | `reproductive-health/SKILL.md` | 经期 + 体温 + 情绪 + 睡眠 |
| 每周健康总结、周回顾 | `weekly-review/SKILL.md` | 活动 + 睡眠 + 心率 + 运动 + 压力 |
| "我今天怎么样"、整体健康概览 | `health-overview/SKILL.md` | 活动数据综合 |

---

## 三、加载策略

### 渐进式加载（推荐）

```
用户提问
  → 读本索引，定位 skill
  → 加载对应 skill 完整内容
  → 执行 CLI 命令获取数据
  → 按 skill 分析框架输出结论
```

### 何时用单指标 skill vs 教练型 skill

- 用户问"我今天心率多少" → 单指标 `heart-rate/SKILL.md`
- 用户问"我心率最近是不是有问题" → 教练型 `heart-monitor/SKILL.md`（多维分析）
- 用户问"我最近睡不好" → 教练型 `sleep-coach/SKILL.md`
- 用户说"帮我看看压力" → 教练型 `stress-management/SKILL.md`

### 不确定时的降级策略

1. 先加载对应单指标 skill 获取数据
2. 若数据提示需要跨域分析，再加载对应教练型 skill
3. 若用户问题横跨多个领域，加载 `multi-metric/SKILL.md` 并行拉取数据

---

## 四、skill 分类速览

```
单指标（19个）：heart-rate / resting-heart-rate / hrv / heart-rhythm /
                sleep / naps / stress / emotion / activity / workouts /
                blood-oxygen / blood-pressure / blood-glucose / body-temperature /
                body-composition / nutrition / menstrual-cycle / vo2max / multi-metric

教练型（10个）：sleep-coach / heart-monitor / stress-management / workout-tracker /
               blood-sugar-coach / body-temp-coach / weight-management /
               reproductive-health / weekly-review / health-overview
```

---

## 五、时间查询通用规范

### 时间映射规则

| 用户表述 | 查询范围 |
|---------|---------|
| 无时间词 | 不传时间参数，由工具默认 |
| "最近" / "这几天" | 最近 7 天（今天往前 6 天） |
| "本周" | 本周一至今天 |
| "上周" | 上周完整自然周（周一至周日） |
| "本月" | 当月 1 号至今天 |
| "上个月" | 上个自然月（完整月份） |
| "这 X 周" | 本周一往前 (X-1)×7 天至今天 |
| "最近 X 周" | 今天往前 X×7 天 |
| "最近 X 个月" | 今天往前 X 个月同日至今天 |
| "上 X 个月前" | 对应完整自然月 |
| "至今" / "历史" / "长期" | 两年前的今天至今 |

### 日期准确性（不可违反）

回复中引用的数据日期必须与查询结果中的实际日期严格一致：

- **查到哪天说哪天**，不可将 A 日期的数据说成 B 日期
- 多日范围查询中部分日期有数据、部分无数据时，必须区分说明，不得以有数据的日期泛化整个范围
- 无数据的日期如实告知，不跳过、不用相邻日期数据替代

### 查询"上次何时"

当用户询问某事上次何时发生（"上次血压高是什么时候"、"上次跑步是哪天"）：

1. 先查**最近 1 个月**
2. 无数据 → 往前扩查至**最近 3 个月**（共 4 个月）
3. 仍无数据 → 最多扩展到**最近 1 年**
4. 超过 1 年仍未找到 → 如实告知暂无相关记录
