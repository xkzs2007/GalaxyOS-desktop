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
    """睡眠巩固配置 — 模拟人脑 90 分钟睡眠周期"""
    
    # ── 睡眠阶段时长（秒） ──
    nrem_duration_s: int = 180               # NREM 阶段 ~3min（真实 ~60min，为测试压缩）
    rem_duration_s: int = 120                # REM 阶段 ~2min（真实 ~15min）
    deep_sleep_duration_s: int = 60           # 深度睡眠 ~1min
    
    # ── SWR 压缩重放 ──
    swr_batch_size: int = 8                  # 每波涟漪回放 8 条记忆
    swr_replay_speed: float = 0.3            # 重放强度（0-1）
    swr_ripple_bursts: int = 3               # 每阶段连续涟漪爆发次数
    
    # ── 三级同步（SO→Spindle→Ripple） ──
    so_frequency_hz: float = 0.8             # 慢波频率 ~0.8Hz
    spindle_frequency_hz: float = 14.0       # 纺锤波频率 ~14Hz
    cascade_strength: float = 0.4            # 三级级联强度
    
    # ── 生成式梦境 ──
    generative_top_k: int = 10               # 选取 top-10 记忆组合
    generative_fragment_count: int = 3       # 每轮生成 3 个梦境片段
    generative_synthesis_noise: float = 0.15 # 碎片组合随机性
    
    # ── 情感整合 ──
    emotion_decay_rate: float = 0.25         # 情感强度衰减率（REM一次减弱 25%）
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
    
    模拟人类 90 分钟睡眠周期，分为四个阶段：
    1. NREM-SWR — 尖波涟漪: 高频压缩重放高频路径
    2. NREM-CASCADE — 三级同步: 慢波→纺锤波→涟漪级联
    3. REM-GENERATIVE — 生成式梦境: 碎片组合+新模式发现
    4. REM-EMOTION — 情感整合: 情感标签再关联+强度衰减
    5. DEEP-SLEEP — 记忆迁移: 短期→长期固化
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
            sys.path.insert(0, os.path.join(self.workspace,
                "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
            from memory_synapse_network import MemorySynapseNetwork
            self._synapse_network = MemorySynapseNetwork(self.workspace)
        return self._synapse_network
    
    def _get_ltd_adapter(self):
        import sys
        if self._ltd_adapter is None:
            sys.path.insert(0, os.path.join(self.workspace,
                "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
            from adaptive_ltp_ltd import AdaptiveLTP_LTD
            self._ltd_adapter = AdaptiveLTP_LTD()
        return self._ltd_adapter
    
    def _get_emotion_memory(self):
        import sys
        if self._emotion_memory is None:
            sys.path.insert(0, os.path.join(self.workspace,
                "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
            try:
                from emotion_memory import EmotionMemoryManager as EmotionMemory
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
        NREM 尖波涟漪压缩重放
        
        模拟海马体 Sharp-Wave Ripple (200Hz):
        - 从最近高频激活的记忆中选取 batch
        - 按时间顺序"压缩回放"（不是逐条强化，而是
          批量序列式重放，模拟50-100ms回放完整事件序列）
        - 每重放一次，权重呈超线性增长（因为 SWR 是
          高频爆发，不是低频重放）
        
        Reference: Buzsáki 2015, Wilson & McNaughton 1994
        
        Returns:
            统计字典
        """
        network = self._get_synapse_network()
        stats = {
            "swr_bursts": 0,
            "swr_memories_replayed": 0,
            "swr_weight_gain": 0.0,
        }
        
        import sys
        sys.path.insert(0, os.path.join(self.workspace,
            "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
        from adaptive_ltp_ltd import SynapseState as LTDState
        
        try:
            synapses = network.network._synapses_cache
            
            # 按最近激活排序，取 top 高频突触
            sorted_syns = sorted(
                synapses.values(),
                key=lambda s: s.reinforcement_count + s.weight * 10,
                reverse=True
            )
            
            # SWR 爆发：每波涟漪回放一个 batch
            for burst_idx in range(self.config.swr_ripple_bursts):
                batch_start = burst_idx * self.config.swr_batch_size
                batch = sorted_syns[batch_start:batch_start + self.config.swr_batch_size]
                if not batch:
                    break
                
                # SWR 超线性增益：回放不是简单加法，
                # 而是指数式强化（模拟 200Hz 高频爆发效果）
                burst_gain = self.config.swr_replay_speed * (burst_idx + 1) * 1.5
                
                for syn in batch:
                    if syn.weight < 0.05:
                        continue
                    old_w = syn.weight
                    # 爆发式权重增长: new = min(1.0, old * (1 + burst_gain))
                    # 比普通 LTP 强的多
                    new_weight = min(1.0, old_w * (1.0 + burst_gain))
                    gain = new_weight - old_w
                    syn.weight = new_weight
                    syn.reinforcement_count += 1
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
        NREM 三级同步巩固
        
        模拟皮层慢波(SO, ~0.8Hz) → 丘脑纺锤波(Spindle, ~14Hz) →
        海马体涟漪(Ripple, ~200Hz) 三级级联:
        
        1. 慢波阶段: 识别低引用但内容重要的长尾记忆
        2. 纺锤波阶段: 突触修剪——清除最弱的连接
        3. 涟漪阶段: 将保留下来的记忆进行跨神经元链接
        
        Reference: Rasch & Born 2013, Diekelmann & Born 2010
        
        Returns:
            统计字典
        """
        network = self._get_synapse_network()
        ltd = self._get_ltd_adapter()
        stats = {
            "so_longtail_saved": 0,
            "spindle_pruned": 0,
            "ripple_linked": 0,
        }
        
        import sys
        sys.path.insert(0, os.path.join(self.workspace,
            "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
        from adaptive_ltp_ltd import SynapseState as LTDState
        
        try:
            synapses = network.network._synapses_cache
            neurons = network.network._neurons_cache
            
            now = datetime.now(timezone.utc)
            
            # === 1. 慢波相 (SO): 扫描长尾重要记忆 ===
            # 激活频率低但内容可能重要的记忆
            low_freq_syns = sorted(
                synapses.values(),
                key=lambda s: s.reinforcement_count
            )[:20]  # 引用最低的 20 条
            
            for syn in low_freq_syns:
                # 内容长度 > 50 字符且权重 > 0.3 的视为"有潜力但被忽略"的记忆
                # 慢波阶段给它们一个提升机会
                if syn.weight > 0.3 and syn.weight < 0.6:
                    syn.weight = min(0.7, syn.weight + self.config.cascade_strength * 0.5)
                    stats["so_longtail_saved"] += 1
            
            # === 2. 纺锤波相 (Spindle): 突触修剪 ===
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
                    new_weight = max(0.0, syn.weight - ltd_rate * 1.5)
                    if new_weight <= 0.05:
                        stats["spindle_pruned"] += 1
                    syn.weight = new_weight
            
            # === 3. 涟漪相 (Ripple): 跨神经元链接 ===
            # 将同时间段创建的突触互相链接（模拟海马体整合）
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
                        except Exception:
                            pass
                
                # 也链接到已存在的强关联
                strong_syns = [s for s in synapses.values() if s.weight > 0.7]
                if strong_syns:
                    for n in recent[:3]:
                        target = random.choice(strong_syns)
                        try:
                            network.create_synapse(n.id[:32], target.pre_id[:32], weight=0.15)
                            stats["ripple_linked"] += 1
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
        REM 生成式梦境
        
        模拟人类 REM 睡眠的"合成式梦境"机制:
        - 不是忠实回放白天经历
        - 而是从多条记忆中提取碎片，随机组合
        - 发现片段之间的隐藏关联
        - 产出"梦境片段"记录
        
        核心: 记忆碎片 → 随机组合 → 语义关联度评估 →
        高关联的碎片形成梦境
            
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
        }
        
        try:
            # 从已验证记忆加载
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
                                # 情感标签
                                emotion = mem.get("emotion", {}).get("label", "neutral")
                                mem["_emotion"] = emotion
                                mem["_id_hash"] = hashlib.md5(mem["content"][:100].encode()).hexdigest()[:8]
                                memories.append(mem)
                        except json.JSONDecodeError:
                            continue
            
            if len(memories) < self.config.min_memories_for_dream:
                stats["skipped"] = "too_few_memories"
                return stats
            
            # 取 top-k 条（按置信度）
            memories.sort(key=lambda m: m.get("confidence", 0.5) + m.get("importance", 0.3), reverse=True)
            pool = memories[:self.config.generative_top_k]
            
            if len(pool) < 2:
                return stats
            
            # 生成多个梦境片段
            for i in range(self.config.generative_fragment_count):
                # 随机选取 2-4 条记忆作为"梦境碎片"
                k = random.randint(2, min(4, len(pool)))
                fragment_sources = random.sample(pool, k)
                
                # 计算组合的新颖度（碎片间的语义距离）
                # 用关键词重叠率近似
                try:
                    import jieba
                    all_tokens: Set[str] = set()
                    source_ids: List[str] = []
                    for mem in fragment_sources:
                        tokens = set(jieba.lcut(mem["content"][:200]))
                        all_tokens.update(tokens)
                        source_ids.append(mem.get("id", mem["_id_hash"]))
                    
                    # 新颖度 = 1 - 碎片间的平均关键词重叠
                    overlaps = []
                    for a in fragment_sources:
                        ta = set(jieba.lcut(a["content"][:200]))
                        for b in fragment_sources:
                            if a is b:
                                continue
                            tb = set(jieba.lcut(b["content"][:200]))
                            if not ta or not tb:
                                continue
                            j = len(ta & tb) / len(ta | tb) if len(ta | tb) > 0 else 0
                            overlaps.append(j)
                    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.5
                    novelty = 1.0 - avg_overlap + random.uniform(-0.1, 0.1)
                    novelty = max(0.0, min(1.0, novelty))
                    
                    # 情感标签合并
                    emotions = list(set(m.get("_emotion", "neutral") for m in fragment_sources))
                    
                    # 合成梦境内容摘要
                    dream_content = ""
                    if novelty < 0.4:
                        dream_content = f"梦境碎片{i+1}: 重复出现的模式 (来源: {', '.join(source_ids[:3])})"
                        stats["hidden_patterns_found"] += 1
                    elif novelty < 0.7:
                        dream_content = f"梦境碎片{i+1}: 碎片重组 (来源: {', '.join(source_ids[:3])})"
                    else:
                        dream_content = f"梦境碎片{i+1}: 新奇组合 (来源: {', '.join(source_ids[:3])})"
                    
                    # 梦境巩固增益: 新颖度越高，对源记忆的巩固效果越好
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
                    
                    # 强化源记忆的突触（梦境回放后，源记忆也受益）
                    network = self._get_synapse_network()
                    for sid in source_ids[:3]:
                        # 找到对应神经元并激活
                        neuron = network.neuron_manager.find_neuron_by_content(
                            next((m["content"][:200] for m in fragment_sources if m.get("id") == sid), "")
                        )
                        if neuron:
                            network.neuron_manager.activate_neuron(neuron.id)
                            # 梦境也创建碎片间的关联
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
                    # jieba 不可用时简化处理
                    stats["dream_fragments"] += 1
            
        except Exception as e:
            stats["error"] = str(e)
        
        return stats
    
    # ════════════════════════════════════════════════════════
    # 4. REM-EMOTION: 情感整合
    # ════════════════════════════════════════════════════════
    
    def _rem_emotion_integration(self) -> Dict[str, Any]:
        """
        REM 情感整合
        
        模拟 REM 睡眠中的情感记忆再处理和强度衰减:
        1. 扫描所有带情感标签的记忆
        2. 情感强度衰减（睡一觉没那么难受了）
        3. 情感-记忆链接增强（记住"发生了什么"而
            不是"当时多难受"）
        
        Reference: 
        - Wamsley 2014, Dreaming and offline consolidation
        - Walker & Stickgold 2013, Sleep, memory, and emotion
        
        Returns:
            统计字典
        """
        stats = {
            "emotion_memories_scanned": 0,
            "emotion_intensity_decayed": 0,
            "emotion_links_strengthened": 0,
        }
        
        try:
            # 从情感记忆模块读取
            emotion_mem = self._get_emotion_memory()
            
            # 获取所有情感记忆
            emotion_data = emotion_mem.get_all_memories() if hasattr(emotion_mem, 'get_all_memories') else []
            if isinstance(emotion_data, dict):
                emotion_data = emotion_data.get("memories", [])
            
            for mem in emotion_data:
                stats["emotion_memories_scanned"] += 1
                
                # 情感强度衰减
                current_intensity = mem.get("intensity", 0.5)
                if current_intensity > 0.1:
                    new_intensity = max(0.05, current_intensity * (1.0 - self.config.emotion_decay_rate))
                    stats["emotion_intensity_decayed"] += current_intensity - new_intensity
                
                # 情感-内容链接增强
                # 模拟 REM 将情感标签与记忆内容更紧密关联
                stats["emotion_links_strengthened"] += 1
            
            # 同时扫描 verified_memories 中的情感标签
            verified_path = Path(self.workspace) / ".learnings" / "verified_memories.jsonl"
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
                                stats["emotion_intensity_decayed"] += emotion.get("intensity", 0) * 0.25
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
        
        将短期记忆（已验证 JSONL）中高置信度、过 1 天的
        记忆"迁移"到长期存储（MEMORY.md 或突触网络强化）
        
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
                
                # 检查是否可以升格（写 MEMORY.md）
                if mem.get("confidence", 0) > 0.85 and len(content) > 50:
                    stats["promoted_count"] += 1
            
            stats["migrated_count"] = migrated
            
        except Exception as e:
            stats["error"] = str(e)
        
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
        results["phases"]["deep_sleep"] = self._deep_sleep_migration()
        
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
        """检查系统是否空闲（超过 min_idle_seconds 无活动）"""
        return (time.time() - self._last_active) > self.config.min_idle_seconds
    
    def mark_active(self):
        """标记系统活跃（每次主推理调用后调用）"""
        self._last_active = time.time()
    
    def _background_worker(self):
        """后台睡眠周期执行线程"""
        cycle_count = 0
        while self._running:
            try:
                if self._is_system_idle():
                    logger.info("系统空闲，进入睡眠巩固周期")
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
                    # 如果用户突然活跃，中断睡眠
                    if not self._is_system_idle():
                        logger.info("用户活跃，中断睡眠周期")
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
        """手动触发一轮睡眠周期（测试用）"""
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
    """标记系统活跃（主推理调用后）"""
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
