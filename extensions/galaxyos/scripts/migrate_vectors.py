#!/usr/bin/env python3
"""
为已迁移的 28K 条记忆建立向量索引（补 embedding 步骤）
读取 verified_memories.jsonl → embedding API → 写入向量库

用法:
  python3 migrate_vectors.py [--batch 100]
"""

import os, sys, json, time, sqlite3, struct
from pathlib import Path

def get_embedding_client_v2():
    """从 OpenClaw 配置读取 embedding 客户端"""
    config_path = Path(os.path.expanduser("~/.openclaw/back.openclaw.json"))
    with open(config_path) as f:
        d = json.load(f)
    ms = d.get("agents", {}).get("defaults", {}).get("memorySearch", {})
    remote = ms.get("remote", {})
    base_url = remote.get("baseUrl", "").rstrip("/")
    headers = dict(remote.get("headers", {}))
    api_key = headers.get("x-api-key", "")
    model = ms.get("model", "text-embedding-v1.0")
    
    # 额外 headers
    import httpx
    custom_headers = {k: v for k, v in headers.items() if k.lower() not in ("content-type",)}
    http_client = httpx.Client(headers=custom_headers)
    
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    return client, model, 128


def embed(client, model, text):
    if not text or len(text.strip()) < 5:
        return None
    try:
        resp = client.embeddings.create(model=model, input=text[:2000])
        return resp.data[0].embedding
    except Exception:
        return None


def main(dry_run=False, batch_size=100):
    jsonl_path = Path(os.path.expanduser("~/.openclaw/workspace/.learnings/verified_memories.jsonl"))
    vector_db = Path(os.path.expanduser("~/.openclaw/memory-tdai/unified_vectors.db"))
    
    if not jsonl_path.exists():
        print(f"❌ {jsonl_path} 不存在")
        return
    
    with open(jsonl_path) as f:
        total = sum(1 for _ in f)
    
    # 检查迁移来源的记录（已标记 source=memory_tdai_migration 或 memory_core_import）
    # 或者全部重新建立向量索引
    print(f"JSONL 总计 {total} 条记录")
    
    client, model, dim = get_embedding_client_v2()
    print(f"Embedding: model={model}, dim={dim}")
    
    if dry_run:
        print("🧪 dry-run, 只测试 embedding API")
        test_entry = None
        with open(jsonl_path) as f:
            for line in f:
                e = json.loads(line)
                if e.get("source") in ("memory_tdai_migration", "memory_core_import"):
                    test_entry = e
                    break
        if test_entry:
            vec = embed(client, model, test_entry["content"])
            print(f"  测试: {test_entry['content'][:60]}...")
            print(f"  向量: {len(vec)} dim, 前5值: {[round(v,4) for v in vec[:5]]}")
        return
    
    # 逐条 embedding + 写向量库（直接写 SQLite BLOB，兼容 memory-tdai 格式）
    conn = sqlite3.connect(str(vector_db))
    
    # 备份旧表
    conn.execute("ALTER TABLE vectors RENAME TO vectors_old")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vectors (
            id TEXT PRIMARY KEY,
            content TEXT,
            metadata TEXT,
            source TEXT,
            embedding BLOB
        )
    """)
    conn.commit()
    
    # 重建 FAISS 索引前先清理
    faiss_path = Path(os.path.expanduser("~/.openclaw/memory-tdai/unified_vectors.faiss"))
    if faiss_path.exists():
        faiss_path.unlink()
    meta_path = Path(os.path.expanduser("~/.openclaw/memory-tdai/unified_vectors.faiss.meta"))
    if meta_path.exists():
        meta_path.unlink()
    
    migrated = 0
    skipped = 0
    errors = 0
    t0 = time.time()
    
    cursor = conn.execute("SELECT id, content, metadata, source FROM vectors_old")
    
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        
        batch_data = []
        for row_id, content, meta_json, source in rows:
            vec = embed(client, model, content)
            if vec is None:
                skipped += 1
                continue
            vec_blob = struct.pack(f"{len(vec)}f", *vec)
            batch_data.append((row_id, content, meta_json, source or "memory_core_import", vec_blob))
        
        if batch_data:
            conn.executemany(
                "INSERT OR REPLACE INTO vectors (id, content, metadata, source, embedding) VALUES (?, ?, ?, ?, ?)",
                batch_data
            )
            conn.commit()
        
        migrated += len(batch_data)
        
        elapsed = time.time() - t0
        rate = migrated / elapsed if elapsed > 0 else 0
        eta = (total - migrated) / rate if rate > 0 else 0
        print(f"  进度: {migrated}/{total} ({migrated*100//total}%) | {rate:.0f} 条/秒 | ETA {eta:.0f}s | 跳过:{skipped} err:{errors}")
    
    conn.close()
    elapsed = time.time() - t0
    print(f"\n✅ 向量索引完成")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  索引: {migrated} 条")
    print(f"  跳过: {skipped} 条（embedding 失败）")
    print(f"  速率: {migrated/elapsed:.1f} 条/秒")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch", type=int, default=100)
    args = parser.parse_args()
    main(dry_run=args.dry_run, batch_size=args.batch)
