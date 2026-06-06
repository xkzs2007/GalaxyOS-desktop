#!/usr/bin/env python3
"""
混合记忆搜索（公开安全版）
使用 FTS 全文搜索，不依赖原生扩展
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import List, Dict

# 配置路径
CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
LLM_CONFIG = CONFIG_DIR / "llm_config.json"

# 数据库路径（v3.0.0 公私分离：优先使用环境变量）
_OPENCLAW_HOME = Path.home() / ".openclaw"  # 内部工具，保留默认路径
VECTORS_DB = Path(os.environ.get("OPENCLAW_VECTORS_DB", str(_OPENCLAW_HOME / "memory-tdai" / "vectors.db")))


def load_config() -> Dict:
    """加载配置"""
    if LLM_CONFIG.exists():
        try:
            return json.loads(LLM_CONFIG.read_text())
        except Exception:
            pass
    return {}


def get_db_connection() -> sqlite3.Connection:
    """获取数据库连接（纯 Python，无原生扩展）"""
    conn = sqlite3.connect(str(VECTORS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def fts_search(query: str, top_k: int = 10) -> List[Dict]:
    """全文搜索"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 使用 LIKE 进行全文搜索
        cursor.execute("""
            SELECT id, content, metadata, created_at
            FROM memories
            WHERE content LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (f"%{query}%", top_k))

        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row["id"],
                "content": row["content"],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "created_at": row["created_at"],
                "source": "fts"
            })

        return results
    except Exception as e:
        print(f"FTS 搜索错误: {e}")
        return []
    finally:
        conn.close()


def hybrid_search(query: str, top_k: int = 10) -> List[Dict]:
    """混合搜索（公开安全版：仅 FTS）"""
    print(f"🔍 搜索: {query}")
    return fts_search(query, top_k)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: hybrid_memory_search.py <query>")
        sys.exit(1)

    query = sys.argv[1]
    results = hybrid_search(query)

    print(f"\n找到 {len(results)} 条结果:")
    for i, r in enumerate(results, 1):
        content_short = r["content"][:80] + "..." if len(r["content"]) > 80 else r["content"]
        print(f"{i}. {content_short}")
