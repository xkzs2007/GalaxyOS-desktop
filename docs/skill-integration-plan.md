# 待激活技能集成方案

## 概述

将低利用率但有价值的技能集成到小艺 Claw 系统架构中，提升整体能力。

---

## 🎯 高优先级集成

### 1. excalidraw-diagram — 图表绘制

**当前状态**: 未使用
**价值**: 可视化能力强，适合画流程图、架构图
**集成位置**: Layer 12 多模态生成层

**集成方案**:
```python
# visual_generation.py 中新增
from pathlib import Path
import subprocess
import json

# Excalidraw 技能路径
EXCALIDRAW_SKILL_PATH = os.path.expanduser(
    "~/.openclaw/workspace/skills/axton-obsidian-visual-skills-excalidraw-diagram"
)

def generate_diagram(self, content: str, diagram_type: str = "flowchart") -> Dict[str, Any]:
    """
    生成图表（使用 excalidraw-diagram）
    
    Args:
        content: 图表内容描述
        diagram_type: 图表类型 (flowchart, architecture, mindmap, sequence)
    
    Returns:
        图表生成结果
    """
    logger.info(f"生成图表: {diagram_type}")
    
    # 构建 prompt
    prompt_map = {
        "flowchart": f"生成流程图：{content}",
        "architecture": f"生成架构图：{content}",
        "mindmap": f"生成思维导图：{content}",
        "sequence": f"生成时序图：{content}"
    }
    
    prompt = prompt_map.get(diagram_type, f"生成图表：{content}")
    
    # 调用 excalidraw-diagram（通过 skill 机制）
    # 这里需要根据 excalidraw-diagram 的实际 API 调用
    
    return {
        "success": True,
        "diagram_type": diagram_type,
        "content": content,
        "message": "图表生成功能已集成，等待 excalidraw-diagram 调用实现"
    }
```

**应用场景**:
- 系统架构图
- 流程图
- 思维导图
- 时序图

---

### 2. weather — 天气查询

**当前状态**: 未使用
**价值**: 实用性强，用户常问
**集成位置**: 心跳检查 + 主动推送

**集成方案**:
```python
# heartbeat_task_executor.py 中新增
def check_weather(self):
    """检查天气并推送提醒"""
    weather = self.get_weather()
    if weather.get('alert'):  # 恶劣天气预警
        self.push_alert(weather['alert'])
    return weather
```

**应用场景**:
- 心跳时检查天气
- 用户问天气时直接回答
- 恶劣天气预警推送

---

### 3. deep-search-and-insight-synthesize — 深度调研

**当前状态**: 未使用
**价值**: 系统性调研能力强
**集成位置**: Layer 2 检索增强层

**集成方案**:
```python
# crag_retriever.py 中新增
def deep_research(self, topic: str):
    """
    深度调研
    
    Args:
        topic: 调研主题
    
    Returns:
        调研报告
    """
    # 调用 deep-search-and-insight-synthesize
    pass
```

**应用场景**:
- 用户说"调研一下 xxx"
- 需要多源信息综合分析
- 生成专业调研报告

---

### 4. best-minds — 名人思维模拟

**当前状态**: 未使用
**价值**: 多角度思考，增强决策
**集成位置**: Layer 11 思考技能层

**集成方案**:
```python
# skill_coordinator.py 中新增
def simulate_expert_thinking(self, problem: str, experts: List[str]):
    """
    模拟专家思维
    
    Args:
        problem: 问题
        experts: 专家列表 (如 ["乔布斯", "马斯克", "巴菲特"])
    
    Returns:
        多角度分析结果
    """
    pass
```

**应用场景**:
- 用户说"乔布斯会怎么看这个问题"
- 需要多角度分析
- 创意发散

---

## 🟡 中优先级集成

### 5. multi-agent-collaboration — 多智能体协作

**当前状态**: 未使用
**价值**: 复杂任务分解与协作
**集成位置**: Layer 7 模块协调层

**应用场景**:
- 复杂项目分解
- 多角色协作
- 任务分配与协调

---

### 6. natural-language-planner — 自然语言规划

**当前状态**: 未使用
**价值**: 从自然语言生成执行计划
**集成位置**: Layer 7 模块协调层

**应用场景**:
- 用户说"帮我规划一下 xxx"
- 项目计划生成
- 任务分解

---

### 7. imap-smtp-email — 邮件收发

**当前状态**: 未配置
**价值**: 邮件自动化
**集成位置**: 小艺能力层

**需要配置**:
- IMAP 服务器地址
- SMTP 服务器地址
- 邮箱账号密码

**应用场景**:
- 检查邮件
- 发送邮件
- 邮件摘要

---

### 8. fitness-coach — 健身指导

**当前状态**: 未使用
**价值**: 健康管理
**集成位置**: 小艺能力层

**应用场景**:
- 制定健身计划
- 运动数据分析
- 健康建议

---

## 🟢 低优先级（按需激活）

### 9. brainhole-factory — 脑洞生成

**应用场景**: 创意发散、头脑风暴

### 10. webapp-testing — Web 测试

**应用场景**: 测试本地 Web 应用

### 11. ship-learn-next — 学习内容转换

**应用场景**: 将学习材料转换为结构化内容

---

## 📊 集成优先级矩阵

| 技能 | 价值 | 难度 | 优先级 |
|------|------|------|--------|
| excalidraw-diagram | 高 | 低 | 🔴 P0 |
| weather | 高 | 低 | 🔴 P0 |
| deep-search | 高 | 中 | 🔴 P0 |
| best-minds | 中 | 低 | 🟡 P1 |
| multi-agent | 高 | 高 | 🟡 P1 |
| nl-planner | 中 | 中 | 🟡 P1 |
| email | 高 | 中 | 🟡 P1 |
| fitness | 中 | 低 | 🟢 P2 |

---

## 🚀 实施计划

### Phase 1: 快速集成（本周）
- [ ] excalidraw-diagram → Layer 12
- [ ] weather → 心跳检查
- [ ] deep-search → Layer 2

### Phase 2: 深度集成（下周）
- [ ] best-minds → Layer 11
- [ ] multi-agent → Layer 7
- [ ] nl-planner → Layer 7

### Phase 3: 按需配置
- [ ] email → 需要用户配置邮箱
- [ ] fitness → 需要健康数据权限

---

## 📝 集成代码模板

```python
# unified_coordinator_v2.py 中新增

SKILL_INTEGRATIONS = {
    "excalidraw": {
        "skill": "excalidraw-diagram",
        "trigger": ["画图", "流程图", "架构图", "思维导图"],
        "layer": 12
    },
    "weather": {
        "skill": "weather",
        "trigger": ["天气", "气温", "下雨"],
        "layer": "heartbeat"
    },
    "deep_search": {
        "skill": "deep-search-and-insight-synthesize",
        "trigger": ["调研", "深入研究", "全面分析"],
        "layer": 2
    },
    "best_minds": {
        "skill": "best-minds",
        "trigger": ["怎么看", "如果是", "模拟"],
        "layer": 11
    }
}
```

---

**创建时间**: 2026-04-21
**作者**: 小艺 Claw
