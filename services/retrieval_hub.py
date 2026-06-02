"""retrieval_hub.py — 统一检索入口

集成五路检索（本地 + DAG + 突触 + 论文引擎 + 联网搜索），
并行执行后 RRF 融合排序。

查询预处理复用 scripts_core 的 QueryUnderstanding / QueryRewriter / QueryRouter。

提供给 R-CCAM 的 _retrieval_phase 和 _action_phase 使用。
"""

import os, sys, json, logging, time, re, sqlite3, subprocess
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# 共享线程池（最多 4 线程，2 核够用）
_PARALLEL_POOL = ThreadPoolExecutor(max_workers=4)

# ---------- scripts_core 桥接 ----------
_SCRIPTS_CORE_DIR = os.path.expanduser(
    "~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/scripts")
if _SCRIPTS_CORE_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_CORE_DIR)


def _preprocess_query(query: str) -> Dict:
    """
    查询预处理流水线：理解→改写→路由

    复用 scripts_core 已有的 QueryUnderstanding / QueryRewriter / QueryRouter。
    返回预处理后的查询信息，供 retrieval_hub 决策检索策略。
    """
    result = {
        "original": query,
        "rewritten": query,
        "corrections": [],
        "mode": "balanced",
        "intent": ("search", 0.5),
        "complexity": "medium",
        "entities": [],
        "search_hints": {},
    }
    try:
        from scripts_core.understand import QueryUnderstanding
        from scripts_core.rewriter import QueryRewriter
        from scripts_core.router import QueryRouter

        # 1. 理解
        understand = QueryUnderstanding.analyze(query)
        result["intent"] = understand.get("intent", ("search", 0.5))
        result["complexity"] = understand.get("complexity", "medium")
        result["entities"] = understand.get("entities", [])

        # 2. 改写
        rewritten, corrections = QueryRewriter.rewrite(query)
        result["rewritten"] = rewritten
        result["corrections"] = corrections

        # 3. 路由
        mode = QueryRouter.select_mode(rewritten, use_llm=False)
        result["mode"] = mode

        # 4. 搜索提示
        try:
            hints = QueryUnderstanding.get_search_hints(understand)
            result["search_hints"] = hints
        except Exception:
            pass

        logger.info(
            f"query_preprocess: '{query}'→ rewrite='{rewritten}' "
            f"mode={mode} intent={result['intent'][0]} complexity={result['complexity']}"
        )
    except ImportError as e:
        logger.warning(f"scripts_core 未找到，跳过查询预处理: {e}")
    except Exception as e:
        logger.warning(f"查询预处理异常: {e}")

    return result


# ---------- RRF 融合 ----------

def _rrf_merge(all_results: List[List[Dict]], k: int = 60) -> List[Dict]:
    """RRF 融合多路检索结果

    来源权重：
    - dag / dag_msg: ×1.5（语义向量搜到的结果精度更高）
    - 其他（local / paper / web / synapse）: ×1.0
    """
    DAG_BOOST = 1.5
    scores = {}
    sources = {}
    for results in all_results:
        for i, r in enumerate(results):
            rid = r.get('content', r.get('id', str(i)))[:200]
            if rid not in scores:
                scores[rid] = 0
                sources[rid] = r
            src = r.get('source', 'unknown')
            weight = DAG_BOOST if src in ('dag', 'dag_msg') else 1.0
            scores[rid] += weight / (k + i + 1)
            if 'sources' not in sources[rid]:
                sources[rid]['sources'] = set()
            sources[rid]['sources'].add(src)

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    merged = []
    for rid, score in ranked:
        item = dict(sources[rid])
        item['rrf_score'] = round(score, 4)
        item['source'] = '/'.join(
            sorted(sources[rid]['sources'])
        ) if isinstance(sources[rid].get('sources'), set) else item.get('source', 'unknown')
        if 'sources' in item:
            del item['sources']
        merged.append(item)
    return merged


# ---------- Knowledge Graph 懒加载 ----------
_KG_INSTANCE = None

