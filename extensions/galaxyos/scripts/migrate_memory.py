#!/usr/bin/env python3
"""
memory-tdai → GalaxyOS 记忆迁移脚本

从旧 memory-tdai 的 unified_vectors.db 读取 27K 条记忆，
逐条灌入当前 GalaxyOS 的 UnifiedEntry（重新用 128-dim embedding 编码）。

用法:
  python3 migrate_memory.py              # 全量迁移
  python3 migrate_memory.py --dry-run    # 只预览，不写
  python3 migrate_memory.py --batch 500  # 每批 500 条
"""

import os, sys, json, time, hashlib, sqlite3, argparse
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.openclaw/extensions/galaxyos/scripts"))

def get_stats():
    """迁移前后统计"""
    # 当前 jsonl 条数
    jsonl_path = Path("~/.openclaw/workspace/.learnings/verified_memories.jsonl").expanduser()
    current_count = 0
    if jsonl_path.exists():
        with open(jsonl_path) as f:
            current_count = sum(1 for _ in f)
    
    # 旧库条数
    old_db = Path("~/.openclaw/memory-tdai/unified_vectors.db").expanduser()
    old_count = 0
    if old_db.exists():
        conn = sqlite3.connect(str(old_db))
        old_count = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
        conn.close()
    
    return current_count, old_count

def migrate(dry_run=False, batch_size=100):
    old_db_path = Path("~/.openclaw/memory-tdai/unified_vectors.db").expanduser()
    
    if not old_db_path.exists():
        print(f"❌ 旧数据库不存在: {old_db_path}")
        return
    
    before, total = get_stats()
    print(f"迁移前: 当前系统 {before} 条, 待迁移 {total} 条")
    
    if dry_run:
        print("🧪 dry-run 模式，不会写入")
    
    # 连接旧库
    conn = sqlite3.connect(str(old_db_path))
    cursor = conn.execute("SELECT id, content, metadata, source FROM vectors ORDER BY rowid")
    
    # 导入 UnifiedEntry
    if not dry_run:
        sys.path.insert(0, str(Path("~/.openclaw/extensions/galaxyos/scripts").expanduser()))
        os.chdir(str(Path("~/.openclaw/workspace").expanduser()))
        from unified_entry import UnifiedEntry
        entry = UnifiedEntry()
    
    migrated = 0
    skipped = 0
    errors = 0
    t0 = time.time()
    
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        
        for row_id, content, meta_json, source in rows:
            if not content or len(content.strip()) < 10:
                skipped += 1
                continue
            
            # 解析 metadata 提取有用字段
            meta = {}
            if meta_json:
                try:
                    meta = json.loads(meta_json)
                except json.JSONDecodeError:
                    pass
            
            # 生成内容 hash 作为去重依据
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            
            if dry_run:
                migrated += 1
                if migrated <= 3:
                    print(f"  [{migrated}] {source}: {content[:80]}...")
                continue
            
            try:
                result = entry.store(
                    content=content,
                    source=source or "memory_tdai_migration",
                    # session_id 统一为空（旧数据不限定 session）
                )
                if isinstance(result, dict) and "error" in result:
                    errors += 1
                    print(f"  ⚠ 存储失败 [{row_id[:12]}]: {result['error']}")
                else:
                    migrated += 1
            except Exception as e:
                errors += 1
                print(f"  ❌ 异常 [{row_id[:12]}]: {e}")
        
        elapsed = time.time() - t0
        rate = migrated / elapsed if elapsed > 0 else 0
        print(f"  进度: {migrated}/{total} 迁移, {skipped} 跳过, {errors} 错误 | {rate:.1f} 条/秒")
        
        # 每批稍歇，防 API 限流
        if not dry_run:
            time.sleep(0.5)
    
    conn.close()
    
    after, _ = get_stats()
    elapsed = time.time() - t0
    print(f"\n✅ 迁移完成")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  迁移: {migrated} 条")
    print(f"  跳过: {skipped} 条（内容过短）")
    print(f"  错误: {errors} 条")
    print(f"  迁移前: {before} 条 → 迁移后: {after} 条")
    print(f"  速率: {migrated/elapsed:.1f} 条/秒")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="memory-tdai → GalaxyOS 记忆迁移")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写入")
    parser.add_argument("--batch", type=int, default=500, help="每批条数（默认500）")
    args = parser.parse_args()
    
    migrate(dry_run=args.dry_run, batch_size=args.batch)
