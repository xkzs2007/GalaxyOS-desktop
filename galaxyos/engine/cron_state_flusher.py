#!/usr/bin/env python3
"""
cron_state_flusher.py — GalaxyOS 组件

作用: Gateway 关闭前强制将 cron 运行时状态写盘，避免 picod SIGTERM 后
     cron 调度器 state 丢失导致任务重复投递。

工作流:
  1. 监听 SIGTERM/SIGINT
  2. 调用 `openclaw cron list --json` 获取当前 cron 任务状态
  3. 同步写入 ~/.openclaw/cron/jobs-state.json
  4. 写入 ~/.openclaw/cron/.last_shutdown 标记文件（记录退出时间戳）
  5. 提供去重查询: Gateway 启动时检测 .last_shutdown 是否 < 2min，
     如是则对已投递任务跳过重跑

依赖: Python 3 标准库（os, signal, json, subprocess, time, datetime）
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone


CRON_DIR = os.path.expanduser("~/.openclaw/cron")
STATE_FILE = os.path.join(CRON_DIR, "jobs-state.json")
JOBS_FILE = os.path.join(CRON_DIR, "jobs.json")
SHUTDOWN_MARKER = os.path.join(CRON_DIR, ".last_shutdown")
CRON_BIN = "openclaw"  # 假设在 PATH 中
DEDUP_WINDOW_SECONDS = 120  # 2 分钟内重启视为非正常重启


def get_cron_state():
    """通过 openclaw CLI 获取当前 cron 任务运行时状态"""
    try:
        result = subprocess.run(
            [CRON_BIN, "cron", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        sys.stderr.write(f"[cron_state_flusher] get_cron_state failed: {e}\n")
    return None


def flush_state():
    """强行同步写 cron state 到磁盘"""
    state = get_cron_state()
    if state is None:
        # 降级: 至少读当前 state 文件写回去（刷缓存）
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    state = json.load(f)
            except (json.JSONDecodeError, OSError):
                state = {"version": 1, "jobs": {}}
        else:
            state = {"version": 1, "jobs": {}}

    try:
        os.makedirs(CRON_DIR, exist_ok=True)
        # 同步写入（不使用缓冲）
        with open(STATE_FILE, "w") as f:
            f.write(json.dumps(state, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())

        # 写关闭标记
        now_ts = time.time()
        with open(SHUTDOWN_MARKER, "w") as f:
            f.write(f"{now_ts}\n")
            f.flush()
            os.fsync(f.fileno())

        sys.stderr.write(
            f"[cron_state_flusher] state flushed OK @ {datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat()}\n"
        )
    except OSError as e:
        sys.stderr.write(f"[cron_state_flusher] flush failed: {e}\n")


def check_unclean_restart():
    """检查是否为非正常重启（需要去重）"""
    if not os.path.exists(SHUTDOWN_MARKER):
        return False
    try:
        with open(SHUTDOWN_MARKER) as f:
            last_ts = float(f.readline().strip())
        elapsed = time.time() - last_ts
        return elapsed < DEDUP_WINDOW_SECONDS
    except (OSError, ValueError):
        return False


def dedup_pending_jobs():
    """
    去重逻辑: 读取 state 中各 job 的 lastRunAt，
    如果 lastRunAt 在 DEDUP_WINDOW_SECONDS 内，标记为已执行。
    返回需要跳过的 job ids。
    """
    now = int(time.time() * 1000)  # ms
    skip_ids = set()

    if not os.path.exists(STATE_FILE):
        return skip_ids

    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return skip_ids

    jobs = state.get("jobs", {})
    if isinstance(jobs, list):
        # 旧格式: jobs 是数组
        for job in jobs:
            jid = job.get("id") or job.get("name")
            last_run = job.get("lastRunAt") or job.get("last_run_at_ms")
            if jid and last_run and (now - last_run) < (DEDUP_WINDOW_SECONDS * 1000):
                skip_ids.add(jid)
    elif isinstance(jobs, dict):
        # 新格式: jobs 是 dict
        for jid, job in jobs.items():
            if isinstance(job, dict):
                last_run = job.get("lastRunAt") or job.get("last_run_at_ms")
                if last_run and (now - last_run) < (DEDUP_WINDOW_SECONDS * 1000):
                    skip_ids.add(jid)

    return skip_ids


def signal_handler(signum, frame):
    """SIGTERM/SIGINT handler"""
    sys.stderr.write(f"[cron_state_flusher] received signal {signum}, flushing state...\n")
    flush_state()
    sys.exit(0)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "dedup":
        # 去重模式: 启动时调用，返回需要跳过的 job ids
        is_unclean = check_unclean_restart()
        skip = dedup_pending_jobs()
        result = {
            "unclean_restart": is_unclean,
            "skip_job_ids": list(skip),
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    if len(sys.argv) > 1 and sys.argv[1] == "flush":
        # 主动 flush 模式: 立即刷 state
        flush_state()
        return

    # 守护模式: 注册 signal handler，等待信号
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # 写入一个启动标记（启动时先刷一次）
    flush_state()

    # 等待信号
    signal.pause()


if __name__ == "__main__":
    main()
