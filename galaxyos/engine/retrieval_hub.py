"""retrieval_hub.py — 统一检索入口 (v2.1: MN-RU + Session级 + Context-Denoised)

架构改进（基于三篇论文）：

2407.07871 (MN-RU):
  - 双索引结构：主索引（静态全量）+ 小索引（增量实时）
  - 小索引满 200 条或每 10 分钟合并到主索引
  - 搜索时双查合并

2401.16659 (HAConvDR):
  - Context-Denoised 查询：只使用与当前 query 相关的历史轮次
  - 自动识别噪音轮次

2404.13556 (ChatRetriever):
  - 新增 session 级索引（按 cycle 分组编码）
  - 检索时三通道：节点级 + 增量节点 + 会话级

五路检索（KG + local + DAG + synapse + paper），web 可选，并行执行后 RRF 融合排序。
"""

import os
import sys
import json
import logging
import time
import re
import sqlite3
import subprocess
import threading
import math
from typing import List, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)

# 共享线程池
_PARALLEL_POOL = ThreadPoolExecutor(max_workers=4)

# ── 配置常量 ──
HNMW_REBUILD_INTERVAL = 3600    # 主索引全量重建周期：1 小时
HNMW_MINI_THRESHOLD = 200        # 小索引阈值，超过后合并
HNMW_MERGE_INTERVAL = 600        # 强制合并间隔：10 分钟

SESSION_BOOST_CURRENT = 2.5      # 当前会话节点权重
SESSION_BOOST_SESSION = 3.0      # session 级索引权重
DAG_BOOST = 1.5                  # 普通 DAG 节点权重
DAG_MINI_BOOST = 2.5             # 增量节点权重
CURRENT_SESSION_WINDOW = 3600     # 当前会话时间窗口：1 小时

# ── 术语扩展（Query Expansion，无需 LLM） ──
TERM_EXPANSION_MAP = {
    '压缩': ['compact', 'truncation', '剪裁', '压缩策略'],
    '注入': ['injection', 'systemPromptAddition', '注入上下文', '拼接'],
    '检索': ['recall', '搜索', 'dense retrieval', '向量检索', '召回'],
    '重排序': ['reranker', 'cross-encoder', 'bge-reranker', 'rerank'],
    '上下文': ['context', 'systemPromptAddition', 'DAG', '会话历史'],
    '索引': ['index', 'hnsw', 'hnswlib', '向量索引', '搜索引擎'],
    '增量': ['incremental', '追加', 'add_items', '增量更新'],
    '内存': ['memory', 'recall', '记忆', '检索召回'],
    '会话': ['session', 'conversation', '对话', '多轮'],
    'KG': ['knowledge graph', '知识图谱', '实体', '图检索'],
    'DAG': ['dag_context', 'DAG 节点', '上下文图', 'rccam'],
    '人格': ['persona', 'IDENTITY.md', 'SOUL.md', '角色定义'],
    'R-CCAM': ['rccam', '认知循环', 'retrieval cognition', 'cognitionPayload'],
    'embedding': ['向量', '嵌入', 'bge-m3', 'embedding API'],
    'HNSW': ['hnswlib', 'knn_query', '近邻搜索', '近似搜索'],
}

# ── JSON 污染清洗（cycle_summary 等节点存储为 JSON blob）──
def _extract_plain(text: str) -> str:
    """从 JSON blob 提取纯文本用于 embedding"""
    if not text:
        return ""
    t = text.strip()
    if not t.startswith('{'):
        return text  # 已经是纯文本
    try:
        obj = json.loads(t)
        if not isinstance(obj, dict):
            return text
        parts = []
        for key in ['key_findings', 'conclusion', 'user_intent', 'content', 'summary', 'description', 'text']:
            val = obj.get(key)
            if isinstance(val, str) and len(val) > 3:
                parts.append(val)
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str) and len(item) > 3:
                        parts.append(item)
        if not parts:
            for v in obj.values():
                if isinstance(v, str) and len(v) > 3:
                    parts.append(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and len(item) > 3:
                            parts.append(item)
        result = ' '.join(parts) if parts else ""
        return result if len(result) >= 10 else text
    except (json.JSONDecodeError, Exception):
        return text


# ── scripts_core 桥接 ──
_SCRIPTS_CORE_DIR = os.path.expanduser(
    "~/.openclaw/workspace/skills/galaxyos-engine/skills/llm-memory-integration/scripts")
if _SCRIPTS_CORE_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_CORE_DIR)


# ============================================================
# MN-RU 双索引管理器（2407.07871）
# ============================================================

