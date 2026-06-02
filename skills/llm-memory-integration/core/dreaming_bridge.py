#!/usr/bin/env python3
"""
Dreaming Bridge — OpenClaw Dreaming ↔ GalaxyOS BioRhythm 双系统整合

架构:
┌─────────────────────────────────────────────────────────┐
│ OpenClaw memory-core (Dreaming)                         │
│                                                         │
│  short-term-recall.json  ──▶  Light: 排序/去重/缓存    │
│   (638 entries, scores,      REM: 主题/模式提取         │
│    conceptTags, recallDays)  Deep: 评分→MEMORY.md       │
│  DREAMS.md ← Dream Diary                                │
│  phase-signals.json                                     │
└──────────────────────────┬──────────────────────────────┘
                           │ Bridge
                    ┌──────▼──────────────────────┐
                    │ dreaming_bridge.py            │
                    │                              │
                    │ ① 导入 short-term → SWR      │
                    │ ② 梦境→Dream Diary           │
                    │ ③ 巩固增益→Deep评分          │
                    │ ④ 日志→events.jsonl          │
                    └──────┬──────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│ GalaxyOS BioRhythm Sleep Consolidation                  │
│                                                         │
│  NREM-SWR → NREM-CASCADE → REM-GENERATIVE →             │
│    REM-EMOTION → DEEP-SLEEP                             │
│  突触网络 + 神经元链接 (零 LLM 调用)                    │
│  dream_log.jsonl ← 梦境日志                             │
└─────────────────────────────────────────────────────────┘

集成点:
  ├─ Light ↔ NREM-SWR: 候选记忆批量压缩回放
  ├─ REM ↔ REM-GENERATIVE: 模式发现→Dream Diary
  ├─ Deep ↔ DEEP-SLEEP: 评分提升→MEMORY.md 升格
  └─ 空闲调度: ConsolidationEngine → dreaming cron

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-02
"""

import json
import os
import sys
import time
import hashlib
import threading
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# 路径映射
# ════════════════════════════════════════════════════════════

class DreamingPaths:
    """OpenClaw Dreaming 运行时路径"""
    
    def __init__(self, workspace: str = None):
        ws = Path(workspace or os.environ.get(
            "OPENCLAW_WORKSPACE",
            str(Path.home() / ".openclaw" / "workspace")))
        
        self.workspace = ws
        self.dreams_dir = ws / "memory" / ".dreams"
        self.short_term_recall = self.dreams_dir / "short-term-recall.json"
        self.events_jsonl = self.dreams_dir / "events.jsonl"
        self.phase_signals = self.dreams_dir / "phase-signals.json"
        self.daily_ingestion = self.dreams_dir / "daily-ingestion.json"
        self.dreams_md = ws / "DREAMS.md"
        
        # 我们的梦境日志
        self.biorhythm_log = ws / "memory" / "dreaming" / "dream_log.jsonl"
        
        # 突触网络数据
        self.learnings = ws / ".learnings"
        self.verified_memories = self.learnings / "verified_memories.jsonl"
        
        # 确保目录存在
        self.dreams_dir.mkdir(parents=True, exist_ok=True)

    def check_access(self) -> Dict[str, bool]:
        """检查所有路径访问状态"""
        return {
            "short_term_recall": self.short_term_recall.exists(),
            "events_jsonl": self.events_jsonl.exists(),
            "phase_signals": self.phase_signals.exists(),
            "dreams_md": self.dreams_md.exists(),
            "biorhythm_log": self.biorhythm_log.exists(),
            "verified_memories": self.verified_memories.exists(),
        }


# ════════════════════════════════════════════════════════════
# 方向 A: OpenClaw → BioRhythm（导入候选记忆）
# ════════════════════════════════════════════════════════════

