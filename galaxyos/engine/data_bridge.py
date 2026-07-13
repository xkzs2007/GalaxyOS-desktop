#!/usr/bin/env python3
"""
数据桥接：腾讯云 L1 记忆 → UnifiedVectorStore

从 vectors.db 读取 l1_records，生成 embedding，写入 UnifiedVectorStore。
同时更新十亿云集成模块，新增 smart_processor 智能处理层。
"""

import sys
import json
import sqlite3
import time
import numpy as np
from pathlib import Path

CORE = Path.home() / ".openclaw/workspace/skills/galaxyos-engine/skills/llm-memory-integration/core"
sys.path.insert(0, str(CORE))

from semantic_cache import EmbeddingClient
from unified_vector_store import UnifiedVectorStore
from llm_client import load_config

config = load_config()
emb_config = config.get("embedding", {})

client = EmbeddingClient(
    api_key=emb_config.get("api_key", ""),
    base_url=emb_config.get("base_url", "https://ai.gitee.com/v1"),
    model=emb_config.get("model", "Qwen3-Embedding-8B"),
    dimensions=emb_config.get("dimensions", 4096),
)

def get_l1_records():
    conn = sqlite3.connect(str(Path.home() / ".openclaw/memory-tdai/vectors.db"))
    rows = conn.execute("""
        SELECT record_id, content, type, priority, scene_name, timestamp_str, metadata_json
        FROM l1_records ORDER BY priority DESC
    """).fetchall()
    conn.close()
    return rows

def sync():
    rows = get_l1_records()
    store = UnifiedVectorStore()

    before = store.count()
    print(f"同步前 UnifiedVectorStore: {before} 条")
    print(f"待同步 L1 记录: {len(rows)} 条")

    if not rows:
        print("没有需要同步的数据")
        return

    vectors, contents, metadatas, ids = [], [], [], []

    for i, (rid, content, typ, priority, scene, ts, meta_json) in enumerate(rows):
        if i % 5 == 0:
            print(f"\r  读取: {i}/{len(rows)}", end="")

        # 生成 embedding（分批生成避免 API 限流）
        try:
            vec = client.embed(content[:500])  # 截断避免过长文本
        except Exception as e:
            print(f"\n  嵌入失败 [{rid[:12]}]: {e}")
            continue

        if vec is None or len(vec) != 4096:
            continue

        vectors.append(vec.tolist() if hasattr(vec, 'tolist') else list(vec))
        contents.append(content)
        metadatas.append({
            "type": typ, "priority": priority,
            "scene_name": scene, "timestamp_str": ts,
            "metadata_json": meta_json, "original_id": rid,
            "source": "memory-tdai"
        })
        ids.append(rid)

        time.sleep(0.1)  # 限速

    print(f"\r  读取完成: {len(vectors)} 条")

    if not vectors:
        print("没有有效数据")
        return

    # 批量写入（UnifiedVectorStore 每次 add_vectors 是批量操作）
    # 但为了避免一次写入过多，分批
    batch_size = 10
    total_added = 0

    for i in range(0, len(vectors), batch_size):
        batch_vecs = vectors[i:i+batch_size]
        batch_contents = contents[i:i+batch_size]
        batch_metas = metadatas[i:i+batch_size]
        batch_ids = ids[i:i+batch_size]

        try:
            store.add_vectors(
                vectors=batch_vecs,
                contents=batch_contents,
                metadatas=batch_metas,
                ids=batch_ids,
                source="memory-tdai"
            )
            total_added += len(batch_vecs)
        except Exception as e:
            print(f"  写入批次 {i//batch_size} 失败: {e}")

        print(f"\r  写入: {total_added}/{len(vectors)}", end="")

    print()

    after = store.count()
    print("\n✅ 同步完成:")
    print(f"  成功写入: {total_added} 条")
    print(f"  UnifiedVectorStore 总数: {before} → {after} 条")

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("预览模式")
        rows = get_l1_records()
        print(f"L1 记录数: {len(rows)}")
        for r in rows[:3]:
            print(f"  [{r[3]}] [{r[2]}] {r[1][:60]}...")
    else:
        sync()