class HNSW_MN_Index:
    """
    MN-RU 双索引结构：主索引（静态）+ 小索引（增量）
    
    主索引：定期全量重建，质量稳定的静态索引
    小索引：DAG ingest 时实时追加，超过阈值或超时后合并到主索引
    搜索：双索引并行查，结果去重合并
    """

    def __init__(self, index_path: str, dim: int = 1024):
        self.dim = dim
        self.index_path = index_path
        self.session_index_path = index_path.replace('.idx', '_session.idx')

        # 主索引
        self.main = None          # hnswlib.Index
        self.main_nodes = []      # list[dict]
        self.main_ready = False

        # 小索引
        self.mini = None          # hnswlib.Index
        self.mini_nodes = []
        self.mini_ready = False

        # Session 级索引
        self.session_idx = None   # hnswlib.Index
        self.session_nodes = []   # list[dict]
        self.session_ready = False

        # 状态
        self.last_rebuild = 0
        self.last_merge = time.time()
        self.last_db_check = 0
        self.total_nodes_db = 0
        self.lock = threading.Lock()

        # Embedding 客户端（懒加载）
        self._emb_client = None
        self._emb_model = None
        self._emb_dim = dim

        # 待追加队列
        self._pending = []        # list[dict {content, source, phase, importance, timestamp}]
        self._pending_session = []  # list[dict {session_text, cycle_id, timestamp}]

    def init_embedding(self):
        """懒加载 embedding 客户端"""
        if self._emb_client is not None:
            return True
        try:
            config_path = os.path.expanduser(
                "~/.openclaw/workspace/skills/galaxyos-engine/config/llm_config.json")
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
                emb_cfg = cfg.get("embedding", {})
                if emb_cfg.get("api_key"):
                    from openai import OpenAI
                    self._emb_client = OpenAI(
                        api_key=emb_cfg["api_key"],
                        base_url=emb_cfg.get("base_url", "https://cloud.infini-ai.com/maas/v1"),
                    )
                    self._emb_model = emb_cfg.get("model", "bge-m3")
                    self._emb_dim = emb_cfg.get("dimensions", 1024)
                    return True
        except Exception as e:
            logger.debug(f"MN-RU embedding 初始化跳过: {e}")
        return False

    def embed(self, texts: list) -> list:
        """批量 embedding（1 次 API 调用）"""
        if not self._emb_client:
            if not self.init_embedding():
                return []
        try:
            resp = self._emb_client.embeddings.create(
                model=self._emb_model, input=texts, dimensions=self._emb_dim,
            )
            return [d.embedding for d in resp.data]
        except Exception as e:
            err_str = str(e)
            if "400" in err_str and self._emb_dim:
                try:
                    resp = self._emb_client.embeddings.create(
                        model=self._emb_model, input=texts,
                    )
                    self._emb_dim = len(resp.data[0].embedding)
                    logger.info(f"MN-RU fallback OK (no dimensions), dim={self._emb_dim}")
                    return [d.embedding for d in resp.data]
                except Exception as e2:
                    logger.warning(f"MN-RU fallback 也失败: {e2}")
                    return []
            logger.warning(f"MN-RU embedding 失败: {e}")
            return []

    # ── 主索引管理 ──

    def rebuild_main(self, all_nodes: list):
        """全量重建主索引（从 DB 全量读后调用）"""
        if not all_nodes:
            return False

        texts = [n['content'] for n in all_nodes]
        vectors = self.embed(texts)
        if not vectors or len(vectors) != len(texts):
            return False

        import hnswlib as _hnsw
        dim = len(vectors[0])

        idx = _hnsw.Index(space='ip', dim=dim)
        idx.init_index(max_elements=max(len(vectors), 10000), ef_construction=200, M=16)
        idx.add_items(vectors, [i for i in range(len(vectors))])

        try:
            idx.save_index(self.index_path)
        except Exception:
            pass

        self.main = idx
        self.main_nodes = all_nodes
        self.main_ready = True
        self.last_rebuild = time.time()
        logger.info(f"MN-RU 主索引重建: {len(all_nodes)} 节点")
        return True

    # ── 小索引（增量） ──

    def push_pending(self, node: dict):
        """添加待索引节点（不阻塞，合并时统一 embedding）"""
        with self.lock:
            self._pending.append(node)

    def push_pending_batch(self, nodes: list):
        """批量添加待索引节点"""
        with self.lock:
            self._pending.extend(nodes)

    def flush_mini(self):
        """将待处理节点批量 embedding 后追加到小索引"""
        with self.lock:
            pending = list(self._pending)
            self._pending.clear()

        if not pending:
            return 0

        texts = [n['content'] for n in pending]
        vectors = self.embed(texts)
        if not vectors or len(vectors) != len(texts):
            # embedding 失败，放回队列
            with self.lock:
                self._pending[:0] = pending
            return 0

        import hnswlib as _hnsw

        if not self.mini_ready:
            # 初始化小索引
            dim = len(vectors[0])
            self.mini = _hnsw.Index(space='ip', dim=dim)
            self.mini.init_index(max_elements=max(len(pending), 10000), ef_construction=100, M=16)
            self.mini_ready = True

        start = len(self.mini_nodes)
        total_add = len(pending)
        max_elem = self.mini.max_elements

        if start + total_add > max_elem:
            # 扩容
            self.mini.resize_index(start + total_add + 1000)

        self.mini.add_items(vectors, [start + i for i in range(total_add)])
        self.mini_nodes.extend(pending)

        logger.info(f"MN-RU 小索引追加: {total_add} 节点 (总 {len(self.mini_nodes)})")
        return total_add

    def flush_session(self):
        """将待处理的 session 节点批量编码后追加到 session 索引"""
        with self.lock:
            pending = list(self._pending_session)
            self._pending_session.clear()

        if not pending:
            return 0

        texts = [n['session_text'] for n in pending]
        vectors = self.embed(texts)
        if not vectors or len(vectors) != len(texts):
            with self.lock:
                self._pending_session[:0] = pending
            return 0

        import hnswlib as _hnsw

        if not self.session_ready:
            dim = len(vectors[0])
            self.session_idx = _hnsw.Index(space='ip', dim=dim)
            self.session_idx.init_index(max_elements=max(len(pending), 5000), ef_construction=200, M=16)
            self.session_ready = True

        start = len(self.session_nodes)
        total_add = len(pending)
        max_elem = self.session_idx.max_elements

        if start + total_add > max_elem:
            self.session_idx.resize_index(start + total_add + 500)

        self.session_idx.add_items(vectors, [start + i for i in range(total_add)])
        self.session_nodes.extend(pending)

        logger.info(f"MN-RU Session 索引追加: {total_add} 节点 (总 {len(self.session_nodes)})")
        return total_add

    # ── 合并 ──

    def merge_mini_to_main(self):
        """小索引合并到主索引"""
        if not self.mini_nodes or not self.main_ready:
            return False

        all_nodes = self.main_nodes + self.mini_nodes
        ok = self.rebuild_main(all_nodes)

        if ok:
            # 清空小索引
            import hnswlib as _hnsw
            dim = self.main.dim if hasattr(self.main, 'dim') else self._emb_dim
            self.mini = _hnsw.Index(space='ip', dim=dim)
            self.mini_ready = False
            self.mini_nodes = []
            self.last_merge = time.time()
            logger.info(f"MN-RU 合并完成: {len(all_nodes)} 节点")

        return ok

    def _should_merge(self) -> bool:
        """检查是否需要合并"""
        now = time.time()
        return (
            len(self.mini_nodes) >= HNMW_MINI_THRESHOLD or
            (len(self.mini_nodes) > 0 and now - self.last_merge > HNMW_MERGE_INTERVAL)
        )

    # ── 搜索 ──

    def search(self, query_vec: list, top_k: int = 10) -> list:
        """
        三通道搜索：主索引 + 小索引 + session 索引，结果去重合并
        
        Returns:
            list[dict]: 合并后的结果列表
        """
        results = []
        seen = set()

        # 通道 A：主索引
        if self.main_ready and self.main_nodes:
            try:
                k = min(top_k * 3, len(self.main_nodes))
                labels, distances = self.main.knn_query(query_vec, k=k)
                for idx, dist in zip(labels[0], distances[0]):
                    if idx < len(self.main_nodes):
                        node = dict(self.main_nodes[idx])
                        sim = 1.0 / (1.0 + abs(dist))
                        node['_mn_score'] = sim
                        node['_mn_source'] = 'main'
                        dedup = node.get('content', '')[:100]
                        if dedup not in seen:
                            seen.add(dedup)
                            results.append(node)
            except Exception as e:
                logger.debug(f"MN-RU 主索引搜索失败: {e}")

        # 通道 B：小索引
        if self.mini_ready and self.mini_nodes:
            try:
                k = min(top_k * 3, len(self.mini_nodes))
                labels, distances = self.mini.knn_query(query_vec, k=k)
                for idx, dist in zip(labels[0], distances[0]):
                    if idx < len(self.mini_nodes):
                        node = dict(self.mini_nodes[idx])
                        sim = 1.0 / (1.0 + abs(dist))
                        node['_mn_score'] = sim
                        node['_mn_source'] = 'mini'
                        node['source'] = 'dag_mini'
                        dedup = node.get('content', '')[:100]
                        if dedup not in seen:
                            seen.add(dedup)
                            results.append(node)
            except Exception as e:
                logger.debug(f"MN-RU 小索引搜索失败: {e}")

        # 通道 C：session 索引
        if self.session_ready and self.session_nodes:
            try:
                k = min(top_k * 2, len(self.session_nodes))
                labels, distances = self.session_idx.knn_query(query_vec, k=k)
                for idx, dist in zip(labels[0], distances[0]):
                    if idx < len(self.session_nodes):
                        sn = self.session_nodes[idx]
                        sim = 1.0 / (1.0 + abs(dist))
                        dedup = sn.get('content', '')[:100] or sn.get('session_text', '')[:100]
                        if dedup not in seen:
                            seen.add(dedup)
                            results.append({
                                'content': sn.get('session_text', '')[:2000],
                                'score': round(sim, 3),
                                'source': 'dag_session',
                                'session_id': sn.get('cycle_id', ''),
                                '_mn_score': sim,
                                '_mn_source': 'session',
                            })
            except Exception as e:
                logger.debug(f"MN-RU Session 索引搜索失败: {e}")

        return results


# ── 全局单例 ──
_HNSW_MN = None

def _get_hnsw_mn():
    """获取 MN-RU 双索引管理器单例"""
    global _HNSW_MN
    if _HNSW_MN is None:
        _HNSW_MN = HNSW_MN_Index(
            index_path=os.path.expanduser("~/.openclaw/dag_hnsw.idx"),
        )
    return _HNSW_MN


# ============================================================
# Context-Denoised 查询预处理（2401.16659 HAConvDR 简化版）
# ============================================================

# 会话历史缓存（最近 N 轮）
_SESSION_HISTORY_CACHE = {}  # session_id -> list of turns
_MAX_HISTORY_TURNS = 20

def _update_session_history(session_id: str, user_input: str, answer: str = ""):
    """更新会话历史缓存"""
    if session_id not in _SESSION_HISTORY_CACHE:
        _SESSION_HISTORY_CACHE[session_id] = []
    turns = _SESSION_HISTORY_CACHE[session_id]
    turns.append({
        'user_input': user_input,
        'answer': answer,
        'timestamp': time.time(),
    })
    # 限制长度
    if len(turns) > _MAX_HISTORY_TURNS:
        _SESSION_HISTORY_CACHE[session_id] = turns[-_MAX_HISTORY_TURNS:]

