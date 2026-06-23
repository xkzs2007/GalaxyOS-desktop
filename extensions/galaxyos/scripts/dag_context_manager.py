#!/usr/bin/env python3
"""
DAG (有向无环图) 上下文管理器

基于 LCM 论文思想的上下文管理方案：
1. 每条消息作为独立节点，保留依赖关系（DAG）
2. 当上下文超 threshold 时，增量压缩旧消息为摘要节点
3. 原始消息永久保留在 SQLite，支持回溯还原
4. 投机解码生成摘要，降成本提速度

论文参考:
- Lossless Context Management (LCM) - Voltropy
- REST: Retrieval-Based Speculative Decoding (1.62-2.36x)
- LLMLingua: Compressing Prompts for Accelerated Inference

Layer: L9 (会话管理层)

Adapted for claw-core: removed numpy dependency, standalone SQLite-based DAG.
"""

import os
import json
import time
import logging
import sqlite3
import hashlib
import threading
import re
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict, Counter
from pathlib import Path
from blob_arena import BlobArena, get_blob_arena, generate_memo

logger = logging.getLogger(__name__)

# ============================================================================
# 数据库路径
# ============================================================================
DAG_DB_DIR = Path(os.path.expanduser("~/.openclaw"))
DAG_DB_PATH = DAG_DB_DIR / "dag_context.db"


# ============================================================================
# 节点类型与数据模型
# ============================================================================

class DAGNodeType:
    """节点类型"""
    MESSAGE = "message"          # 原始消息
    SUMMARY = "summary"          # 摘要节点
    PERSONA = "persona"          # 人格注入（永不压缩）
    SNAPSHOT = "snapshot"        # 会话快照
    SYSTEM = "system"            # 系统消息


class PhaseNodeType:
    """R-CCAM 阶段节点类型"""
    USER_INPUT = "rccam_user_input"
    RETRIEVAL = "rccam_retrieval"
    COGNITION = "rccam_cognition"
    CONTROL = "rccam_control"
    ACTION = "rccam_action"
    MEMORY = "rccam_memory"
    CYCLE_SUMMARY = "rccam_cycle_summary"


class PriorityLevel:
    """优先级"""
    CRITICAL = 0     # 永不压缩、永不裁减（人格）


# ============================================================================
# Cognition Forest 子树常量
# ============================================================================

class CognitionForestType:
    """Cognition Forest 子树类型"""
    USER = "user"     # 用户画像：人格文件、偏好、记忆快照
    SELF = "self"     # 系统能力：可用技能、模块状态、配置
    ENV  = "env"      # 运行环境：时间、位置、设备、网络
    META = "meta"     # 元认知：自进化建议、反思记录、KoRa 模式

    ALL_TYPES = [USER, SELF, ENV, META]

_COG_SUBTREE_SESSION_PREFIX = "_cog_subtree_"


class PriorityLevel:
    """优先级"""
    CRITICAL = 0     # 永不压缩、永不裁减（人格）
    HIGH = 1         # 最后被裁（重要决策、会话快照）
    NORMAL = 2       # 普通消息，可被摘要
    LOW = 3          # 优先被摘要（系统日志等）


@dataclass
class DAGNode:
    """DAG 节点"""
    node_id: str                     # 唯一 ID
    node_type: str                   # 节点类型
    session_key: str                 # 所属会话
    content: str                     # 节点内容
    tokens: int = 0                  # token 数
    priority: int = PriorityLevel.NORMAL
    parent_ids: List[str] = field(default_factory=list)     # 父节点 ID
    children_ids: List[str] = field(default_factory=list)   # 子节点 ID
    is_summary: bool = False         # 是否为摘要节点
    summary_of_ids: List[str] = field(default_factory=list) # 摘要覆盖的节点 ID
    importance_score: float = 0.5    # 重要性 [0, 1]
    emotion_score: float = 0.0       # 情感分数 [-1, 1]
    keywords: List[str] = field(default_factory=list)       # 关键词
    entities: List[str] = field(default_factory=list)       # 实体
    timestamp: float = 0.0           # 时间戳
    metadata: Dict[str, Any] = field(default_factory=dict)  # 额外元数据
    blob_id: str = ""                # v2: BlobArena 引用（完整原文）
    depth: int = 0                   # v3: 摘要层级（0=原始，1=一次摘要，2=二次摘要...）
    is_evicted: bool = False         # v3: 已被更高级摘要替代，不参与 assemble

    def to_dict(self) -> dict:
        d = asdict(self)
        # 保持向后兼容
        if 'blob_id' not in d:
            d['blob_id'] = ''
        if 'depth' not in d:
            d['depth'] = 0
        if 'is_evicted' not in d:
            d['is_evicted'] = False
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "DAGNode":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================================
# DAG 上下文管理器核心
# ============================================================================

