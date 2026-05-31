"""retrieval_hub.py — 统一检索入口

集成五路检索（本地 + DAG + 突触 + 论文引擎 + 联网搜索），
结果 RRF 融合排序，统一返回格式。

提供给 R-CCAM 的 _retrieval_phase 和 _action_phase 使用。
"""

import os, sys, json, logging, time, re, sqlite3
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ---------- RRF 融合 ----------
def _rrf_merge(all_results: List[List[Dict]], k: int = 60) -> List[Dict]:
    """RRF 融合多路检索结果"""
    scores = {}
    sources = {}
    for results in all_results:
        for i, r in enumerate(results):
            rid = r.get('content', r.get('id', str(i)))[:200]
            if rid not in scores:
                scores[rid] = 0
                sources[rid] = r
            scores[rid] += 1 / (k + i + 1)
            # 合并 source 标签
            src = r.get('source', 'unknown')
            if 'sources' not in sources[rid]:
                sources[rid]['sources'] = set()
            sources[rid]['sources'].add(src)
    
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    merged = []
    for rid, score in ranked:
        item = dict(sources[rid])
        item['rrf_score'] = round(score, 4)
        item['source'] = '/'.join(sorted(sources[rid]['sources'])) if isinstance(sources[rid].get('sources'), set) else item.get('source', 'unknown')
        if 'sources' in item:
            del item['sources']
        merged.append(item)
    return merged


def retrieval_hub(
    query: str,
    top_k: int = 8,
    include_web: bool = None,
    max_web_results: int = 3,
) -> Dict[str, any]:
    """
    统一检索入口：五路并行，RRF 融合。
    
    Args:
        query: 查询文本
        top_k: 返回结果数
        include_web: 是否联网搜索（None=自动判断/True=强制/False=禁用）
        max_web_results: 联网最多几条
    
    Returns:
        {
            "results": [...],       # RRF 融合后的结果列表
            "sources": [...],       # 各来源的原始结果路径
            "stats": {              # 各路的统计
                "local": N, "dag": N, "synapse": N,
                "paper": N, "web": N, "confidence": 0.0
            }
        }
    """
    start_time = time.time()
    all_source_results = []
    
    # ===== 1. 本地检索（yaoyao_bridge） =====
    local_results = []
    try:
        sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/scripts"))
        from yaoyao_bridge import search_yaoyao
        local_results = search_yaoyao(query, limit=top_k)
        # 标准化字段
        for r in local_results:
            r.setdefault('source', 'local')
            r.setdefault('score', 0)
            r['content'] = r.get('user_text', r.get('content', str(r)))
        logger.info(f"  local: {len(local_results)} results")
    except Exception as e:
        logger.warning(f"  local failed: {e}")
    all_source_results.append(local_results)
    
    # ===== 2. DAG 上下文检索（语义搜索 rccam_nodes） =====
    dag_results = []
    try:
        dag_db = os.path.expanduser("~/.openclaw/workspace/.dag_context.db")
        if os.path.exists(dag_db):
            conn = sqlite3.connect(dag_db)
            cur = conn.execute("SELECT rowid, node_type, phase_name, content, importance_score, confidence, node_id "
                               "FROM rccam_nodes ORDER BY timestamp DESC LIMIT 100")
            all_nodes = cur.fetchall()
            conn.close()
            q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))
            scored = []
            for rowid, ntype, phase, content, imp, conf, nid in all_nodes:
                content = content or ''
                n_words = set(re.findall(r'[\w\u4e00-\u9fff]+', content.lower()))
                overlap = len(q_words & n_words)
                if overlap > 0:
                    score = overlap / max(len(q_words), 1) * 0.7 + (imp or 0.5) * 0.3
                    scored.append({
                        'content': content[:1500],
                        'score': round(score, 3),
                        'source': 'dag',
                        'node_id': nid,
                        'phase': phase or ntype,
                    })
            scored.sort(key=lambda x: -x['score'])
            dag_results = scored[:top_k]
        logger.info(f"  dag: {len(dag_results)} results")
    except Exception as e:
        logger.warning(f"  dag failed: {e}")
    all_source_results.append(dag_results)
    
    # ===== 3. 突触巩固引擎检索（MemorySynapseNetwork） =====
    synapse_results = []
    try:
        _ws = os.path.expanduser("~/.openclaw/workspace")
        _core_dir = os.path.join(_ws, "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core")
        sys.path.insert(0, _core_dir)
        from memory_consolidation import ConsolidationEngine
        ce = ConsolidationEngine(_ws)
        # 预热：将 DAG 高重要性节点固化到突触网络
        if hasattr(ce, 'consolidate_from_dag'):
            ce.consolidate_from_dag()
        # 取突触网络做检索
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
                    synapse_results.append({
                        'content': n.content[:1000] if n.content else '',
                        'score': float(s),
                        'source': 'synapse',
                    })
        logger.info(f"  synapse: {len(synapse_results)} results")
    except Exception as e:
        logger.debug(f"  synapse skipped: {e}")
    all_source_results.append(synapse_results)
    
    # ===== 4. 论文引擎检索（RAPTOR + GraphRAG + Reflection + Toolformer） =====
    paper_results = []
    try:
        sys.path.insert(0, os.path.expanduser(
            "~/.openclaw/workspace/skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
        from four_advancements import get_advancements
        fa = get_advancements()
        # 预热引擎（首次调用自动构建摘要树和实体图）
        _stat = fa.get_status()
        if not _stat.get('raptor_tree_built'):
            fa.raptor.build_tree()
        if not _stat.get('graphrag_entities'):
            fa.graphrag.extract_from_dag()
        paper_results = fa.search(query, top_k=4)
        logger.info(f"  paper engines: {len(paper_results)} results")
    except Exception as e:
        logger.debug(f"  paper engines skipped: {e}")
    all_source_results.append(paper_results)
    
    # ===== 5. 联网搜索 =====
    web_results = []
    if include_web is not False:
        # 自动判断：本地结果置信度低时自动触发
        local_scores = [r.get('score', 0) for r in local_results[:3]]
        dag_scores = [r.get('score', 0) for r in dag_results[:3]]
        max_local = max(local_scores + dag_scores) if (local_scores or dag_scores) else 0
        should_web = (include_web is True) or (max_local < 0.3)
        
        if should_web:
            try:
                import subprocess
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
                                web_results.append({
                                    'content': item.get('content', item.get('snippet', '{}').format(item))[:1500],
                                    'score': 0.5 - i * 0.05,
                                    'source': 'web',
                                    'title': item.get('title', ''),
                                    'url': item.get('url', ''),
                                })
                logger.info(f"  web: {len(web_results)} results")
            except Exception as e:
                logger.debug(f"  web search skipped: {e}")
    all_source_results.append(web_results)
    
    # ===== RRF 融合 =====
    merged = _rrf_merge(all_source_results)[:top_k]
    
    # ===== 计算统计 =====
    current_max = max((r.get('score', 0) for r in merged), default=0) if merged else 0
    confidence = max(min(current_max * 2, 1.0), 0)
    
    stats = {
        "local": len(local_results),
        "dag": len(dag_results),
        "synapse": len(synapse_results),
        "paper": len(paper_results),
        "web": len(web_results),
        "total": sum(len(r) for r in all_source_results),
        "merged": len(merged),
        "confidence": round(confidence, 2),
        "time_ms": round((time.time() - start_time) * 1000),
    }
    
    return {
        "results": merged,
        "stats": stats,
        "sources": all_source_results,
    }