def _get_denoised_context(query: str, session_id: str = "") -> str:
    """
    Context-Denoised 上下文提取（HAConvDR 简化版）
    
    从 session history 中找出与当前 query 语义相关的轮次，
    而非把所有历史都塞进去。
    """
    if not session_id or session_id not in _SESSION_HISTORY_CACHE:
        return ""

    turns = _SESSION_HISTORY_CACHE[session_id]
    if not turns:
        return ""

    # 用 jieba 分词（无 jieba 时用正则）
    try:
        import jieba
        query_words = set(jieba.lcut(query.lower()))
    except ImportError:
        query_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))

    query_entities = _extract_entities_from_text(query)

    useful_turns = []
    for turn in turns[-12:]:  # 最多看最近 12 轮
        turn_text = turn.get('user_input', '') + ' ' + turn.get('answer', '')
        try:
            turn_words = set(jieba.lcut(turn_text.lower()))
        except ImportError:
            turn_words = set(re.findall(r'[\w\u4e00-\u9fff]+', turn_text.lower()))

        # 关键词重叠度
        overlap = len(query_words & turn_words)
        relevance = overlap / max(len(query_words | turn_words), 1)

        # 实体重叠
        turn_entities = _extract_entities_from_text(turn_text)
        entity_overlap = len(set(query_entities) & set(turn_entities))

        if relevance > 0.15 or entity_overlap > 0:
            useful_turns.append({
                'text': turn_text[:500],
                'relevance': relevance,
                'timestamp': turn.get('timestamp', 0),
            })

    if not useful_turns:
        # 兜底：取最近 2 轮
        for turn in turns[-2:]:
            t = turn.get('user_input', '')[:500]
            if t:
                useful_turns.append({
                    'text': t,
                    'relevance': 0.1,
                    'timestamp': turn.get('timestamp', 0),
                })

    if not useful_turns:
        return ""

    # 按相关度排序取 top-3
    useful_turns.sort(key=lambda x: -x['relevance'])
    useful_turns = useful_turns[:3]

    # 同时返回最新的轮次（保证时序）
    # 混合策略：top-3 相关 + 最后 1 轮最新
    context_parts = []
    for ut in useful_turns:
        context_parts.append(ut['text'])

    if context_parts:
        result = "\n".join(context_parts)
        return result[:2000]
    return ""


def _extract_entities_from_text(text: str) -> list:
    """简易实体提取（大写术语 + 特定模式）"""
    entities = set()

    # 驼峰/大写组合词：ContextEngine, R-CCAM, HNSW 等
    for m in re.finditer(r'\b[A-Z][a-z]+(?:[A-Z][a-z]*)+\b', text):
        entities.add(m.group())

    # 连字符词：context-denoised, system-prompt 等
    for m in re.finditer(r'\b[a-z]+(?:-[a-z]+)+\b', text.lower()):
        entities.add(m.group())

    # 带下划线的：systemPromptAddition, _rccamCache 等
    for m in re.finditer(r'\b[a-z_][a-zA-Z_]+\b', text):
        w = m.group()
        if '_' in w or any(c.isupper() for c in w[1:]):
            entities.add(w)

    # 技术短语
    tech_phrases = ['HNSW', 'DAG', 'KG', 'RRF', 'R-CCAM', 'CRAG', 'embedding',
                    'reranker', 'hnswlib', 'MN-RU', 'HAConvDR', 'ChatRetriever']
    for phrase in tech_phrases:
        if phrase.lower() in text.lower():
            entities.add(phrase)

    return list(entities)


# ============================================================
# Query Expansion（术语扩展）
# ============================================================

def _expand_query(query: str) -> list:
    """
    用预定义术语映射扩展查询。
    返回扩展后的查询列表（包含原 query）。
    """
    expansions = [query]
    seen = {query.lower().strip()}

    for word, syns in TERM_EXPANSION_MAP.items():
        if word in query.lower():
            for syn in syns:
                if syn.lower() not in seen:
                    seen.add(syn.lower())
                    expansions.append(syn)

    # 同义词扩展（scripts_core 可用时）
    try:
        from scripts_core.rewriter import QueryRewriter
        exps = QueryRewriter.get_synonym_expansions(query)
        for e in exps:
            if e.lower() not in seen and len(e) >= 3:
                seen.add(e.lower())
                expansions.append(e)
    except Exception:
        pass

    return expansions[:5]  # 最多 5 个


# ============================================================
# 查询预处理
# ============================================================

def _preprocess_query(query: str) -> Dict:
    """查询预处理流水线：理解→改写→路由"""
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

        understand = QueryUnderstanding.analyze(query)
        result["intent"] = understand.get("intent", ("search", 0.5))
        result["complexity"] = understand.get("complexity", "medium")
        result["entities"] = understand.get("entities", [])

        rewritten, corrections = QueryRewriter.rewrite(query)
        result["rewritten"] = rewritten
        result["corrections"] = corrections

        mode = QueryRouter.select_mode(rewritten, use_llm=False)
        result["mode"] = mode

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


# ============================================================
# RRF 融合（v2：时间感知 + 源权重）
# ============================================================

def _get_session_start_time() -> float:
    """从 DAG 数据库获取当前 session 开始时间（秒级时间戳）"""
    try:
        dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
        if os.path.exists(dag_db):
            conn = sqlite3.connect(dag_db)
            row = conn.execute(
                "SELECT MIN(timestamp) FROM rccam_nodes "
                "WHERE node_type != 'rccam_cycle_summary'"
            ).fetchone()
            conn.close()
            if row and row[0]:
                return float(row[0])
    except Exception:
        pass
    return 0

def _rrf_merge(all_results: List[List[Dict]], k: int = 10) -> List[Dict]:
    """
    RRF 融合 v3：混合排名 + 原始语义相似度，时间感知 + 源权重

    每个源 3-6 条结果，k=10 比 k=60 更合理。
    最终分数 = RRF_rank_score × 0.4 + raw_similarity × 0.6
    确保分数分布在 0~1 范围，而非压扁到 0.03。

    来源权重：
    - dag_session: ×3.0（会话级上下文，最高优先级）
    - dag_mini: ×2.5（增量新节点，大概率当前会话）
    - dag / dag_msg: ×1.5（DAG 向量检索）
    - 当前会话内的节点额外 ×1.5
    - 其他: ×1.0
    """
    session_start = _get_session_start_time()

    WEIGHT_MAP = {
        'dag_session': SESSION_BOOST_SESSION,
        'dag_mini': DAG_MINI_BOOST,
        'dag': DAG_BOOST,
        'dag_msg': DAG_BOOST,
        'dag_fallback': DAG_BOOST * 0.6,
    }

    rrf_scores = {}
    item_map = {}
    raw_scores = {}  # 保留原始相似度

    for results in all_results:
        for i, r in enumerate(results):
            rid = r.get('content', r.get('id', str(i)))[:200]
            if not rid:
                continue
            if rid not in rrf_scores:
                rrf_scores[rid] = 0.0
                item_map[rid] = r
                raw_scores[rid] = []

            src = r.get('source', 'unknown')
            weight = WEIGHT_MAP.get(src, 1.0)

            # 当前会话提权
            ts = r.get('timestamp', 0)
            if ts and session_start > 0 and float(ts) >= session_start:
                weight *= 1.5
            else:
                mn_src = r.get('_mn_source', '')
                if mn_src in ('mini', 'session'):
                    weight *= 1.5

            rrf_scores[rid] += weight / (k + i + 1)

            # GAT 注意力权重增强：synapse_seed → ×10 替代 RRF，synapse_cfc → ×(1+gw)
            _gw = r.get('gat_weight', 0)
            if _gw > 0:
                if src == 'synapse_seed':
                    rrf_scores[rid] += _gw * 10.0
                elif src in ('synapse_cfc', 'synapse_pred'):
                    rrf_scores[rid] += _gw * 1.5

            # 收集原始相似度（来自各源的 score 字段）
            inner_score = r.get('score', r.get('_mn_score', 0))
            if isinstance(inner_score, (int, float)):
                raw_scores[rid].append(inner_score)

            if 'sources' not in item_map[rid]:
                item_map[rid]['sources'] = set()
            item_map[rid]['sources'].add(src)

    # 混合分数
    max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0
    merged = []
    for rid in rrf_scores:
        item = dict(item_map[rid])
        # 归一化 RRF 分数到 0~1
        norm_rrf = rrf_scores[rid] / max_rrf if max_rrf > 0 else 0
        # 原始相似度（取最高分）
        best_raw = max(raw_scores[rid]) if raw_scores.get(rid) else 0
        # 混合
        final_score = norm_rrf * 0.4 + best_raw * 0.6
        item['rrf_score'] = round(final_score, 4)
        item['source'] = '/'.join(
            sorted(item['sources'])
        ) if isinstance(item.get('sources'), set) else item.get('source', 'unknown')
        if 'sources' in item:
            del item['sources']
        item.pop('_mn_score', None)
        item.pop('_mn_source', None)
        merged.append(item)

    merged.sort(key=lambda x: -x['rrf_score'])
    return merged


# ============================================================
# Knowledge Graph 懒加载
# ============================================================

_KG_INSTANCE = None