def _get_kg():
    """懒加载 temporal_kg 单例"""
    global _KG_INSTANCE
    if _KG_INSTANCE is None:
        try:
            _paths = [
                os.path.expanduser("~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"),
                os.path.dirname(os.path.abspath(__file__)),
            ]
            for p in _paths:
                if p not in sys.path:
                    sys.path.insert(0, p)
            from temporal_kg import get_temporal_kg
            _KG_INSTANCE = get_temporal_kg()
        except Exception as e:
            logger.warning(f"KG 懒加载失败: {e}")
            _KG_INSTANCE = None
    return _KG_INSTANCE

# ========== 6 路检索函数（可独立并行） ==========


def _do_kg(query: str, top_k: int) -> list:
    """0. KG as Memory Backbone: 图结构检索主通道。优先于向量检索执行。"""
    results = []
    try:
        kg = _get_kg()
        if kg:
            raw = kg.retrieve_by_entities(query, top_k=top_k)
            for r in raw:
                results.append({
                    "content": r.get("content", ""),
                    "score": r.get("score", 0.0),
                    "confidence": r.get("score", 0.5),
                    "source": "kg",
                    "entities": r.get("entities", []),
                    "relation": r.get("relation", ""),
                    "distance": r.get("distance", 0),
                    "edge_id": r.get("edge_id", ""),
                })
            logger.debug(f"_do_kg: {len(results)} results for '{query[:30]}'")
        else:
            logger.debug("_do_kg: KG 不可用, 跳过")
    except Exception as e:
        logger.warning(f"_do_kg failed: {e}")
    return results

def _do_local(query: str, top_k: int) -> list:
    """1. 本地检索（XiaoyiClawLLM.recall + dag_fallback 降级）"""
    _r = []
    try:
        sys.path.insert(0, os.path.expanduser("~/.openclaw/extensions/claw-core/dist/scripts"))
        from xiaoyi_claw_api import recall as xiaoyi_recall
        memory_results = xiaoyi_recall(query, top_k=top_k, enhance_with_kg=True)
        for r in memory_results:
            content = r.get('content') or r.get('user_text') or ''
            _r.append({
                'content': content[:1500],
                'score': r.get('score', 0.5),
                'source': r.get('metadata', {}).get('source', r.get('source', 'local')),
            })
        logger.info(f"  local(XiaoyiClawLLM.recall): {len(_r)} results")
        return _r
    except Exception as e:
        logger.warning(f"  local via recall() failed: {e}")
    # 降级：dag_fallback
    try:
        dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
        if os.path.exists(dag_db):
            conn = sqlite3.connect(dag_db)
            rows = conn.execute("""
                SELECT content, node_type, rowid FROM dag_nodes
                WHERE content IS NOT NULL AND content != ''
                ORDER BY rowid DESC LIMIT 50
            """).fetchall()
            conn.close()
            q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))
            for c, nt, rid in rows:
                c_words = set(re.findall(r'[\w\u4e00-\u9fff]+', (c or '').lower()))
                overlap = len(q_words & c_words)
                if overlap > 0:
                    _r.append({
                        'content': c[:1500],
                        'score': overlap / max(len(q_words), 1),
                        'source': 'dag_fallback',
                    })
            logger.info(f"  local(dag_fallback): {len(_r)} results")
    except Exception as e2:
        logger.warning(f"  dag fallback failed: {e2}")
    return _r


# ── DAG HNSWLib 本地向量索引检索 ──
_DAG_EMB_CLIENT = None
_DAG_EMB_MODEL = None
_DAG_EMB_DIM = None
_DAG_HNSW_INDEX = None        # hnswlib.Index 实例
_DAG_HNSW_NODES = []           # [{node_id, content, source, phase, importance}, ...]
_DAG_HNSW_DB_CHECK = None      # 上次检查 DB 时的节点总数
_DAG_HNSW_INDEX_PATH = os.path.expanduser("~/.openclaw/dag_hnsw.idx")

