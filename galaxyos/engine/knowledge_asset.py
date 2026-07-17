#!/usr/bin/env python3
"""
knowledge_asset.py — Unified KnowledgeAsset model that unifies skill + memory

MemGAS-SkVM 集成的核心数据模型。

KnowledgeAsset:
  - raw_content: 原始内容（技能定义、记忆文本等）
  - multi_granularity: 多粒度表示 (session/turn/summary/keyword)
  - capability_profile: 能力画像（SkVM 26 维原语）
  - association_graph: 关联图边
  - compiled_artifact: 编译后产物（SkVM compiler 产出）
  - asset_type: "skill" | "memory"

AssetRegistry:
  - CRUD for assets
  - query by capability/category
  - store/load from BlobArena
"""

import json
import time
import logging
import threading
import os
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


# ── Types ──

class AssetType(str, Enum):
    SKILL = "skill"
    MEMORY = "memory"
    PLUGIN = "plugin"
    KNOWLEDGE = "knowledge"


class GranularityLevel(str, Enum):
    SESSION = "session_level"     # 完整文本
    TURN = "turn_level"           # 分句/分段
    SUMMARY = "summary_level"     # 摘要
    KEYWORD = "keyword_level"     # 关键词


@dataclass
class AssociationEdge:
    """关联图中的一条边"""
    target_asset_id: str
    relation: str           # "similar_to", "depends_on", "complements", "conflicts", ...
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_asset_id": self.target_asset_id,
            "relation": self.relation,
            "weight": self.weight,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AssociationEdge":
        return cls(
            target_asset_id=data["target_asset_id"],
            relation=data["relation"],
            weight=data.get("weight", 1.0),
            metadata=data.get("metadata", {}),
        )


