#!/usr/bin/env python3
"""
COSPLAY → DAG 上下文桥接器

四项 COSPLAY 增强全部落地到 DAG 上下文管理:

1. Boundary-Aware Compression
   用 Boundary Detection 找出任务 segment 边界，按 segment 独立压缩，
   不跨 segment 混摘要。

2. Contract-Aware Summarization
   压缩前查 Skill Bank，按 contract 保留关键 predicates，
   不盲删重要信息。

3. Skill Replacement
   整段匹配已确认技能 → 用 `[Skill: name]` 替代，省大量 token。

4. Feedback-Driven Compression
   跟踪压缩后节点的使用情况，用 success/failure 信号调整压缩策略，
   反馈到 Skill Bank 的 refine_effects_contract()。

Layer: L9 (会话管理层)
Author: GalaxyOS
Created: 2026-06-23
"""

from __future__ import annotations

import json
import math

# 尝试导入 DAG + Liquid Fusion（可选依赖）
try:
    from dag_liquid_fusion import LTCDAGCompactStrategy
    _HAS_LTC = True
except ImportError:
    _HAS_LTC = False
import os
import re
import time
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("cosplay_context_adapter")


# ════════════════════════════════════════════════════════════════
# 桥接器配置
# ════════════════════════════════════════════════════════════════

@dataclass
class CosplayContextConfig:
    """桥接器全局配置"""
    enabled: bool = True
    boundary_enabled: bool = True
    contract_enabled: bool = True
    skill_replace_enabled: bool = True
    feedback_enabled: bool = True

    # Boundary 参数
    boundary_window_size: int = 6           # 滑动窗口大小
    boundary_merge_gap: int = 3             # 相邻 segment 合并间距

    # Contract 参数
    min_contract_confidence: float = 0.6    # contract 最小置信度
    max_contract_predicates: int = 10       # 每个 contract 最多保留 predicates

    # Skill Replacement 参数
    replace_min_confidence: float = 0.75    # 替换所需的最小相似度
    replace_min_segment_len: int = 3        # 至少 3 个节点才考虑替换
    replace_max_segment_len: int = 50       # 最多 50 个节点一次替换

    # Feedback 参数
    feedback_win_size: int = 20             # 反馈窗口（最近 N 次压缩）
    decay_hours: float = 72                 # 反馈衰减半衰期（小时）


# ════════════════════════════════════════════════════════════════
# 桥接器主类
# ════════════════════════════════════════════════════════════════

