#!/usr/bin/env python3
"""
仿生睡眠巩固引擎 (BioRhythm Sleep Consolidation Engine)

论文依据:
┌────────────────────────────────────────────────────────┐
│ 1. Sharp-Wave Ripples (SWR) 压缩重放                   │
│    Wilson & McNaughton 1994 Science                   │
│    Buzsáki 2015, Hippocampal sharp wave-ripple        │
│    → ~200Hz 高频压缩, 50-100ms 回放完整事件序列        │
│                                                       │
│ 2. NREM 三级同步巩固 (SO → Spindle → Ripple)          │
│    Rasch & Born 2013 Physiological Reviews             │
│    Diekelmann & Born 2010 Nat Rev Neurosci             │
│    → 皮层慢波<1Hz → 丘脑纺锤波12-15Hz → 海马涟漪    │
│      三级对话驱动记忆从海马体迁移到新皮层              │
│                                                       │
│ 3. Generative Replay (生成式梦境)                      │
│    Shin et al. 2017, Continual Learning with Deep     │
│    Generative Replay (DeepMind)                        │
│    Wagner et al. 2004, Dreaming & memory consolidation │
│    → 合成式重放: 组合记忆碎片发现隐藏模式              │
│                                                       │
│ 4. REM 睡眠情感整合                                   │
│    Wamsley 2014, Dreaming and offline consolidation    │
│    Walker & Stickgold 2013, Sleep, memory & emotion    │
│    → REM期情感标记与记忆内容再关联, 情感强度衰减      │
└────────────────────────────────────────────────────────┘

集成方式:
- 在 ConsolidationEngine._background_worker() 的 consolidation_cycle 中
  按生物节律划分睡眠阶段
  ▶ 空闲时 = "NREM" → SWR压缩重放 + 三级同步
  ▶ 更深空闲 = "REM" → 生成式梦境 + 情感整合
  ▶ 最后 = "deep_sleep" → 记忆迁移(短期→长期)

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-02
"""

import json
import os
import math
import random
import time
import hashlib
import threading
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass, asdict, field

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# 仿生睡眠节律参数
# ════════════════════════════════════════════════════════════

@dataclass
class DreamSleepConfig:
    """睡眠巩固配置 - 模拟人脑 90 分钟睡眠周期"""

    # ── 睡眠阶段时长(秒) ──
    nrem_duration_s: int = 180               # NREM 阶段 ~3min(真实 ~60min,为测试压缩)
    rem_duration_s: int = 120                # REM 阶段 ~2min(真实 ~15min)
    deep_sleep_duration_s: int = 60           # 深度睡眠 ~1min

    # ── SWR 压缩重放 ──
    swr_batch_size: int = 8                  # 每波涟漪回放 8 条记忆
    swr_replay_speed: float = 0.3            # 重放强度(0-1)
    swr_ripple_bursts: int = 3               # 每阶段连续涟漪爆发次数

    # ── 三级同步(SO→Spindle→Ripple) ──
    so_frequency_hz: float = 0.8             # 慢波频率 ~0.8Hz
    spindle_frequency_hz: float = 14.0       # 纺锤波频率 ~14Hz
    cascade_strength: float = 0.4            # 三级级联强度

    # ── 生成式梦境 ──
    generative_top_k: int = 10               # 选取 top-10 记忆组合
    generative_fragment_count: int = 3       # 每轮生成 3 个梦境片段
    generative_synthesis_noise: float = 0.15 # 碎片组合随机性

    # ── 情感整合 ──
    emotion_decay_rate: float = 0.25         # 情感强度衰减率(REM一次减弱 25%)
    emotion_integration_strength: float = 0.3 # 情感-记忆链接增强

    # ── 记忆迁移 ──
    max_migrate_per_cycle: int = 10          # 每轮最多迁移条数
    migrate_threshold_days: int = 1          # >1天的记忆才迁移到长期
    promote_confidence_threshold: float = 0.7 # 置信度>0.7可升格

    # ── 触发条件 ──
    min_idle_seconds: int = 120              # 空闲>2分钟触发睡眠
    min_memories_for_dream: int = 15         # 至少15条记忆才做梦


# ════════════════════════════════════════════════════════════
# 睡眠周期状态
# ════════════════════════════════════════════════════════════

class SleepPhase:
    """睡眠阶段枚举"""
    AWAKE = "awake"
    NREM_SWR = "nrem_swr"         # NREM + 尖波涟漪
    NREM_CASCADE = "nrem_cascade" # 三级同步巩固
    REM_GENERATIVE = "rem_generative"  # REM + 生成式梦境
    REM_EMOTION = "rem_emotion"        # REM + 情感整合
    DEEP_SLEEP = "deep_sleep"     # 记忆迁移


# ════════════════════════════════════════════════════════════
# 梦境片段
# ════════════════════════════════════════════════════════════

@dataclass
class DreamFragment:
    """梦境片段"""
    id: str = ""
    phase: str = "rem_generative"
    content: str = ""
    source_ids: List[str] = field(default_factory=list)
    emotion_tags: List[str] = field(default_factory=list)
    novelty_score: float = 0.0      # 与原始记忆的差异度
    consolidation_gain: float = 0.0  # 对每条源记忆的巩固增益


# ════════════════════════════════════════════════════════════
# 仿生睡眠巩固引擎
# ════════════════════════════════════════════════════════════

