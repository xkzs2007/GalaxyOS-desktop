#!/usr/bin/env python3
"""
LFM Skill Bank — 将 COSPLAY 的 Skill Bank 架构移植到 GalaxyOS LFM

架构映射:
  COSPLAY Contract Learning  →  从记忆轨迹自动推断效果契约
  COSPLAY Bank Maintenance  →  Merge/Split/Refine/Retire 五操作
  COSPLAY Skill + Protocol  →  LFM 记忆银行 + 突触网络联动
  COSPLAY MemoryStore/RAG   →  LFM embedding 检索增强

集成入口:
  memory_consolidation.py: ConsolidationEngine._run_consolidation_cycle()

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-23 (移植自 COSPLAY: arxiv 2604.20987)
"""

from __future__ import annotations

import json
import math
import os
import time
import hashlib
import logging
import numpy as np
from collections import Counter, defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple, Callable
from galaxyos.shared.paths import workspace

logger = logging.getLogger("lfm_skill_bank")


# ════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════

@dataclass
class LfmSkillBankConfig:
    """Skill Bank 全部配置参数"""

    # ── Effects / Contract Learning ──
    eff_freq: float = 0.8                  # 效果字面量最低出现频率
    min_instances_per_skill: int = 3       # 学契约所需最少实例数
    max_effects_per_skill: int = 30        # 每技能最大效果字面量数
    reliability_min_for_effects: float = 0.6  # 可靠性阈值
    instance_pass_literal_frac: float = 0.7   # 实例通过所需的字面量比例

    # ── Bank Maintenance ──
    merge_eff_jaccard_thresh: float = 0.6     # Merge Jaccard 阈值
    merge_emb_cosine_thresh: float = 0.85     # Merge 余弦阈值
    merge_transition_overlap_k: int = 5       # Transition overlap K
    merge_transition_overlap_min: float = 0.4 # Transition overlap 最小比例

    # ── ProtoSkill 升级 ──
    proto_min_support: int = 3                # ProtoSkill 升级所需最少支持数
    proto_min_consistency: float = 0.5        # 最低一致性
    proto_min_pass_rate: float = 0.6          # 最低验证通过率

    # ── Retire 淘汰 ──
    retire_freq_decay: float = 0.7            # 访问频率衰减
    retire_min_frequency: float = 0.1         # 最低频率（过低则淘汰）
    retire_max_unused_days: int = 90          # 最长未使用天数

    # ── 多维打分权重 ──
    score_weight_quality: float = 0.30
    score_weight_reuse: float = 0.25
    score_weight_contract: float = 0.20
    score_weight_consistency: float = 0.15
    score_weight_exploration: float = 0.10

    # ── 存储路径 ──
    skill_bank_path: str = ""                 # 留空=自动推断
    workspace: str = ""


# ════════════════════════════════════════════════════════════════
# 数据 Schema
# ════════════════════════════════════════════════════════════════

@dataclass
class LfmSegmentRecord:
    """一段记忆轨迹切片的记录（类比 COSPLAY SegmentRecord）"""

    seg_id: str                              # segment ID
    traj_id: str                             # 轨迹 ID（记忆来源）
    t_start: float                           # 起始时间戳
    t_end: float                             # 结束时间戳
    skill_label: str = ""                    # 关联的技能/契约 ID

    # 起始/结束状态（predicate 表示）
    predicates_start: Dict[str, float] = field(default_factory=dict)
    predicates_end: Dict[str, float] = field(default_factory=dict)

    # 效果（从 predicates 变化计算）
    eff_add: Set[str] = field(default_factory=set)
    eff_del: Set[str] = field(default_factory=set)
    eff_event: Set[str] = field(default_factory=set)

    # 上下文
    content: str = ""                        # 原始记忆内容
    embedding: List[float] = field(default_factory=list)
    cumulative_reward: float = 0.0           # 该段累积奖励/重要性
    intention_tags: List[str] = field(default_factory=list)

    def effect_signature(self) -> str:
        a = ",".join(sorted(self.eff_add)) if self.eff_add else ""
        d = ",".join(sorted(self.eff_del)) if self.eff_del else ""
        e = ",".join(sorted(self.eff_event)) if self.eff_event else ""
        return f"A:{a}|D:{d}|E:{e}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> LfmSegmentRecord:
        d = dict(d)
        d["eff_add"] = set(d.get("eff_add", []))
        d["eff_del"] = set(d.get("eff_del", []))
        d["eff_event"] = set(d.get("eff_event", []))
        return cls(**d)


@dataclass
class ProtoSkill:
    """ProtoSkill — 待升级的候选技能（类比 COSPLAY ProtoSkill）"""

    skill_id: str
    name: str = ""
    description: str = ""

    # 统计
    support: int = 0                         # 被观察到的次数
    consistency: float = 0.0                 # 一致性评分
    pass_rate: float = 0.0                   # 验证通过率
    avg_quality: float = 0.0                 # 平均质量评分

    created_at: float = field(default_factory=time.time)
    last_observed: float = field(default_factory=time.time)

    def is_ready(self, config: LfmSkillBankConfig) -> bool:
        return (self.support >= config.proto_min_support
                and self.consistency >= config.proto_min_consistency
                and self.pass_rate >= config.proto_min_pass_rate)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ProtoSkill:
        return cls(**d)


