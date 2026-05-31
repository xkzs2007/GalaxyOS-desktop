#!/usr/bin/env python3
"""
腾讯云记忆 L1 结构化记忆 → UnifiedVectorStore 迁移脚本

功能：
1. 读取 vectors.db 中所有 L1 结构化记忆记录（191条）
2. 使用 bge-m3 生成 1024 维向量
3. 写入 unified_vectors.db（UnifiedVectorStore SQLite 后端）
4. 清理旧的 128 维向量数据

用法：
  python3 scripts/migrate_tencent_to_unified.py          # 执行迁移
  python3 scripts/migrate_tencent_to_unified.py --dry-run # 预览
  python3 scripts/migrate_tencent_to_unified.py --force   # 强制覆盖已有

依赖：
  pip install openai numpy
"""

import os, sys, json, time, logging, hashlib, argparse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_tencent_to_unified")

# ── 路径 ──
HOME = Path.home()
TENCENT_DB = HOME / ".openclaw" / "memory-tdai" / "vectors.db"
UNIFIED_DB = HOME / ".openclaw" / "memory-tdai" / "unified_vectors.db"
CONFIG_PATH = HOME / ".openclaw" / "workspace" / "skills" / "xiaoyi-claw-omega-final" / "config" / "llm_config.json"

# ── 默认 embedding 配置 ──
DEFAULT_EMBEDDING = {
    "api_key": "YOUR_EMBED_API_KEY",
    "base_url": "https://cloud.infini-ai.com/maas/v1",
    "model": "bge-m3",
    "dimensions": 1024
}

def load_embedding_config():
    """加载 embedding 客户端配置"""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
            emb = cfg.get("embedding", {})
            if emb.get("api_key"):
                return emb
        except Exception as e:
            logger.warning(f"读取配置失败: {e}")
    return DEFAULT_EMBEDDING

def get_embedding_client(emb_cfg):
    """初始化 embedding API 客户端"""
    from openai import OpenAI
    return OpenAI(
        api_key=emb_cfg["api_key"],
        base_url=emb_cfg.get("base_url", "https://cloud.infini-ai.com/maas/v1"),
    )

def generate_embeddings(client, model: str, texts: list) -> list:
    """批量生成向量"""
    all_embs = []
    # 分批，每批 10 条
    BATCH = 10
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        try:
            resp = client.embeddings.create(
                input=batch,
                model=model,
            )
            batch_embs = [d.embedding for d in resp.data]
            all_embs.extend(batch_embs)
            logger.info(f"  向量生成: {i + len(batch)}/{len(texts)}")
            if len(texts) > BATCH:
                time.sleep(0.2)  # 限速
        except Exception as e:
            logger.warning(f"  批量向量生成在 {i} 处失败: {e}")
            # 单条重试
            for t in batch:
                try:
                    resp = client.embeddings.create(input=[t], model=model)
                    all_embs.append(resp.data[0].embedding)
                    time.sleep(0.1)
                except Exception as e2:
                    logger.error(f"  单条向量生成失败: {e2}")
                    all_embs.append([0.0] * emb_cfg.get("dimensions", 1024))
    return all_embs

def read_tencent_l1():
    """读取腾讯云 L1 结构化记忆记录"""
    import sqlite3
    import json as _json
    
    conn = sqlite3.connect(str(TENCENT_DB))
    conn.row_factory = sqlite3.Row
    
    rows = conn.execute("""
        SELECT record_id, content, type, priority, scene_name,
               session_key, session_id, created_time, metadata_json
        FROM l1_records
        ORDER BY priority DESC, created_time DESC
    """).fetchall()
    
    records = []
    for r in rows:
        meta = {}
        try:
            meta = _json.loads(r["metadata_json"] or "{}")
        except:
            pass
        
        # 生成唯一 ID
        content_hash = hashlib.md5(r["content"].encode()).hexdigest()[:8]
        record_id = f"tencent_l1_{r['record_id'][-12:]}_{content_hash}"
        
        records.append({
            "id": record_id,
            "content": r["content"],
            "type": r["type"],
            "priority": r["priority"] or 50,
            "scene_name": r["scene_name"],
            "session_key": r["session_key"],
            "created_time": r["created_time"],
            "metadata": meta,
        })
    
    conn.close()
    return records

def read_unified_existing_ids():
    """读取 unified_vectors.db 已有的 L1 记录 ID"""
    import sqlite3
    if not UNIFIED_DB.exists():
        return set()
    
    try:
        conn = sqlite3.connect(str(UNIFIED_DB))
        rows = conn.execute("SELECT id FROM vectors WHERE source = 'memory-tdai'").fetchall()
        conn.close()
        return set(r[0] for r in rows)
    except:
        return set()

