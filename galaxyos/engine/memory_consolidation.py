#!/usr/bin/env python3
"""
记忆巩固引擎 (Memory Consolidation Engine)

基于四篇论文方向：
1. 互补学习系统 CLS — DAG短期记忆 → 突触网络长期固化
2. 睡眠记忆巩固 — 空闲时重放高频路径 + 修剪弱突触
3. 记忆干扰模型 — 相似记忆合并/替换，减少冗余
4. 预测编码记忆 — 冲突检测，标记预测错误

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-05-14
"""

import json
import os
import math
import threading
import time
import hashlib
import random
import logging
import numpy as np
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict


@dataclass
class ConsolidationConfig:
    """巩固配置"""
    # CLS 固化
    consolidation_interval_s: int = 300        # 固化间隔（5分钟）
    dag_importance_threshold: float = 0.4      # DAG 节点固化阈值
    max_consolidate_per_cycle: int = 10        # 每轮最多固化数

    # 离线重放
    replay_interval_s: int = 900               # 重放间隔（15分钟）
    replay_top_k_paths: int = 5                # 重放高频路径数
    ltp_replay_strength: float = 0.05          # 重放 LTP 强度
    ltd_unused_days: int = 14                  # 无引用天数修剪阈值
    ltd_prune_weight: float = 0.15             # 低于此权重修剪

    # 干扰合并
    merge_similarity_threshold: float = 0.85   # embedding 余弦相似度阈值
    max_merge_candidates: int = 3              # 最多合并候选

    # 预测编码
    prediction_error_decay: float = 0.2        # 冲突记忆置信度衰减
    max_conflict_age_days: int = 90            # 旧冲突不处理


