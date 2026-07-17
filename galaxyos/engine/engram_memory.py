#!/usr/bin/env python3
"""
Engram — Conditional Memory via Scalable Lookup

将 DeepSeek 的 Engram (arXiv:2601.07372) 思想嵌入 GalaxyOS 记忆系统：
  - N-gram 嵌入哈希表 → O(1) 有条件查找
  - 确定性寻址（无 softmax，无注意力）
  - 替代 MoE 的部分计算，提供稀疏记忆新维度

核心：条件记忆 = 静态知识查找 + 可学习嵌入 + N-gram 上下文哈希

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-14
"""

import os
import json
import math
import time
import hashlib
import logging
from typing import Dict, List, Optional, Tuple, Any, Set
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger("engram")

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    logger.warning("torch 未安装，Engram 将使用纯 Python 回退模式")
    TORCH_AVAILABLE = False

import numpy as np


# ==================== 工具函数 ====================

def _stable_hash(text: str, mod: int) -> int:
    """确定性哈希：用于 N-gram 的槽位寻址

    O(1) 查找的核心：不需要 softmax 查找，直接哈希定位。
    """
    return int(hashlib.sha256(text.encode('utf-8')).hexdigest(), 16) % mod


def _ngrams(tokens: List[str], n: int = 2) -> List[str]:
    """从 token 列表生成 N-gram"""
    return [" ".join(tokens[i:i+n]) for i in range(len(tokens) - n + 1)]


def _tokenize(text: str) -> List[str]:
    """简易中文/英文分词，用于 N-gram 构造"""
    tokens = []
    i = 0
    while i < len(text):
        # 中文单字
        if ord(text[i]) > 0x4e00:
            tokens.append(text[i])
            i += 1
        # 英文单词
        elif text[i].isalpha():
            j = i
            while j < len(text) and text[j].isalpha():
                j += 1
            tokens.append(text[i:j].lower())
            i = j
        # 数字
        elif text[i].isdigit():
            j = i
            while j < len(text) and text[j].isdigit():
                j += 1
            tokens.append(text[i:j])
            i = j
        else:
            i += 1
    return tokens


# ==================== N-gram 哈希表 ====================