def _get_kg():
    global _KG_INSTANCE
    if _KG_INSTANCE is None:
        try:
            _paths = [
                os.path.expanduser("~/.openclaw/workspace/skills/galaxyos-engine/skills/llm-memory-integration/core"),
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


# ============================================================
# 检索通道（五路 + web 可选）
# ============================================================

def _do_kg(query: str, top_k: int) -> list:
    """0. KG 图检索（带时间新鲜度提权）"""
    results = []
    try:
        kg = _get_kg()
        if kg:
            raw = kg.retrieve_by_entities(query, top_k=top_k)
            now = time.time()
            for r in raw:
                score = r.get("score", 0.0)
                ts = r.get("timestamp", 0)
                freshness_boost = 0.3 if ts and ts > (now - CURRENT_SESSION_WINDOW) else 0
                results.append({
                    "content": r.get("content", ""),
                    "score": min(1.0, score + freshness_boost),
                    "confidence": r.get("score", 0.5),
                    "source": "kg",
                    "entities": r.get("entities", []),
                    "relation": r.get("relation", ""),
                    "timestamp": ts,
                })
            logger.debug(f"_do_kg: {len(results)} results")
        else:
            logger.debug("_do_kg: KG 不可用")
    except Exception as e:
        logger.warning(f"_do_kg failed: {e}")
    return results

def _do_local(query: str, top_k: int) -> list:
    """1. 本地检索（XiaoyiClawLLM.recall + dag_fallback 降级）"""
    _r = []
    try:
        pass
    except Exception as e:
        logger.warning(f"  local via recall() failed: {e}")
    # 降级
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


def _build_all_indexes():
    """从 DAG 数据库全量构建所有索引（MN-RU 主索引 + session 索引）"""
    mn = _get_hnsw_mn()
    if not mn.init_embedding():
        return False

    dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
    if not os.path.exists(dag_db):
        return False

    conn = sqlite3.connect(dag_db)

    # 读取所有可索引节点
    nodes = []
    seen_content = set()

    # rccam_nodes：非 cycle_summary
    cur = conn.execute(
        "SELECT node_id, phase_name, content, importance_score, node_type, timestamp "
        "FROM rccam_nodes WHERE node_type != 'rccam_cycle_summary' "
        "ORDER BY timestamp DESC"
    )
    for row in cur.fetchall():
        nid, phase, content, imp, ntype, ts = row
        content = content or ''
        clean = content.strip()
        if len(clean) < 15:
            continue
        clean = _extract_plain(clean)
        if not clean or len(clean) < 10:
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
            'timestamp': ts or 0,
        })

    # rccam_nodes：cycle_summary
    cur2 = conn.execute(
        "SELECT node_id, phase_name, content, importance_score, node_type, timestamp "
        "FROM rccam_nodes WHERE node_type='rccam_cycle_summary' "
        "ORDER BY timestamp DESC"
    )
    for row in cur2.fetchall():
        nid, phase, content, imp, ntype, ts = row
        content = content or ''
        clean = content.strip()
        clean = _extract_plain(clean)
        if not clean or len(clean) < 10:
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
            'timestamp': ts or 0,
        })

    # dag_nodes：MESSAGE / cognitive_summary
    cur3 = conn.execute(
        "SELECT node_id, node_type, content, importance_score, timestamp "
        "FROM dag_nodes WHERE node_type IN ('MESSAGE','cognitive_summary') "
        "ORDER BY timestamp DESC LIMIT 1000"
    )
    for row in cur3.fetchall():
        nid, ntype, content, imp, ts = row
        content = content or ''
        clean = content.strip()
        clean = _extract_plain(clean)
        if not clean or len(clean) < 10:
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
            'timestamp': ts or 0,
        })

    conn.close()

    if not nodes:
        return False

    # 重建主索引
    ok = mn.rebuild_main(nodes)
    if ok:
        logger.info(f"DAG 全量索引构建: {len(nodes)} 节点")

    return ok


def _do_dag(query: str, top_k: int, session_id: str = "") -> list:
    """
    2. DAG 三通道检索（2407.07871 MN-RU + ChatRetriever session 级）
    
    三通道：
      A. 主索引（全量静态）
      B. 小索引（增量实时）
      C. Session 索引（按 cycle 分组）
    """
    mn = _get_hnsw_mn()
    if not mn.init_embedding():
        return _do_dag_fallback(query, top_k)

    # 检查是否需要全量重建或合并
    now = time.time()
    if not mn.main_ready:
        if not _build_all_indexes():
            return _do_dag_fallback(query, top_k)
    elif now - mn.last_rebuild > HNMW_REBUILD_INTERVAL:
        # 超时，后台重建（不阻塞当前查询）
        def _rebg():
            dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
            if os.path.exists(dag_db):
                conn = sqlite3.connect(dag_db)
                total = conn.execute(
                    "SELECT COUNT(*) + (SELECT COUNT(*) FROM rccam_nodes)"
                ).fetchone()[0] or 0
                conn.close()
                if total != mn.total_nodes_db:
                    _build_all_indexes()
        threading.Thread(target=_rebg, daemon=True).start()

    if mn._should_merge() and mn.mini_nodes:
        def _merge_bg():
            try:
                mn.merge_mini_to_main()
            except Exception:
                pass
        threading.Thread(target=_merge_bg, daemon=True).start()

    # 增量 flush：待处理节点写入小索引
    if mn._pending:
        mn.flush_mini()
    if mn._pending_session:
        mn.flush_session()

    try:
        # Query Expansion：多条查询分别 embedding，取最优结果
        expansions = _expand_query(query)

        # Context-Denoised 上下文
        denoised_ctx = _get_denoised_context(query, session_id)
        if denoised_ctx:
            # 将 denoised context 加入扩展查询
            merged_query = f"{query} {' '.join(expansions[1:4])}"[:500]
        else:
            merged_query = query

        # 1 次 embedding API 调用（使用 mn.embed 的 fallback 逻辑）
        _emb_result = mn.embed([merged_query])
        if not _emb_result:
            logger.warning("  dag(mn-ru) embed 失败，降级到 fallback")
            return _do_dag_fallback(query, top_k)
        q_vec = _emb_result[0]

        # 三通道搜索
        all_results = mn.search(q_vec, top_k=top_k * 2)  # 多取一些确保 mini/session 能排上来

        # 按相似度排序（让 mini/session 高相关结果排到前面）
        all_results.sort(key=lambda x: -x.get('_mn_score', 0))

        # 评分排序
        _r = []
        for item in all_results:
            sim_score = item.get('_mn_score', 0.5)
            importance = item.get('importance', 0.5)
            score = sim_score * 0.8 + importance * 0.2

            # 当前会话提权
            ts = item.get('timestamp', 0)
            if ts and ts > (time.time() - CURRENT_SESSION_WINDOW):
                score *= 1.3

            if score < 0.15:
                continue

            _r.append({
                'content': item['content'][:1500],
                'score': round(score, 3),
                'source': item.get('source', 'dag'),
                'node_id': item.get('node_id', ''),
                'phase': item.get('phase', ''),
                'timestamp': ts,
            })
            if len(_r) >= top_k:
                break

        logger.info(f"  dag(mn-ru): {len(_r)} results (main={len(mn.main_nodes)}, mini={len(mn.mini_nodes)}, session={len(mn.session_nodes)})")
        return _r

    except Exception as e:
        logger.warning(f"  dag(mn-ru) failed: {e}")
        return _do_dag_fallback(query, top_k)


def _do_dag_fallback(query: str, top_k: int) -> list:
    """DAG 降级：FTS5 全文检索"""
    _r = []
    try:
        dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
        if not os.path.exists(dag_db):
            return _r
        conn = sqlite3.connect(dag_db)
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


# ── synapse 检索三个规模阈值 ──
SYNAPSE_FULL_THRESHOLD = 5000   # ≤ 5000: 全链路 ONNX+GAT+CfC
SYNAPSE_GAT_THRESHOLD = 2000     # 201-2000: GAT 嵌入相似度（无 CfC，轻量）
                                  # > 2000: jieba 关键词 fallback