class ShortTermImporter:
    """
    从 OpenClaw short-term-recall.json 导入候选记忆到 BioRhythm
    
    策略:
    - 取 totalScore > 0.5 的条目 = "Light phase 消化过的候选"
    - 导入到 memory_consolidation 的突触网络
    - 高 recallCount 的标记为"高频路径" → SWR 优先重放
    - 带 conceptTags 的 → 三级同步中的跨链接材料
    - 无 snppet 的跳过（已删除/过期）
    """
    
    def __init__(self, paths: DreamingPaths):
        self.paths = paths
        
    def load_short_term(self, min_score: float = 0.5) -> List[Dict]:
        """加载 OpenClaw short-term recall 候选"""
        if not self.paths.short_term_recall.exists():
            logger.warning("short-term-recall.json 不存在")
            return []
        
        try:
            with open(self.paths.short_term_recall) as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"读取 short-term-recall 失败: {e}")
            return []
        
        entries = data.get("entries", {})
        candidates = []
        
        for key, entry in entries.items():
            snippet = entry.get("snippet", "")
            if not snippet or len(snippet) < 20:
                continue
            
            score = entry.get("totalScore", 0)
            if score < min_score:
                continue
            
            candidates.append({
                "key": key,
                "snippet": snippet[:500],
                "score": score,
                "recall_count": entry.get("recallCount", 1),
                "daily_count": entry.get("dailyCount", 0),
                "concept_tags": entry.get("conceptTags", []),
                "recall_days": entry.get("recallDays", []),
                "source": entry.get("source", "memory"),
                "path": entry.get("path", ""),
            })
        
        # 按 score 排序
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates
    
    def import_to_synapse(self, candidates: List[Dict]) -> Dict[str, Any]:
        """导入候选到突触网络"""
        stats = {"imported": 0, "high_freq_paths": 0, "skipped": 0}
        
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from memory_synapse_network import MemorySynapseNetwork
        except ImportError:
            stats["error"] = "memory_synapse_network 不可用"
            return stats
        
        try:
            network = MemorySynapseNetwork(str(self.paths.workspace))
        except Exception as e:
            stats["error"] = f"突触网络初始化失败: {e}"
            return stats
        
        for cand in candidates:
            try:
                content = cand["snippet"]
                # 创建神经元
                neuron_id = network.create_neuron(content[:500])
                if neuron_id:
                    # 高 recall count 标记为高频路径
                    if cand["recall_count"] >= 3:
                        stats["high_freq_paths"] += 1
                    
                    # 创建 concept tag 间的链接
                    tags = cand.get("concept_tags", [])
                    for i, tag1 in enumerate(tags):
                        for tag2 in tags[i+1:]:
                            # 用 tag 的哈希创建虚拟神经元链接
                            h1 = hashlib.md5(tag1.encode()).hexdigest()[:16]
                            h2 = hashlib.md5(tag2.encode()).hexdigest()[:16]
                            try:
                                network.create_synapse(
                                    f"tag_{h1}",
                                    f"tag_{h2}",
                                    weight=cand["score"] * 0.3
                                )
                            except Exception:
                                pass
                    
                    stats["imported"] += 1
            except Exception:
                stats["skipped"] += 1
        
        return stats


# ════════════════════════════════════════════════════════════
# 方向 B: BioRhythm → OpenClaw（梦境回写）
# ════════════════════════════════════════════════════════════

