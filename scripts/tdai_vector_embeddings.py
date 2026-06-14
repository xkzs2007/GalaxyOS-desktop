#!/usr/bin/env python3
"""补全 unified_vectors.db 中缺失的向量嵌入 (128d text-embedding-v1.0)"""
import json, os, time, sqlite3, sys
import requests
import numpy as np

UNIFIED_DB = os.path.expanduser("~/.openclaw/memory-tdai/unified_vectors.db")

with open(os.path.expanduser("~/.openclaw/openclaw.json")) as f:
    cfg = json.load(f)
ms = cfg["agents"]["defaults"]["memorySearch"]
rem = ms["remote"]
BASE_URL = rem["baseUrl"].rstrip("/")
HD = dict(rem.get("headers", {}))
HD["Content-Type"] = "application/json"
MODEL = ms["model"]

BATCH = 100
CP = os.path.join(os.path.dirname(UNIFIED_DB), ".migration_cp.json")


def get_pending():
    conn = sqlite3.connect(UNIFIED_DB)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r) for r in conn.execute(
            "SELECT id, content FROM vectors "
            "WHERE embedding IS NULL OR length(embedding) = 0 "
            "ORDER BY id"
        ).fetchall()
    ]
    conn.close()
    return rows


def update_checkpoint(ids):
    with open(CP, "w") as f:
        json.dump({"processed_ids": list(ids)}, f)


def do_embedding(texts, timeout=120):
    """单次嵌入请求，自动处理空输入错误"""
    if not texts:
        return None
    r = requests.post(
        f"{BASE_URL}/embeddings",
        headers=HD,
        json={"input": texts, "model": MODEL},
        timeout=timeout,
    )
    r.raise_for_status()
    j = r.json()
    dd = j.get("data")
    if dd is None:
        err_msg = str(j.get("error", {}).get("message", ""))
        raise ValueError(f"null data: {err_msg} | resp: {json.dumps(j)[:300]}")
    return [d["embedding"] for d in sorted(dd, key=lambda x: x["index"])]


def embed_batch(items):
    """对一批 items，发送嵌入请求并写入 DB。自动处理：
    - 空内容过滤（跳过空字符记录，标记 checkpoint）
    - 重试（最多 5 次）
    - 单条回退（如果整批失败，逐条尝试）
    返回 (已嵌入数, 已跳过数)
    """
    batch_size = len(items)
    ids = [it["id"] for it in items]
    texts = [it["content"] for it in items]

    # 1) 过滤空内容
    valid_idx = [i for i, t in enumerate(texts) if t and t.strip()]
    if len(valid_idx) < batch_size:
        skipped = [items[i] for i in range(batch_size) if i not in valid_idx]
        print(f"   跳过 {len(skipped)} 条空内容")
        items = [items[i] for i in valid_idx]
        texts = [texts[i] for i in valid_idx]
        if not items:
            return (0, len(skipped))

    # 2) 重试循环
    vectors = None
    for attempt in range(5):
        try:
            vectors = do_embedding(texts)
            break
        except Exception as e:
            if attempt < 4:
                s = 2**attempt
                print(f"   重试 {attempt+1}/{4}: {str(e)[:120]}, {s}s 后重试")
                time.sleep(s)
            else:
                # 5 次全失败 -> 逐条回退
                print(f"   批量失败, 逐条回退 ({len(items)} 条)")
                single_success = 0
                for it in items:
                    try:
                        v = do_embedding([it["content"]], timeout=60)
                        if v:
                            _write_embeddings([(it["id"], v[0])])
                            single_success += 1
                    except Exception as e2:
                        print(f"   单条失败: id={it['id'][:20]}... {str(e2)[:80]}")
                        time.sleep(1)
                return (single_success, 0)

    if vectors is None or len(vectors) != len(items):
        print(f"   获取失败: vectors={len(vectors) if vectors else 0}, items={len(items)}")
        return (0, 0)

    # 3) 写入 DB
    pairs = [(it["id"], vec) for it, vec in zip(items, vectors)]
    _write_embeddings(pairs)
    return (len(pairs), 0)


def _write_embeddings(pairs):
    """写入一批 (id, embedding_list) 到 DB"""
    conn = sqlite3.connect(UNIFIED_DB)
    conn.execute("BEGIN")
    for rec_id, vec_list in pairs:
        blob = np.array(vec_list, dtype=np.float32).tobytes()
        conn.execute(
            "UPDATE vectors SET embedding=? WHERE id=? AND "
            "(embedding IS NULL OR length(embedding)=0)",
            (blob, rec_id),
        )
    conn.execute("COMMIT")
    conn.close()


def main():
    pending = get_pending()
    total = len(pending)
    print(f"需嵌入: {total} 条")

    if total == 0:
        print("全部完成！")
        return

    # 加载 checkpoint 并过滤
    cp_ids = set()
    if os.path.exists(CP):
        with open(CP) as f:
            cp = json.load(f)
            cp_ids = set(cp.get("processed_ids", []))

    actual = [p for p in pending if p["id"] not in cp_ids]
    if not actual:
        # checkpoint 脏了（标记了但 DB 没数据）
        print("checkpoint 脏数据，重建...")
        cp_ids = set()
        actual = pending

    batches = [actual[i : i + BATCH] for i in range(0, len(actual), BATCH)]
    print(f"分 {len(batches)} 批处理（每批 {BATCH} 条）")

    t0 = time.time()
    total_ok = 0
    total_skip = 0
    for bi, batch in enumerate(batches):
        bt = time.time()
        ok, skip = embed_batch(batch)
        total_ok += ok
        total_skip += skip

        # 更新 checkpoint
        new_ids = [it["id"] for it in batch]
        cp_ids.update(new_ids)
        update_checkpoint(cp_ids)

        elapsed = time.time() - bt
        total_elapsed = time.time() - t0
        pct = (bi + 1) / len(batches) * 100
        rps = total_ok / total_elapsed if total_elapsed > 0 else 0
        eta = (len(batches) - bi - 1) * elapsed / 60 if elapsed > 0 else 0
        print(
            f"[{bi+1}/{len(batches)}] {pct:.0f}% "
            f"| +{ok} | {elapsed:.1f}s "
            f"| 总计{total_ok}条 | {rps:.1f}条/s"
            f"{f' | ETA {eta:.0f}min' if eta > 0 else ''}"
        )

    total_elapsed = time.time() - t0
    print(f"\n✅ 完成！嵌入 {total_ok} 条，跳过 {total_skip} 条，"
          f"耗时 {total_elapsed/60:.1f} 分钟")

    # 最终验证
    conn = sqlite3.connect(UNIFIED_DB)
    embedded = conn.execute(
        "SELECT COUNT(*) FROM vectors WHERE embedding IS NOT NULL AND length(embedding) > 0"
    ).fetchone()[0]
    missing = conn.execute(
        "SELECT COUNT(*) FROM vectors WHERE embedding IS NULL OR length(embedding) = 0"
    ).fetchone()[0]
    conn.close()
    print(f"最终状态: 已嵌入 {embedded}, 未嵌入 {missing}")


if __name__ == "__main__":
    main()