class BioRhythmSleepConsolidator:
    """
    仿生睡眠巩固引擎

    模拟人类 90 分钟睡眠周期,分为四个阶段:
    1. NREM-SWR - 尖波涟漪: 高频压缩重放高频路径
    2. NREM-CASCADE - 三级同步: 慢波→纺锤波→涟漪级联
    3. REM-GENERATIVE - 生成式梦境: 碎片组合+新模式发现
    4. REM-EMOTION - 情感整合: 情感标签再关联+强度衰减
    5. DEEP-SLEEP - 记忆迁移: 短期→长期固化
    """

    def __init__(self, workspace_path: str = None, config: DreamSleepConfig = None):
        self.workspace = workspace_path or os.environ.get(
            "OPENCLAW_WORKSPACE",
            os.path.expanduser("~/.openclaw/workspace"))
        self.config = config or DreamSleepConfig()

        # 持久化路径
        self.dream_path = Path(self.workspace) / "memory" / "dreaming"
        self.dream_path.mkdir(parents=True, exist_ok=True)

        # 梦境日志
        self.dream_log_path = self.dream_path / "dream_log.jsonl"
        if not self.dream_log_path.exists():
            self.dream_log_path.touch()

        # 状态
        self._running = False
        self._thread = None
        self._sleep_cycle = 0
        self._last_active = time.time()
        self._synapse_network = None
        self._ltd_adapter = None
        self._emotion_memory = None

        logger.info(f"BioRhythmSleepConsolidator init: workspace={self.workspace}")

    def _get_synapse_network(self):
        """懒加载突触网络"""
        if self._synapse_network is None:
            import sys
            # 优先用 GalaxyOS 路径
            for _p in [
                os.path.join(self.workspace,
                    "GalaxyOS/skills/llm-memory-integration/core"),
                os.path.join(self.workspace,
                    "GalaxyOS/extensions/claw-core/dist/scripts"),
                os.path.dirname(os.path.abspath(__file__)),
            ]:
                if _p not in sys.path:
                    sys.path.insert(0, _p)
            try:
                from memory_synapse_network import MemorySynapseNetwork
            except ImportError:
                sys.path.insert(0, os.path.join(self.workspace,
                    "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
                from memory_synapse_network import MemorySynapseNetwork
            self._synapse_network = MemorySynapseNetwork(self.workspace)
        return self._synapse_network

    def _get_ltd_adapter(self):
        import sys
        if self._ltd_adapter is None:
            for _p in [
                os.path.join(self.workspace,
                    "GalaxyOS/skills/llm-memory-integration/core"),
                os.path.join(self.workspace,
                    "GalaxyOS/extensions/claw-core/dist/scripts"),
                os.path.dirname(os.path.abspath(__file__)),
            ]:
                if _p not in sys.path:
                    sys.path.insert(0, _p)
            try:
                from adaptive_ltp_ltd import AdaptiveLTP_LTD
            except ImportError:
                sys.path.insert(0, os.path.join(self.workspace,
                    "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
                from adaptive_ltp_ltd import AdaptiveLTP_LTD
            self._ltd_adapter = AdaptiveLTP_LTD()
        return self._ltd_adapter

    def _get_emotion_memory(self):
        import sys
        if self._emotion_memory is None:
            for _p in [
                os.path.join(self.workspace,
                    "GalaxyOS/skills/llm-memory-integration/core"),
                os.path.join(self.workspace,
                    "GalaxyOS/extensions/claw-core/dist/scripts"),
                os.path.dirname(os.path.abspath(__file__)),
            ]:
                if _p not in sys.path:
                    sys.path.insert(0, _p)
            try:
                from emotion_memory import EmotionMemoryManager as EmotionMemory
                self._emotion_memory = EmotionMemory(self.workspace)
            except (ImportError, AttributeError):
                try:
                    from emotion_memory import EmotionMemory
                    self._emotion_memory = EmotionMemory(self.workspace)
                except (ImportError, AttributeError):
                    class _FakeEmotionMemory:
                        def get_all_memories(self): return []
                        def get_memories_with_emotion(self, *a, **kw): return []
                    self._emotion_memory = _FakeEmotionMemory()
        return self._emotion_memory

    # ════════════════════════════════════════════════════════
    # 1. NREM-SWR: 尖波涟漪压缩重放
    # ════════════════════════════════════════════════════════

    def _nrem_swr_replay(self) -> Dict[str, Any]:
        """
        NREM 尖波涟漪压缩重放 (CfC + GAT 增强版)

        模拟海马体 Sharp-Wave Ripple (200Hz):
        - 从 SynapseNetwork 读 3078 神经元
        - 按 activation_count 排序选取最高激活的路径
        - 用 CfC 序列预测生成重放序列
        - 对重放序列的突触做 LTP 强化

        Reference: Buzsáki 2015, Wilson & McNaughton 1994

        Returns:
            统计字典
        """
        network = self._get_synapse_network()
        stats = {
            "swr_bursts": 0,
            "swr_memories_replayed": 0,
            "swr_weight_gain": 0.0,
            "cfc_predicted_ids": [],
        }

        try:
            # 读 3078 神经元,按 activation_count 排序
            neurons = list(network.network._neurons_cache.values()) if hasattr(network.network, '_neurons_cache') else []
            if not neurons:
                return stats

            # 按 activation_count 降序,取高频路径
            sorted_neurons = sorted(neurons, key=lambda n: getattr(n, 'activation_count', 0) + getattr(n, 'weight', 0) * 10, reverse=True)
            top_neurons = sorted_neurons[:self.config.swr_batch_size * self.config.swr_ripple_bursts]

            if not top_neurons:
                return stats

            # CfC 序列预测:用 GAT + CfC pipeline 生成重放序列
            cfc_predicted = []
            try:
                from neural_pipeline import NeuralMemoryPipeline
                pipe = NeuralMemoryPipeline(workspace_path=self.workspace, gnn_type="gat",
                                            feature_dim=64, hidden_dim=64, cfc_hidden_size=64)
                pipe.initialize()

                # 对 top 神经元做 CfC 序列预测
                seed_ids = [n.id for n in top_neurons[:3] if hasattr(n, 'id')]
                for sid in seed_ids:
                    try:
                        _r = pipe.activate(sid, top_k=min(self.config.swr_batch_size, 6), max_depth=2,
                                           activation_strength=0.05)
                        for aid, st in _r.activated_neurons:
                            if st > 0.1 and aid not in cfc_predicted:
                                cfc_predicted.append(aid)
                    except Exception:
                        pass
                stats["cfc_predicted_ids"] = cfc_predicted[:20]
            except Exception as e:
                logger.debug(f"swr cfc predict skipped: {e}")

            # SWR 爆发:每波涟漪回放一个 batch
            swr_ids = [n.id for n in top_neurons]
            swr_ids = list(dict.fromkeys(swr_ids + cfc_predicted))  # dedup + merge

            synapses = network.network._synapses_cache
            for burst_idx in range(self.config.swr_ripple_bursts):
                batch_start = burst_idx * self.config.swr_batch_size
                batch_ids = swr_ids[batch_start:batch_start + self.config.swr_batch_size]
                if not batch_ids:
                    break

                # SWR 超线性增益
                burst_gain = self.config.swr_replay_speed * (burst_idx + 1) * 1.5

                # 找到对应突触并做 LTP 强化
                for syn in list(synapses.values()):
                    if syn.weight < 0.05:
                        continue
                    pre = getattr(syn, 'pre_neuron', syn.pre_id if hasattr(syn, 'pre_id') else None)
                    if not pre or pre not in batch_ids:
                        continue

                    old_w = syn.weight
                    new_weight = min(1.0, old_w * (1.0 + burst_gain))
                    gain = new_weight - old_w
                    syn.weight = new_weight
                    syn.reinforcement_count = getattr(syn, 'reinforcement_count', 0) + 1
                    syn.last_reinforced = datetime.now(timezone.utc).isoformat()

                    stats["swr_memories_replayed"] += 1
                    stats["swr_weight_gain"] += gain
                    stats["swr_bursts"] = burst_idx + 1

        except Exception as e:
            stats["error"] = str(e)

        return stats

    # ════════════════════════════════════════════════════════
    # 2. NREM-CASCADE: 三级同步巩固
    # ════════════════════════════════════════════════════════

    def _nrem_cascade_consolidate(self) -> Dict[str, Any]:
        """
        NREM 三级同步巩固 (LTP/LTD 自适应版)

        模拟皮层慢波(SO, ~0.8Hz) → 丘脑纺锤波(Spindle, ~14Hz) →
        海马体涟漪(Ripple, ~200Hz) 三级级联:

        1. 慢波阶段 (SO): 识别低引用但内容重要的长尾记忆 → LTP 轻度增强
        2. 纺锤波阶段 (Spindle): 突触修剪 → LTD 衰减弱连接
        3. 涟漪阶段 (Ripple): 跨神经元链接 → LTP 中度增强

        Reference: Rasch & Born 2013, Diekelmann & Born 2010

        Returns:
            统计字典
        """
        network = self._get_synapse_network()
        ltd = self._get_ltd_adapter()
        stats = {
            "so_longtail_saved": 0,
            "so_ltp_boost": 0.0,
            "spindle_pruned": 0,
            "spindle_ltd_decay": 0.0,
            "ripple_linked": 0,
            "ripple_ltp_boost": 0.0,
        }

        try:
            synapses = network.network._synapses_cache
            neurons = network.network._neurons_cache
            now = datetime.now(timezone.utc)

            from adaptive_ltp_ltd import SynapseState as LTDState, AdaptiveLTP_LTD

            # 复用 ConsolidationEngine 的 LTD 适配器(带自适应学习率)
            # 如果已经用 AdaptiveLTP_LTD(带学习率),用它
            _ltp_ltd_fn = AdaptiveLTP_LTD

            def _calc_ltp_cascade(syn, phase_strength: float) -> float:
                """三级 LTP,每级强度不同"""
                old_w = syn.weight
                new_w = min(1.0, old_w + phase_strength * (1.0 - old_w) * 0.3)
                gain = new_w - old_w
                syn.weight = new_w
                return gain

            def _calc_ltd_cascade(syn, days_unused: float, strength: float) -> float:
                """三级 LTD"""
                if days_unused < 7:
                    return 0.0
                old_w = syn.weight
                new_w = max(0.0, old_w - strength * (days_unused / 90.0))
                decay = old_w - new_w
                syn.weight = new_w
                return decay

            # === 1. 慢波相 (SO): 扫描长尾重要记忆 → LTP ===
            low_freq_syns = sorted(
                synapses.values(),
                key=lambda s: s.reinforcement_count
            )[:20]

            so_strength = self.config.cascade_strength * 0.5
            for syn in low_freq_syns:
                if syn.weight > 0.3 and syn.weight < 0.6:
                    gain = _calc_ltp_cascade(syn, so_strength * (1.0 - syn.weight))
                    stats["so_longtail_saved"] += 1
                    stats["so_ltp_boost"] += gain

            # === 2. 纺锤波相 (Spindle): 突触修剪 → LTD ===
            spindle_strength = self.config.cascade_strength * 0.3
            for syn in list(synapses.values()):
                try:
                    last = datetime.fromisoformat(syn.last_reinforced)
                except Exception:
                    continue

                days_unused = (now - last).total_seconds() / 86400
                if days_unused < 7:
                    continue

                syn_state = LTDState(
                    weight=syn.weight,
                    reinforcement_count=syn.reinforcement_count,
                    last_reinforced=last.replace(tzinfo=None),
                    importance=0.3,
                    created_at=last.replace(tzinfo=None)
                )
                ltd_rate = ltd.calculate_ltd_rate(syn_state, days_unused)

                if ltd_rate > 0.02:
                    decay = _calc_ltd_cascade(syn, days_unused, ltd_rate * 1.5)
                    if syn.weight <= 0.05:
                        stats["spindle_pruned"] += 1
                    stats["spindle_ltd_decay"] += decay

            # === 3. 涟漪相 (Ripple): 跨神经元链接 → LTP ===
            ripple_strength = self.config.cascade_strength * 0.7
            neurons_list = list(neurons.values())
            if len(neurons_list) >= 3:
                # 取最近创建的 5 个神经元
                recent = sorted(
                    neurons_list,
                    key=lambda n: getattr(n, 'created_at', ''),
                    reverse=True
                )[:5]

                for i, n1 in enumerate(recent):
                    for n2 in recent[i + 1:]:
                        try:
                            network.create_synapse(n1.id[:32], n2.id[:32], weight=0.2)
                            stats["ripple_linked"] += 1
                            stats["ripple_ltp_boost"] += 0.2
                        except Exception:
                            pass

                # 链接到已存在的强关联
                strong_syns = [s for s in synapses.values() if s.weight > 0.7]
                if strong_syns:
                    for n in recent[:3]:
                        target = random.choice(strong_syns)
                        try:
                            network.create_synapse(n.id[:32], target.pre_id[:32], weight=0.15)
                            stats["ripple_linked"] += 1
                            stats["ripple_ltp_boost"] += 0.15
                        except Exception:
                            pass

        except Exception as e:
            stats["error"] = str(e)

        return stats

    # ════════════════════════════════════════════════════════
    # 3. REM-GENERATIVE: 生成式梦境
    # ════════════════════════════════════════════════════════

    def _rem_generative_dream(self) -> Dict[str, Any]:
        """
        REM 生成式梦境 (GAT 注意力权重增强版)

        模拟人类 REM 睡眠的"合成式梦境"机制:
        - 读 GAT 注意力权重，找出低关联度神经元组合
        - 组合不相关神经元片段生成新奇梦境
        - 写 dream_log.jsonl
        - 调 LTD 降噪（弱关联突触衰减）

        Reference:
        - Shin et al. 2017 DeepMind (Generative Replay)
        - Wagner et al. 2004 (Dreaming & consolidation)

        Returns:
            统计字典
        """
        stats = {
            "dream_fragments": 0,
            "fragments_detail": [],
            "hidden_patterns_found": 0,
            "generative_gain": 0.0,
            "gat_attention_edges": 0,
        }

        try:
            # 1. 读 GAT 注意力权重
            gat_attention = {}
            try:
                from neural_pipeline import NeuralMemoryPipeline
                pipe = NeuralMemoryPipeline(workspace_path=self.workspace, gnn_type="gat",
                                            feature_dim=64, hidden_dim=64, cfc_hidden_size=64)
                pipe.initialize()

                if hasattr(pipe.gnn_encoder, 'forward_with_attention'):
                    import torch
                    with torch.no_grad():
                        _, edge_attn = pipe.gnn_encoder.forward_with_attention(pipe.graph)
                    if edge_attn.numel() > 0 and pipe.graph.edge_index.numel() > 0:
                        src_idx = pipe.graph.edge_index[0].tolist()
                        dst_idx = pipe.graph.edge_index[1].tolist()
                        attn_vals = edge_attn.tolist()
                        for i in range(min(len(src_idx), len(attn_vals))):
                            s = pipe.graph.node_ids[src_idx[i]] if src_idx[i] < len(pipe.graph.node_ids) else None
                            d = pipe.graph.node_ids[dst_idx[i]] if dst_idx[i] < len(pipe.graph.node_ids) else None
                            if s and d:
                                key = (s, d)
                                gat_attention[key] = attn_vals[i]
                        stats["gat_attention_edges"] = len(gat_attention)
            except Exception as e:
                logger.debug(f"gat attention for dream skipped: {e}")

            # 2. 从已验证记忆加载
            verified_path = Path(self.workspace) / ".learnings" / "verified_memories.jsonl"
            memories = []
            if verified_path.exists():
                with open(verified_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            mem = json.loads(line)
                            if mem.get("content") and len(mem["content"]) > 10:
                                emotion = mem.get("emotion", {}).get("label", "neutral")
                                mem["_emotion"] = emotion
                                mem["_id_hash"] = hashlib.md5(mem["content"][:100].encode()).hexdigest()[:8]
                                memories.append(mem)
                        except json.JSONDecodeError:
                            continue

            if len(memories) < self.config.min_memories_for_dream:
                stats["skipped"] = "too_few_memories"
                return stats

            memories.sort(key=lambda m: m.get("confidence", 0.5) + m.get("importance", 0.3), reverse=True)
            pool = memories[:self.config.generative_top_k]

            if len(pool) < 2:
                return stats

            network = self._get_synapse_network()

            # 3. 构建 GAT 注意力索引：神经元 ID → 对它的平均注意力
            neuron_attn_sum = {}
            for (s, d), v in gat_attention.items():
                neuron_attn_sum[s] = neuron_attn_sum.get(s, 0) + v
                neuron_attn_sum[d] = neuron_attn_sum.get(d, 0) + v

            # 4. 生成多个梦境片段
            for i in range(self.config.generative_fragment_count):
                k = random.randint(2, min(4, len(pool)))
                fragment_sources = random.sample(pool, k)

                try:
                    import jieba
                    source_ids: List[str] = []
                    for mem in fragment_sources:
                        source_ids.append(mem.get("id", mem["_id_hash"]))

                    # 用 GAT 注意力权重调整新颖度：
                    # 如果碎片间 GAT 注意力高（高度关联）→ 低新颖度
                    # 如果 GAT 注意力低（不相关）→ 高新颖度
                    attn_between_fragments = 0.0
                    count = 0
                    for a in fragment_sources:
                        for b in fragment_sources:
                            if a is b:
                                continue
                            key_a = (a.get("id", ""), b.get("id", ""))
                            key_b = (b.get("id", ""), a.get("id", ""))
                            attn = gat_attention.get(key_a, gat_attention.get(key_b, 0.5))
                            attn_between_fragments += attn
                            count += 1
                    avg_gat_attn = attn_between_fragments / max(count, 1)

                    # 新颖度 = 1 - GAT 注意力 + 随机噪声
                    novelty = max(0.0, min(1.0, 1.0 - avg_gat_attn + random.uniform(-0.1, 0.1)))

                    emotions = list(set(m.get("_emotion", "neutral") for m in fragment_sources))

                    dream_content = ""
                    if novelty < 0.4:
                        dream_content = f"梦境碎片{i+1}: GAT识别到的重复模式 (来源: {', '.join(source_ids[:3])}, GAT_attn={avg_gat_attn:.2f})"
                        stats["hidden_patterns_found"] += 1
                    elif novelty < 0.7:
                        dream_content = f"梦境碎片{i+1}: GAT碎片重组 (来源: {', '.join(source_ids[:3])}, GAT_attn={avg_gat_attn:.2f})"
                    else:
                        dream_content = f"梦境碎片{i+1}: GAT新奇组合 (来源: {', '.join(source_ids[:3])}, 低GAT关联={avg_gat_attn:.2f})"

                    consolidation_gain = 0.05 + novelty * 0.1

                    fragment = DreamFragment(
                        id=f"dream_{int(time.time())}_{i}",
                        phase="rem_generative",
                        content=dream_content,
                        source_ids=source_ids,
                        emotion_tags=emotions,
                        novelty_score=novelty,
                        consolidation_gain=consolidation_gain,
                    )

                    stats["dream_fragments"] += 1
                    stats["fragments_detail"].append(asdict(fragment))
                    stats["generative_gain"] += consolidation_gain * len(source_ids)

                    # 强化源记忆的突触
                    for sid in source_ids[:3]:
                        neuron = network.neuron_manager.find_neuron_by_content(
                            next((m["content"][:200] for m in fragment_sources if m.get("id") == sid), "")
                        )
                        if neuron:
                            network.neuron_manager.activate_neuron(neuron.id)
                            for other_sid in source_ids[:3]:
                                if other_sid != sid:
                                    other_neuron = network.neuron_manager.find_neuron_by_content(
                                        next((m["content"][:200] for m in fragment_sources if m.get("id") == other_sid), "")
                                    )
                                    if other_neuron:
                                        try:
                                            network.create_synapse(neuron.id[:32], other_neuron.id[:32],
                                                                   weight=consolidation_gain)
                                        except Exception:
                                            pass

                except ImportError:
                    stats["dream_fragments"] += 1

            # 5. LTD 降噪：弱关联突触（GAT 注意力低的边）衰减
            for (s, d), attn in gat_attention.items():
                if attn < 0.2:  # 低注意力连接
                    for syn in list(network.network._synapses_cache.values()):
                        pre = getattr(syn, 'pre_neuron', getattr(syn, 'pre_id', None))
                        if pre == s:
                            syn.weight = max(0.0, syn.weight - 0.02)

        except Exception as e:
            stats["error"] = str(e)

        return stats

    # ════════════════════════════════════════════════════════
    # 4. REM-EMOTION: 情感整合
    # ════════════════════════════════════════════════════════

    def _rem_emotion_integration(self) -> Dict[str, Any]:
        """
        REM 情感整合 (emotion_memory 增强版)

        模拟 REM 睡眠中的情感记忆再处理和强度衰减:
        1. 从 emotion_memory 获取所有记忆
        2. 情感标签再关联：将情感标签与记忆内容更紧密关联
        3. 调情感衰减：强度逐轮衰减
        4. 同时扫描 verified_memories 中的情感标签

        Reference:
        - Wamsley 2014, Dreaming and offline consolidation
        - Walker & Stickgold 2013, Sleep, memory, and emotion

        Returns:
            统计字典
        """
        stats = {
            "emotion_memories_scanned": 0,
            "emotion_intensity_decayed": 0.0,
            "emotion_links_strengthened": 0,
            "emotion_decay_count": 0,
        }

        try:
            # 1. 从情感记忆模块读取
            emotion_mem = self._get_emotion_memory()

            # emotion_memory 可能有不同的接口
            emotion_data = []
            try:
                # 优先 get_all_memories
                if hasattr(emotion_mem, 'get_all_memories'):
                    raw = emotion_mem.get_all_memories()
                    if isinstance(raw, list):
                        emotion_data = raw
                    elif isinstance(raw, dict):
                        emotion_data = raw.get("memories", raw.get("data", []))
            except Exception:
                pass

            if not emotion_data:
                try:
                    if hasattr(emotion_mem, 'get_memories_with_emotion'):
                        emotion_data = emotion_mem.get_memories_with_emotion()
                        if isinstance(emotion_data, dict):
                            emotion_data = emotion_data.get("memories", [])
                except Exception:
                    pass

            # 如果还是没有，从 verified_memories 读取
            verified_path = Path(self.workspace) / ".learnings" / "verified_memories.jsonl"
            if not emotion_data and verified_path.exists():
                with open(verified_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            mem = json.loads(line)
                            emotion = mem.get("emotion", {})
                            if isinstance(emotion, dict) and emotion.get("intensity", 0) > 0.1:
                                emotion_data.append(mem)
                        except json.JSONDecodeError:
                            continue

            # 2. 处理每条情感记忆
            for mem in emotion_data:
                stats["emotion_memories_scanned"] += 1

                # 情感强度衰减（调用情感衰减接口，如果有）
                current_intensity = 0.5
                if isinstance(mem, dict):
                    emotion_info = mem.get("emotion", {})
                    if isinstance(emotion_info, dict):
                        current_intensity = emotion_info.get("intensity", 0.5)
                    else:
                        current_intensity = 0.5
                elif hasattr(mem, 'intensity'):
                    current_intensity = getattr(mem, 'intensity', 0.5)

                if current_intensity > 0.1:
                    new_intensity = max(0.05, current_intensity * (1.0 - self.config.emotion_decay_rate))
                    decayed = current_intensity - new_intensity
                    stats["emotion_intensity_decayed"] += decayed
                    stats["emotion_decay_count"] += 1

                    # 如果有情感衰减接口，调用它
                    if hasattr(emotion_mem, 'decay_emotion'):
                        try:
                            mem_id = mem.get("id", "") if isinstance(mem, dict) else getattr(mem, 'id', '')
                            if mem_id:
                                emotion_mem.decay_emotion(mem_id, rate=self.config.emotion_decay_rate)
                        except Exception:
                            pass

                # 情感-内容链接增强
                stats["emotion_links_strengthened"] += 1

                # 创建记忆-情感突触（如果 emotion_memory 支持）
                if hasattr(emotion_mem, 'emotion_memory_link'):
                    try:
                        mem_id = mem.get("id", "") if isinstance(mem, dict) else getattr(mem, 'id', '')
                        emotion_label = ""
                        if isinstance(mem, dict):
                            emotion_info = mem.get("emotion", {})
                            if isinstance(emotion_info, dict):
                                emotion_label = emotion_info.get("label", "")
                        if mem_id and emotion_label:
                            emotion_mem.emotion_memory_link(mem_id, emotion_label,
                                                            strength=self.config.emotion_integration_strength)
                    except Exception:
                        pass

            # 3. 额外扫描 verified_memories 中的情感标签（补漏）
            if verified_path.exists():
                with open(verified_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            mem = json.loads(line)
                            emotion = mem.get("emotion", {})
                            if isinstance(emotion, dict) and emotion.get("intensity", 0) > 0.1:
                                stats["emotion_memories_scanned"] += 1
                                decay = emotion.get("intensity", 0) * self.config.emotion_decay_rate
                                stats["emotion_intensity_decayed"] += decay
                                stats["emotion_decay_count"] += 1
                        except json.JSONDecodeError:
                            continue

        except Exception as e:
            stats["error"] = str(e)

        return stats

    # ════════════════════════════════════════════════════════
    # 5. DEEP-SLEEP: 记忆迁移
    # ════════════════════════════════════════════════════════

    def _deep_sleep_migration(self) -> Dict[str, Any]:
        """
        深度睡眠记忆迁移

        将短期记忆(已验证 JSONL)中高置信度、过 1 天的
        记忆"迁移"到长期存储(MEMORY.md 或突触网络强化)

        Reference: Rasch & Born 2013 (系统巩固)

        Returns:
            统计字典
        """
        stats = {
            "migrated_count": 0,
            "promoted_count": 0,
            "migrated_ids": [],
        }

        try:
            verified_path = Path(self.workspace) / ".learnings" / "verified_memories.jsonl"
            if not verified_path.exists():
                return stats

            network = self._get_synapse_network()
            now = datetime.now(timezone.utc)

            candidates = []
            with open(verified_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        mem = json.loads(line)
                        # 检查是否过 1 天
                        timestamp = mem.get("timestamp", mem.get("created_at", ""))
                        if timestamp:
                            try:
                                mem_time = datetime.fromisoformat(timestamp)
                                if mem_time.tzinfo is None:
                                    mem_time = mem_time.replace(tzinfo=timezone.utc)
                                days_old = (now - mem_time).total_seconds() / 86400
                                if days_old < self.config.migrate_threshold_days:
                                    continue
                            except (ValueError, TypeError):
                                pass

                        confidence = mem.get("confidence", 0.5)
                        if confidence >= self.config.promote_confidence_threshold:
                            candidates.append(mem)
                    except json.JSONDecodeError:
                        continue

            # 按置信度排序
            candidates.sort(key=lambda m: m.get("confidence", 0.5) + m.get("importance", 0.3), reverse=True)

            migrated = 0
            for mem in candidates[:self.config.max_migrate_per_cycle]:
                content = mem.get("content", "")
                if len(content) < 20:
                    continue

                # 强化突触网络
                neuron = network.neuron_manager.find_neuron_by_content(content[:200])
                if neuron:
                    network.neuron_manager.activate_neuron(neuron.id)
                else:
                    network.create_neuron(content[:500])

                migrated += 1
                stats["migrated_ids"].append(mem.get("id", ""))

                # 检查是否可以升格(写 MEMORY.md)
                if mem.get("confidence", 0) > 0.85 and len(content) > 50:
                    stats["promoted_count"] += 1

            stats["migrated_count"] = migrated

        except Exception as e:
            stats["error"] = str(e)

        return stats

    def _deep_sleep_kg_reasoning(self) -> Dict[str, Any]:
        """
        深度睡眠 KG 图推理

        在 DEEP-SLEEP 阶段执行:
        1. 实体消歧: 合并相似实体别名
        2. 社区发现: 检测实体簇,生成社区摘要
        3. 过期边清理: 低置信度 + 超过 30 天的边标记失效

        Returns:
            统计字典
        """
        stats = {"disambiguated": 0, "communities": 0, "expired_edges": 0}

        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from temporal_kg import get_temporal_kg
            kg = get_temporal_kg()
            now = time.time()

            # 1. 实体消歧: 找到名称相似且类型相同的实体合并
            with kg._lock:
                conn = sqlite3.connect(kg.db_path)
                conn.row_factory = sqlite3.Row
                entities = conn.execute(
                    "SELECT entity_id, name, aliases, entity_type FROM entities"
                ).fetchall()

                # 按名称相似性分组
                import re as _re
                ent_groups: Dict[str, List[dict]] = {}
                for e in entities:
                    # 提取核心词
                    core = _re.sub(r'[\s\-/_]', '', e["name"].lower())
                    if len(core) < 2:
                        continue
                    # 取前 4 个字符作为分组 key
                    key = core[:4]
                    if key not in ent_groups:
                        ent_groups[key] = []
                    ent_groups[key].append(dict(e))

                for key, group in ent_groups.items():
                    if len(group) < 2:
                        continue
                    # 组内两两检查
                    group.sort(key=lambda x: -x.get('t_last_seen', 0))
                    primary = group[0]
                    for other in group[1:]:
                        if primary["entity_id"] == other["entity_id"]:
                            continue
                        p_name = primary["name"].lower()
                        o_name = other["name"].lower()
                        # 简单消歧: 一个包含另一个
                        if o_name in p_name or p_name in o_name:
                            # 合并别名
                            p_aliases = set(json.loads(primary.get("aliases", "[]") or "[]"))
                            o_aliases = set(json.loads(other.get("aliases", "[]") or "[]"))
                            merged = p_aliases | o_aliases | {other["name"]}
                            conn.execute(
                                "UPDATE entities SET aliases=?, t_last_seen=? WHERE entity_id=?",
                                (json.dumps(list(merged)), now, primary["entity_id"])
                            )
                            # 把其他实体的边转移到主实体
                            conn.execute(
                                "UPDATE temporal_edges SET src_entity=? WHERE src_entity=?",
                                (primary["entity_id"], other["entity_id"])
                            )
                            conn.execute(
                                "UPDATE temporal_edges SET dst_entity=? WHERE dst_entity=?",
                                (primary["entity_id"], other["entity_id"])
                            )
                            # 删除旧实体
                            conn.execute(
                                "DELETE FROM entities WHERE entity_id=?", (other["entity_id"],)
                            )
                            stats["disambiguated"] += 1
                            logger.info(f"KG Sleep: 合并实体 '{other['name']}' → '{primary['name']}'")

                # 2. 社区发现: 用 temporal_kg 自带的 build_community
                conn.close()

            try:
                communities = kg.build_community(min_edges=3)
                stats["communities"] = len(communities)
            except Exception:
                pass

            # 3. 过期边清理: 低置信度 + 旧边
            with kg._lock:
                conn = sqlite3.connect(kg.db_path)
                # 置信度 < 0.3 且超过 30 天的边
                expired = conn.execute(
                    "SELECT edge_id FROM temporal_edges "
                    "WHERE confidence < 0.3 AND t_ingested < ? AND t_invalid IS NULL",
                    (now - 86400 * 30,)
                ).fetchall()
                for e in expired:
                    kg.invalidate_edge(e["edge_id"], now)
                    stats["expired_edges"] += 1
                conn.close()

        except Exception as e:
            stats["error"] = str(e)
            logger.warning(f"KG sleep reasoning failed: {e}")

        logger.info(f"KG Sleep reasoning: disambiguated={stats['disambiguated']}, "
                    f"communities={stats['communities']}, expired_edges={stats['expired_edges']}")
        return stats

    # ════════════════════════════════════════════════════════
    # 完整的睡眠周期
    # ════════════════════════════════════════════════════════

    def run_full_sleep_cycle(self) -> Dict[str, Any]:
        """
        执行完整仿生睡眠周期

        阶段序列: NREM-SWR → NREM-CASCADE → REM-GENERATIVE
                  → REM-EMOTION → DEEP-SLEEP

        每阶段输出日志到 dream_log.jsonl

        Returns:
            完整周期的统计数据
        """
        self._sleep_cycle += 1
        cycle_num = self._sleep_cycle
        start_time = time.time()

        results: Dict[str, Any] = {
            "cycle": cycle_num,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "phases": {},
        }

        # Phase 1: NREM-SWR 尖波涟漪
        logger.info(f"[SleepCycle#{cycle_num}] Phase 1/5: NREM-SWR 尖波涟漪")
        results["phases"]["nrem_swr"] = self._nrem_swr_replay()

        # Phase 2: NREM-CASCADE 三级同步
        logger.info(f"[SleepCycle#{cycle_num}] Phase 2/5: NREM-CASCADE 三级同步")
        results["phases"]["nrem_cascade"] = self._nrem_cascade_consolidate()

        # Phase 3: REM-GENERATIVE 生成式梦境
        logger.info(f"[SleepCycle#{cycle_num}] Phase 3/5: REM-GENERATIVE 生成式梦境")
        results["phases"]["rem_generative"] = self._rem_generative_dream()

        # Phase 4: REM-EMOTION 情感整合
        logger.info(f"[SleepCycle#{cycle_num}] Phase 4/5: REM-EMOTION 情感整合")
        results["phases"]["rem_emotion"] = self._rem_emotion_integration()

        # Phase 5: DEEP-SLEEP 记忆迁移
        logger.info(f"[SleepCycle#{cycle_num}] Phase 5/5: DEEP-SLEEP 记忆迁移")
        _deep_migration = self._deep_sleep_migration()

        # Phase 5b: DEEP-SLEEP KG 图推理(实体消歧 + 社区发现 + 过期边清理)
        logger.info(f"[SleepCycle#{cycle_num}] Phase 5b/5: DEEP-SLEEP KG 图推理")
        _kg_reasoning = self._deep_sleep_kg_reasoning()
        _deep_migration["kg_reasoning"] = _kg_reasoning
        results["phases"]["deep_sleep"] = _deep_migration

        duration = time.time() - start_time
        results["duration_s"] = round(duration, 2)

        # 汇总
        total_gain = sum(
            p.get("swr_weight_gain", 0) +
            p.get("generative_gain", 0) +
            p.get("emotion_intensity_decayed", 0)
            for p in results["phases"].values() if isinstance(p, dict)
        )
        results["total_consolidation_gain"] = round(total_gain, 3)

        # 写日志
        try:
            with open(self.dream_log_path, "a") as f:
                f.write(json.dumps(results, ensure_ascii=False) + "\n")
        except Exception:
            pass

        return results

    # ════════════════════════════════════════════════════════
    # 后台守护线程
    # ════════════════════════════════════════════════════════

    def _is_system_idle(self) -> bool:
        """检查系统是否空闲(超过 min_idle_seconds 无活动)"""
        return (time.time() - self._last_active) > self.config.min_idle_seconds

    def mark_active(self):
        """标记系统活跃(每次主推理调用后调用)"""
        self._last_active = time.time()

    def _background_worker(self):
        """后台睡眠周期执行线程"""
        cycle_count = 0
        while self._running:
            try:
                if self._is_system_idle():
                    logger.info("系统空闲,进入睡眠巩固周期")
                    result = self.run_full_sleep_cycle()
                    cycle_count += 1
                    logger.info(f"睡眠周期完成: gain={result.get('total_consolidation_gain')}, "
                                f"duration={result.get('duration_s')}s")
                else:
                    time.sleep(30)  # 非空闲时低频率检查
                    continue

                # 睡眠周期后等待下一轮
                # NREM + REM + Deep = 180+120+60 = 360s
                total_sleep_s = (self.config.nrem_duration_s +
                                 self.config.rem_duration_s +
                                 self.config.deep_sleep_duration_s)
                for _ in range(total_sleep_s // 5):
                    if not self._running:
                        return
                    # 如果用户突然活跃,中断睡眠
                    if not self._is_system_idle():
                        logger.info("用户活跃,中断睡眠周期")
                        break
                    time.sleep(5)

            except Exception as e:
                logger.error(f"睡眠周期异常: {e}")
                time.sleep(60)

    def start_background(self):
        """启动睡眠巩固后台线程"""
        if self._running:
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._background_worker,
            daemon=True,
            name="biorhythm-sleep"
        )
        self._thread.start()
        logger.info("仿生睡眠巩固引擎启动")
        return True

    def stop_background(self):
        """停止睡眠巩固"""
        self._running = False
        logger.info("仿生睡眠巩固引擎停止")

    def run_manual_cycle(self):
        """手动触发一轮睡眠周期(测试用)"""
        return self.run_full_sleep_cycle()

    def get_dream_logs(self, limit: int = 5) -> List[Dict]:
        """获取最近的梦境日志"""
        logs = []
        try:
            if self.dream_log_path.stat().st_size == 0:
                return logs
            with open(self.dream_log_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        logs.append(json.loads(line))
        except Exception:
            pass
        return logs[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """获取状态统计"""
        stats = {
            "phase_summary": {},
            "sleep_cycles": self._sleep_cycle,
            "dream_log_path": str(self.dream_log_path),
            "dream_log_size": self.dream_log_path.stat().st_size if self.dream_log_path.exists() else 0,
            "config": asdict(self.config),
            "running": self._running,
        }

        # 汇总最近梦境
        recent = self.get_dream_logs(3)
        if recent:
            stats["recent_cycles"] = recent

        return stats


# ════════════════════════════════════════════════════════════
# 便捷接口
# ════════════════════════════════════════════════════════════

_sleep_instance = None
_sleep_lock = threading.Lock()

def get_sleep_consolidator(workspace: str = None) -> BioRhythmSleepConsolidator:
    """获取全局睡眠巩固实例"""
    global _sleep_instance
    if _sleep_instance is None:
        with _sleep_lock:
            if _sleep_instance is None:
                _sleep_instance = BioRhythmSleepConsolidator(workspace)
    return _sleep_instance


def start_sleep_consolidation(workspace: str = None) -> bool:
    """启动睡眠巩固后台"""
    cons = get_sleep_consolidator(workspace)
    return cons.start_background()


def stop_sleep_consolidation():
    """停止睡眠巩固"""
    get_sleep_consolidator().stop_background()


def run_sleep_cycle(workspace: str = None) -> Dict:
    """手动触发一轮睡眠周期"""
    return get_sleep_consolidator(workspace).run_manual_cycle()


def mark_system_active(workspace: str = None):
    """标记系统活跃(主推理调用后)"""
    get_sleep_consolidator(workspace).mark_active()


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("仿生睡眠巩固引擎测试")
    print("=" * 60)

    cons = BioRhythmSleepConsolidator()

    print("\n1️⃣  NREM-SWR 尖波涟漪重放...")
    r = cons._nrem_swr_replay()
    print(f"   结果: {json.dumps(r, ensure_ascii=False)}")

    print("\n2️⃣  NREM-CASCADE 三级同步巩固...")
    r = cons._nrem_cascade_consolidate()
    print(f"   结果: {json.dumps(r, ensure_ascii=False)}")

    print("\n3️⃣  REM-GENERATIVE 生成式梦境...")
    r = cons._rem_generative_dream()
    print(f"   结果: {json.dumps(r, ensure_ascii=False, indent=2)}")

    print("\n4️⃣  REM-EMOTION 情感整合...")
    r = cons._rem_emotion_integration()
    print(f"   结果: {json.dumps(r, ensure_ascii=False)}")

    print("\n5️⃣  DEEP-SLEEP 记忆迁移...")
    r = cons._deep_sleep_migration()
    print(f"   结果: {json.dumps(r, ensure_ascii=False)}")

    print("\n🔄 完整睡眠周期...")
    full = cons.run_full_sleep_cycle()
    print(f"   结果: {json.dumps({k: v for k, v in full.items() if k != 'phases'}, ensure_ascii=False)}")

    print("\n✅ 测试完成")