def _init_dag_embedding():
    """懒加载 embedding 客户端"""
    global _DAG_EMB_CLIENT, _DAG_EMB_MODEL, _DAG_EMB_DIM
    if _DAG_EMB_CLIENT is not None:
        return True
    try:
        config_path = os.path.expanduser(
            "~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/config/llm_config.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
            emb_cfg = cfg.get("embedding", {})
            if emb_cfg.get("api_key"):
                from openai import OpenAI
                _DAG_EMB_CLIENT = OpenAI(
                    api_key=emb_cfg["api_key"],
                    base_url=emb_cfg.get("base_url", "https://cloud.infini-ai.com/maas/v1"),
                )
                _DAG_EMB_MODEL = emb_cfg.get("model", "bge-m3")
                _DAG_EMB_DIM = emb_cfg.get("dimensions", 1024)
                return True
    except Exception as e:
        logger.debug(f"DAG embedding 初始化跳过: {e}")
    return False


def _build_dag_hnsw_index():
    """从 dag_context.db 构建/重建 HNSWLib 索引

    只索引 rccam_nodes 的 cycle_summary 和 dag_nodes 的 MESSAGE。
    检查 DB 节点计数，无变化时跳过重建。
    """
    global _DAG_HNSW_INDEX, _DAG_HNSW_NODES, _DAG_HNSW_DB_CHECK
    dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
    if not os.path.exists(dag_db):
        return False

    conn = sqlite3.connect(dag_db)

    # 检查 DB 节点总数是否有变化
    total = conn.execute(
        "SELECT COUNT(*) + (SELECT COUNT(*) FROM rccam_nodes)"
    ).fetchone()[0] or 0

    if _DAG_HNSW_INDEX is not None and _DAG_HNSW_DB_CHECK == total:
        conn.close()
        return True  # DB 没变化，索引仍有效

    # 读取所有可索引节点（不限制压缩/摘要状态，保留原始细节）
    nodes = []
    seen_content = set()  # 去重

    # rccam_nodes：所有非 cycle_summary 的阶段节点（user_input/cognition/control/action...）
    cur = conn.execute(
        "SELECT node_id, phase_name, content, importance_score, node_type "
        "FROM rccam_nodes WHERE node_type != 'rccam_cycle_summary' "
        "ORDER BY timestamp DESC"
    )
    for row in cur.fetchall():
        nid, phase, content, imp, ntype = row
        content = content or ''
        clean = content.strip()
        if len(clean) < 15:
            continue
        dedup_key = clean[:80]
        if dedup_key in seen_content:
            continue
        seen_content.add(dedup_key)
        nodes.append({
            'node_id': nid,
            'content': clean[:1000],
            'source': 'dag',
            'phase': phase or ntype,
            'importance': imp or 0.5,
        })

    # rccam_nodes：cycle_summary 节点也加上
    cur2 = conn.execute(
        "SELECT node_id, phase_name, content, importance_score, node_type "
        "FROM rccam_nodes WHERE node_type='rccam_cycle_summary' "
        "ORDER BY timestamp DESC"
    )
    for row in cur2.fetchall():
        nid, phase, content, imp, ntype = row
        content = content or ''
        clean = content.strip()
        if len(clean) < 10:
            continue
        dedup_key = clean[:80]
        if dedup_key in seen_content:
            continue
        seen_content.add(dedup_key)
        nodes.append({
            'node_id': nid,
            'content': clean[:1000],
            'source': 'dag',
            'phase': phase or ntype,
            'importance': imp or 0.5,
        })

    # dag_nodes：所有 MESSAGE / cognitive_summary（不限制 is_summary）
    cur3 = conn.execute(
        "SELECT node_id, node_type, content, importance_score "
        "FROM dag_nodes WHERE node_type IN ('MESSAGE','cognitive_summary') "
        "ORDER BY timestamp DESC LIMIT 1000"
    )
    for row in cur3.fetchall():
        nid, ntype, content, imp = row
        content = content or ''
        clean = content.strip()
        if len(clean) < 10:
            continue
        dedup_key = clean[:80]
        if dedup_key in seen_content:
            continue
        seen_content.add(dedup_key)
        nodes.append({
            'node_id': nid,
            'content': clean[:1000],
            'source': 'dag_msg',
            'phase': ntype,
            'importance': imp or 0.5,
        })

    conn.close()

    if not nodes:
        return False

    # 批量生成向量
    texts = [n['content'] for n in nodes]
    try:
        resp = _DAG_EMB_CLIENT.embeddings.create(
            model=_DAG_EMB_MODEL,
            input=texts,
            dimensions=_DAG_EMB_DIM,
        )
        vectors = [d.embedding for d in resp.data]
    except Exception as e:
        logger.warning(f"DAG 索引批量 embedding 失败: {e}")
        return False

    import hnswlib as _hnsw
    dim = len(vectors[0])
    idx = _hnsw.Index(space='ip', dim=dim)

    # 尝试加载已有索引作为起点
    loaded = False
    if os.path.exists(_DAG_HNSW_INDEX_PATH):
        try:
            idx.load_index(_DAG_HNSW_INDEX_PATH)
            loaded = True
        except Exception:
            pass

    if not loaded:
        idx.init_index(max_elements=max(len(vectors), 10000),
                        ef_construction=200, M=16)

    idx.add_items(vectors, [i for i in range(len(vectors))])
    idx.save_index(_DAG_HNSW_INDEX_PATH)

    _DAG_HNSW_INDEX = idx
    _DAG_HNSW_NODES = nodes
    _DAG_HNSW_DB_CHECK = total
    logger.info(f"  DAG HNSW 索引构建完成: {len(nodes)} 节点, dim={dim}")
    return True


def _do_dag(query: str, top_k: int) -> list:
    """2. DAG 上下文检索（HNSWLib 向量语义搜索 rccam_nodes + dag_nodes）

    流程：
      1. 查 embedding API 生成查询向量（1 次调用）
      2. HNSWLib knn_query 本地搜索（0 次额外 API 调用）
      3. 余弦距离 → 最终评分排序
    """
    if not _init_dag_embedding():
        return _do_dag_fallback(query, top_k)

    if not _build_dag_hnsw_index():
        return _do_dag_fallback(query, top_k)

    try:
        # 1 次 API 调用生成查询向量
        q_resp = _DAG_EMB_CLIENT.embeddings.create(
            model=_DAG_EMB_MODEL,
            input=[query],
            dimensions=_DAG_EMB_DIM,
        )
        q_vec = q_resp.data[0].embedding

        n_total = len(_DAG_HNSW_NODES)
        k = min(top_k * 3, n_total) if n_total > 0 else 1
        if k < 1:
            return []

        labels, distances = _DAG_HNSW_INDEX.knn_query(q_vec, k=k)
        labels = labels[0]
        distances = distances[0]

        # ip space: smaller distance = more similar (dot product, negative for similarity)
        # normalize to [0, 1] similarity score
        _r = []
        for idx, dist in zip(labels, distances):
            if idx >= len(_DAG_HNSW_NODES):
                continue
            node = _DAG_HNSW_NODES[idx]
            # ip distance → cosine-like: sim = 1 / (1 + abs(dist))
            sim = 1.0 / (1.0 + abs(dist))
            if sim < 0.2:
                continue  # 相似度太低跳过
            score = sim * 0.8 + (node.get('importance', 0.5)) * 0.2
            _r.append({
                'content': node['content'][:1500],
                'score': round(score, 3),
                'source': node['source'],
                'node_id': node['node_id'],
                'phase': node.get('phase', ''),
            })
            if len(_r) >= top_k:
                break

        logger.info(f"  dag(hnsw): {len(_r)} results (index={n_total} nodes)")
        return _r
    except Exception as e:
        logger.warning(f"  dag(hnsw) failed: {e}")
        return _do_dag_fallback(query, top_k)


def _do_dag_fallback(query: str, top_k: int) -> list:
    """DAG 降级：FTS5 关键词全文检索"""
    _r = []
    try:
        dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
        if not os.path.exists(dag_db):
            return _r
        conn = sqlite3.connect(dag_db)
        # 尝试 FTS5 全文搜索
        try:
            cur = conn.execute(
                "SELECT rowid, node_type, phase_name, content, importance_score, node_id "
                "FROM rccam_nodes WHERE content MATCH ? ORDER BY rank LIMIT ?",
                (query, top_k)
            )
            _r = [{
                'content': (r[3] or '')[:1500],
                'score': 0.5,
                'source': 'dag',
                'node_id': r[5],
                'phase': r[1] or r[2],
            } for r in cur.fetchall()]
        except sqlite3.OperationalError:
            pass

        if not _r:
            # 兜底：关键词 like 搜索
            words = [w for w in re.findall(r'[\w\u4e00-\u9fff]+', query.lower()) if len(w) > 1]
            if words:
                conditions = ' AND '.join([f"LOWER(content) LIKE '%{w}%'" for w in words])
                try:
                    cur = conn.execute(
                        f"SELECT rowid, node_type, content, node_id FROM rccam_nodes "
                        f"WHERE {conditions} LIMIT {top_k}"
                    )
                    _r = [{
                        'content': (r[2] or '')[:1500],
                        'score': 0.3,
                        'source': 'dag_fallback',
                        'node_id': r[3],
                    } for r in cur.fetchall()]
                except Exception:
                    pass
        conn.close()
        logger.info(f"  dag(fallback): {len(_r)} results")
    except Exception as e:
        logger.warning(f"  dag(fallback) failed: {e}")
    return _r


def _do_tencentdb(query: str, top_k: int) -> list:
    """已移除：数据已迁移至 UnifiedVectorStore"""
    return []


def _do_synapse(query: str) -> list:
    """3. 突触巩固引擎检索"""
    _r = []
    try:
        _ws = os.path.expanduser("~/.openclaw/workspace")
        _core_dir = os.path.join(
            _ws, "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core")
        sys.path.insert(0, _core_dir)
        from memory_consolidation import ConsolidationEngine
        ce = ConsolidationEngine(_ws)
        if hasattr(ce, 'consolidate_from_dag'):
            ce.consolidate_from_dag()
        syn_network = ce._get_synapse_network()
        if syn_network:
            q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))
            neurons = syn_network.neuron_manager.get_all_neurons()
            best_nid, best_score = None, 0
            for n in neurons:
                if n.content:
                    n_words = set(re.findall(r'[\w\u4e00-\u9fff]+', n.content.lower()))
                    overlap = len(q_words & n_words) / max(len(q_words), 1)
                    if overlap > best_score:
                        best_score = overlap
                        best_nid = n.id
            if best_nid and best_score > 0:
                associated = syn_network.find_associated(best_nid, top_k=3)
                for n, s in associated:
                    _r.append({
                        'content': n.content[:1000] if n.content else '',
                        'score': float(s),
                        'source': 'synapse',
                    })
        logger.info(f"  synapse: {len(_r)} results")
    except Exception as e:
        logger.debug(f"  synapse skipped: {e}")
    return _r