@dataclass
class KnowledgeAsset:
    """
    统一知识资产模型——统一 Skill 和 Memory

    关键设计：
    - raw_content: 保留原始内容，不做截断
    - multi_granularity: 缓存多粒度表示，避免重复计算
    - capability_profile: 能力画像字典（由 CapabilityRegistry 生成）
    - association_graph: 关联图边列表（由 GMMAssociator 维护）
    - compiled_artifact: 编译产物字典（由 SkillCompiler 生成）
    - asset_type: "skill" / "memory" / "plugin" / "knowledge"
    """
    asset_id: str
    asset_type: AssetType
    raw_content: str
    multi_granularity: Dict[str, Any] = field(default_factory=dict)
    capability_profile: Dict[str, Any] = field(default_factory=dict)
    association_graph: List[AssociationEdge] = field(default_factory=list)
    compiled_artifact: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    version: int = 1
    tags: List[str] = field(default_factory=list)
    category: str = ""
    source: str = ""               # e.g. "user_conversation", "skill_scan", "clawhub"
    access_count: int = 0
    importance: float = 0.5        # 0-1, 由 MemGAS 维护

    def to_dict(self, include_raw: bool = True) -> Dict[str, Any]:
        d = {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type.value,
            "multi_granularity": self.multi_granularity,
            "capability_profile": self.capability_profile,
            "association_graph": [e.to_dict() for e in self.association_graph],
            "compiled_artifact": self.compiled_artifact,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "tags": self.tags,
            "category": self.category,
            "source": self.source,
            "access_count": self.access_count,
            "importance": self.importance,
        }
        if include_raw:
            d["raw_content"] = self.raw_content
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeAsset":
        edges = [AssociationEdge.from_dict(e) for e in data.get("association_graph", [])]
        return cls(
            asset_id=data["asset_id"],
            asset_type=AssetType(data.get("asset_type", "memory")),
            raw_content=data.get("raw_content", ""),
            multi_granularity=data.get("multi_granularity", {}),
            capability_profile=data.get("capability_profile", {}),
            association_graph=edges,
            compiled_artifact=data.get("compiled_artifact", {}),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            version=data.get("version", 1),
            tags=data.get("tags", []),
            category=data.get("category", ""),
            source=data.get("source", ""),
            access_count=data.get("access_count", 0),
            importance=data.get("importance", 0.5),
        )

    def to_blob(self) -> bytes:
        """序列化为 blob 存储格式"""
        return json.dumps(self.to_dict(include_raw=True), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_blob(cls, data: bytes) -> "KnowledgeAsset":
        return cls.from_dict(json.loads(data.decode("utf-8")))

    def record_access(self):
        self.access_count += 1
        self.updated_at = time.time()


class AssetRegistry:
    """
    资产注册表——管理 KnowledgeAsset 的 CRUD

    特性：
    - 内存索引（dict）+ BlobArena 持久化
    - 按 capability / category / tags 查询
    - 增量 flush 到 BlobArena
    - 线程安全
    """

    def __init__(self, blob_arena=None):
        from blob_arena import BlobArena, get_blob_arena

        self._arena = blob_arena or get_blob_arena()
        self._assets: Dict[str, KnowledgeAsset] = {}
        self._lock = threading.Lock()

        # 索引
        self._category_index: Dict[str, Set[str]] = {}
        self._tag_index: Dict[str, Set[str]] = {}
        self._type_index: Dict[AssetType, Set[str]] = {}

        # 元数据: asset_id → blob_id（持久化后的位置）
        self._blob_map: Dict[str, str] = {}
        self._dirty_ids: Set[str] = set()

        # 统计
        self._total_saved = 0

    # ── CRUD ──

    def register(self, asset: KnowledgeAsset) -> str:
        """
        注册资产到 registry。如果 asset_id 已存在则更新。

        Returns:
            asset_id
        """
        with self._lock:
            asset.updated_at = time.time()
            old_type = None
            if asset.asset_id in self._assets:
                old_asset = self._assets[asset.asset_id]
                old_type = old_asset.asset_type
                asset.version = old_asset.version + 1

            self._assets[asset.asset_id] = asset
            self._dirty_ids.add(asset.asset_id)

            # 更新索引
            self._rebuild_indices_for_asset(asset, old_type=old_type)

        return asset.asset_id

    def get(self, asset_id: str) -> Optional[KnowledgeAsset]:
        with self._lock:
            asset = self._assets.get(asset_id)
            if asset:
                asset.record_access()
            return asset

    def delete(self, asset_id: str) -> bool:
        with self._lock:
            if asset_id not in self._assets:
                return False
            old = self._assets.pop(asset_id, None)
            self._dirty_ids.discard(asset_id)
            self._blob_map.pop(asset_id, None)

            # 清理索引
            if old:
                self._remove_from_indices(old)
                # 清理关联边
                for e in old.association_graph:
                    target = self._assets.get(e.target_asset_id)
                    if target:
                        target.association_graph = [
                            edge for edge in target.association_graph
                            if edge.target_asset_id != asset_id
                        ]

            return True

    def list_ids(self) -> List[str]:
        with self._lock:
            return list(self._assets.keys())

    def count(self) -> int:
        with self._lock:
            return len(self._assets)

    # ── 查询 ──

    def query_by_capability(self, capability_key: str, min_score: float = 0.0) -> List[KnowledgeAsset]:
        """
        按能力维度查询

        Args:
            capability_key: 如 "web_access", "reasoning", "tool_exec"
            min_score: 最低分数

        Returns:
            匹配的资产列表（按重要性降序）
        """
        results = []
        with self._lock:
            for asset in self._assets.values():
                score = asset.capability_profile.get(capability_key, 0)
                if isinstance(score, (int, float)) and score >= min_score:
                    results.append(asset)
                elif isinstance(score, dict):
                    # 复合能力（如 web_access: {level, enabled})
                    inner = score.get("level", score.get("score", 0))
                    if isinstance(inner, (int, float)) and inner >= min_score:
                        results.append(asset)
        results.sort(key=lambda a: a.importance, reverse=True)
        return results

    def query_by_category(self, category: str) -> List[KnowledgeAsset]:
        with self._lock:
            ids = self._category_index.get(category, set())
            return [self._assets[a_id] for a_id in ids if a_id in self._assets]

    def query_by_tag(self, tag: str) -> List[KnowledgeAsset]:
        with self._lock:
            ids = self._tag_index.get(tag, set())
            return [self._assets[a_id] for a_id in ids if a_id in self._assets]

    def query_by_type(self, asset_type: AssetType) -> List[KnowledgeAsset]:
        with self._lock:
            ids = self._type_index.get(asset_type, set())
            return [self._assets[a_id] for a_id in ids if a_id in self._assets]

    def search(
        self,
        query: str,
        top_k: int = 10,
        type_filter: Optional[AssetType] = None,
    ) -> List[KnowledgeAsset]:
        """
        简单文本搜索（jieba 召回 + 关键词重叠排序）

        未来可升级为 embedding 语义检索。
        """
        try:
            import jieba
            q_words = set(jieba.lcut(query.lower()))
        except ImportError:
            import re
            q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))

        scored = []
        with self._lock:
            for asset in self._assets.values():
                if type_filter and asset.asset_type != type_filter:
                    continue
                try:
                    import jieba
                    a_words = set(jieba.lcut(asset.raw_content[:2000].lower()))
                    # 再加上多粒度表示中的关键词
                    kw = asset.multi_granularity.get("keyword_level", [])
                    if isinstance(kw, list):
                        a_words.update(set(k.lower() for k in kw))
                except ImportError:
                    a_words = set(re.findall(r'[\w\u4e00-\u9fff]+', asset.raw_content[:2000].lower()))

                overlap = len(q_words & a_words)
                if overlap >= 1:
                    score = overlap / max(len(q_words | a_words), 1)
                    # 重要性权重
                    score = score * 0.7 + asset.importance * 0.3
                    scored.append((score, asset))

        scored.sort(key=lambda x: -x[0])
        return [a for _, a in scored[:top_k]]

    # ── 持久化 ──

    def flush(self) -> int:
        """
        将 dirty 资产 flush 到 BlobArena

        Returns:
            实际写入的资产数
        """
        with self._lock:
            dirty = list(self._dirty_ids)
            self._dirty_ids.clear()

        count = 0
        for a_id in dirty:
            asset = self._assets.get(a_id)
            if not asset:
                continue
            try:
                blob_id = self._arena.append_text(json.dumps(
                    asset.to_dict(include_raw=True), ensure_ascii=False
                ))
                self._blob_map[a_id] = blob_id
                count += 1
            except Exception as e:
                logger.error(f"AssetRegistry: flush {a_id} failed: {e}")
                with self._lock:
                    self._dirty_ids.add(a_id)

        self._total_saved += count
        return count

    def load_all(self) -> int:
        """
        从 BlobArena 加载所有已持久化的资产

        Returns:
            加载的资产数
        """
        # BlobArena 不支持枚举所有 blob_id，这里从 arena info 尝试
        # 实际使用中，AssetRegistry 维护一个独立的资产索引文件
        index_path = os.path.expanduser(
            "~/.openclaw/dag_blob_arena/asset_registry.index"
        )
        if not os.path.exists(index_path):
            return 0

        count = 0
        try:
            with open(index_path) as f:
                index_data = json.load(f)
            for entry in index_data:
                blob_id = entry.get("blob_id", "")
                if not blob_id:
                    continue
                try:
                    data = self._arena.read_text(blob_id)
                    asset = KnowledgeAsset.from_dict(json.loads(data))
                    self._assets[asset.asset_id] = asset
                    self._blob_map[asset.asset_id] = blob_id
                    self._rebuild_indices_for_asset(asset)
                    count += 1
                except Exception as e:
                    logger.warning(f"AssetRegistry: load {blob_id} failed: {e}")
        except Exception as e:
            logger.warning(f"AssetRegistry: load index failed: {e}")

        logger.info(f"AssetRegistry: loaded {count} assets")
        return count

    def save_index(self) -> bool:
        """保存 blob_id → asset_id 索引到文件"""
        index_path = os.path.expanduser(
            "~/.openclaw/dag_blob_arena/asset_registry.index"
        )
        try:
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            entries = [
                {"asset_id": a_id, "blob_id": b_id}
                for a_id, b_id in self._blob_map.items()
            ]
            with open(index_path, "w") as f:
                json.dump(entries, f, ensure_ascii=False)
            return True
        except Exception as e:
            logger.warning(f"AssetRegistry: save index failed: {e}")
            return False

    # ── 关联图操作 ──

    def add_edge(self, source_id: str, edge: AssociationEdge) -> bool:
        """添加关联边"""
        with self._lock:
            asset = self._assets.get(source_id)
            if not asset:
                return False
            # 去重
            for existing in asset.association_graph:
                if existing.target_asset_id == edge.target_asset_id and \
                   existing.relation == edge.relation:
                    existing.weight = max(existing.weight, edge.weight)
                    return True
            asset.association_graph.append(edge)
            self._dirty_ids.add(source_id)
            return True

    def remove_edges(self, source_id: str, target_id: str) -> int:
        """移除两个资产之间的所有边"""
        with self._lock:
            asset = self._assets.get(source_id)
            if not asset:
                return 0
            before = len(asset.association_graph)
            asset.association_graph = [
                e for e in asset.association_graph
                if e.target_asset_id != target_id
            ]
            removed = before - len(asset.association_graph)
            if removed > 0:
                self._dirty_ids.add(source_id)
            return removed

    def get_neighbors(self, asset_id: str, max_depth: int = 1) -> List[Tuple[KnowledgeAsset, str, float]]:
        """
        获取关联邻居（广度优先）

        Returns:
            [(asset, relation, weight), ...]
        """
        visited = {asset_id}
        current = {asset_id}
        results = []
        for _ in range(max_depth):
            next_set = set()
            for a_id in current:
                asset = self._assets.get(a_id)
                if not asset:
                    continue
                for edge in asset.association_graph:
                    if edge.target_asset_id not in visited:
                        neighbor = self._assets.get(edge.target_asset_id)
                        if neighbor:
                            visited.add(edge.target_asset_id)
                            next_set.add(edge.target_asset_id)
                            results.append((neighbor, edge.relation, edge.weight))
            current = next_set
        return results

    # ── 统计 ──

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            type_counts = {}
            for asset in self._assets.values():
                t = asset.asset_type.value
                type_counts[t] = type_counts.get(t, 0) + 1
            return {
                "total_assets": len(self._assets),
                "type_counts": type_counts,
                "dirty_count": len(self._dirty_ids),
                "total_saved": self._total_saved,
                "total_blob_mapped": len(self._blob_map),
                "categories": len(self._category_index),
                "tags": len(self._tag_index),
            }

    # ── 内部索引维护 ──

    def _rebuild_indices_for_asset(self, asset: KnowledgeAsset, old_type: Optional[AssetType] = None):
        if old_type:
            self._type_index.get(old_type, set()).discard(asset.asset_id)
        self._type_index.setdefault(asset.asset_type, set()).add(asset.asset_id)

        if asset.category:
            self._category_index.setdefault(asset.category, set()).add(asset.asset_id)

        for tag in asset.tags:
            self._tag_index.setdefault(tag, set()).add(asset.asset_id)

    def _remove_from_indices(self, asset: KnowledgeAsset):
        self._type_index.get(asset.asset_type, set()).discard(asset.asset_id)

        if asset.category:
            self._category_index.get(asset.category, set()).discard(asset.asset_id)

        for tag in asset.tags:
            self._tag_index.get(tag, set()).discard(asset.asset_id)