class NgramHashTable:
    """
    N-gram 嵌入哈希表 — Engram 的核心

    把经典 N-gram 嵌入改造成 O(1) 有条件查找：
    - 每个 N-gram 哈希到一个槽位
    - 槽位存储嵌入向量 + 元数据
    - 查找 = 哈希 → 返回嵌入（常数时间）

    论文公式：
    lookup(ngram) = E[hash(ngram) % N]
    其中 E ∈ R^{N×d} 是可学习的嵌入表
    """

    def __init__(self, num_slots: int = 65536,
                 embed_dim: int = 64,
                 ngram_n: int = 2,
                 persist_path: str = ""):
        self.num_slots = num_slots          # 哈希表槽位数
        self.embed_dim = embed_dim          # 嵌入维度
        self.ngram_n = ngram_n               # N-gram 的 N
        self.persist_path = persist_path     # 持久化路径

        # 嵌入表: num_slots × embed_dim
        self._table: Dict[int, np.ndarray] = {}  # hash → embedding
        self._meta: Dict[int, dict] = {}          # hash → metadata
        self._hit_count: Dict[int, int] = {}      # hash → 命中次数

        # 如果持久化路径存在且文件存在, 加载
        if persist_path and os.path.exists(persist_path):
            self._load()

    def lookup(self, ngram: str) -> Tuple[Optional[np.ndarray], bool]:
        """O(1) 查找 N-gram 嵌入

        Returns:
            (embedding_or_None, 是否命中)
        """
        h = _stable_hash(ngram, self.num_slots)

        if h in self._table:
            self._hit_count[h] = self._hit_count.get(h, 0) + 1
            return self._table[h], True

        return None, False

    def insert(self, ngram: str, embedding: np.ndarray,
               metadata: Optional[dict] = None):
        """插入 N-gram → 嵌入映射"""
        h = _stable_hash(ngram, self.num_slots)
        self._table[h] = embedding
        self._meta[h] = metadata or {}
        self._hit_count[h] = 0

    def get_or_create(self, ngram: str, default_fn=None) -> np.ndarray:
        """查找或创建嵌入

        支持"有条件记忆"：如果 N-gram 没见过，用 default_fn 生成嵌入
        """
        emb, hit = self.lookup(ngram)
        if emb is not None:
            return emb

        # 未命中：创建新嵌入
        if default_fn:
            emb = default_fn()
        else:
            emb = np.random.randn(self.embed_dim).astype(np.float32)
            emb = emb / (np.linalg.norm(emb) + 1e-8)  # 归一化

        self.insert(ngram, emb)
        return emb

    def batch_lookup(self, ngrams: List[str]) -> Tuple[np.ndarray, np.ndarray]:
        """批量 O(1) 查找

        Returns:
            (embeddings: [B, d], hit_mask: [B] 布尔)
        """
        B = len(ngrams)
        embs = np.zeros((B, self.embed_dim), dtype=np.float32)
        hits = np.zeros(B, dtype=bool)

        for i, ng in enumerate(ngrams):
            emb, hit = self.lookup(ng)
            if emb is not None:
                embs[i] = emb
                hits[i] = True

        return embs, hits

    def get_hit_rate(self) -> float:
        """缓存命中率"""
        total = sum(self._hit_count.values())
        if total == 0:
            return 0.0
        # 每个插入算一次"机会"，命中 / (命中 + 机会)
        opportunities = len(self._table) * 10  # 近似
        return min(1.0, total / (total + opportunities))

    def get_top_ngrams(self, k: int = 10) -> List[Tuple[str, int]]:
        """返回命中次数最高的 N-gram"""
        # 需要反向映射：hash → ngram
        # 只存最近访问的，这里取近似
        sorted_hits = sorted(self._hit_count.items(), key=lambda x: -x[1])
        result = []
        for h, cnt in sorted_hits[:k]:
            meta = self._meta.get(h, {})
            result.append((meta.get("ngram", f"<hash:{h}>"), cnt))
        return result

    def save(self, path: Optional[str] = None):
        """持久化哈希表"""
        save_path = path or self.persist_path
        if not save_path:
            return

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

        data = {
            "num_slots": self.num_slots,
            "embed_dim": self.embed_dim,
            "ngram_n": self.ngram_n,
            "table": {str(k): v.tolist() for k, v in self._table.items()},
            "meta": {str(k): v for k, v in self._meta.items()},
            "hit_count": {str(k): v for k, v in self._hit_count.items()},
        }

        with open(save_path, "w") as f:
            json.dump(data, f)

        logger.info(f"Engram 哈希表已保存: {save_path} "
                    f"({len(self._table)} 槽位, {sum(self._hit_count.values())} 命中)")

    def _load(self):
        """从文件加载哈希表"""
        try:
            with open(self.persist_path, "r") as f:
                data = json.load(f)

            self.num_slots = data.get("num_slots", self.num_slots)
            self.embed_dim = data.get("embed_dim", self.embed_dim)
            self.ngram_n = data.get("ngram_n", self.ngram_n)
            self._table = {int(k): np.array(v, dtype=np.float32)
                          for k, v in data.get("table", {}).items()}
            self._meta = {int(k): v for k, v in data.get("meta", {}).items()}
            self._hit_count = {int(k): v for k, v in data.get("hit_count", {}).items()}

            logger.info(f"Engram 哈希表已加载: {self.persist_path} "
                        f"({len(self._table)} 槽位)")
        except Exception as e:
            logger.warning(f"Engram 加载失败: {e}")


# ==================== Engram 条件记忆 ====================

@dataclass
class EngramConfig:
    """Engram 配置"""
    num_slots: int = 65536           # 哈希表槽位数（论文：27B 参数级 → 大规模）
    embed_dim: int = 64              # 嵌入维度
    ngram_n: int = 2                 # N-gram 大小
    context_window: int = 5          # 上下文窗口长度
    memory_threshold: float = 0.3    # 记忆触发阈值
    persist_path: str = ""           # 持久化路径
    use_torch: bool = True           # 是否使用 PyTorch 加速
    prefetch_enabled: bool = True    # 运行时预取（论文特色）

    def __post_init__(self):
        if not TORCH_AVAILABLE:
            self.use_torch = False