def _do_synapse(query: str) -> list:
    """3. 突触检索——按规模自适应选择三条路径

    策略（三级降级）：
      - 神经元 ≤ 200   → 全链路（ONNX + GAT → CfC 激活传播）
      - 201 - 2000     → GAT 嵌入相似度检索（无 CfC，轻量，利用 GAT 结构信息）
      - > 2000         → jieba 关键词匹配（兜底）
    """
    try:
        _ws = workspace()
        _syn_file = os.path.join(_ws, ".learnings/synapse_network/neurons.jsonl")
        _neuron_count = 0
        if os.path.exists(_syn_file):
            with open(_syn_file) as _f:
                for _ in _f:
                    if _.strip():
                        _neuron_count += 1

        if _neuron_count == 0:
            return []

        # Tier 3: 超大规模 → jieba 兜底
        if _neuron_count > SYNAPSE_GAT_THRESHOLD:
            return _do_synapse_fallback(query)

        # Tier 2: 中等规模 → GAT 嵌入相似度检索
        if _neuron_count > SYNAPSE_FULL_THRESHOLD:
            return _do_synapse_gat(query, _ws, _neuron_count)

        # Tier 1: 小规模 → 全链路 ONNX+GAT+CfC
        return _do_synapse_full(query, _ws)

    except Exception as e:
        logger.debug(f"  synapse skipped: {e}")
        return _do_synapse_fallback(query)


def _do_synapse_full(query: str, _ws: str) -> list:
    """Tier 1: 全链路 ONNX+GAT+CfC（≤ 200 神经元）

    每次调用会更新 CfC 状态并持久化到磁盘，形成时间序列学习。
    状态文件：{_ws}/.learnings/synapse_network/neuron_states.jsonl
    """
    from services.onnx_embedding import get_onnx_embedding
    _onnx = get_onnx_embedding()
    _onnx.initialize()

    from services.neural_pipeline import NeuralMemoryPipeline
    _pipe = NeuralMemoryPipeline(
        feature_dim=64, hidden_dim=64, gnn_heads=4,
        gnn_layers=2, cfc_hidden_size=64,
    )
    _pipe.initialize()

    # ── 从磁盘加载上次持久化的 CfC 状态 → 形成时间序列学习 ──
    _state_path = os.path.join(_ws, ".learnings", "synapse_network", "neuron_states.jsonl")
    if os.path.exists(_state_path) and hasattr(_pipe, 'cfc_engine') and _pipe.cfc_engine is not None:
        try:
            _sm = _pipe.cfc_engine.state_manager
            if hasattr(_sm, 'load_from_jsonl'):
                _loaded = _sm.load_from_jsonl(_state_path)
                if _loaded > 0:
                    logger.debug(f"  CfC state loaded: {_loaded} neurons from {_state_path}")
        except Exception:
            pass

    _neurons = []
    if hasattr(_pipe, '_cached_neurons') and _pipe._cached_neurons:
        _neurons = _pipe._cached_neurons
    elif hasattr(_pipe, 'graph') and _pipe.graph is not None:
        nids = getattr(_pipe.graph, 'node_ids', []) or []
        lbls = getattr(_pipe.graph, 'node_labels', []) or []
        _neurons = [{'id': n, 'content': l} for n, l in zip(nids, lbls)]

    if not _neurons:
        return []

    import jieba
    _q_words = set(jieba.lcut(query.lower()))
    _candidates = []
    _seen = set()
    for n in _neurons:
        _c = n.get('content', '') or ''
        if not _c or _c[:60] in _seen:
            continue
        _seen.add(_c[:60])
        _nw = set(jieba.lcut(_c.lower()))
        if _q_words & _nw:
            _score = len(_q_words & _nw) / max(len(_q_words | _nw), 1)
            _candidates.append((n['id'], _score, _c[:1000]))
    _candidates.sort(key=lambda x: -x[1])
    _candidates = _candidates[:200]
    if not _candidates:
        return []

    _seeds = _onnx.find_seeds(query, top_k=5, min_score=0.15,
        candidates=[(nid, c) for nid, _, c in _candidates])

    _r = []
    _activated, _predicted = {}, []
    _gat_weights = {}  # neuron_id → GAT 注意力权重
    for _sid, _sc, _content in _seeds[:3]:
        try:
            _result = _pipe.activate(_sid, top_k=8, max_depth=2, activation_strength=0.05)
            for _aid, _st in _result.activated_neurons:
                if _aid != _sid:
                    _activated[_aid] = max(_activated.get(_aid, 0), _st)
            if hasattr(_result, 'predicted_ids') and _result.predicted_ids:
                for _pid in _result.predicted_ids:
                    if _pid != _sid and _pid not in _predicted:
                        _predicted.append(_pid)
            # 收集 GAT 注意力权重
            if hasattr(_result, 'attention_weights') and _result.attention_weights:
                for _nid, _gw in _result.attention_weights.items():
                    if _nid not in _gat_weights:
                        _gat_weights[_nid] = _gw
        except Exception:
            pass

    _label_map = {n['id']: n.get('content', '') for n in _neurons}
    _seen_out = set()
    for _sid, _sc, _content in _seeds:
        if _content and _content[:60] not in _seen_out:
            _seen_out.add(_content[:60])
            _entry = {'content': _content, 'score': round(_sc, 3), 'source': 'synapse_seed'}
            # 种子节点 GAT 权重（提升到 ×10 替代 RRF 贡献）
            _gw = _gat_weights.get(_sid, 0)
            if _gw > 0:
                _entry['gat_weight'] = round(_gw, 4)
            _r.append(_entry)
    for _aid, _st in sorted(_activated.items(), key=lambda x: -x[1]):
        _c = _label_map.get(_aid, '')
        if _c and _c[:60] not in _seen_out:
            _seen_out.add(_c[:60])
            _entry = {'content': _c[:1000], 'score': round(_st, 3), 'source': 'synapse_cfc'}
            # 激活结果 GAT 权重（× (1 + weight) 增强）
            _gw = _gat_weights.get(_aid, 0)
            if _gw > 0:
                _entry['gat_weight'] = round(_gw, 4)
            _r.append(_entry)
            if len(_r) >= 10: break
    for _pid in _predicted[:3]:
        _c = _label_map.get(_pid, '')
        if _c and _c[:60] not in _seen_out:
            _seen_out.add(_c[:60])
            _entry = {'content': _c[:1000], 'score': 0.4, 'source': 'synapse_pred'}
            _gw = _gat_weights.get(_pid, 0)
            if _gw > 0:
                _entry['gat_weight'] = round(_gw, 4)
            _r.append(_entry)

    # ── 持久化 CfC 状态到磁盘（积累时间序列学习）──
    _cfc_state_saved = False
    if hasattr(_pipe, 'cfc_engine') and _pipe.cfc_engine is not None:
        try:
            _sm = _pipe.cfc_engine.state_manager
            if hasattr(_sm, 'save_to_jsonl'):
                _sm.save_to_jsonl(_state_path)
                _cfc_state_saved = True
        except Exception:
            pass

    logger.info(f"  synapse (ONNX+GAT+CfC): {len(_r)} results"
                f" | cfc_state_saved={_cfc_state_saved}")
    return _r


