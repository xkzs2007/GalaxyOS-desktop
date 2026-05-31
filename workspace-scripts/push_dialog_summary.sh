#!/bin/bash
# 对话结束推送到负一屏
# 用法: ./push_dialog_summary.sh "对话摘要内容"

SUMMARY="${1:-对话已结束}"
TIMESTAMP=$(date +%s)
TASK_ID="dialog_${TIMESTAMP}"

# 创建 JSON
cat > /tmp/dialog_summary.json << JSONEOF
{
  "task_name": "对话摘要",
  "task_content": "$SUMMARY",
  "task_result": "对话已结束"
}
JSONEOF

# 调用推送脚本
cd ~/.openclaw/workspace/skills/today-task
python scripts/task_push.py --data /tmp/dialog_summary.json
