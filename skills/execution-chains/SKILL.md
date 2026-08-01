---
name: execution-chains
description: "执行链引擎 - 规则驱动的任务执行。关键词：健康检查、心跳执行、记忆存储、记忆检索、工作流执行、系统状态"
---

# 执行链引擎

**核心原则**：小艺只提供运行能力，其他一切由规则决定。

---

## 执行链映射表

| 触发词 | 执行链 | 命令 |
|--------|--------|------|
| 健康检查、检查系统 | health_check | `unified_entry.py health` |
| 执行心跳、运行心跳 | heartbeat_run | `run_heartbeat.py` |
| 记住、记下来 | memory_store | `unified_entry.py store --content` |
| 回忆、检索 | memory_recall | `unified_entry.py recall --query` |
| 执行工作流 | workflow_run | `unified_entry.py workflow --scenario` |
| 系统状态、模块状态 | system_status | `unified_entry.py status` |
| 读取文件 | file_read | `cat [路径]` |
| 执行命令 | command_run | `[命令]` |

---

## 执行规则

### 五大铁律

1. **必须**按执行链定义执行
2. **禁止**自主选择工具
3. **禁止**修改命令参数
4. **禁止**在失败后尝试其他方案
5. **必须**按失败处理规则输出

### 失败处理规则

- 命令执行失败 → 输出 "[命令名]执行失败"，不做其他操作
- 返回 error → 输出 "失败：[error信息]"，不做其他操作
- 返回空结果 → 输出 "没有找到"，不做其他操作

---

## 执行链详细定义

### 执行链 1：健康检查

**触发条件**：用户说"健康检查"、"检查系统"、"系统状态"

**执行步骤**：
```bash
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py health
```

**预期结果**：`{"healthy": true, ...}`

**失败处理**：输出 "健康检查执行失败"

---

### 执行链 2：心跳执行

**触发条件**：用户说"执行心跳"、"运行心跳"、"后台任务"

**执行步骤**：
```bash
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/run_heartbeat.py
```

**预期结果**：`{"status": "success", ...}`

**失败处理**：输出 "心跳执行失败"

---

### 执行链 3：记忆存储

**触发条件**：用户说"记住"、"记下来"、"存储记忆" + 内容

**执行步骤**：
```bash
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py store --content "[内容]"
```

**预期结果**：`{"memory_id": "...", ...}`

**失败处理**：输出 "存储失败"

---

### 执行链 4：记忆检索

**触发条件**：用户说"回忆"、"检索"、"之前说过" + 查询内容

**执行步骤**：
```bash
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py recall --query "[查询]"
```

**预期结果**：返回 JSON 数组

**失败处理**：输出 "没有找到相关记忆"

---

### 执行链 5：工作流执行

**触发条件**：用户说"执行工作流" + 工作流名称

**执行步骤**：
```bash
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py workflow --scenario "[工作流名称]"
```

**预期结果**：`{"success_count": N, ...}`

**失败处理**：输出 "工作流执行失败"

---

### 执行链 6：系统状态

**触发条件**：用户说"系统状态"、"模块状态"、"查看状态"

**执行步骤**：
```bash
python3 ~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts/unified_entry.py status
```

**预期结果**：返回模块状态 JSON

**失败处理**：输出 "状态查询失败"

---

## 完整定义

见 `EXECUTION_CHAINS.md`

---

## 监控

所有执行链调用记录到日志，便于审计和调试。

---

**版本**：1.0.0
**创建时间**：2026-04-23
