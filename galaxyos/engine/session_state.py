#!/usr/bin/env python3
"""
会话状态快照管理脚本（方案 A - 自动保存 + 方案 B - 恢复）

功能：
1. capture: 捕获当前会话状态并保存到 memory/session-state-latest.json
2. recall: 读取最新会话状态，输出结构化摘要

用法：
  python3 scripts/session_state.py capture [--topic "xxx"] [--file "file1,file2"]
  python3 scripts/session_state.py recall
  python3 scripts/session_state.py recall --brief

集成：
  - 建议在 AGENTS.md 的 Every Session 节中调用 recall
  - 建议在对话结束时自动调用 capture
"""

import json
import os
import sys
import datetime

STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "memory", "session-state-latest.json")
DAILY_FILE_PREFIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "memory")
ACTIVE_TASKS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "skills", "proactive-tasks", "tasks", "active_tasks.json")


def load_state():
    """加载当前最新状态"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    return None


def save_state(data):
    """保存状态到文件"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_daily_summary(date_str):
    """获取某天记忆文件的摘要（前 200 字）"""
    filepath = os.path.join(DAILY_FILE_PREFIX, f"{date_str}.md")
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read(200)
        return content.strip()
    return None


# ====== 方案 A：捕获会话状态 ======

def cmd_capture(args):
    """捕获当前会话状态"""
    topic = args.get("--topic") or ""
    active_files_str = args.get("--file") or ""

    active_files = [f.strip() for f in active_files_str.split(",") if f.strip()]

    now = datetime.datetime.now()

    # 读取已有状态（保留未更新的字段）
    old_state = load_state() or {}

    new_state = {
        "captured_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "captured_date": now.strftime("%Y-%m-%d"),
        "captured_weekday": ["周一","周二","周三","周四","周五","周六","周日"][now.weekday()],
        "topic": topic or old_state.get("topic", ""),
        "active_files": active_files or old_state.get("active_files", []),
        "pending_decisions": old_state.get("pending_decisions", []),
        "reasoning_chain": old_state.get("reasoning_chain", []),
        "unresolved_issues": old_state.get("unresolved_issues", []),
        "active_task": load_active_task(),
        "conversation_summary": old_state.get("conversation_summary", ""),
        "version": 2
    }

    save_state(new_state)
    print(f"✅ 会话状态已保存 ({new_state['captured_at']})")
    return new_state


def load_active_task():
    """从 proactive-tasks 加载当前活跃任务"""
    if os.path.exists(ACTIVE_TASKS_FILE):
        try:
            with open(ACTIVE_TASKS_FILE, 'r', encoding='utf-8') as f:
                tasks = json.load(f)
            if isinstance(tasks, list) and tasks:
                active = [t for t in tasks if t.get("status") in ("active", "pending")]
                if active:
                    return {
                        "title": active[0].get("title", ""),
                        "status": active[0].get("status", ""),
                        "priority": active[0].get("priority", "")
                    }
        except (json.JSONDecodeError, IOError):
            pass
    return None


# ====== 方案 B：恢复会话上下文 ======

def cmd_recall(args):
    """恢复会话上下文"""
    state = load_state()
    if not state:
        print("⚠️ 没有找到历史会话状态。这可能是首次启动或状态文件已过期。")
        return

    brief = args.get("--brief", False)
    captured = state.get("captured_at", "未知")
    date = state.get("captured_date", "")
    weekday = state.get("captured_weekday", "")

    # 检查是否过期（超过 24 小时 = 旧会话）
    now = datetime.datetime.now()
    try:
        captured_dt = datetime.datetime.strptime(captured, "%Y-%m-%d %H:%M:%S")
        hours_diff = (now - captured_dt).total_seconds() / 3600
        is_stale = hours_diff > 24
    except ValueError:
        is_stale = False

    if is_stale and not brief:
        # 过期状态，尝试从记忆文件补充
        daily_summary = get_daily_summary(date.replace("-", "-"))
        print(f"📋 上次会话状态（{captured}，{weekday}）")
        print(f"   话题：{state.get('topic', '未记录')}")
        print(f"   状态文件已过期（{hours_diff:.0f} 小时前），请结合记忆文件获取完整上下文。")
        return

    topic = state.get("topic", "")
    active_files = state.get("active_files", [])
    pending = state.get("pending_decisions", [])
    reasoning = state.get("reasoning_chain", [])
    unresolved = state.get("unresolved_issues", [])
    active_task = state.get("active_task", None)

    if brief:
        # 简要模式：只输出关键信息
        parts = [f"📋 上回说到（{captured}）"]
        if topic:
            parts.append(f"  话题：{topic}")
        if pending:
            parts.append(f"  待决策：{len(pending)} 项")
        if active_task:
            parts.append(f"  进行中：{active_task.get('title', '')}")
        print("\n".join(parts))
        return

    print(f"📋 上回说到（{captured}，{weekday}）")
    print("=" * 40)

    if topic:
        print(f"\n📌 话题：{topic}")

    if active_files:
        print("\n📄 涉及文件：")
        for f in active_files:
            print(f"  • {f}")

    if pending:
        print("\n🤔 待决策项：")
        for p in pending:
            print(f"  • {p}")

    if reasoning:
        print("\n🧠 已有推理结论：")
        for r in reasoning:
            print(f"  • {r}")

    if unresolved:
        print("\n❓ 未解决事项：")
        for u in unresolved:
            print(f"  • {u}")

    if active_task:
        print(f"\n🎯 进行中的任务：{active_task.get('title', '')}")
        print(f"   状态：{active_task.get('status', '')}")

    print("\n" + "=" * 40)


# ====== 主入口 ======

def main():
    if len(sys.argv) < 2:
        print("用法：")
        print("  python3 scripts/session_state.py capture [--topic 话题] [--file 文件1,文件2]")
        print("  python3 scripts/session_state.py recall [--brief]")
        print("")
        print("capture — 捕获当前会话状态")
        print("recall  — 恢复上次会话上下文")
        print("  --brief  只输出摘要")
        return

    cmd = sys.argv[1]
    args = {}
    i = 2
    while i < len(sys.argv):
        key = sys.argv[i]
        if key.startswith("--"):
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                args[key] = sys.argv[i + 1]
                i += 2
            else:
                args[key] = True
                i += 1
        else:
            i += 1

    if cmd == "capture":
        cmd_capture(args)
    elif cmd == "recall":
        cmd_recall(args)
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
