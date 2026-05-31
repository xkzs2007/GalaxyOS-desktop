# 未整合组件清单

## 扫描时间
2026-04-21 12:20

## 发现的未整合组件

### 1. 自主任务系统 (autonomous-tasks)
- **位置**: `skills/autonomous-tasks/`
- **版本**: v10.3.1
- **内容**:
  - `agents/AUTONOMOUS.md` - 自主任务配置
  - `agents/memory/` - 自主任务记忆
- **状态**: ⚠️ 未集成到主流程

### 2. 主动 Agent 系统 (hz-proactive-agent)
- **位置**: `skills/hz-proactive-agent/`
- **内容**:
  - `SKILL.md` - 主动 Agent 技能文档
  - `scripts/security-audit.sh` - 安全审计脚本
  - `references/` - 参考资料
- **状态**: ⚠️ 未集成到心跳系统

### 3. 自然语言规划器 (natural-language-planner)
- **位置**: `skills/natural-language-planner/`
- **内容**:
  - `scripts/dashboard_server.py` - Kanban 仪表板服务器
  - `scripts/file_manager.py` - 文件管理
  - `scripts/index_manager.py` - 索引管理
  - `templates/` - 模板
- **状态**: ⚠️ 未启动仪表板服务

### 4. Git 笔记记忆 (git-notes-memory)
- **位置**: `skills/git-notes-memory/`
- **内容**:
  - `memory.py` - Git 笔记记忆系统
  - `config.json` - 配置
- **状态**: ⚠️ 未集成到记忆系统

### 5. Proactive Tasks 脚本
- **位置**: `skills/proactive-tasks/scripts/`
- **内容**:
  - `task_manager.py` - 任务管理器
  - `memory_integration.py` - 记忆集成
- **状态**: ⚠️ 未在心跳中调用

### 6. Brain 知识库
- **位置**: `workspace/brain/`
- **内容**:
  - `people/` - 人物记忆
  - `orgs/` - 组织记忆
  - `ideas/` - 想法记录
  - `tech/` - 技术记录
- **状态**: ⚠️ 未与腾讯云记忆同步

### 7. Today-Task 推送服务
- **位置**: `skills/today-task/`
- **配置**:
  - `pushServiceUrl` - 推送服务地址
  - `records_dir` - 记录目录
- **状态**: ✅ 已配置，但未主动调用

### 8. xiaoyi-channel 插件
- **位置**: `extensions/xiaoyi-channel/`
- **功能**: 小艺通道通信
- **状态**: ✅ 已启用

### 9. 设备配对信息
- **位置**: `~/.openclaw/devices/`
- **内容**:
  - `paired.json` - 已配对设备
  - `pending.json` - 待配对设备
- **状态**: ℹ️ 信息性文件

### 10. 身份认证信息
- **位置**: `~/.openclaw/identity/`
- **内容**:
  - `device.json` - 设备信息
  - `device-auth.json` - 认证信息
- **状态**: ℹ️ 信息性文件

## 建议整合优先级

### 🔴 高优先级
1. **autonomous-tasks** - 集成到心跳，实现自主工作
2. **hz-proactive-agent** - 集成到心跳，实现主动建议
3. **proactive-tasks/task_manager.py** - 心跳时检查并执行任务

### 🟡 中优先级
4. **natural-language-planner** - 启动 Kanban 仪表板
5. **git-notes-memory** - 集成到记忆系统
6. **brain 知识库** - 与腾讯云记忆同步

### 🟢 低优先级
7. **today-task** - 完善推送触发逻辑
8. **devices/identity** - 信息性文件，无需整合
