#!/usr/bin/env python3
"""memory-core → unified_vectors 全量迁移 (128d text-embedding-v1.0)"""
import json, os, time, sqlite3, sys
import requests
import numpy as np

MEMORY_DB = os.path.expanduser("~/.openclaw/memory/main.sqlite")
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

cp_ids = set()
if os.path.exists(CP):
    with open(CP) as f:
        cp_ids = set(json.load(f).get("processed_ids", []))

conn = sqlite3.connect(MEMORY_DB)
conn.row_factory = sqlite3.Row
chunks = [dict(r) for r in conn.execute(
    "SELECT id, path, source, start_line, end_line, hash, model, text, updated_at "
    "FROM chunks ORDER BY source ASC, updated_at ASC"
).fetchall()]
conn.close()
print(f"Total: {len(chunks)}, done: {len(cp_ids)}, left: {len(chunks)-len(cp_ids)}")

pending = [c for c in chunks if c["id"] not in cp_ids]
if not pending:
    print("All done!"); sys.exit(0)

conn = sqlite3.connect(UNIFIED_DB)
conn.execute("""CREATE TABLE IF NOT EXISTS vectors (
    id TEXT PRIMARY KEY, content TEXT, metadata TEXT, source TEXT, embedding BLOB
)""")
conn.execute("PRAGMA journal_mode=WAL")
conn.close()

batches = [pending[i:i+BATCH] for i in range(0, len(pending), BATCH)]
print(f"Batches: {len(batches)}")

t0 = time.time()
total_w = 0
for bi, batch in enumerate(batches):
    bt = time.time()
    texts = [b["text"] for b in batch]
    # retry loop
    vectors = None
    for attempt in range(5):
        try:
            r = requests.post(f"{BASE_URL}/embeddings", headers=HD,
                              json={"input": texts, "model": MODEL}, timeout=120)
            r.raise_for_status()
            j = r.json()
            dd = j.get("data")
            if dd is None:
                raise ValueError(f"null data: {json.dumps(j)[:200]}")
            vectors = [d["embedding"] for d in sorted(dd, key=lambda x: x["index"])]
            break
        except Exception as e:
            if attempt < 4:
                s = 2 ** attempt
                print(f"  ⚠️ batch {bi+1} attempt {attempt+1}: {type(e).__name__}, retry in {s}s")
                time.sleep(s)
            else:
                print(f"  ❌ batch {bi+1} failed after 5 attempts: {e}")
                sys.exit(1)
    if vectors is None:
        sys.exit(1)

    conn = sqlite3.connect(UNIFIED_DB)
    conn.execute("BEGIN")
    for chunk, vec in zip(batch, vectors):
        meta = json.dumps({"path":chunk["path"],"src":chunk["source"],
                           "sl":chunk["start_line"],"el":chunk["end_line"],
                           "hash":chunk["hash"],"model":chunk["model"],
                           "ts":chunk["updated_at"]})
        blob = np.array(vec, dtype=np.float32).tobytes()
        conn.execute("INSERT OR REPLACE INTO vectors (id,content,metadata,source,embedding) VALUES (?,?,?,?,?)",
                     (chunk["id"],chunk["text"],meta,"memory_core_import",blob))
    conn.execute("COMMIT"); conn.close()
    total_w += len(batch)
    cp_ids.update(b["id"] for b in batch)
    with open(CP,"w") as f: json.dump({"processed_ids":list(cp_ids)},f)

    el = time.time()-bt; ta = time.time()-t0; p = (bi+1)/len(batches)*100
    print(f"[{bi+1}/{len(batches)}] {p:.0f}% | +{len(batch)} | {el:.1f}s | {ta/60:.1f}min")

print(f"\nDone! +{total_w}")
cnt = sqlite3.connect(UNIFIED_DB).execute(
    "SELECT COUNT(*) FROM vectors WHERE source='memory_core_import'").fetchone()[0]
print(f"Verified: {cnt}")
if cnt == len(chunks) and os.path.exists(CP):
    os.remove(CP); print("CP cleaned")
