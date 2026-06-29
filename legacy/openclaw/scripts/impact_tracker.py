"""
Impact Tracker — 模块效果追踪器

追踪每个模块的"预测 vs 实际"，计算命中率/有效性，
为 MetaOptimizer 提供数据基础。

架构定位: PipelineEngine 的最后一个 Phase，消费所有模块产出
"""

import time
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("galaxyos.impact")

# ─── 存储 ──────────────────────────────────────────────

_OPENCLAW_HOME = Path(__file__).parents[3] / ".openclaw"
_METRICS_DIR = _OPENCLAW_HOME / "galaxyos" / "impact_metrics"
_METRICS_DIR.mkdir(parents=True, exist_ok=True)


class ModuleMetrics:
    """单个模块的跨轮次指标"""

    def __init__(self, module_id: str, session_id: str):
        self.module_id = module_id
        self.session_id = session_id
        self.rounds: List[Dict[str, Any]] = []  # 每轮的快照
        self.params: Dict[str, Any] = {}         # 当前参数

    def record(self, metrics: Dict[str, Any]):
        """记录一轮指标"""
        entry = {"ts": time.time(), **metrics}
        self.rounds.append(entry)
        if len(self.rounds) > 50:
            self.rounds.pop(0)

    def recent(self, n: int = 5) -> List[Dict[str, Any]]:
        return self.rounds[-n:]

    @property
    def trend(self) -> str:
        """最近 5 轮的趋势: up/down/flat"""
        if len(self.rounds) < 3:
            return "flat"
        vals = [r.get("hit_rate", 0.5) for r in self.rounds[-5:]]
        if len(vals) < 2:
            return "flat"
        mid = len(vals) // 2
        avg_first = sum(vals[:mid]) / max(mid, 1)
        avg_last = sum(vals[mid:]) / max(len(vals) - mid, 1)
        if avg_last > avg_first * 1.1:
            return "up"
        elif avg_last < avg_first * 0.9:
            return "down"
        return "flat"


