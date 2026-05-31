# 维护指南

## 维护任务

### 每周任务

| 任务 | 时间 | 脚本 |
|------|------|------|
| 完整维护 | 周一 03:00 | `run_maintenance.py` |
| FTS 重建 | 周日 02:00 | `rebuild_fts.py` |

### 每日任务

| 任务 | 时间 | 脚本 |
|------|------|------|
| 覆盖率检查 | 06:00 | `check_coverage.py` |

## 手动执行

```bash
# 完整维护
python3 ~/.openclaw/workspace/skills/llm-memory-integration/scripts/run_maintenance.py

# 检查覆盖率
python3 ~/.openclaw/workspace/skills/llm-memory-integration/scripts/check_coverage.py

# 重建 FTS
python3 ~/.openclaw/workspace/skills/llm-memory-integration/scripts/rebuild_fts.py
```

## 监控阈值

| 指标 | 阈值 | 说明 |
|------|------|------|
| L1 覆盖率 | ≥ 95% | 结构化记忆向量覆盖 |
| L0 覆盖率 | ≥ 60% | 原始对话向量覆盖 |
| 数据库大小 | < 100 MB | 定期清理 |
| 零向量 | < 5 条 | 需要修复 |

## 日志位置

- 维护日志: `~/.openclaw/memory-tdai/.metadata/maintenance.log`
- 推送记录: `~/.openclaw/workspace/skills/today-task/push_records/`

## 故障排查

### 覆盖率下降
1. 检查向量 API 是否正常
2. 运行 `optimize_vector_system.py` 修复
3. 检查 memory-tencentdb 插件状态

### 数据库过大
1. 运行 VACUUM 清理
2. 检查是否有孤立向量
3. 考虑归档旧数据

### FTS 搜索失效
1. 运行 `rebuild_fts.py` 重建索引
2. 检查 FTS 表是否存在
3. 验证分词器配置
