#!/usr/bin/env python3
"""
多层 CfC 序列预测模块

预测用户下一个可能激活的记忆（记忆序列预测）：
- 输入: [batch, seq_len, input_dim] — 最近 N 个记忆的 embedding 序列
- 输出: [batch, output_dim] — 预测的下一个记忆 embedding
- 用途: 预取、联想推理、路径推荐

核心思想：
  当用户问了一系列问题时，记忆是按时间序列触发的。
  多层 CfC（Closed-form Continuous-time）可以捕捉这种序列依赖关系。
  比 LSTM 好在连续时间建模 — CfC 用微分方程近似描述隐藏状态随"时间"的演化。

架构:
  CfC (ncps.torch.CfC) → 序列 RNN，输出最终隐藏状态
  → Linear 投影到 embedding 维度
  → Cosine 相似度查找最近邻记忆

集成方式:
  - 从 memory_synapse_network 获取激活历史
  - 记录每次记忆激活事件
  - 训练后可用于实时预测

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-06
"""

import json
import math
import logging
import random
from collections import deque, defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Deque, Any
from dataclasses import dataclass, field, asdict

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

logger = logging.getLogger("cfc_sequence_predictor")

try:
    from ncps.torch import CfC
    from ncps.wirings import AutoNCP
    NCP_AVAILABLE = True
except ImportError:
    logger.warning("ncps 未安装，CfC 序列预测器不可用")
    NCP_AVAILABLE = False


# ==================== 激活事件记录 ====================

@dataclass
class ActivationEvent:
    """单次记忆激活事件"""
    memory_id: str
    embedding: List[float]
    timestamp: str = ""
    context_id: Optional[str] = None  # 同一会话/上下文的 ID
    strength: float = 1.0  # 激活强度（0~1）


class ActivationHistory:
    """
    激活历史缓冲区

    维护最近 N 次记忆激活事件的循环缓冲区（环形队列）。
    支持分会话（context）记录。
    """

    def __init__(self, maxlen: int = 100):
        self.maxlen = maxlen
        # 全局序列（按发生顺序）
        self._events: Deque[ActivationEvent] = deque(maxlen=maxlen)
        # 按上下文分组的序列
        self._contexts: Dict[str, Deque[ActivationEvent]] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )

    def record(self, memory_id: str, embedding: List[float],
               context_id: Optional[str] = None,
               strength: float = 1.0) -> ActivationEvent:
        """记录一次激活事件"""
        event = ActivationEvent(
            memory_id=memory_id,
            embedding=embedding,
            timestamp=datetime.now(timezone.utc).isoformat(),
            context_id=context_id,
            strength=strength,
        )
        self._events.append(event)

        if context_id:
            self._contexts[context_id].append(event)

        return event

    def get_recent_embeddings(self, n: int) -> List[List[float]]:
        """获取最近 N 个记忆的 embedding 序列（按时间顺序）"""
        recent = list(self._events)[-n:]
        return [e.embedding for e in recent]

    def get_recent_ids(self, n: int) -> List[str]:
        """获取最近 N 个记忆 ID 序列"""
        recent = list(self._events)[-n:]
        return [e.memory_id for e in recent]

    def get_context_sequence(self, context_id: str, n: int) -> List[List[float]]:
        """获取指定上下文的最近 N 个 embedding 序列"""
        events = list(self._contexts.get(context_id, deque()))[-n:]
        return [e.embedding for e in events]

    def get_all_sequences(self, seq_len: int,
                          min_seq_len: int = 2) -> List[List[List[float]]]:
        """
        从所有上下文提取训练序列

        从每个上下文中提取所有长度为 seq_len 的连续子序列。
        用于监督学习：前 seq_len-1 个→预测第 seq_len 个。

        Returns:
            [[seq_len 个 embedding], ...]
        """
        sequences = []

        # 全局序列
        events = list(self._events)
        if len(events) >= seq_len:
            seq = [e.embedding for e in events]
            sequences.extend(self._slide_windows(seq, seq_len))

        # 各上下文序列
        for ctx_id, ctx_events in self._contexts.items():
            evts = list(ctx_events)
            if len(evts) >= seq_len:
                seq = [e.embedding for e in evts]
                sequences.extend(self._slide_windows(seq, seq_len))

        return sequences

    @staticmethod
    def _slide_windows(seq: List, window: int) -> List[List]:
        """滑动窗口提取"""
        return [seq[i:i + window] for i in range(len(seq) - window + 1)]

    def clear(self, context_id: Optional[str] = None):
        """清除记录"""
        if context_id:
            self._contexts.pop(context_id, None)
        else:
            self._events.clear()
            self._contexts.clear()

    def size(self) -> int:
        """事件总数"""
        return len(self._events)

    def context_count(self) -> int:
        """上下文数"""
        return len(self._contexts)

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "maxlen": self.maxlen,
            "events": [
                asdict(e) for e in self._events
            ],
            "contexts": {
                ctx_id: [asdict(e) for e in events]
                for ctx_id, events in self._contexts.items()
            }
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActivationHistory":
        """反序列化"""
        hist = cls(maxlen=data.get("maxlen", 100))
        for ed in data.get("events", []):
            hist._events.append(ActivationEvent(**ed))
        for ctx_id, events in data.get("contexts", {}).items():
            for ed in events:
                hist._contexts[ctx_id].append(ActivationEvent(**ed))
        return hist