def _do_paper(query: str, top_k: int) -> list:
    """4. 论文引擎检索（RAPTOR + GraphRAG + Reflection + Toolformer）"""
    _r = []
    try:
        sys.path.insert(0, os.path.expanduser(
            "~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
        from four_advancements import get_advancements
        fa = get_advancements()
        _stat = fa.get_status()
        if not _stat.get('raptor_tree_built'):
            fa.raptor.build_tree()
        if not _stat.get('graphrag_entities'):
            fa.graphrag.extract_from_dag()
        _r = fa.search(query, top_k=min(top_k, 4))
        logger.info(f"  paper engines: {len(_r)} results")
    except Exception as e:
        logger.debug(f"  paper engines skipped: {e}")
    return _r


def _do_web(query: str, max_web_results: int) -> list:
    """5. 联网搜索"""
    _r = []
    try:
        search_script = os.path.expanduser(
            "~/.openclaw/workspace/skills/xiaoyi-web-search/search.js")
        if os.path.exists(search_script):
            result = subprocess.run(
                ["node", search_script, query, "-n", str(max_web_results)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                items = json.loads(result.stdout)
                if isinstance(items, list):
                    for i, item in enumerate(items):
                        _r.append({
                            'content': item.get('content', item.get('snippet', str(item)))[:1500],
                            'score': 0.5 - i * 0.05,
                            'source': 'web',
                            'title': item.get('title', ''),
                            'url': item.get('url', ''),
                        })
        logger.info(f"  web: {len(_r)} results")
    except Exception as e:
        logger.debug(f"  web search skipped: {e}")
    return _r


# ========== 统一入口 ==========

# ========== CRAG 检索质量评估 ==========

def _assess_retrieval_quality(query: str, results: List[Dict]) -> Dict[str, any]:
    """
    检索质量评估 (CRAG 风格)

    Returns:
        judgment: "correct" | "ambiguous" | "incorrect"
        confidence: float 0~1
        top_score: float 最高分
    """
    if not results:
        return {"judgment": "incorrect", "confidence": 0, "top_score": 0}

    top_score = max((r.get("rrf_score", r.get("score", 0)) for r in results), default=0)

    if top_score > 0.5:
        return {"judgment": "correct", "confidence": min(1.0, top_score * 1.2), "top_score": top_score}
    elif top_score > 0.2:
        return {"judgment": "ambiguous", "confidence": top_score, "top_score": top_score}
    else:
        return {"judgment": "incorrect", "confidence": top_score, "top_score": top_score}


# ========== CRAG 分解 → 子查询 → 融合 ==========

def _decompose_query(query: str) -> List[str]:
    """
    把复合 query 拆成子查询。

    优先用 scripts_core.rewriter 的语义扩展，后备用 LLM Flash prompt 拆解。
    兜底策略：用标点和逻辑连接词切分。

    Args:
        query: 原始查询文本

    Returns:
        子查询列表（至少包含原 query）
    """
    sub_queries = []

    if len(query) < 10:
        return [query]

    # 先用 scripts_core 的语义扩展
    try:
        from scripts_core.rewriter import QueryRewriter
        expansions = QueryRewriter.get_synonym_expansions(query)
        for e in expansions:
            if len(e) >= 5 and e.lower() != query.lower():
                sub_queries.append(e)
    except Exception:
        pass

    # 再用 LLM Flash 做语义拆分
    if len(sub_queries) < 2:
        try:
            from xiaoyi_claw_api import get_xiaoyi_claw as _get_xiayi
            inst = _get_xiayi()
            llm_flash = getattr(inst, 'llm_flash', None)
            if llm_flash:
                prompt = (
                    f"将以下复合查询拆解为多个独立的子查询。"
                    f"每个子查询应聚焦单一概念或主题。\n\n"
                    f"查询: {query}\n\n"
                    f"以 JSON 数组格式返回子查询，例如："
                    f"[\"子查询1\", \"子查询2\"]\n"
                    f"如果无需拆解，返回 [\"{query}\"]"
                )
                rsp = llm_flash.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=300,
                    temperature=0.1,
                )
                text = rsp.choices[0].message.content.strip()
                jm = re.search(r'\[[^\]]+\]', text)
                if jm:
                    parsed = json.loads(jm.group())
                    if isinstance(parsed, list) and len(parsed) > 0:
                        sub_queries = sub_queries[:2] + [s.strip() for s in parsed if s.strip() and s.strip() not in sub_queries]
        except Exception as e:
            logger.warning(f"_decompose_query LLM 分词失败: {e}")

    if len(sub_queries) < 2:
        sub_queries = []
        delimiters = [" 和 ", " 与 ", " 及 ", "、", "；", ",", ".", "。", "，", " vs ", " vs "]
        parts = [query]
        for d in delimiters:
            new_parts = []
            for p in parts:
                if p.find(d) >= 0:
                    new_parts.extend(p.split(d))
                else:
                    new_parts.append(p)
            parts = new_parts

        for p in parts:
            p = p.strip()
            if len(p) >= 3 and len(p) < len(query) * 0.9:
                sub_queries.append(p)

    seen = set()
    result = []
    for sq in [query] + sub_queries:
        key = sq.lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(sq)

    logger.info(f"_decompose_query: '{query}' → {result}")
    return result


def _recompose_results(sub_results: List[List[Dict]]) -> List[Dict]:
    """
    子查询结果按 RRF 融合去重。
    """
    if not sub_results:
        return []

    valid = [r for r in sub_results if r]
    if len(valid) <= 1:
        return valid[0] if valid else []

    scores = {}
    sources = {}
    k = 60

    for results in valid:
        for i, r in enumerate(results):
            rid = r.get('content', r.get('id', str(i)))[:200]
            if rid not in scores:
                scores[rid] = 0
                sources[rid] = dict(r)
            scores[rid] += 1 / (k + i + 1)

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    merged = []
    for rid, score in ranked:
        item = sources[rid]
        item['rrf_score'] = round(score, 4)
        item['crag_decomposed'] = True
        merged.append(item)

    return merged


# ========== Adaptive-RAG query 复杂度分类（降级用） ==========

def classify_query_complexity(query: str) -> Dict[str, any]:
    """
    查询复杂度分类（scripts_core 不可用时的降级）
    """
    q_words = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))
    q_len = len(query)

    tech_words = {"代码", "bug", "api", "python", "架构", "配置", "部署",
                  "算法", "协议", "接口", "依赖", "线程", "进程", "缓存",
                  "神经网络", "训练", "优化", "损失函数", "梯度"}
    complex_markers = {"为什么", "如何", "对比", "区别", "原理",
                       "机制", "流程", "步骤", "方案", "设计"}

    has_tech = len(q_words & tech_words) > 0
    has_complex = len(q_words & complex_markers) > 0

    if q_len < 10 and not has_tech:
        return {"level": "simple", "strategy": "quick_recall"}
    if has_complex or (has_tech and q_len > 30):
        return {"level": "complex", "strategy": "deep_search"}
    if has_tech:
        return {"level": "medium", "strategy": "standard_recall"}
    return {"level": "simple", "strategy": "quick_recall"}


# ========== 统一入口 ==========

def retrieval_hub(
    query: str,
    top_k: int = 8,
    include_web: bool = None,
    max_web_results: int = 3,
    enable_crag: bool = True,
    preprocessed: Dict = None,
) -> Dict[str, any]:
    """
    统一检索入口：五路并行，RRF 融合，质量评估。

    自动对 query 做预处理（scripts_core 理解→改写→路由），
    也可通过 preprocessed 参数传入预处理结果跳过重跑。
    """
    start_time = time.time()
    pool = _PARALLEL_POOL

    # 查询预处理
    pp = preprocessed or _preprocess_query(query)
    effective_query = pp.get("rewritten", query)
    mode = pp.get("mode", "balanced")
    intent_name = pp.get("intent", ("search", 0.5))[0]
    complexity = pp.get("complexity", "medium")

    # 以 scripts_core 的分析为准确定 q_class
    q_class = classify_query_complexity(effective_query)
    if mode == "fast":
        q_class["level"] = "simple"
    elif mode == "full":
        q_class["level"] = "complex"
    elif complexity == "simple":
        q_class["level"] = "simple"

    # simple/fast 模式减配
    is_fast = q_class.get("level") == "simple"

    # ═══ 并行发射 (六路: kg + local + dag + synapse + paper + web) ═══
    # KG 作为第 0 路插入，在 RRF 融合时自动与向量检索结果竞争
    future_map = {
        "kg": pool.submit(_do_kg, effective_query, top_k),
        "local": pool.submit(_do_local, effective_query, top_k),
        "dag":   pool.submit(_do_dag, effective_query, top_k),
    }
    if not is_fast:
        future_map["synapse"] = pool.submit(_do_synapse, effective_query)
        future_map["paper"] = pool.submit(_do_paper, effective_query, top_k)

    # 联网搜索
    should_web = include_web is True
    if include_web is None:
        should_web = not is_fast
    if should_web:
        future_map["web"] = pool.submit(_do_web, effective_query, max_web_results)
    else:
        future_map["web"] = None

    # 收集结果
    all_source_results = []
    name_to_results = {}

    for name in ["kg", "local", "dag", "synapse", "paper", "web"]:
        fut = future_map.get(name)
        if fut is None:
            name_to_results[name] = []
            continue
        try:
            r = fut.result(timeout=25)
        except Exception as e:
            logger.warning(f"  {name} parallel failed: {e}")
            r = []
        name_to_results[name] = r
        all_source_results.append(r)

    # RRF 融合
    merged = _rrf_merge(all_source_results)[:top_k]

    # CRAG: 对 complex 查询做 decompose-recompose
    crag_decomposed = False
    if enable_crag and q_class.get('level') == 'complex' and len(effective_query) >= 10:
        sub_queries = _decompose_query(effective_query)
        if len(sub_queries) > 1:
            sub_results_list = []
            for sq in sub_queries:
                if sq.strip().lower() == effective_query.strip().lower():
                    sub_results_list.append(merged)
                    continue
                sq_local = _do_local(sq, max(2, top_k // 2))
                sq_dag = _do_dag(sq, max(2, top_k // 2))
                sq_combined = _rrf_merge([sq_local, sq_dag])[:max(2, top_k // 2)]
                sub_results_list.append(sq_combined)

            if len(sub_results_list) > 1:
                recomposed = _recompose_results(sub_results_list)[:top_k]
                if recomposed:
                    merged = recomposed
                    crag_decomposed = True
                    logger.info(
                        f"CRAG decompose: {len(sub_queries)} sub-queries → {len(merged)} merged results"
                    )

    # 检索质量评估
    quality = _assess_retrieval_quality(effective_query, merged)

    # 置信度
    local_scores = [r.get('score', 0) for r in name_to_results.get('local', [])[:3]]
    dag_scores = [r.get('score', 0) for r in name_to_results.get('dag', [])[:3]]
    kg_scores = [r.get('score', 0) for r in name_to_results.get('kg', [])[:3]]
    all_max = local_scores + dag_scores + kg_scores
    max_local = max(all_max) if all_max else 0
    current_max = max((r.get('rrf_score', r.get('score', 0)) for r in merged), default=0) if merged else 0
    confidence = max(min(current_max * 2, 1.0), max_local)

    stats = {
        "kg": len(name_to_results.get('kg', [])),
        "local": len(name_to_results.get('local', [])),
        "dag": len(name_to_results.get('dag', [])),
        "synapse": len(name_to_results.get('synapse', [])),
        "paper": len(name_to_results.get('paper', [])),
        "web": len(name_to_results.get('web', [])),
        "total": sum(len(r) for r in all_source_results),
        "merged": len(merged),
        "confidence": round(quality["confidence"], 2),
        "quality_judgment": quality["judgment"],
        "query_complexity": q_class["level"],
        "query_mode": mode,
        "query_rewritten": effective_query,
        "query_corrections": pp.get("corrections", []),
        "query_intent": intent_name,
        "crag_decomposed": crag_decomposed,
        "preprocessed": True,
        "time_ms": round((time.time() - start_time) * 1000),
    }

    return {
        "results": merged,
        "stats": stats,
        "sources": all_source_results,
        "quality": quality,
        "query_class": q_class,
        "preprocess": {
            "original": query,
            "rewritten": effective_query,
            "corrections": pp.get("corrections", []),
            "mode": mode,
            "intent": intent_name,
            "complexity": complexity,
            "entities": pp.get("entities", []),
        },
    }