@dataclass
class LfmSkillEffectsContract:
    """效果契约 — 描述某技能/模式的预期效果（类比 COSPLAY SkillEffectsContract）"""

    skill_id: str
    version: int = 1
    name: str = ""
    description: str = ""

    # 效果集
    eff_add: Set[str] = field(default_factory=set)     # 会新增的 predicate
    eff_del: Set[str] = field(default_factory=set)     # 会删除的 predicate
    eff_event: Set[str] = field(default_factory=set)   # 会触发的事件

    # 统计
    support: Dict[str, int] = field(default_factory=dict)
    n_instances: int = 0

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def total_literals(self) -> int:
        return len(self.eff_add) + len(self.eff_del) + len(self.eff_event)

    def bump_version(self) -> None:
        self.version += 1
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["eff_add"] = sorted(self.eff_add)
        d["eff_del"] = sorted(self.eff_del)
        d["eff_event"] = sorted(self.eff_event)
        d["support"] = {k: v for k, v in self.support.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> LfmSkillEffectsContract:
        d = dict(d)
        d["eff_add"] = set(d.get("eff_add", []))
        d["eff_del"] = set(d.get("eff_del", []))
        d["eff_event"] = set(d.get("eff_event", []))
        return cls(**d)


@dataclass
class LfmVerificationReport:
    """验证报告 — 契约在实例上的通过率"""

    skill_id: str
    n_instances: int = 0

    eff_add_success_rate: Dict[str, float] = field(default_factory=dict)
    eff_del_success_rate: Dict[str, float] = field(default_factory=dict)
    eff_event_rate: Dict[str, float] = field(default_factory=dict)

    overall_pass_rate: float = 0.0
    worst_segments: List[str] = field(default_factory=list)
    failure_signatures: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> LfmVerificationReport:
        return cls(**d)


@dataclass
class LfmSkill:
    """LFM 技能 — 整合契约 + 协议 + 实例引用

    类比 COSPLAY Skill: Part 1=Protocol Store, Part 2=Evidence Store
    """

    skill_id: str
    version: int = 1

    # Part 1: Protocol Store（检索用）
    name: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    embedding: List[float] = field(default_factory=list)

    # Part 2: Evidence Store（管理用）
    contract: Optional[LfmSkillEffectsContract] = None
    sub_episode_ids: List[str] = field(default_factory=list)  # 关联 segment ID

    # 质量评分
    quality_score: float = 0.0
    reuse_success_rate: float = 0.0
    consistency_score: float = 0.0
    exploration_value: float = 0.0

    # 生命周期
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    use_count: int = 0
    retired: bool = False
    retired_at: Optional[float] = None

    def compute_skill_score(self, config: LfmSkillBankConfig) -> float:
        """五维加权评分"""
        score = 0.0
        score += self.quality_score * config.score_weight_quality
        score += self.reuse_success_rate * config.score_weight_reuse
        score += (self.contract.total_literals / config.max_effects_per_skill
                  if self.contract else 0) * config.score_weight_contract
        score += self.consistency_score * config.score_weight_consistency
        score += self.exploration_value * config.score_weight_exploration
        return score

    def to_dict(self) -> dict:
        d = asdict(self)
        d["contract"] = self.contract.to_dict() if self.contract else None
        return d

    def to_retrieval_view(self) -> dict:
        """决策体视角：仅返回检索所需信息"""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "score": self.compute_skill_score(LfmSkillBankConfig()),
            "use_count": self.use_count,
            "n_instances": len(self.sub_episode_ids),
            "contract_literals": self.contract.total_literals if self.contract else 0,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LfmSkill:
        d = dict(d)
        c = d.get("contract")
        d["contract"] = LfmSkillEffectsContract.from_dict(c) if c else None
        d["sub_episode_ids"] = list(d.get("sub_episode_ids", []))
        return cls(**d)


# ════════════════════════════════════════════════════════════════
# Effect 计算（从轨迹切片 → 效果集）
# ════════════════════════════════════════════════════════════════

def compute_segment_effects(
    rec: LfmSegmentRecord,
    config: LfmSkillBankConfig,
) -> LfmSegmentRecord:
    """从 segment 的起始/结束 predicates 计算效果

    类比 COSPLAY effects_compute.py compute_effects()
    """
    p_thresh = config.reliability_min_for_effects

    # Booleanize：概率 ≥ 阈值 = True
    true_start = {k for k, v in rec.predicates_start.items() if v >= p_thresh}
    true_end = {k for k, v in rec.predicates_end.items() if v >= p_thresh}

    rec.eff_add = true_end - true_start
    rec.eff_del = true_start - true_end

    # Event 效果：统计 end 中新增的 event 类 predicate
    rec.eff_event = {k for k in true_end if k.startswith("event.")
                     and k not in true_start}

    return rec


# ════════════════════════════════════════════════════════════════
# Contract Learning（从 segments 聚合 → 效果契约）
# ════════════════════════════════════════════════════════════════

def _frequent_literals(
    counter: Counter,
    n_instances: int,
    freq_thresh: float,
    budget: int,
) -> Tuple[Set[str], Dict[str, int]]:
    """保留出现频率 ≥ freq_thresh 的字面量，上限 budget

    类比 COSPLAY contract_learn.py _frequent_literals()
    """
    qualifying = {
        lit: cnt for lit, cnt in counter.items()
        if cnt / n_instances >= freq_thresh
    }
    if len(qualifying) > budget:
        top = sorted(qualifying.items(), key=lambda x: -x[1])[:budget]
        qualifying = dict(top)
    return set(qualifying.keys()), qualifying


def learn_effects_contract(
    skill_id: str,
    instances: List[LfmSegmentRecord],
    config: LfmSkillBankConfig,
    prev_version: int = 0,
) -> LfmSkillEffectsContract:
    """从 segment 实例学习效果契约

    类比 COSPLAY contract_learn.py learn_effects_contract()
    """
    n = len(instances)
    if n == 0:
        return LfmSkillEffectsContract(skill_id=skill_id)

    add_counts: Counter = Counter()
    del_counts: Counter = Counter()
    event_counts: Counter = Counter()

    for rec in instances:
        add_counts.update(rec.eff_add)
        del_counts.update(rec.eff_del)
        event_counts.update(rec.eff_event)

    budget = config.max_effects_per_skill

    eff_add, sup_add = _frequent_literals(add_counts, n, config.eff_freq, budget)
    eff_del, sup_del = _frequent_literals(del_counts, n, config.eff_freq, budget)
    eff_event, sup_evt = _frequent_literals(event_counts, n, config.eff_freq, budget)

    # 全局上限控制
    total = len(eff_add) + len(eff_del) + len(eff_event)
    if total > budget:
        all_items = (
            [(lit, sup_add[lit], "add") for lit in eff_add]
            + [(lit, sup_del[lit], "del") for lit in eff_del]
            + [(lit, sup_evt[lit], "evt") for lit in eff_event]
        )
        all_items.sort(key=lambda x: -x[1])
        kept = all_items[:budget]
        eff_add = {lit for lit, _, cat in kept if cat == "add"}
        eff_del = {lit for lit, _, cat in kept if cat == "del"}
        eff_event = {lit for lit, _, cat in kept if cat == "evt"}

    support = {}
    support.update({k: v for k, v in sup_add.items() if k in eff_add})
    support.update({k: v for k, v in sup_del.items() if k in eff_del})
    support.update({k: v for k, v in sup_evt.items() if k in eff_event})

    return LfmSkillEffectsContract(
        skill_id=skill_id,
        version=prev_version + 1,
        eff_add=eff_add,
        eff_del=eff_del,
        eff_event=eff_event,
        support=support,
        n_instances=n,
    )


# ════════════════════════════════════════════════════════════════
# Contract Verification（验证契约在实例上的通过率）
# ════════════════════════════════════════════════════════════════

def verify_effects_contract(
    contract: LfmSkillEffectsContract,
    instances: List[LfmSegmentRecord],
    config: LfmSkillBankConfig,
) -> LfmVerificationReport:
    """验证契约在各实例上的通过率

    类比 COSPLAY contract_verify.py verify_effects_contract()
    """
    n = len(instances)
    if n == 0:
        return LfmVerificationReport(skill_id=contract.skill_id)

    add_ok: Counter = Counter()
    del_ok: Counter = Counter()
    evt_ok: Counter = Counter()

    instance_failure_counts: Dict[str, int] = {}
    failure_sig_counter: Counter = Counter()
    total_literals = contract.total_literals

    for rec in instances:
        fails: List[str] = []
        for p in contract.eff_add:
            if p not in rec.eff_add and p not in rec.predicates_end:
                fails.append(f"miss_add:{p}")
            else:
                add_ok[p] += 1
        for p in contract.eff_del:
            if p not in rec.eff_del and p in rec.predicates_end:
                fails.append(f"miss_del:{p}")
            else:
                del_ok[p] += 1
        for e in contract.eff_event:
            if e not in rec.eff_event:
                fails.append(f"miss_evt:{e}")
            else:
                evt_ok[e] += 1

        instance_failure_counts[rec.seg_id] = len(fails)
        if fails:
            sig = "|".join(sorted(fails))
            failure_sig_counter[sig] += 1

    # 逐字面量成功率
    eff_add_sr = {p: add_ok[p] / n for p in contract.eff_add}
    eff_del_sr = {p: del_ok[p] / n for p in contract.eff_del}
    eff_event_r = {e: evt_ok[e] / n for e in contract.eff_event}

    # 整体通过率
    pass_thresh = config.instance_pass_literal_frac
    passing = 0
    for rec in instances:
        n_fails = instance_failure_counts.get(rec.seg_id, 0)
        if total_literals == 0:
            passing += 1
        elif (total_literals - n_fails) / total_literals >= pass_thresh:
            passing += 1
    overall_pass_rate = passing / n if n > 0 else 0.0

    worst_segments = sorted(
        instance_failure_counts.items(), key=lambda x: -x[1]
    )
    worst_list = [sid for sid, cnt in worst_segments[:10] if cnt > 0]

    return LfmVerificationReport(
        skill_id=contract.skill_id,
        n_instances=n,
        eff_add_success_rate=eff_add_sr,
        eff_del_success_rate=eff_del_sr,
        eff_event_rate=eff_event_r,
        overall_pass_rate=overall_pass_rate,
        worst_segments=worst_list,
        failure_signatures=dict(failure_sig_counter),
    )


# ════════════════════════════════════════════════════════════════
# Contract Refinement（基于验证结果精炼契约）
# ════════════════════════════════════════════════════════════════

def refine_effects_contract(
    contract: LfmSkillEffectsContract,
    report: LfmVerificationReport,
    config: LfmSkillBankConfig,
) -> LfmSkillEffectsContract:
    """基于验证报告精炼契约：移除低成功率字面量

    类比 COSPLAY contract_refine.py refine_effects_contract()
    """
    refined_add = {
        p for p in contract.eff_add
        if report.eff_add_success_rate.get(p, 0) >= config.instance_pass_literal_frac
    }
    refined_del = {
        p for p in contract.eff_del
        if report.eff_del_success_rate.get(p, 0) >= config.instance_pass_literal_frac
    }
    refined_event = {
        e for e in contract.eff_event
        if report.eff_event_rate.get(e, 0) >= config.instance_pass_literal_frac
    }

    contract.eff_add = refined_add
    contract.eff_del = refined_del
    contract.eff_event = refined_event
    contract.bump_version()

    return contract


# ════════════════════════════════════════════════════════════════
# LFM Skill Bank（持久的技能库引擎）
# ════════════════════════════════════════════════════════════════

class LfmSkillBank:
    """LFM 技能库 — 类比 COSPLAY SkillBankMVP + 自有增强

    核心数据：
      _skills: Dict[str, LfmSkill] — 已验证的技能（已固化）
      _proto_skills: Dict[str, ProtoSkill] — 候选技能（待升级）
      _segments: Dict[str, LfmSegmentRecord] — 所有 segment 记录
      _pending_segments: List[LfmSegmentRecord] — 待处理的 segment

    工作流：
      1. ingest_segment() → _pending_segments
      2. discover_proto_skills() → _proto_skills
      3. promote_proto_skills() → _skills
      4. merge/refine/split/retire → bank maintenance
      5. save() → 持久化
    """

    def __init__(self, config: Optional[LfmSkillBankConfig] = None):
        self.config = config or LfmSkillBankConfig()
        self._skills: Dict[str, LfmSkill] = {}
        self._proto_skills: Dict[str, ProtoSkill] = {}
        self._segments: Dict[str, LfmSegmentRecord] = {}
        self._pending_segments: List[LfmSegmentRecord] = []
        self._history: List[dict] = []
        self._path = ""

    # ── Properties ──────────────────────────────────────────────

    @property
    def skill_ids(self) -> List[str]:
        return [sid for sid, s in self._skills.items() if not s.retired]

    @property
    def proto_skill_ids(self) -> List[str]:
        return list(self._proto_skills.keys())

    @property
    def n_active_skills(self) -> int:
        return len(self.skill_ids)

    @property
    def n_proto_skills(self) -> int:
        return len(self._proto_skills)

    @property
    def n_segments(self) -> int:
        return len(self._segments)

    @property
    def n_pending(self) -> int:
        return len(self._pending_segments)

    # ── Skill CRUD ─────────────────────────────────────────────

    def get_skill(self, skill_id: str) -> Optional[LfmSkill]:
        return self._skills.get(skill_id)

    def get_contract(self, skill_id: str) -> Optional[LfmSkillEffectsContract]:
        skill = self._skills.get(skill_id)
        return skill.contract if skill else None

    def has_skill(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def add_skill(self, skill: LfmSkill) -> None:
        self._skills[skill.skill_id] = skill
        self._log("add_skill", skill.skill_id, skill.version)

    def remove_skill(self, skill_id: str) -> bool:
        if skill_id in self._skills:
            self._skills[skill_id].retired = True
            self._skills[skill_id].retired_at = time.time()
            self._log("retire_skill", skill_id, self._skills[skill_id].version)
            return True
        return False

    def retire_skill(self, skill_id: str) -> bool:
        """标记技能为已淘汰（保留记录但不参与检索）"""
        return self.remove_skill(skill_id)

    # ── ProtoSkill CRUD ────────────────────────────────────────

    def get_proto_skill(self, skill_id: str) -> Optional[ProtoSkill]:
        return self._proto_skills.get(skill_id)

    def add_proto_skill(self, ps: ProtoSkill) -> None:
        self._proto_skills[ps.skill_id] = ps
        self._log("add_proto", ps.skill_id, 0)

    def update_proto_skill(self, ps: ProtoSkill) -> None:
        self._proto_skills[ps.skill_id] = ps

    def remove_proto_skill(self, skill_id: str) -> None:
        self._proto_skills.pop(skill_id, None)
        self._log("remove_proto", skill_id, 0)

    # ── Segment 管理 ───────────────────────────────────────────

    def ingest_segment(self, rec: LfmSegmentRecord) -> str:
        """摄入一段记忆轨迹切片，存入 pending 队列"""
        if not rec.seg_id:
            rec.seg_id = f"seg_{hashlib.md5(f'{rec.traj_id}:{rec.t_start}:{rec.t_end}'.encode()).hexdigest()[:12]}"

        # 计算效果
        rec = compute_segment_effects(rec, self.config)

        self._segments[rec.seg_id] = rec
        self._pending_segments.append(rec)
        return rec.seg_id

    def get_segment(self, seg_id: str) -> Optional[LfmSegmentRecord]:
        return self._segments.get(seg_id)

    def get_segments_by_skill(self, skill_id: str) -> List[LfmSegmentRecord]:
        return [s for s in self._segments.values() if s.skill_label == skill_id]

    # ── 核心：发现 ProtoSkill ──────────────────────────────────

    def discover_proto_skills(self) -> List[str]:
        """从 pending segments 中发现新的 ProtoSkill

        策略：
          1. 对 pending segments 按 embedding 余弦相似度聚类
          2. 每个聚类 → 一个 ProtoSkill
          3. 计算支持数、一致性、平均质量
        """
        if not self._pending_segments:
            return []

        discovered: List[str] = []

        # 对有 embedding 的 segment 做简单聚类
        emb_clusters: Dict[str, List[LfmSegmentRecord]] = {}

        for seg in self._pending_segments:
            if seg.embedding and len(seg.embedding) >= 128:
                # 找最近的已有 cluster
                best_cluster = None
                best_sim = 0.0

                for cid, members in emb_clusters.items():
                    if members and members[0].embedding and len(members[0].embedding) >= 128:
                        sim = _cosine_similarity(
                            np.array(seg.embedding),
                            np.array(members[0].embedding)
                        )
                        if sim > best_sim:
                            best_sim = sim
                            best_cluster = cid

                if best_cluster and best_sim >= 0.7:
                    emb_clusters[best_cluster].append(seg)
                else:
                    cid = f"proto_{hashlib.md5(f'{seg.traj_id}:{time.time()}'.encode()).hexdigest()[:8]}"
                    emb_clusters[cid] = [seg]
            else:
                # 无 embedding → 按 skill_label 聚
                label = seg.skill_label or "unknown"
                if label not in emb_clusters:
                    emb_clusters[label] = []
                emb_clusters[label].append(seg)

        # 对每个聚类，创建或更新 ProtoSkill
        for cid, members in emb_clusters.items():
            if len(members) < self.config.min_instances_per_skill:
                continue

            # 效果一致性：计算成员间 eff_add 的 Jaccard 均值
            consistencies = []
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    jac = _jaccard(members[i].eff_add, members[j].eff_add)
                    consistencies.append(jac)
            consistency = (sum(consistencies) / len(consistencies)
                          if consistencies else 0.5)

            # 学习契约
            contract = learn_effects_contract(cid, members, self.config)

            # 验证
            report = verify_effects_contract(contract, members, self.config)

            # 平均质量
            avg_quality = sum(s.cumulative_reward for s in members) / len(members)

            # 计算特征描述
            add_desc = ", ".join(sorted(contract.eff_add)[:5])
            key_desc = add_desc if add_desc else f"{len(members)} segments"

            ps = ProtoSkill(
                skill_id=cid,
                name=f"pattern_{key_desc[:30]}",
                support=len(members),
                consistency=consistency,
                pass_rate=report.overall_pass_rate,
                avg_quality=avg_quality,
            )

            if cid in self._proto_skills:
                existing = self._proto_skills[cid]
                existing.support += ps.support
                existing.consistency = (existing.consistency + consistency) / 2
                existing.pass_rate = (existing.pass_rate + ps.pass_rate) / 2
                existing.last_observed = time.time()
            else:
                self.add_proto_skill(ps)
                discovered.append(cid)

            # 标记 segment
            for seg in members:
                seg.skill_label = cid

        # 清空 pending
        self._pending_segments = []

        self._log("discover_proto", f"{len(discovered)} new", 0)
        return discovered

    # ── 核心：ProtoSkill → Skill 升级 ─────────────────────────

    def promote_proto_skills(self) -> List[str]:
        """将达标的 ProtoSkill 升级为正式 Skill

        Phase 2.2 改造：毕业前调用 injection_scanner 扫描合约内容，
        检测 prompt injection 特征。高风险合约隔离，中风险进审核队列，
        低风险放行。扫描通过的合约记录来源追溯，便于污染回滚。
        """
        promoted: List[str] = []

        # Phase 2.2: 导入内容扫描器（延迟导入避免循环依赖）
        try:
            from injection_scanner import scan_before_graduate, get_provenance_store
            _scanner_available = True
        except ImportError:
            _scanner_available = False
            logger.warning("injection_scanner not available, skipping content scan")

        for sid, ps in list(self._proto_skills.items()):
            if not ps.is_ready(self.config):
                continue

            segments = self.get_segments_by_skill(sid)
            if len(segments) < self.config.min_instances_per_skill:
                continue

            # 学习正式契约
            contract = learn_effects_contract(sid, segments, self.config)
            report = verify_effects_contract(contract, segments, self.config)
            contract = refine_effects_contract(contract, report, self.config)

            # Phase 2.2: 毕业前内容扫描
            if _scanner_available:
                scan_result = scan_before_graduate(ps, contract)
                if scan_result.risky and scan_result.risk_level == "high":
                    # 高风险：直接隔离，不毕业
                    logger.warning(
                        f"ProtoSkill {sid} QUARANTINED (high risk injection): "
                        f"score={scan_result.score:.2f}, reason={scan_result.reason}"
                    )
                    continue
                elif scan_result.risky and scan_result.risk_level == "medium":
                    # 中风险：进入审核队列，暂不毕业
                    logger.info(
                        f"ProtoSkill {sid} sent to review queue (medium risk): "
                        f"score={scan_result.score:.2f}"
                    )
                    continue
                # 低风险或安全：放行，记录来源追溯
                get_provenance_store().record(ps.name, {
                    "source": "cosplay",
                    "scan_passed": True,
                    "scan_score": scan_result.score,
                    "proto_skill_id": sid,
                })

            skill = LfmSkill(
                skill_id=sid,
                name=ps.name[:60],
                description=ps.description or f"自动从 {ps.support} 个轨迹切片发现",
                contract=contract,
                sub_episode_ids=[s.seg_id for s in segments],
                quality_score=ps.avg_quality,
                consistency_score=ps.consistency,
                exploration_value=0.3,  # 新技能初始探索价值
            )

            self.add_skill(skill)
            self.remove_proto_skill(sid)
            promoted.append(sid)

            # Phase 3.1: 毕业产物输出为 OpenClaw SKILL.md 格式
            # 使 OpenClaw 的 250ms hot-reload 技能发现机制能自动识别新技能
            try:
                self._export_skill_md(skill, ps)
            except Exception as e:
                logger.warning(f"SKILL.md export failed for {sid}: {e}")

            logger.info(f"ProtoSkill promoted: {sid} (support={ps.support}, "
                       f"pass_rate={ps.pass_rate:.3f}, n_instances={len(segments)})")

        return promoted

    def _export_skill_md(self, skill, proto_skill) -> str:
        """
        Phase 3.1: 将毕业技能输出为 OpenClaw SKILL.md 格式

        生成含 YAML frontmatter 的 SKILL.md，写入 workspace/skills/ 目录。
        OpenClaw 的技能发现机制（250ms debounce hot-reload）会自动加载。

        Args:
            skill: LfmSkill 实例
            proto_skill: ProtoSkill 实例

        Returns:
            str: 写入的 SKILL.md 文件路径
        """
        import os
        import time

        # 确定技能输出目录
        ws = self.config.workspace or os.environ.get("OPENCLAW_WORKSPACE", "")
        if not ws:
            ws = workspace()
        skills_root = os.path.join(ws, "skills")
        os.makedirs(skills_root, exist_ok=True)

        # 技能目录名（清理非法字符）
        safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in skill.name)
        if not safe_name or safe_name == "-":
            safe_name = skill.skill_id[:20]
        skill_dir = os.path.join(skills_root, safe_name)
        os.makedirs(skill_dir, exist_ok=True)

        # 构建 YAML frontmatter
        # Progressive disclosure: description 控制在 97 字符以内
        description = (skill.description or "")[:97]
        requires_lines = []
        if hasattr(proto_skill, "requires") and proto_skill.requires:
            for key, val in proto_skill.requires.items():
                requires_lines.append(f"  {key}: {val}")

        frontmatter_lines = [
            "---",
            f"name: {safe_name}",
            f"description: {description}",
            f"version: {skill.version}",
            "tags:",
            "  - auto-learned",
            "  - cosplay",
            "source: lfm-skill-bank",
            f"skill_id: {skill.skill_id}",
        ]
        if requires_lines:
            frontmatter_lines.append("requires:")
            frontmatter_lines.extend(requires_lines)
        frontmatter_lines.append("---")

        # 构建正文
        body_lines = [
            f"# {skill.name}",
            "",
            f"{skill.description or '自动学习的技能'}",
            "",
            "## 技能合约",
            "",
        ]

        if skill.contract:
            contract = skill.contract
            if hasattr(contract, "eff_add") and contract.eff_add:
                body_lines.append("### 前置效果（eff_add）")
                body_lines.append("```")
                for eff in sorted(contract.eff_add):
                    body_lines.append(f"  - {eff}")
                body_lines.append("```")
                body_lines.append("")

            if hasattr(contract, "eff_del") and contract.eff_del:
                body_lines.append("### 后置删除（eff_del）")
                body_lines.append("```")
                for eff in sorted(contract.eff_del):
                    body_lines.append(f"  - {eff}")
                body_lines.append("```")
                body_lines.append("")

            if hasattr(contract, "eff_event") and contract.eff_event:
                body_lines.append("### 触发事件（eff_event）")
                body_lines.append("```")
                for evt in sorted(contract.eff_event):
                    body_lines.append(f"  - {evt}")
                body_lines.append("```")
                body_lines.append("")

        body_lines.extend([
            "## 统计信息",
            "",
            f"- 支持度（support）: {getattr(proto_skill, 'support', 'N/A')}",
            f"- 一致性: {getattr(proto_skill, 'consistency', 'N/A'):.3f}" if hasattr(proto_skill, "consistency") else "- 一致性: N/A",
            f"- 通过率: {getattr(proto_skill, 'pass_rate', 'N/A'):.3f}" if hasattr(proto_skill, "pass_rate") else "- 通过率: N/A",
            f"- 质量评分: {skill.quality_score:.3f}",
            f"- 创建时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## 触发条件",
            "",
            "本技能由 COSPLAY/LFM Skill Bank 自动学习生成，在匹配的执行轨迹场景下自动触发。",
            "",
        ])

        # 写入文件
        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        content = "\n".join(frontmatter_lines) + "\n\n" + "\n".join(body_lines)
        with open(skill_md_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"SKILL.md exported: {skill_md_path}")
        return skill_md_path

    # ── 核心：Bank Maintenance ──────────────────────────────────

    def run_maintenance(self) -> Dict[str, Any]:
        """运行一轮完整的 bank maintenance

        操作：
          1. Merge — 合并相似技能（Jaccard + cosine + transition 三重校验）
          2. Split — 拆分效果分布过宽的技能
          3. Refine — 更新高频效果 + 淘汰低频效果
          4. Retire — 淘汰长期不用的技能
        """
        stats: Dict[str, Any] = {}

        merges = self._run_merge()
        stats["merges"] = [{"canonical": m["canonical"],
                           "merged": m["merged"],
                           "reason": m["reason"]} for m in merges]

        splits = self._run_split()
        stats["splits"] = splits

        refinements = self._run_refine()
        stats["refinements"] = refinements

        retires = self._run_retire()
        stats["retires"] = retires

        stats["n_active"] = self.n_active_skills
        stats["n_proto"] = self.n_proto_skills
        stats["n_segments"] = self.n_segments
        stats["n_pending"] = self.n_pending

        return stats

    def _run_merge(self) -> List[Dict]:
        """Merge — 检测并合并相似技能

        三重校验：
          1. 效果 Jaccard ≥ threshold
          2. Embedding 余弦 ≥ threshold
          3. Transition overlap ≥ threshold
        """
        merges: List[Dict] = []
        active_ids = self.skill_ids

        if len(active_ids) < 2:
            return merges

        # 构建 profile
        profiles: Dict[str, dict] = {}
        for sid in active_ids:
            skill = self._skills[sid]
            contract = skill.contract
            if not contract:
                continue
            profiles[sid] = {
                "all_effects": (contract.eff_add | contract.eff_del | contract.eff_event),
                "embedding": skill.embedding if skill.embedding else None,
                "segments": self.get_segments_by_skill(sid),
            }

        # 候选对发现
        candidates: Set[Tuple[str, str]] = set()
        sids = list(profiles.keys())
        for i in range(len(sids)):
            for j in range(i + 1, len(sids)):
                candidates.add((sids[i], sids[j]))

        # 三重校验
        for a, b in candidates:
            pa = profiles[a]
            pb = profiles[b]

            # 1. Jaccard
            eff_jac = _jaccard(pa["all_effects"], pb["all_effects"])
            if eff_jac < self.config.merge_eff_jaccard_thresh:
                continue

            # 2. Embedding cosine
            emb_a = np.array(pa["embedding"]) if pa["embedding"] else None
            emb_b = np.array(pb["embedding"]) if pb["embedding"] else None
            if emb_a is not None and emb_b is not None and len(emb_a) > 0 and len(emb_b) > 0:
                emb_cos = _cosine_similarity(emb_a, emb_b)
                if emb_cos < self.config.merge_emb_cosine_thresh:
                    continue

            # 通过所有校验 → 执行 merge
            if len(pa["segments"]) >= len(pb["segments"]):
                canonical, retired = a, b
            else:
                canonical, retired = b, a

            all_segments = pa["segments"] + pb["segments"]
            for seg in all_segments:
                seg.skill_label = canonical

            contract = learn_effects_contract(
                canonical, all_segments, self.config,
                prev_version=self._skills[canonical].version
            )
            report = verify_effects_contract(contract, all_segments, self.config)
            contract = refine_effects_contract(contract, report, self.config)

            # 更新 canonical
            skill = self._skills[canonical]
            skill.contract = contract
            skill.sub_episode_ids = [s.seg_id for s in all_segments]
            skill.bump_version()

            # 淘汰 retired
            self.retire_skill(retired)

            merges.append({
                "canonical": canonical,
                "merged": retired,
                "reason": f"jac={eff_jac:.3f}",
            })

            self._log("merge", f"{retired}→{canonical}", skill.version)

        return merges

    def _run_split(self) -> List[Dict]:
        """Split — 检测效果分布过宽的技能并拆分

        策略：对 sub_episodes 做二次聚类，如果出现多个明显不同的效果模式则拆分
        """
        splits: List[Dict] = []

        for sid in self.skill_ids:
            skill = self._skills[sid]
            segments = self.get_segments_by_skill(sid)
            if len(segments) < 5:
                continue

            # 计算效果签名
            signatures: List[Tuple[str, LfmSegmentRecord]] = []
            for seg in segments:
                sig = seg.effect_signature()
                signatures.append((sig, seg))

            # 去重看有多少个不同的效果模式
            unique_sigs = set(s for s, _ in signatures)

            if 2 <= len(unique_sigs) <= len(segments) // 2:
                # 有明显不同的效果模式 → 拆分
                sig_groups: Dict[str, List[LfmSegmentRecord]] = {}
                for sig, seg in signatures:
                    if sig not in sig_groups:
                        sig_groups[sig] = []
                    sig_groups[sig].append(seg)

                for sig, members in sig_groups.items():
                    if len(members) < self.config.min_instances_per_skill:
                        continue

                    child_id = f"{sid}_split_{hashlib.md5(sig.encode()).hexdigest()[:6]}"
                    contract = learn_effects_contract(child_id, members, self.config)
                    child_skill = LfmSkill(
                        skill_id=child_id,
                        name=f"{skill.name} ({sig[:20]})",
                        contract=contract,
                        sub_episode_ids=[s.seg_id for s in members],
                        quality_score=skill.quality_score,
                        exploration_value=0.5,  # 拆分后的技能探索价值更高
                    )
                    self.add_skill(child_skill)

                # 淘汰原技能
                self.retire_skill(sid)
                splits.append({
                    "parent": sid,
                    "children": [f"{sid}_split_{hashlib.md5(s.encode()).hexdigest()[:6]}"
                                for s in unique_sigs],
                    "n_patterns": len(unique_sigs),
                })
                self._log("split", sid, skill.version)

        return splits

    def _run_refine(self) -> List[Dict]:
        """Refine — 基于新 segment 更新契约

        对所有 active skill：
          1. 收集所有关联 segment
          2. 重新学习契约
          3. 比较新旧版本
        """
        refinements: List[Dict] = []

        for sid in self.skill_ids:
            skill = self._skills[sid]
            segments = self.get_segments_by_skill(sid)
            if len(segments) < self.config.min_instances_per_skill:
                continue

            old_contract = skill.contract
            new_contract = learn_effects_contract(
                sid, segments, self.config,
                prev_version=old_contract.version if old_contract else 0
            )

            if old_contract:
                # 计算变化量
                old_total = old_contract.total_literals
                new_total = new_contract.total_literals
                added = new_total - old_total

                if added != 0:
                    report = verify_effects_contract(new_contract, segments, self.config)
                    new_contract = refine_effects_contract(new_contract, report, self.config)

                    skill.contract = new_contract
                    skill.bump_version()

                    refinements.append({
                        "skill_id": sid,
                        "old_literals": old_total,
                        "new_literals": new_contract.total_literals,
                        "pass_rate": report.overall_pass_rate,
                    })
            else:
                skill.contract = new_contract
                skill.bump_version()

        return refinements

    def _run_retire(self) -> List[str]:
        """Retire — 淘汰长期不用的技能

        淘汰条件（满足任一）：
          1. use_count == 0 and 创建超过 90 天
          2. 访问频率 < min_frequency
          3. 一致性评分 < 0.2
        """
        retired: List[str] = []
        now = time.time()

        for sid in list(self.skill_ids):
            skill = self._skills[sid]

            # 条件 1：从未使用过且太老
            age_days = (now - skill.created_at) / 86400
            if skill.use_count == 0 and age_days > self.config.retire_max_unused_days:
                self.retire_skill(sid)
                retired.append(sid)
                continue

            # 条件 2：访问频率过低
            if skill.use_count > 0:
                freq = skill.use_count / max(age_days, 1)
                if freq < self.config.retire_min_frequency and age_days > 30:
                    self.retire_skill(sid)
                    retired.append(sid)
                    continue

            # 条件 3：一致性过低
            if skill.consistency_score < 0.2 and age_days > 7:
                self.retire_skill(sid)
                retired.append(sid)
                continue

        if retired:
            self._log("retire", f"{len(retired)} skills", 0)

        return retired

    # ── 检索 ────────────────────────────────────────────────────

    def retrieve_skills(
        self,
        query_embedding: Optional[np.ndarray] = None,
        top_k: int = 5,
    ) -> List[Dict]:
        """检索最相关的 active skills

        支持 embedding 相似度检索 + 兜底返回最新技能
        """
        active = [s for s in self._skills.values() if not s.retired]
        if not active:
            return []

        if query_embedding is not None and any(s.embedding for s in active):
            scored: List[Tuple[float, LfmSkill]] = []
            for skill in active:
                if skill.embedding and len(skill.embedding) >= 128:
                    sim = _cosine_similarity(
                        query_embedding,
                        np.array(skill.embedding)
                    )
                    scored.append((sim, skill))
                else:
                    scored.append((0.0, skill))

            scored.sort(key=lambda x: -x[0])
            return [s.to_retrieval_view() for _, s in scored[:top_k]]

        # 兜底：返回质量评分最高的
        scored = [(s.compute_skill_score(self.config), s) for s in active]
        scored.sort(key=lambda x: -x[0])
        return [s.to_retrieval_view() for _, s in scored[:top_k]]

    def get_evidence_view(self, skill_id: str) -> Optional[Dict]:
        """返回技能的证据视图（segment 指针 + 摘要）"""
        skill = self._skills.get(skill_id)
        if not skill:
            return None

        segments = self.get_segments_by_skill(skill_id)
        return {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "n_sub_episodes": len(skill.sub_episode_ids),
            "segments": [
                {"seg_id": s.seg_id, "effect_sig": s.effect_signature(),
                 "content": s.content[:200]}
                for s in segments[:10]
            ],
            "contract": skill.contract.to_dict() if skill.contract else None,
            "quality_score": skill.quality_score,
            "reuse_success_rate": skill.reuse_success_rate,
            "consistency_score": skill.consistency_score,
        }

    # ── 持久化 ──────────────────────────────────────────────────

    def save(self, filepath: Optional[str] = None) -> None:
        path = filepath or self._path
        if not path:
            ws = self.config.workspace or os.environ.get("OPENCLAW_WORKSPACE", str(Path(workspace())))
            path = str(Path(ws) / "memory" / "lfm_skill_bank.json")

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "skills": {sid: s.to_dict() for sid, s in self._skills.items()},
            "proto_skills": {sid: ps.to_dict() for sid, ps in self._proto_skills.items()},
            "segments": {sid: seg.to_dict() for sid, seg in self._segments.items()},
            "history": self._history[-1000:],  # 只保留最近 1000 条
            "saved_at": time.time(),
            "version": 1,
        }

        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"LfmSkillBank saved: {len(self._skills)} skills, "
                   f"{len(self._proto_skills)} proto, {len(self._segments)} segments")

    def load(self, filepath: Optional[str] = None) -> bool:
        path = filepath or self._path
        if not path:
            ws = self.config.workspace or os.environ.get("OPENCLAW_WORKSPACE", str(Path(workspace())))
            path = str(Path(ws) / "memory" / "lfm_skill_bank.json")

        p = Path(path)
        if not p.exists():
            self._path = path
            return False

        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._skills = {
                sid: LfmSkill.from_dict(sd) for sid, sd in data.get("skills", {}).items()
            }
            self._proto_skills = {
                sid: ProtoSkill.from_dict(psd) for sid, psd in data.get("proto_skills", {}).items()
            }
            self._segments = {
                sid: LfmSegmentRecord.from_dict(sd) for sid, sd in data.get("segments", {}).items()
            }
            self._history = data.get("history", [])

            logger.info(f"LfmSkillBank loaded: {len(self._skills)} skills, "
                       f"{len(self._proto_skills)} proto, {len(self._segments)} segments")
            self._path = path
            return True
        except Exception as e:
            logger.warning(f"LfmSkillBank load failed: {e}")
            self._path = path
            return False

    # ── History ─────────────────────────────────────────────────

    def _log(self, event: str, target: str, version: int) -> None:
        self._history.append({
            "event": event,
            "target": target,
            "version": version,
            "timestamp": time.time(),
        })

    # ── Stats ───────────────────────────────────────────────────

    def summary(self) -> Dict:
        """全量状态摘要"""
        return {
            "n_skills": len(self._skills),
            "n_active": self.n_active_skills,
            "n_retired": len(self._skills) - self.n_active_skills,
            "n_proto": self.n_proto_skills,
            "n_segments": self.n_segments,
            "n_pending": self.n_pending,
            "history_len": len(self._history),
            "skills": {
                sid: s.to_retrieval_view()
                for sid, s in self._skills.items() if not s.retired
            },
        }

    def stats(self) -> Dict:
        """简要统计"""
        return self.summary()