# ==================== 多层 CfC 序列预测器 ====================

class CfCSequencePredictor(nn.Module):
    """
    多层 CfC 序列预测器

    输入: [batch, seq_len, input_dim] — 最近 N 个记忆的 embedding 序列
    输出: [batch, output_dim] — 预测的下一个记忆 embedding

    训练后可用于：
      - 预取（用户可能马上就要哪个记忆）
      - 联想推理（从序列中发现因果关系）
      - 推荐路径（在当前上下文中推荐下一步关联）

    架构:
      ┌──────────┐    ┌──────────┐    ┌──────────┐
      │ CfC RNN  │ →  │ LayerNorm│ →  │ Linear   │ → prediction embedding
      │(ncps)    │    │          │    │(proj)    │
      └──────────┘    └──────────┘    └──────────┘

    CfC 的连续时间建模优势:
      CfC 用微分方程近似描述隐藏状态随时间的演化，比 LSTM/GRU 更适合
      处理"不规律时间间隔"的记忆激活事件（用户可能几分钟激活一次，
      也可能几天不激活）。通过 timespans 参数传递时间间隔信息。
    """

    def __init__(
        self,
        input_dim: int = 16,
        hidden_dim: int = 64,
        output_dim: Optional[int] = None,
        seq_len: int = 5,
        mode: str = "default",
        backbone_units: int = 0,
        backbone_layers: int = 0,
        use_mixed_memory: bool = False,
        max_history: int = 100,
        use_autoncp: bool = True,
        autoncp_sparsity: float = 0.5,
        device: str = "cpu",
    ):
        """
        Args:
            input_dim: 记忆 embedding 维度
            hidden_dim: CfC 隐藏状态维度
            output_dim: 输出 embedding 维度（默认=input_dim）
            seq_len: 输入序列长度（最近 N 个记忆）
            mode: CfC 模式（"default", "pure", "no_gate"）
            backbone_units: backbone 隐藏单元数（0=跳过 backbone 直接近似）
            backbone_layers: backbone 层数（0=跳过 backbone）
            use_mixed_memory: 是否混合 LSTM 增强长期依赖
            max_history: 激活历史最大长度
            use_autoncp: 是否用 AutoNCP Wiring 替代纯 CfC
            autoncp_sparsity: AutoNCP 稀疏度
            device: 设备
        """
        super().__init__()

        if not NCP_AVAILABLE:
            raise RuntimeError(
                "ncps 未安装。请安装: pip install ncps"
            )

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim or input_dim
        self.seq_len = seq_len
        self.device = torch.device(device)

        # 激活历史
        self.history = ActivationHistory(maxlen=max_history)

        # 记忆 embedding 索引（memory_id → embedding）
        self._memory_index: Dict[str, List[float]] = {}

        # ===== 核心：多层 CfC RNN =====

        if use_autoncp:
            # AutoNCP Wiring 版本 —
            # 自动分配 sensory/inter/command/motor 神经元拓扑
            # 更符合生物神经网络结构
            # AutoNCP requires output_size < units-2
            ncp_output_dim = max(1, hidden_dim // 4)
            self.wiring = AutoNCP(
                units=hidden_dim,
                output_size=ncp_output_dim,
                sparsity_level=autoncp_sparsity,
            )
            self.use_wiring = True
        else:
            self.wiring = None
            self.use_wiring = False

        if self.use_wiring:
            # WiredCfCCell 模式 — 使用 NCP Wiring
            # 用 proj_size 统一输出维度到 hidden_dim（不论 wiring.output_dim）
            self.cfc = CfC(
                input_size=input_dim,
                units=self.wiring,
                proj_size=hidden_dim,
                return_sequences=False,
                batch_first=True,
                mixed_memory=use_mixed_memory,
                mode=mode,
            )
        else:
            # 纯 CfC 模式 — 标准多层 RNN
            self.cfc = CfC(
                input_size=input_dim,
                units=hidden_dim,
                proj_size=hidden_dim,
                return_sequences=False,
                batch_first=True,
                mixed_memory=use_mixed_memory,
                mode=mode,
                backbone_units=backbone_units,
                backbone_layers=backbone_layers,
            )

        # 输出投影层（两个分支的 CfC 输出都是 hidden_dim）
        self.output_proj = nn.Sequential(
            nn.LayerNorm(hidden_dim),
            nn.Linear(hidden_dim, self.output_dim),
        )

        # 训练状态
        self._training_history: Dict[str, Any] = {
            "loss": [],
            "cosine_sim": [],
            "epoch": 0,
        }

        logger.info(
            f"CfC 序列预测器初始化完成: "
            f"input={input_dim}, hidden={hidden_dim}, "
            f"output={self.output_dim}, seq_len={seq_len}, "
            f"wiring={'AutoNCP' if use_autoncp else 'none'}"
        )

    def forward(
        self,
        x: torch.Tensor,
        timespans: Optional[torch.Tensor] = None,
        hx: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """
        前向传播

        Args:
            x: 输入序列 [batch, seq_len, input_dim]
            timespans: 可选的时间间隔 [batch, seq_len] 或 [batch, seq_len, 1]
                       每个时间步距前一步的时间间隔（归一化后）
            hx: 初始隐藏状态（可选）

        Returns:
            prediction: 预测的下一个 embedding [batch, output_dim]
        """
        # CfC 前向（return_sequences=False, 只返回最后输出）
        output, hx = self.cfc(x, hx=hx, timespans=timespans)

        # 投影到输出维度
        prediction = self.output_proj(output)

        return prediction

    # ==================== 记录激活 ====================

    def record_activation(
        self,
        memory_id: str,
        embedding: List[float],
        context_id: Optional[str] = None,
        strength: float = 1.0,
    ):
        """
        记录一次记忆激活事件

        同时更新 memory_index。

        Args:
            memory_id: 记忆 ID
            embedding: 记忆的 embedding 向量
            context_id: 可选会话/上下文 ID（用于分上下文训练）
            strength: 激活强度（0~1）
        """
        self.history.record(
            memory_id=memory_id,
            embedding=embedding,
            context_id=context_id,
            strength=strength,
        )
        self._memory_index[memory_id] = embedding

    def register_memory_batch(
        self,
        memories: Dict[str, List[float]],
    ):
        """
        批量注册记忆到索引（不触发激活记录）

        Args:
            memories: {memory_id: embedding}
        """
        self._memory_index.update(memories)

    # ==================== 预测 ====================

    @torch.no_grad()
    def predict_next(
        self,
        context_embeddings: List[List[float]],
        timespans: Optional[List[float]] = None,
    ) -> List[float]:
        """
        给定当前上下文，预测下一个记忆 embedding

        Args:
            context_embeddings: 最近 N 个记忆的 embedding 列表
                                [seq_len, input_dim]
            timespans: 可选的时间间隔列表 [seq_len]
                       每个时间步距前一步的时间间隔（归一化后）

        Returns:
            predicted_embedding: 预测的下一个记忆 embedding [output_dim]
        """
        if len(context_embeddings) == 0:
            logger.warning("空上下文序列，返回零向量")
            return [0.0] * self.output_dim

        # 填充/截断到 seq_len
        seq = self._pad_or_truncate(context_embeddings, self.seq_len)

        # 转 tensor
        x = torch.tensor(seq, dtype=torch.float32, device=self.device).unsqueeze(0)

        ts = None
        if timespans is not None:
            ts_seq = self._pad_or_truncate(timespans, self.seq_len)
            ts = torch.tensor(ts_seq, dtype=torch.float32, device=self.device).unsqueeze(0)

        # 前向
        prediction = self.forward(x, timespans=ts)

        return prediction.squeeze(0).tolist()

    @torch.no_grad()
    def predict_batch(
        self,
        batch_sequences: List[List[List[float]]],
        batch_timespans: Optional[List[List[float]]] = None,
    ) -> List[List[float]]:
        """
        批量预测

        Args:
            batch_sequences: [[seq_len, input_dim], ...]
            batch_timespans: [[seq_len], ...] 可选

        Returns:
            [[output_dim], ...]
        """
        B = len(batch_sequences)
        if B == 0:
            return []

        # 统一填充到 seq_len
        padded = []
        for seq in batch_sequences:
            padded.append(self._pad_or_truncate(seq, self.seq_len))

        x = torch.tensor(padded, dtype=torch.float32, device=self.device)

        ts = None
        if batch_timespans is not None:
            ts_padded = []
            for tspans in batch_timespans:
                ts_padded.append(self._pad_or_truncate(tspans, self.seq_len))
            ts = torch.tensor(ts_padded, dtype=torch.float32, device=self.device)

        predictions = self.forward(x, timespans=ts)
        return predictions.tolist()

    # ==================== 推荐 ====================

    @torch.no_grad()
    def recommend(
        self,
        top_k: int = 5,
        exclude_ids: Optional[List[str]] = None,
    ) -> List[Tuple[str, float]]:
        """
        推荐最可能激活的 top K 记忆

        从最近历史中取 seq_len 个 embedding => 预测下一个 =>
        在 memory_index 中找 cosine 相似度最高的记忆。

        Args:
            top_k: 返回 top K
            exclude_ids: 排除的记忆 ID 列表（如已经激活过的）

        Returns:
            [(memory_id, score), ...]
        """
        if len(self._memory_index) == 0:
            return []

        # 获取最近序列
        recent = self.history.get_recent_embeddings(self.seq_len)
        if len(recent) < 1:
            return []

        # 预测
        predicted = self.predict_next(recent)

        # 相似度搜索
        exclude = set(exclude_ids or [])
        scores = []
        for mid, emb in self._memory_index.items():
            if mid in exclude:
                continue
            sim = self._cosine_similarity(predicted, emb)
            scores.append((mid, sim))

        # 排序取 top
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    @torch.no_grad()
    def recommend_from_embeddings(
        self,
        context_embeddings: List[List[float]],
        memory_embeddings: Dict[str, List[float]],
        top_k: int = 5,
        exclude_ids: Optional[List[str]] = None,
    ) -> List[Tuple[str, float]]:
        """
        从给定的 embedding 序列预测并推荐

        不依赖内部状态，适用于独立查询。

        Args:
            context_embeddings: [seq_len, input_dim]
            memory_embeddings: {id: embedding}
            top_k: 返回 top K
            exclude_ids: 排除列表

        Returns:
            [(memory_id, score), ...]
        """
        if len(context_embeddings) == 0 or len(memory_embeddings) == 0:
            return []

        predicted = self.predict_next(context_embeddings)

        exclude = set(exclude_ids or [])
        scores = []
        for mid, emb in memory_embeddings.items():
            if mid in exclude:
                continue
            sim = self._cosine_similarity(predicted, emb)
            scores.append((mid, sim))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    # ==================== 训练 ====================

    def train_on_history(
        self,
        activation_sequences: List[List[str]],
        memory_embeddings: Dict[str, List[float]],
        epochs: int = 100,
        lr: float = 0.001,
        batch_size: int = 32,
        val_split: float = 0.1,
        patience: int = 10,
        verbose: bool = True,
    ) -> Dict[str, list]:
        """
        在历史激活序列上训练

        构造训练数据：seq_len 个连续 embedding → 下一个 embedding

        Args:
            activation_sequences: [[memory_id, ...], ...]
                                 每个列表是按时间顺序激活的记忆 ID 序列
            memory_embeddings: {memory_id: embedding}
            epochs: 训练轮数
            lr: 学习率
            batch_size: 批大小
            val_split: 验证集比例
            patience: 早停耐心值
            verbose: 是否打印训练日志

        Returns:
            {loss: [...], cosine_sim: [...]}
        """
        # 同步记忆索引
        self.register_memory_batch(memory_embeddings)

        # 构造训练样本
        x_list = []
        y_list = []

        for seq_ids in activation_sequences:
            # 把 ID 序列转成 embedding 序列
            embs = []
            for mid in seq_ids:
                if mid in memory_embeddings:
                    embs.append(memory_embeddings[mid])

            if len(embs) < self.seq_len + 1:
                continue

            # 滑动窗口
            for i in range(len(embs) - self.seq_len):
                x_window = embs[i:i + self.seq_len]
                y_next = embs[i + self.seq_len]
                x_list.append(x_window)
                y_list.append(y_next)

        N = len(x_list)
        if N == 0:
            logger.warning("训练数据为空，跳过训练")
            return self._training_history

        # 转 tensor
        x_tensor = torch.tensor(x_list, dtype=torch.float32, device=self.device)
        y_tensor = torch.tensor(y_list, dtype=torch.float32, device=self.device)

        # 训练/验证集分割
        n_val = max(1, int(N * val_split))
        perm = torch.randperm(N, device=self.device)
        val_idx = perm[:n_val]
        train_idx = perm[n_val:]

        x_train, y_train = x_tensor[train_idx], y_tensor[train_idx]
        x_val, y_val = x_tensor[val_idx], y_tensor[val_idx]

        # 优化器
        optimizer = torch.optim.AdamW(self.parameters(), lr=lr, weight_decay=1e-5)
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="min", factor=0.5, patience=5, min_lr=1e-6
        )

        # 损失函数：余弦嵌入损失 + MSE
        cos_criterion = nn.CosineEmbeddingLoss(margin=0.0)
        mse_criterion = nn.MSELoss()

        best_val_loss = float("inf")
        best_state = None
        no_improve = 0

        if verbose:
            print(f"{'='*60}")
            print("CfC 序列预测器训练")
            print(f"  训练样本: {len(train_idx)}, 验证样本: {len(val_idx)}")
            print(f"  序列长度: {self.seq_len}, Embedding 维度: {self.input_dim}")
            print(f"  Epochs: {epochs}, LR: {lr}, Batch: {batch_size}")
            print(f"{'='*60}")

        for epoch in range(1, epochs + 1):
            self.train()

            # 训练
            total_loss = 0.0
            total_cos = 0.0
            n_batches = 0

            # mini-batch
            for i in range(0, len(x_train), batch_size):
                bx = x_train[i:i + batch_size]
                by = y_train[i:i + batch_size]

                optimizer.zero_grad()

                pred = self.forward(bx)

                # 余弦嵌入损失：鼓励预测和目标的余弦相似度
                # CosineEmbeddingLoss: 需要 target=1 表示相似
                B = pred.size(0)
                target = torch.ones(B, device=self.device)
                cos_loss = cos_criterion(pred, by, target)

                # MSE：鼓励数值接近
                mse_loss = mse_criterion(pred, by)

                # 组合损失
                loss = 0.7 * cos_loss + 0.3 * mse_loss
                loss.backward()

                # 梯度裁剪
                torch.nn.utils.clip_grad_norm_(self.parameters(), 1.0)
                optimizer.step()

                total_loss += loss.item()
                # Cosine 相似度
                with torch.no_grad():
                    cos_sim = F.cosine_similarity(pred, by, dim=1).mean().item()
                    total_cos += cos_sim
                n_batches += 1

            avg_loss = total_loss / n_batches
            avg_cos = total_cos / n_batches

            # 验证
            self.eval()
            with torch.no_grad():
                val_pred = self.forward(x_val)
                val_loss = 0.7 * cos_criterion(val_pred, y_val, torch.ones(n_val, device=self.device)).item() + \
                           0.3 * mse_criterion(val_pred, y_val).item()
                val_cos = F.cosine_similarity(val_pred, y_val, dim=1).mean().item()

            scheduler.step(val_loss)

            # 记录
            self._training_history["loss"].append(avg_loss)
            self._training_history["cosine_sim"].append(avg_cos)
            self._training_history["epoch"] = epoch

            # 早停
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                best_state = {k: v.clone() for k, v in self.state_dict().items()}
                no_improve = 0
            else:
                no_improve += 1

            if verbose and (epoch == 1 or epoch % 10 == 0 or epoch == epochs):
                current_lr = optimizer.param_groups[0]["lr"]
                print(
                    f"  Epoch {epoch:3d}/{epochs}  "
                    f"loss={avg_loss:.4f}  "
                    f"cos={avg_cos:.4f}  "
                    f"val_loss={val_loss:.4f}  "
                    f"val_cos={val_cos:.4f}  "
                    f"lr={current_lr:.6f}"
                )

            if no_improve >= patience:
                if verbose:
                    print(f"  早停: {patience} 轮无改善")
                break

        # 恢复最佳参数
        if best_state is not None:
            self.load_state_dict(best_state)
            if verbose:
                print(f"\n  恢复最佳模型: val_loss={best_val_loss:.4f}")

        if verbose:
            print(f"{'='*60}")
            print(f"训练完成: {self._training_history['epoch']} epochs")
            print(f"  最终 val cosine sim: {val_cos:.4f}")
            print(f"{'='*60}")

        return self._training_history

    def train_on_history_batches(
        self,
        activation_sequences_batches: List[List[List[str]]],
        memory_embeddings: Dict[str, List[float]],
        epochs_per_batch: int = 50,
        lr: float = 0.001,
        verbose: bool = True,
    ) -> Dict[str, list]:
        """
        增量训练：分批在多个激活序列集合上训练

        适用于持续学习场景。

        Args:
            activation_sequences_batches: [[[id, ...], ...], ...]
            memory_embeddings: {id: embedding}
            epochs_per_batch: 每批的 epoch 数
            lr: 学习率
            verbose: 是否打印日志

        Returns:
            训练历史
        """
        full_history: Dict[str, List[Any]] = {"loss": [], "cosine_sim": []}

        for i, batch in enumerate(activation_sequences_batches):
            if verbose:
                print(f"\n增量训练阶段 {i + 1}/{len(activation_sequences_batches)}")

            history = self.train_on_history(
                activation_sequences=batch,
                memory_embeddings=memory_embeddings,
                epochs=epochs_per_batch,
                lr=lr,
                batch_size=32,
                verbose=verbose,
            )

            full_history["loss"].extend(history["loss"])
            full_history["cosine_sim"].extend(history["cosine_sim"])

        return full_history

    # ==================== 序列增强 ====================

    def predict_with_context_decay(
        self,
        context_embeddings: List[List[float]],
        decay_factor: float = 0.95,
    ) -> List[float]:
        """
        带上下文衰减的预测

        较旧的记忆对预测的影响逐渐衰减：
        - 第 seq_len-1 个 × 1.0
        - 第 seq_len-2 个 × decay_factor
        - 第 seq_len-3 个 × decay_factor²
        以此类推。

        Args:
            context_embeddings: [seq_len, input_dim]
            decay_factor: 衰减因子（0~1）

        Returns:
            predicted_embedding
        """
        if len(context_embeddings) == 0:
            return [0.0] * self.output_dim

        seq = self._pad_or_truncate(context_embeddings, self.seq_len)

        # 计算衰减权重
        weights = [decay_factor ** (self.seq_len - 1 - i) for i in range(self.seq_len)]

        # 加权输入 — 直接作用在输入层（替代 timespans 效果）
        weighted = []
        for emb, w in zip(seq, weights):
            weighted.append([v * w for v in emb])

        return self.predict_next(weighted)

    def predict_with_timespans(
        self,
        context_embeddings: List[List[float]],
        timestamps: List[str],
    ) -> List[float]:
        """
        带真实时间间隔的预测

        Args:
            context_embeddings: [seq_len, input_dim]
            timestamps: ISO 时间戳列表 [seq_len]

        Returns:
            predicted_embedding
        """
        if len(context_embeddings) < 2:
            return self.predict_next(context_embeddings)

        # 计算归一化时间间隔
        timespans = []
        for i in range(len(timestamps)):
            if i == 0:
                # 第一个时间步：从 0 到第一个事件的时间
                try:
                    t0 = datetime.fromisoformat(timestamps[0])
                    # 相对参考时间（最早时间前推 1 小时）
                    t_ref = t0 - timedelta(hours=1)
                    ts = (t0 - t_ref).total_seconds()
                except Exception:
                    ts = 1.0
            else:
                try:
                    t_prev = datetime.fromisoformat(timestamps[i - 1])
                    t_curr = datetime.fromisoformat(timestamps[i])
                    ts = (t_curr - t_prev).total_seconds()
                except Exception:
                    ts = 1.0

            # 归一化到 [0.1, 2.0]（避免极端值）
            ts_norm = max(0.1, min(2.0, math.log(ts + 1) / 10 + 0.5))
            timespans.append(ts_norm)

        return self.predict_next(context_embeddings, timespans=timespans)

    # ==================== 辅助方法 ====================

    @staticmethod
    def _pad_or_truncate(seq: List, target_len: int) -> List:
        """
        填充或截断序列到 target_len

        填充从序列头部补（用第一个元素的零向量补），
        确保最近的事件在序列尾部。
        """
        if len(seq) >= target_len:
            return seq[-target_len:]

        # 用第一个 embedding 的零向量补
        if len(seq) == 0:
            return []

        fill = [0.0] * len(seq[0])
        padding = [fill] * (target_len - len(seq))
        return padding + list(seq)

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """计算余弦相似度"""
        a_np = np.array(a, dtype=np.float32)
        b_np = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(a_np)
        norm_b = np.linalg.norm(b_np)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a_np, b_np) / (norm_a * norm_b))

    # ==================== 参数保存/加载 ====================

    def export_params(self) -> dict:
        """导出全部参数（用于持久化）"""
        state = self.state_dict()
        # torch.Tensor → list
        serialized = {
            k: v.tolist() if isinstance(v, torch.Tensor) else v
            for k, v in state.items()
        }
        return {
            "model_state": serialized,
            "config": {
                "input_dim": self.input_dim,
                "hidden_dim": self.hidden_dim,
                "output_dim": self.output_dim,
                "seq_len": self.seq_len,
                "device": str(self.device),
            },
            "training_history": self._training_history,
            "activation_history": self.history.to_dict(),
            "memory_index": {
                mid: emb
                for mid, emb in list(self._memory_index.items())[:1000]
                # 限制导出大小，防止超大文件
            },
        }

    def import_params(self, data: dict):
        """导入参数"""
        config = data.get("config", {})

        # 恢复配置
        if config:
            self.input_dim = config.get("input_dim", self.input_dim)
            self.hidden_dim = config.get("hidden_dim", self.hidden_dim)
            self.output_dim = config.get("output_dim", self.output_dim)
            self.seq_len = config.get("seq_len", self.seq_len)

        # 恢复模型状态
        model_state = data.get("model_state", {})
        if model_state:
            state_dict = {
                k: torch.tensor(v, device=self.device)
                for k, v in model_state.items()
            }
            # 只加载匹配的键（允许部分加载）
            current_keys = set(self.state_dict().keys())
            loadable = {k: v for k, v in state_dict.items() if k in current_keys}
            missing = current_keys - set(state_dict.keys())
            if missing:
                logger.warning(f"导入时缺失参数: {missing}")
            self.load_state_dict(loadable, strict=False)

        # 恢复训练历史
        self._training_history = data.get("training_history", self._training_history)

        # 恢复激活历史
        hist_data = data.get("activation_history")
        if hist_data:
            self.history = ActivationHistory.from_dict(hist_data)

        # 恢复记忆索引
        index = data.get("memory_index", {})
        if index:
            self._memory_index.update(index)

        logger.info(
            f"参数导入完成: {len(model_state)} 个权重张量, "
            f"{len(self._memory_index)} 条记忆索引"
        )

    def save(self, path: str):
        """保存模型到文件"""
        data = self.export_params()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"模型保存到: {path}")

    @classmethod
    def load(cls, path: str, device: str = "cpu") -> "CfCSequencePredictor":
        """从文件加载模型"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        config = data.get("config", {})
        predictor = cls(
            input_dim=config.get("input_dim", 16),
            hidden_dim=config.get("hidden_dim", 64),
            output_dim=config.get("output_dim"),
            seq_len=config.get("seq_len", 5),
            device=device,
        )
        predictor.import_params(data)
        logger.info(f"模型加载: {path}")
        return predictor


# ==================== 工具函数 ====================

def build_training_from_synapse_network(
    synapse_network,
    seq_len: int = 5,
    min_seq_len: int = 2,
) -> Tuple[List[List[str]], Dict[str, List[float]]]:
    """
    从 MemorySynapseNetwork 构建训练数据

    提取：
    1. activation_sequences: 所有神经元的激活时间线序列
    2. memory_embeddings:    所有神经元的 embedding

    Args:
        synapse_network: MemorySynapseNetwork 实例
        seq_len: 序列长度
        min_seq_len: 最小序列长度

    Returns:
        (activation_sequences, memory_embeddings)
    """
    network = synapse_network.network
    network._load()

    neurons = network._neurons_cache
    if not neurons:
        return [], {}

    # 按激活时间排序
    sorted_neurons = sorted(
        neurons.values(),
        key=lambda n: n.last_activated or n.created_at or "",
    )

    # 全局激活序列
    activation_sequence = [n.id for n in sorted_neurons]

    # 构建多序列（按时间窗口拆分）
    sequences = []
    window = max(seq_len + 1, min_seq_len + 1)
    for i in range(0, len(activation_sequence), window):
        chunk = activation_sequence[i:i + window + seq_len]
        if len(chunk) >= window:
            sequences.append(chunk)

    # memory_embeddings
    memory_embeddings = {
        n.id: n.embedding if n.embedding else [0.0] * 16
        for n in neurons.values()
    }

    return sequences, memory_embeddings


def generate_synthetic_training_data(
    num_memories: int = 30,
    num_sequences: int = 10,
    seq_length: int = 8,
    embedding_dim: int = 16,
    seed: int = 42,
) -> Tuple[List[List[str]], Dict[str, List[float]]]:
    """
    生成合成训练数据（用于测试）

    模拟以下使用模式：
    - 核心循环：一些记忆经常一起激活（话题连续性）
    - 话题转移：偶尔切换到另一个 topic
    - 随机扰动：一些孤立激活

    Args:
        num_memories: 记忆总数
        num_sequences: 序列数
        seq_length: 每个序列的长度
        embedding_dim: embedding 维度
        seed: 随机种子

    Returns:
        (activation_sequences, memory_embeddings)
    """
    rng = np.random.RandomState(seed)

    # 生成随机 embedding（话题群组）
    num_clusters = 4
    cluster_centers = rng.randn(num_clusters, embedding_dim) * 0.5
    cluster_centers = cluster_centers / np.linalg.norm(cluster_centers, axis=1, keepdims=True)

    memories_per_cluster = max(2, num_memories // num_clusters)
    memory_embeddings = {}
    memory_clusters = {}
    nid = 0

    for c in range(num_clusters):
        base = cluster_centers[c]
        for _ in range(memories_per_cluster):
            if nid >= num_memories:
                break
            mid = f"mem_{nid:04d}"
            noise = rng.randn(embedding_dim) * 0.1
            emb = base + noise
            emb = emb / np.linalg.norm(emb)
            memory_embeddings[mid] = emb.tolist()
            memory_clusters[mid] = c
            nid += 1

    # 生成序列：同一 cluster 内跳转 + 偶尔跨 cluster
    activation_sequences = []
    all_mids = list(memory_embeddings.keys())

    for _ in range(num_sequences):
        seq = []
        current_cluster = rng.randint(num_clusters)
        cluster_mids = [m for m, c in memory_clusters.items() if c == current_cluster]

        for _ in range(seq_length):
            # 70% 在同一个 cluster
            if current_cluster is not None and rng.random() < 0.7 and cluster_mids:
                mid = rng.choice(cluster_mids)
            else:
                mid = rng.choice(all_mids)
                current_cluster = memory_clusters.get(mid) or 0

            seq.append(mid)

        activation_sequences.append(seq)

    return activation_sequences, memory_embeddings


# ==================== 自测试 ====================

def _test_basic():
    """基础测试：创建预测器，基本前向"""
    print("\n1. 基本前向测试")

    predictor = CfCSequencePredictor(
        input_dim=16,
        hidden_dim=32,
        output_dim=16,
        seq_len=5,
        device="cpu",
    )

    # 模拟输入
    x = torch.randn(2, 5, 16)
    output = predictor.forward(x)

    assert output.shape == (2, 16), f"输出形状错误: {output.shape}"
    print(f"  ✓ 前向传播正常: {list(output.shape)}")

    return predictor


def _test_record_and_predict(predictor: CfCSequencePredictor):
    """测试记录和预测"""
    print("\n2. 记录和预测测试")

    # 注册记忆
    for i in range(10):
        mid = f"test_mem_{i}"
        emb = [math.sin(i * 0.5 + j * 0.1) for j in range(16)]
        predictor.record_activation(mid, emb, context_id="test_session")

    # 预测
    recent = predictor.history.get_recent_embeddings(5)
    assert len(recent) == 5, f"历史长度错误: {len(recent)}"
    print(f"  ✓ 历史记录: {predictor.history.size()} 事件")

    predicted = predictor.predict_next(recent)
    assert len(predicted) == 16, f"预测维度错误: {len(predicted)}"
    print(f"  ✓ 预测 embedding: 维度={len(predicted)}")

    # 带 timespans 预测
    predicted2 = predictor.predict_next(recent, timespans=[1.0, 0.8, 1.2, 0.5, 1.5])
    assert len(predicted2) == 16
    print("  ✓ 带 timespans 预测正常")


def _test_recommend(predictor: CfCSequencePredictor):
    """测试推荐"""
    print("\n3. 推荐测试")

    # 先用 predict_next 测试 recommend_from_embeddings
    memory_embeddings = {
        f"mem_{i}": [math.cos(i * 0.3 + j * 0.2) for j in range(16)]
        for i in range(20)
    }
    context = [[0.1 * (j + 1) for j in range(16)] for _ in range(5)]

    recs = predictor.recommend_from_embeddings(
        context, memory_embeddings, top_k=3
    )
    assert len(recs) <= 3
    print(f"  ✓ recommend_from_embeddings: {len(recs)} 条推荐")

    # 从历史推荐
    recs2 = predictor.recommend(top_k=3)
    print(f"  ✓ recommend from history: {len(recs2)} 条推荐")

    # 检查返回格式
    if recs2:
        mid, score = recs2[0]
        print(f"  顶部推荐: {mid} (score={score:.4f})")


def _test_context_decay(predictor: CfCSequencePredictor):
    """测试带衰退的预测"""
    print("\n4. 带上下文衰减的预测")

    context = [[float(j) for j in range(16)] for _ in range(5)]
    emb = predictor.predict_with_context_decay(context, decay_factor=0.9)
    assert len(emb) == 16
    print(f"  ✓ 衰减预测正常: 维度={len(emb)}")


def _test_export_import(predictor: CfCSequencePredictor):
    """测试导出/导入"""
    print("\n5. 导出/导入测试")

    data = predictor.export_params()
    assert "model_state" in data
    assert "config" in data
    print(f"  ✓ 导出: {len(data['model_state'])} 个权重")

    # 导入到新实例
    predictor2 = CfCSequencePredictor(
        input_dim=16,
        hidden_dim=32,
        output_dim=16,
        seq_len=5,
        device="cpu",
    )
    predictor2.import_params(data)

    # 验证一致性
    x = torch.randn(1, 5, 16)
    with torch.no_grad():
        out1 = predictor.forward(x)
        out2 = predictor2.forward(x)
    diff = (out1 - out2).abs().max().item()
    assert diff < 1e-5, f"导入后输出不一致: max diff={diff}"
    print(f"  ✓ 导入/导出一致性: max diff={diff:.2e}")


def _test_training():
    """测试训练循环"""
    print("\n6. 训练测试")

    predictor = CfCSequencePredictor(
        input_dim=16,
        hidden_dim=32,
        output_dim=16,
        seq_len=5,
        device="cpu",
    )

    # 生成合成数据
    sequences, embeddings = generate_synthetic_training_data(
        num_memories=20,
        num_sequences=5,
        seq_length=8,
        embedding_dim=16,
    )

    # 训练前预测
    context = [embeddings[sequences[0][i]] for i in range(5)]
    pre_pred = predictor.predict_next(context)

    # 训练
    history = predictor.train_on_history(
        sequences, embeddings,
        epochs=30,
        lr=0.01,
        batch_size=8,
        verbose=True,
    )

    assert len(history["loss"]) > 0
    final_loss = history["loss"][-1]
    print(f"  ✓ 训练完成: final loss={final_loss:.4f}")

    # 训练后预测
    post_pred = predictor.predict_next(context)

    # 验证预测不同（学习到了一些东西）
    diff = sum(abs(a - b) for a, b in zip(pre_pred, post_pred))
    print(f"  训练前后预测差异: {diff:.4f}")


def _test_synthetic_data():
    """测试合成数据生成"""
    print("\n7. 合成数据测试")

    sequences, embeddings = generate_synthetic_training_data(
        num_memories=30,
        num_sequences=8,
        seq_length=10,
        embedding_dim=16,
    )

    assert len(sequences) == 8
    assert len(embeddings) >= 20, f"embedding 数量不足: {len(embeddings)}"
    print(f"  ✓ 合成数据: {len(sequences)} 序列, {len(embeddings)} 记忆")

    # 检查 embedding 维度
    for mid, emb in embeddings.items():
        assert len(emb) == 16, f"embedding 维度错误: {mid}"
    print("  ✓ 所有 embedding 维度正确")


def _test_save_load(tmp_path: str = "/tmp/cfc_seq_test.json"):
    """测试保存/加载"""
    print("\n8. 保存/加载测试")

    predictor = CfCSequencePredictor(
        input_dim=16,
        hidden_dim=32,
        output_dim=16,
        seq_len=5,
        device="cpu",
    )

    # 写入一些状态
    for i in range(5):
        mid = f"save_mem_{i}"
        emb = [float(i + j) * 0.1 for j in range(16)]
        predictor.record_activation(mid, emb)

    # 保存
    predictor.save(tmp_path)

    # 加载
    predictor2 = CfCSequencePredictor.load(tmp_path)

    # 验证
    x = torch.randn(1, 5, 16)
    with torch.no_grad():
        out1 = predictor.forward(x)
        out2 = predictor2.forward(x)
    diff = (out1 - out2).abs().max().item()
    assert diff < 1e-5, f"保存/加载后输出不一致: {diff}"

    # 验证历史
    assert predictor2.history.size() == 5
    assert len(predictor2._memory_index) == 5

    print(f"  ✓ 保存/加载正常: max diff={diff:.2e}, 历史={predictor2.history.size()}")


def _test_empty_and_edge():
    """测试边界条件"""
    print("\n9. 边界条件测试")

    predictor = CfCSequencePredictor(
        input_dim=16,
        hidden_dim=32,
        output_dim=16,
        seq_len=5,
        device="cpu",
    )

    # 空序列预测
    empty_pred = predictor.predict_next([])
    assert all(v == 0.0 for v in empty_pred)
    print("  ✓ 空序列预测返回零向量")

    # 短序列预测
    short_pred = predictor.predict_next([[1.0] * 16] * 2)
    assert len(short_pred) == 16
    print("  ✓ 短序列预测正常")

    # 空推荐
    empty_recs = predictor.recommend(top_k=3)
    assert empty_recs == []
    print("  ✓ 空推荐返回空列表")

    # 空训练
    history = predictor.train_on_history([], {}, epochs=5)
    print("  ✓ 空训练不崩溃")


def main():
    """运行所有测试"""
    print("=" * 55)
    print("CfC 序列预测器 — 自测试")
    print("=" * 55)

    if not NCP_AVAILABLE:
        print("❌ ncps 未安装，测试跳过")
        print("  安装: pip install ncps")
        return 1

    try:
        predictor = _test_basic()
        _test_record_and_predict(predictor)
        _test_recommend(predictor)
        _test_context_decay(predictor)
        _test_export_import(predictor)
        _test_training()
        _test_synthetic_data()
        _test_save_load()
        _test_empty_and_edge()

        print()
        print("=" * 55)
        print("✅ 全部测试通过")
        print("=" * 55)
        return 0

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