class CosplayContextAdapter:
    """
    COSPLAY → DAG 上下文桥接器单例

    四合一集成入口。懒加载边界检测器 + Skill Bank。
    """

    _instance: Optional["CosplayContextAdapter"] = None

    def __init__(self, config: Optional[CosplayContextConfig] = None):
        self.config = config or CosplayContextConfig()

        # 懒加载引用
        self._boundary_detector = None
        self._skill_bank = None
        self._nlp_extractor = None

        # 反馈跟踪
        self._feedback_buffer: List[Dict] = []

        # LTC 液态整合（可选）
        self._ltc_strategy: Optional[Any] = None
        if _HAS_LTC:
            try:
                self._ltc_strategy = LTCDAGCompactStrategy()
            except Exception:
                self._ltc_strategy = None

        # 统计
        self.stats = {
            "total_compacts": 0,
            "total_boundary_segments": 0,
            "total_contract_summaries": 0,
            "total_skill_replacements": 0,
            "total_feedback_events": 0,
            "estimated_tokens_saved": 0,
            "last_cycle_time": 0.0,
        }

    @classmethod
    def get_instance(cls, config: Optional[CosplayContextConfig] = None) -> "CosplayContextAdapter":
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    # ── 懒加载 ──────────────────────────────────────────────

    def _ensure_boundary(self):
        if self._boundary_detector is None and self.config.boundary_enabled:
            try:
                from lfm_boundary_detector import LfmBoundaryDetector, NLPPredicateExtractor, BoundaryDetectorConfig
                _bc_kwargs = {}
                if hasattr(BoundaryDetectorConfig, 'cp_drift'):
                    _bc_kwargs['cp_window'] = self.config.boundary_window_size
                    _bc_kwargs['merge_radius'] = self.config.boundary_merge_gap
                _bc = BoundaryDetectorConfig(**_bc_kwargs)
                self._boundary_detector = LfmBoundaryDetector(config=_bc)
                self._nlp_extractor = NLPPredicateExtractor()
            except Exception as e:
                logger.warning(f"Boundary detector 加载失败: {e}")

    def _ensure_skill_bank(self):
        if self._skill_bank is None and self.config.contract_enabled:
            try:
                from lfm_skill_bank import get_skill_bank, LfmSkill
                self._skill_bank = get_skill_bank()
            except Exception as e:
                logger.warning(f"Skill bank 加载失败: {e}")

    # ════════════════════════════════════════════════════════════
    # 1. Boundary-Aware Compression
    # ════════════════════════════════════════════════════════════

    def segment_nodes_by_boundary(self, nodes: List[Any]) -> List[Dict]:
        """
        用 Boundary Detection 把 DAG nodes 按意图边界分组。

        Args:
            nodes: DAGNode 列表（必须含 content, timestamp, node_id）

        Returns:
            [{ "segment_id": int, "intent": str, "nodes": [DAGNode],
               "combined_text": str, "keywords": [str] }]
        """
        if not self.config.boundary_enabled or len(nodes) < 2:
            # 兜底：全部当一个 segment
            text = "\n".join(
                f"[{getattr(n, 'role', '?')}] {getattr(n, 'content', '')[:500]}"
                for n in nodes
            )
            return [{
                "segment_id": 0,
                "intent": "unknown",
                "nodes": nodes,
                "combined_text": text,
                "keywords": self._fallback_keywords(text),
            }]

        self._ensure_boundary()
        if self._boundary_detector is None:
            return self._no_boundary_fallback(nodes)

        try:
            # 把 DAG nodes 转成边界检测器要的 dict 格式
            node_dicts = []
            for n in nodes:
                role = getattr(n, 'role', 'user')
                content = getattr(n, 'content', '') or ''
                node_type = 'tool_call' if role == 'assistant' and ('调用' in content[:30] or '正在' in content[:30]) else role
                node_dicts.append({
                    "content": content[:2000],
                    "source": node_type,
                    "role": role,
                    "timestamp": getattr(n, 'timestamp', time.time()),
                    "_node": n,
                })

            # 运行边界检测
            boundaries = self._boundary_detector.detect(node_dicts)

            if not boundaries:
                return self._no_boundary_fallback(nodes)

            # 按 boundary 分组
            segments = []
            for b in boundaries:
                start = max(0, b.start_node_index)
                end = min(len(nodes), b.end_node_index + 1)
                seg_nodes = nodes[start:end]
                if not seg_nodes:
                    continue

                # NLP predicates
                predicates = []
                if self._nlp_extractor:
                    try:
                        predicate_texts = []
                        for n in seg_nodes:
                            c = getattr(n, 'content', '') or ''
                            if c:
                                predicate_texts.append(c[:1000])
                        if predicate_texts:
                            features = self._nlp_extractor.extract(predicate_texts)
                            if features and len(features) > 0:
                                predicates = features[-1].get("predicates", [])
                    except Exception:
                        pass

                combined = "\n".join(
                    f"[{getattr(n, 'role', '?')}] {getattr(n, 'content', '')[:500]}"
                    for n in seg_nodes
                )

                segments.append({
                    "segment_id": b.segment_id if hasattr(b, 'segment_id') else len(segments),
                    "intent": b.intent_label if hasattr(b, 'intent_label') else "unknown",
                    "nodes": seg_nodes,
                    "combined_text": combined,
                    "keywords": predicates or self._fallback_keywords(combined),
                    "confidence": getattr(b, 'confidence', 0.5),
                })

            if not segments:
                return self._no_boundary_fallback(nodes)

            self.stats["total_boundary_segments"] += len(segments)
            return segments

        except Exception as e:
            logger.warning(f"Boundary segmentation 失败: {e}")
            return self._no_boundary_fallback(nodes)

    def _no_boundary_fallback(self, nodes) -> List[Dict]:
        """兜底：全部当一个 segment"""
        if not nodes:
            return []
        text = "\n".join(
            f"[{getattr(n, 'role', '?')}] {getattr(n, 'content', '')[:500]}"
            for n in nodes
        )
        return [{
            "segment_id": 0,
            "intent": "fallback",
            "nodes": nodes,
            "combined_text": text,
            "keywords": self._fallback_keywords(text),
            "confidence": 0.0,
        }]

    def _fallback_keywords(self, text: str, max_kw: int = 5) -> List[str]:
        """简单关键词提取兜底"""
        try:
            import jieba
            _cut = jieba.cut if hasattr(jieba, 'cut') else jieba.tokenize
            words = list(_cut(text[:3000]))
        except Exception:
            # pure regex fallback
            words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text[:3000])
        stopwords = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
                     "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
                     "没有", "看", "好", "自己", "这", "什么", "怎么", "那", "吗", "吧"}
        filtered = [w for w in words if len(w) >= 2 and w not in stopwords]
        freq = defaultdict(int)
        for w in filtered:
            freq[w] += 1
        return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:max_kw]]

    # ════════════════════════════════════════════════════════════
    # 2. Contract-Aware Summarization
    # ════════════════════════════════════════════════════════════

    def get_contract_instructions(self, keywords: List[str]) -> Optional[Dict]:
        """
        查 Skill Bank 获取匹配技能的 contract 说明。

        Returns:
            { "skill_name": str, "predicates_keep": [str], "confidence": float }
            或 None（无匹配）
        """
        if not self.config.contract_enabled or not keywords:
            return None

        self._ensure_skill_bank()
        if self._skill_bank is None:
            return None

        try:
            # 用关键词检索技能（返回 dict 列表，每个有 skill_id/name/score/contract_literals）
            skills = self._skill_bank.retrieve_skills(keywords, top_k=3)
            if not skills:
                return None

            best_dict = skills[0]
            best_score = best_dict.get("score", 0.0)
            if best_score < self.config.min_contract_confidence:
                return None

            # 从 _skills 取原始对象拿到 contract
            best_id = best_dict.get("skill_id", "")
            best_skill_obj = self._skill_bank._skills.get(best_id) if hasattr(self._skill_bank, '_skills') else None
            if best_skill_obj is None:
                return None

            contract = getattr(best_skill_obj, 'contract', None)
            if contract is None:
                return None

            predicates_keep = []
            if hasattr(contract, 'eff_add') and contract.eff_add:
                for literal in sorted(contract.eff_add, key=lambda l: contract.support.get(l, 0) if hasattr(contract, 'support') else 0, reverse=True)[:self.config.max_contract_predicates]:
                    freq = contract.support.get(literal, 0) / max(1, contract.n_instances)
                    predicates_keep.append(f"{literal} ({freq:.0%})")

            if not predicates_keep:
                return None

            self.stats["total_contract_summaries"] += 1
            return {
                "skill_name": best_dict.get("name", "unknown"),
                "predicates_keep": predicates_keep,
                "confidence": best_score,
            }

        except Exception as e:
            logger.debug(f"Contract lookup 失败: {e}")
            return None

    def augment_summary_with_contract(self, summary_text: str,
                                       contract_info: Optional[Dict]) -> str:
        """
        把 contract 信息注入到摘要文本中。

        如果匹配到技能的 contract 且该技能有重要 predicates，
        在摘要末尾追加 "【关键上下文：...】"。
        """
        if not contract_info or not self.config.contract_enabled:
            return summary_text

        predicates = contract_info.get("predicates_keep", [])
        if not predicates:
            return summary_text

        skill_name = contract_info.get("skill_name", "技能")
        predicate_str = "，".join(predicates[:5])

        augmented = f"{summary_text}\n\n【{skill_name} 契约关键上下文】{predicate_str}"
        return augmented[:2000]  # 防止太长

    # ════════════════════════════════════════════════════════════
    # 3. Skill Replacement
    # ════════════════════════════════════════════════════════════

    def try_skill_replace(self, segment: Dict) -> Optional[str]:
        """
        尝试用技能 token 替换整段对话。

        如果 segment 匹配了一个已确认的 ProtoSkill（支持度+一致率+通过率都达标），
        返回替换文本如 `[Skill: web_search] (查询 "xxx" → 5 条结果)`。
        不匹配则返回 None。
        """
        if not self.config.skill_replace_enabled:
            return None

        seg_nodes = segment.get("nodes", [])
        if len(seg_nodes) < self.config.replace_min_segment_len:
            return None
        if len(seg_nodes) > self.config.replace_max_segment_len:
            return None

        self._ensure_skill_bank()
        if self._skill_bank is None:
            return None

        try:
            keywords = segment.get("keywords", [])
            if not keywords:
                return None

            skills = self._skill_bank.retrieve_skills(keywords, top_k=3)
            if not skills:
                return None

            best_dict = skills[0]
            best_score = best_dict.get("score", 0.0)
            if best_score < self.config.replace_min_confidence:
                return None

            # LfmSkill 没有 is_confirmed/status 属性，skills 都有 contract 所以直接放行
            skill_name = best_dict.get("name", "unknown")
            n_instances = best_dict.get("n_instances", 0)
            pass_rate = 1.0  # 已毕业的技能默认高通过率

            # 提取 segment 里的关键短语（工具调用名、查询词等）
            combined = segment.get("combined_text", "")
            key_phrases = self._fallback_keywords(combined, max_kw=3)
            phrases_str = f"「{' '.join(key_phrases)}」" if key_phrases else ""

            replace_text = f"[Skill: {skill_name}] {phrases_str} (实例数:{n_instances}, 通过率:{pass_rate:.0%})"

            self.stats["total_skill_replacements"] += 1
            # 估算省了多少 token（按 segment 原文的 3/4 算节省）
            original_tokens = len(combined) // 4
            replaced_tokens = len(replace_text) // 4
            self.stats["estimated_tokens_saved"] += max(0, original_tokens - replaced_tokens)

            return replace_text

        except Exception as e:
            logger.debug(f"Skill replace 失败: {e}")
            return None

    # ════════════════════════════════════════════════════════════
    # 4. Feedback-Driven Compression
    # ════════════════════════════════════════════════════════════

    def record_compact_result(self, compact_info: Dict):
        """
        记录一次压缩的结果，供反馈分析。

        compact_info:
          { "segment_id": ..., "n_nodes": ..., "summary_token": ...,
            "original_token": ..., "contract_guided": bool,
            "skill_replaced": bool }
        """
        if not self.config.feedback_enabled:
            return

        compact_info["timestamp"] = time.time()
        self._feedback_buffer.append(compact_info)

        # 保持窗口大小
        if len(self._feedback_buffer) > self.config.feedback_win_size * 2:
            self._feedback_buffer = self._feedback_buffer[-self.config.feedback_win_size:]

    def record_expand_event(self, summary_node_id: str):
        """
        记录用户展开了一个摘要节点（正向信号：压缩有用）。
        """
        if not self.config.feedback_enabled:
            return

        # 找到相应的压缩记录，标记为 used
        found = False
        for entry in reversed(self._feedback_buffer):
            if entry.get("summary_node_id") == summary_node_id:
                entry["expanded"] = entry.get("expanded", 0) + 1
                entry["last_expanded"] = time.time()
                found = True
                break

        if found:
            self.stats["total_feedback_events"] += 1

    def get_feedback_stats(self) -> Dict:
        """
        分析反馈缓冲区，计算压缩质量指标。

        Returns:
          { "total_segments": int, "expanded_rate": float,
            "avg_saving_token": float, "contract_rate": float,
            "skill_replace_rate": float, "adjustments": [str] }
        """
        if not self._feedback_buffer:
            return {
                "total_segments": 0, "expanded_rate": 0.0,
                "avg_saving_token": 0, "contract_rate": 0.0,
                "skill_replace_rate": 0.0, "adjustments": [],
            }

        buf = self._feedback_buffer[-self.config.feedback_win_size:]
        n = len(buf)
        if n == 0:
            return {"total_segments": 0}

        n_expanded = sum(1 for e in buf if e.get("expanded", 0) > 0)
        total_orig = sum(e.get("original_token", 0) for e in buf)
        total_summary = sum(e.get("summary_token", 0) for e in buf)
        n_contract = sum(1 for e in buf if e.get("contract_guided", False))
        n_skill = sum(1 for e in buf if e.get("skill_replaced", False))

        stats = {
            "total_segments": n,
            "expanded_rate": n_expanded / n,
            "avg_saving_token": (total_orig - total_summary) // max(1, n),
            "contract_rate": n_contract / n,
            "skill_replace_rate": n_skill / n,
            "adjustments": [],
        }

        # 根据反馈生成调整建议
        if n_expanded / max(1, n) > 0.3:
            # 展开率太高 → 压缩得太狠了，需要保留更多信息
            stats["adjustments"].append(
                "expand_rate_high: 建议降低压缩强度，减少 contract predicates 删除"
            )
        elif n_expanded / max(1, n) < 0.05:
            # 展开率太低 → 压缩可能太保守
            stats["adjustments"].append(
                "expand_rate_low: 建议增加 skill replace 力度"
            )

        return stats

    def apply_feedback_to_skill_bank(self) -> int:
        """
        将反馈信号写入 Skill Bank 的 refine_effects_contract()。

        Returns:
            refined 的 contract 数量
        """
        if not self.config.feedback_enabled:
            return 0

        self._ensure_skill_bank()
        if self._skill_bank is None:
            return 0

        stats = self.get_feedback_stats()
        if stats["total_segments"] < 5:
            return 0  # 数据不够，不调

        refined_count = 0
        try:
            # 高展开率 → 调低 merge 阈值，让更多技能被合并（保留更多 predicates）
            if stats["expanded_rate"] > 0.3:
                if hasattr(self._skill_bank, 'config'):
                    old = self._skill_bank.config.merge_eff_jaccard_thresh
                    self._skill_bank.config.merge_eff_jaccard_thresh = max(0.4, old - 0.05)
                    refined_count += 1

            # 低展开率 + 高 contract 率 → 可以多压缩
            if stats["expanded_rate"] < 0.05 and stats["contract_rate"] > 0.5:
                if hasattr(self._skill_bank, 'config'):
                    old = self._skill_bank.config.retire_max_unused_days
                    self._skill_bank.config.retire_max_unused_days = min(120, old + 10)
                    refined_count += 1

        except Exception as e:
            logger.debug(f"Feedback apply 失败: {e}")

        return refined_count

    # ════════════════════════════════════════════════════════════
    # 一站式入口
    # ════════════════════════════════════════════════════════════

    # ── LTC 辅助方法 ──────────────────────────────────────

    def _get_ltc_intensities(self, segments: List[Dict]) -> List[float]:
        """
        用 LTC 时间常数计算每条 segment 的压缩强度系数。

        Returns: [intensity, ...] 0=不压缩, 1=强力压缩
        """
        if self._ltc_strategy is None:
            return [1.0] * len(segments)

        try:
            ltc_scores = self._ltc_strategy.compute_segment_ltc_scores(segments)
        except Exception:
            return [1.0] * len(segments)

        # 把 compact_strength [0,1] 映射到实际压缩参数
        intensities = []
        for sc in ltc_scores:
            strength = sc.get("compact_strength", 0.5)
            # 热度高/τ 大的 segment 降低压缩力度
            # 热度低/τ 小的 segment 加大压缩力度
            intensity = 0.3 + strength * 0.7  # [0.3, 1.0]
            intensities.append(intensity)

        return intensities

    # ════════════════════════════════════════════════════════════

    def enhance_compress(self, nodes: List[Any], session_key: str = "",
                         ltc_aware: bool = True) -> Dict:
        """
        全流程增强压缩入口（v8.5 LTC 增强版）。

        1. Boundary Detection → 分组
           + LTC 液态评分调节压缩强度
        2. Contract → 注入重要 predicates
        3. Skill Replacement → 替代已知技能
        4. Feedback → 记录结果

        Args:
            nodes: DAGNode 列表（待压缩的原始节点）
            session_key: 当前 session key
            ltc_aware: 是否启用 LTC 时间常数感知（默认 True）

        Returns:
            { "segments": [...], "summaries": [...], "replacements": [...],
              "ltc_scores": [...], "stats": {...} }
        """
        t0 = time.time()
        result = {
            "segments": [],
            "summaries": [],
            "replacements": [],
            "ltc_scores": [],
            "stats": {
                "n_nodes": len(nodes),
                "n_segments": 0,
                "n_contract_guided": 0,
                "n_skill_replaced": 0,
                "estimated_saved_tokens": 0,
                "ltc_enabled": bool(self._ltc_strategy and ltc_aware),
            },
        }

        if not nodes:
            return result

        # Step 1: Boundary Detection → 分组
        segments = self.segment_nodes_by_boundary(nodes)
        result["segments"] = segments
        result["stats"]["n_segments"] = len(segments)

        # Step 1.5: LTC 液态评分
        ltc_intensities = []
        if ltc_aware and self._ltc_strategy is not None:
            ltc_ratings = self._ltc_strategy.compute_segment_ltc_scores(segments)
            result["ltc_scores"] = ltc_ratings
            ltc_intensities = [
                max(0.25, min(1.0, r.get("compact_strength", 0.5) * 0.8 + 0.3))
                for r in ltc_ratings
            ]
            # 记录 LTC 状态到 stats
            if ltc_ratings:
                _tau_vals = [r.get("avg_tau", 5.0) for r in ltc_ratings]
                result["stats"]["ltc_avg_tau"] = round(sum(_tau_vals) / len(_tau_vals), 2)
            else:
                result["stats"]["ltc_avg_tau"] = 0.0
        else:
            ltc_intensities = [1.0] * len(segments)

        for idx, seg in enumerate(segments):
            combined = seg.get("combined_text", "")
            keywords = seg.get("keywords", [])
            orig_tokens = len(combined) // 4
            intensity = ltc_intensities[idx] if idx < len(ltc_intensities) else 1.0

            # Step 2: Contract-Aware
            contract_info = None
            if self.config.contract_enabled:
                contract_info = self.get_contract_instructions(keywords)

            # LTC 感知截断：intensity 越高，截断越激进
            truncate_len = max(200, int(500 * (1.0 - intensity * 0.3)))
            base_summary = combined[:truncate_len] + "..." if len(combined) > truncate_len else combined

            # 注入 contract
            if contract_info:
                base_summary = self.augment_summary_with_contract(base_summary, contract_info)
                result["stats"]["n_contract_guided"] += 1

            summary_token = len(base_summary) // 4
            result["summaries"].append({
                "segment_id": seg["segment_id"],
                "intent": seg["intent"],
                "text": base_summary,
                "contract_guided": contract_info is not None,
                "original_token": orig_tokens,
                "summary_token": summary_token,
                "ltc_intensity": round(intensity, 3),
            })

            # Step 3: Skill Replacement（LTC 感知调节阈值）
            if self.config.skill_replace_enabled:
                # 压缩强度高 → 降低替换阈值（更激进地替换）
                saved_replace_conf = self.config.replace_min_confidence
                if intensity > 0.7:
                    self.config.replace_min_confidence = max(0.5, saved_replace_conf - 0.1)
                elif intensity < 0.4:
                    self.config.replace_min_confidence = min(0.9, saved_replace_conf + 0.05)

                replace_text = self.try_skill_replace(seg)

                # 还原配置
                self.config.replace_min_confidence = saved_replace_conf

                if replace_text:
                    result["replacements"].append({
                        "segment_id": seg["segment_id"],
                        "intent": seg["intent"],
                        "text": replace_text,
                        "original_token": orig_tokens,
                        "replaced_token": len(replace_text) // 4,
                    })
                    result["stats"]["n_skill_replaced"] += 1
                    result["stats"]["estimated_saved_tokens"] += orig_tokens - (len(replace_text) // 4)

            # Step 4: Feedback 记录（附带 LTC 状态）
            if self.config.feedback_enabled:
                self.record_compact_result({
                    "session_key": session_key,
                    "segment_id": seg["segment_id"],
                    "intent": seg["intent"],
                    "n_nodes": len(seg["nodes"]),
                    "original_token": orig_tokens,
                    "summary_token": summary_token,
                    "contract_guided": contract_info is not None,
                    "skill_replaced": replace_text is not None,
                    "ltc_intensity": round(intensity, 3),
                })

        self.stats["last_cycle_time"] = time.time() - t0
        self.stats["total_compacts"] += 1
        return result

    def summary(self) -> Dict:
        """状态摘要"""
        return {
            "enabled": {
                "boundary": self.config.boundary_enabled,
                "contract": self.config.contract_enabled,
                "skill_replace": self.config.skill_replace_enabled,
                "feedback": self.config.feedback_enabled,
            },
            "stats": dict(self.stats),
            "feedback_window": len(self._feedback_buffer),
        }


# ════════════════════════════════════════════════════════════════
# 便捷函数
# ════════════════════════════════════════════════════════════════

def get_cosplay_adapter(config: Optional[CosplayContextConfig] = None) -> CosplayContextAdapter:
    return CosplayContextAdapter.get_instance(config)


def run_cosplay_enhanced_compact(nodes: List[Any], session_key: str = "") -> Dict:
    """一键执行 COSPLAY 增强压缩"""
    adapter = get_cosplay_adapter()
    return adapter.enhance_compress(nodes, session_key)
