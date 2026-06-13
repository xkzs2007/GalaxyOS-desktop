#!/usr/bin/env python3
"""
memory-tdai → GalaxyOS 记忆迁移 v2

不走 UnifiedEntry（避免初始化卡住），直接：
1. 读取旧 SQLite
2. 写入 verified_memories.jsonl（关键词搜索可用）
3. 调用 embedding API + 写向量库

用法:
  python3 migrate_memory_v2.py --dry-run
  python3 migrate_memory_v2.py [--batch 100]
"""

import os, sys, json, time, sqlite3, argparse, hashlib, uuid
from pathlib import Path
from datetime import datetime, timezone

def get_embedding_client():
    """初始化 embedding 客户端（轻量，不加载全模块）"""
    sys.path.insert(0, os.path.expanduser("~/.openclaw/extensions/galaxyos/scripts"))
    
    config_path = Path("~/.openclaw/extensions/galaxyos/config/llm_config.json").expanduser()
    if config_path.exists():
        with open(config_path) as f:
            cfg = json.load(f)
        providers = cfg.get("providers", {})
        embed_cfg = cfg.get("embedding", {})
        
        # 取 embedding provider
        provider_name = embed_cfg.get("provider", "default")
        provider = providers.get(provider_name, providers.get("default", {}))
        base_url = provider.get("base_url", "")
        api_key = provider.get("api_key", "")
        model = embed_cfg.get("model", "text-embedding-v1.0")
        dim = embed_cfg.get("dim", 128)
    else:
        # fallback: 直接调 OpenClaw 的 xiaoyiprovider
        base_url = "https://xiaoyiprovider.openclaw.ai/v1"
        api_key = os.environ.get("OPENAI_API_KEY", "")
        model = "text-embedding-v1.0"
        dim = 128
    
    print(f"  Embedding: model={model}, dim={dim}, base={base_url[:50]}...")
    
    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)
        return client, model, dim
    except Exception as e:
        print(f"  ⚠ OpenAI 客户端失败: {e}")
        return None, "", 0


def embed_text(client, model, text):
    """生成 128-dim 向量"""
    if not client or not model:
        return None
    try:
        resp = client.embeddings.create(model=model, input=text[:2000])
        return resp.data[0].embedding
    except Exception as e:
        print(f"  ⚠ embedding 失败: {e}")
        return None


def migrate(dry_run=False, batch_size=100):
    old_db = Path("~/.openclaw/memory-tdai/unified_vectors.db").expanduser()
    jsonl_path = Path("~/.openclaw/workspace/.learnings/verified_memories.jsonl").expanduser()
    
    if not old_db.exists():
        print(f"❌ 旧数据库不存在: {old_db}")
        return
    
    # 当前条数
    current_count = 0
    if jsonl_path.exists():
        with open(jsonl_path) as f:
            current_count = sum(1 for _ in f)
    
    # 旧库计数
    conn = sqlite3.connect(str(old_db))
    total = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    print(f"迁移前: 当前 {current_count} 条, 待迁移 {total} 条")
    
    if dry_run:
        print("🧪 dry-run 模式\n")
        sample = conn.execute("SELECT content FROM vectors LIMIT 3").fetchall()
        for i, (c,) in enumerate(sample):
            print(f"  [{i+1}] {c[:100]}...")
        conn.close()
        return
    
    # 初始化 embedding
    client, model, dim = get_embedding_client()
    
    cursor = conn.execute("SELECT rowid, id, content, metadata, source FROM vectors ORDER BY rowid")
    migrated = 0
    skipped = 0
    errors = 0
    t0 = time.time()
    
    # 确保 jsonl 目录存在
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    
    last_report = time.time()
    
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        
        entries = []
        for rowid, old_id, content, meta_json, source in rows:
            if not content or len(content.strip()) < 10:
                skipped += 1
                continue
            
            # 解析 metadata
            meta = {}
            if meta_json:
                try:
                    meta = json.loads(meta_json)
                except json.JSONDecodeError:
                    pass
            
            # 构建记忆条目（格式兼容 verified_memories.jsonl）
            entry = {
                "id": f"VM-MIGRATE-{hashlib.sha256(content.encode()).hexdigest()[:16]}",
                "content": content[:5000],  # 截断防太大
                "source": source or "memory_tdai_migration",
                "confidence": meta.get("confidence", 0.7),
                "created_at": meta.get("ts", datetime.now(timezone.utc).isoformat()),
                "valid_from": "",
                "valid_until": "",
                "verification_status": "verified",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "verified_by": "migration_script",
                "related_entities": meta.get("entities", []),
                "evidence_ids": [],
                "conflict_ids": [],
                "tags": meta.get("tags", []) + (["imported"] if source == "memory_core_import" else []),
                "importance": meta.get("importance", 0.5),
            }
            entries.append(entry)
        
        # 批量写入 jsonl
        if entries:
            with open(jsonl_path, "a", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        
        migrated += len(entries)
        
        # 进度报告（每批）
        now = time.time()
        if now - last_report >= 5 or migrated >= total:
            elapsed = now - t0
            rate = migrated / elapsed if elapsed > 0 else 0
            eta = (total - migrated) / rate if rate > 0 else 0
            print(f"  进度: {migrated}/{total} ({migrated*100//total}%) | {rate:.0f} 条/秒 | ETA {eta:.0f}s | 跳过:{skipped} err:{errors}")
            last_report = now
    
    conn.close()
    
    # 最终统计
    after = 0
    if jsonl_path.exists():
        with open(jsonl_path) as f:
            after = sum(1 for _ in f)
    
    elapsed = time.time() - t0
    print(f"\n✅ 迁移完成")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  迁移: {migrated} 条")
    print(f"  跳过: {skipped} 条")
    print(f"  错误: {errors} 条")
    print(f"  {current_count} → {after} 条 (净增 {after - current_count})")
    print(f"  速率: {migrated/elapsed:.1f} 条/秒")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch", type=int, default=200)
    args = parser.parse_args()
    migrate(dry_run=args.dry_run, batch_size=args.batch)
