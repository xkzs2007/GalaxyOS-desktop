#!/usr/bin/env python3
"""回溯填充旧 DAG 节点的 scene_trace"""

import sys
import os
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from smart_processor import SmartProcessor

# 初始化 SmartProcessor
sp = SmartProcessor()
if not getattr(sp.llm, 'api_key', None):
    print("❌ SmartProcessor 初始化失败，没有 api_key")
    sys.exit(1)

BASE_URL = sp.llm.base_url.rstrip('/')
API_KEY = sp.llm.api_key
DB_PATH = os.path.expanduser("~/.openclaw/dag_context.db")
OLD_DB_PATH = DB_PATH  # 统一走 expanduser 路径，不再硬编码 sandbox
RATE_LIMIT_SLEEP = 1.0  # 每次请求后等 1 秒
BATCH_SIZE = 5  # 并发批大小

def generate_scene_trace(content: str) -> str:
    """为一条消息生成 scene_trace"""
    import httpx
    prompt = (
        f"为下面这条消息生成一个简短的场景轨迹（30-60字），"
        f"说明这个消息出现在什么情境中、用户想达成什么目的。\n"
        f"格式：[场景] 用户/AI正在做某事，想达成某目的。\n\n"
        f"[消息内容]\n{content[:500]}"
    )
    data = {
        "model": "deepseek-v4-flash",
        "messages": [
            {"role": "system", "content": "你是一个知识丰富的AI助手，请基于提供的上下文回答用户问题。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 200,
        "temperature": 0.7,
        "extra_body": {"user_id": "dag-scene-encoder", "thinking": True},
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{BASE_URL}/chat/completions", json=data, headers=headers)
        if resp.status_code == 200:
            j = resp.json()
            msg = j["choices"][0]["message"]
            return (msg.get("content") or msg.get("reasoning_content") or "")[:120]
        return ""

def backfill_db(db_path: str, label: str):
    if not os.path.exists(db_path):
        print(f"  {label}: DB 不存在，跳过")
        return 0, 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 检查 scene_trace 列是否存在
    cols = [r[1] for r in conn.execute("PRAGMA table_info(dag_nodes)").fetchall()]
    if 'scene_trace' not in cols:
        conn.execute("ALTER TABLE dag_nodes ADD COLUMN scene_trace TEXT DEFAULT ''")
        conn.commit()
        print(f"  {label}: 添加 scene_trace 列")

    # 找出需要回溯的节点（有内容、非摘要、scene_trace 为空）
    rows = conn.execute(
        "SELECT node_id, content, length(content) as clen FROM dag_nodes "
        "WHERE (scene_trace IS NULL OR scene_trace = '') AND length(content) >= 20 "
        "AND is_summary = 0 ORDER BY timestamp ASC"
    ).fetchall()

    if not rows:
        print(f"  {label}: 没有需要回溯的节点 ✅")
        conn.close()
        return 0, 0

    print(f"  {label}: 找到 {len(rows)} 个需要回溯的节点")

    def process(row):
        node_id = row["node_id"]
        content = row["content"]
        trace = generate_scene_trace(content)
        if trace:
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE dag_nodes SET scene_trace = ? WHERE node_id = ?", (trace, node_id))
            conn.commit()
            conn.close()
            return node_id, trace
        return node_id, ""

    success = 0
    total = len(rows)

    # 分批并发
    for i in range(0, total, BATCH_SIZE):
        batch = rows[i:i+BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            futs = {executor.submit(process, row): row for row in batch}
            for fut in as_completed(futs):
                try:
                    node_id, trace = fut.result()
                    if trace:
                        success += 1
                        print(f"    ✅ [{i//BATCH_SIZE + 1}/{total // BATCH_SIZE + 1}] {node_id[:24]}... → {trace[:40]}...")
                    else:
                        print(f"    ❌ [{i//BATCH_SIZE + 1}/{total // BATCH_SIZE + 1}] {node_id[:24]}... → 生成失败")
                except Exception as e:
                    print(f"    ❌ 异常: {e}")
        time.sleep(RATE_LIMIT_SLEEP)

    conn.close()
    return success, total

if __name__ == "__main__":
    print("=" * 60)
    print("scene_trace 回溯填充")
    print("=" * 60)

    # 回溯两个 DB
    for db_path, label in [(DB_PATH, "workspace DB"), (OLD_DB_PATH, "old DB")]:
        s, t = backfill_db(db_path, label)
        print(f"  {label}: 成功 {s}/{t}")

    print("=" * 60)
    print("完成 ✅")
    print(f"注意: 共触发 {t} 次 DeepSeek Flash API 调用，注意查看用量")