# ── 全局单例 ──

_ASSET_REGISTRY: Optional[AssetRegistry] = None
_ASSET_REGISTRY_LOCK = threading.Lock()


def get_asset_registry() -> AssetRegistry:
    """获取全局 AssetRegistry 单例"""
    global _ASSET_REGISTRY
    if _ASSET_REGISTRY is None:
        with _ASSET_REGISTRY_LOCK:
            if _ASSET_REGISTRY is None:
                from blob_arena import get_blob_arena
                _ASSET_REGISTRY = AssetRegistry(blob_arena=get_blob_arena())
                _ASSET_REGISTRY.load_all()
    return _ASSET_REGISTRY


# ── 便捷函数 ──

def create_skill_asset(
    skill_id: str,
    raw_content: str,
    capability_profile: Optional[Dict] = None,
    tags: Optional[List[str]] = None,
    category: str = "",
) -> KnowledgeAsset:
    """便捷创建 skill 类型资产"""
    return KnowledgeAsset(
        asset_id=skill_id,
        asset_type=AssetType.SKILL,
        raw_content=raw_content,
        capability_profile=capability_profile or {},
        tags=tags or [],
        category=category or "skill",
        source="skill_scan",
    )


def create_memory_asset(
    memory_id: str,
    raw_content: str,
    tags: Optional[List[str]] = None,
    category: str = "",
    source: str = "user_conversation",
) -> KnowledgeAsset:
    """便捷创建 memory 类型资产"""
    return KnowledgeAsset(
        asset_id=memory_id,
        asset_type=AssetType.MEMORY,
        raw_content=raw_content,
        tags=tags or [],
        category=category or "memory",
        source=source,
    )


