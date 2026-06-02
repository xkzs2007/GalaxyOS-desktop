"""
独立重排序模块（bge-reranker-v2-m3，无问芯穹免费）


用法:
    from reranker import rerank_results
    reranked = rerank_results("查询", candidates, top_k=8)
"""

import os
import sys
import json
import logging
from typing import List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Reranker 配置（无问芯穹 bge-reranker-v2-m3，免费）──────────
RERANKER_URL = os.environ.get(
    "YAOYAO_RERANKER_URL",
    "https://cloud.infini-ai.com/maas/v1/rerank",
)

# 从 unified_config.json 读 embedding API key（与 embedding.py/llm_client.py 统一）
def _load_embedding_key() -> str:
    env_key = os.environ.get("YAOYAO_RERANKER_KEY", "")
    if env_key:
        return env_key
    config_paths = [
        Path(__file__).parent.parent / "skills" / "llm-memory-integration" / "config" / "unified_config.json",
        Path(__file__).parent.parent.parent / "skills" / "llm-memory-integration" / "config" / "unified_config.json",
        Path.home() / ".openclaw" / "workspace" / "skills" / "xiaoyi-claw-omega-final" / "skills" / "llm-memory-integration" / "config" / "unified_config.json",
    ]
    for cp in config_paths:
        if cp.exists():
            try:
                cfg = json.loads(cp.read_text())
                key = cfg.get("embedding", {}).get("api_key", "")
                if key and key != "YOUR_EMBEDDING_API_KEY":
                    return key
            except Exception:
                continue
    return ""

RERANKER_API_KEY = _load_embedding_key()
RERANKER_MODEL = "bge-reranker-v2-m3"


def rerank_results(query: str, candidates: List[Dict], top_k: int = 10) -> List[Dict]:
    """
    用 bge-reranker-v2-m3 对候选结果重排序（公开接口）

    query + documents 一起传，reranker 做 cross-encoder 重新打分。
    
    Args:
        query: 原始查询
        candidates: 候选结果列表（通常传 top_k * 2）
        top_k: 返回结果数
    
    Returns:
        重排序后的结果列表
    """
    if not candidates:
        return []

    import requests as httpreq

    # 提取要排序的文本（去重，保留 50 个以内防 token 超限）
    seen_texts = set()
    docs = []
    doc_map = []
    for c in candidates:
        text = c.get("content", "").strip()
        if text and text not in seen_texts:
            seen_texts.add(text)
            docs.append(text)
            doc_map.append(c)
        if len(docs) >= 50:
            break

    if not docs:
        return candidates[:top_k]

    try:
        resp = httpreq.post(
            RERANKER_URL,
            headers={
                "Authorization": f"Bearer {RERANKER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": RERANKER_MODEL,
                "query": query,
                "documents": docs,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.warning(f"Reranker API 返回 {resp.status_code}: {resp.text[:200]}")
            return candidates[:top_k]

        result = resp.json()
        reranked_data = result.get("data", result.get("results", []))

        # 按 reranker 分数重新排序
        scored = []
        for item in reranked_data:
            idx = item.get("index", item.get("id", -1))
            score = item.get("relevance_score", item.get("score", 0))
            if 0 <= idx < len(doc_map):
                doc_map[idx]["score"] = score
                scored.append(doc_map[idx])

        if not scored:
            return candidates[:top_k]

        scored.sort(key=lambda x: x.get("score", 0), reverse=True)
        return scored[:top_k]

    except Exception as e:
        logger.warning(f"Reranker 调用失败: {e}")
        return candidates[:top_k]


def __main():
    """CLI 测试入口"""
    print("测试用例 - 请从 Python 代码中调用 rerank_results()")


if __name__ == "__main__":
    __main()
