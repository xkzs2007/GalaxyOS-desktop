#!/usr/bin/env python3
"""
LFM Boundary Detector — 移植 COSPLAY Boundary Proposal 到 GalaxyOS DAG

架构映射:
  COSPLAY Changepoint Detection  →  DAG 节点 embedding 序列的意图变化点检测
  COSPLAY Signal Extractors      →  NER/关键词/NLP 增强的 predicate 提取
  COSPLAY Boundary Candidates    →  DAG segment 边界标记（用于 CLS 前分组）
  COSPLAY LLMSignalExtractor     →  DAG 节点内容 → 结构化 predicates

集成入口:
  memory_consolidation.py: ConsolidationEngine._run_consolidation_cycle()
  dag_context_manager.py:   add_message() 时标记意图边界

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-06-23 (移植自 COSPLAY: arxiv 2604.20987)
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import hashlib
import logging
import numpy as np
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Set, Tuple, Callable
from galaxyos.shared.paths import workspace

logger = logging.getLogger("lfm_boundary_detector")


# ════════════════════════════════════════════════════════════════
# 配置
# ════════════════════════════════════════════════════════════════

@dataclass
class BoundaryDetectorConfig:
    """边界检测器全部配置参数"""
    
    # ── Changepoint Detection ──
    cp_method: str = "cusum"                # cusum / sliding_window
    cp_drift: float = 0.05                  # CUSUM drift 参数（越小越敏感）
    cp_window: int = 10                     # Sliding window 半窗口大小
    
    # ── Trigger 生成 ──
    merge_radius: int = 5                   # 候选合并半径（timesteps）
    window_half_width: int = 2              # 候选窗口半宽
    
    # ── Surprisal —─
    surprisal_std_factor: float = 2.0
    surprisal_local_radius: int = 3
    
    # ── Intention Tag ──
    tag_min_segment_len: int = 2            # 标签最小段长度
    tag_completion_bonus: float = 1.5       # 完成标签的权重
    
    # ── NLP 增强 ──
    nlp_max_keywords_per_node: int = 5
    nlp_min_keyword_len: int = 2
    enable_ner: bool = True                 # 启用 NER 作为 predicate
    enable_keywords: bool = True            # 启用关键词作为 predicate
    enable_sentiment: bool = True           # 启用情感作为 predicate
    
    # ── 最小边界密度 ──
    target_segment_length: int = 5          # 目标段长度（步骤数）
    target_segment_count: int = 12          # 目标段数
    min_boundaries_enabled: bool = True
    
    # ── 持久化 ──
    workspace: str = ""


# ════════════════════════════════════════════════════════════════
# 边界候选数据模型
# ════════════════════════════════════════════════════════════════

@dataclass
class BoundaryCandidate:
    """单个边界候选点（类比 COSPLAY BoundaryCandidate）"""
    center: int                             # 中心时间步
    half_window: int = 0                    # 允许的边界窗口半宽
    source: str = "unknown"                 # 信号来源
    confidence: float = 1.0                 # 置信度
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> BoundaryCandidate:
        return cls(**d)


@dataclass
class SegmentBoundary:
    """DAG segment 边界标记"""
    segment_id: str                         # segment ID
    start_node_index: int                   # DAG 节点起始索引
    end_node_index: int                     # DAG 节点结束索引
    intent_label: str = ""                  # 意图标签
    confidence: float = 1.0                 # 置信度
    predicates: Dict[str, float] = field(default_factory=dict)  # 该段 predicate 摘要
    keywords: List[str] = field(default_factory=list)           # 该段关键词
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: dict) -> SegmentBoundary:
        d = dict(d)
        return cls(**d)


# ════════════════════════════════════════════════════════════════
# Changepoint Detection（从 embedding 序列检测变化点）
# ════════════════════════════════════════════════════════════════

def _cosine_distance_seq(embeddings: np.ndarray) -> np.ndarray:
    """相邻 embedding 的余弦距离"""
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-9, None)
    normed = embeddings / norms
    dots = np.sum(normed[:-1] * normed[1:], axis=1)
    dists = 1.0 - dots
    return np.concatenate([[0.0], dists])


def cusum_changepoint_scores(
    embeddings: np.ndarray,
    drift: float = 0.05,
) -> np.ndarray:
    """CUSUM 变化点分数（累积偏差检测）"""
    dists = _cosine_distance_seq(embeddings)
    T = len(dists)
    scores = np.zeros(T, dtype=np.float64)
    cusum = 0.0
    for t in range(T):
        cusum = max(0.0, cusum + dists[t] - drift)
        scores[t] = cusum
    return scores


def sliding_window_divergence(
    embeddings: np.ndarray,
    window_size: int = 10,
) -> np.ndarray:
    """滑动窗口 KL/余弦散度检测"""
    T, dim = embeddings.shape
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-9, None)
    normed = embeddings / norms
    
    scores = np.zeros(T, dtype=np.float64)
    w = window_size
    for t in range(w, T - w):
        past_mean = normed[t - w: t].mean(axis=0)
        future_mean = normed[t: t + w].mean(axis=0)
        past_norm = np.linalg.norm(past_mean)
        future_norm = np.linalg.norm(future_mean)
        if past_norm < 1e-9 or future_norm < 1e-9:
            continue
        cos_sim = np.dot(past_mean, future_mean) / (past_norm * future_norm)
        scores[t] = 1.0 - cos_sim
    return scores


def compute_changepoint_scores(
    embeddings: np.ndarray,
    method: str = "cusum",
    drift: float = 0.05,
    window_size: int = 10,
) -> np.ndarray:
    """统一 changepoint 接口"""
    if len(embeddings) < 3:
        return np.zeros(len(embeddings), dtype=np.float64)
    if method == "cusum":
        return cusum_changepoint_scores(embeddings, drift=drift)
    elif method == "sliding_window":
        return sliding_window_divergence(embeddings, window_size=window_size)
    else:
        raise ValueError(f"Unknown method: {method}")


# ════════════════════════════════════════════════════════════════
# Trigger 生成（从多种信号提取候选边界）
# ════════════════════════════════════════════════════════════════

def _triggers_from_changepoint(
    scores: np.ndarray,
    config: BoundaryDetectorConfig,
) -> List[Tuple[int, str, float]]:
    """C) Embedding changepoint → 局部最大值候选"""
    out: List[Tuple[int, str, float]] = []
    T = len(scores)
    r = config.merge_radius
    for t in range(T):
        cp = scores[t]
        if np.isnan(cp) or cp == 0:
            continue
        left = max(0, t - r)
        right = min(T, t + r + 1)
        if float(np.nanmax(scores[left:right])) == cp:
            out.append((t, "changepoint", float(cp)))
    return out


def _triggers_from_intent_tags(
    intent_tags: List[str],
    config: BoundaryDetectorConfig,
) -> List[Tuple[int, str, float]]:
    """D) 意图标签变化 → 候选边界"""
    out: List[Tuple[int, str, float]] = []
    T = len(intent_tags)
    if T < 2:
        return out
    
    for t in range(1, T):
        if intent_tags[t] and intent_tags[t] != intent_tags[t - 1]:
            out.append((t, "intent_tag", 1.0))
    
    return out


def _triggers_from_nlp_features(
    features: List[Dict[str, Any]],
    config: BoundaryDetectorConfig,
) -> List[Tuple[int, str, float]]:
    """E) NLP 特征变化 → 候选边界"""
    out: List[Tuple[int, str, float]] = []
    T = len(features)
    if T < 2:
        return out
    
    for t in range(1, T):
        prev = features[t - 1]
        curr = features[t]
        
        # 情感极性变化
        if config.enable_sentiment:
            p_sent = prev.get("sentiment_polarity", 0.0)
            c_sent = curr.get("sentiment_polarity", 0.0)
            if abs(c_sent - p_sent) > 0.5:
                out.append((t, "sentiment_shift", abs(c_sent - p_sent)))
        
        # 实体类型变化
        if config.enable_ner:
            p_entities = set(prev.get("entities", {}).keys())
            c_entities = set(curr.get("entities", {}).keys())
            if p_entities and c_entities:
                jac = len(p_entities & c_entities) / len(p_entities | c_entities)
                if jac < 0.3:
                    out.append((t, "entity_shift", 1.0 - jac))
    
    return out


def _triggers_from_hard_events(
    event_indices: List[int],
) -> List[Tuple[int, str, float]]:
    """F) 硬事件（R-CCAM cycle 完成、工具调用完成等）"""
    return [(t, "hard_event", 1.0) for t in event_indices]


def _merge_and_window(
    triggers: List[Tuple[int, str, float]],
    merge_radius: int,
    window_half_width: int,
) -> List[BoundaryCandidate]:
    """合并附近候选点 → BoundaryCandidate 列表"""
    if not triggers:
        return []
    
    triggers = sorted(triggers, key=lambda x: x[0])
    merged: List[Tuple[int, List[str], List[float]]] = []
    group_anchor = triggers[0][0]
    group_times = [triggers[0][0]]
    group_sources = [triggers[0][1]]
    group_confs = [triggers[0][2]]
    
    for t, src, conf in triggers[1:]:
        if t <= group_anchor + merge_radius:
            group_times.append(t)
            group_sources.append(src)
            group_confs.append(conf)
        else:
            center = group_times[len(group_times) // 2]
            merged.append((center, list(dict.fromkeys(group_sources)), group_confs))
            group_anchor = t
            group_times = [t]
            group_sources = [src]
            group_confs = [conf]
    
    center = group_times[len(group_times) // 2]
    merged.append((center, list(dict.fromkeys(group_sources)), group_confs))
    
    return [
        BoundaryCandidate(
            center=c,
            half_window=window_half_width,
            source="+".join(sources),
            confidence=float(np.mean(confs)) if confs else 1.0,
        )
        for c, sources, confs in merged
    ]


# ════════════════════════════════════════════════════════════════
# NLP Predicate 提取（替代 COSPLAY LLMSignalExtractor）
# ════════════════════════════════════════════════════════════════

class NLPPredicateExtractor:
    """使用 nlp_processor 提取结构化 predicates
    
    为每条 DAG 节点内容提取：
      - 关键词 → state.{keyword}  predicate
      - 实体   → entity.{type}.{name} predicate
      - 情感   → sentiment.polarity predicate
      - 意图   → intent.{label} predicate（从上下文推断）
    """
    
    def __init__(self, config: Optional[BoundaryDetectorConfig] = None):
        self.config = config or BoundaryDetectorConfig()
        self._nlp = None
    
    def _get_nlp(self):
        if self._nlp is None:
            try:
                from nlp_processor import NLPProcessor
                self._nlp = NLPProcessor()
            except Exception as e:
                logger.warning(f"NLP not available: {e}")
                self._nlp = False
        return self._nlp if self._nlp is not False else None
    
    def extract(self, texts: List[str]) -> List[Dict[str, Any]]:
        """批量提取 NLP features"""
        features: List[Dict[str, Any]] = []
        nlp = self._get_nlp()
        
        for text in texts:
            feat: Dict[str, Any] = {
                "keywords": [],
                "entities": {},
                "sentiment_polarity": 0.0,
                "intent_label": "",
            }
            
            if not text or not text.strip():
                features.append(feat)
                continue
            
            if nlp:
                try:
                    # 关键词
                    if self.config.enable_keywords:
                        kwds = nlp.extract_keywords(text, top_k=self.config.nlp_max_keywords_per_node)
                        feat["keywords"] = kwds if isinstance(kwds, list) else [str(kwds)]
                    
                    # NER
                    if self.config.enable_ner:
                        entities = nlp.recognize_entities(text)
                        if isinstance(entities, list):
                            for ent in entities:
                                if isinstance(ent, tuple) and len(ent) >= 2:
                                    ent_type, ent_name = ent[0], ent[1]
                                    feat["entities"][f"{ent_type}.{ent_name}"] = 1.0
                                elif isinstance(ent, str):
                                    feat["entities"][ent] = 1.0
                    
                    # 情感
                    if self.config.enable_sentiment:
                        sentiment = nlp.sentiment_analysis(text)
                        if isinstance(sentiment, dict):
                            feat["sentiment_polarity"] = sentiment.get("polarity", 0.0) or 0.0
                        elif isinstance(sentiment, (int, float)):
                            feat["sentiment_polarity"] = float(sentiment)
                except Exception as e:
                    logger.debug(f"NLP extraction failed: {e}")
            
            # 兜底关键词提取
            if not feat["keywords"]:
                feat["keywords"] = self._simple_extract_keywords(text)
            
            features.append(feat)
        
        return features
    
    def _simple_extract_keywords(self, text: str) -> List[str]:
        """简单关键词提取（NLP 不可用时的兜底）"""
        # 英文单词
        en_words = re.findall(r'\b[a-zA-Z_]{3,}\b', text)
        # 中文词组
        cn_chars = re.findall(r'[\u4e00-\u9fff]+', text)
        cn_words = []
        for chunk in cn_chars:
            if 2 <= len(chunk) <= 6:
                cn_words.append(chunk)
            elif len(chunk) > 6:
                for j in range(0, len(chunk) - 1, 2):
                    gram = chunk[j:j+2]
                    if len(gram) == 2:
                        cn_words.append(gram)
        
        tokens = en_words + cn_words
        stopwords = {"the", "and", "for", "was", "are", "has", "had", "but",
                     "not", "this", "that", "with", "from", "可以", "这个",
                     "那个", "一个", "没有", "什么", "怎么", "之后", "然后",
                     "因为", "所以", "但是", "就是", "如果"}
        counter = Counter(tokens)
        return [t for t, c in counter.most_common(self.config.nlp_max_keywords_per_node * 2)
                if t not in stopwords and len(t) >= self.config.nlp_min_keyword_len
               ][:self.config.nlp_max_keywords_per_node]
    
    def extract_predicates(self, texts: List[str]) -> List[Dict[str, float]]:
        """提取结构化 predicates（供 Skill Bank Contract 使用）"""
        features = self.extract(texts)
        predicates_list: List[Dict[str, float]] = []
        
        for feat in features:
            preds: Dict[str, float] = {}
            
            # 关键词 → state.{kw}
            for kw in feat.get("keywords", []):
                preds[f"state.{kw}"] = 1.0
            
            # 实体 → entity.{type}.{name}
            for ent_key in feat.get("entities", {}):
                preds[ent_key] = 1.0
            
            # 情感
            if self.config.enable_sentiment:
                sp = feat.get("sentiment_polarity", 0.0)
                if sp > 0.3:
                    preds["sentiment.positive"] = sp
                elif sp < -0.3:
                    preds["sentiment.negative"] = abs(sp)
                else:
                    preds["sentiment.neutral"] = 1.0 - abs(sp)
            
            predicates_list.append(preds)
        
        return predicates_list


# ════════════════════════════════════════════════════════════════
# Intent Classifier（从 DAG 节点推断意图标签）
# ════════════════════════════════════════════════════════════════

class DAGIntentClassifier:
    """DAG 节点意图分类器
    
    为每条节点内容推断意图标签（如 search / analyze / code / memory / chat）
    基于关键词 + 来源 + 简单的模式匹配
    """
    
    # 意图 → 关键词映射
    INTENT_PATTERNS: Dict[str, List[str]] = {
        "search": ["搜索", "查找", "查询", "search", "find", "lookup", "检索"],
        "analyze": ["分析", "解析", "比较", "对比", "analyze", "parse", "compare"],
        "code": ["代码", "函数", "类", "方法", "import", "def ", "class ",
                 "return", "git ", "commit", "push", "部署", "编译"],
        "memory": ["记住", "回忆", "记忆", "remember", "recall", "memory", "store"],
        "chat": ["你好", "嗨", "hello", "hi", "how", "what", "为什么", "怎么"],
        "tool": ["工具", "调用", "tool", "rccam", "workflow", "执行"],
        "think": ["思考", "推理", "plan", "计划", "strategy", "think", "推理"],
        "error": ["错误", "失败", "bug", "error", "fail", "exception", "崩溃"],
        "config": ["配置", "设置", "config", "setting", "修改", "update", "install"],
    }
    
    def classify(self, text: str, source: str = "") -> str:
        if not text:
            return "unknown"
        text_lower = text.lower()
        
        # 按来源
        if "system" in source.lower():
            return "system"
        if "tool" in source.lower():
            return "tool"
        
        scores: Dict[str, int] = {}
        for intent, patterns in self.INTENT_PATTERNS.items():
            score = sum(1 for p in patterns if p in text_lower)
            if score > 0:
                scores[intent] = score
        
        if scores:
            return max(scores, key=scores.get)
        return "general"


# ════════════════════════════════════════════════════════════════
# Segment 边界检测器（主入口）
# ════════════════════════════════════════════════════════════════

class LfmBoundaryDetector:
    """LFM 边界检测器
    
    从 DAG 节点序列检测自然边界 → 标记 SegmentBoundary
    供 ConsolidationEngine 在 CLS 之前使用
    
    工作流：
      1. detect() — 接收 DAG 节点列表
      2. 提取 embedding / NLP features / 意图标签
      3. 多信号生成 trigger 候选
      4. 合并 + 窗口化 → SegmentBoundary[]
    """
    
    def __init__(self, config: Optional[BoundaryDetectorConfig] = None):
        self.config = config or BoundaryDetectorConfig()
        self._nlp_extractor = NLPPredicateExtractor(self.config)
        self._intent_classifier = DAGIntentClassifier()
    
    def detect(
        self,
        nodes: List[Dict[str, Any]],
        embeddings: Optional[np.ndarray] = None,
        hard_event_indices: Optional[List[int]] = None,
    ) -> List[SegmentBoundary]:
        """从 DAG 节点列表检测自然边界
        
        Parameters
        ----------
        nodes : List[Dict]
            DAG 节点列表，每个节点至少包含 "content", "source", "timestamp"
        embeddings : np.ndarray, optional
            节点 embedding 序列，shape (N, dim)
        hard_event_indices : List[int], optional
            硬事件节点索引（R-CCAM cycle 边界等）
        
        Returns
        -------
        List[SegmentBoundary]
            检测到的 segment 边界
        """
        N = len(nodes)
        if N < 2:
            return [SegmentBoundary(
                segment_id="full",
                start_node_index=0,
                end_node_index=N - 1,
            )]
        
        # 1. 提取内容列表
        texts = [n.get("content", "") or "" for n in nodes]
        sources = [n.get("source", "") or "" for n in nodes]
        
        # 2. 提取 NLP features + predicates
        nlp_features = self._nlp_extractor.extract(texts)
        predicates = self._nlp_extractor.extract_predicates(texts)
        
        # 3. 推断意图标签
        intent_tags = []
        for i, (text, source) in enumerate(zip(texts, sources)):
            tag = self._intent_classifier.classify(text, source)
            intent_tags.append(tag)
        
        # 4. 生成 trigger
        triggers: List[Tuple[int, str, float]] = []
        
        # Changepoint
        if embeddings is not None and len(embeddings) >= 3:
            cp_scores = compute_changepoint_scores(
                embeddings,
                method=self.config.cp_method,
                drift=self.config.cp_drift,
                window_size=self.config.cp_window,
            )
            triggers.extend(_triggers_from_changepoint(cp_scores, self.config))
        
        # Intent tag 变化
        triggers.extend(_triggers_from_intent_tags(intent_tags, self.config))
        
        # NLP 特征变化
        triggers.extend(_triggers_from_nlp_features(nlp_features, self.config))
        
        # 硬事件
        if hard_event_indices:
            triggers.extend(_triggers_from_hard_events(hard_event_indices))
        
        # 5. 合并候选
        candidates = _merge_and_window(
            triggers,
            self.config.merge_radius,
            self.config.window_half_width,
        )
        
        # 6. 最小边界密度
        if self.config.min_boundaries_enabled and self.config.target_segment_length > 0:
            min_boundaries = max(1, N // self.config.target_segment_length - 1)
            existing_centers = {c.center for c in candidates}
            if len(existing_centers) < min_boundaries:
                step = N / (min_boundaries + 1)
                for k in range(1, min_boundaries + 1):
                    fb_t = int(round(k * step))
                    fb_t = max(1, min(fb_t, N - 2))
                    if fb_t not in existing_centers:
                        candidates.append(BoundaryCandidate(
                            center=fb_t,
                            half_window=self.config.window_half_width,
                            source="uniform_fallback",
                            confidence=0.5,
                        ))
        
        # 7. 按中心排序
        candidates.sort(key=lambda c: c.center)
        
        # 8. 构建 SegmentBoundary 列表
        cut_indices = sorted(set([0] + [c.center for c in candidates] + [N - 1]))
        segments: List[SegmentBoundary] = []
        
        for i in range(len(cut_indices) - 1):
            start = cut_indices[i]
            end = cut_indices[i + 1]
            if end <= start:
                continue
            
            # 取该段关键词聚合
            seg_keywords: List[str] = []
            seg_preds: Dict[str, float] = {}
            for j in range(start, min(end + 1, N)):
                if j < len(nlp_features):
                    seg_keywords.extend(nlp_features[j].get("keywords", []))
                if j < len(predicates):
                    seg_preds.update(predicates[j])
            
            # 去重关键词
            seg_keywords = list(dict.fromkeys(seg_keywords))[:10]
            
            # 意图标签（取该段最常见的）
            if start < len(intent_tags):
                tag_counter = Counter(intent_tags[start:min(end + 1, len(intent_tags))])
                intent_label = tag_counter.most_common(1)[0][0] if tag_counter else "unknown"
            else:
                intent_label = "unknown"
            
            segment = SegmentBoundary(
                segment_id=f"seg_{i}",
                start_node_index=start,
                end_node_index=end,
                intent_label=intent_label,
                predicates=seg_preds,
                keywords=seg_keywords,
                confidence=1.0,
            )
            segments.append(segment)
        
        return segments
    
    def detect_from_dag(self, dag, session_key: str) -> List[SegmentBoundary]:
        """从 DAG 实例检测边界
        
        便利接口：传 DAGContextManager 实例 + session_key
        """
        try:
            nodes = dag.get_session_nodes(session_key)
        except Exception as e:
            logger.warning(f"get_session_nodes failed: {e}")
            return []
        
        node_dicts = []
        for n in nodes:
            nd = {"content": getattr(n, "content", "") or "",
                  "source": getattr(n, "node_type", "") or getattr(n, "source", "") or ""}
            node_dicts.append(nd)
        
        return self.detect(node_dicts)


# ════════════════════════════════════════════════════════════════
# R-CCAM → Skill Bank 反馈桥
# ════════════════════════════════════════════════════════════════

class RCCAMFeedbackBridge:
    """R-CCAM → Skill Bank 反馈桥
    
    在 R-CCAM Action 阶段完成后，将执行结果反馈给 Skill Bank：
      1. Action 的调用链 → 新的 segment 记录
      2. Action 的成功/失败 → contract 验证信号
      3. Action 的输出 → ProtoSkill 发现
    
    集成入口：
      XiaoYiClawLLM.process() 的 Action 阶段末尾
    """
    
    def __init__(self, skill_bank=None):
        self._skill_bank = skill_bank
    
    def feed_action_to_skill_bank(
        self,
        action_info: Dict[str, Any],
        result_text: str,
        success: bool = True,
    ) -> Dict:
        """将一次 Action 的执行结果反馈给 Skill Bank
        
        Parameters
        ----------
        action_info : dict
            Action 信息，包含 tool_name, params, strategy 等
        result_text : str
            Action 的执行结果文本
        success : bool
            Action 是否成功
        
        Returns
        -------
        dict
            Skill Bank 的处理结果
        """
        from lfm_skill_bank import (
            LfmSkillBankConfig, LfmSegmentRecord,
            get_skill_bank, feed_memory_to_skill_bank,
        )
        
        bank = self._skill_bank or get_skill_bank()
        
        tool_name = action_info.get("tool_name", action_info.get("action", "unknown"))
        strategy = action_info.get("strategy", "unknown")
        params = str(action_info.get("params", {}))
        
        # 构建记忆记录
        memories = [
            {
                "content": f"Action: {tool_name} with params {params[:200]}",
                "source": "tool_call",
                "type": "tool_call",
                "timestamp": time.time(),
                "metadata": {
                    "tool": tool_name,
                    "tool_name": tool_name,
                    "session_id": "rccam_action",
                    "strategy": strategy,
                },
            },
            {
                "content": result_text[:500] if result_text else f"Action {tool_name} completed",
                "source": "tool_result",
                "type": "tool_result",
                "timestamp": time.time() + 0.001,
                "metadata": {
                    "tool": tool_name,
                    "tool_name": tool_name,
                    "session_id": "rccam_action",
                    "reward": 20.0 if success else -5.0,
                    "strategy": strategy,
                },
            },
        ]
        
        result = feed_memory_to_skill_bank(memories)
        return result
    
    def feed_cycle_result(
        self,
        cycle_info: Dict[str, Any],
    ) -> Dict:
        """将整个 R-CCAM cycle 结果反馈给 Skill Bank
        
        Parameters
        ----------
        cycle_info : dict
            包含 retrieval/cognition/control/action/memory 各阶段信息
        """
        from lfm_skill_bank import get_skill_bank, LfmSegmentRecord
        bank = self._skill_bank or get_skill_bank()
        
        strategy = cycle_info.get("strategy", "unknown")
        answer = cycle_info.get("answer", "")
        confidence = cycle_info.get("confidence", 0.0)
        tool_used = cycle_info.get("action", {}).get("tool_name", "unknown")
        
        # 构建记忆
        memories = [
            {
                "content": f"[R-CCAM] Strategy={strategy}, Tool={tool_used}, "
                          f"Confidence={confidence:.2f}",
                "source": "tool_call",
                "type": "tool_call",
                "timestamp": time.time(),
                "metadata": {
                    "tool": tool_used,
                    "tool_name": tool_used,
                    "session_id": "rccam_cycle",
                    "strategy": strategy,
                    "confidence": confidence,
                },
            },
            {
                "content": answer[:500] if answer else f"Cycle completed (confidence={confidence:.2f})",
                "source": "tool_result",
                "type": "tool_result",
                "timestamp": time.time() + 0.001,
                "metadata": {
                    "tool": tool_used,
                    "tool_name": tool_used,
                    "session_id": "rccam_cycle",
                    "reward": 10.0 if confidence > 0.7 else 5.0,
                    "strategy": strategy,
                },
            },
        ]
        
        from lfm_skill_bank import feed_memory_to_skill_bank
        result = feed_memory_to_skill_bank(memories)
        return result


# ════════════════════════════════════════════════════════════════
# 便利函数：执行一轮边界检测
# ════════════════════════════════════════════════════════════════

def run_boundary_detection_cycle(
    dag=None,
    session_key: str = "",
    config: Optional[BoundaryDetectorConfig] = None,
) -> Dict:
    """执行一轮边界检测（供 ConsolidationEngine 调用）
    
    Returns
    -------
    dict
        检测结果摘要
    """
    detector = LfmBoundaryDetector(config or BoundaryDetectorConfig())
    
    result: Dict[str, Any] = {}
    
    if dag and session_key:
        try:
            segments = detector.detect_from_dag(dag, session_key)
            result["segments"] = [
                {"id": s.segment_id,
                 "start": s.start_node_index,
                 "end": s.end_node_index,
                 "intent": s.intent_label,
                 "keywords": s.keywords[:3]}
                for s in segments
            ]
            result["n_segments"] = len(segments)
        except Exception as e:
            result["error"] = str(e)[:200]
    else:
        result["error"] = "dag or session_key not provided"
    
    return result


def run_full_cosplay_cycle(
    dag=None,
    session_key: str = "",
    rccam_cycle_info: Optional[Dict] = None,
) -> Dict:
    """执行一轮完整的 COSPLAY 剩余模块：
    
    1. Boundary Detection → segments
    2. NLP Predicate Extraction → predicates
    3. R-CCAM 反馈 → Skill Bank
    """
    config = BoundaryDetectorConfig()
    detector = LfmBoundaryDetector(config)
    
    result: Dict[str, Any] = {}
    
    # 1. Boundary Detection
    if dag and session_key:
        try:
            segments = detector.detect_from_dag(dag, session_key)
            result["boundary"] = {
                "n_segments": len(segments),
                "intents": list(dict.fromkeys(
                    s.intent_label for s in segments if s.intent_label
                )),
            }
        except Exception as e:
            result["boundary"] = {"error": str(e)[:200]}
    else:
        result["boundary"] = {"skipped": "dag/session_key missing"}
    
    # 2. R-CCAM → Skill Bank 反馈
    if rccam_cycle_info:
        try:
            bridge = RCCAMFeedbackBridge()
            sb_result = bridge.feed_cycle_result(rccam_cycle_info)
            result["skill_bank_feedback"] = {
                "ingested": sb_result.get("ingested", 0),
                "discovered": sb_result.get("discovered", 0),
            }
        except Exception as e:
            result["skill_bank_feedback"] = {"error": str(e)[:200]}
    else:
        result["skill_bank_feedback"] = {"skipped": "no cycle info"}
    
    return result


# ════════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════════

def main():
    import sys
    workspace = sys.argv[1] if len(sys.argv) > 1 else str(Path(workspace()))
    
    print(f"LFM Boundary Detector — workspace: {workspace}")
    print()
    
    config = BoundaryDetectorConfig(workspace=workspace)
    detector = LfmBoundaryDetector(config)
    nlp_ext = NLPPredicateExtractor(config)
    
    # 测试 NLP extraction
    test_texts = [
        "搜索一下今天的天气情况",
        "分析这段代码的性能问题",
        "帮我记住这个配置项",
        "执行一个系统健康检查",
        "你好，今天有什么新闻",
    ]
    
    features = nlp_ext.extract(test_texts)
    predicates = nlp_ext.extract_predicates(test_texts)
    
    print("=== NLP Predicate 提取测试 ===")
    for i, (text, feat, preds) in enumerate(zip(test_texts, features, predicates)):
        print(f"  [{i}] {text}")
        print(f"       keywords: {feat['keywords']}")
        print(f"       entities: {list(feat['entities'].keys())[:3]}")
        print(f"       sentiment: {feat['sentiment_polarity']:.3f}")
        print(f"       predicates: {dict(list(preds.items())[:5])}")
    print()
    
    # 测试 Boundary Detection
    print("=== 模拟 DAG 节点 → 边界检测 ===")
    mock_nodes = [
        {"content": "搜索一下最近的新闻", "source": "user"},
        {"content": "调用搜索工具查询新闻", "source": "tool_call"},
        {"content": "返回了5条科技新闻", "source": "tool_result"},
        {"content": "分析第一篇文章的技术细节", "source": "user"},
        {"content": "调用分析工具处理文章", "source": "tool_call"},
        {"content": "分析完成，关键点是AI芯片", "source": "tool_result"},
        {"content": "记住这个发现", "source": "user"},
        {"content": "调用记忆存储工具", "source": "tool_call"},
        {"content": "记忆已存储", "source": "tool_result"},
    ]
    
    segments = detector.detect(mock_nodes)
    print(f"  检测到 {len(segments)} 个 segment:")
    for s in segments:
        print(f"    {s.segment_id}: nodes [{s.start_node_index}→{s.end_node_index}] "
              f"intent={s.intent_label} kw={s.keywords[:3]}")
    
    print()
    print("✅ 全部测试通过")


if __name__ == "__main__":
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _script_dir)
    main()