class EngramMemory:
    """
    Engram 条件记忆系统

    论文核心思想实现：
    1. 条件记忆 = 替代 MoE 的一部分计算
    2. O(1) 确定性查找 = 不需要 softmax 路由
    3. N-gram 嵌入 = 查到的模式直接作为门控输入
    4. 运行时预取 = 确定性寻址支持 host memory 预取

    在 GalaxyOS 中的角色：
    - 为记忆系统提供"静态知识查找"通道
    - 与 LTC/CfC 的"动态时序建模"互补
    - 与 MemoryOS 的 HeatTracker 结合，用 O(1) 查找替代部分热度计算
    """

    def __init__(self, config: Optional[EngramConfig] = None):
        self.config = config or EngramConfig()
        self._table = NgramHashTable(
            num_slots=self.config.num_slots,
            embed_dim=self.config.embed_dim,
            ngram_n=self.config.ngram_n,
            persist_path=self.config.persist_path,
        )

        # 访问记录（用于预取）
        self._access_history: List[str] = []
        self._max_history: int = 1000

        # 统计
        self._total_lookups = 0
        self._cache_hits = 0
        self._prefetch_hits = 0

        logger.info(f"Engram 条件记忆已初始化: "
                    f"slots={self.config.num_slots}, "
                    f"dim={self.config.embed_dim}, "
                    f"ngram_n={self.config.ngram_n}")

    def lookup(self, text: str) -> Tuple[Optional[np.ndarray], dict]:
        """条件记忆查找

        三步走：
        1. 分词 → N-gram
        2. 批量哈希查找 → O(1) 命中/未命中
        3. 未命中的 N-gram 用 MLP 或默认值生成

        Args:
            text: 输入文本

        Returns:
            (aggregated_embedding_or_None, info_dict)
            info_dict = {
                "hit": 是否至少有一个 N-gram 命中,
                "hit_rate": 命中率,
                "ngrams": N-gram 列表,
                "top_match": 最高匹配度的 N-gram,
            }
        """
        self._total_lookups += 1

        # 1. 分词 + N-gram
        tokens = _tokenize(text)
        if len(tokens) < self.config.ngram_n:
            return None, {"hit": False, "hit_rate": 0.0, "ngrams": [], "reason": "太短"}

        ngrams = _ngrams(tokens, self.config.ngram_n)
        B = len(ngrams)

        # 2. 批量查找
        embs, hits = self._table.batch_lookup(ngrams)
        hit_count = int(hits.sum())
        hit_rate = hit_count / B if B > 0 else 0.0

        # 更新统计
        self._cache_hits += hit_count
        self._access_history.extend(ngrams[-3:])  # 只保留最近的
        if len(self._access_history) > self._max_history:
            self._access_history = self._access_history[-self._max_history:]

        # 3. 聚合嵌入
        if hit_count > 0:
            # 有命中的 N-gram → 平均它们的嵌入
            hit_embs = embs[hits]
            agg_emb = hit_embs.mean(axis=0)

            # 找到最高匹配
            top_idx = int(hits.argmax()) if hits.any() else 0
            top_ngram = ngrams[top_idx]
        else:
            # 没有命中 → 用 MLP 或随机生成
            agg_emb = None
            top_ngram = ""

        # 4. 预取（论文特色：确定性寻址支持 host memory 预取）
        if self.config.prefetch_enabled and hit_rate > 0:
            self._prefetch(ngrams)

        info = {
            "hit": hit_count > 0,
            "hit_rate": hit_rate,
            "ngrams": ngrams[:10],  # 只返回前 10 个
            "ngram_total": B,
            "top_match": top_ngram,
            "embedding_norm": float(np.linalg.norm(agg_emb)) if agg_emb is not None else 0.0,
        }

        return agg_emb, info

    def remember(self, text: str, embedding: Optional[np.ndarray] = None):
        """将文本存入条件记忆

        把文本的关键 N-gram 与嵌入关联起来。
        下次遇到相似文本时，O(1) 命中。
        """
        tokens = _tokenize(text)
        if len(tokens) < self.config.ngram_n:
            return

        ngrams = _ngrams(tokens, self.config.ngram_n)

        # 如果没有提供嵌入，生成一个
        if embedding is None and self.config.embed_dim == 2048:
            try:
                from lfm_adaptive_operator import RealLFMNetwork
                _lfm = RealLFMNetwork()
                emb_lfm = _lfm.embed_text(text[:512])
                if emb_lfm is not None:
                    embedding = emb_lfm.astype(np.float32)
            except Exception:
                pass
        if embedding is None:
            embedding = np.random.randn(self.config.embed_dim).astype(np.float32)
            embedding = embedding / (np.linalg.norm(embedding) + 1e-8)

        # 将嵌入分给所有 N-gram（论文中的 N-gram 分片）
        per_ngram = embedding / math.sqrt(len(ngrams))

        for ng in ngrams:
            self._table.get_or_create(ng, default_fn=lambda: per_ngram.copy())

    def batch_remember(self, texts: List[str],
                       embeddings: Optional[List[np.ndarray]] = None):
        """批量存入条件记忆"""
        for i, text in enumerate(texts):
            emb = embeddings[i] if embeddings else None
            self.remember(text, emb)

    def forget(self, text: str):
        """遗忘特定文本的条件记忆

        删除所有相关 N-gram 的映射（通过重新初始化嵌入实现遗忘）
        """
        tokens = _tokenize(text)
        if len(tokens) < self.config.ngram_n:
            return

        ngrams = _ngrams(tokens, self.config.ngram_n)

        # 重设命中的槽位（论文中没有显式 forget，这里做 soft forget）
        for ng in ngrams:
            h = _stable_hash(ng, self.config.num_slots)
            if h in self._table._table:
                self._table._table[h] = np.zeros(self.config.embed_dim, dtype=np.float32)

    def _prefetch(self, ngrams: List[str]):
        """运行时预取（论文特色）

        Engram 的确定性寻址允许：
        - 在计算当前批次的同时，预取下一个批次的嵌入
        - 从 host memory 异步加载到 fast memory
        - 对推理延迟几乎无开销
        """
        # 在实际系统中，这里会调用 DMA 或 prefetch 指令
        # 在 Python 级别，我们只做标记
        for ng in ngrams:
            self._table.lookup(ng)  # 热访问
            self._prefetch_hits += 1

    def get_hit_rate(self) -> float:
        """转发到 NgramHashTable"""
        return self._table.get_hit_rate()

    def get_status(self) -> dict:
        """获取系统状态"""
        return {
            "total_slots": self.config.num_slots,
            "filled_slots": len(self._table._table),
            "fill_ratio": len(self._table._table) / self.config.num_slots,
            "total_lookups": self._total_lookups,
            "cache_hits": self._cache_hits,
            "prefetch_hits": self._prefetch_hits,
            "hit_rate": self._cache_hits / max(1, self._total_lookups),
            "embed_dim": self.config.embed_dim,
            "ngram_n": self.config.ngram_n,
            "persist_path": self.config.persist_path or "无",
        }

    def save(self, path: Optional[str] = None):
        """持久化"""
        self._table.save(path)

    def load(self, path: Optional[str] = None):
        """加载"""
        load_path = path or self.config.persist_path
        if load_path and os.path.exists(load_path):
            self._table = NgramHashTable(
                num_slots=self.config.num_slots,
                embed_dim=self.config.embed_dim,
                ngram_n=self.config.ngram_n,
                persist_path=load_path,
            )