# ── 测试入口 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    reg = get_asset_registry()

    s1 = create_skill_asset(
        "skill_web_search_001",
        "Perform web search using xiaoyi-web-search. Supports query expansion and result dedup.",
        capability_profile={"web_access": 0.9, "search": 0.8},
        tags=["search", "web", "external"],
        category="retrieval",
    )
    s2 = create_skill_asset(
        "skill_memory_recall_001",
        "Recall memories using hybrid search. Supports KG + DAG + synapse + paper.",
        capability_profile={"memory": 0.85, "search": 0.7},
        tags=["memory", "recall", "hybrid"],
        category="retrieval",
    )

    m1 = create_memory_asset(
        "mem_user_pref_001",
        "User prefers Chinese expressions and qiqing-liuyu style. Avoids AI-isms.",
        tags=["preference", "style"],
        category="user_profile",
    )

    for asset in [s1, s2, m1]:
        reg.register(asset)
        print(f"Registered: {asset.asset_id} ({asset.asset_type.value})")

    print(f"\nStats: {reg.get_stats()}")
    print(f"Search 'search': {len(reg.search('search'))} results")
    print(f"Query web_access>=0.8: {len(reg.query_by_capability('web_access', 0.8))} results")
    print(f"Query category 'retrieval': {len(reg.query_by_category('retrieval'))} results")

    reg.flush()
    reg.save_index()
    print("Flushed and index saved")