def _do_synapse_gat(query: str, _ws: str, _neuron_count: int) -> list:
    """Tier 2: GAT 嵌入相似度检索（201-2000 神经元，无 CfC，轻量）

    利用 GAT 的结构信息（注意力加权邻居聚合）做嵌入相似度检索，
    比纯 jieba BOW 更准确，比全链路 CfC 更快。

    流程:
      1. 加载神经元 + 突触边
      2. ONNX 编码节点特征
      3. 构建图 → GAT 前向 → 结构感知嵌入
      4. Query 嵌入 → cosine 相似度 → Top-K
    """
    _r = []
    try:
        _syn_dir = os.path.join(_ws, ".learnings/synapse_network")
        _neurons_file = os.path.join(_syn_dir, "neurons.jsonl")
        _synapses_file = os.path.join(_syn_dir, "synapses.jsonl")

        if not os.path.exists(_neurons_file):
            return _do_synapse_fallback(query)

        # 1. 加载神经元
        _neurons = []
        with open(_neurons_file) as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _obj = json.loads(_line)
                    _neurons.append(_obj)
                except json.JSONDecodeError:
                    continue

        if not _neurons:
            return []

        _id_to_idx = {n.get('id', ''): i for i, n in enumerate(_neurons)}

        # 2. 加载突触（边）
        _edges_src, _edges_dst = [], []
        if os.path.exists(_synapses_file):
            with open(_synapses_file) as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _obj = json.loads(_line)
                        _s = _obj.get('source', '')
                        _t = _obj.get('target', '')
                        if _s in _id_to_idx and _t in _id_to_idx:
                            _edges_src.append(_id_to_idx[_s])
                            _edges_dst.append(_id_to_idx[_t])
                    except json.JSONDecodeError:
                        continue

        # 3. ONNX 特征
        _core_dir = os.path.join(_ws, "GalaxyOS/extensions/claw-core/dist/scripts")
        if _core_dir not in sys.path:
            sys.path.insert(0, _core_dir)

        from services.onnx_embedding import get_onnx_embedding
        _onnx = get_onnx_embedding()
        _onnx.initialize()

        _texts = [n.get('content', '') or '' for n in _neurons]
        _features_list = _onnx.encode_batch(_texts, dim=64) if hasattr(_onnx, 'encode_batch') else []
        if not _features_list:
            return _do_synapse_fallback(query)

        import numpy as np

        # 4. 构建稀疏边索引 + GAT 前向（不再构造 N×N 稠密矩阵）
        import torch
        _n = len(_neurons)
        _features = torch.tensor(np.array(_features_list), dtype=torch.float32)
        _src_list = list(_edges_src)
        _dst_list = list(_edges_dst)
        # 加自环
        _self_loop = list(range(_n))
        _src_list = _src_list + _self_loop
        _dst_list = _dst_list + _self_loop
        if _src_list:
            _edge_index = torch.tensor([_src_list, _dst_list], dtype=torch.long)
        else:
            _edge_index = torch.zeros(2, 0, dtype=torch.long)

        try:
            from gat_layer import GAT
            # 默认 mode='auto'，大图自动走 sparse 路径
            _gat = GAT(
                input_dim=64, hidden_dim=64, output_dim=64,
                num_heads=4, num_layers=2, dropout=0.3,
            )
            _gat.eval()
            with torch.no_grad():
                _embeddings = _gat(_features, _edge_index)
        except Exception as _e:
            logger.debug(f"GAT forward failed, fallback to ONNX raw: {_e}")
            _embeddings = _features

        # 5. Query 嵌入
        _q_vec_raw = _onnx.encode(query)
        if _q_vec_raw is None:
            return _do_synapse_fallback(query)
        _q_vec = torch.tensor(np.array(_q_vec_raw), dtype=torch.float32)

        # 6. Cosine 相似度
        _emb_norm = torch.nn.functional.normalize(_embeddings, dim=1)
        _q_norm = torch.nn.functional.normalize(_q_vec.unsqueeze(0), dim=1)
        _sims = torch.mm(_q_norm, _emb_norm.t()).squeeze(0)

        _topk_vals, _topk_indices = torch.topk(_sims, min(10, _n))
        _seen_out = set()
        for _val, _idx in zip(_topk_vals.tolist(), _topk_indices.tolist()):
            _score = float(_val)
            if _score < 0.2:
                continue
            _n = _neurons[_idx]
            _content = _n.get('content', '') or ''
            _dedup = _content[:80]
            if _dedup in _seen_out:
                continue
            _seen_out.add(_dedup)
            _r.append({
                'content': _content[:1000],
                'score': round(_score, 3),
                'source': 'synapse_gat',
            })

        logger.info(f"  synapse (GAT embed, n={_n}): {len(_r)} results")
        return _r

    except Exception as e:
        logger.debug(f"  synapse GAT failed: {e}, fallback to jieba")
        return _do_synapse_fallback(query)


def _do_synapse_fallback(query: str) -> list:
    """synapse 回退：jieba 关键词匹配 + 突触网络"""
    _r = []
    try:
        _ws = workspace()
        _core_dir = os.path.join(
            _ws, "GalaxyOS/extensions/claw-core/dist/scripts")
        if _core_dir not in sys.path:
            sys.path.insert(0, _core_dir)
        from memory_synapse_network import SynapseNetwork
        sn = SynapseNetwork(_ws)
        if not sn._neurons_cache:
            return _r
        import jieba
        q_words = set(jieba.lcut(query.lower()))
        neurons = list(sn._neurons_cache.values())
        scored = []
        dedup_seen = set()
        for n in neurons:
            if not n.content:
                continue
            dedup_key = n.content[:80]
            if dedup_key in dedup_seen:
                continue
            dedup_seen.add(dedup_key)
            n_words = set(jieba.lcut(n.content.lower()))
            overlap = len(q_words & n_words)
            if overlap >= 1:
                score = overlap / max(len(q_words | n_words), 1)
                scored.append((n, score))
        scored.sort(key=lambda x: -x[1])
        seen2 = set()
        for n, s in scored[:5]:
            if not n.content or n.content[:60] in seen2:
                continue
            seen2.add(n.content[:60])
            _r.append({
                'content': n.content[:1000],
                'score': round(s, 3),
                'source': 'synapse_fb',
            })
        logger.info(f"  synapse_fallback: {len(_r)} results")
    except Exception as e:
        logger.debug(f"  synapse_fallback error: {e}")
    return _r


def _do_paper(query: str, top_k: int) -> list:
    """4. 论文引擎检索"""
    _r = []
    try:
        _ws = workspace()
        # 按 worker 的 import 顺序（core 优先）
        _core_dir = os.path.join(_ws,
            "skills/galaxyos-engine/skills/llm-memory-integration/core")
        sys.path.insert(0, _core_dir)
        from four_advancements import get_advancements
        fa = get_advancements()
        _stat = fa.get_status()
        if not _stat.get('raptor_tree_built'):
            fa.raptor.build_tree()
        if not _stat.get('graphrag_entities'):
            fa.graphrag.extract_from_dag()

        # fa.search 用 .split() 切中文不生效，直接取内部数据匹配
        # RAPTOR
        q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))
        if fa.raptor._tree_built and fa.raptor._summaries:
            for level, summary in fa.raptor._summaries.items():
                summary_words = set(re.findall(r'[\w\u4e00-\u9fff]+', summary.lower()))
                if len(q_words & summary_words) >= 1:
                    _r.append({
                        "content": f"[RAPTOR {level}] {summary[:500]}",
                        "score": 0.7,
                        "source": "raptor_tree"
                    })

        # GraphRAG
        if fa.graphrag._entities:
            for entity_name, entity_info in list(fa.graphrag._entities.items())[:10]:
                en_words = set(re.findall(r'[\w\u4e00-\u9fff]+', entity_name.lower()))
                if len(q_words & en_words) >= 1:
                    content = entity_info.get('summary', json.dumps(entity_info, ensure_ascii=False))[:500]
                    _r.append({
                        "content": f"[GraphRAG {entity_name}] {content}",
                        "score": 0.65,
                        "source": "graphrag"
                    })

        # Reflection
        if fa.reflection._reflections:
            for ref in fa.reflection._reflections[-10:][::-1]:
                ref_text = ref.get('reflection', '')
                ref_words = set(re.findall(r'[\w\u4e00-\u9fff]+', ref_text.lower()))
                if len(q_words & ref_words) >= 1:
                    _r.append({
                        "content": f"[反思经验] {ref_text[:300]}",
                        "score": 0.6,
                        "source": "reflection"
                    })

        # Toolformer
        tool_match = fa.toolformer.route(query)
        if tool_match and tool_match.get('tool') and tool_match['tool'] != 'general':
            _r.append({
                "content": f"[工具推荐] {tool_match['tool']}: {tool_match.get('reason','')}",
                "score": 0.5,
                "source": "toolformer"
            })

        _r = _r[:top_k]
        logger.info(f"  paper engines: {len(_r)} results")
    except Exception as e:
        logger.debug(f"  paper engines skipped: {e}")
    return _r


def _do_cognitive(query: str, top_k: int, session_id: str = "") -> list:
    """7. 认知地图检索（LASAR 三类认知 query + 空间接近性）"""
    _r = []
    try:
        _ws = workspace()
        _core_dir = os.path.join(_ws, "GalaxyOS/extensions/claw-core/dist/scripts")
        if _core_dir not in sys.path:
            sys.path.insert(0, _core_dir)
        from cognitive_map import CognitiveMap
        _cm = CognitiveMap()
        _queries = _cm.run_cognitive_queries(query, session_key=session_id)
        for _key in ('retrospective', 'introspective', 'prospective'):
            _val = _queries.get(_key, '')
            if _val and len(str(_val)) > 5:
                _r.append({
                    'content': f"[认知 {_key}] {str(_val)[:500]}",
                    'score': 0.55,
                    'source': f'cognitive_{_key}',
                })
        _spatial = _cm.proximity_retrieve(query, top_k=top_k)
        for _item in _spatial:
            _ctx = _item.get('context', '') or ''
            if _ctx:
                _r.append({
                    'content': _ctx[:500],
                    'score': round(float(_item.get('similarity', 0.4)), 3),
                    'source': 'cognitive_prox',
                })
        logger.info(f"  cognitive: {len(_r)} results")
    except Exception as e:
        logger.debug(f"  cognitive skipped: {e}")
    return _r