# ==================== Engram + MemoryOS 融合 ====================

class EngramEnhancedHeatTracker:
    """
    Engram 增强的热度跟踪器

    将 Engram 的 O(1) 条件记忆查找与 MemoryOS 的 HeatTracker 融合：

    原始 HeatTracker:
        heat = decay(heat) + boost_on_access

    Engram 增强:
        heat = decay(heat) + boost_on_access + engram_boost * hit_rate

    当 Engram 命中率高时，说明该内容是"常见模式"，
    作为静态知识应该获得更高保留优先级。
    """

    def __init__(self, engram: EngramMemory,
                 decay_hours: float = 24.0,
                 boost_on_access: float = 0.1,
                 engram_boost_factor: float = 0.3,
                 max_heat: float = 10.0):
        self.engram = engram
        self.decay_hours = decay_hours
        self.boost_on_access = boost_on_access
        self.engram_boost_factor = engram_boost_factor
        self.max_heat = max_heat
        self._nodes: Dict[str, Dict] = {}

    def record_access(self, node_id: str, content: str = "",
                      now: Optional[float] = None, session_id: str = ""):
        """记录一次访问（Engram 增强版）"""
        if now is None:
            now = time.time()

        key = f"{node_id}#{session_id}" if session_id else node_id
        if key not in self._nodes:
            self._nodes[key] = {
                "heat": 0.0,
                "first_access": now,
                "last_access": now,
                "access_count": 0,
            }

        node = self._nodes[key]
        hours_since = (now - node["last_access"]) / 3600

        # 1. 时效性衰减
        if hours_since > 0:
            decay = math.exp(-hours_since / self.decay_hours)
            node["heat"] *= decay

        # 2. 基础升温
        boost = self.boost_on_access

        # 3. Engram 增强
        if content:
            _, info = self.engram.lookup(content)
            if info["hit"]:
                boost += self.engram_boost_factor * info["hit_rate"]

        node["heat"] = min(self.max_heat, node["heat"] + boost)
        node["last_access"] = now
        node["access_count"] += 1

    def get_heat(self, node_id: str, now: Optional[float] = None, session_id: str = "") -> float:
        """获取热度（Engram 增强版）"""
        key = f"{node_id}#{session_id}" if session_id else node_id
        node = self._nodes.get(key)
        if not node:
            # 用 Engram 尝试查找
            return 0.0

        if now is None:
            now = time.time()

        hours_since = (now - node["last_access"]) / 3600
        decayed = node["heat"] * math.exp(-hours_since / self.decay_hours)
        return decayed

    def should_keep(self, node_id: str, threshold: float = 0.3,
                    session_id: str = "") -> bool:
        return self.get_heat(node_id, session_id=session_id) >= threshold

    def get_top_nodes(self, k: int = 10, session_id: str = "") -> List[str]:
        prefix = f"#{session_id}" if session_id else ""
        items = [(nid, self.get_heat(nid)) for nid in self._nodes
                 if not session_id or nid.endswith(prefix)]
        items.sort(key=lambda x: -x[1])
        return [nid for nid, _ in items[:k]]

    def get_status(self, session_id: str = "") -> dict:
        prefix = f"#{session_id}" if session_id else ""
        nodes = {k: v for k, v in self._nodes.items()
                 if not session_id or k.endswith(prefix)}

        return {
            "total_nodes": len(nodes),
            "hot": sum(1 for n in nodes.values() if n["heat"] >= 1.0),
            "warm": sum(1 for n in nodes.values() if 0.3 <= n["heat"] < 1.0),
            "cold": len(nodes) - sum(1 for n in nodes.values() if n["heat"] >= 0.3),
            "engram_status": self.engram.get_status(),
        }


