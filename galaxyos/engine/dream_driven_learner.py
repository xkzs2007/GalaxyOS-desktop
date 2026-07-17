#!/usr/bin/env python3
"""
梦境驱动学习器 (DreamDrivenLearner)

将睡眠巩固期间生成的梦境碎片作为合成训练数据，
在线微调 LFM 的小型 adapter head。

核心流程：
1. 从 dream_log.jsonl 收集高新颖度的梦境碎片
2. 用内容做对比学习训练（同源碎片互相拉近，异源碎片推远）
3. 模型更新：每次 sleep 周期后增量更新 adapter 参数
4. adapter 参数持久化到磁盘，推理时自动加载

与 GalaxyOS 管线的关系：
- 在 biorhythm_sleep_consolidation 的 run_full_sleep_cycle 末尾调用
- 不阻塞主线程，异步执行
- 只更新 small adapter（~1M params），不动 2.2B LFM 主模型

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-15
"""

import json
import math
import os
import random
import time
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from datetime import datetime, timezone, timedelta
from galaxyos.shared.paths import workspace


class DreamDrivenLearner:
    """
    梦境驱动学习器

    使用睡眠梦境碎片的对比学习来在线更新 adapter。
    """

    def __init__(self, workspace_path: Optional[str] = None):
        self.workspace_path = Path(workspace_path or workspace())
        self.learn_path = self.workspace_path / ".learnings" / "dream_learning"

        # 持久化
        self.adapter_path = self.learn_path / "dream_adapter.npy"
        self.adapter_metadata_path = self.learn_path / "adapter_metadata.json"
        self.training_log_path = self.learn_path / "training_log.jsonl"
        self.learn_path.mkdir(parents=True, exist_ok=True)
        for p in [self.training_log_path]:
            if not p.exists():
                p.touch()

        # LFM embedding 引擎
        self._lfm = None
        try:
            from lfm_adaptive_operator import RealLFMNetwork
            self._lfm_cls = RealLFMNetwork
            self._lfm_available = True
        except ImportError:
            self._lfm_available = False

        # Adapter 参数: (2048, 2048) 线性变换矩阵
        # 从零初始化为近单位阵（对角占优）
        self.adapter_W: Optional[np.ndarray] = None
        self._load_adapter()

        # 训练状态
        self.train_count = 0
        self.last_dream_cycle = 0

        # 超参数
        self.learning_rate = 0.001
        self.margin = 0.3  # 对比学习 margin
        self.batch_size = 8
        self.max_samples = 200  # 每次最多采用 200 条梦境

        # 新颖度阈值——只选高新颖度的碎片作为训练数据
        self.min_novelty_for_training = 0.5

    def _ensure_lfm(self):
        """懒加载 LFM"""
        if self._lfm is None and self._lfm_available:
            try:
                self._lfm = self._lfm_cls()
                self._lfm._ensure()
            except Exception:
                pass
        return self._lfm is not None

    def _load_adapter(self):
        """加载 adapter 参数"""
        try:
            if self.adapter_path.exists():
                self.adapter_W = np.load(self.adapter_path)
            if self.adapter_metadata_path.exists():
                meta = json.loads(self.adapter_metadata_path.read_text())
                self.train_count = meta.get("train_count", 0)
                self.last_dream_cycle = meta.get("last_dream_cycle", 0)
        except Exception:
            pass
        if self.adapter_W is None or self.adapter_W.shape != (2048, 2048):
            # 近单位阵初始化：对角 0.9，非对角 ±0.01
            W = np.eye(2048, dtype=np.float32) * 0.9
            noise = np.random.uniform(-0.01, 0.01, (2048, 2048)).astype(np.float32)
            self.adapter_W = W + noise

    def _save_adapter(self):
        """持久化 adapter 参数"""
        try:
            np.save(self.adapter_path, self.adapter_W)
            self.adapter_metadata_path.write_text(json.dumps({
                "train_count": self.train_count,
                "last_dream_cycle": self.last_dream_cycle,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }), encoding="utf-8")
        except Exception:
            pass

    def _log_training(self, entry: Dict):
        """记录训练日志"""
        try:
            with open(self.training_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 数据收集 ──

    def collect_dream_fragments(self, dream_log_path: str,
                                 min_cycle: int = 0) -> List[Dict]:
        """
        从梦境日志中收集高新颖度碎片

        Args:
            dream_log_path: dream_log.jsonl 路径
            min_cycle: 最小 cycle 号（避免重复处理）

        Returns:
            [{"content": str, "novelty": float, "source_ids": [str],
              "cycle": int, "emotion_tags": [str]}, ...]
        """
        fragments = []
        try:
            with open(dream_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        cycle = entry.get("cycle", 0)
                        if cycle <= min_cycle:
                            continue
                        phases = entry.get("phases", {})
                        rem = phases.get("rem_generative", {})
                        detail = rem.get("fragments_detail", [])
                        for frag in detail:
                            novelty = frag.get("novelty_score", 0)
                            if novelty >= self.min_novelty_for_training:
                                fragments.append({
                                    "content": frag.get("content", ""),
                                    "novelty": novelty,
                                    "source_ids": frag.get("source_ids", []),
                                    "cycle": cycle,
                                    "emotion_tags": frag.get("emotion_tags", ["neutral"]),
                                    "gain": frag.get("consolidation_gain", 0),
                                })
                    except Exception:
                        continue
        except Exception:
            pass
        return fragments

    # ── 对比学习训练 ──

    def _embed_batch(self, texts: List[str]) -> Optional[np.ndarray]:
        """
        批量文本 → embedding 矩阵 (N, 2048)

        先用 LFM embed_text，再用 adapter_W 变换。
        """
        if not texts:
            return None
        if not self._ensure_lfm():
            return None

        assert self._lfm is not None
        embs = []
        for t in texts:
            try:
                emb = self._lfm.embed_text(t[:512])
                if emb is not None and len(emb) == 2048:
                    embs.append(emb)
                else:
                    embs.append(np.zeros(2048, dtype=np.float32))
            except Exception:
                embs.append(np.zeros(2048, dtype=np.float32))

        X = np.stack(embs, axis=0)  # (N, 2048)
        # Adapter 前向：Z = X @ W^T
        assert self.adapter_W is not None
        Z = X.astype(np.float32) @ self.adapter_W.T.astype(np.float32)
        # L2 归一化
        norms = np.linalg.norm(Z, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-8)
        Z = Z / norms
        return Z.astype(np.float32)

    def _contrastive_loss(self, anchor: np.ndarray, positive: np.ndarray,
                           negatives: List[np.ndarray]) -> float:
        """
        对比损失：同源拉近，异源推远

        Loss = -log( exp(sim(a,p)/τ) / Σ exp(sim(a, n)/τ) )
        """
        tau = 0.1
        a_norm = anchor / max(np.linalg.norm(anchor), 1e-8)

        sim_pos = float(np.dot(a_norm, positive / max(np.linalg.norm(positive), 1e-8)))

        exp_pos = math.exp(sim_pos / tau)
        exp_neg_sum = 0.0
        for neg in negatives:
            sim_neg = float(np.dot(a_norm, neg / max(np.linalg.norm(neg), 1e-8)))
            exp_neg_sum += math.exp(sim_neg / tau)

        total = exp_pos + exp_neg_sum
        if total < 1e-10:
            return 0.0
        return -math.log(max(exp_pos / total, 1e-10))

    def _contrastive_gradient(self, anchor: np.ndarray, positive: np.ndarray,
                                negatives: List[np.ndarray],
                                W: np.ndarray) -> np.ndarray:
        """
        对比梯度近似（不依赖 autograd，用数值近似）

        对 W 的梯度方向 = (Z_a - Z_p) 外积 X_a + Σ (Z_a - Z_n) 外积 X_a

        Returns:
            (2048, 2048) 梯度估计
        """
        tau = 0.1
        grad = np.zeros_like(W)

        # anchor embedding: Z_a = X_a @ W^T
        # 简化梯度：dL/dW ≈ (Z_a - Z_target) ⊗ X_a / τN
        # 这里用近似外积更新

        # 正样本梯度方向：拉近
        diff_pos = (anchor - positive) / max(np.linalg.norm(anchor - positive), 1e-8)
        grad += 0.01 * np.outer(diff_pos, anchor)  # 简化的外积近似

        # 负样本梯度方向：推远
        for neg in negatives:
            diff_neg = (anchor - neg) / max(np.linalg.norm(anchor - neg), 1e-8)
            grad -= 0.005 * np.outer(diff_neg, anchor)

        return grad

    def train_step(self, fragments: List[Dict]) -> Dict[str, Any]:
        """
        执行一轮对比学习训练

        Args:
            fragments: collect_dream_fragments 返回的碎片列表

        Returns:
            训练统计
        """
        if len(fragments) < 2:
            return {"skipped": "too_few_fragments", "count": len(fragments)}

        # 分组：同源碎片互为 positive，异源为 negative
        # 按 source_ids 的 top-1 来源分组
        groups: Dict[str, List[Dict]] = {}
        for frag in fragments:
            primary = frag["source_ids"][0] if frag["source_ids"] else "unknown"
            if primary not in groups:
                groups[primary] = []
            groups[primary].append(frag)

        # 提取文本
        texts = [f["content"] for f in fragments]
        Z = self._embed_batch(texts)
        if Z is None or len(Z) < 2:
            return {"skipped": "embedding_failed", "count": len(fragments)}

        # 建立文本→embedding 索引
        text_to_emb = {fragments[i]["content"]: Z[i] for i in range(len(fragments))}

        total_loss = 0.0
        train_pairs = 0
        grad_accum = np.zeros_like(self.adapter_W)

        # 对每组选 anchor-positive 对
        for group_id, group_frags in groups.items():
            if len(group_frags) < 2:
                continue

            # 取同一组的两个碎片作为 anchor-positive
            for i in range(len(group_frags)):
                for j in range(i + 1, len(group_frags)):
                    a_text = group_frags[i]["content"]
                    p_text = group_frags[j]["content"]
                    a_emb = text_to_emb.get(a_text)
                    p_emb = text_to_emb.get(p_text)
                    if a_emb is None or p_emb is None:
                        continue

                    # 选取其他组的作为负样本
                    neg_embs = []
                    for other_id, other_frags in groups.items():
                        if other_id == group_id:
                            continue
                        # 从其他组随机取 1 条
                        sample = random.choice(other_frags)
                        n_emb = text_to_emb.get(sample["content"])
                        if n_emb is not None:
                            neg_embs.append(n_emb)

                    if not neg_embs:
                        continue

                    # 计算 loss
                    loss = self._contrastive_loss(a_emb, p_emb, neg_embs)
                    total_loss += loss
                    train_pairs += 1

                    # 积累梯度
                    assert self.adapter_W is not None
                    grad = self._contrastive_gradient(a_emb, p_emb, neg_embs,
                                                       self.adapter_W)
                    grad_accum += grad

        if train_pairs == 0:
            return {"skipped": "no_trainable_pairs", "fragment_count": len(fragments)}

        # 应用梯度更新
        avg_loss = total_loss / train_pairs
        lr = self.learning_rate * max(0.5, 1.0 - self.train_count / 500.0)  # 衰减
        assert self.adapter_W is not None
        self.adapter_W = self.adapter_W - lr * grad_accum / train_pairs

        # Frobenius 范数约束（防止 W 爆炸）
        frob = np.linalg.norm(self.adapter_W)
        if frob > 10.0:
            self.adapter_W = self.adapter_W * (10.0 / frob)

        self.train_count += 1

        return {
            "fragments_loaded": len(fragments),
            "train_pairs": train_pairs,
            "avg_loss": round(avg_loss, 6),
            "learning_rate": round(lr, 6),
            "W_frobenius_norm": round(float(np.linalg.norm(self.adapter_W)), 4),
            "W_diag_mean": round(float(np.mean(np.diag(self.adapter_W))), 4),
        }

    # ── 外部入口 ──

    def learn_from_dreams(self, dream_log_path: str,
                           current_cycle: Optional[int] = None) -> Dict[str, Any]:
        """
        入口：从梦境日志执行一次学习

        在 biorhythm_sleep_consolidation 的 run_full_sleep_cycle 末尾调用。

        Args:
            dream_log_path: dream_log.jsonl 路径
            current_cycle: 当前 sleep cycle 号

        Returns:
            学习统计
        """
        min_cycle = self.last_dream_cycle

        fragments = self.collect_dream_fragments(dream_log_path, min_cycle=min_cycle)

        if not fragments:
            return {"skipped": "no_new_dream_fragments",
                    "min_cycle": min_cycle}

        result = self.train_step(fragments)

        if current_cycle is not None:
            self.last_dream_cycle = current_cycle

        # 持久化
        self._save_adapter()
        self._log_training({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_cycle": current_cycle,
            **result,
        })

        return result

    def embed_with_dream_adapter(self, text: str) -> Optional[np.ndarray]:
        """
        用学习后的 adapter 增强 embedding

        推理时调用：base = LFM.embed_text(text)
                    enhanced = adapter(base)

        Args:
            text: 输入文本

        Returns:
            (2048,) 增强后的 embedding，或 base embedding
        """
        if not self._ensure_lfm():
            return None
        assert self._lfm is not None
        assert self.adapter_W is not None
        try:
            emb = self._lfm.embed_text(text[:512])
            if emb is not None and len(emb) == 2048:
                enhanced = emb.astype(np.float32) @ self.adapter_W.T.astype(np.float32)
                norm = np.linalg.norm(enhanced)
                if norm > 0:
                    enhanced = enhanced / norm
                return enhanced.astype(np.float32)
        except Exception:
            pass
        return None

    def get_stats(self) -> Dict[str, Any]:
        """获取学习器状态"""
        W = self.adapter_W
        diag_mean = float(np.mean(np.diag(W))) if W is not None else 0
        off_diag_std = float(np.std(W - np.diag(np.diag(W)))) if W is not None else 0
        return {
            "train_count": self.train_count,
            "last_dream_cycle": self.last_dream_cycle,
            "W_diag_mean": round(diag_mean, 4),
            "W_off_diag_std": round(off_diag_std, 4),
            "W_frobenius_norm": round(float(np.linalg.norm(W)), 4) if W is not None else 0,
            "lfm_available": self._lfm_available,
            "adapter_params": f"{2048*2048:,} ({(2048*2048*4)/1024/1024:.1f} MB)",
        }


# ── 快捷入口 ──

_LEARNER: Optional[DreamDrivenLearner] = None


def get_learner(workspace_path: Optional[str] = None) -> DreamDrivenLearner:
    """获取/创建全局梦境学习器"""
    global _LEARNER
    if _LEARNER is None:
        _LEARNER = DreamDrivenLearner(workspace_path)
    return _LEARNER


def learn_from_dreams(dream_log_path: Optional[str] = None,
                       workspace_path: Optional[str] = None,
                       current_cycle: Optional[int] = None) -> Dict:
    """快速执行梦境学习"""
    if dream_log_path is None:
        dream_log_path = os.path.join(
            workspace_path or workspace(),
            "memory/dreaming/dream_log.jsonl"
        )
    learner = get_learner(workspace_path)
    return learner.learn_from_dreams(dream_log_path, current_cycle)


def dream_stats() -> Dict:
    """获取梦境学习器状态"""
    return get_learner().get_stats()


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    learner = get_learner()
    if cmd == "learn":
        log_path = sys.argv[2] if len(sys.argv) > 2 else None
        if not log_path:
            log_path = os.path.expanduser("~/.openclaw/workspace/memory/dreaming/dream_log.jsonl")
        result = learner.learn_from_dreams(log_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "embed":
        text = sys.argv[2] if len(sys.argv) > 2 else "hello"
        emb = learner.embed_with_dream_adapter(text)
        if emb is not None:
            print(f"embedding: ({emb.shape[0]},) norm={np.linalg.norm(emb):.4f}")
        else:
            print("embedding 不可用")
    else:
        print(json.dumps(learner.get_stats(), indent=2, ensure_ascii=False))
