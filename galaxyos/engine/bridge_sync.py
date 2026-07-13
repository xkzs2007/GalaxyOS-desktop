#!/usr/bin/env python3
"""
腾讯云记�?�?UnifiedVectorStore 批量同步

�?vectors.db �?l1_records 读取文本数据�?
通过 unified_entry.py store 逐条写入�?
"""

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from galaxyos.shared.paths import galaxyos_home, workspace

TENCENTDB = Path(galaxyos_home()) / "memory-tdai"
VECTORS_DB = TENCENTDB / "vectors.db"
UNIFIED_DB = TENCENTDB / "unified_vectors.db"
UNIFIED_ENTRY = Path(workspace()) / "skills" / "galaxyos-engine" / "scripts" / "unified_entry.py"
WORKSPACE = Path(workspace())

def get_unified_count():
    try:
        import sqlite3
        if UNIFIED_DB.exists():
            conn = sqlite3.connect(str(UNIFIED_DB))
            cnt = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
            conn.close()
            return cnt
    except Exception: pass
    return 0

def store(content, source="memory-tdai"):
    try:
        r = subprocess.run(
            ["python3", str(UNIFIED_ENTRY), "store", "--content", content, "--source", source, "--json"],
            capture_output=True, text=True, timeout=30, cwd=str(WORKSPACE)
        )
        if r.returncode == 0:
            d = json.loads(r.stdout)
            return True, d.get("memory_id", "")
        return False, r.stderr[:200]
    except Exception as e:
        return False, str(e)

def sync_batch(rows, batch_size=3):
    success, failed = 0, 0
    total = len(rows)

    for i in range(0, total, batch_size):
        batch = rows[i:i+batch_size]
        for item in batch:
            ok, mid = store(item["content"], "memory-tdai")
            if ok:
                success += 1
            else:
                failed += 1
            time.sleep(0.2)  # 限�?
        print(f"\r  进度: {min((i+batch_size)/total*100, 100):.0f}% ({success+failed}/{total})", end="")

    print()
    return success, failed

def main():
    dry_run = "--dry-run" in sys.argv

    # 读取 L1 数据
    conn = sqlite3.connect(str(VECTORS_DB))
    rows = conn.execute("""
        SELECT record_id, content, type, priority
        FROM l1_records
        ORDER BY priority DESC
    """).fetchall()
    conn.close()

    items = [{"record_id": r[0], "content": r[1], "type": r[2], "priority": r[3]} for r in rows]

    before = get_unified_count()
    print(f"同步�?UnifiedVectorStore: {before} �?)
    print(f"待同�?L1 记录: {len(items)} �?)

    if dry_run:
        print(f"预览最高优先级: {items[0]['content'][:60]}...")
        print("�?Dry run, 未写�?)
        return

    success, failed = sync_batch(items)

    after = get_unified_count()
    print(f"\n同步�?UnifiedVectorStore: {after} �?)
    print(f"成功: {success} | 失败: {failed} | 新增: {after - before}")

if __name__ == "__main__":
    main()
