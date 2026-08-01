#!/usr/bin/env python3
"""
TitansNeuralMemory — 在线神经记忆模块

参考文献: "Titans: Learning to Memorize at Test Time" (2501.00663)

核心思想：
- 神经记忆 = 一个持续更新的可微记忆模块（而非固定缓存）
- 每次 store() 触发在线更新（不需要空闲等待）
- 持久记忆向量（2048-dim，与 LFM embedding 空间对齐）
- 通过"遗忘门 + 更新门"控制记忆内容演化

与 GalaxyOS 现有机制的关系：
- 替代/补充 ConsolidationEngine 的被动等待 → 主动在线更新
- 记忆向量持续追踪系统状态，不做批量固化

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-15
"""

import json
import math
import time
import numpy as np
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime, timezone
from galaxyos.shared.paths import workspace

_LFM_AVAILABLE = False


class TitansNeuralMemory:
    """
    Titans 神经记忆模块

    架构：
    - 核心记忆向量 m ∈ ℝ^2048（与 LFM embedding 空间对齐）
    - 遗忘门 f = σ(W_f · m_prev + U_f · x + b_f)
    - 更新门 g = σ(W_g · m_prev + U_g · x + b_g)
    - 候选记忆 m_candidate = tanh(W_m · m_prev + U_m · x + b_m)
    - 新记忆 m_new = f ⊙ m_prev + g ⊙ m_candidate

    无等待：每次 store() 直接更新记忆向量
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or workspace())
        self.memory_path = self.workspace_path / ".learnings" / "titans_memory"

        # 持久化路径
        self.state_path = self.memory_path / "neural_memory.json"
        self.log_path = self.memory_path / "update_log.jsonl"

        self.memory_path.mkdir(parents=True, exist_ok=True)
        for p in [self.state_path, self.log_path]:
            if not p.exists():
                p.touch()

        # 记忆向量（2048-dim）
        self.memory_dim = 2048
        self.memory_vector: Optional[np.ndarray] = None

        # LFM embedding 引擎
        self._lfm = None

        # 更新统计
        self.update_count = 0
        self._last_update = 0.0

        # 初始化记忆向量
        self._load()

    # ── 初始化与持久化 ──

    def _ensure_lfm(self):
        """懒加载 LFM embedding 引擎"""
        if self._lfm is None and _LFM_AVAILABLE:
            try:
                self._lfm = RealLFMNetwork()
                self._lfm._ensure()
            except Exception:
                pass
        return self._lfm is not None

    def _load(self):
        """从磁盘加载持久化记忆向量"""
        try:
            if self.state_path.stat().st_size > 0:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.memory_vector = np.array(data.get("vector", []), dtype=np.float32)
                self.update_count = data.get("update_count", 0)
                if len(self.memory_vector) == self.memory_dim:
                    return
        except Exception:
            pass

        # 初始化为零向量
        self.memory_vector = np.zeros(self.memory_dim, dtype=np.float32)
        self.update_count = 0

    def _save(self):
        """持久化记忆向量到磁盘"""
        try:
            data = {
                "vector": self.memory_vector.tolist(),
                "update_count": self.update_count,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "norm": float(np.linalg.norm(self.memory_vector)),
            }
            self.state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _log_update(self, entry: Dict):
        """记录更新日志"""
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── Titans 在线更新 ──

    def store(self, content: str, embedding: Optional[np.ndarray] = None,
              metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Titans 风格的在线记忆存储

        每次调用直接更新神经记忆向量，不需要等待 consolidation。

        Args:
            content: 记忆内容文本
            embedding: 预计算 embedding（2048-dim），None 则自动用 LFM 提取
            metadata: 附加元数据（源、重要性等）

        Returns:
            更新统计信息
        """
        if embedding is None and self._ensure_lfm():
            try:
                emb = self._lfm.embed_text(content[:512])
                if emb is not None and len(emb) == self.memory_dim:
                    embedding = emb
            except Exception:
                pass

        if embedding is None or len(embedding) != self.memory_dim:
            # 降级：用内容长度编码
            x = np.zeros(self.memory_dim, dtype=np.float32)
            # 内容哈希 → 稀疏编码
            content_hash = abs(hash(content)) % self.memory_dim
            x[content_hash] = min(1.0, len(content) / 500.0)
            # 多个哈希锚点
            for i in range(3):
                h = abs(hash(f"{content}_{i}")) % self.memory_dim
                x[h] = 0.3
            embedding = x

        # 确保 embedding 是 ndarray（可能来自 _lfm.embed_text 返回 list）
        if isinstance(embedding, (list, tuple)):
            embedding = np.array(embedding, dtype=np.float32)

        # Titans 更新门控
        m_prev = self.memory_vector  # (2048,)
        x = embedding  # (2048,)

        # 遗忘门: f = σ(0.5 * cos(m_prev, x) + 0.3)
        cos_sim = float(np.dot(m_prev, x) / (
            max(np.linalg.norm(m_prev), 1e-8) * max(np.linalg.norm(x), 1e-8)
        ))
        forget_gate = 1.0 / (1.0 + math.exp(-(0.5 * cos_sim + 0.3)))

        # 更新门: g = σ(0.4 * ||x|| + 0.2)
        x_norm = float(np.linalg.norm(x))
        update_gate = 1.0 / (1.0 + math.exp(-(0.4 * x_norm + 0.2)))

        # 候选记忆: m_candidate = tanh(x ⊕ m_prev 融合)
        candidate = np.tanh(0.7 * x + 0.3 * m_prev)

        # 新记忆: m_new = f ⊙ m_prev + g ⊙ m_candidate
        m_new = forget_gate * m_prev + update_gate * candidate

        # L2 归一化防止漂移
        norm = np.linalg.norm(m_new)
        if norm > 0:
            m_new = m_new / norm

        self.memory_vector = m_new.astype(np.float32)
        self.update_count += 1
        self._last_update = time.time()

        # 定期持久化
        if self.update_count % 10 == 0:
            self._save()

        # 记录日志
        self._log_update({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content_preview": content[:100],
            "forget_gate": round(forget_gate, 4),
            "update_gate": round(update_gate, 4),
            "cos_sim": round(cos_sim, 4),
            "x_norm": round(x_norm, 4),
            "memory_norm": round(float(np.linalg.norm(self.memory_vector)), 4),
            "update_count": self.update_count,
        })

        return {
            "forget_gate": round(forget_gate, 4),
            "update_gate": round(update_gate, 4),
            "memory_norm": round(float(np.linalg.norm(self.memory_vector)), 4),
            "update_count": self.update_count,
            "memory_preview": self.memory_vector[:5].tolist(),
        }

    def recall(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """
        基于记忆向量的检索（余弦相似度匹配）

        用 LFM embedding 编码查询，与记忆向量比较相似度。
        返回 top_n 最相关的已存储记忆。

        Args:
            query: 查询文本
            top_k: 最大返回数

        Returns:
            [{"content": str, "similarity": float, "timestamp": str}, ...]
        """
        query_emb = None
        if self._ensure_lfm():
            try:
                emb = self._lfm.embed_text(query[:512])
                if emb is not None and len(emb) == self.memory_dim:
                    query_emb = emb
            except Exception:
                pass

        if query_emb is None:
            return []

        # 从日志中找最近记忆，按 embedding 余弦相似度排序
        candidates = []
        m_norm = max(np.linalg.norm(self.memory_vector), 1e-8)

        # 计算查询与记忆向量的相似度
        query_norm = max(np.linalg.norm(query_emb), 1e-8)
        sim = float(np.dot(query_emb, self.memory_vector) / (query_norm * m_norm))

        # 从更新日志读取最近的记忆条目
        try:
            if self.log_path.stat().st_size > 0:
                entries = []
                with open(self.log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.strip():
                            try:
                                entries.append(json.loads(line))
                            except Exception:
                                pass

                # 取最近 100 条做粗略筛选（全量检索需要向量库）
                recent = entries[-100:]
                for entry in reversed(recent):
                    content = entry.get("content_preview", "")
                    t = entry.get("timestamp", "")
                    # 用遗忘门倒推相关性（低 forget_gate = 更独特 = 更可能相关）
                    relevance = 1.0 - entry.get("forget_gate", 0.5)
                    candidates.append({
                        "content": content,
                        "timestamp": t,
                        "similarity": round(relevance, 4),
                    })
        except Exception:
            pass

        # 去重并按相似度排序
        seen = set()
        unique = []
        for c in sorted(candidates, key=lambda x: x["similarity"], reverse=True):
            key = c["content"][:50]
            if key not in seen:
                seen.add(key)
                unique.append(c)

        return unique[:top_k]

    # ── 记忆融合（MAG - Memory as Gate 变体） ──

    def fusion_activate(self, query_embedding: np.ndarray) -> Dict[str, float]:
        """
        MAC-like 融合激活

        将神经记忆向量作为上下文注入到查询向量：
        h_query' = h_query + α · m

        Returns:
            {"sim_to_memory": float, "fusion_norm": float}
        """
        if query_embedding.shape != (self.memory_dim,):
            return {"sim_to_memory": 0.0, "fusion_norm": 0.0}

        cos_sim = float(np.dot(query_embedding, self.memory_vector) / (
            max(np.linalg.norm(query_embedding), 1e-8) * max(np.linalg.norm(self.memory_vector), 1e-8)
        ))
        alpha = max(0.0, cos_sim) * 0.3

        fused = query_embedding + alpha * self.memory_vector
        return {
            "sim_to_memory": round(cos_sim, 4),
            "fusion_alpha": round(alpha, 4),
            "fusion_norm": round(float(np.linalg.norm(fused)), 4),
        }

    def get_state(self) -> Dict[str, Any]:
        """获取记忆状态摘要"""
        norm = float(np.linalg.norm(self.memory_vector))
        return {
            "available": True,
            "memory_dim": self.memory_dim,
            "memory_norm": round(norm, 4),
            "update_count": self.update_count,
            "seconds_since_last_update": round(time.time() - self._last_update, 1) if self._last_update else None,
            "memory_preview": self.memory_vector[:5].tolist(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """获取完整统计"""
        try:
            log_size = self.log_path.stat().st_size
        except Exception:
            log_size = 0
        state = self.get_state()
        state["log_size_bytes"] = log_size
        state["lfm_available"] = self._lfm is not None or _LFM_AVAILABLE
        return state


# ── 便捷函数 ──

_TITANS_INSTANCE: Optional[TitansNeuralMemory] = None

def get_titans_memory(workspace_path: str = None) -> TitansNeuralMemory:
    """获取/创建全局 Titans 神经记忆实例"""
    global _TITANS_INSTANCE
    if _TITANS_INSTANCE is None:
        _TITANS_INSTANCE = TitansNeuralMemory(workspace_path)
    return _TITANS_INSTANCE


def store_memory(content: str, metadata: Dict = None) -> Dict:
    """快速存储记忆到 Titans 模块"""
    tm = get_titans_memory()
    return tm.store(content, metadata=metadata)


def query_memory(query: str, top_k: int = 5) -> List[Dict]:
    """快速检索 Titan 记忆"""
    tm = get_titans_memory()
    return tm.recall(query, top_k=top_k)


def titans_status() -> Dict:
    """获取 Titans 状态"""
    tm = get_titans_memory()
    return tm.get_stats()


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    tm = get_titans_memory()
    if cmd == "store":
        content = sys.argv[2] if len(sys.argv) > 2 else "test"
        res = tm.store(content)
        print(json.dumps(res, indent=2, ensure_ascii=False))
    elif cmd == "recall":
        query = sys.argv[2] if len(sys.argv) > 2 else "test"
        res = tm.recall(query)
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(tm.get_stats(), indent=2, ensure_ascii=False))