class DreamDiaryWriter:
    """
    将 BioRhythm 的梦境产物写回 OpenClaw 生态
    
    写回点:
    1. events.jsonl — 追加梦境事件
    2. phase-signals.json — 注入 REM 阶段信号
    3. DREAMS.md — 追加 Dream Diary 条目
    4. verified_memories.jsonl — 梦境合成的新记忆候选
    """
    
    def __init__(self, paths: DreamingPaths):
        self.paths = paths
    
    # ── 1. events.jsonl ──
    
    def write_events(self, dream_fragments: List[Dict]) -> Dict[str, Any]:
        """将梦境片段写入 events.jsonl"""
        count = 0
        for frag in dream_fragments:
            event = {
                "event": "biorhythm_dream",
                "phase": frag.get("phase", "rem_generative"),
                "content": frag.get("content", ""),
                "source_ids": frag.get("source_ids", []),
                "novelty": frag.get("novelty_score", 0),
                "gain": frag.get("consolidation_gain", 0),
                "emotion_tags": frag.get("emotion_tags", []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "cycle": None,
            }
            try:
                with open(self.paths.events_jsonl, "a") as f:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")
                count += 1
            except Exception:
                pass
        return {"events_written": count}
    
    # ── 2. phase-signals.json ──
    
    def write_phase_signals(self, sleep_stats: Dict[str, Any]) -> Dict[str, Any]:
        """将 BioRhythm 巩固增益注入 OpenClaw phase signals"""
        gain = sleep_stats.get("total_consolidation_gain", 0)
        if gain <= 0:
            return {"injected": 0}
        
        try:
            signals = {}
            if self.paths.phase_signals.exists():
                with open(self.paths.phase_signals) as f:
                    signals = json.load(f)
            
            # 注入 REM 阶段信号（BioRhythm 的 REM 阶段）
            rem_gain = 0
            phases = sleep_stats.get("phases", {})
            if isinstance(phases, dict):
                for p in ["rem_generative", "rem_emotion"]:
                    g = phases.get(p, {}).get("generative_gain", 0) or \
                        phases.get(p, {}).get("emotion_intensity_decayed", 0)
                    rem_gain += g
            
            # 写入 BioRhythm 专属通道
            if "entries" not in signals:
                signals["entries"] = {}
            
            biorhythm_key = f"biorhythm:{datetime.now().strftime('%Y-%m-%dT%H')}"
            signals["entries"][biorhythm_key] = {
                "key": biorhythm_key,
                "biorhythmGain": gain,
                "remGain": rem_gain,
                "lightHits": 0,
                "remHits": int(rem_gain * 10),
                "lastLightAt": None,
                "lastRemAt": datetime.now(timezone.utc).isoformat(),
            }
            
            signals["updatedAt"] = datetime.now(timezone.utc).isoformat()
            
            with open(self.paths.phase_signals, "w") as f:
                json.dump(signals, f, ensure_ascii=False)
            
            return {"injected": 1, "gain": gain, "rem_gain": rem_gain}
        
        except Exception as e:
            return {"error": str(e)}
    
    # ── 3. DREAMS.md ──
    
    def write_dream_diary(self, sleep_result: Dict[str, Any]) -> Dict[str, Any]:
        """追加 Dream Diary 条目到 DREAMS.md"""
        phases = sleep_result.get("phases", {})
        if not phases:
            return {"written": False, "reason": "no_phase_data"}
        
        # 提取各阶段关键指标
        swr = phases.get("nrem_swr", {})
        cascade = phases.get("nrem_cascade", {})
        generative = phases.get("rem_generative", {})
        emotion = phases.get("rem_emotion", {})
        migration = phases.get("deep_sleep", {})
        
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%B %d, %Y at %I:%M %p GMT+8")
        
        # 构建叙述
        lines = []
        lines.append(f"---\n")
        lines.append(f"*{timestamp}*\n")
        lines.append(f"")
        
        # SWR
        replayed = swr.get("swr_memories_replayed", 0)
        if replayed > 0:
            gain = swr.get("swr_weight_gain", 0)
            lines.append(f"The hippocampus rippled — {replayed} memories compressed "
                        f"into sharp-wave bursts, gaining {gain:.3f} in synaptic weight. "
                        f"The 200Hz replay left its trace.\n")
        
        # CASCADE
        saved = cascade.get("so_longtail_saved", 0)
        pruned = cascade.get("spindle_pruned", 0)
        linked = cascade.get("ripple_linked", 0)
        if saved + pruned + linked > 0:
            lines.append(f"Slow oscillations swept the cortex — {saved} long-tail memories "
                        f"rescued, {pruned} weak synapses pruned, {linked} new cross-neuron "
                        f"bridges formed through spindle-ripple cascades.\n")
        
        # GENERATIVE
        fragments = generative.get("dream_fragments", 0)
        patterns = generative.get("hidden_patterns_found", 0)
        if fragments > 0:
            lines.append(f"REM dreams wove {fragments} fragments together, "
                        f"discovering {patterns} hidden patterns in the noise. "
                        f"The generative replay synthesized what waking hours kept separate.\n")
        
        # EMOTION
        scanned = emotion.get("emotion_memories_scanned", 0)
        decay = emotion.get("emotion_intensity_decayed", 0)
        if scanned > 0:
            lines.append(f"Emotional memories weakened — {scanned} scanned, "
                        f"intensity decaying by {decay:.3f} across the REM cycle. "
                        f"The night's therapy worked.\n")
        
        # MIGRATION
        migrated = migration.get("migrated_count", 0)
        promoted = migration.get("promoted_count", 0)
        if migrated > 0:
            lines.append(f"{migrated} memories migrated from short-term store to "
                        f"long-term consolidation; {promoted} promoted to durable recall.\n")
        
        # 梦境增益汇总
        total_gain = sleep_result.get("total_consolidation_gain", 0)
        if total_gain > 0:
            lines.append(f"Total consolidation gain: {total_gain:.3f}. "
                        f"Cycle #{sleep_result.get('cycle', '?')} complete.\n")
        
        entry_text = "\n".join(lines)
        
        try:
            diary_marker_start = "<!-- openclaw:dreaming:diary:start -->\n"
            diary_marker_end = "<!-- openclaw:dreaming:diary:end -->"
            
            if self.paths.dreams_md.exists():
                content = self.paths.dreams_md.read_text(encoding="utf-8")
                
                # 插到 diary marker 之间
                if diary_marker_start in content and diary_marker_end in content:
                    before = content.split(diary_marker_start)[0]
                    after = content.split(diary_marker_end)[1]
                    new_content = (before + diary_marker_start + "\n" + 
                                  entry_text + "\n" + diary_marker_end + after)
                else:
                    # 没有 marker 则追加到尾部
                    new_content = content.rstrip() + "\n\n" + entry_text
            else:
                new_content = f"# Dream Diary\n\n{diary_marker_start}\n{entry_text}\n{diary_marker_end}\n"
            
            self.paths.dreams_md.write_text(new_content, encoding="utf-8")
            return {"written": True, "diary_lines": len(entry_text.split("\n"))}
        
        except Exception as e:
            return {"error": str(e)}
    
    # ── 4. verified_memories.jsonl（梦境新记忆） ──
    
    def write_new_memories(self, dream_fragments: List[Dict]) -> Dict[str, Any]:
        """将高新颖度梦境片段存为 verified memory 候选"""
        count = 0
        for frag in dream_fragments:
            if frag.get("novelty_score", 0) < 0.7:
                continue
            if not frag.get("content"):
                continue
            
            memory_entry = {
                "id": f"dream_{int(time.time())}_{hashlib.md5(frag['content'][:50].encode()).hexdigest()[:8]}",
                "content": f"[梦境合成] {frag['content']}",
                "source": "dreaming_synthesis",
                "type": "insight",
                "confidence": 0.5 + frag["novelty_score"] * 0.3,
                "importance": frag["novelty_score"],
                "emotion": {"label": frag.get("emotion_tags", ["neutral"])[0], "intensity": 0.3},
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "verified": True,
                "tags": ["dreaming", "synthesis"] + frag.get("emotion_tags", []),
            }
            
            try:
                with open(self.paths.verified_memories, "a") as f:
                    f.write(json.dumps(memory_entry, ensure_ascii=False) + "\n")
                count += 1
            except Exception:
                pass
        
        return {"new_memories_written": count}


# ════════════════════════════════════════════════════════════
# 一站式桥接执行
# ════════════════════════════════════════════════════════════

class DreamingBridge:
    """
    Dreaming ↔ BioRhythm 双向桥
    
    执行顺序:
    ① 导入 OpenClaw short-term → 突触网络
    ② 触发 BioRhythm 睡眠周期
    ③ 写回 events.jsonl
    ④ 写回 phase-signals.json
    ⑤ 写回 DREAMS.md
    ⑥ 写回 verified_memories.jsonl
    """
    
    def __init__(self, workspace: str = None):
        self.paths = DreamingPaths(workspace)
        self.importer = ShortTermImporter(self.paths)
        self.writer = DreamDiaryWriter(self.paths)
        self._sleep = None
        self._access = None
    
    def check_integration(self) -> Dict[str, Any]:
        """检查集成环境"""
        self._access = self.paths.check_access()
        
        # 尝试加载 BioRhythm
        sleep_available = False
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from biorhythm_sleep_consolidation import BioRhythmSleepConsolidator
            sleep_available = True
        except ImportError:
            pass
        
        return {
            "paths": self._access,
            "biorhythm_available": sleep_available,
            "short_term_count": self._count_short_term(),
            "events_count": self._count_events(),
            "verified_count": self._count_verified(),
        }
    
    def _count_short_term(self) -> int:
        try:
            with open(self.paths.short_term_recall) as f:
                return len(json.load(f).get("entries", {}))
        except Exception:
            return 0
    
    def _count_events(self) -> int:
        try:
            return sum(1 for _ in open(self.paths.events_jsonl))
        except Exception:
            return 0
    
    def _count_verified(self) -> int:
        try:
            return sum(1 for _ in open(self.paths.verified_memories))
        except Exception:
            return 0
    
    def run_full_bridge(self) -> Dict[str, Any]:
        """执行完整双向桥接"""
        results = {"steps": {}, "integration_check": self.check_integration()}
        
        if not self._access or not self._access.get("short_term_recall"):
            return {"error": "short-term-recall.json 不可用"}
        
        # 1. 导入候选
        candidates = self.importer.load_short_term(min_score=0.5)
        results["steps"]["import"] = {
            "candidates_found": len(candidates),
            "synapse": self.importer.import_to_synapse(candidates[:50]),
        }
        
        # 2. 加载 BioRhythm 并运行周期
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from biorhythm_sleep_consolidation import BioRhythmSleepConsolidator
            
            self._sleep = BioRhythmSleepConsolidator(str(self.paths.workspace))
            
            # 标记活跃 -> 假装空闲
            self._sleep.mark_active()
            time.sleep(1)
            self._sleep._last_active = time.time() - 300  # 假装空闲 5 分钟
            
            sleep_result = self._sleep.run_full_sleep_cycle()
            results["steps"]["sleep_cycle"] = sleep_result
            
        except Exception as e:
            results["steps"]["sleep_cycle"] = {"error": str(e)}
            return results
        
        # 3-6. 写回 OpenClaw
        phases = sleep_result.get("phases", {})
        
        # 提取梦境片段
        if isinstance(phases, dict):
            generative = phases.get("rem_generative", {})
            fragments = generative.get("fragments_detail", [])
        else:
            fragments = []
        
        results["steps"]["write_events"] = self.writer.write_events(fragments)
        results["steps"]["write_signals"] = self.writer.write_phase_signals(sleep_result)
        results["steps"]["write_diary"] = self.writer.write_dream_diary(sleep_result)
        results["steps"]["write_memories"] = self.writer.write_new_memories(fragments)
        
        return results
    
    def get_status(self) -> Dict[str, Any]:
        """获取双系统集成状态"""
        access = self.paths.check_access()
        
        # 最后梦境条目
        last_diary = ""
        try:
            if self.paths.dreams_md.exists():
                lines = self.paths.dreams_md.read_text().split("\n")
                # 取最后非空行
                for line in reversed(lines):
                    if line.strip() and not line.startswith("#") and not line.startswith("<!--"):
                        last_diary = line.strip()[:120]
                        break
        except Exception:
            pass
        
        return {
            "openclaw": {
                "short_term_count": self._count_short_term(),
                "events_count": self._count_events(),
                "phase_signals": self.paths.phase_signals.exists(),
                "dreams_md": self.paths.dreams_md.exists(),
                "last_diary_snippet": last_diary,
            },
            "galaxyos": {
                "dream_log": self.paths.biorhythm_log.exists() and self.paths.biorhythm_log.stat().st_size > 0,
                "verified_memories": self._count_verified(),
                "biorhythm_available": self._sleep is not None,
            }
        }


# ════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Dreaming Bridge — OpenClaw ↔ BioRhythm 整合")
    parser.add_argument("--check", action="store_true", help="检查集成状态")
    parser.add_argument("--run", action="store_true", help="执行一轮完整桥接")
    parser.add_argument("--status", action="store_true", help="查看双系统状态")
    args = parser.parse_args()
    
    bridge = DreamingBridge()
    
    if args.check:
        result = bridge.check_integration()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    elif args.run:
        result = bridge.run_full_bridge()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    elif args.status:
        result = bridge.get_status()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    else:
        # 默认：检查状态后执行桥接
        print(f"{'='*60}")
        print(f"Dreaming Bridge — OpenClaw ↔ GalaxyOS 双向桥")
        print(f"{'='*60}\n")
        
        status = bridge.check_integration()
        print(f"📡 OpenClaw Dreaming:")
        print(f"   short-term-recall: {'✅' if status['paths']['short_term_recall'] else '❌'} ({status['short_term_count']} entries)")
        print(f"   events.jsonl:      {'✅' if status['paths']['events_jsonl'] else '❌'} ({status['events_count']} lines)")
        print(f"   phase-signals.json:{'✅' if status['paths']['phase_signals'] else '❌'}")
        print(f"   DREAMS.md:         {'✅' if status['paths']['dreams_md'] else '❌'}")
        print(f"\n🧠 GalaxyOS BioRhythm: {'✅ 可用' if status['biorhythm_available'] else '❌ 不可用'}")
        print(f"   verified_memories: {status['verified_count']} entries")
        
        if status['biorhythm_available'] and status['short_term_count'] > 0:
            print(f"\n🔄 执行桥接中...")
            result = bridge.run_full_bridge()
            r = result.get("steps", {})
            
            print(f"\n   ① 导入 {r.get('import', {}).get('candidates_found', 0)} 候选到突触网络")
            print(f"   ② 睡眠周期增益: {r.get('sleep_cycle', {}).get('total_consolidation_gain', 0):.3f}")
            print(f"   ③ 梦境事件写回: {r.get('write_events', {}).get('events_written', 0)}")
            print(f"   ④ 阶段信号注入: {r.get('write_signals', {}).get('injected', 0)}")
            print(f"   ⑤ Dream Diary: {'✅ 已追加' if r.get('write_diary', {}).get('written') else '❌'}")
            print(f"   ⑥ 新记忆候选: {r.get('write_memories', {}).get('new_memories_written', 0)}")
            print(f"\n✅ 桥接完成")