class ImpactTracker:
    """
    效果追踪器。

    每轮 pipeline run 结束后调用 track() 记录所有模块的预测 vs 实际。
    MetaOptimizer 调用 get_recommendations() 读取分析结果。
    """

    def __init__(self, persist_path: Optional[Path] = None):
        self.persist_path = persist_path or _METRICS_DIR
        self.persist_path.mkdir(parents=True, exist_ok=True)
        # session_id → module_id → ModuleMetrics
        self._store: Dict[str, Dict[str, ModuleMetrics]] = defaultdict(dict)
        # 上一轮的记录（用于对比预测 vs 实际）
        self._prev_predictions: Dict[str, Dict[str, Any]] = {}

    # ─── 每轮追踪 ──────────────────────────────────────

    def track(
        self,
        session_id: str,
        query: str,
        ctx: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        记录一轮产出，返回本轮 metrics。

        Args:
            session_id: 会话ID
            query: 本轮用户输入
            ctx: pipeline engine 产出的完整上下文

        Returns:
            {module_id: {hit_rate, ...}} 本轮指标
        """
        now = time.time()
        prev = self._prev_predictions.get(session_id, {})
        round_metrics: Dict[str, Any] = {}

        # ── SSM: 预测 vs 实际 ────
        ssm_pred = ctx.get("ssm_predicted", [])
        if ssm_pred:
            # 预测了哪些记忆ID
            predicted_ids = set()
            for p in ssm_pred:
                if isinstance(p, dict):
                    pid = p.get("id", "")
                elif isinstance(p, (list, tuple)) and len(p) > 0:
                    pid = p[0]
                else:
                    pid = str(p)
                if pid:
                    predicted_ids.add(pid)

            # 实际访问的记忆（看 heat_top 中有哪些）
            heat_top = ctx.get("heat_top", [])
            actual_ids = set()
            for h in heat_top:
                if isinstance(h, dict):
                    actual_ids.add(h.get("id", ""))
                else:
                    actual_ids.add(str(h))

            # 命中率
            if predicted_ids:
                hits = predicted_ids & actual_ids
                hit_rate = len(hits) / len(predicted_ids)
            else:
                hit_rate = 0.0

            round_metrics["ssm"] = {
                "predicted": len(predicted_ids),
                "actual": len(actual_ids),
                "hits": len(hits := predicted_ids & actual_ids) if predicted_ids else 0,
                "hit_rate": round(hit_rate, 3),
            }

        # ── IsREL: 阈值调整效果 ────
        isrel_override = ctx.get("isrel_threshold_override")
        if isrel_override is not None:
            crag_quality = ctx.get("crag_quality", 0.5)
            should_retrieve = ctx.get("should_retrieve", True)
            # 对比前一轮的质量
            prev_quality = prev.get("crag_quality", 0.5)
            quality_delta = crag_quality - prev_quality
            round_metrics["isrel"] = {
                "threshold_override": isrel_override,
                "retrieved": should_retrieve,
                "crag_quality": crag_quality,
                "quality_delta": round(quality_delta, 3),
            }

        # ── CoEvolve: 失败检测效果 ────
        coe_failure = ctx.get("coevolve_failure", False)
        coe_pattern = ctx.get("coevolve_pattern")
        if coe_failure:
            round_metrics["coevolve"] = {
                "failure": True,
                "pattern": coe_pattern,
                "retrieval_quality": ctx.get("crag_quality", 0.5),
            }

        # ── TurnRecovery: 恢复效果 ────
        if ctx.get("turn_recovery"):
            round_metrics["turn_recovery"] = {
                "triggered": True,
                "action": ctx.get("turn_action", "unknown"),
            }

        # ── MemoryOS: profile 是否被使用（Value Gate 会更新这个指标） ────
        profile = ctx.get("memoryos_profile", "")
        if profile:
            round_metrics["memoryos"] = {
                "profile_length": len(profile),
                "usage_rate": ctx.get("_memoryos_usage_rate", 0.0),
            }

        # ── CfC: 意图预测一致性 ────
        cfc_intent = ctx.get("cfc_intent")
        if cfc_intent:
            round_metrics["cfc"] = {
                "intent_detected": bool(cfc_intent),
                "confidence": getattr(cfc_intent, "confidence", 0.5)
                if not isinstance(cfc_intent, dict) else cfc_intent.get("confidence", 0.5),
            }

        # 存储到 session 级 store
        for mod_id, metrics in round_metrics.items():
            ms = self._module_store(session_id, mod_id)
            ms.record(metrics)

        # 保存本轮 ctx 的关键字段，供下轮对比
        self._prev_predictions[session_id] = {
            "crag_quality": ctx.get("crag_quality", 0.5),
            "cove_ratio": ctx.get("cove_verified_ratio", 0.5),
            "should_retrieve": ctx.get("should_retrieve", True),
            "ts": now,
        }

        self._persist(session_id)
        return round_metrics

    def report_usage(
        self,
        session_id: str,
        module_id: str,
        field: str,
        value: float,
    ):
        """Value Gate 反馈：模块产出的利用率"""
        ms = self._module_store(session_id, module_id)
        # 找到最近一条记录的 field 并更新
        if ms.rounds:
            ms.rounds[-1][field] = value

    # ─── 分析接口 ──────────────────────────────────────

    def get_hit_rate(self, session_id: str, module_id: str) -> float:
        """最近 5 轮的平均命中率"""
        ms = self._module_store(session_id, module_id)
        rates = [r.get("hit_rate", 0) for r in ms.recent(5) if "hit_rate" in r]
        if not rates:
            return 0.0
        return sum(rates) / len(rates)

    def get_trend(self, session_id: str, module_id: str) -> str:
        """最近趋势 up/down/flat"""
        return self._module_store(session_id, module_id).trend

    def get_metrics_snapshot(self, session_id: str) -> Dict[str, Any]:
        """返回某 session 所有模块的最新指标"""
        snapshot = {}
        sessions = self._store.get(session_id, {})
        for mod_id, ms in sessions.items():
            if ms.rounds:
                snapshot[mod_id] = {
                    **ms.rounds[-1],
                    "trend": ms.trend,
                }
        return snapshot

    def get_quality_trend(self, session_id: str, n: int = 5) -> List[float]:
        """最近 n 轮的检索质量变化"""
        ms = self._module_store(session_id, "crag")
        return [r.get("quality", 0.5) for r in ms.recent(n) if "quality" in r]

    # ─── 内部 ───────────────────────────────────────────

    def _module_store(self, session_id: str, module_id: str) -> ModuleMetrics:
        if module_id not in self._store[session_id]:
            self._store[session_id][module_id] = ModuleMetrics(module_id, session_id)
        return self._store[session_id][module_id]

    def _persist(self, session_id: str):
        """持久化到磁盘（JSON）"""
        if session_id not in self._store:
            return
        path = self.persist_path / f"{session_id}.json"
        try:
            data = {}
            for mod_id, ms in self._store[session_id].items():
                data[mod_id] = {
                    "rounds": ms.rounds[-20:],  # 只存最近 20 轮
                    "params": ms.params,
                }
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            logger.debug(f"[impact] persist fail: {e}")

    def load_session(self, session_id: str):
        """加载持久化的 session 数据"""
        path = self.persist_path / f"{session_id}.json"
        if not path.exists():
            return
        try:
            with open(path) as f:
                data = json.load(f)
            for mod_id, entry in data.items():
                ms = self._module_store(session_id, mod_id)
                ms.rounds = entry.get("rounds", [])
                ms.params = entry.get("params", {})
        except Exception as e:
            logger.debug(f"[impact] load fail: {e}")

    def reset_session(self, session_id: str):
        """重置 session 数据"""
        self._store.pop(session_id, None)
        self._prev_predictions.pop(session_id, None)
        path = self.persist_path / f"{session_id}.json"
        if path.exists():
            path.unlink(missing_ok=True)