# ==================== 简单测试 ====================

def test_engram():
    """快速验证 Engram 功能"""
    config = EngramConfig(
        num_slots=256,
        embed_dim=16,
        ngram_n=2,
        persist_path="/tmp/engram_test.json",
    )

    mem = EngramMemory(config)

    # 记忆几个文本
    texts = [
        "今天天气很好适合出门散步",
        "明天要开会记得准备材料",
        "神经网络模型训练需要大量数据",
    ]

    for t in texts:
        mem.remember(t)

    # 查找已知文本
    emb, info = mem.lookup("今天天气")
    assert info["hit"], "应该命中已知 N-gram"
    print(f"✅ 已知文本命中: hit_rate={info['hit_rate']:.2f}")

    # 查找未知文本
    emb, info = mem.lookup("量子计算是新兴领域")
    print(f"未知文本: hit={info['hit']}, top={info.get('top_match', '')}")

    # 系统状态
    status = mem.get_status()
    print(f"系统状态: {status['filled_slots']}/{status['total_slots']} 槽位已填充")
    print(f"命中率: {status['hit_rate']:.2f}")

    print("✅ Engram 基础测试通过")
    return mem


# ==================== 集成测试 ====================

def test_engram_heat_integration():
    """测试 Engram + HeatTracker 融合"""
    engram = EngramMemory(EngramConfig(num_slots=128, embed_dim=8, ngram_n=2))

    # 先记忆一些模式
    for text in ["高频模式A", "高频模式B", "高频模式A", "高频模式A"]:
        engram.remember(text)

    tracker = EngramEnhancedHeatTracker(engram)

    # 记录访问（含 Engram 模式匹配的内容）
    tracker.record_access("node1", content="高频模式A相关的内容", session_id="s1")
    tracker.record_access("node2", content="完全不相关的内容", session_id="s1")

    heat1 = tracker.get_heat("node1", session_id="s1")
    heat2 = tracker.get_heat("node2", session_id="s1")

    print(f"node1 (有 Engram 命中) heat={heat1:.3f}")
    print(f"node2 (无 Engram 命中) heat={heat2:.3f}")
    assert heat1 > heat2, "Engram 命中的节点应该获得额外热度"

    print("✅ Engram + HeatTracker 融合测试通过")


if __name__ == "__main__":
    test_engram()
    print()
    test_engram_heat_integration()