def write_to_unified(records, embeddings):
    """写入 UnifiedVectorStore"""
    import sqlite3, struct
    
    conn = sqlite3.connect(str(UNIFIED_DB))
    
    # 确保表存在
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vectors (
            id TEXT PRIMARY KEY,
            content TEXT,
            metadata TEXT,
            source TEXT,
            embedding BLOB
        )
    """)
    
    inserted = 0
    for rec, emb in zip(records, embeddings):
        try:
            # 序列化向量
            import struct as _st
            emb_bytes = _st.pack(f'{len(emb)}f', *emb)
            
            meta_json = json.dumps({
                "type": rec["type"],
                "priority": rec["priority"],
                "scene_name": rec["scene_name"],
                "session_key": rec["session_key"],
                "created_time": rec["created_time"],
                **rec["metadata"]
            }, ensure_ascii=False)
            
            conn.execute(
                "INSERT OR REPLACE INTO vectors (id, content, metadata, source, embedding) VALUES (?, ?, ?, ?, ?)",
                (rec["id"], rec["content"][:2000], meta_json, "memory-tdai", emb_bytes)
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"  写入失败 {rec['id'][:20]}: {e}")
    
    conn.commit()
    conn.close()
    return inserted

def cleanup_old_vectors():
    """清理旧的 128 维向量数据（来自 old tencent bridge）"""
    import sqlite3
    conn = sqlite3.connect(str(UNIFIED_DB))
    
    del_count = conn.execute("DELETE FROM vectors WHERE source IN ('memory-tdai', 'test')").rowcount
    
    # 重建索引（空间回收）
    conn.execute("VACUUM")
    conn.close()
    
    return del_count

def count_remaining():
    import sqlite3
    conn = sqlite3.connect(str(UNIFIED_DB))
    total = conn.execute("SELECT COUNT(*) FROM vectors").pluck().get()
    by_source = {}
    for row in conn.execute("SELECT source, COUNT(*) as cnt FROM vectors GROUP BY source").fetchall():
        by_source[row[0]] = row[1]
    conn.close()
    return total, by_source

def main():
    parser = argparse.ArgumentParser(description="腾讯云 L1 记忆 → UnifiedVectorStore 迁移")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不执行写入")
    parser.add_argument("--force", action="store_true", help="覆盖已有数据（清理旧的 128 维向量）")
    args = parser.parse_args()
    
    # 1. 加载配置
    logger.info("1. 加载 embedding 配置...")
    emb_cfg = load_embedding_config()
    logger.info(f"   model={emb_cfg.get('model')}, dim={emb_cfg.get('dimensions', '?')}")
    
    # 2. 读取腾讯云 L1 数据
    logger.info("2. 读取腾讯云 L1 数据...")
    records = read_tencent_l1()
    logger.info(f"   共 {len(records)} 条 L1 记录")
    
    if args.dry_run:
        for r in records[:5]:
            logger.info(f"  [{r['priority']}] [{r['type']}] {r['content'][:60]}...")
        logger.info(f"\n预览结束，未执行写入。")
        return
    
    # 3. 检查已有数据
    existing_ids = read_unified_existing_ids()
    if not args.force and existing_ids:
        # 去重：跳过已存在的
        before = len(records)
        records = [r for r in records if r["id"] not in existing_ids]
        logger.info(f"   已有 {len(existing_ids)} 条，跳过重复，剩余 {len(records)} 条待迁移")
        if not records:
            logger.info("  没有新数据需要迁移。")
            return
    
    if args.force and existing_ids:
        logger.info(f"3. 清理旧数据 ({len(existing_ids)} 条)...")
        deleted = cleanup_old_vectors()
        logger.info(f"   已清理 {deleted} 条旧向量")
    
    # 4. 生成向量
    logger.info(f"{'4' if not args.force else '5'}. 生成向量...")
    client = get_embedding_client(emb_cfg)
    model = emb_cfg.get("model", "bge-m3")
    
    texts = [r["content"][:1000] for r in records]  # 截取前1000字符
    embeddings = generate_embeddings(client, model, texts)
    
    # 验证维度
    dims = [len(e) for e in embeddings]
    logger.info(f"   向量维度: min={min(dims)}, max={max(dims)}")
    
    if not all(d == dims[0] for d in dims):
        logger.error("向量维度不一致！")
        sys.exit(1)
    
    # 5. 写入
    logger.info(f"{'5' if not args.force else '6'}. 写入 UnifiedVectorStore...")
    inserted = write_to_unified(records, embeddings)
    logger.info(f"   已写入 {inserted} 条")
    
    # 6. 统计
    total, by_source = count_remaining()
    logger.info(f"\n{'6' if not args.force else '7'}. 最终统计:")
    logger.info(f"   UnifiedVectorStore 总量: {total} 条")
    for src, cnt in sorted(by_source.items()):
        logger.info(f"     {src}: {cnt}")
    
    # 7. 三拷贝同步
    script_path = Path(__file__).resolve()
    targets = [
        Path.home() / ".openclaw" / "workspace" / "skills" / "xiaoyi-claw-omega-final" / "scripts" / "migrate_tencent_to_unified.py",
        Path.home() / ".openclaw" / "workspace" / "skills" / "xiaoyi-claw-omega-final" / "skills" / "llm-memory-integration" / "core" / "migrate_tencent_to_unified.py",
        Path.home() / ".openclaw" / "extensions" / "claw-core" / "dist" / "scripts" / "migrate_tencent_to_unified.py",
    ]
    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(str(script_path), str(t))
    
    logger.info("三拷贝同步完成。迁移成功 ✅")

if __name__ == "__main__":
    main()