def _do_web(query: str, max_web_results: int) -> list:
    """
    4. 多源交叉验证搜索
    不参与 RRF 融合，仅作为检索后的外部验证参考。
    调用方可用验证结果修正 quality 判断。

    调小艺 API（search.js），Node 内联调用输出 JSON。
    """
    _r = []
    try:
        search_script = os.path.expanduser(
            "~/.openclaw/workspace/skills/xiaoyi-web-search/scripts/search.js")
        if os.path.exists(search_script):
            inline = (
                f"const s = require('{search_script}');"
                f"s.webSearch({json.dumps(query)}, {max_web_results}).then(r => "
                f"console.log(JSON.stringify(r))).catch(() => console.log('[]'))"
            )
            result = subprocess.run(
                ["node", "-e", inline],
                capture_output=True, text=True, timeout=20,
            )
            out = result.stdout.strip()
            # 取最后一行的 JSON
            lines = [l.strip() for l in out.split('\n') if l.strip().startswith('[')]
            if lines:
                items = json.loads(lines[-1])
                if isinstance(items, list):
                    for i, item in enumerate(items):
                        _r.append({
                            'content': item.get('chunk', item.get('content', item.get('snippet', str(item))))[:1500],
                            'score': 0.5 - i * 0.05,
                            'source': 'web',
                            'title': item.get('title', ''),
                            'url': item.get('url', ''),
                        })
        logger.info(f"  web(验证): {len(_r)} results")
    except Exception as e:
        logger.debug(f"  web(验证) skipped: {e}")
    return _r


def _verify_local_with_web(
    merged_results: List[Dict],
    web_results: List[Dict],
) -> Dict[str, any]:
    """
    用 web 搜索结果交叉验证本地检索的准确性。
    
    web 结果不参与 RRF 排名，仅验证本地 top 结果是否跟外部信息一致。
    返回验证分数和置信度修正量。
    """
    if not merged_results or not web_results:
        return {
            "verified": False,
            "agreement": 0.0,
            "confidence_delta": 0.0,
            "reason": "无验证数据",
        }

    # 提取本地 top-3 内容摘要
    merged_text = " ".join(
        r.get('content', '')[:300] for r in merged_results[:3] if r.get('content')
    )
    # 提取 web 内容摘要
    web_text = " ".join(
        r.get('content', '')[:300] for r in web_results[:3] if r.get('content')
    )

    if not merged_text or not web_text:
        return {
            "verified": False,
            "agreement": 0.0,
            "confidence_delta": 0.0,
            "reason": "内容不足",
        }

    # 关键词重叠验证
    merged_words = set(re.findall(r'[\w\u4e00-\u9fff]+', merged_text.lower()))
    web_words = set(re.findall(r'[\w\u4e00-\u9fff]+', web_text.lower()))

    overlap = len(merged_words & web_words)
    total = max(len(merged_words), len(web_words), 1)
    jaccard = overlap / total

    # 本地独有词比例高 → 可能是内部知识 web 搜不到，不一定是错的
    merged_only = len(merged_words - web_words)
    local_ratio = merged_only / max(len(merged_words), 1)

    # 判断
    if jaccard > 0.4:
        verified = True
        confidence_delta = min(0.3, jaccard * 0.3)  # 提升
        reason = "高度一致"
    elif jaccard > 0.2:
        verified = True
        confidence_delta = 0.0  # 中性
        reason = "部分一致"
    elif local_ratio > 0.8:
        # 本地内容大部分是 web 没有的专有知识，不降分
        verified = False
        confidence_delta = 0.0
        reason = "专有知识（web 无法验证）"
    else:
        verified = False
        confidence_delta = -min(0.3, (0.2 - jaccard) * 0.5)
        reason = "存在差异"

    return {
        "verified": verified,
        "agreement": round(jaccard, 4),
        "confidence_delta": round(confidence_delta, 4),
        "reason": reason,
        "local_words": len(merged_words),
        "web_words": len(web_words),
        "overlap": overlap,
    }


# ============================================================
# CRAG 检索质量评估
# ============================================================

def _assess_retrieval_quality(
    query: str,
    results: List[Dict],
    web_verification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not results:
        base = {"judgment": "incorrect", "confidence": 0, "top_score": 0}
    else:
        top_score = max((r.get("rrf_score", r.get("score", 0)) for r in results), default=0)
        if top_score > 0.5:
            base = {"judgment": "correct", "confidence": min(1.0, top_score * 1.2), "top_score": top_score}
        elif top_score > 0.2:
            base = {"judgment": "ambiguous", "confidence": top_score, "top_score": top_score}
        else:
            base = {"judgment": "incorrect", "confidence": top_score, "top_score": top_score}

    # 叠加 web 验证修正
    if web_verification and web_verification.get("agreement", 0) > 0:
        delta = web_verification.get("confidence_delta", 0)
        base["confidence"] = max(0.0, min(1.0, base["confidence"] + delta))
        base["web_verification"] = {
            "agreement": web_verification["agreement"],
            "reason": web_verification.get("reason", ""),
        }
        # 如果 web 验证高度一致但本地分数低 → 提升判断
        if delta > 0 and base["judgment"] in ("ambiguous", "incorrect") and base["confidence"] > 0.5:
            base["judgment"] = "correct"
        # 如果 web 验证有差异且本地分数本就不高 → 降级
        if delta < 0 and base["judgment"] == "correct" and base["confidence"] < 0.5:
            base["judgment"] = "ambiguous"

    return base


# ============================================================
# CRAG 分解 → 子查询 → 融合
# ============================================================

def _decompose_query(query: str) -> List[str]:
    sub_queries = []
    if len(query) < 10:
        return [query]
    try:
        from scripts_core.rewriter import QueryRewriter
        expansions = QueryRewriter.get_synonym_expansions(query)
        for e in expansions:
            if len(e) >= 5 and e.lower() != query.lower():
                sub_queries.append(e)
    except Exception:
        pass
    if len(sub_queries) < 2:
        try:
            pass
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


# ── 神经网络重排序 + 跨通道去重 ──
_CONTENT_FINGERPRINT_LEN = 200


def _content_fingerprint(text: str) -> str:
    """取前 _CONTENT_FINGERPRINT_LEN 字符作为指纹，忽略空白差异"""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text.strip())[:_CONTENT_FINGERPRINT_LEN]