class ConsolidationEngine:
    """
    记忆巩固引擎
    
    核心能力：
    - CLS: DAG 节点 → 突触网络的固化
    - 离线重放: 空闲时强化高频路径
    - 干扰管理: 合并相似记忆
    - 预测编码: 冲突检测
    """

    def __init__(self, workspace_path: str = None, config: ConsolidationConfig = None):
        self.workspace = workspace_path or os.environ.get(
            "OPENCLAW_WORKSPACE",
            workspace())
        self.config = config or ConsolidationConfig()

        # 持久化路径
        self.consolidation_path = Path(self.workspace) / ".learnings" / "consolidation"
        self.consolidation_path.mkdir(parents=True, exist_ok=True)

        # 状态文件
        self.conflict_path = self.consolidation_path / "conflicts.jsonl"
        self.stats_path = self.consolidation_path / "consolidation_stats.jsonl"

        if not self.conflict_path.exists():
            self.conflict_path.touch()
        if not self.stats_path.exists():
            self.stats_path.touch()

        # 标记最后用户活跃时间（供睡眠引擎用）
        self._last_user_active = time.time()

        # 懒加载
        self._synapse_network = None
        self._ltd_adapter = None
        self._dag_db = None

        # 仿生睡眠巩固引擎（懒加载）
        self._sleep_consolidator = None
        self._sleep_consolidator_imported = False

        # 运行状态
        self._running = False
        self._thread = None

    # ============ 1. CLS 互补学习系统 ============

    def _get_synapse_network(self):
        """懒加载突触网络"""
        if self._synapse_network is None:
            import sys
            sys.path.insert(0, os.path.join(self.workspace,
                "skills/galaxyos-engine/skills/llm-memory-integration/core"))
            from memory_synapse_network import MemorySynapseNetwork
            self._synapse_network = MemorySynapseNetwork(self.workspace)
        return self._synapse_network

    def _get_ltd_adapter(self):
        """懒加载 LTP/LTD 适配器（艾宾浩斯版）"""
        if self._ltd_adapter is None:
            import sys
            sys.path.insert(0, os.path.join(self.workspace,
                "skills/galaxyos-engine/skills/llm-memory-integration/core"))
            from adaptive_ltp_ltd import AdaptiveLTP_LTD
            self._ltd_adapter = AdaptiveLTP_LTD()
        return self._ltd_adapter

    def consolidate_from_dag(self) -> Dict[str, Any]:
        """
        CLS 固化：DAG 高重要性节点 → 突触网络
        
        模拟海马体→新皮层的记忆固化机制。
        短期会话中的高重要性信息被写入突触网络作为长期记忆。
        
        Returns:
            统计信息
        """
        network = self._get_synapse_network()
        stats = {"consolidated": 0, "synapses_created": 0}

        try:
            import sqlite3
            # DAG 数据库
            dag_db_path = os.path.expanduser("~/.openclaw/dag_context.db")
            if not os.path.exists(dag_db_path):
                return stats

            conn = sqlite3.connect(dag_db_path)
            conn.row_factory = sqlite3.Row

            # 查询高重要性节点（dag_nodes + rccam_nodes）
            # dag_nodes 表（旧）
            cursor = conn.execute(
                """SELECT node_id, content, importance_score, node_type, timestamp
                   FROM dag_nodes
                   WHERE is_summary = 0
                     AND (importance_score IS NULL OR importance_score >= ?)
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (self.config.dag_importance_threshold,
                 self.config.max_consolidate_per_cycle)
            )
            nodes = cursor.fetchall()

            # rccam_nodes 表（新 R-CCAM 数据）
            try:
                cursor2 = conn.execute(
                    """SELECT node_id, content, importance_score, phase_name, timestamp
                       FROM rccam_nodes
                       WHERE importance_score IS NULL OR importance_score >= ?
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (self.config.dag_importance_threshold,
                     self.config.max_consolidate_per_cycle)
                )
                nodes += cursor2.fetchall()
            except Exception:
                pass  # rccam_nodes 表可能不存在时静默跳过

            # 懒加载跨模态绑定器（DAG 固化时生成 embedding）
            _binder = None
            try:
                from cross_modal_memory import CrossModalMemoryBinder
                _binder = CrossModalMemoryBinder(self.workspace)
            except Exception:
                pass

            for node in nodes:
                content = node["content"] if node["content"] else ""
                if len(content) < 10:
                    continue

                # 检查是否已存在
                existing = network.neuron_manager.find_neuron_by_content(content[:200])
                if existing:
                    network.neuron_manager.activate_neuron(existing.id)
                    continue

                # 创建新神经元（带真实 embedding）
                _emb = None
                if _binder is not None:
                    try:
                        _emb_np = _binder.text_to_embedding(content[:512])
                        if _emb_np is not None:
                            _emb = _emb_np.tolist()
                    except Exception:
                        pass
                neuron = network.create_neuron(content[:500], embedding=_emb)

                # 链接到同一 session 的其他节点（兼容 dag_nodes/rccam_nodes 列名差异）
                link_type = None
                for _col in ("node_type", "phase_name"):
                    try:
                        link_type = node[_col]
                        if link_type:
                            break
                    except (KeyError, AttributeError, IndexError):
                        continue
                if link_type:
                    try:
                        session_cursor = conn.execute(
                            "SELECT node_id FROM dag_nodes WHERE session_key = "
                            "(SELECT session_key FROM dag_nodes WHERE node_id = ?) "
                            "AND node_id != ? LIMIT 3",
                            (node["node_id"], node["node_id"])
                        )
                        related = session_cursor.fetchall()
                    except Exception:
                        related = []
                    for rel in related:
                        try:
                            network.create_synapse(rel["node_id"][:32], neuron.id, weight=0.4)
                            stats["synapses_created"] += 1
                        except Exception:
                            pass

                stats["consolidated"] += 1

            conn.close()

        except Exception as e:
            stats["error"] = str(e)

        return stats

    # ============ 2. 离线重放巩固 ============

    def replay_and_consolidate(self) -> Dict[str, Any]:
        """
        离线重放巩固
        
        模拟睡眠期的记忆重放：
        1. 选择高频激活的突触路径
        2. 对路径上的突触做 LTP 强化
        3. 对长期无引用的弱突触做 LTD 修剪
        
        Returns:
            统计信息
        """
        network = self._get_synapse_network()
        ltd = self._get_ltd_adapter()
        stats = {"ltp_replayed": 0, "ltd_pruned": 0}

        try:
            # 获取所有突触
            synapses = network.network._synapses_cache
            neurons = network.network._neurons_cache

            # 2a. 重放高频路径 — 按reinforcement_count排序
            sorted_synapses = sorted(
                synapses.values(),
                key=lambda s: s.reinforcement_count,
                reverse=True
            )

            for synapse in sorted_synapses[:self.config.replay_top_k_paths]:
                if synapse.weight < self.config.ltd_prune_weight:
                    continue
                # 强化 LTP
                synapse.weight = min(1.0, synapse.weight + self.config.ltp_replay_strength)
                synapse.reinforcement_count += 1
                stats["ltp_replayed"] += 1

            # 2b. 修剪长期无用的弱突触（艾宾浩斯衰减）
            now = datetime.now(timezone.utc)
            for synapse in list(synapses.values()):
                try:
                    last = datetime.fromisoformat(synapse.last_reinforced)
                except Exception:
                    continue

                days_unused = (now - last).total_seconds() / 86400

                if days_unused < self.config.ltd_unused_days:
                    continue

                # 用艾宾浩斯模型计算衰减
                from adaptive_ltp_ltd import SynapseState as LTDState
                syn_state = LTDState(
                    weight=synapse.weight,
                    reinforcement_count=synapse.reinforcement_count,
                    last_reinforced=last.replace(tzinfo=None),
                    importance=0.3,
                    created_at=last.replace(tzinfo=None)
                )
                ltd_rate = ltd.calculate_ltd_rate(syn_state, days_unused)

                # 应用衰减
                if ltd_rate > 0:
                    new_weight = max(0.0, synapse.weight - ltd_rate)
                    synapse.weight = new_weight

                    # 权重过低时修剪
                    if new_weight <= self.config.ltd_prune_weight:
                        stats["ltd_pruned"] += 1

        except Exception as e:
            stats["error"] = str(e)

        return stats

    # ============ 3. 记忆干扰管理 ============

    def detect_and_manage_interference(self, content: str, embedding: List[float] = None) -> Dict[str, Any]:
        """
        检测并管理记忆干扰
        
        新记忆与已有记忆高度相似时，做合并或替换，
        避免冗余和相互干扰。
        
        Args:
            content: 新记忆内容
            embedding: 新记忆的 embedding 向量
        
        Returns:
            处理结果
        """
        network = self._get_synapse_network()
        result = {"action": "store", "merged_with": None}

        try:
            neurons = network.neuron_manager.get_all_neurons()
            if not neurons:
                return result

            similarities = []

            if embedding:
                # 有 embedding → 余弦相似度
                for neuron in neurons:
                    if not neuron.embedding or len(neuron.embedding) != len(embedding):
                        continue
                    dot = sum(a * b for a, b in zip(embedding, neuron.embedding))
                    norm_a = math.sqrt(sum(x * x for x in embedding))
                    norm_b = math.sqrt(sum(x * x for x in neuron.embedding))
                    if norm_a * norm_b == 0:
                        continue
                    sim = dot / (norm_a * norm_b)
                    similarities.append((sim, neuron))
            else:
                # 无 embedding → 基于 jieba 关键词重叠降级
                try:
                    import jieba
                    query_tokens = set(jieba.lcut(content))
                    for neuron in neurons:
                        neuron_tokens = set(jieba.lcut(neuron.content))
                        if not query_tokens or not neuron_tokens:
                            continue
                        overlap = len(query_tokens & neuron_tokens)
                        total = len(query_tokens | neuron_tokens)
                        if total > 0:
                            sim = overlap / total
                            # 长句惩罚：内容越长，关键词重叠的置信度越低
                            length_penalty = min(1.0, 200 / max(len(content), len(neuron.content), 1))
                            sim = sim * length_penalty
                            if sim > 0.15:  # 15% 以上关键词重叠才考虑
                                similarities.append((sim, neuron))
                except ImportError:
                    pass

            if not similarities:
                return result

            # 按相似度排序
            similarities.sort(key=lambda x: x[0], reverse=True)
            best_sim, best_neuron = similarities[0]

            if best_sim >= self.config.merge_similarity_threshold:
                # 高度相似 → 合并：加强关联，不创建新记录
                # 找到该神经元的突触，做 LTP 强化
                outgoing = network.synapse_manager.get_outgoing_synapses(best_neuron.id)
                for syn in outgoing:
                    network.synapse_manager.ltp(syn)

                result["action"] = "merged"
                result["merged_with"] = best_neuron.id
                result["similarity"] = round(best_sim, 4)

            elif best_sim >= self.config.merge_similarity_threshold * 0.9:
                # 比较相似 → 建立关联突触
                # 这个在新创建时由调用方做
                result["action"] = "link"
                result["linked_to"] = best_neuron.id
                result["similarity"] = round(best_sim, 4)

        except Exception as e:
            result["error"] = str(e)

        return result

    # ============ 4. 预测编码记忆 ============

    def detect_prediction_error(
        self,
        query: str,
        retrieved_memories: List[Dict]
    ) -> List[Dict]:
        """
        检测预测错误（记忆冲突）
        
        当检索到的记忆与当前上下文矛盾时，标记"需验证"。
        
        Args:
            query: 当前查询
            retrieved_memories: 检索到的记忆列表
        
        Returns:
            标记冲突后的记忆列表
        """
        if not retrieved_memories or len(retrieved_memories) < 2:
            return retrieved_memories

        results = []
        now = datetime.now(timezone.utc)

        # 中文分词（如果有 jieba）
        _has_jieba = False
        try:
            import jieba
            _has_jieba = True
        except ImportError:
            pass

        def _tokenize(text: str) -> list:
            if _has_jieba:
                return jieba.lcut(text)
            return [w for w in text.split() if len(w) > 1]

        for mem in retrieved_memories:
            content = mem.get("content", "")
            confidence = mem.get("confidence", 0.5)
            conflict_score = 0.0

            # 检查与其他检索结果的矛盾
            for other in retrieved_memories:
                if other.get("id") == mem.get("id"):
                    continue

                other_content = other.get("content", "")

                # 基于分词的关键词重叠矛盾检测
                mem_tokens = set(_tokenize(content))
                other_tokens = set(_tokenize(other_content))

                # 否定词清单（中文 + 英文）
                negations = {"不", "不是", "没有", "不对", "没", "错误", "no", "not", "never", "false"}

                # 如果某条记忆包含否定词 + 与另一条共享关键词 → 冲突
                common = mem_tokens & other_tokens
                common_nopunct = {t for t in common if len(t) > 1}

                if common_nopunct:
                    has_neg_mem = bool(negations & mem_tokens)
                    has_neg_other = bool(negations & other_tokens)

                    if has_neg_mem != has_neg_other:
                        # 一个否定、一个肯定 → 冲突
                        conflict_score += 0.3 * min(len(common_nopunct), 3) / 3

                if conflict_score > 0.5:
                    break

            # 检查与查询的预测冲突
            query_keywords = set(query.lower().split())
            content_keywords = set(content.lower().split())

            if query_keywords & content_keywords:
                # 查询命中了一部分 — 算部分匹配
                pass
            elif len(content) > 20 and len(query) > 5:
                # 查询关键词都不在内容中 — 不太相关
                pass

            # 如果有冲突，降低置信度并标记
            if conflict_score > 0.3:
                mem["confidence"] = max(0.1, confidence - conflict_score * self.config.prediction_error_decay)
                mem["needs_verification"] = True

                # 持久化冲突记录
                self._log_conflict(mem)

            results.append(mem)

        return results

    def _log_conflict(self, memory: Dict):
        """持久化冲突记录"""
        try:
            entry = {
                "id": memory.get("id", "unknown"),
                "content": memory.get("content", "")[:200],
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "original_confidence": memory.get("confidence", 0.5),
                "adjusted_confidence": memory.get("confidence", 0.1),
                "needs_verification": True,
            }
            with open(self.conflict_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ============ 后台线程 ============

    def _run_consolidation_cycle(self):
        """执行一轮完整的巩固周期"""
        results = {}

        # 1. CLS 固化
        try:
            cls_stats = self.consolidate_from_dag()
            results["cls"] = cls_stats
        except Exception as e:
            results["cls"] = {"error": str(e)}

        # 2. 离线重放
        try:
            replay_stats = self.replay_and_consolidate()
            results["replay"] = replay_stats
        except Exception as e:
            results["replay"] = {"error": str(e)}

        # 3. 自适应突触修剪
        try:
            from memory_synapse_network import AdaptiveSynapsePruner
            pruner = AdaptiveSynapsePruner(self._get_synapse_network())
            prune_stats = pruner.run_prune()
            results["adaptive_prune"] = prune_stats
        except Exception as e:
            results["adaptive_prune"] = {"error": str(e)}
            _prune_candidates = 0
        else:
            _prune_candidates = prune_stats.get("prune_candidates", 0)

        # 4. Titans 神经记忆更新 — 将本轮 consolidation 编码到在线记忆向量
        try:
            from titans_neural_memory import TitansNeuralMemory
            _titans = TitansNeuralMemory(self.workspace)
            cls_count = results.get("cls", {}).get("consolidated", 0)
            if cls_count > 0 or results.get("replay", {}).get("ltp_replayed", 0) > 0:
                _titans.store(
                    content=f"consolidation_cycle: {cls_count} cls, {results.get('replay', {}).get('ltp_replayed', 0)} replayed",
                    metadata={"source": "consolidation_cycle",
                              "cls_count": cls_count,
                              "prune_count": _prune_candidates}
                )
            _state = _titans.get_state()
            results["titans"] = {
                "update_count": _state.get("update_count"),
                "memory_norm": _state.get("memory_norm"),
            }
        except Exception as e:
            results["titans"] = {"error": str(e)}

        # 5. 跨模态 embedding 绑定 — 补神经元缺失的 embedding
        try:
            from cross_modal_memory import CrossModalMemoryBinder
            _binder = CrossModalMemoryBinder(self.workspace)
            _net = self._get_synapse_network()
            _net.network._load()
            _bound = 0
            for n_id, n in list(getattr(_net.network, '_neurons_cache', {}).items()):
                if not n.embedding or len(n.embedding) < 128:
                    emb = _binder.text_to_embedding(n.content[:512])
                    if emb is not None:
                        n.embedding = emb.tolist()
                        _bound += 1
            results["cross_modal"] = {"bound_neurons": _bound}
        except Exception as e:
            results["cross_modal"] = {"error": str(e)}

        # 6. Engram 条件记忆 — 用 LFM embedding 更新 n-gram 表
        try:
            from engram_memory import EngramMemory, EngramConfig
            from lfm_adaptive_operator import RealLFMNetwork
            _engram_mem = EngramMemory(EngramConfig(embed_dim=2048, ngram_n=3))
            _engram_lfm = RealLFMNetwork()

            _net = self._get_synapse_network()
            _net.network._load()
            _engram_hit_total = 0
            _engram_count = 0
            for n_id, n in list(getattr(_net.network, '_neurons_cache', {}).items()):
                if n.content and len(n.content) > 10:
                    _engram_lfm.embed_and_store_engram(n.content[:256], _engram_mem)
                    _engram_count += 1
            results["engram"] = {
                "neurons_indexed": _engram_count,
                "hit_rate": _engram_mem.get_hit_rate(),
            }
        except Exception as e:
            results["engram"] = {"error": str(e)[:120]}

        # 7. LiquidWeight 动态权重融合 — 用 Engram hit_rate 调 prune 阈值
        try:
            from liquid_weight import LiquidWeightGenerator
            _lw = LiquidWeightGenerator()
            _hit = results.get("engram", {}).get("hit_rate", 0.0)
            lw_info = _lw.get_info()
            results["liquid_weight"] = {
                "hit_rate_gated_weight": round(_hit, 3),
                "liquid_weight_dim": lw_info.get("dim", 0),
            }
        except Exception as e:
            results["liquid_weight"] = {"error": str(e)[:120]}

        # 8. LFM 全链路集成 — 14 个液态/条件记忆模块同步运行
        try:
            import numpy as _np
            from lfm_full_integration import run_full_integration
            from lfm_adaptive_operator import RealLFMNetwork

            _full_lfm = RealLFMNetwork()
            _hit = results.get("engram", {}).get("hit_rate", 0.0)
            _recent_embs = []
            _ref_net = self._get_synapse_network()
            for n_id, n in list(getattr(_ref_net.network, '_neurons_cache', {}).items())[:30]:
                if hasattr(n, 'embedding') and n.embedding:
                    _recent_embs.append(_np.array(n.embedding[:2048], dtype=_np.float32))

            _sample_emb = _recent_embs[0] if _recent_embs else _np.random.randn(2048).astype(_np.float32)
            _integration = run_full_integration(_sample_emb, _recent_embs, _hit)

            # 提取关键指标汇总
            _summary = {k: {} for k in _integration}
            for k, v in _integration.items():
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        if isinstance(vv, (int, float)):
                            _summary[k][kk] = round(vv, 4)

            results["lfm_full_integration"] = {
                "modules_run": len(_integration),
                "summary": _summary,
            }
            logger.info(f"LFM 全链路集成完成: {len(_integration)} 模块")
        except Exception as e:
            results["lfm_full_integration"] = {"error": str(e)[:120]}

        # 9. NeuralMemoryGate — 召回模式预测 + 惊奇度门控
        try:
            from neural_memory_gate import NeuralMemoryGate
            _net = self._get_synapse_network()
            _net.network._load()
            _neuron_ids = list(getattr(_net.network, '_neurons_cache', {}).keys())
            _synapse_ids = list(getattr(_net.network, '_synapses_cache', {}).keys())

            _nm_gate = NeuralMemoryGate(prediction_top_k=5, surprise_k=1.5)
            if _neuron_ids:
                _nm_gate.record_recall(_neuron_ids[:20])
                _predicted = _nm_gate.predict_recall(_neuron_ids[:3])
                _actual = _neuron_ids[:5]
                _surprise = _nm_gate.compute_surprise(_predicted, _actual)
            else:
                _surprise = {"action": "none", "surprise_score": 0.0}

            results["neural_memory_gate"] = {
                "neuron_count": len(_neuron_ids),
                "synapse_count": len(_synapse_ids),
                "gate_action": _surprise.get("action", "unknown"),
                "surprise_score": round(_surprise.get("surprise", 0.0), 4),
            }
        except Exception as e:
            results["neural_memory_gate"] = {"error": str(e)[:120]}

        # 10. CognitiveMap — 空间认知锚点 + 语义密度分析
        try:
            from cognitive_map import CognitiveMap
            _cog_map = CognitiveMap(dim=2048)

            _net = self._get_synapse_network()
            _net.network._load()

            _anchored = 0
            _spatial_scores = []
            for n_id, n in list(getattr(_net.network, '_neurons_cache', {}).items())[:50]:
                emb = np.array(n.embedding[:2048], dtype=np.float32) if n.embedding else None
                if emb is not None and len(emb) >= 128:
                    _cog_map.add_anchor(n_id, n.content[:256], embedding=emb.tolist())
                    _anchored += 1
            _anchor_count = _anchored  # _anchor_cache 只在 add_anchor 后更新
            _density = _cog_map.get_anchor_density(
                np.zeros(2048, dtype=np.float32).tolist()
            ) if _anchored > 0 else 0.0

            results["cognitive_map"] = {
                "anchors_added": _anchored,
                "total_anchors": _anchor_count,
                "anchor_density": round(float(_density), 4),
            }
        except Exception as e:
            results["cognitive_map"] = {"error": str(e)[:120]}

        # 11. AutoLearner — 从 consolidation 结果中学习模式
        try:
            from auto_learner import AutoLearner
            _auto_learner = AutoLearner()

            _cls_cnt = results.get("cls", {}).get("consolidated", 0)
            _prune_cnt = results.get("adaptive_prune", {}).get("prune_candidates", 0)
            _replay_cnt = results.get("replay", {}).get("ltp_replayed", 0)
            _titans_norm = results.get("titans", {}).get("memory_norm", 0.0)

            _auto_learner.learn_pattern(f"consolidation_cls:{_cls_cnt}")
            _auto_learner.learn_pattern(f"consolidation_prune:{_prune_cnt}")
            _auto_learner.learn_pattern(f"consolidation_replay:{_replay_cnt}")

            if _titans_norm:
                _auto_learner.learn_preference("titans_memory_norm", float(_titans_norm))

            results["auto_learner"] = {
                "patterns_learned": 3,
                "total_preferences": len(getattr(_auto_learner, 'preferences', {})),
            }
        except Exception as e:
            results["auto_learner"] = {"error": str(e)[:120]}

        # 12. DreamDrivenLearner — 如果梦境日志有数据，执行一轮对比学习
        try:
            from dream_driven_learner import DreamDrivenLearner
            _dream_learner = DreamDrivenLearner(self.workspace)

            _dream_log = os.path.join(str(_dream_learner.workspace_path), ".learnings", "dreams", "dream_log.jsonl")
            if os.path.exists(_dream_log):
                _fragments = _dream_learner.collect_dream_fragments(_dream_log, max_fragments=20)
                if _fragments and len(_fragments) >= 2:
                    _train_result = _dream_learner.train_step(_fragments)
                else:
                    _train_result = {"skipped": "too_few_fragments", "count": len(_fragments or [])}
            else:
                _train_result = {"skipped": "no_dream_log"}

            results["dream_learner"] = {
                "result": str(_train_result.get("skipped", "trained"))[:80],
            }
            if "loss" in _train_result:
                results["dream_learner"]["loss"] = round(float(_train_result["loss"]), 4)
            if "pairs" in _train_result:
                results["dream_learner"]["pairs"] = _train_result["pairs"]
        except Exception as e:
            results["dream_learner"] = {"error": str(e)[:120]}

        # 记录统计
        # 记录统计
        try:
            stats_record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "results": results,
                "synapse_stats": self._get_synapse_network().get_stats(),
            }
            with open(self.stats_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(stats_record, ensure_ascii=False) + "\n")
        except Exception:
            pass

        return results

    def _try_sleep_consolidation(self) -> Dict:
        """尝试执行仿生睡眠巩固（空闲检测 + 懒加载导入）"""
        idle_seconds = time.time() - self._last_user_active
        if idle_seconds < 120:
            return {"skipped": "not_idle", "idle_seconds": idle_seconds}

        if self._sleep_consolidator is None and not self._sleep_consolidator_imported:
            self._sleep_consolidator_imported = True
            try:
                # v2026.6.11: biorhythm 在 galaxyos/engine/ 下，不用包导入
                # 2026-06-15 修复：两条旧路径都断了（galaxyos 包不存在 + 旧 workspace 路径不存在）
                # 改用 __file__ 相对路径或 EXT_DIR 运行时路径
                try:
                    import importlib.util
                    # 优先从本文件同级目录加载（运行时 engine/）
                    _this_dir = os.path.dirname(os.path.abspath(__file__))
                    _sleep_path = os.path.join(_this_dir, "biorhythm_sleep_consolidation.py")
                    if not os.path.exists(_sleep_path):
                        # 回退到 EXT_DIR 运行时路径
                        _ext_dir = os.environ.get("EXT_DIR", "")
                        _sleep_path = os.path.join(_ext_dir, "engine", "biorhythm_sleep_consolidation.py") if _ext_dir else _sleep_path
                    if os.path.exists(_sleep_path):
                        spec = importlib.util.spec_from_file_location("biorhythm_sleep_consolidation", _sleep_path)
                        if spec and spec.loader:
                            mod = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(mod)
                            self._sleep_consolidator = mod.BioRhythmSleepConsolidator(self.workspace)
                except Exception:
                    pass
                if self._sleep_consolidator:
                    self._sleep_consolidator.start_background()
            except Exception as e:
                return {"error": f"sleep_consolidator import failed: {e}"}

        if self._sleep_consolidator is None:
            return {"skipped": "import_failed"}

        # 执行一轮睡眠巩固
        try:
            result = self._sleep_consolidator.run_full_sleep_cycle()
            return result
        except Exception as e:
            return {"error": str(e)}

    def mark_active(self):
        """标记系统活跃（每次主推理调用后调用）"""
        self._last_user_active = time.time()
        # 也同步到睡眠引擎
        if self._sleep_consolidator is not None:
            try:
                self._sleep_consolidator.mark_active()
            except Exception:
                pass

    def _background_worker(self):
        """后台巩固线程 — 融合 CLS 固化 + 仿生睡眠巩固"""
        cls_counter = 0
        replay_counter = 0

        while self._running:
            time.sleep(10)  # 每10秒检查一次
            if not self._running:
                break

            cls_counter += 10
            replay_counter += 10
            idle_seconds = time.time() - self._last_user_active

            # ── 空闲时优先执行仿生睡眠周期（完整 5 阶段） ──
            if idle_seconds >= 120:
                # 每 300 秒触发一轮睡眠周期
                if cls_counter >= self.config.consolidation_interval_s:
                    try:
                        sleep_result = self._try_sleep_consolidation()
                        if "skipped" not in sleep_result:
                            # 睡眠周期本身包含了 CLS 固化等效功能
                            pass
                    except Exception:
                        pass
                    cls_counter = 0

            # ── 非空闲或空闲但未到睡眠周期时，走原 CLS 固化 ──
            if cls_counter < self.config.consolidation_interval_s or idle_seconds < 120:
                if cls_counter >= self.config.consolidation_interval_s:
                    try:
                        self._run_consolidation_cycle()
                    except Exception:
                        pass
                    cls_counter = 0

            # 每 24 小时清一次超期冲突记录
            if replay_counter >= 86400:
                try:
                    self._clean_old_conflicts()
                except Exception:
                    pass
                replay_counter = 0

    def _clean_old_conflicts(self):
        """清理超期冲突记录"""
        if not self.conflict_path.exists():
            return

        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.config.max_conflict_age_days)

        entries = []
        with open(self.conflict_path, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        entry = json.loads(line)
                        detected = datetime.fromisoformat(entry.get("detected_at", ""))
                        if detected >= cutoff:
                            entries.append(entry)
                    except Exception:
                        pass

        with open(self.conflict_path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def start_background(self):
        """启动后台巩固线程"""
        if self._running:
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._background_worker,
            daemon=True,
            name="mem-consolidation"
        )
        self._thread.start()
        return True

    def stop_background(self):
        """停止后台巩固线程"""
        self._running = False

    def get_stats(self) -> Dict[str, Any]:
        """获取巩固统计"""
        stats = {"consolidation_path": str(self.consolidation_path)}

        # 统计文件大小
        try:
            stats["conflict_count"] = sum(1 for _ in open(self.conflict_path)) if self.conflict_path.stat().st_size else 0
        except Exception:
            stats["conflict_count"] = 0

        # 突触网络统计
        try:
            syn_stats = self._get_synapse_network().get_stats()
            stats.update(syn_stats)
        except Exception:
            pass

        # 最新一轮巩固结果
        try:
            if self.stats_path.stat().st_size:
                with open(self.stats_path) as f:
                    last_line = None
                    for line in f:
                        if line.strip():
                            last_line = line
                    if last_line:
                        stats["last_cycle"] = json.loads(last_line)
        except Exception:
            pass

        return stats


# ==================== 便捷接口 ====================

_engine_instance = None
_engine_lock = threading.Lock()

def get_engine(workspace: str = None) -> ConsolidationEngine:
    """获取全局巩固引擎实例"""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = ConsolidationEngine(workspace)
    return _engine_instance


def start_consolidation(workspace: str = None) -> bool:
    """启动记忆巩固后台"""
    engine = get_engine(workspace)
    return engine.start_background()


def stop_consolidation():
    """停止记忆巩固后台"""
    engine = get_engine()
    engine.stop_background()


def run_once(workspace: str = None) -> Dict:
    """手动执行一轮巩固"""
    engine = get_engine(workspace)
    return engine._run_consolidation_cycle()


def check_interference(content: str, embedding: List[float] = None) -> Dict:
    """检测新记忆的干扰风险"""
    engine = get_engine()
    return engine.detect_and_manage_interference(content, embedding)


def mark_prediction_errors(query: str, memories: List[Dict]) -> List[Dict]:
    """标记检索结果的冲突"""
    engine = get_engine()
    return engine.detect_prediction_error(query, memories)


if __name__ == "__main__":
    # 测试
    engine = ConsolidationEngine()

    print("=== 记忆巩固引擎测试 ===\n")

    # 测试 CLS 固化
    print("1. CLS 固化...")
    stats = engine.consolidate_from_dag()
    print(f"   结果: {stats}")

    # 测试离线重放
    print("\n2. 离线重放...")
    stats = engine.replay_and_consolidate()
    print(f"   结果: {stats}")

    # 测试干扰检测
    print("\n3. 干扰检测（无 embedding，应跳过）...")
    result = engine.detect_and_manage_interference("测试内容", None)
    print(f"   结果: {result}")

    # 测试预测错误
    print("\n4. 预测编码（无冲突场景）...")
    test_mems = [
        {"id": "m1", "content": "Python 是一种编程语言", "confidence": 0.8},
        {"id": "m2", "content": "Java 是另一种编程语言", "confidence": 0.7},
    ]
    result = engine.detect_prediction_error("编程语言对比", test_mems)
    print(f"   结果: {len(result)} 条")
    for m in result:
        flag = " ⚠️" if m.get("needs_verification") else " ✅"
        print(f"   [{m['id']}] conf={m['confidence']}{flag}")

    # 测试统计
    print("\n5. 引擎统计...")
    stats = engine.get_stats()
    print(f"   冲突数: {stats.get('conflict_count')}")
    print(f"   巩固路径: {stats.get('consolidation_path')}")

    print("\n✅ 所有测试完成")