# ════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度"""
    a = np.asarray(a, dtype=np.float32).flatten()
    b = np.asarray(b, dtype=np.float32).flatten()
    if len(a) == 0 or len(b) == 0:
        return 0.0
    dot = float(np.dot(a, b))
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _jaccard(a: Set, b: Set) -> float:
    """Jaccard 相似度"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ════════════════════════════════════════════════════════════════
# 单例工厂
# ════════════════════════════════════════════════════════════════

_LFM_SKILL_BANK_INSTANCE: Optional[LfmSkillBank] = None


def get_skill_bank(
    config: Optional[LfmSkillBankConfig] = None,
    autoload: bool = True,
) -> LfmSkillBank:
    """获取 LFM Skill Bank 单例"""
    global _LFM_SKILL_BANK_INSTANCE
    if _LFM_SKILL_BANK_INSTANCE is None:
        _LFM_SKILL_BANK_INSTANCE = LfmSkillBank(config or LfmSkillBankConfig())
        if autoload:
            _LFM_SKILL_BANK_INSTANCE.load()
    return _LFM_SKILL_BANK_INSTANCE


def run_skill_bank_cycle(workspace: Optional[str] = None) -> Dict:
    """运行一轮完整的 Skill Bank 周期（供 ConsolidationEngine 调用）"""
    config = LfmSkillBankConfig(workspace=workspace or "")
    bank = get_skill_bank(config)

    result: Dict[str, Any] = {}

    # 1. 发现 ProtoSkill
    try:
        discovered = bank.discover_proto_skills()
        result["discovered"] = len(discovered)
    except Exception as e:
        result["discovered"] = f"error: {e}"

    # 2. 升级
    try:
        promoted = bank.promote_proto_skills()
        result["promoted"] = len(promoted)
    except Exception as e:
        result["promoted"] = f"error: {e}"

    # 3. Bank Maintenance
    try:
        maintenance = bank.run_maintenance()
        result["maintenance"] = {
            "merges": len(maintenance.get("merges", [])),
            "splits": len(maintenance.get("splits", [])),
            "refinements": len(maintenance.get("refinements", [])),
            "retires": len(maintenance.get("retires", [])),
        }
    except Exception as e:
        result["maintenance"] = f"error: {e}"

    # 4. 持久化
    try:
        bank.save()
        result["saved"] = True
    except Exception as e:
        result["saved"] = f"error: {e}"

    result["n_active"] = bank.n_active_skills
    result["n_proto"] = bank.n_proto_skills

    return result


# ════════════════════════════════════════════════════════════════
# Phase 4: 自动从执行链/记忆系统提取 → 生成 contracts
# ════════════════════════════════════════════════════════════════

def extract_segments_from_memory(
    memories: List[Dict],
    config: Optional[LfmSkillBankConfig] = None,
) -> List[LfmSegmentRecord]:
    """从记忆记录列表提取 segment 记录

    每条记忆记录应包含：
      - content: 内容文本
      - source/type: 来源类型（tool_call, user_input, ai_response）
      - timestamp: 时间戳
      - embedding: 向量（可选）
      - metadata: 元数据（tool_name, params, result 等）

    Predicate 提取策略：
      - tool_call → 以 "tool.{name}.called" 为 event predicate
      - tool_result → 以 "tool.{name}.success/failure" 为 end predicate
      - content 中的关键词提取为状态 predicate
    """
    config = config or LfmSkillBankConfig()
    segments: List[LfmSegmentRecord] = []

    # 按源分组（同一次 agent 调用为一个 traj）
    traj_groups: Dict[str, List[Dict]] = {}
    for mem in memories:
        traj = mem.get("traj_id") or mem.get("session_id",
            mem.get("metadata", {}).get("session_id", "default"))
        if traj not in traj_groups:
            traj_groups[traj] = []
        traj_groups[traj].append(mem)

    for traj_id, mems in traj_groups.items():
        # 排序
        mems.sort(key=lambda m: m.get("timestamp", m.get("created_at", 0)))

        # 分析模式：连续相似的调用 → 一个 segment
        if len(mems) >= 2:
            # 按工具调用和回复配对
            pairs: List[List[Dict]] = []
            current: List[Dict] = []

            for m in mems:
                src = m.get("source", m.get("type", "unknown"))
                if "tool" in src.lower() or "ai" in src.lower():
                    if current and len(current) >= 4:
                        pairs.append(current)
                        current = []
                current.append(m)
            if current:
                pairs.append(current)

            for i, pair in enumerate(pairs):
                if len(pair) < 2:
                    continue

                # 从 pair 提取 predicates
                predicates_start: Dict[str, float] = {}
                predicates_end: Dict[str, float] = {}
                events: List[str] = []

                for m in pair:
                    content = m.get("content", "") or ""
                    meta = m.get("metadata", {})
                    tool_name = meta.get("tool", meta.get("tool_name", ""))
                    src = m.get("source", m.get("type", ""))

                    if "tool" in src.lower() and tool_name:
                        # 工具调用 → event
                        events.append(f"event.tool.{tool_name}")
                        # 调用前状态
                        predicates_start[f"tool.{tool_name}.ready"] = 1.0

                    # 从内容中提取关键词作为状态 predicate
                    for kw in _extract_keywords(content):
                        if len(kw) > 2:
                            predicates_end[f"state.{kw}"] = 1.0

                seg_id = f"seg_{traj_id}_{i}"
                seg = LfmSegmentRecord(
                    seg_id=seg_id,
                    traj_id=traj_id,
                    t_start=pair[0].get("timestamp", pair[0].get("created_at", 0.0)) if isinstance(pair[0].get("timestamp"), (int, float)) else i * 1.0,
                    t_end=pair[-1].get("timestamp", pair[-1].get("created_at", 0.0)) if isinstance(pair[-1].get("timestamp"), (int, float)) else (i + 1) * 1.0,
                    predicates_start=predicates_start,
                    predicates_end=predicates_end,
                    eff_event=set(events),
                    content=" | ".join(m.get("content", "")[:200] for m in pair if m.get("content")),
                    cumulative_reward=sum(
                        m.get("metadata", {}).get("reward", 0) or 0
                        for m in pair
                    ),
                )
                segments.append(seg)

    return segments


def _extract_keywords(text: str, max_k: int = 5) -> List[str]:
    """从文本中提取关键词（简单低频过滤）"""
    import re
    # 中文/英文关键词
    tokens: List[str] = []

    # 英文单词
    en_words = re.findall(r'\b[a-zA-Z_]{3,}\b', text)
    tokens.extend(en_words)

    # 中文词组（长度 2-6 的连续汉字）
    cn_chars = re.findall(r'[\u4e00-\u9fff]+', text)
    for chunk in cn_chars:
        if 2 <= len(chunk) <= 6:
            tokens.append(chunk)
        elif len(chunk) > 6:
            # 长中文切 2-gram
            for j in range(0, len(chunk) - 1, 2):
                gram = chunk[j:j+2]
                if len(gram) == 2:
                    tokens.append(gram)

    # 去重 + 低频过滤
    from collections import Counter
    counter = Counter(tokens)
    stopwords = {"the", "and", "for", "was", "are", "has", "had",
                 "but", "not", "this", "that", "with", "from",
                 "可以", "这个", "那个", "一个", "没有", "什么", "怎么",
                 "之后", "然后", "因为", "所以", "但是", "就是", "如果"}
    return [
        t for t, c in counter.most_common(max_k * 2)
        if t not in stopwords and len(t) >= 2
    ][:max_k]


def feed_memory_to_skill_bank(
    memories: List[Dict],
    workspace: Optional[str] = None,
) -> Dict:
    """将记忆记录批量喂入 Skill Bank

    完整流程：
      1. extract_segments_from_memory → LfmSegmentRecord[]
      2. ingest_segment → skill bank pending queue
      3. discover_proto_skills → 发现新模式
      4. promote_proto_skills → 升级达标技能
      5. run_maintenance → Merge/Split/Refine/Retire
      6. save → 持久化
    """
    config = LfmSkillBankConfig(workspace=workspace or "")
    bank = get_skill_bank(config)

    # 1. 提取 segment
    segments = extract_segments_from_memory(memories, config)

    # 2. 摄入
    ingested = 0
    for seg in segments:
        bank.ingest_segment(seg)
        ingested += 1

    # 3. 发现
    discovered = bank.discover_proto_skills()

    # 4. 升级
    promoted = bank.promote_proto_skills()

    # 5. Bank Maintenance
    maintenance = bank.run_maintenance()

    # 6. 持久化
    bank.save()

    return {
        "ingested": ingested,
        "discovered": len(discovered),
        "promoted": len(promoted),
        "n_active_skills": bank.n_active_skills,
        "n_proto_skills": bank.n_proto_skills,
        "maintenance": maintenance,
    }


# ════════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════════

def main():
    """命令行入口：运行一轮 Skill Bank 周期并输出结果"""
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else str(Path(workspace()))

    print(f"LFM Skill Bank — workspace: {workspace}")
    print()

    config = LfmSkillBankConfig(workspace=workspace)
    bank = get_skill_bank(config)

    print(f"Loaded: {bank.n_active_skills} active skills, "
          f"{bank.n_proto_skills} proto, {bank.n_segments} segments")
    print()

    result = run_skill_bank_cycle(workspace)

    print("=== Skill Bank Cycle Results ===")
    for k, v in result.items():
        if isinstance(v, dict):
            print(f"  {k}:")
            for kk, vv in v.items():
                print(f"    {kk}: {vv}")
        else:
            print(f"  {k}: {v}")
    print()

    print("=== Active Skills ===")
    summary = bank.summary()
    for sid, info in summary.get("skills", {}).items():
        print(f"  {sid}: {info}")


if __name__ == "__main__":
    # 修复 import path
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)
    main()