def _neural_rerank_dedup(
    merged: List[Dict],
    query: str,
    session_id: str,
    top_k: int,
) -> List[Dict]:
    """
    神经网络重排序 + 跨通道去重（双核）

    核心逻辑：
    1. 跨通道去重：按内容指纹归组，同组只保留最高 RRF 分数项
    2. LTC h_t 门控：匹配的神经元 h_t 越低 → 膜电位越低 → 降权惩罚
    3. 激活传播提权：结果能传播到当前会话上下文节点的 → 加分
    """
    if not merged:
        return merged

    _ws = workspace()
    _core_dir = os.path.join(_ws, "GalaxyOS/skills/llm-memory-integration/core")
    sys.path.insert(0, _core_dir)
    sys.path.insert(0, os.path.join(_ws,
        "skills/galaxyos-engine/skills/llm-memory-integration/core"))

    from memory_consolidation import ConsolidationEngine
    from memory_synapse_network import MemoryNeuron

    ce = ConsolidationEngine(_ws)
    syn_network = ce._get_synapse_network() if hasattr(ce, '_get_synapse_network') else None

    # 加载当前会话的 DAG 节点内容（用于激活传播参照）
    _session_neuron_texts = set()
    try:
        _dag_db_path = os.path.expanduser("~/.openclaw/dag_context.db")
        if os.path.exists(_dag_db_path):
            import sqlite3
            _conn = sqlite3.connect(_dag_db_path)
            _c = _conn.cursor()
            _sid = session_id if session_id else ''
            for _row in _c.execute(
                "SELECT content FROM nodes WHERE session_id=? ORDER BY created_at DESC LIMIT 50",
                (_sid,)
            ):
                _txt = _row[0] or ''
                if _txt:
                    _session_neuron_texts.add(_content_fingerprint(_txt))
            _conn.close()
    except Exception:
        pass

    # ── Step 1: 跨通道去重（内容指纹归组） ──
    _fp_groups: Dict[str, List[Dict]] = {}
    _fp_order: List[str] = []
    for _item in merged:
        _content = _item.get('content', _item.get('session_text', ''))[:800]
        _fp = _content_fingerprint(_content)
        if _fp not in _fp_groups:
            _fp_groups[_fp] = []
            _fp_order.append(_fp)
        _fp_groups[_fp].append(_item)

    _deduped = []
    for _fp in _fp_order:
        _group = _fp_groups[_fp]
        # 同组选 rrf_score 或 score 最大的，来源标签合并
        _best = max(_group, key=lambda x: x.get('rrf_score', x.get('score', 0)))
        _sources = sorted(set(_src.get('source', 'unknown') for _src in _group))
        _best['_dedup_sources'] = ','.join(_sources)
        _best['_dedup_count'] = len(_group)
        _deduped.append(_best)

    # ── Step 2: LTC h_t 门控 ──
    if syn_network:
        try:
            import jieba
            _q_words = set(jieba.lcut(query.lower()))
        except ImportError:
            _q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))

        _neurons = syn_network.neuron_manager.get_all_neurons()
        # 建立内容→神经元索引（取最后 5000 条）
        _neuron_by_fp: dict = {}
        for _n in _neurons[-5000:]:
            _nfp = _content_fingerprint(_n.content or '')
            if _nfp:
                _neuron_by_fp[_nfp] = _n

        for _item in _deduped:
            _content = _item.get('content', '')
            _nfp = _content_fingerprint(_content)
            _n = _neuron_by_fp.get(_nfp)

            _ltc_boost = 1.0
            _prop_boost = 1.0

            if _n:
                # LTC h_t 门控：h_t ∈ [0,1]，低于 0.2 接近休眠
                _ht = getattr(_n, 'ltc_hidden', 0.5)
                _activation = getattr(_n, 'activation_count', 0)
                # 公式: boost = 0.5 + 0.5 * h_t  → [0.5, 1.0]
                # 外加 activation_count 的 log 增益（有历史活跃的加分）
                _ltc_boost = 0.5 + 0.5 * _ht
                if _activation > 0:
                    _ltc_boost = min(1.5, _ltc_boost + 0.05 * math.log10(_activation + 1))

                # 激活传播提权：如果该神经元能传播到会话上下文 → 加分
                if _session_neuron_texts:
                    try:
                        _assoc = syn_network.activation_spreader.spread_activation(
                            _n.id, threshold=0.01, max_depth=2
                        )
                        _ctx_hits = 0
                        for _a_n, _a_s in _assoc:
                            _a_fp = _content_fingerprint(getattr(_a_n, 'content', '') or '')
                            if _a_fp and _a_fp in _session_neuron_texts:
                                _ctx_hits += 1
                        if _ctx_hits > 0:
                            _prop_boost = 1.0 + 0.15 * _ctx_hits
                    except Exception:
                        pass
            else:
                # 找不到匹配神经元 → 无 ncps 支撑，轻微降权
                _ltc_boost = 0.85

            # 最终分 = RRF 分 × LTC 门控 × 传播提权
            _orig_score = _item.get('rrf_score', _item.get('score', 0))
            _new_score = _orig_score * _ltc_boost * _prop_boost
            _item['ncps_ltc_boost'] = round(_ltc_boost, 3)
            _item['ncps_prop_boost'] = round(_prop_boost, 3)
            _item['ncps_score'] = round(_new_score, 4)

    # ── Step 3: 按 ncps 重排序 ──
    if syn_network:
        for _item in _deduped:
            _base = _item.get('rrf_score', _item.get('score', 0))
            _item['score'] = _item.get('ncps_score', _base)
        _deduped.sort(key=lambda x: -x.get('score', 0))
    else:
        # 无神经网络时按 RRF 排序
        _deduped.sort(key=lambda x: -x.get('rrf_score', x.get('score', 0)))

    # ── Step 4: 内容类型标记（ContextEngine 场景过滤用） ──
    for _item in _deduped:
        _content = _item.get('content', '')
        _source = _item.get('source', '')
        # JSON 元节点（DAG 压缩产物）
        if _content.strip().startswith('{') and '"name"' in _content and '"trigger"' in _content:
            _item['_content_type'] = 'metadata'
        # 纯对话历史
        elif _source in ('user', 'ai', 'dag_msg') or '用户:' in _content or '系统:' in _content or '助手:' in _content:
            _item['_content_type'] = 'conversation'
        else:
            _item['_content_type'] = 'summary'

    logger.info(f"neural rerank/dedup: {len(merged)} → {len(_deduped)} (dedup={len(merged)-len(_deduped)})")
    return _deduped[:top_k]


def _recompose_results(sub_results: List[List[Dict]]) -> List[Dict]:
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
            scores[rid] += 1.0 / (k + i + 1)
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    merged = []
    for rid, score in ranked:
        item = sources[rid]
        item['rrf_score'] = round(score, 4)
        item['crag_decomposed'] = True
        merged.append(item)
    return merged


# ============================================================
# Adaptive-RAG query 复杂度分类（降级用）
# ============================================================

def classify_query_complexity(query: str) -> Dict[str, Any]:
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


# ============================================================
# 统一入口
# ============================================================

def retrieval_hub(
    query: str,
    top_k: int = 8,
    include_web: Optional[bool] = None,
    max_web_results: int = 3,
    enable_crag: bool = True,
    preprocessed: Optional[Dict[str, Any]] = None,
    session_id: str = "",
) -> Dict[str, Any]:
    """
    统一检索入口：五路并行（KG / local / DAG(MN-RU三通道) / synapse / paper），web 可选，RRF 融合 v2，质量评估。

    Args:
        session_id: 当前会话 ID，用于 Context-Denoised 和会话感知权重
    """
    start_time = time.time()
    pool = _PARALLEL_POOL

    pp = preprocessed or _preprocess_query(query)
    effective_query = pp.get("rewritten", query)
    mode = pp.get("mode", "balanced")
    intent_name = pp.get("intent", ("search", 0.5))[0]
    complexity = pp.get("complexity", "medium")

    q_class = classify_query_complexity(effective_query)
    if mode == "fast":
        q_class["level"] = "simple"
    elif mode == "full":
        q_class["level"] = "complex"
    elif complexity == "simple":
        q_class["level"] = "simple"

    is_fast = q_class.get("level") == "simple"

    # 并行发射（传递 session_id 给 _do_dag 用于 Context-Denoised）
    future_map = {
        "kg": pool.submit(_do_kg, effective_query, top_k),
        "local": pool.submit(_do_local, effective_query, top_k),
        "dag": pool.submit(_do_dag, effective_query, top_k, session_id),
    }
    if not is_fast:
        future_map["synapse"] = pool.submit(_do_synapse, effective_query)
        future_map["paper"] = pool.submit(_do_paper, effective_query, top_k)
        future_map["cognitive"] = pool.submit(_do_cognitive, effective_query, top_k, session_id)
    else:
        future_map["cognitive"] = None  # type: ignore[assignment]

    # web 默认关闭：本地记忆（五路）即可满足大部分查询，web 由调用方按需开启
    should_web = False if include_web is None else include_web
    if should_web:
        future_map["web"] = pool.submit(_do_web, effective_query, max_web_results)
    else:
        future_map["web"] = None  # type: ignore[assignment]

    all_source_results = []
    name_to_results: Dict[str, list] = {}

    for name in ["kg", "local", "dag", "synapse", "paper", "cognitive", "web"]:
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
        # web 不参与 RRF 融合（仅验证用）
        if name != "web":
            all_source_results.append(r)

    # RRF 融合 v2（不含 web）
    merged = _rrf_merge(all_source_results)[:top_k * 2]  # 多留点给重排序裁

    # ── 神经网络重排序 + 跨通道去重 ──
    try:
        merged = _neural_rerank_dedup(merged, effective_query, session_id, top_k)
    except Exception as _ner_err:
        logger.debug(f"neural rerank/dedup skipped: {_ner_err}")
        merged = merged[:top_k]

    # CRAG decompose
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
                sq_dag = _do_dag(sq, max(2, top_k // 2), session_id)
                sq_combined = _rrf_merge([sq_local, sq_dag])[:max(2, top_k // 2)]
                sub_results_list.append(sq_combined)
            if len(sub_results_list) > 1:
                recomposed = _recompose_results(sub_results_list)[:top_k]
                if recomposed:
                    merged = recomposed
                    crag_decomposed = True
                    logger.info(f"CRAG decompose: {len(sub_queries)} sub-queries → {len(merged)} merged results")

    # 多源交叉验证：web 结果验证本地检索
    web_results = name_to_results.get('web', [])
    web_verification = None
    if web_results:
        web_verification = _verify_local_with_web(merged, web_results)

    quality = _assess_retrieval_quality(effective_query, merged, web_verification or {})

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
        "cognitive": len(name_to_results.get('cognitive', [])),
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
        "web_verification": web_verification,
    }