class DAGContextManager:
    """
    DAG 上下文管理器

    功能:
    1. 消息节点存储（SQLite）
    2. 增量摘要触发与生成
    3. 上下文组装（原始消息 + 摘要）
    4. 回溯检索（摘要 → 原始消息）
    5. 人格节点保护（永不压缩）
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        max_context_tokens: int = 240000,
        fresh_tail_count: int = 20,
        leaf_chunk_tokens: int = 8000,
        summary_target_tokens: int = 500,
        context_threshold: float = 0.75,
        use_blob_arena: bool = True,
    ):
        self.db_path = db_path or str(DAG_DB_PATH)
        self.max_context_tokens = max_context_tokens
        self.fresh_tail_count = fresh_tail_count
        self.leaf_chunk_tokens = leaf_chunk_tokens
        self.summary_target_tokens = summary_target_tokens
        self.context_threshold = context_threshold
        self.use_blob_arena = use_blob_arena

        # BlobArena 实例（v2: 无损存储）
        self._blob_arena = get_blob_arena() if self.use_blob_arena else None

        # SQLite 锁（线程安全）
        self._lock = threading.Lock()

        # 确保数据库目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # 初始化数据库
        self._init_db()

        # SpatialTopologyGraph 懒加载（AriGraph 空间拓扑）
        self._sg = None

        # ── v2: Meta 参数自适应（参考 ALMA） ──
        self._meta = {
            'leaf_chunk_tokens': leaf_chunk_tokens,
            'context_threshold': context_threshold,
            'summary_target_tokens': summary_target_tokens,
            'fresh_tail_count': fresh_tail_count,
            # 自适应跟踪统计
            'compact_history': [],      # 最近 10 次压缩的统计
            'context_full_count': 0,    # context 填满次数
            'compact_skip_count': 0,    # 触发压缩但实际没必要的次数
            'adjust_count': 0,          # 参数调整次数
            'last_compact_tokens': 0,
            'last_context_ratio': 0.0,
        }

        logger.info(f"DAG Context Manager v2 初始化: db={self.db_path}, "
                    f"max_tokens={max_context_tokens}, threshold={context_threshold}, "
                    f"blob_arena={self.use_blob_arena}")

    def _init_db(self):
        """初始化 SQLite 数据库"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS dag_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tokens INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 2,
                    parent_ids TEXT DEFAULT '[]',
                    children_ids TEXT DEFAULT '[]',
                    is_summary INTEGER DEFAULT 0,
                    summary_of_ids TEXT DEFAULT '[]',
                    importance_score REAL DEFAULT 0.5,
                    emotion_score REAL DEFAULT 0.0,
                    keywords TEXT DEFAULT '[]',
                    entities TEXT DEFAULT '[]',
                    timestamp REAL DEFAULT 0.0,
                    metadata TEXT DEFAULT '{}'
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS rccam_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    session_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tokens INTEGER DEFAULT 0,
                    parent_ids TEXT DEFAULT '[]',
                    cycle_id TEXT DEFAULT '',
                    previous_cycle_id TEXT DEFAULT '',
                    phase_name TEXT DEFAULT '',
                    cycle_index INTEGER DEFAULT 0,
                    priority INTEGER DEFAULT 2,
                    is_summary INTEGER DEFAULT 0,
                    is_compressed INTEGER DEFAULT 0,
                    importance_score REAL DEFAULT 0.5,
                    confidence REAL DEFAULT 0.5,
                    validation TEXT DEFAULT 'unknown',
                    keywords TEXT DEFAULT '[]',
                    strategy TEXT DEFAULT '',
                    timestamp REAL DEFAULT 0.0,
                    metadata TEXT DEFAULT '{}'
                )
            """)

            conn.execute("CREATE INDEX IF NOT EXISTS idx_session_key ON dag_nodes(session_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_priority ON dag_nodes(priority)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_node_type ON dag_nodes(node_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON dag_nodes(timestamp)")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_cycle ON rccam_nodes(session_key, cycle_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_phase ON rccam_nodes(session_key, phase_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cycle_index ON rccam_nodes(session_key, cycle_index)")

            try:
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS dag_fts
                    USING fts5(content, keywords, entities, content=dag_nodes, content_rowid=rowid)
                """)
            except Exception:
                logger.warning("FTS5 不可用，跳过全文搜索索引")

            # v2: blob_id 列（无损存储兼容迁移）
            for tbl in ('dag_nodes', 'rccam_nodes'):
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN blob_id TEXT DEFAULT ''")
                except Exception:
                    pass

            # v3: depth + is_evicted 列（层级摘要）
            for tbl in ('dag_nodes',):
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN depth INTEGER DEFAULT 0")
                except Exception:
                    pass
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN is_evicted INTEGER DEFAULT 0")
                except Exception:
                    pass

            conn.commit()
            conn.close()

    def add_node(self, node: DAGNode) -> bool:
        """添加一个节点到 DAG"""
        data = node.to_dict()

        for field_name in ['parent_ids', 'children_ids', 'summary_of_ids', 'keywords', 'entities']:
            if isinstance(data.get(field_name), list):
                data[field_name] = json.dumps(data[field_name])
        if isinstance(data.get('metadata'), dict):
            data['metadata'] = json.dumps(data['metadata'])

        _full_content = data.get('content', '')

        # v2: 如果有原文且 blob_arena 可用，备份原文到 BlobArena
        if self._blob_arena and data.get('content') and len(data['content']) > 200:
            try:
                # memo 替换全文，原文存 blob
                _orig = data['content']
                _blob_id = self._blob_arena.append_text(_orig)
                _memo = generate_memo(_orig) if generate_memo else _orig[:200]
                data['content'] = f"[memo] {_memo}"
                data['blob_id'] = _blob_id
            except Exception:
                pass  # blob 失败不阻塞

        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                _blob_id_val = data.get('blob_id', '')
                conn.execute("""
                    INSERT OR REPLACE INTO dag_nodes
                    (node_id, node_type, session_key, content, blob_id, tokens, priority,
                     parent_ids, children_ids, is_summary, summary_of_ids,
                     importance_score, emotion_score, keywords, entities,
                     timestamp, metadata, depth, is_evicted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data['node_id'], data['node_type'], data['session_key'],
                    data['content'], _blob_id_val, data['tokens'], data['priority'],
                    data['parent_ids'], data['children_ids'],
                    1 if data['is_summary'] else 0,
                    data['summary_of_ids'],
                    data['importance_score'], data['emotion_score'],
                    data['keywords'], data['entities'],
                    data['timestamp'] or time.time(),
                    data['metadata'],
                    data.get('depth', 0),
                    1 if data.get('is_evicted', False) else 0
                ))
                conn.commit()
                conn.close()
                # MemGAS: 为长内容创建 KnowledgeAsset（非阻塞）
                if _full_content and len(_full_content) > 200:
                    try:
                        self._create_asset_for_node(node, _full_content)
                    except Exception:
                        pass
                return True
            except Exception as e:
                logger.error(f"添加节点失败: {e}")
                return False
    
    # ── MemGAS: add_node 后，对长内容创建 KnowledgeAsset ──
    def _create_asset_for_node(self, node: DAGNode, content: str):
        """
        对长内容（>200 字符）创建 KnowledgeAsset，提取多粒度表示并建立关联边。
        """
        if not content or len(content) < 200:
            return
        try:
            from knowledge_asset import get_asset_registry, create_memory_asset, AssociationEdge
            from multi_granularity import MultiGranularityExtractor, GMMAssociator
            
            _reg = get_asset_registry()
            _ext = MultiGranularityExtractor()
            _assoc = GMMAssociator(n_components=5)
            
            # 检查是否已创建过
            _existing = _reg.search(content[:100], top_k=1, type_filter=None)
            for _ex in _existing:
                if _ex.raw_content[:100] == content[:100]:
                    return  # 已注册
            
            # 创建资产
            _asset = create_memory_asset(
                memory_id=node.node_id,
                raw_content=content[:2000],
                tags=node.keywords if hasattr(node, 'keywords') and node.keywords else [],
                category=node.node_type if hasattr(node, 'node_type') else 'dag',
                source=f"dag_{node.node_type if hasattr(node, 'node_type') else 'unknown'}",
            )
            
            # 多粒度
            _asset.multi_granularity = _ext.extract(content)
            
            # GMM 关联（与已有资产）
            _existing_texts = [a.raw_content[:500] for a in _reg.list_ids()[:100] if _reg.get(a)]
            if _existing_texts:
                _assoc.fit(_existing_texts + [content[:500]])
                _edges = _assoc.associate(content[:500])
                for _target_id, _relation, _weight in _edges:
                    _asset.association_graph.append(
                        AssociationEdge(
                            target_asset_id=_target_id,
                            relation=_relation,
                            weight=_weight,
                        )
                    )
                    # 双向边
                    _target_asset = _reg.get(_target_id)
                    if _target_asset:
                        _target_asset.association_graph.append(
                            AssociationEdge(
                                target_asset_id=_asset.asset_id,
                                relation=_relation,
                                weight=_weight,
                            )
                        )
            
            _reg.register(_asset)

            # ── 同步到突触网络（双向桥接） ──
            try:
                from memory_synapse_network import SynapseNetwork
                _sn = SynapseNetwork(os.path.expanduser("~/.openclaw/workspace"))
                _sn._load()
                # 检查是否已存在相同内容的神经元（幂等）
                _exists = False
                for _n in _sn._neurons_cache.values():
                    if _n.content[:100] == content[:100]:
                        _n.activation_count += 1
                        _exists = True
                        break
                if not _exists:
                    from memory_synapse_network import MemoryNeuron
                    import hashlib as _hl
                    _neuron = MemoryNeuron(
                        id=f"NRN-DAG-{_hl.md5(content[:200].encode()).hexdigest()[:16]}",
                        content=content,
                        created_at=time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()),
                        activation_count=1,
                    )
                    _sn._neurons_cache[_neuron.id] = _neuron
                    _sn._save_neuron(_neuron)
            except Exception:
                pass

            logger.debug(f"KnowledgeAsset created for DAG node: {_asset.asset_id}")
        except Exception as _ae:
            logger.debug(f"KnowledgeAsset creation skip: {_ae}")

    def add_message(
        self,
        session_key: str,
        role: str,
        content: str,
        tokens: int = 0,
        importance: float = 0.5,
        emotion: float = 0.0,
        priority: int = PriorityLevel.NORMAL,
        keywords: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        parent_ids: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """添加一条消息节点，返回节点 ID"""
        node_id = f"{session_key}_{role}_{int(time.time()*1000)}_{hashlib.md5(content.encode()[:32]).hexdigest()[:8]}"

        _meta = {"role": role}
        if metadata:
            _meta.update(metadata)

        node = DAGNode(
            node_id=node_id,
            node_type=DAGNodeType.MESSAGE,
            session_key=session_key,
            content=content,
            tokens=tokens or len(content) // 4,
            priority=priority,
            parent_ids=parent_ids or [],
            importance_score=importance,
            emotion_score=emotion,
            keywords=keywords or [],
            entities=entities or [],
            timestamp=time.time(),
            metadata=_meta
        )

        self.add_node(node)
        return node_id

    def add_persona_node(
        self,
        session_key: str,
        content: str,
        tokens: int = 0,
        source: str = "claw-bootstrap_hook",
    ) -> str:
        """添加人格节点（priority: CRITICAL，永不压缩）"""
        node_id = f"persona_{session_key}_{int(time.time())}"

        node = DAGNode(
            node_id=node_id,
            node_type=DAGNodeType.PERSONA,
            session_key=session_key,
            content=content,
            tokens=tokens or len(content) // 4,
            priority=PriorityLevel.CRITICAL,
            timestamp=time.time(),
            metadata={"source": source, "persist_across_sessions": True}
        )

        self.add_node(node)
        return node_id

    def get_session_nodes(
        self,
        session_key: str,
        priority_max: Optional[int] = None,
        node_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[DAGNode]:
        """获取会话的所有节点"""
        conditions = ["session_key = ?"]
        params = [session_key]

        if priority_max is not None:
            conditions.append("priority <= ?")
            params.append(priority_max)

        if node_type:
            conditions.append("node_type = ?")
            params.append(node_type)

        query = f"SELECT * FROM dag_nodes WHERE {' AND '.join(conditions)} ORDER BY timestamp DESC"
        if limit:
            query += f" LIMIT {limit}"

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

        return [self._row_to_node(dict(row)) for row in rows]

    def _row_to_node(self, row: dict) -> DAGNode:
        """SQLite 行转 DAGNode"""
        for field_name in ['parent_ids', 'children_ids', 'summary_of_ids', 'keywords', 'entities']:
            if isinstance(row.get(field_name), str):
                try:
                    row[field_name] = json.loads(row[field_name])
                except (json.JSONDecodeError, TypeError):
                    row[field_name] = []
        if isinstance(row.get('metadata'), str):
            try:
                row['metadata'] = json.loads(row['metadata'])
            except (json.JSONDecodeError, TypeError):
                row['metadata'] = {}

        row['is_summary'] = bool(row.get('is_summary', False))
        row['is_evicted'] = bool(row.get('is_evicted', False))
        row['depth'] = int(row.get('depth', 0))
        return DAGNode.from_dict(row)

    def assemble_context(
        self,
        session_key: str,
        fresh_tail_count: Optional[int] = None,
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, Dict]:
        """
        组装最终上下文

        策略:
        1. priority: CRITICAL 的节点强制包含（人格）
        2. 最近 fresh_tail_count 条原始消息保留
        3. 从旧到新，用摘要节点填充剩余空间
        4. priority: HIGH 节点最后被裁
        """
        fresh_tail_count = fresh_tail_count or self.fresh_tail_count
        max_tokens = max_tokens or self.max_context_tokens

        all_nodes = self.get_session_nodes(session_key)

        # 分离各类节点
        critical_nodes = [n for n in all_nodes if n.priority == PriorityLevel.CRITICAL]
        high_nodes = [n for n in all_nodes if n.priority == PriorityLevel.HIGH]
        # 过滤已淘汰的摘要（被更高级摘要替代）
        summary_nodes = [n for n in all_nodes 
                        if n.is_summary and not n.is_evicted 
                        and n.priority <= PriorityLevel.NORMAL]
        message_nodes = [n for n in all_nodes if not n.is_summary and n.priority >= PriorityLevel.NORMAL]

        # 按时间排序
        critical_nodes.sort(key=lambda n: n.timestamp)
        high_nodes.sort(key=lambda n: n.timestamp)
        # 摘要优先高 depth（更紧凑），同 depth 从旧到新
        summary_nodes.sort(key=lambda n: (-n.depth, n.timestamp))
        message_nodes.sort(key=lambda n: n.timestamp)

        # 第一步：关键节点（人格等）强制包含
        result_parts = []
        used_tokens = 0

        for node in critical_nodes:
            result_parts.append(("critical", node))
            used_tokens += node.tokens

        # 第二步：最近原始消息（加入时间衰减权重后重排序）
        now = time.time()
        def _time_weight(node):
            age_hours = (now - node.timestamp) / 3600 if node.timestamp > 0 else 0
            decay = max(0.1, 1.0 - age_hours / (24 * 30))  # 30天半衰期
            return node.importance_score * decay

        message_nodes.sort(key=lambda n: n.timestamp)  # 保证时间顺序
        recent_messages = message_nodes[-fresh_tail_count:] if message_nodes else []
        older_messages = message_nodes[:-fresh_tail_count] if len(message_nodes) > fresh_tail_count else []

        for node in recent_messages:
            if used_tokens + node.tokens > max_tokens:
                break
            result_parts.append(("recent", node))
            used_tokens += node.tokens

        # 用时间衰减 + 重要性排序的旧消息填充剩余空间
        older_messages.sort(key=_time_weight, reverse=True)
        for node in older_messages:
            if used_tokens + node.tokens > max_tokens:
                break
            result_parts.append(("weighted_old", node))
            used_tokens += node.tokens

        # 第三步：摘要节点（优先高 depth，同 depth 内从旧到新）
        allowed_summary_tokens = max_tokens - used_tokens
        summary_tokens_used = 0

        for node in summary_nodes:
            if summary_tokens_used + node.tokens > allowed_summary_tokens:
                break
            result_parts.append(("summary", node))
            summary_tokens_used += node.tokens
            used_tokens += node.tokens

        # 第四步：如果还有空间，放 priority: HIGH 节点
        for node in high_nodes:
            if used_tokens + node.tokens > max_tokens:
                break
            result_parts.append(("high", node))
            used_tokens += node.tokens

        # 组装最终文本（v2: 有 blob_id 的还原完整原文）
        assembled_text = "\n\n".join([
            self.restore_full_content(node) for _, node in result_parts
        ])

        stats = {
            "total_tokens": used_tokens,
            "max_tokens": max_tokens,
            "critical_nodes": len(critical_nodes),
            "recent_messages": len(recent_messages),
            "summary_nodes_used": len([p for p in result_parts if p[0] == "summary"]),
            "summary_nodes_total": len(summary_nodes),
            "high_nodes_used": len([p for p in result_parts if p[0] == "high"]),
            "total_nodes_stored": len(all_nodes),
        }

        # 第五步：追加 R-CCAM cycle_summary（从 rccam_nodes 读最近 3 个 cycle 的摘要）
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT content, cycle_id, cycle_index FROM rccam_nodes "
                "WHERE session_key=? AND node_type='rccam_cycle_summary' AND is_compressed=0 "
                "ORDER BY cycle_index DESC LIMIT 3",
                (session_key,)
            )
            rccam_cycles = cur.fetchall()
            conn.close()
            if rccam_cycles:
                rccam_parts = []
                for row in reversed(rccam_cycles):
                    rccam_parts.append(f"[R-CCAM 循环 {row['cycle_index']}]\n{row['content'][:500]}")
                rccam_text = "\n\n".join(rccam_parts)
                rccam_tokens = len(rccam_text) // 2
                if used_tokens + rccam_tokens <= max_tokens:
                    assembled_text += "\n\n" + rccam_text
                    used_tokens += rccam_tokens
                    stats["rccam_cycles_used"] = len(rccam_cycles)
        except Exception:
            pass

        # ── COSPLAY 增强: Contract 上下文注入 ──
        try:
            from cosplay_context_adapter import get_cosplay_adapter
            _ca = get_cosplay_adapter()
            if _ca.config.contract_enabled:
                # 从当前节点的摘要中提取关键词，查 contract
                _summary_keywords = []
                for _, node in result_parts:
                    if hasattr(node, 'keywords') and node.keywords:
                        _summary_keywords.extend(node.keywords[:3])
                if _summary_keywords:
                    _ci = _ca.get_contract_instructions(_summary_keywords[:5])
                    if _ci:
                        _contract_text = "【COSPLAY 契约上下文】" + "，".join(_ci["predicates_keep"][:5])
                        _ct_tokens = len(_contract_text) // 2
                        if used_tokens + _ct_tokens <= max_tokens:
                            assembled_text += "\n\n" + _contract_text
                            used_tokens += _ct_tokens
                            stats["cosplay_contract_injected"] = True
        except Exception:
            pass

        return assembled_text, stats

    def _adjust_meta_params(self, stats: Dict):
        """
        [v2] Meta 参数自适应调整（参考 ALMA）。

        观察最近压缩行为，调整以下参数：
        - leaf_chunk_tokens：触发压缩的原始 token 数阈值
        - context_threshold：上下文使用率阈值
        - summary_target_tokens：摘要目标 token 数

        自适应规则（基于最近 10 次历史）：
        1. context 频繁填满（>80%）→ leaf_chunk_tokens 下调 10%
        2. context 使用率持续低（<40%）→ leaf_chunk_tokens 上调 15%
        3. 频繁触发压缩但没什么好压的 → context_threshold 上调 0.05
        4. 摘要 token 数波动大 → 调小 summary_target_tokens
        """
        _h = self._meta['compact_history']
        if len(_h) < 3:
            return

        _recent = _h[-5:]  # 最近 5 次

        # 1. context 频繁满
        _full_count = sum(1 for s in _recent if s.get('context_usage_ratio', 0) > 0.8)
        if _full_count >= 3 and self._meta['leaf_chunk_tokens'] > 1000:
            self._meta['leaf_chunk_tokens'] = max(
                1000, int(self._meta['leaf_chunk_tokens'] * 0.9)
            )
            self.leaf_chunk_tokens = self._meta['leaf_chunk_tokens']
            self._meta['adjust_count'] += 1
            logger.info(f"Meta: context 满 {_full_count}/{len(_recent)}, "
                        f"leaf_chunk_tokens ↓ {self._meta['leaf_chunk_tokens']}")

        # 2. context 使用率持续低
        _low_count = sum(1 for s in _recent if s.get('context_usage_ratio', 1) < 0.4)
        if _low_count >= 4:
            self._meta['leaf_chunk_tokens'] = min(
                64000, int(self._meta['leaf_chunk_tokens'] * 1.15)
            )
            self.leaf_chunk_tokens = self._meta['leaf_chunk_tokens']
            self._meta['adjust_count'] += 1
            logger.info(f"Meta: context 低 {_low_count}/{len(_recent)}, "
                        f"leaf_chunk_tokens ↑ {self._meta['leaf_chunk_tokens']}")

        # 3. 频繁触发但无效压缩
        _skip_count = self._meta['compact_skip_count']
        if _skip_count >= 3 and len(_h) >= 5:
            self._meta['context_threshold'] = min(0.95,
                self._meta['context_threshold'] + 0.05)
            self.context_threshold = self._meta['context_threshold']
            self._meta['compact_skip_count'] = 0
            self._meta['adjust_count'] += 1
            logger.info(f"Meta: 无效压缩 {_skip_count} 次, "
                        f"context_threshold ↑ {self._meta['context_threshold']:.2f}")

    def should_compact(self, session_key: str) -> Tuple[bool, Dict]:
        """检查是否需要触发增量压缩 [v2: Meta 自适应阈值]

        额外检查时序 KG 的社区摘要是否可用。
        如果 TKG 社区摘要内容丰富，可适当降低压缩阈值（用社区摘要替代部分原始消息）
        """
        all_nodes = self.get_session_nodes(session_key)

        raw_nodes = [n for n in all_nodes if not n.is_summary and n.priority >= PriorityLevel.NORMAL]
        raw_tokens = sum(n.tokens for n in raw_nodes)

        _adjusted_leaf = self._meta['leaf_chunk_tokens']
        _adjusted_threshold = self._meta['context_threshold']
        threshold_tokens = int(self.max_context_tokens * _adjusted_threshold)
        needs_compact = raw_tokens > _adjusted_leaf

        # 检查 TKG 社区摘要是否可用
        community_summary_available = False
        try:
            tkg = self._get_temporal_kg()
            session_graph = tkg.get_session_graph(session_key)
            if session_graph['stats']['edge_count'] >= 2:
                communities = tkg.build_community(min_edges=2)
                community_summary_available = len(communities) > 0
        except Exception:
            pass

        # 如果社区摘要可用，可提前触发压缩
        if community_summary_available and raw_tokens > _adjusted_leaf * 0.7:
            needs_compact = True

        # v3: 检查摘要节点膨胀 — 当 low-depth（depth<=1）摘要 token 超过阈值时
        # 触发二次压缩（summary_compact），而不是标记 needs_compact
        summary_nodes_all = [n for n in all_nodes if n.is_summary and not n.is_evicted]
        low_depth_summary_tokens = sum(
            n.tokens for n in summary_nodes_all if n.depth <= 1
        )
        summary_overflow = low_depth_summary_tokens > self.max_context_tokens * 0.15

        context_usage_ratio = raw_tokens / self.max_context_tokens if self.max_context_tokens else 0

        stats = {
            "raw_nodes": len(raw_nodes),
            "raw_tokens": raw_tokens,
            "summary_nodes": len(summary_nodes_all),
            "summary_tokens": sum(n.tokens for n in summary_nodes_all),
            "low_depth_summary_tokens": low_depth_summary_tokens,
            "summary_overflow": summary_overflow,
            "threshold_tokens": threshold_tokens,
            "leaf_chunk_tokens": _adjusted_leaf,
            "context_threshold": _adjusted_threshold,
            "needs_compact": needs_compact,
            "context_usage_ratio": context_usage_ratio,
            "community_summary_available": community_summary_available,
            "meta": {
                "compact_history_len": len(self._meta['compact_history']),
                "adjust_count": self._meta['adjust_count'],
                "leaf_chunk_tokens_meta": self._meta['leaf_chunk_tokens'],
                "context_threshold_meta": self._meta['context_threshold'],
            },
        }

        # 记录压缩历史
        if needs_compact:
            self._meta['compact_history'].append({
                'context_usage_ratio': context_usage_ratio,
                'raw_tokens': raw_tokens,
                'ts': time.time(),
            })
            # 只保留最近 10 次
            if len(self._meta['compact_history']) > 10:
                self._meta['compact_history'] = self._meta['compact_history'][-10:]

            self._meta['last_compact_tokens'] = raw_tokens
            self._meta['last_context_ratio'] = context_usage_ratio

        # Meta 自适应
        self._adjust_meta_params(stats)

        return needs_compact, stats

    def ensure_auto_compact(self, session_key: str) -> Dict:
        """Worker dag_compact handler 调用的兼容入口

        v3: 先检查摘要膨胀（summary_overflow），触发 summary_compact；
        否则走原来的 auto_summarize。
        """
        # 先跑 should_compact 拿到摘要膨胀信息
        _, stats = self.should_compact(session_key)
        if stats.get('summary_overflow') and stats.get('low_depth_summary_tokens', 0) > 0:
            result = self.summary_compact(session_key)
            if result.get('summarized', 0) > 0:
                return result
        # 降级到原始 auto_summarize
        return self.auto_summarize(session_key=session_key, batch_size=10)

    def auto_summarize(
        self,
        session_key: str,
        batch_size: int = 10,
        summary_text: Optional[str] = None,
    ) -> Dict:
        """
        自动为旧消息生成摘要节点

        Args:
            session_key: 会话 key
            batch_size: 一批摘要的消息数
            summary_text: 外部提供的摘要文本（如由 Worker/LLM 生成），None 则截断降级
        """
        needs_compact, _ = self.should_compact(session_key)
        if not needs_compact:
            return {"summarized": 0, "reason": "leaf_chunk_tokens 未达到阈值"}

        all_nodes = self.get_session_nodes(session_key)
        raw_nodes = [n for n in all_nodes if not n.is_summary and n.priority >= PriorityLevel.NORMAL]

        if len(raw_nodes) <= self.fresh_tail_count + batch_size:
            return {"summarized": 0, "reason": "消息数不够，保留最近上下文"}

        to_summarize = raw_nodes[:-self.fresh_tail_count][:batch_size]
        if not to_summarize:
            return {"summarized": 0, "reason": "没有可摘要的节点"}

        # v2: 先将原文存到 BlobArena（无损保留），节点只存 memo
        if self._blob_arena:
            combined_text = "\n".join([n.content for n in to_summarize])
            _blob_id = self._blob_arena.append_text(combined_text)
            # memo = 轻量检索索引，不做价值判断
            memo_text = generate_memo(combined_text) if generate_memo else combined_text[:200]
            keywords = _extract_keywords(combined_text)
            _method = "blob_arena"
        else:
            # 降级到旧版 Flash/截断
            combined_text = "\n".join([n.content for n in to_summarize])
            _blob_id = ""
            if not summary_text:
                try:
                    from xiaoyi_claw_api import get_global_xiaoyi_claw
                    _xc = get_global_xiaoyi_claw()
                    if _xc and _xc.llm_flash:
                        _flash_resp = _xc.llm_flash.chat.completions.create(
                            model=_xc._llm_flash_model,
                            messages=[{"role": "user",
                                "content": f"请用中文为以下对话内容生成简洁的摘要，保留核心信息和关键结论：\n\n{combined_text[:3000]}\n\n摘要："}],
                            max_tokens=256, temperature=0.1,
                        )
                        summary_text = _flash_resp.choices[0].message.content.strip()[:800]
                        _method = "flash"
                except Exception:
                    pass
            if not summary_text:
                summary_text = combined_text[:500] + "..." if len(combined_text) > 500 else combined_text
                _method = "rule_truncate"
            memo_text = summary_text
            keywords = _extract_keywords(summary_text)

        summary_node_id = f"summ_{session_key}_{int(time.time())}_{hashlib.md5((memo_text or combined_text).encode()[:16]).hexdigest()[:8]}"
        summary_node = DAGNode(
            node_id=summary_node_id,
            node_type=DAGNodeType.SUMMARY,
            session_key=session_key,
            content=f"[摘要] {memo_text}",
            blob_id=_blob_id if _blob_id else getattr(summary_node, 'blob_id', ''),
            tokens=len(memo_text) // 4,
            priority=PriorityLevel.NORMAL,
            is_summary=True,
            depth=1,  # 一次摘要
            summary_of_ids=[n.node_id for n in to_summarize],
            timestamp=time.time(),
            keywords=keywords,
        )
        self.add_node(summary_node)

        return {
            "summarized": len(to_summarize),
            "summary_node_id": summary_node_id,
            "summary_length": len(memo_text),
            "method": _method,
            "blob_id": _blob_id,
        }

    def expand_summary(self, summary_node_id: str) -> List[DAGNode]:
        """展开摘要，找回原始消息"""
        # ── COSPLAY 反馈: 展开事件 → 记录为正向信号 ──
        try:
            from cosplay_context_adapter import get_cosplay_adapter
            _ca_exp = get_cosplay_adapter()
            _ca_exp.record_expand_event(summary_node_id)
        except Exception:
            pass

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT summary_of_ids FROM dag_nodes WHERE node_id = ?",
                (summary_node_id,)
            )
            row = cursor.fetchone()
            if not row:
                conn.close()
                return []

            summary_of_ids = json.loads(row['summary_of_ids'])
            if not summary_of_ids:
                conn.close()
                return []

            placeholders = ','.join(['?' for _ in summary_of_ids])
            cursor = conn.execute(
                f"SELECT * FROM dag_nodes WHERE node_id IN ({placeholders})",
                summary_of_ids
            )
            rows = cursor.fetchall()
            conn.close()

        nodes = [self._row_to_node(dict(r)) for r in rows]
        nodes.sort(key=lambda n: n.timestamp)
        return nodes

    # ── v2: BlobArena 无损恢复 ──

    def restore_full_content(self, node: DAGNode) -> str:
        """
        从 BlobArena 恢复节点的完整原文".

        如果节点有 blob_id，从 BlobArena 读取全量原文。
        如果没有（旧数据），返回节点自身的 content。

        Args:
            node: DAGNode 或 dict（含 blob_id 字段）

        Returns:
            str: 完整原文
        """
        if not self._blob_arena:
            return node.content if hasattr(node, 'content') else node.get('content', '')

        blob_id = getattr(node, 'blob_id', '') if hasattr(node, 'blob_id') else node.get('blob_id', '')
        if blob_id:
            try:
                full_text = self._blob_arena.read_text(blob_id)
                if full_text:
                    return full_text
            except Exception:
                pass
        # 降级：返回节点自身的 content
        return node.content if hasattr(node, 'content') else node.get('content', '')

    def restore_batch_content(self, nodes: List[DAGNode]) -> List[Dict]:
        """批量恢复节点原文"""
        result = []
        for n in nodes:
            doc = n.to_dict() if hasattr(n, 'to_dict') else dict(n)
            doc['full_content'] = self.restore_full_content(n)
            result.append(doc)
        return result

    def get_node_count(self) -> Dict[str, Dict[str, int]]:
        """获取节点统计"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT node_type, priority, COUNT(*) as count
                FROM dag_nodes
                GROUP BY node_type, priority
            """)
            rows = cursor.fetchall()
            conn.close()

        stats = defaultdict(lambda: defaultdict(int))
        for row in rows:
            stats[row[0]][row[1]] = row[2]

        return dict(stats)

    # ========================================================================
    # Cognition Forest 子树（四类独立子树，永不压缩）
    # ========================================================================

    def _cog_subtree_key(self, forest_type: str) -> str:
        """生成 Cognition Forest 子树 session_key"""
        return f"{_COG_SUBTREE_SESSION_PREFIX}{forest_type}"

    def add_cognition_subtree(
        self,
        forest_type: str,
        content: str,
        tokens: int = 0,
        source: str = "",
        metadata: Optional[Dict] = None,
    ) -> str:
        """
        写入一条 Cognition Forest 子树数据。

        Args:
            forest_type: 子树类型 (user/self/env/meta)
            content: 数据内容
            tokens: token 数
            source: 数据来源（如 personality_files/dag_assemble/system_monitor）
            metadata: 额外元数据

        Returns:
            节点 ID
        """
        if forest_type not in CognitionForestType.ALL_TYPES:
            logger.warning(f"未知子树类型: {forest_type}, 跳过")
            return ""

        session_key = self._cog_subtree_key(forest_type)
        node_id = f"cog_{forest_type}_{source}_{int(time.time()*1000)}_{hashlib.md5(content.encode()[:16]).hexdigest()[:8]}"

        _meta = {"source": source, "forest_type": forest_type}
        if metadata:
            _meta.update(metadata)

        node = DAGNode(
            node_id=node_id,
            node_type=DAGNodeType.PERSONA,  # 同人格节点级别保护
            session_key=session_key,
            content=content,
            tokens=tokens or len(content) // 4,
            priority=PriorityLevel.CRITICAL,  # 永不压缩
            parent_ids=[],
            importance_score=0.9 if forest_type in (CognitionForestType.USER, CognitionForestType.SELF) else 0.7,
            timestamp=time.time(),
            metadata=_meta,
        )

        self.add_node(node)
        return node_id

    def get_cognition_subtree(
        self,
        forest_type: str,
        limit: Optional[int] = 5,
    ) -> List[DAGNode]:
        """
        获取 Cognition Forest 子树数据（按时间倒序）。

        Args:
            forest_type: 子树类型 (user/self/env/meta)
            limit: 返回条数，None 返回全部

        Returns:
            节点列表（最新在前）
        """
        session_key = self._cog_subtree_key(forest_type)
        return self.get_session_nodes(
            session_key=session_key,
            priority_max=PriorityLevel.CRITICAL,
            limit=limit,
        )

    def list_cognition_subtrees(self) -> Dict[str, int]:
        """列出所有 Cognition Forest 子树及其节点数量"""
        result = {}
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            for ft in CognitionForestType.ALL_TYPES:
                sk = self._cog_subtree_key(ft)
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM dag_nodes WHERE session_key = ?",
                    (sk,)
                )
                result[ft] = cursor.fetchone()[0]
            conn.close()
        return result

    def clear_cognition_subtree(self, forest_type: str) -> int:
        """
        清空指定 Cognition Forest 子树。

        Args:
            forest_type: 子树类型 (user/self/env/meta)

        Returns:
            删除的节点数
        """
        if forest_type not in CognitionForestType.ALL_TYPES:
            logger.warning(f"未知子树类型: {forest_type}, 跳过")
            return 0

        session_key = self._cog_subtree_key(forest_type)
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                "DELETE FROM dag_nodes WHERE session_key = ?",
                (session_key,)
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
        logger.info(f"清空 Cognition Forest 子树 '{forest_type}': 删除 {deleted} 个节点")
        return deleted

    # ========================================================================
    # R-CCAM 阶段节点（新式节点，写入 rccam_nodes 表）
    # ========================================================================

    PHASE_PARENT_MAP = {
        "user_input":  [],
        "retrieval":   ["user_input"],
        "cognition":   ["retrieval"],
        "control":     ["cognition"],
        "action":      ["control"],
        "memory":      ["action"],
    }

    PHASE_ORDER = [
        "user_input", "retrieval", "cognition",
        "control", "action", "memory", "cycle_summary",
    ]

    def add_rccam_node(self, session_key, cycle_id, cycle_index, phase_name,
                        content, strategy="", confidence=0.5, validation="unknown",
                        importance=0.5, parent_ids=None, priority=2,
                        node_type=None, metadata=None,
                        previous_cycle_id="", blob_id=""):
        """写入一个 R-CCAM 阶段节点到 rccam_nodes 表

        v2: 增加 blob_id 参数，支持 BlobArena 无损存储
        """
        node_type = node_type or f"rccam_{phase_name}"
        node_id = f"rccam_{phase_name}_{session_key}_{cycle_index}_{int(time.time()*1000)}"

        if parent_ids is None:
            parent_ids = []
            expected = self.PHASE_PARENT_MAP.get(phase_name, [])
            if expected:
                with self._lock:
                    conn = sqlite3.connect(self.db_path)
                    cur = conn.execute(
                        "SELECT node_id FROM rccam_nodes WHERE session_key=? AND cycle_id=? AND phase_name=?",
                        (session_key, cycle_id, expected[-1])
                    )
                    row = cur.fetchone()
                    if row:
                        parent_ids.append(row[0])
                    conn.close()

        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)

        tokens = len(str(content)) // 2 or 1
        ts = time.time()

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                INSERT OR REPLACE INTO rccam_nodes
                (node_id, node_type, session_key, content, blob_id, tokens,
                 parent_ids, cycle_id, previous_cycle_id, phase_name, cycle_index,
                 priority, is_summary, is_compressed,
                 importance_score, confidence, validation,
                 keywords, strategy, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node_id, node_type, session_key, str(content), blob_id, tokens,
                json.dumps(parent_ids), cycle_id, previous_cycle_id, phase_name, cycle_index,
                priority, 0, 0,
                importance, confidence, validation,
                json.dumps(self._extract_keywords(content)),
                strategy, ts,
                json.dumps(metadata or {}),
            ))
            conn.commit()
            conn.close()

        return node_id

    def get_rccam_cycle_nodes(self, session_key, cycle_id):
        """获取指定 cycle 内的所有阶段节点，按 phase 顺序排列"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM rccam_nodes WHERE session_key=? AND cycle_id=? ORDER BY timestamp ASC",
                (session_key, cycle_id)
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()

        def _phase_key(n):
            p = n.get("phase_name", "")
            try:
                return self.PHASE_ORDER.index(p)
            except ValueError:
                return 99
        rows.sort(key=_phase_key)
        return rows

    def get_rccam_session_cycles(self, session_key):
        """获取一个 session 的所有 cycle_id 及其元信息"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """SELECT DISTINCT cycle_id, MIN(timestamp) as first_ts,
                          MAX(cycle_index) as max_idx,
                          MAX(is_summary) as has_summary
                 FROM rccam_nodes WHERE session_key=? AND cycle_id != ''
                 GROUP BY cycle_id ORDER BY first_ts ASC""",
                (session_key,)
            )
            cycles = [dict(r) for r in cur.fetchall()]
            conn.close()
        return cycles

    def assemble_from_cycles(self, session_key, fresh_cycles=3, max_tokens=240000, trace_parent_depth=2):
        """
        assemble context from rccam cycles + parent_ids trace.
        trace_parent_depth: parent_ids causal tracing depth (0=off, 1=direct parent, 2=recursive two levels)
        """
        persona_nodes = self.get_session_nodes(session_key, priority_max=0, limit=10)
        cycles = self.get_rccam_session_cycles(session_key)

        # 修复 F-18: 之前 Cognitive Forest 节点（_cog_subtree_user / _cog_subtree_self）
        # 既不在单会话 assemble 范围（要 session_key == self.session_key），也不在
        # cross_session_memory_restore 范围（明确过滤）。现在在 assemble 顶部
        # 主动注入这两种节点作为 persona 补充，让自进化能力 / 用户画像真正可被读到。
        try:
            for _cog_prefix in ("_cog_subtree_user", "_cog_subtree_self"):
                _cog_nodes = self.get_session_nodes(_cog_prefix, priority_max=0, limit=5)
                for _cn in _cog_nodes:
                    if _cn.content and _cn.content not in [p[1] for p in persona_nodes if hasattr(p, '__iter__') and False]:
                        persona_nodes.append(_cn)
        except Exception:
            pass

        result_parts = []
        used_tokens = 0
        traced_ids = set()

        for n in persona_nodes:
            result_parts.append(("persona", n.content))
            used_tokens += max(n.tokens, len(n.content) // 2)

        if cycles:
            recent = cycles[-fresh_cycles:] if len(cycles) > fresh_cycles else cycles
            older = cycles[:-fresh_cycles] if len(cycles) > fresh_cycles else []

            for c in recent:
                nodes = self.get_rccam_cycle_nodes(session_key, c["cycle_id"])
                # parent_ids causal trace: collect upstream nodes
                upstream_nodes = []
                if trace_parent_depth > 0:
                    # 修复 F-17: 之前每个 parent_id 都新开 sqlite3.connect/close，
                    # 在 trace_parent_depth=2 + 30 节点时产生 60+ 次连接。
                    # 改为一次连接 + 复用 cursor + IN (?,?,?) 批量查。
                    stack = [(n, 0) for n in nodes]
                    _to_query = {}  # (pid, depth) → nodes 需要它
                    while stack:
                        node_entry, d = stack.pop()
                        if d >= trace_parent_depth:
                            continue
                        try:
                            pids = json.loads(node_entry.get("parent_ids", "[]"))
                        except Exception:
                            pids = []
                        for pid in pids:
                            if pid in traced_ids:
                                continue
                            traced_ids.add(pid)
                            _to_query[pid] = d
                    if _to_query:
                        with self._lock:
                            conn = sqlite3.connect(self.db_path)
                            conn.row_factory = sqlite3.Row
                            placeholders = ",".join("?" * len(_to_query))
                            cur = conn.execute(
                                f"SELECT * FROM rccam_nodes WHERE node_id IN ({placeholders}) AND session_key=?",
                                (*_to_query.keys(), session_key)
                            )
                            rows = cur.fetchall()
                            conn.close()
                        for prow in rows:
                            pd = dict(prow)
                            upstream_nodes.append(pd)
                            stack.append((pd, _to_query.get(pd.get("node_id"), 0) + 1))
                for up in upstream_nodes:
                    ut = up.get("tokens", len(up.get("content","")) // 2)
                    if used_tokens + ut > max_tokens * 0.95:
                        continue
                    up_label = "[upstream {}:{}] ".format(
                        up.get("cycle_id","?")[-16:], up.get("phase_name","?"))
                    result_parts.append((f"upstream_{up['node_id']}",
                                         up_label + up.get("content","")[:500]))
                    used_tokens += ut
                for node_entry in nodes:
                    if node_entry.get("is_compressed", 0):
                        continue
                    t = node_entry.get("tokens", 0) or len(node_entry.get("content","")) // 2
                    if used_tokens + t > max_tokens * 0.95:
                        break
                    pname = node_entry.get("phase_name", "?")
                    label = "[{}] ".format(pname.upper())
                    result_parts.append((f"cycle_{c['cycle_id']}_{pname}",
                                         label + node_entry.get("content","")))
                    used_tokens += t

            for c in reversed(older):
                summary = self.get_cycle_summary(session_key, c["cycle_id"])
                if summary:
                    st = summary.get("tokens", len(summary["content"]) // 2)
                    if used_tokens + st <= max_tokens:
                        result_parts.append((f"summ_{c['cycle_id']}",
                                             f"[Cycle {c.get('cycle_index','?')} summary] {summary['content']}"))
                        used_tokens += st
                    else:
                        break

        has_rccam = bool(cycles)
        if not has_rccam:
            text, _ = self.assemble_context(session_key, fresh_tail_count=20, max_tokens=max_tokens)
            if text:
                result_parts.append(("old_dag", text))

        assembled = "\n\n".join([c for _, c in result_parts])
        stats = {
            "total_tokens": used_tokens,
            "max_tokens": max_tokens,
            "persona_nodes": len(persona_nodes),
            "total_cycles": len(cycles),
            "total_parts": len(result_parts),
            "traced_parents": len(traced_ids),
        }
        return assembled, stats

    def get_cycle_summary(self, session_key, cycle_id):
        """获取某个 cycle 的摘要节点"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT * FROM rccam_nodes WHERE session_key=? AND cycle_id=? AND node_type='rccam_cycle_summary' LIMIT 1",
                (session_key, cycle_id)
            )
            row = cur.fetchone()
            conn.close()
        if row:
            d = dict(row)
            try:
                d["metadata"] = json.loads(d["metadata"]) if isinstance(d["metadata"], str) else d["metadata"]
            except Exception:
                d["metadata"] = {}
            return d
        return None

    def write_capability_node(self, capability: dict, session_key: str):
        """将自进化检测到的稳定模式写为 evolved_capability 节点到 rccam_nodes"""
        import json, time, uuid
        node_id = f"cap_{capability.get('name','pattern')[:30]}_{int(time.time())}"
        content = json.dumps(capability, ensure_ascii=False)
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute("""
                    INSERT OR REPLACE INTO rccam_nodes
                    (node_id, node_type, session_key, content, tokens, confidence,
                     parent_ids, timestamp, is_compressed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """, (
                    node_id,
                    "evolved_capability",
                    session_key,
                    content,
                    len(content) // 2,
                    capability.get("confidence", 0.5),
                    json.dumps([]),
                    time.time(),
                ))
                conn.commit()
                conn.close()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"write_capability_node failed: {e}")

    def query_capability_nodes(self, limit: int = 5, session_key: str = 'xiaoyi-claw-dag') -> List[Dict]:
        """查询最近的 evolved_capability 节点（APO/ThinkingEnhanced 自优化结果）"""
        import json
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT content, timestamp, node_id FROM rccam_nodes "
                    "WHERE session_key=? AND node_type='evolved_capability' "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (session_key, limit))
                rows = cursor.fetchall()
                conn.close()
                return [json.loads(r["content"]) for r in rows if r["content"]]
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"query_capability_nodes failed: {e}")
                return []


    def write_cycle_summary(self, session_key, cycle_id, cycle_index,
                             user_intent, key_findings, conclusion,
                             confidence=0.5, source_phases=None):
        """写入 cycle_summary 节点，标记原始节点为 is_compressed"""
        content_obj = {
            "cycle": cycle_index,
            "user_intent": user_intent[:200],
            "key_findings": (key_findings or [])[:5],
            "conclusion": conclusion[:200] if conclusion else "",
            "confidence": confidence,
            "source_phases": source_phases or {},
        }
        content = json.dumps(content_obj, ensure_ascii=False)

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE rccam_nodes SET is_compressed=1 WHERE session_key=? AND cycle_id=? AND node_type != 'rccam_cycle_summary'",
                (session_key, cycle_id)
            )
            conn.commit()
            conn.close()

        previous_cycle_id = cycle_id.replace(f"_{cycle_index}", f"_{cycle_index - 1}") if cycle_index and cycle_index > 1 else ""

        return self.add_rccam_node(
            session_key=session_key, cycle_id=cycle_id,
            cycle_index=cycle_index, phase_name="cycle_summary",
            content=content, strategy="cycle_summary",
            confidence=confidence, validation="passed",
            importance=0.8, node_type="rccam_cycle_summary",
            previous_cycle_id=previous_cycle_id,
        )

    def expand_rccam_cycle(self, session_key, cycle_id):
        """展开 cycle_summary，从 rccam_nodes 恢复原始阶段节点"""
        nodes = self.get_rccam_cycle_nodes(session_key, cycle_id)
        return [n for n in nodes if n["node_type"] != "rccam_cycle_summary"]

    def rccam_compact_needed(self, session_key):
        """检查全域是否需要触发压缩（rccam_nodes + dag_nodes 总 token）

        返回:
            needs_soft: 总 raw_tokens > τ_soft(6K)
            needs_hard: 总 raw_tokens > τ_hard(12K)
            compressible_cycles: R-CCAM 可压缩的 cycle
            compressible_dag: DAG 可压缩的消息数
            stats: 统计信息

        全域总 token = rccam_nodes raw + dag_nodes 非人格非摘要 raw
        """
        TAU_SOFT, TAU_HARD = 6000, 12000

        with self._lock:
            conn = sqlite3.connect(self.db_path)
            # R-CCAM nodes raw（未压缩的非 cycle_summary 节点）
            cur = conn.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM rccam_nodes "
                "WHERE session_key=? AND is_compressed=0 AND node_type != 'rccam_cycle_summary'",
                (session_key,)
            )
            rccam_raw = cur.fetchone()[0] or 0

            # DAG nodes raw（非摘要、priority>NORMAL 的普通消息）
            cur = conn.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM dag_nodes "
                "WHERE session_key=? AND is_summary=0 AND priority>=?",
                (session_key, PriorityLevel.NORMAL)
            )
            dag_raw = cur.fetchone()[0] or 0

            # DAG 普通消息总数
            cur = conn.execute(
                "SELECT COUNT(*) FROM dag_nodes "
                "WHERE session_key=? AND is_summary=0 AND priority>=?",
                (session_key, PriorityLevel.NORMAL)
            )
            dag_raw_count = cur.fetchone()[0] or 0
            conn.close()

        raw_tokens = rccam_raw + dag_raw
        needs_soft = raw_tokens > TAU_SOFT
        needs_hard = raw_tokens > TAU_HARD

        cycles = self.get_rccam_session_cycles(session_key)
        compressible_cycles = []
        if needs_soft and rccam_raw > 0:
            # 策略：如果只有1个cycle但有太多phase节点，直接压这个cycle
            # 如果多个cycle，压超过2轮以前的
            # 如果某个cycle内有超过100个未压缩节点，也标记为可压缩
            for c in cycles:
                if c.get("has_summary", 0):
                    continue
                if len(cycles) > 2 and (len(cycles) - cycles.index(c)) > 2:
                    compressible_cycles.append(c["cycle_id"])
                elif len(cycles) <= 2:
                    # 单/双 cycle 场景：检查cycle内phase节点数
                    with self._lock:
                        conn_inner = sqlite3.connect(self.db_path)
                        cnt = conn_inner.execute(
                            "SELECT COUNT(*) FROM rccam_nodes WHERE session_key=? AND cycle_id=? AND is_compressed=0 AND node_type!='rccam_cycle_summary'",
                            (session_key, c["cycle_id"])
                        ).fetchone()[0] or 0
                        conn_inner.close()
                    if cnt > 20:
                        compressible_cycles.append(c["cycle_id"])
                        break  # 一个cycle就够了

        # DAG 可压缩消息（保留最近 8 条，其余都可以压）
        compressible_dag = max(0, dag_raw_count - 8) if needs_soft else 0

        # 决定优先压缩哪边
        # 策略：优先压 R-CCAM cycle（因为结构化的认知摘要更有用），
        # 再压 DAG 消息。如果 rccam 无 cycle 可压但 dag_raw 大，压 dag。
        compress_priority = "rccam_first"
        if not compressible_cycles and compressible_dag > 0:
            compress_priority = "dag_only"
        elif not compressible_cycles and not needs_soft:
            compress_priority = "none"

        stats = {
            "raw_tokens": raw_tokens, "rccam_raw": rccam_raw, "dag_raw": dag_raw,
            "tau_soft": TAU_SOFT, "tau_hard": TAU_HARD,
            "total_cycles": len(cycles), "compressible_cycles": len(compressible_cycles),
            "dag_raw_count": dag_raw_count, "compressible_dag": compressible_dag,
            "compress_priority": compress_priority,
        }
        return needs_soft, needs_hard, compressible_cycles, stats

    def compact_rccam_cycle(self, session_key, cycle_id, expandable=False):
        """
        [v2] 压缩 R-CCAM cycle — BlobArena 无损存储 + Memo 检索索引

        废除 LCM 三级压缩协议（不再丢弃任何原始内容）。
        改为：
        - 保存完整的原始阶段节点到 BlobArena（无损）
        - summary 节点只存 ~50 词 memo + blob_id（检索索引用）
        - assemble 时自动从 BlobArena 还原完整原文

        Args:
            session_key: 会话 key
            cycle_id: 要压缩的 cycle ID
            expandable: 如果 True，写入 dag_nodes 表（支持 expand 回溯）；
                        否则写 rccam_nodes 表（默认）
        """
        nodes = self.get_rccam_cycle_nodes(session_key, cycle_id)
        if not nodes:
            return {"summarized": 0, "reason": "no nodes"}

        # 构建完整内容用于 BlobArena 存储
        full_text = ""
        phase_names_raw = [n["phase_name"] for n in nodes]
        unique_phases = set(phase_names_raw)
        multiple_rounds = len(phase_names_raw) > len(unique_phases) * 1.5

        # 标记要压缩的节点
        marked_ids = []
        for n in nodes:
            marked_ids.append(n["node_id"])
            # 构建完整原文
            full_text += f"[{n['phase_name']}]\n{n['content']}\n\n"

        # 生成 memo（检索索引用，不丢信息因为 BlobArena 存了全文）
        if self._blob_arena:
            # 全文存到 BlobArena
            _blob_id = self._blob_arena.append_text(full_text)
            # 生成轻量 memo
            _memo_text = generate_memo(full_text) if generate_memo else full_text[:200]
            _method = "blob_arena_memo"
            _confidence = 0.5
            _validation = "blob_arena_preserved"
        else:
            # 降级到旧版 Flash 摘要（仍保留部分信息）
            _blob_id = ""
            try:
                from xiaoyi_claw_api import get_global_xiaoyi_claw
                _xc = get_global_xiaoyi_claw()
                if _xc and _xc.llm_flash:
                    _flash_resp = _xc.llm_flash.chat.completions.create(
                        model=_xc._llm_flash_model,
                        messages=[{"role": "user",
                            "content": f"请为以下 R-CCAM 认知循环生成简洁摘要（保留核心结论和关键发现）：\n\n{full_text[:3000]}\n\n摘要："}],
                        max_tokens=256, temperature=0.1,
                    )
                    _memo_text = _flash_resp.choices[0].message.content.strip()[:500]
                    _method = "flash"
                else:
                    _memo_text = full_text[:300] + "..."
                    _method = "rule_truncate"
            except Exception:
                _memo_text = full_text[:300] + "..."
                _method = "rule_truncate"
            _confidence = 0.3
            _validation = _method

        # 标记原始节点已压缩
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE rccam_nodes SET is_compressed=1 WHERE node_id IN (" +
                ",".join(["?"] * len(marked_ids)) + ")",
                marked_ids
            )
            conn.commit()
            conn.close()

        if multiple_rounds:
            # 多轮场景：多个 chunk，每个独立 memo
            chunk_size = 50
            results = []
            total = len(nodes)
            target_table = "dag_nodes" if expandable else "rccam_nodes"
            for start in range(0, total, chunk_size):
                chunk = nodes[start:start + chunk_size]
                if not chunk:
                    continue
                chunk_ids = [n["node_id"] for n in chunk]
                chunk_full = ""
                for n in chunk:
                    chunk_full += f"[{n['phase_name']}]\n{n['content']}\n\n"

                # chunk 级别 blob
                _chunk_blob_id = ""
                if self._blob_arena:
                    _chunk_blob_id = self._blob_arena.append_text(chunk_full)
                _chunk_memo = generate_memo(chunk_full) if (generate_memo and self._blob_arena) else chunk_full[:200]

                if target_table == "rccam_nodes":
                    # 用 add_rccam_node 但通过 blob 方式
                    summary_node_id = self.add_rccam_node(
                        session_key=session_key, cycle_id=cycle_id,
                        cycle_index=chunk[0].get("cycle_index", 1),
                        phase_name="cycle_summary",
                        content=f"[memo] {_chunk_memo}",
                        blob_id=_chunk_blob_id,
                        strategy="cycle_summary",
                        confidence=0.4, validation="blob_arena",
                        importance=0.5, node_type="rccam_cycle_summary",
                    )
                else:
                    summary_node_id = "cog_summ_" + chunk_ids[0][:30]
                    summary_node = DAGNode(
                        node_id=summary_node_id,
                        node_type="cognitive_summary",
                        session_key=session_key,
                        content=f"[memo] {_chunk_memo}",
                        blob_id=_chunk_blob_id,
                        tokens=len(_chunk_memo) // 4 or 1,
                        priority=PriorityLevel.NORMAL,
                        is_summary=True,
                        summary_of_ids=chunk_ids,
                        timestamp=chunk[-1]["timestamp"],
                    )
                    self.add_node(summary_node)
                results.append(summary_node_id)
            return {"summarized": len(results), "nodes_affected": len(marked_ids), "method": _method}

        # ── 单轮场景 ──
        cycle_index = nodes[0]["cycle_index"]
        phase_map = {n["phase_name"]: n["content"] for n in nodes}

        # 构建 memo 内容
        user_intent = phase_map.get("user_input", "")[:200]
        conclusions = phase_map.get("action") or phase_map.get("control", "")[:200]

        # memo 用 generate_memo（只做检索索引，不做价值判断）
        memo_for_index = _memo_text if self._blob_arena else full_text[:300]

        previous_cycle_id = cycle_id.replace(f"_{cycle_index}", f"_{cycle_index - 1}") if cycle_index and cycle_index > 1 else ""

        target_table = "dag_nodes" if expandable else "rccam_nodes"
        if target_table == "rccam_nodes":
            return self.add_rccam_node(
                session_key=session_key, cycle_id=cycle_id,
                cycle_index=cycle_index, phase_name="cycle_summary",
                content=f"[memo] {memo_for_index}",
                blob_id=_blob_id,
                strategy="cycle_summary",
                confidence=_confidence, validation=_validation,
                importance=0.7, node_type="rccam_cycle_summary",
                previous_cycle_id=previous_cycle_id,
            )
        else:
            summary_node_id = "cog_summ_" + nodes[0]["node_id"][:30]
            summary_node = DAGNode(
                node_id=summary_node_id,
                node_type="cognitive_summary",
                session_key=session_key,
                content=f"[memo] {memo_for_index}",
                blob_id=_blob_id,
                tokens=len(memo_for_index) // 4 or 1,
                priority=PriorityLevel.NORMAL,
                is_summary=True,
                summary_of_ids=[n["node_id"] for n in nodes],
                timestamp=nodes[-1]["timestamp"],
            )
            self.add_node(summary_node)
            return {"summarized": 1, "node_id": summary_node_id, "method": _method}

    def summary_compact(self, session_key: str,
                        max_to_compress: int = 40,
                        reserve_recent_depth: int = 0) -> Dict:
        """
        二次摘要压缩：对 depth<=1 的旧摘要执行层级摘要（v3）。

        当 low_depth_summary_tokens > max_context * 0.15 时触发。
        将最老的 depth<=1 摘要按批次合并为 depth=2 高层摘要，
        原 depth<=1 摘要标记 is_evicted=True 不参与 assemble。

        Args:
            session_key: 会话 key
            max_to_compress: 最多处理多少批
            reserve_recent_depth: 保留最近 N 批 depth<=1 不压缩

        Returns:
            {"summarized": 批次, "nodes_evicted": 淘汰数, "depth": 2}
        """
        all_nodes = self.get_session_nodes(session_key)

        # 只取 depth<=1、未被淘汰的摘要
        target = [n for n in all_nodes if n.is_summary
                  and not n.is_evicted and n.depth <= 1
                  and n.priority <= PriorityLevel.NORMAL]
        target.sort(key=lambda n: n.timestamp)

        if len(target) <= reserve_recent_depth + 2:
            return {"summarized": 0, "nodes_evicted": 0, "reason": "摘要数不够"}

        # 保留最近 N 批不压缩
        if reserve_recent_depth > 0 and len(target) > reserve_recent_depth * 3:
            compressible = target[:-(reserve_recent_depth * 3)]
        else:
            compressible = target[:-2]  # 至少保留 2 个

        if not compressible:
            return {"summarized": 0, "nodes_evicted": 0, "reason": "无可压缩摘要"}

        # 每 SUMMARY_BATCH 个摘要合并为一个高层摘要
        SUMMARY_BATCH = 6
        batches = [compressible[i:i + SUMMARY_BATCH]
                   for i in range(0, min(len(compressible), max_to_compress * SUMMARY_BATCH), SUMMARY_BATCH)]

        if not batches:
            return {"summarized": 0, "nodes_evicted": 0}

        summarized = 0
        all_evicted = []

        for batch in batches:
            if not batch or len(batch) < 2:
                continue

            # 从 BlobArena 还原摘要的原文
            full_texts = []
            for n in batch:
                ft = self.restore_full_content(n)
                full_texts.append(ft)
            combined = "\n\n---\n\n".join(full_texts)

            # 生成高层摘要（短一些，强调跨对话主题）
            high_summary = combined[:2000] + "\n[...]" if len(combined) > 2000 else combined
            high_summary = f"[高层摘要] 以下 {len(batch)} 个旧讨论的融合概括:\n{high_summary[:1500]}"

            _blob_id = ""
            try:
                if self._blob_arena:
                    _blob_id = self._blob_arena.append_text(combined)
            except Exception:
                pass

            summary_id = f"high_summ_{session_key}_{int(time.time()*1000)}_{hashlib.md5(batch[0].node_id.encode()[:16]).hexdigest()[:6]}"
            summary_node = DAGNode(
                node_id=summary_id,
                node_type="high_level_summary",
                session_key=session_key,
                content=high_summary,
                blob_id=_blob_id,
                tokens=len(high_summary) // 4 or 1,
                priority=PriorityLevel.NORMAL,
                is_summary=True,
                depth=2,  # 二次摘要
                summary_of_ids=[n.node_id for n in batch],
                timestamp=time.time(),
            )

            # 标记原摘要为已淘汰
            batch_ids = [n.node_id for n in batch]
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                try:
                    conn.execute(
                        "UPDATE dag_nodes SET is_evicted=1 WHERE node_id IN ({})".format(
                            ",".join(["?" for _ in batch_ids])
                        ),
                        batch_ids
                    )
                    conn.commit()
                finally:
                    conn.close()

            self.add_node(summary_node)
            all_evicted.extend(batch_ids)
            summarized += 1

        return {
            "summarized": summarized,
            "nodes_evicted": len(all_evicted),
            "depth": 2,
        }

    def cognitive_compress_dag_messages(
        self, session_key,
        max_to_compress=20,
        reserve_recent=4,
        cosplay_enhanced: bool = True,
    ):
        """
        对 dag_nodes 的旧消息应用 LCM 三级压缩协议（认知级压缩）。

        将 dag_nodes 中的普通消息按对话轮次分组，
        每组压缩为一个结构化摘要节点（含 user_intent / key_findings / conclusion）。

        Args:
            session_key: 会话 key
            max_to_compress: 最多压缩多少组
            reserve_recent: 保留最近 N 轮不压缩

        Returns:
            { "summarized": 压缩的轮次数, "nodes_affected": 标记的原始节点数 }
        """
        all_nodes = self.get_session_nodes(session_key)
        # 只取非摘要、priority>=NORMAL 的消息，按时间排序
        raw_msgs = [n for n in all_nodes if not n.is_summary
                    and n.priority >= PriorityLevel.NORMAL
                    and n.node_type == DAGNodeType.MESSAGE]
        raw_msgs.sort(key=lambda n: n.timestamp)

        if len(raw_msgs) <= reserve_recent * 2:
            return {"summarized": 0, "nodes_affected": 0, "reason": "消息数不够"}

        # 保留最近 reserve_recent 轮（每轮 user+assistant=2 条）
        compressible = raw_msgs[:-(reserve_recent * 2)]
        if not compressible:
            return {"summarized": 0, "nodes_affected": 0, "reason": "最近轮次已全部保留"}

        # 按对话轮次分组（user+assistant 为一组）
        # 消息本身就是交替 user→assistant 的，按 2 条切分即可
        groups = []
        i = 0
        while i < len(compressible):
            if i + 1 < len(compressible):
                groups.append(compressible[i:i+2])
                i += 2
            else:
                groups.append([compressible[i]])
                i += 1

        # 最多压缩 max_to_compress 组
        groups = groups[:max_to_compress]
        if not groups:
            return {"summarized": 0, "nodes_affected": 0}

        # ── COSPLAY 增强: Boundary Detection → 重新分组 ──
        if cosplay_enhanced:
            try:
                from cosplay_context_adapter import get_cosplay_adapter
                _ca = get_cosplay_adapter()
                if _ca.config.boundary_enabled:
                    flat_nodes = [n for g in groups for n in g]
                    _segments = _ca.segment_nodes_by_boundary(flat_nodes)
                    if len(_segments) > 1 and len(_segments) <= len(groups) * 2:
                        # 用 boundary 分组替换简单轮次分组
                        groups = [s["nodes"] for s in _segments]
                        logger.info(f"COSPLAY boundary: {len(_segments)} segments from original {len(groups)} pairs")
            except Exception:
                pass

        summarized_count = 0
        all_affected_ids = []
        _cosplay_stats = {"contract_guided": 0, "skill_replaced": 0, "saved_tokens": 0}

        for group in groups:
            if not group:
                continue

            # v2: 从 BlobArena 还原完整原文（不截断）
            full_texts = []
            for n in group:
                full_texts.append(self.restore_full_content(n))
            full_combined = "\n\n".join(full_texts)

            # ── COSPLAY 增强: Contract-Aware + Skill Replacement ──
            _cosplay_replace = None
            _cosplay_contract = None
            if cosplay_enhanced:
                try:
                    from cosplay_context_adapter import get_cosplay_adapter
                    _ca2 = get_cosplay_adapter()
                    _keywords = self._extract_keywords(full_combined[:2000])[:5]
                    # 检查 skill replace（整段替代）
                    if _ca2.config.skill_replace_enabled:
                        _seg_for_replace = {
                            "nodes": group,
                            "combined_text": full_combined,
                            "keywords": _keywords,
                        }
                        _cosplay_replace = _ca2.try_skill_replace(_seg_for_replace)
                    # 获取 contract 指导
                    if _ca2.config.contract_enabled and _cosplay_replace is None:
                        _ci = _ca2.get_contract_instructions(_keywords)
                        if _ci:
                            _cosplay_contract = _ci
                            _cosplay_stats["contract_guided"] += 1
                except Exception:
                    pass

            # BlobArena 无损存储完整原文，SQLite 只存 memo
            _blob_id = ""
            _memo_text = ""
            try:
                if self._blob_arena:
                    _blob_id = self._blob_arena.append_text(full_combined)
                    _memo_text = generate_memo(full_combined) if generate_memo else full_combined[:200]
                else:
                    _memo_text = full_combined[:500] + "..." if len(full_combined) > 500 else full_combined
            except Exception:
                _memo_text = full_combined[:500] + "..." if len(full_combined) > 500 else full_combined

            # ── Skill Replacement: 用技能 token 替代全文 ──
            if _cosplay_replace is not None:
                _memo_text = _cosplay_replace[:500]
                _cosplay_stats["skill_replaced"] += 1
                _cosplay_stats["saved_tokens"] += max(0, len(full_combined)//4 - len(_cosplay_replace)//4)
            # ── Contract 注入: 追加关键 predicates ──
            elif _cosplay_contract:
                from cosplay_context_adapter import get_cosplay_adapter
                _ca2 = get_cosplay_adapter()
                _memo_text = _ca2.augment_summary_with_contract(_memo_text, _cosplay_contract)[:500]

            # 提取关键词
            keywords = self._extract_keywords(full_combined[:2000])[:5]

            # 创建摘要节点（存 memo + blob_id）
            summary_id = f"cog_summ_{session_key}_{int(time.time()*1000)}_{hashlib.md5(group[0].node_id.encode()[:16]).hexdigest()[:6]}"
            summary_node = DAGNode(
                node_id=summary_id,
                node_type="cognitive_summary",
                session_key=session_key,
                content=f"[认知摘要] {_memo_text}",
                blob_id=_blob_id,
                tokens=len(_memo_text) // 4 or 1,
                priority=PriorityLevel.NORMAL,
                is_summary=True,
                depth=1,  # 一次摘要
                summary_of_ids=[n.node_id for n in group],
                timestamp=time.time(),
                keywords=keywords,
                entities=keywords[:5],
            )

            # 标记原始节点已摘要
            group_ids = [n.node_id for n in group]
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                for nid in group_ids:
                    conn.execute(
                        "UPDATE dag_nodes SET is_summary=1 WHERE node_id=?",
                        (nid,)
                    )
                conn.commit()
                conn.close()

            self.add_node(summary_node)
            all_affected_ids.extend(group_ids)
            summarized_count += 1

        # ── COSPLAY 反馈: 记录压缩结果 ──
        if cosplay_enhanced and summarized_count > 0:
            try:
                from cosplay_context_adapter import get_cosplay_adapter
                _ca3 = get_cosplay_adapter()
                for group in groups:
                    _ca3.record_compact_result({
                        "session_key": session_key,
                        "n_nodes": len(group),
                        "contract_guided": _cosplay_stats["contract_guided"] > 0,
                        "skill_replaced": _cosplay_stats["skill_replaced"] > 0,
                    })
            except Exception:
                pass

        return {
            "summarized": summarized_count,
            "nodes_affected": len(all_affected_ids),
            "cosplay_enhanced": cosplay_enhanced,
            "cosplay_contract_guided": _cosplay_stats["contract_guided"],
            "cosplay_skill_replaced": _cosplay_stats["skill_replaced"],
            "cosplay_tokens_saved": _cosplay_stats["saved_tokens"],
        }

    def get_rccam_stats(self, session_key):
        """获取 R-CCAM DAG 全景统计"""
        cycles = self.get_rccam_session_cycles(session_key)
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            total_nodes = conn.execute(
                "SELECT COUNT(*) FROM rccam_nodes WHERE session_key=?",
                (session_key,)).fetchone()[0] or 0
            total_bytes = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(content)), 0) FROM rccam_nodes WHERE session_key=?",
                (session_key,)).fetchone()[0] or 0
            compressed = conn.execute(
                "SELECT COUNT(*) FROM rccam_nodes WHERE session_key=? AND is_compressed=1",
                (session_key,)).fetchone()[0] or 0
            conn.close()
        return {
            "rccam_nodes": total_nodes, "total_bytes": total_bytes,
            "compressed_nodes": compressed, "total_cycles": len(cycles),
        }

    def _extract_keywords(self, text):
        """简易关键词提取"""
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', str(text)[:2000])
        word_counts = Counter(w for w in words if len(w) > 1)
        return [w for w, _ in word_counts.most_common(10)]

    def get_db_size(self) -> int:
        """获取数据库大小（字节）"""
        try:
            return os.path.getsize(self.db_path)
        except OSError:
            return 0

    # ========================================================================
    # 时序知识图谱集成
    # ========================================================================

    def add_temporal_edge_to_session(self, session_key: str,
                                      src_entity: str, dst_entity: str,
                                      relation: str, timestamp: float = None):
        """
        添加一条时序 KG 边并关联到 DAG 会话。

        如果 temporal_kg 未加载，自动懒加载。
        """
        try:
            tkg = self._get_temporal_kg()
            edge_id = tkg.add_temporal_edge(
                src_entity, dst_entity, relation,
                timestamp=timestamp or time.time(),
                session_key=session_key
            )
            return edge_id
        except Exception as e:
            logger.warning(f"add_temporal_edge_to_session 失败: {e}")
            return None

    def get_temporal_graph_for_session(self, session_key: str) -> dict:
        """
        获取会话的时序子图。
        """
        try:
            tkg = self._get_temporal_kg()
            return tkg.get_session_graph(session_key)
        except Exception as e:
            logger.warning(f"get_temporal_graph_for_session 失败: {e}")
            return {"entities": [], "edges": [], "stats": {"entity_count": 0, "edge_count": 0}}

    def get_session_community(self, session_key: str, min_edges: int = 2) -> list:
        """
        获取会话的社区聚类结果。
        """
        try:
            tkg = self._get_temporal_kg()
            session_graph = tkg.get_session_graph(session_key)
            if session_graph['stats']['edge_count'] < min_edges:
                return []
            return tkg.build_community(min_edges=min_edges)
        except Exception as e:
            logger.warning(f"get_session_community 失败: {e}")
            return []

    def _get_temporal_kg(self):
        """懒加载 TemporalKnowledgeGraph"""
        if not hasattr(self, '_tkg') or self._tkg is None:
            from temporal_kg import TemporalKnowledgeGraph
            tkg_db = os.path.join(os.path.dirname(self.db_path), 'temporal_kg.db')
            self._tkg = TemporalKnowledgeGraph(db_path=tkg_db)
        return self._tkg

    def _get_spatial_graph(self):
        """懒加载 SpatialTopologyGraph"""
        if not hasattr(self, '_sg') or self._sg is None:
            try:
                from spatial_topology import SpatialTopologyGraph
                sg_db = os.path.join(os.path.dirname(self.db_path), 'spatial_topology.db')
                self._sg = SpatialTopologyGraph(db_path=sg_db)
            except Exception as e:
                logger.warning(f"SpatialTopologyGraph 懒加载失败: {e}")
                self._sg = False
        return self._sg if self._sg else None

    # ========================================================================
    # AriGraph 空间拓扑整合（scene 感知方法）
    # ========================================================================

    def get_nearest_scene_nodes(self, session_key: str, scene_label: str,
                                 max_distance: int = 2, limit: int = 10) -> list:
        """
        获取指定场景附近的 DAG 节点。

        通过 spatial_topology 找到场景的拓扑邻居，
        然后从 dag_nodes 中过滤出这些邻居对应的消息节点。

        Args:
            session_key: DAG 会话 key
            scene_label: 场景名称
            max_distance: 最大图遍历距离
            limit: 最大返回数量

        Returns:
            节点列表（含内容、metadata 等）
        """
        try:
            from spatial_topology import get_spatial_graph
            sg = get_spatial_graph()
        except Exception:
            return []

        # 获取场景邻居
        try:
            neighbors = sg.get_scene_neighbors(scene_label, depth=max_distance)
            scene_node = sg.get_scene(scene_label)
        except Exception as e:
            logger.warning(f"get_nearest_scene_nodes: spatial lookup failed: {e}")
            return []

        # 收集所有相关 label
        related_labels = {scene_label}
        if scene_node:
            related_labels.add(scene_node.label)
            for a in scene_node.aliases:
                related_labels.add(a)
        for n in neighbors:
            related_labels.add(n.label)
            for a in n.aliases:
                related_labels.add(a)

        # 从 DAG 中找包含这些 label 的节点
        all_nodes = self.get_session_nodes(session_key, limit=limit * 5)
        matched = []

        for node in all_nodes:
            content = node.content or ""
            # 检查 content 是否提到相关场景
            label_score = 0
            for label in related_labels:
                if label.lower() in content.lower():
                    # label 越长匹配越精确
                    label_score = max(label_score, len(label) / 20.0)

            if label_score > 0:
                matched.append({
                    "node_id": node.node_id,
                    "content": content[:500],
                    "label_score": label_score,
                    "score": node.importance_score * 0.5 + label_score * 0.5,
                    "timestamp": node.timestamp,
                    "keywords": node.keywords,
                    "entities": node.entities,
                    "scene_label": scene_label,
                })

        matched.sort(key=lambda x: -x["score"])
        return matched[:limit]

    def get_scene_tree(self, session_key: str) -> dict:
        """
        获取会话的场景树（层级结构）。

        通过 spatial_topology 获取所有 scene 类型节点，
        按 parent_id 组织为树形结构。

        Returns:
            {
                "tree": [{label, type, children: [...], neighbors: [...]}],
                "roots": [root scenes],
                "flat": [all scenes flat],
            }
        """
        try:
            from spatial_topology import get_spatial_graph
            sg = get_spatial_graph()
        except Exception:
            return {"tree": [], "roots": [], "flat": []}

        try:
            # 从 spatial_topology 获取所有 scene 节点
            with sg._lock:
                conn = sqlite3.connect(sg.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM spatial_nodes WHERE node_type IN ('scene', 'context') ORDER BY label"
                )
                rows = [dict(r) for r in cursor.fetchall()]
                conn.close()

            # 构建树
            nodes_map = {}
            for r in rows:
                nid = r["node_id"]
                try:
                    children_ids = json.loads(r["children_ids"]) if isinstance(r["children_ids"], str) else r["children_ids"]
                except Exception:
                    children_ids = []
                try:
                    aliases = json.loads(r["aliases"]) if isinstance(r["aliases"], str) else r["aliases"]
                except Exception:
                    aliases = []

                # 获取邻居标签
                neighbor_labels = []
                try:
                    nn = sg.get_scene_neighbors(r["label"], depth=1)
                    neighbor_labels = [n.label for n in nn if n.label != r["label"]]
                except Exception:
                    pass

                node = {
                    "node_id": nid,
                    "label": r["label"],
                    "type": r["node_type"],
                    "parent_id": r.get("parent_id"),
                    "children": [],
                    "children_labels": [],
                    "aliases": aliases,
                    "neighbors": neighbor_labels,
                }
                nodes_map[nid] = node

                # 收集子节点 label
                for cid in children_ids:
                    if cid in nodes_map:
                        node["children_labels"].append(nodes_map[cid]["label"])

            # 构建 parent→children 关系
            for nid, node in nodes_map.items():
                if node["parent_id"] and node["parent_id"] in nodes_map:
                    parent = nodes_map[node["parent_id"]]
                    parent["children"].append(node)

            # 根节点（无 parent 的 scene 节点）
            roots = [n for n in nodes_map.values() if n["parent_id"] is None or n["parent_id"] not in nodes_map]

            flat = [{"label": n["label"], "type": n["type"], "neighbors": n["neighbors"]}
                    for n in nodes_map.values()]

            return {
                "tree": list(nodes_map.values()),
                "roots": [{"label": r["label"], "type": r["type"], "children_labels": r["children_labels"]}
                          for r in roots],
                "flat": flat,
            }

        except Exception as e:
            logger.warning(f"get_scene_tree failed: {e}")
            return {"tree": [], "roots": [], "flat": []}

    def get_session_navigation_history(self, session_key: str) -> list:
        """
        获取会话的导航历史。

        从 spatial_topology 的 navigation_records 中读取
        与当前会话相关的导航记录。

        Returns:
            导航记录列表
        """
        try:
            from spatial_topology import get_spatial_graph
            sg = get_spatial_graph()
        except Exception:
            return []

        try:
            with sg._lock:
                conn = sqlite3.connect(sg.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    """SELECT nr.*, sn_from.label as from_label, sn_to.label as to_label
                       FROM navigation_records nr
                       LEFT JOIN spatial_nodes sn_from ON nr.from_node = sn_from.node_id
                       LEFT JOIN spatial_nodes sn_to ON nr.to_node = sn_to.node_id
                       ORDER BY nr.timestamp DESC
                       LIMIT 20"""
                )
                rows = []
                for r in cursor.fetchall():
                    dr = dict(r)
                    try:
                        path = json.loads(dr["path"]) if isinstance(dr["path"], str) else dr["path"]
                    except Exception:
                        path = []
                    rows.append({
                        "record_id": dr["record_id"],
                        "from_label": dr["from_label"],
                        "to_label": dr["to_label"],
                        "path": path,
                        "context": dr.get("context", ""),
                        "timestamp": dr["timestamp"],
                    })
                conn.close()

            # 如果有 session 关键词过滤
            if session_key:
                # 尝试匹配 context 字段包含 session key 的记录
                filtered = [r for r in rows if session_key in r.get("context", "")]
                if filtered:
                    return filtered

            return rows[:10]

        except Exception as e:
            logger.warning(f"get_session_navigation_history failed: {e}")
            return []

    def add_message_with_scene(
        self,
        session_key: str,
        role: str,
        content: str,
        scene_label: Optional[str] = None,
        tokens: int = 0,
        importance: float = 0.5,
        emotion: float = 0.0,
        priority: int = PriorityLevel.NORMAL,
        keywords: Optional[List[str]] = None,
        entities: Optional[List[str]] = None,
        parent_ids: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> str:
        """
        添加消息节点并关联场景标签。

        与 add_message 相同，但接受 scene_label 参数并存到 metadata。
        """
        _meta = {"role": role}
        if metadata:
            _meta.update(metadata)
        if scene_label:
            _meta["scene_label"] = scene_label

        return self.add_message(
            session_key=session_key,
            role=role,
            content=content,
            tokens=tokens,
            importance=importance,
            emotion=emotion,
            priority=priority,
            keywords=keywords,
            entities=entities,
            parent_ids=parent_ids,
            metadata=_meta,
        )

    # ========================================================================
    # LASAR Cognitive Map 集成
    # ========================================================================

    def _get_cognitive_map(self):
        """懒加载 LASAR CognitiveMap"""
        if not hasattr(self, '_cm') or self._cm is None:
            try:
                from cognitive_map import CognitiveMap
                cm_db = os.path.join(os.path.dirname(self.db_path), 'cognitive_map.db')
                self._cm = CognitiveMap(db_path=cm_db)
            except Exception as e:
                logger.warning(f"CognitiveMap 懒加载失败: {e}")
                self._cm = None
        return self._cm

    def add_cognitive_anchor(self, node_id: str) -> Optional[str]:
        """
        为 DAG 节点创建认知锚点。

        提取节点的 content, 用 CognitiveMap 生成锚点向量并持久化。

        Args:
            node_id: DAG 节点 ID

        Returns:
            锚点 ID, 失败返回 None
        """
        cm = self._get_cognitive_map()
        if not cm:
            return None

        # 从数据库读取节点
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM dag_nodes WHERE node_id = ?", (node_id,))
                row = cursor.fetchone()
                conn.close()
            except Exception:
                return None

        if not row:
            return None

        node_data = dict(row)
        content = node_data.get('content', '')
        session_key = node_data.get('session_key', '')

        if not content:
            return None

        return cm.add_anchor(node_id, content[:500], session_key)

    def get_cognitive_context_for_session(self, session_key: str) -> Dict:
        """
        获取会话的认知地图上下文。

        返回当前会话在认知空间的位置、密度、附近锚点等信息。

        Args:
            session_key: 会话 Key

        Returns:
            认知上下文 dict
        """
        cm = self._get_cognitive_map()
        if not cm:
            return {"has_cognitive_map": False}

        # 获取该会话最近的节点
        recent_nodes = self.get_session_nodes(session_key, limit=5)
        if not recent_nodes:
            return {"has_cognitive_map": True, "no_recent_nodes": True}

        # 用最新内容的投影作为当前认知位置
        latest_content = recent_nodes[0].content or ""
        vec = cm.compute_anchor_vector(latest_content[:500])
        density = cm.get_anchor_density(vec)
        nearby = cm.get_nearby_anchors(vec, k=5)

        # 执行三类认知 query
        queries_result = cm.run_cognitive_queries(latest_content[:300], session_key)

        return {
            "has_cognitive_map": True,
            "cognitive_density": round(density, 3),
            "nearby_anchors": len(nearby),
            "nearby_contexts": [a.context[:100] for a in nearby[:3]],
            "retrospective": queries_result.get("retrospective", ""),
            "introspective": queries_result.get("introspective", ""),
            "prospective": queries_result.get("prospective", ""),
            "landscape": cm.get_cognitive_landscape(region=vec),
        }

    def get_nearby_nodes_in_cognitive_space(self, node_id: str,
                                             k: int = 5) -> List[Dict]:
        """
        在认知空间中找与指定节点最接近的 N 个节点。

        不是语义相似，而是空间接近性（位置接近）。

        Args:
            node_id: 源节点 ID
            k: 返回数量

        Returns:
            [{node_id, content, similarity, distance}, ...]
        """
        cm = self._get_cognitive_map()
        if not cm:
            return []

        # 查该节点是否有锚点
        # 简单方法: 计算该节点的内容向量
        with self._lock:
            try:
                conn = sqlite3.connect(self.db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT content, session_key FROM dag_nodes WHERE node_id = ?",
                    (node_id,))
                row = cursor.fetchone()
                conn.close()
            except Exception:
                return None

        if not row:
            return []

        content = row['content']
        session_key = row['session_key']
        vec = cm.compute_anchor_vector(content[:500])

        # 用认知 map 取附近锚点
        nearby = cm.get_nearby_anchors(vec, k=k)

        results = []
        for anchor in nearby:
            if anchor.node_id == node_id:
                continue
            sim = cm.spatial_similarity(vec, anchor.anchor_vector)
            results.append({
                "node_id": anchor.node_id,
                "content": anchor.context[:200],
                "similarity": round(sim, 4),
                "anchor_id": anchor.anchor_id,
            })

        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:k]

    # ════════════════════════════════════════════════════
    # MemGAS: KnowledgeAsset 查询
    # ════════════════════════════════════════════════════

    def get_assets_for_session(self, session_key: str, limit: int = 20) -> List[Dict]:
        """
        获取指定 session 在 AssetRegistry 中对应的知识资产。
        
        从 DAG 库读取该 session 的长内容节点（>200 字符），
        在 AssetRegistry 中搜索对应资产并返回。
        如果资产不存在，即时创建。

        Args:
            session_key: DAG 会话 key
            limit: 最大返回资产数

        Returns:
            [{asset_dict}, ...]
        """
        assets = []
        try:
            from knowledge_asset import get_asset_registry, create_memory_asset, AssociationEdge, AssetType
            from multi_granularity import MultiGranularityExtractor, GMMAssociator

            reg = get_asset_registry()
            extractor = MultiGranularityExtractor()
            associator = GMMAssociator(n_components=5)

            # 从 DAG 读该 session 的节点
            nodes = self.get_session_nodes(session_key, limit=limit * 2)
            if not nodes:
                # 再试 rccam_nodes
                try:
                    with self._lock:
                        conn = sqlite3.connect(self.db_path)
                        conn.row_factory = sqlite3.Row
                        cursor = conn.execute(
                            "SELECT node_id, content, keywords, node_type, timestamp "
                            "FROM rccam_nodes WHERE session_key=? "
                            "ORDER BY timestamp DESC LIMIT ?",
                            (session_key, limit * 2)
                        )
                        rows = [dict(r) for r in cursor.fetchall()]
                        conn.close()
                    for row in rows:
                        content = row.get('content', '') or ''
                        if len(content) < 200:
                            continue
                        content = self.restore_full_content(DAGNode(
                            node_id=row['node_id'],
                            node_type=row.get('node_type', 'rccam'),
                            session_key=session_key,
                            content=content,
                            blob_id='',
                        )) if hasattr(self, 'restore_full_content') else content

                        # 搜索已注册资产
                        existing = reg.search(content[:100], top_k=1)
                        if existing:
                            assets.append(existing[0].to_dict(include_raw=False))
                        else:
                            # 创建新资产
                            asset = create_memory_asset(
                                memory_id=row['node_id'],
                                raw_content=content[:2000],
                                category='rccam',
                                source=f'rccam_{row.get("node_type", "")}',
                            )
                            asset.multi_granularity = extractor.extract(content)

                            # 关联现有资产
                            all_ids = reg.list_ids()
                            existing_texts = [
                                reg.get(aid).raw_content[:500]
                                for aid in all_ids[:100] if reg.get(aid)
                            ]
                            if existing_texts:
                                associator.fit(existing_texts + [content[:500]])
                                edges = associator.associate(content[:500])
                                for target_id, relation, weight in edges:
                                    asset.association_graph.append(
                                        AssociationEdge(
                                            target_asset_id=target_id,
                                            relation=relation,
                                            weight=weight,
                                        )
                                    )

                            reg.register(asset)
                            assets.append(asset.to_dict(include_raw=False))

                except Exception as _re:
                    logger.debug(f"get_assets_for_session rccam: {_re}")
                return assets[:limit]

            for node in nodes:
                content = self.restore_full_content(node) if hasattr(self, 'restore_full_content') else node.content
                if not content or len(content) < 200:
                    continue

                # 搜索已注册资产
                existing = reg.search(content[:100], top_k=1)
                if existing:
                    assets.append(existing[0].to_dict(include_raw=False))
                else:
                    # 创建新资产
                    asset = create_memory_asset(
                        memory_id=node.node_id,
                        raw_content=content[:2000],
                        tags=node.keywords if hasattr(node, 'keywords') and node.keywords else [],
                        category=node.node_type if hasattr(node, 'node_type') else 'dag',
                        source=f'dag_nodes_{node.node_type if hasattr(node, "node_type") else "unknown"}',
                    )
                    asset.multi_granularity = extractor.extract(content)

                    # 关联
                    all_ids = reg.list_ids()
                    existing_texts = [
                        reg.get(aid).raw_content[:500]
                        for aid in all_ids[:100] if reg.get(aid)
                    ]
                    if existing_texts:
                        associator.fit(existing_texts + [content[:500]])
                        edges = associator.associate(content[:500])
                        for target_id, relation, weight in edges:
                            asset.association_graph.append(
                                AssociationEdge(
                                    target_asset_id=target_id,
                                    relation=relation,
                                    weight=weight,
                                )
                            )

                    reg.register(asset)
                    assets.append(asset.to_dict(include_raw=False))

            return assets[:limit]

        except Exception as e:
            logger.warning(f"get_assets_for_session failed: {e}")
            return []

    def close(self):
        """关闭资源"""
        pass

    # ====================================================================
    # MemGPT 风格分页 + 中断
    # ====================================================================

    def get_paging_stats(self) -> Dict:
        """
        获取活跃/归档分页统计

        Returns:
            {
                "active_nodes": int,
                "archived_nodes": int,
                "total_nodes": int,
                "paging_active": bool,
                "page_size": int,
            }
        """
        # 通过 PageManager 获取统计
        pm = PageManager(self)
        return pm.get_stats()

    def compact_with_paging(self, force: bool = False) -> Dict:
        """
        先分页再压缩 — MemGPT 风格

        1. 检查节点数是否触发了分页阈值
        2. 如果触发，先执行 page_out 将旧节点换出
        3. 再执行 auto_summarize 压缩

        Args:
            force: 是否强制分页+压缩（忽略阈值检查）

        Returns:
            {
                "paged_out": int,
                "summarized": int,
                "active_nodes": int,
                "archived_nodes": int,
            }
        """
        pm = PageManager(self)
        return pm.compact_with_paging(force=force)

    def _interrupt_handler(self, session_key: str) -> Dict:
        """
        系统资源不足时的中断处理

        触发条件（任一即可）：
        - DAG 节点总数 > 500
        - 内存占用 > 80%（通过系统资源估算）
        - compact_with_paging 累计失败 3 次

        中断流程：
        1. 先做 page_out 把最旧的 30% 节点换出
        2. 对剩余活跃节点做摘要合并
        3. 返回恢复状态

        Returns:
            {"interrupted": bool, "reason": str, "details": Dict}
        """
        try:
            pm = PageManager(self)

            # 检查是否真的需要中断
            all_nodes = self.get_session_nodes(session_key) if session_key else []
            total_nodes = len(all_nodes)

            # 估算内存压力（简单模型）
            memory_pressure = False
            import psutil
            try:
                mem = psutil.virtual_memory()
                memory_pressure = mem.percent > 80.0
            except (ImportError, AttributeError):
                # psutil 可能不可用，用节点数估算
                memory_pressure = total_nodes > 300

            needs_interrupt = (total_nodes > 500 or memory_pressure)

            if not needs_interrupt:
                return {"interrupted": False, "reason": "资源充足，无需中断"}

            # 1. 换出最旧的 30% 节点
            all_sorted = sorted(all_nodes, key=lambda n: n.timestamp)
            page_out_targets = all_sorted[:max(1, len(all_sorted) // 3)]
            archived = pm._batch_page_out(
                [n.node_id for n in page_out_targets],
                session_key
            )

            # 2. 对活跃节点做摘要
            result = self.auto_summarize(session_key, batch_size=20)

            stats = pm.get_stats()

            logger.info(
                f"中断处理完成: "
                f"archived={archived}, "
                f"summarized={result.get('summarized', 0)}, "
                f"active={stats['active_nodes']}"
            )

            return {
                "interrupted": True,
                "reason": f"节点数 {total_nodes} > 500 或内存压力",
                "details": {
                    "archived_nodes": archived,
                    "summarized_batches": result.get('summarized', 0),
                    "active_now": stats['active_nodes'],
                    "archived_now": stats['archived_nodes'],
                }
            }

        except Exception as e:
            logger.error(f"中断处理失败: {e}")
            return {
                "interrupted": False,
                "reason": f"中断处理异常: {e}",
                "details": {}
            }


# ============================================================================
# PageManager — MemGPT 风格分页管理
# ============================================================================

class PageManager:
    """
    MemGPT 风格分页管理器

    在 DAG 上层添加分页层：
    - 活跃页（active）：最近 N 个节点，直接参与上下文组装
    - 换出页（archived）：归档节点，保留元数据和内容摘要
    - 自动阈值触发：节点数 > page_threshold 时自动分页

    不修改 DAG 数据库表结构，通过元数据标记分页状态。
    原始节点内容在被 page_out 后替换为摘要，但完整内容
    可通过 page_in 恢复（存在备份字段中）。
    """

    def __init__(
        self,
        dag_manager: 'DAGContextManager',
        page_threshold: int = 300,
        recent_keep_count: int = 50,
        archive_summary_max_tokens: int = 200,
    ):
        """
        Args:
            dag_manager: DAGContextManager 实例
            page_threshold: 触发分页的节点阈值
            recent_keep_count: 保留为活跃的最近节点数
            archive_summary_max_tokens: 归档摘要最大 token 数
        """
        self.dag = dag_manager
        self.page_threshold = page_threshold
        self.recent_keep_count = recent_keep_count
        self.archive_summary_max_tokens = archive_summary_max_tokens

        # 熔断计数器
        self._consecutive_failures = 0
        self._max_failures = 5
        self._circuit_breaker_timestamp = 0.0
        self._circuit_breaker_cooldown = 5.0  # 5 秒自动恢复

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def _should_page(self, count: int) -> bool:
        """
        判断是否需要分页

        Args:
            count: 当前节点数

        Returns:
            True 如果节点数超过阈值且未触发熔断
        """
        if time.time() < self._circuit_breaker_timestamp:
            return False
        return count > self.page_threshold

    def _page_in(self, node_id: str) -> bool:
        """
        从归档恢复到活跃

        将归档节点的原始内容从 metadata['_archived_content']
        恢复到 content 字段。

        Args:
            node_id: 节点 ID

        Returns:
            True 如果恢复成功
        """
        try:
            conn = sqlite3.connect(self.dag.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT content, metadata FROM dag_nodes WHERE node_id = ?",
                (node_id,)
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                return False

            metadata = json.loads(row['metadata']) if isinstance(row['metadata'], str) else row['metadata']

            # 检查是否有归档备份
            archived_content = metadata.get('_archived_content')
            if not archived_content:
                return False  # 未被归档，无需恢复

            # 恢复原始内容，清除归档标记
            metadata.pop('_archived_content', None)
            metadata['_archived'] = False

            conn = sqlite3.connect(self.dag.db_path)
            conn.execute(
                "UPDATE dag_nodes SET content = ?, metadata = ? WHERE node_id = ?",
                (archived_content, json.dumps(metadata), node_id)
            )
            conn.commit()
            conn.close()

            logger.info(f"Page in: {node_id}")
            self._consecutive_failures = 0
            return True

        except Exception as e:
            logger.warning(f"page_in 失败 ({node_id}): {e}")
            self._consecutive_failures += 1
            self._check_circuit_breaker()
            return False

    def _page_out(self, node_ids: List[str]) -> int:
        """
        把指定节点换出到归档

        1. 提取内容摘要（保留关键信息）
        2. 原始内容移到 metadata['_archived_content']
        3. content 字段替换为摘要
        4. metadata['_archived'] = True

        Args:
            node_ids: 要归档的节点 ID 列表

        Returns:
            成功归档的节点数
        """
        return self._batch_page_out(node_ids, None)

    def _batch_page_out(self, node_ids: List[str], session_key: Optional[str]) -> int:
        """
        批量换出节点

        Args:
            node_ids: 节点 ID 列表
            session_key: 会话 key（用于摘要上下文）

        Returns:
            成功归档的节点数
        """
        if not node_ids:
            return 0

        success_count = 0

        with self.dag._lock:
            conn = sqlite3.connect(self.dag.db_path)
            conn.row_factory = sqlite3.Row

            for node_id in node_ids:
                try:
                    cursor = conn.execute(
                        "SELECT content, metadata, node_type, priority FROM dag_nodes WHERE node_id = ?",
                        (node_id,)
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue

                    # 跳过已归档、已摘要、人格节点
                    metadata = json.loads(row['metadata']) if isinstance(row['metadata'], str) else row['metadata']
                    if metadata.get('_archived', False):
                        continue
                    if row['node_type'] == DAGNodeType.SUMMARY:
                        continue
                    if row['priority'] == PriorityLevel.CRITICAL:
                        continue

                    original_content = row['content']

                    # 生成简单摘要
                    summary = self._make_archive_summary(original_content)

                    # 保存原始内容到 metadata
                    metadata['_archived_content'] = original_content
                    metadata['_archived'] = True
                    metadata['_archived_at'] = time.time()

                    # 用摘要替换内容
                    conn.execute(
                        "UPDATE dag_nodes SET content = ?, metadata = ? WHERE node_id = ?",
                        (summary, json.dumps(metadata), node_id)
                    )
                    success_count += 1

                except Exception as e:
                    logger.warning(f"page_out 失败 ({node_id}): {e}")
                    continue

            conn.commit()
            conn.close()

        self._consecutive_failures = 0
        logger.info(f"Batch page out: {success_count}/{len(node_ids)} nodes")
        return success_count

    def compact_with_paging(self, force: bool = False) -> Dict:
        """
        先分页再压缩 — 主入口

        1. 获取所有会话的节点统计
        2. 检查是否需要分页
        3. 如果触发：page_out 最旧的一批 → 再 auto_summarize

        Args:
            force: 跳过阈值检查强制执行

        Returns:
            {
                "paged_out": int,
                "summarized": int,
                "active_nodes": int,
                "archived_nodes": int,
            }
        """
        result = {"paged_out": 0, "summarized": 0, "active_nodes": 0, "archived_nodes": 0}

        try:
            # 获取所有会话的节点计数
            conn = sqlite3.connect(self.dag.db_path)
            cursor = conn.execute(
                "SELECT session_key, COUNT(*) as cnt FROM dag_nodes GROUP BY session_key"
            )
            session_counts = {r[0]: r[1] for r in cursor.fetchall()}
            conn.close()

            total_nodes = sum(session_counts.values())

            if not force and not self._should_page(total_nodes):
                # 未触发分页阈值，但尝试常规压缩
                for sk in session_counts:
                    self.dag.auto_summarize(sk, batch_size=10)
                return result

            # 分页：对每个会话换出最旧的节点
            total_paged = 0
            total_summarized = 0

            for session_key, count in session_counts.items():
                if count <= self.recent_keep_count:
                    continue

                # 获取该会话的节点（时间升序 = 最旧在前）
                all_nodes = self.dag.get_session_nodes(session_key)
                all_nodes.sort(key=lambda n: n.timestamp)

                # 保留最近 recent_keep_count 条，其余 page_out
                if len(all_nodes) > self.recent_keep_count:
                    to_archive = [
                        n.node_id for n in all_nodes[:-self.recent_keep_count]
                        if n.priority != PriorityLevel.CRITICAL
                        and n.node_type != DAGNodeType.SUMMARY
                    ]
                    if to_archive:
                        archived = self._batch_page_out(to_archive, session_key)
                        total_paged += archived

                # 压缩（摘要归档节点以外的消息）
                need_compact, _ = self.dag.should_compact(session_key)
                if need_compact or force:
                    r = self.dag.auto_summarize(session_key, batch_size=15)
                    total_summarized += r.get('summarized', 0)

            stats = self.get_stats()
            result.update({
                "paged_out": total_paged,
                "summarized": total_summarized,
                "active_nodes": stats['active_nodes'],
                "archived_nodes": stats['archived_nodes'],
            })

        except Exception as e:
            logger.error(f"compact_with_paging 失败: {e}")
            self._consecutive_failures += 1
            self._check_circuit_breaker()

        return result

    def get_stats(self) -> Dict:
        """获取活跃/归档分页统计"""
        try:
            conn = sqlite3.connect(self.dag.db_path)

            # 活跃节点（未归档）
            cursor = conn.execute(
                """SELECT COUNT(*) FROM dag_nodes
                   WHERE json_extract(metadata, '$._archived') IS NULL
                      OR json_extract(metadata, '$._archived') = 0
                      OR json_extract(metadata, '$._archived') = 'false'"""
            )
            active = cursor.fetchone()[0]

            # 归档节点
            cursor = conn.execute(
                """SELECT COUNT(*) FROM dag_nodes
                   WHERE json_extract(metadata, '$._archived') = 1
                      OR json_extract(metadata, '$._archived') = 'true'"""
            )
            archived = cursor.fetchone()[0]

            # 总节点
            cursor = conn.execute("SELECT COUNT(*) FROM dag_nodes")
            total = cursor.fetchone()[0]

            conn.close()

            return {
                "active_nodes": active,
                "archived_nodes": archived,
                "total_nodes": total,
                "paging_active": archived > 0,
                "page_threshold": self.page_threshold,
                "recent_keep_count": self.recent_keep_count,
                "circuit_breaker_active": time.time() < self._circuit_breaker_timestamp,
            }

        except Exception as e:
            logger.warning(f"PageManager get_stats 失败: {e}")
            return {
                "active_nodes": 0,
                "archived_nodes": 0,
                "total_nodes": 0,
                "paging_active": False,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _make_archive_summary(self, content: str) -> str:
        """
        为归档内容生成简要摘要

        保留前 100 字符作为摘要，标记为归档。
        完整的原始内容通过 page_in 恢复。
        """
        if len(content) <= 150:
            return content

        lines = content.split('\n')
        important_lines = [l for l in lines if len(l.strip()) > 20]

        if not important_lines:
            return content[:100] + "..."

        # 取前几行
        summary_lines = important_lines[:3]
        summary = " | ".join(l.strip()[:50] for l in summary_lines)

        if len(summary) > self.archive_summary_max_tokens * 4:
            summary = summary[:self.archive_summary_max_tokens * 4 - 3] + "..."

        return f"[归档] {summary}"

    def _check_circuit_breaker(self):
        """检查熔断器"""
        if self._consecutive_failures >= self._max_failures:
            self._circuit_breaker_timestamp = time.time() + self._circuit_breaker_cooldown
            logger.warning(
                f"PageManager 熔断 {self._circuit_breaker_cooldown}s "
                f"({self._consecutive_failures} 次连续失败)"
            )
            self._consecutive_failures = 0


# ============================================================================
# 辅助函数
# ============================================================================

def _extract_keywords(text: str) -> List[str]:
    """简易关键词提取（无需 numpy / jieba）"""
    words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text)
    word_counts = Counter(w for w in words if len(w) > 1)
    return [w for w, _ in word_counts.most_common(15)]


_dag_instances: Dict[str, DAGContextManager] = {}


def get_dag_manager(
    session_key: str = "default",
    **kwargs,
) -> DAGContextManager:
    """获取 DAG 上下文管理器实例（单例缓存）"""
    global _dag_instances
    instance = _dag_instances.get(session_key)
    if instance is None:
        instance = DAGContextManager(**kwargs)
        _dag_instances[session_key] = instance
    return instance


# ============================================================================
# Tree-of-Thought + MemGPT 集成入口
# ============================================================================


def multi_path_search(query: str, context: str = "", llm=None) -> Dict:
    """
    ToT + Memory Paging 统一调用

    执行 Tree-of-Thought 搜索后，自动触发 DagContextManager
    的 compact_with_paging 进行分页管理（如果节点超阈值）。

    Args:
        query: 搜索问题
        context: 可选的背景上下文
        llm: LLM Flash 客户端（可选，从 xiaoyi_claw_api 自动获取）

    Returns:
        {
            "search_result": Dict,        # ToT 搜索结果
            "paging_result": Dict,         # MemGPT 分页结果
            "success": bool,
        }
    """
    result = {"search_result": {}, "paging_result": {}, "success": False}

    try:
        # 1. Tree-of-Thought 搜索
        from tree_of_thought import TreeOfThought

        tot = TreeOfThought(llm_flash=llm)
        search_result = tot.search(query, context)
        result["search_result"] = search_result
        result["success"] = True

        # 2. MemGPT 分页（由 dag_context_manager 自动触发）
        try:
            dag = get_dag_manager()
            paging_result = dag.compact_with_paging(force=False)
            result["paging_result"] = paging_result
        except Exception as pe:
            logger.warning(f"multi_path_search paging 失败: {pe}")
            result["paging_result"] = {"error": str(pe)}

    except Exception as e:
        logger.error(f"multi_path_search 失败: {e}")
        result["search_result"] = {"error": str(e)}

    return result


__all__ = [
    'DAGContextManager',
    'DAGNode',
    'DAGNodeType',
    'PhaseNodeType',
    'PriorityLevel',
    'PageManager',
    'get_dag_manager',
    'multi_path_search',
]
